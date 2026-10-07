"""Wall Clock (core/wallclock.py) and the Ontime source (integrations/ontime/).

Pure parsing against the fixtures captured from Ontime 4.14.0, the offset maths (midnight wrap,
threshold), the source running only while the card is assigned, and a fake Ontime server that
checks the client is read-only: no WebSocket data frame, only the three allowed paths, no
redirects, oversize messages dropped.
"""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from stagewatch.core import wallclock
from stagewatch.core.config import SiteConfig, WallClockConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.integrations.ontime import OntimeIntegration
from stagewatch.integrations.ontime import parse
from stagewatch.integrations.ontime.client import OntimeError, OntimeSource, check_connection, http_get_json
from stagewatch.integrations.ontime.emulate import EmulatedClock
from stagewatch.web.server import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "ontime"
ALLOWED = {"/api/version", "/api/poll", "/ws"}
UTC = SiteConfig(timezone="UTC")


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ parsing (fixtures)
def test_ws_connect_fixtures_parse_runtime_data_only():
    for name in ("ws_connect_1.json", "ws_connect_2.json", "ws_connect_3.json"):
        kinds, clocks = [], []
        for item in load(name)["messages"]:
            kind, ms = parse.parse_ws_message(json.dumps(item["msg"]))
            kinds.append((item["msg"]["tag"], kind))
            if kind == "clock":
                clocks.append(ms)
        # log, client-init and client-list are ignored; runtime-data gives the clock
        assert {t for t, k in kinds if k == "ignored"} >= {"log", "client-init", "client-list"}
        assert all(k == "clock" for t, k in kinds if t == "runtime-data")
        assert clocks and all(0 <= c <= 86_400_000 for c in clocks)


def test_runtime_data_poll_and_version_fixtures():
    rt = load("runtime_data.json")
    assert parse.parse_ws_message(json.dumps(rt[0])) == ("clock", 58994023)
    assert parse.parse_poll(load("api_poll.json")["body"]) == 58994023
    assert parse.parse_version(load("api_version.json")["body"]) == "4.14.0"


@pytest.mark.parametrize("clock", [-1, 86_400_001, 1.5, True, "58994023", None])
def test_bad_clock_values_are_invalid(clock):
    raw = json.dumps({"tag": "runtime-data", "payload": {"clock": clock}})
    assert parse.parse_ws_message(raw) == ("invalid", None)
    assert parse.parse_poll({"payload": {"clock": clock}}) is None


def test_clock_range_edges_and_missing_payload():
    for ms in (0, 86_400_000):
        assert parse.parse_ws_message(json.dumps({"tag": "runtime-data", "payload": {"clock": ms}})) == ("clock", ms)
    assert parse.parse_ws_message(json.dumps({"tag": "runtime-data"})) == ("invalid", None)
    assert parse.parse_ws_message(json.dumps({"tag": "runtime-data", "payload": []})) == ("invalid", None)


@pytest.mark.parametrize("raw", ["not json", b"\xff\xfe", "[]", "7", json.dumps({"tag": "ontime-clock", "payload": {"clock": 5}}),
                                 json.dumps({"payload": {"clock": 5}})])
def test_other_messages_are_ignored(raw):
    assert parse.parse_ws_message(raw) == ("ignored", None)


def test_oversize_message_is_ignored():
    big = json.dumps({"tag": "runtime-data", "payload": {"clock": 5, "pad": "x" * parse.MAX_BYTES}})
    assert parse.parse_ws_message(big) == ("ignored", None)


@pytest.mark.parametrize("body", [{"payload": 4}, {"payload": "a b"}, {"payload": "x" * 40}, {"payload": "<script>"}, [], None])
def test_version_must_be_short_and_plain(body):
    assert parse.parse_version(body) is None


def test_addresses():
    assert parse.ws_url("http://127.0.0.1:4001") == "ws://127.0.0.1:4001/ws"
    assert parse.ws_url("http://ontime.local") == "ws://ontime.local:80/ws"
    assert parse.ws_url("http://[::1]:4001") == "ws://[::1]:4001/ws"
    assert parse.split_url("http://[::1]:4001") == ("::1", 4001)


