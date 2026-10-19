"""Shelly power meters (phase 1: HTTP polling, read only).

What is REAL and what is SYNTHETIC. ``pmmini-gen3-*.json`` have the shape of what a Shelly PM Mini Gen3
answered on 10/10/2026 (read-only GETs), with the address and MAC replaced by made-up ones. Every other
``docs-*.json`` file is SYNTHETIC: built here from the field names in Shelly's documentation, never
captured from a device. All addresses are 127.0.0.1 or 192.0.2.x (documentation) and all MACs start
02:5e (locally administered), so nothing here is a real device or site.

Expected numbers are worked out by hand in the docstrings.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import create_model

from stagewatch import acoustics
from stagewatch.core import cards
from stagewatch.core.calibration import set_calibration
from stagewatch.core.config import (SHELLY_POLL_DEFAULT_S, Config, ConfigStore, ShellyDeviceConfig, Threshold, ShellyChannelConfig, _Model)
from stagewatch.core.hub import Hub
from stagewatch.core.model import ENV_KINDS, UNITS, Device, Entity, Kind, Status
from stagewatch.integrations.shelly import (InUse, ShellyIntegration, seed_emulate_shelly, validate_host)
from stagewatch.integrations.shelly import readings as rd
from stagewatch.integrations.shelly.emulate import DEMO3_HOST, DEMO_HOST, EmulatedFleet, EmulatedMeter, emulated_mac
from stagewatch.integrations.shelly.http import (ALLOWED_METHODS, ForbiddenMethod, ShellyError, get_json, rpc_path)
from stagewatch.integrations.shelly.transport import (HttpPollTransport, Target, Transport, parse_device_info)
from stagewatch.web.server import create_app
from test_ontime import until
from test_static_compat import FORBIDDEN

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "shelly"
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
PKG = ROOT / "src" / "stagewatch" / "integrations" / "shelly"

MAC = "025e00000099"
HOST = "192.0.2.50"


def fx(name: str) -> dict:
    data = json.loads((FIX / name).read_text(encoding="utf-8"))
    data.pop("_note", None)
    return data


def by_id(items):
    return {r.object_id: r for r in items}


# ===================================================================== the reading layer (pure)
def test_real_pm_mini_gen3_status_gives_five_readings_and_no_optional_ones():
    """The real PM Mini Gen3 shape: voltage 244.8 V, current 0.000 A, apower 0.0 W, freq 50.0 Hz and the
    counter 15662.781 Wh. It sent no aprtpower and no pf, so those come out as None (never 0)."""
    got = by_id(rd.readings_from_status(fx("pmmini-gen3-getstatus.json")))
    assert got["pm1_0_voltage"].value == 244.8 and got["pm1_0_voltage"].kind == Kind.VOLTAGE
    assert got["pm1_0_current"].value == 0.0 and got["pm1_0_current"].kind == Kind.CURRENT
    assert got["pm1_0_power"].value == 0.0 and got["pm1_0_power"].kind == Kind.POWER
    assert got["pm1_0_freq"].value == 50.0 and got["pm1_0_freq"].kind == Kind.FREQUENCY
    assert got["pm1_0_energy"].value == 15662.781 and got["pm1_0_energy"].cumulative is True
    assert got["pm1_0_pf"].value is None
    # it sends no apparent power, so one is calculated: 244.8 V x 0.000 A = 0.0 VA, labelled as calculated
    assert got["pm1_0_apparent"].value == 0.0 and got["pm1_0_apparent"].calculated == "volts x amps"
    # and a calculated reactive power from 0 VA and 0 W = 0 var, so seven readings in all
    assert got["pm1_0_reactive"].value == 0.0 and got["pm1_0_reactive"].calculated
    assert sum(1 for r in got.values() if r.value is not None) == 7


def test_pm1_optional_fields_appear_when_the_meter_sends_them():
    got = by_id(rd.readings_from_status(fx("docs-pm1-getstatus.json")))
    assert got["pm1_0_apparent"].value == 288.0 and got["pm1_0_apparent"].kind == Kind.APPARENT_POWER
    assert got["pm1_0_pf"].value == 0.97 and got["pm1_0_pf"].kind == Kind.POWER_FACTOR
    assert got["pm1_0_power"].value == 280.5


def test_energy_is_kept_exactly_as_received_in_watt_hours():
    """1234.5 Wh stays 1234.5 (not 1.2345 kWh, not rounded); by_minute and returned energy are not read."""
    got = by_id(rd.readings_from_status(fx("docs-pm1-getstatus.json")))
    assert got["pm1_0_energy"].value == 1234.5 and UNITS[Kind.ENERGY] == "Wh"
    assert not any("by_minute" in r.object_id or "ret" in r.object_id for r in got.values())


def test_three_phase_em_with_energy_totals():
    """Phase A is the documentation's own example: 4.029 A, 236.1 V, 951.2 W. The neutral is null (no CT)
    so it is None; totals 6.029 A and 1421.2 W are passed on, not recalculated."""
    got = by_id(rd.readings_from_status(fx("docs-em-getstatus.json")))
    assert (got["em0_a_current"].value, got["em0_a_voltage"].value, got["em0_a_power"].value) == (4.029, 236.1, 951.2)
    assert got["em0_n_current"].value is None
    assert got["em0_c_pf"].value is None
    assert got["em0_total_current"].value == 6.029 and got["em0_total_power"].value == 1421.2
    assert got["emdata0_a_energy"].value == 5000.5 and got["emdata0_total_energy"].value == 8010.75
    assert all(r.kind == Kind.ENERGY and r.cumulative for k, r in got.items() if k.startswith("emdata"))


def test_single_circuit_em1_with_null_fields_gives_none_not_zero():
    got = by_id(rd.readings_from_status(fx("docs-em1-getstatus.json")))
    assert got["em1_0_power"].value == 700.0 and got["em1_0_apparent"].value == 740.0
    assert all(got[f"em1_1_{f}"].value is None for f in ("current", "voltage", "power", "apparent", "pf", "freq"))


def test_metering_relay_is_read_for_power_only_and_a_plain_relay_gives_nothing():
    """switch:0 has apower so its power, volts, amps, frequency, energy and internal temperature are read.
    Its `output` (on or off) is never read. switch:1 has no apower, so nothing comes from it."""
    got = by_id(rd.readings_from_status(fx("docs-switch-getstatus.json")))
    assert got["switch_0_power"].value == 1500.0 and got["switch_0_energy"].value == 98765.4
    assert got["switch_0_device_temp"].value == 41.2 and got["switch_0_device_temp"].kind == Kind.TEMPERATURE
    assert not any(k.startswith("switch_1") for k in got)
    assert not any("output" in k or "relay" in k for k in got)


def test_components_are_chosen_by_presence_not_by_model_name():
    status = {"sys": {"model": "ANYTHING"}, "pm1:3": {"apower": 5.0}, "wifi": {}, "input:0": {}}
    assert rd.components(status) == [("pm1", 3)]
    assert rd.readings_from_status({"sys": {}, "wifi": {}}) == []


@pytest.mark.parametrize("junk", [None, "x", [], True, float("nan"), float("inf"), {"a": 1}, "12"])
def test_values_that_are_not_finite_numbers_are_none(junk):
    status = {"pm1:0": {"voltage": junk, "current": junk, "apower": junk, "freq": junk, "aenergy": junk}}
    assert all(r.value is None for r in rd.readings_from_status(status))


@pytest.mark.parametrize("status", [None, [], "x", 3, {"pm1:0": None}, {"pm1:0": []}, {"em:0": {"a_current": {}}},
                                    {"pm1:0": {"aenergy": {"total": "x"}}}, {"pm1:999999": {}}, {f"pm1:{i}": 1 for i in range(50)}])
def test_hostile_status_never_raises(status):
    rd.readings_from_status(status)
    rd.component_words(status)


def test_names_are_plain_and_channels_are_numbered_from_one():
    one = rd.readings_from_status(fx("docs-pm1-getstatus.json"))
    assert rd.display_name(by_id(one)["pm1_0_power"], rd.channel_counts(one)) == "Power"
    two = rd.readings_from_status(fx("docs-em1-getstatus.json"))
    assert rd.display_name(by_id(two)["em1_1_power"], rd.channel_counts(two)) == "Channel 2 power"
    em = rd.readings_from_status(fx("docs-em-getstatus.json"))
    assert rd.display_name(by_id(em)["em0_a_current"], rd.channel_counts(em)) == "Line 1 (Brown) current"
    assert rd.display_name(by_id(em)["em0_total_power"], rd.channel_counts(em)) == "Total power"
    assert rd.component_words(fx("docs-pm1-getstatus.json")) == ["Power meter"]


def test_power_kinds_are_not_air_kinds():
    for kind in (Kind.POWER, Kind.APPARENT_POWER, Kind.CURRENT, Kind.VOLTAGE, Kind.ENERGY, Kind.POWER_FACTOR, Kind.FREQUENCY):
        assert kind not in ENV_KINDS
    assert [UNITS[k] for k in (Kind.POWER, Kind.APPARENT_POWER, Kind.CURRENT, Kind.VOLTAGE, Kind.ENERGY, Kind.POWER_FACTOR, Kind.FREQUENCY)] \
        == ["W", "VA", "A", "V", "Wh", "", "Hz"]


# ============================================================================ the device info
def test_device_info_from_the_real_shape():
    info = parse_device_info(fx("pmmini-gen3-deviceinfo.json"))
    assert (info.mac, info.model, info.app, info.firmware, info.auth_enabled) == (MAC, "S3PM-001PCEU16", "MiniPMG3", "2.1.0-beta2", False)


@pytest.mark.parametrize("data", [None, [], {}, {"mac": "025E00000099"}, {"mac": "025E00000099", "gen": 1},
                                  {"mac": "nonsense", "gen": 3}, {"mac": "025E00000099", "gen": True}, {"mac": 5, "gen": 3}])
def test_device_info_that_is_not_a_gen2_plus_shelly_is_refused(data):
    with pytest.raises(ShellyError) as e:
        parse_device_info(data)
    assert e.value.category == "not_shelly"


def test_device_with_a_password_is_reported_in_words_not_read():
    info = parse_device_info({**fx("pmmini-gen3-deviceinfo.json"), "auth_en": True})
    assert info.auth_enabled is True


# ==================================================================== the read-only allow-list
FORBIDDEN_METHODS = ["Switch.Set", "Switch.Toggle", "Switch.SetConfig", "Cover.Open", "Cover.GoToPosition", "Light.Set",
                     "Shelly.Reboot", "Shelly.Update", "Shelly.FactoryReset", "Shelly.SetAuth", "Shelly.GetStatusX",
                     "Ws.SetConfig", "Mqtt.SetConfig", "Script.Start", "Webhook.Create", "EMData.DeleteAllData",
                     "EMData.ResetCounters", "EM.GetStatus", "PM1.ResetCounters", "Shelly.GetConfig", "Shelly.GetComponents",
                     "shelly.getstatus", "Shelly.GetStatus/../Shelly.Reboot", "Shelly.GetStatus?x=1", "", " Shelly.GetStatus"]


def test_the_allow_list_is_exactly_the_read_methods():
    assert ALLOWED_METHODS == frozenset({"Shelly.GetDeviceInfo", "Shelly.GetStatus", "gen1.shelly", "gen1.status"})
    assert rpc_path("Shelly.GetStatus") == "/rpc/Shelly.GetStatus"
    assert rpc_path("Shelly.GetDeviceInfo") == "/rpc/Shelly.GetDeviceInfo"


@pytest.mark.parametrize("method", FORBIDDEN_METHODS)
def test_every_other_method_is_refused_before_any_request(method):
    with pytest.raises(ForbiddenMethod):
        rpc_path(method)


async def test_a_refused_method_never_reaches_the_network():
    async with FakeShelly() as srv:
        for method in FORBIDDEN_METHODS:
            with pytest.raises(ForbiddenMethod):
                await get_json("127.0.0.1", method, port=srv.port, address_ok=lambda ip: True)
        assert srv.requests == []


def test_no_path_can_switch_a_relay_or_use_the_old_relay_url():
    for m in ALLOWED_METHODS:
        path = rpc_path(m)
        assert "turn=" not in path and "/relay/" not in path and "Switch" not in path and "?" not in path


def test_only_the_http_module_opens_connections():
    """A guard on the source: nothing else in the Shelly package opens a socket or makes a request."""
    for p in PKG.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for needle in ("open_connection", "import socket", "urllib", "import httpx", "import requests", "aiohttp", "websockets"):
            if p.name == "http.py" and needle == "open_connection":
                continue
            assert needle not in text, f"{p.name} uses {needle}"


# ========================================================================== a fake Shelly (HTTP)
class FakeShelly:
    """An in-process HTTP server that records every request line and answers in the mode asked for."""

    def __init__(self, mode="ok", info=None, status=None):
        self.mode, self.requests, self.server = mode, [], None
        self.info = info if info is not None else fx("pmmini-gen3-deviceinfo.json")
        self.status = status if status is not None else fx("pmmini-gen3-getstatus.json")

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            line = head.split(b"\r\n")[0].decode()
            self.requests.append(line)
            path = line.split(" ")[1]
            body = json.dumps(self.info if path.endswith("GetDeviceInfo") else self.status).encode()
            mode = self.mode
            if mode == "redirect":
                writer.write(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/rpc/Switch.Set\r\nContent-Length: 0\r\n\r\n")
            elif mode == "auth":
                writer.write(b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Digest realm=\"x\"\r\nContent-Length: 0\r\n\r\n")
            elif mode == "notfound":
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nSECRET-ME")
            elif mode == "chunked":
                writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                half = len(body) // 2
                for part in (body[:half], body[half:]):
                    writer.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                writer.write(b"0\r\n\r\n")
            elif mode == "bigheader":
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {300 * 1024}\r\n\r\n".encode())
            elif mode == "bigstream":
                writer.write(b"HTTP/1.1 200 OK\r\n\r\n" + b"{" + b" " * (300 * 1024))
            elif mode == "drip":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n{")
                await writer.drain()
                await asyncio.sleep(1)
            elif mode == "garbage":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\nnot json!!!!")
            elif mode == "array":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]")
            elif mode == "silent":
                await asyncio.sleep(1)
            else:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                             + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def loose(ip):
    return True


async def ask(srv, method="Shelly.GetStatus", **kw):
    return await get_json("127.0.0.1", method, port=srv.port, address_ok=loose, **kw)


async def test_get_json_reads_both_allowed_methods_with_plain_gets():
    async with FakeShelly() as srv:
        assert (await ask(srv, "Shelly.GetDeviceInfo"))["mac"] == "025E00000099"
        assert (await ask(srv))["pm1:0"]["voltage"] == 244.8
        assert srv.requests == ["GET /rpc/Shelly.GetDeviceInfo HTTP/1.1", "GET /rpc/Shelly.GetStatus HTTP/1.1"]


async def test_chunked_replies_are_read():
    async with FakeShelly("chunked") as srv:
        assert (await ask(srv))["pm1:0"]["current"] == 0.0


async def test_a_redirect_is_never_followed():
    async with FakeShelly("redirect") as srv:
        with pytest.raises(ShellyError) as e:
            await ask(srv)
        assert e.value.category == "redirect"
        assert len(srv.requests) == 1 and not any("Switch" in r for r in srv.requests)


@pytest.mark.parametrize(("mode", "category"), [("auth", "needs_password"), ("notfound", "not_shelly"), ("bigheader", "too_large"),
                                                ("bigstream", "too_large"), ("garbage", "bad_reply"), ("array", "bad_reply")])
async def test_replies_that_are_not_a_shelly_status_become_a_fixed_category(mode, category):
    async with FakeShelly(mode) as srv:
        with pytest.raises(ShellyError) as e:
            await ask(srv)
        assert e.value.category == category
        assert "SECRET-ME" not in str(e.value) and "not json" not in str(e.value)


@pytest.mark.parametrize("mode", ["drip", "silent"])
async def test_a_slow_or_silent_meter_times_out(mode):
    async with FakeShelly(mode) as srv:
        with pytest.raises(ShellyError) as e:
            await ask(srv, timeout=0.3)
        assert e.value.category == "timeout"


async def test_a_closed_port_is_refused_or_unreachable():
    async with FakeShelly() as srv:
        port = srv.port
    with pytest.raises(ShellyError) as e:
        await get_json("127.0.0.1", "Shelly.GetStatus", port=port, address_ok=loose, timeout=1.0)
    assert e.value.category in ("refused", "unreachable", "timeout")      # Windows can take a while to say "refused"


@pytest.mark.parametrize("host", ["127.0.0.1", "8.8.8.8", "169.254.1.1", "224.0.0.1", "0.0.0.0", "::1", "::ffff:192.168.1.5",
                                  "shelly.local", "localhost", "", "192.168.1.5/x", "1.2.3", None, 5, 3232235781, "3232235781"])
async def test_only_private_ip_addresses_are_ever_contacted(host):
    """The default check refuses loopback, public, link-local, multicast, unspecified, IPv4-mapped and
    every host name, before a connection is made."""
    with pytest.raises(ShellyError) as e:
        await get_json(host, "Shelly.GetStatus")
    assert e.value.category == "not_local"


@pytest.mark.parametrize("host", ["192.168.1.5", "10.0.0.7", "172.16.4.4", "192.0.2.50", "fd00::5"])
def test_private_addresses_pass_the_check(host):
    assert validate_host(host) == host


def test_validate_host_refuses_names_and_public_addresses():
    for bad in ("example.com", "8.8.8.8", "127.0.0.1", " ", 5, None):
        with pytest.raises(ShellyError):
            validate_host(bad)
    assert validate_host(" 192.168.1.5 ") == "192.168.1.5"


# ====================================================================== the transport (polling)
class Recorder:
    def __init__(self):
        self.events = []

    def identified(self, mac, info):
        self.events.append(("identified", mac))

    def status(self, mac, status, ts):
        self.events.append(("status", mac))

    def lost(self, mac, category, failures):
        self.events.append(("lost", category, failures))


class ScriptedFetch:
    """Answers GetDeviceInfo and GetStatus; ``plan`` is a list of categories to fail with, one per GetStatus."""

    def __init__(self, plan=(), info=None):
        self.plan, self.calls = list(plan), []
        self.info = info or fx("pmmini-gen3-deviceinfo.json")

    async def __call__(self, host, method):
        self.calls.append(method)
        rpc_path(method)
        if method == "Shelly.GetDeviceInfo":
            return copy.deepcopy(self.info)
        if self.plan:
            cat = self.plan.pop(0)
            if cat:
                raise ShellyError(cat)
        return fx("pmmini-gen3-getstatus.json")


async def run_transport(fetch, polls, interval=3.0):
    """Run the polling loop with an instant sleep, until the sink has seen ``polls`` events."""
    sink, delays = Recorder(), []

    async def fast_sleep(d):
        delays.append(d)
        await asyncio.sleep(0)

    t = HttpPollTransport(fetch, sleep=fast_sleep)
    await t.start(sink)
    t.add(Target(MAC, HOST, interval))
    await until(lambda: len([e for e in sink.events if e[0] in ("status", "lost")]) >= polls)
    await t.stop()
    return sink, delays


async def test_polling_backs_off_after_failures_and_checks_identity_again():
    """Interval 3 s. Failure n waits 3 x 2^n s, capped at 60: 6, 12, 24. A success goes back to 3 s. After
    every failure the meter is asked who it is again (GetDeviceInfo) before its status is trusted."""
    fetch = ScriptedFetch(plan=["timeout", "timeout", "timeout", None])
    sink, delays = await run_transport(fetch, 4)
    assert [e for e in sink.events if e[0] == "lost"] == [("lost", "timeout", 1), ("lost", "timeout", 2), ("lost", "timeout", 3)]
    assert delays[:4] == [6.0, 12.0, 24.0, 3.0]
    assert fetch.calls.count("Shelly.GetDeviceInfo") == 4 and set(fetch.calls) <= ALLOWED_METHODS


async def test_backoff_never_goes_past_a_minute():
    fetch = ScriptedFetch(plan=["timeout"] * 9)
    sink, delays = await run_transport(fetch, 9)
    assert max(delays) == 60.0


async def test_a_different_meter_at_the_address_is_never_read():
    other = {**fx("pmmini-gen3-deviceinfo.json"), "mac": "025E000000AA"}
    sink, _ = await run_transport(ScriptedFetch(info=other), 2)
    assert ("status", MAC) not in sink.events and ("lost", "other_meter", 1) in sink.events


async def test_a_meter_with_a_password_is_reported_not_read():
    locked = {**fx("pmmini-gen3-deviceinfo.json"), "auth_en": True}
    fetch = ScriptedFetch(info=locked)
    sink, _ = await run_transport(fetch, 1)
    assert sink.events[0] == ("lost", "needs_password", 1) and "Shelly.GetStatus" not in fetch.calls


async def test_the_whole_loop_against_a_fake_server_asks_only_the_two_allowed_things(tmp_path):
    """The real client, the real transport and the real integration against a fake meter: afterwards the
    server's own log shows only plain GETs of the two allowed paths. Anything else fails this test."""
    async with FakeShelly() as srv:
        hub = Hub(tmp_path)
        hub.config.shelly_devices.append(ShellyDeviceConfig(id="rack", host="127.0.0.1", mac=MAC, name="Rack", poll_interval_s=2))

        async def fetch(host, method):
            return await get_json(host, method, port=srv.port, address_ok=loose)

        integ = ShellyIntegration(hub, fetch=fetch)
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: hub.entities.get("rack.pm1_0_power") is not None and hub.entities["rack.pm1_0_power"].value is not None)
            assert hub.devices["rack"].status == Status.OK
        finally:
            await integ.stop()
            hub.recorder.close()
        assert srv.requests, "nothing was asked"
        assert set(srv.requests) <= {"GET /rpc/Shelly.GetDeviceInfo HTTP/1.1", "GET /rpc/Shelly.GetStatus HTTP/1.1"}


