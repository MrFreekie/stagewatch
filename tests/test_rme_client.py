"""The read-only RME client against a fake unit (a real WebSocket server on a free local port).

Every scenario also asserts the fake unit received ZERO data frames from Stagewatch: it listens and never
speaks. Time is injected where a scenario needs seconds (the 8 s first-frame timer, the 10 s silence
limit, the stable window); otherwise the limits are shrunk. Waits are `until(...)` on the end state.
Frames are SYNTHETIC (see tests/fixtures/rme/). 127.0.0.1 is allowed here only by injecting an
address check; the real check refuses it.
"""

from __future__ import annotations

import asyncio
import http
import json
import logging
import re
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from stagewatch.integrations.rme import client as rme_client
from stagewatch.integrations.rme.client import TEXT, RmeClient, RmeError, probe, validate_host
from test_ontime import free_port, until

FIX = Path(__file__).parent / "fixtures" / "rme"
STATUS = (FIX / "status_full.json").read_text(encoding="utf-8")
DELTA_TEMP = (FIX / "delta_temp.json").read_text(encoding="utf-8")
DELTA_PSU = (FIX / "delta_psu.json").read_text(encoding="utf-8")
SERIAL = "SN-PLACEHOLDER-0001"
METER = bytes(range(256)) * 4 + bytes(48)          # 1072 bytes, like a meter frame
assert len(METER) == 1072


async def hold(ws):
    await ws.wait_closed()


class FakeRme:
    """A fake RME unit. ``script(ws, n)`` is called per connection (n = 1, 2, ...); when it returns the
    socket closes. Everything the client sends is kept in ``received``; a test fails if it is not empty."""

    def __init__(self, script=None, *, redirect_to: str | None = None, max_size: int = 4 << 20):
        self.port = free_port()
        self.script = script or self.normal
        self.received: list = []
        self.connections = 0
        self.redirect_to = redirect_to
        self.max_size = max_size
        self._server = None
        self._sockets: set = set()

    @staticmethod
    async def normal(ws, n):
        await ws.send(STATUS)
        await ws.send(DELTA_TEMP)
        await ws.send(METER)
        await ws.send(DELTA_PSU)
        await hold(ws)

    def _redirect(self, connection, request):
        if self.redirect_to:
            resp = connection.respond(http.HTTPStatus.FOUND, "moved\n")
            resp.headers["Location"] = self.redirect_to
            return resp
        return None

    async def _handler(self, ws):
        self.connections += 1
        n = self.connections
        self._sockets.add(ws)

        async def drain():
            try:
                async for message in ws:
                    self.received.append(message)
            except Exception:  # noqa: BLE001
                pass

        reader = asyncio.create_task(drain())
        try:
            await self.script(ws, n)
        except Exception:  # noqa: BLE001
            pass
        finally:
            reader.cancel()
            self._sockets.discard(ws)

    async def __aenter__(self):
        self._server = await serve(self._handler, "127.0.0.1", self.port, max_size=self.max_size,
                                   process_request=self._redirect)
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()
        assert self.received == [], "Stagewatch sent the unit something"

    async def drop(self):
        for ws in list(self._sockets):
            await ws.close()


class Sink:
    def __init__(self):
        self.links: list[tuple[bool, str, bool]] = []
        self.states: list[str] = []
        self.temps: list = []

    def on_link(self, up, detail="", waiting=False):
        self.links.append((up, detail, waiting))

    def on_state(self, state, result):
        from stagewatch.integrations.rme import parse
        self.states.append(result.kind)
        self.temps.append(parse.make_view(state.data).temp_c)

    @property
    def ups(self):
        return [x for x in self.links if x[0]]

    @property
    def downs(self):
        return [x for x in self.links if not x[0] and not x[2]]


def make_client(port, sink, **kw):
    kw.setdefault("backoff_min_s", 0.02)
    kw.setdefault("backoff_max_s", 0.08)
    kw.setdefault("jitter", lambda: 0.0)
    kw.setdefault("tick_s", 0.02)
    kw.setdefault("address_ok", lambda ip: True)
    return RmeClient(lambda: ("127.0.0.1", port), sink.on_state, sink.on_link, **kw)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ the normal path
async def test_full_status_then_deltas_then_binary_and_nothing_sent():
    async with FakeRme() as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: sink.states == ["baseline", "delta", "delta"] and cl.binary_frames >= 1)
            assert sink.temps == [61.9, 63.0, 63.0]                       # 335.05 K, then 336.15 K
            assert sink.ups == [(True, "", False)]                         # up only once the full status arrived
            assert sink.links[0] == (False, TEXT["waiting"], True)         # connected, waiting: not online
            assert cl.last_frame_at is not None and cl.last_text_at is not None
        finally:
            await cl.stop()


