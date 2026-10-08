"""Sensor accuracy and the opt-in accuracy-weighted site average.

Weights are inverse-variance: w = 1 / accuracy**2, in canonical units (degC, %RH, Pa), and are
renormalised over the sensors actually in the average. Expected values below are worked by hand.
"""

import time

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stagewatch.core.calibration import set_calibration
from stagewatch.core.config import Calibration, Config, ConfigStore, EntitySettings, SiteConfig
from stagewatch.core.derived import AvgInput, average, robust_mean
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app

A = AvgInput


# ------------------------------------------------------------------ the maths
def test_two_sensors_weights_are_25_to_1():
    """0.1 and 0.5 degC: w = 1/0.01 = 100 and 1/0.25 = 4, so 25:1. Shares 100/104 = 0.961538
    and 4/104 = 0.038462. Values 20.0 and 21.0: mean = 20.0 + 0.038462 * 1.0 = 20.038462."""
    r = average([A("a", 20.0, 0.1), A("b", 21.0, 0.5)], None, True)
    assert r.weighted and r.used == 2 and r.reason == ""
    assert r.shares["a"] == pytest.approx(100 / 104)
    assert r.shares["b"] == pytest.approx(4 / 104)
    assert r.shares["a"] / r.shares["b"] == pytest.approx(25.0)
    assert r.mean == pytest.approx(20.0 + 4 / 104)
    assert sum(r.shares.values()) == pytest.approx(1.0)


def test_equal_accuracies_give_the_plain_mean():
    """Same figure on both: weights equal, so (20 + 21) / 2 = 20.5."""
    r = average([A("a", 20.0, 0.3), A("b", 21.0, 0.3)], None, True)
    assert r.weighted and r.mean == pytest.approx(20.5)
    assert r.shares == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}


def test_switch_off_is_the_old_equal_average():
    r = average([A("a", 20.0, 0.1), A("b", 21.0, 0.5)], None, False)
    assert not r.weighted and r.reason == "off" and r.mean == 20.5
    assert r.shares == {"a": 0.5, "b": 0.5}
    # exactly what robust_mean gives, with and without rejection
    vals = [20.0, 20.4, 31.0]
    inputs = [A(f"s{i}", v, 0.1) for i, v in enumerate(vals)]
    assert average(inputs, 3.0, False).mean == robust_mean(vals, 3.0)[0]
    assert average(inputs, None, False).mean == robust_mean(vals, None)[0]


def test_one_missing_accuracy_keeps_equal_weights():
    """One of two has no figure: equal weights, (20 + 21) / 2 = 20.5; the reason counts 1 missing."""
    r = average([A("a", 20.0, 0.1), A("b", 21.0, None)], None, True)
    assert not r.weighted and r.reason == "missing" and r.missing == 1
    assert r.mean == 20.5 and r.shares == {"a": 0.5, "b": 0.5}


def test_two_missing_are_counted():
    r = average([A("a", 20.0, 0.1), A("b", 21.0, None), A("c", 22.0, None)], None, True)
    assert r.reason == "missing" and r.missing == 2 and r.mean == pytest.approx(21.0)


def test_mixed_basis_keeps_equal_weights():
    r = average([A("a", 20.0, 0.1, "typical"), A("b", 21.0, 0.5, "maximum")], None, True)
    assert not r.weighted and r.reason == "mixed" and r.mean == 20.5


def test_same_basis_maximum_is_weighted():
    r = average([A("a", 20.0, 0.1, "maximum"), A("b", 21.0, 0.5, "maximum")], None, True)
    assert r.weighted and r.mean == pytest.approx(20.0 + 4 / 104)


def test_zero_or_negative_accuracy_counts_as_missing():
    assert average([A("a", 20.0, 0.0), A("b", 21.0, 0.5)], None, True).reason == "missing"
    assert average([A("a", 20.0, -0.1), A("b", 21.0, 0.5)], None, True).reason == "missing"
    assert average([A("a", 20.0, float("nan")), A("b", 21.0, 0.5)], None, True).reason == "missing"


def test_three_sensors_weighted_mean():
    """0.1, 0.1, 0.5 with 20.0, 20.4, 21.0: w = 100, 100, 4 (sum 204).
    mean = (2000 + 2040 + 84) / 204 = 4124 / 204 = 20.215686."""
    r = average([A("a", 20.0, 0.1), A("b", 20.4, 0.1), A("c", 21.0, 0.5)], None, True)
    assert r.mean == pytest.approx(4124 / 204)
    assert r.shares["c"] == pytest.approx(4 / 204)


