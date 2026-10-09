"""Saving a changed calibration offset or include-in-average setting adds one system marker.

Expected texts are written out by hand. The marker is stored before the PUT answers, so the
helper waits for the end state (with a short deadline) rather than assuming it.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.calibration import change_text
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.web.server import create_app

ENT = "foh.temp"
HW = "mac:aabbccddeeff/temp"


def until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition not reached in time")
        time.sleep(0.02)


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path)
    hub.register_device(Device("foh", "FOH", "esphome", status=Status.OK))
    hub.register_entity(Entity(ENT, "foh", "Stage Left Temp", Kind.TEMPERATURE, "°C", hw_key=HW))
    hub.update_state(ENT, 20.0)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "4321"}).status_code == 200
        yield c


def _put(client, offset, include=True):
    r = client.put(f"/api/admin/entities/{ENT}", json={"offset": offset, "include_in_average": include})
    assert r.status_code == 200


def _labels(client):
    return [m.label for m in client.hub.recorder.markers()]


def test_offset_change_adds_one_marker_with_old_and_new(client):
    _put(client, 0.3)
    until(lambda: len(_labels(client)) == 1)
    assert _labels(client) == ["Calibration changed: Stage Left Temp offset +0.3 °C (was 0 °C)"]
    assert client.hub.recorder.markers()[0].source == "hub"
    _put(client, -0.25)
    until(lambda: len(_labels(client)) == 2)
    assert _labels(client)[1] == "Calibration changed: Stage Left Temp offset -0.25 °C (was +0.3 °C)"


def test_include_change_and_both_in_one_save(client):
    _put(client, 0.0, include=False)
    until(lambda: len(_labels(client)) == 1)
    assert _labels(client) == ["Stage Left Temp now left out of the site average (was included)"]
    _put(client, 1.5, include=True)
    until(lambda: len(_labels(client)) == 2)
    assert _labels(client)[1] == ("Calibration changed: Stage Left Temp offset +1.5 °C (was 0 °C); "
                                  "now included in the site average (was left out)")


def test_save_that_changes_nothing_adds_no_marker(client):
    _put(client, 0.0)          # same as the defaults a new sensor starts with
    _put(client, 0.5)
    until(lambda: len(_labels(client)) == 1)
    _put(client, 0.5)          # saved again, unchanged
    assert len(_labels(client)) == 1


def test_adoption_and_start_up_add_no_marker(client, tmp_path):
    assert _labels(client) == []     # registering the sensor and its first reading made none
    _put(client, 0.5)
    until(lambda: len(_labels(client)) == 1)
    # A restart reads the saved offset from disk: no new marker for it.
    hub2 = Hub(tmp_path)
    assert [m.label for m in hub2.recorder.markers() if "alibration" in m.label] == [x for x in _labels(client) if "alibration" in x]


def test_equipment_tick_is_not_a_change(client):
    client.hub.devices["foh"].role = "equipment"
    _put(client, 0.0, include=False)
    assert _labels(client) == []


def test_public_marker_has_only_the_allowed_fields_and_no_address(client):
    _put(client, 0.3)
    until(lambda: len(_labels(client)) == 1)
    m = client.get("/api/snapshot").json()["markers"][0]
    assert set(m) == {"id", "ts", "label", "source", "hidden", "note"}
    assert "aabbcc" not in m["label"].lower()
    assert client.patch(f"/api/markers/{m['id']}", json={"hidden": True, "note": "after rain"}).status_code == 200


def test_change_text_wording():
    assert change_text("A", "Pa", 0.0, -100.0, True, True) == "Calibration changed: A offset -100 Pa (was 0 Pa)"
    assert change_text("A", "%", 2.0, 2.0, True, True) == ""
    assert change_text("A", "", 1.0, 1.0, False, True) == "A now included in the site average (was left out)"
