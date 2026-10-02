import pytest
from fastapi.testclient import TestClient

from stagewatch.core.hub import Hub
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.integrations.osc_out import OscOutIntegration
from stagewatch.web.server import create_app


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    hub.add_integration(OscOutIntegration(hub))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def _setup_admin(client, pin="1234"):
    r = client.post("/api/admin/setup", json={"pin": pin})
    assert r.status_code == 200


def test_onboarding_then_login(client):
    assert client.get("/api/info").json()["admin_setup_required"] is True
    assert client.get("/api/admin/state").status_code == 401
    _setup_admin(client)
    assert client.post("/api/admin/setup", json={"pin": "9999"}).status_code == 409
    assert client.get("/api/admin/state").status_code == 200
    client.post("/api/admin/logout")
    client.cookies.clear()
    assert client.get("/api/admin/state").status_code == 401
    assert client.post("/api/admin/login", json={"pin": "0000"}).status_code == 401
    assert client.post("/api/admin/login", json={"pin": "1234"}).status_code == 200
    assert client.get("/api/admin/state").status_code == 200


def test_admin_state_never_leaks_secrets(client):
    _setup_admin(client)
    client.hub.config.esphome_devices.append(
        __import__("stagewatch.core.config", fromlist=["x"]).EsphomeDeviceConfig(
            id="n1", host="n1.local", noise_psk="SECRETKEY"))
    body = client.get("/api/admin/state").text
    assert "SECRETKEY" not in body and "pbkdf2" not in body


def test_adopt_ignores_legacy_password_field(client):
    _setup_admin(client)
    esp = client.hub.integrations.get("esphome")
    if esp is None:
        return
    from stagewatch.web.server import AdoptBody
    assert "password" not in AdoptBody.model_fields
    assert AdoptBody.model_validate({"host": "n1.local", "password": "x"}).host == "n1.local"


def test_user_dashboard_permissions(client):
    # default "wall" dashboard doesn't allow markers; "foh" does
    assert client.post("/api/markers", json={"label": "x", "dashboard": "wall"}).status_code == 403
    r = client.post("/api/markers", json={"label": "Aligned", "dashboard": "foh"})
    assert r.status_code == 200 and r.json()["label"] == "Aligned"
    assert client.post("/api/alarms/ack", json={"dashboard": "phone"}).status_code == 403
    assert client.post("/api/alarms/ack", json={"dashboard": "foh"}).status_code == 200