def test_dropout_renormalises_the_weights():
    """Sensor b goes stale (the caller no longer passes it): the others are renormalised,
    100 and 4 -> (2000 + 84) / 104 = 20.038462, shares 0.961538 / 0.038462, summing to 1."""
    r = average([A("a", 20.0, 0.1), A("c", 21.0, 0.5)], None, True)
    assert r.mean == pytest.approx(2084 / 104)
    assert sum(r.shares.values()) == pytest.approx(1.0)


def test_outlier_is_rejected_and_its_missing_figure_does_not_block():
    """20.0, 20.4, 31.0 with a 3.0 limit: the median is 20.4, so 31.0 is rejected. The rejected
    sensor has no accuracy figure, but only sensors still in the average count, so weighting
    applies to the other two (equal figures): mean 20.2, shares 0.5 and 0.5, none for 'c'."""
    r = average([A("a", 20.0, 0.1), A("b", 20.4, 0.1), A("c", 31.0, None)], 3.0, True)
    assert r.weighted and r.rejected == ["c"] and r.used == 2
    assert r.mean == pytest.approx(20.2) and "c" not in r.shares


def test_outlier_rejection_is_not_weakened_by_a_good_accuracy():
    """A very accurate sensor far from the median is still rejected (rejection looks at values)."""
    r = average([A("a", 20.0, 0.5), A("b", 20.4, 0.5), A("c", 31.0, 0.01)], 3.0, True)
    assert r.rejected == ["c"] and r.mean == pytest.approx(20.2)


def test_pressure_in_pascal():
    """100 Pa and 200 Pa (1 and 2 hPa): w = 1e-4 and 2.5e-5, ratio 4:1, shares 0.8 and 0.2.
    Values 101300 and 101400: mean = 101300 + 0.2 * 100 = 101320."""
    r = average([A("a", 101300.0, 100.0), A("b", 101400.0, 200.0)], None, True)
    assert r.shares["a"] == pytest.approx(0.8) and r.mean == pytest.approx(101320.0)


def test_single_sensor_and_nothing():
    r = average([A("a", 20.0, 0.1)], None, True)
    assert r.mean == 20.0 and r.shares == {"a": 1.0} and r.reason == ""
    assert average([], None, True).mean is None


# ------------------------------------------------------------------ the hub
def _hub(tmp_path, weigh=True):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    h.config.site.weight_by_accuracy = weigh
    h.register_device(Device("a", "a", "test"))
    h.register_device(Device("b", "b", "test"))
    h.register_entity(Entity("a.temperature", "a", "a t", Kind.TEMPERATURE))
    h.register_entity(Entity("b.temperature", "b", "b t", Kind.TEMPERATURE))
    return h


def test_hub_weights_by_accuracy_and_reports_shares(tmp_path):
    h = _hub(tmp_path)
    set_calibration(h.config, "a.temperature", "", 0.0, True, accuracy=0.1)
    set_calibration(h.config, "b.temperature", "", 0.0, True, accuracy=0.5)
    h.update_state("a.temperature", 20.0)
    h.update_state("b.temperature", 21.0)
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(20.0 + 4 / 104)
    info = h.average_info["temperature"]
    assert info["weighted"] and info["note"] == "Weighted by accuracy"
    assert info["sensors"]["a.temperature"]["share"] == pytest.approx(100 / 104)
    h.recorder.close()


def test_hub_switch_off_is_equal_and_shows_equal_shares(tmp_path):
    h = _hub(tmp_path, weigh=False)
    set_calibration(h.config, "a.temperature", "", 0.0, True, accuracy=0.1)
    set_calibration(h.config, "b.temperature", "", 0.0, True, accuracy=0.5)
    h.update_state("a.temperature", 20.0)
    h.update_state("b.temperature", 21.0)
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(20.5)
    info = h.average_info["temperature"]
    assert info["note"] == "" and info["sensors"]["b.temperature"]["share"] == 0.5
    h.recorder.close()


def test_hub_fixed_texts_for_missing_and_mixed(tmp_path):
    h = _hub(tmp_path)
    set_calibration(h.config, "a.temperature", "", 0.0, True, accuracy=0.1)
    h.update_state("a.temperature", 20.0)
    h.update_state("b.temperature", 21.0)
    h.compute_site()
    assert h.average_info["temperature"]["note"] == "Equal weights: 1 sensor has no accuracy figure"
    assert h.entities["site.temperature"].value == pytest.approx(20.5)
    set_calibration(h.config, "b.temperature", "", 0.0, True, accuracy=0.5, accuracy_basis="maximum")
    h.compute_site()
    assert h.average_info["temperature"]["note"] == "Equal weights: the figures mix typical and maximum"
    h.recorder.close()