# ------------------------------------------------------------------ offset maths
def utc_ts(h, m, s, micro=0):
    return datetime(2026, 1, 15, h, m, s, micro, tzinfo=timezone.utc).timestamp()


def test_offset_across_midnight_wraps_both_ways():
    # Ontime says 23:59:59.8; Stagewatch is already at 00:00:00.3: Ontime is 0.5 s behind.
    assert wallclock.compute_offset_s(86_399_800, utc_ts(0, 0, 0, 300_000), UTC) == pytest.approx(-0.5)
    # Ontime already at 00:00:00.3; Stagewatch still at 23:59:59.8: Ontime is 0.5 s ahead.
    assert wallclock.compute_offset_s(300, utc_ts(23, 59, 59, 800_000), UTC) == pytest.approx(0.5)


def test_offset_sign_and_whole_hours():
    ts = utc_ts(12, 0, 0)
    assert wallclock.compute_offset_s(12 * 3_600_000 + 3200, ts, UTC) == pytest.approx(3.2)
    assert wallclock.compute_offset_s(13 * 3_600_000, ts, UTC) == pytest.approx(3600)
    assert wallclock.compute_offset_s(11 * 3_600_000, ts, UTC) == pytest.approx(-3600)


def test_wrap_offset_range():
    assert wallclock.wrap_offset_s(43_200) == -43_200   # the +/-12 h edge goes one way, never out of range
    assert wallclock.wrap_offset_s(86_400) == 0
    assert -43_200 <= wallclock.wrap_offset_s(-100_000.5) < 43_200


class _Src:
    name, label = "ontime", "Ontime"


def test_warn_threshold_absorbs_one_second_of_granularity():
    ts = utc_ts(12, 0, 0)

    def warn(delta_ms, threshold=2.0):
        r = wallclock.ClockReading(12 * 3_600_000 + delta_ms, ts, "ok")
        return wallclock.reading_message(_Src(), r, UTC, threshold)

    assert warn(1000)["warn"] is False and warn(-1000)["warn"] is False   # up to 1 s of source granularity
    assert warn(2000)["warn"] is False                                  # not above the threshold
    assert warn(2500)["warn"] is True and warn(-3200)["warn"] is True
    assert warn(2500, threshold=5.0)["warn"] is False
    assert warn(3_600_000)["warn"] is True


def test_message_shape_and_offline_has_no_time():
    ts = utc_ts(12, 0, 0)
    ok = wallclock.reading_message(_Src(), wallclock.ClockReading(43_200_000, ts, "ok", "WebSocket"), UTC, 2.0)
    assert set(ok) == {"source", "label", "clock_ms", "received_at", "status", "offset_s", "warn"}
    off = wallclock.reading_message(_Src(), wallclock.ClockReading(None, ts, "offline", "Can't reach Ontime"), UTC, 2.0)
    assert off["clock_ms"] is None and off["offset_s"] is None and off["warn"] is False
    assert "detail" not in off


# ------------------------------------------------------------------ emulate
def test_emulated_clock_is_site_time_plus_half_a_second_with_a_dropout():
    now = [utc_ts(12, 0, 0)]
    clock = EmulatedClock(lambda: UTC, clock=lambda: now[0])
    r = clock.latest()
    assert r.status == "ok" and r.clock_ms == 12 * 3_600_000 + 500
    assert wallclock.compute_offset_s(r.clock_ms, r.received_at, UTC) == pytest.approx(0.5)
    now[0] += 879
    assert clock.latest().status == "ok"
    now[0] += 2           # inside the last 20 s of the 15 minutes
    off = clock.latest()
    assert off.status == "offline" and off.clock_ms is None
    now[0] += 20
    assert clock.latest().status == "ok"


