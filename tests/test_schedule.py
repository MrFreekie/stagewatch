"""WP7 schedule backend: the import parser, now/next, rebase (incl. a DST date), the Recorder
CRUD, the hub service (bus, snapshot, WS), the admin API (409 stale show, caps -> 422), the
public GET, the emulate demo day and the per-path body-size overrides.

DST facts used (Europe/London 2026, checked against the rules, not taken from zoneinfo): clocks
go back on Sun 25 Oct 2026 at 01:00 UTC (02:00 BST -> 01:00 GMT), so 01:30 happens twice that
morning and the first occurrence (BST, fold=0) is 00:30 UTC. Expected instants are plain UTC.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import schedule as sched
from stagewatch.core import sitetime
from stagewatch.core.config import SiteConfig
from stagewatch.core.hub import Hub
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import DEFAULT_BODY_OVERRIDES, create_app

PIN = "4711"
EVIL = {"Origin": "http://evil.example"}
LON = SiteConfig(timezone="Europe/London", day_rollover="06:00")
NY = SiteConfig(timezone="America/New_York", day_rollover="06:00")
DAY = "2026-10-02"  # Fri 2 Oct 2026, BST (UTC+1)
PUBLIC_ITEM_KEYS = {"id", "stage", "kind", "title", "date", "start", "end", "planned_start", "planned_end",
                    "setlist", "marker"}


def utc(*args) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


# =================================================================== import parser
def test_lines_ranges_rollover_and_bad_lines():
    text = ("19:00 Doors\n"
            "19:30-20:15 Support: The Harbour Lights\n"
            "\n"
            "# a comment line\n"
            "23:00–00:30 Late set\n"          # en dash; the end is after midnight
            "this is not a time\n"
            "25:00 Impossible hour\n"
            "21:00-20:00 Backwards\n"
            "9.45 Changeover crew call\n"          # H.MM accepted
            "23:30 Curfew\n")
    res = sched.parse_import(text, "lines", DAY, LON)
    assert res.format == "lines"
    rows = {r["title"]: r for r in res.rows}
    assert list(rows) == ["Doors", "Support: The Harbour Lights", "Late set", "Changeover crew call", "Curfew"]
    assert rows["Doors"]["kind"] == "doors" and rows["Doors"]["end"] == "" and rows["Doors"]["planned_end"] is None
    assert rows["Doors"]["planned_start"] == utc(2026, 10, 2, 18, 0)
    sup = rows["Support: The Harbour Lights"]
    assert (sup["kind"], sup["start"], sup["end"]) == ("act", "19:30", "20:15")
    assert (sup["planned_start"], sup["planned_end"]) == (utc(2026, 10, 2, 18, 30), utc(2026, 10, 2, 19, 15))
    late = rows["Late set"]
    assert late["planned_start"] == utc(2026, 10, 2, 22, 0)
    assert late["planned_end"] == utc(2026, 10, 2, 23, 30)  # 00:30 BST on Sat 3 Oct
    assert rows["Changeover crew call"]["start"] == "09:45" and rows["Changeover crew call"]["kind"] == "changeover"
    assert rows["Curfew"]["kind"] == "curfew"
    assert res.errors == [
        {"line": 6, "error": sched.MSG_LINE_UNREADABLE},
        {"line": 7, "error": sched.MSG_TIME},
        {"line": 8, "error": sched.MSG_END_BEFORE_START},
    ]
    assert res.error_count == 3


def test_times_before_the_rollover_belong_to_the_next_morning():
    res = sched.parse_import("02:00 Silent disco ends\n05:59 Load-out done\n06:00 Breakfast", "lines", DAY, LON)
    starts = [r["planned_start"] for r in res.rows]
    assert starts == [utc(2026, 10, 3, 1, 0), utc(2026, 10, 3, 4, 59), utc(2026, 10, 2, 5, 0)]
    # a range across the rollover the wrong way round is refused
    res = sched.parse_import("05:00-07:00 Across the rollover", "lines", DAY, LON)
    assert res.rows == [] and res.errors == [{"line": 1, "error": sched.MSG_END_BEFORE_START}]


def test_csv_positional_header_quotes_and_bad_rows():
    text = ("19:00,,Doors\n"
            '19:30,20:15,"Support: Band, with friends",act,Main stage\n'
            "20:30,Changeover\n"                    # start,title
            "21:00,22:30,Headliner,ACT,main stage\n"  # kind is case-insensitive
            "22:00,,Thing,party\n"                  # unknown kind
            "22:00,,Thing,act,Main stage,extra\n"   # too many columns
            "22:00\n"                               # too short
            "22:00,,x" + "y" * 120 + "\n"           # title too long
            "22:00,,Thing,act," + "S" * 41 + "\n"   # stage too long
            "22:00,,Tab\there\n"                    # control character
            "22:00,,Bidi ‮evil\n")             # format character
    res = sched.parse_import(text, "auto", DAY, LON)
    assert res.format == "csv"
    assert [(r["title"], r["kind"], r["stage"]) for r in res.rows] == [
        ("Doors", "doors", ""), ("Support: Band, with friends", "act", "Main stage"),
        ("Changeover", "changeover", ""), ("Headliner", "act", "main stage")]
    assert [(e["line"], e["error"]) for e in res.errors] == [
        (5, sched.MSG_KIND), (6, sched.MSG_ROW_LONG), (7, sched.MSG_ROW_SHORT), (8, sched.MSG_TITLE_LEN),
        (9, sched.MSG_STAGE_LEN), (10, sched.MSG_TITLE_HIDDEN), (11, sched.MSG_LINE_HIDDEN)]
    assert "evil" not in json.dumps(res.errors) and "Tab" not in json.dumps(res.errors)


def test_csv_header_in_any_order_and_bad_header():
    res = sched.parse_import("Title,Stage,Start,End\nDoors,,18:00,\nBand,Main,19:00,20:00\n", "csv", DAY, LON)
    assert res.errors == []
    assert [(r["title"], r["stage"], r["start"], r["end"]) for r in res.rows] == [
        ("Doors", "", "18:00", ""), ("Band", "Main", "19:00", "20:00")]
    res = sched.parse_import("start,title,colour\n19:00,Doors,red\n", "csv", DAY, LON)
    assert res.rows == [] and res.errors == [{"line": 1, "error": sched.MSG_HEADER}]


def test_csv_multiline_quoted_cell_reports_the_right_line():
    res = sched.parse_import('19:00,,"Two\nlines"\n20:00,,Fine\nnot-a-time,,Bad\n', "csv", DAY, LON)
    assert [r["title"] for r in res.rows] == ["Fine"]
    # the quoted newline is a control character in a title; the next rows keep their own numbers
    assert [(e["line"], e["error"]) for e in res.errors] == [(1, sched.MSG_LINE_HIDDEN), (4, sched.MSG_TIME)]


def test_excel_csv_utf8_with_a_byte_order_mark():
    """Excel's "CSV UTF-8" save starts the file with U+FEFF and uses CRLF line ends."""
    text = "﻿Start,End,Title,Kind,Stage\r\n18:00,,Doors,,\r\n19:00,19:45,Café Society,act,Main\r\n"
    res = sched.parse_import(text, "auto", DAY, LON)
    assert res.format == "csv" and res.errors == []
    assert [(r["title"], r["stage"]) for r in res.rows] == [("Doors", ""), ("Café Society", "Main")]
    # only one leading mark is removed; one anywhere else is still a hidden character
    res = sched.parse_import("﻿19:00 Doors\n20:00 Band﻿", "lines", DAY, LON)
    assert [r["title"] for r in res.rows] == ["Doors"] and res.errors == [{"line": 2, "error": sched.MSG_LINE_HIDDEN}]


