"""FOH advice (AD2): the config models, Recorder.value_near, the reference marker lookup and the
AdviceService numbers. Expected numbers come from plans/ad0-physics-signoff.md (an independent
implementation of ISO 9613-1 and the Cramer speed of sound), not from the code under test."""

import time

import pytest
from pydantic import ValidationError

from stagewatch.core import advice as adv
from stagewatch.core.config import (AdviceConfig, AdvicePath, AdvicePosition, Config, ConfigStore,
                                    salvage)
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Marker, Status
from stagewatch.core.recorder import Recorder

SECRETISH = "do-not-echo-this-text"


# ------------------------------------------------------------------ helpers
def make_hub(tmp_path, **kw):
    hub = Hub(tmp_path, **kw)
    hub.config.site.smoothing_tau_s = 0   # site values follow the sensors exactly
    return hub


def add_env(hub, dev="env", area="", kinds=(Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE)):
    hub.register_device(Device(dev, dev, "test", area=area, status=Status.OK))
    for kind in kinds:
        hub.register_entity(Entity(f"{dev}.{kind.value}", dev, kind.value, kind))


def air(hub, ts, t=None, rh=None, p=None, dev="env", site=True):
    for kind, v in ((Kind.TEMPERATURE, t), (Kind.HUMIDITY, rh), (Kind.PRESSURE, p)):
        if v is not None:
            hub.update_state(f"{dev}.{kind.value}", v, ts)
    if site:
        hub.compute_site(ts)


def configure(hub, paths=(), positions=(), **kw):
    hub.config.advice = AdviceConfig(paths=list(paths), positions=list(positions), **kw)
    hub.bus.publish("config", None)   # what save_config tells everyone


def path(pid="tower", distance=60, **kw):
    return AdvicePath(id=pid, name=kw.pop("name", pid), distance_m=distance, **kw)


def positions():
    return [AdvicePosition(id="foh", name="FOH", distance_m=40),
            AdvicePosition(id="rear", name="Rear", distance_m=90)]


def band(pos, label):
    return next(b for b in pos["bands"] if b["band"] == label)


# ------------------------------------------------------------------ config
def test_an_old_config_loads_with_no_advice():
    cfg = Config.model_validate({"schema_version": 3, "site": {"name": "Old"}})
    assert cfg.advice == AdviceConfig()
    assert cfg.advice.paths == [] and cfg.advice.positions == []
    assert (cfg.advice.drift_reference, cfg.advice.air_loss_reference) == ("Aligned", "Soundcheck")
    assert (cfg.advice.air_loss_resolution, cfg.advice.alarm, cfg.advice.hold_s) == ("octave", "silent", 120)
    assert cfg.advice.marker_on_alert is True


def test_the_config_round_trips_through_the_file(tmp_path):
    hub = Hub(tmp_path)
    hub.config.advice = AdviceConfig(
        paths=[path("tower_l", 85, name=" Delay tower L ", source_area="Delay tower 1",
                    current_delay_ms=240.5, drift_threshold_ms=None, phase_hint_hz=80)],
        positions=positions(), air_loss_resolution="third", alarm="beep", hold_s=30,
        drift_reference="Aligned", air_loss_reference="Line check")
    hub.save_config()
    back = ConfigStore(tmp_path / "config.yaml").load().advice
    assert back == hub.config.advice
    assert back.paths[0].name == "Delay tower L"            # trimmed
    assert back.paths[0].drift_threshold_ms is None and back.air_loss_resolution == "third"


def test_the_id_defaults_to_the_slug_of_the_name():
    assert AdvicePath.model_validate({"name": "Delay Tower L", "distance_m": 10}).id == "delay_tower_l"
    assert AdvicePosition.model_validate({"name": "Rear", "distance_m": 90}).id == "rear"
    assert AdvicePath.model_validate({"id": "x", "name": "Delay Tower L", "distance_m": 10}).id == "x"


@pytest.mark.parametrize("model, bad", [
    (AdvicePath, {"distance_m": 0}), (AdvicePath, {"distance_m": -3}), (AdvicePath, {"distance_m": 0.49}),
    (AdvicePath, {"distance_m": 1000.1}), (AdvicePath, {"distance_m": float("nan")}),
    (AdvicePath, {"distance_m": float("inf")}), (AdvicePath, {"name": "   "}), (AdvicePath, {"name": "x" * 41}),
    (AdvicePath, {"id": "Has Space"}), (AdvicePath, {"id": "x" * 33}), (AdvicePath, {"id": ""}),
    (AdvicePath, {"source_area": "x" * 41}), (AdvicePath, {"current_delay_ms": -1}),
    (AdvicePath, {"current_delay_ms": 2000.1}), (AdvicePath, {"drift_threshold_ms": 0.19}),
    (AdvicePath, {"drift_threshold_ms": 10.1}), (AdvicePath, {"phase_hint_hz": 19}),
    (AdvicePath, {"phase_hint_hz": 20001}),
    (AdvicePosition, {"distance_m": 0}), (AdvicePosition, {"distance_m": 1001}), (AdvicePosition, {"name": ""}),
])
def test_a_path_or_position_outside_its_bounds_is_refused(model, bad):
    good = {"id": "p", "name": "P", "distance_m": 10}
    model.model_validate(good)
    with pytest.raises(ValidationError):
        model.model_validate({**good, **bad})


