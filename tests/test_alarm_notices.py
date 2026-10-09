"""Alarm notice timers (dashboard list only) and short, plain connection wording.

Clocks are fake: every time is passed in or patched, nothing sleeps. Hand-worked times use the
defaults, 2 minutes after an acknowledgement and 30 minutes unchanged (120 s and 1,800 s).
"""

from __future__ import annotations

import logging
import re
import subprocess
import shutil
import time
from pathlib import Path

import pytest
import yaml
from aioesphomeapi import (
    InvalidAuthAPIError,
    InvalidEncryptionKeyAPIError,
    RequiresEncryptionAPIError,
)
from aioesphomeapi.core import (
    ResolveAPIError,
    ResolveTimeoutAPIError,
    SocketAPIError,
    TimeoutAPIError,
)
from fastapi.testclient import TestClient

from stagewatch.core import config as config_mod
from stagewatch.core import statustext as st
from stagewatch.core.alarms import AlarmEngine
from stagewatch.core.config import AlarmsConfig, Config, ConfigStore, EsphomeDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Status
from stagewatch.integrations.esphome import _NodeConnection
from stagewatch.version import CONFIG_SCHEMA_VERSION
from stagewatch.web.auth import hash_pin, verify_pin
from stagewatch.web.server import create_app

HIDE, FOLD = 120.0, 1800.0
ADDR_EXAMPLE = ("Error connecting to [AddrInfo(family=<AddressFamily.AF_INET: 2>, "
                "type=<SocketKind.SOCK_STREAM: 1>, proto=6, sockaddr=IPv4Address(('192.0.2.77', 6053)))]")
IP_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


# ------------------------------------------------------------------ engine timers
def notices(eng, now, hide=HIDE, fold=FOLD):
    return {d["id"]: d for d in eng.notice_list(now, hide, fold)}


def test_acknowledged_advisory_leaves_the_list_two_minutes_after_the_ack():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A: missing", 1000.0)
    eng.ack_all(1010.0)
    assert notices(eng, 1010.0)["device:a"]["hide_in"] == 120.0
    assert notices(eng, 1129.0)["device:a"]["hide_in"] == pytest.approx(1.0)   # 1010 + 120 - 1129
    assert "device:a" not in notices(eng, 1130.0)                              # exactly at the limit: gone
    assert "device:a" in eng.active and eng.active["device:a"].acked           # state untouched


def test_quiet_unacknowledged_advisory_folds_after_thirty_minutes_but_stays_listed():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A: missing", 1000.0, silent=True)
    n = notices(eng, 2799.0)["device:a"]
    assert n["old"] is False and n["fold_in"] == pytest.approx(1.0)
    n = notices(eng, 2800.0)["device:a"]                                       # 1000 + 1800
    assert n["old"] is True and n["fold_in"] is None and n["hide_in"] is None


def test_a_sounding_unacknowledged_advisory_is_never_folded():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A: missing", 0.0)
    n = notices(eng, 10 ** 6)["device:a"]
    assert n["old"] is False and n["fold_in"] is None and n["hide_in"] is None and eng.sounding


def test_alert_and_stop_never_time_out():
    eng = AlarmEngine()
    eng.set_condition("device:b", True, 2, "B", 0.0)
    eng.set_condition("device:c", True, 3, "C", 0.0)
    for now in (10.0, 5000.0, 10 ** 7):
        n = notices(eng, now)
        assert n["device:b"]["old"] is False and n["device:b"]["fold_in"] is None
        assert n["device:c"]["old"] is False
    eng.ack_all(100.0)
    n = notices(eng, 10 ** 7)                                                  # acknowledged: still listed
    assert {"device:b", "device:c"} <= set(n) and n["device:b"]["hide_in"] is None


def test_a_cleared_and_returned_condition_gets_a_fresh_timer():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A: missing", 0.0)
    eng.ack_all(5.0)
    assert "device:a" not in notices(eng, 500.0)                               # hidden
    eng.set_condition("device:a", False, 1, "", 600.0)                         # node came back
    eng.set_condition("device:a", True, 1, "A: missing", 700.0)                # and went again
    n = notices(eng, 700.0)["device:a"]
    assert n["acked"] is False and n["hide_in"] is None and n["old"] is False
    assert eng.sounding


