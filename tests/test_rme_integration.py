"""The RME integration through the hub: simulated units (emulate) with injected time, a real client against
a fake unit, entities and roles, alarms, markers (live only), the public message and the Test button.

Time is injected (a Ticker moves the clock and wakes the simulated units), so the six-minute script runs in
moments. Frames are SYNTHETIC. Waits are `until(...)` on the end state.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial

import pytest

from stagewatch.core.config import RmeDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Kind, Status
from stagewatch.integrations.rme import MANIFEST, RmeIntegration, emulated_probe, probe_message, seed_emulate_rme
from stagewatch.integrations.rme.client import TEXT, RmeClient, RmeError
from stagewatch.integrations.rme.emulate import DEMO_UNITS, EmulatedRmeSource, scene
from test_ontime import until
from test_rme_client import FakeRme, SERIAL, STATUS, DELTA_PSU, DELTA_TEMP, hold

U1, U2 = "stage_l_pre_simulated", "monitor_world_simulated"
D1, D2 = f"rme_{U1}", f"rme_{U2}"


class Ticker:
    """A clock the test moves, and a sleep that waits for the next move."""

    def __init__(self):
        self.t = 0.0
        self._waiters: list[asyncio.Future] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, _delay):
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        await fut

    async def step(self, dt: float = 1.0, n: int = 1):
        for _ in range(n):
            self.t += dt
            waiters, self._waiters = self._waiters, []
            for w in waiters:
                if not w.done():
                    w.set_result(None)
            for _ in range(8):
                await asyncio.sleep(0)


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path, emulate=True)
    yield h
    h.recorder.close()


def labels(hub) -> list[str]:
    return [m.label for m in hub.recorder.markers()]


def marker(hub, text):
    found = [m for m in hub.recorder.markers() if text in m.label]
    assert found, f"no marker with {text!r} in {labels(hub)}"
    return found[0]


def unit_msg(hub, uid):
    return next(u for u in hub.snapshot()["rme_units"]["units"] if u["id"] == uid)


async def started(hub, tk):
    assert seed_emulate_rme(hub.config) is True
    integ = RmeIntegration(hub, emulate=True, clock=tk.clock, sleep=tk.sleep)
    hub.add_integration(integ)
    await integ.start()
    await tk.step(0)
    return integ


# ------------------------------------------------------------------ the simulated script
async def test_the_six_minute_script_psu_clock_offline_gap_and_unknown_word(hub):
    tk = Ticker()
    integ = await started(hub, tk)
    try:
        await tk.step(1, 5)                                                        # t = 5 s
        assert hub.devices[D1].status == Status.OK and hub.devices[D2].status == Status.OK
        m1 = unit_msg(hub, U1)
        assert (m1["status"], m1["link_up"], m1["name"]) == ("ok", True, "Stage L pre (simulated)")
        assert abs(m1["temp_c"] - 57.8) < 0.11                                     # 57.5 + 5.4 sin(2 pi 3 / 360) = 57.78
        assert m1["psu"] == [{"n": 1, "on": True}, {"n": 2, "on": True}] and m1["redundancy"] is True
        assert (m1["clock"]["source_label"], m1["clock"]["locked"], m1["clock"]["rate_hz"]) == ("Word clock", True, 48000)
        assert (m1["preset"]["active_name"], m1["preset"]["changed"]) == ("Live 1", False)
        assert (m1["avb"]["active"], m1["avb"]["total"]) == (4, 8)
        assert labels(hub) == [] or all("RME" not in x for x in labels(hub))        # a baseline makes no marker

        # equipment readings: temperature and the two supplies, never in a site average
        for suffix, kind in (("core_temp", Kind.TEMPERATURE), ("psu_1", Kind.CONTACT), ("psu_2", Kind.CONTACT)):
            e = hub.entities[f"{D1}.{suffix}"]
            assert e.kind == kind and e.locked_equipment is True and hub.role_of(e) == "equipment"
        assert hub.entities[f"{D1}.core_temp"].unit == "°C" and hub.entities[f"{D1}.psu_1"].value == 1.0
        assert hub.devices[D1].role == "equipment" and hub.devices[D1].category == "service"

        await tk.step(1, 95)                                                       # t = 100: supply 2 is off
        assert hub.entities[f"{D1}.psu_2"].value == 0.0
        m1 = unit_msg(hub, U1)
        assert (m1["status"], m1["level"]) == ("warning", 2)                       # redundancy alert on: an Alert
        assert hub.devices[D1].status == Status.COMPROMISED
        alarm = hub.alarms.active[f"rme:{U1}:psu"]
        assert alarm.level == 2 and alarm.silent is True and "power supply 2 off" in alarm.message
        assert hub.alarms.sounding is False and hub.alarms.max_level == 0           # silent: nothing sounds
        lost = marker(hub, "PSU 2 lost")
        assert lost.label == "RME Stage L pre (simulated): PSU 2 lost" and lost.source == "rme" and lost.hidden is False

        await tk.step(1, 15)                                                       # t = 115: restored
        assert hub.entities[f"{D1}.psu_2"].value == 1.0 and f"rme:{U1}:psu" not in hub.alarms.active
        marker(hub, "PSU 2 restored")
        assert hub.devices[D1].status == Status.OK

        await tk.step(1, 45)                                                       # t = 160: clock source changed, Notice
        marker(hub, "clock source Word clock to Internal")
        notice = marker(hub, "Clock: CHECK (Input not in sync)")
        assert notice.hidden is True                                               # a Notice is a hidden marker
        m1 = unit_msg(hub, U1)
        assert m1["status"] == "check" and m1["clock"]["source_label"] == "Internal"
        assert hub.alarms.active[f"rme:{U1}:sound:Clock"].level == 1 and hub.alarms.active[f"rme:{U1}:sound:Clock"].silent is True

        await tk.step(1, 40)                                                       # t = 200: unit 2 closes its socket
        await tk.step(1, 10)                                                       # t = 210
        m2 = unit_msg(hub, U2)
        assert (m2["status"], m2["link_up"]) == ("offline", False)
        assert hub.devices[D2].status == Status.MISSING and hub.alarms.active[f"device:{D2}"].silent is True
        frozen = hub.entities[f"{D2}.core_temp"].updated
        t2 = m2["temp_c"]
        await tk.step(1, 10)
        assert hub.entities[f"{D2}.core_temp"].updated == frozen and unit_msg(hub, U2)["temp_c"] == t2   # a gap: frozen, nothing invented
        assert f"rme:{U2}:psu" not in hub.alarms.active
        marker(hub, "Monitor world (simulated): went offline")
        assert not any("back after" in x for x in labels(hub))

        await tk.step(1, 20)                                                       # t = 240: back
        assert unit_msg(hub, U2)["status"] == "ok" and hub.devices[D2].status == Status.OK
        assert marker(hub, "back after").label.endswith("(readings missing meanwhile)")
        assert "back after 30 s" in marker(hub, "back after").label or "back after 31 s" in marker(hub, "back after").label

        await tk.step(1, 25)                                                       # t = 265: a word nobody has seen
        m1 = unit_msg(hub, U1)
        assert m1["status"] == "check"                                             # never green
        assert any(s["word"] == "Unrecognised: Surprise" and s["symbol"] == "question" and s["level"] == 1 for s in m1["soundness"])
        assert hub.alarms.active[f"rme:{U1}:sound:Preset"].silent is True
        assert marker(hub, "Preset: Unrecognised: Surprise").hidden is True

        await tk.step(1, 30)                                                       # t = 295: it cleared
        assert unit_msg(hub, U1)["status"] == "ok" and not [a for a in hub.alarms.active if a.startswith("rme:")]
        assert hub.alarms.sounding is False

        await tk.step(1, 100)                                                      # past the end of the cycle: runs again
        assert unit_msg(hub, U1)["link_up"] is True and unit_msg(hub, U2)["link_up"] is True
        text = json.dumps(hub.snapshot()["rme_units"]) + " ".join(labels(hub))
        for private in ("SIM-0001", "SIM-0002", "192.0.2", "Group A", "gptp", "serial"):
            assert private not in text
    finally:
        await integ.stop()


async def test_a_hot_rack_never_moves_the_site_average_speed_of_sound_or_calibration(hub):
    """The air sensor reads 20 C; the RME unit reports 60 C. Site values stay with the air sensor."""
    import time
    from stagewatch.core.model import Device, Entity
    hub.config.site.smoothing_tau_s = 0
    hub.register_device(Device("air", "Air", "test", status=Status.OK))
    for kind, v in ((Kind.TEMPERATURE, 20.0), (Kind.HUMIDITY, 50.0), (Kind.PRESSURE, 101325.0)):
        hub.register_entity(Entity(f"air.{kind.value}", "air", kind.value, kind))
        hub.update_state(f"air.{kind.value}", v)
    hub.compute_site(time.time())
    before = (hub.entities["site.temperature"].value, hub.entities["site.speed_of_sound"].value)
    assert before[0] == pytest.approx(20.0)
    tk = Ticker()
    integ = await started(hub, tk)
    try:
        await tk.step(1, 5)
        hub.update_state(f"{D1}.core_temp", 60.0)
        assert hub.entities[f"{D1}.core_temp"].value == 60.0
        hub.compute_site(time.time())
        assert (hub.entities["site.temperature"].value, hub.entities["site.speed_of_sound"].value) == before
        hub.config.entities[f"{D1}.core_temp"] = type(hub.config.entity_settings("x"))(offset=5.0)       # even a stray offset is not applied
        hub.update_state(f"{D1}.core_temp", 60.0)
        assert hub.entities[f"{D1}.core_temp"].value == 60.0
    finally:
        await integ.stop()


def test_an_unknown_soundness_word_is_logged_once_as_the_word_only(hub, caplog):
    import logging
    from stagewatch.integrations.rme import parse
    caplog.set_level(logging.INFO)
    integ = RmeIntegration(hub, emulate=True)
    st = parse.RmeState()
    st.ingest(json.dumps({"msg": "status", "data": {"device": {"entity_name": "Secret Name", "serial_number": "SN-LOG-1",
                                                               "soundness": {"state": "Surprise!", "cause": "Whatever"}}}}))
    view = parse.make_view(st.data)
    for _ in range(3):
        integ._note_unknown(view)
    lines = [r.getMessage() for r in caplog.records if "unrecognised" in r.getMessage()]
    assert len(lines) == 1 and "Surprise" in lines[0]
    assert "Secret Name" not in caplog.text and "SN-LOG-1" not in caplog.text and "Whatever" not in caplog.text


def test_the_script_is_deterministic_and_the_temperature_stays_between_52_and_63():
    temps = [scene(i, t).temp_c for i in (0, 1) for t in range(0, 360)]
    assert 52 <= min(temps) and max(temps) <= 63
    assert scene(0, 10).temp_c == scene(0, 370).temp_c
    assert [scene(0, t).psu2 for t in (89, 90, 109, 110)] == [True, False, False, True]
    assert [scene(1, t).online for t in (199, 200, 229, 230)] == [True, False, False, True]
    assert [scene(0, t).source for t in (149, 150, 189, 190)] == ["wordclock", "internal", "internal", "wordclock"]
    assert [scene(0, t).preset_state for t in (259, 260, 279, 280)] == ["Good", "Surprise", "Surprise", "Good"]


async def test_restarting_mid_script_makes_no_back_filled_marker(hub):
    """At t = 100 supply 2 is off. A fresh start sees that as its baseline: no 'lost' marker, no 'back after'.
    The tile still shows the Alert, because the unit says so now."""
    tk = Ticker()
    cfg = RmeDeviceConfig(id="u", host="192.0.2.21", name="Unit U", serial="SIM-0001")
    hub.config.rme_devices.append(cfg)
    integ = RmeIntegration(hub, emulate=True, clock=tk.clock, sleep=tk.sleep,
                           source_factory=lambda o, u: EmulatedRmeSource(
                               partial(o._on_state, u), partial(o._on_link, u), index=0, name="Unit U",
                               serial="SIM-0001", clock=tk.clock, sleep=tk.sleep, start_at_s=100))
    hub.add_integration(integ)
    await integ.start()
    try:
        await tk.step(1, 2)
        m = unit_msg(hub, "u")
        assert m["psu"][1] == {"n": 2, "on": False} and m["status"] == "warning"
        assert not any("RME" in x for x in labels(hub))
        await tk.step(1, 10)                                                       # t = 112: restored while watching
        assert any("PSU 2 restored" in x for x in labels(hub)) and not any("lost" in x for x in labels(hub))
    finally:
        await integ.stop()


async def test_removing_a_unit_removes_its_device_alarms_and_message(hub):
    tk = Ticker()
    integ = await started(hub, tk)
    try:
        await tk.step(1, 100)
        assert f"rme:{U1}:psu" in hub.alarms.active
        await integ.remove(U1)
        assert D1 not in hub.devices and not any(e.device_id == D1 for e in hub.entities.values())
        assert not [a for a in hub.alarms.active if a.startswith(f"rme:{U1}")]
        assert [u["id"] for u in hub.snapshot()["rme_units"]["units"]] == [U2]
        await integ.remove(U2)
        assert hub.snapshot()["rme_units"] is None and hub.config.rme_devices == []
        with pytest.raises(LookupError):
            await integ.remove(U2)
    finally:
        await integ.stop()


async def test_no_unit_means_nothing_runs_and_no_message(hub):
    integ = RmeIntegration(hub, emulate=True)
    hub.add_integration(integ)
    await integ.start()
    try:
        assert integ.snapshot() is None and hub.snapshot()["rme_units"] is None
        assert not [d for d in hub.devices if d.startswith("rme_")]
        assert integ.admin_status()["units"] == []
    finally:
        await integ.stop()


def test_seed_is_for_a_fresh_emulate_config_only(hub):
    assert seed_emulate_rme(hub.config) is True
    assert [u.name for u in hub.config.rme_devices] == [u["name"] for u in DEMO_UNITS]
    from stagewatch.core.cards import KNOWN_CARDS
    wall = next(d for d in hub.config.dashboards if d.slug == "wall")
    assert ("rme_units" in wall.cards) == ("rme_units" in KNOWN_CARDS)             # the card id is added once the dashboard knows it
    assert seed_emulate_rme(hub.config) is False                                  # already there: not again


def test_manifest_is_honest():
    m = MANIFEST
    assert (m.domain, m.tier, m.direction, m.iot_class, m.vendors) == ("rme", "experimental", "in", "local_push", ("RME",))
    assert m.protocols == ("WebSocket (JSON)",) and m.entity_kinds == ("temperature", "contact")
    for phrase in ("sends it no commands or settings", "12Mic", "untested", "no login"):
        assert phrase in m.description


# ------------------------------------------------------------------ a real client against a fake unit
async def test_real_client_through_the_hub_status_gap_and_back(hub):
    async def script(ws, n):
        await ws.send(STATUS)
        if n == 1:
            await ws.send(DELTA_TEMP)
            await ws.send(DELTA_PSU)
        await hold(ws)

    async with FakeRme(script) as srv:
        hub.config.rme_devices.append(RmeDeviceConfig(id="stage_l", host="127.0.0.1", port=srv.port, serial=SERIAL))
        integ = RmeIntegration(hub, emulate=False, address_ok=lambda ip: True,
                               source_factory=lambda o, u: RmeClient(
                                   lambda: (u.cfg.host, u.cfg.port), partial(o._on_state, u), partial(o._on_link, u),
                                   expected_serial_fn=lambda: u.cfg.serial, address_ok=lambda ip: True,
                                   backoff_min_s=0.02, backoff_max_s=0.05, jitter=lambda: 0.0, tick_s=0.02))
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: "rme_stage_l.psu_2" in hub.entities and hub.entities["rme_stage_l.psu_2"].value == 1.0)
            dev = hub.devices["rme_stage_l"]
            assert dev.name == "Stage L" and dev.model == "12Mic" and dev.manufacturer == "RME"       # the unit's own name
            assert hub.entities["rme_stage_l.core_temp"].value == 63.0                                  # 336.15 K
            assert dev.status == Status.COMPROMISED and dev.status_detail == "Check: see the RME card"  # the preset Notice
            assert any("PSU 2 restored" in x for x in labels(hub))                                      # a live delta
            msg = unit_msg(hub, "stage_l")
            assert msg["status"] == "check" and msg["preset"]["changed"] is True and msg["preset"]["loaded_name"] == "Live 2"

            await srv.drop()
            await until(lambda: hub.devices["rme_stage_l"].status in (Status.MISSING, Status.OK, Status.COMPROMISED)
                        and any("went offline" in x for x in labels(hub)))
            await until(lambda: any("back after" in x for x in labels(hub)))
            assert hub.entities["rme_stage_l.psu_2"].value == 0.0              # the fresh status says supply 2 is off; nothing marked as 'lost'
            assert not any("PSU 2 lost" in x for x in labels(hub))
            assert srv.connections >= 2
        finally:
            await integ.stop()


async def test_a_different_unit_at_the_address_leaves_the_device_missing_with_plain_text(hub):
    async with FakeRme() as srv:
        hub.config.rme_devices.append(RmeDeviceConfig(id="u", host="127.0.0.1", port=srv.port, serial="SN-OTHER"))
        integ = RmeIntegration(hub, emulate=False, address_ok=lambda ip: True,
                               source_factory=lambda o, u: RmeClient(
                                   lambda: (u.cfg.host, u.cfg.port), partial(o._on_state, u), partial(o._on_link, u),
                                   expected_serial_fn=lambda: u.cfg.serial, address_ok=lambda ip: True,
                                   backoff_min_s=0.5, backoff_max_s=1.0))
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: hub.devices["rme_u"].status == Status.MISSING)
            assert hub.devices["rme_u"].status_detail == TEXT["other_unit"]
            assert not [e for e in hub.entities.values() if e.device_id == "rme_u"]
            assert unit_msg(hub, "u")["status"] == "offline"
            assert "SN-OTHER" not in json.dumps(hub.snapshot()) and SERIAL not in json.dumps(hub.snapshot())
            assert "Replace unit" in integ.admin_status()["units"][0]["hint"]
        finally:
            await integ.stop()


# ------------------------------------------------------------------ Test, Add, Save, Remove
class Probe:
    def __init__(self, **info):
        self.info = {"model": "12Mic", "firmware": "2.0.1", "name": "Stage L", "serial": SERIAL, **info}
        self.calls: list = []

    async def __call__(self, ip, port, address_ok=None):
        self.calls.append((ip, port))
        return dict(self.info)


async def test_test_button_against_a_fake_unit_returns_no_serial_and_the_unit_gets_nothing(hub):
    async with FakeRme() as srv:
        integ = RmeIntegration(hub, emulate=False, address_ok=lambda ip: True)
        hub.add_integration(integ)
        got = await integ.test("127.0.0.1", srv.port)
        assert got == {"model": "12Mic", "firmware": "2.0.1", "name": "Stage L", "already_added": False}
        assert probe_message(got) == "RME 12Mic, firmware 2.0.1, named 'Stage L'. Reading works."
        with pytest.raises(RmeError) as exc:
            await RmeIntegration(hub, emulate=False).test("127.0.0.1", srv.port)       # the real address check refuses loopback
        assert exc.value.category == "not_local"


async def test_add_reads_the_serial_names_it_after_the_unit_and_refuses_the_same_unit_twice(hub):
    probe = Probe()
    integ = RmeIntegration(hub, emulate=False, probe_fn=probe, address_ok=lambda ip: True,
                           source_factory=lambda o, u: _Idle())
    hub.add_integration(integ)
    await integ.start()
    try:
        cfg = await integ.add("192.168.1.50", 80, "", "Stage left")
        assert (cfg.id, cfg.name, cfg.area, cfg.serial, cfg.host, cfg.port) == ("stage_l", "", "Stage left", SERIAL, "192.168.1.50", 80)
        assert hub.devices["rme_stage_l"].name == "RME unit"        # until the unit's own status gives its name
        from stagewatch.integrations.rme import InUse
        with pytest.raises(InUse):
            await integ.add("192.168.1.50", 80, "x", "")                          # same address
        with pytest.raises(InUse):
            await integ.add("192.168.1.51", 80, "x", "")                          # same serial at another address
        probe.info["serial"] = "SN-2"
        cfg2 = await integ.add("192.168.1.51", 80, "Stage L", "")
        assert cfg2.id == "stage_l_2" and cfg2.name == "Stage L"                  # the id is made unique
        strict = RmeIntegration(hub, emulate=False, probe_fn=probe)        # the real address check
        for bad in ("8.8.8.8", "12mic.local", "127.0.0.1", ""):
            with pytest.raises(RmeError) as exc:
                await strict.add(bad, 80, "x", "")
            assert exc.value.category == "not_local"
        assert len(probe.calls) == 3                                       # a refused address is never contacted
    finally:
        await integ.stop()


async def test_the_room_for_eight_units_is_enforced(hub):
    from stagewatch.integrations.rme import InUse
    probe = Probe()
    integ = RmeIntegration(hub, emulate=False, probe_fn=probe, address_ok=lambda ip: True, source_factory=lambda o, u: _Idle())
    hub.add_integration(integ)
    await integ.start()
    try:
        for i in range(8):
            probe.info["serial"] = f"SN-{i}"
            await integ.add(f"192.168.1.{10 + i}", 80, f"Unit {i}", "")
        probe.info["serial"] = "SN-9"
        with pytest.raises(InUse) as exc:
            await integ.add("192.168.1.99", 80, "Ninth", "")
        assert "8" in str(exc.value) and len(hub.config.rme_devices) == 8
    finally:
        await integ.stop()


async def test_save_edits_name_area_and_a_new_address_must_answer_as_the_same_unit(hub):
    probe = Probe()
    integ = RmeIntegration(hub, emulate=False, probe_fn=probe, address_ok=lambda ip: True, source_factory=lambda o, u: _Idle())
    hub.add_integration(integ)
    await integ.start()
    try:
        await integ.add("192.168.1.50", 80, "", "")
        await integ.update("stage_l", name="FOH pre", area="FOH", record_input_changes=True)
        cfg = hub.config.rme_devices[0]
        assert (cfg.name, cfg.area, cfg.record_input_changes) == ("FOH pre", "FOH", True) and hub.devices["rme_stage_l"].name == "FOH pre"
        probe.info["serial"] = "SN-OTHER"
        with pytest.raises(RmeError) as exc:
            await integ.update("stage_l", host="192.168.1.60")
        assert exc.value.category == "other_unit" and cfg.host == "192.168.1.50"       # nothing changed
        await integ.update("stage_l", host="192.168.1.60", replace_unit=True)           # a swapped unit, on purpose
        assert (cfg.host, cfg.serial) == ("192.168.1.60", "SN-OTHER")
        with pytest.raises(LookupError):
            await integ.update("nope", name="x")
        with pytest.raises(RmeError):
            await integ.update("stage_l", host="12mic.local")
    finally:
        await integ.stop()


def test_emulated_probe_is_offline_and_has_a_placeholder_serial():
    got = emulated_probe("192.168.1.50")
    assert got["serial"].startswith("SIM-") and "simulated" in got["firmware"].lower()


class _Idle:
    """A source that does nothing (for admin tests)."""
    label = "idle"
    last_frame_at = last_text_at = None

    async def start(self):
        pass

    async def stop(self):
        pass

    def hint(self):
        return ""


# ------------------------------------------------------------------ lost updates and the alarm log
async def _driven_unit(hub, tk):
    """A real-looking unit whose frames the test feeds in by hand (no socket)."""
    from stagewatch.integrations.rme.parse import RmeState
    integ = RmeIntegration(hub, emulate=False, probe_fn=Probe(), address_ok=lambda ip: True,
                           source_factory=lambda o, u: _Idle(), clock=tk.clock, sleep=tk.sleep)
    hub.add_integration(integ)
    await integ.start()
    await integ.add("192.168.1.50", 80, "", "")
    unit = integ._units["stage_l"]
    st = RmeState()
    integ._on_link(unit, True)
    integ._on_state(unit, st, st.ingest(STATUS))

    def feed(text):
        integ._on_state(unit, st, st.ingest(text))
    return integ, unit, feed


async def test_a_burst_of_alarm_changes_costs_a_bounded_number_of_log_writes_and_ends_correct(hub):
    tk = Ticker()
    integ, unit, feed = await _driven_unit(hub, tk)
    calls = []
    real = hub._alarm_changed
    hub._alarm_changed = lambda changes: (calls.append(len(changes)), real(changes))[1]
    try:
        off, on = json.dumps({"device": {"psu": {"state": [True, False]}}}), json.dumps({"device": {"psu": {"state": [True, True]}}})
        for i in range(100):
            feed(off if i % 2 == 0 else on)             # 100 toggles at one instant, ending "on"
        assert len(calls) <= 2
        key = f"rme:{unit.cfg.id}:psu"
        await tk.step(3.0)
        integ.tick()
        assert key not in hub.alarms.active             # the final state is what ends up there
        assert len(calls) <= 3
        feed(off)
        await tk.step(3.0)
        integ.tick()
        assert key in hub.alarms.active and len(calls) <= 5
    finally:
        await integ.stop()


async def test_a_later_full_status_that_changes_nothing_is_a_delta_not_a_forced_refresh(hub):
    tk = Ticker()
    integ, unit, feed = await _driven_unit(hub, tk)
    forced = []
    real = integ._refresh
    integ._refresh = lambda u, force=False: (forced.append(force), real(u, force))[1]
    try:
        feed(STATUS)
        assert forced == [False]
    finally:
        await integ.stop()


async def test_after_a_lost_update_the_unit_is_down_and_old_values_are_not_rerecorded(hub):
    """The client drops the link when it drops a text frame (see test_rme_client); here the integration's
    side: the unit shows as away and the tick records nothing for it."""
    tk = Ticker()
    integ, unit, _feed = await _driven_unit(hub, tk)
    try:
        integ._on_link(unit, False, TEXT["lost_update"])
        assert unit.phase == "down" and hub.devices[unit.device_id].status == Status.MISSING
        pushed = dict(unit.pushed)
        await tk.step(60.0)
        integ.tick()
        assert unit.pushed == pushed
    finally:
        await integ.stop()


# ------------------------------------------------------------------ duplicates, area, a source that fails to start
async def test_replace_unit_runs_the_same_duplicate_serial_check_as_add(hub):
    from stagewatch.integrations.rme import InUse
    probe = Probe()
    integ = RmeIntegration(hub, emulate=False, probe_fn=probe, address_ok=lambda ip: True,
                           source_factory=lambda o, u: _Idle())
    hub.add_integration(integ)
    await integ.start()
    try:
        first = await integ.add("192.168.1.50", 80, "One", "")
        probe.info["serial"] = "SN-2"
        second = await integ.add("192.168.1.51", 80, "Two", "")
        probe.info["serial"] = SERIAL                               # the unit at .51 now answers as unit one
        with pytest.raises(InUse):
            await integ.update(second.id, replace_unit=True)
        with pytest.raises(InUse):
            await integ.update(second.id, host="192.168.1.52")
        assert second.serial == "SN-2" and second.host == "192.168.1.51" and first.serial == SERIAL
        probe.info["serial"] = "SN-3"
        await integ.update(second.id, replace_unit=True)             # a serial nobody else has is fine
        assert second.serial == "SN-3"
    finally:
        await integ.stop()


async def test_clearing_the_area_reaches_the_device(hub):
    probe = Probe()
    integ = RmeIntegration(hub, emulate=False, probe_fn=probe, address_ok=lambda ip: True,
                           source_factory=lambda o, u: _Idle())
    hub.add_integration(integ)
    await integ.start()
    try:
        cfg = await integ.add("192.168.1.50", 80, "One", "Stage left")
        assert hub.devices[f"rme_{cfg.id}"].area == "Stage left"
        await integ.update(cfg.id, area="")
        assert hub.devices[f"rme_{cfg.id}"].area == ""
    finally:
        await integ.stop()


async def test_a_source_that_fails_to_start_is_tried_again_with_backoff(hub):
    tk = Ticker()
    attempts = []

    def factory(owner, unit):
        attempts.append(owner._clock())
        if len(attempts) <= 2:
            raise RuntimeError("boom")
        return _Idle()

    integ = RmeIntegration(hub, emulate=False, probe_fn=Probe(), address_ok=lambda ip: True,
                           source_factory=factory, clock=tk.clock, sleep=tk.sleep)
    hub.add_integration(integ)
    await integ.start()
    try:
        await integ.add("192.168.1.50", 80, "One", "")
        unit = integ._units["one"]
        assert unit.source is None and unit.phase == "down" and unit.retry_at == 5.0
        integ.tick()
        await asyncio.sleep(0)
        assert len(attempts) == 1                                    # not yet
        await tk.step(5.0)
        integ.tick()
        await until(lambda: len(attempts) == 2 and not unit.retrying)
        assert unit.source is None and unit.retry_at == 5.0 + 10.0   # the wait doubled
        await tk.step(10.0)
        integ.tick()
        await until(lambda: unit.source is not None)
        assert unit.start_failures == 0 and unit.retry_at is None and len(attempts) == 3
    finally:
        await integ.stop()
