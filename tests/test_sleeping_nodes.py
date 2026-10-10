"""Nodes that sleep between readings: "Sleeping" instead of stale or offline while inside the limit.

The limit is 2.5 wake intervals rounded up to whole minutes: 5 min -> 12.5 -> 13 min = 780 s;
1 min -> 2.5 -> 3 min = 180 s. Ages are set by moving the stored times back, never by waiting.
"""

import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, ValidationError

from stagewatch.core.config import Config, ConfigStore, EsphomeDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app

T = Kind.TEMPERATURE
LIMIT_5 = 13 * 60.0


def _hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    return h


def _node(h, dev="n1", minutes=5, value=20.0, status=Status.OK, role="environment"):
    h.register_device(Device(dev, dev, "esphome", status=status, role=role, sleep_minutes=minutes))
    h.register_entity(Entity(f"{dev}.temperature", dev, "Temp", T))
    if value is not None:
        h.update_state(f"{dev}.temperature", value)
    return h.devices[dev]


def _age(h, dev, seconds):
    """Make the node's last reading `seconds` old."""
    t = time.time() - seconds
    h.devices[dev].last_reading = t
    for e in h.entities.values():
        if e.device_id == dev:
            e.updated = t


def _offline_alarm(h, dev):
    return f"device:{dev}" in h.alarms.active


# ------------------------------------------------------------------ the limit
def test_limit_is_two_and_a_half_intervals_rounded_up_to_whole_minutes():
    assert Device("a", "A", "x", sleep_minutes=5).sleep_limit_s == 780.0
    assert Device("a", "A", "x", sleep_minutes=1).sleep_limit_s == 180.0
    assert Device("a", "A", "x", sleep_minutes=10).sleep_limit_s == 1500.0
    assert Device("a", "A", "x").sleep_limit_s == 0.0


# ------------------------------------------------------------------ unchanged when off
def test_a_node_that_does_not_sleep_behaves_as_before(tmp_path):
    h = _hub(tmp_path)
    d = _node(h, minutes=0)
    h.set_device_status("n1", Status.MISSING, "Connection lost")
    assert not d.is_sleeping(time.time())
    assert _offline_alarm(h, "n1")
    assert "sleeps" not in d.to_dict() and "sleeping" not in d.to_dict()
    ent = h.entity_dict(h.entities["n1.temperature"], time.time(), 60.0)
    assert "sleeping" not in ent
    _age(h, "n1", 90)
    assert h.entity_dict(h.entities["n1.temperature"], time.time(), 60.0)["stale"] is True


# ------------------------------------------------------------------ sleeping
def test_inside_the_limit_it_is_sleeping_with_no_alarm_and_the_last_value_kept(tmp_path):
    h = _hub(tmp_path)
    d = _node(h, value=21.5)
    _age(h, "n1", 600)                       # 10 min old, limit 13 min
    h.set_device_status("n1", Status.MISSING, "Connection lost")
    h.tick()
    assert d.is_sleeping(time.time()) and not _offline_alarm(h, "n1")
    out = d.to_dict()
    assert out["sleeps"] is True and out["sleeping"] is True and out["status"] == "missing"
    assert out["last_reading"] == pytest.approx(time.time() - 600, abs=5)
    ent = h.entity_dict(h.entities["n1.temperature"], time.time(), 60.0)
    assert ent["stale"] is False and ent["sleeping"] is True and ent["value"] == 21.5


def test_past_the_limit_it_is_stale_and_offline_as_normal(tmp_path):
    h = _hub(tmp_path)
    d = _node(h)
    _age(h, "n1", 600)
    h.set_device_status("n1", Status.MISSING, "Connection lost")
    h.tick()
    assert not _offline_alarm(h, "n1")
    _age(h, "n1", LIMIT_5 + 5)               # just over 13 min
    h.tick()                                 # no event arrived: the tick notices
    assert not d.is_sleeping(time.time()) and _offline_alarm(h, "n1")
    assert d.to_dict()["sleeping"] is False
    ent = h.entity_dict(h.entities["n1.temperature"], time.time(), 60.0)
    assert ent["stale"] is True and "sleeping" not in ent


def test_waking_clears_sleeping_and_a_late_node_alarm(tmp_path):
    h = _hub(tmp_path)
    d = _node(h)
    _age(h, "n1", LIMIT_5 + 5)
    h.set_device_status("n1", Status.MISSING, "Connection lost")
    h.tick()
    assert _offline_alarm(h, "n1")
    h.update_state("n1.temperature", 19.0)
    h.set_device_status("n1", Status.OK)
    assert not _offline_alarm(h, "n1") and not d.is_sleeping(time.time())


def test_nothing_is_assumed_without_a_real_reading_or_when_faulty(tmp_path):
    h = _hub(tmp_path)
    d = _node(h, value=None)                 # never reported
    h.set_device_status("n1", Status.MISSING, "Connection lost")
    assert not d.is_sleeping(time.time()) and _offline_alarm(h, "n1")
    assert d.to_dict()["last_reading"] is None
    (tmp_path / "b").mkdir()
    h2 = _hub(tmp_path / "b")
    d2 = _node(h2)
    h2.set_device_status("n1", Status.FAULT, "Wrong board")
    assert not d2.is_sleeping(time.time()) and _offline_alarm(h2, "n1")


def test_a_sleeping_value_counts_in_the_average_only_while_inside_the_limit(tmp_path):
    """Two environment nodes at 20.0 and 22.0 degC average to 21.0 while the sleeper is current;
    once its reading is past the limit only the other node, 22.0 degC, is left."""
    h = _hub(tmp_path)
    _node(h, "n1", 5, 20.0)
    _node(h, "n2", 0, 22.0)
    _age(h, "n1", 600)
    h.set_device_status("n1", Status.MISSING, "Connection lost")
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(21.0)
    _age(h, "n1", LIMIT_5 + 5)
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(22.0)