# ===================================================================== through the hub (sink calls)
class NullTransport(Transport):
    name = "test"

    def __init__(self):
        self.targets = {}

    async def start(self, sink):
        pass

    async def stop(self):
        pass

    def add(self, target):
        self.targets[target.mac] = target

    def remove(self, mac):
        self.targets.pop(mac, None)


class Fetch:
    """A scripted meter for the integration's admin calls (test, add, update)."""

    def __init__(self, status=None, info=None):
        self.status = status or fx("pmmini-gen3-getstatus.json")
        self.info = info or fx("pmmini-gen3-deviceinfo.json")
        self.calls, self.fail = [], None

    async def __call__(self, host, method):
        rpc_path(method)
        self.calls.append((host, method))
        if self.fail:
            raise ShellyError(self.fail)
        return copy.deepcopy(self.info if method == "Shelly.GetDeviceInfo" else self.status)


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    yield h
    h.recorder.close()


async def make(hub, cfg=None, status=None, fetch=None):
    cfg = cfg or ShellyDeviceConfig(id="rack", host=HOST, mac=MAC, name="Mic rack", area="FOH")
    hub.config.shelly_devices.append(cfg)
    integ = ShellyIntegration(hub, fetch=fetch or Fetch(), transport=NullTransport())
    hub.add_integration(integ)
    await integ.start()
    return integ, cfg