def test_a_changed_condition_shows_again_at_once_with_a_fresh_timer():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A: missing (timed out)", 0.0, status="missing")
    eng.ack_all(5.0)
    assert "device:a" not in notices(eng, 500.0)
    eng.set_condition("device:a", True, 1, "A: fault (wrong encryption key)", 500.0, status="fault")
    n = notices(eng, 500.0)["device:a"]
    assert n["message"].startswith("A: fault") and n["hide_in"] == HIDE and n["acked"] is True
    assert "device:a" not in notices(eng, 620.0)
    # unacknowledged old one: a change brings it out of the fold-out
    eng.set_condition("device:z", True, 1, "Z: missing", 0.0, silent=True, status="missing")
    assert notices(eng, 2000.0)["device:z"]["old"] is True
    eng.set_condition("device:z", True, 1, "Z: fault", 2000.0, silent=True, status="fault")
    assert notices(eng, 2000.0)["device:z"]["old"] is False


def test_a_change_of_phrase_alone_updates_the_line_but_keeps_the_timers():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A: missing (timed out)", 0.0, status="missing")
    eng.ack_all(10.0)
    ch = eng.set_condition("device:a", True, 1, "A: missing (can't reach the node)", 100.0, status="missing")
    assert ch is not None and ch.event == "change"
    n = notices(eng, 100.0)["device:a"]
    assert n["message"].endswith("(can't reach the node)") and n["hide_in"] == pytest.approx(30.0)   # 10 + 120 - 100
    assert "device:a" not in notices(eng, 130.0)
    assert eng.set_condition("device:a", True, 1, "A: missing (can't reach the node)", 140.0, status="missing") is None


def test_a_rising_level_sounds_again():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A", 0.0, status="missing")
    eng.ack_all(1.0)
    assert not eng.sounding
    eng.set_condition("device:a", True, 2, "A worse", 5.0, status="missing")
    assert eng.sounding and eng.active["device:a"].acked is False
    eng.ack_all(6.0)
    eng.set_condition("device:a", True, 1, "A better", 7.0, status="missing")     # falling: stays acknowledged
    assert not eng.sounding


def test_bus_publishes_on_raise_change_status_change_and_clear(hub):
    seen = []
    hub.bus.subscribe("alarms", lambda _t, payload: seen.append([a["message"] for a in payload]))
    hub.set_device_status("n1", Status.MISSING, st.TIMED_OUT)
    hub.set_device_status("n1", Status.MISSING, st.CANT_REACH)                 # phrase only
    hub.set_device_status("n1", Status.FAULT, st.BAD_KEY)                      # status
    hub.set_device_status("n1", Status.OK)
    assert len(seen) == 4
    assert seen[0][0].endswith("missing (timed out)") and seen[1][0].endswith("missing (can't reach the node)")
    assert seen[2][0].endswith("fault (wrong encryption key)") and seen[3] == []
    assert [e["event"] for e in hub.recorder.alarm_log()] == ["clear", "change", "change", "raise"]


def test_a_threshold_value_moving_does_not_restart_the_timer():
    """The alarm text moves with the reading; only a new alarm or a level change is a change."""
    from stagewatch.core.config import Threshold
    eng = AlarmEngine()
    t = Threshold(id="t1", entity="a.temperature", above=30.0, level=1, hold_s=0, hysteresis=1)
    values = {"a.temperature": 31.0}
    lookup = lambda e: (values[e], False)
    eng.evaluate([t], lookup, 0.0)
    values["a.temperature"] = 32.5
    eng.evaluate([t], lookup, 1700.0)
    assert eng.active["threshold:t1"].message.endswith("32.5 above 30") or "32.5" in eng.active["threshold:t1"].message
    assert notices(eng, 1801.0)["threshold:t1"]["old"] is False               # sounding: never folded
    eng.ack_all(1800.0)                                                        # a threshold notice hides like any advisory
    assert "threshold:t1" in notices(eng, 1919.0) and "threshold:t1" not in notices(eng, 1920.0)


