"""Sound level (SPL): the sound-level kind, up to three chosen values (input + metric) on one
timeline, the Sound level card's data, the emulated source, settings and the admin API. The Smaart
client and its message parsing are in test_smaart_client.py.

Numbers are recorded exactly as received: no averaging, smoothing, rounding or calibration, a missing
value is "not available" (never zero), and offline is a gap. The Smaart message shapes were read from
Smaart's own web page script and are NOT tested against a live Smaart, so every Smaart-shaped message
in the tests is SYNTHETIC (made up to match those notes).
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core import cards, spl
from stagewatch.core.config import Config, ConfigStore, EntitySettings, SplConfig, SplSlot, migrate, salvage
from stagewatch.core.hub import Hub
from stagewatch.core.model import ENV_KINDS, UNITS, Device, Entity, Kind, Status
from stagewatch.core.spl import SplReading
from stagewatch.integrations.smaart import MANIFEST, SmaartIntegration, seed_emulate_spl
from stagewatch.integrations.smaart.client import SmaartSource
from stagewatch.integrations.smaart.emulate import INPUT_LABELS, EmulatedSplSource
from stagewatch.integrations.smaart.source import SplSource
from stagewatch.version import CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION
from stagewatch.web.server import create_app
from test_ontime import until

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
PIN = "4711"

# ------------------------------------------------------------------------ core vocabulary
@pytest.mark.parametrize("raw,expected", [
    (94.3, 94.3), (94, 94.0), (0, 0.0), (-50.0, -50.0), (250, 250.0), (130.123456789, 130.123456789),
])
def test_clean_level_passes_numbers_on_unchanged(raw, expected):
    got = spl.clean_level(raw)
    assert got == expected and isinstance(got, float)


@pytest.mark.parametrize("raw", [None, True, False, "94.3", "", [], {}, float("nan"), float("inf"), -float("inf"),
                                 250.01, -50.01, 1e308, 10 ** 400])
def test_clean_level_refuses_anything_that_is_not_a_plain_in_range_number(raw):
    assert spl.clean_level(raw) is None


def test_slots_helpers():
    assert spl.clean_slots(["a_slow", "a_slow", "nope", "c_slow", "laeq_15m", "z_slow"]) == ["a_slow", "c_slow", "laeq_15m"]
    assert spl.clean_slots("a_slow") == [] and spl.clean_slots(None) == []
    assert spl.slots_error(["a_slow", "c_slow", "laeq_15m"]) is None and spl.slots_error([]) is None
    assert spl.slots_error(["a_slow"] * 2) and spl.slots_error(["nope"]) and spl.slots_error(["a_slow", "c_slow", "z_slow", "a_fast"])
    assert spl.slots_error("a_slow") and spl.slots_error([1])
    assert spl.DEFAULT_SLOTS == ("a_slow", "c_slow", "laeq_15m") and spl.MAX_SLOTS == 3


def test_metric_labels_carry_weighting_time_constant_metric_and_period():
    a = spl.METRIC_BY_KEY["a_slow"]
    assert a.labels(1) == {"weighting": "A", "metric": "SPL", "slot": "1", "time_constant": "Slow"}
    leq = spl.METRIC_BY_KEY["laeq_15m"]
    assert leq.labels(3) == {"weighting": "A", "metric": "Leq", "slot": "3", "period": "15 min"}
    assert a.entity_id == "spl.a_slow"
    assert len({m.key for m in spl.METRICS}) == len(spl.METRICS)


def test_the_new_kind_is_in_db_with_its_unit_and_not_an_environment_kind():
    assert Kind.SOUND_LEVEL.value == "sound_level" and UNITS[Kind.SOUND_LEVEL] == "dB"
    assert Kind.SOUND_LEVEL not in ENV_KINDS


def test_version_text_is_short_plain_ascii():
    assert spl.clean_version("9.1.2") == "9.1.2"
    for bad in (None, 9, "", "x" * 25, "9.\n1", "<script>", "cafÃ©"):
        assert spl.clean_version(bad) == ""


# --------------------------------------------------------------------- hub / recorder
@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    yield h
    h.recorder.close()


def add_level(hub, key="a_slow", slot=1):
    m = spl.METRIC_BY_KEY[key]
    if "spl" not in hub.devices:
        hub.register_device(Device("spl", "Sound level", "smaart", category="service"))
    return hub.register_entity(Entity(m.entity_id, "spl", m.name, Kind.SOUND_LEVEL, "dB", 1, labels=m.labels(slot)))


def test_value_is_recorded_exactly_and_a_calibration_offset_never_touches_it(hub):
    e = add_level(hub)
    hub.config.entities[e.id] = EntitySettings(offset=5.0)   # as if an admin had set one
    hub.update_state(e.id, 94.3, 1000.0)
    assert e.value == 94.3 and e.raw_value == 94.3
    hub.recorder.flush()
    rows = hub.recorder._db.execute("SELECT value FROM states WHERE entity_id = ?", (e.id,)).fetchall()
    assert rows == [(94.3,)]


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "94", True])
def test_a_value_that_is_not_a_finite_number_is_not_available(hub, bad):
    e = add_level(hub)
    hub.update_state(e.id, 94.3, 1000.0)
    hub.update_state(e.id, bad, 1001.0)
    assert e.value is None and e.raw_value is None and e.updated == 1001.0


def test_public_entity_shape_has_labels_only_for_sound_levels(hub):
    e = add_level(hub)
    hub.update_state(e.id, 94.3, 1000.0)
    d = hub.entity_dict(e, 1001.0, 60.0)
    assert set(d) == {"id", "device_id", "name", "kind", "unit", "decimals", "derived", "value", "raw_value",
                      "updated", "stale", "labels"}
    assert d["kind"] == "sound_level" and d["unit"] == "dB" and "role" not in d and "offset" not in d
    site = hub.entity_dict(hub.entities["site.temperature"], 1001.0, 60.0)
    assert "labels" not in site


def test_sound_levels_never_enter_a_site_average_or_the_equipment_group(hub):
    hub.register_device(Device("n1", "Node", "esphome"))
    t = hub.register_entity(Entity("n1.t", "n1", "Temp", Kind.TEMPERATURE, "Â°C", 1))
    hub.update_state(t.id, 20.0)
    e = add_level(hub)
    hub.update_state(e.id, 99.0)
    hub.compute_site()
    assert hub.entities["site.temperature"].value == 20.0
    assert hub.role_of(e) == "environment" and not any(
        x.kind == Kind.SOUND_LEVEL for x in hub._env_inputs(Kind.TEMPERATURE, 0.0))


def test_labels_are_kept_in_the_entity_description_for_history(hub):
    e = add_level(hub, "laeq_15m", 3)
    meta = hub.recorder.entity_meta()[e.id]
    assert meta["kind"] == "sound_level" and meta["unit"] == "dB"
    assert meta["doc"]["labels"] == {"weighting": "A", "metric": "Leq", "slot": "3", "period": "15 min"}


def test_history_gives_the_last_reading_in_each_bucket_and_never_an_average(hub):
    """Readings 0..9 s apart. With two 5 s buckets the points are the readings at 4 s and 9 s, untouched
    (an average would give 90.0 and 95.0 or something in between)."""
    e = add_level(hub)
    base = 1_000_000.0
    for i in range(10):
        hub.update_state(e.id, 90.0 + i + 0.07, base + i)
    out = hub.recorder.history([e.id], base, base + 10, max_points=2)[e.id]
    assert out == [[base + 4, 94.07], [base + 9, 99.07]]


def test_history_skips_not_available_records_so_an_outage_is_a_gap(hub):
    e = add_level(hub)
    base = 1_000_000.0
    for i, v in enumerate([90.5, 91.5, None, None, None, None, None, None, 92.5, 93.5]):
        hub.update_state(e.id, v, base + i)
    pts = hub.recorder.history([e.id], base, base + 10, max_points=10)[e.id]
    assert [round(p[0] - base) for p in pts] == [0, 1, 8, 9]
    assert [p[1] for p in pts] == [90.5, 91.5, 92.5, 93.5]
    # a bucket whose last record is "not available" still reports its last real reading
    pts = hub.recorder.history([e.id], base, base + 10, max_points=2)[e.id]
    assert pts == [[base + 1, 91.5], [base + 9, 93.5]]


def test_other_kinds_are_still_averaged_in_history(hub):
    hub.register_device(Device("n1", "Node", "esphome"))
    t = hub.register_entity(Entity("n1.t", "n1", "Temp", Kind.TEMPERATURE, "Â°C", 1))
    base = 1_000_000.0
    hub.update_state(t.id, 10.0, base + 1)
    hub.update_state(t.id, 20.0, base + 2)
    out = hub.recorder.history([t.id], base, base + 10, max_points=1)[t.id]
    assert len(out) == 1 and out[0][1] == 15.0


# ------------------------------------------------------------- integration (fake source)
class FakeSource(SplSource):
    label = "Fake"

    def __init__(self, owner):
        super().__init__(owner._reading, owner._link)
        self.started = self.stopped = 0

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def make(hub, slots=("a_slow", "c_slow", "laeq_15m"), clock=None, **kw):
    clock = clock or Clock()
    hub.config.spl = SplConfig(enabled=True, host="127.0.0.1", port=26000, slots=list(slots))
    integ = SmaartIntegration(hub, source_factory=FakeSource, clock=clock, **kw)
    hub.add_integration(integ)
    await integ.apply()
    return integ, integ._source, clock


def values(hub):
    return {e.id: e.value for e in hub.entities.values() if e.device_id == "spl"}


async def test_apply_registers_one_entity_per_chosen_value_in_slot_order_and_a_service_device(hub):
    integ, src, _ = await make(hub)
    assert src.started == 1
    assert hub.devices["spl"].category == "service" and hub.devices["spl"].status == Status.INITIALIZING
    ents = [e for e in hub.entities.values() if e.device_id == "spl"]
    assert [(e.id, e.labels["slot"]) for e in ents] == [("spl.a_slow", "1"), ("spl.c_slow", "2"), ("spl.laeq_15m", "3")]
    assert all(e.kind == Kind.SOUND_LEVEL and e.unit == "dB" and e.value is None for e in ents)
    await integ.stop()


async def test_readings_are_stored_exactly_and_a_missing_value_is_not_available(hub):
    integ, src, clock = await make(hub)
    src._on_link(True, "Connected")
    src._on_reading(SplReading(1000.0, {"SPL A Slow": 94.3, "SPL C Slow": 99.15, "LAeq 15": None}, "9.1"))
    assert values(hub) == {"spl.a_slow": 94.3, "spl.c_slow": 99.15, "spl.laeq_15m": None}
    assert hub.devices["spl"].status == Status.OK and "2 of 3" in hub.devices["spl"].status_detail
    src._on_reading(SplReading(1001.0, {}))
    assert all(v is None for v in values(hub).values())
    assert hub.devices["spl"].status == Status.COMPROMISED
    await integ.stop()


async def test_a_value_the_source_does_not_offer_at_all_stays_not_available(hub):
    integ, src, _ = await make(hub, slots=("z_slow", "a_slow"))
    src._on_link(True, "x")
    src._on_reading(SplReading(1000.0, {"SPL A Slow": 90.0}))
    assert values(hub) == {"spl.z_slow": None, "spl.a_slow": 90.0}
    await integ.stop()


async def test_link_loss_is_a_gap_then_a_marker_when_readings_come_back(hub):
    integ, src, clock = await make(hub)
    src._on_link(True, "Connected")
    src._on_reading(SplReading(1000.0, {"SPL A Slow": 90.0, "SPL C Slow": 95.0, "LAeq 15": 92.0}))
    clock.t = 1001.0
    src._on_link(False, "Can't reach Smaart")
    assert hub.devices["spl"].status == Status.MISSING
    assert hub.alarms.to_list() and all(a["silent"] for a in hub.alarms.to_list())   # a notice, never a sound
    assert all(v is None for v in values(hub).values())
    assert hub.recorder.markers() == []    # nothing is marked while it is down, and nothing is back-filled
    clock.t = 1131.0
    src._on_link(True, "Connected")
    src._on_reading(SplReading(1131.0, {"SPL A Slow": 91.0, "SPL C Slow": 96.0, "LAeq 15": 93.0}))
    assert hub.devices["spl"].status == Status.OK and not hub.alarms.to_list()
    marks = [m for m in hub.recorder.markers() if m.source == "spl"]
    assert len(marks) == 1 and "resumed after a gap" in marks[0].label and "2 min" in marks[0].label
    hub.recorder.flush()
    rows = hub.recorder._db.execute(
        "SELECT value FROM states WHERE entity_id = 'spl.a_slow' ORDER BY rowid").fetchall()
    assert rows == [(90.0,), (None,), (91.0,)]    # one gap record, no zeros, no carried-forward value
    await integ.stop()


async def test_a_flapping_link_adds_at_most_one_marker_a_minute(hub):
    integ, src, clock = await make(hub, slots=("a_slow",))
    for n in range(3):
        src._on_link(True, "up")
        src._on_reading(SplReading(1000.0 + n * 10, {"SPL A Slow": 90.0}))
        src._on_link(False, "down")
    src._on_link(True, "up")
    src._on_reading(SplReading(1040.0, {"SPL A Slow": 90.0}))
    assert len([m for m in hub.recorder.markers() if m.source == "spl"]) == 1
    await integ.stop()


async def test_no_marker_on_the_first_connection_only_after_a_gap(hub):
    integ, src, _ = await make(hub)
    src._on_link(False, "Can't reach Smaart")      # never had data: nothing to mark
    src._on_link(True, "up")
    src._on_reading(SplReading(1000.0, {"SPL A Slow": 90.0}))
    assert [m for m in hub.recorder.markers() if m.source == "spl"] == []
    await integ.stop()


async def test_connected_but_silent_is_a_gap_too(hub):
    integ, src, clock = await make(hub, stale_after_s=10.0)
    src._on_link(True, "up")
    src._on_reading(SplReading(1000.0, {"SPL A Slow": 90.0, "SPL C Slow": 95.0, "LAeq 15": 92.0}))
    clock.t = 1010.0
    integ.tick()
    assert values(hub)["spl.a_slow"] == 90.0 and hub.devices["spl"].status == Status.OK   # exactly at the limit: not yet
    clock.t = 1010.5
    integ.tick()
    assert all(v is None for v in values(hub).values())
    assert hub.devices["spl"].status == Status.COMPROMISED
    clock.t = 1020.0
    src._on_reading(SplReading(1020.0, {"SPL A Slow": 91.0, "SPL C Slow": 96.0, "LAeq 15": 93.0}))
    assert values(hub)["spl.a_slow"] == 91.0 and hub.devices["spl"].status == Status.OK
    assert len([m for m in hub.recorder.markers() if m.source == "spl"]) == 1
    await integ.stop()


async def test_connected_but_never_any_value_says_so_and_stores_nothing(hub):
    integ, src, clock = await make(hub, stale_after_s=10.0)
    src._on_link(True, "up")
    clock.t = 1011.0
    integ.tick()
    assert hub.devices["spl"].status == Status.COMPROMISED and "no values" in hub.devices["spl"].status_detail
    hub.recorder.flush()
    assert hub.recorder._db.execute("SELECT COUNT(*) FROM states WHERE entity_id LIKE 'spl.%'").fetchone()[0] == 0
    await integ.stop()


async def test_changing_the_chosen_values_keeps_exactly_those_entities_and_their_history(hub):
    integ, src, _ = await make(hub)
    src._on_link(True, "up")
    src._on_reading(SplReading(1000.0, {"SPL A Slow": 90.0, "SPL C Slow": 95.0, "LAeq 15": 92.0}))
    hub.config.spl = SplConfig(enabled=True, host="127.0.0.1", port=26000, slots=["laeq_15m", "a_fast"])
    await integ.apply()
    assert src.started == 1 and src.stopped == 0     # same source: no reconnect for a value change
    ents = {e.id: e.labels["slot"] for e in hub.entities.values() if e.device_id == "spl"}
    assert ents == {"spl.laeq_15m": "1", "spl.a_fast": "2"}
    hub.recorder.flush()
    assert hub.recorder._db.execute("SELECT COUNT(*) FROM states WHERE entity_id = 'spl.a_slow'").fetchone()[0] == 1
    await integ.stop()


async def test_a_new_address_restarts_the_source_and_switching_off_removes_the_device(hub):
    integ, src, _ = await make(hub)
    hub.config.spl = SplConfig(enabled=True, host="127.0.0.2", port=26000, slots=["a_slow"])
    await integ.apply()
    assert src.stopped == 1 and integ._source is not src and integ._source.started == 1
    hub.config.spl = SplConfig(enabled=False, host="127.0.0.2", port=26000, slots=["a_slow"])
    await integ.apply()
    assert integ._source is None and "spl" not in hub.devices
    assert not [e for e in hub.entities.values() if e.device_id == "spl"]
    await integ.stop()


async def test_enabled_without_an_address_does_not_start_a_real_source(hub):
    hub.config.spl = SplConfig(enabled=True, host="", port=None)      # the defaults are 127.0.0.1:26000
    integ = SmaartIntegration(hub, source_factory=FakeSource)
    await integ.apply()
    assert integ._source is None and "spl" not in hub.devices


async def test_callbacks_after_switch_off_do_nothing(hub):
    integ, src, _ = await make(hub)
    hub.config.spl = SplConfig(enabled=False)
    await integ.apply()
    src._on_link(False, "late")
    src._on_reading(SplReading(1.0, {"SPL A Slow": 90.0}))
    assert "spl" not in hub.devices and not [m for m in hub.recorder.markers() if m.source == "spl"]


def test_manifest_is_honest():
    assert MANIFEST.tier == "experimental" and MANIFEST.direction == "in" and MANIFEST.entity_kinds == ("sound_level",)
    text = MANIFEST.description.lower()
    assert "not tested" in text and "four fixed messages" in text and "never averages" in text
    assert "history" in text and "live smaart" in text


# --------------------------------------------------------------------------- emulate
def test_emulated_source_behaves_like_the_protocol_and_has_an_outage_and_an_overload():
    from stagewatch.integrations.smaart import mapping
    src = EmulatedSplSource(lambda r: None, lambda u, d: None, first_outage_s=60, outage_every_s=300, outage_s=10)
    msgs = src.messages(5.0)
    assert list(msgs) == list(INPUT_LABELS) and len(INPUT_LABELS) == 2
    v = mapping.parse_stream_message(msgs[INPUT_LABELS[0]])
    assert set(v) == {"SPL A Slow", "SPL C Slow", "LAeq 1", "LAeq 10"} and "LAeq 15" not in v
    assert all(spl.clean_level(x) is not None for x in v.values())
    assert [src.in_outage(t) for t in (0, 59.9, 60, 69.9, 70, 359.9, 360, 369.9, 370)] == \
        [False, False, True, True, False, False, True, True, False]
    # an overload point on the first input only: "not available", the rest still numbers
    assert [src.in_overload(t) for t in (0, 29.9, 30, 32.9, 33, 119.9, 120, 122.9)] == \
        [False, False, True, True, False, False, True, True]
    ov = mapping.parse_stream_message(src.messages(31.0)[INPUT_LABELS[0]])
    assert ov["SPL A Slow"] is None and ov["SPL C Slow"] is not None
    assert mapping.parse_stream_message(src.messages(31.0)[INPUT_LABELS[1]])["SPL A Slow"] is not None
    src.set_wanted([INPUT_LABELS[1]])
    assert list(src.messages(5.0)) == [INPUT_LABELS[1]]


def test_input_name_is_cleaned_capped_and_empty_when_unknown():
    assert spl.clean_input_name("ASIO MADIface USB : Channel 7 (1)") == "ASIO MADIface USB : Channel 7 (1)"
    assert spl.clean_input_name("A\x00B\x1b[31m\u202eC\r\nD\t E") == "AB[31mC D E"
    assert spl.clean_input_name("x" * 5000) == "x" * spl.INPUT_NAME_MAX
    assert spl.clean_input_name("<img src=x onerror=alert(1)>") == "<img src=x onerror=alert(1)>"   # text only; shown with textContent
    assert [spl.clean_input_name(v) for v in (None, 5, b"x", "", "  \n ")] == [""] * 5


def test_sources_report_an_input_name_the_emulated_one_and_the_real_one_empty():
    emu = EmulatedSplSource(lambda r: None, lambda u, d: None)
    assert emu.input_name == ""
    emu.input_name = "q" * 999 + "\x00"
    assert emu.input_name == "q" * spl.INPUT_NAME_MAX
    real = SmaartSource(lambda: None, lambda r: None, lambda u, d: None)
    assert real.input_name == ""


async def test_the_input_name_reaches_the_public_device_and_the_admin_status(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.config.spl = SplConfig(enabled=True, slots=["a_slow"])
    integ = SmaartIntegration(hub, emulate=True, source_factory=lambda o: EmulatedSplSource(
        o._reading, o._link, o._catalog, period_s=0.01, first_outage_s=60))
    hub.add_integration(integ)
    try:
        await integ.start()
        want = "ASIO MADIface USB : Channel 7 (1)"
        await until(lambda: hub.devices["spl"].input_name == want)
        assert hub.devices["spl"].to_dict()["input_name"] == want
        assert integ.admin_status()["input_name"] == want and integ.admin_status()["source"] == "Simulated Smaart"
    finally:
        await integ.stop()
        hub.recorder.close()


async def test_hostile_input_and_metric_names_are_cleaned_before_they_are_public(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.config.spl = SplConfig(enabled=True, slots=["a_slow"])
    made = []

    def factory(o):
        s = EmulatedSplSource(o._reading, o._link, o._catalog, period_s=0.01, first_outage_s=60)
        made.append(s)
        return s

    integ = SmaartIntegration(hub, emulate=True, source_factory=factory)
    hub.add_integration(integ)
    try:
        await integ.start()
        await until(lambda: integ._inputs)           # the simulated Smaart has announced its own lists
        made[0]._catalog(["<b>\x00" + "L" * 500], ["<i>\x00M" * 3])
        name = hub.devices["spl"].to_dict()["input_name"]
        assert len(name) == spl.INPUT_NAME_MAX and "\x00" not in name
        st = integ.admin_status()
        assert st["inputs"] == [name] and "\x00" not in st["metrics"][0]
        made[0]._catalog([], [])
        assert hub.devices["spl"].input_name == "" and "input_name" not in hub.devices["spl"].to_dict()
    finally:
        await integ.stop()
        hub.recorder.close()


async def test_emulate_mode_runs_through_the_hub_with_a_dropout_and_comes_back(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.config.spl = SplConfig(enabled=True, slots=["a_slow", "c_slow", "laeq_15m"])
    integ = SmaartIntegration(hub, emulate=True, source_factory=lambda o: EmulatedSplSource(
        o._reading, o._link, o._catalog, period_s=0.01, first_outage_s=0.15, outage_every_s=1.0, outage_s=0.25))
    hub.add_integration(integ)
    try:
        await integ.start()
        assert hub.devices["spl"].name == "Sound level (simulated)"
        await until(lambda: values(hub)["spl.a_slow"] is not None)
        v = values(hub)
        # the simulated Smaart has no "LAeq 15": the "not available" path, said so in the admin status
        assert v["spl.c_slow"] is not None and v["spl.laeq_15m"] is None
        assert [s["metric_listed"] for s in integ.admin_status()["slots"]] == [True, True, False]
        await until(lambda: hub.devices["spl"].status == Status.MISSING)      # the dropout
        await until(lambda: values(hub)["spl.a_slow"] is None)
        await until(lambda: hub.devices["spl"].status == Status.OK and values(hub)["spl.a_slow"] is not None
                    and any(m.source == "spl" for m in hub.recorder.markers()))
        assert hub.devices["spl"].status_detail == "Receiving values (2 of 3 available)"
    finally:
        await integ.stop()
        hub.recorder.close()


def test_seed_emulate_spl_switches_it_on_once_for_a_fresh_config():
    cfg = Config()
    assert seed_emulate_spl(cfg) is True
    assert cfg.spl.enabled and "spl_live" in cfg.dashboard("foh").cards and "spl_live" in cfg.dashboard("wall").cards
    assert cfg.spl.effective_slots() == [("", "SPL A Slow"), ("", "SPL C Slow"), ("", "LAeq 10")]
    assert "spl_live" not in cfg.dashboard("phone").cards
    assert seed_emulate_spl(cfg) is False
    saved = Config.model_validate(cfg.model_dump(mode="json"))
    assert seed_emulate_spl(saved) is False        # settings exist after a save: never re-enabled behind the admin's back


# ------------------------------------------------------------------------------- config
def test_older_config_without_the_section_loads_with_defaults_and_no_schema_bump():
    assert CONFIG_SCHEMA_VERSION == 2 and DB_SCHEMA_VERSION == 3      # additive config, no table change: no bump
    old = yaml.safe_load((ROOT / "tests" / "fixtures" / "v1" / "config.yaml").read_text(encoding="utf-8"))
    cfg = Config.model_validate(migrate(old))
    assert cfg.spl == SplConfig() and cfg.spl.enabled is False and cfg.spl.host == "127.0.0.1" and cfg.spl.port == 26000
    assert cfg.spl.slots == ["a_slow", "c_slow", "laeq_15m"] and cfg.spl.meters is None and cfg.spl.password == ""
    assert cfg.spl.effective_slots() == [("", "SPL A Slow"), ("", "SPL C Slow"), ("", "LAeq 15")]
    assert cfg.site.name == old["site"]["name"]


def test_saved_settings_round_trip_through_a_save_and_a_load(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.spl = SplConfig(enabled=True, host="Smaart-Laptop.local", port=26000, slots=["c_fast", "a_peak"])
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load()
    # an unset password and unset meters are not written at all: a file that never used them is unchanged
    assert again.spl.model_dump() == {"enabled": True, "host": "smaart-laptop.local", "port": 26000,
                                      "slots": ["c_fast", "a_peak"]}
    assert "password" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert again.spl.password == "" and again.spl.meters is None


def test_input_and_metric_slots_round_trip_and_older_builds_still_see_the_first_input_ones(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    pairs = [("", "SPL A Slow"), (INPUT_LABELS[1], "SPL C Slow"), ("", "LAeq 10")]
    store.config.spl = SplConfig(enabled=True, meters=[SplSlot(metric=m, source=s) for s, m in pairs],
                                 slots=spl.legacy_keys_for(pairs))
    store.save()
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))["spl"]
    assert raw["slots"] == ["a_slow"] and raw["meters"][1] == {"metric": "SPL C Slow", "source": INPUT_LABELS[1]}
    assert ConfigStore(tmp_path / "config.yaml").load().spl.effective_slots() == pairs


def test_an_older_file_with_metric_only_slots_still_loads_with_no_source(tmp_path, caplog):
    """A file written before sources existed: only Stagewatch metric keys, no meters key."""
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 2, "site": {"name": "Keep Me"},
                                 "spl": {"enabled": True, "host": "192.168.1.5", "port": 26000,
                                         "slots": ["c_slow", "z_fast", "a_slow"]}}), encoding="utf-8")
    cfg = ConfigStore(p).load()
    # no source = "the first input Smaart lists"; a key with no known Smaart name is kept as it was
    # written (Smaart will not list it, so it shows as not available) and keeps its old entity id
    assert cfg.spl.effective_slots() == [("", "SPL C Slow"), ("", "z_fast"), ("", "SPL A Slow")]
    assert spl.slot_ids(cfg.spl.effective_slots()) == ["spl.c_slow", "spl.z_fast", "spl.a_slow"]
    assert cfg.site.name == "Keep Me"


def test_slot_ids_are_stable_unique_and_carry_the_source_only_when_one_is_chosen():
    ids = spl.slot_ids([("", "SPL A Slow"), (INPUT_LABELS[1], "SPL A Slow"), ("", "LAeq 10"), ("", "LAeq 10")])
    assert ids[0] == "spl.a_slow" and ids[2] == "spl.laeq_10" and ids[3] == "spl.laeq_10_2"
    assert ids[1] == "spl.a_slow.asio_madiface_usb_channel_8_2" and len(set(ids)) == 4
    assert spl.slot_labels(2, "SPL C Slow", "X") == {"weighting": "C", "metric": "SPL", "slot": "2", "time_constant": "Slow",
                                                     "smaart_name": "SPL C Slow", "source": "X"}
    assert spl.slot_labels(1, "LAeq 10", "") == {"slot": "1", "smaart_name": "LAeq 10"}


@pytest.mark.parametrize("host", ["http://x", "x/y", "x y", "a@b", "8.8.8.8", "2001:4860:4860::8888", "224.0.0.1", "0.0.0.0",
                                  "x" * 254, "-bad", "bad-", "bad\n", "bad host", 5])
def test_the_address_must_be_a_plain_host_on_the_local_network(host):
    with pytest.raises(Exception):
        SplConfig(host=host)


@pytest.mark.parametrize("host,expected", [("", ""), ("192.168.1.20", "192.168.1.20"), ("10.0.0.5", "10.0.0.5"),
                                           ("127.0.0.1", "127.0.0.1"), ("fe80::1", "fe80::1"), ("Smaart.LOCAL", "smaart.local")])
def test_good_addresses(host, expected):
    assert SplConfig(host=host).host == expected


def test_slots_on_load_are_cleaned_not_refused(caplog):
    cfg = SplConfig(slots=["a_slow", "a_slow", "bogus", "c_slow", "z_slow", "laeq_15m"])
    assert cfg.slots == ["a_slow", "c_slow", "z_slow"]
    assert SplConfig(slots="a_slow").slots == list(spl.DEFAULT_SLOTS)
    assert "bogus" not in caplog.text


def test_a_damaged_section_resets_only_itself(tmp_path, caplog):
    raw = {"schema_version": 2, "site": {"name": "Keep Me", "altitude_m": 12.0}, "spl": {"enabled": True, "host": "http://x:1/y", "port": 99999},
           "barometer": {"hemisphere": "south"}}
    cfg, notes = salvage(raw, "")
    assert cfg.site.name == "Keep Me" and cfg.barometer.hemisphere == "south"
    assert cfg.spl.host == "" and cfg.spl.port == 26000 and cfg.spl.slots == list(spl.DEFAULT_SLOTS) and any("spl" in n for n in notes)   # bad address cleared, bad port back to the default
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    loaded = ConfigStore(p).load()
    assert loaded.site.name == "Keep Me" and loaded.spl.host == "" and loaded.spl.port == 26000
    assert "http://x" not in caplog.text


def test_a_newer_release_adding_keys_to_the_section_is_ignored():
    assert SplConfig.model_validate({"enabled": True, "future": "x"}).enabled is True


def test_damaged_values_and_password_are_cleaned_on_load_without_losing_the_rest(tmp_path, caplog):
    """A damaged meters list or password is dropped on its own; the address, port and site survive."""
    secret = "hunter2-damaged"
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 2, "site": {"name": "Keep Me"}, "spl": {
        "enabled": True, "host": "192.168.1.5", "port": 26000, "password": secret + "\x01",
        "meters": [{"metric": "SPL A Slow"}, {"metric": ""}, {"source": "x"}, "junk", {"metric": "SPL A Slow"},
                   {"metric": "LAeq 10", "source": INPUT_LABELS[0]}, {"metric": "d"}, {"metric": "e"}]}}), encoding="utf-8")
    store = ConfigStore(p)
    cfg = store.load()
    assert cfg.site.name == "Keep Me" and store.invalid_files() == []
    assert cfg.spl.host == "192.168.1.5" and cfg.spl.port == 26000 and cfg.spl.enabled
    assert cfg.spl.password == ""
    assert cfg.spl.effective_slots() == [("", "SPL A Slow"), (INPUT_LABELS[0], "LAeq 10"), ("", "d")]   # usable, once each, at most three
    assert secret not in caplog.text
    # a meters list that is not a list at all falls back to the older slots
    assert SplConfig.model_validate({"meters": "oops", "slots": ["a_slow"]}).effective_slots() == [("", "SPL A Slow")]
    # and a whole damaged section (wrong types everywhere) resets only itself
    cfg, notes = salvage({"schema_version": 2, "site": {"name": "Keep Me"},
                          "spl": {"enabled": "maybe", "port": "x", "meters": 5, "password": [1]}}, "")
    assert cfg.site.name == "Keep Me" and cfg.spl == SplConfig() and any("spl" in n for n in notes)


def test_the_password_is_not_in_the_repr_or_the_logs_and_is_checked():
    secret = "s3cret-Smaart-pw"
    c = SplConfig(password=secret)
    assert secret not in repr(c) and secret not in str(c) and secret not in repr(Config(spl=c))
    assert c.password == secret
    for bad in ("x" * 129, "a\nb", "a\x00b", ["x"]):
        with pytest.raises(Exception) as exc:
            SplConfig(password=bad)
        assert "x" * 129 not in str(exc.value) and "a\nb" not in str(exc.value)
    assert SplConfig(password="").password == "" and SplConfig(password=None).password == ""


# ---------------------------------------------------------------------- cards and API
def test_the_card_is_known_but_not_on_by_default():
    assert "spl_live" in cards.KNOWN_CARDS
    assert all("spl_live" not in cards.default_cards(layout) for layout in cards.LAYOUT_DEFAULTS)
    assert cards.strict_cards_error(["env_tiles", "spl_live"]) is None


@pytest.fixture
def client_app(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    integ = SmaartIntegration(hub, emulate=True, source_factory=lambda o: EmulatedSplSource(
        o._reading, o._link, o._catalog, period_s=0.02, first_outage_s=1000))
    hub.add_integration(integ)
    app = create_app(hub, lan_addresses=lambda: ["192.0.2.10"])
    with TestClient(app) as c:
        c.hub = hub
        yield c


def admin(c):
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200


def poll(cond, timeout=5.0):
    """Wait (with a deadline) for something the app does in the background."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting")