# ------------------------------------------------------------------ hub, service, device
def make_hub(tmp_path, **kw):
    hub = Hub(tmp_path, emulate=True)
    integ = OntimeIntegration(hub, emulate=True, **kw)
    hub.add_integration(integ)
    return hub, integ


def assign_card(hub, on=True):
    for d in hub.config.dashboards:
        d.cards = [c for c in d.cards if c != "wall_clock"] + (["wall_clock"] if on and d.slug == hub.config.dashboards[0].slug else [])


async def test_source_runs_only_while_the_card_is_assigned(tmp_path):
    hub, _ = make_hub(tmp_path)
    await hub.start()
    try:
        # Never added by default: nothing runs, no device, no clock in the snapshot.
        assert all("wall_clock" not in d.cards for d in hub.config.dashboards)
        assert hub.wall_clock.active is False and "ontime" not in hub.devices
        assert hub.snapshot()["wall_clock"] is None

        assign_card(hub)
        hub.save_config()
        await until(lambda: hub.wall_clock.active)
        dev = hub.devices["ontime"]
        assert dev.category == "service" and dev.to_dict()["category"] == "service"
        snap = hub.snapshot()["wall_clock"]
        assert snap["status"] == "ok" and snap["label"] == "Ontime" and snap["warn"] is False
        assert snap["offset_s"] == pytest.approx(0.5, abs=0.05)

        assign_card(hub, on=False)
        hub.save_config()
        await until(lambda: not hub.wall_clock.active)
        assert "ontime" not in hub.devices and hub.snapshot()["wall_clock"] is None
    finally:
        await hub.stop()


async def test_changing_the_address_restarts_the_source(tmp_path):
    class Probe(EmulatedClock):
        starts = 0

        async def start(self):
            Probe.starts += 1
            await super().start()

    hub = Hub(tmp_path, emulate=True)
    inner = Probe(lambda: hub.config.site)
    hub.add_integration(OntimeIntegration(hub, emulate=True, inner=inner))
    assign_card(hub)
    await hub.start()
    try:
        assert Probe.starts == 1
        hub.config.wall_clock = WallClockConfig(ontime_url="http://10.1.2.3:4001")
        hub.save_config()
        await until(lambda: Probe.starts == 2)
    finally:
        await hub.stop()