def test_zero_means_never():
    eng = AlarmEngine()
    eng.set_condition("device:a", True, 1, "A", 0.0)
    eng.set_condition("device:b", True, 1, "B", 0.0)
    eng.ack_all(0.0)
    eng.set_condition("device:c", True, 1, "C", 0.0, silent=True)
    n = notices(eng, 10 ** 8, hide=0, fold=0)
    assert set(n) == {"device:a", "device:b", "device:c"}
    assert all(not d["old"] and d["hide_in"] is None and d["fold_in"] is None for d in n.values())
    # each timer is independent
    n = notices(eng, 10 ** 8, hide=0, fold=FOLD)
    assert {"device:a", "device:b"} <= set(n) and n["device:c"]["old"] is True
    n = notices(eng, 10 ** 8, hide=HIDE, fold=0)
    assert set(n) == {"device:c"}


def test_silent_advisory_notices_fold_like_any_other():
    eng = AlarmEngine()
    eng.set_condition("clock", True, 1, "Clock: missing", 0.0, silent=True)
    assert notices(eng, FOLD + 1)["clock"]["old"] is True


# ------------------------------------------------------------------ hub with a fake clock
class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(time, "time", c)
    return c


@pytest.fixture
def hub(tmp_path, clock):
    h = Hub(tmp_path)
    h.register_device(Device("n1", "Feather ESP8266 + BME280", "esphome", status=Status.OK))
    yield h
    h.recorder.close()


def ids(hub):
    return [a["id"] for a in hub.snapshot()["alarms"]]


def test_hub_ack_then_expiry_and_reload_follows_the_server_clock(hub, clock):
    hub.set_device_status("n1", Status.MISSING, st.CANT_REACH)
    assert hub.alarms.to_list()[0]["message"] == "Feather ESP8266 + BME280: missing (can't reach the node)"
    clock.t += 30
    assert hub.ack_alarms("test") == 1
    assert ids(hub) == ["device:n1"]
    clock.t += 119
    assert ids(hub) == ["device:n1"]
    assert hub.snapshot()["alarms"][0]["hide_in"] == pytest.approx(1.0)        # a reload gets what is left
    clock.t += 1
    assert ids(hub) == []                                                      # a reload no longer lists it
    assert hub.devices["n1"].status == Status.MISSING                          # the state is untouched
    assert "device:n1" in hub.alarms.active


def test_hub_timers_follow_the_admin_setting_and_zero_is_never(hub, clock):
    hub.config.alarms = AlarmsConfig(hide_acked_min=5, fold_old_min=0)
    hub.set_device_status("n1", Status.MISSING, st.TIMED_OUT)
    hub.ack_alarms("test")
    clock.t += 299
    assert ids(hub) == ["device:n1"]
    clock.t += 1
    assert ids(hub) == []
    hub.config.alarms = AlarmsConfig(hide_acked_min=0, fold_old_min=0)
    assert ids(hub) == ["device:n1"]                                           # 0 = never hides
    clock.t += 10 ** 7
    assert hub.snapshot()["alarms"][0]["old"] is False


def test_hub_condition_returning_and_changing_reappears(hub, clock):
    hub.set_device_status("n1", Status.MISSING, st.TIMED_OUT)
    hub.ack_alarms("test")
    clock.t += 200
    assert ids(hub) == []
    hub.set_device_status("n1", Status.FAULT, st.REFUSED)                      # the status changed
    assert ids(hub) == ["device:n1"]
    assert hub.snapshot()["alarms"][0]["message"].endswith("(connection refused)")
    clock.t += 200
    assert ids(hub) == []
    hub.set_device_status("n1", Status.OK)
    assert "device:n1" not in hub.alarms.active
    hub.set_device_status("n1", Status.MISSING, st.REFUSED)                    # and comes back
    a = hub.snapshot()["alarms"][0]
    assert a["acked"] is False and a["hide_in"] is None and a["old"] is False


def test_the_list_sent_after_an_ack_carries_the_timers(hub, clock):
    hub.set_device_status("n1", Status.MISSING, st.TIMED_OUT)
    hub.ack_alarms("test")
    assert hub.alarm_notices()[0]["hide_in"] == HIDE


def test_public_alarm_field_set_is_exact(hub):
    hub.set_device_status("n1", Status.MISSING, st.TIMED_OUT)
    assert set(hub.snapshot()["alarms"][0]) == {
        "id", "level", "level_name", "message", "since", "acked", "silent", "old", "hide_in", "fold_in"}


