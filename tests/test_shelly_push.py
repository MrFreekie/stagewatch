"""The Shelly WebSocket receiver, H&T sensors and pushed power meters (Part B). Read-only: Stagewatch only listens.

What is REAL and what is SYNTHETIC. Nothing here was captured from a real device. ``docs-ht-fullstatus.json``
and the three-phase frames are SYNTHETIC: built from the field names and examples in Shelly's documentation.
All addresses are 192.0.2.x (documentation) and all MACs start 02:5e (locally administered). Everything is
untested on hardware.

Expected numbers are worked out by hand in the docstrings. Limits and timeouts are injected, so nothing
sleeps and hopes.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core.calibration import set_calibration
from stagewatch.core.config import Config, ConfigStore, ShellySensorConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Kind, Status
from stagewatch.integrations.shelly import ShellyIntegration, seed_emulate_push
from stagewatch.integrations.shelly import emulate_push as ep
from stagewatch.integrations.shelly import ht, rpc
from stagewatch.integrations.shelly.push import PushError
from stagewatch.integrations.shelly.receiver import POLICY, NORMAL, TOO_BIG, Receiver
from stagewatch.integrations.shelly.transport import Transport
from stagewatch.web.server import create_app

FIX = Path(__file__).resolve().parent / "fixtures" / "shelly"
HT_MAC, EM_MAC = "025e00000501", "025e00000502"
IP1, IP2 = "192.0.2.71", "192.0.2.72"


def fx(name: str) -> dict:
    data = json.loads((FIX / name).read_text(encoding="utf-8"))
    data.pop("_note", None)
    return data


def frame(prefix: str, mac: str, method: str, params: dict, **extra) -> bytes:
    return json.dumps({"src": f"{prefix}-{mac}", "dst": "user_1", "method": method, "params": params, **extra}).encode()


def ht_frame(mac: str = HT_MAC, **over) -> bytes:
    data = fx("docs-ht-fullstatus.json")
    data["src"] = f"shellyhtg3-{mac}"
    data["params"]["sys"]["mac"] = mac.upper()
    for key, value in over.items():
        comp, _, leaf = key.partition("__")
        if leaf == "":
            data["params"][comp] = value
        else:
            node = data["params"][comp]
            parts = leaf.split("__")
            for p in parts[:-1]:
                node = node[p]
            node[parts[-1]] = value
    return json.dumps(data).encode()


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class NullTransport(Transport):
    name = "none"

    async def start(self, sink): ...
    async def stop(self): ...
    def add(self, target): ...
    def remove(self, mac): ...


# ===================================================================== rpc.py (pure)
def test_a_good_frame_is_parsed_and_the_mac_comes_from_src():
    f = rpc.parse_frame(ht_frame())
    assert isinstance(f, rpc.Frame) and f.mac == HT_MAC and f.prefix == "shellyhtg3" and f.full
    assert f.ts == 1631186545.04
    assert set(rpc.component_names(f.params)) == {"sys", "temperature:0", "humidity:0", "devicepower:0"}


@pytest.mark.parametrize("raw,reason", [
    pytest.param(b"x" * (rpc.MAX_FRAME + 1), "size", id="oversize"),
    pytest.param(b"[" * 5000, "not_json", id="deep-nesting"),
    (b"not json", "not_json"),
    (b"[1, 2]", "not_object"),
    (b'{"src": "x", "method": "NotifyStatus", "params": {}}', "bad_src"),
    (b'{"src": "shellyhtg3-nothex000000", "method": "NotifyStatus", "params": {}}', "bad_src"),
    (b'{"src": "shellyhtg3-025e00000501", "method": "Shelly.Reboot", "params": {}}', "method"),
    (b'{"src": "shellyhtg3-025e00000501", "method": "NotifyStatus", "params": []}', "bad_params"),
    (b'{"id": 5, "src": "shellyhtg3-025e00000501", "method": "NotifyStatus", "params": {}}', "request"),
    (b'{"src": "shellyhtg3-025e00000501", "method": "NotifyFullStatus", "params": {"sys": {"mac": "025E00000999"}}}', "mac_mismatch"),
    (b"\xff\xfe", "not_json"),
])
def test_bad_frames_are_rejected_with_a_fixed_reason(raw, reason):
    got = rpc.parse_frame(raw)
    assert isinstance(got, rpc.Reject) and got.reason == reason


def test_nesting_deeper_than_eight_is_rejected():
    deep: dict = {}
    node = deep
    for _ in range(10):
        node["a"] = {}
        node = node["a"]
    raw = json.dumps({"src": "shellyhtg3-025e00000501", "method": "NotifyStatus", "params": {"x:0": deep}}).encode()
    assert rpc.parse_frame(raw) == rpc.Reject("depth")


def test_the_parser_never_raises_on_random_damage():
    rng = random.Random(7)
    good = ht_frame()
    for _ in range(3000):
        data = bytearray(good)
        for _ in range(rng.randint(1, 8)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.3:
            data = data[: rng.randrange(len(data))]
        got = rpc.parse_frame(bytes(data))
        assert isinstance(got, (rpc.Frame, rpc.Reject))
    for junk in (None, 5, 1.5, [], {}, object()):
        assert isinstance(rpc.parse_frame(junk), rpc.Reject)


def _f(method, params, mac=EM_MAC):
    return rpc.parse_frame(frame("shellypro3em", mac, method, params))


def test_a_full_status_replaces_and_a_partial_one_merges_and_null_deletes():
    full = _f("NotifyFullStatus", {"ts": 1, "em:0": {"id": 0, "a_current": 4.0, "a_voltage": 230.0}, "sys": {"uptime": 5}})
    stored = rpc.merge_status(None, full)
    assert stored == {"em:0": {"id": 0, "a_current": 4.0, "a_voltage": 230.0}, "sys": {"uptime": 5}}
    delta = _f("NotifyStatus", {"ts": 2, "em:0": {"a_current": 5.5}})
    merged = rpc.merge_status(stored, delta)
    assert merged["em:0"] == {"id": 0, "a_current": 5.5, "a_voltage": 230.0}
    assert stored["em:0"]["a_current"] == 4.0                      # the old status is never changed
    gone = rpc.merge_status(merged, _f("NotifyStatus", {"em:0": {"a_voltage": None}}))
    assert "a_voltage" not in gone["em:0"]
    again = rpc.merge_status(merged, _f("NotifyFullStatus", {"em:0": {"id": 0}}))
    assert again == {"em:0": {"id": 0}}                            # a full status replaces everything


def test_a_partial_status_with_nothing_held_is_not_a_status():
    assert rpc.merge_status(None, _f("NotifyStatus", {"em:0": {"a_current": 5.5}})) == {}


def test_clock_skew_is_the_device_clock_minus_ours_and_none_without_a_time():
    f = _f("NotifyStatus", {"ts": 1000.0})
    assert rpc.clock_skew(f, 940.0) == 60.0
    assert rpc.clock_skew(_f("NotifyStatus", {"em:0": {}}), 940.0) is None


def test_components_are_named_in_plain_words_without_values():
    assert rpc.kind_of(["temperature:0", "humidity:0"]) == rpc.HT
    assert rpc.kind_label(["em:0", "emdata:0"]) == "Three-phase meter (em)"
    assert rpc.kind_label(["em1:0"]) == "Single-circuit meter (em1)"
    assert rpc.kind_label(["pm1:0"]) == "Power meter (pm1)"
    assert rpc.kind_label(["input:0"]) == "Not supported yet" and rpc.kind_of(["input:0"]) == rpc.OTHER
    assert rpc.component_names({"em:0": {}, "BAD NAME": {}, "x" * 40: {}, "ts": 1.0}) == ["em:0"]


# ===================================================================== ht.py (pure)
def test_the_documented_example_gives_the_documented_numbers():
    """tC 24.4, rh 73.7 and battery 50 % come straight from the documentation examples. tF (75.9) is never used."""
    got = ht.values(fx("docs-ht-fullstatus.json")["params"])
    assert got == {"temperature": 24.4, "humidity": 73.7, "battery": 50.0}
    assert ht.info(fx("docs-ht-fullstatus.json")["params"]) == {
        "wake_period_s": 7200.0, "wake_cause": "periodic", "external_power": False, "battery_v": 4.87}


def test_a_null_or_missing_field_is_none_or_absent_never_zero():
    params = fx("docs-ht-fullstatus.json")["params"]
    params["temperature:0"]["tC"] = None
    params["humidity:0"]["rh"] = "wet"
    got = ht.values(params)
    assert got["temperature"] is None and got["humidity"] is None
    assert ht.values({"sys": {}}) == {}                          # no component, no entry: nothing is blanked
    assert ht.values(params, names={"humidity:0"}) == {"humidity": None}
    assert "signal" not in ht.values(params)                      # no wifi.rssi sent: no signal reading
    params["wifi"] = {"rssi": -61}
    assert ht.values(params)["signal"] == -61.0


@pytest.mark.parametrize("period,ext,expected", [
    (300, True, 660.0),          # 2.2 x 300 = 660; 300 + 1800 = 2100: the smaller
    (3600, False, 5400.0),       # 2.2 x 3600 = 7920; 3600 + 1800 = 5400: the smaller
    (7200, False, 9000.0),       # 2.2 x 7200 = 15840; 7200 + 1800 = 9000: 2.5 hours
    (60, True, 132.0),           # 2.2 x 60 = 132, above the 120 s floor
    (30, True, 120.0),           # 66 s would be below the floor
    (100000, False, 21600.0),    # never above 6 hours... 100000 + 1800 is capped
    (None, False, 9000.0), (None, True, 720.0), (0, None, 9000.0),
])
def test_stale_limit_formula(period, ext, expected):
    assert ht.stale_limit_s(period, ext) == expected


def test_low_battery_advisory_has_hysteresis_and_skips_usb_power():
    assert ht.battery_low(20.0, False, False) is True      # exactly 20 %: on
    assert ht.battery_low(20.5, False, False) is False     # 20.5 %: not yet on
    assert ht.battery_low(22.0, False, True) is True       # held between 20 and 25
    assert ht.battery_low(25.0, False, True) is False      # 25 %: cleared
    assert ht.battery_low(10.0, True, True) is False       # on USB power: never
    assert ht.battery_low(None, False, True) is False


def test_the_rated_range_is_zero_to_forty_degrees():
    assert [ht.out_of_range(t) for t in (-0.1, 0.0, 40.0, 40.1, None)] == [True, False, False, True, False]


# ===================================================================== the receiver (fake clock, fake sink)
class Sink:
    def __init__(self, known: str = "") -> None:
        self.got: list = []
        self.known = known

    def known_ip(self, mac):
        return self.known

    def deliver(self, mac, f, prev, new, ip, now):
        self.got.append((mac, f.method, prev, new, ip, now))


def make_rx(clock=None, on=True, **kw):
    clock = clock or Clock()
    sink = Sink()
    state = {"on": on, "ignored": set()}
    rx = Receiver(clock=clock, enabled=lambda: state["on"], ignored=lambda: state["ignored"], sink=sink, **kw)
    return rx, sink, state, clock


def test_off_by_default_and_a_browser_is_refused():
    rx, _, state, _ = make_rx(on=False)
    assert rx.admit(IP1, False) == POLICY and rx.counters["refused_off"] == 1
    state["on"] = True
    assert rx.admit(IP1, True) == POLICY and rx.counters["refused_origin"] == 1     # a web page sends an Origin
    assert rx.admit(IP1, False) is None


def test_four_connections_from_one_address_and_thirty_two_in_all():
    rx, *_ = make_rx(handshakes_per_min=1000)
    for _ in range(4):
        assert rx.admit(IP1, False) is None
        rx.open(IP1)
    assert rx.admit(IP1, False) == POLICY and rx.counters["refused_per_ip"] == 1   # the fifth
    for i in range(28):
        ip = f"192.0.2.{100 + i}"
        assert rx.admit(ip, False) is None
        rx.open(ip)
    assert rx.live == 32
    assert rx.admit("192.0.2.200", False) == POLICY and rx.counters["refused_total"] == 1


def test_ten_new_connections_a_minute_from_one_address_then_it_recovers():
    rx, _, _, clock = make_rx()
    for _ in range(10):
        assert rx.admit(IP1, False) is None            # a device that connects and goes away each time
    assert rx.admit(IP1, False) == POLICY and rx.counters["refused_rate"] == 1
    assert rx.admit(IP2, False) is None                # another address is not affected
    clock.t += 60.0
    assert rx.admit(IP1, False) is None


def test_an_unknown_sender_is_listed_by_component_names_only_and_closed(caplog):
    rx, sink, _, clock = make_rx()
    conn = rx.open(IP1)
    with caplog.at_level("INFO"):
        assert rx.handle(conn, ht_frame()) == NORMAL
    assert "not on your list" in caplog.text and HT_MAC not in caplog.text and IP1 not in caplog.text    # no address in a log
    assert sink.got == []                              # nothing is delivered, nothing stored
    [entry] = rx.unknown_list()
    assert set(entry) == {"mac", "src", "ip", "model", "components", "label", "supported", "first_seen", "last_seen", "count"}
    assert entry["mac"] == HT_MAC and entry["ip"] == IP1 and entry["model"] == "Shelly H&T Gen3"
    assert entry["label"] == "Temperature and humidity sensor (H&T)" and entry["supported"] is True
    text = json.dumps(entry)
    assert "24.4" not in text and "73.7" not in text and "4.87" not in text       # no values, ever
    assert rx.status_of(HT_MAC) is None


def test_the_unknown_list_keeps_the_newest_32():
    rx, *_ = make_rx()
    for i in range(40):
        mac = f"025e0000{i:04x}"
        conn = rx.open(IP1)
        rx.handle(conn, frame("shellyhtg3", mac, "NotifyFullStatus", {"temperature:0": {"tC": 1.0}}))
    assert len(rx.unknown) == 32
    assert "025e00000000" not in rx.unknown and "025e00000027" in rx.unknown


def test_an_ignored_sender_is_closed_and_not_listed():
    rx, sink, state, _ = make_rx()
    state["ignored"] = {HT_MAC}
    assert rx.handle(rx.open(IP1), ht_frame()) == NORMAL
    assert rx.unknown_list() == [] and rx.counters["ignored"] == 1 and sink.got == []


def test_an_adopted_sender_is_delivered_with_its_merged_status():
    rx, sink, _, clock = make_rx()
    rx.allow(HT_MAC, "sensor")
    conn = rx.open(IP1)
    assert rx.handle(conn, ht_frame()) is None
    mac, method, prev, new, ip, now = sink.got[-1]
    assert (mac, method, prev, ip, now) == (HT_MAC, "NotifyFullStatus", None, IP1, 1_000_000.0)
    assert new["temperature:0"]["tC"] == 24.4
    clock.t += 1
    assert rx.handle(conn, frame("shellyhtg3", HT_MAC, "NotifyStatus", {"ts": 5, "temperature:0": {"tC": 25.0}})) is None
    assert sink.got[-1][3]["temperature:0"] == {"id": 0, "tC": 25.0, "tF": 75.9}      # merged into the held status
    assert sink.got[-1][3]["humidity:0"]["rh"] == 73.7


def test_a_partial_status_before_any_full_one_is_delivered_empty():
    rx, sink, *_ = make_rx()
    rx.allow(HT_MAC, "sensor")
    rx.handle(rx.open(IP1), frame("shellyhtg3", HT_MAC, "NotifyStatus", {"temperature:0": {"tC": 25.0}}))
    assert sink.got[-1][3] == {}


def test_one_connection_speaks_for_one_device():
    rx, *_ = make_rx()
    rx.allow(HT_MAC, "sensor")
    rx.allow(EM_MAC, "meter")
    conn = rx.open(IP1)
    assert rx.handle(conn, ht_frame()) is None
    assert rx.handle(conn, frame("shellypro3em", EM_MAC, "NotifyFullStatus", {"em:0": {}})) == POLICY
    assert rx.counters["mac_change"] == 1


def test_a_second_sender_for_an_adopted_mac_is_refused_unless_it_is_at_the_known_address():
    rx, sink, *_ = make_rx()
    rx.allow(HT_MAC, "sensor")
    sink.known = IP1
    first, second = rx.open(IP1), rx.open(IP2)
    assert rx.handle(first, ht_frame()) is None
    assert rx.handle(second, ht_frame()) == POLICY and rx.counters["conflict"] == 1 and HT_MAC in rx.conflicts
    # the sender at the known address wins even when the other connected first
    rx2, sink2, *_ = make_rx()
    rx2.allow(HT_MAC, "sensor")
    sink2.known = IP2
    a, b = rx2.open(IP1), rx2.open(IP2)
    assert rx2.handle(a, ht_frame()) is None
    assert rx2.handle(b, ht_frame()) is None and a.superseded
    assert rx2.handle(a, ht_frame()) == NORMAL        # the old one is told goodbye


def test_frame_rate_ten_a_second_with_a_burst_of_thirty():
    rx, _, _, clock = make_rx()
    rx.allow(HT_MAC, "sensor")
    conn = rx.open(IP1)
    codes = [rx.handle(conn, ht_frame()) for _ in range(31)]
    assert codes[:30] == [None] * 30 and codes[30] == POLICY       # the 31st in the same instant
    clock.t += 0.5                                                # 0.5 s x 10/s = 5 frames back
    assert [rx.handle(conn, ht_frame()) for _ in range(6)] == [None] * 5 + [POLICY]


def test_the_global_rate_is_two_hundred_a_second():
    rx, _, _, clock = make_rx(handshakes_per_min=10_000, max_per_ip=10_000, max_total=10_000)
    macs = [f"025e000007{i:02x}" for i in range(10)]
    for m in macs:
        rx.allow(m, "sensor")
    conns = [rx.open(f"192.0.2.{i}") for i in range(1, 11)]
    codes = []
    for i in range(210):                      # 21 each: under every per-connection limit, over the shared one
        codes.append(rx.handle(conns[i % 10], ht_frame(macs[i % 10])))
    assert codes[:200] == [None] * 200 and POLICY in codes[200:] and rx.counters["rate"] >= 1


def test_an_oversize_frame_closes_with_1009_and_junk_closes_after_twenty():
    rx, *_ = make_rx()
    conn = rx.open(IP1)
    assert rx.handle(conn, b"x" * (rpc.MAX_FRAME + 1)) == TOO_BIG
    junk = rx.open(IP1)
    codes = [rx.handle(junk, b"nope") for _ in range(21)]
    assert codes[:20] == [None] * 20 and codes[20] == POLICY and rx.counters["not_json"] == 21


def test_switching_the_receiver_off_closes_a_live_connection_on_its_next_frame():
    rx, _, state, _ = make_rx()
    rx.allow(HT_MAC, "sensor")
    conn = rx.open(IP1)
    assert rx.handle(conn, ht_frame()) is None
    state["on"] = False
    assert rx.handle(conn, ht_frame()) == POLICY


def test_a_request_from_a_device_is_ignored_never_answered():
    rx, sink, *_ = make_rx()
    rx.allow(HT_MAC, "sensor")
    raw = frame("shellyhtg3", HT_MAC, "NotifyStatus", {"temperature:0": {"tC": 1.0}}, id=7)
    assert rx.handle(rx.open(IP1), raw) is None and sink.got == [] and rx.counters["request"] == 1


# ===================================================================== the endpoint (a real ASGI call: count what the server sends)
async def drive(app, frames, *, origin=None, subprotocols=(), cookie=None, ip="192.0.2.77", host="testserver"):
    """Run one WebSocket connection straight through the ASGI app and return everything the server sent."""
    inbox: asyncio.Queue = asyncio.Queue()
    sent: list = []
    headers = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": "/shelly/ws", "raw_path": b"/shelly/ws",
             "query_string": b"", "headers": headers, "client": (ip, 5555), "server": ("testserver", 80),
             "subprotocols": list(subprotocols)}
    await inbox.put({"type": "websocket.connect"})
    for item in frames:
        await inbox.put({"type": "websocket.receive", **({"text": item} if isinstance(item, str) else {"bytes": item})})

    async def receive():
        return await inbox.get()

    async def send(message):
        sent.append(message)
        if message["type"] == "websocket.accept" and not frames:
            pass

    task = asyncio.create_task(app(scope, receive, send))
    # after the frames the device hangs up
    await inbox.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(task, 5)
    return sent


@pytest.fixture
def rig(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    integ = ShellyIntegration(h, transport=NullTransport())
    h.add_integration(integ)
    app = create_app(h)
    yield h, integ, app
    h.recorder.close()


def closes(sent):
    return [m.get("code") for m in sent if m["type"] == "websocket.close"]


async def test_the_server_never_sends_a_data_frame(rig):
    h, integ, app = rig
    h.config.shelly_receiver.enabled = True
    integ.push.rx.allow(HT_MAC, "sensor")
    sent = await drive(app, [ht_frame(), frame("shellyhtg3", HT_MAC, "NotifyStatus", {"x:0": {}}, id=3), b"junk"])
    assert [m["type"] for m in sent if m["type"] == "websocket.send"] == []         # not one message sent
    assert sent[0]["type"] == "websocket.accept"


async def test_a_real_device_names_the_pc_by_its_lan_address_and_port_in_the_host_header(rig):
    """A Shelly sends Host: <address it was given>:<port>. The host check must let a literal address through."""
    h, integ, app = rig
    h.config.shelly_receiver.enabled = True
    sent = await drive(app, [ht_frame()], host="192.0.2.10:8080")
    assert sent[0]["type"] == "websocket.accept" and closes(sent) == [NORMAL]
    assert [u["mac"] for u in integ.push.rx.unknown_list()] == [HT_MAC]
    sent = await drive(app, [ht_frame()], host="attacker.example")           # a rebinding name is still refused
    assert not any(m["type"] == "websocket.accept" for m in sent)


async def test_the_json_rpc_subprotocol_is_echoed_when_offered(rig):
    h, integ, app = rig
    h.config.shelly_receiver.enabled = True
    sent = await drive(app, [], subprotocols=["json-rpc"])
    assert sent[0]["type"] == "websocket.accept" and sent[0].get("subprotocol") == "json-rpc"
    sent = await drive(app, [])
    assert sent[0].get("subprotocol") is None


async def test_receiver_off_and_a_browser_origin_are_refused_before_accepting(rig):
    h, integ, app = rig
    sent = await drive(app, [ht_frame()])
    assert closes(sent) == [POLICY] and not any(m["type"] == "websocket.accept" for m in sent)
    h.config.shelly_receiver.enabled = True
    sent = await drive(app, [ht_frame()], origin="http://evil.example")
    assert closes(sent) == [POLICY] and not any(m["type"] == "websocket.accept" for m in sent)
    assert integ.push.rx.unknown_list() == []


async def test_an_oversize_frame_closes_with_1009(rig):
    h, integ, app = rig
    h.config.shelly_receiver.enabled = True
    sent = await drive(app, ["x" * (rpc.MAX_FRAME + 1)])
    assert closes(sent) == [TOO_BIG]


async def test_a_connection_that_says_nothing_is_closed_after_the_first_frame_time(rig):
    h, integ, app = rig
    h.config.shelly_receiver.enabled = True
    integ.push.rx.first_frame_s = 0.05

    inbox: asyncio.Queue = asyncio.Queue()
    sent: list = []
    await inbox.put({"type": "websocket.connect"})
    scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": "/shelly/ws", "raw_path": b"/shelly/ws",
             "query_string": b"", "headers": [(b"host", b"testserver")], "client": (IP1, 1), "server": ("testserver", 80), "subprotocols": []}

    async def send(m):
        sent.append(m)

    await asyncio.wait_for(app(scope, inbox.get, send), 5)      # no disconnect ever comes: only the timeout ends it
    assert closes(sent) == [POLICY] and integ.push.rx.counters["timeout"] == 1


async def test_an_admin_cookie_grants_nothing_on_the_receiver(rig):
    h, integ, app = rig
    h.config.shelly_receiver.enabled = True
    sent = await drive(app, [ht_frame()], cookie="stagewatch_admin=anything")
    assert closes(sent) == [NORMAL]                                   # still just an unknown sender
    assert [u["mac"] for u in integ.push.rx.unknown_list()] == [HT_MAC]
    assert HT_MAC not in {s.mac for s in h.config.shelly_sensors} and not [d for d in h.devices if "foh" in d]


async def test_the_connection_slot_is_given_back_when_the_device_goes(rig):
    h, integ, app = rig
    h.config.shelly_receiver.enabled = True
    for _ in range(6):
        await drive(app, [ht_frame()])
    assert integ.push.rx.live == 0


# ===================================================================== H&T through the hub
async def make(tmp_path, clock=None, adopt=True, **kw):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    h.config.shelly_receiver.enabled = True
    if adopt:
        h.config.shelly_sensors = [ShellySensorConfig(id="foh_tent", mac=HT_MAC, name="FOH tent", area="FOH")]
    clock = clock or Clock()
    integ = ShellyIntegration(h, transport=NullTransport(), clock=clock)
    h.add_integration(integ)
    await integ.start()
    return h, integ, clock


def wake(integ, clock, raw=None, ip=IP1):
    """A battery device wakes, sends and its socket closes."""
    rx = integ.push.rx
    assert rx.admit(ip, False) is None
    conn = rx.open(ip)
    code = rx.handle(conn, raw if raw is not None else ht_frame())
    rx.release(conn)
    return code


async def test_an_adopted_h_and_t_gives_temperature_humidity_and_battery_in_canonical_units(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        dev = h.devices["foh_tent"]
        assert dev.status == Status.INITIALIZING and "Press the button" in dev.status_detail
        assert wake(integ, clock) is None
        snap = {e["id"]: e for e in h.snapshot()["entities"]}
        t, rh, b = snap["foh_tent.temperature"], snap["foh_tent.humidity"], snap["foh_tent.battery"]
        assert (t["kind"], t["unit"], t["value"]) == ("temperature", "°C", 24.4)
        assert (rh["kind"], rh["unit"], rh["value"]) == ("humidity", "%", 73.7)
        assert (b["kind"], b["unit"], b["value"]) == ("battery", "%", 50.0)
        assert "role" not in t                                  # Environment: nothing marks it Equipment
        assert t["updated"] == clock.t                          # the time it was received, not the device's clock
        assert h.devices["foh_tent"].status == Status.OK
        assert "signal" not in {k.split(".")[1] for k in snap}  # this frame has no wifi.rssi: no signal reading
    finally:
        await integ.stop()
        h.recorder.close()


async def test_an_h_and_t_is_left_out_of_the_site_average_until_ticked(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        wake(integ, clock)
        e = h.entities["foh_tent.temperature"]
        assert h.calibration_for(e).include_in_average is False
        assert h._env_inputs(Kind.TEMPERATURE, clock.t) == []
        set_calibration(h.config, e.id, e.hw_key, 0.0, True)
        assert h.calibration_for(e).include_in_average is True
        assert h._env_inputs(Kind.TEMPERATURE, clock.t) == [e]
    finally:
        await integ.stop()
        h.recorder.close()


async def test_readings_stay_current_for_two_and_a_half_hours_then_go_stale_and_missing(tmp_path):
    """wakeup_period 7200 s: min(2.2 x 7200 = 15840, 7200 + 1800 = 9000) = 9000 s."""
    h, integ, clock = await make(tmp_path)
    try:
        wake(integ, clock)
        e = h.entities["foh_tent.temperature"]
        assert h.stale_after_for(e) == 9000.0
        t0 = clock.t
        assert not e.is_stale(t0 + 8999, h.stale_after_for(e)) and e.is_stale(t0 + 9001, h.stale_after_for(e))
        clock.t = t0 + 8999
        integ.push.check(clock.t)
        assert h.devices["foh_tent"].status == Status.OK
        clock.t = t0 + 9001
        integ.push.check(clock.t)
        dev = h.devices["foh_tent"]
        assert dev.status == Status.MISSING and "No report since" in dev.status_detail and "Batteries flat" in dev.status_detail
        alarm = h.alarms.active["device:foh_tent"]
        assert alarm.silent and alarm.level == 1
        integ.push.check(clock.t + 30)                          # checking again changes nothing
        assert h.devices["foh_tent"].status_detail == dev.status_detail
        # it wakes again: the readings are fresh and the notice goes
        clock.t += 100
        wake(integ, clock)
        assert h.devices["foh_tent"].status == Status.OK and "device:foh_tent" not in h.alarms.active
        assert h.entities["foh_tent.temperature"].updated == clock.t
    finally:
        await integ.stop()
        h.recorder.close()


async def test_low_battery_is_a_silent_notice_with_hysteresis_and_never_on_usb(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        key = "shelly:foh_tent:battery_low"
        for pct, expect in ((50, False), (22, False), (20, True), (22, True), (24, True), (25, False)):
            clock.t += 1
            wake(integ, clock, _with_battery(pct))
            on = key in h.alarms.active
            assert on is expect, pct
            if on:
                assert h.alarms.active[key].silent and h.alarms.active[key].level == 1
                assert h.alarms.active[key].message == f"FOH tent: battery low ({pct} %)"
        wake(integ, clock, _with_battery(10, usb=True))
        assert key not in h.alarms.active                        # on USB power the battery does not matter
    finally:
        await integ.stop()
        h.recorder.close()


def _with_battery(pct, usb=False):
    data = fx("docs-ht-fullstatus.json")
    data["params"]["devicepower:0"]["battery"]["percent"] = pct
    data["params"]["devicepower:0"]["external"]["present"] = usb
    return json.dumps(data).encode()


async def test_a_sensor_error_is_a_gap_and_is_said_so(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        wake(integ, clock)
        data = fx("docs-ht-fullstatus.json")
        data["params"]["temperature:0"]["tC"] = None
        clock.t += 5
        wake(integ, clock, json.dumps(data).encode())
        e = h.entities["foh_tent.temperature"]
        assert e.value is None and e.raw_value is None
        assert h.devices["foh_tent"].status_detail == "Sensor error"
        assert h.entities["foh_tent.humidity"].value == 73.7        # the other readings are unaffected
    finally:
        await integ.stop()
        h.recorder.close()


async def test_a_reading_outside_the_rated_range_is_a_hint_not_an_alarm(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        data = fx("docs-ht-fullstatus.json")
        data["params"]["temperature:0"]["tC"] = 41.0
        wake(integ, clock, json.dumps(data).encode())
        assert "rated range" in h.devices["foh_tent"].status_detail and h.devices["foh_tent"].status == Status.OK
        assert not [a for a in h.alarms.active.values() if not a.silent]
    finally:
        await integ.stop()
        h.recorder.close()


async def test_a_partial_status_only_updates_what_it_carries_and_a_sleep_event_updates_nothing(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        rx = integ.push.rx
        conn = rx.open(IP1)
        rx.handle(conn, ht_frame())
        first = clock.t
        clock.t += 60
        rx.handle(conn, frame("shellyhtg3", HT_MAC, "NotifyStatus", {"ts": 9, "temperature:0": {"tC": 26.5}}))
        assert h.entities["foh_tent.temperature"].value == 26.5 and h.entities["foh_tent.temperature"].updated == clock.t
        assert h.entities["foh_tent.humidity"].updated == first       # not carried, so not refreshed
        clock.t += 60
        rx.handle(conn, frame("shellyhtg3", HT_MAC, "NotifyEvent", {"ts": 9, "events": [{"component": "sys", "event": "sleep"}]}))
        assert h.entities["foh_tent.temperature"].updated == clock.t - 60
        # a sys-only status carries no reading either
        rx.handle(conn, frame("shellyhtg3", HT_MAC, "NotifyStatus", {"sys": {"uptime": 3}}))
        assert h.entities["foh_tent.temperature"].updated == clock.t - 60
    finally:
        await integ.stop()
        h.recorder.close()


async def test_a_new_address_is_followed_by_mac_and_noted(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        wake(integ, clock, ip=IP1)
        assert h.config.shelly_sensors[0].last_ip == IP1
        wake(integ, clock, ip=IP2)
        assert h.config.shelly_sensors[0].last_ip == IP2
        admin = integ.push.admin([])
        assert admin["sensors"][0]["ip_note"] == f"now at {IP2}"
    finally:
        await integ.stop()
        h.recorder.close()


async def test_the_public_snapshot_has_no_mac_or_address(tmp_path):
    h, integ, clock = await make(tmp_path)
    try:
        wake(integ, clock)
        text = json.dumps(h.snapshot())
        assert HT_MAC not in text and HT_MAC.upper() not in text and IP1 not in text
    finally:
        await integ.stop()
        h.recorder.close()


async def test_adoption_only_from_the_found_list_and_only_once(tmp_path):
    h, integ, clock = await make(tmp_path, adopt=False)
    try:
        with pytest.raises(LookupError):
            integ.push.adopt(HT_MAC, "FOH tent", "FOH")
        wake(integ, clock)                                       # an unknown sender: listed, closed, nothing stored
        assert [u["mac"] for u in integ.push.rx.unknown_list()] == [HT_MAC] and not [d for d in h.devices if "foh" in d]
        new_id = integ.push.adopt("02:5E:00:00:05:01", "FOH tent", "FOH")
        assert new_id == "foh_tent" and h.config.shelly_sensors[0].last_ip == IP1
        assert integ.push.rx.unknown_list() == []
        wake(integ, clock)
        assert h.entities["foh_tent.temperature"].value == 24.4
        # the same device again, or the same name, is refused with fixed text
        integ.push.rx.unknown[HT_MAC] = _entry()
        with pytest.raises(PushError, match="already been added"):
            integ.push.adopt(HT_MAC, "Another", "")
    finally:
        await integ.stop()
        h.recorder.close()


def _entry():
    from stagewatch.integrations.shelly.receiver import Unknown
    return Unknown(HT_MAC, f"shellyhtg3-{HT_MAC}", IP1, "Shelly H&T Gen3", ["temperature:0", "humidity:0"], "x", 0.0, 0.0)


async def test_an_unsupported_found_device_cannot_be_adopted(tmp_path):
    h, integ, clock = await make(tmp_path, adopt=False)
    try:
        integ.push.rx.handle(integ.push.rx.open(IP1), frame("shellyplus1", "025e00000599", "NotifyFullStatus", {"input:0": {"id": 0}}))
        with pytest.raises(PushError, match="not supported yet"):
            integ.push.adopt("025e00000599", "Thing", "")
        assert not h.config.shelly_sensors and not h.config.shelly_devices
    finally:
        await integ.stop()
        h.recorder.close()


# ===================================================================== a power meter that pushes (Pro 3EM shape)
def em_status(**changes):
    comps = fx("docs-em-getstatus.json")
    for k, v in changes.items():
        comps["em:0"][k] = v
    return comps


def em_frame(method, params, mac=EM_MAC):
    return frame("shellypro3em", mac, method, params)


async def make_meter(tmp_path):
    h, integ, clock = await make(tmp_path, adopt=False)
    integ.push.rx.handle(integ.push.rx.open(IP1), em_frame("NotifyFullStatus", {"ts": 1, **em_status()}))
    name = integ.push.adopt(EM_MAC, "Dock 3EM", "Dock")
    assert name == "dock_3em" and h.config.shelly_devices[0].transport == "push" and h.config.shelly_devices[0].host == IP1
    return h, integ, clock


async def test_a_pushed_three_phase_meter_gives_the_same_entities_as_a_polled_one(tmp_path):
    """Phase A is the documentation's own example: 4.029 A, 236.1 V, 951.2 W. Totals 6.029 A and 1421.2 W."""
    h, integ, clock = await make_meter(tmp_path)
    try:
        conn = integ.push.rx.open(IP1)
        assert integ.push.rx.handle(conn, em_frame("NotifyFullStatus", {"ts": 1, "sys": {"mac": EM_MAC.upper()}, **em_status()})) is None
        e = h.entities
        assert e["dock_3em.em0_a_current"].value == 4.029 and e["dock_3em.em0_a_voltage"].value == 236.1
        assert e["dock_3em.em0_a_power"].value == 951.2 and e["dock_3em.em0_total_power"].value == 1421.2
        assert e["dock_3em.emdata0_total_energy"].value == 8010.75
        assert "dock_3em.em0_n_current" not in e                       # no neutral CT: null, never registered, never zero
        snap = {x["id"]: x for x in h.snapshot()["entities"]}
        assert snap["dock_3em.em0_a_current"]["role"] == "equipment"
        assert h.devices["dock_3em"].status == Status.OK and h.devices["dock_3em"].status_detail == "Readings sent by the meter"
    finally:
        await integ.stop()
        h.recorder.close()


