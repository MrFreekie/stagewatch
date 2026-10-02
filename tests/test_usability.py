"""Usability batch: connect-a-tablet addresses, diagnostics bundle, vendored QR code, static rules."""

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch import diagnostics, netinfo
from stagewatch.core.config import EsphomeDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "stagewatch" / "web" / "static"

# Obviously fake secrets (never real keys).
FAKE_KEY = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU="   # 44-char base64
FAKE_PW = "hunter2-fake-password"
FAKE_PSK2 = "c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0LTEyMzQ="  # 44-char base64, appears only in a log
PIN = "4711"


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    app = create_app(hub, lan_addresses=lambda: ["192.0.2.10", "198.51.100.7"])
    app.state.port = 8123
    with TestClient(app) as c:
        c.hub = hub
        c.data = tmp_path
        yield c


def _admin(c):
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200


# ------------------------------------------------------------ connect a tablet
def test_connect_info_is_admin_only_and_uses_configured_port(client):
    assert client.get("/api/admin/connect").status_code == 401
    _admin(client)
    r = client.get("/api/admin/connect").json()
    assert r["port"] == 8123 and r["addresses"] == ["192.0.2.10", "198.51.100.7"]
    foh = next(d for d in r["dashboards"] if d["slug"] == "foh")
    assert foh["ip"] == ["http://192.0.2.10:8123/d/foh", "http://198.51.100.7:8123/d/foh"]
    assert foh["mdns"] == "http://stagewatch.local:8123/d/foh"
    assert r["home"]["ip"][0] == "http://192.0.2.10:8123"


def test_wall_address_is_public_but_minimal(client):
    r = client.get("/api/dashboard/wall/address").json()
    assert r == {"url": "http://192.0.2.10:8123/d/wall"}      # one address, no interface list
    assert client.get("/api/dashboard/foh/address").json() == {"url": ""}   # no connect_footer card
    assert client.get("/api/dashboard/nope/address").json() == {"url": ""}


def test_local_ipv4_excludes_loopback_and_link_local(monkeypatch):
    class Ip:
        def __init__(self, ip): self.ip = ip

    class Ad:
        def __init__(self, ips): self.ips = ips

    monkeypatch.setattr(netinfo.ifaddr, "get_adapters", lambda: [
        Ad([Ip("127.0.0.1"), Ip("169.254.3.4"), Ip("192.0.2.5"), Ip(("fe80::1", 0, 0)), Ip("192.0.2.5")]),
        Ad([Ip("198.51.100.9"), Ip("0.0.0.0")])])
    assert netinfo.local_ipv4() == ["192.0.2.5", "198.51.100.9"]


# ------------------------------------------------------------------ diagnostics
def _zip_text(resp) -> dict[str, str]:
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    return {n: z.read(n).decode("utf-8") for n in z.namelist()}


def test_diagnostics_requires_admin_and_same_origin(client):
    assert client.get("/api/admin/diagnostics").status_code == 401
    _admin(client)
    assert client.get("/api/admin/diagnostics", headers={"origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/admin/diagnostics").status_code == 200


def test_diagnostics_contains_no_secret_values(client):
    _admin(client)
    hub, data = client.hub, client.data
    hub.config.esphome_devices.append(EsphomeDeviceConfig(
        id="n1", host="n1.local", noise_psk=FAKE_KEY))
    hub.config.site.name = f"Show {FAKE_PSK2}"        # a secret-looking VALUE under an innocent key
    pin_hash = hub.config.admin.pin_hash
    assert pin_hash.startswith("pbkdf2")
    secret_key = (data / "secret.key").read_bytes()
    logs = data / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "stagewatch.log").write_text(
        f"INFO connecting with key {FAKE_PSK2}\nWARN password={FAKE_PW} rejected\nDEBUG hash {pin_hash}\n"
        f"INFO normal line about device n1\n", encoding="utf-8")
    (logs / "launcher.log").write_text("launcher started\n", encoding="utf-8")

    r = client.get("/api/admin/diagnostics")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert re.fullmatch(r'attachment; filename="stagewatch-diagnostics-\d{8}-\d{6}\.zip"',
                        r.headers["content-disposition"])
    files = _zip_text(r)
    for want in ("info.json", "devices.json", "config.json", "updater.json", "alarm_log.json",
                 "logs/stagewatch.log", "logs/launcher.log", "README.txt"):
        assert want in files, want
    assert "secret.key" not in " ".join(files)
    blob = "\n".join(files.values())
    raw = r.content
    for secret in (FAKE_KEY, FAKE_PSK2, FAKE_PW, pin_hash, PIN):
        assert secret not in blob, secret
    assert secret_key not in raw
    assert "pbkdf2" not in blob
    # still useful: the normal line and the device survive
    assert "normal line about device n1" in files["logs/stagewatch.log"]
    cfg = json.loads(files["config.json"])
    assert cfg["admin"] == {"pin_set": True}
    n1 = cfg["esphome_devices"][0]
    assert n1["host"] == "n1.local" and n1["noise_psk"] == diagnostics.REDACTED
    assert "password" not in n1
    info = json.loads(files["info.json"])
    assert info["version"] and "platform" in info and "uptime_s" in info
    assert any(d["id"] == "site" for d in json.loads(files["devices.json"]))