async def test_emulated_dropout_is_a_silent_missing_alarm(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    now = [utc_ts(12, 0, 0)]
    inner = EmulatedClock(lambda: UTC, clock=lambda: now[0])
    hub.add_integration(OntimeIntegration(hub, emulate=True, inner=inner))
    assign_card(hub)
    await hub.start()
    try:
        assert hub.devices["ontime"].status == Status.OK and hub.alarms.to_list() == []
        now[0] += 890
        inner._on_change(inner.latest())
        assert hub.devices["ontime"].status == Status.MISSING
        alarms = hub.alarms.to_list()
        assert len(alarms) == 1 and alarms[0]["silent"] is True
        assert hub.alarms.sounding is False and hub.alarms.max_level == 0   # never beeps
        now[0] += 20
        inner._on_change(inner.latest())
        assert hub.devices["ontime"].status == Status.OK and hub.alarms.to_list() == []
    finally:
        await hub.stop()


async def test_unreachable_ontime_goes_missing_with_a_silent_alarm(tmp_path):
    hub = Hub(tmp_path)   # not emulate
    url = f"http://127.0.0.1:{free_port()}"
    hub.config.wall_clock = WallClockConfig(ontime_url=url)
    inner = OntimeSource(lambda: hub.config.wall_clock.ontime_url, poll_every_s=0.05, backoff_min_s=0.05,
                         backoff_max_s=0.1)
    hub.add_integration(OntimeIntegration(hub, inner=inner))
    assign_card(hub)
    await hub.start()
    try:
        await until(lambda: hub.devices.get("ontime") and hub.devices["ontime"].status == Status.MISSING,
                    timeout=30)   # a refused connection can take ~2 s to report on Windows
        assert hub.devices["ontime"].status_detail == "Can't reach Ontime"
        alarm = hub.alarms.to_list()[0]
        assert alarm["silent"] is True and hub.alarms.sounding is False and hub.alarms.max_level == 0
        snap = hub.snapshot()["wall_clock"]
        assert snap["status"] == "offline" and snap["clock_ms"] is None
        assert url not in json.dumps(hub.snapshot()["wall_clock"])
    finally:
        await hub.stop()


# ------------------------------------------------------------------ fake Ontime server
class FakeOntime:
    """Serves /api/version, /api/poll and a WebSocket at /ws; records every request path and
    every data frame a client sends."""

    def __init__(self, clock_ms=58_994_023, ws=True, poll="ok", redirect_ws=False, big_ws=False,
                 logs_only=False):
        self.clock_ms, self.ws, self.poll, self.redirect_ws, self.big_ws = clock_ms, ws, poll, redirect_ws, big_ws
        self.logs_only = logs_only
        self.paths: list[str] = []
        self.received: list = []
        self.server = None
        self.port = 0

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def _respond(self, connection, status, body, **headers):
        r = connection.respond(status, body)
        r.headers["Content-Type"] = "application/json"
        for k, v in headers.items():
            r.headers[k.replace("_", "-")] = v
        return r

    def process_request(self, connection, request):
        self.paths.append(request.path)
        p = request.path
        if p == "/api/version":
            return self._respond(connection, HTTPStatus.ACCEPTED, json.dumps({"payload": "4.14.0"}))
        if p == "/api/poll":
            if self.poll == "ok":
                return self._respond(connection, HTTPStatus.ACCEPTED, json.dumps({"payload": {"clock": self.clock_ms}}))
            if self.poll == "redirect":
                return self._respond(connection, HTTPStatus.FOUND, "", Location="/elsewhere")
            if self.poll == "big":
                return self._respond(connection, HTTPStatus.ACCEPTED, "x" * (parse.MAX_BYTES + 10))
            return self._respond(connection, HTTPStatus.NOT_FOUND, "{}")
        if p == "/ws":
            if self.redirect_ws:
                return self._respond(connection, HTTPStatus.FOUND, "", Location="/redirected")
            return None if self.ws else self._respond(connection, HTTPStatus.NOT_FOUND, "{}")
        return self._respond(connection, HTTPStatus.NOT_FOUND, "{}")

    async def handler(self, ws):
        async def rx():
            try:
                async for m in ws:
                    self.received.append(m)
            except Exception:
                pass

        rx_task = asyncio.create_task(rx())
        try:
            if self.big_ws:
                await ws.send("x" * (2 * parse.MAX_BYTES))
                await asyncio.sleep(0.5)
                return
            if self.logs_only:   # frames keep arriving, but never a clock
                while True:
                    await asyncio.sleep(0.05)
                    await ws.send(json.dumps({"tag": "log", "payload": {"text": "hello"}}))
            for item in load("ws_connect_1.json")["messages"]:
                await ws.send(json.dumps(item["msg"]))
            while True:
                await asyncio.sleep(0.05)
                self.clock_ms = (self.clock_ms + 50) % 86_400_000
                await ws.send(json.dumps({"tag": "runtime-data", "payload": {"clock": self.clock_ms}}))
        except Exception:
            pass
        finally:
            rx_task.cancel()

    async def __aenter__(self):
        self.server = await serve(self.handler, "127.0.0.1", 0, process_request=self.process_request,
                                  close_timeout=0.2)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


def fast_source(url, **kw):
    return OntimeSource(lambda: url, poll_every_s=0.05, backoff_min_s=0.05, backoff_max_s=0.2,
                        no_data_s=1.0, open_timeout_s=1.0, **kw)


async def test_reads_clock_over_websocket_and_sends_nothing():
    async with FakeOntime() as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: src.latest().status == "ok" and src.transport == "websocket")
            first = src.latest().clock_ms
            await until(lambda: src.latest().clock_ms != first)   # it keeps ticking
            assert src.version == "4.14.0"
            await asyncio.sleep(0.3)
        finally:
            await src.stop()
        assert fake.received == []                      # not one WebSocket data frame sent
        assert set(fake.paths) <= ALLOWED               # and only the three allowed paths asked for
        assert "/ws" in fake.paths and "/api/version" in fake.paths


