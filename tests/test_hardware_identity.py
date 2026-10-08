"""Hardware identity and calibration (plan §1.9, WP10a): calibration follows the board's MAC."""

from __future__ import annotations

import asyncio
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
from stagewatch.web.server import RESOLVE_REFUSED, LiveFeed, create_app

# Locally administered (02:...) test MACs only: never real hardware.
MAC_A = "02:5e:00:00:aa:01"
MAC_B = "02:5e:00:00:bb:02"
A = "025e0000aa01"
B = "025e0000bb02"
T0 = 1_790_952_000.0  # Fri 2 Oct 2026, 14:40 UTC


def mac_forms(mac12: str) -> list[str]:
    """Every way a MAC could show up in text."""
    colon = ":".join(mac12[i:i + 2] for i in range(0, 12, 2))
    return [mac12, mac12.upper(), colon, colon.upper(), colon.replace(":", "-")]


# ------------------------------------------------------------------ pure identity
@pytest.mark.parametrize("raw", ["02:5E:00:00:AA:01", "02-5e-00-00-aa-01", "025e.0000.aa01",
                                 "025E0000AA01", " 02:5e:00:00:aa:01 "])
def test_normalise_mac_accepts_common_forms(raw):
    assert cal.normalise_mac(raw) == A
    assert EsphomeDeviceConfig(id="n", host="h", mac=raw).mac == A  # config accepts the same forms


@pytest.mark.parametrize("raw", ["02:5e:00:00:aa", "02:5e:00:00:aa:01:ff", "zz5e0000aa01",
                                 "02:5e:00:00:aa:0g", "02_5e_00_00_aa_01"])
def test_normalise_mac_refuses_anything_else(raw):
    assert cal.normalise_mac(raw) is None
    with pytest.raises(ValueError):
        EsphomeDeviceConfig(id="n", host="h", mac=raw)


def test_normalise_mac_non_strings():
    assert cal.normalise_mac(None) is None and cal.normalise_mac(12) is None
    assert EsphomeDeviceConfig(id="n", host="h", mac="").mac == ""


def test_keys():
    assert cal.node_key(MAC_A) == f"mac:{A}"
    assert cal.format_mac(A) == "02:5e:00:00:aa:01"
    assert cal.sensor_key(f"mac:{A}", "temperature") == f"mac:{A}/temperature"
    assert cal.sensor_key(f"mac:{A}", "Bad-Id") == ""  # never write a key the config would refuse
    assert cal.fallback_key("foh", "humidity") == "dev:foh/humidity"
    with pytest.raises(ValueError):
        cal.node_key("not-a-mac")


def test_check_identity_rules_and_public_text_has_no_mac():
    known = [("foh", A), ("stage_l", "")]
    assert cal.check_identity("foh", A, MAC_A, known).action == "ok"
    first = cal.check_identity("stage_l", "", MAC_B, known)
    assert (first.action, first.mac) == ("first", B)
    diff = cal.check_identity("foh", A, MAC_B, known)
    assert (diff.action, diff.reason, diff.mac, diff.expected) == ("fault", "different", B, A)
    assert diff.detail == "different hardware at this address"
    dup = cal.check_identity("stage_l", "", MAC_A, known)
    assert (dup.action, dup.other_id) == ("fault", "foh")
    assert dup.detail == "same board as another device"
    assert cal.check_identity("stage_l", "", "", known).action == "no_mac"
    unreadable = cal.check_identity("foh", A, "", known)
    assert unreadable.action == "fault" and unreadable.reason == "unreadable"
    for ident in (diff, dup, unreadable):
        assert not any(f in ident.detail for f in mac_forms(A) + mac_forms(B)) and "foh" not in ident.detail


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


