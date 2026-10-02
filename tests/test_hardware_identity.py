"""Hardware identity and calibration (plan §1.9, WP10a): calibration follows the board's MAC."""

from __future__ import annotations

import logging

import pytest
import yaml
from aioesphomeapi import (
    BinarySensorInfo,
    DeviceInfo,
    InvalidAuthAPIError,
    InvalidEncryptionKeyAPIError,
    RequiresEncryptionAPIError,
    SensorInfo,
    SensorState,
)
from fastapi.testclient import TestClient

from stagewatch.core import calibration as cal
from stagewatch.core.config import Calibration, CalibrationEntry, Config, EntitySettings, EsphomeDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.integrations.esphome import EsphomeIntegration, _NodeConnection
from stagewatch.integrations.esphome.emulate import EmulatedNode
from stagewatch.web.server import create_app

# Locally administered (02:...) test MACs only: never real hardware.
MAC_A = "02:5e:00:00:aa:01"
MAC_B = "02:5e:00:00:bb:02"
A = "025e0000aa01"
B = "025e0000bb02"
T0 = 1_790_952_000.0  # Fri 2 Oct 2026, 14:40 UTC, fixed so history dates are predictable


# ------------------------------------------------------------------ pure identity
@pytest.mark.parametrize("raw", ["02:5E:00:00:AA:01", "02-5e-00-00-aa-01", "025e.0000.aa01",
                                 "025E0000AA01", " 02:5e:00:00:aa:01 "])
def test_normalise_mac_accepts_common_forms(raw):
    assert cal.normalise_mac(raw) == A


@pytest.mark.parametrize("raw", ["", "02:5e:00:00:aa", "02:5e:00:00:aa:01:ff", "zz5e0000aa01",
                                 None, 12, "02:5e:00:00:aa:0g"])
def test_normalise_mac_refuses_anything_else(raw):
    assert cal.normalise_mac(raw) is None


def test_keys():
    assert cal.node_key(MAC_A) == f"mac:{A}"
    assert cal.format_mac(A) == "02:5e:00:00:aa:01"
    assert cal.sensor_key(f"mac:{A}", "temperature") == f"mac:{A}/temperature"
    assert cal.sensor_key(f"mac:{A}", "Bad-Id") == ""  # never write a key the config would refuse
    assert cal.fallback_key("foh", "humidity") == "dev:foh/humidity"
    with pytest.raises(ValueError):
        cal.node_key("not-a-mac")


def test_check_identity_rules():
    known = [("foh", A), ("stage_l", "")]
    assert cal.check_identity("foh", A, MAC_A, known).action == "ok"
    first = cal.check_identity("stage_l", "", MAC_B, known)
    assert (first.action, first.mac) == ("first", B)
    diff = cal.check_identity("foh", A, MAC_B, known)
    assert diff.action == "fault" and diff.reason == "different" and diff.mac == B
    assert diff.detail == ("different hardware at this address "
                           "(expected 02:5e:00:00:aa:01, found 02:5e:00:00:bb:02)")
    dup = cal.check_identity("stage_l", "", MAC_A, known)
    assert dup.action == "fault" and dup.detail == "same board as 'foh'" and dup.other_id == "foh"
    assert cal.check_identity("stage_l", "", "", known).action == "no_mac"
    unreadable = cal.check_identity("foh", A, "", known)
    assert unreadable.action == "fault" and unreadable.reason == "unreadable"


# ------------------------------------------------------------- pure calibration
def test_calibration_for_prefers_hardware_then_legacy_then_defaults():
    cfg = Config()
    key = f"mac:{A}/temperature"
    assert cal.calibration_for(cfg, "foh.temperature", key).offset == 0.0
    cfg.entities["foh.temperature"] = EntitySettings(offset=-0.4, include_in_average=False)
    got = cal.calibration_for(cfg, "foh.temperature", key)
    assert (got.offset, got.include_in_average) == (-0.4, False)
    cfg.calibrations[key] = Calibration(offset=0.3)
    assert cal.calibration_for(cfg, "foh.temperature", key).offset == 0.3
    assert cal.calibration_for(cfg, "foh.temperature", "").offset == -0.4  # no hardware key: legacy


