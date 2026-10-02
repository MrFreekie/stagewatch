"""Ignoring discovered ESPHome nodes, and the /ws message size cap."""

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.config import MAX_IGNORED, Config
from stagewatch.core.hub import Hub
from stagewatch.integrations.esphome import EsphomeIntegration, discovery_key
from stagewatch.web.limits import WS_MAX_MESSAGE
from stagewatch.web.server import create_app


def _node(name, mac="", addr="192.0.2.10"):
    return {"name": name, "host": f"{name}.local", "address": addr, "port": 6053, "mac": mac,
            "friendly_name": name.title(), "board": "esp32", "version": "", "encrypted": False}


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    esp = EsphomeIntegration(hub, emulate=True)
    hub.add_integration(esp)
    esp.discovered["xiao-ir"] = _node("xiao-ir", mac="AA:BB:CC:00:11:22")
    esp.discovered["stage-l"] = _node("stage-l", addr="192.0.2.11")  # no MAC in the TXT record
    with TestClient(create_app(hub)) as c:
        c.hub, c.esp = hub, esp
        r = c.post("/api/admin/setup", json={"pin": "1234"})
        assert r.status_code == 200
        yield c


def test_key_is_mac_when_present_else_name():
    assert discovery_key(_node("a", mac="AA:BB:CC:00:11:22")) == "mac:aabbcc001122"
    assert discovery_key(_node("a", mac="aabbcc001122")) == "mac:aabbcc001122"
    assert discovery_key(_node("Stage-L")) == "name:stage-l"
    assert discovery_key(_node("x", mac="not-a-mac")) == "name:x"


def test_config_default_is_empty_and_old_files_load():
    assert Config().esphome_ignored == []
    assert Config.model_validate({"schema_version": 2, "mdns_name": "x"}).esphome_ignored == []


def test_ignore_hides_and_unignore_restores(client):
    names = lambda: {d["name"] for d in client.get("/api/admin/state").json()["discovered"]}  # noqa: E731
    assert names() == {"xiao-ir", "stage-l"}
    key = discovery_key(client.esp.discovered["xiao-ir"])
    assert client.post("/api/admin/esphome/ignore", json={"key": key}).status_code == 200
    state = client.get("/api/admin/state").json()
    assert names() == {"stage-l"}
    assert state["ignored"] == [{"key": key, "name": "xiao-ir", "host": "xiao-ir.local",
                                 "friendly_name": "Xiao-Ir", "seen": True}]
    # ignoring twice does not duplicate; it survives a save and reload
    assert client.post("/api/admin/esphome/ignore", json={"key": key}).status_code == 200
    assert client.hub.config.esphome_ignored == [key]
    assert Config.model_validate(client.hub.config.model_dump()).esphome_ignored == [key]
    # a node that left the network stays in the list, labelled as not seen, and can be removed
    del client.esp.discovered["xiao-ir"]
    assert client.get("/api/admin/state").json()["ignored"][0]["seen"] is False
    assert client.post("/api/admin/esphome/unignore", json={"key": key}).status_code == 200
    assert client.hub.config.esphome_ignored == []
    client.esp.discovered["xiao-ir"] = _node("xiao-ir", mac="AA:BB:CC:00:11:22")
    assert names() == {"xiao-ir", "stage-l"}


def test_ignore_by_name_when_no_mac(client):
    assert client.post("/api/admin/esphome/ignore", json={"key": "name:stage-l"}).status_code == 200
    assert {d["name"] for d in client.esp.discovered_list()} == {"xiao-ir"}


def test_ignore_validation_and_fixed_errors(client):
    r = client.post("/api/admin/esphome/ignore", json={"key": "mac:deadbeef0000"})
    assert r.status_code == 404 and "deadbeef" not in r.text  # unknown keys are refused, not stored
    assert client.hub.config.esphome_ignored == []
    assert client.post("/api/admin/esphome/unignore", json={"key": "mac:deadbeef0000"}).status_code == 404
    assert client.post("/api/admin/esphome/ignore", json={"key": ""}).status_code == 422
    assert client.post("/api/admin/esphome/ignore", json={"key": "x" * 81}).status_code == 422
    big = client.post("/api/admin/esphome/ignore", content=b'{"key":"' + b"x" * 70000 + b'"}',
                      headers={"content-type": "application/json"})
    assert big.status_code == 413


def test_ignore_list_is_capped(client):
    client.hub.config.esphome_ignored = [f"name:filler{i}" for i in range(MAX_IGNORED)]
    r = client.post("/api/admin/esphome/ignore", json={"key": "name:stage-l"})
    assert r.status_code == 409 and len(client.hub.config.esphome_ignored) == MAX_IGNORED


def test_ignore_routes_need_admin_and_same_origin(client):
    key = "name:stage-l"
    assert client.post("/api/admin/esphome/ignore", json={"key": key},
                       headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/admin/esphome/unignore", json={"key": key},
                       headers={"origin": "http://evil.example"}).status_code == 403
    assert client.hub.config.esphome_ignored == []
    client.cookies.clear()
    assert client.post("/api/admin/esphome/ignore", json={"key": key}).status_code == 401
    assert client.post("/api/admin/esphome/unignore", json={"key": key}).status_code == 401
    assert client.hub.config.esphome_ignored == []


# ---------------------------------------------------------------- /ws size cap
def test_ws_normal_traffic_and_oversize_closes_1009(client):
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_json({"type": "ping"})
        for _ in range(20):
            if ws.receive_json()["type"] == "pong":
                break
        else:
            pytest.fail("no pong")
        ws.send_text(json.dumps({"type": "add_marker", "label": "x" * (WS_MAX_MESSAGE + 1)}))
        with pytest.raises(Exception) as ei:
            for _ in range(50):
                ws.receive_json()
        assert getattr(ei.value, "code", None) == 1009
    assert not any(m.label.startswith("xxxx") for m in client.hub.recorder.markers())


def test_ws_cap_enforced_by_uvicorn_itself(tmp_path):
    """The real server (uvicorn ws_max_size) refuses an oversize frame; small ones still work."""
    import socket

    import uvicorn
    import websockets.sync.client as wsc

    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    app = create_app(hub)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                           ws_max_size=WS_MAX_MESSAGE))
    t = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            threading.Event().wait(0.05)
        assert server.started
        with wsc.connect(f"ws://127.0.0.1:{port}/ws?dashboard=foh", max_size=None) as ws:
            assert json.loads(ws.recv())["type"] == "snapshot"
            ws.send(json.dumps({"type": "ping"}))
            while json.loads(ws.recv())["type"] != "pong":
                pass
            ws.send("x" * (WS_MAX_MESSAGE + 1))
            with pytest.raises(Exception) as ei:
                for _ in range(50):
                    ws.recv()
            assert getattr(getattr(ei.value, "rcvd", None), "code", None) == 1009
    finally:
        server.should_exit = True
        t.join(5)
