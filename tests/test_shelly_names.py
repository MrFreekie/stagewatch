"""Shelly channel names read from the meter, and the device total hidden on multi-circuit meters.

Names come from one read-only GET (Shelly.GetConfig, or /settings for a first-generation meter). The admin's
label always wins; nothing is ever written to a meter. The EM Gen3 fixture has the names read from the
owner's meter (two circuits named, device name left at its default); its status is SYNTHETIC with round
numbers: 3.0 A / 700 W and 1.5 A / 300 W at 230 V, so the device total is 1,000 W (counted once).
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path

import pytest

from stagewatch.core.config import PowerPointConfig, ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.integrations.shelly import ShellyIntegration
from stagewatch.integrations.shelly import transport as tp
from stagewatch.integrations.shelly.http import ALLOWED_METHODS, ForbiddenMethod, ShellyError, rpc_path
from test_shelly import NullTransport

FIX = Path(__file__).resolve().parent / "fixtures" / "shelly"
STATIC = Path(__file__).resolve().parent.parent / "src" / "stagewatch" / "web" / "static"
MAC = "025e00000042"
T0 = 1_790_000_000 - (1_790_000_000 % 86400) + 12 * 3600


def fx(name):
    data = json.loads((FIX / name).read_text(encoding="utf-8"))
    data.pop("_note", None)
    return data


class EmFetch:
    """An EM Gen3 with two named circuits. Records every request; a GetConfig can be made to fail or hang."""

    def __init__(self, config=None, config_fail=None, hang=False):
        self.calls, self.config_fail, self.hang = [], config_fail, hang
        self.config = fx("em-gen3-getconfig.json") if config is None else config

    async def __call__(self, host, method):
        rpc_path(method)
        self.calls.append(method)
        if method == "Shelly.GetDeviceInfo":
            return fx("em-gen3-deviceinfo.json")
        if method == "Shelly.GetStatus":
            return fx("em-gen3-getstatus.json")
        if method == "Shelly.GetConfig":
            if self.hang:
                await asyncio.sleep(60)
            if self.config_fail:
                raise ShellyError(self.config_fail)
            return copy.deepcopy(self.config)
        raise ShellyError("not_shelly")


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    yield h
    h.recorder.close()


async def start(hub, fetch):
    integ = ShellyIntegration(hub, fetch=fetch, transport=NullTransport())
    hub.add_integration(integ)
    await integ.start()
    return integ


# ------------------------------------------------------------------ reading the names
def test_names_of_the_owners_em_gen3():
    n = tp.parse_names(fx("em-gen3-getconfig.json"), False)
    assert n.device == "Shelly EM Gen3"
    assert n.channels == {"em1_0": "CU-10-All-Lights", "em1_1": "CU-00-Spare"}


def test_a_meter_with_no_names_gives_none():
    n = tp.parse_names({"sys": {"device": {"name": None}}, "switch:0": {"id": 0, "name": None}, "pm1:0": {"id": 0}}, False)
    assert n == tp.DeviceNames("", {})


def test_gen1_settings_give_the_device_name_only():
    n = tp.parse_names({"name": "FOH EM", "relays": [{"name": "contactor"}]}, True)
    assert n.device == "FOH EM" and n.channels == {}


def test_names_are_plain_short_text():
    cfg = {"sys": {"device": {"name": "A\x00B\x1b[31m\n" + "x" * 80}}, "em1:0": {"name": 5}, "em1:1": {"name": "  two   words\t"},
           "em:0": {"name": "<b>Three-phase</b>"}, "em1:9": {"name": "out of range"}}
    n = tp.parse_names(cfg, False)
    assert len(n.device) == tp.NAME_LIMIT and all(c.isprintable() for c in n.device)
    assert n.channels == {"em1_1": "two words", "em0": "<b>Three-phase</b>"}     # kept as text: the page uses textContent
    for junk in (None, [], "x", 5):
        assert tp.parse_names(junk, False) == tp.DeviceNames()


async def test_a_name_read_that_fails_or_hangs_gives_no_names_quickly(monkeypatch):
    assert (await tp.read_names(EmFetch(config_fail="timeout"), "192.0.2.1", False)) == tp.DeviceNames()
    monkeypatch.setattr(tp, "NAMES_TIMEOUT_S", 0.05)
    assert (await asyncio.wait_for(tp.read_names(EmFetch(hang=True), "192.0.2.1", False), 2)) == tp.DeviceNames()


# ------------------------------------------------------------------ add, test, suggest
async def test_test_and_add_use_the_names_on_the_meter_as_circuit_labels(hub):
    fetch = EmFetch()
    integ = await start(hub, fetch)
    seen = await integ.test("192.0.2.60")
    assert seen["device_name"] == "Shelly EM Gen3"
    assert [x["name"] for x in seen["circuit_names"]] == ["CU-10-All-Lights", "CU-00-Spare"]
    cfg = await integ.add("192.0.2.60", "Workshop", "Hall", 3.0)
    assert cfg.name == "Workshop"                                  # the typed name is never replaced
    assert {c.key: c.label for c in cfg.channels} == {"em1_0": "CU-10-All-Lights", "em1_1": "CU-00-Spare"}
    assert hub.devices["workshop"].public["channels"]["em1_0"]["label"] == "CU-10-All-Lights"
    assert "Shelly EM Gen3" not in json.dumps(hub.snapshot())      # the device's own name is not on public pages


async def test_add_carries_on_with_default_labels_when_the_names_cannot_be_read(hub):
    integ = await start(hub, EmFetch(config_fail="timeout"))
    cfg = await integ.add("192.0.2.60", "Workshop", "", 3.0)
    assert cfg.channels == [] and cfg.name == "Workshop"


async def test_typed_label_wins_and_the_device_name_is_only_a_hint_until_asked_for(hub):
    integ = await start(hub, EmFetch())
    cfg = await integ.add("192.0.2.60", "Workshop", "", 3.0)
    cfg.channels[0].label = "Dimmers"                               # typed by the admin afterwards
    integ.names(MAC, tp.DeviceNames("Shelly EM Gen3", {"em1_0": "CU-10-All-Lights", "em1_1": "CU-00-Spare"}))
    integ.status(MAC, fx("em-gen3-getstatus.json"), T0)
    chans = {c["key"]: c for c in integ.admin_status()["meters"][0]["channels"]}
    assert chans["em1_0"]["label"] == "Dimmers" and chans["em1_0"]["device_name"] == "CU-10-All-Lights"   # differs: hint
    assert chans["em1_1"]["label"] == "CU-00-Spare" and chans["em1_1"]["device_name"] == ""               # same: no hint
    assert integ.admin_status()["meters"][0]["device_name"] == "Shelly EM Gen3"                            # differs from "Workshop"
    out = await integ.read_device_names("workshop")                 # "Use the device's name": suggests, saves nothing
    assert out["channels"]["em1_0"] == "CU-10-All-Lights" and cfg.channels[0].label == "Dimmers"
    with pytest.raises(LookupError):
        await integ.read_device_names("nope")


async def test_only_read_requests_are_ever_sent(hub):
    """Add, Test, the name read and the polling loop together: every request is on the allow-list, and the
    allow-list holds only reads. A Set or Update method is refused before anything is sent."""
    fetch = EmFetch()
    integ = await start(hub, fetch)
    await integ.test("192.0.2.60")
    await integ.add("192.0.2.60", "Workshop", "", 3.0)
    await integ.read_device_names("workshop")
    t = tp.HttpPollTransport(fetch, sleep=lambda d: asyncio.sleep(0.01))
    seen = []

    class Sink:
        def identified(self, mac, info): pass
        def status(self, mac, status, ts): seen.append("status")
        def lost(self, mac, category, failures): pass
        def names(self, mac, names): seen.append(names)

    await t.start(Sink())
    t.add(tp.Target(MAC, "192.0.2.60", 2.0))
    for _ in range(100):
        if any(isinstance(x, tp.DeviceNames) for x in seen):
            break
        await asyncio.sleep(0.02)
    await t.stop()
    assert any(isinstance(x, tp.DeviceNames) and x.device == "Shelly EM Gen3" for x in seen)
    assert set(fetch.calls) <= ALLOWED_METHODS
    assert all(re.match(r"^(Shelly\.Get[A-Za-z]+|gen1\.(shelly|status|settings))$", m) for m in ALLOWED_METHODS)
    assert fetch.calls.count("Shelly.GetConfig") == 2 + 1 + 1       # test, add, "use the name", one per poll connection
    for bad in ("Shelly.SetConfig", "Switch.Set", "Shelly.Reboot", "Shelly.Update", "Sys.SetConfig", "Shelly.FactoryReset"):
        with pytest.raises(ForbiddenMethod):
            rpc_path(bad)


def test_one_name_read_per_connection_in_the_poll_loop():
    src = (Path(__file__).resolve().parent.parent / "src/stagewatch/integrations/shelly/transport.py").read_text(encoding="utf-8")
    assert src.count("read_names(self._request") == 1


# ------------------------------------------------------------------ no double counting
async def _two_circuits(hub, device_total):
    hub.config.power_points = [PowerPointConfig(id="gen", name="Generator", role="source", unit_no=1)]
    chans = [ShellyChannelConfig(key=f"em1_{i}", in_total=True, role="equipment", unit_no=i + 1, upstream="m:workshop") for i in (0, 1)]
    cfg = ShellyDeviceConfig(id="workshop", host="192.0.2.60", mac=MAC, name="Workshop", channels=chans, role="isu", unit_no=1,
                             upstream="p:gen", combined_bar=device_total)
    hub.config.shelly_devices.append(cfg)
    integ = await start(hub, EmFetch())
    integ.status(MAC, fx("em-gen3-getstatus.json"), T0)
    return integ


@pytest.mark.parametrize("device_total", [False, True])
async def test_a_meter_and_its_circuits_are_counted_once(hub, device_total):
    """700 W + 300 W. Site total 1,000 W (not 2,000 W); the device's own sum is 1,000 W; the tree counts the
    meter node and the circuits below it once: 1,000 W. The option only changes what the card shows."""
    await _two_circuits(hub, device_total)
    e = hub.entities
    assert e["workshop.meter_power_load"].value == pytest.approx(1000.0)
    assert e["power_site.site_power_load"].value == pytest.approx(1000.0)
    assert e["power_site.tree_site"].value == pytest.approx(1000.0)
    assert hub.devices["workshop"].public.get("combined_bar", False) is device_total


# ------------------------------------------------------------------ the card
def test_card_shows_the_device_total_only_when_asked_for():
    js = (STATIC / "power.js").read_text(encoding="utf-8")
    assert "const showTotal = multi && !!device.combined_bar;" in js
    assert 'if (multi && !showTotal) return "";' in js            # the meter's demand rows go with it
    assert "All circuits, total (calculated)" in js
    adm = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert "All circuits, total (summary line" in adm and "Use the device's name" in adm