async def test_a_measured_neutral_arrives_by_push_and_the_meter_stays_out_of_the_calculated_neutral(tmp_path):
    """A Pro 3EM with its neutral clamp reports n_current itself (3.5 A, then 4.25 A in a partial status). Its three
    lines are its own, so the calculated-neutral code (which works from single circuits) does not take it."""
    h, integ, clock = await make_meter(tmp_path)
    try:
        conn = integ.push.rx.open(IP1)
        integ.push.rx.handle(conn, em_frame("NotifyFullStatus", {"ts": 1, **em_status(n_current=3.5)}))
        n = h.entities["dock_3em.em0_n_current"]
        assert n.value == 3.5
        clock.t += 10
        integ.push.rx.handle(conn, em_frame("NotifyStatus", {"ts": 2, "em:0": {"n_current": 4.25}}))
        assert (n.value, n.updated) == (4.25, clock.t)
        assert "shelly_neutral.neutral_current" not in h.entities          # nothing calculated from this meter
    finally:
        await integ.stop()
        h.recorder.close()


async def test_only_what_changed_in_a_partial_status_is_recorded(tmp_path):
    h, integ, clock = await make_meter(tmp_path)
    try:
        conn = integ.push.rx.open(IP1)
        integ.push.rx.handle(conn, em_frame("NotifyFullStatus", {"ts": 1, **em_status()}))
        first = clock.t
        clock.t += 30
        integ.push.rx.handle(conn, em_frame("NotifyStatus", {"ts": 2, "em:0": {"a_current": 5.0}}))
        cur, volt = h.entities["dock_3em.em0_a_current"], h.entities["dock_3em.em0_a_voltage"]
        assert (cur.value, cur.updated) == (5.0, clock.t)
        assert (volt.value, volt.updated) == (236.1, first)             # not carried: not stamped fresh
        assert h.entities["dock_3em.em0_total_current"].updated == first
    finally:
        await integ.stop()
        h.recorder.close()