def put(c, **body):
    base = {"enabled": True, "host": "", "port": None, "slots": ["a_slow", "c_slow", "laeq_15m"]}
    return c.put("/api/admin/spl", json={**base, **body})


def test_put_spl_needs_the_admin_session_and_the_same_origin(client_app):
    c = client_app
    assert put(c).status_code == 401
    admin(c)
    r = c.put("/api/admin/spl", json={"enabled": True, "slots": []}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert put(c, slots=["a_slow"]).status_code == 200


@pytest.mark.parametrize("body", [
    {"slots": ["a_slow", "c_slow", "z_slow", "a_fast"]}, {"slots": ["nope"]}, {"slots": ["a_slow", "a_slow"]},
    {"slots": "a_slow"}, {"slots": [1]}, {"enabled": "yes"}, {"enabled": 1}, {"port": "26000"}, {"port": 0}, {"port": 70000},
    {"port": 26000.5}, {"host": "8.8.8.8"}, {"host": "http://x"}, {"host": "x" * 300}, {"extra": 1},
    {"meters": [{"metric": "a"}] * 4}, {"meters": [{"metric": "SPL A Slow"}, {"metric": "SPL A Slow"}]},
    {"meters": [{"metric": ""}]}, {"meters": [{"metric": "a\x00b"}]}, {"meters": [{"metric": "x" * 65}]},
    {"meters": [{"metric": "a", "source": "y" * 81}]}, {"meters": [{"metric": "a", "extra": 1}]}, {"meters": "a"},
    {"meters": [{"metric": "a", "source": "bad\nsource"}]}, {"meters": [5]},
    {"password": "p" * 129}, {"password": "bad\x01pw"}, {"password": 5}, {"clear_password": "yes"},
    {"password": "newpw", "clear_password": True},
])
def test_put_spl_refuses_bad_input_with_fixed_text_and_changes_nothing(client_app, body):
    c = client_app
    admin(c)
    assert put(c, password="old-secret-pw").status_code == 200
    before = c.hub.config.spl.model_dump()
    r = put(c, **body)
    assert r.status_code == 422
    for echoed in ("8.8.8.8", "http://x", "nope", "old-secret-pw", "newpw", "bad\x01pw", "p" * 129, "bad\nsource"):
        assert echoed not in r.text
    assert c.hub.config.spl.model_dump() == before


def test_put_spl_in_real_mode_needs_an_address_and_port(tmp_path):
    hub = Hub(tmp_path)
    hub.add_integration(SmaartIntegration(hub, source_factory=FakeSource))
    with TestClient(create_app(hub, lan_addresses=lambda: ["192.0.2.10"])) as c:
        admin(c)
        assert put(c).status_code == 422                       # no address
        assert put(c, enabled=False).status_code == 200
        r = put(c, host="192.168.1.5")                          # no port: Smaart's usual one
        assert r.status_code == 200 and hub.config.spl.port == 26000
        assert hub.integrations["smaart"]._source is not None


def test_saving_with_a_password_or_after_a_refused_one_restarts_the_source_but_a_plain_save_does_not(tmp_path):
    hub = Hub(tmp_path)
    integ = SmaartIntegration(hub, source_factory=FakeSource)
    hub.add_integration(integ)
    with TestClient(create_app(hub, lan_addresses=lambda: ["192.0.2.10"])) as c:
        admin(c)
        assert put(c, host="192.168.1.5", port=26000).status_code == 200
        first = integ._source
        assert put(c, host="192.168.1.5", port=26000, slots=["c_slow"]).status_code == 200
        assert integ._source is first and first.stopped == 0          # a plain save: no reconnect
        assert put(c, host="192.168.1.5", port=26000, password=SECRET).status_code == 200
        second = integ._source
        assert second is not first and first.stopped == 1               # a new password: log in afresh
        assert put(c, host="192.168.1.5", port=26000, password=SECRET).status_code == 200
        third = integ._source
        assert third is not second and second.stopped == 1              # the same password again: still tries again
        third.problem = "wrong_password"
        assert put(c, host="192.168.1.5", port=26000).status_code == 200
        assert integ._source is not third and third.stopped == 1        # saving after a refusal: tries again


@pytest.mark.parametrize("host", ["::ffff:192.168.1.5", "::ffff:8.8.8.8", "2002:c0a8:105::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2"])
def test_ipv6_forms_that_hide_another_address_are_refused_as_typed_addresses(host):
    from stagewatch.core.config import spl_host_error
    assert spl_host_error(host) and "local network" in spl_host_error(host)
    with pytest.raises(Exception):
        SplConfig(host=host)
    assert spl_host_error("127.0.0.1") is None and spl_host_error("::1") is None and spl_host_error("fe80::1") is None


def test_a_numeric_password_in_the_file_is_read_as_text_and_never_logged(tmp_path, caplog):
    p = tmp_path / "config.yaml"
    p.write_text("schema_version: 2\nspl:\n  enabled: true\n  host: 192.168.1.5\n  password: 1234\n", encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert cfg.spl.password == "1234" and cfg.spl.host == "192.168.1.5"
    assert SplConfig(password=1234).password == "1234" and "1234" not in caplog.text
    assert SplConfig(password=0.5).password == "0.5"
    with pytest.raises(Exception):
        SplConfig(password=True)


def test_put_spl_applies_at_once_and_the_snapshot_has_the_public_shape_only(client_app):
    c = client_app
    admin(c)
    r = put(c, slots=["c_slow", "a_fast"])
    assert r.status_code == 200 and r.json() == {"ok": True, "changed": True, "password_set": False}
    snap = c.get("/api/snapshot").json()
    ents = [e for e in snap["entities"] if e["kind"] == "sound_level"]
    assert [e["id"] for e in ents] == ["spl.c_slow", "spl.a_fast"]
    assert {"weighting": "C", "metric": "SPL", "slot": "1", "time_constant": "Slow",
            "smaart_name": "SPL C Slow"}.items() <= ents[0]["labels"].items()
    assert set(ents[0]["labels"]) <= {"weighting", "metric", "slot", "time_constant", "smaart_name", "source"}
    dev = next(d for d in snap["devices"] if d["id"] == "spl")
    assert set(dev) == {"id", "name", "integration", "category", "manufacturer", "model", "area", "status", "status_detail", "input_name",
                       "chart_range", "chart_min_db", "chart_max_db"}   # the graph range is the only addition
    assert dev["category"] == "service"


def test_put_spl_with_input_and_metric_slots_makes_one_entity_each_with_smaarts_own_text(client_app):
    c = client_app
    admin(c)
    meters = [{"source": "", "metric": "SPL A Slow"}, {"source": INPUT_LABELS[1], "metric": "LAeq 10"},
              {"source": INPUT_LABELS[1], "metric": "SPL A Slow"}]
    assert put(c, meters=meters).status_code == 200
    ents = [e for e in c.get("/api/snapshot").json()["entities"] if e["kind"] == "sound_level"]
    assert [e["id"] for e in ents] == ["spl.a_slow", "spl.laeq_10.asio_madiface_usb_channel_8_2",
                                       "spl.a_slow.asio_madiface_usb_channel_8_2"]
    assert [e["name"] for e in ents] == ["SPL A Slow", "LAeq 10", "SPL A Slow"]
    assert [e["labels"]["smaart_name"] for e in ents] == ["SPL A Slow", "LAeq 10", "SPL A Slow"]
    assert c.hub.config.spl.slots == ["a_slow"]                  # what an older build can still show
    st = c.get("/api/admin/state").json()
    assert st["config"]["spl"]["meters"][1] == {"metric": "LAeq 10", "source": INPUT_LABELS[1]}
    # the simulated Smaart answers: the lists come from it, and each slot says whether it is listed
    poll(lambda: c.get("/api/admin/state").json()["spl"]["inputs"])
    sp = c.get("/api/admin/state").json()["spl"]
    assert sp["inputs"] == list(INPUT_LABELS) and sp["metrics"] == ["SPL A Slow", "SPL C Slow", "LAeq 1", "LAeq 10"]
    assert [s["metric_listed"] for s in sp["slots"]] == [True, True, True] and [s["source_listed"] for s in sp["slots"]] == [True] * 3
    assert sp["verified"] is False and sp["default_metrics"]["laeq_15m"] == "LAeq 15"


def test_an_unrecognised_input_or_metric_is_kept_and_reported_not_silently_changed(client_app):
    c = client_app
    admin(c)
    assert put(c, meters=[{"source": "Nowhere : Channel 1", "metric": "LAeq 15"}]).status_code == 200
    poll(lambda: c.get("/api/admin/state").json()["spl"]["inputs"])
    sl = c.get("/api/admin/state").json()["spl"]["slots"][0]
    assert sl["source"] == "Nowhere : Channel 1" and sl["metric"] == "LAeq 15"
    assert sl["source_listed"] is False and sl["metric_listed"] is False and sl["available"] is False


def test_the_address_never_reaches_public_endpoints_but_the_admin_sees_it(client_app):
    c = client_app
    admin(c)
    assert put(c, host="192.168.50.77", port=26000).status_code == 200
    public = "".join(c.get(u).text for u in ("/api/snapshot", "/api/info", "/api/dashboard/foh"))
    assert "192.168.50.77" not in public and "26000" not in public
    st = c.get("/api/admin/state").json()
    assert st["config"]["spl"]["host"] == "192.168.50.77"
    assert st["spl"]["max_slots"] == 3 and st["spl"]["password_set"] is False
    assert "host" not in st["spl"]
    c.post("/api/admin/logout")
    assert c.get("/api/admin/state").status_code == 401


def test_switching_off_removes_the_card_data_and_a_body_over_the_limit_is_refused(client_app):
    c = client_app
    admin(c)
    assert put(c, enabled=False).status_code == 200
    assert not [e for e in c.get("/api/snapshot").json()["entities"] if e["kind"] == "sound_level"]
    r = c.put("/api/admin/spl", content=json.dumps({"enabled": True, "pad": "x" * 70_000}),
              headers={"content-type": "application/json"})
    assert r.status_code == 413


SECRET = "Sm4art-API-pw-Zq7"


def test_the_smaart_password_is_write_only_empty_keeps_it_and_clear_removes_it(client_app):
    c = client_app
    admin(c)
    r = put(c, password=SECRET)
    assert r.status_code == 200 and r.json()["password_set"] is True and SECRET not in r.text
    assert c.hub.config.spl.password == SECRET
    st = c.get("/api/admin/state")
    assert SECRET not in st.text and "password" not in st.json()["config"]["spl"]
    assert st.json()["spl"]["password_set"] is True
    assert put(c, enabled=True, password="").status_code == 200 and c.hub.config.spl.password == SECRET   # empty = unchanged
    assert put(c, password="").json()["password_set"] is True
    assert put(c, clear_password=True).json()["password_set"] is False and c.hub.config.spl.password == ""
    assert c.get("/api/admin/state").json()["spl"]["password_set"] is False
    # a new password replaces; it is stored in the settings file like the ESPHome key
    assert put(c, password=SECRET + "2").status_code == 200 and c.hub.config.spl.password == SECRET + "2"
    assert (SECRET + "2") in (c.hub.data_dir / "config.yaml").read_text(encoding="utf-8")


def test_the_smaart_password_is_needed_to_be_admin_to_set_and_never_reaches_a_public_surface(client_app, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    c = client_app
    assert put(c, password=SECRET).status_code == 401 and c.hub.config.spl.password == ""
    admin(c)
    assert c.put("/api/admin/spl", json={"enabled": True, "password": SECRET},
                 headers={"Origin": "http://evil.example"}).status_code == 403
    assert put(c, password=SECRET, host="192.168.1.5", port=26000).status_code == 200
    pages = [c.get(u).text for u in ("/api/snapshot", "/api/info", "/api/dashboard/foh", "/api/history?entities=spl.a_slow")]
    pages += [c.get("/api/admin/state").text, c.get("/api/admin/software").text]
    assert all(SECRET not in p for p in pages)
    assert SECRET not in caplog.text
    integ = c.hub.integrations["smaart"]
    assert SECRET not in repr(integ) and SECRET not in repr(integ._source) and SECRET not in json.dumps(integ.info())
    assert SECRET not in repr(c.hub.config) and SECRET not in repr(c.hub.config.spl)
    # the live WebSocket snapshot is public too
    with c.websocket_connect("/ws") as ws:
        assert SECRET not in json.dumps(ws.receive_json())


def test_the_smaart_password_is_redacted_from_the_diagnostics_bundle(client_app):
    import io
    import zipfile
    c = client_app
    admin(c)
    assert put(c, password=SECRET).status_code == 200
    (c.hub.data_dir / "logs").mkdir(exist_ok=True)
    (c.hub.data_dir / "logs" / "stagewatch.log").write_text(f"INFO something password={SECRET}\nplain {SECRET}\n", encoding="utf-8")
    r = c.get("/api/admin/diagnostics")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    assert SECRET not in blob and SECRET.encode() not in r.content
    cfg = json.loads(z.read("config.json"))
    assert cfg["spl"]["password"] == "[redacted]"


def test_a_sound_level_cannot_be_given_a_calibration_offset(client_app):
    c = client_app
    admin(c)
    assert put(c).status_code == 200
    r = c.put("/api/admin/entities/spl.a_slow", json={"offset": 3.0, "include_in_average": True})
    assert r.status_code == 409 and "cannot be adjusted" in r.text
    assert "spl.a_slow" not in c.hub.config.entities


def test_a_dashboard_can_hold_the_card_and_it_round_trips(client_app):
    c = client_app
    admin(c)
    r = c.put("/api/admin/dashboards", json=[{"slug": "foh", "cards": ["env_tiles", "spl_live", "chart"]}])
    assert r.status_code == 200 and "spl_live" in c.get("/api/dashboard/foh").json()["cards"]


def test_history_endpoint_returns_unaveraged_points_for_sound_levels(client_app):
    c = client_app
    hub = c.hub
    e = hub.entities["spl.a_slow"] if "spl.a_slow" in hub.entities else None
    admin(c)
    put(c)
    base = 2_000_000.0
    for i in range(6):
        hub.update_state("spl.a_slow", 80.0 + i * 1.11, base + i)
    pts = c.get(f"/api/history?entities=spl.a_slow&since={base}&until={base + 6}&points=10").json()["spl.a_slow"]
    assert [p[1] for p in pts if base <= p[0] < base + 6] == [80.0 + i * 1.11 for i in range(6)]


# ------------------------------------------------------------------------- static / JS
def test_the_card_has_one_section_a_registry_entry_and_an_admin_name():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert html.count('data-card="spl_live"') == 1 and "/static/spl.js" in html
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "spl_live:" in js
    admin_js = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert "spl_live:" in admin_js and "splCard()" in admin_js


def test_js_view_cases_run_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "spl_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


# ------------------------------------------------------------------ review fixes
@pytest.mark.parametrize("host", ["134744072", "0x8.0x8.0x8.0x8", "8.8.2056", "010.010.010.010", "host.0x10", "a.b.123",
                                  "0X7F000001"])
def test_numeric_looking_host_names_are_refused(host):
    with pytest.raises(Exception):
        SplConfig(host=host)


def test_ordinary_names_with_digits_still_work():
    assert SplConfig(host="smaart2.local").host == "smaart2.local" and SplConfig(host="foh-1").host == "foh-1"


def test_a_bad_saved_address_is_cleared_on_load_not_salvaged(tmp_path, caplog):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 2, "site": {"name": "Keep Me"},
                                 "spl": {"enabled": True, "host": "8.8.8.8", "port": 26000, "slots": ["a_slow"]}}), encoding="utf-8")
    store = ConfigStore(p)
    cfg = store.load()
    assert cfg.spl.host == "" and cfg.spl.port == 26000 and cfg.spl.enabled and cfg.spl.slots == ["a_slow"]
    assert cfg.site.name == "Keep Me" and store.invalid_files() == []      # no salvage, no quarantined file
    assert "8.8.8.8" not in caplog.text and "not usable" in caplog.text


