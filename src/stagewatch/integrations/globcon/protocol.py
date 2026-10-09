"""GLOBCON wire format: a small hand-written protobuf reader and writer, and the one list of what
Stagewatch may ever send.

Source: the field numbers below were read from the generated protobuf code in GLOBCON's own web app
("Remote Controller", main.47b68a92.js), and the message flow was confirmed against a real capture of
that app talking to a real GLOBCON (09/10/2026, 1,288 frames). Not tested beyond that capture.

    Container { Any msg = 1; Method method = 2; int32 seq = 15; }
    Method    { GET=0 SET=1 UPDATE=2 ACTION=3 SUBSCRIBE=4 UNSUBSCRIBE=5 AUTH=6 }
    Any       { string type_url = 1; bytes value = 2; }      (type name = last "/" segment of type_url)
    Value     { string path = 1; one of { bool bval = 2; double dval = 3; int32 ival = 4; string sval = 5; } }
    ValueList { repeated Value values = 1; }
    RTValue   { string path = 1; bytes blob = 2; }
    Ping      { string service = 1; bool verbose = 2; }
    Pong      { repeated string services = 1; }

Read-only by construction. ``allowed()`` is the single gate every outgoing frame passes: it decodes the
frame and accepts only Ping, GET, SUBSCRIBE and UNSUBSCRIBE of the read paths below, and AUTH with the
log-in path. SET, UPDATE and ACTION (which move faders, mute, solo, change layers and run functions on
the controller) can never pass, whatever calls the client.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass

# Container.Method
GET, SET, UPDATE, ACTION, SUBSCRIBE, UNSUBSCRIBE, AUTH = range(7)
METHOD_NAMES = {GET: "GET", SET: "SET", UPDATE: "UPDATE", ACTION: "ACTION", SUBSCRIBE: "SUBSCRIBE",
                UNSUBSCRIBE: "UNSUBSCRIBE", AUTH: "AUTH"}

#: Methods Stagewatch may send, and nothing else.
SENDABLE_METHODS = (GET, SUBSCRIBE, UNSUBSCRIBE, AUTH)

SEQ = 11                      # the web app's constant sequence number (the server does not seem to use it)
PING_SERVICE = "globcon remote"   # the string the web app sends; the only one seen to work
API_PATH = "/api/v1"
DEFAULT_PORT = 9091
MAX_CONTROLLERS = 16
MAX_STRIPS = 16
MAX_LAYERS = 12
METER_NONE_DB = -250.0        # GLOBCON's "no signal / unused" level
MAX_DEPTH_BYTES = 1 << 20     # hard stop for any single frame we are asked to decode


class DecodeError(ValueError):
    """Not a well-formed message (truncated, bad wire type, over-long). Never carries the data."""


# ---------------------------------------------------------------- reading
def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf) or shift > 63:
            raise DecodeError("varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def fields(buf: bytes) -> list[tuple[int, int, object]]:
    """[(field number, wire type, value)]. Wire type 0 gives an int, 1 and 5 give bytes (8 / 4),
    2 gives bytes. Groups and unknown wire types are an error."""
    if len(buf) > MAX_DEPTH_BYTES:
        raise DecodeError("too large")
    out: list[tuple[int, int, object]] = []
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        num, wt = key >> 3, key & 7
        if num == 0:
            raise DecodeError("field 0")
        if wt == 0:
            val, pos = _varint(buf, pos)
        elif wt == 1:
            val, pos = buf[pos:pos + 8], pos + 8
        elif wt == 5:
            val, pos = buf[pos:pos + 4], pos + 4
        elif wt == 2:
            n, pos = _varint(buf, pos)
            if n > end - pos:
                raise DecodeError("length")
            val, pos = buf[pos:pos + n], pos + n
        else:
            raise DecodeError("wire type")
        if wt in (1, 5) and len(val) != (8 if wt == 1 else 4):
            raise DecodeError("truncated")
        out.append((num, wt, val))
    return out


def _text(raw: object) -> str:
    if not isinstance(raw, (bytes, bytearray)):
        raise DecodeError("text")
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise DecodeError("utf-8") from None


@dataclass(frozen=True)
class Value:
    """One GLOBCON value. ``kind`` is "b", "d", "i" or "s" (which member was present), or "" for
    a path sent with no value."""
    path: str
    kind: str = ""
    value: object = None


@dataclass(frozen=True)
class RTValue:
    path: str
    blob: bytes


@dataclass(frozen=True)
class Container:
    method: int
    type_name: str      # "Value", "ValueList", "RTValue", "Ping", "Pong" or whatever the sender said
    payload: bytes
    seq: int = 0


def _signed32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - (1 << 32) if n & 0x80000000 else n


def decode_value(buf: bytes) -> Value:
    path, kind, value = "", "", None
    for num, wt, raw in fields(buf):
        if num == 1 and wt == 2:
            path = _text(raw)
        elif num == 2 and wt == 0:
            kind, value = "b", bool(raw)
        elif num == 3 and wt == 1:
            kind, value = "d", struct.unpack("<d", raw)[0]
        elif num == 4 and wt == 0:
            kind, value = "i", _signed32(int(raw))
        elif num == 5 and wt == 2:
            kind, value = "s", _text(raw)
    return Value(path, kind, value)


def decode_value_list(buf: bytes) -> list[Value]:
    return [decode_value(raw) for num, wt, raw in fields(buf) if num == 1 and wt == 2]


def decode_rtvalue(buf: bytes) -> RTValue:
    path, blob = "", b""
    for num, wt, raw in fields(buf):
        if num == 1 and wt == 2:
            path = _text(raw)
        elif num == 2 and wt == 2:
            blob = bytes(raw)
    return RTValue(path, blob)


def decode_container(frame: bytes) -> Container:
    method, seq, type_url, payload = 0, 0, "", b""
    for num, wt, raw in fields(frame):
        if num == 1 and wt == 2:
            for n2, w2, r2 in fields(raw):
                if n2 == 1 and w2 == 2:
                    type_url = _text(r2)
                elif n2 == 2 and w2 == 2:
                    payload = bytes(r2)
        elif num == 2 and wt == 0:
            method = int(raw)
        elif num == 15 and wt == 0:
            seq = _signed32(int(raw))
    return Container(method, type_url.rsplit("/", 1)[-1], payload, seq)


def decode_ping_service(buf: bytes) -> str:
    for num, wt, raw in fields(buf):
        if num == 1 and wt == 2:
            return _text(raw)
    return ""


# ---------------------------------------------------------------- writing
def _enc_varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _enc_len(num: int, data: bytes) -> bytes:
    return _enc_varint(num << 3 | 2) + _enc_varint(len(data)) + data


def encode_value(path: str, sval: str | None = None, *, bval: bool | None = None,
                 ival: int | None = None) -> bytes:
    """A Value with at most one member set (``sval`` is the log-in password in an AUTH message; the
    others are for the simulated feed and tests)."""
    out = _enc_len(1, path.encode("utf-8"))
    if bval is not None:
        out += _enc_varint(2 << 3) + _enc_varint(1 if bval else 0)
    if ival is not None:
        out += _enc_varint(4 << 3) + _enc_varint(ival)
    if sval is not None:
        out += _enc_len(5, sval.encode("utf-8"))
    return out


def encode_value_list(values: list[bytes]) -> bytes:
    return b"".join(_enc_len(1, v) for v in values)


def encode_rtvalue(path: str) -> bytes:
    return _enc_len(1, path.encode("utf-8"))


def encode_ping(service: str = PING_SERVICE) -> bytes:
    return _enc_len(1, service.encode("utf-8"))


def encode_container(method: int, type_name: str, payload: bytes, seq: int = SEQ) -> bytes:
    """The web app packs with the prefix "~", so the type URL reads ``~/Value``."""
    any_msg = _enc_len(1, f"~/{type_name}".encode("utf-8")) + _enc_len(2, payload)
    out = _enc_len(1, any_msg)
    if method:
        out += _enc_varint(2 << 3) + _enc_varint(method)
    if seq:
        out += _enc_varint(15 << 3) + _enc_varint(seq)
    return out


# ------------------------------------------------- what Stagewatch may send
GENERAL_PATH = "/general/"
_CONTROLLER_RE = re.compile(r"^/controller/(\d{1,2})/(meters)?$")
_LOGIN_RE = re.compile(r"^/general/login/(\d{1,2})$")


def controller_path(n: int) -> str:
    return f"/controller/{n}/"


def meters_path(n: int) -> str:
    return f"/controller/{n}/meters"


def ping() -> bytes:
    return encode_container(GET, "Ping", encode_ping())


def get_value(path: str) -> bytes:
    return encode_container(GET, "Value", encode_value(path))


def subscribe_value(path: str) -> bytes:
    return encode_container(SUBSCRIBE, "Value", encode_value(path))


def unsubscribe_value(path: str) -> bytes:
    return encode_container(UNSUBSCRIBE, "Value", encode_value(path))


def subscribe_meters(n: int) -> bytes:
    return encode_container(SUBSCRIBE, "RTValue", encode_rtvalue(meters_path(n)))


def unsubscribe_meters(n: int) -> bytes:
    return encode_container(UNSUBSCRIBE, "RTValue", encode_rtvalue(meters_path(n)))


def login(controller: int, password: str) -> bytes:
    return encode_container(AUTH, "Value", encode_value(f"/general/login/{controller}", password))


def _read_path_ok(path: str) -> bool:
    if path == GENERAL_PATH:
        return True
    m = _CONTROLLER_RE.match(path)
    return bool(m) and int(m.group(1)) < MAX_CONTROLLERS


def allowed(frame: object) -> bool:
    """True only for a frame that is exactly one of the read-only messages Stagewatch is allowed to
    send. The frame is decoded here, so what is checked is what would arrive, not what the caller
    says it is. Anything that does not decode, any other method (SET, UPDATE, ACTION) and any other
    type or path is refused."""
    if not isinstance(frame, (bytes, bytearray)):
        return False
    try:
        c = decode_container(bytes(frame))
        if c.method not in SENDABLE_METHODS:
            return False
        if c.type_name == "Ping":
            return c.method == GET and decode_ping_service(c.payload) == PING_SERVICE
        if c.type_name == "Value":
            v = decode_value(c.payload)
            if c.method == AUTH:
                m = _LOGIN_RE.match(v.path)
                return bool(m) and int(m.group(1)) < MAX_CONTROLLERS and v.kind == "s"
            return c.method in (GET, SUBSCRIBE, UNSUBSCRIBE) and v.kind == "" and _read_path_ok(v.path) \
                and not v.path.endswith("/meters")
        if c.type_name == "RTValue":
            r = decode_rtvalue(c.payload)
            m = _CONTROLLER_RE.match(r.path)
            return c.method in (SUBSCRIBE, UNSUBSCRIBE) and not r.blob and bool(m) and bool(m.group(2)) \
                and int(m.group(1)) < MAX_CONTROLLERS
    except DecodeError:
        return False
    return False


# ------------------------------------------------------------------ meters
def parse_meters(blob: bytes) -> list[float | None] | None:
    """The level block: little-endian float32 dB, one per strip of the controller's current layer,
    at most MAX_STRIPS. GLOBCON's -250 (no signal / unused), NaN, infinity and anything outside
    -250..+30 dB become None ("empty"), never zero. None for a block that is not whole float32s."""
    if not isinstance(blob, (bytes, bytearray)) or not blob or len(blob) % 4:
        return None
    n = min(len(blob) // 4, MAX_STRIPS)
    out: list[float | None] = []
    for (v,) in struct.iter_unpack("<f", bytes(blob[:n * 4])):
        out.append(None if not math.isfinite(v) or v <= METER_NONE_DB + 0.5 or v > 30.0 else float(v))
    return out


def encode_meters(levels: list[float | None]) -> bytes:
    """For the simulated feed and tests: the inverse of parse_meters (None -> -250)."""
    return b"".join(struct.pack("<f", METER_NONE_DB if v is None else v) for v in levels)
