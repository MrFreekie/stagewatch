"""Sound level (SPL) logging, phase 2: the sound-level kind, up to three chosen values on one
timeline, the Sound level card's data, the emulated source, and the read-only Smaart client.

Numbers are recorded exactly as received: no averaging, smoothing, rounding or calibration, a missing
value is "not available" (never zero), and offline is a gap. The Smaart wire format is NOT known (the
SDK is not public), so every Smaart-shaped message below is SYNTHETIC and read through a synthetic
mapping table defined here; the shipped table is empty and one test holds it to that.
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import subprocess
from http import HTTPStatus
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from stagewatch.core import cards, spl
from stagewatch.core.config import Config, ConfigStore, EntitySettings, SplConfig, migrate, salvage
from stagewatch.core.hub import Hub
from stagewatch.core.model import ENV_KINDS, UNITS, Device, Entity, Kind, Status
from stagewatch.core.spl import SplReading
from stagewatch.integrations.smaart import MANIFEST, SmaartIntegration, seed_emulate_spl
from stagewatch.integrations.smaart import mapping
from stagewatch.integrations.smaart.client import TEXT, SmaartSource, ws_url
from stagewatch.integrations.smaart.emulate import OFFERED, EmulatedSplSource
from stagewatch.integrations.smaart.mapping import Mapping, parse_frame
from stagewatch.integrations.smaart.source import SplSource
from stagewatch.version import CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION
from stagewatch.web.server import create_app
from test_ontime import free_port, until

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
PIN = "4711"

# ------------------------------------------------------------------ SYNTHETIC wire format
# Invented for these tests only. NOT Smaart's real format (unknown until the SDK is read).
SYNTH = Mapping(version=("app", "version"), metrics={
    "a_slow": ("levels", "a_slow"), "c_slow": ("levels", "c_slow"), "laeq_15m": ("levels", "leq", 0),
})
SYNTH_V8 = Mapping(version=("app", "version"), metrics={"a_slow": ("old", "a"), "c_slow": ("old", "c")})
TABLE = {"9": SYNTH, "8": SYNTH_V8}
DEFAULT_T = Mapping(version=("app", "version"), metrics={})


def frame(version="9.1", **levels) -> str:
    return json.dumps({"app": {"version": version}, "levels": levels})


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
    for bad in (None, 9, "", "x" * 25, "9.\n1", "<script>", "café"):
        assert spl.clean_version(bad) == ""


# --------------------------------------------------------------------------- parsing
def test_synthetic_frame_values_come_out_exactly_as_sent():
    p = parse_frame(json.dumps({"app": {"version": "9.1"}, "levels": {"a_slow": 94.3, "c_slow": 99.1, "leq": [96.05]}}),
                    TABLE, DEFAULT_T)
    assert p.version == "9.1"
    assert p.values == {"a_slow": 94.3, "c_slow": 99.1, "laeq_15m": 96.05}


def test_each_version_uses_its_own_table_and_unknown_versions_use_the_default():
    old = json.dumps({"app": {"version": "8.4"}, "old": {"a": 88.8, "c": 91.0}, "levels": {"a_slow": 1.0}})
    assert parse_frame(old, TABLE, DEFAULT_T).values == {"a_slow": 88.8, "c_slow": 91.0}
    assert parse_frame(frame("7.0", a_slow=80.0), TABLE, DEFAULT_T) is None   # no table for 7: nothing mapped


def test_a_missing_value_is_absent_and_a_bad_one_is_none_never_zero():
    p = parse_frame(frame(a_slow=94.3), TABLE, DEFAULT_T)
    assert p.values == {"a_slow": 94.3} and "c_slow" not in p.values
    p = parse_frame(frame(a_slow="94.3", c_slow=None, leq=None), TABLE, DEFAULT_T)
    assert p is not None and p.values == {"a_slow": None, "c_slow": None}
    assert all(v is None for v in p.values.values())


HOSTILE = [
    "not json", "", b"\xff\xfe", "[]", "7", "null", '"text"', "{}", '{"levels": 5}',
    '{"levels": {"a_slow": NaN}}', '{"levels": {"a_slow": Infinity}}', '{"levels": {"a_slow": -Infinity}}',
    '{"levels": {"a_slow": 1e999}}', '{"levels": {"a_slow": true}}', '{"levels": {"a_slow": [94]}}',
    "[" * 5000 + "]" * 5000, '{"a":' * 3000 + "1" + "}" * 3000,
]


@pytest.mark.parametrize("raw", HOSTILE)
def test_hostile_json_never_raises_and_never_makes_a_number(raw):
    got = parse_frame(raw, TABLE, DEFAULT_T)
    assert got is None or all(v is None for v in got.values.values())


def test_oversize_message_is_dropped_before_it_is_decoded():
    big = json.dumps({"levels": {"a_slow": 94.3}, "pad": "x" * mapping.MAX_BYTES})
    assert parse_frame(big, TABLE, DEFAULT_T) is None


def test_path_longer_than_the_limit_or_through_the_wrong_type_finds_nothing():
    deep = Mapping(version=None, metrics={"a_slow": tuple(["k"] * (mapping.MAX_DEPTH + 1))})
    assert mapping.dig({"k": {"k": 1}}, deep.metrics["a_slow"]) is mapping._MISSING
    assert mapping.dig({"levels": [1, 2]}, ("levels", 5)) is mapping._MISSING
    assert mapping.dig({"levels": [1, 2]}, ("levels", "x")) is mapping._MISSING
    assert mapping.dig({"levels": 3}, ("levels", 0)) is mapping._MISSING
    assert mapping.dig({"a": 1}, ("a",)) == 1


def test_shipped_mapping_table_is_empty_and_marked_unverified():
    """No Smaart field name is known, so none may be invented: every metric maps to nothing."""
    assert mapping.VERIFIED is False
    assert mapping.BY_MAJOR == {}
    assert mapping.DEFAULT.version is None
    assert set(mapping.DEFAULT.metrics) == set(spl.METRIC_BY_KEY) and all(p is None for p in mapping.DEFAULT.metrics.values())
    assert parse_frame(frame(a_slow=94.3)) is None   # with the real (empty) table nothing is ever read


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
    t = hub.register_entity(Entity("n1.t", "n1", "Temp", Kind.TEMPERATURE, "°C", 1))
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
    t = hub.register_entity(Entity("n1.t", "n1", "Temp", Kind.TEMPERATURE, "°C", 1))
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
    src._on_reading(SplReading(1000.0, {"a_slow": 94.3, "c_slow": 99.15, "laeq_15m": None}, "9.1"))
    assert values(hub) == {"spl.a_slow": 94.3, "spl.c_slow": 99.15, "spl.laeq_15m": None}
    assert hub.devices["spl"].status == Status.OK and "2 of 3" in hub.devices["spl"].status_detail
    src._on_reading(SplReading(1001.0, {}))
    assert all(v is None for v in values(hub).values())
    assert hub.devices["spl"].status == Status.COMPROMISED
    await integ.stop()


async def test_a_value_the_source_does_not_offer_at_all_stays_not_available(hub):
    integ, src, _ = await make(hub, slots=("z_slow", "a_slow"))
    src._on_link(True, "x")
    src._on_reading(SplReading(1000.0, {"a_slow": 90.0}))
    assert values(hub) == {"spl.z_slow": None, "spl.a_slow": 90.0}
    await integ.stop()


async def test_link_loss_is_a_gap_then_a_marker_when_readings_come_back(hub):
    integ, src, clock = await make(hub)
    src._on_link(True, "Connected")
    src._on_reading(SplReading(1000.0, {"a_slow": 90.0, "c_slow": 95.0, "laeq_15m": 92.0}))
    clock.t = 1001.0
    src._on_link(False, "Can't reach Smaart")
    assert hub.devices["spl"].status == Status.MISSING
    assert hub.alarms.to_list() and all(a["silent"] for a in hub.alarms.to_list())   # a notice, never a sound
    assert all(v is None for v in values(hub).values())
    assert hub.recorder.markers() == []    # nothing is marked while it is down, and nothing is back-filled
    clock.t = 1131.0
    src._on_link(True, "Connected")
    src._on_reading(SplReading(1131.0, {"a_slow": 91.0, "c_slow": 96.0, "laeq_15m": 93.0}))
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
        src._on_reading(SplReading(1000.0 + n * 10, {"a_slow": 90.0}))
        src._on_link(False, "down")
    src._on_link(True, "up")
    src._on_reading(SplReading(1040.0, {"a_slow": 90.0}))
    assert len([m for m in hub.recorder.markers() if m.source == "spl"]) == 1
    await integ.stop()


async def test_no_marker_on_the_first_connection_only_after_a_gap(hub):
    integ, src, _ = await make(hub)
    src._on_link(False, "Can't reach Smaart")      # never had data: nothing to mark
    src._on_link(True, "up")
    src._on_reading(SplReading(1000.0, {"a_slow": 90.0}))
    assert [m for m in hub.recorder.markers() if m.source == "spl"] == []
    await integ.stop()


async def test_connected_but_silent_is_a_gap_too(hub):
    integ, src, clock = await make(hub, stale_after_s=10.0)
    src._on_link(True, "up")
    src._on_reading(SplReading(1000.0, {"a_slow": 90.0, "c_slow": 95.0, "laeq_15m": 92.0}))
    clock.t = 1010.0
    integ.tick()
    assert values(hub)["spl.a_slow"] == 90.0 and hub.devices["spl"].status == Status.OK   # exactly at the limit: not yet
    clock.t = 1010.5
    integ.tick()
    assert all(v is None for v in values(hub).values())
    assert hub.devices["spl"].status == Status.COMPROMISED
    clock.t = 1020.0
    src._on_reading(SplReading(1020.0, {"a_slow": 91.0, "c_slow": 96.0, "laeq_15m": 93.0}))
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
    src._on_reading(SplReading(1000.0, {"a_slow": 90.0, "c_slow": 95.0, "laeq_15m": 92.0}))
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
    hub.config.spl = SplConfig(enabled=True)
    integ = SmaartIntegration(hub, source_factory=FakeSource)
    await integ.apply()
    assert integ._source is None and "spl" not in hub.devices


async def test_callbacks_after_switch_off_do_nothing(hub):
    integ, src, _ = await make(hub)
    hub.config.spl = SplConfig(enabled=False)
    await integ.apply()
    src._on_link(False, "late")
    src._on_reading(SplReading(1.0, {"a_slow": 90.0}))
    assert "spl" not in hub.devices and not [m for m in hub.recorder.markers() if m.source == "spl"]


def test_manifest_is_honest():
    assert MANIFEST.tier == "experimental" and MANIFEST.direction == "in" and MANIFEST.entity_kinds == ("sound_level",)
    text = MANIFEST.description.lower()
    assert "not tested" in text and "sends nothing" in text and "never averages" in text


# --------------------------------------------------------------------------- emulate
def test_emulated_source_offers_some_values_and_not_others_and_has_an_outage():
    src = EmulatedSplSource(lambda r: None, lambda u, d: None, first_outage_s=60, outage_every_s=300, outage_s=10)
    v = src.values(5.0)
    assert set(v) == set(OFFERED) and "z_slow" not in v and "a_peak" not in v
    assert all(spl.clean_level(x) is not None for x in v.values())
    assert [src.in_outage(t) for t in (0, 59.9, 60, 69.9, 70, 359.9, 360, 369.9, 370)] == \
        [False, False, True, True, False, False, True, True, False]


async def test_emulate_mode_runs_through_the_hub_with_a_dropout_and_comes_back(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.config.spl = SplConfig(enabled=True, slots=["a_slow", "z_slow", "laeq_15m"])
    integ = SmaartIntegration(hub, emulate=True, source_factory=lambda o: EmulatedSplSource(
        o._reading, o._link, period_s=0.01, first_outage_s=0.15, outage_every_s=1.0, outage_s=0.25))
    hub.add_integration(integ)
    try:
        await integ.start()
        assert hub.devices["spl"].name == "Sound level (simulated)"
        await until(lambda: values(hub)["spl.a_slow"] is not None)
        v = values(hub)
        assert v["spl.z_slow"] is None and v["spl.laeq_15m"] is not None      # the "not available" path
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
    assert "spl_live" not in cfg.dashboard("phone").cards
    assert seed_emulate_spl(cfg) is False
    saved = Config.model_validate(cfg.model_dump(mode="json"))
    assert seed_emulate_spl(saved) is False        # settings exist after a save: never re-enabled behind the admin's back


# ------------------------------------------------------------------ real client, fake server
class FakeSmaart:
    """A WebSocket server that plays a script of frames and records everything it receives."""

    def __init__(self, script=(), redirect_to=None):
        self.script = list(script)
        self.received: list = []
        self.connections = 0
        self.redirect_to = redirect_to
        self.release = asyncio.Event()
        self.close_after_script = False

    def process_request(self, connection, request):
        if self.redirect_to:
            resp = connection.respond(HTTPStatus.FOUND, "")
            resp.headers["Location"] = self.redirect_to
            return resp
        return None

    async def handler(self, ws):
        self.connections += 1
        recv = asyncio.create_task(self._drain(ws))
        try:
            for item in self.script:
                if isinstance(item, (int, float)):
                    await asyncio.sleep(item)
                else:
                    await ws.send(item)
            if self.close_after_script:
                await ws.close()
                return
            await self.release.wait()
        finally:
            recv.cancel()

    async def _drain(self, ws):
        try:
            async for msg in ws:
                self.received.append(msg)
        except Exception:  # noqa: BLE001
            pass

    async def __aenter__(self):
        self.server = await serve(self.handler, "127.0.0.1", 0, process_request=self.process_request, close_timeout=0.2)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.release.set()
        self.server.close()
        await self.server.wait_closed()


class Sink:
    def __init__(self):
        self.readings: list[SplReading] = []
        self.links: list[tuple[bool, str]] = []

    def reading(self, r):
        self.readings.append(r)

    def link(self, up, detail):
        self.links.append((up, detail))


def client(port, sink, **kw):
    kw.setdefault("table", TABLE)
    kw.setdefault("default", DEFAULT_T)
    kw.setdefault("backoff_min_s", 0.05)
    kw.setdefault("backoff_max_s", 0.1)
    return SmaartSource(lambda: ("127.0.0.1", port), sink.reading, sink.link, **kw)


async def test_client_delivers_values_exactly_and_never_sends_a_single_frame():
    async with FakeSmaart([frame(a_slow=94.3, c_slow=99.1), frame(a_slow=94.9)]) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: len(sink.readings) == 2)
            assert sink.readings[0].values == {"a_slow": 94.3, "c_slow": 99.1} and sink.readings[0].version == "9.1"
            assert sink.readings[1].values == {"a_slow": 94.9}
            assert src.version == "9.1" and sink.links[0][0] is True
            await asyncio.sleep(0.2)
            assert srv.received == []   # read-only: not a log-in, not a subscribe, not a command
        finally:
            await src.stop()


async def test_client_ignores_hostile_and_unknown_frames_and_keeps_going():
    script = ["not json", "[]", '{"levels": {"a_slow": NaN}}', '{"levels": {"a_slow": 1e999}}',
              json.dumps({"other": 1}), frame(a_slow=94.3)]
    async with FakeSmaart(script) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: any(r.values.get("a_slow") == 94.3 for r in sink.readings))
            assert all(r.values.get("a_slow") in (94.3, None) for r in sink.readings)
            assert srv.connections == 1
        finally:
            await src.stop()


async def test_client_drops_an_oversize_message_by_closing_and_reconnects():
    big = json.dumps({"levels": {"a_slow": 99.0}, "pad": "x" * (mapping.MAX_BYTES + 100)})
    async with FakeSmaart([big]) as srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: srv.connections >= 2)
            assert sink.readings == []
            assert (False, TEXT["too_large"]) in sink.links
        finally:
            await src.stop()


async def test_client_reports_a_closed_connection_then_reconnects_and_delivers_again():
    srv = FakeSmaart([frame(a_slow=90.0)])
    srv.close_after_script = True
    async with srv:
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: srv.connections >= 2 and len(sink.readings) >= 2)
            ups = [u for u, _ in sink.links]
            assert ups[:3] == [True, False, True]       # up, gap, up again
            assert (False, TEXT["closed"]) in sink.links
        finally:
            await src.stop()


async def test_client_caps_how_many_messages_it_looks_at_each_second():
    async with FakeSmaart([frame(a_slow=90.0 + i / 10) for i in range(40)]) as srv:
        sink = Sink()
        src = client(srv.port, sink, max_frames_per_s=5)
        await src.start()
        try:
            await until(lambda: src.dropped_frames >= 30 or len(sink.readings) >= 5)
            await asyncio.sleep(0.2)
            assert len(sink.readings) == 5 and src.dropped_frames == 35
        finally:
            await src.stop()


async def test_client_follows_no_redirect():
    async with FakeSmaart([frame(a_slow=90.0)]) as target, FakeSmaart(redirect_to="PLACEHOLDER") as srv:
        srv.redirect_to = f"ws://127.0.0.1:{target.port}/"
        sink = Sink()
        src = client(srv.port, sink)
        await src.start()
        try:
            await until(lambda: any(not up for up, _ in sink.links))
            await asyncio.sleep(0.2)
            assert target.connections == 0 and sink.readings == []
        finally:
            await src.stop()


async def test_client_with_the_shipped_empty_table_connects_but_reads_nothing_and_says_why():
    async with FakeSmaart([frame(a_slow=94.3)]) as srv:
        sink = Sink()
        src = SmaartSource(lambda: ("127.0.0.1", srv.port), sink.reading, sink.link, backoff_min_s=0.05, backoff_max_s=0.1)
        await src.start()
        try:
            await until(lambda: sink.links and sink.links[0][0] is True)
            await asyncio.sleep(0.2)
            assert sink.readings == [] and src.verified is False
            assert sink.links[0][1] == TEXT["connected_unverified"]
        finally:
            await src.stop()


async def test_client_with_no_address_waits_and_says_so_and_unreachable_is_plain_text():
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
    assert all("127.0.0.1" not in d and str(port) not in d for _, d in sink.links)   # no address in crew text


async def test_client_through_the_integration_updates_the_hub_and_never_blocks_it(hub):
    async with FakeSmaart([frame(a_slow=94.3, c_slow=99.1, leq=[96.0])]) as srv:
        hub.config.spl = SplConfig(enabled=True, host="127.0.0.1", port=srv.port, slots=["a_slow", "c_slow", "laeq_15m"])
        integ = SmaartIntegration(hub, source_factory=lambda o: client(srv.port, o_sink(o)))
        hub.add_integration(integ)
        await integ.start()
        try:
            await until(lambda: values(hub).get("spl.laeq_15m") == 96.0)
            assert values(hub) == {"spl.a_slow": 94.3, "spl.c_slow": 99.1, "spl.laeq_15m": 96.0}
            assert hub.devices["spl"].status == Status.OK
        finally:
            await integ.stop()


def o_sink(owner):
    s = Sink()
    s.reading, s.link = owner._reading, owner._link
    return s


def test_websocket_address_is_built_plainly():
    assert ws_url("192.0.2.5", 26000) == "ws://192.0.2.5:26000/"
    assert ws_url("::1", 26000) == "ws://[::1]:26000/"
    assert ws_url("smaart-laptop", 1) == "ws://smaart-laptop:1/"


def test_the_client_source_never_calls_send():
    """A belt-and-braces check on the code itself: nothing in the client or emulator sends."""
    for name in ("client.py", "emulate.py", "__init__.py", "mapping.py", "source.py"):
        src = (ROOT / "src" / "stagewatch" / "integrations" / "smaart" / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith(("#", '"', "*", "-")))
        assert ".send(" not in code and ".send_text(" not in code and ".write(" not in code, name


# ------------------------------------------------------------------------------- config
def test_older_config_without_the_section_loads_with_defaults_and_no_schema_bump():
    assert CONFIG_SCHEMA_VERSION == 2 and DB_SCHEMA_VERSION == 3      # additive config, no table change: no bump
    old = yaml.safe_load((ROOT / "tests" / "fixtures" / "v1" / "config.yaml").read_text(encoding="utf-8"))
    cfg = Config.model_validate(migrate(old))
    assert cfg.spl == SplConfig() and cfg.spl.enabled is False and cfg.spl.host == "" and cfg.spl.port is None
    assert cfg.spl.slots == ["a_slow", "c_slow", "laeq_15m"]
    assert cfg.site.name == old["site"]["name"]


def test_saved_settings_round_trip_through_a_save_and_a_load(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.spl = SplConfig(enabled=True, host="Smaart-Laptop.local", port=26000, slots=["c_fast", "a_peak"])
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load()
    assert again.spl.model_dump() == {"enabled": True, "host": "smaart-laptop.local", "port": 26000,
                                      "slots": ["c_fast", "a_peak"]}


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
    assert cfg.spl.host == "" and cfg.spl.port is None and cfg.spl.slots == list(spl.DEFAULT_SLOTS) and any("spl" in n for n in notes)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    loaded = ConfigStore(p).load()
    assert loaded.site.name == "Keep Me" and loaded.spl.host == "" and loaded.spl.port is None
    assert "http://x" not in caplog.text


def test_a_newer_release_adding_keys_to_the_section_is_ignored():
    assert SplConfig.model_validate({"enabled": True, "future": "x"}).enabled is True


# ---------------------------------------------------------------------- cards and API
def test_the_card_is_known_but_not_on_by_default():
    assert "spl_live" in cards.KNOWN_CARDS
    assert all("spl_live" not in cards.default_cards(layout) for layout in cards.LAYOUT_DEFAULTS)
    assert cards.strict_cards_error(["env_tiles", "spl_live"]) is None


@pytest.fixture
def client_app(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    integ = SmaartIntegration(hub, emulate=True, source_factory=lambda o: EmulatedSplSource(
        o._reading, o._link, period_s=0.02, first_outage_s=1000))
    hub.add_integration(integ)
    app = create_app(hub, lan_addresses=lambda: ["192.0.2.10"])
    with TestClient(app) as c:
        c.hub = hub
        yield c


def admin(c):
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200


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
])
def test_put_spl_refuses_bad_input_with_fixed_text_and_changes_nothing(client_app, body):
    c = client_app
    admin(c)
    before = c.hub.config.spl.model_dump()
    r = put(c, **body)
    assert r.status_code == 422
    for echoed in ("8.8.8.8", "http://x", "nope"):
        assert echoed not in r.text
    assert c.hub.config.spl.model_dump() == before


def test_put_spl_in_real_mode_needs_an_address_and_port(tmp_path):
    hub = Hub(tmp_path)
    hub.add_integration(SmaartIntegration(hub, source_factory=FakeSource))
    with TestClient(create_app(hub, lan_addresses=lambda: ["192.0.2.10"])) as c:
        admin(c)
        assert put(c).status_code == 422 and put(c, host="192.168.1.5").status_code == 422
        assert put(c, enabled=False).status_code == 200
        assert put(c, host="192.168.1.5", port=26000).status_code == 200
        assert hub.integrations["smaart"]._source is not None


def test_put_spl_applies_at_once_and_the_snapshot_has_the_public_shape_only(client_app):
    c = client_app
    admin(c)
    r = put(c, slots=["c_slow", "a_fast"])
    assert r.status_code == 200 and r.json() == {"ok": True, "changed": True}
    snap = c.get("/api/snapshot").json()
    ents = [e for e in snap["entities"] if e["kind"] == "sound_level"]
    assert [e["id"] for e in ents] == ["spl.c_slow", "spl.a_fast"]
    assert ents[0]["labels"] == {"weighting": "C", "metric": "SPL", "slot": "1", "time_constant": "Slow"}
    dev = next(d for d in snap["devices"] if d["id"] == "spl")
    assert set(dev) == {"id", "name", "integration", "category", "manufacturer", "model", "area", "status", "status_detail"}
    assert dev["category"] == "service"


def test_the_address_never_reaches_public_endpoints_but_the_admin_sees_it(client_app):
    c = client_app
    admin(c)
    assert put(c, host="192.168.50.77", port=26000).status_code == 200
    public = "".join(c.get(u).text for u in ("/api/snapshot", "/api/info", "/api/dashboard/foh"))
    assert "192.168.50.77" not in public and "26000" not in public
    st = c.get("/api/admin/state").json()
    assert st["config"]["spl"]["host"] == "192.168.50.77"
    assert [m["key"] for m in st["spl"]["metrics"]] == [m.key for m in spl.METRICS] and st["spl"]["max_slots"] == 3
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
    from stagewatch.integrations.smaart import client as cl

    async def getaddrinfo(self, host, port, **kw):
        return [(0, 0, 0, "", ("8.8.8.8", port))]
    monkeypatch.setattr(asyncio.get_running_loop().__class__, "getaddrinfo", getaddrinfo)
    sink = Sink()
    src = SmaartSource(lambda: ("smaart.example", 1), sink.reading, sink.link, backoff_min_s=0.05, backoff_max_s=0.1)
    await src.start()
    await until(lambda: (False, TEXT["not_local"]) in sink.links)
    await src.stop()
    assert sink.readings == []


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


async def _waits(script, n, **kw):
    waits = []
    real = asyncio.sleep

    async def spy(d):
        waits.append(d)
        await real(0.01)
    srv = FakeSmaart(script)
    srv.close_after_script = True
    async with srv:
        src = client(srv.port, Sink(), backoff_min_s=0.05, backoff_max_s=0.4, sleep=spy, **kw)
        await src.start()
        try:
            await until(lambda: len(waits) >= n)
        finally:
            await src.stop()
    return waits[:n]


async def test_backoff_grows_when_the_link_drops_quickly_even_after_readings():
    assert await _waits([frame(a_slow=90.0)], 4, stable_s=30.0) == [0.1, 0.2, 0.4, 0.4]


async def test_backoff_starts_over_after_a_stable_link():
    assert await _waits([0.15, frame(a_slow=90.0)], 2, stable_s=0.1) == [0.05, 0.05]


async def test_a_failing_callback_does_not_end_the_simulated_source():
    calls = []

    def bad(*a):
        calls.append(a)
        raise RuntimeError("boom")
    src = EmulatedSplSource(bad, bad, period_s=0.01, first_outage_s=1000)
    await src.start()
    await until(lambda: len(calls) >= 4)
    await src.stop()