def test_tsv_paste_from_a_spreadsheet():
    with_header = "Start\tEnd\tTitle\tKind\tStage\n18:00\t\tDoors\t\t\n19:00\t19:45\tSupport, live\tact\tMain\n"
    res = sched.parse_import(with_header, "auto", DAY, LON)
    assert res.format == "tsv" and res.errors == []
    assert [(r["start"], r["end"], r["title"], r["kind"], r["stage"]) for r in res.rows] == [
        ("18:00", "", "Doors", "doors", ""), ("19:00", "19:45", "Support, live", "act", "Main")]
    without = "18:00\t\tDoors\n19:00\t19:45\tSupport\n20:00\tHeadliner\n21:00\tbad\tRow\n"
    res = sched.parse_import(without, "auto", DAY, LON)
    assert res.format == "tsv"
    assert [(r["start"], r["end"], r["title"]) for r in res.rows] == [
        ("18:00", "", "Doors"), ("19:00", "19:45", "Support"), ("20:00", "", "Headliner")]
    assert res.errors == [{"line": 4, "error": sched.MSG_END_TIME}]
    # a tab between the time and the title in a lines paste is fine
    assert [r["title"] for r in sched.parse_import("19:00\tDoors", "lines", DAY, LON).rows] == ["Doors"]
    # ...but a title can't contain one
    assert sched.parse_import("19:00 Door\ts", "lines", DAY, LON).errors == [
        {"line": 1, "error": sched.MSG_TITLE_HIDDEN}]


def test_spring_forward_gap_times_are_refused_on_import():
    """Sun 29 Mar 2026 in London: 01:00-01:59 doesn't exist (01:00 GMT -> 02:00 BST)."""
    sat = "2026-03-28"  # the night of 28/29 Mar: 01:30 is the morning after (before the rollover)
    res = sched.parse_import("01:30 Too late\n00:30 Fine\n00:45-01:15 Ends in the gap\n02:00 Fine too", "lines", sat, LON)
    gap = "That time doesn't exist on this date because the clocks go forward. Use 02:00 or later."
    assert res.errors == [{"line": 1, "error": gap}, {"line": 3, "error": gap}]
    assert [r["planned_start"] for r in res.rows] == [utc(2026, 3, 29, 0, 30), utc(2026, 3, 29, 1, 0)]


def test_auto_detects_lines_with_commas_in_titles():
    assert sched.detect_format("19:30-20:15 Support: Band, with friends") == "lines"
    assert sched.detect_format("# header comment\n19:00,Doors") == "csv"
    assert sched.detect_format("start,end,title\n") == "csv"
    assert sched.detect_format("") == "lines"


def test_import_caps():
    ok = "\n".join("19:00 Act" for _ in range(sched.IMPORT_MAX_ROWS))
    assert len(sched.parse_import(ok, "lines", DAY, LON).rows) == 300
    with pytest.raises(sched.ImportTooLarge) as e:
        sched.parse_import(ok + "\n19:00 One too many", "lines", DAY, LON)
    assert str(e.value) == sched.MSG_IMPORT_TOO_MANY
    csv_ok = "start,title\n" + "\n".join("19:00,Act" for _ in range(300))  # a header doesn't count
    assert len(sched.parse_import(csv_ok, "csv", DAY, LON).rows) == 300
    with pytest.raises(sched.ImportTooLarge):
        sched.parse_import(csv_ok + "\n19:00,Act", "csv", DAY, LON)
    with pytest.raises(sched.ImportTooLarge) as e:
        sched.parse_import("#" * (sched.IMPORT_MAX_BYTES + 1), "lines", DAY, LON)
    assert str(e.value) == sched.MSG_IMPORT_TOO_BIG
    # bytes, not characters: 21,846 three-byte characters are over 64 KB
    with pytest.raises(sched.ImportTooLarge):
        sched.parse_import("#" + "€" * 21846, "lines", DAY, LON)


def test_import_error_list_is_bounded_but_counted():
    res = sched.parse_import("\n".join("nonsense" for _ in range(250)), "lines", DAY, LON)
    assert len(res.errors) == sched.IMPORT_ERRORS_MAX and res.error_count == 250


def test_text_checks():
    assert sched.clean_setlist("a\r\nb\rc") == "a\nb\nc"
    for bad in ("tab\there", "zero​width", "nul\x00", "rtl‮"):
        with pytest.raises(ValueError):
            sched.clean_setlist(bad)
        with pytest.raises(ValueError):
            sched.clean_title(bad)
    with pytest.raises(ValueError):
        sched.clean_title("two\nlines")
    # line and paragraph separators (Zl, Zp) and piled-up combining marks
    for bad in ("line sep", "para sep", "é̂̃̄"):
        with pytest.raises(ValueError):
            sched.clean_title(bad)
        with pytest.raises(ValueError):
            sched.clean_stage(bad)
    assert sched.clean_title("é̂̃ ok") == "é̂̃ ok"  # three is fine
    assert sched.clean_title("Beyoncé and Björk") == "Beyoncé and Björk"
    with pytest.raises(ValueError, match="8 KB"):
        sched.clean_setlist("€" * 2731)  # 8,193 bytes
    assert sched.clean_setlist("€" * 2730)
    assert sched.norm_hhmm("9:05") == "09:05" and sched.norm_hhmm("24:00") is None and sched.norm_hhmm("1900") is None


# =================================================================== now / next
def _item(i, title, kind, start, end=None, stage=""):
    return {"id": i, "sort": i, "stage": stage, "kind": kind, "title": title,
            "planned_start": start, "planned_end": end, "setlist": ""}


