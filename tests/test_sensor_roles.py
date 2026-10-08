"""Sensor roles: Environment (air at the site) and Equipment (gear, never averaged).

Expected values are worked by hand in each docstring.
"""

import json
import re
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stagewatch import acoustics
from stagewatch.core.calibration import copy_node, move_legacy, set_calibration
from stagewatch.core.cards import KNOWN_CARDS, default_cards
from stagewatch.core.config import Calibration, Config, ConfigStore, EntitySettings, EsphomeDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app

STATIC = Path(__file__).resolve().parent.parent / "src" / "stagewatch" / "web" / "static"


def _hub(tmp_path, weigh=False):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    h.config.site.weight_by_accuracy = weigh
    return h


def _node(h, dev, role, kinds, values):
    """A node with one sensor per kind, each updated to its value."""
    h.register_device(Device(dev, dev, "test", role=role))
    for kind, value in zip(kinds, values):
        h.register_entity(Entity(f"{dev}.{kind.value}", dev, kind.value, kind))
        h.update_state(f"{dev}.{kind.value}", value)


T, H, P = Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE


# ---------------------------------------------------- equipment never enters a site value
def test_equipment_temperature_does_not_move_the_site_values(tmp_path):
    """Environment 20.0 and 22.0 degC average to 21.0; humidity 50 %; pressure 100,000 Pa. A rack at
    45.0 degC / 90 % / 90,000 Pa on an Equipment node must change none of them, so the speed of
    sound is speed_of_sound(21.0, 50, 100000) and the dew point is dew_point(21.0, 50)."""
    h = _hub(tmp_path)
    _node(h, "e1", "environment", [T, H, P], [20.0, 50.0, 100000.0])
    _node(h, "e2", "environment", [T], [22.0])
    _node(h, "rack", "equipment", [T, H, P], [45.0, 90.0, 90000.0])
    h.compute_site()
    v = {k: h.entities[k].value for k in ("site.temperature", "site.humidity", "site.pressure",
                                          "site.speed_of_sound", "site.dew_point")}
    assert v["site.temperature"] == pytest.approx(21.0)
    assert v["site.humidity"] == pytest.approx(50.0)
    assert v["site.pressure"] == pytest.approx(100000.0)
    assert v["site.speed_of_sound"] == pytest.approx(acoustics.speed_of_sound(21.0, 50.0, 100000.0))
    assert v["site.dew_point"] == pytest.approx(acoustics.dew_point_c(21.0, 50.0))
    assert h.site_meta["sensors"] == {"temperature": 2, "humidity": 1, "pressure": 1}
    h.recorder.close()


def test_an_equipment_only_site_has_no_average(tmp_path):
    """Only a rack at 45 degC: there is no site temperature (None), no speed of sound, and the
    barometer says there is no sensor rather than reading the rack's pressure."""
    h = _hub(tmp_path)
    _node(h, "rack", "equipment", [T, P], [45.0, 90000.0])
    h.compute_site()
    assert h.entities["site.temperature"].value is None
    assert h.entities["site.pressure"].value is None
    assert h.entities["site.speed_of_sound"].value is None
    assert h.site_meta["baro"] == {"state": "no_sensor"}
    assert h.site_meta["pressure_source"] == "altitude"
    h.recorder.close()


def test_equipment_takes_no_share_and_shares_renormalise_over_environment(tmp_path):
    """Weighted by accuracy. Environment a (0.1 degC, 20.0) and b (0.15, 21.0): w = 1 and 4/9,
    shares 9/13 and 4/13, mean 20 + 4/13. A rack with the best figure (0.01 degC, 60.0) would
    take 99 % if it counted; it must take none and leave a and b exactly as above."""
    h = _hub(tmp_path, weigh=True)
    _node(h, "a", "environment", [T], [20.0])
    _node(h, "b", "environment", [T], [21.0])
    _node(h, "rack", "equipment", [T], [60.0])
    for ent, acc in (("a", 0.1), ("b", 0.15), ("rack", 0.01)):
        set_calibration(h.config, f"{ent}.temperature", "", 0.0, True, accuracy=acc)
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(20.0 + 4 / 13)
    info = h.average_info["temperature"]
    assert info["weighted"] and info["note"] == "Weighted by accuracy"
    s = info["sensors"]
    assert s["a.temperature"]["share"] == pytest.approx(9 / 13)
    assert s["b.temperature"]["share"] == pytest.approx(4 / 13)
    assert s["rack.temperature"] == {"state": "equipment", "share": None}
    h.recorder.close()