def test_websocket_snapshot_carries_the_timers(tmp_path, clock):
    hub = Hub(tmp_path)
    hub.register_device(Device("n1", "Node", "esphome", status=Status.OK))
    hub.set_device_status("n1", Status.MISSING, st.CANT_REACH)
    clock.t += 100
    with TestClient(create_app(hub)) as c:
        with c.websocket_connect("/ws?dashboard=foh") as ws:
            snap = ws.receive_json()
            a = snap["alarms"][0]
            assert a["fold_in"] is None and a["old"] is False and a["acked"] is False
            assert "since" in a and "hide_in" in a
    hub.recorder.close()


# ------------------------------------------------------------------ config
def test_alarms_config_defaults_and_bounds():
    a = AlarmsConfig()
    assert (a.hide_acked_min, a.fold_old_min) == (2, 30)
    assert AlarmsConfig(hide_acked_min=0, fold_old_min=0).fold_old_min == 0
    assert AlarmsConfig(hide_acked_min=1440, fold_old_min=10080).hide_acked_min == 1440
    for bad in ({"hide_acked_min": -1}, {"hide_acked_min": 1441}, {"fold_old_min": -1},
                {"fold_old_min": 10081}, {"fold_old_min": "soon"}):
        with pytest.raises(Exception):
            AlarmsConfig(**bad)
    assert AlarmsConfig(future_key=1).hide_acked_min == 2


def test_no_schema_bump_and_no_migration_invents_the_section():
    assert Config().schema_version == CONFIG_SCHEMA_VERSION
    raw = {"schema_version": CONFIG_SCHEMA_VERSION, "site": {"name": "X"}}
    assert "alarms" not in config_mod.migrate(dict(raw))


def test_a_config_saved_before_this_loads_with_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.admin.pin_hash = hash_pin("1234")
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.pop("alarms", None)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = ConfigStore(path).load()
    assert loaded.alarms == AlarmsConfig() and verify_pin("1234", loaded.admin.pin_hash)


def test_round_trip_and_an_older_build_forgets_only_this_section(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.admin.pin_hash = hash_pin("1234")
    cfg.site.name = "Kept venue"
    cfg.alarms = AlarmsConfig(hide_acked_min=7, fold_old_min=0)
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    assert ConfigStore(path).load().alarms == cfg.alarms
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    older = {k: v for k, v in Config.model_fields.items() if k != "alarms"}
    again = Config.model_validate({k: raw[k] for k in older if k in raw})
    assert again.site.name == "Kept venue" and again.alarms == AlarmsConfig()


def test_a_bad_alarms_section_resets_only_itself(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.admin.pin_hash = hash_pin("1234")
    cfg.site.name = "Kept venue"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["alarms"] = {"hide_acked_min": "sideways", "fold_old_min": 99999999}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        old = ConfigStore(path)
        loaded = old.load()
    assert verify_pin("1234", loaded.admin.pin_hash) and not old.recovery_required
    assert loaded.site.name == "Kept venue" and loaded.alarms == AlarmsConfig()
    assert "sideways" not in caplog.text and "1234" not in caplog.text


# ------------------------------------------------------------------ endpoint
@pytest.fixture
def admin(tmp_path):
    hub = Hub(tmp_path)
    app = create_app(hub, manage_hub=False)
    with TestClient(app) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "4821"}).status_code == 200
        yield c
    hub.recorder.close()


def test_put_alarms_needs_admin_and_same_origin(tmp_path):
    hub = Hub(tmp_path)
    anon = TestClient(create_app(hub, manage_hub=False))
    assert anon.put("/api/admin/alarms", json={"hide_acked_min": 1, "fold_old_min": 1}).status_code == 401
    hub.recorder.close()


def test_put_alarms_refuses_cross_origin_and_bad_values(admin):
    ok = {"hide_acked_min": 5, "fold_old_min": 60}
    assert admin.put("/api/admin/alarms", json=ok, headers={"Origin": "http://evil.example"}).status_code == 403
    for bad in ({"hide_acked_min": -1, "fold_old_min": 1}, {"hide_acked_min": 1441, "fold_old_min": 1},
                {"hide_acked_min": 1, "fold_old_min": 10081}, {"hide_acked_min": "2", "fold_old_min": 1},
                {"hide_acked_min": 1.5, "fold_old_min": 1}, {"hide_acked_min": True, "fold_old_min": 1},
                {"hide_acked_min": 1, "fold_old_min": 1, "extra": 1}):
        r = admin.put("/api/admin/alarms", json=bad)
        assert r.status_code == 422, bad
    assert admin.hub.config.alarms == AlarmsConfig()