def test_the_api_stays_strict_about_the_address(tmp_path):
    hub = Hub(tmp_path)
    with TestClient(create_app(hub, lan_addresses=lambda: [])) as c:
        admin(c)
        assert put(c, host="134744072", port=1).status_code == 422


def test_entity_dict_sends_no_offset_for_a_sound_level_even_with_a_stale_setting(hub):
    e = add_level(hub)
    hub.config.entities[e.id] = EntitySettings(offset=5.0)
    hub.update_state(e.id, 94.3, 1000.0)
    assert "offset" not in hub.entity_dict(e, 1001.0, 60.0)


# ------------------------------------------------------------------------- locations (labels only)
def test_clean_location_is_plain_capped_and_tidy():
    assert spl.clean_location("  FOH \t desk ") == "FOH desk"
    assert spl.clean_location("A\x00B\x1b\u202eC\r\nD") == "ABC D"
    assert spl.clean_location("x" * 500) == "x" * spl.LOCATION_MAX
    assert spl.clean_location("<img src=x onerror=alert(1)>")[:5] == "<img "      # text only; shown with textContent
    assert [spl.clean_location(v) for v in (None, 5, b"x", "", " \n ")] == [""] * 5


def test_location_resolution_input_then_default_then_none():
    cfg = SplConfig(location="Default", locations={INPUT_LABELS[0]: "FOH"})
    assert cfg.location_for(INPUT_LABELS[0]) == "FOH"          # its own
    assert cfg.location_for(INPUT_LABELS[1]) == "Default"      # no own: the default
    assert cfg.location_for("") == "Default"                   # input not known yet: the default
    assert SplConfig(locations={INPUT_LABELS[0]: "FOH"}).location_for(INPUT_LABELS[1]) == ""
    assert SplConfig(locations={INPUT_LABELS[0]: "FOH"}).location_for("") == ""