def test_equipment_without_an_accuracy_figure_does_not_block_weighting(tmp_path):
    """The rack has no accuracy figure. If it counted, one missing figure would force equal weights
    on a and b. It does not count, so a and b stay weighted (9/13 and 4/13)."""
    h = _hub(tmp_path, weigh=True)
    _node(h, "a", "environment", [T], [20.0])
    _node(h, "b", "environment", [T], [21.0])
    _node(h, "rack", "equipment", [T], [60.0])
    set_calibration(h.config, "a.temperature", "", 0.0, True, accuracy=0.1)
    set_calibration(h.config, "b.temperature", "", 0.0, True, accuracy=0.15)
    h.compute_site()
    assert h.average_info["temperature"]["weighted"] is True
    assert h.entities["site.temperature"].value == pytest.approx(20.0 + 4 / 13)
    h.recorder.close()


# ---------------------------------------------------- the per-sensor override
def test_sensor_override_both_ways(tmp_path):
    """Node role Environment, one sensor overridden to Equipment: it leaves the average (a = 20.0
    alone). Node role Equipment, one sensor overridden to Environment: it joins (20 and 30 give 25)."""
    h = _hub(tmp_path)
    _node(h, "a", "environment", [T], [20.0])
    _node(h, "b", "environment", [T], [30.0])
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(25.0)
    set_calibration(h.config, "b.temperature", "", 0.0, True, role="equipment")
    assert h.role_of(h.entities["b.temperature"]) == "equipment"
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(20.0)

    _node(h, "rack", "equipment", [T, H], [60.0, 70.0])
    set_calibration(h.config, "rack.temperature", "", 0.0, True, role="environment")
    assert h.role_of(h.entities["rack.temperature"]) == "environment"
    assert h.role_of(h.entities["rack.humidity"]) == "equipment"     # follows its node
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(40.0)    # (20 + 60) / 2
    assert h.entities["site.humidity"].value is None
    # "" goes back to following the node
    set_calibration(h.config, "rack.temperature", "", 0.0, True, role="")
    assert h.role_of(h.entities["rack.temperature"]) == "equipment"
    h.recorder.close()


def test_override_follows_the_board_and_is_kept_when_not_sent():
    cfg = Config()
    key, new = "mac:025e00000009/temperature", "mac:025e0000000a"
    set_calibration(cfg, "x.temperature", key, 0.5, True, role="equipment")
    set_calibration(cfg, "x.temperature", key, 0.7, True)            # an old client: no role sent
    assert cfg.calibrations[key].role == "equipment"
    assert copy_node(cfg, "mac:025e00000009", new) == 1
    assert cfg.calibrations[f"{new}/temperature"].role == "equipment"
    cfg.entities["y.temperature"] = EntitySettings(role="equipment")
    move_legacy(cfg, "y", "mac:025e0000000b")
    assert cfg.calibrations["mac:025e0000000b/temperature"].role == "equipment"
    set_calibration(cfg, "z.temperature", "", 0.0, True, role="equipment")      # no hardware key
    set_calibration(cfg, "z.temperature", "", 1.0, True)
    assert cfg.entities["z.temperature"].role == "equipment"


def test_changing_a_role_restarts_smoothing(tmp_path):
    """A role change is a setting: the smoothed average starts afresh (no easing in)."""
    h = _hub(tmp_path)
    h.config.site.smoothing_tau_s = 600
    _node(h, "a", "environment", [T], [20.0])
    _node(h, "b", "environment", [T], [30.0])
    now = time.time()
    h.compute_site(now)
    set_calibration(h.config, "b.temperature", "", 0.0, True, role="equipment")
    h.compute_site(now + 1)
    assert h.entities["site.temperature"].value == pytest.approx(20.0)
    h.recorder.close()


def test_equipment_threshold_works_on_its_own_value(tmp_path):
    """A threshold on the rack's own temperature (above 45) fires at 46 whatever the room is at."""
    from stagewatch.core.config import Threshold
    h = _hub(tmp_path)
    _node(h, "a", "environment", [T], [20.0])
    _node(h, "rack", "equipment", [T], [46.0])
    h.config.thresholds = [Threshold(id="rack_hot", entity="rack.temperature", above=45.0, level=2)]
    h.tick()
    assert any(a["id"] == "threshold:rack_hot" or "rack_hot" in a["id"] for a in h.alarms.to_list())
    assert h.entities["site.temperature"].value == pytest.approx(20.0)
    h.recorder.close()


# ---------------------------------------------------- config
def test_old_configs_default_to_environment():
    cfg = Config.model_validate({"esphome_devices": [{"id": "foh", "host": "foh.local"}],
                                 "entities": {"foh.temperature": {"offset": 0.4}},
                                 "calibrations": {"mac:025e00000002/temperature": {"offset": 0.4}}})
    assert cfg.esphome_devices[0].role == "environment"
    assert cfg.entities["foh.temperature"].role == ""
    assert cfg.calibrations["mac:025e00000002/temperature"].role == ""


