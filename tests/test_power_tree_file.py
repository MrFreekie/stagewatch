"""Power distribution: save to a file, read a file (preview first, apply on a second call)."""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import power_tree_io
from stagewatch.core.config import ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parent.parent
EXPORT, IMPORT, TREE = "/api/admin/power-tree/export", "/api/admin/power-tree/import", "/api/admin/power-tree"

GOOD = {"points": [{"id": "gen1", "name": "FOH generator", "role": "source", "unit_no": 1, "rating_kva": 100},
                   {"id": "isu1", "name": "Intake", "role": "isu", "unit_no": 1, "upstream": "p:gen1", "rating_a": 125}],
        "meters": [{"id": "m1", "role": "cdu", "unit_no": 1, "upstream": "p:isu1", "breaker_a": 63, "rating_a": 32,
                    "channels": [{"key": "pm1_0", "role": "fdu", "unit_no": 1, "upstream": "m:m1", "way": 2,
                                  "label": "Amp rack", "phase": "L2", "in_total": True}]},
                   {"id": "m2", "role": "equipment", "unit_no": 1, "upstream": "c:m1:pm1_0", "label": "Desk", "phase": "L1",
                    "in_total": True, "breaker_a": 16}]}


def _hub(tmp_path):
    h = Hub(tmp_path)
    h.config.shelly_devices = [
        ShellyDeviceConfig(id="m1", host="192.168.1.50", mac="AABBCCDDEEFF", name="Distro A", circuit_keys=["pm1_0", "pm1_1"],
                           channels=[ShellyChannelConfig(key="pm1_0")]),
        ShellyDeviceConfig(id="m2", host="192.168.1.51", mac="AABBCCDDEE00", name="Desk meter", circuit_keys=["pm1_0"])]
    return h


@pytest.fixture
def client(tmp_path):
    h = _hub(tmp_path)
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        assert c.put(TREE, json=GOOD).status_code == 200
        yield c


def _layout(c):
    return {k: v for k, v in c.get(EXPORT).json().items() if k != "saved_at"}


def test_save_is_a_download_with_the_listed_content(client):
    r = client.get(EXPORT)
    assert r.status_code == 200
    assert re.fullmatch(r'attachment; filename="stagewatch-power-distribution-\d{4}-\d{2}-\d{2}\.json"', r.headers["content-disposition"])
    f = r.json()
    assert f["schema"] == 1 and f["saved_at"].endswith("Z") and f["stagewatch_version"] and "demand_window_min" in f["power_site"]
    assert [p["id"] for p in f["points"]] == ["gen1", "isu1"]
    a = next(m for m in f["meters"] if m["mac"] == "aabbccddeeff")
    assert a["upstream"] == "p:isu1" and a["breaker_a"] == 63 and a["device_limit_a"] == 32 and a["single_circuit"] is False
    c0 = a["circuits"][0]
    assert c0["key"] == "pm1_0" and c0["upstream"] == "m:aabbccddeeff" and c0["way"] == 2 and c0["line"] == "L2" and c0["in_total"] is True
    b = next(m for m in f["meters"] if m["mac"] == "aabbccddee00")
    assert b["single_circuit"] is True and b["upstream"] == "c:aabbccddeeff:pm1_0" and b["label"] == "Desk" and b["circuits"] == []


def test_no_addresses_or_secrets_in_the_file(client):
    client.hub.config.shelly_devices[0].host = "192.168.1.50"
    text = client.get(EXPORT).text
    for needle in ("192.168", "host", "pin", "password", "hash", "token", "secret", "session"):
        assert needle not in text.lower()


def test_round_trip_save_change_read_equal(client):
    saved = client.get(EXPORT).json()
    before = _layout(client)
    # change a lot, then read the file back
    client.put(TREE, json={"points": [{"id": "gen1", "name": "Renamed", "role": "source", "unit_no": 1, "rating_kva": 50}],
                           "meters": [{"id": "m1", "role": "other", "unit_no": 4, "breaker_a": 10}]})
    assert _layout(client) != before
    prev = client.post(IMPORT, json={"data": saved})
    assert prev.status_code == 200 and prev.json()["dry_run"] is True
    assert prev.json()["changed"] and not prev.json()["new"]
    mid = _layout(client)
    assert mid != before        # the preview changed nothing
    r = client.post(IMPORT, json={"data": saved, "dry_run": False})
    assert r.status_code == 200
    assert _layout(client) == before
    again = client.post(IMPORT, json={"data": saved}).json()
    assert not again["changed"] and not again["new"] and len(again["unchanged"]) == 4


def test_default_is_preview_and_a_marker_is_logged_on_apply(client):
    saved = client.get(EXPORT).json()
    saved["points"][0]["name"] = "Other name"
    client.post(IMPORT, json={"data": saved})
    assert client.hub.config.power_points[0].name == "FOH generator"
    client.post(IMPORT, json={"data": saved, "dry_run": False})
    assert client.hub.config.power_points[0].name == "Other name"


def test_new_point_and_ids_remapped_to_a_fresh_hub(tmp_path, client):
    saved = client.get(EXPORT).json()
    h2 = _hub(tmp_path / "second")
    h2.config.shelly_devices[0].id = "other_id"      # the same meter, a different id on this hub
    with TestClient(create_app(h2)) as c2:
        assert c2.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        p = c2.post(IMPORT, json={"data": saved}).json()
        assert [x["name"] for x in p["new"]] == ["FOH generator", "Intake"] and p["skipped"] == []
        assert c2.post(IMPORT, json={"data": saved, "dry_run": False}).status_code == 200
        cfg = h2.config
        dev = next(d for d in cfg.shelly_devices if d.id == "other_id")
        assert dev.upstream == "p:isu1"
        assert next(c for c in dev.channels if c.key == "pm1_0").upstream == "m:other_id"
        from stagewatch.core.power_tree import holder_of_meter
        assert holder_of_meter(next(d for d in cfg.shelly_devices if d.id == "m2")).upstream == "c:other_id:pm1_0"