async def test_falls_back_to_polling_when_the_websocket_fails():
    async with FakeOntime(ws=False) as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: src.latest().status == "ok" and src.transport == "polling")
            assert src.latest().clock_ms == 58_994_023
            assert src.latest().detail == "Polling"
        finally:
            await src.stop()
        assert set(fake.paths) <= ALLOWED and "/api/poll" in fake.paths


async def test_redirects_are_never_followed():
    async with FakeOntime(ws=False, poll="redirect") as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: "/api/poll" in fake.paths)
            await asyncio.sleep(0.3)
            assert src.latest().status == "offline" and src.latest().clock_ms is None
        finally:
            await src.stop()
        assert "/elsewhere" not in fake.paths
    async with FakeOntime(redirect_ws=True, poll="ok") as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: src.transport == "polling")   # the WebSocket redirect is refused; polling works
        finally:
            await src.stop()
        assert "/redirected" not in fake.paths
    async with FakeOntime(poll="redirect") as fake:
        with pytest.raises(OntimeError) as err:
            await asyncio.to_thread(http_get_json, fake.url, "/api/poll")
        assert err.value.category == "not_ontime" and "/elsewhere" not in fake.paths


async def test_oversize_websocket_message_is_dropped_and_the_client_survives():
    async with FakeOntime(big_ws=True, poll="missing") as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: fake.paths.count("/ws") >= 2)   # connection closed, retried
            assert src.latest().status != "ok" and src.latest().clock_ms is None
            assert src._task is not None and not src._task.done()
        finally:
            await src.stop()


async def test_oversize_http_body_is_refused():
    async with FakeOntime(poll="big") as fake:
        with pytest.raises(OntimeError) as err:
            await asyncio.to_thread(http_get_json, fake.url, "/api/poll")
        assert err.value.category == "too_large"


async def test_only_the_two_read_paths_can_be_requested_over_http():
    async with FakeOntime() as fake:
        for path in ("/api/timer/start", "/api/event", "/ws", "/", "/api/poll/../x"):
            with pytest.raises(ValueError):
                http_get_json(fake.url, path)
        assert fake.paths == []   # nothing was even sent


async def test_check_connection_categories():
    async with FakeOntime() as fake:
        assert await asyncio.to_thread(check_connection, fake.url) == {"ok": True, "version": "4.14.0"}
    r = await asyncio.to_thread(check_connection, f"http://127.0.0.1:{free_port()}")
    assert r == {"ok": False, "category": "unreachable"}


# ------------------------------------------------------------------ web
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def admin(client):
    assert client.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def put_card(client, on=True):
    dashboards = client.get("/api/info").json()["dashboards"]
    dashboards[0]["cards"] = [c for c in dashboards[0]["cards"] if c != "wall_clock"] + (["wall_clock"] if on else [])
    r = client.put("/api/admin/dashboards", json=dashboards)
    assert r.status_code == 200, r.text


def wait(cond, timeout=5.0):
    import time
    end = time.time() + timeout
    while not cond():
        assert time.time() < end, "timed out"
        time.sleep(0.05)


def test_wall_clock_endpoints_need_admin_and_same_origin(client):
    body = {"source": "ontime", "ontime_url": "http://127.0.0.1:4001", "warn_offset_s": 2}
    assert client.put("/api/admin/wall-clock", json=body).status_code == 401
    assert client.post("/api/admin/wall-clock/test", json={}).status_code == 401
    admin(client)
    evil = {"origin": "http://evil.example"}
    assert client.put("/api/admin/wall-clock", json=body, headers=evil).status_code == 403
    assert client.post("/api/admin/wall-clock/test", json={}, headers=evil).status_code == 403