async def test_a_partial_status_before_a_full_one_records_nothing(tmp_path):
    h, integ, clock = await make_meter(tmp_path)
    try:
        integ.push.rx.handle(integ.push.rx.open(IP1), em_frame("NotifyStatus", {"em:0": {"a_current": 5.0}}))
        assert not [e for e in h.entities if e.startswith("dock_3em.")]
        assert h.devices["dock_3em"].status == Status.INITIALIZING
    finally:
        await integ.stop()
        h.recorder.close()


async def test_a_pushed_meter_going_quiet_is_a_gap_and_a_marker_when_it_returns(tmp_path):
    """Socket open: 600 s. Socket closed: 120 s (owner decision 3, still open for the owner to tune)."""
    h, integ, clock = await make_meter(tmp_path)
    try:
        rx = integ.push.rx
        conn = rx.open(IP1)
        rx.handle(conn, em_frame("NotifyFullStatus", {"ts": 1, **em_status()}))
        last = clock.t
        clock.t = last + 599
        integ.push.check(clock.t)
        assert h.devices["dock_3em"].status == Status.OK           # open socket, steady load: no news is not stale
        clock.t = last + 601
        integ.push.check(clock.t)
        assert h.devices["dock_3em"].status == Status.MISSING and "No reading since" in h.devices["dock_3em"].status_detail
        assert h.alarms.active["device:dock_3em"].silent
        frozen = h.entities["dock_3em.em0_a_current"]
        assert frozen.value == 4.029 and frozen.updated == last      # frozen with its age, no zero, no back-fill
        before = len(h.recorder.markers())
        clock.t += 10
        rx.handle(conn, em_frame("NotifyStatus", {"ts": 2, "em:0": {"a_current": 5.0}}))
        assert h.devices["dock_3em"].status == Status.OK
        labels = [m.label for m in h.recorder.markers()]
        assert labels.count("Dock 3EM: meter back") == 1 and len(labels) == before + 1
        # a closed socket is judged at 120 s
        rx.release(conn)
        clock.t += 119
        integ.push.check(clock.t)
        assert h.devices["dock_3em"].status == Status.OK
        clock.t += 2
        integ.push.check(clock.t)
        assert h.devices["dock_3em"].status == Status.MISSING
    finally:
        await integ.stop()
        h.recorder.close()


