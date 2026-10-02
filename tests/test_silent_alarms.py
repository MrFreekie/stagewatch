"""F5 silent alarms: on-screen notices that never sound, never count toward max_level, never need ack."""

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.alarms import AlarmEngine
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Status
from stagewatch.web.server import create_app


def test_silent_alarm_is_not_sounding_and_not_in_max_level():
    eng = AlarmEngine()
    eng.set_condition("device:x", True, 3, "X: missing", 1.0, silent=True)
    assert eng.sounding is False
    assert eng.max_level == 0
    assert eng.to_list()[0]["silent"] is True


def test_default_alarm_is_audible_and_dict_carries_flag():
    eng = AlarmEngine()
    eng.set_condition("device:y", True, 1, "Y: missing", 1.0)
    assert eng.sounding is True and eng.max_level == 1
    assert eng.to_list()[0]["silent"] is False


def test_ack_all_skips_silent_and_mix_behaves():
    eng = AlarmEngine()
    eng.set_condition("device:quiet", True, 3, "quiet", 1.0, silent=True)
    eng.set_condition("device:loud", True, 2, "loud", 2.0)
    assert eng.sounding and eng.max_level == 2
    acked = eng.ack_all()
    assert [a.id for a in acked] == ["device:loud"]
    assert eng.active["device:quiet"].acked is False
    assert not eng.sounding
    assert eng.max_level == 2  # acked audible alarm still counts, silent never does
    assert len(eng.to_list()) == 2


def test_silent_only_ack_does_nothing():
    eng = AlarmEngine()
    eng.set_condition("device:quiet", True, 1, "quiet", 1.0, silent=True)
    assert eng.ack_all() == []


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.register_device(Device("clock", "Wall Clock", "test", status=Status.OK))
    yield h
    h.recorder.close()


def test_hub_silent_status_alarm_and_log_suffix(hub):
    hub.set_device_status("clock", Status.MISSING, "no data", silent_alarm=True)
    alarm = hub.alarms.to_list()[0]
    assert alarm["silent"] is True
    assert not hub.alarms.sounding and hub.alarms.max_level == 0
    assert hub.ack_alarms("test") == 0
    hub.set_device_status("clock", Status.OK)
    assert hub.alarms.to_list() == []
    log = hub.recorder.alarm_log()
    assert [e["event"] for e in log] == ["clear", "raise"]
    assert all(e["message"].endswith(" (silent)") for e in log)


def test_hub_audible_alarm_log_has_no_suffix(hub):
    hub.set_device_status("clock", Status.MISSING, "no data")
    assert hub.alarms.sounding
    assert not hub.recorder.alarm_log()[0]["message"].endswith("(silent)")


def test_websocket_snapshot_and_alarm_broadcast_carry_silent(tmp_path):
    hub = Hub(tmp_path)
    hub.register_device(Device("clock", "Wall Clock", "test", status=Status.OK))
    hub.set_device_status("clock", Status.MISSING, "no data", silent_alarm=True)
    with TestClient(create_app(hub)) as c:
        with c.websocket_connect("/ws?dashboard=foh") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert snap["sounding"] is False
            assert [a["silent"] for a in snap["alarms"]] == [True]
            hub.set_device_status("clock", Status.OK)
            hub.set_device_status("clock", Status.FAULT, "x")  # audible this time
            seen = []
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "alarms":
                    seen.append(msg)
                    if msg["sounding"]:
                        break
            assert seen[-1]["sounding"] is True
            assert seen[-1]["alarms"][0]["silent"] is False
    hub.recorder.close()


def test_dashboard_js_banner_uses_notice_style_and_hides_ack_for_silent_only():
    from pathlib import Path
    import stagewatch.web as web
    js = (Path(web.__file__).parent / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert '" show notice"' in js and "a.silent" in js
    # Ack is driven by state.sounding, which the server computes without silent alarms.
    assert '$("ack").hidden = !(canAck && state.sounding)' in js
