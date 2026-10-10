"""DirectOut GLOBCON level meters: the hand-written protobuf reader and writer, the read-only gate on
everything sent, the real client against a fake GLOBCON (a WebSocket server in this file), the state
mapping, the integration through the hub in emulate mode, the config, and the API.

What is REAL and what is SYNTHETIC. The field numbers, paths and message flow come from GLOBCON's own web
app and one real capture of it (09/10/2026: Ping every 2 s, SUBSCRIBE Value /general/ and
/controller/0/, SUBSCRIBE RTValue /controller/0/meters, a 64-byte block of 16 little-endian float32 dB
about every 0.1 s, -250 meaning no signal, strip labels "Input Manager #1", "Flex Channel 3", "USB 1").
Stagewatch has never run against a live GLOBCON. Every frame in this file is built here to match that
description, so it is SYNTHETIC: the names are made up and nothing is from a show.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from stagewatch.core.config import GLOBCON_RANGES, Config, ConfigStore, Dashboard, GlobconCardOptions, GlobconConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.integrations.globcon import (
    CARD_ID, GlobconIntegration, seed_emulate_globcon, wanted_controllers)
from stagewatch.integrations.globcon import mapping, protocol
from stagewatch.integrations.globcon.client import TEXT, GlobconClient, is_acceptable_address, ws_url
from stagewatch.integrations.globcon.emulate import EmulatedGlobconSource, level_block
from stagewatch.integrations.globcon.protocol import Value
from stagewatch.web.server import create_app
from test_ontime import free_port, until

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
PW = "Gl0bc0n-Zq7-secret"


# ----------------------------------------------------------------------------- the wire format
def test_ping_frame_matches_the_bytes_worked_out_by_hand():
    """Container{ msg: Any{ type_url "~/Ping", value: Ping{ service "globcon remote" } }, seq 11 }.
    Ping = 0a 0e + 14 chars (16 bytes); Any = 0a 06 "~/Ping" + 12 10 + 16 (26 bytes);
    Container = 0a 1a + 26, then field 15 varint 11 = 78 0b. GET is 0, so the method is left out."""
    want = (bytes([0x0A, 0x1A, 0x0A, 0x06]) + b"~/Ping" + bytes([0x12, 0x10, 0x0A, 0x0E])
            + b"globcon remote" + bytes([0x78, 0x0B]))
    assert protocol.ping() == want
    c = protocol.decode_container(want)
    assert (c.method, c.type_name, c.seq) == (protocol.GET, "Ping", 11)
    assert protocol.decode_ping_service(c.payload) == "globcon remote"


def test_subscribe_meters_frame_bytes_by_hand():
    """RTValue{path "/controller/0/meters"} = 0a 14 + 20 chars; Any = 0a 0a "~/RTValue" is 9 chars so
    0a 09 ...; method SUBSCRIBE = 4 -> 10 04; seq 78 0b."""
    path = b"/controller/0/meters"
    assert len(path) == 20
    rt = bytes([0x0A, 20]) + path
    any_msg = bytes([0x0A, 9]) + b"~/RTValue" + bytes([0x12, len(rt)]) + rt
    want = bytes([0x0A, len(any_msg)]) + any_msg + bytes([0x10, 0x04, 0x78, 0x0B])
    assert protocol.subscribe_meters(0) == want


def test_value_decode_all_four_kinds_and_a_negative_int():
    assert protocol.decode_value(protocol.encode_value("/a", bval=True)) == Value("/a", "b", True)
    assert protocol.decode_value(protocol.encode_value("/a", ival=-3)) == Value("/a", "i", -3)
    assert protocol.decode_value(protocol.encode_value("/a", sval="Flex Channel 3")) == Value("/a", "s", "Flex Channel 3")
    dval = bytes([0x0A, 2]) + b"/d" + bytes([0x19]) + struct.pack("<d", -12.5)     # field 3, wire type 1
    assert protocol.decode_value(dval) == Value("/d", "d", -12.5)
    assert protocol.decode_value(protocol.encode_value("/p")) == Value("/p", "", None)


def test_value_list_and_rtvalue_round_trip():
    lst = protocol.encode_value_list([protocol.encode_value("/x", ival=1), protocol.encode_value("/y", bval=False)])
    assert protocol.decode_value_list(lst) == [Value("/x", "i", 1), Value("/y", "b", False)]
    blob = protocol.encode_meters([-12.0, None])
    rt = bytes([0x0A, 3]) + b"/m/" + bytes([0x12, len(blob)]) + blob
    assert protocol.decode_rtvalue(rt) == protocol.RTValue("/m/", blob)


@pytest.mark.parametrize("bad", [b"\x0a", b"\x0a\x05ab", b"\x80", b"\x08", b"\x0b", b"\x00\x01", b"\x0d\x01\x02", b"\xff" * 12])
def test_damaged_frames_raise_decode_error_and_nothing_else(bad):
    with pytest.raises(protocol.DecodeError):
        protocol.decode_container(bad)


def test_meter_block_all_the_shapes():
    """16 whole-dB values like the capture; -250 is empty; NaN, infinity and silly values are empty
    too; a block that is not whole float32s is refused; more than 16 is cut to 16."""
    vals = [-21.0, -8.0, -250.0] + [-12.0] * 13
    assert protocol.parse_meters(struct.pack("<16f", *vals)) == [-21.0, -8.0, None] + [-12.0] * 13
    odd = struct.pack("<4f", float("nan"), float("inf"), float("-inf"), 99.0)
    assert protocol.parse_meters(odd) == [None, None, None, None]
    assert protocol.parse_meters(struct.pack("<2f", 0.0, 30.0)) == [0.0, 30.0]
    assert protocol.parse_meters(struct.pack("<f", 30.5)) == [None]
    assert protocol.parse_meters(b"") is None and protocol.parse_meters(b"\x00\x00\x00") is None
    assert len(protocol.parse_meters(struct.pack("<20f", *([-10.0] * 20)))) == 16
    assert protocol.parse_meters("not bytes") is None


# -------------------------------------------------------------------- the read-only gate
def _all_frames():
    """Every kind of frame we could build, with every method: the allow-list must pass exactly the
    read-only ones."""
    payloads = {
        "Value": [protocol.encode_value(p) for p in ("/general/", "/controller/0/", "/controller/15/",
                                                      "/controller/0/faders/3/gain", "/controller/0/controls/layer",
                                                      "/controller/16/", "/other/")]
                 + [protocol.encode_value("/controller/0/faders/3/mute", bval=True),
                    protocol.encode_value("/controller/0/controls/layer", ival=1),
                    protocol.encode_value("/general/login/0", sval="x"),
                    protocol.encode_value("/general/login/16", sval="x"),
                    protocol.encode_value("/general/login/0")],
        "RTValue": [bytes([0x0A, len(p)]) + p for p in (b"/controller/0/meters", b"/controller/0/", b"/controller/99/meters")],
        "Ping": [protocol.encode_ping(), protocol.encode_ping("someone else")],
        "Action": [bytes([0x0A, 4]) + b"mute"],
        "Other": [b""],
    }
    for method in range(7):
        for name, plist in payloads.items():
            for p in plist:
                yield method, name, p


def test_only_read_only_frames_pass_the_gate_and_no_set_update_or_action_ever_does():
    passed = []
    for method, name, payload in _all_frames():
        frame = protocol.encode_container(method, name, payload)
        if protocol.allowed(frame):
            passed.append((method, name))
            assert method in (protocol.GET, protocol.SUBSCRIBE, protocol.UNSUBSCRIBE, protocol.AUTH)
        assert method not in (protocol.SET, protocol.UPDATE, protocol.ACTION) or not protocol.allowed(frame)
    assert set(passed) == {(protocol.GET, "Ping"), (protocol.GET, "Value"), (protocol.SUBSCRIBE, "Value"),
                           (protocol.UNSUBSCRIBE, "Value"), (protocol.SUBSCRIBE, "RTValue"),
                           (protocol.UNSUBSCRIBE, "RTValue"), (protocol.AUTH, "Value")}


def test_each_message_the_client_builds_is_allowed():
    for frame in (protocol.ping(), protocol.get_value("/general/"), protocol.get_value(protocol.controller_path(3)),
                  protocol.subscribe_value("/general/"), protocol.subscribe_value(protocol.controller_path(15)),
                  protocol.unsubscribe_value(protocol.controller_path(0)), protocol.subscribe_meters(0),
                  protocol.unsubscribe_meters(15), protocol.login(2, PW)):
        assert protocol.allowed(frame)


def test_the_gate_refuses_a_set_that_looks_like_a_get_and_other_tricks():
    set_gain = protocol.encode_container(protocol.SET, "Value", protocol.encode_value("/controller/0/faders/0/gain", ival=0))
    action = protocol.encode_container(protocol.ACTION, "Action", bytes([0x0A, 3]) + b"foo")
    update = protocol.encode_container(protocol.UPDATE, "Value", protocol.encode_value("/general/"))
    meters_as_value = protocol.encode_container(protocol.SUBSCRIBE, "Value", protocol.encode_value("/controller/0/meters"))
    get_with_value = protocol.encode_container(protocol.GET, "Value", protocol.encode_value("/general/", ival=1))
    login_wrong_path = protocol.encode_container(protocol.AUTH, "Value", protocol.encode_value("/controller/0/", sval="x"))
    for frame in (set_gain, action, update, meters_as_value, get_with_value, login_wrong_path, b"", b"\x00", "text", None, 5):
        assert protocol.allowed(frame) is False


# ---------------------------------------------------------------------------- the state mapping
def test_values_land_in_the_right_places_and_hostile_ones_are_ignored():
    st = mapping.GlobconState()
    good = [Value("/general/channelsNumber", "i", 16), Value("/general/name/0", "s", "FOH Desk"),
            Value("/general/requiresPassword/0", "b", False), Value("/controller/0/controls/layer", "i", 1),
            Value("/controller/0/controls/layerLabel/1", "s", "Monitors"), Value("/controller/0/faders/2/label", "s", "Flex Channel 3"),
            Value("/controller/0/faders/2/hasLevel", "b", True), Value("/controller/0/faders/9/hasLevel", "b", False)]
    assert all(mapping.apply_value(st, v) for v in good)
    c = st.controllers[0]
    assert (st.channels, c.name, c.layer, c.layer_labels, c.labels, c.has_level) == (
        16, "FOH Desk", 1, {1: "Monitors"}, {2: "Flex Channel 3"}, {2: True, 9: False})
    bad = [Value("/controller/16/faders/0/label", "s", "x"), Value("/controller/0/faders/16/label", "s", "x"),
           Value("/controller/0/faders/1/label", "i", 3), Value("/controller/0/controls/layer", "i", 12),
           Value("/controller/0/controls/layer", "i", -1), Value("/controller/0/controls/layer", "s", "1"),
           Value("/general/channelsNumber", "i", 17), Value("/general/name/99", "s", "x"),
           Value("/controller/0/faders/1/hasLevel", "s", "true"), Value("/controller/0/faders/1/gain", "d", 1.0),
           Value("/controller/0/controls/layerLabel/12", "s", "x"), Value("/nonsense", "s", "x")]
    assert not any(mapping.apply_value(st, v) for v in bad)
    assert 16 not in st.controllers and set(st.controllers) == {0}


def test_labels_are_cleaned_and_capped():
    st = mapping.GlobconState()
    mapping.apply_value(st, Value("/controller/0/faders/0/label", "s", "A‮B\x00C\n" + "x" * 200))
    label = st.controllers[0].labels[0]
    assert "‮" not in label and "\x00" not in label and "\n" not in label and len(label) <= mapping.LABEL_MAX


def test_meters_are_kept_as_received_and_a_bad_block_leaves_the_last_good_one():
    st = mapping.GlobconState()
    assert mapping.apply_meters(st, 0, struct.pack("<4f", -10.0, -250.0, -3.0, -20.0), 100.0)
    assert st.controllers[0].levels == [-10.0, None, -3.0, -20.0] and st.controllers[0].meters_at == 100.0
    assert not mapping.apply_meters(st, 0, b"\x01\x02\x03", 101.0)
    assert not mapping.apply_meters(st, 16, struct.pack("<f", -1.0), 101.0)
    assert st.controllers[0].levels == [-10.0, None, -3.0, -20.0] and st.controllers[0].meters_at == 100.0


def _loaded_state():
    st = mapping.GlobconState()
    mapping.apply_value(st, Value("/general/name/0", "s", "Controller 1"))
    mapping.apply_value(st, Value("/controller/0/controls/layer", "i", 0))
    mapping.apply_value(st, Value("/controller/0/controls/layerLabel/0", "s", "Inputs"))
    for i in range(16):
        mapping.apply_value(st, Value(f"/controller/0/faders/{i}/label", "s", f"Strip {i}"))
        mapping.apply_value(st, Value(f"/controller/0/faders/{i}/hasLevel", "b", i < 10 or i in (12,)))
    mapping.apply_meters(st, 0, struct.pack("<16f", *([-20.0] * 3 + [-250.0] + [-12.0] * 12)), 50.0)
    return st


def test_public_message_exact_shape_one_based_and_every_known_strip():
    msg = mapping.public_message(_loaded_state(), [0], True, 50.5)
    assert set(msg) == {"status", "label", "controllers"} and msg["status"] == "ok"
    (c,) = msg["controllers"]
    assert set(c) == {"controller", "name", "layer", "layer_label", "locked", "meters_at", "strips"}
    assert (c["controller"], c["name"], c["layer"], c["layer_label"], c["locked"], c["meters_at"]) == (1, "Controller 1", 0, "Inputs", False, 50.0)
    assert [s["index"] for s in c["strips"]] == list(range(16))                     # all 16, whether or not they have a level
    assert all(set(s) == {"index", "label", "meter", "db"} for s in c["strips"])
    assert [s["db"] for s in c["strips"]][:8] == [-20.0, -20.0, -20.0, None, -12.0, -12.0, -12.0, -12.0]   # -250 is None, never 0
    assert [s["meter"] for s in c["strips"]] == [i < 10 or i == 12 for i in range(16)]   # hasLevel as GLOBCON said
    assert c["strips"][10]["label"] == "Strip 10" and c["strips"][10]["meter"] is False   # labelled, no meter
    assert "password" not in json.dumps(msg) and "127.0.0.1" not in json.dumps(msg)


def test_a_controller_with_nothing_known_has_no_strips_and_unknown_meter_is_none():
    st = mapping.GlobconState()
    mapping.apply_value(st, Value("/controller/0/faders/3/label", "s", "Only label"))
    (c,) = mapping.public_message(st, [0], True, 1.0)["controllers"]
    assert c["strips"] == [{"index": 3, "label": "Only label", "meter": None, "db": None}]
    (c,) = mapping.public_message(st, [5], True, 1.0)["controllers"]
    assert c["strips"] == []


def test_status_waiting_ok_stale_offline_and_frozen_levels_are_kept():
    st = _loaded_state()
    assert mapping.public_message(st, [0], True, 50.0 + mapping.LEVEL_STALE_S)["status"] == "ok"          # exactly at the limit
    assert mapping.public_message(st, [0], True, 50.0 + mapping.LEVEL_STALE_S + 0.1)["status"] == "stale"  # one tick past
    off = mapping.public_message(st, [0], False, 90.0)
    assert off["status"] == "offline" and off["controllers"][0]["strips"][0]["db"] == -20.0              # frozen, not zeroed
    assert off["controllers"][0]["meters_at"] == 50.0
    assert mapping.public_message(mapping.GlobconState(), [0], True, 1.0)["status"] == "waiting"
    other = mapping.public_message(st, [5], True, 51.0)                                                  # a controller we never heard
    assert other["controllers"][0]["controller"] == 6 and other["controllers"][0]["strips"] == []


def test_a_controller_that_wants_a_password_is_marked_locked():
    st = mapping.GlobconState()
    mapping.apply_value(st, Value("/general/requiresPassword/2", "b", True))
    assert mapping.public_message(st, [2], True, 1.0)["controllers"][0]["locked"] is True
    mapping.apply_value(st, Value("/general/authorized/2", "b", True))
    assert mapping.public_message(st, [2], True, 1.0)["controllers"][0]["locked"] is False


# ---------------------------------------------------------- the real client, against a fake GLOBCON
class FakeGlobcon:
    """A WebSocket server that speaks enough GLOBCON to test the client. It records every frame it
    receives (decoded), and answers subscriptions like the capture: names and strip labels as an UPDATE
    ValueList, then meters about every 50 ms."""

    def __init__(self, *, requires_password=(), meters_every=0.05, send_meters=True, raw_frames=(), accept_login=True):
        self.port = free_port()
        self.frames: list[protocol.Container] = []
        self.raw: list[bytes] = []
        self.requires = set(requires_password)
        self.every, self.send_meters, self.pre = meters_every, send_meters, list(raw_frames)
        self.accept_login = accept_login
        self.logins: list[tuple[str, str]] = []
        self.connections = 0
        self._server = None
        self._sockets: set = set()
        self.layer_label = "Inputs"

    async def __aenter__(self):
        self._server = await serve(self._handler, "127.0.0.1", self.port, max_size=1 << 20)
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def drop(self):
        for ws in list(self._sockets):
            await ws.close()

    def ids(self, method, type_name):
        return [protocol.decode_value(c.payload).path if type_name == "Value" else protocol.decode_rtvalue(c.payload).path
                for c in self.frames if c.method == method and c.type_name == type_name]

    def _update(self, values):
        return protocol.encode_container(protocol.UPDATE, "ValueList", protocol.encode_value_list(values))

    def _general(self):
        vs = [protocol.encode_value("/general/channelsNumber", ival=16)]
        for n in range(16):
            vs.append(protocol.encode_value(f"/general/name/{n}", sval=f"Desk {n + 1}"))
            vs.append(protocol.encode_value(f"/general/requiresPassword/{n}", bval=n in self.requires))
        return self._update(vs)

    def _controller(self, n):
        vs = [protocol.encode_value(f"/controller/{n}/controls/layer", ival=0),
              protocol.encode_value(f"/controller/{n}/controls/layerLabel/0", sval=self.layer_label)]
        for i in range(16):
            vs.append(protocol.encode_value(f"/controller/{n}/faders/{i}/label", sval=f"Flex Channel {i + 1}"))
            vs.append(protocol.encode_value(f"/controller/{n}/faders/{i}/hasLevel", bval=i < 8))
        return self._update(vs)

    async def _meters(self, ws, n):
        t = 0
        while True:
            levels = [float(-10 - ((t + i) % 20)) for i in range(16)]
            levels[2] = -250.0
            rt = bytes([0x0A, len(f"/controller/{n}/meters")]) + f"/controller/{n}/meters".encode() \
                + bytes([0x12, 64]) + protocol.encode_meters(levels)
            await ws.send(protocol.encode_container(protocol.UPDATE, "RTValue", rt))
            t += 1
            await asyncio.sleep(self.every)

    async def _handler(self, ws):
        self.connections += 1
        self._sockets.add(ws)
        tasks: dict[int, asyncio.Task] = {}
        try:
            for frame in self.pre:
                await ws.send(frame)
            async for raw in ws:
                if isinstance(raw, str):
                    continue
                self.raw.append(raw)
                c = protocol.decode_container(raw)
                self.frames.append(c)
                if c.type_name == "Value":
                    v = protocol.decode_value(c.payload)
                    if c.method == protocol.SUBSCRIBE and v.path == "/general/":
                        await ws.send(self._general())
                    elif c.method == protocol.SUBSCRIBE and v.path.startswith("/controller/"):
                        await ws.send(self._controller(int(v.path.split("/")[2])))
                    elif c.method == protocol.AUTH:
                        self.logins.append((v.path, v.value))
                        await ws.send(protocol.encode_container(protocol.AUTH, "Value", protocol.encode_value(
                            f"/general/authorized/{v.path.rsplit('/', 1)[-1]}", bval=self.accept_login)))
                elif c.type_name == "RTValue":
                    n = int(protocol.decode_rtvalue(c.payload).path.split("/")[2])
                    if c.method == protocol.SUBSCRIBE and self.send_meters and n not in tasks:
                        tasks[n] = asyncio.create_task(self._meters(ws, n))
                    elif c.method == protocol.UNSUBSCRIBE and n in tasks:
                        tasks.pop(n).cancel()
                elif c.type_name == "Ping":
                    await ws.send(protocol.encode_container(protocol.GET, "Pong", protocol._enc_len(1, b"globcon")))
        except Exception:
            pass
        finally:
            for t in tasks.values():
                t.cancel()
            self._sockets.discard(ws)


class Sink:
    def __init__(self):
        self.values: list[Value] = []
        self.meters: list[tuple[int, bytes]] = []
        self.links: list[tuple[bool, str]] = []

    def on_values(self, vs):
        self.values.extend(vs)

    def on_meters(self, n, blob):
        self.meters.append((n, blob))

    def on_link(self, up, detail):
        self.links.append((up, detail))


def make_client(port, sink, password="", **kw):
    return GlobconClient(lambda: ("127.0.0.1", port), sink.on_values, sink.on_meters, sink.on_link,
                         password_fn=lambda: password, backoff_min_s=0.05, backoff_max_s=0.2, **kw)


async def test_client_subscribes_to_what_it_needs_and_receives_names_labels_and_levels():
    async with FakeGlobcon() as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        cl.set_wanted([0, 3])
        await cl.start()
        try:
            await until(lambda: {n for n, _b in sink.meters} == {0, 3} and len(sink.meters) > 4)
            paths = {v.path: v.value for v in sink.values}
            assert paths["/general/name/0"] == "Desk 1" and paths["/controller/3/faders/1/label"] == "Flex Channel 2"
            assert (True, TEXT["connected"]) in sink.links
            assert srv.ids(protocol.SUBSCRIBE, "Value") == ["/general/", "/controller/0/", "/controller/3/"]
            assert srv.ids(protocol.SUBSCRIBE, "RTValue") == ["/controller/0/meters", "/controller/3/meters"]
            assert srv.ids(protocol.GET, "Value")[:1] == ["/general/"]
            blob = next(b for n, b in sink.meters if n == 0)
            assert len(blob) == 64 and protocol.parse_meters(blob)[2] is None       # -250 -> empty
            await until(lambda: any(c.type_name == "Ping" for c in srv.frames))
        finally:
            await cl.stop()


async def test_client_never_sends_anything_but_read_only_methods():
    """The pin: after a full session with subscribe, unsubscribe, ping and a log-in, every frame the
    server received is one the gate allows, and the only methods seen are GET, SUBSCRIBE, UNSUBSCRIBE and AUTH."""
    async with FakeGlobcon(requires_password=(0,)) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, password=PW)
        cl.set_wanted([0, 1])
        await cl.start()
        try:
            await until(lambda: srv.logins and len(sink.meters) > 3)
            cl.set_wanted([1])
            await until(lambda: srv.ids(protocol.UNSUBSCRIBE, "RTValue") == ["/controller/0/meters"])
            await until(lambda: any(c.type_name == "Ping" for c in srv.frames))
        finally:
            await cl.stop()
        assert srv.raw and all(protocol.allowed(f) for f in srv.raw)
        assert {c.method for c in srv.frames} <= {protocol.GET, protocol.SUBSCRIBE, protocol.UNSUBSCRIBE, protocol.AUTH}
        assert not ({c.method for c in srv.frames} & {protocol.SET, protocol.UPDATE, protocol.ACTION})
        assert set(cl.sent_methods) <= {"GET", "SUBSCRIBE", "UNSUBSCRIBE", "AUTH"}


async def test_the_send_gate_in_the_client_refuses_a_set_even_if_something_tries():
    class Ws:
        sent: list = []

        async def send(self, frame):
            self.sent.append(frame)

    ws = Ws()
    cl = make_client(1, Sink())
    for method, name, payload in [(protocol.SET, "Value", protocol.encode_value("/controller/0/faders/0/mute", bval=True)),
                                  (protocol.UPDATE, "Value", protocol.encode_value("/controller/0/faders/0/mute", bval=True)),
                                  (protocol.ACTION, "Action", bytes([0x0A, 3]) + b"foo")]:
        with pytest.raises(RuntimeError):
            await cl._send(ws, protocol.encode_container(method, name, payload))
    assert ws.sent == []
    await cl._send(ws, protocol.ping())
    assert len(ws.sent) == 1


async def test_login_only_when_a_password_is_set_and_asked_for_and_never_twice():
    async with FakeGlobcon(requires_password=(0,), accept_login=False) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, password=PW)
        cl.set_wanted([0])
        await cl.start()
        try:
            await until(lambda: srv.logins)
            await asyncio.sleep(0.4)
            assert srv.logins == [("/general/login/0", PW)]                       # refused: not tried again
        finally:
            await cl.stop()
    async with FakeGlobcon(requires_password=(0,)) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, password="")
        cl.set_wanted([0])
        await cl.start()
        try:
            await until(lambda: len(sink.meters) > 2)
            assert srv.logins == [] and protocol.AUTH not in {c.method for c in srv.frames}
        finally:
            await cl.stop()
    async with FakeGlobcon() as srv:                                              # no password wanted: a set one is not sent
        sink = Sink()
        cl = make_client(srv.port, sink, password=PW)
        cl.set_wanted([0])
        await cl.start()
        try:
            await until(lambda: len(sink.meters) > 2)
            assert srv.logins == []
        finally:
            await cl.stop()


async def test_client_reconnects_after_a_drop_and_reports_the_link(caplog):
    caplog.set_level(logging.DEBUG)
    async with FakeGlobcon() as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, password=PW)
        cl.set_wanted([0])
        await cl.start()
        try:
            await until(lambda: len(sink.meters) > 2)
            before = len(sink.meters)
            await srv.drop()
            await until(lambda: any(not up for up, _d in sink.links))
            await until(lambda: srv.connections >= 2 and len(sink.meters) > before + 2)
            assert sink.links[-1][0] is True
        finally:
            await cl.stop()
    assert PW not in caplog.text


async def test_client_to_nothing_reports_down_with_fixed_text_and_no_address():
    sink = Sink()
    cl = make_client(free_port(), sink)
    cl.set_wanted([0])
    await cl.start()
    try:
        await until(lambda: sink.links)
        assert sink.links[0] == (False, TEXT["unreachable"])
        assert "127.0.0.1" not in sink.links[0][1]
    finally:
        await cl.stop()


async def test_client_with_no_address_says_so():
    sink = Sink()
    cl = GlobconClient(lambda: None, sink.on_values, sink.on_meters, sink.on_link, backoff_min_s=0.05, backoff_max_s=0.1)
    await cl.start()
    try:
        await until(lambda: sink.links)
        assert sink.links[0] == (False, TEXT["no_address"])
    finally:
        await cl.stop()


async def test_hostile_frames_are_dropped_and_the_connection_survives():
    junk = [b"\xff\xff\xff", b"", protocol.encode_container(protocol.UPDATE, "Value", b"\x0a\xff"),
            protocol.encode_container(protocol.UPDATE, "RTValue", b"\x0a\x03abc"),
            protocol.encode_container(protocol.UPDATE, "Mystery", b"\x00"),
            protocol.encode_container(protocol.UPDATE, "RTValue", bytes([0x0A, 20]) + b"/controller/0/meters" + bytes([0x12, 3]) + b"abc")]
    async with FakeGlobcon(raw_frames=junk) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        cl.set_wanted([0])
        await cl.start()
        try:
            await until(lambda: len(sink.meters) > 3 and sink.values)
            assert srv.connections == 1
        finally:
            await cl.stop()


async def test_an_oversize_frame_closes_the_connection_not_the_hub():
    async with FakeGlobcon(raw_frames=[b"\x00" * (300 * 1024)]) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        cl.set_wanted([0])
        await cl.start()
        try:
            await until(lambda: srv.connections >= 2)
            assert any(not up for up, _d in sink.links)
        finally:
            await cl.stop()


async def test_a_silent_server_is_dropped_after_the_silence_limit():
    async def mute(ws):
        async for _ in ws:
            pass
    port = free_port()
    async with serve(mute, "127.0.0.1", port):
        sink = Sink()
        cl = make_client(port, sink, silence_s=0.4)
        cl.set_wanted([0])
        await cl.start()
        try:
            await until(lambda: (False, TEXT["silent"]) in sink.links)
        finally:
            await cl.stop()


def test_addresses_this_computer_and_local_networks_are_fine_public_ones_are_not():
    import ipaddress
    for ok in ("127.0.0.1", "::1", "192.168.1.20", "10.0.0.5", "169.254.1.1"):
        assert is_acceptable_address(ipaddress.ip_address(ok))
    for bad in ("8.8.8.8", "224.0.0.1", "0.0.0.0", "::ffff:8.8.8.8", "2001:4860:4860::8888"):
        assert not is_acceptable_address(ipaddress.ip_address(bad))
    assert ws_url("127.0.0.1", 9091) == "ws://127.0.0.1:9091/api/v1" and ws_url("::1", 9091) == "ws://[::1]:9091/api/v1"


# -------------------------------------------------------------------------------------- emulate
async def test_emulated_source_gives_names_labels_levels_and_a_dropout():
    sink = Sink()
    src = EmulatedGlobconSource(sink.on_values, sink.on_meters, sink.on_link, meter_hz=100, dropout_every_s=0)
    src.set_wanted([0, 1])
    await src.start()
    try:
        await until(lambda: len(sink.meters) > 6)
        names = {v.path: v.value for v in sink.values}
        assert names["/general/name/1"] == "Controller 2" and names["/controller/0/faders/2/label"] == "Flex Channel 3"
        assert names["/controller/0/faders/8/hasLevel"] is False                     # USB strips have no level
        levels = protocol.parse_meters(next(b for n, b in sink.meters if n == 0))
        assert len(levels) == 16 and all(v is None or -72 <= v <= 0 for v in levels) and levels[8] is None
        src.dropout(0.3)
        await until(lambda: (False, "Simulated dropout") in sink.links)
        await until(lambda: sink.links[-1][0] is True, timeout=3)
    finally:
        await src.stop()


def test_level_block_has_whole_db_and_no_signal_for_unused_strips():
    for t in (0.0, 3.3, 10.0, 21.5):
        vals = protocol.parse_meters(level_block(0, t))
        assert len(vals) == 16 and all(v is None or v == round(v) for v in vals)
        assert all(v is None for v in vals[8:])


# ------------------------------------------------------------------- through the hub (emulate)
@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path, emulate=True)
    yield h
    h.recorder.close()


def give_card(hub, slug="foh", controller=1, strips=8):
    d = hub.config.dashboard(slug)
    d.cards = [*d.cards, CARD_ID]
    d.globcon = GlobconCardOptions(controller=controller, strips=strips)


async def test_no_card_means_nothing_runs_and_no_device(hub):
    integ = GlobconIntegration(hub, emulate=True)
    hub.add_integration(integ)
    await integ.start()
    try:
        assert integ.snapshot() is None and "globcon" not in hub.devices
        assert hub.snapshot()["globcon_meters"] is None
        assert integ.admin_status()["running"] is False and integ.admin_status()["card_assigned"] is False
    finally:
        await integ.stop()


async def test_emulate_card_runs_through_the_hub_and_publishes_slowly(hub):
    give_card(hub, controller=1)
    integ = GlobconIntegration(hub, emulate=True, source_factory=lambda o: EmulatedGlobconSource(
        o._values, o._meters, o._link, meter_hz=40, dropout_every_s=0))
    hub.add_integration(integ)
    got: list[dict] = []
    hub.bus.subscribe("globcon_meters", lambda _t, m: got.append(m))
    await integ.start()
    try:
        await until(lambda: hub.devices.get("globcon") and hub.devices["globcon"].status == Status.OK, timeout=5)
        dev = hub.devices["globcon"]
        assert dev.role == "equipment" and dev.category == "service" and "simulated" in dev.name.lower()
        n0 = len(got)
        await asyncio.sleep(1.0)
        sent = len(got) - n0
        assert 1 <= sent <= 5                          # ~4 a second to browsers, while the source makes 40 a second
        snap = hub.snapshot()["globcon_meters"]
        assert snap["status"] == "ok" and snap["label"] == "Simulated GLOBCON"
        (c,) = snap["controllers"]
        assert (c["controller"], c["name"], c["layer_label"]) == (1, "Controller 1", "Inputs")
        assert [s["label"] for s in c["strips"]][:3] == ["Input Manager #1", "Input Manager #2", "Flex Channel 3"]
        assert len(c["strips"]) == 16 and [s["meter"] for s in c["strips"]] == [True] * 8 + [False] * 8   # USB strips: no meter
        assert all(s["db"] is None for s in c["strips"][8:])
        assert hub.entities.get("globcon") is None and not [e for e in hub.entities.values() if e.device_id == "globcon"]
    finally:
        await integ.stop()


async def test_a_dropout_freezes_the_levels_goes_missing_quietly_then_recovers(hub):
    give_card(hub)
    holder = {}

    def factory(o):
        holder["src"] = EmulatedGlobconSource(o._values, o._meters, o._link, meter_hz=40, dropout_every_s=0)
        return holder["src"]

    integ = GlobconIntegration(hub, emulate=True, source_factory=factory)
    hub.add_integration(integ)
    await integ.start()
    try:
        await until(lambda: hub.devices.get("globcon") and hub.devices["globcon"].status == Status.OK)
        holder["src"].dropout(0.6)
        await until(lambda: hub.devices["globcon"].status == Status.MISSING)
        frozen = integ.snapshot()
        assert frozen["status"] == "offline"
        strips = frozen["controllers"][0]["strips"]
        assert any(s["db"] is not None for s in strips) and frozen["controllers"][0]["meters_at"] is not None   # frozen, not blanked or zeroed
        assert all(s["db"] != 0 for s in strips)
        alarm = hub.alarms.active.get("device:globcon") if hasattr(hub.alarms, "active") else None
        assert alarm is None or alarm.silent
        await until(lambda: hub.devices["globcon"].status == Status.OK, timeout=5)
    finally:
        await integ.stop()


async def test_removing_the_card_stops_the_source_and_removes_the_device(hub):
    give_card(hub)
    integ = GlobconIntegration(hub, emulate=True)
    hub.add_integration(integ)
    await integ.start()
    try:
        await until(lambda: "globcon" in hub.devices)
        d = hub.config.dashboard("foh")
        d.cards = [c for c in d.cards if c != CARD_ID]
        await integ.apply()
        assert "globcon" not in hub.devices and integ.snapshot() is None and integ._source is None
    finally:
        await integ.stop()


async def test_two_dashboards_on_different_controllers_share_one_connection(hub):
    give_card(hub, "foh", controller=2, strips=4)
    give_card(hub, "wall", controller=5, strips=8)
    assert wanted_controllers(hub.config) == [1, 4]
    seen = {}

    def factory(o):
        src = EmulatedGlobconSource(o._values, o._meters, o._link, meter_hz=40, dropout_every_s=0)
        seen["src"] = src
        return src

    integ = GlobconIntegration(hub, emulate=True, source_factory=factory)
    hub.add_integration(integ)
    await integ.start()
    try:
        await until(lambda: (m := integ.snapshot()) and m["status"] == "ok" and len(m["controllers"]) == 2
                    and all(c["strips"] for c in m["controllers"]))
        assert [c["controller"] for c in integ.snapshot()["controllers"]] == [2, 5]
        assert seen["src"]._wanted == [1, 4]
    finally:
        await integ.stop()


async def test_live_only_nothing_is_written_to_the_record(hub):
    give_card(hub)
    integ = GlobconIntegration(hub, emulate=True, source_factory=lambda o: EmulatedGlobconSource(
        o._values, o._meters, o._link, meter_hz=40, dropout_every_s=0))
    hub.add_integration(integ)
    before = len(hub.recorder.markers())
    await integ.start()
    try:
        await until(lambda: hub.devices.get("globcon") and hub.devices["globcon"].status == Status.OK)
        assert len(hub.recorder.markers()) == before
        assert not [k for k in hub.entities if k.startswith("globcon")]
    finally:
        await integ.stop()


async def test_real_client_through_the_integration_and_the_hub(hub):
    async with FakeGlobcon() as srv:
        hub.config.globcon = GlobconConfig(host="127.0.0.1", port=srv.port)
        give_card(hub, controller=3, strips=4)
        hub.emulate = False
        integ = GlobconIntegration(hub)
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: (m := integ.snapshot()) and m["status"] == "ok" and m["controllers"][0]["strips"])
            (c,) = integ.snapshot()["controllers"]
            assert (c["controller"], c["name"]) == (3, "Desk 3")
            assert [s["label"] for s in c["strips"]][:2] == ["Flex Channel 1", "Flex Channel 2"]
            assert c["strips"][2]["db"] is None and c["strips"][0]["db"] is not None      # strip 3 sent -250
            await until(lambda: hub.devices["globcon"].status == Status.OK)
            dev = hub.devices["globcon"]
            assert dev.status == Status.OK and "not yet tested against a live GLOBCON" in dev.status_detail
            assert srv.ids(protocol.SUBSCRIBE, "RTValue") == ["/controller/2/meters"]
            assert integ.admin_status()["verified"] is False
            await srv.drop()
            await until(lambda: hub.devices["globcon"].status == Status.MISSING)
            assert integ.snapshot()["status"] == "offline"
            await until(lambda: hub.devices["globcon"].status == Status.OK, timeout=8)
        finally:
            await integ.stop()
            hub.emulate = True


async def test_a_layer_change_on_globcon_changes_the_header_live(hub):
    async with FakeGlobcon() as srv:
        hub.config.globcon = GlobconConfig(host="127.0.0.1", port=srv.port)
        give_card(hub)
        hub.emulate = False
        integ = GlobconIntegration(hub)
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: (m := integ.snapshot()) and m["controllers"][0]["layer_label"] == "Inputs")
            integ._values([Value("/controller/0/controls/layer", "i", 1),
                           Value("/controller/0/controls/layerLabel/1", "s", "Monitors")])
            assert integ.snapshot()["controllers"][0]["layer_label"] == "Monitors"
        finally:
            await integ.stop()
            hub.emulate = True


def test_seed_puts_the_card_on_the_wall_only_for_a_fresh_emulate_config():
    cfg = Config()
    assert seed_emulate_globcon(cfg) is True
    assert CARD_ID in cfg.dashboard("wall").cards and CARD_ID not in cfg.dashboard("foh").cards
    assert seed_emulate_globcon(cfg) is False
    saved = Config(globcon=GlobconConfig())
    assert seed_emulate_globcon(saved) is False


# -------------------------------------------------------------------------------------- config
def test_defaults_and_additive_loading_of_an_older_file(tmp_path):
    assert GlobconConfig().host == "127.0.0.1" and GlobconConfig().port == 9091 and GlobconConfig().password == ""
    assert Dashboard(slug="x").globcon == GlobconCardOptions(controller=1, strips=8)
    assert Dashboard(slug="x").globcon.range is None                  # unset: the older behaviour is kept
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = Config()
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.pop("globcon", None)                                    # what 0.3.x wrote
    for row in raw["dashboards"]:
        row.pop("globcon", None)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    loaded = ConfigStore(path).load()
    assert loaded.globcon == GlobconConfig() and [d.globcon.controller for d in loaded.dashboards] == [1, 1, 1]
    assert [d.cards for d in loaded.dashboards] == [d.cards for d in Config().dashboards]


def test_an_unset_password_stays_out_of_the_saved_file(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = Config()
    store.save()
    assert "password" not in yaml.safe_load(path.read_text(encoding="utf-8"))["globcon"]
    store.config.globcon.password = PW
    store.save()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["globcon"]["password"] == PW


def test_damaged_globcon_settings_reset_only_themselves(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    cfg = Config()
    cfg.site.name = "Kept venue"
    cfg.dashboards[0].globcon = GlobconCardOptions(controller=7, strips=4)
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["globcon"] = {"host": "8.8.8.8", "port": 9091, "password": "p" * 500}
    raw["dashboards"][0]["globcon"] = {"controller": 99, "strips": "lots"}
    raw["dashboards"][1]["globcon"] = "nonsense"
    raw["dashboards"][2]["globcon"] = {"controller": 12, "strips": 4}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    store2 = ConfigStore(path)
    loaded = store2.load()
    assert not store2.recovery_required and loaded.site.name == "Kept venue" and len(loaded.dashboards) == 3
    assert loaded.globcon.host == "" and loaded.globcon.password == ""
    assert loaded.dashboards[0].globcon == GlobconCardOptions() and loaded.dashboards[1].globcon == GlobconCardOptions()
    assert loaded.dashboards[2].globcon == GlobconCardOptions(controller=12, strips=4)
    assert "8.8.8.8" not in caplog.text and "ppppp" not in caplog.text


def test_card_options_validation_bounds():
    for c, s in ((0, 8), (17, 8), (1, 5), (1, 0), (True, 8)):
        assert GlobconCardOptions.model_validate({"controller": c, "strips": s}) == GlobconCardOptions()
    assert GlobconCardOptions.model_validate({"controller": 16, "strips": 4}) == GlobconCardOptions(controller=16, strips=4)
    assert GlobconCardOptions.model_validate("junk") == GlobconCardOptions()


@pytest.mark.parametrize("r", ["1-4", "5-8", "1-8", "9-12", "13-16", "9-16", "1-16"])
def test_each_range_is_accepted_and_survives_a_save_and_load(tmp_path, r):
    cfg = Config()
    cfg.dashboards[0].globcon = GlobconCardOptions(controller=2, strips=8, range=r)
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["dashboards"][0]["globcon"]["range"] == r
    assert ConfigStore(path).load().dashboards[0].globcon == GlobconCardOptions(controller=2, strips=8, range=r)
    assert GLOBCON_RANGES[r][1] - GLOBCON_RANGES[r][0] + 1 in (4, 8, 16)


def test_the_ranges_are_the_groups_the_owner_asked_for():
    assert GLOBCON_RANGES == {"1-4": (1, 4), "5-8": (5, 8), "1-8": (1, 8), "9-12": (9, 12), "13-16": (13, 16),
                              "9-16": (9, 16), "1-16": (1, 16)}


def test_a_bad_range_in_a_file_falls_back_to_unset_and_keeps_the_rest(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    for bad in ("17-20", "1-9", 8, ["1-4"], True, "", "9-16 "):
        assert GlobconCardOptions.model_validate({"controller": 3, "strips": 4, "range": bad}) == GlobconCardOptions(controller=3, strips=4)
    assert "17-20" not in caplog.text
    cfg = Config()
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["dashboards"][0]["globcon"] = {"controller": 5, "strips": 4, "range": "nonsense"}
    raw["dashboards"][1]["globcon"] = {"controller": 6, "strips": 8, "range": "9-16"}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    store2 = ConfigStore(path)
    loaded = store2.load()
    assert not store2.recovery_required
    assert loaded.dashboards[0].globcon == GlobconCardOptions(controller=5, strips=4)
    assert loaded.dashboards[1].globcon == GlobconCardOptions(controller=6, strips=8, range="9-16")


def test_an_older_build_ignores_range_and_forgets_it_but_the_old_fields_stay(tmp_path):
    from pydantic import BaseModel, ConfigDict

    class OldOptions(BaseModel):                         # what the previous build knew: no range
        model_config = ConfigDict(extra="ignore")
        controller: int = 1
        strips: int = 8

    cfg = Config()
    cfg.dashboards[0].globcon = GlobconCardOptions(controller=4, strips=4, range="13-16")
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    old = OldOptions.model_validate(raw["dashboards"][0]["globcon"])        # the older build reads it without complaint
    assert (old.controller, old.strips) == (4, 4)
    raw["dashboards"][0]["globcon"] = old.model_dump()                     # ... and its next save drops range
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    again = ConfigStore(path).load()
    assert again.dashboards[0].globcon == GlobconCardOptions(controller=4, strips=4)      # old behaviour, still working


# ------------------------------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path, emulate=True)
    h.add_integration(GlobconIntegration(h, emulate=True))
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


def test_the_card_is_known_and_never_added_by_default(client):
    from stagewatch.core import cards
    assert CARD_ID in cards.KNOWN_CARDS and CARD_ID not in {c for v in cards.LAYOUT_DEFAULTS.values() for c in v}
    assert CARD_ID in client.get("/api/admin/state").json()["cards"]["known"]


def test_dashboard_put_round_trips_options_and_is_strict(client):
    rows = client.get("/api/info").json()["dashboards"]
    rows[0]["cards"] = [*rows[0]["cards"], CARD_ID]
    rows[0]["globcon"] = {"controller": 9, "strips": 4}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    assert client.hub.config.dashboards[0].globcon == GlobconCardOptions(controller=9, strips=4)
    assert client.get("/api/dashboard/foh").json()["globcon"] == {"controller": 9, "strips": 4, "range": None}
    rows[0]["globcon"] = {"controller": 2, "strips": 8, "range": "9-16"}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    assert client.get("/api/dashboard/foh").json()["globcon"] == {"controller": 2, "strips": 8, "range": "9-16"}
    rows[0]["globcon"] = {"controller": 2, "strips": 8, "range": None}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    for bad_range in ("9-17", 9, "", ["1-4"], True):
        rows[0]["globcon"] = {"controller": 2, "strips": 8, "range": bad_range}
        assert client.put("/api/admin/dashboards", json=rows).status_code == 422
    rows[0]["globcon"] = {"controller": 9, "strips": 4}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    for bad in ({"controller": 0, "strips": 4}, {"controller": 17, "strips": 4}, {"controller": 1, "strips": 6},
                {"controller": "2", "strips": 4}, {"controller": 1.5, "strips": 4}, {"controller": True, "strips": 4},
                {"controller": 1, "strips": 4, "extra": 1}, [], "x", None):
        r = client.put("/api/admin/dashboards", json=[{**rows[0], "globcon": bad}] + rows[1:])
        assert r.status_code == 422, bad
    assert client.hub.config.dashboards[0].globcon == GlobconCardOptions(controller=9, strips=4)
    without = [{k: v for k, v in row.items() if k != "globcon"} for row in rows]            # an older admin page
    assert client.put("/api/admin/dashboards", json=without).status_code == 200
    assert client.hub.config.dashboards[0].globcon == GlobconCardOptions(controller=9, strips=4)
    new = client.put("/api/admin/dashboards", json=without + [{"slug": "extra"}])
    assert new.status_code == 200 and client.hub.config.dashboard("extra").globcon == GlobconCardOptions()


def test_settings_put_needs_admin_same_origin_and_valid_input(client):
    body = {"host": "127.0.0.1", "port": 9091}
    anon = TestClient(client.app)
    assert anon.put("/api/admin/globcon", json=body).status_code == 401
    r = client.put("/api/admin/globcon", json=body, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    for bad in ({"host": "", "port": 9091}, {"host": "8.8.8.8", "port": 9091}, {"host": "134744072", "port": 9091},
                {"host": "a b", "port": 9091}, {"host": "127.0.0.1", "port": 0}, {"host": "127.0.0.1", "port": "9091"},
                {"host": "127.0.0.1", "port": 9091, "extra": 1}, {"host": "x" * 300, "port": 9091},
                {"host": "127.0.0.1", "port": 9091, "password": "p\nq"},
                {"host": "127.0.0.1", "port": 9091, "password": PW, "clear_password": True}):
        r = client.put("/api/admin/globcon", json=bad)
        assert r.status_code in (413, 422), bad
        assert PW not in r.text and "8.8.8.8" not in r.text
    assert client.hub.config.globcon.password == ""


def test_password_is_write_only_and_never_leaks(client, caplog):
    caplog.set_level(logging.DEBUG)
    r = client.put("/api/admin/globcon", json={"host": "192.168.50.9", "port": 9092, "password": PW})
    assert r.status_code == 200 and r.json() == {"ok": True, "changed": True, "password_set": True}
    assert PW not in r.text
    state = client.get("/api/admin/state")
    assert PW not in state.text and "password" not in state.json()["config"]["globcon"]
    assert state.json()["globcon"]["password_set"] is True and state.json()["config"]["globcon"]["host"] == "192.168.50.9"
    for url in ("/api/snapshot", "/api/info", "/api/dashboard/foh"):
        assert PW not in client.get(url).text and "192.168.50.9" not in client.get(url).text
    r = client.put("/api/admin/globcon", json={"host": "192.168.50.9", "port": 9092})           # empty = keep
    assert r.json()["password_set"] is True and client.hub.config.globcon.password == PW
    r = client.put("/api/admin/globcon", json={"host": "192.168.50.9", "port": 9092, "clear_password": True})
    assert r.json()["password_set"] is False and client.hub.config.globcon.password == ""
    assert PW not in caplog.text


def test_public_snapshot_carries_only_the_agreed_fields(client):
    rows = client.get("/api/info").json()["dashboards"]
    rows[0]["cards"] = [*rows[0]["cards"], CARD_ID]
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        snap = client.get("/api/snapshot").json()["globcon_meters"]
        if snap and snap["controllers"] and snap["controllers"][0]["strips"]:
            break
        time.sleep(0.1)
    assert snap and set(snap) == {"status", "label", "controllers"}
    assert set(snap["controllers"][0]) == {"controller", "name", "layer", "layer_label", "locked", "meters_at", "strips"}
    text = json.dumps(snap)
    for forbidden in ("127.0.0.1", "9091", "password", "host", "port"):
        assert forbidden not in text


def test_gain_fader_and_mute_values_never_reach_the_public_message():
    st = mapping.GlobconState()
    for v in (Value("/controller/0/faders/0/gain", "d", -3.0), Value("/controller/0/faders/0/mute", "b", True),
              Value("/controller/0/faders/0/solo", "b", True), Value("/controller/0/faders/0/hasLevel", "b", True)):
        mapping.apply_value(st, v)
    text = json.dumps(mapping.public_message(st, [0], True, 1.0))
    assert "gain" not in text and "mute" not in text and "solo" not in text


# ------------------------------------------------------------------------------------- static
def test_page_has_the_card_and_loads_the_script_before_the_dashboard():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    order = [html.index(f"/static/{n}.js") for n in ("common", "globcon", "dashboard")]
    assert order == sorted(order)
    assert html.count('data-card="globcon_meters"') == 1 and 'id="globcon-card"' in html


def test_script_uses_only_safe_dom_and_makes_no_requests():
    js = re.sub(r"//.*", "", (STATIC / "globcon.js").read_text(encoding="utf-8"))
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "requestAnimationFrame", "fetch(", "XMLHttpRequest", "toLocale", "Intl."):
        assert bad not in js
    assert "http://" not in js and "https://" not in js


def test_admin_names_the_card_and_has_the_settings():
    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert "globcon_meters:" in js and "/api/admin/globcon" in js and "GLOBCON controller" in js
    assert "Not yet tested on a real GLOBCON" in js


def test_only_the_client_module_opens_a_socket_and_only_through_the_gate():
    src = ROOT / "src" / "stagewatch" / "integrations" / "globcon"
    for p in src.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        if p.name != "client.py":
            assert ".send(" not in text.replace("_send(", "")
    client_py = (src / "client.py").read_text(encoding="utf-8")
    assert client_py.count("await ws.send(") == 1 and "protocol.allowed(frame)" in client_py
    for name in ("SET", "UPDATE", "ACTION"):
        assert f"protocol.{name}" not in client_py


def test_node_view_logic():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "globcon_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