def test_move_legacy_copies_and_keeps_the_rollback_mirror():
    cfg = Config(entities={"foh.temperature": EntitySettings(offset=-0.4),
                           "foh.humidity": EntitySettings(offset=2.5, include_in_average=False),
                           "foh_2.temperature": EntitySettings(offset=9.0),  # another device
                           "stage_l.humidity": EntitySettings(offset=1.0)})
    before = cfg.model_dump()["entities"]
    assert cal.move_legacy(cfg, "foh", f"mac:{A}", now=T0) == 2
    assert cfg.model_dump()["entities"] == before  # legacy kept as the mirror for 0.2.0
    t = cfg.calibrations[f"mac:{A}/temperature"]
    h = cfg.calibrations[f"mac:{A}/humidity"]
    assert t.offset == -0.4 and t.include_in_average is True
    assert h.offset == 2.5 and h.include_in_average is False
    assert [(e.method, e.offset, e.date) for e in t.history] == [("migrated", -0.4, "2026-10-02T14:40:00Z")]
    assert set(cfg.calibrations) == {f"mac:{A}/temperature", f"mac:{A}/humidity"}
    assert cal.move_legacy(cfg, "foh", f"mac:{A}", now=T0) == 0  # nothing new: no second save
    Config.model_validate(cfg.model_dump(mode="json"))  # what was written loads again


def test_move_legacy_when_both_exist_keeps_the_hardware_record(caplog):
    key = f"mac:{A}/temperature"
    cfg = Config(entities={"foh.temperature": EntitySettings(offset=-7.25)},
                 calibrations={key: Calibration(offset=0.125, include_in_average=False)})
    with caplog.at_level(logging.INFO):
        assert cal.move_legacy(cfg, "foh", f"mac:{A}", now=T0) == 1
    assert cfg.calibrations[key].offset == 0.125 and cfg.calibrations[key].history == []
    # The mirror follows the hardware record, so 0.2.0 would show what 0.3.0 shows.
    assert cfg.entities["foh.temperature"] == EntitySettings(offset=0.125, include_in_average=False)
    assert "foh.temperature" in caplog.text
    assert "7.25" not in caplog.text and "0.125" not in caplog.text  # logged without values
    assert cal.move_legacy(cfg, "foh", f"mac:{A}", now=T0) == 0


def test_copy_and_drop_node():
    old, new = f"mac:{A}", f"mac:{B}"
    cfg = Config(calibrations={f"{old}/temperature": Calibration(offset=-0.4, history=[
        CalibrationEntry(offset=-0.4, date="2026-06-01T09:00:00Z", method="manual")])})
    assert cal.copy_node(cfg, old, new, now=T0) == 1
    moved = cfg.calibrations[f"{new}/temperature"]
    assert moved.offset == -0.4 and [e.method for e in moved.history] == ["moved", "manual"]
    assert f"{old}/temperature" in cfg.calibrations
    assert cal.drop_node(cfg, new) == 1 and f"{new}/temperature" not in cfg.calibrations


def test_set_calibration_writes_the_hardware_record_and_keeps_the_mirror_in_step():
    key = f"mac:{A}/temperature"
    cfg = Config(entities={"foh.temperature": EntitySettings(offset=-0.4)})
    cal.set_calibration(cfg, "foh.temperature", key, -0.5, True, now=T0)
    assert cfg.calibrations[key].offset == -0.5
    assert [e.method for e in cfg.calibrations[key].history] == ["manual"]
    assert cfg.entities["foh.temperature"] == EntitySettings(offset=-0.5)  # mirror updated
    cal.set_calibration(cfg, "foh.temperature", key, -0.5, False, now=T0)  # offset unchanged
    assert len(cfg.calibrations[key].history) == 1 and not cfg.calibrations[key].include_in_average
    assert cfg.entities["foh.temperature"].include_in_average is False
    cal.set_calibration(cfg, "foh.humidity", f"mac:{A}/humidity", 1.0, True)  # no legacy entry
    assert "foh.humidity" not in cfg.entities  # no mirror is created
    cal.set_calibration(cfg, "x.temperature", "", 1.0, True)  # no hardware key: legacy entry
    assert cfg.entities["x.temperature"].offset == 1.0