def test_equipment_is_still_never_averaged_and_can_sleep(tmp_path):
    h = _hub(tmp_path)
    _node(h, "rack", 5, 60.0, role="equipment")
    _node(h, "n2", 0, 22.0)
    _age(h, "rack", 600)
    h.set_device_status("rack", Status.MISSING, "Connection lost")
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(22.0)
    assert h.entity_dict(h.entities["rack.temperature"], time.time(), 60.0)["sleeping"] is True


def test_changing_the_setting_takes_effect_at_once(tmp_path):
    h = _hub(tmp_path)
    d = _node(h, minutes=0)
    _age(h, "n1", 300)
    h.set_device_status("n1", Status.MISSING, "Connection lost")
    assert _offline_alarm(h, "n1")
    h.set_node_sleep("n1", 5)
    assert d.is_sleeping(time.time()) and not _offline_alarm(h, "n1")
    h.set_node_sleep("n1", 0)
    assert not d.is_sleeping(time.time()) and _offline_alarm(h, "n1")


# ------------------------------------------------------------------ config
def test_config_default_round_trip_and_range(tmp_path):
    assert EsphomeDeviceConfig(id="a", host="a.local").sleep_minutes == 0
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.esphome_devices.append(EsphomeDeviceConfig(id="a", host="a.local", sleep_minutes=5))
    store.save()
    assert ConfigStore(tmp_path / "config.yaml").load().esphome_devices[0].sleep_minutes == 5
    with pytest.raises(ValidationError):
        EsphomeDeviceConfig(id="a", host="a.local", sleep_minutes=-1)
    with pytest.raises(ValidationError):
        EsphomeDeviceConfig(id="a", host="a.local", sleep_minutes=5000)


def test_unreadable_sleep_values_in_a_file_count_as_off(caplog):
    raw = {"esphome_devices": [{"id": "a", "host": "h", "name": "Keep me", "sleep_minutes": "odd-text"},
                               {"id": "b", "host": "h2", "sleep_minutes": -4},
                               {"id": "c", "host": "h3", "sleep_minutes": 7}]}
    with caplog.at_level("WARNING"):
        cfg = Config.model_validate(raw)
    assert [c.sleep_minutes for c in cfg.esphome_devices] == [0, 0, 7]
    assert cfg.esphome_devices[0].name == "Keep me"
    assert "odd-text" not in caplog.text


def test_an_older_build_ignores_the_key_and_the_schema_is_not_bumped(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.esphome_devices.append(EsphomeDeviceConfig(id="a", host="a.local", sleep_minutes=5))
    store.save()

    class OldNode(BaseModel):
        model_config = ConfigDict(extra="ignore")
        id: str
        host: str

    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    old = OldNode.model_validate(raw["esphome_devices"][0])
    assert old.id == "a" and not hasattr(old, "sleep_minutes")
    from stagewatch.version import CONFIG_SCHEMA_VERSION
    assert CONFIG_SCHEMA_VERSION == 2


def test_salvage_keeps_a_sleeping_node(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 2,
                                 "esphome_devices": [{"id": "a", "host": "a.local", "sleep_minutes": 5}],
                                 "site": {"smoothing_tau_s": "not a number"}}), encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert cfg.esphome_devices and cfg.esphome_devices[0].sleep_minutes == 5


# ------------------------------------------------------------------ API and public payload
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def test_admin_can_set_the_interval_and_the_public_payload_stays_plain(client):
    hub = client.hub
    dev = next(d for d in hub.devices.values() if d.id != "site" and d.category == "sensor")
    r = client.patch(f"/api/admin/devices/{dev.id}", json={"sleep_minutes": 5})
    assert r.status_code == 200 and dev.sleep_minutes == 5
    assert client.get("/api/admin/state").json()["hardware"]["devices"][dev.id]["sleep_minutes"] == 5
    snap = client.get("/api/snapshot").json()
    row = next(d for d in snap["devices"] if d["id"] == dev.id)
    assert row["sleeps"] is True and row["sleeping"] is False
    assert "sleep_minutes" not in json.dumps(snap)   # the setting itself stays admin only
    assert client.patch(f"/api/admin/devices/{dev.id}", json={"sleep_minutes": -1}).status_code == 422
    assert client.patch(f"/api/admin/devices/{dev.id}", json={"sleep_minutes": 99999}).status_code == 422
    assert client.patch(f"/api/admin/devices/{dev.id}", json={"sleep_minutes": 0}).status_code == 200
    snap = client.get("/api/snapshot").json()
    assert "sleeps" not in next(d for d in snap["devices"] if d["id"] == dev.id)


# ------------------------------------------------------------------ iot_class on manifests
def test_iot_class_on_manifests_and_plain_words_in_admin():
    from pathlib import Path

    from stagewatch.integrations import osc_out
    from stagewatch.integrations.esphome import MANIFEST as esphome_m
    from stagewatch.integrations.globcon import MANIFEST as globcon_m
    from stagewatch.integrations.ontime import MANIFEST as ontime_m
    from stagewatch.integrations.smaart import MANIFEST as smaart_m

    for m in (esphome_m, ontime_m, smaart_m, globcon_m):
        assert m.iot_class == "local_push", m.domain
    assert osc_out.MANIFEST.iot_class is None      # output only: left unset
    js = (Path(__file__).resolve().parent.parent / "src/stagewatch/web/static/admin.js").read_text(encoding="utf-8")
    for words in ("Local, live push", "Local, checks every few seconds", "Uses the internet"):
        assert words in js