T = 1_790_000_000.0  # an arbitrary instant; minutes below are relative to it
M = 60.0
DAY_ITEMS = [
    _item(1, "Doors", "doors", T),                                   # no end: until the next start
    _item(2, "Support", "act", T + 60 * M, T + 105 * M, "Main"),
    _item(3, "Second stage act", "act", T + 60 * M, T + 120 * M, "Second"),
    _item(4, "Changeover", "changeover", T + 105 * M, T + 135 * M, "Main"),
    _item(5, "Headliner", "act", T + 135 * M, T + 225 * M, "Main"),
    _item(6, "Curfew", "curfew", T + 270 * M),
    _item(7, "Second stage curfew", "curfew", T + 300 * M, None, "Second"),
]


@pytest.mark.parametrize("minute,stage,state,current,nxt,curfew", [
    (-30, "", "before", None, 1, 6),
    (0, "", "running", 1, 2, 6),
    (30, "Main", "running", 1, 2, 6),
    (70, "Main", "running", 2, 4, 6),
    (70, "second", "running", 3, None, 6),    # stage match ignores case; Main items are filtered out
    (110, "Main", "running", 4, 5, 6),
    (110, "Second", "running", 3, None, 6),
    (125, "Second", "between", None, None, 6),
    (240, "Main", "between", None, None, 6),  # after the last act, before curfew
    (280, "Main", "over", None, None, 6),     # past curfew: show over, the passed curfew kept
    (280, "Second", "over", None, None, 7),   # the Second stage's own curfew is still to come...
])
def test_now_next(minute, stage, state, current, nxt, curfew):
    nn = sched.now_next(DAY_ITEMS, T + minute * M, stage)
    got = (nn["state"], *(nn[k]["id"] if nn[k] else None for k in ("current", "next", "curfew")))
    if (minute, stage) == (280, "Second"):
        # ...so it isn't over yet: nothing running, the Second curfew in 20 min
        assert got == ("between", None, None, 7) and nn["seconds_to_curfew"] == 20 * M
        return
    assert got == (state, current, nxt, curfew)


def test_now_next_curfew_countdown_and_after():
    assert sched.now_next(DAY_ITEMS, T + 255 * M, "")["seconds_to_curfew"] == 15 * M
    after = sched.now_next(DAY_ITEMS, T + 400 * M, "")
    assert after["state"] == "over" and after["curfew"]["id"] == 7 and after["seconds_to_curfew"] == -100 * M


def test_now_next_edges():
    assert sched.now_next([], T, "")["state"] == "empty"
    # start <= now < end: the boundary belongs to the next item
    nn = sched.now_next(DAY_ITEMS, T + 105 * M, "Main")
    assert nn["current"]["id"] == 4 and nn["next"]["id"] == 5
    # an open-ended last item without a curfew stays current
    open_end = [_item(1, "Late act", "act", T)]
    assert sched.now_next(open_end, T + 600 * M, "")["current"]["id"] == 1
    # ...but never past a curfew
    assert sched.now_next(open_end + [_item(2, "Curfew", "curfew", T + 60 * M)], T + 61 * M, "")["current"] is None
    # an item with no stage shows everywhere; a dashboard with no stage sees every item
    assert sched.stage_matches("", "Main") and sched.stage_matches("Main", "") and not sched.stage_matches("Main", "B")


def test_load_out_after_the_curfew_runs_normally():
    """Curfew 23:00, Load Out 23:15-01:00: NOW is the Load Out at 23:30 and 00:30; over at 01:05."""
    c = T
    items = [_item(1, "Headliner", "act", c - 90 * M, c - 10 * M), _item(2, "Curfew", "curfew", c),
             _item(3, "Load Out", "load_out", c + 15 * M, c + 120 * M)]

    def at(m):
        nn = sched.now_next(items, c + m * M, "")
        return nn["state"], nn["current"]["id"] if nn["current"] else None, nn["next"]["id"] if nn["next"] else None

    assert at(-5) == ("between", None, 3)
    assert at(10) == ("between", None, 3)
    assert at(30) == ("running", 3, None)
    assert at(90) == ("running", 3, None)
    assert at(125) == ("over", None, None)
    # open-ended: runs on; an act that starts before the curfew is still cut off at it
    items[2]["planned_end"] = None
    assert at(600)[:2] == ("running", 3)
    assert sched.now_next(items, c - 5 * M, "")["current"] is None


def test_new_kinds_and_inferring_them_from_titles():
    assert sched.KINDS == ("venue_access", "load_in", "crew_call", "soundcheck", "doors", "act",
                           "changeover", "curfew", "load_out", "other")
    for k in ("venue access", "load in", "crew call", "soundcheck", "load out"):
        assert k in sched.MSG_KIND
    for title, kind in [
        ("PA Load In - Rigger Call", "load_in"), ("Video Load In", "load_in"), ("Get-in", "load_in"),
        ("LOAD-OUT", "load_out"), ("Get out", "load_out"), ("Matt Soundcheck", "soundcheck"),
        ("Sound check", "soundcheck"), ("Crew Call", "crew_call"), ("Venue access for trucks", "venue_access"),
        ("Pre-Roll", "act"), ("doors", "doors"), ("Doors 19:00", "doors"), ("Curfew", "curfew"),
        ("Changeover", "changeover"), ("Changeover and load in", "changeover"),
        ("Download", "act"), ("Upload in progress", "act"), ("Reload Intro", "act"),
    ]:
        assert sched.infer_kind(title) == kind, title


# =================================================================== rebase
def test_rebase_across_the_london_dst_change():
    """Fri 23 Oct -> Sat 24 Oct 2026: the night of 24/25 Oct has the clocks going back."""
    items = [
        _item(1, "Headliner", "act", utc(2026, 10, 23, 18, 0), utc(2026, 10, 23, 19, 30)),  # 19:00-20:30 BST
        _item(2, "Late", "act", utc(2026, 10, 24, 0, 30)),   # 01:30 BST, the morning after
        _item(3, "Load-out", "other", utc(2026, 10, 24, 2, 0)),  # 03:00 BST, the morning after
    ]
    moved = sched.rebase(items, "2026-10-23", LON, "2026-10-24", LON)
    assert [sitetime.local_hhmm(m["planned_start"], LON) for m in moved] == ["19:00", "01:30", "03:00"]
    assert moved[0]["planned_start"] == utc(2026, 10, 24, 18, 0)        # BST still
    assert moved[0]["planned_end"] == utc(2026, 10, 24, 19, 30)
    assert moved[1]["planned_start"] == utc(2026, 10, 25, 0, 30)        # 01:30 happens twice: the first (BST)
    assert moved[2]["planned_start"] == utc(2026, 10, 25, 3, 0)         # 03:00 GMT: 25 hours later
    assert items[0]["planned_start"] == utc(2026, 10, 23, 18, 0)        # the input is not changed
    # and on to Sun 25 Oct: the evening is GMT now
    again = sched.rebase(moved, "2026-10-24", LON, "2026-10-25", LON)
    assert again[0]["planned_start"] == utc(2026, 10, 25, 19, 0)
    assert again[1]["planned_start"] == utc(2026, 10, 26, 1, 30)