def test_known_board_check():
    node = f"mac:{A}"
    cfg = Config(calibrations={f"{node}/temperature": Calibration(offset=-0.4)})
    assert cal.known_board_needs_check(cfg, "foh", node)  # records, but not this device's
    cfg.entities["foh.temperature"] = EntitySettings(offset=-0.4)
    assert not cal.known_board_needs_check(cfg, "foh", node)  # its own mirrored settings
    cfg.entities["foh.temperature"] = EntitySettings(offset=-0.3)
    assert cal.known_board_needs_check(cfg, "foh", node)
    assert not cal.known_board_needs_check(Config(), "foh", node)  # no records at all


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
        info = DeviceInfo(mac_address=self.mac, name="node", friendly_name="Node",
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


async def test_first_connect_records_the_mac_and_copies_legacy_offsets(tmp_path):
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
    assert set(saved["entities"]) == {"foh.temperature", "stage_r.temperature"}  # mirror kept
    node.client.subscribed(SensorState(key=1, state=22.0))
    assert hub.entities["foh.temperature"].value == pytest.approx(21.6)
    hub.recorder.close()


async def test_upgrade_path_with_records_already_there_is_not_held(tmp_path):
    """Records that are this device's own mirrored settings (e.g. the MAC save was lost) are the
    normal upgrade path: no check needed."""
    hub = Hub(tmp_path)
    hub.config.entities["foh.temperature"] = EntitySettings(offset=-0.4)
    hub.config.calibrations[f"mac:{A}/temperature"] = Calibration(offset=-0.4)
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local")
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_A)
    await node._on_connect()
    assert hub.devices["foh"].status == Status.OK and cfg.mac == A and node.conflict is None
    hub.recorder.close()


async def test_same_mac_at_a_new_host_reattaches(tmp_path):
    """The board moved to a new address: its recorded MAC matches, so its calibration follows."""
    hub = Hub(tmp_path)
    hub.config.calibrations[f"mac:{A}/temperature"] = Calibration(offset=-0.4)
    cfg = EsphomeDeviceConfig(id="foh", host="192.0.2.99", mac=A)
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_A)
    await node._on_connect()
    assert hub.devices["foh"].status == Status.OK and node.conflict is None
    node.client.subscribed(SensorState(key=1, state=22.0))
    assert hub.entities["foh.temperature"].value == pytest.approx(21.6)
    hub.recorder.close()


async def test_known_board_under_a_new_name_is_held_until_checked(tmp_path, monkeypatch):
    """Deleted and re-adopted under a new name: its records aren't applied silently."""
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    hub.config.calibrations[f"mac:{A}/temperature"] = Calibration(offset=-0.4)
    cfg = EsphomeDeviceConfig(id="front_of_house", host="192.0.2.100")
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_A)
    esp._nodes[cfg.id] = node
    await node._on_connect()
    dev = hub.devices["front_of_house"]
    assert (dev.status, dev.status_detail) == (Status.FAULT, "board needs checking in Admin")
    assert cfg.mac == "" and node.client.subscribed is None
    assert "front_of_house.temperature" not in hub.entities
    assert esp.hardware_conflicts() == {"front_of_house": {
        "reason": "known_board", "expected": "", "found": A, "other": "", "records": 1}}

    async def no_restart(c, detail):
        return None
    monkeypatch.setattr(esp, "_restart_node", no_restart)
    await esp.resolve_hardware("front_of_house", "move_calibration")  # use the records
    assert cfg.mac == A and hub.config.calibrations[f"mac:{A}/temperature"].offset == -0.4
    node2 = await _node(hub, cfg, MAC_A)  # the reconnect
    await node2._on_connect()
    node2.client.subscribed(SensorState(key=1, state=22.0))
    assert hub.entities["front_of_house.temperature"].value == pytest.approx(21.6)
    hub.recorder.close()