def test_put_wall_clock_validates_and_saves(client):
    admin(client)
    ok = {"source": "ontime", "ontime_url": "http://192.0.2.10:4001/", "warn_offset_s": 5}
    r = client.put("/api/admin/wall-clock", json=ok)
    assert r.status_code == 200 and r.json()["ontime_url"] == "http://192.0.2.10:4001"
    assert client.hub.config.wall_clock.warn_offset_s == 5
    state = client.get("/api/admin/state").json()
    assert state["config"]["wall_clock"]["ontime_url"] == "http://192.0.2.10:4001"
    assert "wall_clock" in state and "ontime_url" not in state["wall_clock"]
    for bad in ({"ontime_url": "https://192.0.2.10"}, {"ontime_url": "http://u:p@192.0.2.10"},
                {"ontime_url": "http://192.0.2.10/api/timer/start"}, {"ontime_url": "ftp://x"},
                {"warn_offset_s": 0}, {"warn_offset_s": 0.9}, {"warn_offset_s": 61}, {"ontime_url": "http://" + "a" * 300}, {"source": "ntp"}):
        r = client.put("/api/admin/wall-clock", json={**ok, **bad})
        assert r.status_code == 422, bad
    assert client.hub.config.wall_clock.ontime_url == "http://192.0.2.10:4001"


def test_test_connection_in_emulate_mode_uses_no_network(client, monkeypatch):
    admin(client)
    monkeypatch.setattr("stagewatch.web.server.check_connection",
                        lambda url: pytest.fail("emulate mode must not touch the network"))
    assert client.post("/api/admin/wall-clock/test", json={}).json() == {"ok": True, "version": "emulated"}


def test_test_connection_reports_fixed_text_only(tmp_path, monkeypatch):
    hub = Hub(tmp_path)   # real mode
    hub.add_integration(OntimeIntegration(hub))
    seen = []

    def fake_check(url):
        seen.append(url)
        return {"ok": False, "category": "unreachable"}

    monkeypatch.setattr("stagewatch.web.server.check_connection", fake_check)
    with TestClient(create_app(hub)) as c:
        admin(c)
        r = c.post("/api/admin/wall-clock/test", json={"ontime_url": "http://192.0.2.99:4001"})
        assert r.status_code == 200 and r.json()["ok"] is False and r.json()["category"] == "unreachable"
        assert "192.0.2.99" not in r.text and seen == ["http://192.0.2.99:4001"]
        monkeypatch.setattr("stagewatch.web.server.check_connection", lambda url: {"ok": True, "version": "4.14.0"})
        assert c.post("/api/admin/wall-clock/test", json={}).json() == {"ok": True, "version": "4.14.0"}
        bad = c.post("/api/admin/wall-clock/test", json={"ontime_url": "https://SECRETHOST/x"})
        assert bad.status_code == 422 and "SECRETHOST" not in bad.text
        assert c.post("/api/admin/wall-clock/test", json={"ontime_url": "x" * 400}).status_code == 422


def test_card_round_trips_and_the_clock_reaches_dashboards(client):
    admin(client)
    assert client.hub.wall_clock.active is False
    assert client.get("/api/snapshot").json()["wall_clock"] is None
    put_card(client)
    wait(lambda: client.hub.wall_clock.active)
    snap = client.get("/api/snapshot").json()
    assert snap["wall_clock"]["status"] == "ok" and snap["wall_clock"]["source"] == "ontime"
    assert "127.0.0.1" not in json.dumps(snap["wall_clock"])
    dev = next(d for d in snap["devices"] if d["id"] == "ontime")
    assert dev["category"] == "service"
    assert next(d for d in snap["devices"] if d["id"] == "site")["category"] == "sensor"
    state = client.get("/api/admin/state").json()
    assert state["wall_clock"]["active"] is True and state["wall_clock"]["status"] == "ok"
    with client.websocket_connect("/ws?dashboard=" + client.hub.config.dashboards[0].slug) as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["wall_clock"]["status"] == "ok"
        for _ in range(60):
            msg = ws.receive_json()
            if msg["type"] == "wall_clock":
                assert msg["label"] == "Ontime" and msg["warn"] is False
                break
        else:
            pytest.fail("no wall_clock message on the live feed")
    put_card(client, on=False)
    wait(lambda: not client.hub.wall_clock.active)