def test_put_alarms_needs_both_fields(admin):
    assert admin.put("/api/admin/alarms", json={"hide_acked_min": 3}).status_code == 422
    assert admin.put("/api/admin/alarms", json={"fold_old_min": 3}).status_code == 422
    assert admin.put("/api/admin/alarms", json={}).status_code == 422
    assert admin.hub.config.alarms == AlarmsConfig()


def test_put_alarms_saves_and_admin_state_shows_it(admin):
    r = admin.put("/api/admin/alarms", json={"hide_acked_min": 0, "fold_old_min": 45})
    assert r.status_code == 200 and r.json() == {"hide_acked_min": 0, "fold_old_min": 45}
    assert admin.get("/api/admin/state").json()["config"]["alarms"] == {"hide_acked_min": 0, "fold_old_min": 45}
    saved = yaml.safe_load((admin.hub.data_dir / "config.yaml").read_text(encoding="utf-8")) \
        if hasattr(admin.hub, "data_dir") else None
    if saved is not None:
        assert saved["alarms"]["fold_old_min"] == 45


# ------------------------------------------------------------------ plain wording
@pytest.mark.parametrize("err, phrase", [
    (SocketAPIError(ADDR_EXAMPLE), st.CANT_REACH),
    (SocketAPIError(ADDR_EXAMPLE + ": [Errno 113] Connect call failed ('192.0.2.77', 6053)"), st.CANT_REACH),
    (SocketAPIError(ADDR_EXAMPLE + ": [Errno 111] Connect call failed ('192.0.2.77', 6053)"), st.REFUSED),
    (SocketAPIError("Error connecting to x: [WinError 10061] No connection could be made"), st.REFUSED),
    (TimeoutAPIError("Timeout while connecting to [AddrInfo(...)]"), st.TIMED_OUT),
    (ResolveTimeoutAPIError("Timeout while resolving IP address for ['node.local']"), st.TIMED_OUT),
    (ResolveAPIError("Error resolving node.local to IP address: Name or service not known"), st.NAME_NOT_FOUND),
    (InvalidEncryptionKeyAPIError("x"), st.BAD_KEY),
    (RequiresEncryptionAPIError("x"), st.BAD_KEY),
    (OSError("Network is unreachable"), st.CANT_REACH),
    (TimeoutError(), st.TIMED_OUT),
    (RuntimeError("something odd at 192.0.2.5"), st.FALLBACK),
    (ValueError(), st.FALLBACK),
])
def test_connection_errors_become_one_fixed_phrase(err, phrase):
    assert st.describe_connect_error(err) == phrase
    assert phrase in {st.CANT_REACH, st.REFUSED, st.TIMED_OUT, st.BAD_KEY, st.NAME_NOT_FOUND, st.FALLBACK}