async def test_known_board_start_fresh(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    hub.config.calibrations[f"mac:{A}/temperature"] = Calibration(offset=-0.4)
    cfg = EsphomeDeviceConfig(id="foh2", host="192.0.2.100")
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_A)
    esp._nodes[cfg.id] = node
    await node._on_connect()

    async def no_restart(c, detail):
        return None
    monkeypatch.setattr(esp, "_restart_node", no_restart)
    await esp.resolve_hardware("foh2", "new_hardware")
    assert cfg.mac == A and cal.records_of(hub.config, f"mac:{A}") == []
    node2 = await _node(hub, cfg, MAC_A)
    await node2._on_connect()
    node2.client.subscribed(SensorState(key=1, state=22.0))
    assert hub.entities["foh2.temperature"].value == pytest.approx(22.0)
    hub.recorder.close()


async def test_different_mac_is_fault_and_no_states_are_accepted(tmp_path):
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local", mac=A)
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_B)
    esp._nodes["foh"] = node
    await node._on_connect()
    dev = hub.devices["foh"]
    assert (dev.status, dev.status_detail) == (Status.FAULT, "different hardware at this address")
    assert "foh.temperature" not in hub.entities and node.client.subscribed is None
    node._on_state(SensorState(key=1, state=22.0))  # even a stray state goes nowhere
    assert "foh.temperature" not in hub.entities
    assert cfg.mac == A  # the recorded board is kept until an admin decides
    assert any(a["id"] == "device:foh" for a in hub.alarms.to_list())  # the offline alarm is raised
    assert esp.hardware_conflicts() == {"foh": {"reason": "different", "expected": A, "found": B,
                                                "other": "", "records": 0}}
    hub.recorder.close()


async def test_duplicate_board_is_fault(tmp_path):
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    hub.config.esphome_devices += [EsphomeDeviceConfig(id="foh", host="foh-node.local", mac=A),
                                   EsphomeDeviceConfig(id="foh_copy", host="192.0.2.20")]
    node = await _node(hub, hub.config.esphome_devices[1], MAC_A)
    esp._nodes["foh_copy"] = node
    await node._on_connect()
    assert hub.devices["foh_copy"].status == Status.FAULT
    assert hub.devices["foh_copy"].status_detail == "same board as another device"
    assert hub.config.esphome_devices[1].mac == "" and node.client.subscribed is None
    assert not any(e.device_id == "foh_copy" for e in hub.entities.values())
    assert esp.hardware_conflicts()["foh_copy"]["other"] == "foh"  # admin only
    for action in ("new_hardware", "move_calibration", "forget_mac"):
        with pytest.raises(LookupError):
            await esp.resolve_hardware("foh_copy", action)
    hub.recorder.close()


@pytest.mark.parametrize("reason", ["different", "duplicate"])
async def test_fault_never_puts_a_mac_or_other_id_in_public_text(tmp_path, reason):
    """Status text, the WS device and alarms messages, the snapshot and the alarm log."""
    hub = Hub(tmp_path)
    feed = LiveFeed(hub)
    q: asyncio.Queue = asyncio.Queue()
    feed.clients.add(q)
    if reason == "different":
        cfg = EsphomeDeviceConfig(id="desk", host="192.0.2.20", name="Desk", mac=A)
        hub.config.esphome_devices.append(cfg)
        found = MAC_B
    else:
        hub.config.esphome_devices.append(EsphomeDeviceConfig(id="other_board", host="192.0.2.21", mac=B))
        cfg = EsphomeDeviceConfig(id="desk", host="192.0.2.20", name="Desk")
        hub.config.esphome_devices.append(cfg)
        found = MAC_B
    node = await _node(hub, cfg, found)
    await node._on_connect()
    assert hub.devices["desk"].status == Status.FAULT
    messages = []
    while not q.empty():
        messages.append(q.get_nowait())
    assert {m["type"] for m in messages} >= {"device", "alarms"}
    public = str(messages) + str(hub.snapshot()) + str(hub.recorder.alarm_log())
    assert "Desk: fault" in public
    for form in mac_forms(A) + mac_forms(B):
        assert form not in public
    assert "other_board" not in public
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
    assert cfg.mac == A and node.conflict is None
    hub.recorder.close()