def feed(integ, status, ts):
    integ.status(MAC, copy.deepcopy(status), ts)


async def test_real_pm_mini_status_makes_equipment_entities_in_canonical_units(hub):
    integ, _ = await make(hub)
    feed(integ, fx("pmmini-gen3-getstatus.json"), 1000.0)
    snap = {e["id"]: e for e in hub.snapshot()["entities"]}
    power = snap["rack.pm1_0_power"]
    assert (power["kind"], power["unit"], power["value"], power["role"]) == ("power", "W", 0.0, "equipment")
    assert snap["rack.pm1_0_voltage"]["unit"] == "V" and snap["rack.pm1_0_voltage"]["value"] == 244.8
    assert snap["rack.pm1_0_energy"]["unit"] == "Wh" and snap["rack.pm1_0_energy"]["value"] == 15662.781
    assert snap["rack.pm1_0_freq"]["unit"] == "Hz"
    assert "rack.pm1_0_pf" not in snap    # this meter sends no power factor
    assert snap["rack.pm1_0_apparent"]["labels"]["calculated"] == "volts x amps" and snap["rack.pm1_0_apparent"]["value"] == 0.0
    assert all(snap[k]["role"] == "equipment" for k in snap if k.startswith("rack."))
    assert hub.devices["rack"].status == Status.OK and hub.devices["rack"].category == "meter"
    assert snap["rack.pm1_0_power"]["labels"] == {"channel": "pm1_0", "field": "power"}


async def test_optional_readings_get_entities_only_when_the_meter_sends_them(hub):
    integ, _ = await make(hub)
    feed(integ, fx("docs-pm1-getstatus.json"), 1000.0)
    assert {"rack.pm1_0_apparent", "rack.pm1_0_pf"} <= set(hub.entities)


async def test_public_snapshot_has_no_address_mac_or_firmware(hub):
    integ, _ = await make(hub)
    integ.identified(MAC, parse_device_info(fx("pmmini-gen3-deviceinfo.json")))
    feed(integ, fx("pmmini-gen3-getstatus.json"), 1000.0)
    text = json.dumps(hub.snapshot()) + json.dumps(hub.devices["rack"].to_dict())
    for secret in (HOST, MAC, MAC.upper(), "025e", "2.1.0-beta2", "S3PM", "mac:"):
        assert secret.lower() not in text.lower(), secret
    assert set(hub.devices["rack"].to_dict()) == {"id", "name", "integration", "category", "manufacturer", "model", "area", "status", "status_detail"}
    admin = integ.admin_status()["meters"][0]
    assert admin["host"] == HOST and admin["mac"] == MAC and admin["firmware"] == "2.1.0-beta2"


async def test_hardware_key_follows_the_mac(hub):
    integ, _ = await make(hub)
    feed(integ, fx("pmmini-gen3-getstatus.json"), 1000.0)
    assert hub.entities["rack.pm1_0_power"].hw_key == f"mac:{MAC}/pm1_0_power"
    assert hub.devices["rack"].hw_id == f"mac:{MAC}"


async def test_unchanged_values_are_recorded_at_most_every_ten_seconds(hub):
    """Same reading polled every 3 s: recorded at 100, skipped at 103, 106 and 109, recorded again at 110
    (10 s after 100). A changed value is recorded at once."""
    integ, _ = await make(hub)
    status = fx("pmmini-gen3-getstatus.json")
    ent = lambda: hub.entities["rack.pm1_0_voltage"].updated   # noqa: E731
    seen = []
    for ts in (100.0, 103.0, 106.0, 109.0, 110.0):
        feed(integ, status, ts)
        seen.append(ent())
    assert seen == [100.0, 100.0, 100.0, 100.0, 110.0]
    changed = copy.deepcopy(status)
    changed["pm1:0"]["voltage"] = 245.1
    feed(integ, changed, 111.0)
    assert ent() == 111.0 and hub.entities["rack.pm1_0_voltage"].value == 245.1


async def test_offline_is_a_gap_with_frozen_values_never_zero_and_one_quiet_notice(hub):
    """Two missed polls are a blip. The third makes the meter MISSING with a silent level-1 notice. The
    last reading stays exactly as it was, with its old time; nothing is written as zero."""
    integ, _ = await make(hub)
    status = fx("docs-pm1-getstatus.json")
    feed(integ, status, 1000.0)
    power = hub.entities["rack.pm1_0_power"]
    assert power.value == 280.5
    integ.lost(MAC, "timeout", 1)
    integ.lost(MAC, "timeout", 2)
    assert hub.devices["rack"].status == Status.OK
    integ.lost(MAC, "timeout", 3)
    assert hub.devices["rack"].status == Status.MISSING
    assert hub.devices["rack"].status_detail.startswith("No reply since ")
    assert (power.value, power.updated) == (280.5, 1000.0)
    alarm = hub.alarms.active["device:rack"]
    assert alarm.silent is True and alarm.level == 1
    integ.lost(MAC, "timeout", 4)
    assert (power.value, power.updated) == (280.5, 1000.0)
    assert not [m for m in hub.recorder.markers() if "back" in m.label]


async def test_meter_back_marker_is_added_once_and_only_after_a_gap_seen_live(hub):
    integ, _ = await make(hub)
    status = fx("pmmini-gen3-getstatus.json")
    feed(integ, status, 1000.0)
    feed(integ, status, 1003.0)
    assert [m.label for m in hub.recorder.markers() if m.source == "shelly"] == []     # the first reading is not "back"
    for n in (1, 2, 3):
        integ.lost(MAC, "timeout", n)
    feed(integ, status, 1100.0)
    feed(integ, status, 1103.0)
    labels = [m.label for m in hub.recorder.markers() if m.source == "shelly"]
    assert labels == ["Mic rack: meter back"]
    assert hub.devices["rack"].status == Status.OK
    assert "device:rack" not in hub.alarms.active


async def test_a_problem_that_is_not_a_blip_shows_at_once_in_plain_words(hub):
    integ, _ = await make(hub)
    integ.lost(MAC, "needs_password", 1)
    dev = hub.devices["rack"]
    assert dev.status == Status.FAULT and dev.status_detail == "Needs a password: not supported yet"
    assert hub.alarms.active["device:rack"].silent is True
    integ.lost(MAC, "other_meter", 1)
    assert hub.devices["rack"].status_detail == "A different meter answered at the saved address"


async def test_missing_before_any_reading_says_so(hub):
    integ, _ = await make(hub)
    for n in (1, 2, 3):
        integ.lost(MAC, "refused", n)
    assert hub.devices["rack"].status == Status.MISSING and hub.devices["rack"].status_detail == "No reply yet"


