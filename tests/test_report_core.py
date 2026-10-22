"""End-of-day show report, phase A: the report core (query, build, redact, svg, html, csv_zip).

Expected values are worked out by hand in the comments.  The fixture database is made with the
real Recorder (so the schema is the real one) and filled with plain SQL.
"""

import csv
import io
import re
import sqlite3
import zipfile
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stagewatch.core import recorder as rec_mod
from stagewatch.core.recorder import Recorder
from stagewatch.core.report import (ReportError, ReportScope, build_report, make_zip, open_readonly, render_html)
from stagewatch.core.report import csv_zip, redact
from stagewatch.core.report.query import counter_delta, resolve_window, summarise

LON = ZoneInfo("Europe/London")


def ts(y, mo, d, h, mi=0, s=0, tz=LON):
    return datetime(y, mo, d, h, mi, s, tzinfo=tz).timestamp()


T0 = ts(2026, 10, 3, 19, 0)          # 19:00 BST on the show day (BST: UTC+1)
CFG = SimpleNamespace(
    site=SimpleNamespace(name="Test Site", timezone="Europe/London", day_rollover="06:00", reference_distance_m=30.0),
    dashboards=[SimpleNamespace(slug="foh", title="FOH desk")])


def make_db(path, fill):
    rec = Recorder(path)
    rec.close()
    db = sqlite3.connect(str(path))
    fill(db)
    db.commit()
    db.close()
    return path