async def test_no_mac_clears_the_hardware_id(tmp_path):
    hub = Hub(tmp_path)
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local")
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, "")
    hub.devices["foh"].hw_id = f"mac:{A}"  # left over from earlier
    await node._on_connect()
    assert hub.devices["foh"].status == Status.OK and hub.devices["foh"].hw_id == ""
    assert hub.entities["foh.temperature"].hw_key == ""
    hub.recorder.close()


async def _faulted(tmp_path, cfg_mac, board_mac, calibrations=None, entities=None):
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    hub.config.calibrations.update(calibrations or {})
    hub.config.entities.update(entities or {})
    cfg = EsphomeDeviceConfig(id="foh", host="foh-node.local", mac=cfg_mac)
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, board_mac)
    esp._nodes["foh"] = node
    await node._on_connect()
    restarted = []

    async def fake_restart(c, detail):
        restarted.append(c.id)
    esp._restart_node = fake_restart
    return hub, esp, cfg, restarted


async def test_resolve_different_move_calibration(tmp_path):
    hub, esp, cfg, restarted = await _faulted(
        tmp_path, A, MAC_B, {f"mac:{A}/temperature": Calibration(offset=-0.4)},
        {"foh.temperature": EntitySettings(offset=-0.4)})
    await esp.resolve_hardware("foh", "move_calibration")
    assert cfg.mac == B and restarted == ["foh"]
    assert hub.config.calibrations[f"mac:{B}/temperature"].offset == -0.4
    assert hub.config.calibrations[f"mac:{B}/temperature"].history[0].method == "moved"
    assert f"mac:{A}/temperature" in hub.config.calibrations  # kept in case the old board returns
    assert hub.config.entities["foh.temperature"].offset == -0.4  # mirror still right
    hub.recorder.close()


async def test_resolve_different_new_hardware(tmp_path):
    hub, esp, cfg, restarted = await _faulted(
        tmp_path, A, MAC_B, {f"mac:{A}/temperature": Calibration(offset=-0.4)},
        {"foh.temperature": EntitySettings(offset=-0.4)})
    with pytest.raises(LookupError):
        await esp.resolve_hardware("foh", "forget_mac")  # not for this problem
    assert cfg.mac == A and restarted == []
    await esp.resolve_hardware("foh", "new_hardware")
    assert cfg.mac == B and restarted == ["foh"]
    assert f"mac:{B}/temperature" not in hub.config.calibrations
    assert "foh.temperature" not in hub.config.entities  # the old board's offsets don't carry over
    assert f"mac:{A}/temperature" in hub.config.calibrations
    hub.recorder.close()


async def test_resolve_unreadable_forget_mac(tmp_path):
    hub, esp, cfg, restarted = await _faulted(tmp_path, A, "")
    assert hub.devices["foh"].status_detail == "could not read the board's hardware address"
    for action in ("new_hardware", "move_calibration"):
        with pytest.raises(LookupError):
            await esp.resolve_hardware("foh", action)
    assert cfg.mac == A
    await esp.resolve_hardware("foh", "forget_mac")
    assert cfg.mac == "" and restarted == ["foh"]
    hub.recorder.close()


async def test_resolve_refuses_when_nothing_is_wrong(tmp_path):
    hub, esp, cfg, restarted = await _faulted(tmp_path, A, MAC_A)
    assert hub.devices["foh"].status == Status.OK
    for action in ("new_hardware", "move_calibration", "forget_mac"):
        with pytest.raises(LookupError):
            await esp.resolve_hardware("foh", action)
    with pytest.raises(LookupError):
        await esp.resolve_hardware("nope", "new_hardware")
    assert restarted == []
    hub.recorder.close()


# ------------------------------------------------------------------ concurrency
class StubNode:
    def __init__(self, host):
        self.host, self.stopped = host, False

    async def stop(self):
        await asyncio.sleep(0.01)
        self.stopped = True


