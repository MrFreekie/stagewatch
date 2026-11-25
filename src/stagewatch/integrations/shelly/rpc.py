"""Frames a Shelly sends to Stagewatch's WebSocket receiver: parsing and merging. Pure: no network, no hub,
never raises.

A Shelly with "Outbound WebSocket" switched on connects to the receiver and sends JSON-RPC notifications:
``NotifyFullStatus`` (every component), ``NotifyStatus`` (only what changed) and ``NotifyEvent``. Stagewatch
only listens. Source: Shelly Gen2+ API documentation, "Notifications" and the Outbound WebSocket (Ws)
component; the frame shape is in tests/fixtures/shelly/docs-*.json (copied from those pages, not captured).

Unverified (never captured from a real device yet): what ``Origin`` header or subprotocol a Shelly sends, the
exact event names in ``NotifyEvent``, and whether a steady load makes a meter send regular updates.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from ...core.model import normalise_mac

MAX_FRAME = 64 * 1024            # the same cap as the dashboards' socket
MAX_DEPTH = 8                    # nesting allowed inside a frame
MAX_COMPONENTS = 48              # components kept per device
MAX_KEYS = 64                    # keys looked at in one object
MAX_INT_DIGITS = 20              # no real reading has a longer whole number: refused at parse time
METHODS = frozenset({"NotifyFullStatus", "NotifyStatus", "NotifyEvent"})
_SRC = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_COMPONENT = re.compile(r"^[a-z][a-z0-9_]{0,23}(:[0-9]{1,2})?$")
_HEX12 = re.compile(r"^[0-9A-Fa-f]{12}$")

# Friendly model labels for the "src" prefixes the documentation shows. Anything else is shown as its (cleaned) prefix.
MODEL_LABELS = {"shellyhtg3": "Shelly H&T Gen3", "shellyplusht": "Shelly Plus H&T"}


@dataclass(frozen=True)
class Frame:
    method: str
    src: str
    mac: str                    # 12 lower-case hex, from src
    prefix: str                 # src without the MAC, cleaned: "shellyhtg3"
    ts: float | None            # the device's own clock (only ever used for the clock check, never as a sample time)
    params: dict = field(default_factory=dict)

    @property
    def full(self) -> bool:
        return self.method == "NotifyFullStatus"


@dataclass(frozen=True)
class Reject:
    reason: str                 # not_json, not_object, bad_src, method, request, depth, mac_mismatch, bad_params, size


def _depth_ok(value: object, limit: int = MAX_DEPTH) -> bool:
    """True when nothing is nested deeper than ``limit`` (iterative: a hostile frame cannot overflow the stack)."""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > limit:
            return False
        stack.extend((c, depth + 1) for c in children)
    return True


def _parse_int(text: str) -> int:
    if len(text) > MAX_INT_DIGITS:
        raise ValueError("number too long")
    return int(text)


def parse_frame(raw: object) -> Frame | Reject:
    """One frame as received. Anything that is not a well-formed notification from a Shelly is a Reject with a
    fixed reason; the content is never repeated."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            if len(raw) > MAX_FRAME:
                return Reject("size")
            raw = bytes(raw).decode("utf-8")
        elif isinstance(raw, str):
            if len(raw) > MAX_FRAME:
                return Reject("size")
        else:
            return Reject("not_json")
        data = json.loads(raw, parse_int=_parse_int)
    except (ValueError, RecursionError, UnicodeDecodeError, OverflowError):
        return Reject("not_json")
    if not isinstance(data, dict):
        return Reject("not_object")
    if not _depth_ok(data):
        return Reject("depth")
    if "id" in data:
        return Reject("request")        # a request from the device: never answered
    src = data.get("src")
    if not isinstance(src, str) or not _SRC.match(src) or "-" not in src:
        return Reject("bad_src")
    prefix, _, tail = src.rpartition("-")
    if not _HEX12.match(tail) or not prefix:
        return Reject("bad_src")
    mac = tail.lower()
    method = data.get("method")
    if method not in METHODS:
        return Reject("method")
    params = data.get("params")
    if not isinstance(params, dict):
        return Reject("bad_params")
    sys_ = params.get("sys")
    if isinstance(sys_, dict) and "mac" in sys_ and normalise_mac(sys_.get("mac")) != mac:
        return Reject("mac_mismatch")
    ts = _finite(params.get("ts"))
    return Frame(method, src, mac, "".join(c for c in prefix.lower() if c.isalnum())[:40], ts, params)