def test_the_edges_of_the_ranges_are_allowed():
    for d in (0.5, 1000):
        assert AdvicePath(id="p", name="P", distance_m=d).distance_m == d
        assert AdvicePosition(id="p", name="P", distance_m=d).distance_m == d
    for th in (0.2, 10):
        assert AdvicePath(id="p", name="P", distance_m=5, drift_threshold_ms=th).drift_threshold_ms == th


def test_advice_settings_are_checked_and_never_echoed():
    P = lambda i: {"id": f"p{i}", "name": f"P{i}", "distance_m": 5}   # noqa: E731
    AdviceConfig.model_validate({"paths": [P(i) for i in range(12)], "positions": [P(i) for i in range(4)]})
    for bad in ({"paths": [P(i) for i in range(13)]}, {"positions": [P(i) for i in range(5)]},
                {"paths": [P(1), P(1)]}, {"positions": [P(2), P(2)]},
                {"air_loss_resolution": SECRETISH}, {"alarm": SECRETISH}, {"hold_s": 901}, {"hold_s": -1},
                {"drift_reference": ""}, {"air_loss_reference": "  "}, {"drift_reference": "x" * 121}):
        with pytest.raises(ValidationError) as e:
            AdviceConfig.model_validate(bad)
        assert SECRETISH not in str(e.value)
    assert [b for b in ("octave", "third")] == [AdviceConfig(air_loss_resolution=b).air_loss_resolution
                                                 for b in ("octave", "third")]
    assert not hasattr(AdvicePath(id="p", name="P", distance_m=5), "air_loss")   # no per-path air loss


def test_loading_drops_unusable_entries_and_keeps_the_rest(caplog):
    raw = {"advice": {"paths": [
        {"id": "ok", "name": "OK", "distance_m": 20},
        {"id": "ok", "name": "Same id", "distance_m": 21},
        {"id": "zero", "name": "Zero", "distance_m": 0},
        "junk", {"name": SECRETISH, "distance_m": "far"}],
        "positions": [{"id": "foh", "name": "FOH", "distance_m": 40}, {"id": "bad", "name": "", "distance_m": 5}],
        "alarm": "beep"}}
    cfg = Config.model_validate(raw)
    assert [p.id for p in cfg.advice.paths] == ["ok"] and cfg.advice.paths[0].distance_m == 20
    assert [p.id for p in cfg.advice.positions] == ["foh"] and cfg.advice.alarm == "beep"
    assert SECRETISH not in caplog.text


def test_a_damaged_advice_setting_resets_only_itself():
    raw = {"site": {"name": "Keep me"}, "advice": {
        "paths": [{"id": "t", "name": "T", "distance_m": 30}], "alarm": "loud", "hold_s": 60}}
    with pytest.raises(ValidationError):
        Config.model_validate(raw)
    cfg, notes = salvage(raw)
    assert cfg.site.name == "Keep me"
    assert [p.id for p in cfg.advice.paths] == ["t"] and cfg.advice.hold_s == 60
    assert cfg.advice.alarm == "silent"            # only the damaged field fell back
    assert any("advice" in n for n in notes)


# ------------------------------------------------------------------ Recorder.value_near
@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "t.sqlite3")
    yield r
    r.close()


def test_value_near_takes_the_last_reading_at_or_before(rec):
    rec.record_state("e", 1.0, 1000.0)
    rec.record_state("e", 2.0, 1100.0)
    rec.record_state("e", 3.0, 1200.0)
    assert rec.value_near("e", 1150.0) == 2.0
    assert rec.value_near("e", 1100.0) == 2.0           # exactly at: counts
    assert rec.value_near("e", 5000.0, before_s=3800) == 3.0


def test_value_near_windows_and_the_reading_just_after(rec):
    rec.record_state("e", 10.0, 1000.0)
    rec.record_state("e", 11.0, 1100.0)
    assert rec.value_near("e", 1100.0 + 600.0) == 11.0       # exactly 600 s older: still inside
    assert rec.value_near("e", 1100.0 + 600.1) is None       # one tick older than the window
    assert rec.value_near("e", 900.0) == 10.0                # nothing before: the first one after, in 120 s
    assert rec.value_near("e", 880.0) == 10.0                # exactly 120 s before the first reading
    assert rec.value_near("e", 879.9) is None                # 120.1 s: too far
    assert rec.value_near("e", 900.0, after_s=50) is None
    assert rec.value_near("e", 900.0, after_s=0) is None
    assert rec.value_near("nope", 1000.0) is None

