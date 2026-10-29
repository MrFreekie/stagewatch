"""Wi-Fi card: the join-code text rules live in common.js (checked by source), the networks are stored in the
config, only reach a dashboard that has the card, and never appear in diagnostics, logs or errors."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch import diagnostics
from stagewatch.core import cards
from stagewatch.core.config import Config
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

PIN = "4711"
EVIL = {"Origin": "http://evil.example"}
PASS = "Zq9!hunter;2:x"
NET = {"label": "Crew", "ssid": "Crew Net", "password": PASS, "security": "WPA", "note": "2.4 GHz only"}


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
        yield c


def with_card(c, on=True):
    rows = c.get("/api/info").json()["dashboards"]
    rows[0]["cards"] = [x for x in rows[0]["cards"] if x != "wifi"] + (["wifi"] if on else [])
    assert c.put("/api/admin/dashboards", json=rows).status_code == 200
    return rows[0]["slug"]


def test_card_registered_off_by_default_and_half_capable():
    assert "wifi" in cards.KNOWN_CARDS and "wifi" in cards.HALF_CAPABLE
    for layout in cards.LAYOUT_DEFAULTS:
        assert "wifi" not in cards.default_cards(layout)


def test_old_config_loads_and_bad_entries_are_salvaged():
    assert Config.model_validate({"dashboards": [{"slug": "foh"}]}).wifi.networks == []
    raw = {"wifi": {"networks": [NET | {"id": "a"}, {"ssid": ""}, "junk", NET | {"id": "a"}, {**NET, "id": "b", "security": "x"}]}}
    nets = Config.model_validate(raw).wifi.networks
    assert [n.id for n in nets] == ["a", "b"] and nets[1].security == "WPA"


def test_join_code_text_escapes_and_quotes():
    js = (Path(__file__).parents[1] / "src/stagewatch/web/static/common.js").read_text(encoding="utf-8")
    bs = chr(92) * 2
    assert f'replace(/([{bs};,:"])/g, "{bs}$1")' in js
    assert 'WIFI:T:${open ? "nopass" : "WPA"};S:${val(n.ssid)};' in js and "H:true;" in js


def test_only_a_dashboard_with_the_card_gets_passwords(client):
    slug = with_card(client, on=False)
    assert client.put("/api/admin/wifi", json={"networks": [NET]}).status_code == 200
    assert PASS not in client.get("/api/snapshot").text + client.get("/api/info").text
    assert client.get(f"/api/wifi?dashboard={slug}").json() == {"networks": []}
    assert client.get("/api/wifi?dashboard=nope").json() == {"networks": []}
    with_card(client)
    got = client.get(f"/api/wifi?dashboard={slug}").json()["networks"]
    assert got[0]["ssid"] == "Crew Net" and got[0]["password"] == PASS
    assert client.get(f"/api/wifi?dashboard={slug}").headers["cache-control"] == "no-store"


def test_save_needs_admin_and_same_origin_and_checks_input(client):
    assert client.put("/api/admin/wifi", json={"networks": [NET]}, headers=EVIL).status_code == 403
    for bad in ({**NET, "password": "short"}, {**NET, "ssid": " "}, {**NET, "ssid": "é" * 20}, {**NET, "password": "bad\x01pass12"}):
        r = client.put("/api/admin/wifi", json={"networks": [bad]})
        assert r.status_code == 422 and "short" not in r.text and "pass12" not in r.text
    assert client.put("/api/admin/wifi", json={"networks": [NET | {"security": "nopass", "password": ""}]}).status_code == 200
    assert client.put("/api/admin/wifi", json={"networks": [NET] * 7}).status_code == 422
    client.post("/api/admin/logout")
    client.cookies.clear()
    assert client.put("/api/admin/wifi", json={"networks": []}).status_code in (401, 403)


def test_password_stays_out_of_diagnostics_and_logs(client, caplog):
    with caplog.at_level(logging.DEBUG):
        assert client.put("/api/admin/wifi", json={"networks": [NET]}).status_code == 200
    assert PASS not in caplog.text
    cfg = client.hub.config.model_dump(mode="json")
    known = diagnostics.known_secrets(cfg)
    assert PASS in known
    red = json.dumps(diagnostics.redact_config(cfg, known))
    assert PASS not in red and "Crew Net" in red
    assert PASS not in diagnostics.redact_text(f"joined with {PASS}", known)


def test_emulate_shows_demo_networks_when_none_are_set(client):
    slug = with_card(client)
    nets = client.get(f"/api/wifi?dashboard={slug}").json()["networks"]
    assert len(nets) == 3 and [n["security"] for n in nets] == ["WPA", "WPA", "nopass"]
