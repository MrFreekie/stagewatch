import time

import pytest

from stagewatch.core.alarms import AlarmEngine
from stagewatch.core.config import Config, ConfigStore, Threshold
from stagewatch.core.derived import Ema, robust_mean
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.core.recorder import Recorder


# ------------------------------------------------------------------ derived
def test_robust_mean_rejects_outlier_with_three_sensors():
    mean, used = robust_mean([20.0, 20.4, 31.0], 3.0)
    assert used == 2 and mean == pytest.approx(20.2)


def test_robust_mean_keeps_both_of_two():
    mean, used = robust_mean([20.0, 30.0], 3.0)
    assert used == 2 and mean == pytest.approx(25.0)


def test_robust_mean_empty():
    assert robust_mean([], 3.0) == (None, 0)


def test_ema_converges_and_resets_after_gap():
    ema = Ema(10.0)
    assert ema.update(20.0, 0.0) == 20.0
    v = ema.update(30.0, 10.0)
    assert 20.0 < v < 30.0
    assert ema.update(40.0, 1000.0) == 40.0  # gap > 5*tau resets


# ------------------------------------------------------------------- alarms
def _lookup(values):
    return lambda eid: (values.get(eid), False)


def test_alarm_hold_hysteresis_and_ack():
    t = Threshold(id="hot", entity="x", above=30.0, hysteresis=1.0, hold_s=5.0, level=2)
    eng = AlarmEngine()
    vals = {"x": 31.0}
    assert eng.evaluate([t], _lookup(vals), 0.0) == []          # holding
    changes = eng.evaluate([t], _lookup(vals), 6.0)
    assert [c.event for c in changes] == ["raise"] and eng.sounding
    vals["x"] = 29.5                                           # inside hysteresis band
    assert eng.evaluate([t], _lookup(vals), 7.0) == []
    eng.ack_all()
    assert not eng.sounding and eng.active                     # ack silences, doesn't clear
    vals["x"] = 28.9
    assert [c.event for c in eng.evaluate([t], _lookup(vals), 8.0)] == ["clear"]
    assert not eng.active


def test_stale_input_neither_raises_nor_clears():
    t = Threshold(id="cold", entity="x", below=5.0)
    eng = AlarmEngine()
    assert eng.evaluate([t], lambda eid: (1.0, True), 0.0) == []


def test_removed_threshold_clears_alarm():
    t = Threshold(id="hot", entity="x", above=30.0)
    eng = AlarmEngine()
    eng.evaluate([t], _lookup({"x": 40.0}), 0.0)
    assert [c.event for c in eng.evaluate([], _lookup({"x": 40.0}), 1.0)] == ["clear"]