def test_value_near_ignores_not_available_records_and_other_shows(rec):
    rec.record_state("e", 5.0, 1000.0)
    rec.record_state("e", None, 1050.0)                 # sensor dropped out: not a number
    assert rec.value_near("e", 1060.0) == 5.0
    first = rec.show_id
    rec.start_show("Day 2")
    assert rec.show_id != first
    assert rec.value_near("e", 1060.0) is None           # the current show has none
    assert rec.value_near("e", 1060.0, show_id=first) == 5.0
    assert rec.value_near("e", 1060.0, first) == 5.0


def test_value_at_still_means_before_only(rec):
    rec.record_state("e", 7.0, 1000.0)
    assert rec.value_at("e", 1100.0) == 7.0
    assert rec.value_at("e", 900.0) is None              # a later reading is never used by value_at
    assert rec.value_at("e", 1600.0) == 7.0 and rec.value_at("e", 1600.1) is None


# ------------------------------------------------------------------ reference marker
def M(i, ts, label):
    return Marker(i, ts, label)


def test_the_reference_marker_matches_a_prefix_ignoring_case_and_spaces():
    ms = [M(1, 100, "aligned"), M(2, 200, "Aligned (delays re-timed)"), M(3, 300, "Realigned"), M(4, 150, "Soundcheck")]
    assert adv.find_reference_marker(ms, "Aligned", 1000).id == 2          # newest wins
    assert adv.find_reference_marker(ms, "  ALIGNED ", 1000).id == 2
    assert adv.find_reference_marker(ms, "Align", 1000).id == 2            # prefix, not the middle ("Realigned")
    assert adv.find_reference_marker(ms, "aligned", 150).id == 1
    assert adv.find_reference_marker([M(1, 100, "  Aligned")], "aligned", 1000).id == 1
    assert adv.find_reference_marker(ms, "Line check", 1000) is None
    assert adv.find_reference_marker(ms, "", 1000) is None and adv.find_reference_marker([], "Aligned", 1000) is None


def test_a_marker_in_the_future_is_not_a_reference_yet():
    assert adv.find_reference_marker([M(1, 500, "Aligned")], "Aligned", 499.9) is None
    assert adv.find_reference_marker([M(1, 500, "Aligned")], "Aligned", 500).id == 1


def test_two_markers_at_the_same_time_pick_the_later_one():
    assert adv.find_reference_marker([M(7, 100, "Aligned"), M(9, 100, "Aligned 2")], "Aligned", 200).id == 9


# ------------------------------------------------------------------ the service: reference handling
def test_nothing_is_shown_without_a_reference_marker(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 5, 20, 50, 101325)
    configure(hub, [path()], positions())
    snap = hub.advice.compute(now)
    assert snap["drift_ref"] is None and snap["air_ref"] is None
    p = snap["paths"][0]
    assert p["drift_ms"] is None and p["suggested_delay_ms"] is None and p["level"] == "ok"
    pos = snap["positions"][0]
    assert all(b["change_db"] is None for b in pos["bands"])
    assert band(pos, "8k")["now_db"] is not None            # the loss now needs no marker


def test_only_todays_show_is_searched_and_there_is_no_earlier_day_fallback(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 20)
    configure(hub, [path()])
    assert hub.advice.compute(now)["drift_ref"]["label"] == "Aligned"
    hub.start_show("Day 2")                                  # publishes "show"
    assert hub.advice.compute(now)["drift_ref"] is None      # yesterday's marker is not used
    assert hub.advice.compute(now)["paths"][0]["drift_ms"] is None


def test_deleting_a_marker_goes_back_to_the_previous_match(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 40, 20, 50, 101325)
    first = hub.add_marker("Aligned", "test", now - 30)
    air(hub, now - 20, 22, 50, 101325)
    second = hub.add_marker("Aligned again", "test", now - 10)
    configure(hub, [path()])
    assert hub.advice.compute(now)["drift_ref"]["marker_id"] == second.id
    hub.delete_marker(second.id)
    assert hub.advice.compute(now)["drift_ref"]["marker_id"] == first.id
    hub.delete_marker(first.id)
    assert hub.advice.compute(now)["drift_ref"] is None