def test_rebase_onto_a_spring_forward_gap():
    """Fri 27 -> Sat 28 Mar 2026: the morning after becomes Sun 29 Mar, when 01:00-01:59 is
    skipped. 01:30 lands on the fold=0 instant 01:30Z (shown as 02:30 BST)."""
    items = [_item(1, "Late", "act", utc(2026, 3, 28, 1, 30)),          # 01:30 GMT, Sat morning
             _item(2, "Evening", "act", utc(2026, 3, 27, 19, 0))]
    moved = sched.rebase(items, "2026-03-27", LON, "2026-03-28", LON)
    assert moved[0]["planned_start"] == utc(2026, 3, 29, 1, 30)
    assert sitetime.local_hhmm(moved[0]["planned_start"], LON) == "02:30"
    assert moved[1]["planned_start"] == utc(2026, 3, 28, 19, 0)


def test_rebase_to_another_zone_keeps_local_times():
    items = [_item(1, "Doors", "doors", utc(2026, 10, 2, 17, 0)), _item(2, "Curfew", "curfew", utc(2026, 10, 2, 23, 30))]
    moved = sched.rebase(items, DAY, LON, DAY, NY)
    assert [sitetime.local_hhmm(m["planned_start"], NY) for m in moved] == ["18:00", "00:30"]
    assert moved[0]["planned_start"] == utc(2026, 10, 2, 22, 0)  # 18:00 EDT
    assert moved[1]["planned_start"] == utc(2026, 10, 3, 4, 30)  # 00:30 EDT on Sat 3 Oct


# =================================================================== API fixtures
@pytest.fixture
def client(tmp_path):
    """No emulate: the schedule starts empty."""
    hub = Hub(tmp_path)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def _admin(c):
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200


def _london_day(c, day=DAY):
    s = c.hub.config.site.model_dump()
    assert c.put("/api/admin/site", json={**s, "timezone": "Europe/London"}).status_code == 200
    assert c.patch("/api/admin/shows/current", json={"day": day}).status_code == 200


BASIC = [
    {"kind": "doors", "title": "Doors", "start": "18:00"},
    {"kind": "act", "title": "Support", "start": "19:00", "end": "19:45", "stage": "Main stage",
     "setlist": "1. One\n2. **Two**"},
    {"kind": "act", "title": "Acoustic tent", "start": "19:00", "end": "20:00", "stage": "Tent"},
    {"kind": "curfew", "title": "Curfew", "start": "00:30"},
]


def _put(c, items, show_id=None, revision=None, **kw):
    rev = c.hub.schedule.revision() if revision is None else revision
    return c.put("/api/admin/schedule", json={"show_id": show_id or c.hub.recorder.show_id, "revision": rev,
                                              "items": items}, **kw)


def _commit(c, text, revision=None, show_id=None):
    rev = c.hub.schedule.revision() if revision is None else revision
    return c.post("/api/admin/schedule/import", json={"text": text, "dry_run": False, "revision": rev,
                                                      "show_id": show_id or c.hub.recorder.show_id})


def _editable(items):
    """What the admin page sends back for unchanged items."""
    return [{k: i[k] for k in ("id", "date", "stage", "kind", "title", "start", "end", "setlist")} for i in items]


# =================================================================== API
def test_put_schedule_resolves_times_and_round_trips(client):
    _admin(client)
    _london_day(client)
    r = _put(client, BASIC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"show_id", "day", "revision", "items"} and body["day"] == DAY
    assert body["revision"] == client.hub.schedule.revision() > 0
    items = body["items"]
    assert [i["title"] for i in items] == ["Doors", "Support", "Acoustic tent", "Curfew"]
    assert all(set(i) == PUBLIC_ITEM_KEYS for i in items)
    cur = items[-1]
    assert (cur["date"], cur["start"], cur["end"], cur["planned_start"]) == (
        "2026-10-03", "00:30", "", utc(2026, 10, 2, 23, 30))
    assert items[0]["date"] == DAY
    assert items[1]["planned_end"] == utc(2026, 10, 2, 18, 45) and items[1]["setlist"] == "1. One\n2. **Two**"
    # sending the list back with ids keeps the ids
    ids = [i["id"] for i in items]
    resend = _editable(items)
    resend[0]["title"] = "Doors open"
    again = _put(client, resend).json()
    assert [i["id"] for i in again["items"]] == ids and again["items"][0]["title"] == "Doors open"
    assert again["revision"] > body["revision"]
    # an empty list clears it
    assert _put(client, []).json()["items"] == []


def test_put_schedule_is_admin_and_same_origin(client):
    assert _put(client, BASIC).status_code == 401
    _admin(client)
    assert _put(client, BASIC, headers=EVIL).status_code == 403
    for path in ("/api/admin/schedule/import", "/api/admin/schedule/demo"):
        assert client.post(path, json={"text": "19:00 Doors"} if "import" in path else {}, headers=EVIL).status_code == 403
    client.post("/api/admin/logout")
    assert client.post("/api/admin/schedule/import", json={"text": "19:00 Doors"}).status_code == 401
    assert client.post("/api/admin/schedule/demo").status_code == 401
    assert client.hub.schedule.items() == []


def test_stale_show_id_gets_409_and_changes_nothing(client):
    _admin(client)
    old = client.hub.recorder.show_id
    assert _put(client, BASIC, show_id=old).status_code == 200
    assert _put(client, BASIC, show_id=old + 5).status_code == 409
    # a new day started on another page: the old tab can't overwrite it
    assert client.post("/api/admin/shows", json={"name": "Day 2"}).status_code == 200
    r = _put(client, [{"kind": "act", "title": "Old tab", "start": "20:00"}], show_id=old)
    assert r.status_code == 409 and "Nothing has been changed" in r.json()["detail"]
    assert client.hub.schedule.items() == []                             # Day 2 untouched
    assert len(client.hub.recorder.schedule_items(old)) == 4              # Day 1 untouched
    assert _commit(client, "19:00 Doors", show_id=old).status_code == 409
    assert client.post("/api/admin/schedule/demo", json={"show_id": old}).status_code == 409
    assert client.hub.schedule.items() == []


SECRET = "zz-do-not-echo-zz"