def test_models_validate_strictly():
    with pytest.raises(ValidationError):
        EsphomeDeviceConfig(id="x", host="h", role="rack")
    with pytest.raises(ValidationError):
        Calibration(role="rack")
    with pytest.raises(ValidationError):
        EntitySettings(role="rack")


def test_unreadable_roles_in_a_file_fall_back_without_losing_anything(caplog):
    """A role from a newer release: the node counts as Environment, the sensor follows its node,
    the offset next to it stays, and the log gives counts only, never the value."""
    raw = {"esphome_devices": [{"id": "x", "host": "h", "name": "Keep me", "role": "mystery-role"}],
           "entities": {"x.temperature": {"offset": 0.7, "role": "odd-value"}},
           "calibrations": {"mac:025e00000002/humidity": {"offset": 2.0, "role": "weird-word"}}}
    with caplog.at_level("WARNING"):
        cfg = Config.model_validate(raw)
    assert cfg.esphome_devices[0].role == "environment" and cfg.esphome_devices[0].name == "Keep me"
    assert cfg.entities["x.temperature"].offset == 0.7 and cfg.entities["x.temperature"].role == ""
    assert cfg.calibrations["mac:025e00000002/humidity"].offset == 2.0
    for word in ("mystery-role", "odd-value", "weird-word"):
        assert word not in caplog.text


def test_round_trip_and_an_older_build_averages_everything_again(tmp_path):
    """The role is saved, reloads, and an older build (which has no such field) ignores the key
    and so reads the node as an ordinary sensor node: it would average everything again."""
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.esphome_devices.append(EsphomeDeviceConfig(id="rack", host="rack.local", role="equipment"))
    set_calibration(store.config, "rack.temperature", "mac:025e00000002/temperature", 0.5, True, role="environment")
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load()
    assert again.esphome_devices[0].role == "equipment"
    assert again.calibrations["mac:025e00000002/temperature"].role == "environment"
    from pydantic import BaseModel, ConfigDict

    class OldNode(BaseModel):
        model_config = ConfigDict(extra="ignore")
        id: str
        host: str
        name: str = ""

    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    old = OldNode.model_validate(raw["esphome_devices"][0])
    assert old.id == "rack" and not hasattr(old, "role")


def test_schema_version_not_bumped_for_this_change():
    """Additive fields with defaults only. If this fails, the schema-change rules apply."""
    from stagewatch.version import CONFIG_SCHEMA_VERSION
    assert CONFIG_SCHEMA_VERSION == 2


def test_salvage_keeps_nodes_with_roles(tmp_path):
    """A config with one broken section is salvaged; the nodes (with their roles) survive."""
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 2,
                                 "esphome_devices": [{"id": "rack", "host": "rack.local", "role": "equipment"}],
                                 "site": {"smoothing_tau_s": "not a number"}}), encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert cfg.esphome_devices and cfg.esphome_devices[0].role == "equipment"


# ---------------------------------------------------- the API and the public payload
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def _admin(client):
    assert client.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def test_emulate_has_an_equipment_node_that_never_moves_the_site(client):
    hub = client.hub
    rack = hub.entities["sim_rack_1.temperature"]
    assert hub.role_of(rack) == "equipment" and hub.devices["sim_rack_1"].role == "equipment"
    hub.devices["sim_rack_1"].role = "equipment"
    for _ in range(3):
        hub.update_state(rack.id, 80.0)
        hub.compute_site()
    # the rack is 80 degC; the room sensors are around 21-24 degC
    assert hub.entities["site.temperature"].value < 30.0
    assert hub.site_meta["sensors"]["temperature"] == 3


def test_public_payload_has_role_only_for_equipment(client):
    snap = client.get("/api/snapshot").json()
    ents = {e["id"]: e for e in snap["entities"]}
    assert ents["sim_rack_1.temperature"]["role"] == "equipment"
    for eid, e in ents.items():
        if not eid.startswith("sim_rack_1"):
            assert "role" not in e, eid
    assert "role" not in ents["site.temperature"]
    # nothing else about roles or calibration leaks: no device role, no override fields
    for d in snap["devices"]:
        assert "role" not in d
    text = json.dumps(snap)
    for word in ("role_now", "include_in_average", "accuracy", "average_info"):
        assert word not in text
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_text()
    assert "role_now" not in first and first.count('"role"') == len(
        [e for e in ents.values() if e.get("role")])