def test_hub_stale_and_excluded_sensors_get_no_share_and_weights_renormalise(tmp_path):
    h = _hub(tmp_path)
    h.register_device(Device("c", "c", "test"))
    h.register_entity(Entity("c.temperature", "c", "c t", Kind.TEMPERATURE))
    for e, acc in (("a", 0.1), ("b", 0.5), ("c", 0.1)):
        set_calibration(h.config, f"{e}.temperature", "", 0.0, True, accuracy=acc)
    now = time.time()
    h.update_state("a.temperature", 20.0, now)
    h.update_state("b.temperature", 21.0, now)
    h.update_state("c.temperature", 22.0, now - 1000)           # stale
    h.compute_site(now)
    s = h.average_info["temperature"]["sensors"]
    assert s["c.temperature"] == {"state": "stale", "share": None}
    assert s["a.temperature"]["share"] == pytest.approx(100 / 104)
    set_calibration(h.config, "b.temperature", "", 0.0, False, accuracy=0.5)   # ticked off
    h.compute_site(now)
    s = h.average_info["temperature"]["sensors"]
    assert s["b.temperature"] == {"state": "off", "share": None}
    assert s["a.temperature"]["share"] == 1.0
    h.recorder.close()


def test_hub_outlier_state(tmp_path):
    h = _hub(tmp_path)
    h.register_device(Device("c", "c", "test"))
    h.register_entity(Entity("c.temperature", "c", "c t", Kind.TEMPERATURE))
    h.update_state("a.temperature", 20.0)
    h.update_state("b.temperature", 20.4)
    h.update_state("c.temperature", 31.0)
    h.compute_site()
    assert h.average_info["temperature"]["sensors"]["c.temperature"] == {"state": "outlier", "share": None}
    assert h.entities["site.temperature"].value == pytest.approx(20.2)
    h.recorder.close()


def test_accuracy_above_the_kind_limit_in_a_file_counts_as_missing(tmp_path):
    h = _hub(tmp_path)
    h.config.entities["a.temperature"] = EntitySettings.model_construct(
        offset=0.0, include_in_average=True, accuracy=19.0, accuracy_basis="typical")
    assert h.accuracy_of(h.entities["a.temperature"])[0] == 19.0
    h.config.entities["a.temperature"] = EntitySettings.model_construct(
        offset=0.0, include_in_average=True, accuracy=25.0, accuracy_basis="typical")
    assert h.accuracy_of(h.entities["a.temperature"])[0] is None
    h.recorder.close()


def test_smoothing_restarts_when_the_weighting_changes(tmp_path):
    h = _hub(tmp_path, weigh=False)
    h.config.site.smoothing_tau_s = 30
    now = time.time()
    h.update_state("a.temperature", 20.0, now)
    h.update_state("b.temperature", 21.0, now)
    h.compute_site(now)
    set_calibration(h.config, "a.temperature", "", 0.0, True, accuracy=0.1)
    set_calibration(h.config, "b.temperature", "", 0.0, True, accuracy=0.5)
    h.config.site.weight_by_accuracy = True
    h.compute_site(now + 1)
    assert h.entities["site.temperature"].value == pytest.approx(20.0 + 4 / 104)   # at once
    h.recorder.close()


# ------------------------------------------------------------------ config
def test_defaults_and_old_files_load():
    assert SiteConfig().weight_by_accuracy is False
    cfg = Config.model_validate({"site": {"name": "x"}, "entities": {"a.temperature": {"offset": 0.5}},
                                 "calibrations": {"mac:025e00000002/temperature": {"offset": 1.0}}})
    assert cfg.entities["a.temperature"].accuracy is None
    assert cfg.calibrations["mac:025e00000002/temperature"].accuracy_basis == "typical"


def test_models_validate_strictly():
    for bad in (0, -1, float("nan"), float("inf"), 10 ** 6):
        with pytest.raises(ValidationError):
            EntitySettings(accuracy=bad)
        with pytest.raises(ValidationError):
            Calibration(accuracy=bad)
    with pytest.raises(ValidationError):
        Calibration(accuracy=0.1, accuracy_basis="best")
    assert Calibration(accuracy=0.1, accuracy_basis="maximum").accuracy == 0.1


