"""The Refresh button on the Sound level card: the running client asks Smaart for its input and metric
names again (the one allowed input-list question, nothing new), the lists and the chosen values follow,
a value whose input or metric is gone is "not available" (never zero, never moved to another input), and
the endpoint is admin-only, same-origin only and rate-limited. The fake Smaart is the one in
integrations/smaart/emulate.py; its messages are SYNTHETIC."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.config import SplConfig, SplSlot
from stagewatch.core.hub import Hub
from stagewatch.integrations.smaart import REFRESH_TEXT, SmaartIntegration, outbound
from stagewatch.integrations.smaart.emulate import (
    CHANNELS, DEVICE_NAME, INPUT_LABELS, EmulatedSplSource, FakeSmaartServer)
from stagewatch.web.server import create_app
from test_ontime import until
from test_smaart_client import A, C, client, o_sink

PIN = "test-pin-1234"


class Mono:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    yield h
    h.recorder.close()


def make(hub, srv, meters, mono):
    hub.config.spl = SplConfig(enabled=True, host="127.0.0.1", port=srv.port,
                               meters=[SplSlot(metric=m, source=s) for s, m in meters])
    # poll_s is long: only the button can make the client ask again during these tests
    integ = SmaartIntegration(hub, mono=mono, source_factory=lambda o: client(
        srv.port, o_sink(o), wanted=o._wanted_sources(), poll_s=600.0))
    hub.add_integration(integ)
    return integ


def listed_inputs(srv):
    return [t for _p, t in srv.received if outbound.kind_of(t) == outbound.INPUTS]


def values(hub):
    return {e.id: e.value for e in hub.entities.values() if e.device_id == "spl"}


async def test_refresh_picks_up_a_renamed_input_and_sends_only_allowed_messages(hub):
    mono = Mono()
    async with FakeSmaartServer(period_s=0.02) as srv:
        integ = make(hub, srv, [("", A), (INPUT_LABELS[1], C)], mono)
        await integ.start()
        try:
            await until(lambda: integ.admin_status()["inputs"] == list(INPUT_LABELS))
            asked = len(listed_inputs(srv))
            srv.channel_names = (CHANNELS[0], "Renamed FOH")
            assert integ.admin_status()["inputs"] == list(INPUT_LABELS)       # nothing yet: no poll in sight
            done, text = await integ.refresh_catalog()
            assert done and text == "Refreshed: 2 inputs, 4 metrics"
            assert len(listed_inputs(srv)) == asked + 1
            assert integ.admin_status()["inputs"] == [INPUT_LABELS[0], f"{DEVICE_NAME} : Renamed FOH"]
            # the value that named the old input is not available, still pointing at the old name
            st = {s["metric"]: s for s in integ.admin_status()["slots"]}
            assert st[C]["source"] == INPUT_LABELS[1] and st[C]["source_listed"] is False
            await until(lambda: values(hub)["spl.a_slow"] is not None)
            assert values(hub)["spl.c_slow.asio_madiface_usb_channel_8_2"] is None
            assert all(outbound.allowed(t) for _p, t in srv.received)
        finally:
            await integ.stop()


async def test_a_removed_input_makes_its_value_not_available_never_zero_and_it_is_not_re_pointed(hub):
    mono = Mono()
    async with FakeSmaartServer(period_s=0.02) as srv:
        integ = make(hub, srv, [(INPUT_LABELS[1], A), ("", C)], mono)
        await integ.start()
        try:
            second = "spl.a_slow.asio_madiface_usb_channel_8_2"
            await until(lambda: values(hub).get(second) is not None)
            srv.inputs = 1
            done, text = await integ.refresh_catalog()
            assert done and text == "Refreshed: 1 input, 4 metrics"
            assert values(hub)[second] is None and values(hub)[second] != 0
            await until(lambda: values(hub)["spl.c_slow"] is not None)       # the first input carries on
            assert integ.slot_entities()[0][1] == (INPUT_LABELS[1], A)       # still the same input name
            assert hub.entities[second].labels["source"] == INPUT_LABELS[1]
            assert values(hub)[second] is None                               # and stayed empty
        finally:
            await integ.stop()


async def test_a_removed_metric_is_not_available(hub):
    mono = Mono()
    async with FakeSmaartServer(period_s=0.02) as srv:
        integ = make(hub, srv, [("", A), ("", C)], mono)
        await integ.start()
        try:
            await until(lambda: values(hub).get("spl.c_slow") is not None)
            srv.metrics = (A, "LAeq 1")
            done, text = await integ.refresh_catalog()
            assert done and text == "Refreshed: 2 inputs, 2 metrics"
            assert values(hub)["spl.c_slow"] is None
            assert integ.admin_status()["metrics"] == [A, "LAeq 1"]
        finally:
            await integ.stop()


async def test_refresh_is_rate_limited(hub):
    mono = Mono()
    async with FakeSmaartServer(period_s=0.02) as srv:
        integ = make(hub, srv, [("", A)], mono)
        await integ.start()
        try:
            await until(lambda: integ.admin_status()["inputs"])
            assert (await integ.refresh_catalog())[0] is True
            asked = len(listed_inputs(srv))
            for _ in range(5):                                                # a button mash
                assert await integ.refresh_catalog() == (False, REFRESH_TEXT["recent"])
            assert len(listed_inputs(srv)) == asked
            mono.t += 2.1
            assert (await integ.refresh_catalog())[0] is True
            assert len(listed_inputs(srv)) == asked + 1
        finally:
            await integ.stop()


async def test_refresh_with_the_link_down_says_so_and_sends_nothing(hub):
    mono = Mono()
    async with FakeSmaartServer() as srv:
        integ = make(hub, srv, [("", A)], mono)
        port = srv.port
    # the server is gone: nothing to ask
    await integ.start()
    try:
        assert port and await integ.refresh_catalog() == (False, REFRESH_TEXT["down"])
        assert await integ.refresh_catalog() == (False, REFRESH_TEXT["down"])   # down does not use up the allowance
    finally:
        await integ.stop()


async def test_refresh_when_not_switched_on(hub):
    integ = SmaartIntegration(hub)
    hub.add_integration(integ)
    assert await integ.refresh_catalog() == (False, REFRESH_TEXT["off"])


# ------------------------------------------------------------------------------ the endpoint
REFRESH = "/api/admin/spl/refresh"


@pytest.fixture
def app_client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    integ = SmaartIntegration(hub, emulate=True, source_factory=lambda o: EmulatedSplSource(
        o._reading, o._link, o._catalog, period_s=0.02, first_outage_s=1000))
    hub.add_integration(integ)
    app = create_app(hub, lan_addresses=lambda: ["192.0.2.10"])
    with TestClient(app) as c:
        c.hub = hub
        yield c


def wait(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting")


def test_refresh_endpoint_is_admin_only_and_same_origin_only(app_client):
    c = app_client
    assert c.post(REFRESH).status_code == 401
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
    assert c.post(REFRESH, headers={"Origin": "http://evil.example"}).status_code == 403
    # not switched on yet: a fixed sentence, nothing else
    r = c.post(REFRESH)
    assert r.status_code == 409 and r.json()["detail"] in (REFRESH_TEXT["off"], REFRESH_TEXT["down"])
    assert c.put("/api/admin/spl", json={"enabled": True, "host": "", "port": None,
                                         "meters": [{"source": "", "metric": "SPL A Slow"}]}).status_code == 200
    wait(lambda: c.get("/api/admin/state").json()["spl"]["inputs"])
    r = c.post(REFRESH)
    assert r.status_code == 200 and r.json() == {"ok": True, "result": "Refreshed: 2 inputs, 4 metrics"}
    r = c.post(REFRESH)                                                       # straight away again
    assert r.status_code == 429 and r.json()["detail"] == REFRESH_TEXT["recent"]
    assert c.get("/api/admin/state").json()["spl"]["inputs"] == list(INPUT_LABELS)
