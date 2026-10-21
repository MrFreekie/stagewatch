"""First-generation Shelly meters (Shelly EM): found by falling back when /rpc is a 404, read from
/status, and still read-only. Fixtures are real replies from a Shelly EM (fw v1.14.0), MAC anonymised."""

from __future__ import annotations

import asyncio
import json

import pytest

from stagewatch.core.config import ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.integrations.shelly import ShellyIntegration
from stagewatch.integrations.shelly.emulate import DEMO1_HOST, DEMO_HOST, EmulatedFleet
from stagewatch.integrations.shelly.http import ALLOWED_METHODS, ShellyError, get_json, rpc_path
from stagewatch.integrations.shelly.readings import gen1_status, readings_from_status
from stagewatch.integrations.shelly.transport import (HttpPollTransport, Target, identify, parse_gen1_info,
                                                      read_status)
from test_ontime import until
from test_shelly import FIX, FakeShelly, Recorder, by_id, fx, loose

GEN1_MAC = "025e00000098"


class FakeGen1:
    """A first-generation meter: /rpc/... is a 404; /shelly and /status answer. Logs every request line."""

    def __init__(self, info=None, status=None):
        self.requests = []
        self.info = info if info is not None else fx("gen1-shem-shelly.json")
        self.status = status if status is not None else fx("gen1-shem-status.json")

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            line = (await reader.readuntil(b"\r\n\r\n")).split(b"\r\n")[0].decode()
            self.requests.append(line)
            path = line.split(" ")[1]
            if path in ("/shelly", "/status"):
                body = json.dumps(self.info if path == "/shelly" else self.status).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def fetcher(srv):
    async def fetch(host, method):
        return await get_json(host, method, port=srv.port, address_ok=loose)
    return fetch


def test_the_allow_list_is_the_read_methods():
    assert ALLOWED_METHODS == frozenset({"Shelly.GetDeviceInfo", "Shelly.GetStatus", "Shelly.GetConfig", "gen1.shelly", "gen1.status", "gen1.settings"})
    assert rpc_path("gen1.shelly") == "/shelly" and rpc_path("gen1.status") == "/status"
    for bad in ("/relay/0?turn=on", "gen1.relay", "gen1.reboot", "gen1.settings/x", "/shelly", "/status"):
        with pytest.raises(Exception):
            rpc_path(bad)


def test_gen1_info_from_the_real_shape_and_other_replies_are_refused():
    info = parse_gen1_info(fx("gen1-shem-shelly.json"))
    assert (info.mac, info.model, info.auth_enabled) == (GEN1_MAC, "SHEM", False)
    assert info.firmware.endswith("v1.14.0-gcb84623")
    assert parse_gen1_info({**fx("gen1-shem-shelly.json"), "auth": True}).auth_enabled is True
    for bad in (None, [], {}, {"mac": "025E00000098"}, {**fx("gen1-shem-shelly.json"), "gen": 2}, {"type": "SHEM", "mac": "x"}):
        with pytest.raises(ShellyError) as e:
            parse_gen1_info(bad)
        assert e.value.category == "not_shelly"


def test_gen1_emeters_become_channels_kept_exactly_as_sent():
    r = by_id(readings_from_status(gen1_status(fx("gen1-shem-status.json"))))
    assert r["gen1em_0_power"].value == 228.36 and r["gen1em_0_voltage"].value == 242.3
    assert r["gen1em_0_reactive"].value == -175.4 and r["gen1em_0_pf"].value == -0.79      # sign kept as sent
    assert r["gen1em_0_energy"].value == 1615941.4 and r["gen1em_0_energy"].cumulative      # watt-hours, not kWh
    assert r["gen1em_0_energy_ret"].value == 97.8 and r["gen1em_1_power"].value == 61.5
    assert "gen1em_0_current" not in r and not any("relay" in k or "ison" in k for k in r)   # relay never read


def test_gen1_channel_that_is_not_valid_gives_none_not_zero():
    st = fx("gen1-shem-status.json")
    st["emeters"][0]["is_valid"] = False
    r = by_id(readings_from_status(gen1_status(st)))
    assert r["gen1em_0_power"].value is None and r["gen1em_1_power"].value == 61.5


