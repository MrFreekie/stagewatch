"""Events & shows API and UI (WP6): next day vs new event, renaming, the show-day date, the
0.2.0 request body, auth, reload broadcast, day rollover, double submits and a v1 database."""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch import updater_common as uc
from stagewatch.core.hub import Hub
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
FIX_V1 = ROOT / "tests" / "fixtures" / "v1"
PIN = "4711"
EVIL = {"Origin": "http://evil.example"}


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def _admin(c):
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200


def _state(c):
    r = c.get("/api/admin/state")
    assert r.status_code == 200
    return r.json()


def _show_rows(hub):
    db = sqlite3.connect(str(hub.recorder.path))
    try:
        return db.execute("SELECT id, name, event_id, ended FROM shows ORDER BY id").fetchall()
    finally:
        db.close()


# ------------------------------------------------------------- next day / new event
def test_next_day_keeps_the_event_and_new_event_ends_it(client):
    _admin(client)
    first = _state(client)
    ev1 = first["event"]
    assert first["show"]["event_id"] == ev1["id"] and first["show"]["event_name"] == ev1["name"]

    r = client.post("/api/admin/shows", json={"name": "Day 2", "event": "current",
                                              "from_show_id": first["show"]["id"]})
    assert r.status_code == 200
    day2 = r.json()
    assert day2["name"] == "Day 2" and day2["event_id"] == ev1["id"] and day2["day_set"] is False

    r = client.post("/api/admin/shows", json={"name": "Day 1", "event": "new", "event_name": " Autumn Festival ",
                                              "day": "2026-11-06"})
    assert r.status_code == 200
    new = r.json()
    assert new["event_name"] == "Autumn Festival" and new["event_id"] != ev1["id"]
    assert new["day"] == "2026-11-06" and new["day_set"] is True

    st = _state(client)
    assert st["event"] == {"id": new["event_id"], "name": "Autumn Festival", "created": st["event"]["created"]}
    assert [e["name"] for e in st["events"]][:2] == ["Autumn Festival", ev1["name"]]
    old = st["events"][1]
    assert old["ended"] is not None and [s["name"] for s in old["shows"]][0] == "Day 2"
    assert all(s["day"] and len(s["day"]) == 10 for e in st["events"] for s in e["shows"])
    assert [s["name"] for s in st["events"][0]["shows"]] == ["Day 1"]
    snap = client.get("/api/snapshot").json()["show"]
    assert snap["event_name"] == "Autumn Festival" and snap["name"] == "Day 1" and snap["day"] == "2026-11-06"


