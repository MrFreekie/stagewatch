"""Show report: the admin routes (auth, errors with fixed wording, busy, headers, no leaks)."""
from __future__ import annotations

import io
import re
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parent.parent
REPORT, ZIP, DEMO = "/api/admin/report", "/api/admin/report/data.zip", "/api/admin/report/demo"


@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path, emulate=True)
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        r = c.post(DEMO)
        assert r.status_code == 200, r.text
        c.show = r.json()["show_id"]
        yield c


def test_needs_admin_login(client):
    anon = TestClient(client.app)
    for url in (REPORT + "?show_id=1", ZIP + "?show_id=1", "/api/admin/report/shows", "/api/admin/report/markers?show_id=1"):
        assert anon.get(url).status_code == 401
    assert anon.post(DEMO).status_code == 401


def test_cross_origin_refused(client):
    r = client.get(REPORT + f"?show_id={client.show}", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_demo_only_in_emulate(tmp_path):
    with TestClient(create_app(Hub(tmp_path))) as c:
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        assert c.post(DEMO).status_code == 409


def test_demo_twice_gives_the_same_day(client):
    assert client.post(DEMO).json()["show_id"] == client.show


def test_html_headers_and_content(client):
    r = client.get(REPORT, params={"show_id": client.show})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "script-src" not in r.headers["content-security-policy"]
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert re.fullmatch(r'inline; filename="stagewatch-report-demo-festival-\d{4}-\d{2}-\d{2}\.html"', r.headers["content-disposition"])
    assert "<script" not in r.text.lower()
    assert "Line check" in r.text and "Headliner on" in r.text
    # the 21:40 unclean stop is reported
    assert "21:40" in r.text and "21:47" in r.text
    d = client.get(REPORT, params={"show_id": client.show, "download": "true"})
    assert d.headers["content-disposition"].startswith("attachment;")


def test_zip_download(client):
    r = client.get(ZIP, params={"show_id": client.show, "preset": "custom", "from": "19:30", "to": "23:00"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "no-store"
    assert re.fullmatch(r'attachment; filename="stagewatch-report-demo-festival-\d{4}-\d{2}-\d{2}-custom\.zip"', r.headers["content-disposition"])
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "report.html" in z.namelist() and any(n.endswith(".csv") for n in z.namelist())


def test_sharing_excerpt_and_sections(client):
    r = client.get(REPORT, params={"show_id": client.show, "share": "true", "sections": "summary,alarms"})
    assert r.status_code == 200
    assert "dashboard:foh" not in r.text      # a sharing excerpt does not say who acknowledged
    assert "Headliner on" not in r.text      # markers section not ticked


def test_between_markers_and_lists(client):
    m = client.get("/api/admin/report/markers", params={"show_id": client.show}).json()["markers"]
    ids = {x["label"]: x["id"] for x in m}
    r = client.get(REPORT, params={"show_id": client.show, "preset": "markers",
                                   "from_marker": ids["Doors"], "to_marker": ids["Curfew"]})
    assert r.status_code == 200 and "Line check" not in r.text
    shows = client.get("/api/admin/report/shows").json()["shows"]
    assert any(s["id"] == client.show and s["event"] == "Demo Festival" for s in shows)
    assert any(s["current"] for s in shows)


@pytest.mark.parametrize("params,status,text", [
    ({"show_id": 99999}, 404, "No such show."),
    ({"show_id": 0}, 422, None),
    ({"show_id": 1, "preset": "weird<b>"}, 422, "Unknown range."),
    ({"show_id": 1, "sections": "summary,evil<script>"}, 422, "Unknown section."),
    ({"show_id": 1, "preset": "custom", "from": "19:3x", "to": "20:00"}, 422, None),
    ({"show_id": "SHOW<script>"}, 422, None),
])
def test_errors_are_fixed_wording_and_never_echo(client, params, status, text):
    r = client.get(REPORT, params=params)
    assert r.status_code == status
    assert "<script" not in r.text and "weird" not in r.text and "evil" not in r.text and "19:3x" not in r.text
    if text:
        assert r.json()["detail"] == text


def test_range_errors(client):
    base = {"show_id": client.show, "preset": "custom"}
    r = client.get(REPORT, params={**base, "from": "23:00", "to": "19:00"})
    assert r.status_code == 200 or r.status_code == 422     # 23:00 -> 19:00 is a legal overnight range or a refusal
    r = client.get(REPORT, params={**base, "from": "10:00", "to": "10:00"})
    assert r.status_code == 422
    r = client.get(REPORT, params={"show_id": client.show, "preset": "markers", "from_marker": 999999, "to_marker": 999998})
    assert r.status_code == 422 and r.json()["detail"] == "Pick two markers from this show."
    r = client.get(REPORT, params={"show_id": client.show, "preset": "show_time"})
    assert r.status_code == 200      # the demo has doors and curfew


def test_busy_gives_429(client):
    started, release = threading.Event(), threading.Event()
    real = client.hub.recorder.flush

    def slow_flush():
        started.set()
        release.wait(5)
        real()
    client.hub.recorder.flush = slow_flush
    out = {}

    def first():
        out["r"] = client.get(REPORT, params={"show_id": client.show})
    t = threading.Thread(target=first)
    t.start()
    assert started.wait(5)
    # the first request is inside the lock (flush runs under it) -> a second is refused at once
    second = TestClient(client.app)
    second.cookies.update(client.cookies)
    r = second.get(REPORT, params={"show_id": client.show})
    release.set()
    t.join(10)
    assert r.status_code == 429 and r.json()["detail"] == "A report is already being made. Try again in a moment."
    assert out["r"].status_code == 200


def test_deadline_gives_503_with_fixed_text(client):
    client.app.state.report_deadline_s = 0.000001
    r = client.get(REPORT, params={"show_id": client.show})
    assert r.status_code == 503
    assert "took too long" in r.json()["detail"] and "stagewatch report" in r.json()["detail"]


def test_no_addresses_or_secrets(client):
    rec = client.hub.recorder
    rec.add_marker("Rigger on 10.20.30.40 aa:bb:cc:dd:ee:ff", "admin")
    rec.flush()
    sid = rec.show_id
    for url in (REPORT, ZIP):
        r = client.get(url, params={"show_id": sid})
        body = r.text if url == REPORT else b"".join(zipfile.ZipFile(io.BytesIO(r.content)).read(n) for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist()).decode("utf-8", "replace")
        assert "10.20.30.40" not in body and "aa:bb:cc:dd:ee:ff" not in body.lower()
        assert "hw_key" not in body and "noise_psk" not in body and "pin_hash" not in body
    assert "[address]" in client.get(REPORT, params={"show_id": sid}).text


def test_admin_card_uses_no_innerhtml():
    js = (ROOT / "src/stagewatch/web/static/report-card.js").read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", js)
    assert "localStorage" in js and "try" in js
    html = (ROOT / "src/stagewatch/web/static/admin.html").read_text(encoding="utf-8")
    assert "report-card.js" in html


def test_sharing_with_system_or_schedule_sections_is_refused(client):
    for sec in ("system", "summary,schedule"):
        r = client.get(REPORT, params={"show_id": client.show, "share": "true", "sections": sec})
        assert r.status_code == 422
        assert r.json()["detail"] == "A Sharing excerpt never includes system events or the running order."
    r = client.get(REPORT, params={"show_id": client.show, "share": "true", "sections": "summary"})
    assert r.status_code == 200
    r = client.get(REPORT, params={"show_id": client.show, "sections": "system"})     # a full report may have them
    assert r.status_code == 200


def test_csp_in_the_page_and_in_the_header(client):
    r = client.get(REPORT, params={"show_id": client.show})
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', r.text)
    assert meta, "the page carries its own policy"
    for part in ("default-src 'none'", "style-src 'unsafe-inline'", "img-src data:", "base-uri 'none'", "form-action 'none'"):
        assert part in meta.group(1)
    z = zipfile.ZipFile(io.BytesIO(client.get(ZIP, params={"show_id": client.show}).content))
    assert "Content-Security-Policy" in z.read("report.html").decode("utf-8")


def test_demo_day_waits_for_the_report_lock(client):
    started, release = threading.Event(), threading.Event()
    real = client.hub.recorder.flush

    def slow_flush():
        started.set()
        release.wait(5)
        real()
    client.hub.recorder.flush = slow_flush
    t = threading.Thread(target=lambda: client.get(REPORT, params={"show_id": client.show}))
    t.start()
    assert started.wait(5)
    second = TestClient(client.app)
    second.cookies.update(client.cookies)
    r = second.post(DEMO)
    release.set()
    t.join(10)
    assert r.status_code == 429 and r.json()["detail"].startswith("A report is already being made")
