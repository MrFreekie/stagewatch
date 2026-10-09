"""The Smaart client: message parsing, the four-message allow-list, and the real client run against a
fake Smaart (a WebSocket server in integrations/smaart/emulate.py).

The Smaart message shapes were read from the script Smaart's own SPL web page uses. They are NOT tested
against a live Smaart and no real data message has been seen, so every message below is SYNTHETIC: made
up to match those notes. Numbers must come out exactly as sent; a missing, flagged-overload or damaged
value is "not available" (never zero); the client sends only the four allowed messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPStatus
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from stagewatch.core import spl
from stagewatch.core.config import SplConfig, SplSlot
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.core.spl import SplReading
from stagewatch.integrations.smaart import SmaartIntegration, mapping, outbound
from stagewatch.integrations.smaart.client import TEXT, SmaartSource, ws_url
from stagewatch.integrations.smaart.emulate import (
    DEVICE_NAME, INPUT_LABELS, FakeSmaartServer, stream_message)
from test_ontime import free_port, until

ROOT = Path(__file__).resolve().parent.parent
SMAART = ROOT / "src" / "stagewatch" / "integrations" / "smaart"
PW = "Sm4art-Zq7-secret"
PROBE_PATH = "/api/v4/"
A, C, LEQ1, LEQ10 = "SPL A Slow", "SPL C Slow", "LAeq 1", "LAeq 10"


def inputs_doc(devices=None, metrics=(A, C, "FS Peak")) -> dict:
    devices = devices if devices is not None else [{"deviceName": DEVICE_NAME, "activeCalibratedChannels": [
        {"channelName": "Channel 7 (1)", "streamEndpoint": "/stream/0", "logEndpointPrefix": "/log/0"}]}]
    return {"response": {"devices": devices, "metrics": list(metrics)}}


# --------------------------------------------------------------------------- parsing
def test_probe_and_login_replies():
    assert mapping.parse_probe_reply({"response": {"authenticationRequired": True}}) is True
    assert mapping.parse_probe_reply({"response": {"authenticationRequired": False}}) is False
    assert mapping.parse_probe_reply({"response": {}}) is False          # tolerant: no flag = no password asked
    assert mapping.parse_probe_reply({"response": {"authenticationRequired": "yes"}}) is False   # not a real bool: not "required"
    for not_it in (None, [], 5, "x", {}, {"response": 5}, {"response": []}, {"nothing": 1}):
        assert mapping.parse_probe_reply(not_it) is None and not mapping.is_probe_reply(not_it)
    assert mapping.parse_auth_reply({"response": {"status": True}}) is True
    assert mapping.parse_auth_reply({"response": {"status": False}}) is False
    assert mapping.parse_auth_reply({"response": {"status": 0}}) is False
    assert mapping.parse_auth_reply({"response": {"authenticationRequired": True}}) is False   # asked again = not accepted
    assert mapping.parse_auth_reply({"response": {}}) is False
    assert mapping.parse_auth_reply([]) is None and mapping.parse_auth_reply({"x": 1}) is None


def test_inputs_reply_gives_device_colon_channel_endpoints_and_smaarts_own_metric_names():
    doc = {"response": {
        "devices": [{"deviceName": "ASIO MADIface USB", "activeCalibratedChannels": [
            {"channelName": "Channel 7 (1)", "streamEndpoint": "/stream/a", "logEndpointPrefix": "/log/a",
             "alarms": [{"level": "red", "metric": "SPL A Slow"}]},
            {"channelName": "Channel 8 (2)", "streamEndpoint": "/stream/b"}]}],
        "metrics": ["SPL A Slow", "FS Peak", "SPL C Slow", "LAeq 1", "LAeq 10", "SPL A Slow"],
        "colorThresholds": [{"greenAboveLevel": 0}]}}
    cat = mapping.parse_inputs_reply(doc)
    assert [(i.label, i.endpoint) for i in cat.inputs] == [
        ("ASIO MADIface USB : Channel 7 (1)", "/stream/a"), ("ASIO MADIface USB : Channel 8 (2)", "/stream/b")]
    assert cat.metrics == ("SPL A Slow", "SPL C Slow", "LAeq 1", "LAeq 10")      # FS Peak dropped, once each, Smaart's order
    assert mapping.parse_inputs_reply({"response": {"devices": []}}) == mapping.Catalog((), ())   # none active: valid, empty
    assert mapping.parse_inputs_reply({"response": {"devices": [], "metrics": 5}}).metrics == ()


@pytest.mark.parametrize("doc", [None, [], 5, "x", {}, {"response": 5}, {"response": {}}, {"response": {"devices": "x"}},
                                 {"response": {"devices": {"a": 1}}}])
def test_inputs_reply_that_is_not_one_is_none(doc):
    assert mapping.parse_inputs_reply(doc) is None


def test_hostile_inputs_are_left_out_not_trusted():
    bad_endpoints = ["//evil.example/x", "http://evil.example/x", "evil.example/x", "/../x", "/a/../b", "/a b", "/a?x=1",
                     "/a\nb", "", None, 5, ["/x"], "/" + "x" * 300, "@evil.example", "/a#b"]
    chans = [{"channelName": f"C{n}", "streamEndpoint": e} for n, e in enumerate(bad_endpoints)]
    chans += [{"channelName": 5, "streamEndpoint": "/ok"}, {"streamEndpoint": "/ok"}, "junk", None, 7,
              {"channelName": "Fine", "streamEndpoint": "/ok/1"}]
    devs = [{"deviceName": "D", "activeCalibratedChannels": chans}, {"deviceName": 5, "activeCalibratedChannels": chans},
            {"activeCalibratedChannels": chans}, "junk", {"deviceName": "E", "activeCalibratedChannels": "x"}]
    cat = mapping.parse_inputs_reply({"response": {"devices": devs, "metrics": [5, None, "ok", "x" * 500, "a\x00b"]}})
    assert [(i.label, i.endpoint) for i in cat.inputs] == [("D : Fine", "/ok/1")]
    assert all(len(m) <= spl.METRIC_NAME_MAX and "\x00" not in m for m in cat.metrics) and "ok" in cat.metrics
    # a huge list is capped
    many = [{"deviceName": f"D{n}", "activeCalibratedChannels": [{"channelName": f"C{k}", "streamEndpoint": "/s"} for k in range(50)]}
            for n in range(100)]
    cat = mapping.parse_inputs_reply({"response": {"devices": many, "metrics": [f"M{n}" for n in range(500)]}})
    assert len(cat.inputs) <= mapping.MAX_CHANNELS and len(cat.metrics) <= mapping.MAX_METRICS


def test_stream_message_values_come_out_exactly_as_sent():
    raw = json.dumps({"metrics": [{"SPL A Slow": 63.2}, {"SPL C Slow": 68.05, "violation": True},
                                  {"LAeq 1": 70, "violation": False}, {"LAeq 10": 71.123456789}]})
    out = mapping.parse_stream_message(raw)
    assert out == {"SPL A Slow": 63.2, "SPL C Slow": 68.05, "LAeq 1": 70.0, "LAeq 10": 71.123456789}   # a violation flag changes nothing
    assert all(isinstance(v, float) for v in out.values())
    assert mapping.parse_stream_message(raw.encode()) == out


def test_stream_message_name_is_the_first_key_that_is_not_a_flag():
    assert mapping.parse_stream_message('{"metrics":[{"violation":true,"SPL A Slow":60.5}]}') == {"SPL A Slow": 60.5}
    assert mapping.parse_stream_message('{"metrics":[{"SPL A Slow":60.5,"SPL C Slow":1}]}') == {"SPL A Slow": 60.5}


def test_an_overload_flag_or_a_missing_or_damaged_value_is_not_available_never_zero():
    out = mapping.parse_stream_message(json.dumps({"metrics": [
        {"SPL A Slow": 130.0, "overload": True}, {"SPL C Slow": None}, {"LAeq 1": "70.1"}, {"LAeq 10": True},
        {"Z": 99.0, "overload": 1}, {"Y": 98.0, "overload": False}, {"X": 251.0}, {"W": -50.5}, {"V": [1]}]}))
    assert out == {"SPL A Slow": None, "SPL C Slow": None, "LAeq 1": None, "LAeq 10": None, "Z": None, "Y": 98.0,
                   "X": None, "W": None, "V": None}
    assert 0 not in [v for v in out.values() if v is not None]


HOSTILE = [
    "not json", "", b"\xff\xfe", "[]", "7", "null", '"text"', "{}", '{"metrics": 5}', '{"metrics": {"a": 1}}',
    '{"metrics": [5, null, "x", []]}', '{"metrics": [{}]}', '{"metrics": [{"violation": true}]}',
    '{"metrics": [{"SPL A Slow": NaN}]}', '{"metrics": [{"SPL A Slow": Infinity}]}', '{"metrics": [{"SPL A Slow": 1e999}]}',
    "[" * 5000 + "]" * 5000, '{"a":' * 3000 + "1" + "}" * 3000, '{"metrics": [{"": 5}]}',
]


@pytest.mark.parametrize("raw", HOSTILE)
def test_hostile_stream_json_never_raises_and_never_makes_a_number(raw):
    got = mapping.parse_stream_message(raw)
    assert got is None or all(v is None for v in got.values())


def test_oversize_message_is_dropped_before_it_is_decoded():
    big = json.dumps({"metrics": [{"SPL A Slow": 94.3}], "pad": "x" * mapping.MAX_BYTES})
    assert mapping.parse_stream_message(big) is None and mapping.decode(big) is None


def test_the_shipped_mapping_is_marked_unverified():
    assert mapping.VERIFIED is False and SmaartSource.verified is False
    assert mapping.API_PATH == "/api/v4/"


# ----------------------------------------------------------------- the four allowed messages
def test_exactly_four_messages_in_exactly_these_shapes():
    assert outbound.probe() == '{"action":"get"}'
    assert outbound.inputs() == '{"action":"get","target":"activeCalibratedInputs"}'
    assert outbound.fps() == '{"action":"set","properties":[{"targetFPS":1}]}'
    assert json.loads(outbound.login(PW)) == {"action": "set", "properties": [{"password": PW}]}
    assert [outbound.kind_of(m) for m in (outbound.probe(), outbound.inputs(), outbound.login(PW), outbound.fps())] == [
        outbound.PROBE, outbound.INPUTS, outbound.LOGIN, outbound.FPS]
    assert outbound.TARGET_FPS == 1
    # awkward passwords are escaped properly and still count as a log-in, and nothing else
    for pw in ('a"b', "a\\b", "ü€", "a\nb", "{}", '","x":"'):
        assert outbound.kind_of(outbound.login(pw)) == outbound.LOGIN and json.loads(outbound.login(pw))["properties"] == [{"password": pw}]


@pytest.mark.parametrize("text", [
    None, 5, b'{"action":"get"}', "", "x", "[]", "{}", '{"action":"set"}', '{"action":"get","target":"other"}',
    '{"action":"get","target":"activeCalibratedInputs","x":1}', '{"action":"get","x":1}', '{"action":"post"}',
    '{"action":"set","properties":[]}', '{"action":"set","properties":[{"targetFPS":2}]}',
    '{"action":"set","properties":[{"targetFPS":"1"}]}', '{"action":"set","properties":[{"targetFPS":true}]}',
    '{"action":"set","properties":[{"targetFPS":1.0}]}', '{"action":"set","properties":[{"targetFPS":1,"password":"x"}]}',
    '{"action":"set","properties":[{"password":5}]}', '{"action":"set","properties":[{"password":"x"},{"targetFPS":1}]}',
    '{"action":"set","properties":[{"start":true}]}', '{"action":"set","properties":[{"gain":3}]}',
    '{"action":"set","properties":[{"calibration":1}]}', '{"action":"set","properties":[{"logging":false}]}',
    '{"action":"set","properties":[{"alarms":[]}]}', '{"action":"set","target":"x","properties":[{"password":"x"}]}',
    '{"action":"get","target":"history"}', '{"action":"delete"}', '{"action":"set","properties":{"password":"x"}}',
])
def test_anything_else_is_refused(text):
    assert outbound.kind_of(text) is None and not outbound.allowed(text)


def test_the_client_has_one_send_and_it_checks_the_list_first():
    """A check on the code itself: in the whole Smaart package only the checked ``_send`` sends to
    Smaart (the fake server in emulate.py sends the other way), nothing writes to a socket, and the
    history streams are never named."""
    code = {}
    for name in ("client.py", "mapping.py", "outbound.py", "source.py", "__init__.py"):
        src = (SMAART / name).read_text(encoding="utf-8")
        code[name] = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith(("#", '"', "*", "-")))
    for name, text in code.items():
        assert ".send_text(" not in text and ".write(" not in text and ".sendall(" not in text, name
        assert ".send(" not in text or name == "client.py", name
    assert code["client.py"].count(".send(") == 1 and "await ws.send(text)" in code["client.py"]
    send = code["client.py"].split("async def _send")[1].split("async def ")[0]
    assert "outbound.allowed(text)" in send and send.index("outbound.allowed") < send.index(".send(")
    for name in ("client.py", "mapping.py"):
        assert "logEndpoint" not in code[name] and "loggedData" not in code[name], name   # no history streams, no back-fill


# ------------------------------------------------------------------------- fake Smaart
class Sink:
    def __init__(self):
        self.readings: list[SplReading] = []
        self.links: list[tuple[bool, str]] = []
        self.catalogs: list[tuple[list, list]] = []

    def reading(self, r):
        self.readings.append(r)

    def link(self, up, detail):
        self.links.append((up, detail))

    def catalog(self, inputs, metrics):
        self.catalogs.append((list(inputs), list(metrics)))


def client(port, sink, wanted=("",), password="", **kw):
    kw.setdefault("backoff_min_s", 0.05)
    kw.setdefault("backoff_max_s", 0.1)
    kw.setdefault("poll_s", 0.2)
    kw.setdefault("no_inputs_poll_s", 0.1)
    kw.setdefault("auth_retry_s", 0.1)
    kw.setdefault("reply_timeout_s", 1.0)
    src = SmaartSource(lambda: ("127.0.0.1", port), sink.reading, sink.link, sink.catalog,
                       password_fn=lambda: password, **kw)
    src.set_wanted(list(wanted))
    return src


def probe_kinds(srv):
    return [outbound.kind_of(t) for p, t in srv.received if p == PROBE_PATH]


def stream_frames(srv, n):
    return [t for p, t in srv.received if p == f"/stream/{n}"]


async def test_no_password_the_client_probes_asks_for_inputs_and_asks_each_stream_for_one_fps_only():
    async with FakeSmaartServer() as srv:
        sink = Sink()
        src = client(srv.port, sink, wanted=("", INPUT_LABELS[1]))
        await src.start()
        try:
            await until(lambda: {r.source for r in sink.readings} == set(INPUT_LABELS))
            await asyncio.sleep(0.5)    # a few polls
            first = [t for p, t in srv.received if p == PROBE_PATH]
            assert first[0] == '{"action":"get"}' and first[1] == '{"action":"get","target":"activeCalibratedInputs"}'
            assert set(probe_kinds(srv)) == {outbound.PROBE, outbound.INPUTS} and probe_kinds(srv).count(outbound.PROBE) == 1
            assert probe_kinds(srv).count(outbound.INPUTS) >= 2           # asked again, never anything new
            assert sorted(set(srv.paths)) == [PROBE_PATH, "/stream/0", "/stream/1"] and srv.paths.count(PROBE_PATH) == 1
            for n in (0, 1):
                assert stream_frames(srv, n) == ['{"action":"set","properties":[{"targetFPS":1}]}']
            # everything ever sent is on the list, and no password was sent (none was asked for)
            assert all(outbound.allowed(t) for _p, t in srv.received)
            assert outbound.LOGIN not in probe_kinds(srv)
            assert not any(p.startswith("/log") for p in srv.paths)       # Smaart's history is never read
        finally:
            await src.stop()


async def test_values_are_delivered_exactly_with_the_input_they_came_from():
    async with FakeSmaartServer(stream_frames=[
            '{"metrics":[{"SPL A Slow":63.2},{"SPL C Slow":68.05,"violation":true},{"LAeq 1":70.0},{"LAeq 10":71.123456789}]}'],
            period_s=0.05) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: sink.readings)
            r = sink.readings[0]
            assert r.values == {A: 63.2, C: 68.05, LEQ1: 70.0, LEQ10: 71.123456789} and r.source == INPUT_LABELS[0]
            assert sink.catalogs[0] == (list(INPUT_LABELS), [A, C, LEQ1, LEQ10])      # FS Peak left out
            assert sink.links[0] == (True, TEXT["connected"]) and "not yet tested" in TEXT["connected"]
            assert src.problem == ""
        finally:
            await src.stop()


async def test_an_overload_point_is_not_available_and_the_rest_still_number():
    async with FakeSmaartServer(overload=frozenset({A}), period_s=0.02) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: len(sink.readings) >= 3)
            assert all(r.values[A] is None and r.values[C] is not None for r in sink.readings)
        finally:
            await src.stop()


async def test_slots_on_the_same_input_share_one_stream_and_other_inputs_get_their_own():
    async with FakeSmaartServer() as srv:
        sink = Sink()
        # three slots: first input twice (once as "", once by name) and the second input
        src = client(srv.port, sink, wanted=("", INPUT_LABELS[0], INPUT_LABELS[1]))
        await src.start()
        try:
            await until(lambda: {r.source for r in sink.readings} == set(INPUT_LABELS))
            assert srv.paths.count("/stream/0") == 1 and srv.paths.count("/stream/1") == 1
        finally:
            await src.stop()
    async with FakeSmaartServer() as srv:
        sink = Sink()
        src = client(srv.port, sink, wanted=("", ""))
        await src.start()
        try:
            await until(lambda: sink.readings)
            await asyncio.sleep(0.2)
            assert srv.paths.count("/stream/0") == 1 and "/stream/1" not in srv.paths
            assert {r.source for r in sink.readings} == {INPUT_LABELS[0]}
        finally:
            await src.stop()


async def test_changing_the_wanted_inputs_opens_and_closes_streams_without_reconnecting_the_probe():
    async with FakeSmaartServer() as srv:
        sink = Sink()
        src = client(srv.port, sink, wanted=("",), poll_s=30)
        await src.start()
        try:
            await until(lambda: sink.readings)
            assert "/stream/1" not in srv.paths
            src.set_wanted([INPUT_LABELS[1]])
            await until(lambda: any(r.source == INPUT_LABELS[1] for r in sink.readings))
            n0 = len([r for r in sink.readings if r.source == INPUT_LABELS[0]])
            await asyncio.sleep(0.3)
            assert len([r for r in sink.readings if r.source == INPUT_LABELS[0]]) <= n0 + 1    # the first stream was closed
            assert srv.paths.count(PROBE_PATH) == 1
            src.set_wanted(["Nowhere : Channel 1"])          # not listed by Smaart: nothing opened for it
            await asyncio.sleep(0.2)
            assert srv.paths.count("/stream/0") == 1 and srv.paths.count("/stream/1") == 1
        finally:
            await src.stop()


async def test_password_required_and_right_it_logs_in_once_then_asks_for_inputs():
    async with FakeSmaartServer(password=PW) as srv:
        sink = Sink()
        src = client(srv.port, sink, password=PW)
        await src.start()
        try:
            await until(lambda: sink.readings)
            kinds = probe_kinds(srv)
            assert kinds[:3] == [outbound.PROBE, outbound.LOGIN, outbound.INPUTS] and kinds.count(outbound.LOGIN) == 1
            assert [t for p, t in srv.received if p == PROBE_PATH][1] == outbound.login(PW)
            assert all(outbound.allowed(t) for _p, t in srv.received)
        finally:
            await src.stop()


async def test_wrong_password_says_so_and_is_not_tried_again():
    async with FakeSmaartServer(password=PW) as srv:
        sink = Sink()
        waits = []

        async def sleeper(d):
            waits.append(d)
            await asyncio.sleep(d)
        src = client(srv.port, sink, password="not-" + PW, sleep=sleeper)
        await src.start()
        try:
            await until(lambda: (False, TEXT["wrong_password"]) in sink.links and src.problem == "wrong_password")
            await asyncio.sleep(0.5)                           # long enough for many retries at these speeds
            assert srv.paths.count(PROBE_PATH) == 1 and probe_kinds(srv).count(outbound.LOGIN) == 1
            assert sink.readings == [] and "/stream/0" not in srv.paths
            assert PW not in " ".join(d for _, d in sink.links) and "not-" + PW not in TEXT["wrong_password"]
        finally:
            await src.stop()


async def test_password_needed_but_none_set_says_so_never_sends_a_login_and_checks_again_slowly():
    async with FakeSmaartServer(password=PW) as srv:
        sink = Sink()
        src = client(srv.port, sink, password="")
        await src.start()
        try:
            await until(lambda: (False, TEXT["auth_needed"]) in sink.links and srv.paths.count(PROBE_PATH) >= 2)
            assert src.problem == "auth_needed" and outbound.LOGIN not in probe_kinds(srv)
            assert sink.readings == []
        finally:
            await src.stop()


async def test_smaart_with_no_active_inputs_says_to_start_logging_and_picks_them_up_when_they_appear():
    async with FakeSmaartServer(inputs=0) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: (True, TEXT["no_inputs"]) in sink.links)
            assert src.problem == "no_inputs" and sink.catalogs[0][0] == [] and "Start logging" in TEXT["no_inputs"]
            assert [p for p in srv.paths if p.startswith("/stream")] == []
            srv.inputs = 2
            await until(lambda: sink.readings)
            assert src.problem == "" and sink.catalogs[-1][0] == list(INPUT_LABELS)
            assert srv.paths.count(PROBE_PATH) == 1                # same connection: no reconnect needed
        finally:
            await src.stop()


@pytest.mark.parametrize("frames", [["not json"], ['["a"]'], ['{"response": 5}'], ['{"response": []}'], ['{"hello": "x"}'] * 7])
async def test_an_address_that_does_not_answer_like_smaarts_v4_api_is_told_so_plainly(frames):
    async with FakeSmaartServer(probe_frames=frames) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: (False, TEXT["not_smaart"]) in sink.links)
            assert "/api/v4/" in TEXT["not_smaart"] and src.problem == "api"
            assert sink.readings == [] and probe_kinds(srv)[0] == outbound.PROBE
            assert all(k in (outbound.PROBE,) for k in probe_kinds(srv))      # nothing more was sent to it
        finally:
            await src.stop()


async def test_a_few_unrelated_messages_before_the_reply_are_skipped_but_not_forever():
    class Chatty(FakeSmaartServer):
        async def _probe(self, ws):
            async for text in ws:
                self.received.append((PROBE_PATH, text))
                kind = outbound.kind_of(text)
                for _ in range(3):
                    await ws.send('{"hello": "greeting"}')
                if kind == outbound.PROBE:
                    await ws.send(json.dumps({"response": {"authenticationRequired": False}}))
                elif kind == outbound.INPUTS:
                    await ws.send(self.inputs_reply())
    async with Chatty() as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: sink.readings)
        finally:
            await src.stop()
    async with FakeSmaartServer(probe_frames=['{"hello": "x"}'] * 6 + ['{"response": {"authenticationRequired": false}}']) as srv:
        sink = Sink()           # more than the cap of unrelated messages: not Smaart's API
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: (False, TEXT["not_smaart"]) in sink.links)
        finally:
            await src.stop()


async def test_a_name_lookup_that_hangs_times_out_with_the_timeout_message(monkeypatch):
    from stagewatch.integrations.smaart import client as cl
    monkeypatch.setattr(cl, "OPEN_TIMEOUT_S", 0.1)

    async def getaddrinfo(self, host, port, **kw):
        await asyncio.sleep(30)
    monkeypatch.setattr(asyncio.get_running_loop().__class__, "getaddrinfo", getaddrinfo)
    sink = Sink()
    src = SmaartSource(lambda: ("smaart.example", 1), sink.reading, sink.link, backoff_min_s=0.05, backoff_max_s=0.1)
    await src.start()
    try:
        await until(lambda: (False, TEXT["timeout"]) in sink.links)
    finally:
        await src.stop()


async def test_apply_with_restart_logs_in_again_after_a_refused_password(hub):
    async with FakeSmaartServer(password=PW) as srv:
        integ = make_integ(hub, srv, [("", A)], password="not-" + PW)
        await integ.start()
        try:
            await until(lambda: integ.admin_status()["problem"] == "wrong_password")
            srv.password = "not-" + PW                 # as if the password had been corrected in Smaart
            await integ.apply()                         # nothing changed: stays parked
            await asyncio.sleep(0.3)
            assert probe_kinds(srv).count(outbound.LOGIN) == 1
            await integ.apply(restart=True)             # what saving the settings now does
            await until(lambda: hub.devices["spl"].status == Status.OK)
            assert probe_kinds(srv).count(outbound.LOGIN) == 2
        finally:
            await integ.stop()


async def test_an_address_that_never_answers_is_a_timeout_not_a_hang():
    async with FakeSmaartServer(probe_frames=[]) as srv:
        sink = Sink()
        src = client(srv.port, sink, reply_timeout_s=0.2)
        await src.start()
        try:
            await until(lambda: (False, TEXT["timeout"]) in sink.links)
        finally:
            await src.stop()


async def test_a_path_that_is_not_the_v4_api_closes_and_is_reported_without_a_reading():
    class Other(FakeSmaartServer):
        async def _handler(self, ws):
            await ws.close(1008)
    async with Other() as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: any(not up for up, _ in sink.links))
            assert sink.readings == []
        finally:
            await src.stop()


async def test_a_dropped_stream_is_a_gap_then_the_client_reconnects_and_readings_carry_on():
    async with FakeSmaartServer(drop_stream_after=3, period_s=0.02) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: [u for u, _ in sink.links][:4] == [True, False, True, True] or
                        (len(sink.links) >= 3 and sink.links[1][0] is False and sink.links[2][0] is True))
            n = len(sink.readings)
            await until(lambda: len(sink.readings) >= n + 3)
            assert (False, TEXT["closed"]) in sink.links or any(not up for up, _ in sink.links)
            assert srv.paths.count(PROBE_PATH) >= 2 and srv.paths.count("/stream/0") >= 2
            assert all(outbound.allowed(t) for _p, t in srv.received)
        finally:
            await src.stop()


async def test_a_probe_that_closes_is_reported_and_retried_with_a_growing_wait():
    waits = []
    real = asyncio.sleep

    async def spy(d):
        waits.append(d)
        await real(0.01)
    async with FakeSmaartServer(probe_close_after_s=0.0) as srv:
        sink = Sink()
        src = client(srv.port, sink, backoff_min_s=0.05, backoff_max_s=0.4, sleep=spy, stable_s=30.0)
        await src.start()
        try:
            await until(lambda: len(waits) >= 4)
        finally:
            await src.stop()
    assert waits[:4] == [0.1, 0.2, 0.4, 0.4]
    assert (False, TEXT["closed"]) in sink.links


async def test_backoff_starts_over_after_a_stable_link():
    waits = []
    real = asyncio.sleep

    async def spy(d):
        waits.append(d)
        await real(0.01)
    async with FakeSmaartServer(probe_close_after_s=0.15) as srv:
        sink = Sink()
        src = client(srv.port, sink, backoff_min_s=0.05, backoff_max_s=0.4, sleep=spy, stable_s=0.1)
        await src.start()
        try:
            await until(lambda: len(waits) >= 2)
        finally:
            await src.stop()
    assert waits[:2] == [0.05, 0.05]


async def test_hostile_and_unknown_stream_frames_are_ignored_and_the_good_one_still_arrives():
    frames = ["not json", "[]", '{"metrics": 5}', '{"metrics":[{"SPL A Slow": NaN}]}', '{"metrics":[{"SPL A Slow": 1e999}]}',
              '{"other": 1}', '{"response":{"status":true}}', "[" * 3000 + "]" * 3000, '{"metrics":[{"SPL A Slow": 94.3}]}']
    async with FakeSmaartServer(stream_frames=frames, period_s=0.3) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: any(r.values.get(A) == 94.3 for r in sink.readings))
            assert all(r.values.get(A) in (94.3, None) for r in sink.readings)
            assert srv.paths.count("/stream/0") == 1
        finally:
            await src.stop()


async def test_an_oversize_stream_message_closes_the_connection_and_it_comes_back():
    big = json.dumps({"metrics": [{"SPL A Slow": 99.0}], "pad": "x" * (mapping.MAX_BYTES + 100)})
    async with FakeSmaartServer(stream_frames=[big], period_s=0.05) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: srv.paths.count("/stream/0") >= 2)
            assert sink.readings == [] and (False, TEXT["too_large"]) in sink.links
        finally:
            await src.stop()


async def test_the_client_looks_at_only_so_many_messages_a_second():
    frames = [stream_message({A: 90.0 + i / 10}) for i in range(40)]
    async with FakeSmaartServer(stream_frames=frames, period_s=5.0) as srv:
        sink = Sink()
        src = client(srv.port, sink, max_frames_per_s=5)
        await src.start()
        try:
            await until(lambda: src.dropped_frames >= 35)
            assert len(sink.readings) == 5 and src.dropped_frames == 35
        finally:
            await src.stop()


async def test_the_client_follows_no_redirect():
    async with FakeSmaartServer() as target:
        def refuse(connection, request):
            resp = connection.respond(HTTPStatus.FOUND, "")
            resp.headers["Location"] = f"ws://127.0.0.1:{target.port}{PROBE_PATH}"
            return resp
        server = await serve(lambda ws: None, "127.0.0.1", 0, process_request=refuse)
        try:
            port = server.sockets[0].getsockname()[1]
            sink = Sink()
            src = client(port, sink)
            await src.start()
            try:
                await until(lambda: any(not up for up, _ in sink.links))
                await asyncio.sleep(0.2)
                assert target.paths == [] and sink.readings == []
            finally:
                await src.stop()
        finally:
            server.close()
            await server.wait_closed()


async def test_no_address_and_unreachable_are_plain_text_without_the_address():
    sink = Sink()
    src = SmaartSource(lambda: None, sink.reading, sink.link, backoff_min_s=0.05, backoff_max_s=0.1)
    await src.start()
    await until(lambda: (False, TEXT["no_address"]) in sink.links)
    await src.stop()
    sink = Sink()
    port = free_port()
    src = client(port, sink)
    await src.start()
    await until(lambda: (False, TEXT["unreachable"]) in sink.links)
    await src.stop()
    assert all("127.0.0.1" not in d and str(port) not in d for _, d in sink.links)


def test_websocket_address_is_built_plainly():
    assert ws_url("192.0.2.5", 26000) == "ws://192.0.2.5:26000/"
    assert ws_url("192.0.2.5", 26000, "/api/v4/") == "ws://192.0.2.5:26000/api/v4/"
    assert ws_url("::1", 26000, "/stream/0") == "ws://[::1]:26000/stream/0"
    assert ws_url("smaart-laptop", 1) == "ws://smaart-laptop:1/"


async def test_the_password_is_never_logged_even_with_debug_logging_on():
    """Everything Stagewatch itself logs, and the websockets library's frame log for the client's
    connections (which would show the log-in message at DEBUG), must not hold the password. (The fake
    server's own library logging is not Stagewatch's and is left out.)"""
    from stagewatch.integrations.smaart import client as cl
    assert cl._WS_LOG.propagate is False        # the client's frame log goes nowhere
    records: list[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)
    root = logging.getLogger()
    handler, old_level = Grab(), root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        await _password_run()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    ours = [r.getMessage() + str(r.exc_info) for r in records if not r.name.startswith("websockets.server")]
    assert ours and not any(r.name.startswith("websockets.client") for r in records)
    assert not any(PW in m for m in ours)


async def _password_run():
    async with FakeSmaartServer(password=PW) as srv:
        sink = Sink()
        src = client(srv.port, sink, password=PW)
        await src.start()
        try:
            await until(lambda: sink.readings)
        finally:
            await src.stop()
    async with FakeSmaartServer(password=PW) as srv:     # and a wrong one, which also logs the failure
        sink = Sink()
        src = client(srv.port, sink, password="wrong-" + PW)
        await src.start()
        try:
            await until(lambda: src.problem == "wrong_password")
        finally:
            await src.stop()
    assert PW not in repr(src) and PW not in " ".join(d for _, d in sink.links)


async def test_a_failing_callback_does_not_end_the_simulated_source():
    from stagewatch.integrations.smaart.emulate import EmulatedSplSource
    calls = []

    def bad(*a):
        calls.append(a)
        raise RuntimeError("boom")
    src = EmulatedSplSource(bad, bad, bad, period_s=0.01, first_outage_s=1000)
    await src.start()
    await until(lambda: len(calls) >= 4)
    await src.stop()


# ----------------------------------------------------------- resolving names (local network only)
async def test_resolved_names_must_all_be_local(monkeypatch):
    from stagewatch.integrations.smaart import client as cl

    def fake(answers):
        async def getaddrinfo(self, host, port, **kw):
            return [(0, 0, 0, "", (a, port)) for a in answers]
        monkeypatch.setattr(asyncio.get_running_loop().__class__, "getaddrinfo", getaddrinfo)

    fake(["192.168.1.9"])
    assert await cl.resolve_local("smaart.local", 1) == "192.168.1.9"
    for bad in (["8.8.8.8"], ["192.168.1.9", "8.8.8.8"], ["127.0.0.1"], ["169.254.1.1"], ["2002:808:808::1"],
                ["2001:0:4136:e378:8000:63bf:3fff:fdd2"], ["::ffff:8.8.8.8"], ["2001:4860:4860::8888"], ["0.0.0.0"], []):
        fake(bad)
        with pytest.raises(cl.NotLocal):
            await cl.resolve_local("smaart.local", 1)
    assert await cl.resolve_local("127.0.0.1", 1) == "127.0.0.1"   # a typed IP was checked by the settings


async def test_a_name_that_resolves_publicly_is_refused_and_nothing_is_connected(monkeypatch):
    async def getaddrinfo(self, host, port, **kw):
        return [(0, 0, 0, "", ("8.8.8.8", port))]
    monkeypatch.setattr(asyncio.get_running_loop().__class__, "getaddrinfo", getaddrinfo)
    sink = Sink()
    src = SmaartSource(lambda: ("smaart.example", 1), sink.reading, sink.link, backoff_min_s=0.05, backoff_max_s=0.1)
    await src.start()
    await until(lambda: (False, TEXT["not_local"]) in sink.links)
    await src.stop()
    assert sink.readings == []


# -------------------------------------------------------------- through the integration and hub
@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    yield h
    h.recorder.close()


def values(hub):
    return {e.id: e.value for e in hub.entities.values() if e.device_id == "spl"}


def make_integ(hub, srv, meters, password=""):
    hub.config.spl = SplConfig(enabled=True, host="127.0.0.1", port=srv.port, password=password,
                               meters=[SplSlot(metric=m, source=s) for s, m in meters])
    integ = SmaartIntegration(hub, source_factory=lambda o: client(
        srv.port, o_sink(o), wanted=o._wanted_sources(), password=hub.config.spl.password))
    hub.add_integration(integ)
    return integ


def o_sink(owner):
    s = Sink()
    s.reading, s.link, s.catalog = owner._reading, owner._link, owner._catalog
    return s


async def test_two_inputs_through_the_hub_each_slot_gets_its_own_input_and_metric_exactly(hub):
    frames0 = ['{"metrics":[{"SPL A Slow":61.1},{"SPL C Slow":66.2},{"LAeq 1":70.3},{"LAeq 10":71.4}]}']
    async with FakeSmaartServer(stream_frames=frames0, period_s=0.05) as srv:
        integ = make_integ(hub, srv, [("", A), (INPUT_LABELS[1], A), ("", LEQ10)])
        await integ.start()
        try:
            await until(lambda: values(hub).get("spl.laeq_10") == 71.4 and values(hub).get("spl.a_slow") == 61.1)
            ids = [e.id for e in hub.entities.values() if e.device_id == "spl"]
            assert ids == ["spl.a_slow", "spl.a_slow.asio_madiface_usb_channel_8_2", "spl.laeq_10"]
            # the second input's stream is the same canned frames here, so it reads 61.1 too: the point is
            # that it came from its own stream, with its own label
            await until(lambda: values(hub)["spl.a_slow.asio_madiface_usb_channel_8_2"] == 61.1)
            labels = hub.entities["spl.a_slow.asio_madiface_usb_channel_8_2"].labels
            assert labels["source"] == INPUT_LABELS[1] and labels["smaart_name"] == A
            assert hub.entities["spl.a_slow"].labels["source"] == INPUT_LABELS[0]      # "" resolved to the first input
            assert hub.devices["spl"].status == Status.OK and "not yet tested against a live Smaart" in hub.devices["spl"].status_detail
            assert hub.devices["spl"].input_name == ""          # two inputs: each value says its own
            st = integ.admin_status()
            assert st["inputs"] == list(INPUT_LABELS) and st["metrics"] == [A, C, LEQ1, LEQ10] and st["verified"] is False
            assert [s["source_listed"] for s in st["slots"]] == [True, True, True]
            assert srv.paths.count("/stream/0") == 1 and srv.paths.count("/stream/1") == 1
            assert all(outbound.allowed(t) for _p, t in srv.received)
        finally:
            await integ.stop()


async def test_one_input_for_all_slots_shows_its_name_on_the_device(hub):
    async with FakeSmaartServer() as srv:
        integ = make_integ(hub, srv, [("", A), ("", C)])
        await integ.start()
        try:
            await until(lambda: hub.devices["spl"].input_name == INPUT_LABELS[0])
        finally:
            await integ.stop()


async def test_an_overload_point_is_recorded_as_not_available_not_zero(hub):
    async with FakeSmaartServer(overload=frozenset({A})) as srv:
        integ = make_integ(hub, srv, [("", A), ("", C)])
        await integ.start()
        try:
            await until(lambda: values(hub).get("spl.c_slow") is not None)
            assert values(hub)["spl.a_slow"] is None
            assert hub.devices["spl"].status == Status.OK and "1 of 2" in hub.devices["spl"].status_detail
            hub.recorder.flush()
            rows = hub.recorder._db.execute("SELECT value FROM states WHERE entity_id = 'spl.a_slow'").fetchall()
            assert rows and all(r == (None,) for r in rows)
        finally:
            await integ.stop()


async def test_wrong_password_through_the_hub_is_a_clear_silent_notice(hub):
    async with FakeSmaartServer(password=PW) as srv:
        integ = make_integ(hub, srv, [("", A)], password="not-" + PW)
        await integ.start()
        try:
            await until(lambda: hub.devices["spl"].status == Status.MISSING)
            assert hub.devices["spl"].status_detail == TEXT["wrong_password"]
            assert hub.alarms.to_list() and all(a["silent"] for a in hub.alarms.to_list())
            assert integ.admin_status()["problem"] == "wrong_password"
            await asyncio.sleep(0.3)
            assert srv.paths.count(PROBE_PATH) == 1
            # saving the right password logs in at once
            hub.config.spl = SplConfig(enabled=True, host="127.0.0.1", port=srv.port, password=PW,
                                       meters=[SplSlot(metric=A)])
            await integ.apply()
            await until(lambda: hub.devices["spl"].status == Status.OK)
            assert probe_kinds(srv).count(outbound.LOGIN) == 2
        finally:
            await integ.stop()