# ----------------------------------------------------------- config/recorder
def test_config_roundtrip_and_tolerant_load(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.load()
    store.config.site.name = "Test"
    store.save()
    assert ConfigStore(tmp_path / "config.yaml").load().site.name == "Test"
    (tmp_path / "bad.yaml").write_text("site: [not, a, dict]\n", encoding="utf-8")
    bad = ConfigStore(tmp_path / "bad.yaml")
    assert isinstance(bad.load(), Config)  # falls back to defaults
    assert (tmp_path / "bad.invalid.yaml").exists()


def test_recorder_history_markers_and_shows(tmp_path):
    rec = Recorder(tmp_path / "db.sqlite3")
    now = time.time()
    for i in range(10):
        rec.record_state("a", float(i), now - 100 + i * 10)
    hist = rec.history(["a"], now - 200, now, max_points=5)
    assert 1 <= len(hist["a"]) <= 6
    assert rec.value_at("a", now - 45) == pytest.approx(5.0)
    m = rec.add_marker("Aligned", "test", now - 50)
    assert [x.label for x in rec.markers()] == ["Aligned"]
    rec.start_show("Day 2")
    assert rec.markers() == [] and rec.current_show()["name"] == "Day 2"
    assert rec.marker(m.id) is not None
    rec.close()


# --------------------------------------------------------------------- hub
@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    for dev, t, rh, p in (("a", 20.0, 50.0, 101000.0), ("b", 22.0, 60.0, 101100.0)):
        h.register_device(Device(dev, dev, "test", status=Status.OK))
        for kind, val in ((Kind.TEMPERATURE, t), (Kind.HUMIDITY, rh), (Kind.PRESSURE, p)):
            h.register_entity(Entity(f"{dev}.{kind.value}", dev, kind.value, kind))
            h.update_state(f"{dev}.{kind.value}", val)
    yield h
    h.recorder.close()


def test_hub_site_average_and_speed_of_sound(hub):
    hub.config.site.smoothing_tau_s = 0
    hub.compute_site()
    assert hub.entities["site.temperature"].value == pytest.approx(21.0)
    assert hub.entities["site.pressure"].value == pytest.approx(101050.0)
    assert hub.site_meta["pressure_source"] == "measured"
    assert 343 < hub.entities["site.speed_of_sound"].value < 346


def test_hub_calibration_offset_and_exclusion(hub):
    from stagewatch.core.config import EntitySettings
    hub.config.site.smoothing_tau_s = 0
    hub.config.entities["b.temperature"] = EntitySettings(offset=-2.0)
    hub.update_state("b.temperature", 22.0)
    hub.compute_site()
    assert hub.entities["site.temperature"].value == pytest.approx(20.0)
    hub.config.entities["a.temperature"] = EntitySettings(include_in_average=False)
    hub.compute_site()
    assert hub.entities["site.temperature"].value == pytest.approx(20.0)  # only b (20 after offset)


def test_settings_change_restarts_smoothing_but_readings_stay_smoothed(hub):
    from stagewatch.core.config import EntitySettings
    hub.config.site.smoothing_tau_s = 300
    t0 = time.time()
    hub.compute_site(t0)
    assert hub.entities["site.temperature"].value == pytest.approx(21.0)
    # A reading change is smoothed: 10 s into a 300 s average barely moves.
    hub.update_state("b.temperature", 32.0)
    hub.compute_site(t0 + 10)
    assert hub.entities["site.temperature"].value < 21.5
    # A settings change (leave sensor a out) shows at once.
    hub.config.entities["a.temperature"] = EntitySettings(include_in_average=False)
    hub.compute_site(t0 + 11)
    assert hub.entities["site.temperature"].value == pytest.approx(32.0)
    # So does a new smoothing time.
    hub.update_state("b.temperature", 20.0)
    hub.config.site.smoothing_tau_s = 120
    hub.compute_site(t0 + 12)
    assert hub.entities["site.temperature"].value == pytest.approx(20.0)


def test_hub_falls_back_to_altitude_without_pressure(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    h.config.site.altitude_m = 1000
    h.register_device(Device("a", "a", "test"))
    h.register_entity(Entity("a.temperature", "a", "t", Kind.TEMPERATURE))
    h.update_state("a.temperature", 15.0)
    h.compute_site()
    assert h.site_meta["pressure_source"] == "altitude"
    assert h.entities["site.pressure"].value is None
    assert h.entities["site.speed_of_sound"].value is not None
    h.recorder.close()


def test_marker_delta_reports_travel_time_change(hub):
    hub.config.site.smoothing_tau_s = 0
    hub.compute_site(time.time() - 30)
    marker = hub.add_marker("Aligned", "test", time.time() - 20)
    for dev in ("a", "b"):
        hub.update_state(f"{dev}.temperature", 30.0)
    hub.compute_site()
    d = hub.marker_delta(marker.id)
    assert d["values"]["site.temperature"]["delta"] == pytest.approx(9.0)
    # warmer air = faster sound = shorter travel time
    assert d["delta_travel_ms"] < 0


def test_device_offline_raises_advisory_alarm(hub):
    hub.set_device_status("a", Status.MISSING, "connection lost")
    assert any(a["id"] == "device:a" and a["level"] == 1 for a in hub.alarms.to_list())
    hub.set_device_status("a", Status.OK)
    assert not any(a["id"] == "device:a" for a in hub.alarms.to_list())


# ------------------------------------------------- invalid config must not reopen onboarding
import pytest  # noqa: E402
import yaml  # noqa: E402

from stagewatch.core.config import ALLOW_ONBOARDING_FLAG, reset_admin_pin, valid_pin_hash  # noqa: E402
from stagewatch.web.auth import hash_pin, verify_pin  # noqa: E402


def _write(path, data):
    path.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data, encoding="utf-8")


def test_valid_pin_hash_format():
    assert valid_pin_hash(hash_pin("1234"))
    for bad in ("", None, 5, "plain", "pbkdf2_sha256$x$a$b", "pbkdf2_sha256$1000$!!$!!", "md5$1$YWJj$YWJj"):
        assert not valid_pin_hash(bad)


def test_invalid_config_keeps_pin_and_valid_sections(tmp_path):
    pin = hash_pin("1234")
    _write(tmp_path / "config.yaml", {
        "admin": {"pin_hash": pin},
        "site": {"name": "Arena", "altitude_m": 120},
        "esphome_devices": [{"id": "a", "host": "h1"}, {"id": "b", "host": "h2", "port": 99999}],
        "osc_out": {"rate_hz": "fast"},  # invalid section: reset
        "thresholds": "nonsense",
    })
    store = ConfigStore(tmp_path / "config.yaml")
    cfg = store.load()
    assert verify_pin("1234", cfg.admin.pin_hash)
    assert cfg.site.name == "Arena" and cfg.site.altitude_m == 120
    assert [d.id for d in cfg.esphome_devices] == ["a"]  # only the bad entry is dropped
    assert cfg.osc_out.rate_hz == 1.0 and cfg.thresholds == []
    assert not store.recovery_required
    assert (tmp_path / "config.invalid.yaml").exists()
    # the salvaged config was written back: the next boot is clean and still has the PIN
    again = ConfigStore(tmp_path / "config.yaml")
    assert verify_pin("1234", again.load().admin.pin_hash) and not again.recovery_required


def test_unparseable_config_still_recovers_pin_by_scan(tmp_path):
    pin = hash_pin("4321")
    (tmp_path / "config.yaml").write_text(f"admin:\n  pin_hash: {pin}\nsite: {{name: [unclosed\n",
                                          encoding="utf-8")
    store = ConfigStore(tmp_path / "config.yaml")
    assert verify_pin("4321", store.load().admin.pin_hash)
    assert not store.recovery_required


@pytest.mark.parametrize("content", ["site: [unclosed\n", "admin: {pin_hash: nope}\nsite: {altitude_m: x}\n",
                                     "", "- a\n- b\n"])
def test_unsalvageable_pin_requires_recovery(tmp_path, content):
    (tmp_path / "config.yaml").write_text(content, encoding="utf-8")
    store = ConfigStore(tmp_path / "config.yaml")
    assert store.load().admin.pin_hash == ""
    assert store.recovery_required  # network onboarding stays closed
    again = ConfigStore(tmp_path / "config.yaml")
    again.load()
    assert again.recovery_required  # ... also after a restart


def test_fresh_install_is_not_recovery(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.load()
    assert not store.recovery_required


def test_invalid_files_are_never_overwritten(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("site: [one\n", encoding="utf-8")
    ConfigStore(p).load()
    first = (tmp_path / "config.invalid.yaml").read_text(encoding="utf-8")
    p.write_text("site: [two\n", encoding="utf-8")
    ConfigStore(p).load()
    assert (tmp_path / "config.invalid.yaml").read_text(encoding="utf-8") == first
    later = list(tmp_path.glob("config.invalid-*.yaml"))
    assert len(later) == 1 and "two" in later[0].read_text(encoding="utf-8")


def test_reset_admin_pin_reopens_onboarding_locally(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("site: [unclosed\n", encoding="utf-8")
    assert ConfigStore(p).load() is not None
    msg = reset_admin_pin(tmp_path)
    assert "Restart" in msg and (tmp_path / ALLOW_ONBOARDING_FLAG).exists()
    store = ConfigStore(p)
    store.load()
    assert not store.recovery_required and store.config.admin.pin_hash == ""
    store.config.admin.pin_hash = hash_pin("9999")
    store.save()  # onboarding done: the override is spent
    assert not (tmp_path / ALLOW_ONBOARDING_FLAG).exists()


def test_reset_admin_pin_clears_an_existing_hash(tmp_path):
    p = tmp_path / "config.yaml"
    _write(p, {"admin": {"pin_hash": hash_pin("1234")}, "site": {"name": "Keep"}})
    assert "cleared" in reset_admin_pin(tmp_path)
    cfg = ConfigStore(p).load()
    assert cfg.admin.pin_hash == "" and cfg.site.name == "Keep"
    with pytest.raises(FileNotFoundError):
        reset_admin_pin(tmp_path / "missing")