def test_move_legacy_moves_with_a_migrated_entry():
    cfg = Config(entities={"foh.temperature": EntitySettings(offset=-0.4),
                           "foh.humidity": EntitySettings(offset=2.5, include_in_average=False),
                           "foh_2.temperature": EntitySettings(offset=9.0),  # another device
                           "stage_l.humidity": EntitySettings(offset=1.0)})
    assert cal.move_legacy(cfg, "foh", f"mac:{A}", now=T0) == 2
    assert set(cfg.entities) == {"foh_2.temperature", "stage_l.humidity"}
    t = cfg.calibrations[f"mac:{A}/temperature"]
    h = cfg.calibrations[f"mac:{A}/humidity"]
    assert t.offset == -0.4 and t.include_in_average is True
    assert h.offset == 2.5 and h.include_in_average is False
    assert [(e.method, e.offset, e.date) for e in t.history] == [("migrated", -0.4, "2026-10-02T14:40:00Z")]
    assert cal.move_legacy(cfg, "foh", f"mac:{A}", now=T0) == 0  # nothing left: no second save
    Config.model_validate(cfg.model_dump(mode="json"))  # what was written loads again


def test_move_legacy_when_both_exist_keeps_the_hardware_record(caplog):
    key = f"mac:{A}/temperature"
    cfg = Config(entities={"foh.temperature": EntitySettings(offset=-7.25)},
                 calibrations={key: Calibration(offset=0.125)})
    with caplog.at_level(logging.INFO):
        assert cal.move_legacy(cfg, "foh", f"mac:{A}", now=T0) == 1
    assert cfg.entities == {}
    assert cfg.calibrations[key].offset == 0.125 and cfg.calibrations[key].history == []
    assert "foh.temperature" in caplog.text
    assert "7.25" not in caplog.text and "0.125" not in caplog.text  # logged without values


def test_copy_node_keeps_the_old_records():
    old, new = f"mac:{A}", f"mac:{B}"
    cfg = Config(calibrations={f"{old}/temperature": Calibration(offset=-0.4, history=[
        CalibrationEntry(offset=-0.4, date="2026-06-01T09:00:00Z", method="manual")])})
    assert cal.copy_node(cfg, old, new, now=T0) == 1
    moved = cfg.calibrations[f"{new}/temperature"]
    assert moved.offset == -0.4 and [e.method for e in moved.history] == ["moved", "manual"]
    assert f"{old}/temperature" in cfg.calibrations


def test_set_calibration_writes_the_hardware_record_with_history():
    key = f"mac:{A}/temperature"
    cfg = Config(entities={"foh.temperature": EntitySettings(offset=-0.4)})
    cal.set_calibration(cfg, "foh.temperature", key, -0.5, True, now=T0)
    assert "foh.temperature" not in cfg.entities
    assert cfg.calibrations[key].offset == -0.5
    assert [e.method for e in cfg.calibrations[key].history] == ["manual"]
    cal.set_calibration(cfg, "foh.temperature", key, -0.5, False, now=T0)  # offset unchanged
    assert len(cfg.calibrations[key].history) == 1 and not cfg.calibrations[key].include_in_average
    cal.set_calibration(cfg, "x.temperature", "", 1.0, True)  # no hardware key: legacy entry
    assert cfg.entities["x.temperature"].offset == 1.0


# ---------------------------------------------------------- offsets through the hub
def _hub_with_entity(tmp_path, hw_key):
    hub = Hub(tmp_path)
    hub.register_device(Device("foh", "FOH", "esphome", status=Status.OK))
    hub.register_entity(Entity("foh.temperature", "foh", "Temperature", Kind.TEMPERATURE, "°C", hw_key=hw_key))
    return hub


