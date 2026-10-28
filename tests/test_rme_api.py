"""The RME admin endpoints: admin and same-origin only, strict bodies, fixed error text, rate limit, and no
serial number or address on any public feed. Runs in emulate mode, so Test and Add make no network call
(the real probe against a fake unit is covered in test_rme_client.py). Addresses are placeholders.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.hub import Hub
from stagewatch.integrations.rme import RmeIntegration
from stagewatch.web.server import create_app

HOST = "192.168.1.50"
HOST2 = "192.168.1.51"
EVIL = {"Origin": "http://evil.example"}


@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path, emulate=True)
    h.add_integration(RmeIntegration(h, emulate=True))
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        now = [1000.0]
        c.app.state.rme_clock = lambda: now[0]
        c.now = now
        yield c


def later(client, secs=3.0):
    client.now[0] += secs


def add(client, host=HOST, name="", area="Stage left"):
    later(client)
    return client.post("/api/admin/rme", json={"host": host, "name": name, "area": area})


def test_every_route_needs_admin(client):
    anon = TestClient(client.app)
    assert anon.get("/api/admin/rme").status_code == 401
    assert anon.post("/api/admin/rme/test", json={"host": HOST}).status_code == 401
    assert anon.post("/api/admin/rme", json={"host": HOST}).status_code == 401
    assert anon.put("/api/admin/rme/x", json={}).status_code == 401
    assert anon.delete("/api/admin/rme/x").status_code == 401


def test_writes_refuse_another_origin(client):
    assert add(client).status_code == 200
    uid = client.hub.config.rme_devices[0].id
    later(client)
    assert client.post("/api/admin/rme/test", json={"host": HOST}, headers=EVIL).status_code == 403
    assert client.post("/api/admin/rme", json={"host": HOST2}, headers=EVIL).status_code == 403
    assert client.put(f"/api/admin/rme/{uid}", json={"name": "x"}, headers=EVIL).status_code == 403
    assert client.delete(f"/api/admin/rme/{uid}", headers=EVIL).status_code == 403
    assert len(client.hub.config.rme_devices) == 1 and client.hub.config.rme_devices[0].name == ""


def test_test_says_model_firmware_name_and_nothing_else(client):
    later(client)
    r = client.post("/api/admin/rme/test", json={"host": HOST})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["already_added"] is False
    assert body["message"] == "RME 12Mic, firmware 2.0.1 (simulated), named 'Simulated 12Mic'. Reading works."
    assert set(body) == {"ok", "model", "firmware", "name", "already_added", "message"}
    assert "SIM-" not in r.text and HOST not in r.text and "serial" not in r.text


def test_test_refuses_public_addresses_and_names_with_fixed_text_and_no_echo(client):
    for host in ("8.8.8.8", "12mic.local", "localhost", "127.0.0.1", "169.254.1.1", "0.0.0.0", "999.1.1.1", "http://192.168.1.5", "134744072"):
        later(client)
        r = client.post("/api/admin/rme/test", json={"host": host})
        assert r.status_code == 200, host
        assert r.json() == {"ok": False, "category": "not_local", "message": "That address is not on a private network"}
        assert host not in r.text


def test_test_is_rate_limited_to_one_every_two_seconds(client):
    later(client)
    assert client.post("/api/admin/rme/test", json={"host": HOST}).status_code == 200
    client.now[0] += 1.9
    r = client.post("/api/admin/rme/test", json={"host": HOST})
    assert r.status_code == 429 and "Nothing has been changed" in r.json()["detail"]
    assert client.post("/api/admin/rme", json={"host": HOST}).status_code == 429
    client.now[0] += 0.2
    assert client.post("/api/admin/rme/test", json={"host": HOST}).status_code == 200


@pytest.mark.parametrize("body", [
    {}, {"host": ""}, {"host": 5}, {"host": None}, {"host": HOST, "port": 0}, {"host": HOST, "port": 65536},
    {"host": HOST, "port": "80"}, {"host": HOST, "port": True}, {"host": HOST, "port": 80.5}, {"host": "x" * 65},
    {"host": HOST, "extra": 1}, {"host": HOST, "serial": "SN-1"}, {"host": HOST, "name": "n" * 41},
    {"host": HOST, "name": "line\nbreak"}, {"host": HOST, "name": "zero​width"}, {"host": HOST, "area": "a" * 41},
    {"host": HOST, "name": 5}, [], "text",
])
def test_bad_bodies_are_refused_without_echo(client, body):
    later(client)
    for url in ("/api/admin/rme/test", "/api/admin/rme"):
        r = client.post(url, json=body)
        assert r.status_code == 422, (url, body)
        for value in (body.values() if isinstance(body, dict) else []):
            if isinstance(value, str) and len(value) >= 4:
                assert value not in r.text, "the error echoed what was sent"
    assert client.hub.config.rme_devices == []


def test_add_list_save_remove_round_trip(client):
    r = add(client)
    assert r.status_code == 200 and r.json() == {"id": "simulated_12mic"}
    cfg = client.hub.config.rme_devices[0]
    assert (cfg.host, cfg.port, cfg.area, cfg.name) == (HOST, 80, "Stage left", "") and cfg.serial.startswith("SIM-")
    listing = client.get("/api/admin/rme").json()
    assert listing["max_units"] == 8 and listing["emulate"] is True
    (row,) = listing["units"]
    assert row["host"] == HOST and row["serial_set"] is True and "serial" not in row
    assert set(row) == {"id", "name", "area", "host", "port", "serial_set", "record_input_changes", "status", "detail",
                        "phase", "hint", "model", "firmware", "last_seen_age_s"}
    later(client)
    assert client.put("/api/admin/rme/simulated_12mic", json={"name": "FOH pre", "area": "FOH", "record_input_changes": True}).json() == {"ok": True}
    assert (cfg.name, cfg.area, cfg.record_input_changes) == ("FOH pre", "FOH", True)
    assert client.hub.devices["rme_simulated_12mic"].name == "FOH pre"
    assert client.put("/api/admin/rme/simulated_12mic", json={}).status_code == 200            # nothing to change is fine
    assert client.delete("/api/admin/rme/simulated_12mic").json() == {"ok": True}
    assert client.hub.config.rme_devices == [] and "rme_simulated_12mic" not in client.hub.devices
    assert client.delete("/api/admin/rme/simulated_12mic").status_code == 404
    assert client.put("/api/admin/rme/simulated_12mic", json={"name": "x"}).status_code == 404


@pytest.mark.parametrize("body", [{"name": 5}, {"port": 0}, {"port": "80"}, {"host": ""}, {"replace_unit": "yes"},
                                  {"record_input_changes": 1}, {"serial": "x"}, {"unknown": 1}, {"name": "a\nb"}])
def test_bad_save_bodies_are_422_and_change_nothing(client, body):
    assert add(client).status_code == 200
    later(client)
    r = client.put("/api/admin/rme/simulated_12mic", json=body)
    assert r.status_code == 422
    cfg = client.hub.config.rme_devices[0]
    assert (cfg.name, cfg.port) == ("", 80)


def test_the_same_unit_twice_is_refused_with_fixed_text(client):
    assert add(client).status_code == 200
    r = add(client)
    assert r.status_code == 409 and r.json()["detail"] == "That unit has already been added. Nothing has been changed."
    assert len(client.hub.config.rme_devices) == 1


def test_a_ninth_unit_is_refused(client):
    for i in range(8):
        assert add(client, host=f"192.168.1.{10 + i}", name=f"Unit {i}").status_code == 200
    r = add(client, host="192.168.1.99", name="Ninth")
    assert r.status_code == 409 and "8 RME units" in r.json()["detail"]
    assert len(client.hub.config.rme_devices) == 8


def test_a_changed_address_to_a_public_one_is_refused_and_nothing_changes(client):
    assert add(client).status_code == 200
    later(client)
    r = client.put("/api/admin/rme/simulated_12mic", json={"host": "8.8.8.8"})
    assert r.status_code == 422 and r.json()["detail"] == "That address is not on a private network. Nothing has been changed."
    assert client.hub.config.rme_devices[0].host == HOST


def wait_for_status(client, want="ok", timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        snap = client.get("/api/snapshot").json().get("rme_units")
        if snap and snap["units"] and snap["units"][0]["status"] == want:
            return snap
        time.sleep(0.05)
    raise AssertionError("the simulated unit did not report in time")


def test_no_serial_or_address_on_any_public_feed_and_serial_masked_for_admin(client):
    assert add(client, name="Stage L").status_code == 200
    snap = wait_for_status(client)
    cfg = client.hub.config.rme_devices[0]
    serial = cfg.serial
    assert serial.startswith("SIM-") and len(serial) > 6
    (unit,) = snap["units"]
    assert unit["name"] == "Stage L" and unit["temp_c"] is not None
    public = [client.get(u).text for u in ("/api/snapshot", "/api/info", "/api/dashboard/foh", "/api/dashboard/wall")]
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["rme_units"]["units"][0]["id"] == cfg.id
        public.append(json.dumps(first))
    for text in public:
        for private in (HOST, serial, "SIM-", "gptp", "Group A", "dest_addr", "stream_id"):
            assert private not in text, private
    state = client.get("/api/admin/state")
    assert serial not in state.text and state.json()["config"]["rme_devices"][0]["serial"] == "set"
    assert state.json()["config"]["rme_devices"][0]["host"] == HOST            # the address is for the admin only
    assert state.json()["rme"]["units"][0]["serial_set"] is True
    assert state.json()["integrations"] and any(i["manifest"]["domain"] == "rme" for i in state.json()["integrations"])
    assert HOST not in json.dumps([i for i in state.json()["integrations"] if i["manifest"]["domain"] == "rme"])


def test_never_added_by_default_and_the_snapshot_key_is_none_without_units(client):
    from stagewatch.core import cards
    assert "rme_units" not in {c for v in cards.LAYOUT_DEFAULTS.values() for c in v}
    assert client.get("/api/snapshot").json()["rme_units"] is None


def test_the_support_bundle_redaction_hides_address_and_serial():
    from stagewatch.diagnostics import redact_config
    raw = {"rme_devices": [{"id": "u", "host": HOST, "port": 80, "serial": "SN-SECRET-1", "name": "Unit"}]}
    out = json.dumps(redact_config(raw))
    assert HOST not in out and "SN-SECRET-1" not in out and '"name": "Unit"' in out