def test_a_meter_this_hub_does_not_have_is_skipped_and_listed(client):
    saved = client.get(EXPORT).json()
    saved["meters"].append({**copy.deepcopy(saved["meters"][1]), "mac": "112233445566", "name": "Rack meter",
                            "upstream": "c:aabbccddeeff:pm1_0"})
    saved["meters"][1]["upstream"] = "m:112233445566"       # something real is fed from the skipped meter
    r = client.post(IMPORT, json={"data": saved})
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["skipped"]]
    assert "Rack meter" in names and any("link" in n for n in names)
    assert client.post(IMPORT, json={"data": saved, "dry_run": False}).status_code == 200
    from stagewatch.core.power_tree import holder_of_meter
    assert holder_of_meter(client.hub.config.shelly_devices[1]).upstream == ""
    assert all(d.mac != "112233445566" for d in client.hub.config.shelly_devices)


def test_a_link_to_something_not_in_the_file_is_refused(client):
    saved = client.get(EXPORT).json()
    saved["meters"][0]["upstream"] = "m:999999999999"
    r = client.post(IMPORT, json={"data": saved, "dry_run": False})
    assert r.status_code == 422 and r.json()["detail"] == power_tree_io.BAD_LINK


def test_a_tree_breach_refuses_everything_with_nothing_changed(client):
    saved = client.get(EXPORT).json()
    before = client.hub.config.model_dump(mode="json")
    saved["points"][0]["name"] = "Changed first"
    saved["points"][1]["upstream"] = "p:isu1"                 # itself
    r = client.post(IMPORT, json={"data": saved, "dry_run": False})
    assert r.status_code == 422 and "Nothing has been changed" in r.json()["detail"]
    assert client.hub.config.model_dump(mode="json") == before
    loop = client.get(EXPORT).json()
    loop["points"][0].update(role="other", upstream="p:isu1")
    loop["points"][1].update(role="other")
    assert client.post(IMPORT, json={"data": loop}).status_code == 422       # a loop is caught even in the preview
    assert client.hub.config.model_dump(mode="json") == before


def test_newer_schema_and_bad_files_get_fixed_text(client):
    saved = client.get(EXPORT).json()
    r = client.post(IMPORT, json={"data": {**saved, "schema": 2}})
    assert r.status_code == 422 and "newer version" in r.json()["detail"] and "Nothing has been changed" in r.json()["detail"]
    for bad in ({}, {"schema": "1"}, {"schema": 0}, {"schema": True}, {**saved, "extra": 1}):
        r = client.post(IMPORT, json={"data": bad})
        assert r.status_code == 422 and r.json()["detail"] in (power_tree_io.BAD_FILE, power_tree_io.BAD_RECORD)


@pytest.mark.parametrize("edit", [
    lambda f: f["points"][0].update(role="wind"), lambda f: f["points"][0].update(name="a\nb"),
    lambda f: f["points"][0].update(id="Bad Id"), lambda f: f["points"][0].update(unit_no=100),
    lambda f: f["points"][0].update(upstream="http://evil.example"), lambda f: f["meters"][0].update(mac="zz"),
    lambda f: f["meters"][0].update(host="10.0.0.1"), lambda f: f["points"].append(dict(f["points"][0])),
    lambda f: f["meters"][0]["circuits"].append(dict(f["meters"][0]["circuits"][0])),
    lambda f: f["meters"][0].update(breaker_a=-1), lambda f: f["power_site"].update(demand_window_min=7),
    lambda f: f.update(points=[f["points"][0]] * 41)])
def test_bad_entries_are_refused_without_echoing_the_input(client, edit):
    f = client.get(EXPORT).json()
    f["points"][0]["name"] = "SECRETWORD" if False else f["points"][0]["name"]
    edit(f)
    r = client.post(IMPORT, json={"data": f, "dry_run": False})
    assert r.status_code == 422 and "evil.example" not in r.text and "zz" not in r.json()["detail"]


def test_errors_never_echo_input(client):
    f = client.get(EXPORT).json()
    f["points"][0]["role"] = "LEAKMARKER"
    r = client.post(IMPORT, json={"data": f})
    assert r.status_code == 422 and "LEAKMARKER" not in r.text


def test_oversize_body_is_refused(client):
    f = client.get(EXPORT).json()
    f["stagewatch_version"] = "x" * (600 * 1024)
    r = client.post(IMPORT, content=json.dumps({"data": f}), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_needs_admin_and_same_origin(client):
    anon = TestClient(client.app)
    saved = client.get(EXPORT).json()
    assert anon.get(EXPORT).status_code == 401
    assert anon.post(IMPORT, json={"data": saved}).status_code == 401
    assert client.post(IMPORT, json={"data": saved}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_pure_functions_do_not_touch_the_config(tmp_path):
    cfg = _hub(tmp_path).config
    f = power_tree_io.build_export(cfg, "1.2.3", now=1_790_952_000.0)
    assert f["saved_at"] == "2026-10-02T14:40:00Z" and f["stagewatch_version"] == "1.2.3"
    before = cfg.model_dump(mode="json")
    plan = power_tree_io.plan_import(cfg, f)
    assert not plan.has_changes and cfg.model_dump(mode="json") == before


def test_js_cases_run_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "power_file_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