def test_locations_are_optional_additive_and_not_written_when_empty(tmp_path):
    assert SplConfig().location == "" and SplConfig().locations == {}
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.spl = SplConfig(enabled=True)
    store.save()
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))["spl"]
    assert "location" not in raw and "locations" not in raw
    store.config.spl = SplConfig(enabled=True, location="FOH", locations={INPUT_LABELS[1]: "Stage left"})
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load().spl
    assert again.location == "FOH" and again.locations == {INPUT_LABELS[1]: "Stage left"}


def test_damaged_locations_are_cleaned_on_load_with_a_count_only_log(tmp_path, caplog):
    many = {f"Input {i}": f"Place {i}" for i in range(12)}
    cfg = SplConfig.model_validate({"locations": {**many, "": "x", "Bad": "", "Hostile\x00": "<b>\x01" + "L" * 99, 5: "n"},
                                    "location": "A\x00" + "B" * 99})
    assert len(cfg.locations) == spl.LOCATIONS_MAX
    assert all(len(v) <= spl.LOCATION_MAX and "\x00" not in v and "\x01" not in v for v in cfg.locations.values())
    assert cfg.location == "A" + "B" * (spl.LOCATION_MAX - 1)
    assert "Place 1" not in caplog.text and "left out" in caplog.text
    assert SplConfig.model_validate({"locations": "oops", "location": 7}).locations == {}
    assert SplConfig.model_validate({"location": ["x"]}).location == ""
    # a whole damaged section still resets only itself
    raw = {"schema_version": 2, "site": {"name": "Keep Me"}, "spl": {"enabled": "maybe", "locations": 5, "location": {"a": 1}}}
    c2, notes = salvage(raw, "")
    assert c2.site.name == "Keep Me" and c2.spl.locations == {} and c2.spl.location == ""
    # and a saved file that never had them loads exactly as before
    old = yaml.safe_load((ROOT / "tests" / "fixtures" / "v1" / "config.yaml").read_text(encoding="utf-8"))
    assert Config.model_validate(migrate(old)).spl.locations == {}


