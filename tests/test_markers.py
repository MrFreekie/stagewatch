"""Marker notes and hiding (DB v3): the note rules, POST/PATCH /api/markers and the WebSocket,
permissions, the live update message, and alarm markers hiding when the alarm is acknowledged."""

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.config import Threshold
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.core.recorder import NOTE_MAX, clean_note
from stagewatch.web.server import create_app

EVIL = {"Origin": "http://evil.example"}
SECRETISH = "do-not-echo-this-text"


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path)  # default dashboards: foh and phone may add markers, wall may not
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def _admin(client):
    assert client.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def _add(client, label="Aligned", **extra):
    r = client.post("/api/markers", json={"label": label, "dashboard": "foh", **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ note rules
def test_clean_note():
    assert clean_note("  Delays re-timed\r\nafter rain\rcheck subs  ") == "Delays re-timed\nafter rain\ncheck subs"
    assert clean_note("") == "" and clean_note("   \n ") == ""
    assert clean_note("é" * NOTE_MAX) == "é" * NOTE_MAX and NOTE_MAX == 1000
    assert clean_note("Läuft gut · 21:45 · 1,013.2 hPa 🙂") == "Läuft gut · 21:45 · 1,013.2 hPa 🙂"
    for bad in ("x" * 1001, "tab\there", "nul\x00", "zero​width", "bidi‮override", "bell\x07", 5, None):
        with pytest.raises(ValueError) as e:
            clean_note(bad)
        assert "tab" not in str(e.value) and "bidi" not in str(e.value)


# ------------------------------------------------------------------ add with a note
def test_add_marker_with_a_note_and_the_shape(client):
    m = _add(client, note="Line 1\nLine 2")
    assert set(m) == {"id", "ts", "label", "source", "hidden", "note"}
    assert (m["note"], m["hidden"], m["source"]) == ("Line 1\nLine 2", False, "dashboard:foh")
    assert _add(client, "No note")["note"] == ""
    snap = client.get("/api/snapshot").json()["markers"]
    assert [(x["label"], x["note"], x["hidden"]) for x in snap] == [("Aligned", "Line 1\nLine 2", False),
                                                                    ("No note", "", False)]


@pytest.mark.parametrize("note", ["x" * 1001 + SECRETISH, SECRETISH + "‮", SECRETISH + "\tx"])
def test_bad_note_on_add_is_refused_without_echo(client, note):
    r = client.post("/api/markers", json={"label": "x", "dashboard": "foh", "note": note})
    assert r.status_code == 422 and SECRETISH not in r.text
    assert client.get("/api/snapshot").json()["markers"] == []


def test_websocket_add_marker_with_note(client):
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_json({"type": "add_marker", "label": "Bad", "note": "nope​"})  # refused: nothing added
        ws.send_json({"type": "add_marker", "label": "Doors", "note": "Queue to the gate"})
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "marker":
                assert msg["marker"]["label"] == "Doors" and msg["marker"]["note"] == "Queue to the gate"
                break
        else:
            pytest.fail("no marker broadcast")
    assert [m.label for m in client.hub.recorder.markers()] == ["Doors"]


# ------------------------------------------------------------------ PATCH
def test_patch_note_and_hide_from_a_dashboard_and_admin(client):
    m = _add(client)
    r = client.patch(f"/api/markers/{m['id']}", json={"note": "Re-aligned delays\nafter rain", "dashboard": "phone"})
    assert r.status_code == 200 and r.json()["note"] == "Re-aligned delays\nafter rain"  # any marker, any allowed dashboard
    r = client.patch(f"/api/markers/{m['id']}", json={"hidden": True, "dashboard": "foh"})
    assert r.status_code == 200 and r.json()["hidden"] is True and r.json()["note"] == "Re-aligned delays\nafter rain"
    snap = client.get("/api/snapshot").json()["markers"]
    assert snap[0]["hidden"] is True  # still in the snapshot (and history); dashboards leave it off the chart
    assert client.get(f"/api/markers/{m['id']}/delta").status_code == 200  # delta maths still work
    _admin(client)
    r = client.patch(f"/api/markers/{m['id']}", json={"hidden": False, "note": ""})  # admin: no dashboard needed
    assert r.status_code == 200 and (r.json()["hidden"], r.json()["note"]) == (False, "")
    rec = client.hub.recorder.marker(m["id"])
    assert (rec.hidden, rec.note) == (False, "")


def test_patch_permissions(client):
    m = _add(client)
    for body in ({"hidden": True, "dashboard": "wall"}, {"hidden": True}, {"hidden": True, "dashboard": "nope"}):
        assert client.patch(f"/api/markers/{m['id']}", json=body).status_code == 403
    assert client.patch(f"/api/markers/{m['id']}", json={"hidden": True, "dashboard": "foh"},
                        headers=EVIL).status_code == 403
    assert client.hub.recorder.marker(m["id"]).hidden is False
    # deleting stays admin-only
    assert client.delete(f"/api/markers/{m['id']}").status_code == 401
    assert client.hub.recorder.marker(m["id"]) is not None


def test_patch_validation(client):
    m = _add(client)
    url = f"/api/markers/{m['id']}"
    for body in ({"dashboard": "foh"},                                  # nothing to change
                 {"hidden": "yes", "dashboard": "foh"},                 # not a boolean
                 {"hidden": 1, "dashboard": "foh"},
                 {"note": None, "dashboard": "foh"},
                 {"note": "x", "label": "new", "dashboard": "foh"},     # unknown field
                 {"note": SECRETISH + "\x00", "dashboard": "foh"},
                 {"note": "y" * 1001 + SECRETISH, "dashboard": "foh"}):
        r = client.patch(url, json=body)
        assert r.status_code == 422, body
        assert SECRETISH not in r.text
    assert client.hub.recorder.marker(m["id"]).note == ""


def test_patch_404_outside_the_current_show(client):
    m = _add(client)
    assert client.patch("/api/markers/99999", json={"hidden": True, "dashboard": "foh"}).status_code == 404
    _admin(client)
    client.post("/api/admin/shows", json={"name": "Day 2"})
    r = client.patch(f"/api/markers/{m['id']}", json={"hidden": True, "dashboard": "foh"})
    assert r.status_code == 404
    assert client.hub.recorder.marker(m["id"]).hidden is False


def test_patch_broadcasts_marker_updated(client):
    m = _add(client)
    with client.websocket_connect("/ws?dashboard=wall") as ws:  # a read-only screen still hears it
        assert ws.receive_json()["type"] == "snapshot"
        client.patch(f"/api/markers/{m['id']}", json={"note": "Check", "hidden": True, "dashboard": "foh"})
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "marker_updated":
                assert msg["marker"]["id"] == m["id"] and msg["marker"]["hidden"] is True
                assert msg["marker"]["note"] == "Check"
                break
        else:
            pytest.fail("no marker_updated broadcast")


# ------------------------------------------------------------------ alarm markers
def _hot_hub(tmp_path):
    hub = Hub(tmp_path)
    hub.config.thresholds = [Threshold(id="hot", entity="foh.temperature", label="FOH temp", above=30, level=2)]
    hub.register_device(Device("foh", "FOH", "test", status=Status.OK))
    hub.register_entity(Entity("foh.temperature", "foh", "Temperature", Kind.TEMPERATURE, "°C"))
    return hub


def _alarm_markers(hub):
    return [m for m in hub.recorder.markers() if m.source == "alarm"]


def test_alarm_marker_hides_when_acknowledged(tmp_path):
    hub = _hot_hub(tmp_path)
    seen = []
    hub.bus.subscribe("marker_updated", lambda _t, m: seen.append(m))
    hub.update_state("foh.temperature", 35.0)
    hub.tick()
    (m,) = _alarm_markers(hub)
    assert m.label.startswith("ALARM: FOH temp") and m.hidden is False
    assert hub.ack_alarms("dashboard:foh") == 1
    assert _alarm_markers(hub)[0].hidden is True and [x.id for x in seen] == [m.id]
    hub.update_state("foh.temperature", 20.0)
    hub.tick()  # clears after the ack: stays hidden
    assert _alarm_markers(hub)[0].hidden is True
    hub.recorder.close()


def test_alarm_that_clears_by_itself_keeps_its_marker(tmp_path):
    hub = _hot_hub(tmp_path)
    hub.update_state("foh.temperature", 35.0)
    hub.tick()
    hub.update_state("foh.temperature", 20.0)
    hub.tick()  # cleared, never acknowledged
    assert hub.ack_alarms("admin") == 0
    hub.update_state("foh.temperature", 36.0)
    hub.tick()  # raised again: a new marker; acknowledging hides only that one
    assert hub.ack_alarms("admin") == 1
    assert [m.hidden for m in _alarm_markers(hub)] == [False, True]
    hub.recorder.close()


STATIC = Path(__file__).resolve().parents[1] / "src" / "stagewatch" / "web" / "static"


def test_marker_helpers_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = Path(__file__).resolve().parent / "js" / "marker_notes_test.js"
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_dashboard_marker_ui_is_safe_and_wired():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    # hidden markers are off the chart; the list has the Show hidden (n) toggle and Un-hide
    assert "chart.markers = SW.visibleMarkers(state.markers, false)" in js
    assert 'id="marker-show-hidden"' in html and "Show hidden (${hiddenN})" in js and '"Un-hide"' in js
    # the note: plain text (textContent via SW.h), Edit note with Save / Cancel, and Hide
    for text in ('"Edit note"', '"Save"', '"Cancel"', '"Hide"', '"PATCH"', '"marker_updated"'):
        assert text in js, text
    assert ".marker-note-text { white-space: pre-wrap;" in (STATIC / "style.css").read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|insertAdjacentHTML", js)


def test_silent_alarms_add_no_marker_and_are_not_acknowledged(tmp_path):
    hub = Hub(tmp_path)
    change = hub.alarms.set_condition("device:x", True, 3, "X: missing", time.time(), silent=True)
    hub._alarm_changed([change])
    assert _alarm_markers(hub) == [] and hub.ack_alarms("admin") == 0
    hub.recorder.close()


def test_active_alarm_marker_cannot_be_changed_by_patch(tmp_path):
    hub = _hot_hub(tmp_path)
    with TestClient(create_app(hub, manage_hub=False)) as client:
        hub.update_state("foh.temperature", 35.0)
        hub.tick()
        (m,) = _alarm_markers(hub)
        r = client.patch(f"/api/markers/{m.id}", json={"hidden": True, "dashboard": "foh"})
        assert r.status_code == 409
        assert _alarm_markers(hub)[0].hidden is False
    hub.recorder.close()


def test_marker_dashboard_name_is_length_limited(client):
    r = client.post("/api/markers", json={"label": "x", "dashboard": "d" * 65})
    assert r.status_code == 422
