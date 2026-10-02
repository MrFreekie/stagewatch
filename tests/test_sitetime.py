"""F4 site time: core/sitetime.py, the `time` block, and the frontend's single time formatter.

DST vectors are for the 2026 change dates (checked against the rules, not taken from zoneinfo):
- Europe/London: clocks go forward Sun 29 Mar 2026 at 01:00 UTC (01:00 GMT -> 02:00 BST) and
  back Sun 25 Oct 2026 at 01:00 UTC (02:00 BST -> 01:00 GMT).
- America/New_York: forward Sun 8 Mar 2026 at 07:00 UTC (02:00 EST -> 03:00 EDT) and back
  Sun 1 Nov 2026 at 06:00 UTC (02:00 EDT -> 01:00 EST).
Expected instants are written as plain UTC so they don't depend on the code under test.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import sitetime
from stagewatch.core.config import SiteConfig
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "stagewatch" / "web" / "static"


def utc(*args) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def site(tz: str = "Europe/London", rollover: str = "06:00") -> SiteConfig:
    return SiteConfig(timezone=tz, day_rollover=rollover)


LON = site("Europe/London")
NYC = site("America/New_York")


# ------------------------------------------------------------ zone / offsets
def test_zone_none_when_unset():
    assert sitetime.zone(site("")) is None
    assert sitetime.zone(LON).key == "Europe/London"


@pytest.mark.parametrize("s, ts, offset", [
    # London spring forward 2026-03-29 01:00 UTC
    (LON, utc(2026, 3, 29, 0, 59, 59), 0),
    (LON, utc(2026, 3, 29, 1, 0, 0), 3600),
    # London fall back 2026-10-25 01:00 UTC
    (LON, utc(2026, 10, 25, 0, 59, 59), 3600),
    (LON, utc(2026, 10, 25, 1, 0, 0), 0),
    # New York spring forward 2026-03-08 07:00 UTC
    (NYC, utc(2026, 3, 8, 6, 59, 59), -5 * 3600),
    (NYC, utc(2026, 3, 8, 7, 0, 0), -4 * 3600),
    # New York fall back 2026-11-01 06:00 UTC
    (NYC, utc(2026, 11, 1, 5, 59, 59), -4 * 3600),
    (NYC, utc(2026, 11, 1, 6, 0, 0), -5 * 3600),
    # half-hour zone, no DST
    (site("Asia/Kolkata"), utc(2026, 7, 1, 12, 0), 19800),
])
def test_utc_offset_around_2026_transitions(s, ts, offset):
    assert sitetime.utc_offset_s(ts, s) == offset


# ------------------------------------------------------------ show_day
@pytest.mark.parametrize("started, expected", [
    (utc(2026, 7, 11, 0, 30), date(2026, 7, 10)),    # 01:30 BST: still the night before
    (utc(2026, 7, 11, 4, 59, 59), date(2026, 7, 10)),  # 05:59:59 BST
    (utc(2026, 7, 11, 5, 0, 0), date(2026, 7, 11)),   # 06:00 BST: new day
    (utc(2026, 7, 10, 22, 0), date(2026, 7, 10)),     # 23:00 BST
    # spring-forward morning: 05:59 BST is before rollover, 06:00 BST is the new day
    (utc(2026, 3, 29, 4, 59), date(2026, 3, 28)),
    (utc(2026, 3, 29, 5, 0), date(2026, 3, 29)),
    # winter: 06:00 GMT = 06:00 UTC
    (utc(2026, 1, 15, 5, 59), date(2026, 1, 14)),
    (utc(2026, 1, 15, 6, 0), date(2026, 1, 15)),
])
def test_show_day_across_rollover(started, expected):
    assert sitetime.show_day(started, LON) == expected


def test_show_day_override_and_midnight_rollover():
    assert sitetime.show_day(utc(2026, 7, 11, 0, 30), LON, "2026-07-12") == date(2026, 7, 12)
    assert sitetime.show_day(utc(2026, 7, 11, 0, 30), LON, None) == date(2026, 7, 10)
    # rollover 00:00: the calendar date
    assert sitetime.show_day(utc(2026, 7, 10, 23, 30), site(rollover="00:00")) == date(2026, 7, 11)


# ------------------------------------------------------------ resolve
@pytest.mark.parametrize("day, hhmm, expected", [
    ("2026-07-10", "23:00", utc(2026, 7, 10, 22, 0)),
    ("2026-07-10", "00:30", utc(2026, 7, 10, 23, 30)),  # before rollover -> 11 Jul 00:30 BST
    ("2026-07-10", "05:59", utc(2026, 7, 11, 4, 59)),
    ("2026-07-10", "06:00", utc(2026, 7, 10, 5, 0)),    # at rollover -> same day
    (date(2026, 7, 10), "19:30", utc(2026, 7, 10, 18, 30)),
])
def test_resolve_across_rollover(day, hhmm, expected):
    assert sitetime.resolve(day, hhmm, LON) == expected


def test_resolve_round_trips_through_show_day_and_local_hhmm():
    for hhmm in ("06:00", "12:00", "23:59", "00:00", "05:59"):
        ts = sitetime.resolve("2026-08-01", hhmm, LON)
        assert sitetime.local_hhmm(ts, LON) == hhmm
        assert sitetime.show_day(ts, LON) == date(2026, 8, 1)


@pytest.mark.parametrize("s, day, hhmm, expected, shows_as", [
    # London gap 01:00-02:00 on 29 Mar: 01:30 (next day via rollover) uses GMT -> 01:30 UTC = 02:30 BST
    (LON, "2026-03-28", "01:30", utc(2026, 3, 29, 1, 30), "02:30"),
    # New York gap 02:00-03:00 on 8 Mar: 02:30 uses EST -> 07:30 UTC = 03:30 EDT
    (NYC, "2026-03-07", "02:30", utc(2026, 3, 8, 7, 30), "03:30"),
])
def test_resolve_spring_forward_gap_fold0(s, day, hhmm, expected, shows_as):
    ts = sitetime.resolve(day, hhmm, s)
    assert ts == expected
    assert sitetime.local_hhmm(ts, s) == shows_as


@pytest.mark.parametrize("s, day, first, second", [
    # London 01:00-02:00 happens twice on 25 Oct: first in BST (00:30 UTC), then GMT (01:30 UTC)
    (LON, "2026-10-24", utc(2026, 10, 25, 0, 30), utc(2026, 10, 25, 1, 30)),
    # New York 01:00-02:00 twice on 1 Nov: first EDT (05:30 UTC), then EST (06:30 UTC)
    (NYC, "2026-10-31", utc(2026, 11, 1, 5, 30), utc(2026, 11, 1, 6, 30)),
])
def test_resolve_fall_back_overlap_first_occurrence(s, day, first, second):
    ts = sitetime.resolve(day, "01:30", s)
    assert ts == first
    assert sitetime.local_hhmm(first, s) == sitetime.local_hhmm(second, s) == "01:30"


def test_resolve_with_midnight_rollover_uses_calendar_day():
    s = site("America/New_York", rollover="00:00")
    assert sitetime.resolve("2026-03-08", "02:30", s) == utc(2026, 3, 8, 7, 30)


@pytest.mark.parametrize("bad", ["24:00", "7:30", "12:60", "", "ab:cd", "１２:００"])
def test_resolve_rejects_bad_times(bad):
    with pytest.raises(ValueError):
        sitetime.resolve("2026-07-10", bad, LON)


# ------------------------------------------------------------ ms since midnight
def test_ms_since_local_midnight():
    assert sitetime.ms_since_local_midnight(utc(2026, 7, 10, 23, 30), LON) == 30 * 60 * 1000  # 00:30 BST
    assert sitetime.ms_since_local_midnight(utc(2026, 7, 11, 3, 59, 59) + 0.25, NYC) == 86_399_250
    assert sitetime.ms_since_local_midnight(utc(2026, 1, 1, 0, 0), LON) == 0
    # wall-clock reading: 03:00 EDT straight after the gap reads 3 h, not 2 h elapsed
    assert sitetime.ms_since_local_midnight(utc(2026, 3, 8, 7, 0), NYC) == 3 * 3600 * 1000


# ------------------------------------------------------------ unset zone (this computer's zone)
def test_unset_zone_uses_os_local_time():
    s = site("")
    ts = utc(2026, 7, 10, 18, 0)
    local = datetime.fromtimestamp(ts).astimezone()
    assert sitetime.utc_offset_s(ts, s) == int(local.utcoffset().total_seconds())
    assert sitetime.local_hhmm(ts, s) == local.strftime("%H:%M")
    assert sitetime.resolve("2026-07-10", "20:00", s) == datetime(2026, 7, 10, 20, 0).timestamp()
    assert sitetime.resolve("2026-07-10", "00:30", s) == datetime(2026, 7, 11, 0, 30).timestamp()
    noon = datetime(2026, 7, 10, 12, 0).timestamp()
    assert sitetime.show_day(noon, s) == date(2026, 7, 10)
    assert sitetime.show_day(datetime(2026, 7, 11, 5, 59).timestamp(), s) == date(2026, 7, 10)
    block = sitetime.time_block(s, ts)
    assert block == {"timezone": "", "utc_offset_s": sitetime.utc_offset_s(ts, s), "day_rollover": "06:00"}


def test_time_block_with_zone():
    assert sitetime.time_block(LON, utc(2026, 7, 1, 12)) == {
        "timezone": "Europe/London", "utc_offset_s": 3600, "day_rollover": "06:00"}
    assert sitetime.time_block(LON, utc(2026, 12, 1, 12))["utc_offset_s"] == 0


# ------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def _admin(client):
    assert client.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def _site_body(client, **changes):
    body = client.hub.config.site.model_dump()
    body.update(changes)
    return body


def test_time_block_in_info_and_snapshot(client):
    for t in (client.get("/api/info").json()["time"], client.get("/api/snapshot").json()["site"]["time"]):
        assert set(t) == {"timezone", "utc_offset_s", "day_rollover"}
        assert t["timezone"] == "" and t["day_rollover"] == "06:00"
        assert isinstance(t["utc_offset_s"], int)


@pytest.mark.parametrize("bad", ["../etc/passwd", "..", "Europe/../../etc", "/etc/localtime",
                                 "C:\\Windows", "Not/AZone", "x" * 65])
def test_put_site_rejects_bad_zones(client, bad):
    _admin(client)
    r = client.put("/api/admin/site", json=_site_body(client, timezone=bad))
    assert r.status_code == 422
    # the message the admin page shows is fixed text (FastAPI's raw "input" echo is a separate,
    # app-wide matter for the 422 handler)
    msgs = [d["msg"] for d in r.json()["detail"]]
    assert msgs and all(bad not in m for m in msgs)
    assert client.hub.config.site.timezone == ""


def test_put_site_rejects_bad_rollover(client):
    _admin(client)
    for bad in ("12:00", "6:00", "06:60", "x"):
        assert client.put("/api/admin/site", json=_site_body(client, day_rollover=bad)).status_code == 422


def test_put_site_requires_admin_and_same_origin(client):
    assert client.put("/api/admin/site", json=_site_body(client, timezone="Europe/London")).status_code == 401
    _admin(client)
    r = client.put("/api/admin/site", json=_site_body(client, timezone="Europe/London"),
                   headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_put_site_zone_change_calls_rebase_hook_and_updates_time_block(client):
    _admin(client)
    calls = []
    client.hub.site_time_changed = lambda old, new: calls.append((old, new))
    r = client.put("/api/admin/site", json=_site_body(client, timezone="America/New_York", day_rollover="05:30"))
    assert r.status_code == 200
    assert calls == [("", "America/New_York")]
    t = client.get("/api/info").json()["time"]
    assert t["timezone"] == "America/New_York" and t["day_rollover"] == "05:30"
    assert t["utc_offset_s"] in (-4 * 3600, -5 * 3600)
    # same zone again (other fields changed): no rebase
    client.put("/api/admin/site", json=_site_body(client, name="Main stage"))
    assert calls == [("", "America/New_York")]


def test_rebase_hook_is_a_noop_for_now(client):
    assert client.hub.site_time_changed("", "Europe/London") is None


# ------------------------------------------------------------ frontend
def _static_sources():
    for p in sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.html")):
        yield p.name, p.read_text(encoding="utf-8")


def test_no_tolocale_outside_fmttime():
    """Every time on screen goes through SW.fmtTime (site zone, 24-hour)."""
    hits = [f"{name}:{i}" for name, src in _static_sources()
            for i, line in enumerate(src.splitlines(), 1) if re.search(r"\btoLocale\w*\(", line)]
    assert not hits, f"use SW.fmtTime instead of toLocale*: {hits}"


def test_old_time_helpers_are_gone():
    for name, src in _static_sources():
        assert not re.search(r"\bSW\.(time|timeSec)\b", src), name
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert "SW.fmtTime = function" in common and "SW.setSiteTime" in common


def test_server_clock_offset_correction_kept():
    dash = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "SW.fmtTime(serverNow()" in dash and "syncClock(msg.now)" in dash