@pytest.mark.parametrize("items,where", [
    ([{"kind": "act", "title": "x" * 121 + SECRET, "start": "19:00"}], "title"),
    ([{"kind": "act", "title": "", "start": "19:00"}], "title"),
    ([{"kind": "act", "title": "Bidi‮" + SECRET, "start": "19:00"}], "title"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "stage": "S" * 41 + SECRET}], "stage"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "stage": "Zero​" + SECRET}], "stage"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "setlist": "€" * 2731 + SECRET}], "setlist"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "setlist": "tab\t" + SECRET}], "setlist"),
    ([{"kind": "party", "title": "Band", "start": "19:00"}], "kind"),
    ([{"kind": "act", "title": "Band", "start": "7pm"}], "start"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "end": "18:00"}], "end"),
    ([{"kind": "act", "title": "Band", "start": "05:00", "end": "07:00"}], "end"),  # across the rollover
    ([{"kind": "act", "title": "Band", "start": "19:00", "colour": SECRET}], "colour"),
    ([{"kind": "act", "title": "Line sep" + SECRET, "start": "19:00"}], "title"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "stage": "Zalgó̂̃̄" + SECRET}], "stage"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "date": "2026-10-09"}], "date"),  # not the show day
    ([{"kind": "act", "title": "Band", "start": "19:00", "date": "2026-02-30"}], "date"),
    ([{"kind": "act", "title": "Band", "start": "19:00", "date": SECRET}], "date"),
    ([{"kind": "act", "title": "Band", "start": "19:00"}] * 301, "items"),
    ([{"kind": "act", "title": f"Band {n}", "start": "19:00", "setlist": "s" * 8000} for n in range(33)], "items"),
    ([{"id": 7, "kind": "act", "title": "A", "start": "19:00"}, {"id": 7, "kind": "act", "title": "B", "start": "20:00"}],
     "items"),
])
def test_caps_and_bad_items_get_422_without_echo(client, items, where):
    _admin(client)
    _london_day(client)
    r = _put(client, items)
    assert r.status_code == 422, r.text
    assert SECRET not in r.text and "x" * 121 not in r.text and "s" * 200 not in r.text
    assert any(where in [str(p) for p in d["loc"]] or where in d["msg"].lower() or where == "items"
               for d in r.json()["detail"])
    assert client.hub.schedule.items() == []


def test_end_before_start_error_points_at_the_item(client):
    _admin(client)
    r = _put(client, [{"kind": "doors", "title": "Doors", "start": "18:00"},
                      {"kind": "act", "title": "Band", "start": "21:00", "end": "20:00"}])
    assert r.status_code == 422
    assert r.json()["detail"] == [{"loc": ["body", "items", 1, "end"], "msg": sched.MSG_END_BEFORE_START,
                                   "type": "value_error"}]


def test_import_dry_run_then_commit(client):
    _admin(client)
    _london_day(client)
    text = "19:00 Doors\n19:30-20:15 Support: Band\nrubbish " + SECRET + "\n23:30 Curfew"
    r = client.post("/api/admin/schedule/import", json={"text": text})
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "lines" and body["error_count"] == 1
    assert body["errors"] == [{"line": 3, "error": sched.MSG_LINE_UNREADABLE}]
    assert [row["title"] for row in body["rows"]] == ["Doors", "Support: Band", "Curfew"]
    assert SECRET not in r.text
    assert client.hub.schedule.items() == []  # a dry run changes nothing
    # committing with a bad line adds nothing, and returns where the problems are, not the rows
    sid = client.hub.recorder.show_id
    r = _commit(client, text)
    assert r.status_code == 422 and SECRET not in r.text
    assert r.json() == {"detail": "Some lines could not be read. Nothing has been added.",
                        "errors": [{"line": 3, "error": sched.MSG_LINE_UNREADABLE}], "error_count": 1}
    assert client.hub.schedule.items() == []
    # commit needs a show id and a revision
    for partial in ({"show_id": sid}, {"revision": 0}, {}):
        assert client.post("/api/admin/schedule/import",
                           json={"text": "19:00 Doors", "dry_run": False, **partial}).status_code == 422
    # a clean import is added after what is there
    assert _put(client, [{"kind": "act", "title": "Soundcheck", "start": "16:00"}]).status_code == 200
    r = _commit(client, "start,end,title\n19:00,,Doors\n19:30,20:15,Support")
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()["schedule"]["items"]] == ["Soundcheck", "Doors", "Support"]
    assert r.json()["schedule"]["revision"] == client.hub.schedule.revision()


def test_lost_update_tab_a_put_after_tab_b_import(client):
    _admin(client)
    _london_day(client)
    assert _put(client, BASIC).status_code == 200
    # Tab A and tab B both load the schedule
    loaded = client.get("/api/schedule").json()
    rev_a = rev_b = loaded["revision"]
    # Tab B imports two more rows
    r = _commit(client, "21:00 Headliner\n22:30 Encore", revision=rev_b)
    assert r.status_code == 200
    # Tab A, still on the old revision, saves its edit: refused, B's rows survive
    edit = _editable(loaded["items"])
    edit[0]["title"] = "Doors (tab A)"
    r = _put(client, edit, revision=rev_a)
    assert r.status_code == 409
    assert r.json()["detail"] == ("The schedule was changed elsewhere. Reload to see it, then make your "
                                  "change again.")
    titles = [i["title"] for i in client.get("/api/schedule").json()["items"]]
    assert "Headliner" in titles and "Encore" in titles and "Doors (tab A)" not in titles
    # Tab B importing again on its own stale revision is refused too
    assert _commit(client, "23:00 Late", revision=rev_b).status_code == 409
    # after reloading, A's change goes through
    fresh = client.get("/api/schedule").json()
    edit = _editable(fresh["items"])
    edit[0]["title"] = "Doors (tab A)"
    assert _put(client, edit, revision=fresh["revision"]).status_code == 200


def test_revision_survives_a_restart(tmp_path):
    hub = Hub(tmp_path)
    rows, _ = sched.build_rows(BASIC, DAY, LON)
    hub.schedule.replace(None, rows)
    rev = hub.schedule.revision()
    hub.schedule.replace(None, rows)  # two writes in the same millisecond still differ
    assert hub.schedule.revision() > rev
    rev = hub.schedule.revision()
    hub.recorder.close()
    again = Hub(tmp_path)
    try:
        assert again.schedule.revision() == rev
    finally:
        again.recorder.close()