def test_old_post_shows_body_still_works(client):
    _admin(client)
    ev = _state(client)["event"]
    r = client.post("/api/admin/shows", json={"name": "Festival day 2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Festival day 2" and r.json()["event_id"] == ev["id"]
    assert len(_state(client)["events"]) == 1


def test_markers_and_alarm_log_follow_the_new_show(client):
    _admin(client)
    assert client.post("/api/markers", json={"label": "Doors"}).status_code == 200
    client.hub.recorder.log_alarm("test", "raise", 1, "Test alarm")
    assert len(_state(client)["alarm_log"]) == 1
    old_id = client.hub.recorder.show_id
    client.post("/api/admin/shows", json={"name": "Day 2"})
    assert client.get("/api/snapshot").json()["markers"] == []
    assert _state(client)["alarm_log"] == []  # the alarm log is per show
    # (the emulate demo day also put its own schedule markers on the old show)
    assert [m.label for m in client.hub.recorder.markers(old_id) if m.source != "schedule"] == ["Doors"]
    client.post("/api/markers", json={"label": "Line check"})
    assert [m["label"] for m in client.get("/api/snapshot").json()["markers"]] == ["Line check"]
    client.post("/api/admin/shows", json={"name": "Day 1", "event": "new", "event_name": "Tour leg 2"})
    assert client.get("/api/snapshot").json()["markers"] == []


# ------------------------------------------------------------- PATCH show / event
def test_patch_current_show_day_and_name(client):
    _admin(client)
    show = _state(client)["show"]
    r = client.patch("/api/admin/shows/current", json={"day": "2026-10-03", "show_id": show["id"]})
    assert r.status_code == 200 and r.json()["day"] == "2026-10-03" and r.json()["day_set"] is True
    assert client.get("/api/snapshot").json()["show"]["day"] == "2026-10-03"
    r = client.patch("/api/admin/shows/current", json={"name": "Load-in"})
    assert r.status_code == 200 and r.json()["name"] == "Load-in" and r.json()["day"] == "2026-10-03"
    # null goes back to the date the show started on
    r = client.patch("/api/admin/shows/current", json={"day": None})
    assert r.json()["day_set"] is False
    assert r.json()["day"] == client.hub.show_day(show["started"], None)
    assert client.hub.recorder.show_id == show["id"]  # no new show
    for bad in ({}, {"day": "2026-02-30"}, {"day": "03-10-2026"}, {"day": "2026-10-3"}, {"name": None},
                {"name": "x", "extra": 1}, {"show_id": show["id"]}):
        assert client.patch("/api/admin/shows/current", json=bad).status_code == 422, bad


def test_patch_day_calls_the_schedule_rebase_hook(client, monkeypatch):
    _admin(client)
    calls = []
    monkeypatch.setattr(client.hub, "show_day_changed", lambda old, new: calls.append((old, new)))
    old = client.get("/api/snapshot").json()["show"]["day"]
    client.patch("/api/admin/shows/current", json={"day": "2030-01-01"})
    client.patch("/api/admin/shows/current", json={"name": "Same day, new name"})
    assert calls == [(old, "2030-01-01")]


def test_patch_current_event_name(client):
    _admin(client)
    ev = _state(client)["event"]
    r = client.patch("/api/admin/events/current", json={"name": "Summer Festival", "event_id": ev["id"]})
    assert r.status_code == 200 and r.json()["name"] == "Summer Festival"
    assert client.get("/api/snapshot").json()["show"]["event_name"] == "Summer Festival"
    assert client.patch("/api/admin/events/current", json={"name": "Old page", "event_id": ev["id"] + 99}).status_code == 409
    assert client.hub.recorder.event_name == "Summer Festival"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 81, "Day\n2", "Day‮2", "Day​2", "Tab\there", "\x00"])
def test_names_are_checked_and_never_echoed(client, bad):
    _admin(client)
    before = _show_rows(client.hub)
    for method, url, body in (
        ("post", "/api/admin/shows", {"name": bad}),
        ("post", "/api/admin/shows", {"name": "Day 1", "event": "new", "event_name": bad}),
        ("patch", "/api/admin/shows/current", {"name": bad}),
        ("patch", "/api/admin/events/current", {"name": bad}),
    ):
        r = getattr(client, method)(url, json=body)
        assert r.status_code == 422, (url, body)
        if bad.strip():
            assert bad not in r.text
        assert "input" not in r.json()["detail"][0]
    assert _show_rows(client.hub) == before


def test_new_event_needs_a_name_and_event_name_needs_new(client):
    _admin(client)
    assert client.post("/api/admin/shows", json={"name": "Day 1", "event": "new"}).status_code == 422
    assert client.post("/api/admin/shows", json={"name": "Day 1", "event_name": "X"}).status_code == 422
    assert client.post("/api/admin/shows", json={"name": "Day 1", "event": "next"}).status_code == 422
    assert client.post("/api/admin/shows", json={"name": "Day 1", "day": "2026-13-01"}).status_code == 422
    r = client.post("/api/admin/shows", json={"name": "Day 1", "event": "new"})
    assert "A new event needs a name" in r.text
    assert len(_show_rows(client.hub)) == 1


# ------------------------------------------------------------- auth
ROUTES = [("post", "/api/admin/shows", {"name": "Day 2"}),
          ("patch", "/api/admin/shows/current", {"name": "Day 2"}),
          ("patch", "/api/admin/events/current", {"name": "Fest"})]


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_show_routes_need_admin_and_same_origin(client, method, url, body):
    assert getattr(client, method)(url, json=body).status_code == 401
    _admin(client)
    assert getattr(client, method)(url, json=body, headers=EVIL).status_code == 403
    assert len(_show_rows(client.hub)) == 1 and client.hub.recorder.show_name != "Day 2"
    assert client.hub.recorder.event_name != "Fest"