def test_redaction_by_key_name_and_value_pattern():
    cfg = {"noise_psk": "abc", "nested": [{"api_key": "x", "ok": 5, "pin_hash": "h", "keepalive": "yes"}],
           "note": f"see {FAKE_KEY} here", "empty_password": "", "n": 3}
    out = diagnostics.redact_config(cfg)
    assert out["noise_psk"] == "[redacted]"
    assert out["nested"][0] == {"api_key": "[redacted]", "ok": 5, "pin_hash": "[redacted]", "keepalive": "yes"}
    assert FAKE_KEY not in out["note"] and out["empty_password"] == "" and out["n"] == 3
    text = diagnostics.redact_text(f"x pbkdf2_sha256$200000$c2FsdA==$ZGlnZXN0 y psk: {FAKE_PW} z", known=("zzzz-known",))
    assert "pbkdf2" not in text and FAKE_PW not in text and text.startswith("x ") and text.endswith(" z")
    assert diagnostics.redact_text("say zzzz-known now", known=("zzzz-known",)) == "say [redacted] now"


def test_diagnostics_size_cap_shrinks_logs():
    big = [f"line {i} " + "x" * 200 for i in range(200_000)]
    data = diagnostics.assemble_zip({"info.json": "{}"}, {"stagewatch.log": big}, cap=200_000)
    assert len(data) <= 200_000
    z = zipfile.ZipFile(io.BytesIO(data))
    assert "info.json" in z.namelist()


def test_tail_lines_reads_only_the_tail(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("\n".join(f"l{i}" for i in range(5000)) + "\n", encoding="utf-8")
    lines = diagnostics.tail_lines(p, n=2000)
    assert len(lines) == 2000 and lines[-1] == "l4999" and lines[0] == "l3000"
    assert diagnostics.tail_lines(tmp_path / "missing.log") is None


# ------------------------------------------------------------ static / frontend rules
def test_vendored_qr_keeps_licence_and_source_note():
    js = (STATIC / "vendor" / "qrcode.js").read_text(encoding="utf-8")
    assert "Copyright (c) 2009 Kazuhiko Arase" in js and "Licensed under the MIT license" in js
    assert "github.com/kazuhikoarase/qrcode-generator" in js and "1.4.4" in js
    lic = (STATIC / "vendor" / "qrcode.LICENSE.txt").read_text(encoding="utf-8")
    assert "Permission is hereby granted" in lic and "Kazuhiko Arase" in lic


def test_no_external_scripts_or_styles_in_pages():
    for page in ("admin.html", "dashboard.html", "index.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        for src in re.findall(r'(?:src|href)="([^"]+)"', html):
            assert not re.match(r"(https?:)?//", src) or "buymeacoffee" in src, (page, src)


def test_dashboard_has_permanent_sound_toggle_and_banner_hooks():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="sound-toggle"' in html and 'id="sound-toggle" type="button"' in html
    assert "hidden" not in re.search(r'<button[^>]*id="sound-toggle"[^>]*>', html).group(0)
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert "Disconnected from Stagewatch" in common and "CONNECTION_GRACE_S = 3" in common
    assert "SW.connection.report" in (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "SW.heartbeat" in (STATIC / "admin.js").read_text(encoding="utf-8")


def test_static_vendor_is_served(client):
    r = client.get("/static/vendor/qrcode.js")
    assert r.status_code == 200 and "qrcode" in r.text