async def test_two_clients_on_one_unit_both_get_the_status():
    async with FakeRme() as srv:
        a, b = Sink(), Sink()
        c1, c2 = make_client(srv.port, a), make_client(srv.port, b)
        await c1.start()
        await c2.start()
        try:
            await until(lambda: a.states[:1] == ["baseline"] and b.states[:1] == ["baseline"])
            assert srv.connections == 2
        finally:
            await c1.stop()
            await c2.stop()


async def test_a_second_full_status_mid_session_is_a_resync():
    async def script(ws, n):
        await ws.send(STATUS)
        await ws.send(STATUS)
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: sink.states == ["baseline", "resync"])
        finally:
            await cl.stop()


async def test_a_delta_before_a_status_is_ignored_and_not_online():
    async def script(ws, n):
        await ws.send(DELTA_TEMP)
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: cl.dropped_frames >= 1)
            assert sink.ups == [] and sink.states == []
        finally:
            await cl.stop()


# ------------------------------------------------------------------ silence and the timers
async def test_a_socket_that_opens_but_says_nothing_for_8_seconds_is_never_online():
    async def script(ws, n):
        await hold(ws)

    clock = Clock()
    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, clock=clock, first_frame_s=8.0, silence_s=10.0)
        await cl.start()
        try:
            await until(lambda: sink.links and sink.links[0][2] is True)
            clock.t += 7.9
            await asyncio.sleep(0.1)
            assert sink.downs == []                                       # not yet 8 s
            clock.t += 0.2
            await until(lambda: sink.downs)
            assert sink.downs[0][1] == TEXT["silent"] == "Connected, but the unit sent nothing in 8 seconds"
            assert sink.ups == []
            assert "8 seconds" in cl.hint() or cl.hint() == ""
        finally:
            await cl.stop()


async def test_ten_seconds_with_no_frame_at_all_drops_the_link_after_it_was_up():
    async def script(ws, n):
        await ws.send(STATUS)
        await hold(ws)

    clock = Clock()
    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, clock=clock)
        await cl.start()
        try:
            await until(lambda: sink.ups)
            clock.t += 9.5
            await asyncio.sleep(0.1)
            assert sink.downs == []
            clock.t += 1.0
            await until(lambda: sink.downs)
            assert sink.downs[0][1] == TEXT["quiet"]
        finally:
            await cl.stop()


async def test_only_binary_frames_keeps_the_socket_but_is_never_online():
    async def script(ws, n):
        for _ in range(60):
            await ws.send(METER)
            await asyncio.sleep(0.01)
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: cl.binary_frames >= 60)
            assert sink.ups == [] and sink.states == [] and srv.connections == 1
            assert sink.links[-1] == (False, TEXT["waiting"], True)
        finally:
            await cl.stop()


async def test_a_socket_that_closes_reconnects_with_backoff_1_2_4_8_and_resets_after_a_stable_spell():
    waits: list[float] = []

    async def fast_sleep(delay):
        waits.append(delay)
        await asyncio.sleep(0.001)

    clock = Clock()

    async def script(ws, n):
        await ws.send(STATUS)
        if n < 5:
            return                                                         # closes at once
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, clock=clock, sleep=fast_sleep, backoff_min_s=1.0, backoff_max_s=30.0,
                         stable_s=30.0)
        await cl.start()
        try:
            await until(lambda: srv.connections >= 5 and len(sink.ups) >= 5)
            assert waits[:4] == [1.0, 2.0, 4.0, 8.0]
            clock.t += 31                                                  # the fifth connection has been up for over 30 s
            await srv.drop()
            await until(lambda: len(waits) >= 5)
            assert waits[4] == 1.0                                         # reset after the stable window
            assert {d for _, d, w in sink.downs} <= {TEXT["closed"], TEXT["quiet"]}     # the clock jump also trips the silence limit
        finally:
            await cl.stop()


async def test_backoff_is_capped_and_jittered():
    waits: list[float] = []

    async def fast_sleep(delay):
        waits.append(delay)
        await asyncio.sleep(0.001)

    async def script(ws, n):
        return

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, sleep=fast_sleep, backoff_min_s=1.0, backoff_max_s=30.0, jitter=lambda: 0.2)
        await cl.start()
        try:
            await until(lambda: len(waits) >= 8)
            assert waits[:7] == [1.2, 2.2, 4.2, 8.2, 16.2, 30.2, 30.2]
        finally:
            await cl.stop()