def test_offsets_apply_identically_before_and_after_the_move(tmp_path):
    hub = _hub_with_entity(tmp_path, f"mac:{A}/temperature")
    hub.config.site.smoothing_tau_s = 0
    hub.config.entities["foh.temperature"] = EntitySettings(offset=-0.4, include_in_average=False)
    hub.update_state("foh.temperature", 22.0)
    before = hub.entities["foh.temperature"].value
    before_inputs = hub._env_inputs(Kind.TEMPERATURE, hub.entities["foh.temperature"].updated)
    assert cal.move_legacy(hub.config, "foh", f"mac:{A}") == 1
    hub.update_state("foh.temperature", 22.0)
    assert hub.entities["foh.temperature"].value == before == pytest.approx(21.6)
    assert hub._env_inputs(Kind.TEMPERATURE, hub.entities["foh.temperature"].updated) == before_inputs == []
    hub.recorder.close()


def test_hw_key_is_never_in_the_public_entity_or_device(tmp_path):
    hub = _hub_with_entity(tmp_path, f"mac:{A}/temperature")
    hub.devices["foh"].hw_id = f"mac:{A}"
    snap = str(hub.snapshot())
    assert A not in snap and "hw_key" not in snap and "hw_id" not in snap
    hub.recorder.close()


# ------------------------------------------------------------ ESPHome connections
class FakeClient:
    """Stands in for aioesphomeapi.APIClient: DeviceInfo plus one sensor and one contact."""

    def __init__(self, mac: str) -> None:
        self.mac = mac
        self.subscribed = None

    async def device_info_and_list_entities(self):
        info = DeviceInfo(mac_address=self.mac, name="foh-node", friendly_name="FOH node",
                          manufacturer="Espressif", model="esp32dev")
        entities = [
            SensorInfo(object_id="temperature", key=1, name="Temperature", device_class="temperature",
                       unit_of_measurement="°C", accuracy_decimals=1),
            BinarySensorInfo(object_id="door", key=2, name="Door"),
        ]
        return info, entities, []

    def subscribe_states(self, cb):
        self.subscribed = cb

    async def disconnect(self):
        return None


async def _node(hub, cfg, mac):
    node = _NodeConnection(hub, cfg, None)
    node.client = FakeClient(mac)
    hub.register_device(Device(cfg.id, cfg.name or cfg.host, "esphome", status=Status.INITIALIZING))
    return node


def _saved(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))


async def test_first_connect_records_the_mac_and_moves_legacy_offsets(tmp_path):
    hub = Hub(tmp_path)
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local")
    hub.config.esphome_devices.append(cfg)
    hub.config.entities["foh.temperature"] = EntitySettings(offset=-0.4)
    hub.config.entities["stage_r.temperature"] = EntitySettings(offset=1.5)  # not connected: stays
    node = await _node(hub, cfg, MAC_A)
    await node._on_connect()
    assert cfg.mac == A and hub.devices["foh"].status == Status.OK
    assert hub.entities["foh.temperature"].hw_key == f"mac:{A}/temperature"
    assert hub.entities["foh.door"].hw_key == f"mac:{A}/door"
    assert hub.devices["foh"].hw_id == f"mac:{A}"
    saved = _saved(tmp_path)
    assert saved["esphome_devices"][0]["mac"] == A
    assert saved["calibrations"][f"mac:{A}/temperature"]["history"][0]["method"] == "migrated"
    assert set(saved["entities"]) == {"stage_r.temperature"}
    node.client.subscribed(SensorState(key=1, state=22.0))
    assert hub.entities["foh.temperature"].value == pytest.approx(21.6)
    hub.recorder.close()


