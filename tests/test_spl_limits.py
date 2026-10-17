"""Sound level limits (0.4.0): set rules, show-day windows, live markers and silent alarms, API.

Times are hand-picked on Saturday 17/10/2026 in Europe/London (BST, UTC+1), day rollover 06:00.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import spl_limits as L
from stagewatch.core.config import Config, SplConfig, SplSlot
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.web.server import create_app

EID = "spl.laeq_15m"   # slot_ids([("", "LAeq 15")]): the old default keeps its old id


def ts(hh, mm, day=17):
    """UTC epoch for HH:MM BST on day/10/2026 (BST = UTC+1, so subtract an hour)."""
    return datetime(2026, 10, day, hh, mm, tzinfo=timezone.utc).timestamp() - 3600


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.timezone = "Europe/London"
    h.config.site.day_rollover = "06:00"
    h.config.spl = SplConfig(enabled=True, meters=[SplSlot(metric="LAeq 15")])
    h.register_device(Device("spl", "Sound level", "smaart", status=Status.OK))
    h.register_entity(Entity(EID, "spl", "LAeq 15", Kind.SOUND_LEVEL, "dB", 1))
    yield h
    h.recorder.close()


def lim(i, v, start="", end="", kind="limit", metric="LAeq 15"):
    return L.SplLimit(id=i, metric=metric, value_db=v, start=start, end=end, kind=kind)


def save(hub, *limits, show=False, note=""):
    s = L.SplLimitSet(limits=list(limits))
    assert L.set_errors(s, hub.config.site) is None
    rec = hub.recorder
    rec.spl_limits_save(rec.event_id, rec.show_id if show else None, L.dump_set(s), "", note)
    hub.spl_limits.reload()


def markers(hub):
    return [m.label for m in hub.recorder.markers() if m.source == "spl_limits"]


def alarms(hub):
    return {a["id"]: a for a in hub.alarms.to_list()}


# ------------------------------------------------------------------ rules
def test_overlap_and_order_are_refused_naming_rows():
    site = Config().site
    site.day_rollover = "06:00"
    bad = L.SplLimitSet(limits=[lim("a", 100, end="22:00"), lim("b", 98, start="21:00")])
    assert L.set_errors(bad, site) == "Rows 1 and 2 overlap for the same value and kind"
    # guidance and limit on the same value may overlap
    ok = L.SplLimitSet(limits=[lim("a", 100), lim("b", 98, kind="guidance")])
    assert L.set_errors(ok, site) is None
    # 23:00 to 01:00 runs over midnight inside the show day (01:00 is before the 06:00 rollover)
    assert L.set_errors(L.SplLimitSet(limits=[lim("a", 100, "23:00", "01:00")]), site) is None
    assert "Row 1" in L.set_errors(L.SplLimitSet(limits=[lim("a", 100, "22:00", "21:00")]), site)
    assert L.set_errors(L.SplLimitSet(limits=[lim("a", 100), lim("a", 90, kind="guidance")]), site)


@pytest.mark.parametrize("bad", [{"value_db": True}, {"value_db": "100"}, {"value_db": 39.9},
                                 {"value_db": 150.1}, {"start": "24:00"}, {"start": "7:00"},
                                 {"kind": "other"}, {"extra": 1}, {"metric": ""}])
def test_limit_model_is_strict(bad):
    with pytest.raises(Exception):
        L.SplLimit.model_validate({"id": "a", "metric": "LAeq 15", "value_db": 100, **bad})


def test_effective_set_override_then_back_to_event(hub):
    rec = hub.recorder
    save(hub, lim("a", 100))
    save(hub, lim("b", 95), show=True)
    assert rec.spl_limits_effective(rec.event_id, rec.show_id)["scope"] == "show"
    rec.spl_limits_save(rec.event_id, rec.show_id, None, "", "clear")
    eff = rec.spl_limits_effective(rec.event_id, rec.show_id)
    assert eff["scope"] == "event" and eff["set"]["limits"][0]["value_db"] == 100
    assert [r["rev"] for r in rec.spl_limits_revisions(rec.event_id)] == [3, 2, 1]


def test_evaluate_window_across_midnight_and_next_change(hub):
    site = hub.config.site
    s = L.SplLimitSet(limits=[lim("a", 102, end="22:00"), lim("b", 98, "22:00", "00:30")])
    ev = L.evaluate(s, ts(21, 0), site)
    assert ev["active"][("", "LAeq 15", "limit")][0].id == "a"
    first = ev["changes"][0]
    assert first["ts"] == ts(22, 0) and first["after"].value_db == 98
    # 00:10 on the 18th still belongs to show day 17/10
    ev = L.evaluate(s, ts(0, 10, day=18), site)
    assert ev["day"] == "2026-10-17" and ev["active"][("", "LAeq 15", "limit")][0].id == "b"
    assert ev["changes"][0]["ts"] == ts(0, 30, day=18) and ev["changes"][0]["after"] is None


# ------------------------------------------------------------------ live behaviour
def test_strictly_above_and_no_reading_is_never_ok(hub):
    save(hub, lim("a", 100))
    svc = hub.spl_limits
    now = ts(20, 0)
    a = svc.set.limits[0]
    assert svc.reading(a, now)["state"] == "no_reading"       # nothing received yet: not "OK", not zero
    hub.update_state(EID, 100.0, now)
    assert svc.reading(a, now)["state"] == "under"            # equal is not over
    hub.update_state(EID, 100.04, now)
    r = svc.reading(a, now)
    assert r["state"] == "over" and r["value"] == 100.04      # passed through exactly as received
    assert svc.reading(a, now + 3600)["state"] == "no_reading"  # stale


def test_not_recorded_when_no_slot_matches(hub):
    save(hub, lim("a", 98, metric="LAeq 10"))
    assert hub.spl_limits.reading(hub.spl_limits.set.limits[0], ts(20, 0))["state"] == "not_recorded"


def test_exceed_marker_once_per_excursion_and_rearm(hub):
    save(hub, lim("a", 100))
    t = ts(20, 0)
    hub.spl_limits.tick(t)
    for i, v in enumerate([101.0, 102.5, 99.0, 101.2]):     # dips under for under 2 minutes
        hub.update_state(EID, v, t + i)
        hub.spl_limits.tick(t + i)
    assert markers(hub) == ["Over limit: LAeq 15 101.0 vs 100 (limit)"]
    assert alarms(hub)["spl:over:a"]["silent"] is True and alarms(hub)["spl:over:a"]["level"] == 1
    # 2 minutes continuously at or under re-arms (and clears the notice)
    hub.update_state(EID, 99.0, t + 10)
    hub.spl_limits.tick(t + 10)
    hub.update_state(EID, 99.5, t + 131)
    hub.spl_limits.tick(t + 131)
    assert "spl:over:a" not in alarms(hub)
    hub.update_state(EID, 100.1, t + 140)
    hub.spl_limits.tick(t + 140)
    assert len(markers(hub)) == 2


def test_stale_value_makes_no_marker_and_does_not_clear(hub):
    save(hub, lim("a", 100))
    t = ts(20, 0)
    hub.spl_limits.tick(t)
    hub.update_state(EID, 105.0, t - 3600)    # an old value: not live
    hub.spl_limits.tick(t)
    assert markers(hub) == [] and "spl:over:a" not in alarms(hub)


def test_guidance_has_no_marker_or_alarm(hub):
    save(hub, lim("g", 90, kind="guidance"))
    t = ts(20, 0)
    hub.spl_limits.tick(t)
    hub.update_state(EID, 95.0, t)
    hub.spl_limits.tick(t)
    assert markers(hub) == [] and alarms(hub) == {}
    assert hub.spl_limits.public(t)["current"][0]["reading"]["state"] == "over"


def test_boundary_marker_live_only_and_warning_before_tightening(hub):
    save(hub, lim("a", 102, end="22:00"), lim("b", 98, "22:00"))
    hub.spl_limits.tick(ts(21, 49))
    assert "spl:change:b" not in alarms(hub)
    hub.spl_limits.tick(ts(21, 50))                      # T - 10 min
    assert alarms(hub)["spl:change:b"]["message"] == "Sound level limit drops to 98 at 22:00: LAeq 15"
    hub.spl_limits.tick(ts(22, 0))
    assert markers(hub) == ["Sound level limit now 98 (was 102): LAeq 15"]
    hub.spl_limits.tick(ts(22, 1))
    assert "spl:change:b" in alarms(hub)
    hub.spl_limits.tick(ts(22, 2))                       # cleared 2 minutes after
    assert "spl:change:b" not in alarms(hub)


def test_no_back_filled_boundary_marker_after_downtime(hub):
    save(hub, lim("a", 102, end="22:00"), lim("b", 98, "22:00"))
    hub.spl_limits.tick(ts(21, 0))
    hub.spl_limits.tick(ts(22, 6))                       # 6 minutes late: off for the boundary
    assert markers(hub) == []


def test_late_limit_ended_marker_is_skipped(hub):
    save(hub, lim("a", 102, end="22:00"))
    hub.spl_limits.tick(ts(21, 0))
    hub.spl_limits.tick(ts(23, 30))                      # 90 minutes late: no back-filled "ended"
    assert markers(hub) == []


def test_rearm_needs_two_observed_minutes(hub):
    save(hub, lim("a", 100))
    t = ts(20, 0)
    hub.spl_limits.tick(t)
    hub.update_state(EID, 101.0, t)
    hub.spl_limits.tick(t)
    hub.update_state(EID, 99.0, t + 1)
    hub.spl_limits.tick(t + 1)
    hub.spl_limits.tick(t + 400)                         # stale in between: not "under"
    hub.update_state(EID, 99.0, t + 401)
    hub.spl_limits.tick(t + 401)
    assert "spl:over:a" in alarms(hub)


def test_restart_makes_no_marker(hub):
    save(hub, lim("a", 102, end="22:00"), lim("b", 98, "22:00"))
    hub.spl_limits.tick(ts(22, 0, ))                    # first tick: just learns what is in force
    assert markers(hub) == []


def test_loosening_gives_no_warning(hub):
    save(hub, lim("a", 98, end="22:00"), lim("b", 102, "22:00"))
    hub.spl_limits.tick(ts(21, 55))
    assert alarms(hub) == {}


def test_marker_text_does_not_hide_the_excess():
    assert L.fmt_level(95.04, 95) == "95.04" and L.fmt_level(101.0, 100) == "101.0"
    assert L.fmt_level(100.0004, 100) == "100.0004"


def test_edit_adds_marker(hub):
    save(hub, lim("a", 100))
    hub.spl_limits.changed("consultant asked for 99")
    assert markers(hub) == ["Sound level limits updated (rev 1): consultant asked for 99"]


def test_config_without_limits_alert_loads_and_stays_out_of_file():
    c = Config.model_validate({"spl": {"enabled": True}})
    assert c.spl.limits_alert.change_warning_min == 10
    assert "limits_alert" not in c.model_dump()["spl"]
    c = Config.model_validate({"spl": {"limits_alert": {"change_warning_min": "lots"}}})
    assert c.spl.limits_alert.change_warning_min == 10   # damaged options reset, section kept


# ------------------------------------------------------------------ API
@pytest.fixture
def client(hub):
    with TestClient(create_app(hub)) as c:
        yield c


def _admin(c):
    assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


BODY = {"scope": "event", "set": {"limits": [{"id": "a", "metric": "LAeq 15", "value_db": 100}],
                                  "set_by": "Consultant", "reference": "Licence 12"},
        "entered_by": "FOH", "change_note": "first"}


def test_admin_routes_need_login_and_same_origin(client):
    assert client.get("/api/admin/spl-limits").status_code == 401
    assert client.put("/api/admin/spl-limits", json=BODY).status_code == 401
    assert client.delete("/api/admin/spl-limits/override").status_code == 401
    assert client.post("/api/admin/spl-limits/copy", json={"from_event_id": 1}).status_code == 401
    assert client.get("/api/admin/spl-limits/revisions").status_code == 401
    settings = {"change_warning_min": 5, "exceed_marker": True, "exceed_alarm": True}
    assert client.put("/api/admin/spl-limits/settings", json=settings).status_code == 401
    _admin(client)
    r = client.put("/api/admin/spl-limits", json=BODY, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    evil = {"Origin": "http://evil.example"}
    assert client.delete("/api/admin/spl-limits/override", headers=evil).status_code == 403
    assert client.post("/api/admin/spl-limits/copy", json={"from_event_id": 1}, headers=evil).status_code == 403
    assert client.put("/api/admin/spl-limits/settings", json=settings, headers=evil).status_code == 403


def test_put_then_public_get(client, hub):
    _admin(client)
    assert client.put("/api/admin/spl-limits", json=BODY).json()["rev"] == 1
    assert client.put("/api/admin/spl-limits", json=BODY).json() == {"ok": True, "rev": 1, "unchanged": True}
    r = client.get("/api/spl-limits")
    assert r.headers["cache-control"] == "no-store"
    j = r.json()
    assert set(j) == {"now", "day", "has_limits", "rev", "updated", "scope", "copied", "set_by", "reference",
                      "public_notes", "limits", "current", "upcoming", "disclaimer", "period_note", "curfew_ts"}
    assert j["rev"] == 1 and j["set_by"] == "Consultant" and j["current"][0]["value_db"] == 100
    assert "entered_by" not in r.text and "FOH" not in r.text   # admin data stays admin
    assert "Sound level limits updated (rev 1): first" in [m.label for m in hub.recorder.markers()]


@pytest.mark.parametrize("change", [
    {"set": {"limits": [{"id": "a", "metric": "LAeq 15", "value_db": 200}]}},
    {"set": {"limits": [{"id": "a", "metric": "x", "value_db": 90}] * 41}},
    {"set": {"public_notes": "x" * 501}},
    {"scope": "venue"},
    {"set": {"limits": [{"id": "a", "metric": "LAeq 15", "value_db": 99, "end": "22:00"},
                        {"id": "b", "metric": "LAeq 15", "value_db": 98, "start": "21:00"}]}},
])
def test_put_caps_give_422(client, change):
    _admin(client)
    r = client.put("/api/admin/spl-limits", json={**BODY, **change})
    assert r.status_code == 422
    assert "x" * 50 not in r.text


def test_override_and_copy(client, hub):
    _admin(client)
    client.put("/api/admin/spl-limits", json=BODY)
    client.put("/api/admin/spl-limits", json={**BODY, "scope": "show",
                                              "set": {"limits": [{"id": "s", "metric": "LAeq 15", "value_db": 96}]}})
    assert client.get("/api/spl-limits").json()["scope"] == "show"
    assert client.delete("/api/admin/spl-limits/override").json()["changed"] is True
    assert client.get("/api/spl-limits").json()["current"][0]["value_db"] == 100
    old_event = hub.recorder.event_id
    hub.start_show("Day 1", new_event_name="Next event")
    assert client.get("/api/spl-limits").json()["has_limits"] is False
    assert client.post("/api/admin/spl-limits/copy", json={"from_event_id": old_event}).status_code == 200
    j = client.get("/api/spl-limits").json()
    assert j["copied"] is True and j["current"][0]["value_db"] == 100
    client.put("/api/admin/spl-limits", json=BODY)
    assert client.get("/api/spl-limits").json()["copied"] is False
    assert client.post("/api/admin/spl-limits/copy", json={"from_event_id": 999}).status_code == 404


def test_settings_saved_and_kept_by_sound_level_save(client, hub):
    _admin(client)
    body = {"change_warning_min": 5, "exceed_marker": False, "exceed_alarm": True}
    assert client.put("/api/admin/spl-limits/settings", json=body).status_code == 200
    assert client.put("/api/admin/spl-limits/settings", json={**body, "exceed_marker": 0}).status_code == 422
    assert hub.config.spl.limits_alert.change_warning_min == 5
    a = client.get("/api/admin/spl-limits").json()
    assert a["settings"]["exceed_marker"] is False and a["slots"] == [{"source": "", "metric": "LAeq 15"}]


def test_live_feed_message_has_no_payload(hub):
    from stagewatch.web.server import LiveFeed
    feed = LiveFeed(hub)
    sent = []
    feed._send_all = sent.append
    feed._on_event("spl_limits", None)
    assert sent == [{"type": "spl_limits_changed"}]


def test_js_helpers():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([node, str(root / "tests" / "js" / "spl_limits_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