def test_a_new_matching_marker_rebases_at_once(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 6, 26, 50, 101325)
    configure(hub, [path()])
    assert hub.advice.compute(now)["paths"][0]["drift_ms"] == pytest.approx(-1.892, abs=0.001)
    hub.add_marker("Aligned", "test", now - 2)               # "I have re-aligned"
    assert hub.advice.compute(now)["paths"][0]["drift_ms"] == pytest.approx(0.0, abs=1e-9)


def test_changing_the_settings_clears_what_was_remembered(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    hub.add_marker("Line check", "test", now - 24)
    configure(hub, [path()])
    assert hub.advice.compute(now)["drift_ref"]["label"] == "Aligned"
    configure(hub, [path()], drift_reference="Line check")
    assert hub.advice.compute(now)["drift_ref"]["label"] == "Line check"


def test_a_marker_with_no_readings_says_so_and_shows_no_numbers(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    hub.add_marker("Aligned", "test", now - 2000)            # long before any reading
    air(hub, now - 5, 20, 50, 101325)
    configure(hub, [path()], positions())
    snap = hub.advice.compute(now)
    assert snap["drift_ref"]["has_readings"] is False
    assert snap["drift_ref"]["delta"] == {"t_c": None, "rh": None, "p_pa": None}
    assert snap["paths"][0]["drift_ms"] is None
    assert all(b["change_db"] is None for b in snap["positions"][0]["bands"])


def test_a_reading_just_after_the_marker_stands_in(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    hub.add_marker("Aligned", "test", now - 30)
    air(hub, now - 20, 20, 50, 101325)                       # first reading, 10 s after the marker
    air(hub, now - 2, 26, 50, 101325)
    configure(hub, [path()])
    assert hub.advice.compute(now)["paths"][0]["drift_ms"] == pytest.approx(-1.892, abs=0.001)


def test_a_marker_without_readings_is_looked_for_again_later(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    configure(hub, [path()])
    assert hub.advice.compute(now)["drift_ref"] is None
    air(hub, now - 3, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 2)
    assert hub.advice.compute(now)["drift_ref"]["marker_id"] > 0     # the marker bus topic cleared the memory


# ------------------------------------------------------------------ the service: drift numbers
def test_drift_over_60_m_for_20_to_26_degrees(tmp_path):
    """AD0: exact speed_of_sound at 50 % RH, 101325 Pa gives -1.892 ms over 60 m (sound arrives earlier)."""
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 26, 50, 101325)
    configure(hub, [path(distance=60, current_delay_ms=170.0, drift_threshold_ms=1.0, phase_hint_hz=100)])
    snap = hub.advice.compute(now)
    p = snap["paths"][0]
    assert p["drift_ms"] == pytest.approx(-1.892, abs=0.001)
    assert p["suggested_delay_ms"] == pytest.approx(170.0 - 1.892, abs=0.001)
    assert p["level"] == "advisory" and p["flags"] == [] and p["stale"] is False
    assert (p["source"], p["source_fallback"]) == ("site", [])
    assert p["phase_deg"] == pytest.approx(-1.892 * 100 * 360 / 1000, abs=0.05)    # -68.1 degrees
    assert snap["drift_ref"]["delta"] == {"t_c": pytest.approx(6.0), "rh": pytest.approx(0.0, abs=1e-9),
                                          "p_pa": pytest.approx(0.0, abs=1e-9)}
    assert snap["drift_ref"]["label"] == "Aligned"


def test_drift_is_linear_in_distance_and_below_its_threshold_is_ok(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 26, 50, 101325)
    configure(hub, [path("a", 30, drift_threshold_ms=1.0), path("b", 60, drift_threshold_ms=1.0),
                    path("c", 120, drift_threshold_ms=None)])
    a, b, c = hub.advice.compute(now)["paths"]
    assert b["drift_ms"] == pytest.approx(2 * a["drift_ms"]) and c["drift_ms"] == pytest.approx(4 * a["drift_ms"])
    assert (a["level"], b["level"], c["level"]) == ("ok", "advisory", "ok")      # 0.95, 1.89, no threshold
    assert a["suggested_delay_ms"] is None and a["current_delay_ms"] is None


def test_missing_humidity_and_pressure_are_assumed_and_flagged(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub, kinds=(Kind.TEMPERATURE,))
    now = time.time()
    air(hub, now - 30, 20)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 26)
    configure(hub, [path(distance=60)])
    p = hub.advice.compute(now)["paths"][0]
    assert p["flags"] == ["humidity_assumed", "pressure_altitude"]
    assert p["drift_ms"] == pytest.approx(-1.892, abs=0.003)       # 50 % and sea level: the AD0 case
    hub.config.site.altitude_m = 1000
    hub.bus.publish("config", None)
    assert hub.advice.compute(now)["paths"][0]["drift_ms"] == pytest.approx(p["drift_ms"], abs=0.02)


def test_outside_the_speed_of_sound_range_is_flagged_but_still_worked_out(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 35, 50, 101325)
    configure(hub, [path()])
    p = hub.advice.compute(now)["paths"][0]
    assert p["flags"] == ["out_of_range"] and p["drift_ms"] < -3


def test_stale_readings_are_flagged_and_frozen_not_dropped(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 400, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 390)
    air(hub, now - 100, 26, 50, 101325)                  # the last reading, older than the 60 s limit
    configure(hub, [path()], positions())
    snap = hub.advice.compute(now)
    assert snap["paths"][0]["stale"] is True and snap["positions"][0]["stale"] is True
    assert snap["paths"][0]["drift_ms"] == pytest.approx(-1.892, abs=0.001)


def test_no_temperature_now_means_no_numbers(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    hub.entities["site.temperature"].value = None        # every temperature sensor gone
    configure(hub, [path()], positions())
    snap = hub.advice.compute(now)
    assert snap["paths"][0]["drift_ms"] is None
    assert snap["positions"][0]["flags"] == ["no_temperature"]
    assert all(b["now_db"] is None for b in snap["positions"][0]["bands"])


def test_a_disabled_path_or_position_is_left_out(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    configure(hub, [path("a"), path("b", enabled=False)],
              [AdvicePosition(id="foh", name="FOH", distance_m=40, enabled=False)])
    snap = hub.advice.compute(time.time())
    assert [p["id"] for p in snap["paths"]] == ["a"] and snap["positions"] == []
    configure(hub, [path("a", enabled=False)])
    assert hub.advice.compute(time.time()) == adv.empty_advice()


# ------------------------------------------------------------------ the service: area source
def test_an_area_source_is_used_per_quantity_with_the_site_as_fallback(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub, "env", "")                                              # the room: 22 degrees, all three
    add_env(hub, "tower", "Delay Tower 1", kinds=(Kind.TEMPERATURE, Kind.HUMIDITY))   # no pressure sensor
    now = time.time()
    air(hub, now - 30, 22, 50, 101325, "env", site=False)
    air(hub, now - 30, 20, 50, dev="tower")
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 22, 50, 101325, "env", site=False)
    air(hub, now - 3, 26, 50, dev="tower")
    configure(hub, [path("area", 60, source_area="delay tower 1"), path("site", 60)])
    area, site = hub.advice.compute(now)["paths"]
    assert area["drift_ms"] == pytest.approx(-1.892, abs=0.001)          # the tower's own 20 -> 26
    assert (area["source"], area["source_fallback"]) == ("area", ["p"])  # pressure came from the site average
    assert site["drift_ms"] == pytest.approx(-0.95, abs=0.05)            # site mean 21 -> 24: half as much
    assert (site["source"], site["source_fallback"]) == ("site", [])


def test_an_area_with_no_sensors_uses_the_site_average_and_says_so(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 26, 50, 101325)
    configure(hub, [path(source_area="Nowhere")])
    p = hub.advice.compute(now)["paths"][0]
    assert p["drift_ms"] == pytest.approx(-1.892, abs=0.001)
    assert (p["source"], p["source_fallback"]) == ("site", ["t", "rh", "p"])


def test_equipment_sensors_never_feed_an_area(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    add_env(hub, "rack", "FOH", kinds=(Kind.TEMPERATURE,))
    hub.set_node_role("rack", "equipment")
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    air(hub, now - 30, 40, dev="rack", site=False)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 26, 50, 101325)
    air(hub, now - 3, 60, dev="rack", site=False)
    configure(hub, [path(source_area="FOH")])
    p = hub.advice.compute(now)["paths"][0]
    assert p["drift_ms"] == pytest.approx(-1.892, abs=0.001) and p["source_fallback"] == ["t", "rh", "p"]


# ------------------------------------------------------------------ the service: air loss (AD0 values)
DAMP_EVENING = ((25, 40.0), (15, 74.32))      # same moisture: 25 C / 40 % to 15 C / 74.32 %
DRYING_OUT = ((20, 70.0), (20, 40.0))


def air_loss_snapshot(tmp_path, before, after, **kw):
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, before[0], before[1], 101325)
    hub.add_marker("Soundcheck", "test", now - 25)
    air(hub, now - 3, after[0], after[1], 101325)
    configure(hub, positions=positions(), **kw)
    return hub, hub.advice.compute(now)


@pytest.mark.parametrize("label, foh, rear", [
    ("4k", (1.004, -0.200), (2.259, -0.449)),
    ("8k", (3.545, -0.635), (7.977, -1.428)),
    ("16k", (12.480, -2.139), (28.081, -4.813)),
])
def test_air_loss_damp_evening_matches_the_ad0_table(tmp_path, label, foh, rear):
    _hub, snap = air_loss_snapshot(tmp_path, *DAMP_EVENING)
    f, r = snap["positions"]
    assert (f["id"], r["id"]) == ("foh", "rear")
    for pos, (loss, change) in ((f, foh), (r, rear)):
        b = band(pos, label)
        assert b["now_db"] == pytest.approx(loss, abs=0.002)
        assert b["change_db"] == pytest.approx(change, abs=0.002)      # negative: less top end lost
    assert f["flags"] == [] and f["stale"] is False


@pytest.mark.parametrize("label, foh, rear", [
    ("4k", (1.445, 0.528), (3.250, 1.188)),
    ("8k", (5.134, 2.070), (11.552, 4.657)),
    ("16k", (16.542, 5.493), (37.220, 12.359)),
])
def test_air_loss_drying_out_matches_the_ad0_table(tmp_path, label, foh, rear):
    """Marker 20 C / 70 %, now 20 C / 40 %: the loss figures are for NOW, so they are the 40 % ones."""
    _hub, snap = air_loss_snapshot(tmp_path, *DRYING_OUT)
    for pos, (_before, change) in zip(snap["positions"], (foh, rear)):
        assert band(pos, label)["change_db"] == pytest.approx(change, abs=0.002)
    f = band(snap["positions"][0], label)
    alpha_now = {"4k": 0.036114, "8k": 0.128361, "16k": 0.413559}[label]
    assert f["now_db"] == pytest.approx(alpha_now * 40, abs=0.002)


def test_air_loss_uses_octave_or_third_octave_bands_above_4k(tmp_path):
    _h, snap = air_loss_snapshot(tmp_path, *DAMP_EVENING)
    assert snap["resolution"] == "octave"
    assert [b["band"] for b in snap["positions"][0]["bands"]] == ["4k", "8k", "16k"]
    _h, snap = air_loss_snapshot(tmp_path / "third", *DAMP_EVENING, air_loss_resolution="third")
    bands = snap["positions"][0]["bands"]
    assert [b["band"] for b in bands] == ["4k", "5k", "6.3k", "8k", "10k", "12.5k", "16k"]
    assert [b["hz_used"] for b in bands] == [3981.07, 5011.87, 6309.57, 7943.28, 10000.0, 12589.25, 15848.93]
    losses = [b["now_db"] for b in bands]
    assert losses == sorted(losses)                               # more loss the higher the band
    assert band(snap["positions"][0], "8k")["change_db"] == pytest.approx(-0.635, abs=0.002)


def test_positions_are_not_compared_with_each_other_and_have_one_marker(tmp_path):
    _h, snap = air_loss_snapshot(tmp_path, *DAMP_EVENING)
    f, r = snap["positions"]
    assert set(f) == {"id", "name", "distance_m", "bands", "flags", "stale"}
    assert band(r, "8k")["change_db"] / band(f, "8k")["change_db"] == pytest.approx(90 / 40)   # same air, longer throw
    assert snap["air_ref"]["label"] == "Soundcheck" and snap["air_ref"]["delta"]["t_c"] == pytest.approx(-10.0)
    assert snap["air_ref"]["delta"]["rh"] == pytest.approx(34.32)


def test_air_loss_needs_a_humidity_reading_and_never_guesses_one(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub, kinds=(Kind.TEMPERATURE, Kind.PRESSURE))
    now = time.time()
    air(hub, now - 30, 20, p=101325)
    hub.add_marker("Soundcheck", "test", now - 25)
    air(hub, now - 3, 15, p=101325)
    configure(hub, positions=positions())
    for pos in hub.advice.compute(now)["positions"]:
        assert pos["flags"] == ["needs_humidity"]
        assert all(b["now_db"] is None and b["change_db"] is None for b in pos["bands"])


def test_air_loss_without_a_pressure_sensor_uses_the_altitude_and_says_so(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub, kinds=(Kind.TEMPERATURE, Kind.HUMIDITY))
    now = time.time()
    air(hub, now - 30, 25, 40)
    hub.add_marker("Soundcheck", "test", now - 25)
    air(hub, now - 3, 15, 74.32)
    configure(hub, positions=positions())
    pos = hub.advice.compute(now)["positions"][0]
    assert pos["flags"] == ["pressure_altitude"]
    assert band(pos, "8k")["change_db"] == pytest.approx(-0.635, abs=0.002)    # sea level: the AD0 case


def test_a_marker_without_humidity_gives_the_loss_but_no_change(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub, kinds=(Kind.TEMPERATURE, Kind.PRESSURE))
    now = time.time()
    air(hub, now - 300, 20, p=101325)
    hub.add_marker("Soundcheck", "test", now - 295)
    add_env(hub, "humid", kinds=(Kind.HUMIDITY,))                  # a humidity sensor arrives much later
    air(hub, now - 3, 20, 50, 101325)
    air(hub, now - 3, rh=50, dev="humid")
    configure(hub, positions=positions())
    pos = hub.advice.compute(now)["positions"][0]
    assert pos["flags"] == ["reference_incomplete"]
    assert band(pos, "8k")["now_db"] is not None and band(pos, "8k")["change_db"] is None


def test_air_loss_outside_the_standards_range_is_flagged(tmp_path):
    _h, snap = air_loss_snapshot(tmp_path, (20, 50.0), (-25, 60.0))
    assert snap["positions"][0]["flags"] == ["out_of_range"]
    assert band(snap["positions"][0], "8k")["now_db"] is not None


# ------------------------------------------------------------------ publishing and the snapshot
def test_advice_changed_needs_a_real_change():
    base = adv.empty_advice()
    base["paths"] = [{"id": "a", "name": "A", "distance_m": 60, "source": "site", "source_fallback": [],
                      "drift_ms": 0.10, "suggested_delay_ms": 100.1, "current_delay_ms": 100.0,
                      "threshold_ms": 1.0, "phase_deg": None, "phase_hint_hz": None, "level": "ok",
                      "flags": [], "stale": False}]
    base["positions"] = [{"id": "foh", "name": "FOH", "distance_m": 40, "flags": [], "stale": False,
                          "bands": [{"band": "8k", "hz_used": 7943.28, "now_db": 3.0, "change_db": 0.5}]}]

    def tweak(**kw):
        import copy
        new = copy.deepcopy(base)
        for k, v in kw.items():
            if k.startswith("pos_"):
                new["positions"][0]["bands"][0][k[4:]] = v
            else:
                new["paths"][0][k] = v
        if "drift_ms" in kw:
            new["paths"][0]["suggested_delay_ms"] = None if kw["drift_ms"] is None else 100.0 + kw["drift_ms"]
        return new

    assert adv.advice_changed(None, base) and not adv.advice_changed(base, tweak())
    assert not adv.advice_changed(base, tweak(drift_ms=0.14)) and adv.advice_changed(base, tweak(drift_ms=0.15))
    assert not adv.advice_changed(base, tweak(pos_now_db=3.09)) and adv.advice_changed(base, tweak(pos_now_db=3.1))
    assert not adv.advice_changed(base, tweak(pos_change_db=0.59)) and adv.advice_changed(base, tweak(pos_change_db=0.61))
    assert adv.advice_changed(base, tweak(level="advisory")) and adv.advice_changed(base, tweak(stale=True))
    assert adv.advice_changed(base, tweak(drift_ms=None)) and adv.advice_changed(base, tweak(flags=["out_of_range"]))
    assert adv.advice_changed(base, adv.empty_advice())


def test_the_tick_publishes_only_on_a_change_and_at_most_every_two_seconds(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub)
    seen = []
    hub.bus.subscribe("advice", lambda _t, payload: seen.append(payload))
    now = time.time()
    air(hub, now - 30, 20, 50, 101325)
    hub.add_marker("Aligned", "test", now - 25)
    configure(hub, [path(distance=60)])
    hub.advice.tick(now)
    assert len(seen) == 1 and seen[0]["paths"][0]["drift_ms"] == pytest.approx(0.0, abs=1e-9)
    air(hub, now, 20.05, 50, 101325)                      # 0.016 ms: too small to matter
    hub.advice.tick(now + 2.5)
    assert len(seen) == 1
    air(hub, now, 21, 50, 101325)                         # about -0.3 ms
    hub.advice.tick(now + 3.0)                            # moved enough, but only 3.0 s since the last publish... ok
    assert len(seen) == 2
    air(hub, now, 22, 50, 101325)
    hub.advice.tick(now + 4.0)                            # moved enough, but under 2 s after the last publish
    assert len(seen) == 2
    hub.advice.tick(now + 5.1)
    assert len(seen) == 3 and seen[-1]["paths"][0]["drift_ms"] == pytest.approx(-0.63, abs=0.05)


def test_an_empty_setup_publishes_nothing_and_has_the_empty_shape(tmp_path):
    hub = make_hub(tmp_path)
    seen = []
    hub.bus.subscribe("advice", lambda _t, payload: seen.append(payload))
    hub.advice.tick(time.time())
    assert seen == [] and hub.snapshot()["advice"] == adv.empty_advice()


def test_a_failure_in_the_advice_never_stops_the_tick(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    configure(hub, [path()])
    monkeypatch.setattr(hub.advice, "compute", lambda now: 1 / 0)
    hub.tick()                                            # must not raise


def test_the_snapshot_block_has_the_planned_fields_and_nothing_private(tmp_path):
    hub = make_hub(tmp_path)
    add_env(hub, "secret_node_7", "Delay Tower 1")
    hub.devices["secret_node_7"].hw_id = "mac:025e00000001"
    now = time.time()
    air(hub, now - 30, 20, 50, 101325, "secret_node_7")
    hub.add_marker("Aligned", "test", now - 25)
    hub.add_marker("Soundcheck", "test", now - 24)
    air(hub, now - 3, 26, 50, 101325, "secret_node_7")
    configure(hub, [path("tower", 60, name="Tower", source_area="Delay Tower 1", current_delay_ms=100)],
              positions())
    hub.advice.tick(now)
    block = hub.snapshot()["advice"]
    assert set(block) == {"drift_ref", "air_ref", "resolution", "paths", "positions"}
    assert set(block["drift_ref"]) == {"label", "ts", "marker_id", "has_readings", "delta"}
    assert set(block["paths"][0]) == {
        "id", "name", "distance_m", "source", "source_fallback", "drift_ms", "suggested_delay_ms",
        "current_delay_ms", "threshold_ms", "phase_deg", "phase_hint_hz", "level", "flags", "stale"}
    assert set(block["positions"][0]["bands"][0]) == {"band", "hz_used", "now_db", "change_db"}
    text = str(block)
    assert "secret_node_7" not in text and "mac:" not in text and "025e" not in text
    assert "Delay Tower 1" not in text.replace("'source_area'", "")      # the area name is a setting, not shown
    assert hub.snapshot()["advice"] == block


# ------------------------------------------------------------------ emulate
async def test_emulate_seeds_the_demo_setup_and_markers_once(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    add_env(hub)
    await hub.advice.stop()
    hub.advice.start_demo()
    try:
        cfg = hub.config.advice
        assert [(p.name, p.distance_m, p.source_area, p.drift_threshold_ms) for p in cfg.paths] == [
            ("Delay tower 1", 85, "Delay tower 1", 0.2), ("Front fill", 8, "", 0.2), ("Sub to tops", 2.5, "", 0.2)]
        assert [(p.name, p.distance_m) for p in cfg.positions] == [("FOH", 40), ("Rear", 90)]
        assert ConfigStore(tmp_path / "config.yaml").load().advice == cfg        # saved to the emulate folder
        assert hub.advice.demo_markers(time.time()) == []                          # no site reading yet
        air(hub, time.time() - 5, 22, 50, 101325)
        added = hub.advice.demo_markers(time.time())
        assert [m.label for m in added] == ["Soundcheck", "Aligned"] and {m.source for m in added} == {"emulate"}
        assert hub.advice.demo_markers(time.time()) == []                          # not twice
    finally:
        await hub.advice.stop()


async def test_emulate_keeps_the_admins_own_setup(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    configure(hub, [path("mine", 33)])
    hub.advice.start_demo()
    try:
        assert [p.id for p in hub.config.advice.paths] == ["mine"] and hub.config.advice.positions == []
    finally:
        await hub.advice.stop()


async def test_emulate_flow_through_the_hub_with_a_node_that_drops_out(tmp_path):
    from stagewatch.integrations.esphome import EsphomeIntegration
    hub = Hub(tmp_path, emulate=True)
    hub.config.site.smoothing_tau_s = 0
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    await hub.start()
    try:
        now = time.time()
        hub.compute_site(now)
        hub.advice.demo_markers(now)
        hub.advice.tick(now + 1)
        snap = hub.snapshot()["advice"]
        assert [p["id"] for p in snap["paths"]] == ["delay_tower_1", "front_fill", "sub_to_tops"]
        assert [p["id"] for p in snap["positions"]] == ["foh", "rear"]
        tower = snap["paths"][0]
        assert tower["source"] == "area" and tower["source_fallback"] == ["p"]   # that node has no pressure sensor
        assert all(p["drift_ms"] is not None for p in snap["paths"])
        # The delay-tower node drops out: its area has no fresh reading, so the site average stands in.
        hub.set_device_status("sim_delay_1", Status.MISSING, "emulated dropout")
        for e in hub.entities.values():
            if e.device_id == "sim_delay_1":
                e.updated = now - 1000
        dropped = hub.advice.compute(now + 2)["paths"][0]
        assert dropped["source"] == "site" and dropped["source_fallback"] == ["t", "rh", "p"]
        # Humidity sensors stop: the air loss estimate refuses to guess.
        for e in list(hub.entities.values()):
            if e.kind == Kind.HUMIDITY and not e.derived:
                e.updated = now - 1000
        hub.compute_site(now + 3)
        assert hub.advice.compute(now + 3)["positions"][0]["flags"] == ["needs_humidity"]
    finally:
        await hub.stop()