def test_salvage_unreadable_accuracy_keeps_the_offset(caplog):
    """A hand-edited bad figure is dropped on load (logged as a count only); the offset stays."""
    raw = {"entities": {"a.temperature": {"offset": 0.7, "accuracy": "lots", "accuracy_basis": "typical"}},
           "calibrations": {"mac:025e00000002/humidity": {"offset": 2.0, "accuracy": -3,
                                                           "accuracy_basis": "weird"}}}
    with caplog.at_level("WARNING"):
        cfg = Config.model_validate(raw)
    assert cfg.entities["a.temperature"].offset == 0.7 and cfg.entities["a.temperature"].accuracy is None
    rec = cfg.calibrations["mac:025e00000002/humidity"]
    assert rec.offset == 2.0 and rec.accuracy is None and rec.accuracy_basis == "typical"
    assert "lots" not in caplog.text and "weird" not in caplog.text


def test_save_and_reload_round_trip_and_older_build_ignores_the_keys(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    cfg = store.config
    cfg.site.weight_by_accuracy = True
    set_calibration(cfg, "a.temperature", "mac:025e00000002/temperature", 0.5, True, accuracy=0.1,
                    accuracy_basis="maximum")
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load()
    assert again.site.weight_by_accuracy is True
    assert again.calibrations["mac:025e00000002/temperature"].accuracy == 0.1
    assert again.calibrations["mac:025e00000002/temperature"].accuracy_basis == "maximum"
    # An older build (no such fields) reads the same file by ignoring unknown keys: model that with
    # a model that has only the old fields.
    from pydantic import BaseModel, ConfigDict

    class OldCal(BaseModel):
        model_config = ConfigDict(extra="ignore")
        offset: float = 0.0
        include_in_average: bool = True

    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    old = OldCal.model_validate(raw["calibrations"]["mac:025e00000002/temperature"])
    assert old.offset == 0.5
    from stagewatch.version import CONFIG_SCHEMA_VERSION
    assert raw["schema_version"] == CONFIG_SCHEMA_VERSION


def test_config_version_not_bumped_for_this_change():
    """Additive fields with defaults only. If this fails, the schema-change rules apply."""
    from stagewatch.version import CONFIG_SCHEMA_VERSION
    assert CONFIG_SCHEMA_VERSION == 2


def test_accuracy_follows_the_board():
    cfg = Config()
    node, new = "mac:025e00000002", "mac:025e00000003"
    set_calibration(cfg, "a.temperature", f"{node}/temperature", 0.5, True, accuracy=0.1,
                    accuracy_basis="maximum")
    from stagewatch.core.calibration import copy_node
    assert copy_node(cfg, node, new) == 1
    moved = cfg.calibrations[f"{new}/temperature"]
    assert moved.accuracy == 0.1 and moved.accuracy_basis == "maximum"


def test_legacy_accuracy_moves_to_the_hardware_record():
    cfg = Config(entities={"foh.temperature": EntitySettings(offset=0.4, accuracy=0.2, accuracy_basis="maximum")})
    from stagewatch.core.calibration import move_legacy
    move_legacy(cfg, "foh", "mac:025e00000002")
    rec = cfg.calibrations["mac:025e00000002/temperature"]
    assert rec.accuracy == 0.2 and rec.accuracy_basis == "maximum"


def test_set_calibration_keeps_accuracy_when_not_sent():
    cfg = Config()
    key = "mac:025e00000002/temperature"
    set_calibration(cfg, "a.temperature", key, 0.0, True, accuracy=0.3)
    set_calibration(cfg, "a.temperature", key, 0.5, True)             # an old client: no accuracy
    assert cfg.calibrations[key].accuracy == 0.3
    set_calibration(cfg, "a.temperature", key, 0.5, True, accuracy=None)   # cleared on purpose
    assert cfg.calibrations[key].accuracy is None
    set_calibration(cfg, "b.temperature", "", 0.0, True, accuracy=0.3)
    set_calibration(cfg, "b.temperature", "", 1.0, True)
    assert cfg.entities["b.temperature"].accuracy == 0.3


# ------------------------------------------------------------------ the API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def _admin(client):
    assert client.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def _put(client, entity, **body):
    body.setdefault("offset", 0.0)
    body.setdefault("include_in_average", True)
    return client.put(f"/api/admin/entities/{entity}", json=body)


def test_entity_accuracy_saves_and_shows_in_admin_state(client):
    _admin(client)
    ent = next(e.id for e in client.hub.entities.values() if e.kind == Kind.HUMIDITY and not e.derived)
    r = _put(client, ent, accuracy=2.0, accuracy_basis="typical")
    assert r.status_code == 200
    client.hub.compute_site()
    st = client.get("/api/admin/state").json()["hardware"]
    assert st["settings"][ent]["accuracy"] == 2.0 and st["settings"][ent]["accuracy_basis"] == "typical"
    assert "averages" in st and st["averages"]["humidity"]["sensors"][ent]["state"] in ("in", "stale", "none")


def test_entity_save_without_accuracy_keeps_it(client):
    _admin(client)
    ent = next(e.id for e in client.hub.entities.values() if e.kind == Kind.TEMPERATURE and not e.derived)
    assert _put(client, ent, accuracy=0.5).status_code == 200
    assert _put(client, ent, offset=0.2).status_code == 200
    assert client.get("/api/admin/state").json()["hardware"]["settings"][ent]["accuracy"] == 0.5
    assert _put(client, ent, accuracy=None).status_code == 200
    assert client.get("/api/admin/state").json()["hardware"]["settings"][ent]["accuracy"] is None


def test_entity_accuracy_validation_has_fixed_text_and_no_echo(client):
    _admin(client)
    ents = [e for e in client.hub.entities.values() if not e.derived]
    temp = next(e.id for e in ents if e.kind == Kind.TEMPERATURE)
    press = next(e.id for e in ents if e.kind == Kind.PRESSURE)
    for bad in (0, -2, 98765.4321, "abc"):
        r = _put(client, temp, accuracy=bad)
        assert r.status_code == 422 and "98765.4321" not in r.text and "abc" not in r.text
    r = _put(client, temp, accuracy=21.0)   # over the 20 degC limit
    assert r.status_code == 422 and r.json()["detail"] == "That accuracy figure is too large for this kind of sensor"
    assert _put(client, temp, accuracy=20.0).status_code == 200
    assert _put(client, press, accuracy=5001.0).status_code == 422
    assert _put(client, press, accuracy=5000.0).status_code == 200
    assert _put(client, temp, accuracy=0.1, accuracy_basis="best").status_code == 422
    other = next((e.id for e in ents if e.kind not in (Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE)), None)
    if other:
        r = _put(client, other, accuracy=1.0)
        assert r.status_code == 422 and "temperature, humidity and pressure" in r.json()["detail"]


def test_entity_accuracy_needs_admin_and_same_origin(client):
    _admin(client)
    ent = next(e.id for e in client.hub.entities.values() if e.kind == Kind.TEMPERATURE and not e.derived)
    r = client.put(f"/api/admin/entities/{ent}", json={"offset": 0, "include_in_average": True, "accuracy": 1.0},
                   headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    client.cookies.clear()
    assert _put(client, ent, accuracy=1.0).status_code == 401
    assert client.get("/api/admin/averages").status_code == 401


def test_site_save_keeps_the_weighting_switch_when_not_sent(client):
    _admin(client)
    site = client.hub.config.site.model_dump()
    site["weight_by_accuracy"] = True
    assert client.put("/api/admin/site", json=site).status_code == 200
    assert client.hub.config.site.weight_by_accuracy is True
    site.pop("weight_by_accuracy")                  # the Site card does not send it
    assert client.put("/api/admin/site", json=site).status_code == 200
    assert client.hub.config.site.weight_by_accuracy is True
    site["weight_by_accuracy"] = False
    assert client.put("/api/admin/site", json=site).status_code == 200
    assert client.hub.config.site.weight_by_accuracy is False
    assert client.get("/api/admin/averages").json()["weight_by_accuracy"] is False


def test_no_accuracy_or_shares_in_the_public_snapshot(client):
    _admin(client)
    client.hub.config.site.weight_by_accuracy = True
    for e in client.hub.entities.values():
        if not e.derived and e.kind in (Kind.TEMPERATURE, Kind.HUMIDITY):
            assert _put(client, e.id, accuracy=0.4321).status_code == 200
    client.hub.compute_site()
    client.cookies.clear()                                   # as an ordinary dashboard
    for url in ("/api/snapshot", "/api/info", "/api/dashboard/foh"):
        r = client.get(url)
        if r.status_code == 200:
            for word in ("accuracy", "0.4321", "share", "weight_by", "Equal weights"):
                assert word not in r.text, (url, word)
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_text()
    assert "accuracy" not in first and "weight_by" not in first and "share" not in first
    assert "accuracy" not in str(client.hub.site_meta)


def test_js_helpers_run_in_node():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([node, str(root / "tests" / "js" / "accuracy_test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