def test_public_snapshot_has_no_admin_only_show_data(client):
    snap = client.get("/api/snapshot").json()
    assert set(snap["show"]) == {"id", "name", "started", "event_id", "event_name", "day", "day_set"}
    assert "events" not in snap and "show_days" not in snap


# ------------------------------------------------------------- reload broadcast
@pytest.mark.parametrize("method,url,body", ROUTES)
def test_every_show_change_reloads_dashboards(client, method, url, body):
    _admin(client)
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert getattr(client, method)(url, json=body).status_code == 200
        for _ in range(50):
            if ws.receive_json()["type"] == "reload":
                break
        else:
            pytest.fail("no reload broadcast")


# ------------------------------------------------------------- double submit
def test_double_submit_starts_one_show(client):
    _admin(client)
    sid = client.hub.recorder.show_id
    body = {"name": "Day 2", "from_show_id": sid}
    assert client.post("/api/admin/shows", json=body).status_code == 200
    r = client.post("/api/admin/shows", json=body)
    assert r.status_code == 409 and "Nothing more has been changed" in r.json()["detail"]
    r = client.post("/api/admin/shows", json={**body, "name": "Day 1", "event": "new", "event_name": "B"})
    assert r.status_code == 409
    assert [row[1] for row in _show_rows(client.hub)].count("Day 2") == 1
    assert len(_show_rows(client.hub)) == 2
    # a stale page can't rename the new day either
    assert client.patch("/api/admin/shows/current", json={"name": "X", "show_id": sid}).status_code == 409
    assert client.hub.recorder.show_name == "Day 2"


def test_concurrent_clicks_start_one_show(client):
    _admin(client)
    sid = client.hub.recorder.show_id
    gate = threading.Barrier(4)
    codes: list[int] = []

    def click():
        gate.wait()
        codes.append(client.post("/api/admin/shows", json={"name": "Day 2", "from_show_id": sid}).status_code)

    threads = [threading.Thread(target=click) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(codes) == [200, 409, 409, 409]
    rows = _show_rows(client.hub)
    assert len(rows) == 2 and sum(1 for r in rows if r[3] is None) == 1


# ------------------------------------------------------------- day rollover
def _hub_at(tmp_path, monkeypatch, now: list[float]) -> Hub:
    monkeypatch.setattr(time, "time", lambda: now[0])
    hub = Hub(tmp_path, emulate=True)
    hub.config.site.timezone = "Europe/London"
    hub.config.site.day_rollover = "06:00"
    hub.start_show("Day 1", day=None)
    return hub


def _bst(day: str, hhmm: str) -> float:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=ZoneInfo("Europe/London")).timestamp()


def test_next_day_before_the_rollover_suggests_tomorrow(tmp_path, monkeypatch):
    now = [_bst("2026-10-02", "13:00")]
    hub = _hub_at(tmp_path, monkeypatch, now)
    assert hub.show_info()["day"] == "2026-10-02"
    now[0] = _bst("2026-10-03", "01:30")  # after the last song, before the 06:00 rollover
    days = hub.show_days()
    assert days == {"today": "2026-10-02", "next": "2026-10-03"}
    # Without a date the new day would still count as the night before ...
    hub.start_show("Day 2 (no date)")
    assert hub.show_info()["day"] == "2026-10-02" and hub.show_info()["day_set"] is False
    # ... so the admin page sends the suggested date, and the new day gets it.
    hub.start_show("Day 2", day=days["next"])
    assert hub.show_info()["day"] == "2026-10-03" and hub.show_info()["day_set"] is True
    hub.recorder.close()


