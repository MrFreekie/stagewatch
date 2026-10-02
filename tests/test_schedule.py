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
PUBLIC_ITEM_KEYS = {"id", "stage", "kind", "title", "start", "end", "planned_start", "planned_end", "setlist"}


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
        (9, sched.MSG_STAGE_LEN), (10, sched.MSG_LINE_HIDDEN), (11, sched.MSG_LINE_HIDDEN)]
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


def _put(c, items, show_id=None, **kw):
    return c.put("/api/admin/schedule", json={"show_id": show_id or c.hub.recorder.show_id, "items": items}, **kw)


# =================================================================== API
def test_put_schedule_resolves_times_and_round_trips(client):
    _admin(client)
    _london_day(client)
    r = _put(client, BASIC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"show_id", "day", "items"} and body["day"] == DAY
    items = body["items"]
    assert [i["title"] for i in items] == ["Doors", "Support", "Acoustic tent", "Curfew"]
    assert all(set(i) == PUBLIC_ITEM_KEYS for i in items)
    cur = items[-1]
    assert (cur["start"], cur["end"], cur["planned_start"]) == ("00:30", "", utc(2026, 10, 2, 23, 30))
    assert items[1]["planned_end"] == utc(2026, 10, 2, 18, 45) and items[1]["setlist"] == "1. One\n2. **Two**"
    # sending the list back with ids keeps the ids
    ids = [i["id"] for i in items]
    resend = [{k: i[k] for k in ("id", "stage", "kind", "title", "start", "end", "setlist")} for i in items]
    resend[0]["title"] = "Doors open"
    again = _put(client, resend).json()["items"]
    assert [i["id"] for i in again] == ids and again[0]["title"] == "Doors open"
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
    assert client.post("/api/admin/schedule/import",
                       json={"text": "19:00 Doors", "dry_run": False, "show_id": old}).status_code == 409
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
    ([{"kind": "act", "title": "Band", "start": "19:00"}] * 301, "items"),
    ([{"kind": "act", "title": f"Band {n}", "start": "19:00", "setlist": "s" * 8000} for n in range(33)], "items"),
    ([{"id": 7, "kind": "act", "title": "A", "start": "19:00"}, {"id": 7, "kind": "act", "title": "B", "start": "20:00"}],
     "items"),
])
def test_caps_and_bad_items_get_422_without_echo(client, items, where):
    _admin(client)
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
    # committing with a bad line adds nothing
    sid = client.hub.recorder.show_id
    r = client.post("/api/admin/schedule/import", json={"text": text, "dry_run": False, "show_id": sid})
    assert r.status_code == 422 and "Nothing has been added" in r.json()["detail"] and SECRET not in r.text
    assert client.hub.schedule.items() == []
    # commit needs a show id
    assert client.post("/api/admin/schedule/import", json={"text": "19:00 Doors", "dry_run": False}).status_code == 422
    # a clean import is added after what is there
    assert _put(client, [{"kind": "act", "title": "Soundcheck", "start": "16:00"}]).status_code == 200
    r = client.post("/api/admin/schedule/import",
                    json={"text": "start,end,title\n19:00,,Doors\n19:30,20:15,Support", "dry_run": False,
                          "show_id": sid})
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()["schedule"]["items"]] == ["Soundcheck", "Doors", "Support"]


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
    r = client.post("/api/admin/schedule/import", json={"text": "19:00 A\n19:00 B", "dry_run": False,
                                                         "show_id": client.hub.recorder.show_id})
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
    assert set(body) == {"show_id", "day", "now", "stage", "items", "now_next"}
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
    assert set(snap) == {"show_id", "day", "items"} and all(set(i) == PUBLIC_ITEM_KEYS for i in snap["items"])


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
        assert [i["kind"] for i in snap["schedule"]["items"]] == ["doors", "act", "changeover", "act", "curfew"]
        nn = c.get("/api/schedule").json()["now_next"]
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
            assert snap["type"] == "snapshot" and snap["schedule"]["items"] == []
            assert set(snap["schedule"]) == {"show_id", "day", "items"}
            assert _put(c, BASIC).status_code == 200
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "schedule":
                    break
            else:
                raise AssertionError("no schedule message")
            assert set(msg) == {"type", "show_id", "day", "items"}
            assert msg["show_id"] == hub.recorder.show_id
            assert [i["title"] for i in msg["items"]] == ["Doors", "Support", "Acoustic tent", "Curfew"]
            assert all(set(i) == PUBLIC_ITEM_KEYS for i in msg["items"])


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


def test_a_full_schedule_fits_under_its_body_limit(client):
    """300 items with 256 KB of setlists (the caps) must not hit the 512 KiB body limit."""
    _admin(client)
    per = sched.SETLIST_TOTAL_MAX_BYTES // 300
    items = [{"kind": "act", "title": "T" * 120, "start": "19:00", "end": "20:00", "stage": "S" * 40,
              "setlist": "é" * (per // 2), "id": None} for _ in range(300)]
    body = json.dumps({"show_id": client.hub.recorder.show_id, "items": items}, ensure_ascii=False).encode()
    assert len(body) <= DEFAULT_BODY_OVERRIDES["/api/admin/schedule"]
    r = client.put("/api/admin/schedule", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 200 and len(r.json()["items"]) == 300