async def test_a_reconnect_storm_is_bounded_by_the_backoff():
    async def script(ws, n):
        return

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, backoff_min_s=0.2, backoff_max_s=0.4)
        await cl.start()
        try:
            await asyncio.sleep(0.5)
            assert srv.connections <= 4                                    # not hundreds
        finally:
            await cl.stop()


# ------------------------------------------------------------------ hostile units
async def test_a_redirect_is_refused_and_never_followed():
    async with FakeRme() as target:
        async with FakeRme(redirect_to=f"ws://127.0.0.1:{target.port}/ws_v2") as srv:
            sink = Sink()
            cl = make_client(srv.port, sink)
            await cl.start()
            try:
                await until(lambda: sink.downs and sink.downs[0][1] == TEXT["redirect"])
                assert target.connections == 0 and sink.ups == []
            finally:
                await cl.stop()


async def test_an_oversize_frame_closes_the_connection_not_the_hub():
    async def script(ws, n):
        await ws.send(STATUS)
        await ws.send("x" * (600 * 1024))
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: sink.downs)
            assert sink.downs[0][1] == TEXT["too_large"]
        finally:
            await cl.stop()


async def test_a_few_bad_frames_are_dropped_and_counted_the_link_survives():
    async def script(ws, n):
        await ws.send(STATUS)
        for bad in ("nope", "[1]", '{"a": NaN}', '{"a":' * 14 + "1" + "}" * 14, json.dumps({"s": "x" * 300})):
            await ws.send(bad)
        await ws.send(DELTA_TEMP)
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: sink.states == ["baseline", "delta"])
            assert cl.bad_frames == 5 and sink.downs == []
            assert sink.temps[-1] == 63.0
        finally:
            await cl.stop()


async def test_more_than_20_bad_frames_in_a_minute_drops_the_link():
    async def script(ws, n):
        await ws.send(STATUS)
        for _ in range(25):
            await ws.send("garbage")
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: sink.downs)
            assert sink.downs[0][1] == TEXT["bad_frames"]
        finally:
            await cl.stop()


async def test_a_text_flood_is_dropped_then_the_link_is():
    """Fast fake clock so the one-second windows roll: more than 10 text frames in each of 2 windows."""
    ticks = [0.0]

    def clock():
        ticks[0] += 0.05
        return ticks[0]

    async def script(ws, n):
        await ws.send(STATUS)
        for _ in range(400):
            await ws.send(DELTA_TEMP)
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, clock=clock, max_text_per_s=10, flood_s=2, first_frame_s=1e9, silence_s=1e9)
        await cl.start()
        try:
            await until(lambda: sink.downs)
            assert sink.downs[0][1] == TEXT["flood"]
            assert cl.dropped_frames > 0
        finally:
            await cl.stop()


async def test_a_status_that_is_not_an_rme_unit_is_refused():
    async def script(ws, n):
        await ws.send(json.dumps({"msg": "status", "data": {"hello": "world"}}))
        await hold(ws)

    async with FakeRme(script) as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: sink.downs and sink.downs[0][1] == TEXT["not_rme"])
            assert sink.ups == [] and cl.problem == "api"
        finally:
            await cl.stop()


async def test_a_different_unit_at_the_saved_address_is_refused():
    async with FakeRme() as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, expected_serial_fn=lambda: "SN-OTHER-UNIT")
        await cl.start()
        try:
            await until(lambda: sink.downs and sink.downs[0][1] == TEXT["other_unit"])
            assert sink.ups == [] and sink.states == []
            assert "Replace unit" in cl.hint()
        finally:
            await cl.stop()


async def test_the_matching_serial_is_accepted():
    async with FakeRme() as srv:
        sink = Sink()
        cl = make_client(srv.port, sink, expected_serial_fn=lambda: SERIAL)
        await cl.start()
        try:
            await until(lambda: sink.ups)
        finally:
            await cl.stop()


# ------------------------------------------------------------------ the address
async def test_public_loopback_and_names_are_refused_without_connecting():
    async with FakeRme() as srv:
        for host in ("127.0.0.1", "8.8.8.8", "12mic.local", "localhost", "", "0.0.0.0", "224.0.0.1"):
            sink = Sink()
            cl = RmeClient(lambda h=host: (h, srv.port), sink.on_state, sink.on_link,
                           backoff_min_s=0.02, backoff_max_s=0.04, jitter=lambda: 0.0)
            await cl.start()
            try:
                await until(lambda: sink.downs)
                assert sink.downs[0][1] == TEXT["not_local"], host
            finally:
                await cl.stop()
        assert srv.connections == 0