def _finite(value: object) -> float | None:
    """A finite float, or None for anything else (including a number too large to be one)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (OverflowError, ValueError):
        return None
    return out if math.isfinite(out) else None


def component_names(params: dict) -> list[str]:
    """The names of the components in a frame (``temperature:0``, ``em:0``), bounded and pattern-checked. Names only:
    never values."""
    out = []
    for key in list(params)[:MAX_KEYS]:
        if isinstance(key, str) and _COMPONENT.match(key) and isinstance(params[key], dict):
            out.append(key)
    return out[:MAX_COMPONENTS]


def _merge(into: dict, delta: dict, depth: int) -> None:
    for key, value in list(delta.items())[:MAX_KEYS * 2]:
        if value is None:
            into.pop(key, None)         # null deletes the key (Notifications documentation)
        elif isinstance(value, dict) and isinstance(into.get(key), dict) and depth < MAX_DEPTH:
            _merge(into[key], value, depth + 1)
        elif isinstance(value, dict):
            into[key] = {}
            if depth < MAX_DEPTH:
                _merge(into[key], value, depth + 1)
        else:
            into[key] = value


def _components_only(params: dict) -> dict:
    return {k: v for k, v in params.items() if k in set(component_names(params))}


def merge_status(stored: dict | None, frame: Frame) -> dict:
    """The device's status after this frame. A full status replaces what was held; a partial status is merged
    into it (a ``null`` removes a key). A partial status with nothing held to merge into gives {}: a delta is not
    a status. Returns a new dict; the stored one is never changed."""
    if frame.method == "NotifyFullStatus":
        out: dict = {}
        _merge(out, _components_only(frame.params), 1)
        return out
    if frame.method != "NotifyStatus" or not stored:
        return dict(stored or {})
    out = {k: json.loads(json.dumps(v)) for k, v in stored.items()}
    _merge(out, _components_only(frame.params), 1)
    for key in list(out)[MAX_COMPONENTS:]:
        del out[key]
    return out


def clock_skew(frame: Frame, received_at: float) -> float | None:
    """Seconds the device's clock is ahead (+) or behind (-) Stagewatch's, or None when it sent no time. Shown to the
    admin ("device clock not set"); never used as the time of a reading."""
    return None if frame.ts is None else frame.ts - received_at


# ------------------------------------------------------------------ what a device is, from its component names
HT, METER, OTHER = "ht", "meter", "other"
_METER_PREFIXES = ("em", "em1", "emdata", "pm1")


def kind_of(names: list[str]) -> str:
    """"ht" (temperature and humidity), "meter" (has a power-capable component) or "other"."""
    bases = {n.split(":")[0] for n in names}
    if {"temperature", "humidity"} <= bases:
        return HT
    if bases & set(_METER_PREFIXES) or "switch" in bases:
        return METER
    return OTHER


def kind_label(names: list[str]) -> str:
    """Plain words for the found list, from the component names."""
    bases = {n.split(":")[0] for n in names}
    if {"temperature", "humidity"} <= bases:
        return "Temperature and humidity sensor (H&T)"
    if "em" in bases:
        return "Three-phase meter (em)"
    if "em1" in bases:
        return "Single-circuit meter (em1)"
    if "pm1" in bases:
        return "Power meter (pm1)"
    if "switch" in bases:
        return "Relay (switch): read only if it measures power"
    return "Not supported yet"


def model_label(prefix: str) -> str:
    return MODEL_LABELS.get(prefix, prefix[:40])