def meta(db, eid, device, kind, unit, name, dev_name, area="", doc="{}"):
    db.execute("INSERT INTO entity_meta (entity_id, device_id, kind, unit, name, device_name, area, updated, doc) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (eid, device, kind, unit, name, dev_name, area, T0, doc))


def states(db, show, eid, pairs):
    db.executemany("INSERT INTO states (show_id, ts, entity_id, value) VALUES (?,?,?,?)",
                   [(show, T0 + t, eid, v) for t, v in pairs])


@pytest.fixture
def dbfile(tmp_path):
    def fill(db):
        db.execute("DELETE FROM shows")
        db.execute("DELETE FROM events")
        db.execute("INSERT INTO events (id, name, created) VALUES (1, 'Test Festival', ?)", (T0,))
        db.execute("INSERT INTO shows (id, name, started, ended, event_id, day) VALUES (1, 'Day 1', ?, ?, 1, '2026-10-03')",
                   (T0, T0 + 4 * 3600))
        db.execute("INSERT INTO shows (id, name, started, ended, event_id, day) VALUES (2, 'Day 2', ?, ?, 1, '2026-10-04')",
                   (T0 + 86400, T0 + 86400 + 6.5 * 3600))
        # Runs: 19:00-20:00 stopped for an update; 20:10 to 22:00:30 never stopped cleanly.
        db.execute("INSERT INTO hub_runs (started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?)",
                   (T0, T0 + 3600, T0 + 3600, "update", "0.4.0", '{"tz":"Europe/London"}'))
        db.execute("INSERT INTO hub_runs (started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?)",
                   (T0 + 3600 + 600, T0 + 3 * 3600, None, "", "0.4.0", '{"tz":"Europe/London"}'))
        meta(db, "esp1.temp", "esp1", "temperature", "°C", "Temperature", "Node A", "FOH")
        meta(db, "meter.energy", "meter1", "energy", "Wh", "Energy", "Distro 1")
        meta(db, "meter.current", "meter1", "current", "A", "Current L1", "Distro 1")
        # 19:00-19:02: 10.0 at 0 s, 20.0 at 10 s.  Hold is 120 s.
        # Minute 1 = (10*10 + 20*50)/60 = 18.3333; minute 2 = 20.  Mean of minutes = 19.16667.
        states(db, 1, "esp1.temp", [(0, 10.0), (10, 20.0)])
        # Gap case, 19:00-19:06: 10 at 0 s and 30 at 300 s, hold 120 s -> covered 0-120 and 300-360.
        states(db, 1, "meter.current", [(0, 5.0), (30, 9.5), (60, 7.0)])
        # Counter: 100, 110, 120, reset to 5, 15 -> 10 + 10 + (reset step not counted) + 10 = 30 Wh.
        states(db, 1, "meter.energy", [(0, 100.0), (60, 110.0), (120, 120.0), (180, 5.0), (240, 15.0)])
        states(db, 1, "site.temperature", [(0, 20.0), (60, 21.0)])
        states(db, 1, "site.speed_of_sound", [(0, 343.0), (60, 344.0)])
        states(db, 1, "esp1.temp", [(3 * 3600 + 30, 22.0)])    # last row of the unclean run
        for a, b, c, d in ((T0 - 5000, "SENTINEL_BEFORE raised", "raise", 2), (T0 - 4900, "SENTINEL_BEFORE raised", "clear", 2)):
            db.execute("INSERT INTO alarm_log (show_id, ts, alarm_id, event, level, message) VALUES (1,?,?,?,?,?)",
                       (a, "sentinel", c, d, b))
        al = [(-1000, "old", "raise", 2, "Old alarm"), (-900, "old", "ack (admin)", 2, "Old alarm"),
              (100, "old", "clear", 2, "Old alarm"),
              (30, "temp.high", "raise", 2, "Temperature high"), (70, "temp.high", "ack (dashboard:foh)", 2, "Temperature high"),
              (300, "temp.high", "clear", 2, "Temperature high"),
              (120, "device:esp1", "raise", 1, 'Node A: missing (10.20.30.40 timed out, aa:bb:cc:dd:ee:ff)'),
              (200, "device:esp1", "clear", 1, "Node A: missing"),
              (50, "software", "updated", 0, "0.3.0 -> 0.4.0")]
        for t, aid, ev, lv, msg in al:
            db.execute("INSERT INTO alarm_log (show_id, ts, alarm_id, event, level, message) VALUES (1,?,?,?,?,?)",
                       (T0 + t, aid, ev, lv, msg))
        for t, label, src in ((-4000, "SENTINEL_MARKER_BEFORE", "admin"), (60, "Line check", "dashboard:foh"),
                              (200, "<script>alert(1)</script> 10.0.0.5 AA:BB:CC:DD:EE:FF fe80::1", "admin"),
                              (250, "=HYPERLINK(\"http://x\")", "admin"), (260, "-2+3", "admin"), (270, "@SUM(A1)", "admin"),
                              (3 * 3600 + 3000, "SENTINEL_MARKER_AFTER", "admin")):
            db.execute("INSERT INTO markers (show_id, ts, label, source) VALUES (1,?,?,?)", (T0 + t, label, src))
        db.execute("INSERT INTO schedule_items (show_id, kind, title, planned_start, planned_end, updated, setlist) "
                   "VALUES (1,'doors','Doors',?,NULL,?, 'SECRET SETLIST')", (T0 + 1800, T0))
        db.execute("INSERT INTO schedule_items (show_id, kind, title, planned_start, planned_end, updated) "
                   "VALUES (1,'curfew','Curfew',?,NULL,?)", (T0 + 3 * 3600, T0))
    return make_db(tmp_path / "stagewatch.sqlite3", fill)


def report(dbfile, **kw):
    conn = open_readonly(dbfile)
    try:
        scope = ReportScope(show_id=kw.pop("show_id", 1), **kw)
        return build_report(conn, CFG, scope, T0 + 5 * 3600, "a copy of a data folder")
    finally:
        conn.close()


CUSTOM = dict(preset="custom", from_hhmm="19:00", to_hhmm="19:06")


# ------------------------------------------------------------------ maths
def test_summarise_time_weighted_mean_uneven_rates():
    st = summarise([(0.0, 10.0), (10.0, 20.0)], 0.0, 120.0)
    assert st.mean == pytest.approx((18.33333333 + 20.0) / 2)
    assert (st.min, st.min_ts, st.max, st.max_ts) == (10.0, 0.0, 20.0, 10.0)
    assert st.coverage == pytest.approx(1.0)


def test_summarise_gap_is_not_filled():
    # 10 at 0 s, 30 at 300 s over 0-360 s: covered 0-120 and 300-360, gap 120-300.
    st = summarise([(0.0, 10.0), (300.0, 30.0)], 0.0, 360.0)
    assert st.coverage == pytest.approx(180 / 360)
    assert st.gaps == [(120.0, 300.0)]
    assert st.mean == pytest.approx((10 + 10 + 30) / 3)      # minutes 1, 2 and 6 only


def test_summarise_none_value_is_a_gap():
    st = summarise([(0.0, 10.0), (60.0, None), (120.0, 10.0)], 0.0, 180.0)
    assert (60.0, 120.0) in st.gaps
    assert st.covered_s == pytest.approx(60 + 60)            # 0-60 and 120-180


def test_counter_delta_with_reset():
    rows = [(0.0, 100.0), (60.0, 110.0), (120.0, 120.0), (180.0, 5.0), (240.0, 15.0)]
    res = counter_delta(rows, 0.0, 300.0)
    assert res.delta == 30.0 and res.resets == 1 and res.gap_s == 0.0


def test_counter_delta_flags_a_gap():
    res = counter_delta([(0.0, 0.0), (600.0, 50.0)], 0.0, 700.0)
    assert res.delta == 50.0 and res.gap_s == 600.0


# ------------------------------------------------------------------ build
def test_environment_stats_and_power_energy(dbfile):
    d = report(dbfile, **dict(preset="custom", from_hhmm="19:00", to_hhmm="19:02"))
    row = d.sections["env"]["kinds"][0]["sensors"][0]
    assert row["label"].startswith("Node A") and row["area"] == "FOH"
    assert row["mean"] == pytest.approx(19.1666667, abs=1e-5) and row["min"] == 10.0 and row["max"] == 20.0
    d = report(dbfile, **dict(preset="custom", from_hhmm="19:00", to_hhmm="19:05"))
    en = d.sections["power"]["energy"]
    assert en[0]["kwh"] == pytest.approx(0.03) and en[0]["resets"] == 1 and not en[0]["estimated"]
    assert d.sections["power"]["total_kwh"] == pytest.approx(0.03)
    cur = d.sections["power"]["current"][0]
    assert cur["max"] == 9.5 and cur["max_ts"] == T0 + 30
    assert "highest recorded sample" in d.sections["power"]["peak_note"]


def test_sensor_gap_listed_with_coverage(dbfile):
    d = report(dbfile, **CUSTOM)
    cur = d.sections["power"]["current"][0]                    # 0, 30, 60 s then nothing to 360 s
    assert cur["coverage"] == pytest.approx((60 + 120) / 360)   # last reading holds 120 s
    gaps = d.sections["gaps"]["sensor"]
    assert any(g["sensor"].startswith("Distro 1") and g["length_s"] == pytest.approx(180) for g in gaps)


def test_full_day_gaps_and_partial_label(dbfile):
    d = report(dbfile)
    down = d.sections["gaps"]["down"]
    assert [round(x["length_s"]) for x in down] == [600, 600]   # run 2 ends at its last row, the 22:50 marker
    assert down[0]["kind"] == "update" and down[1]["kind"] == "unclean"
    assert "2 seconds" in down[1]["note"]
    assert d.header["badge"] == "Full day"
    assert d.header["completeness"].startswith("Partial: recording stopped unexpectedly")
    assert d.sections["summary"]["coverage"] == pytest.approx(1 - 1200 / (4 * 3600))


def test_alarms_pairing_and_range_start_rule(dbfile):
    d = report(dbfile, **CUSTOM)
    rows = {r["id"]: r for r in d.sections["alarms"]["rows"]}
    old = rows["old"]
    assert old["already_active"] and old["raised"] is None and old["acked_before"] and old["ack_ts"] is None
    assert old["duration_s"] == pytest.approx(100.0)          # clipped to the range start
    hot = rows["temp.high"]
    assert hot["time_to_ack_s"] == pytest.approx(40.0) and hot["ack_from"] == "Dashboard: FOH desk"
    assert hot["duration_s"] == pytest.approx(270.0) and hot["cleared"] == T0 + 300
    assert "sentinel" not in rows
    dev = d.sections["devices"]["rows"][0]
    assert dev["device"] == "Node A" and dev["status"] == "missing" and dev["duration_s"] == pytest.approx(80.0)
    assert d.sections["summary"]["never_acked"] == 0


def test_markers_delta_travel(dbfile):
    d = report(dbfile, **CUSTOM)
    rows = d.sections["markers"]["rows"]
    assert [r["label"] for r in rows][0] == "Line check"
    assert rows[0]["delta_first_ms"] is None                  # the first marker is the reference
    # Line check at 60 s -> c = 344 (reading at 60 s); the next marker at 200 s still sees 344.
    assert rows[1]["delta_first_ms"] == pytest.approx(0.0)


def test_show_time_and_marker_presets(dbfile):
    conn = open_readonly(dbfile)
    site = CFG.site
    w = resolve_window(conn, site, ReportScope(1, preset="show_time"), T0 + 5 * 3600)
    assert (w.start, w.end) == (T0 + 1800, T0 + 3 * 3600) and "doors" in w.label
    ids = [r[0] for r in conn.execute("SELECT id FROM markers WHERE show_id = 1 ORDER BY ts")]
    w = resolve_window(conn, site, ReportScope(1, preset="markers", from_marker=ids[1], to_marker=ids[2]), T0)
    assert (w.start, w.end) == (T0 + 60, T0 + 200)
    with pytest.raises(ReportError):
        resolve_window(conn, site, ReportScope(2, preset="show_time"), T0)
    conn.close()


def test_cross_midnight_and_order_errors(dbfile):
    conn = open_readonly(dbfile)
    site = CFG.site
    w = resolve_window(conn, site, ReportScope(2, preset="custom", from_hhmm="19:30", to_hhmm="00:45"), T0 + 86400)
    assert w.end - w.start == pytest.approx(5.25 * 3600) and not w.full_day
    with pytest.raises(ReportError) as ei:
        resolve_window(conn, site, ReportScope(2, preset="custom", from_hhmm="23:00", to_hhmm="22:00"), T0)
    assert ei.value.code == "end_before_start" and "06:00" in ei.value.message
    with pytest.raises(ReportError):
        resolve_window(conn, site, ReportScope(99), T0)
    conn.close()


def test_read_only_connection_cannot_write(dbfile):
    conn = open_readonly(dbfile)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM markers")
    conn.close()


def test_v1_database_falls_back(tmp_path):
    p = tmp_path / "v1.sqlite3"
    db = sqlite3.connect(str(p))
    db.executescript(rec_mod._SCHEMA)
    db.execute("INSERT INTO shows (id, name, started, ended) VALUES (1, 'Old', ?, ?)", (T0, T0 + 3600))
    db.executemany("INSERT INTO states (show_id, ts, entity_id, value) VALUES (1,?,?,?)",
                   [(T0 + t, "site.temperature", 20.0) for t in (0, 60, 120, 1800, 1860)])
    db.commit()
    db.close()
    conn = open_readonly(p)
    d = build_report(conn, CFG, ReportScope(1), T0 + 7200)
    conn.close()
    assert d.sections["gaps"]["fallback"] and d.sections["gaps"]["down"]
    assert "No readings at all" in d.sections["gaps"]["down"][0]["how_ended"]
    assert "env" in d.sections and "error" not in d.sections["env"]
    assert "<h2>Data gaps</h2>" in render_html(d)


def test_open_show_live_label(tmp_path):
    def fill(db):
        db.execute("DELETE FROM shows")
        db.execute("INSERT INTO shows (id, name, started) VALUES (1, 'Now', ?)", (T0,))
    p = make_db(tmp_path / "s.sqlite3", fill)
    conn = open_readonly(p)
    d = build_report(conn, CFG, ReportScope(1), T0 + 3600, live=True)
    conn.close()
    assert d.header["completeness"] == "Partial: show still running, up to 20:00"


# ------------------------------------------------------------------ DST
def test_dst_fall_back_day(tmp_path):
    start = ts(2026, 10, 24, 20, 0)            # 19:00 UTC
    end = ts(2026, 10, 25, 4, 0)               # 04:00 UTC (GMT)

    def fill(db):
        db.execute("DELETE FROM shows")
        db.execute("INSERT INTO shows (id, name, started, ended, day) VALUES (1, 'Clocks back', ?, ?, '2026-10-24')", (start, end))
        db.executemany("INSERT INTO states (show_id, ts, entity_id, value) VALUES (1,?,?,?)",
                       [(start + i * 60, "site.temperature", 10.0 + i / 600) for i in range(0, 540, 1)])
    p = make_db(tmp_path / "d.sqlite3", fill)
    conn = open_readonly(p)
    d = build_report(conn, CFG, ReportScope(1, preset="custom", from_hhmm="23:00", to_hhmm="03:00"), end + 3600)
    conn.close()
    assert d.window.end - d.window.start == pytest.approx(5 * 3600)      # elapsed time, not wall clock
    assert d.header["dst"] and any("clocks changed" in n.lower() for n in d.header["notes"])
    page = render_html(d)
    assert "BST" in page and "GMT" in page


# ------------------------------------------------------------------ redaction, escaping, sharing, CSV
def test_scrub_removes_addresses_but_keeps_times():
    s = redact.scrub("Node at 192.168.1.20:6053 mac AA:BB:CC:DD:EE:FF ipv6 fe80::1ff:fe23:4567:890a host stage-box.local "
                     "serial ABC12345 cafe 19:30:00 and 2001:db8::1")
    for bad in ("192.168", "AA:BB", "fe80", "stage-box", "ABC12345", "2001:db8"):
        assert bad not in s
    assert "19:30:00" in s and "[address]" in s


def test_no_address_leaks_anywhere(dbfile):
    for share in (False, True):
        d = report(dbfile, share=share, sections=None)
        page = render_html(d)
        blobs = [page] + [b.decode("utf-8-sig") for b in csv_zip.csv_files(d).values()]
        with zipfile.ZipFile(io.BytesIO(make_zip(d))) as z:
            blobs += [z.read(n).decode("utf-8-sig") for n in z.namelist()]
        for text in blobs:
            for bad in ("10.20.30.40", "10.0.0.5", "aa:bb:cc", "AA:BB:CC", "fe80::1"):
                assert bad not in text


def test_html_escapes_hostile_names(tmp_path):
    def fill(db):
        db.execute("DELETE FROM shows")
        db.execute("INSERT INTO shows (id, name, started, ended, day) VALUES (1, '<b>Show</b>', ?, ?, '2026-10-03')", (T0, T0 + 600))
        meta(db, "x.temp", "x", "temperature", "°C", "T", '"><svg onload=alert(1)>', "<i>Area</i>")
        states(db, 1, "x.temp", [(0, 20.0), (60, 21.0)])
        db.execute("INSERT INTO markers (show_id, ts, label, source) VALUES (1,?,?,?)", (T0 + 10, "<script>alert(1)</script>", "admin"))
        db.execute("INSERT INTO alarm_log (show_id, ts, alarm_id, event, level, message) VALUES (1,?,?,?,?,?)",
                   (T0 + 5, "a", "raise", 2, "<img src=x onerror=alert(1)>"))
    p = make_db(tmp_path / "h.sqlite3", fill)
    conn = open_readonly(p)
    d = build_report(conn, CFG, ReportScope(1), T0 + 3600)
    conn.close()
    page = render_html(d)
    assert "<script" not in page.lower()
    assert '"><svg' not in page and "<img src=x" not in page and "<b>Show" not in page and "<i>Area" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "http" not in page.replace("http://www.w3.org/2000/svg", "")


def test_sharing_excerpt_defaults_and_no_leak(dbfile):
    d = report(dbfile, share=True, **CUSTOM)
    assert "system" not in d.sections and "schedule" not in d.sections and "gaps" in d.sections
    m = d.sections["markers"]
    assert m["minimal"] and all(r["source"] == "" and r["temp_c"] is None for r in m["rows"])
    assert all(r["ack_from"] == "" for r in d.sections["alarms"]["rows"])
    assert all(r["detail"] == "" for r in d.sections["devices"]["rows"])
    assert d.header["badge"] == "Excerpt"
    page = render_html(d)
    assert "Acknowledged from" not in page and "FOH desk" not in page
    # Nothing recorded outside the range appears in the page or the zip.
    with zipfile.ZipFile(io.BytesIO(make_zip(d))) as z:
        blobs = [page] + [z.read(n).decode("utf-8-sig") for n in z.namelist()]
    for text in blobs:
        for sentinel in ("SENTINEL_BEFORE", "SENTINEL_MARKER_BEFORE", "SENTINEL_MARKER_AFTER", "SECRET SETLIST"):
            assert sentinel not in text
    # The show's own start and end are not in an excerpt header.
    assert "19:00 to 19:06" in page


def test_full_report_never_has_setlist_and_header_lists_omissions(dbfile):
    d = report(dbfile, sections={"summary", "alarms"})
    page = render_html(d)
    assert "SECRET SETLIST" not in page
    assert "Data gaps" in page and "This report leaves out:" in page and "markers" in page.lower()


def test_csv_injection_neutralised_and_format(dbfile):
    d = report(dbfile, **CUSTOM)
    files = csv_zip.csv_files(d)
    raw = files["markers.csv"]
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    labels = [r[2] for r in rows[1:]]
    assert "'=HYPERLINK(\"http://x\")" in labels and "'-2+3" in labels and "'@SUM(A1)" in labels
    assert not any(c.startswith(("=", "+", "@")) for f in files.values() for r in csv.reader(io.StringIO(f.decode("utf-8-sig"))) for c in r)
    assert re.match(r"\d\d/\d\d/\d{4} \d\d:\d\d:\d\d", rows[1][1])
    assert csv_zip.cell(-3.5) == -3.5 and csv_zip.cell("\tx") == "'\tx"


def test_zip_contains_html_and_csvs_and_cap(dbfile):
    d = report(dbfile)
    with zipfile.ZipFile(io.BytesIO(make_zip(d))) as z:
        names = set(z.namelist())
    assert {"report.html", "summary.csv", "env_stats.csv", "readings_1min.csv", "alarms.csv", "outages.csv",
            "markers.csv", "gaps.csv"} <= names
    with pytest.raises(ReportError):
        make_zip(d, max_bytes=10)


def test_charts_are_self_contained_svg(dbfile):
    d = report(dbfile)
    page = render_html(d)
    assert "<svg" in page and "Stagewatch not recording" in page
    assert re.search(r"<script|href=|src=|url\((?!#)", page) is None


def test_section_failure_is_contained(dbfile, monkeypatch):
    from stagewatch.core.report import build
    monkeypatch.setattr(build, "_schedule", lambda ctx: 1 / 0)
    d = report(dbfile, sections={"summary", "schedule", "alarms"})
    assert d.sections["schedule"] == {"error": "ZeroDivisionError"}
    assert "could not be made" in render_html(d) and d.sections["alarms"]["rows"] is not None


def test_unknown_section_rejected(dbfile):
    with pytest.raises(ReportError):
        report(dbfile, sections={"nope"})


def test_report_core_does_not_import_the_hub():
    import subprocess
    import sys
    code = "import sys, stagewatch.core.report; sys.exit(1 if 'stagewatch.core.hub' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


# ------------------------------------------------------------------ limits (security review M1)
def test_summarise_reads_a_cursor_once_and_matches_the_list_result():
    rows = [(0.0, 10.0), (10.0, 20.0), (130.0, 5.0)]
    a = summarise(rows, 0.0, 300.0)
    b = summarise(iter(rows), 0.0, 300.0)
    assert (a.n, a.min, a.max, a.mean, a.interval_s, a.gaps) == (b.n, b.min, b.max, b.mean, b.interval_s, b.gaps)
    assert a.interval_s == 65.0          # median of 10 and 120


def test_row_cap_refuses_with_too_big(dbfile, monkeypatch):
    from stagewatch.core.report import query
    monkeypatch.setattr(query, "MAX_ROWS", 3)
    conn = open_readonly(dbfile)
    try:
        with pytest.raises(ReportError) as ei:
            build_report(conn, CFG, ReportScope(1), T0 + 5 * 3600)
    finally:
        conn.close()
    assert ei.value.code == "too_big"


def test_deadline_is_checked_in_the_python_loop(dbfile):
    conn = open_readonly(dbfile, None)
    try:
        with pytest.raises(ReportError) as ei:
            build_report(conn, CFG, ReportScope(1), T0 + 5 * 3600, deadline_s=-1)
    finally:
        conn.close()
    assert ei.value.code == "deadline"


def test_whole_day_longer_than_48_hours_is_refused(dbfile):
    db = sqlite3.connect(str(dbfile))
    db.execute("UPDATE shows SET ended = ? WHERE id = 1", (T0 + 49 * 3600,))
    db.commit()
    db.close()
    conn = open_readonly(dbfile)
    try:
        with pytest.raises(ReportError) as ei:
            build_report(conn, CFG, ReportScope(1), T0 + 60 * 3600)
        assert ei.value.code == "too_long"
        # a range inside it is still fine
        assert build_report(conn, CFG, ReportScope(1, **CUSTOM), T0 + 60 * 3600).window.preset == "custom"
    finally:
        conn.close()


def test_zip_size_is_checked_while_building(dbfile):
    data = report(dbfile)
    with pytest.raises(ReportError) as ei:
        make_zip(data, max_bytes=100)
    assert ei.value.code == "too_big"


# ------------------------------------------------------------------ run_spans stays local (security review M2)
def test_run_spans_only_searches_runs_that_overlap_the_range(tmp_path):
    from stagewatch.core.report.query import run_spans

    def fill(db):
        db.execute("INSERT INTO hub_runs (started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?)",
                   (T0 - 10 * 86400, T0 - 10 * 86400 + 100, None, "", "1", "{}"))
        db.execute("INSERT INTO hub_runs (started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?)",
                   (T0, T0 + 100, None, "", "1", "{}"))
        meta(db, "a.temp", "a", "temperature", "C", "Temperature", "A")
        # a reading well after the old run's last heartbeat, inside the old run's own window
        states(db, 1, "a.temp", [(-10 * 86400 + 5000, 1.0), (500, 2.0)])
        states(db, 1, "z.other", [(700, 3.0)])
    path = make_db(tmp_path / "stagewatch.sqlite3", fill)
    conn = open_readonly(path)
    try:
        runs = run_spans(conn, 1, T0 + 9999, False, T0, T0 + 3600)
        assert runs[0].end == T0 - 10 * 86400 + 100            # the old run was not searched
        assert runs[1].end == T0 + 700                         # the overlapping one ends at its last row, any entity
        runs_all = run_spans(conn, 1, T0 + 9999)
        assert runs_all[0].end == T0 - 10 * 86400 + 5000
    finally:
        conn.close()
