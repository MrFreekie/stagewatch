"""The public entity payload says when a calibration offset is applied, and nothing else about it."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.web.server import create_app

ENT = "foh.pressure"
HW = "mac:aabbccddeeff/pressure"


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path)
    hub.register_device(Device("foh", "FOH", "esphome", status=Status.OK))
    hub.register_entity(Entity(ENT, "foh", "Pressure", Kind.PRESSURE, "Pa", hw_key=HW))
    hub.update_state(ENT, 101300.0)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "4321"}).status_code == 200
        yield c


def _entity(client):
    return next(e for e in client.get("/api/snapshot").json()["entities"] if e["id"] == ENT)


def _set(client, offset):
    r = client.put(f"/api/admin/entities/{ENT}", json={"offset": offset, "include_in_average": True})
    assert r.status_code == 200


def test_offset_absent_when_zero_and_present_when_set(client):
    assert "offset" not in _entity(client)
    _set(client, -100.0)
    e = _entity(client)
    assert e["offset"] == -100.0
    assert e["value"] == pytest.approx(101200.0)
    _set(client, 0.0)
    assert "offset" not in _entity(client)


def test_derived_entities_never_carry_an_offset(client):
    _set(client, -100.0)
    for e in client.get("/api/snapshot").json()["entities"]:
        if e["derived"]:
            assert "offset" not in e


def test_nothing_else_about_calibration_leaks(client):
    _set(client, -100.0)
    snap = client.get("/api/snapshot").json()
    assert [m["label"] for m in snap["markers"]] == ["Calibration changed: Pressure offset -100 Pa (was 0 Pa)"]
    snap["markers"] = []   # the system marker names the change on purpose; everything else must stay silent
    text = json.dumps(snap).lower()
    assert "aabbccddeeff" not in text and "hw_key" not in text and "calibration" not in text
    assert "include_in_average" not in text
    assert set(_entity(client)) == {"id", "device_id", "name", "kind", "unit", "decimals", "derived",
                                    "value", "raw_value", "updated", "stale", "offset"}


def test_live_update_reaches_open_dashboards(client):
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"
        assert "offset" not in next(e for e in first["entities"] if e["id"] == ENT)
        _set(client, 50.0)
        for _ in range(40):
            msg = ws.receive_json()
            if msg["type"] == "states":
                got = [e for e in msg["entities"] if e["id"] == ENT]
                if got:
                    assert got[0]["offset"] == 50.0
                    break
        else:
            pytest.fail("no live update after the offset changed")
        _set(client, 0.0)
        for _ in range(40):
            msg = ws.receive_json()
            if msg["type"] == "states":
                got = [e for e in msg["entities"] if e["id"] == ENT]
                if got:
                    assert "offset" not in got[0]
                    break
        else:
            pytest.fail("no live update after the offset was cleared")


# ------------------------------------------------------------------ the pages
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"


def test_js_helpers_run_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "offset_note_test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_dashboard_marks_adjusted_sensors_in_text_with_a_name():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert 'role: "img"' in js and '"aria-label"' in js and "SW.OFFSET_NOTE" in js
    assert 'id="offset-note"' in html
    assert "SW.hasOffset(e) ? offsetStar()" in js and "SW.averageAdjusted" in js
    assert "body.layout-wall .offset-note" in css
    assert "innerHTML" not in js
