"""Calibration export, import (with diff and dry run) and the sensor note (plan §1.9, WP10b)."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import calibration_io
from stagewatch.core.config import Calibration
from stagewatch.core.hub import Hub
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app

TEMP = "sim_foh.temperature"
KEY = "mac:025e00000002/temperature"   # the emulated FOH node's fixed, locally administered MAC


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def _save_offset(client, offset):
    r = client.put(f"/api/admin/entities/{TEMP}", json={"offset": offset, "include_in_average": True})
    assert r.status_code == 200


def _export(client):
    r = client.get("/api/admin/calibrations/export")
    assert r.status_code == 200
    return r


def test_export_is_a_download_with_the_records(client):
    _save_offset(client, 0.5)
    r = _export(client)
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert data["format"] == 1 and data["exported"].endswith("Z") and data["site"] == client.hub.config.site.name
    assert data["calibrations"][KEY]["offset"] == 0.5
    assert data["calibrations"][KEY]["history"][0]["method"] == "manual"


def test_round_trip_to_a_fresh_hub(client, tmp_path):
    _save_offset(client, 0.5)
    exported = _export(client).json()
    hub2 = Hub(tmp_path / "second", emulate=True)
    hub2.add_integration(EsphomeIntegration(hub2, emulate=True))
    with TestClient(create_app(hub2)) as c2:
        assert c2.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        r = c2.post("/api/admin/calibrations/import", json={"data": exported, "dry_run": False})
        assert r.status_code == 200
        assert r.json()["added"] == [KEY] and r.json()["unknown_hardware"] == []
        rec = hub2.config.calibrations[KEY]
        assert rec.offset == 0.5 and rec.history[0].method == "import"
        # applied live: the calibrated value moves by the offset
        again = c2.post("/api/admin/calibrations/import", json={"data": exported, "dry_run": True}).json()
        assert again["added"] == [] and again["unchanged"] == [KEY] and again["changed"] == []


def test_diff_changed_added_unchanged_and_unknown(client):
    _save_offset(client, 0.5)
    data = _export(client).json()
    data["calibrations"][KEY]["offset"] = 1.25
    other = copy.deepcopy(data["calibrations"][KEY])
    data["calibrations"]["mac:02aaaaaaaaaa/temperature"] = other
    data["calibrations"][KEY.replace("temperature", "humidity")] = copy.deepcopy(other)
    r = client.post("/api/admin/calibrations/import", json={"data": data, "dry_run": True}).json()
    assert r["changed"] == [{"key": KEY, "fields": {"offset": {"from": 0.5, "to": 1.25}}}]
    assert sorted(r["added"]) == sorted([KEY.replace("temperature", "humidity"), "mac:02aaaaaaaaaa/temperature"])
    assert r["unknown_hardware"] == ["mac:02aaaaaaaaaa/temperature"]
    assert r["unchanged"] == [] and r["dry_run"] is True


def test_dry_run_changes_nothing(client):
    _save_offset(client, 0.5)
    data = _export(client).json()
    data["calibrations"][KEY]["offset"] = 2.0
    data["calibrations"]["mac:02aaaaaaaaaa/temperature"] = data["calibrations"][KEY]
    before = client.hub.config.model_dump(mode="json")["calibrations"]
    markers = len(client.hub.recorder.markers())
    for body in ({"data": data}, {"data": data, "dry_run": True}):   # dry run is the default
        assert client.post("/api/admin/calibrations/import", json=body).status_code == 200
    assert client.hub.config.model_dump(mode="json")["calibrations"] == before
    assert len(client.hub.recorder.markers()) == markers
    assert client.hub.calibration_for(client.hub.entities[TEMP]).offset == 0.5


def test_apply_writes_history_marker_and_live_value(client):
    _save_offset(client, 0.5)
    data = _export(client).json()
    data["calibrations"][KEY]["offset"] = 2.0
    r = client.post("/api/admin/calibrations/import", json={"data": data, "dry_run": False})
    assert r.status_code == 200 and r.json()["changed"][0]["key"] == KEY
    rec = client.hub.config.calibrations[KEY]
    assert rec.offset == 2.0 and [h.method for h in rec.history[:2]] == ["import", "manual"]
    assert client.hub.config.entities.get(TEMP) is None or client.hub.config.entities[TEMP].offset == 2.0
    e = client.hub.entities[TEMP]
    assert e.value == pytest.approx(e.raw_value + 2.0)
    assert any("Calibration imported" in m.label for m in client.hub.recorder.markers())


@pytest.mark.parametrize("data", [
    {}, {"format": 1}, {"format": 2, "calibrations": {}}, {"format": 1, "calibrations": []},
    {"format": 1, "calibrations": {"../etc/passwd": {}}},
    {"format": 1, "calibrations": {KEY: {"offset": "x"}}},
    {"format": 1, "calibrations": {KEY: {"offset": 1e999}}},
])
def test_bad_files_are_refused_without_echo(client, data):
    _save_offset(client, 0.5)
    r = client.post("/api/admin/calibrations/import", content=json.dumps({"data": data, "dry_run": False}),
                    headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert "passwd" not in r.text
    assert client.hub.config.calibrations[KEY].offset == 0.5


def test_too_many_records_refused(client):
    many = {f"dev:x/s{i}": {"offset": 0} for i in range(calibration_io.MAX_RECORDS + 1)}
    r = client.post("/api/admin/calibrations/import", json={"data": {"format": 1, "calibrations": many}, "dry_run": False})
    assert r.status_code == 422 and "too many" in r.json()["detail"]
    assert not any(k.startswith("dev:x/") for k in client.hub.config.calibrations)


def test_import_body_limit_is_1_mib(client):
    pad = "x" * (200 * 1024)   # over the 64 KiB default, under the override: reaches validation
    r = client.post("/api/admin/calibrations/import", json={"data": {"format": 1, "calibrations": {}, "pad": pad}})
    assert r.status_code == 200
    r = client.post("/api/admin/calibrations/import", content=b'{"data":{"pad":"' + b"x" * (1024 * 1024) + b'"}}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_sensors_recorded_as_received_are_refused(client):
    sound = next((e for e in client.hub.entities.values() if e.hw_key), None)
    sound.locked_equipment = True   # stands in for a Shelly meter reading
    data = {"format": 1, "calibrations": {sound.hw_key: {"offset": 9}}}
    r = client.post("/api/admin/calibrations/import", json={"data": data, "dry_run": False}).json()
    assert r["refused"] == [sound.hw_key] and r["added"] == [] and r["changed"] == []


def test_note_adds_a_history_line_and_keeps_the_offset(client):
    _save_offset(client, 0.5)
    r = client.post(f"/api/admin/calibrations/{TEMP}/note", json={"reference": "Kestrel 5500", "note": "FOH, shade"})
    assert r.status_code == 200
    rec = client.hub.config.calibrations[KEY]
    assert rec.offset == 0.5 and rec.history[0].method == "reference" and rec.history[0].note == "FOH, shade"
    hist = client.get("/api/admin/state").json()["hardware"]["calibrations"][TEMP]
    assert hist[0]["reference"] == "Kestrel 5500"


def test_note_refusals(client):
    assert client.post(f"/api/admin/calibrations/{TEMP}/note", json={"note": "x"}).status_code == 404   # no record yet
    _save_offset(client, 0.5)
    url = f"/api/admin/calibrations/{TEMP}/note"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"note": "a‮b"}).status_code == 422
    assert client.post(url, json={"note": "x" * 201}).status_code == 422
    assert client.post(url, json={"note": "x", "extra": 1}).status_code == 422
    assert client.post("/api/admin/calibrations/nope/note", json={"note": "x"}).status_code == 404
    assert len(client.hub.config.calibrations[KEY].history) == 1


def test_all_endpoints_need_admin_and_same_origin(client):
    _save_offset(client, 0.5)
    data = _export(client).json()
    posts = [("/api/admin/calibrations/import", {"data": data, "dry_run": False}),
             (f"/api/admin/calibrations/{TEMP}/note", {"note": "x"})]
    before = client.hub.config.model_dump(mode="json")["calibrations"]
    for url, body in posts:
        assert client.post(url, json=body, headers={"origin": "http://evil.example"}).status_code == 403
    client.cookies.clear()
    for url, body in posts:
        assert client.post(url, json=body).status_code == 401
    assert client.get("/api/admin/calibrations/export").status_code == 401
    assert client.hub.config.model_dump(mode="json")["calibrations"] == before


def test_hardware_keys_stay_out_of_public_data(client):
    _save_offset(client, 0.5)
    client.post(f"/api/admin/calibrations/{TEMP}/note", json={"note": "secret-note", "reference": "secret-ref"})
    public = client.get("/api/snapshot").text
    client.cookies.clear()
    public += client.get("/api/snapshot").text
    for needle in ("025e0000", "mac:", "secret-note", "secret-ref", "history", "hw_key"):
        assert needle not in public


def test_plan_import_is_pure():
    from stagewatch.core.config import Config
    cfg = Config()
    plan = calibration_io.plan_import(cfg, {"format": 1, "calibrations": {"dev:a/temperature": {"offset": 1}}}, set())
    assert plan.added == ["dev:a/temperature"] and cfg.calibrations == {}
    calibration_io.apply_import(cfg, plan, now=1_790_952_000.0)
    assert cfg.calibrations["dev:a/temperature"] == Calibration(
        offset=1, history=cfg.calibrations["dev:a/temperature"].history)
    assert cfg.calibrations["dev:a/temperature"].history[0].date == "2026-10-02T14:40:00Z"