def test_cross_origin_post_refused(client):
    r = client.post("/api/markers", json={"label": "x", "dashboard": "foh"},
                    headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_emulated_nodes_and_snapshot(client):
    snap = client.get("/api/snapshot").json()
    ids = {e["id"] for e in snap["entities"]}
    assert {"sim_foh.temperature", "site.speed_of_sound"} <= ids
    assert len([d for d in snap["devices"] if d["id"] != "site"]) == 3


def test_websocket_snapshot_and_marker(client):
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["dashboard"]["slug"] == "foh"
        ws.send_json({"type": "add_marker", "label": "Doors"})
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "marker":
                assert msg["marker"]["label"] == "Doors"
                break
        else:
            pytest.fail("no marker broadcast")


def test_admin_config_endpoints(client):
    _setup_admin(client)
    r = client.put("/api/admin/thresholds", json=[
        {"id": "hot", "entity": "site.temperature", "above": 35, "level": 2}])
    assert r.status_code == 200
    assert client.put("/api/admin/dashboards", json=[]).status_code == 422
    r = client.put("/api/admin/osc", json={"destinations": [{"host": "127.0.0.1", "port": 9000}]})
    assert r.status_code == 200
    r = client.post("/api/admin/shows", json={"name": "Day 2"})
    assert r.json()["name"] == "Day 2"
    r = client.patch("/api/admin/devices/sim_foh", json={"area": "FOH riser"})
    assert r.status_code == 200
    assert client.hub.devices["sim_foh"].area == "FOH riser"


def test_history_endpoint(client):
    client.hub.recorder.flush()
    r = client.get("/api/history", params={"entities": "sim_foh.temperature,bogus"})
    assert r.status_code == 200
    assert list(r.json()) == ["sim_foh.temperature"]


def test_recovery_required_blocks_network_onboarding(tmp_path):
    (tmp_path / "config.yaml").write_text("site: [unclosed\n", encoding="utf-8")  # no PIN salvageable
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        info = c.get("/api/info").json()
        assert info["recovery_required"] is True and info["admin_setup_required"] is False
        r = c.post("/api/admin/setup", json={"pin": "1234"})
        assert r.status_code == 409 and "recovery" in r.json()["detail"].lower()
        assert c.get("/api/admin/state").status_code == 401
        assert hub.config.admin.pin_hash == ""


def test_recovery_keeps_pin_when_config_is_partly_invalid(tmp_path):
    import yaml

    from stagewatch.web.auth import hash_pin
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(
        {"admin": {"pin_hash": hash_pin("1234")}, "site": {"altitude_m": "high"}}), encoding="utf-8")
    with TestClient(create_app(Hub(tmp_path, emulate=True))) as c:
        info = c.get("/api/info").json()
        assert info["admin_setup_required"] is False and info["recovery_required"] is False
        assert c.post("/api/admin/setup", json={"pin": "9999"}).status_code == 409
        assert c.post("/api/admin/login", json={"pin": "1234"}).status_code == 200


def test_reset_admin_pin_command_reopens_onboarding(tmp_path):
    from stagewatch.core.config import reset_admin_pin
    (tmp_path / "config.yaml").write_text("site: [unclosed\n", encoding="utf-8")
    Hub(tmp_path, emulate=True).recorder.close()
    reset_admin_pin(tmp_path)
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        assert c.get("/api/info").json()["admin_setup_required"] is True
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def test_no_http_route_resets_the_pin(client):
    _setup_admin(client)
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert not any("reset" in p for p in paths)


def test_validation_errors_never_echo_submitted_values(client):
    # FastAPI's default 422 body includes the submitted value; a PIN must never come back.
    _setup_admin(client)
    secret = "98765-not-echoed"
    for body in ({"current": "1234", "new": [secret]}, {"current": "1234", "new": {"x": secret}}):
        r = client.put("/api/admin/pin", json=body)
        assert r.status_code == 422
        assert secret not in r.text
        assert all(set(d) <= {"loc", "msg", "type"} for d in r.json()["detail"])
    r = client.post("/api/admin/setup", json={"pin": [secret]})
    assert r.status_code in (409, 422) and secret not in r.text


def test_pages_and_static_files_are_rechecked_after_updates(client):
    # Without this a tablet can mix a new admin.js with a cached old common.js after an update.
    for path in ("/", "/admin", "/d/foh", "/static/common.js", "/static/admin.js", "/static/style.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache", path


def test_pages_load_scripts_by_content_hash(client):
    # A browser holding old cached scripts (v0.2.0 sent no cache headers) must still get the new
    # ones after an update: every /static/ reference carries a hash of that file's contents.
    import hashlib
    import re
    from stagewatch.web.server import STATIC
    for path in ("/", "/admin", "/d/foh"):
        html = client.get(path).text
        refs = re.findall(r'(?:src|href)="/static/([^"]+)"', html)
        assert refs, path
        for ref in refs:
            name, _, v = ref.partition("?v=")
            assert v == hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:10], ref
            assert client.get(f"/static/{ref}").status_code == 200, ref


def test_bad_websocket_frame_is_ignored_not_fatal(client):
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        ws.receive_json()  # snapshot
        ws.send_text("not json {")
        ws.send_json({"type": "ping"})
        for _ in range(20):
            if ws.receive_json().get("type") == "pong":
                break
        else:
            raise AssertionError("connection did not survive a bad frame")