def test_role_via_api_and_live_update(client):
    """PATCH a node to Equipment: its sensors carry the role at once (a states event reaches the
    open screens without a reload), and its temperature leaves the average."""
    _admin(client)
    events = []
    client.hub.bus.subscribe("entity", lambda topic, payload: events.append(payload.id))
    assert client.patch("/api/admin/devices/sim_foh", json={"role": "equipment"}).status_code == 200
    assert "sim_foh.temperature" in events
    e = client.hub.entities["sim_foh.temperature"]
    assert client.hub.entity_dict(e, time.time(), 60)["role"] == "equipment"
    state = client.get("/api/admin/state").json()["hardware"]
    assert state["devices"]["sim_foh"]["role"] == "equipment"
    assert state["settings"]["sim_foh.temperature"]["role_now"] == "equipment"
    client.hub.compute_site()
    assert client.hub.site_meta["sensors"]["temperature"] == 2
    assert client.patch("/api/admin/devices/sim_foh", json={"role": "environment"}).status_code == 200
    assert "role" not in client.hub.entity_dict(e, time.time(), 60)
    assert client.patch("/api/admin/devices/sim_foh", json={"role": "banana"}).status_code == 422


def test_entity_role_override_via_api(client):
    _admin(client)
    body = {"offset": 0.0, "include_in_average": True}
    assert client.put("/api/admin/entities/sim_foh.temperature", json={**body, "role": "equipment"}).status_code == 200
    s = client.get("/api/admin/state").json()["hardware"]["settings"]["sim_foh.temperature"]
    assert s["role"] == "equipment" and s["role_now"] == "equipment"
    # a save that does not send a role keeps it
    assert client.put("/api/admin/entities/sim_foh.temperature", json=body).status_code == 200
    assert client.get("/api/admin/state").json()["hardware"]["settings"]["sim_foh.temperature"]["role"] == "equipment"
    assert client.put("/api/admin/entities/sim_foh.temperature", json={**body, "role": ""}).status_code == 200
    assert client.get("/api/admin/state").json()["hardware"]["settings"]["sim_foh.temperature"]["role_now"] == "environment"
    r = client.put("/api/admin/entities/sim_foh.temperature", json={**body, "role": "secret-word"})
    assert r.status_code == 422 and "secret-word" not in r.text
    assert client.put("/api/admin/entities/site.temperature", json={**body, "role": "equipment"}).status_code == 422


def test_role_needs_admin_and_same_origin(client):
    assert client.patch("/api/admin/devices/sim_foh", json={"role": "equipment"}).status_code == 401
    _admin(client)
    r = client.patch("/api/admin/devices/sim_foh", json={"role": "equipment"}, headers={"Origin": "http://evil.example"})
    assert r.status_code in (401, 403)
    assert client.hub.devices["sim_foh"].role == "environment"


def test_adopt_takes_a_role_and_defaults_to_environment(client):
    _admin(client)
    r = client.post("/api/admin/esphome/adopt", json={"host": "rack.local", "name": "Amp rack", "role": "equipment"})
    assert r.status_code == 200
    r2 = client.post("/api/admin/esphome/adopt", json={"host": "desk.local", "name": "Desk"})
    assert r2.status_code == 200
    nodes = {c.id: c.role for c in client.hub.config.esphome_devices}
    assert nodes == {"amp_rack": "equipment", "desk": "environment"}
    assert client.post("/api/admin/esphome/adopt", json={"host": "x.local", "role": "banana"}).status_code == 422


# ---------------------------------------------------- the Equipment card and static files
def test_equipment_card_is_known_but_never_on_by_default():
    assert "equipment" in KNOWN_CARDS
    for layout in ("tablet", "phone", "wall"):
        assert "equipment" not in default_cards(layout)


def test_equipment_card_round_trips_through_the_dashboards_api(client):
    _admin(client)
    dashboards = client.get("/api/admin/state").json()["config"]["dashboards"]
    dashboards[0]["cards"] = [*dashboards[0]["cards"], "equipment"]
    assert client.put("/api/admin/dashboards", json=dashboards).status_code == 200
    again = client.get("/api/admin/state").json()["config"]["dashboards"]
    assert "equipment" in again[0]["cards"]


def test_equipment_card_wiring_and_safe_rendering():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    admin = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert 'data-card="equipment"' in html and "hidden" in html.split('data-card="equipment"')[1].split(">")[0]
    assert re.search(r"equipment:\s*\{\s*el: cardEl\(\"equipment\"\)", js)
    assert re.search(r"equipment:\s*\[\"Equipment\"", admin)
    body = js.split("function renderEquipment")[1].split("// ---------------------------------------------------------")[0]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert banned not in body
    assert "SW.isEquipment" in js and "SW.isEquipment" in (STATIC / "common.js").read_text(encoding="utf-8")


def test_chart_and_tables_leave_equipment_out_by_default():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    chart = js.split("function chartEntities")[1].split("function updateChartSeries")[0]
    assert "!SW.isEquipment(e)" in chart
    assert '"Equipment"' in js.split("function renderSensors")[1].split("function renderMarkers")[0]
    assert "Equipment readings are never averaged" in (STATIC / "admin.js").read_text(encoding="utf-8")