async def test_same_mac_at_a_new_host_reattaches(tmp_path):
    """The board moved to a new address (or was deleted and re-adopted): its calibration follows."""
    hub = Hub(tmp_path)
    hub.config.calibrations[f"mac:{A}/temperature"] = Calibration(offset=-0.4)
    cfg = EsphomeDeviceConfig(id="foh", host="192.0.2.99", mac=A)
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_A)
    await node._on_connect()
    assert hub.devices["foh"].status == Status.OK and node.conflict == ""
    node.client.subscribed(SensorState(key=1, state=22.0))
    assert hub.entities["foh.temperature"].value == pytest.approx(21.6)

    # Deleted and re-adopted under a new id: the first connect records the MAC, offset reattaches.
    hub.config.esphome_devices = [EsphomeDeviceConfig(id="front_of_house", host="192.0.2.100")]
    node2 = await _node(hub, hub.config.esphome_devices[0], MAC_A)
    await node2._on_connect()
    node2.client.subscribed(SensorState(key=1, state=22.0))
    assert hub.entities["front_of_house.temperature"].value == pytest.approx(21.6)
    hub.recorder.close()


async def test_different_mac_is_fault_and_no_states_are_accepted(tmp_path):
    hub = Hub(tmp_path)
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local", mac=A)
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_B)
    await node._on_connect()
    dev = hub.devices["foh"]
    assert dev.status == Status.FAULT
    assert dev.status_detail == ("different hardware at this address "
                                 "(expected 02:5e:00:00:aa:01, found 02:5e:00:00:bb:02)")
    assert "foh.temperature" not in hub.entities and node.client.subscribed is None
    node._on_state(SensorState(key=1, state=22.0))  # even a stray state goes nowhere
    assert "foh.temperature" not in hub.entities
    assert cfg.mac == A  # the recorded board is kept until an admin decides
    assert any(a["id"] == "device:foh" for a in hub.alarms.to_list())  # the offline alarm is raised
    assert (node.seen_mac, node.conflict) == (B, "different")
    hub.recorder.close()


async def test_duplicate_board_is_fault(tmp_path):
    hub = Hub(tmp_path)
    hub.config.esphome_devices += [EsphomeDeviceConfig(id="foh", host="foh-node.local", mac=A),
                                   EsphomeDeviceConfig(id="foh_copy", host="192.0.2.20")]
    node = await _node(hub, hub.config.esphome_devices[1], MAC_A)
    await node._on_connect()
    assert hub.devices["foh_copy"].status == Status.FAULT
    assert hub.devices["foh_copy"].status_detail == "same board as 'foh'"
    assert hub.config.esphome_devices[1].mac == "" and node.client.subscribed is None
    assert not any(e.device_id == "foh_copy" for e in hub.entities.values())
    hub.recorder.close()


@pytest.mark.parametrize("err, detail", [
    (InvalidEncryptionKeyAPIError("x"), "encryption key missing or wrong"),
    (RequiresEncryptionAPIError("x"), "encryption key missing or wrong"),
    (InvalidAuthAPIError("x"), "node needs an API password (unsupported; use an encryption key)"),
])
async def test_auth_and_encryption_fault_details_are_unchanged(tmp_path, err, detail):
    """R20: these happen before DeviceInfo, so no MAC is read and the identity FAULT never
    overwrites them."""
    hub = Hub(tmp_path)
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local", mac=A)
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_B)
    await node._on_connect_error(err)
    assert (hub.devices["foh"].status, hub.devices["foh"].status_detail) == (Status.FAULT, detail)
    assert cfg.mac == A and node.conflict == ""
    hub.recorder.close()


async def test_resolve_new_hardware_and_move_calibration(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    hub.config.calibrations[f"mac:{A}/temperature"] = Calibration(offset=-0.4)
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local", mac=A)
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_B)
    esp._nodes["foh"] = node
    await node._on_connect()
    assert esp.hardware_conflicts() == {"foh": {"reason": "different", "expected": A, "found": B}}
    restarted = []

    async def fake_restart(c, detail):
        restarted.append(c.id)
    monkeypatch.setattr(esp, "_restart_node", fake_restart)
    await esp.resolve_hardware("foh", "move_calibration")
    assert cfg.mac == B and restarted == ["foh"]
    assert hub.config.calibrations[f"mac:{B}/temperature"].offset == -0.4
    assert hub.config.calibrations[f"mac:{B}/temperature"].history[0].method == "moved"
    assert f"mac:{A}/temperature" in hub.config.calibrations  # kept in case the old board returns
    with pytest.raises(LookupError):
        await esp.resolve_hardware("stage_l", "new_hardware")
    hub.recorder.close()