HOSTILE = [
    ADDR_EXAMPLE, "Traceback (most recent call last): boom", "node-7.local refused", "10.1.2.3",
    "fe80::1234:5678", "x" * 500, "line one\nline two", "<script>alert(1)</script> [x]", "bad\x00nul",
    "AddrInfo(family=...", "hello 192.0.2.1:6053",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_status_text_never_survives(text):
    assert st.safe_status_detail(text) == st.FALLBACK


PROBES = ["QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=", "/home/pi/x/secret.key", "pass=hunter2", "ws://mixer:4001",
          "key=abc", "C:\\Users\\x\\key.txt", "https://example.test/x", "fe80::1", "::1", "10.0.0.1"]


@pytest.mark.parametrize("text", PROBES)
def test_blocklist_guard_refuses_secrets_paths_urls_and_addresses(text):
    assert st.safe_status_detail(text) == st.FALLBACK


@pytest.mark.parametrize("text", ["Next cue at 19:30:00", "Timed out", "Smaart / Ontime are not answering",
                                  "Wrong encryption key", "No signal for 12:05"])
def test_ordinary_phrases_and_times_pass(text):
    """New integrations must pass phrases from a fixed table: this guard is only a last line."""
    assert st.safe_status_detail(text) == text


def test_short_fixed_phrases_pass_and_non_text_is_dropped():
    for ok in (st.CANT_REACH, st.REFUSED, "Connection lost", "Connecting", "Connected, but no values are arriving",
               "emulated dropout", "API password not supported", "Smaart is not running"):
        assert st.safe_status_detail(ok) == ok
    assert st.safe_status_detail("") == "" and st.safe_status_detail(None) == "" and st.safe_status_detail(7) == ""


def test_alarm_detail_lowercases_ordinary_words_only():
    assert st.alarm_detail("Can't reach the node") == "can't reach the node"
    assert st.alarm_detail("API password not supported") == "API password not supported"
    assert st.alarm_detail("") == ""


async def test_node_connection_error_keeps_the_ip_out_of_every_public_place(tmp_path, caplog):
    hub = Hub(tmp_path)
    cfg = EsphomeDeviceConfig(id="feather", name="Feather ESP8266 + BME280", host="feather-node.local")
    hub.config.esphome_devices.append(cfg)
    node = _NodeConnection(hub, cfg, None)
    hub.register_device(Device("feather", "Feather ESP8266 + BME280", "esphome", status=Status.INITIALIZING))
    with caplog.at_level(logging.WARNING):
        await node._on_connect_error(SocketAPIError(ADDR_EXAMPLE))
        await node._on_connect_error(SocketAPIError(ADDR_EXAMPLE))      # the next retry: same phrase, no new log line
    dev = hub.devices["feather"]
    assert (dev.status, dev.status_detail) == (Status.MISSING, st.CANT_REACH)
    assert hub.alarms.to_list()[0]["message"] == "Feather ESP8266 + BME280: missing (can't reach the node)"
    public = " ".join(str(x) for x in (hub.snapshot(), hub.recorder.alarm_log(), dev.to_dict(), hub.alarm_notices()))
    with TestClient(create_app(hub, manage_hub=False)) as c:
        public += c.get("/api/snapshot").text
        with c.websocket_connect("/ws?dashboard=foh") as ws:
            public += str(ws.receive_json())
    assert "AddrInfo" not in public and not IP_RE.search(public) and "SOCK_STREAM" not in public
    # the full technical text is for the log, once per change of phrase
    lines = [r for r in caplog.records if "can't reach the node" in r.getMessage().lower()]
    assert len(lines) == 1
    assert not IP_RE.search(caplog.text) and "AddrInfo" not in caplog.text and "feather-node" not in caplog.text
    hub.recorder.close()


async def test_node_phrases_for_key_password_and_recovery(tmp_path):
    hub = Hub(tmp_path)
    cfg = EsphomeDeviceConfig(id="d", name="Desk", host="desk-node.local")
    hub.config.esphome_devices.append(cfg)
    node = _NodeConnection(hub, cfg, None)
    hub.register_device(Device("d", "Desk", "esphome", status=Status.INITIALIZING))
    await node._on_connect_error(InvalidEncryptionKeyAPIError("x"))
    assert (hub.devices["d"].status, hub.devices["d"].status_detail) == (Status.FAULT, "Wrong encryption key")
    await node._on_connect_error(InvalidAuthAPIError("secretpassword"))
    assert hub.devices["d"].status_detail == "API password not supported"
    assert "secretpassword" not in str(hub.snapshot())
    hub.recorder.close()


def test_hub_guard_replaces_a_raw_detail_from_any_integration(hub):
    hub.set_device_status("n1", Status.MISSING, ADDR_EXAMPLE)
    assert hub.devices["n1"].status_detail == st.FALLBACK
    assert hub.alarms.to_list()[0]["message"] == "Feather ESP8266 + BME280: missing (connection problem)"


# ------------------------------------------------------------------ the page script
def test_splitalarms_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    root = Path(__file__).resolve().parent
    r = subprocess.run([node, str(root / "js" / "alarm_notices_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_dashboard_script_uses_the_server_timers_and_the_fold_out():
    root = Path(__file__).resolve().parent.parent / "src" / "stagewatch" / "web" / "static"
    js = (root / "dashboard.js").read_text(encoding="utf-8")
    assert "SW.splitAlarms" in js and "Older notices" in js and "setAlarms(msg.alarms)" in js
    assert 'case "alarms": setAlarms(msg.alarms)' in js
    assert "performance.now" in js and "Date.now() - alarmsAt" not in js and 'class: "sr-only"' in js