def test_unchanged_save_after_a_rollover_change_moves_nothing(client):
    """Show day Fri 2 Oct, an item at 03:00 (the morning after: 02:00Z on Sat 3 Oct). Changing the
    rollover to 02:00 and saving the unchanged list must not move it to Fri 2 Oct."""
    _admin(client)
    _london_day(client)
    _put(client, BASIC + [{"kind": "other", "title": "Load-out", "start": "03:00", "end": "05:30"}])
    before = client.get("/api/schedule").json()
    load_out = next(i for i in before["items"] if i["title"] == "Load-out")
    assert load_out["planned_start"] == utc(2026, 10, 3, 2, 0) and load_out["date"] == "2026-10-03"
    s = client.hub.config.site.model_dump()
    for rollover in ("02:00", "06:00", "00:00", "06:00"):
        assert client.put("/api/admin/site", json={**s, "day_rollover": rollover}).status_code == 200
        cur = client.get("/api/schedule").json()
        assert _put(client, _editable(cur["items"]), revision=cur["revision"]).status_code == 200
        after = client.get("/api/schedule").json()["items"]
        assert [(i["id"], i["planned_start"], i["planned_end"]) for i in after] == [
            (i["id"], i["planned_start"], i["planned_end"]) for i in before["items"]], rollover


def test_dated_item_end_is_the_first_matching_time_after_the_start(client):
    _admin(client)
    _london_day(client)
    r = _put(client, [{"kind": "act", "title": "Late", "date": DAY, "start": "23:00", "end": "01:00"},
                      {"kind": "act", "title": "Early", "date": "2026-10-03", "start": "07:00", "end": "08:00"}])
    assert r.status_code == 200, r.text
    late, early = sorted(r.json()["items"], key=lambda i: i["planned_start"])
    assert (late["planned_start"], late["planned_end"]) == (utc(2026, 10, 2, 22, 0), utc(2026, 10, 3, 0, 0))
    # pinned to the morning after even though 07:00 is after the rollover
    assert early["planned_start"] == utc(2026, 10, 3, 6, 0) and early["date"] == "2026-10-03"


def test_spring_forward_gap_times_are_refused_on_put(client):
    _admin(client)
    _london_day(client, "2026-03-28")
    r = _put(client, [{"kind": "act", "title": "Band", "start": "01:30"}])
    assert r.status_code == 422
    assert r.json()["detail"] == [{"loc": ["body", "items", 0, "start"], "type": "value_error",
                                   "msg": "That time doesn't exist on this date because the clocks go "
                                          "forward. Use 02:00 or later."}]
    r = _put(client, [{"kind": "act", "title": "Band", "date": "2026-03-29", "start": "00:30", "end": "01:15"}])
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "end"
    # the evening before and the time the clocks reach are fine
    r = _put(client, [{"kind": "act", "title": "Band", "start": "02:00"}, {"kind": "act", "title": "Eve", "start": "20:00"}])
    assert r.status_code == 200
    assert [i["planned_start"] for i in r.json()["items"]] == [utc(2026, 3, 28, 20, 0), utc(2026, 3, 29, 1, 0)]


def test_import_caps_get_422_without_echo(client):
    _admin(client)
    big = "#" + SECRET + "x" * sched.IMPORT_MAX_BYTES
    r = client.post("/api/admin/schedule/import", json={"text": big})
    assert r.status_code == 422 and SECRET not in r.text
    # 64 KB of text in characters but more in bytes
    r = client.post("/api/admin/schedule/import", json={"text": "€" * 30000})
    assert r.status_code == 422 and sched.MSG_IMPORT_TOO_BIG in r.text
    rows = "\n".join("19:00 Act" for _ in range(301))
    r = client.post("/api/admin/schedule/import", json={"text": rows})
    assert r.status_code == 422 and r.json()["detail"] == sched.MSG_IMPORT_TOO_MANY
    # an import that would take the schedule over 300 items
    assert _put(client, [{"kind": "act", "title": f"Item {n}", "start": "19:00"} for n in range(299)]).status_code == 200
    r = _commit(client, "19:00 A\n19:00 B")
    assert r.status_code == 422 and r.json()["detail"] == sched.MSG_TOO_MANY
    assert len(client.hub.schedule.items()) == 299
    for bad in ({"text": "x", "format": "xml"}, {"text": "x", "extra": 1}):
        assert client.post("/api/admin/schedule/import", json=bad).status_code == 422


def test_public_get_is_read_only_and_has_no_private_fields(client):
    _admin(client)
    _london_day(client)
    _put(client, BASIC)
    client.post("/api/admin/logout")
    r = client.get("/api/schedule")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"show_id", "day", "revision", "now", "stage", "items", "now_next"}
    assert body["revision"] == client.hub.schedule.revision()
    assert set(body["now_next"]) == {"state", "current_id", "next_id", "curfew_id", "seconds_to_curfew"}
    for it in body["items"]:
        assert set(it) == PUBLIC_ITEM_KEYS
        for private in ("updated", "sort", "actual_start", "actual_end", "show_id"):
            assert private not in it
    assert [i["title"] for i in client.get("/api/schedule", params={"stage": "tent"}).json()["items"]] == [
        "Doors", "Acoustic tent", "Curfew"]
    assert client.get("/api/schedule", params={"stage": "S" * 41}).status_code == 422
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/schedule").status_code == 405
    snap = client.get("/api/snapshot").json()["schedule"]
    assert snap == {"show_id": body["show_id"], "day": DAY, "revision": body["revision"]}  # no items


def test_patch_day_rebases_the_schedule_across_dst(client):
    _admin(client)
    _london_day(client, "2026-10-24")
    _put(client, [{"kind": "act", "title": "Headliner", "start": "19:00", "end": "20:30"},
                  {"kind": "act", "title": "Late", "start": "01:30"},
                  {"kind": "other", "title": "Load-out", "start": "03:00"}])
    before = {i["title"]: i for i in client.get("/api/schedule").json()["items"]}
    assert before["Late"]["planned_start"] == utc(2026, 10, 25, 0, 30)
    assert client.patch("/api/admin/shows/current", json={"day": "2026-10-25"}).status_code == 200
    after = {i["title"]: i for i in client.get("/api/schedule").json()["items"]}
    assert {t: (i["start"], i["end"]) for t, i in after.items()} == {
        "Headliner": ("19:00", "20:30"), "Late": ("01:30", ""), "Load-out": ("03:00", "")}
    assert after["Headliner"]["planned_start"] == utc(2026, 10, 25, 19, 0)  # GMT now: 25 hours later
    assert after["Headliner"]["planned_start"] - before["Headliner"]["planned_start"] == 25 * 3600
    assert after["Late"]["planned_start"] == utc(2026, 10, 26, 1, 30)
    assert after["Load-out"]["planned_start"] - before["Load-out"]["planned_start"] == 24 * 3600