@pytest.mark.parametrize("junk", [None, [], {}, {"emeters": None}, {"emeters": "x"}, {"emeters": [None, 5, []]},
                                  {"emeters": [{"power": "x"}]}])
def test_hostile_gen1_status_never_raises(junk):
    readings_from_status(gen1_status(junk))


async def test_a_gen1_meter_is_found_by_falling_back_when_rpc_is_404():
    async with FakeGen1() as srv:
        fetch = fetcher(srv)
        info, gen1 = await identify(fetch, "127.0.0.1")
        assert gen1 and info.mac == GEN1_MAC
        assert readings_from_status(await read_status(fetch, "127.0.0.1", gen1))
        assert srv.requests == ["GET /rpc/Shelly.GetDeviceInfo HTTP/1.1", "GET /shelly HTTP/1.1", "GET /status HTTP/1.1"]


async def test_a_gen2_meter_never_falls_back_to_gen1_paths():
    async with FakeShelly() as srv:
        info, gen1 = await identify(fetcher(srv), "127.0.0.1")
        assert not gen1 and srv.requests == ["GET /rpc/Shelly.GetDeviceInfo HTTP/1.1"]


async def test_a_gen1_meter_with_a_password_is_reported_not_read():
    async with FakeGen1(info={**fx("gen1-shem-shelly.json"), "auth": True}) as srv:
        sink = Recorder()

        async def fast_sleep(d):
            await asyncio.sleep(0)

        t = HttpPollTransport(fetcher(srv), sleep=fast_sleep)
        await t.start(sink)
        t.add(Target(GEN1_MAC, "127.0.0.1", 3.0))
        await until(lambda: sink.events)
        await t.stop()
        assert sink.events[0] == ("lost", "needs_password", 1)
        assert "GET /status HTTP/1.1" not in srv.requests


async def test_the_whole_loop_reads_a_gen1_meter_with_only_the_allowed_gets(tmp_path):
    async with FakeGen1() as srv:
        hub = Hub(tmp_path)
        hub.config.shelly_devices.append(ShellyDeviceConfig(id="em", host="127.0.0.1", mac=GEN1_MAC, name="FOH power", poll_interval_s=2))
        integ = ShellyIntegration(hub, fetch=fetcher(srv))
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: hub.entities.get("em.gen1em_0_power") is not None and hub.entities["em.gen1em_0_power"].value is not None)
            assert hub.entities["em.gen1em_0_power"].value == 228.36 and hub.entities["em.gen1em_1_power"].value == 61.5
            assert hub.entities["em.gen1em_0_energy"].value == 1615941.4 and hub.entities["em.gen1em_0_energy"].unit == "Wh"
            assert hub.devices["em"].status == Status.OK
        finally:
            await integ.stop()
            hub.recorder.close()
        assert set(srv.requests) <= {"GET /rpc/Shelly.GetDeviceInfo HTTP/1.1", "GET /shelly HTTP/1.1", "GET /status HTTP/1.1",
                                  "GET /rpc/Shelly.GetConfig HTTP/1.1", "GET /settings HTTP/1.1"}


async def test_the_emulated_gen1_meter_has_no_rpc_and_answers_the_gen1_paths():
    t = [9_000.0]
    fleet = EmulatedFleet(clock=lambda: t[0], outages=False)
    with pytest.raises(ShellyError) as e:
        await fleet.fetch(DEMO1_HOST, "Shelly.GetDeviceInfo")
    assert e.value.category == "not_shelly"
    assert (await fleet.fetch(DEMO1_HOST, "gen1.shelly"))["type"] == "SHEM"
    first = await fleet.fetch(DEMO1_HOST, "gen1.status")
    t[0] += 3600.0
    second = await fleet.fetch(DEMO1_HOST, "gen1.status")
    grown = second["emeters"][0]["total"] - first["emeters"][0]["total"]
    assert grown == pytest.approx(second["emeters"][0]["power"], abs=0.2)     # 1 W for 1 h = 1 Wh
    with pytest.raises(ShellyError):
        await fleet.fetch(DEMO_HOST, "gen1.shelly")                           # the Gen2 simulator has no Gen1 paths