def _esp_with_stubs(tmp_path):
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    cfg = EsphomeDeviceConfig(id="foh", host="192.0.2.10", mac=A)
    hub.config.esphome_devices.append(cfg)
    hub.register_device(Device("foh", "FOH", "esphome"))
    first = StubNode(cfg.host)
    esp._nodes["foh"] = first
    created = [first]

    async def fake_start(c):
        await asyncio.sleep(0.01)
        node = StubNode(c.host)
        created.append(node)
        esp._nodes[c.id] = node
    esp._start_node = fake_start
    return hub, esp, created


async def test_overlapping_address_changes_leave_one_connection(tmp_path):
    hub, esp, created = _esp_with_stubs(tmp_path)
    await asyncio.gather(esp.update("foh", None, None, "192.0.2.11", None),
                         esp.update("foh", None, None, "192.0.2.12", None))
    running = [n for n in created if not n.stopped]
    assert running == [esp._nodes["foh"]]
    assert esp.config_of("foh").host == running[0].host
    hub.recorder.close()


async def test_address_change_overlapping_a_delete_never_brings_it_back(tmp_path):
    hub, esp, created = _esp_with_stubs(tmp_path)
    await asyncio.gather(esp.update("foh", None, None, "192.0.2.11", None), esp.remove("foh"))
    assert "foh" not in esp._nodes and esp.config_of("foh") is None
    assert all(n.stopped for n in created)
    hub.recorder.close()


# -------------------------------------------------------------------- emulated
def test_emulated_macs_are_fixed_and_locally_administered(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    macs = [n.mac for n in EmulatedNode.defaults(hub)]
    assert macs == ["02:5e:00:00:00:01", "02:5e:00:00:00:02", "02:5e:00:00:00:03", "02:5e:00:00:00:04"]
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


def v020_view(saved: dict, entity_id: str) -> tuple[float, bool]:
    """What 0.2.0 applies: only the legacy ``entities`` map (it ignores ``calibrations``)."""
    e = (saved.get("entities") or {}).get(entity_id) or {}
    return e.get("offset", 0.0), e.get("include_in_average", True)


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
    saved = _saved(tmp_path)
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


async def test_rolled_back_v020_sees_the_same_offsets(tmp_path):
    """After the upgrade and an admin edit, config.yaml read the way 0.2.0 reads it (only the
    ``entities`` map) gives the same offsets and averaging as 0.3.0 applies."""
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(V1_EMULATE_CONFIG), encoding="utf-8")
    hub, esp = await _emulated_hub(tmp_path)
    entity = hub.entities["sim_foh.temperature"]
    cal.set_calibration(hub.config, entity.id, entity.hw_key, -0.7, False)
    hub.save_config()
    saved = _saved(tmp_path)
    for entity_id in V1_EMULATE_CONFIG["entities"]:
        now = hub.calibration_for(hub.entities[entity_id])
        assert v020_view(saved, entity_id) == (now.offset, now.include_in_average)
    assert v020_view(saved, "sim_foh.temperature") == (-0.7, False)
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


def test_adopting_a_board_that_looks_adopted_warns_but_goes_ahead(client):
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10", mac=A))
    client.esp.discovered["foh-node"] = _discovered("foh-node", A)
    listed = client.get("/api/admin/state").json()["discovered"]
    assert listed[0]["maybe_adopted_as"] == "foh" and listed[0]["adopted"] is False
    r = client.post("/api/admin/esphome/adopt", json={"host": "foh-node.local", "id": "foh_again"})
    assert r.status_code == 200
    assert r.json() == {"id": "foh_again",
                        "warning": "This board looks like 'foh', which is already adopted."}
    assert [c.id for c in client.hub.config.esphome_devices] == ["foh", "foh_again"]
    r = client.post("/api/admin/esphome/adopt", json={"host": "192.0.2.41", "id": "n2"})
    assert r.json() == {"id": "n2"}