def test_next_day_after_the_rollover_follows_the_clock(tmp_path, monkeypatch):
    now = [_bst("2026-10-02", "13:00")]
    hub = _hub_at(tmp_path, monkeypatch, now)
    now[0] = _bst("2026-10-03", "09:00")
    assert hub.show_days() == {"today": "2026-10-03", "next": "2026-10-03"}
    hub.start_show("Day 2")  # the admin page sends no date when it equals today
    assert hub.show_info()["day"] == "2026-10-03" and hub.show_info()["day_set"] is False
    # A rest day in between: the suggestion is the real date, not current + 1.
    now[0] = _bst("2026-10-05", "10:00")
    assert hub.show_days() == {"today": "2026-10-05", "next": "2026-10-05"}
    # A day started early (a day ahead) is not suggested again.
    hub.start_show("Day 3", day="2026-10-06")
    assert hub.show_days()["next"] == "2026-10-07"
    hub.recorder.close()


def test_rollover_change_moves_derived_days_but_not_picked_ones(tmp_path, monkeypatch):
    now = [_bst("2026-10-03", "05:00")]
    hub = _hub_at(tmp_path, monkeypatch, now)
    assert hub.show_info()["day"] == "2026-10-02"  # before 06:00: the night before
    hub.config.site.day_rollover = "04:00"
    assert hub.show_info()["day"] == "2026-10-03"
    hub.update_show(day="2026-10-02", set_day=True)
    hub.config.site.day_rollover = "06:00"
    assert hub.show_info()["day"] == "2026-10-02"
    hub.recorder.close()


# ------------------------------------------------------------- v1 database
@pytest.fixture
def v1_client(tmp_path, monkeypatch):
    for name in ("STAGEWATCH_UPDATE_TRIAL", uc.ENV_SUPERVISED, uc.ENV_STATE_DIR):
        monkeypatch.delenv(name, raising=False)
    for name in ("stagewatch.sqlite3", "config.yaml"):
        shutil.copyfile(FIX_V1 / name, tmp_path / name)
    hub = Hub(tmp_path)
    with TestClient(create_app(hub, manage_hub=False)) as c:
        assert c.post("/api/admin/login", json={"pin": "1234"}).status_code == 200
        c.hub = hub
        yield c
    hub.recorder.close()


def test_v1_database_works_with_events_and_days(v1_client):
    c = v1_client
    st = c.get("/api/admin/state").json()
    assert [e["name"] for e in st["events"]] == ["Event 1"]
    v1_shows = st["events"][0]["shows"]
    assert len(v1_shows) == 2 and all(s["day_set"] is False and len(s["day"]) == 10 for s in v1_shows)
    assert st["show"]["event_name"] == "Event 1" and st["show_days"]["next"] >= st["show_days"]["today"]
    old_show = st["show"]["id"]
    old_markers = [m.label for m in c.hub.recorder.markers(old_show)]
    snap = c.get("/api/snapshot").json()["show"]
    assert snap["event_name"] == "Event 1" and snap["name"] == st["show"]["name"]

    assert c.patch("/api/admin/events/current", json={"name": "Spring Tour"}).status_code == 200
    r = c.post("/api/admin/shows", json={"name": "Day 3", "from_show_id": old_show})
    assert r.status_code == 200 and r.json()["event_name"] == "Spring Tour"
    r = c.post("/api/admin/shows", json={"name": "Day 1", "event": "new", "event_name": "Summer Festival"})
    assert r.status_code == 200
    st = c.get("/api/admin/state").json()
    assert [e["name"] for e in st["events"]] == ["Summer Festival", "Spring Tour"]
    assert [len(e["shows"]) for e in st["events"]] == [1, 3]
    assert [m.label for m in c.hub.recorder.markers(old_show)] == old_markers  # history kept


# ------------------------------------------------------------- pages
def test_dashboard_header_shows_dashboard_event_and_day():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "msg.show.event_name" in js and 'sub.join(" · ")' in js


def test_admin_card_has_next_day_and_new_event_with_confirms():
    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    start = js.index("function eventShowCard()")
    body = js[start:js.index("function securityCard()")]
    for text in ('"Event & show"', '"Next day (same event)"', '"New event…"', "dateInput(",
                 "from_show_id", "/api/admin/events/current", "/api/admin/shows/current", "Previous shows"):
        assert text in body, text
    assert 'h("input", { type: "date"' in js and "SW.parseDay(input.value)" in js
    assert body.count("confirm(") == 2
    assert "toLocale" not in body