def test_time_zone_change_rebases_the_schedule(client):
    _admin(client)
    _london_day(client)
    _put(client, BASIC)
    london = client.get("/api/schedule").json()["items"]
    s = client.hub.config.site.model_dump()
    assert client.put("/api/admin/site", json={**s, "timezone": "America/New_York"}).status_code == 200
    ny = client.get("/api/schedule").json()["items"]
    assert [(i["start"], i["end"]) for i in ny] == [(i["start"], i["end"]) for i in london]
    assert all(n["planned_start"] - lo["planned_start"] == 5 * 3600 for n, lo in zip(ny, london))
    # back to unset ("this computer's zone") also keeps the local times
    assert client.put("/api/admin/site", json={**s, "timezone": ""}).status_code == 200
    assert [i["start"] for i in client.get("/api/schedule").json()["items"]] == [i["start"] for i in london]


def test_rollover_change_that_moves_the_show_day_rebases(client):
    _admin(client)
    hub = client.hub
    s = hub.config.site.model_dump()
    assert client.put("/api/admin/site", json={**s, "timezone": "UTC", "day_rollover": "00:00"}).status_code == 200
    started = hub.recorder.show_started
    hub.recorder._db.execute("UPDATE shows SET started = ? WHERE id = ?", (utc(2026, 10, 3, 3, 0), hub.recorder.show_id))
    hub.recorder._db.commit()
    hub.recorder.show_started = utc(2026, 10, 3, 3, 0)
    assert hub.show_info()["day"] == "2026-10-03"
    _put(client, [{"kind": "act", "title": "Band", "start": "20:00"}])
    assert hub.schedule.items()[0]["planned_start"] == utc(2026, 10, 3, 20, 0)
    # a 06:00 rollover makes a show started at 03:00 belong to Fri 2 Oct: the band moves with it
    assert client.put("/api/admin/site", json={**s, "timezone": "UTC", "day_rollover": "06:00"}).status_code == 200
    assert hub.show_info()["day"] == "2026-10-02"
    assert hub.schedule.items()[0]["planned_start"] == utc(2026, 10, 2, 20, 0)
    del started


def test_demo_endpoint_only_when_empty_outside_emulate(client):
    _admin(client)
    r = client.post("/api/admin/schedule/demo")
    if r.status_code == 409:  # within 95 min of the day rollover on this computer: can't fit
        assert "Nothing has been changed" in r.json()["detail"]
        return
    assert r.status_code == 200 and [i["kind"] for i in r.json()["items"]] == [
        "doors", "act", "changeover", "act", "curfew"]
    assert client.post("/api/admin/schedule/demo").status_code == 409  # not empty any more
    assert client.get("/api/admin/state").json()["schedule_limits"]["demo_allowed"] is False


def test_admin_state_lists_schedule_stages_and_limits(client):
    _admin(client)
    _london_day(client)
    _put(client, BASIC)
    st = client.get("/api/admin/state").json()
    assert {"Main stage", "Tent"} <= set(st["stages"])
    lim = st["schedule_limits"]
    assert lim["max_items"] == 300 and lim["kinds"] == list(sched.KINDS) and lim["setlist_max_bytes"] == 8192


# =================================================================== service, demo, WS
def _site_away_from(now: float) -> SiteConfig:
    """UTC with a day rollover at least 3 h away from ``now``, so the demo day always fits."""
    hour = datetime.fromtimestamp(now, timezone.utc).hour
    for r in range(12):
        if min((hour - r) % 24, (r - hour) % 24) >= 3:
            return SiteConfig(timezone="UTC", day_rollover=f"{r:02d}:00")
    raise AssertionError("unreachable")


def test_demo_day_relative_to_now(tmp_path):
    hub = Hub(tmp_path)
    try:
        now = utc(2026, 10, 2, 20, 0, 30)
        hub.config.site = SiteConfig(timezone="Europe/London")
        hub.recorder.set_show_day("2026-10-02")
        pub = hub.schedule.load_demo(now=now)
        items = pub["items"]
        base = utc(2026, 10, 2, 20, 0)
        assert [(i["kind"], i["planned_start"] - base) for i in items] == [
            ("doors", -40 * 60), ("act", -10 * 60), ("changeover", 30 * 60), ("act", 45 * 60), ("curfew", 95 * 60)]
        assert [i["start"] for i in items] == ["20:20", "20:50", "21:30", "21:45", "22:35"]  # BST
        nn = sched.now_next(items, now, "")
        assert nn["state"] == "running" and nn["current"]["title"].startswith("Support")
        assert nn["next"]["kind"] == "changeover" and nn["curfew"]["kind"] == "curfew"
        headliner = items[3]
        assert headliner["setlist"].startswith("## Main set") and "**Harbour Walls**" in headliner["setlist"]
        # on a show day that isn't today, or across the rollover, it refuses
        with pytest.raises(sched.DemoDoesNotFit):
            hub.schedule.load_demo(now=utc(2026, 10, 9, 20, 0))
        hub.recorder.set_show_day("2026-10-02")
        with pytest.raises(sched.DemoDoesNotFit):
            hub.schedule.load_demo(now=utc(2026, 10, 3, 4, 0))  # 05:00 BST: the headliner would cross 06:00
    finally:
        hub.recorder.close()


@pytest.mark.parametrize("minutes_to_rollover,fits", [(95, False), (91, False), (96, True)])
def test_demo_refuses_when_the_curfew_would_cross_the_rollover(tmp_path, minutes_to_rollover, fits):
    """Rollover 05:00 UTC, show day Thu 1 Oct, now on the Friday morning. With the rollover at
    now+91 or now+95 min only the curfew (95 min, no end) lands past it, so it would resolve to
    05:xx on Thursday, a day early: every row is checked, not just the first."""
    hub = Hub(tmp_path)
    try:
        hub.config.site = SiteConfig(timezone="UTC", day_rollover="05:00")
        hub.recorder.set_show_day("2026-10-01")
        now = utc(2026, 10, 2, 5, 0) - minutes_to_rollover * 60
        if fits:
            items = hub.schedule.load_demo(now=now)["items"]
            assert items[-1]["kind"] == "curfew" and items[-1]["planned_start"] == now + 95 * 60
        else:
            with pytest.raises(sched.DemoDoesNotFit):
                hub.schedule.load_demo(now=now)
            assert hub.schedule.items() == []
    finally:
        hub.recorder.close()