async def test_a_status_with_no_power_readings_is_a_quiet_fault(hub):
    integ, _ = await make(hub)
    feed(integ, {"sys": {}, "wifi": {}}, 1000.0)
    assert hub.devices["rack"].status == Status.FAULT
    assert not [e for e in hub.entities if e.startswith("rack.")]


async def test_null_fields_never_become_entities_or_zeros(hub):
    integ, _ = await make(hub)
    feed(integ, fx("docs-em1-getstatus.json"), 1000.0)
    assert "rack.em1_0_power" in hub.entities and not any(e.startswith("rack.em1_1_") for e in hub.entities)


async def test_energy_going_backwards_is_recorded_as_received_and_noted(hub):
    """Counter 500 Wh, then 120 Wh (someone reset the meter). 120 is recorded as it came; nothing is
    subtracted or invented; the admin line says when it went back."""
    integ, _ = await make(hub)
    status = fx("docs-pm1-getstatus.json")
    status["pm1:0"]["aenergy"]["total"] = 500.0
    feed(integ, status, 1000.0)
    assert integ.admin_status()["meters"][0]["energy_went_back"] == ""
    status["pm1:0"]["aenergy"]["total"] = 120.0
    feed(integ, status, 1003.0)
    assert hub.entities["rack.pm1_0_energy"].value == 120.0
    assert integ.admin_status()["meters"][0]["energy_went_back"] != ""


async def test_energy_is_never_averaged_or_smoothed(hub):
    integ, _ = await make(hub)
    status = fx("docs-pm1-getstatus.json")
    for ts, wh in ((1000.0, 100.0), (1003.0, 100.4), (1006.0, 100.9)):
        status["pm1:0"]["aenergy"]["total"] = wh
        feed(integ, status, ts)
        assert hub.entities["rack.pm1_0_energy"].value == wh


# ----------------------------------------------------------- Equipment never reaches an air value
async def test_a_hot_meter_never_moves_a_site_value(hub):
    """A 20.0 C air sensor gives site temperature 20.0. A metering relay reporting its own 70 C changes no
    site value, not the speed of sound, not the dew point."""
    hub.register_device(Device("air", "Air", "test"))
    hub.register_entity(Entity("air.temperature", "air", "Temperature", Kind.TEMPERATURE))
    hub.update_state("air.temperature", 20.0)
    hub.compute_site()
    before = {k: hub.entities[k].value for k in ("site.temperature", "site.speed_of_sound", "site.dew_point", "site.humidity", "site.pressure")}
    assert before["site.temperature"] == 20.0
    assert before["site.speed_of_sound"] == acoustics.speed_of_sound(20.0, 50.0, acoustics.pressure_at_altitude_pa(hub.config.site.altitude_m))
    integ, _ = await make(hub)
    hot = fx("docs-switch-getstatus.json")
    hot["switch:0"]["temperature"]["tC"] = 70.0
    feed(integ, hot, 1000.0)
    temp = hub.entities["rack.switch_0_device_temp"]
    assert temp.value == 70.0 and hub.role_of(temp) == "equipment"
    hub.compute_site()
    assert {k: hub.entities[k].value for k in before} == before
    assert hub.site_meta["sensors"]["temperature"] == 1


async def test_no_setting_can_turn_a_meter_reading_into_an_air_reading(hub):
    integ, _ = await make(hub)
    hot = fx("docs-switch-getstatus.json")
    hot["switch:0"]["temperature"]["tC"] = 70.0
    feed(integ, hot, 1000.0)
    temp = hub.entities["rack.switch_0_device_temp"]
    set_calibration(hub.config, temp.id, temp.hw_key, 5.0, True, role="environment")
    hub.update_state(temp.id, 70.0, 1001.0)
    assert hub.role_of(temp) == "equipment" and temp.value == 70.0      # no offset applied either
    hub.compute_site()
    assert hub.site_meta["sensors"]["temperature"] == 0


# ================================================================================== admin calls
async def test_test_shows_model_firmware_components_and_live_readings(hub):
    integ, _ = await make(hub)
    out = await integ.test("192.0.2.77")
    assert (out["model"], out["firmware"], out["mac"], out["components"]) == ("MiniPMG3", "2.1.0-beta2", MAC, ["Power meter"])
    names = {r["name"]: (r["value"], r["unit"]) for r in out["readings"]}
    assert names == {"Voltage": (244.8, "V"), "Current": (0.0, "A"), "Power": (0.0, "W"), "Frequency": (50.0, "Hz"), "Energy": (15662.781, "Wh"), "Apparent power (calculated)": (0.0, "VA"), "Reactive power (calculated)": (0.0, "var")}
    assert out["already_added"] == "rack"


async def test_test_refuses_public_addresses_and_names_without_asking(hub):
    fetch = Fetch()
    integ, _ = await make(hub, fetch=fetch)
    for host in ("8.8.8.8", "example.com", "127.0.0.1", ""):
        with pytest.raises(ShellyError) as e:
            await integ.test(host)
        assert e.value.category == "not_local"
    assert fetch.calls == []


async def test_test_of_a_meter_with_a_password_says_not_supported_yet(hub):
    integ, _ = await make(hub, fetch=Fetch(info={**fx("pmmini-gen3-deviceinfo.json"), "auth_en": True}))
    with pytest.raises(ShellyError) as e:
        await integ.test("192.0.2.77")
    assert e.value.category == "needs_password"


async def test_add_reads_the_mac_from_the_meter_and_starts_polling(hub):
    integ = ShellyIntegration(hub, fetch=Fetch(), transport=NullTransport())
    hub.add_integration(integ)
    await integ.start()
    cfg = await integ.add("192.0.2.60", "Mic rack", "FOH", 3.0)
    assert (cfg.id, cfg.mac, cfg.host) == ("mic_rack", MAC, "192.0.2.60")
    assert hub.config.shelly_devices == [cfg] and MAC in integ._transport.targets
    assert hub.devices["mic_rack"].status == Status.INITIALIZING and hub.devices["mic_rack"].model == "MiniPMG3"
    with pytest.raises(InUse):
        await integ.add("192.0.2.61", "Other name", "", 3.0)         # the same meter again
    assert len(hub.config.shelly_devices) == 1


async def test_add_refuses_a_name_already_in_use(hub):
    fetch = Fetch()
    integ = ShellyIntegration(hub, fetch=fetch, transport=NullTransport())
    hub.add_integration(integ)
    await integ.start()
    await integ.add("192.0.2.60", "Mic rack", "", 3.0)
    fetch.info = {**fetch.info, "mac": "025E000000AA"}
    with pytest.raises(InUse):
        await integ.add("192.0.2.61", "Mic Rack", "", 3.0)
    with pytest.raises(InUse):
        await integ.add("192.0.2.61", "Site", "", 3.0)


async def test_changing_the_address_must_be_the_same_meter(hub):
    fetch = Fetch()
    integ, cfg = await make(hub, fetch=fetch)
    await integ.update("rack", "New name", None, 4.0, "192.0.2.51")
    assert (cfg.host, cfg.name, cfg.poll_interval_s) == ("192.0.2.51", "New name", 4.0)
    assert hub.devices["rack"].name == "New name"
    fetch.info = {**fetch.info, "mac": "025E000000AA"}
    with pytest.raises(ShellyError) as e:
        await integ.update("rack", None, None, None, "192.0.2.52")
    assert e.value.category == "other_meter" and cfg.host == "192.0.2.51"


async def test_remove_keeps_history_and_forgets_the_meter(hub):
    integ, cfg = await make(hub)
    feed(integ, fx("pmmini-gen3-getstatus.json"), 1000.0)
    await integ.remove("rack")
    assert "rack" not in hub.devices and not hub.config.shelly_devices and MAC not in integ._transport.targets
    with pytest.raises(LookupError):
        await integ.remove("rack")


# ======================================================================================= config
def test_a_config_without_meters_loads_with_none():
    assert Config().shelly_devices == [] and Config.model_validate({"schema_version": 3}).shelly_devices == []