def test_service_device_cannot_be_renamed_or_removed_as_a_node(client):
    admin(client)
    put_card(client)
    wait(lambda: "ontime" in client.hub.devices)
    assert client.patch("/api/admin/devices/ontime", json={"name": "x"}).status_code == 409
    assert client.delete("/api/admin/devices/ontime").status_code == 409
    assert "ontime" in client.hub.devices
    infos = {i["manifest"]["domain"]: i for i in client.get("/api/admin/state").json()["integrations"]}
    assert infos["ontime"]["manifest"]["tier"] == "experimental" and infos["ontime"]["running"] is True

# ------------------------------------------------------------------ review fixes
async def test_slow_drip_host_cannot_hold_the_request_open():
    async def drip(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n\r\n")
        try:
            for _ in range(20):
                writer.write(b" ")        # a byte every 2 s would beat a per-read timeout
                await writer.drain()
                await asyncio.sleep(2.0)
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(drip, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        with pytest.raises(OntimeError) as err:
            await asyncio.to_thread(http_get_json, f"http://127.0.0.1:{port}", "/api/poll", 1.0)
        assert err.value.category == "timeout"
        assert loop.time() - start < 3.5     # one overall deadline, not one per read
    finally:
        server.close()


async def test_websocket_with_only_log_frames_counts_as_no_data():
    async with FakeOntime(logs_only=True, poll="missing") as fake:
        src = OntimeSource(lambda: fake.url, poll_every_s=0.05, backoff_min_s=0.05, backoff_max_s=0.2,
                           no_data_s=0.5, open_timeout_s=1.0)
        await src.start()
        try:
            await until(lambda: fake.paths.count("/ws") >= 2)   # gave up on it and reconnected
            assert src.latest().status != "ok" and src.latest().clock_ms is None
        finally:
            await src.stop()


def test_warn_limit_floor_and_url_length_on_load():
    from stagewatch.core.config import Config
    assert Config.model_validate({"wall_clock": {"warn_offset_s": 0.5}}).wall_clock.warn_offset_s == 1.0
    assert Config.model_validate({"wall_clock": {"warn_offset_s": 3}}).wall_clock.warn_offset_s == 3
    with pytest.raises(ValueError):
        WallClockConfig(warn_offset_s=0.5)
    with pytest.raises(ValueError):
        WallClockConfig(ontime_url="http://" + "a" * 300)


def test_same_zone_source_has_zero_offset_through_london_dst_days():
    from stagewatch.core import sitetime
    london = SiteConfig(timezone="Europe/London")
    # Whole days around both changes, every 10 minutes, plus the repeated hour on 25 Oct.
    for day in ((2026, 3, 28), (2026, 3, 29), (2026, 10, 24), (2026, 10, 25)):
        start = datetime(*day, tzinfo=timezone.utc).timestamp()
        for step in range(0, 3 * 86_400, 600):
            ts = start + step
            ms = sitetime.ms_since_local_midnight(ts, london)
            assert wallclock.compute_offset_s(ms, ts, london) == 0
    first = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp()   # 01:30 BST
    second = first + 3600                                                    # 01:30 GMT, the repeat
    assert sitetime.ms_since_local_midnight(first, london) == sitetime.ms_since_local_midnight(second, london) == 5_400_000
    assert wallclock.compute_offset_s(5_400_000, second, london) == 0
    spring = datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc).timestamp()    # clocks jump to 02:00 BST
    assert sitetime.ms_since_local_midnight(spring, london) == 2 * 3_600_000
    assert wallclock.compute_offset_s(2 * 3_600_000, spring, london) == 0