async def test_power_readings_never_reach_the_air_averages(tmp_path):
    h, integ, clock = await make_meter(tmp_path)
    try:
        sw = em_status()
        sw["switch:0"] = {"id": 0, "apower": 5.0, "temperature": {"tC": 70.0}}
        integ.push.rx.handle(integ.push.rx.open(IP1), em_frame("NotifyFullStatus", {"ts": 1, **sw}))
        assert h._env_inputs(Kind.TEMPERATURE, clock.t) == []
        assert h.entities["dock_3em.switch_0_device_temp"].value == 70.0
    finally:
        await integ.stop()
        h.recorder.close()


async def test_the_receiver_sends_nothing_and_a_poll_meter_is_not_taken_over_by_a_push(tmp_path):
    """An adopted meter with transport poll is not on the receiver's allow-list, so its pushes are only 'unknown'."""
    h = Hub(tmp_path)
    from stagewatch.core.config import ShellyDeviceConfig
    h.config.shelly_receiver.enabled = True
    h.config.shelly_devices = [ShellyDeviceConfig(id="polled", host=IP1, mac=EM_MAC, name="Polled")]
    integ = ShellyIntegration(h, transport=NullTransport())
    h.add_integration(integ)
    await integ.start()
    try:
        assert EM_MAC not in integ.push.rx.allowed
        integ.push.rx.handle(integ.push.rx.open(IP1), em_frame("NotifyFullStatus", {"ts": 1, **em_status()}))
        assert not [e for e in h.entities if e.startswith("polled.")]
    finally:
        await integ.stop()
        h.recorder.close()