def test_put_spl_locations_reach_the_public_entities_and_the_admin_state(client_app):
    c = client_app
    admin(c)
    a, b = INPUT_LABELS[0], INPUT_LABELS[1]
    meters = [{"source": "", "metric": "SPL A Slow"}, {"source": b, "metric": "SPL C Slow"}, {"source": b, "metric": "SPL A Slow"}]
    r = put(c, meters=meters, location="Desk", locations={a: "FOH", b: "  Stage \n left "})
    assert r.status_code == 200
    ents = [e for e in c.get("/api/snapshot").json()["entities"] if e["kind"] == "sound_level"]
    poll(lambda: [e.get("location") for e in c.get("/api/snapshot").json()["entities"] if e["kind"] == "sound_level"] == ["FOH", "Stage left", "Stage left"])
    ents = [e for e in c.get("/api/snapshot").json()["entities"] if e["kind"] == "sound_level"]
    # entity ids and labels are untouched by locations; the only new public field is `location`
    assert [e["id"] for e in ents] == ["spl.a_slow", "spl.c_slow.asio_madiface_usb_channel_8_2", "spl.a_slow.asio_madiface_usb_channel_8_2"]
    assert set(ents[0]) == {"id", "device_id", "name", "kind", "unit", "decimals", "derived", "value", "raw_value",
                            "updated", "stale", "labels", "location"}
    assert all("FOH" not in json.dumps(e["labels"]) for e in ents)
    st = c.get("/api/admin/state").json()["config"]["spl"]
    assert st["location"] == "Desk" and st["locations"] == {a: "FOH", b: "Stage left"}
    # a value whose input has no location of its own gets the default
    assert put(c, meters=meters, location="Desk", locations={a: "FOH"}).status_code == 200
    poll(lambda: [e.get("location") for e in c.get("/api/snapshot").json()["entities"] if e["kind"] == "sound_level"] == ["FOH", "Desk", "Desk"])
    # cleared: the field disappears from the entity
    assert put(c, meters=meters, location="", locations={}).status_code == 200
    poll(lambda: all("location" not in e for e in c.get("/api/snapshot").json()["entities"] if e["kind"] == "sound_level"))
    assert "location" not in c.get("/api/admin/state").json()["config"]["spl"]