def test_adopt_never_takes_a_mac_from_the_client(client):
    r = client.post("/api/admin/esphome/adopt", json={"host": "192.0.2.40", "id": "n1", "mac": A})
    assert r.status_code == 200
    assert client.hub.config.esphome_devices[0].mac == ""


GOOD_HOSTS = ["192.0.2.10", "foh-node.local", "foh_node", "2001:db8::5", "fe80::1"]
BAD_HOSTS = ["bad host", "", "a\nb", "[::1]", "[2001:db8::5]", "fe80::1%eth0", "http:",
             "http://foh", "foh:6053", "foh/x", "x" * 254, "-foh", "foh..local"]


@pytest.mark.parametrize("host", GOOD_HOSTS)
def test_hosts_accepted(client, host):
    assert client.post("/api/admin/esphome/adopt", json={"host": host, "id": "n1"}).status_code == 200
    client.hub.register_device(Device("n1", "N1", "esphome"))
    assert client.patch("/api/admin/devices/n1", json={"host": host}).status_code == 200


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_hosts_refused(client, host):
    r = client.post("/api/admin/esphome/adopt", json={"host": host, "id": "n1"})
    assert r.status_code == 422 and client.hub.config.esphome_devices == []
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10"))
    client.hub.register_device(Device("foh", "FOH", "esphome"))
    r = client.patch("/api/admin/devices/foh", json={"host": host})
    assert r.status_code == 422 and client.hub.config.esphome_devices[0].host == "192.0.2.10"
    if host.strip():
        assert host not in r.text  # fixed error text, never the input


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


@pytest.mark.parametrize("body", [{"port": 0}, {"port": 70000}])
def test_patch_rejects_bad_ports(client, body):
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10"))
    client.hub.register_device(Device("foh", "FOH", "esphome"))
    assert client.patch("/api/admin/devices/foh", json=body).status_code == 422


def test_patch_host_of_an_emulated_node_is_refused(client):
    assert client.patch("/api/admin/devices/sim_foh", json={"host": "192.0.2.50"}).status_code == 409