def test_config_round_trip_and_mac_is_normalised(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.load()
    store.config.shelly_devices = [ShellyDeviceConfig(id="Mic Rack", host="192.0.2.50", mac="02:5E:00:00:00:99", name="Mic rack", area="FOH", poll_interval_s=2.5)]
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load()
    assert again.shelly_devices == [ShellyDeviceConfig(id="mic_rack", host="192.0.2.50", mac=MAC, name="Mic rack", area="FOH", poll_interval_s=2.5)]
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert set(raw["shelly_devices"][0]) == {"id", "host", "mac", "name", "area", "poll_interval_s", "rating_a", "combined_bar", "channels", "role", "unit_no", "upstream"}   # no password field exists


def test_interval_outside_two_to_five_seconds_is_brought_into_range():
    assert ShellyDeviceConfig(id="a", host="192.0.2.1", mac=MAC, poll_interval_s=30).poll_interval_s == 5.0
    assert ShellyDeviceConfig(id="a", host="192.0.2.1", mac=MAC, poll_interval_s=0.1).poll_interval_s == 2.0
    assert ShellyDeviceConfig(id="a", host="192.0.2.1", mac=MAC).poll_interval_s == SHELLY_POLL_DEFAULT_S == 3.0


@pytest.mark.parametrize("bad", [{"host": "example.com"}, {"host": ""}, {"mac": "nope"}, {"mac": ""}, {"poll_interval_s": "fast"}, {"name": "x" * 61}])
def test_a_bad_meter_entry_is_refused(bad):
    good = {"id": "a", "host": "192.0.2.1", "mac": MAC}
    with pytest.raises(Exception):
        ShellyDeviceConfig.model_validate({**good, **bad})


def test_salvage_drops_only_the_bad_meter_and_keeps_the_rest(tmp_path):
    """A damaged meter entry costs that meter alone: the good one, the other sections and the PIN survive."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "site": {"name": "Kept"},
        "shelly_devices": [{"id": "good", "host": "192.0.2.1", "mac": MAC, "name": "Good"},
                           {"id": "bad", "host": "example.com", "mac": "025e0000009a"},
                           {"id": "worse", "host": "192.0.2.3", "mac": "nonsense"}]}), encoding="utf-8")
    cfg = ConfigStore(path).load()
    assert [d.id for d in cfg.shelly_devices] == ["good"] and cfg.site.name == "Kept"
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [d["id"] for d in saved["shelly_devices"]] == ["good"]


def test_older_build_ignores_the_meters_and_drops_them_on_its_next_save():
    """A build from before this feature has no ``shelly_devices`` field. It loads the file anyway, and
    its next save leaves the list out; this build then loads that file with no meters (downgrade, save,
    upgrade loses the meters, nothing else)."""
    old = create_model("OldConfig", __base__=_Model,
                       **{k: (f.annotation, f) for k, f in Config.model_fields.items() if k != "shelly_devices"})
    raw = yaml.safe_load(yaml.safe_dump(Config(shelly_devices=[ShellyDeviceConfig(id="a", host="192.0.2.1", mac=MAC)]).model_dump(mode="json")))
    assert "shelly_devices" in raw
    saved_by_old = old.model_validate(raw).model_dump(mode="json")
    assert "shelly_devices" not in saved_by_old
    assert Config.model_validate(saved_by_old).shelly_devices == []


# ================================================================================ the emulator
def test_emulated_meter_shape_matches_the_real_pm_mini_and_the_energy_adds_up():
    """The energy counter adds the power at each reading times the time since the last one: asked at t0
    and again an hour later, it has grown by exactly that later power in Wh (1 W for 1 h = 1 Wh)."""
    t = [1_000_000.0]
    m = EmulatedMeter(DEMO_HOST, clock=lambda: t[0], outages=False)
    first = m.status(t[0])
    assert set(first) == {"id", "voltage", "current", "apower", "freq", "aenergy", "ret_aenergy"}     # as the real one: no pf, no aprtpower
    t[0] += 3600.0
    second = m.status(t[0])
    assert second["aenergy"]["total"] - first["aenergy"]["total"] == pytest.approx(m.power_w(t[0]), abs=0.001)
    assert 162.0 <= m.power_w(t[0]) <= 388.0
    assert m.mac == emulated_mac(DEMO_HOST) == "025e00000301" and m.info()["mac"].startswith("025E")


async def test_emulated_meter_drops_out_for_forty_seconds_every_five_minutes():
    t = [5_000.0]
    fleet = EmulatedFleet(clock=lambda: t[0])
    await fleet.fetch(DEMO_HOST, "Shelly.GetStatus")                      # starts the clock at 5,000
    for offset, down in ((0, False), (239, False), (240, True), (279, True), (280, False), (300, False), (540, True)):
        t[0] = 5_000.0 + offset
        if down:
            with pytest.raises(ShellyError) as e:
                await fleet.fetch(DEMO_HOST, "Shelly.GetStatus")
            assert e.value.category == "timeout"
        else:
            assert "pm1:0" in await fleet.fetch(DEMO_HOST, "Shelly.GetStatus")


async def test_the_emulated_fleet_has_the_same_allow_list():
    fleet = EmulatedFleet()
    for method in FORBIDDEN_METHODS:
        with pytest.raises(ForbiddenMethod):
            await fleet.fetch(DEMO_HOST, method)


async def test_emulate_mode_shows_a_simulated_meter_through_the_hub(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    try:
        assert seed_emulate_shelly(hub.config) is True
        assert seed_emulate_shelly(hub.config) is False                    # only once
        assert "power" in hub.config.dashboard("wall").cards
        integ = ShellyIntegration(hub, emulate=True)
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: hub.devices["mic_rack_sim"].status == Status.OK and hub.entities.get("mic_rack_sim.pm1_0_energy") is not None
                        and hub.entities["mic_rack_sim.pm1_0_energy"].value is not None)
            ent = {e["id"]: e for e in hub.snapshot()["entities"]}
            assert ent["mic_rack_sim.pm1_0_power"]["role"] == "equipment" and ent["mic_rack_sim.pm1_0_voltage"]["unit"] == "V"
            assert hub.devices["mic_rack_sim"].name == "Mic rack (sim)"
            assert integ.admin_status()["emulate"] is True
        finally:
            await integ.stop()
    finally:
        hub.recorder.close()


def test_seeding_does_nothing_once_meters_exist_or_the_card_is_in_use(tmp_path):
    cfg = Config(shelly_devices=[ShellyDeviceConfig(id="a", host="192.0.2.1", mac=MAC)])
    assert seed_emulate_shelly(cfg) is False
    cfg2 = Config()
    cfg2.dashboards[0].cards = ["power"]
    assert seed_emulate_shelly(cfg2) is False


# =================================================================================== the API
@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    fetch = Fetch()
    integ = ShellyIntegration(h, fetch=fetch, transport=NullTransport())
    h.add_integration(integ)
    with TestClient(create_app(h)) as c:
        c.hub, c.integ, c.fetch = h, integ, fetch
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


ADD = {"host": "192.0.2.60", "name": "Mic rack", "area": "FOH", "interval_s": 3}


def test_every_shelly_route_needs_admin_and_the_same_origin(client):
    anon = TestClient(client.app)
    calls = [("post", "/api/admin/shelly/test", {"host": HOST}), ("post", "/api/admin/shelly/add", ADD),
             ("patch", "/api/admin/shelly/x", {"name": "y"}), ("delete", "/api/admin/shelly/x", None)]
    for verb, url, body in calls:
        kw = {"json": body} if body is not None else {}
        assert getattr(anon, verb)(url, **kw).status_code == 401, url
        assert getattr(client, verb)(url, headers={"Origin": "http://evil.example"}, **kw).status_code == 403, url


def test_test_returns_fixed_text_and_never_the_address_or_meter_text(client):
    ok = client.post("/api/admin/shelly/test", json={"host": "192.0.2.60"}).json()
    assert ok["ok"] is True and ok["model"] == "MiniPMG3" and ok["components"] == ["Power meter"] and len(ok["readings"]) == 7
    r = client.post("/api/admin/shelly/test", json={"host": "8.8.8.8"})
    assert r.json() == {"ok": False, "category": "not_local", "message": r.json()["message"]} and "8.8.8.8" not in r.text
    client.fetch.fail = "needs_password"
    assert "needs a password: not supported yet" in client.post("/api/admin/shelly/test", json={"host": "192.0.2.60"}).json()["message"]


@pytest.mark.parametrize("category", ["timeout", "refused", "unreachable", "needs_password", "not_shelly", "redirect", "too_large", "bad_reply"])
def test_each_failure_category_is_fixed_text(client, category):
    client.fetch.fail = category
    r = client.post("/api/admin/shelly/test", json={"host": "192.0.2.60"})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["category"] == category
    assert "192.0.2.60" not in r.text


@pytest.mark.parametrize("bad", [{"host": ""}, {"host": "x" * 65}, {"host": HOST, "extra": 1}, {}, {"host": 5}])
def test_test_body_is_strict(client, bad):
    assert client.post("/api/admin/shelly/test", json=bad).status_code == 422


@pytest.mark.parametrize("bad", [{"name": ""}, {"name": "x" * 61}, {"name": "a\nb"}, {"name": "a‮b"}, {"area": "x" * 41},
                                 {"interval_s": 1.9}, {"interval_s": 5.1}, {"interval_s": "fast"}, {"mac": MAC}, {"password": "pw"}])
def test_add_body_is_strict(client, bad):
    r = client.post("/api/admin/shelly/add", json={**ADD, **bad})
    assert r.status_code == 422
    assert "pw" not in r.text or bad.get("password") is None


def test_add_refuses_public_addresses_with_fixed_words(client):
    for host in ("8.8.8.8", "example.com", "127.0.0.1"):
        r = client.post("/api/admin/shelly/add", json={**ADD, "host": host})
        assert r.status_code == 422 and host not in r.text and "local network" in r.json()["detail"]
    assert client.hub.config.shelly_devices == []


def test_add_patch_delete_flow_and_conflicts(client):
    r = client.post("/api/admin/shelly/add", json=ADD)
    assert r.status_code == 200 and r.json() == {"id": "mic_rack"}
    again = client.post("/api/admin/shelly/add", json={**ADD, "name": "Another"})
    assert again.status_code == 409 and "already been added" in again.json()["detail"] and "Nothing has been changed" in again.json()["detail"]
    state = client.get("/api/admin/state").json()
    meter = state["shelly"]["meters"][0]
    assert (meter["host"], meter["mac"], meter["id"]) == ("192.0.2.60", MAC, "mic_rack")
    assert state["config"]["shelly_devices"][0]["host"] == "192.0.2.60"
    assert client.patch("/api/admin/shelly/mic_rack", json={"name": "Rack 2", "interval_s": 2.5}).status_code == 200
    assert client.hub.devices["mic_rack"].name == "Rack 2" and client.hub.config.shelly_devices[0].poll_interval_s == 2.5
    client.fetch.info = {**client.fetch.info, "mac": "025E000000AA"}
    other = client.patch("/api/admin/shelly/mic_rack", json={"host": "192.0.2.61"})
    assert other.status_code == 422 and "different meter" in other.json()["detail"] and client.hub.config.shelly_devices[0].host == "192.0.2.60"
    for bad in ({"interval_s": 9}, {"name": ""}, {"host": "8.8.8.8"}, {"unknown": 1}):
        assert client.patch("/api/admin/shelly/mic_rack", json=bad).status_code in (422,)
    assert client.patch("/api/admin/shelly/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/admin/shelly/mic_rack").status_code == 200
    assert client.delete("/api/admin/shelly/mic_rack").status_code == 404
    assert "mic_rack" not in client.hub.devices and client.hub.config.shelly_devices == []


def test_the_generic_device_routes_leave_meters_alone(client):
    client.post("/api/admin/shelly/add", json=ADD)
    assert client.patch("/api/admin/devices/mic_rack", json={"name": "x"}).status_code == 409
    assert client.delete("/api/admin/devices/mic_rack").status_code == 409
    assert "mic_rack" in client.hub.devices


def test_meter_readings_cannot_be_calibrated_or_given_another_role(client):
    client.post("/api/admin/shelly/add", json=ADD)
    client.integ.status(MAC, fx("pmmini-gen3-getstatus.json"), 1000.0)
    r = client.put("/api/admin/entities/mic_rack.pm1_0_power", json={"offset": 5, "include_in_average": True, "role": "environment"})
    assert r.status_code == 409 and "Equipment" in r.json()["detail"]
    assert client.hub.entities["mic_rack.pm1_0_power"].value == 0.0


def test_nothing_public_names_an_address_a_mac_or_a_firmware(client):
    client.post("/api/admin/shelly/add", json=ADD)
    client.integ.identified(MAC, parse_device_info(fx("pmmini-gen3-deviceinfo.json")))
    client.integ.status(MAC, fx("pmmini-gen3-getstatus.json"), 1000.0)
    rows = client.get("/api/info").json()["dashboards"]
    rows[0]["cards"] = [*rows[0]["cards"], "power"]
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    anon = TestClient(client.app)
    with anon.websocket_connect("/ws?dashboard=foh") as ws:
        first = json.dumps(ws.receive_json())
    texts = [anon.get(u).text for u in ("/api/snapshot", "/api/info", "/api/dashboard/foh", "/api/history?entities=mic_rack.pm1_0_power")] + [first]
    for text in texts:
        for secret in ("192.0.2.60", MAC, MAC.upper(), "2.1.0-beta2", "S3PM-001"):
            assert secret not in text, secret
    assert "mic_rack.pm1_0_power" in texts[0]                        # the readings themselves are public
    assert "pm1_0_power" in first


def test_nothing_in_the_logs_names_the_address_or_mac(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/api/admin/shelly/add", json=ADD)
    client.integ.lost(MAC, "timeout", 1)
    client.fetch.fail = "unreachable"
    client.post("/api/admin/shelly/test", json={"host": "192.0.2.60"})
    assert "192.0.2.60" not in caplog.text and MAC not in caplog.text.lower()


# ====================================================================== the card and the page
def test_the_power_card_is_known_never_default_and_can_be_half_width():
    assert "power" in cards.KNOWN_CARDS and "power" in cards.HALF_CAPABLE
    assert "power" not in {c for v in cards.LAYOUT_DEFAULTS.values() for c in v}
    assert cards.strict_cards_error(["env_tiles", "power"]) is None


def test_dashboard_with_the_power_card_round_trips(client):
    rows = client.get("/api/info").json()["dashboards"]
    rows[0]["cards"] = [*rows[0]["cards"], "power"]
    rows[0]["card_sizes"] = {"power": "half"}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    assert client.get("/api/dashboard/foh").json()["cards"][-1] == "power"
    assert client.get("/api/dashboard/foh").json()["card_sizes"] == {"power": "half"}


def test_page_has_the_card_and_loads_its_script_before_the_dashboard():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    order = [html.index(f"/static/{n}.js") for n in ("common", "power", "dashboard")]
    assert order == sorted(order)
    assert html.count('data-card="power"') == 1 and 'id="power-card"' in html


def test_power_script_is_safe_and_old_browser_friendly():
    js = re.sub(r"//.*", "", (STATIC / "power.js").read_text(encoding="utf-8"))
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "requestAnimationFrame", "fetch(", "XMLHttpRequest", "toLocale", "Intl.", "setInterval"):
        assert bad not in js
    assert "http://" not in js and "https://" not in js
    for name, pattern in FORBIDDEN.items():
        assert not re.search(pattern, js), name


def test_admin_has_the_foldable_card_and_the_plain_words():
    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert 'foldCard(shellyCard(), "shelly", false)' in js
    assert "/api/admin/shelly/test" in js and "/api/admin/shelly/add" in js
    assert "never switches a relay" in js and "password is not supported yet" in js and "Warnings at the breaker rating are planned" in js
    assert "category !== \"meter\"" in js


def test_card_makes_no_claim_it_cannot_keep():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert "do not replace the electrician" in html and "never switches anything" in html


# ============================================================================ security review fixes
def _meter(i, **kw):
    return {"id": f"m{i}", "host": f"192.0.2.{i}", "mac": f"025e0000{i:04x}", **kw}


def test_salvage_with_more_than_sixteen_valid_meters_still_loads_with_sixteen(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"site": {"name": "Kept"}, "shelly_devices": [_meter(i) for i in range(1, 18)],
                                    "alarms": {"hide_acked_min": "junk"}}), encoding="utf-8")
    cfg = ConfigStore(path).load()
    assert len(cfg.shelly_devices) == 16 and cfg.site.name == "Kept"


def test_a_plain_load_of_seventeen_meters_keeps_sixteen():
    assert len(Config.model_validate({"shelly_devices": [_meter(i) for i in range(1, 18)]}).shelly_devices) == 16


def test_duplicate_ids_and_macs_keep_only_the_first():
    cfg = Config.model_validate({"shelly_devices": [
        _meter(1), _meter(2, id="m1"), _meter(3, mac=_meter(1)["mac"]), _meter(4)]})
    assert [d.id for d in cfg.shelly_devices] == ["m1", "m4"]


def test_hostile_reply_cannot_register_thousands_of_readings():
    status = {f"{c}:{i}": {"apower": 1.0, "voltage": 1.0} for c in ("pm1", "em1", "em", "emdata", "switch") for i in range(0, 300)}
    assert all(i <= 7 for _, i in rd.components(status))
    assert len(rd.readings_from_status(status)) <= rd.MAX_READINGS == 64


async def test_meter_temperature_is_never_sent_as_a_node_reading_over_osc(hub):
    from stagewatch.integrations.osc_out import OscOutIntegration
    integ, _ = await make(hub)
    hot = fx("docs-switch-getstatus.json")
    hot["switch:0"]["temperature"]["tC"] = 70.0
    feed(integ, hot, __import__("time").time())
    hub.config.osc_out.per_node = True
    msgs = dict(OscOutIntegration(hub).build_messages())
    import math
    assert all(math.isnan(v) for v in msgs["/stagewatch/node/rack/env"])


def test_test_and_add_are_limited_to_ten_a_minute(client):
    codes = [client.post("/api/admin/shelly/test", json={"host": "192.0.2.60"}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]
    r = client.post("/api/admin/shelly/add", json=ADD)
    assert r.status_code == 429 and "192.0.2" not in r.text and client.hub.config.shelly_devices == []


def test_diagnostics_hide_meter_addresses_but_not_esphome_hosts():
    from stagewatch import diagnostics
    raw = {"shelly_devices": [{"id": "a", "host": "192.0.2.60", "mac": MAC, "name": "Rack"}],
           "esphome_devices": [{"id": "e", "host": "192.0.2.9"}]}
    out = diagnostics.redact_config(raw, diagnostics.known_secrets(raw))
    assert out["shelly_devices"][0]["host"] == out["shelly_devices"][0]["mac"] == diagnostics.REDACTED
    assert out["shelly_devices"][0]["name"] == "Rack" and out["esphome_devices"][0]["host"] == "192.0.2.9"

# ===================================================================== this show day's figures
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from stagewatch.integrations.shelly import dayfigures as df  # noqa: E402


def london(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ZoneInfo("Europe/London")).timestamp()


T0 = london(2026, 10, 14, 20, 0)      # 20:00 British Summer Time, show day 2026-10-14 (rollover 06:00)


def pm(power, volts, amps, wh):
    st = fx("docs-pm1-getstatus.json")
    st["pm1:0"].update(apower=power, voltage=volts, current=amps)
    st["pm1:0"]["aenergy"]["total"] = wh
    return st


def test_highest_lowest_and_energy_worked_out_by_hand():
    """Power 100, 250, 180 -> highest 250 W at +5 s. Voltage 230, 236.5, 228 -> low 228 (at +10), high
    236.5 (at +5). Counter 1000, 1010.5, 1030 -> 30 Wh. Counter then goes back to 5 and on to 12:
    30 kept, plus 12 - 5 = 7, so 37 Wh, and the moment it went back is noted."""
    p, v, e = df.DayFigures("power", False, "d"), df.DayFigures("voltage", False, "d"), df.DayFigures("energy", True, "d")
    for i, (pw_, vv, wh) in enumerate([(100, 230, 1000), (250, 236.5, 1010.5), (180, 228, 1030)]):
        p.feed(pw_, T0 + 5 * i); v.feed(vv, T0 + 5 * i); e.feed(wh, T0 + 5 * i)
    assert (p.to_dict()["max"], p.to_dict()["max_at"]) == (250, T0 + 5)
    assert v.to_dict() == {"day": "d", "max": 236.5, "max_at": T0 + 5, "min": 228, "min_at": T0 + 10}
    assert e.to_dict() == {"day": "d", "wh": 30.0, "since": T0}
    e.feed(5, T0 + 15); e.feed(12, T0 + 20)
    assert e.to_dict()["wh"] == 37.0 and e.to_dict()["went_back_at"] == T0 + 15


def test_a_silence_is_a_gap_and_nothing_is_filled_in():
    p = df.DayFigures("power", False, "d")
    p.feed(100, T0); p.feed(110, T0 + 20); p.feed(90, T0 + 120)
    assert p.to_dict()["gaps"] == [[T0 + 20, T0 + 120]]          # 20 s is a normal pause, 100 s is a gap
    assert p.to_dict()["max"] == 110                             # nothing was invented for the gap


async def test_entities_carry_the_day_figures_and_nothing_before_a_reading(hub):
    hub.config.site.timezone = "Europe/London"
    integ, _ = await make(hub)
    assert "day" not in {e["id"]: e for e in hub.snapshot()["entities"]}.get("rack.pm1_0_power", {})
    feed(integ, pm(100.0, 230.0, 0.5, 1000.0), T0)
    feed(integ, pm(250.0, 236.5, 1.1, 1010.5), T0 + 5)
    feed(integ, pm(None, None, None, None), T0 + 10)             # a meter that reads nothing: no change, no zero
    snap = {e["id"]: e for e in hub.snapshot()["entities"]}
    assert snap["rack.pm1_0_power"]["day"] == {"day": "2026-10-14", "max": 250.0, "max_at": T0 + 5}
    assert snap["rack.pm1_0_current"]["day"]["max"] == 1.1
    assert snap["rack.pm1_0_voltage"]["day"]["min"] == 230.0
    assert snap["rack.pm1_0_energy"]["day"] == {"day": "2026-10-14", "wh": 10.5, "since": T0}


async def test_a_new_show_day_starts_afresh_at_the_rollover(hub):
    hub.config.site.timezone = "Europe/London"
    integ, _ = await make(hub)
    feed(integ, pm(900.0, 230.0, 4.0, 2000.0), london(2026, 10, 15, 5, 59))
    feed(integ, pm(50.0, 231.0, 0.2, 2001.0), london(2026, 10, 15, 6, 1))
    day = hub.entities["rack.pm1_0_power"].day
    assert (day["day"], day["max"]) == ("2026-10-15", 50.0)       # 900 W belonged to yesterday's show day
    assert hub.entities["rack.pm1_0_energy"].day["wh"] == 0.0     # counting from this show day's first reading


async def test_after_a_restart_the_day_is_rebuilt_from_what_was_recorded_with_the_gap_shown(tmp_path):
    h1 = Hub(tmp_path); h1.config.site.timezone = "Europe/London"
    integ, _ = await make(h1)
    feed(integ, pm(300.0, 232.0, 1.3, 5000.0), T0)
    feed(integ, pm(120.0, 233.0, 0.5, 5004.0), T0 + 5)
    h1.recorder.close()
    h2 = Hub(tmp_path); h2.config.site.timezone = "Europe/London"
    try:
        integ2, _ = await make(h2)
        feed(integ2, pm(150.0, 234.0, 0.6, 5100.0), T0 + 600)   # Stagewatch was not reading for ten minutes
        d = h2.entities["rack.pm1_0_power"].day
        assert (d["max"], d["max_at"]) == (300.0, T0)
        assert d["gaps"] == [[T0 + 5, T0 + 600]]
        assert h2.entities["rack.pm1_0_energy"].day["wh"] == 100.0   # the meter's own counter: 5100 - 5000
    finally:
        h2.recorder.close()


def test_the_card_never_back_fills_or_shows_zero():
    src = (PKG / "dayfigures.py").read_text(encoding="utf-8")
    assert "or 0" not in src and "= 0.0 if" not in src


def test_js_day_figure_cases_run_in_node():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "power_day_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


async def test_add_warnings_makes_two_ordinary_thresholds_from_the_rating_and_replaces_only_its_own(hub):
    """Rating 16 A: above 12.8 A (80 %, level 1) and above 16 A (level 2), hysteresis 2 % = 0.32 A, held 5 s."""
    cfg = ShellyDeviceConfig(id="rack", host=HOST, mac=MAC, name="Mic rack", area="FOH", rating_a=16)
    integ, _ = await make(hub, cfg)
    with pytest.raises(ValueError):
        integ.apply_warnings("rack")                              # no reading yet
    feed(integ, pm(100.0, 230.0, 1.8, 1000.0), T0)
    mine = Threshold(id="my_own", entity="rack.pm1_0_power", above=500)
    hub.config.thresholds = [mine]
    assert integ.apply_warnings("rack") == 2
    assert integ.apply_warnings("rack") == 2                      # again: replaced, not doubled
    got = {t.id: t for t in hub.config.thresholds}
    assert set(got) == {"my_own", "shelly_rack_pm1_0_current_80", "shelly_rack_pm1_0_current_100"}
    t80, t100 = got["shelly_rack_pm1_0_current_80"], got["shelly_rack_pm1_0_current_100"]
    assert (t80.above, t80.level, t80.hysteresis, t80.hold_s) == (12.8, 1, 0.32, 5.0)
    assert (t100.above, t100.level, t100.entity) == (16.0, 2, "rack.pm1_0_current")
    await integ.remove("rack")
    assert [t.id for t in hub.config.thresholds] == ["my_own"]    # its warnings go with the meter


async def test_add_warnings_needs_a_rating(hub):
    integ, _ = await make(hub)
    feed(integ, pm(100.0, 230.0, 1.8, 1000.0), T0)
    with pytest.raises(ValueError):
        integ.apply_warnings("rack")
    with pytest.raises(LookupError):
        integ.apply_warnings("nope")


async def test_no_rating_means_no_load_notice_and_no_public_rating(hub):
    integ, _ = await make(hub)
    feed(integ, pm(100.0, 230.0, 50.0, 1000.0), T0)
    assert "rating_a" not in hub.devices["rack"].to_dict()


async def test_uk_voltage_range_is_216_2_to_253_volts_and_other_supplies_are_not_judged(hub):
    """230 V +10 % = 253.0; 230 V -6 % = 216.2. 216.2 and 253.0 are inside; 216.1 and 253.1 outside.
    Once outside, it clears only 0.5 V back inside (216.5 and 252.5 stay on)."""
    integ, _ = await make(hub)
    out = lambda: "power:rack.volts" in hub.alarms.active  # noqa: E731
    for volts, expect in [(230.0, False), (216.2, False), (253.0, False), (216.1, True), (216.5, True), (216.7, False),
                          (253.1, True), (252.6, True), (252.4, False), (110.0, False)]:
        feed(integ, pm(100.0, volts, 1.0, 1000.0), T0)
        assert out() == expect, volts


def pmf(freq):
    st = pm(100.0, 230.0, 1.0, 1000.0)
    st["pm1:0"]["freq"] = freq
    return st


async def test_frequency_warns_when_close_to_and_outside_the_uk_50_hz_limits(hub):
    """Red outside 49.5 to 50.5 Hz (level 2), amber outside 49.8 to 50.2 Hz (level 1), clear again with
    0.02 Hz of margin: 50.00 and 49.85 fine; 49.79 and 50.21 close to a limit; 49.49 and 50.51 outside;
    49.6 back to close; 49.81 still close (needs 49.82); 49.9 fine; 60 Hz is not judged."""
    integ, _ = await make(hub)
    got = lambda: (hub.alarms.active.get("power:rack.freq").level if "power:rack.freq" in hub.alarms.active else None)  # noqa: E731
    for hz, expect in [(50.0, None), (49.85, None), (49.79, 1), (50.21, 1), (49.49, 2), (50.51, 2), (49.6, 1),
                       (49.81, 1), (49.9, None), (60.0, None)]:
        feed(integ, pmf(hz), T0)
        assert got() == expect, hz
    feed(integ, pmf(49.49), T0 + 1)
    assert "49.49 Hz is outside the UK range (49.5 to 50.5 Hz)" in hub.alarms.active["power:rack.freq"].message
    assert hub.alarms.active["power:rack.freq"].silent


async def test_frequency_range_for_the_day_is_kept_with_times(hub):
    integ, _ = await make(hub)
    for i, hz in enumerate([50.02, 49.91, 50.08]):
        feed(integ, pmf(hz), T0 + 5 * i)
    d = hub.entities["rack.pm1_0_freq"].day
    assert (d["min"], d["min_at"], d["max"], d["max_at"]) == (49.91, T0 + 5, 50.08, T0 + 10)


async def test_the_rating_can_be_set_and_cleared_and_is_public_and_saved(hub):
    integ, cfg = await make(hub)
    await integ.update("rack", None, None, None, None, 16.0)
    assert cfg.rating_a == 16.0 and hub.devices["rack"].to_dict()["rating_a"] == 16.0
    assert integ.admin_status()["meters"][0]["rating_a"] == 16.0
    await integ.update("rack", None, None, None, None, 0.0)
    assert cfg.rating_a is None and "rating_a" not in hub.devices["rack"].to_dict()
    with pytest.raises(ValueError):
        ShellyDeviceConfig(id="x", host=HOST, mac=MAC, rating_a=-1)


async def test_a_circuit_can_be_included_in_the_site_total_and_that_is_public_and_saved(hub):
    integ, cfg = await make(hub)
    feed(integ, pm(100.0, 230.0, 1.0, 1000.0), T0)
    assert ShellyChannelConfig(key="pm1_0").in_total is False        # off unless ticked
    await integ.update("rack", None, None, None, None, None, [{"key": "pm1_0", "label": "", "phase": "", "rating_a": 0, "in_total": True}])
    assert cfg.channels[0].in_total is True                           # kept although nothing else was typed
    assert hub.devices["rack"].to_dict()["channels"] == {"pm1_0": {"in_total": True}}
    assert integ.admin_status()["meters"][0]["channels"][0]["in_total"] is True
    await integ.update("rack", None, None, None, None, None, [{"key": "pm1_0", "in_total": False}])
    assert cfg.channels == [] and "channels" not in hub.devices["rack"].to_dict()


async def test_combined_load_bar_is_off_by_default_public_when_rated_and_saved(hub):
    integ, cfg = await make(hub)
    assert cfg.combined_bar is False and "combined_bar" not in hub.devices["rack"].to_dict()
    await integ.update("rack", None, None, None, None, 16.0, None, True)
    assert cfg.combined_bar is True and hub.devices["rack"].to_dict()["combined_bar"] is True
    assert integ.admin_status()["meters"][0]["combined_bar"] is True
    await integ.update("rack", None, None, None, None, 0.0)              # no rating: nothing to measure against
    assert "combined_bar" not in hub.devices["rack"].to_dict() and cfg.combined_bar is True
    await integ.update("rack", None, None, None, None, None, None, False)
    assert cfg.combined_bar is False


def test_emulate_seed_ticks_the_two_gen1_channels():
    cfg = Config()
    assert seed_emulate_shelly(cfg) is True
    em = next(d for d in cfg.shelly_devices if d.id == "em_sim")
    assert [(c.key, c.in_total) for c in em.channels] == [("em0", True), ("em1", True)]


def em_status():
    st = fx("docs-em-getstatus.json")
    return st


async def test_each_circuit_can_have_a_label_a_line_and_its_own_rating_and_they_are_public(hub):
    integ, cfg = await make(hub)
    feed(integ, pm(100.0, 230.0, 1.0, 1000.0), T0)
    await integ.update("rack", None, None, None, None, 16.0, [{"key": "pm1_0", "label": "Amp rack 3", "phase": "L2", "rating_a": 10.0}])
    pub = hub.devices["rack"].to_dict()
    assert pub["channels"] == {"pm1_0": {"label": "Amp rack 3", "phase": "L2", "rating_a": 10.0}} and pub["rating_a"] == 16.0
    assert integ.admin_status()["meters"][0]["channels"] == [
        {"key": "pm1_0", "name": "Circuit", "three_phase": False, "label": "Amp rack 3", "phase": "L2", "rating_a": 10.0, "in_total": False,
         "role": "", "unit_no": None, "upstream": ""}]
    await integ.update("rack", None, None, None, None, None, [{"key": "pm1_0", "label": "", "phase": "", "rating_a": 0}])
    assert cfg.channels == [] and "channels" not in hub.devices["rack"].to_dict()      # nothing typed: nothing kept


async def test_saving_circuits_keeps_their_power_tree_fields_and_the_public_device_hides_them(hub):
    integ, cfg = await make(hub)
    feed(integ, pm(100.0, 230.0, 1.0, 1000.0), T0)
    cfg.channels = [ShellyChannelConfig(key="pm1_0", role="fdu", unit_no=1, upstream="m:rack")]
    await integ.update("rack", None, None, None, None, None, [{"key": "pm1_0", "label": "Amp rack 3"}])
    ch = cfg.channels[0]
    assert (ch.label, ch.role, ch.unit_no, ch.upstream) == ("Amp rack 3", "fdu", 1, "m:rack")
    await integ.update("rack", None, None, None, None, None, [{"key": "pm1_0", "label": ""}])
    assert cfg.channels and cfg.channels[0].role == "fdu"      # a role alone keeps the circuit
    assert "upstream" not in str(hub.devices["rack"].to_dict())
    with pytest.raises(ValueError):
        ShellyChannelConfig(key="pm1_0", phase="L4")
    with pytest.raises(ValueError):
        ShellyChannelConfig(key="Bad Key")


async def test_warnings_use_the_circuits_own_rating_before_the_meters(hub):
    """Meter 16 A, circuit 10 A: 80 % of 10 = 8 A and 100 % = 10 A. Hysteresis 2 % of 10 = 0.2 A."""
    integ, _ = await make(hub)
    feed(integ, pm(100.0, 230.0, 1.0, 1000.0), T0)
    await integ.update("rack", None, None, None, None, 16.0, [{"key": "pm1_0", "label": "Amp rack 3", "phase": "", "rating_a": 10.0}])
    integ.apply_warnings("rack")
    got = {t.id: t for t in hub.config.thresholds}
    assert got["shelly_rack_pm1_0_current_80"].above == 8.0 and got["shelly_rack_pm1_0_current_100"].above == 10.0
    assert got["shelly_rack_pm1_0_current_80"].hysteresis == 0.2
    assert "Mic rack Amp rack 3: load 80% of 10 A" == got["shelly_rack_pm1_0_current_80"].label


async def test_a_three_phase_meter_is_listed_as_one_circuit_for_all_three_lines(hub):
    integ, _ = await make(hub)
    feed(integ, em_status(), T0)
    ch = integ.admin_status()["meters"][0]["channels"]
    assert len(ch) == 1 and ch[0]["three_phase"] is True and ch[0]["key"] == "em0"


# ===================================================================== more meter types (P1: vocabulary, sim)
def test_new_power_kinds_are_not_air_kinds_and_have_units():
    for kind, unit in ((Kind.REACTIVE_POWER, "var"), (Kind.THD, "%")):
        assert kind not in ENV_KINDS and UNITS[kind] == unit


def test_a_meter_peak_register_only_notes_when_it_goes_back_and_never_sums_or_diffs():
    """Meter maximum 10, 12, 9 A: nothing is added up; the drop at the third reading is noted."""
    f = df.DayFigures("meter_max_current", False, "d")
    for i, v in enumerate([10.0, 12.0, 9.0]):
        f.feed(v, T0 + 5 * i)
    assert f.to_dict() == {"day": "d", "went_back_at": T0 + 10}
    g = df.DayFigures("meter_max_current", False, "d")
    g.feed(10.0, T0); g.feed(12.0, T0 + 5)
    assert g.to_dict() == {"day": "d"}


async def test_line_to_line_voltage_and_thd_get_day_figures_but_a_meter_max_does_not(hub):
    hub.config.site.timezone = "Europe/London"
    integ, _ = await make(hub)
    feed(integ, pm(100.0, 230.0, 1.0, 1000.0), T0)
    m = integ._meters[MAC]
    def put(oid, group, field, kind, value, ts):
        r = rd.Reading(oid, "em0", group, field, kind, 1, value)
        eid = f"rack.{oid}"
        if oid not in m.entities:
            integ._register(m, r, eid, 1)
        integ._record(m, r, eid, ts)
    for i, v in enumerate([398.0, 405.0, 392.0]):
        put("em0_l12_voltage_ll", "l12", "voltage_ll", Kind.VOLTAGE, v, T0 + 5 * i)
    for i, v in enumerate([2.5, 3.9, 3.1]):
        put("em0_a_thd_v", "a", "thd_v", Kind.THD, v, T0 + 5 * i)
    put("em0_a_meter_max_current", "a", "meter_max_current", Kind.CURRENT, 12.0, T0)
    d = hub.entities["rack.em0_l12_voltage_ll"].day
    assert (d["min"], d["min_at"], d["max"], d["max_at"]) == (392.0, T0 + 10, 405.0, T0 + 5)
    assert hub.entities["rack.em0_a_thd_v"].day["max"] == 3.9 and hub.entities["rack.em0_a_thd_v"].unit == "%"
    assert hub.entities["rack.em0_a_meter_max_current"].day == {"day": "2026-10-14"}
    snap = {e["id"]: e for e in hub.snapshot()["entities"]}
    assert snap["rack.em0_l12_voltage_ll"]["role"] == "equipment"


def test_the_three_phase_sim_has_uneven_lines_and_a_neutral_that_follows():
    """Lines 14, 11, 9 A: neutral = sqrt(196 + 121 + 81 - 154 - 99 - 126) = sqrt(19) = 4.36 A."""
    from stagewatch.integrations.shelly.emulate import neutral_a
    assert round(neutral_a(14.0, 11.0, 9.0), 2) == 4.36
    assert neutral_a(10.0, 10.0, 10.0) == 0.0
    meter = EmulatedMeter(DEMO3_HOST, lambda: 1000.0, outages=False)
    comps = meter.components(1000.0)
    got = rd.readings_from_status(comps)
    ids = {r.object_id: r for r in got}
    assert {"em0_a_current", "em0_b_current", "em0_c_current", "em0_n_current", "em0_total_power", "emdata0_total_energy"} <= set(ids)
    assert all(ids[f"em0_{p}_current"].value > 0 for p in "abc")
    assert ids["em0_n_current"].value > 0


def test_a_fresh_emulate_config_gets_single_phase_three_phase_and_gen1_meters():
    cfg = Config()
    assert seed_emulate_shelly(cfg) is True
    assert [d.id for d in cfg.shelly_devices] == ["mic_rack_sim", "amp_rack_sim", "lighting_sim", "distro_sim", "em_sim"]


# ===================================================================== calculated apparent power
def _sw(**kw):
    return {"switch:0": {"apower": kw.pop("apower", 230.0), **kw}}


def test_switch_apparent_power_is_volts_times_amps_and_labelled_calculated():
    got = by_id(rd.readings_from_status(_sw(voltage=230.0, current=2.0, pf=0.5)))
    assert got["switch_0_apparent"].value == 460.0           # 230 V x 2 A, even with a power factor present
    assert got["switch_0_apparent"].calculated == "volts x amps" and got["switch_0_apparent"].kind == Kind.APPARENT_POWER
    assert rd.display_name(got["switch_0_apparent"], 1) == "Apparent power (calculated)"


def test_apparent_power_falls_back_to_watts_over_power_factor_never_near_zero():
    got = by_id(rd.readings_from_status(_sw(apower=-180.0, voltage=None, current=None, pf=0.9)))
    assert got["switch_0_apparent"].value == 200.0 and got["switch_0_apparent"].calculated == "watts / power factor"
    for pf in (0.0, 0.05, None):
        assert by_id(rd.readings_from_status(_sw(voltage=230.0, current=None, pf=pf)))["switch_0_apparent"].value is None
    assert by_id(rd.readings_from_status(_sw(apower=230.0, voltage=None, current=1.0, pf=0.1)))["switch_0_apparent"].value == 2300.0   # 230 W / 0.1: PF at the limit still works


def test_apparent_power_is_none_when_nothing_to_work_it_from():
    assert by_id(rd.readings_from_status(_sw(voltage=None, current=None)))["switch_0_apparent"].value is None


def test_gen1_em_apparent_power_is_watts_over_power_factor_and_invalid_channel_gives_none():
    st = {"gen1em:0": {"is_valid": True, "power": 450.0, "pf": 0.9, "voltage": 230.0}, "gen1em:1": {"is_valid": False, "power": 450.0, "pf": 0.9}}
    got = by_id(rd.readings_from_status(st))
    assert got["gen1em_0_apparent"].value == 500.0 and got["gen1em_0_apparent"].calculated == "watts / power factor"
    assert got["gen1em_1_apparent"].value is None


def test_meters_own_apparent_power_is_kept_and_not_marked_calculated():
    got = by_id(rd.readings_from_status(fx("docs-pm1-getstatus.json")))
    assert got["pm1_0_apparent"].value == 288.0 and got["pm1_0_apparent"].calculated == ""