def test_put_spl_location_only_change_does_not_restart_smaart_and_keeps_ids_and_history(client_app):
    c = client_app
    admin(c)
    assert put(c).status_code == 200
    integ = c.hub.integrations["smaart"]
    src = integ._source
    ids = sorted(e.id for e in c.hub.entities.values() if e.device_id == "spl")
    assert put(c, location="FOH", locations={INPUT_LABELS[0]: "Desk"}).status_code == 200
    assert integ._source is src                                      # no stop/start, no new login
    assert sorted(e.id for e in c.hub.entities.values() if e.device_id == "spl") == ids
    assert put(c, location="Stage left").status_code == 200
    assert integ._source is src
    assert c.hub.config.spl.locations == {INPUT_LABELS[0]: "Desk"}   # not sent: kept
    # leaving the fields out keeps what is saved
    assert c.put("/api/admin/spl", json={"enabled": True, "host": "", "port": None, "slots": ["a_slow"]}).status_code == 200
    assert c.hub.config.spl.location == "Stage left"


def test_put_spl_refuses_bad_locations_with_fixed_text_and_no_echo(client_app):
    c = client_app
    admin(c)
    a = INPUT_LABELS[0]
    secret = "Hostile-Marker-123"
    cases = [{"location": secret + "\x00"}, {"location": secret + "\u202e"}, {"location": secret * 10},
             {"locations": {a: secret + "\x07"}}, {"locations": {a: secret * 10}}, {"locations": {"": "x"}},
             {"locations": {a + "\x00": "x"}}, {"locations": {f"I{i}": "x" for i in range(9)}},
             {"location": 5}, {"locations": "x"}, {"locations": {a: 5}}, {"location": "x" * 201}]
    for body in cases:
        r = put(c, **body)
        assert r.status_code == 422, body
        assert secret not in r.text, body
    assert c.hub.config.spl.location == "" and c.hub.config.spl.locations == {}
    # the existing extra-fields rule still holds
    assert put(c, place="x").status_code == 422
    # exactly eight is fine, and a blank label removes an input's entry
    eight = {f"Input {i}": f"Place {i}" for i in range(8)}
    assert put(c, locations=eight).status_code == 200 and len(c.hub.config.spl.locations) == 8
    assert put(c, locations={**eight, "Input 0": "  "}).status_code == 200 and len(c.hub.config.spl.locations) == 7


