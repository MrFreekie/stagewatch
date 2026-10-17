"""Per-event logo (plans/custom-logo-upload.md phase 1): file sniffing, the upload API, the
public URL for the current event only, and the reload broadcast."""

from __future__ import annotations

import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.hub import Hub
from stagewatch.web import branding as brand
from stagewatch.web.server import create_app

PIN = "4711"
EVIL = {"Origin": "http://evil.example"}


def png(w=64, h=48, apng=False) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    if apng:
        out += chunk(b"acTL", struct.pack(">II", 2, 0))
    raw = b"".join(b"\x00" + b"\x80" * w for _ in range(h))
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def jpeg(w=100, h=40) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def webp(kind=b"VP8X", w=200, h=100, animated=False) -> bytes:
    if kind == b"VP8X":
        p = bytes([0x02 if animated else 0, 0, 0, 0]) + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    elif kind == b"VP8L":
        p = b"\x2f" + struct.pack("<I", (w - 1) | ((h - 1) << 14)) + b"\x00" * 8
    else:
        p = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", w, h) + b"\x00" * 8
    body = b"WEBP" + kind + struct.pack("<I", len(p)) + p
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------- sniffing
@pytest.mark.parametrize("data,ext,size", [
    (png(64, 48), "png", (64, 48)), (jpeg(100, 40), "jpg", (100, 40)),
    (webp(b"VP8X"), "webp", (200, 100)), (webp(b"VP8L", 300, 50), "webp", (300, 50)),
    (webp(b"VP8 ", 120, 60), "webp", (120, 60))])
def test_sniff_accepts(data, ext, size):
    assert brand.sniff(data) == (ext, *size)