def test_emulate_starts_with_demo_festival_and_a_demo_day(tmp_path):
    first = Hub(tmp_path, emulate=True)
    first.config.site = _site_away_from(time.time())
    first.save_config()
    first.recorder.close()
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        snap = c.get("/api/snapshot").json()
        assert snap["show"]["event_name"] == "Demo Festival" and snap["show"]["name"] == "Day 1"
        assert set(snap["schedule"]) == {"show_id", "day", "revision"} and snap["schedule"]["revision"] > 0
        full = c.get("/api/schedule").json()
        assert [i["kind"] for i in full["items"]] == ["doors", "act", "changeover", "act", "curfew"]
        nn = full["now_next"]
        assert nn["state"] == "running" and nn["seconds_to_curfew"] > 90 * 60
        # emulate mode: the demo can be reloaded over an existing schedule
        _admin(c)
        assert c.post("/api/admin/schedule/demo", json={"show_id": snap["show"]["id"]}).status_code == 200


def test_ws_schedule_message(tmp_path):
    hub = Hub(tmp_path)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        _admin(c)
        with c.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert snap["schedule"] == {"show_id": hub.recorder.show_id, "day": hub.show_info()["day"], "revision": 0}
            r = _put(c, BASIC)
            assert r.status_code == 200
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "schedule":
                    break
            else:
                raise AssertionError("no schedule message")
            # small: no items or setlists; the dashboard fetches GET /api/schedule?stage=
            assert msg == {"type": "schedule", "show_id": hub.recorder.show_id, "revision": r.json()["revision"]}


def test_live_feed_drops_messages_for_a_client_over_the_byte_cap(tmp_path):
    from stagewatch.web import server as srv
    hub = Hub(tmp_path)
    try:
        feed = srv.LiveFeed(hub)
        slow, ok = srv.ClientQueue(), srv.ClientQueue()
        feed.clients.update({slow, ok})
        big = {"type": "x", "pad": "p" * (srv.CLIENT_QUEUE_MAX_BYTES // 2)}
        feed._send_all(big)
        # ok is a reader that keeps up: it takes its message before the next arrives
        ok.get_nowait()
        assert ok.bytes == 0
        feed._send_all(big)  # the second would take the slow client over the cap: dropped
        assert slow.qsize() == 1 and ok.qsize() == 1 and 0 < slow.bytes <= srv.CLIENT_QUEUE_MAX_BYTES
        slow.get_nowait()
        assert slow.bytes == 0
        feed._send_all(big)  # caught up: messages flow again
        assert slow.qsize() == 1
        slow.put_nowait({"type": "pong"})  # direct puts (the /ws pong) are counted too
        assert slow.bytes > srv.CLIENT_QUEUE_MAX_BYTES // 2 and slow.qsize() == 2
    finally:
        hub.recorder.close()


def test_recorder_replace_is_all_or_nothing(tmp_path):
    hub = Hub(tmp_path)
    try:
        rec = hub.recorder
        rows, errors = sched.build_rows(BASIC, DAY, LON)
        assert errors == []
        rec.replace_schedule(rec.show_id, rows)
        before = rec.schedule_items()
        broken = [dict(rows[0]), {**rows[1], "title": None}]  # NOT NULL fails on the second insert
        with pytest.raises(Exception):
            rec.replace_schedule(rec.show_id, broken)
        assert rec.schedule_items() == before
        # ids from another show are never taken over
        other = rec.show_id
        rec.start_show("Day 2")
        rec.replace_schedule(rec.show_id, [{**rows[0], "id": before[0]["id"]}])
        assert rec.schedule_items()[0]["id"] != before[0]["id"]
        assert len(rec.schedule_items(other)) == 4
    finally:
        rec.close()


# =================================================================== body-size overrides
@pytest.mark.parametrize("path,limit", sorted(DEFAULT_BODY_OVERRIDES.items()))
def test_body_override_at_the_limit_plus_and_minus_one(client, path, limit):
    method = client.put if path == "/api/admin/schedule" else client.post
    hdr = {"content-type": "application/json"}
    for size, too_big in ((limit - 1, False), (limit, False), (limit + 1, True)):
        r = method(path, content=b"[" + b" " * (size - 2) + b"]", headers=hdr)
        assert (r.status_code == 413) is too_big, (size, r.status_code)


def test_default_limit_still_applies_elsewhere_and_overrides_merge(tmp_path):
    hub = Hub(tmp_path)
    hdr = {"content-type": "application/json"}
    with TestClient(create_app(hub, body_limit_overrides={"/api/admin/schedule": 1000})) as c:
        assert c.post("/api/admin/schedule/demo", content=b" " * (64 * 1024 + 1), headers=hdr).status_code == 413
        assert c.post("/api/admin/schedule/demo", content=b" " * (64 * 1024), headers=hdr).status_code != 413
        assert c.put("/api/admin/schedule", content=b" " * 1001, headers=hdr).status_code == 413  # caller wins
        assert c.put("/api/admin/schedule", content=b" " * 1000, headers=hdr).status_code != 413
        assert c.post("/api/admin/schedule/import", content=b" " * (256 * 1024), headers=hdr).status_code != 413


def _raw_put(client, items):
    body = json.dumps({"show_id": client.hub.recorder.show_id, "revision": client.hub.schedule.revision(),
                       "items": items}, ensure_ascii=False).encode()
    assert len(body) <= DEFAULT_BODY_OVERRIDES["/api/admin/schedule"], len(body)
    return client.put("/api/admin/schedule", content=body, headers={"content-type": "application/json"})


def test_a_full_schedule_fits_under_its_body_limit(client):
    """300 items with 256 KB of setlists (the caps) must not hit the body limit."""
    _admin(client)
    per = sched.SETLIST_TOTAL_MAX_BYTES // 300
    items = [{"kind": "act", "title": "T" * 120, "start": "19:00", "end": "20:00", "stage": "S" * 40,
              "setlist": "é" * (per // 2), "id": None} for _ in range(300)]
    r = _raw_put(client, items)
    assert r.status_code == 200 and len(r.json()["items"]) == 300


def test_worst_case_json_escaping_gets_422_not_413(client):
    """Setlists of quote marks double in size when JSON-escaped. Just over the 256 KB setlist cap,
    with the longest titles and stages (also all quotes), the body still fits: the admin is told
    which cap was hit (422) instead of a bare "too large" (413)."""
    _admin(client)
    total = sched.SETLIST_TOTAL_MAX_BYTES + 1
    sizes = [min(sched.SETLIST_MAX_BYTES, total - k * sched.SETLIST_MAX_BYTES) for k in range(33)]
    sizes = [s for s in sizes if s > 0]
    items = [{"kind": "act", "title": '"' * 120, "start": "19:00", "stage": '"' * 40,
              "setlist": '"' * (sizes[n] if n < len(sizes) else 1)} for n in range(300)]
    r = _raw_put(client, items)
    assert r.status_code == 422 and sched.MSG_SETLIST_TOTAL in r.text