async def test_locations_end_to_end_through_the_hub_follow_the_resolved_first_input(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.config.spl = SplConfig(enabled=True, meters=[SplSlot(metric="SPL A Slow", source=""), SplSlot(metric="SPL C Slow", source=INPUT_LABELS[1])],
                               slots=["a_slow"], locations={INPUT_LABELS[0]: "FOH", INPUT_LABELS[1]: "Stage left"}, location="Anywhere")
    integ = SmaartIntegration(hub, emulate=True, source_factory=lambda o: EmulatedSplSource(
        o._reading, o._link, o._catalog, period_s=0.01, first_outage_s=60))
    hub.add_integration(integ)
    try:
        await integ.start()
        # before Smaart has listed its inputs the first-input slot only has the default
        await until(lambda: hub.entities["spl.a_slow"].location == "FOH")        # the first input resolved
        assert hub.entities["spl.c_slow.asio_madiface_usb_channel_8_2"].location == "Stage left"
        d = hub.entities["spl.a_slow"].to_dict(0, 60)
        assert d["location"] == "FOH" and "FOH" not in json.dumps(d["labels"]) and d["id"] == "spl.a_slow"
    finally:
        await integ.stop()
        hub.recorder.close()


# ------------------------------------------------------------------------- graph range
def test_chart_range_error_is_fixed_text_and_strict():
    assert spl.chart_range_error(22, 145) is None and spl.chart_range_error(0, 10) is None and spl.chart_range_error(190, 200) is None
    for lo, hi in [(50, 50), (60, 50), (50, 59.9), (-1, 50), (50, 201), (True, 50), ("22", 145), (None, 5), (float("nan"), 50), (0, float("inf"))]:
        err = spl.chart_range_error(lo, hi)
        assert err and "22" not in err and "nan" not in err.lower()


def test_chart_range_defaults_older_config_and_damaged_values_fall_back_to_auto(tmp_path, caplog):
    c = SplConfig()
    assert (c.chart_range, c.chart_min_db, c.chart_max_db) == ("auto", 22.0, 145.0)
    old = yaml.safe_load((ROOT / "tests" / "fixtures" / "v1" / "config.yaml").read_text(encoding="utf-8"))
    assert Config.model_validate(migrate(old)).spl.chart_range == "auto"
    custom = SplConfig(chart_range="custom", chart_min_db=30, chart_max_db=130)
    assert (custom.chart_range, custom.chart_min_db, custom.chart_max_db) == ("custom", 30.0, 130.0)
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.spl = SplConfig(enabled=True)
    store.save()
    assert "chart_range" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")          # defaults are not written
    store.config.spl = custom
    store.save()
    assert ConfigStore(tmp_path / "config.yaml").load().spl.chart_max_db == 130.0
    for bad in ({"chart_range": "wide"}, {"chart_range": "custom", "chart_min_db": 90, "chart_max_db": 80},
                {"chart_range": "custom", "chart_min_db": 50, "chart_max_db": 55}, {"chart_range": "custom", "chart_min_db": "x", "chart_max_db": 99},
                {"chart_range": "custom", "chart_min_db": -5, "chart_max_db": 99}, {"chart_range": "custom", "chart_min_db": 5, "chart_max_db": 999},
                {"chart_range": "custom", "chart_min_db": True, "chart_max_db": 99}, {"chart_min_db": None}):
        caplog.clear()
        got = SplConfig.model_validate({"enabled": True, **bad})
        assert (got.chart_range, got.chart_min_db, got.chart_max_db, got.enabled) == ("auto", 22.0, 145.0, True), bad
        assert "reset to automatic" in caplog.text and "wide" not in caplog.text
    cfg, notes = salvage({"schema_version": 2, "site": {"name": "Keep Me"}, "spl": {"enabled": "maybe", "chart_range": 5}}, "")
    assert cfg.site.name == "Keep Me" and cfg.spl.chart_range == "auto"


def test_put_spl_chart_range_validation_public_fields_and_no_restart(client_app):
    c = client_app
    admin(c)
    assert put(c).status_code == 200
    integ = c.hub.integrations["smaart"]
    src = integ._source
    r = put(c, chart_range="custom", chart_min_db=30, chart_max_db=130.5)
    assert r.status_code == 200 and integ._source is src                       # a display setting: no restart or login
    dev = next(d for d in c.get("/api/snapshot").json()["devices"] if d["id"] == "spl")
    assert (dev["chart_range"], dev["chart_min_db"], dev["chart_max_db"]) == ("custom", 30.0, 130.5)
    st = c.get("/api/admin/state").json()["config"]["spl"]
    assert (st["chart_range"], st["chart_min_db"], st["chart_max_db"]) == ("custom", 30.0, 130.5)
    # not sent: kept. Only auto: the numbers stay for next time.
    assert put(c).status_code == 200 and c.hub.config.spl.chart_range == "custom"
    assert put(c, chart_range="auto").status_code == 200 and integ._source is src
    assert c.hub.config.spl.chart_range == "auto" and c.hub.config.spl.chart_max_db == 130.5
    marker = "7777.123"
    for body in ({"chart_range": "custom", "chart_min_db": 80, "chart_max_db": 80}, {"chart_range": "custom", "chart_min_db": 90, "chart_max_db": 80},
                 {"chart_range": "custom", "chart_min_db": 80, "chart_max_db": 85}, {"chart_range": "custom", "chart_min_db": -1, "chart_max_db": 80},
                 {"chart_range": "custom", "chart_min_db": 10, "chart_max_db": 200.5}, {"chart_range": "custom", "chart_min_db": 125},
                 {"chart_min_db": 130.5, "chart_max_db": 131}, {"chart_range": "wide"}, {"chart_min_db": "20"}, {"chart_min_db": True},
                 {"chart_min_db": float(marker) * 10, "chart_max_db": 1e308}):
        r = put(c, **body)
        assert r.status_code == 422 and marker not in r.text, body
    assert c.hub.config.spl.chart_range == "auto"