# -------------------------------------------------------------------- emulated
def test_emulated_macs_are_fixed_and_locally_administered(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    macs = [n.mac for n in EmulatedNode.defaults(hub)]
    assert macs == ["02:5e:00:00:00:01", "02:5e:00:00:00:02", "02:5e:00:00:00:03"]
    assert all(int(m.split(":")[0], 16) & 0b10 for m in macs)  # locally administered bit
    assert macs == [n.mac for n in EmulatedNode.defaults(hub)]
    hub.recorder.close()


V1_EMULATE_CONFIG = {
    "schema_version": 1,
    "site": {"name": "Upgrade test", "smoothing_tau_s": 0},
    "entities": {"sim_foh.temperature": {"offset": -0.4, "include_in_average": True},
                 "sim_stage_l.humidity": {"offset": 2.5, "include_in_average": False}},
}


async def _emulated_hub(data_dir):
    hub = Hub(data_dir, emulate=True)
    esp = EsphomeIntegration(hub, emulate=True)
    hub.add_integration(esp)
    await esp.start()
    return hub, esp


async def _stop(hub, esp):
    await esp.stop()
    hub.recorder.close()


async def test_upgraded_emulate_install_shows_identical_calibrated_values(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(V1_EMULATE_CONFIG), encoding="utf-8")
    raw = {"sim_foh.temperature": 22.0, "sim_stage_l.humidity": 50.0, "sim_delay_1.temperature": 20.0}
    # What 0.2.0 showed: raw + the entity-keyed offset.
    expected = {"sim_foh.temperature": 21.6, "sim_stage_l.humidity": 52.5, "sim_delay_1.temperature": 20.0}

    hub, esp = await _emulated_hub(tmp_path)
    for entity_id, value in raw.items():
        hub.update_state(entity_id, value)
        assert hub.entities[entity_id].value == pytest.approx(expected[entity_id])
    assert not hub.calibration_for(hub.entities["sim_stage_l.humidity"]).include_in_average
    assert hub.config.entities == {}
    saved = _saved(tmp_path)
    assert saved["entities"] == {}
    foh = saved["calibrations"]["mac:025e00000002/temperature"]
    assert foh["offset"] == -0.4 and foh["history"][0]["method"] == "migrated"
    assert saved["calibrations"]["mac:025e00000001/humidity"]["include_in_average"] is False
    assert hub.entities["sim_delay_1.temperature"].hw_key == "mac:025e00000003/temperature"
    await _stop(hub, esp)

    # Restart: the same hardware keys, the same values, and no second "migrated" entry.
    hub, esp = await _emulated_hub(tmp_path)
    for entity_id, value in raw.items():
        hub.update_state(entity_id, value)
        assert hub.entities[entity_id].value == pytest.approx(expected[entity_id])
    assert len(hub.config.calibrations["mac:025e00000002/temperature"].history) == 1
    assert _saved(tmp_path) == saved
    await _stop(hub, esp)


# ------------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    esp = EsphomeIntegration(hub, emulate=True)
    hub.add_integration(esp)
    with TestClient(create_app(hub)) as c:
        c.hub, c.esp = hub, esp
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def _discovered(name, mac, addr="192.0.2.30"):
    return {"name": name, "host": f"{name}.local", "address": addr, "port": 6053, "mac": mac,
            "friendly_name": name, "board": "esp32", "version": "", "encrypted": False}


def test_adopting_the_same_board_twice_is_refused(client):
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10", mac=A))
    client.esp.discovered["foh-node"] = _discovered("foh-node", A)
    for host in ("foh-node.local", "192.0.2.30"):
        r = client.post("/api/admin/esphome/adopt", json={"host": host, "id": "foh_again"})
        assert r.status_code == 409
        assert r.json()["detail"] == ("This node is already adopted as 'foh'. "
                                      "Change that device's address instead.")
    assert [c.id for c in client.hub.config.esphome_devices] == ["foh"]
    listed = client.get("/api/admin/state").json()["discovered"]
    assert listed[0]["adopted"] is True


def test_adopt_never_takes_a_mac_from_the_client(client):
    r = client.post("/api/admin/esphome/adopt", json={"host": "192.0.2.40", "id": "n1", "mac": A})
    assert r.status_code == 200
    assert client.hub.config.esphome_devices[0].mac == ""


def test_patch_host_and_port_keep_the_mac_and_reconnect(client, monkeypatch):
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10", mac=A))
    client.hub.register_device(Device("foh", "FOH", "esphome"))
    started = []

    async def fake_start(cfg):
        started.append((cfg.host, cfg.port))
    monkeypatch.setattr(client.esp, "emulate", False)
    monkeypatch.setattr(client.esp, "_start_node", fake_start)
    r = client.patch("/api/admin/devices/foh", json={"host": " foh-node.local ", "port": 6054})
    assert r.status_code == 200
    cfg = client.hub.config.esphome_devices[0]
    assert (cfg.host, cfg.port, cfg.mac) == ("foh-node.local", 6054, A)
    assert started == [("foh-node.local", 6054)]
    assert client.patch("/api/admin/devices/foh", json={"name": "FOH desk"}).status_code == 200
    assert len(started) == 1  # a rename doesn't reconnect


@pytest.mark.parametrize("body", [{"host": "bad host"}, {"host": ""}, {"host": "a\nb"},
                                  {"port": 0}, {"port": 70000}, {"host": "x" * 254}])
def test_patch_rejects_bad_addresses(client, body):
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10"))
    client.hub.register_device(Device("foh", "FOH", "esphome"))
    assert client.patch("/api/admin/devices/foh", json=body).status_code == 422
    assert client.hub.config.esphome_devices[0].host == "192.0.2.10"


def test_patch_host_of_an_emulated_node_is_refused(client):
    r = client.patch("/api/admin/devices/sim_foh", json={"host": "192.0.2.50"})
    assert r.status_code == 409


def test_patch_host_needs_admin_and_same_origin(client):
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10"))
    client.hub.register_device(Device("foh", "FOH", "esphome"))
    r = client.patch("/api/admin/devices/foh", json={"host": "192.0.2.11"},
                     headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    client.cookies.clear()
    assert client.patch("/api/admin/devices/foh", json={"host": "192.0.2.11"}).status_code == 401
    assert client.hub.config.esphome_devices[0].host == "192.0.2.10"


def test_admin_state_has_the_hardware_map_and_the_snapshot_does_not(client):
    state = client.get("/api/admin/state").json()
    assert state["hardware"]["entities"]["sim_foh.temperature"] == "mac:025e00000002/temperature"
    assert state["hardware"]["devices"]["sim_foh"] == {"hw_id": "mac:025e00000002", "conflict": None}
    snap = client.get("/api/snapshot").text
    assert "025e0000000" not in snap and "hw_key" not in snap and "hw_id" not in snap


def test_put_entity_writes_the_hardware_record(client):
    r = client.put("/api/admin/entities/sim_foh.temperature", json={"offset": -0.5, "include_in_average": True})
    assert r.status_code == 200
    rec = client.hub.config.calibrations["mac:025e00000002/temperature"]
    assert rec.offset == -0.5 and rec.history[0].method == "manual"
    assert "sim_foh.temperature" not in client.hub.config.entities
    client.hub.update_state("sim_foh.temperature", 22.0)
    assert client.hub.entities["sim_foh.temperature"].value == pytest.approx(21.5)
    # The admin card reads the settings that apply now, so a moved offset is never shown as 0.
    settings = client.get("/api/admin/state").json()["hardware"]["settings"]
    assert settings["sim_foh.temperature"] == {"offset": -0.5, "include_in_average": True}
    assert settings["sim_foh.humidity"] == {"offset": 0.0, "include_in_average": True}
    assert "site.temperature" not in settings