def test_patch_host_needs_admin_and_same_origin(client):
    client.hub.config.esphome_devices.append(EsphomeDeviceConfig(id="foh", host="192.0.2.10"))
    client.hub.register_device(Device("foh", "FOH", "esphome"))
    r = client.patch("/api/admin/devices/foh", json={"host": "192.0.2.11"},
                     headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    client.cookies.clear()
    assert client.patch("/api/admin/devices/foh", json={"host": "192.0.2.11"}).status_code == 401
    assert client.hub.config.esphome_devices[0].host == "192.0.2.10"


def _api_fault(client, monkeypatch, board_mac=MAC_B):
    """Put an adopted node 'foh' (recorded MAC A) into a hardware-check FAULT."""
    hub, esp = client.hub, client.esp
    cfg = EsphomeDeviceConfig(id="foh", host="192.0.2.10", mac=A)
    hub.config.esphome_devices.append(cfg)

    async def connect():  # inside the app's event loop, like a real connection
        node = await _node(hub, cfg, board_mac)
        esp._nodes["foh"] = node
        await node._on_connect()
    client.portal.call(connect)
    restarted = []

    async def fake_restart(c, detail):
        restarted.append(c.id)
    monkeypatch.setattr(esp, "_restart_node", fake_restart)
    return cfg, restarted


def test_resolve_endpoint(client, monkeypatch):
    cfg, restarted = _api_fault(client, monkeypatch)
    state = client.get("/api/admin/state").json()
    assert state["hardware"]["devices"]["foh"]["conflict"] == {
        "reason": "different", "expected": A, "found": B, "other": "", "records": 0}
    url = "/api/admin/esphome/foh/resolve"
    r = client.post(url, json={"action": "forget_mac"})
    assert r.status_code == 409 and r.json()["detail"] == RESOLVE_REFUSED and cfg.mac == A
    assert client.post(url, json={"action": "explode"}).status_code == 422
    assert client.post(url, json={"action": "new_hardware", "extra": 1}).status_code == 422
    assert client.post(url, json={"action": "new_hardware"}).status_code == 200
    assert cfg.mac == B and restarted == ["foh"]
    r = client.post("/api/admin/esphome/nope/resolve", json={"action": "new_hardware"})
    assert r.status_code == 409 and r.json()["detail"] == RESOLVE_REFUSED


def test_resolve_endpoint_forget_mac(client, monkeypatch):
    cfg, restarted = _api_fault(client, monkeypatch, board_mac="")
    url = "/api/admin/esphome/foh/resolve"
    assert client.post(url, json={"action": "move_calibration"}).status_code == 409
    assert client.post(url, json={"action": "forget_mac"}).status_code == 200
    assert cfg.mac == "" and restarted == ["foh"]


def test_resolve_endpoint_needs_admin_and_same_origin(client, monkeypatch):
    cfg, restarted = _api_fault(client, monkeypatch)
    url = "/api/admin/esphome/foh/resolve"
    r = client.post(url, json={"action": "new_hardware"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    client.cookies.clear()
    assert client.post(url, json={"action": "new_hardware"}).status_code == 401
    assert cfg.mac == A and restarted == []


def test_admin_state_has_the_hardware_map_and_the_snapshot_does_not(client, monkeypatch):
    _api_fault(client, monkeypatch)
    state = client.get("/api/admin/state").json()
    assert state["hardware"]["entities"]["sim_foh.temperature"] == "mac:025e00000002/temperature"
    assert state["hardware"]["devices"]["sim_foh"] == {"hw_id": "mac:025e00000002", "conflict": None, "role": "environment", "host": "", "address": ""}
    snap = client.get("/api/snapshot").text
    assert "025e0000000" not in snap and "hw_key" not in snap and "hw_id" not in snap
    for form in mac_forms(A) + mac_forms(B):
        assert form not in snap


def test_put_entity_writes_the_hardware_record(client):
    r = client.put("/api/admin/entities/sim_foh.temperature", json={"offset": -0.5, "include_in_average": True})
    assert r.status_code == 200
    rec = client.hub.config.calibrations["mac:025e00000002/temperature"]
    assert rec.offset == -0.5 and rec.history[0].method == "manual"
    client.hub.update_state("sim_foh.temperature", 22.0)
    assert client.hub.entities["sim_foh.temperature"].value == pytest.approx(21.5)
    # The admin card reads the settings that apply now, so a hardware-only offset is never shown as 0.
    settings = client.get("/api/admin/state").json()["hardware"]["settings"]
    assert settings["sim_foh.temperature"] == {"offset": -0.5, "include_in_average": True, "accuracy": None, "accuracy_basis": "typical",
                                                 "role": "", "role_now": "environment"}
    assert settings["sim_foh.humidity"] == {"offset": 0.0, "include_in_average": True, "accuracy": None, "accuracy_basis": "typical",
                                                 "role": "", "role_now": "environment"}
    assert "site.temperature" not in settings


async def test_node_addresses_are_admin_only_and_show_the_connected_ip(tmp_path):
    """The admin node list shows where each node is; the public snapshot never carries an address."""
    hub = Hub(tmp_path)
    esp = EsphomeIntegration(hub)
    cfg = EsphomeDeviceConfig(id="foh", host="stagewatch-foh.local")
    hub.config.esphome_devices.append(cfg)
    node = await _node(hub, cfg, MAC_A)
    esp._nodes[cfg.id] = node
    node.client.connected_address = None
    assert esp.node_addresses() == {"foh": {"host": "stagewatch-foh.local", "address": ""}}
    node.client.connected_address = "192.0.2.77"
    assert esp.node_addresses() == {"foh": {"host": "stagewatch-foh.local", "address": "192.0.2.77"}}
    assert "192.0.2.77" not in repr(hub.snapshot())
    hub.recorder.close()
