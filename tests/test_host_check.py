"""Host-header check (DNS rebinding) and the public snapshot's field allow-list."""

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.config import Config
from stagewatch.core.hub import Hub
from stagewatch.web.hostcheck import clean_allowed, host_ok
from stagewatch.web.server import create_app


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


@pytest.mark.parametrize("value", [
    "192.168.1.20:8080", "10.0.0.5", "127.0.0.1:8080", "[::1]:8080", "[fe80::1]", "localhost:8080", "LOCALHOST",
    "stagewatch.local:8080", "raspberrypi.local", "SHOWPC", "showpc:8080", "testserver", None])
def test_these_hosts_are_allowed(value):
    assert host_ok(value, "stagewatch", [])


@pytest.mark.parametrize("value", [
    "evil.example", "evil.example:8080", "a.b.c.example.com", "stagewatch.local.evil.example", "x.y.local",
    "", " ", "bad host", "evil.example:notaport", "[::1", "ex ample.com", "stagewatch.com"])
def test_these_hosts_are_refused(value):
    assert not host_ok(value, "stagewatch", [])


def test_an_admin_listed_name_is_allowed_and_a_look_alike_is_not():
    assert host_ok("show.example.org:8080", "stagewatch", ["show.example.org"])
    assert not host_ok("show.example.org.evil.net", "stagewatch", ["show.example.org"])
    assert host_ok("Crew.Example.org", "x", ["crew.example.org"])


def test_clean_allowed_keeps_plain_names_only_and_caps_the_list():
    assert clean_allowed(["Show.Example.org:8080", "bad name", "", 5, "a..b", "show.example.org", "x" * 300]) == ["show.example.org"]
    assert len(clean_allowed([f"h{i}.example.org" for i in range(100)])) == 20


def test_allowed_hosts_is_additive_config():
    assert Config().allowed_hosts == []
    assert Config.model_validate({"allowed_hosts": ["A.example.org", "no good"]}).allowed_hosts == ["a.example.org"]
    assert Config.model_validate({"allowed_hosts": "not a list"}).allowed_hosts == []


def test_a_foreign_host_gets_421_for_http_and_the_ordinary_clients_still_work(client):
    assert client.get("/api/info").status_code == 200                      # the test client's own Host: testserver
    for ok in ("192.168.1.20:8080", "localhost:8080", "stagewatch.local:8080", "[::1]:8080"):
        assert client.get("/api/info", headers={"host": ok}).status_code == 200, ok
    r = client.get("/api/info", headers={"host": "rebind.evil.example:8080"})
    assert r.status_code == 421 and "rebind.evil.example" not in r.text
    assert client.post("/api/admin/login", json={"pin": "1"}, headers={"host": "evil.example"}).status_code == 421


def test_a_foreign_host_cannot_open_the_live_feed(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"host": "evil.example"}) as ws:
            ws.receive_text()
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_the_admin_can_add_a_name_and_it_works_at_once(client):
    assert client.get("/api/info", headers={"host": "show.example.org"}).status_code == 421
    client.hub.config.allowed_hosts = ["show.example.org"]
    assert client.get("/api/info", headers={"host": "show.example.org:8080"}).status_code == 200


# ---------------------------------------------------------------- the public snapshot's field allow-list
SNAPSHOT_KEYS = {"now", "site", "show", "schedule", "wall_clock", "ontime_timer", "ontime_rundown", "globcon_meters",
                 "rme_units", "internet", "advice", "devices", "entities", "markers", "alarms", "sounding"}
SITE_KEYS = {"name", "reference_distance_m", "stale_after_s", "sensors", "pressure_source", "humidity_source",
             "c_out_of_range", "c_out_of_range_bounds", "baro", "time", "schedule_warn", "sun"}


def test_public_snapshot_and_site_fields_are_on_the_allow_list(client):
    snap = client.hub.snapshot()
    assert set(snap) <= SNAPSHOT_KEYS and "internet" in snap
    assert set(snap["site"]) <= SITE_KEYS
    client.hub.site_meta["sun"] = [{"id": "a.temperature", "kept_out": True}]
    snap = client.hub.snapshot()
    assert set(snap["site"]) <= SITE_KEYS and snap["site"]["sun"] == [{"id": "a.temperature", "kept_out": True}]
    assert set(snap["internet"]) <= {"state", "age", "for"}
    text = str(snap)
    assert "pbkdf2" not in text and "password" not in text.lower()