# ===================================================================== config
def test_older_files_load_with_the_receiver_off_and_nothing_else_changes(tmp_path):
    cfg = Config.model_validate({"schema_version": 3})
    assert cfg.shelly_receiver.enabled is False and cfg.shelly_receiver.ignored == [] and cfg.shelly_sensors == []


def test_config_round_trip(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.load()
    store.config.shelly_receiver.enabled = True
    store.config.shelly_receiver.ignored = ["02:5E:00:00:09:09"]
    store.config.shelly_sensors = [ShellySensorConfig(id="Foh Tent", mac="02:5E:00:00:05:01", name="FOH tent", last_ip=IP1)]
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load()
    assert again.shelly_receiver.enabled and again.shelly_receiver.ignored == ["025e00000909"]
    assert [(s.id, s.mac, s.last_ip) for s in again.shelly_sensors] == [("foh_tent", HT_MAC, IP1)]
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert set(raw["shelly_sensors"][0]) == {"id", "mac", "name", "area", "last_ip"}


def test_a_damaged_section_resets_only_itself():
    cfg = Config.model_validate({"schema_version": 3, "shelly_receiver": 5, "shelly_sensors": [
        {"id": "ok", "mac": HT_MAC}, {"id": "bad", "mac": "nope"}, "junk", {"id": "ok", "mac": EM_MAC}, {"id": "dup", "mac": HT_MAC}],
        "shelly_devices": [{"id": "m", "host": IP1, "mac": EM_MAC, "transport": "carrier-pigeon"}]})
    assert cfg.shelly_receiver.enabled is False
    assert [s.id for s in cfg.shelly_sensors] == ["ok"]
    assert cfg.shelly_devices[0].transport == "poll"            # an unknown way in becomes the default


def test_the_sensor_list_is_capped_and_a_bad_address_is_dropped():
    many = [{"id": f"s{i}", "mac": f"025e0000{i:04x}"} for i in range(30)]
    assert len(Config.model_validate({"schema_version": 3, "shelly_sensors": many}).shelly_sensors) == 16
    assert ShellySensorConfig(id="a", mac=HT_MAC, last_ip="not an ip").last_ip == ""


# ===================================================================== the admin API
@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    integ = ShellyIntegration(h, transport=NullTransport())
    h.add_integration(integ)
    with TestClient(create_app(h)) as c:
        c.hub, c.integ = h, integ
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


ROUTES = [("put", "/api/admin/shelly/receiver", {"enabled": True}),
          ("post", "/api/admin/shelly/found/adopt", {"mac": HT_MAC, "name": "x"}),
          ("post", "/api/admin/shelly/found/ignore", {"mac": HT_MAC}),
          ("post", "/api/admin/shelly/found/clear", None),
          ("delete", "/api/admin/shelly/sensor/x", None)]


def test_every_receiver_route_needs_admin_and_the_same_origin(client):
    anon = TestClient(client.app)
    for verb, url, body in ROUTES:
        kw = {"json": body} if body is not None else {}
        assert getattr(anon, verb)(url, **kw).status_code == 401, url
        assert getattr(client, verb)(url, headers={"Origin": "http://evil.example"}, **kw).status_code == 403, url


def test_the_receiver_switch_and_the_found_list_through_the_api(client):
    assert client.put("/api/admin/shelly/receiver", json={"enabled": "yes"}).status_code == 422
    assert client.put("/api/admin/shelly/receiver", json={"enabled": True, "x": 1}).status_code == 422
    assert client.put("/api/admin/shelly/receiver", json={"enabled": True}).status_code == 200
    assert client.hub.config.shelly_receiver.enabled
    state = client.get("/api/admin/state").json()["shelly"]["receiver"]
    assert state["enabled"] is True and state["unknown"] == [] and state["urls"] and "not encrypted or signed" in state["notice"]
    assert all(u.startswith("ws://") and u.endswith("/shelly/ws") for u in state["urls"])
    rx = client.integ.push.rx
    rx.handle(rx.open(IP1), ht_frame())
    state = client.get("/api/admin/state").json()["shelly"]["receiver"]
    assert [u["mac"] for u in state["unknown"]] == [HT_MAC]
    # not on the list: 404; bad bodies: 422 that never echo what was sent
    bad = client.post("/api/admin/shelly/found/adopt", json={"mac": "025e00000999", "name": "Q"})
    assert bad.status_code == 404 and "025e00000999" not in bad.text
    assert client.post("/api/admin/shelly/found/adopt", json={"mac": HT_MAC, "name": ""}).status_code == 422
    assert client.post("/api/admin/shelly/found/adopt", json={"mac": HT_MAC, "name": "a‮b"}).status_code == 422
    ok = client.post("/api/admin/shelly/found/adopt", json={"mac": HT_MAC, "name": "FOH tent", "area": "FOH"})
    assert ok.status_code == 200 and ok.json() == {"id": "foh_tent"}
    again = client.post("/api/admin/shelly/found/adopt", json={"mac": HT_MAC, "name": "Other"})
    assert again.status_code == 404                                 # no longer on the found list
    # public snapshot never shows the address
    snap = json.dumps(client.get("/api/snapshot").json())
    assert HT_MAC not in snap and IP1 not in snap
    assert client.delete("/api/admin/shelly/sensor/foh_tent").status_code == 200
    assert client.delete("/api/admin/shelly/sensor/foh_tent").status_code == 404
    assert "foh_tent" not in client.hub.devices and not client.hub.config.shelly_sensors


def test_ignore_and_clear(client):
    rx = client.integ.push.rx
    client.put("/api/admin/shelly/receiver", json={"enabled": True})
    rx.handle(rx.open(IP1), ht_frame())
    assert client.post("/api/admin/shelly/found/ignore", json={"mac": "02:5E:00:00:05:01"}).status_code == 200
    assert client.hub.config.shelly_receiver.ignored == [HT_MAC] and rx.unknown_list() == []
    assert client.post("/api/admin/shelly/found/ignore", json={"mac": HT_MAC, "ignore": False}).status_code == 200
    assert client.hub.config.shelly_receiver.ignored == []
    assert client.post("/api/admin/shelly/found/ignore", json={"mac": "zzzzzzzzzzzz"}).status_code == 422
    rx.handle(rx.open(IP1), ht_frame())
    assert client.post("/api/admin/shelly/found/clear").status_code == 200 and rx.unknown_list() == []


def test_the_websocket_route_ignores_the_admin_session(client):
    from starlette.websockets import WebSocketDisconnect
    # this client holds the admin cookie; the receiver is still off by default, so it is refused all the same
    assert client.hub.config.shelly_receiver.enabled is False
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/shelly/ws"):
            pass
    assert exc.value.code == POLICY


# ===================================================================== emulate
async def test_the_emulated_devices_run_through_the_real_receiver_and_fail_and_recover(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    assert seed_emulate_push(h.config) is True and seed_emulate_push(h.config) is False
    clock = Clock()
    integ = ShellyIntegration(h, emulate=True, clock=clock)
    h.add_integration(integ)
    await integ.start()
    try:
        sim = integ._sim
        await sim.stop()                                           # drive it by hand with the fake clock
        t0 = clock.t
        seen = {"foh_tent_ht_sim": set(), "dock_3em_push_sim": set()}
        for step in range(0, 61):                                  # 30 s steps over 30 minutes
            clock.t = t0 + step * 30
            sim.step(clock.t)
            integ.push.check(clock.t)
            for dev in seen:
                seen[dev].add(h.devices[dev].status)
            if step == 0:
                assert h.devices["amp_room_ht_sim"].status == Status.OK
                assert h.entities["foh_tent_ht_sim.battery"].value == 23.0
                assert [u["mac"] for u in integ.push.rx.unknown_list()] == [ep.UNK_MAC]
        # the tent sensor skipped three wakes and went missing; the meter's socket dropped for three minutes
        assert Status.MISSING in seen["foh_tent_ht_sim"] and Status.MISSING in seen["dock_3em_push_sim"]
        assert h.devices["foh_tent_ht_sim"].status == Status.OK and h.devices["dock_3em_push_sim"].status == Status.OK
        assert any(m.label.endswith("meter back") for m in h.recorder.markers())
        assert "shelly:foh_tent_ht_sim:battery_low" in h.alarms.active          # 24 % falling 1 % a wake: below 20 % soon
    finally:
        await integ.stop()
        h.recorder.close()