def test_validate_host_accepts_private_ips_only():
    for ok in ("192.168.1.50", "10.1.2.3", "172.16.0.9", "fd00::5", " 192.168.1.50 "):
        assert validate_host(ok) == ok.strip()
    for bad in ("127.0.0.1", "::1", "169.254.1.1", "8.8.8.8", "12mic.local", "localhost", "", "134744072", 5, None, "::ffff:8.8.8.8",
                "192.168.1.50/24", "192.168.1.50:80", "http://192.168.1.50", "192.168.1"):
        with pytest.raises(RmeError) as exc:
            validate_host(bad)
        assert exc.value.category == "not_local"


# ------------------------------------------------------------------ nothing sent, nothing logged
def test_the_package_has_no_send_path():
    root = Path(rme_client.__file__).parent
    for path in root.glob("*.py"):
        code = "\n".join(line.split("#")[0] for line in path.read_text(encoding="utf-8").splitlines())
        assert not re.search(r"\.send\w*\(", code), path.name
        assert not re.search(r"\.(ping|pong|write|sendall)\(", code), path.name
        assert "ping_interval=None" in code or path.name != "client.py" or True
    assert "ping_interval=None" in (root / "client.py").read_text(encoding="utf-8")


async def test_library_frame_logging_is_silenced_so_the_serial_never_reaches_a_log(caplog):
    caplog.set_level(logging.DEBUG)
    async with FakeRme() as srv:
        sink = Sink()
        cl = make_client(srv.port, sink)
        await cl.start()
        try:
            await until(lambda: sink.states[:3] == ["baseline", "delta", "delta"])
        finally:
            await cl.stop()
    assert SERIAL not in caplog.text and "Group Placeholder" not in caplog.text and "Stage L" not in caplog.text


# ------------------------------------------------------------------ the Test button (probe)
async def test_probe_reads_model_firmware_and_name_sends_nothing_and_skips_binary():
    async def script(ws, n):
        await ws.send(METER)
        await ws.send(DELTA_TEMP)                                          # a delta before the status is skipped
        await ws.send(STATUS)
        await hold(ws)

    async with FakeRme(script) as srv:
        got = await probe("127.0.0.1", srv.port, address_ok=lambda ip: True)
        assert got == {"model": "12Mic", "firmware": "2.0.1", "name": "Stage L", "serial": SERIAL}
        await until(lambda: not srv._sockets)                               # closed at once
        assert srv.connections == 1


async def test_probe_to_a_silent_unit_says_so_within_the_wait():
    async def script(ws, n):
        await hold(ws)

    async with FakeRme(script) as srv:
        with pytest.raises(RmeError) as exc:
            await probe("127.0.0.1", srv.port, address_ok=lambda ip: True, wait_s=0.3)
        assert exc.value.category == "silent" and TEXT["silent"].startswith("Connected, but the unit sent nothing")


async def test_probe_of_something_that_is_not_a_unit_and_of_a_closed_port_and_a_redirect():
    async def script(ws, n):
        await ws.send(json.dumps({"msg": "status", "data": {"hello": 1}}))
        await hold(ws)

    async with FakeRme(script) as srv:
        with pytest.raises(RmeError) as exc:
            await probe("127.0.0.1", srv.port, address_ok=lambda ip: True)
        assert exc.value.category == "not_rme"
    with pytest.raises(RmeError) as exc:                                     # nothing listening
        await probe("127.0.0.1", free_port(), address_ok=lambda ip: True, open_timeout_s=2)
    assert exc.value.category == "unreachable"
    async with FakeRme() as target:
        async with FakeRme(redirect_to=f"ws://127.0.0.1:{target.port}/ws_v2") as srv:
            with pytest.raises(RmeError) as exc:
                await probe("127.0.0.1", srv.port, address_ok=lambda ip: True)
            assert exc.value.category == "redirect" and target.connections == 0


async def test_probe_refuses_public_and_names_and_never_connects_or_echoes():
    async with FakeRme() as srv:
        for host in ("127.0.0.1", "8.8.8.8", "12mic.local"):
            with pytest.raises(RmeError) as exc:
                await probe(host, srv.port)
            assert exc.value.category == "not_local" and host not in str(exc.value)
        assert srv.connections == 0
    assert TEXT["not_local"] == "That address is not on a private network"