@pytest.mark.parametrize("data,status", [
    (b"", 422), (png()[:20], 422), (b"\xff\xd8\xff\xe0\x00", 422), (png(4097, 40), 422), (png(20, 40), 422),
    (b"GIF89a" + b"\x00" * 40, 415), (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40, 415),
    (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', 415),
    (png(apng=True), 415), (webp(animated=True), 415), (b"x" * (brand.LOGO_MAX_BYTES + 1), 413)],
    ids=lambda v: str(v)[:20])
def test_sniff_refuses(data, status):
    with pytest.raises(brand.LogoError) as e:
        brand.sniff(data)
    assert e.value.status == status


def test_svg_and_animated_messages():
    with pytest.raises(brand.LogoError, match="SVG"):
        brand.sniff(b"<svg></svg>")
    with pytest.raises(brand.LogoError, match="still image"):
        brand.sniff(png(apng=True))


@pytest.mark.parametrize("bad", ["..", "../1", "1/2", "C:", "-1", "0", "1a", ""])
def test_ids_validated_before_paths(bad):
    with pytest.raises(brand.LogoError):
        brand.valid_id(bad)


# ---------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
        yield c


def eid(c) -> int:
    return c.hub.recorder.event_id


def put(c, data, variant="light", event=None, **kw):
    return c.put(f"/api/admin/events/{event or eid(c)}/logo/{variant}", content=data,
                 headers={"Content-Type": "application/octet-stream", **kw.pop("headers", {})}, **kw)


def test_no_logo_by_default(client):
    b = client.get("/api/info").json()["branding"]
    assert b == {"light_url": None, "dark_url": None, "on_dark": "as_is",
                 "show_on_admin": True, "show_on_index": True}
    assert client.get(f"/branding/event/{eid(client)}/light").status_code == 404


def test_auth_and_origin(client):
    client.post("/api/admin/logout")
    assert put(client, png()).status_code == 401
    assert client.delete(f"/api/admin/events/{eid(client)}/logo/light").status_code == 401
    assert client.get(f"/api/admin/events/{eid(client)}/branding").status_code == 401
    assert client.post("/api/admin/login", json={"pin": PIN}).status_code == 200
    assert put(client, png(), headers=EVIL).status_code == 403
    assert client.put(f"/api/admin/events/{eid(client)}/branding", json={}, headers=EVIL).status_code == 403
    assert not (client.hub.data_dir / "branding").exists()


def test_upload_serve_replace_delete(client, tmp_path):
    r = put(client, png())
    assert r.status_code == 200, r.text
    assert r.json()["light"]["width"] == 64 and "path" not in r.json()["light"]
    folder = tmp_path / "branding" / "events" / str(eid(client))
    assert sorted(p.name for p in folder.glob("logo-*")) == ["logo-light.png"]
    url = client.get("/api/info").json()["branding"]["light_url"]
    g = client.get(url)
    assert g.status_code == 200 and g.content == png()
    assert g.headers["content-type"] == "image/png"
    assert g.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in g.headers["content-security-policy"]
    assert "immutable" in g.headers["cache-control"]
    assert client.get(url.split("?")[0]).headers["cache-control"] == "no-cache"
    # Replace with a different type: one file per variant.
    assert put(client, jpeg()).status_code == 200
    assert sorted(p.name for p in folder.glob("logo-*")) == ["logo-light.jpg"]
    assert client.delete(f"/api/admin/events/{eid(client)}/logo/light").status_code == 200
    assert client.get("/api/info").json()["branding"]["light_url"] is None
    assert client.get(url).status_code == 404


def test_refusals(client):
    assert put(client, b"GIF89a" + b"\x00" * 40).status_code == 415
    assert put(client, png()[:30]).status_code == 422
    assert put(client, png(), variant="other").status_code == 404
    assert put(client, png(), event="999").status_code == 404
    assert put(client, png(), event="..").status_code == 404
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (brand.LOGO_MAX_BYTES + 10)
    assert put(client, big).status_code == 413
    assert put(client, b"x" * (brand.LOGO_MAX_BYTES + 100_000)).status_code == 413
    assert not any((client.hub.data_dir / "branding").rglob("logo-*")) if (client.hub.data_dir / "branding").exists() else True


def test_body_needs_content_length(client):
    def chunks():
        yield png()
    r = client.put(f"/api/admin/events/{eid(client)}/logo/light", content=chunks())
    assert r.status_code == 411


def test_dark_variant_and_options(client):
    assert put(client, png()).status_code == 200
    assert put(client, webp(), variant="dark").status_code == 200
    r = client.put(f"/api/admin/events/{eid(client)}/branding",
                   json={"on_dark": "plate", "show_on_admin": False, "show_on_index": True})
    assert r.status_code == 200
    b = client.get("/api/info").json()["branding"]
    assert b["dark_url"] and b["on_dark"] == "plate" and b["show_on_admin"] is False
    bad = client.put(f"/api/admin/events/{eid(client)}/branding", json={"on_dark": "<script>"})
    assert bad.status_code == 422 and "<script>" not in bad.text


def test_only_current_event_is_public_and_copy(client):
    first = eid(client)
    assert put(client, png()).status_code == 200
    r = client.post("/api/admin/shows", json={"name": "Day 1", "event": "new", "event_name": "Next"})
    assert r.status_code == 200, r.text
    second = eid(client)
    assert second != first
    assert client.get("/api/info").json()["branding"]["light_url"] is None
    assert client.get(f"/branding/event/{first}/light").status_code == 404     # old event: not public
    assert client.get(f"/api/admin/events/{first}/logo/light").status_code == 200   # admin preview
    br = client.get(f"/api/admin/events/{second}/branding").json()
    assert br["previous"] == {"id": first, "name": br["previous"]["name"]}
    assert client.post(f"/api/admin/events/{second}/logo/copy", json={"from_event_id": first}).status_code == 200
    assert client.get("/api/info").json()["branding"]["light_url"]
    # Independent copies: removing the new one leaves the old one.
    client.delete(f"/api/admin/events/{second}/logo/light")
    assert client.get(f"/api/admin/events/{first}/logo/light").status_code == 200
    assert client.post(f"/api/admin/events/{second}/logo/copy", json={"from_event_id": second}).status_code == 422


def test_upload_reloads_open_screens(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()   # snapshot
        assert put(client, png()).status_code == 200
        for _ in range(50):
            if ws.receive_json().get("type") == "reload":
                break
        else:
            pytest.fail("no reload message")


def test_backup_covers_branding():
    from stagewatch import backup
    assert "branding" in backup.DIRS


def test_folder_from_rolled_back_event_is_ignored(client, tmp_path):
    """A reused event id (database rolled back, branding/ kept) must not show the old logo."""
    assert put(client, png()).status_code == 200
    (tmp_path / "branding" / "events" / str(eid(client)) / brand.OWNER).write_text("someone else")
    store = brand.BrandingStore(tmp_path, lambda _id: "new event")
    assert store.info(eid(client)) == {"light": None, "dark": None}
    store.save(eid(client), "dark", png())   # claiming the folder drops the old files
    assert store.info(eid(client))["light"] is None and store.info(eid(client))["dark"]


def test_webp_with_animation_chunks_refused():
    with pytest.raises(brand.LogoError, match="still image"):
        brand.sniff(webp() + b"ANMF" + b"\x00" * 20)
