"""Show report, phase C: power extras (percent of rating, earth leakage trend, generator hint), the
sound level limit step chart, and the list of past crash days.

Every expected number is worked out by hand in the comments.  Fixtures are synthetic (placeholder names,
127.0.0.1); nothing here is real site data.
"""

import csv
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stagewatch.core.config import ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.recorder import Recorder
from stagewatch.core.report import ReportScope, build_report, csv_zip, open_readonly, render_html, svg
from stagewatch.core.report import build as report_build
from stagewatch.core.report.query import crash_days

LON = ZoneInfo("Europe/London")
SITE = SimpleNamespace(name="Test Site", timezone="Europe/London", day_rollover="06:00", reference_distance_m=30.0)


def ts(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=LON).timestamp()


T0 = ts(2026, 10, 3, 19, 0)          # 19:00 BST on the show day
POWER_SITE = SimpleNamespace(gen_loading_pct=80.0, gen_power_factor=0.8, gen_size_kva=15.0, demand_window_min=15)


def cfg(devices=(), power_site=POWER_SITE):
    return SimpleNamespace(site=SITE, dashboards=[], shelly_devices=list(devices), power_site=power_site)


def meter(dev_id, keys, channels=(), rating=None, breaker=None, three=()):
    return ShellyDeviceConfig(id=dev_id, host="127.0.0.1", mac="aabbccddeeff", name=dev_id, circuit_keys=list(keys),
                              three_phase_keys=list(three), channels=list(channels), rating_a=rating, breaker_a=breaker)


def mkdb(tmp_path, fill=None):
    path = tmp_path / "stagewatch.sqlite3"
    Recorder(path).close()
    db = sqlite3.connect(str(path))
    db.execute("DELETE FROM shows")
    db.execute("DELETE FROM events")
    db.execute("INSERT INTO events (id, name, created) VALUES (1, 'Test Festival', ?)", (T0,))
    db.execute("INSERT INTO shows (id, name, started, ended, event_id, day) VALUES (1, 'Day 1', ?, ?, 1, '2026-10-03')",
               (T0, T0 + 4 * 3600))
    db.execute("INSERT INTO hub_runs (started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?)",
               (T0, T0 + 4 * 3600, T0 + 4 * 3600, "stop", "0.7.0", '{"tz":"Europe/London"}'))
    if fill:
        fill(db)
    db.commit()
    db.close()
    return path


def meta(db, eid, device, kind, unit, name, dev_name, labels=None):
    doc = json.dumps({"decimals": 2, "category": "meter", **({"labels": labels} if labels else {})})
    db.execute("INSERT INTO entity_meta (entity_id, device_id, kind, unit, name, device_name, area, updated, doc) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (eid, device, kind, unit, name, dev_name, "", T0, doc))


def states(db, eid, pairs):
    db.executemany("INSERT INTO states (show_id, ts, entity_id, value) VALUES (1,?,?,?)", [(T0 + t, eid, v) for t, v in pairs])


def make(path, config, **scope):
    scope.setdefault("preset", "custom")
    scope.setdefault("from_hhmm", "19:00")
    scope.setdefault("to_hhmm", "19:06")
    conn = open_readonly(path)
    try:
        return build_report(conn, config, ReportScope(show_id=1, **scope), T0 + 5 * 3600, "a copy of a data folder")
    finally:
        conn.close()


def names(data):
    return {n: z for n, z in zip(csv_zip.csv_files(data), csv_zip.csv_files(data).values())}


# ------------------------------------------------------------------ percent of rating
def test_percent_of_rating_uses_the_lower_limit_and_only_the_meters_own_current(tmp_path):
    def fill(db):
        meta(db, "rack1.pm1_0_current", "rack1", "current", "A", "Current", "Rack 1", {"channel": "pm1_0", "field": "current"})
        # Samples 10.0 at 0 s, 6.5 at 60 s, 12.0 at 120 s; the last holds 120 s (to 240 s).
        # One-minute means: 10, 6.5, 12, 12 -> mean 10.125.  Limit = breaker 13 (lower than device 16).
        states(db, "rack1.pm1_0_current", [(0, 10.0), (60, 6.5), (120, 12.0)])
        # A meter with two circuits: only the circuit that has its own rating gets a percentage.
        meta(db, "dual.pm1_0_current", "dual", "current", "A", "Current A", "Dual", {"channel": "pm1_0", "field": "current"})
        meta(db, "dual.pm1_1_current", "dual", "current", "A", "Current B", "Dual", {"channel": "pm1_1", "field": "current"})
        states(db, "dual.pm1_0_current", [(0, 8.0)])      # 8 of the circuit's own 10 A = 80 %
        states(db, "dual.pm1_1_current", [(0, 5.0)])      # no own rating: the meter's 16 A must not be borrowed
        # A three-phase meter: each phase against the circuit's device limit 32 (lower than breaker 63).
        meta(db, "dist.em0_a_current", "dist", "current", "A", "Phase A current", "Dist",
             {"channel": "em0", "field": "current", "group": "a"})
        meta(db, "dist.em0_total_current", "dist", "current", "A", "Total current", "Dist",
             {"channel": "em0", "field": "current", "group": "total"})
        states(db, "dist.em0_a_current", [(0, 16.0)])      # 16 / 32 = 50 %
        states(db, "dist.em0_total_current", [(0, 40.0)])  # a total has no rating
        # Calculated and unknown-meter currents have none.
        meta(db, "power_site.line_1_current", "power_site", "current", "A", "Line 1 current (calculated)", "Site power",
             {"field": "current", "derived": "1"})
        states(db, "power_site.line_1_current", [(0, 9.0)])
        meta(db, "ghost.pm1_0_current", "ghost", "current", "A", "Current", "Ghost", {"channel": "pm1_0", "field": "current"})
        states(db, "ghost.pm1_0_current", [(0, 3.0)])
    config = cfg([meter("rack1", ["pm1_0"], [ShellyChannelConfig(key="pm1_0", rating_a=16, breaker_a=13)]),
                  meter("dual", ["pm1_0", "pm1_1"], [ShellyChannelConfig(key="pm1_0", rating_a=10)], rating=16),
                  meter("dist", ["em0"], [ShellyChannelConfig(key="em0", rating_a=32, breaker_a=63)], three=["em0"])])
    d = make(mkdb(tmp_path, fill), config)
    rows = {r["entity"]: r for r in d.sections["power"]["current"]}
    r = rows["rack1.pm1_0_current"]
    assert (r["limit_a"], r["limit_by"]) == (13.0, "breaker")
    assert r["max"] == 12.0 and r["mean"] == pytest.approx(10.125)
    assert r["max_pct"] == pytest.approx(100 * 12 / 13)            # 92.3077
    assert r["mean_pct"] == pytest.approx(100 * 10.125 / 13)       # 77.8846
    assert r["load_state"] == "high load (80% or more)"
    dual0, dual1 = rows["dual.pm1_0_current"], rows["dual.pm1_1_current"]
    assert (dual0["limit_a"], dual0["limit_by"]) == (10.0, "device") and dual0["max_pct"] == pytest.approx(80.0)
    assert dual0["load_state"] == "high load (80% or more)"
    assert dual1["limit_a"] is None and dual1["max_pct"] is None
    a, tot = rows["dist.em0_a_current"], rows["dist.em0_total_current"]
    assert (a["limit_a"], a["limit_by"]) == (32.0, "device") and a["max_pct"] == pytest.approx(50.0) and a["load_state"] == ""
    assert tot["limit_a"] is None
    assert rows["power_site.line_1_current"]["limit_a"] is None
    assert rows["ghost.pm1_0_current"]["limit_a"] is None
    html = render_html(d)
    assert "Highest as % of limit" in html and "92% - high load" in html and "no limit set" in html
    assert "as set in Stagewatch now" in d.sections["power"]["limit_note"]


def test_over_the_limit_is_worded_by_what_limits(tmp_path):
    def fill(db):
        meta(db, "rack1.pm1_0_current", "rack1", "current", "A", "Current", "Rack 1", {"channel": "pm1_0", "field": "current"})
        states(db, "rack1.pm1_0_current", [(0, 14.0)])          # 14 / 13 = 107.7 %
    d = make(mkdb(tmp_path, fill), cfg([meter("rack1", ["pm1_0"], [ShellyChannelConfig(key="pm1_0", rating_a=16, breaker_a=13)])]))
    r = d.sections["power"]["current"][0]
    assert r["max_pct"] == pytest.approx(100 * 14 / 13) and r["load_state"] == "over the breaker or fuse rating"


def test_no_rating_means_no_percentage_and_no_extra_columns(tmp_path):
    def fill(db):
        meta(db, "rack1.pm1_0_current", "rack1", "current", "A", "Current", "Rack 1", {"channel": "pm1_0", "field": "current"})
        states(db, "rack1.pm1_0_current", [(0, 14.0)])
    d = make(mkdb(tmp_path, fill), cfg())                           # no meters in the config at all
    assert d.sections["power"]["current"][0]["limit_a"] is None
    assert d.sections["power"]["limit_note"] == ""
    assert "Highest as % of limit" not in render_html(d)
    bare = SimpleNamespace(site=SITE, dashboards=[])                  # a config with none of the power fields
    assert make(mkdb(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, fill), bare).sections["power"]["current"][0]["limit_a"] is None


# ------------------------------------------------------------------ earth leakage
def leak_fill(db, eid="rcm.leak", unit="mA", scale=1.0, dev_name="Earth monitor", early=2.0, mid=3.0, late=5.0):
    meta(db, eid, "rcm", "current", unit, "Earth leakage", dev_name, {"channel": "rcm", "field": "leakage"})
    # One reading each minute for 60 minutes: minutes 0-14 early, 15-44 mid, 45-59 late.
    states(db, eid, [(60 * m, (early if m < 15 else mid if m < 45 else late) * scale) for m in range(60)])


HOUR = dict(from_hhmm="19:00", to_hhmm="20:00")


def test_leakage_trend_first_quarter_against_last_quarter(tmp_path):
    d = make(mkdb(tmp_path, leak_fill), cfg(), **HOUR)
    r = d.sections["power"]["leakage"][0]
    # Buckets at minutes 0..59 (each reading holds its whole minute).  Bucket times run 0 to 3540 s, so
    # a quarter is 885 s: early = minutes 0..14 (15 x 2.0), late = minutes 45..59 (15 x 5.0).
    t = r["trend"]
    assert (t["early"], t["late"], t["change"], t["word"]) == (pytest.approx(2.0), pytest.approx(5.0), pytest.approx(3.0), "rising")
    assert r["unit"] == "mA" and r["max"] == 5.0 and r["min"] == 2.0
    # mean of the 60 one-minute means = (15*2 + 30*3 + 15*5) / 60 = 3.25
    assert r["mean"] == pytest.approx(3.25)
    assert d.charts["leakage"]["lines"][0]["label"].startswith("Earth monitor")
    html = render_html(d)
    assert "Earth leakage" in html and "rising: 2.00 mA at the start, 5.00 mA at the end (+3.00 mA)" in html
    assert 'aria-label="Earth leakage (mA) over the report range"' in html and "one line per measuring point" in html


@pytest.mark.parametrize("late,word,change", [(2.3, "steady", 0.3), (1.2, "falling", -0.8), (2.5, "rising", 0.5)])
def test_leakage_trend_words_and_the_half_milliamp_guard(tmp_path, late, word, change):
    # early 2.0: the guard is max(0.5 mA, 10 % of 2.0 = 0.2) = 0.5 mA, and a change of exactly 0.5 counts.
    d = make(mkdb(tmp_path, lambda db: leak_fill(db, mid=2.0, late=late)), cfg(), **HOUR)
    t = d.sections["power"]["leakage"][0]["trend"]
    assert t["word"] == word and t["change"] == pytest.approx(change)


def test_leakage_in_amps_is_shown_in_milliamps(tmp_path):
    d = make(mkdb(tmp_path, lambda db: leak_fill(db, unit="A", scale=0.001)), cfg(), **HOUR)
    r = d.sections["power"]["leakage"][0]
    assert r["unit"] == "mA" and r["max"] == pytest.approx(5.0) and r["trend"]["change"] == pytest.approx(3.0)


def test_leakage_needs_thirty_minutes_and_a_gap_is_not_a_trend(tmp_path):
    def fill(db):
        meta(db, "rcm.leak", "rcm", "current", "mA", "Earth leakage", "Earth monitor", {"channel": "rcm", "field": "leakage"})
        states(db, "rcm.leak", [(60 * m, 2.0 if m < 5 else 9.0) for m in range(20)])    # only 20 minutes
    d = make(mkdb(tmp_path, fill), cfg(), **HOUR)
    r = d.sections["power"]["leakage"][0]
    assert r["trend"] is None
    assert "not enough readings to show a trend" in render_html(d)
    assert r["coverage"] == pytest.approx(21 * 60 / 3600)        # 19 minutes of one-minute holds + the last 120 s hold


def test_leakage_with_no_readings_in_range_is_left_out(tmp_path):
    d = make(mkdb(tmp_path, leak_fill), cfg(), preset="custom", from_hhmm="22:00", to_hhmm="22:30")
    assert d.sections.get("power") is None            # nothing recorded, so no power section and no zeros


# ------------------------------------------------------------------ generator hint
def gen_fill(db, pairs=((0, 8000.0), (60, 12000.0), (120, 9000.0)), lines=None):
    meta(db, report_build.GEN_DEMAND, "power_site", "apparent_power", "VA", "Site demand", "Site power (calculated)",
         {"derived": "1", "field": "demand_va"})
    states(db, report_build.GEN_DEMAND, pairs)
    for i, peak in enumerate(lines or (), start=1):
        eid = report_build.GEN_LINE_DEMAND.format(i)
        meta(db, eid, "power_site", "apparent_power", "VA", f"Line {i}", "Site power (calculated)", {"derived": "1"})
        states(db, eid, [(0, peak / 2), (60, peak)])


def test_generator_hint_from_the_highest_demand_in_range(tmp_path):
    g = make(mkdb(tmp_path, gen_fill), cfg(), to_hhmm="19:03").sections["power"]["generator"]
    # Highest demand 12 000 VA = 12 kVA at 60 s; loading limit 80 % -> smallest 12 / 0.8 = 15 kVA;
    # kW = 15 x 0.8 = 12; next standard size up from 15 is 20.  Size typed: 15 kVA -> 12 / 15 = 80 %, which is at the limit.
    assert g["peak_kva"] == pytest.approx(12.0) and g["peak_ts"] == T0 + 60 and g["basis"] == "total"
    assert g["min_kva"] == pytest.approx(15.0) and g["min_kw"] == pytest.approx(12.0) and g["next_kva"] == 20
    assert g["loaded_pct"] == pytest.approx(80.0) and g["state"] == "limit"
    assert "fuel" in g["fuel_note"].lower() and "not recorded" in g["fuel_note"]


@pytest.mark.parametrize("size,pct,state", [(12.0, 100.0, "over"), (20.0, 60.0, "ok"), (None, None, "")])
def test_generator_state_by_size(tmp_path, size, pct, state):
    ps = SimpleNamespace(gen_loading_pct=80.0, gen_power_factor=0.8, gen_size_kva=size, demand_window_min=15)
    g = make(mkdb(tmp_path, gen_fill), cfg(power_site=ps), to_hhmm="19:03").sections["power"]["generator"]
    assert g["state"] == state and (g["loaded_pct"] is None if pct is None else g["loaded_pct"] == pytest.approx(pct))
    if size is None:
        assert "no percentage is shown" in render_html(make(mkdb(tmp_path / "y" if (tmp_path / "y").mkdir() is None else tmp_path, gen_fill),
                                                           cfg(power_site=ps), to_hhmm="19:03"))


def test_generator_sized_from_three_times_the_busiest_line(tmp_path):
    g = make(mkdb(tmp_path, lambda db: gen_fill(db, lines=(5000.0, 4000.0, 4500.0))), cfg(), to_hhmm="19:03").sections["power"]["generator"]
    # 3 x 5 kVA = 15 kVA is more than the 12 kVA total -> sized from the line: 15 / 0.8 = 18.75, next size 20.
    assert g["basis"] == "line" and g["peak_kva"] == pytest.approx(15.0) and g["min_kva"] == pytest.approx(18.75)
    assert g["next_kva"] == 20


def test_generator_ignores_demand_outside_the_range_and_never_invents_a_row(tmp_path):
    def fill(db):
        gen_fill(db, pairs=((600, 30000.0), (1900, 7000.0), (1960, 6000.0)))     # 30 kVA at 19:10, outside 19:30-20:00
    g = make(mkdb(tmp_path, fill), cfg(), from_hhmm="19:30", to_hhmm="20:00").sections["power"]["generator"]
    assert g["peak_kva"] == pytest.approx(7.0)
    d = make(mkdb(tmp_path / "z" if (tmp_path / "z").mkdir() is None else tmp_path, fill), cfg(), from_hhmm="21:00", to_hhmm="21:30")
    assert d.sections.get("power") is None


def test_generator_sizes_and_sums_match_the_power_card():
    from stagewatch.integrations.shelly import calc
    assert report_build.GEN_SIZES_KVA == calc.GEN_SIZES_KVA
    for peak_va, size in ((12000.0, 15.0), (12000.0, 20.0), (15000.0, None), (90000.0, None)):
        want = calc.generator_suggestion(peak_va, 80.0, 0.8, size)
        got = report_build.GEN_SIZES_KVA
        min_kva = peak_va / 1000 / 0.8
        assert want["min_kva"] == pytest.approx(min_kva)
        assert want["next_kva"] == next((s for s in got if s >= min_kva - 1e-9), None)
        if size:
            assert want["loaded_pct"] == pytest.approx(100 * peak_va / 1000 / size)


# ------------------------------------------------------------------ sound level limit steps
def limit(lid, db, start="", end="", label="FOH LAeq 15", kind="limit"):
    return {"id": lid, "source": "FOH", "metric": "LAeq 15", "kind": kind, "value_db": db, "start": start, "end": end, "label": label}


def spl_rev(db, rev, saved_at, doc, show_id=None, by="Pat", note=""):
    db.execute("INSERT INTO spl_limit_revisions (event_id, show_id, rev, saved_at, entered_by, change_note, doc) "
               "VALUES (1, ?, ?, ?, ?, ?, ?)", (show_id, rev, saved_at, by, note, doc if isinstance(doc, str) else json.dumps({"limits": doc})))


def spl_fill(db):
    # rev 1 (event default, saved an hour BEFORE the range): 100 dB until 21:00, then 95 dB.
    spl_rev(db, 1, T0 - 3600, [limit("a", 100.0, end="21:00"), limit("b", 95.0, start="21:00")], note="PRIVATE_NOTE_1")
    # rev 2 (event default, 20:00, inside the range): the early level is lowered to 98 dB.
    spl_rev(db, 2, T0 + 3600, [limit("a", 98.0, end="21:00"), limit("b", 95.0, start="21:00")], note="PRIVATE_NOTE_2")
    # rev 3 (override for this show, 21:30, inside): one flat 90 dB all day.
    spl_rev(db, 3, T0 + 9000, [limit("c", 90.0)], show_id=1)
    # rev 4 saved AFTER the range ends (22:12): must never appear anywhere.
    spl_rev(db, 4, T0 + 11520, [limit("d", 123.0, label="SENTINEL_LIMIT")], show_id=1)


RANGE = dict(preset="custom", from_hhmm="19:30", to_hhmm="22:00")        # S = T0 + 1800, E = T0 + 10800


def test_limit_steps_follow_revisions_and_time_windows(tmp_path):
    d = make(mkdb(tmp_path, spl_fill), cfg(), **RANGE)
    s = d.sections["spl"]
    S, E = T0 + 1800, T0 + 10800
    # 19:30-20:00 rev 1 first window (100); 20:00-21:00 rev 2 (98); 21:00-21:30 rev 2 second window (95);
    # 21:30-22:00 the show override rev 3 (90).  One series, steps joined end to start.
    assert len(s["steps"]) == 1
    assert s["steps"][0]["steps"] == [[S, T0 + 3600, 100.0], [T0 + 3600, T0 + 7200, 98.0], [T0 + 7200, T0 + 9000, 95.0],
                                      [T0 + 9000, E, 90.0]]
    assert s["steps"][0]["label"] == "FOH FOH LAeq 15"
    assert [r["rev"] for r in s["revisions"]] == [2, 3]
    assert [r["rev"] for r in d.charts["spl"]["revisions"]] == [2, 3]
    assert s["limits_at_start"] == ["FOH LAeq 15: 100 dB (limit) start-21:00", "FOH LAeq 15: 95 dB (limit) 21:00-end"]
    # The set saved before the range gives its limits but its save time (18:00) and note are never shown.
    html = render_html(d)
    assert "PRIVATE_NOTE_1" not in html and "18:00" not in html
    assert "SENTINEL_LIMIT" not in html and "123" not in re.sub(r"[^0-9.]", " ", " ".join(str(x) for x in s["steps"])).split()
    assert "Limit over time" in html and "100 dB" in html and "98 dB" in html and "95 dB" in html and "90 dB" in html
    assert "<script" not in html.lower()


def test_limit_steps_leave_a_gap_where_no_limit_is_set(tmp_path):
    def fill(db):
        spl_rev(db, 1, T0 - 60, [limit("a", 100.0, start="20:00", end="21:00")])     # one hour with a limit, none before or after
    d = make(mkdb(tmp_path, fill), cfg(), **RANGE)
    steps = d.sections["spl"]["steps"][0]["steps"]
    assert steps == [[T0 + 3600, T0 + 7200, 100.0]]
    html = render_html(d)
    assert html.count("<polyline") >= 1


def test_a_cleared_override_falls_back_to_the_event_default():
    # Rows are in saved-time order, as the query returns them: (rev, saved_at, ..., doc, show_id).
    rows = [(3, 5.0, "a", "", '{"limits":[1]}', 7), (1, 10.0, "a", "", '{"limits":[]}', None), (2, 20.0, "a", "", "null", 7)]
    f = report_build._in_force
    assert f(rows, 7, 4.0) is None
    assert f(rows, 7, 6.0)[0] == 3                 # this show's override, saved at 5
    assert f(rows, 7, 15.0)[0] == 3
    assert f(rows, 7, 25.0)[0] == 1                # override cleared at 20: back to the event default
    assert f(rows, 8, 15.0)[0] == 1                # another show never sees show 7's override
    assert f(rows, 8, 6.0) is None                 # the event default was not saved until 10
    assert f(rows, 7, 5.0, strict=True) is None and f(rows, 7, 5.0)[0] == 3
    assert f([(1, 10.0, "a", "", '{"limits":[]}', None)], 7, 10.0, strict=True) is None     # saved exactly at t: not before it


def test_event_without_limits_has_no_step_chart(tmp_path):
    d = make(mkdb(tmp_path), cfg(), **RANGE)
    assert d.sections.get("spl") is None and "spl" not in d.charts


def test_step_chart_svg_has_steps_revision_marks_and_breaks():
    spec = {"title": "Sound level limit (dB)", "more": 0, "revisions": [{"rev": 2, "ts": T0 + 90}],
            "series": [{"label": "FOH LAeq 15", "kind": "limit",
                        "steps": [[T0, T0 + 60, 100.0], [T0 + 60, T0 + 120, 98.0], [T0 + 200, T0 + 300, 95.0]]}]}
    out = svg.step_chart(spec, {"down": [(T0 + 120, T0 + 200)], "markers": [(1, T0 + 10)]}, T0, T0 + 300, SITE)
    assert out.startswith("<svg") and out.count("<polyline") == 2          # the break after 98 dB is not drawn across
    assert ">r2<" in out and ">100 dB<" in out and ">95 dB<" in out and "Stagewatch not recording" in out
    assert "<script" not in out
    empty = svg.step_chart({"title": "Sound level limit (dB)", "series": []}, {}, T0, T0 + 300, SITE)
    assert "no limit was set" in empty


def test_sharing_keeps_the_steps_but_not_who_or_why(tmp_path):
    shared = make(mkdb(tmp_path, spl_fill), cfg(), share=True, **RANGE)
    html = render_html(shared)
    assert "Limit over time" in html and "PRIVATE_NOTE_2" not in html and "Pat" not in re.sub(r"<[^>]+>", " ", html).split()
    blob = b"".join(csv_zip.csv_files(shared).values()).decode("utf-8")
    assert "PRIVATE_NOTE" not in blob and "spl_limit_steps" not in blob


# ------------------------------------------------------------------ CSV and ZIP
def test_csv_has_the_new_data_and_hostile_text_is_neutralised(tmp_path):
    def fill(db):
        leak_fill(db, dev_name="@evil", early=3.0, mid=2.0, late=1.0)           # falling
        meta(db, "rack1.pm1_0_current", "rack1", "current", "A", "=cmd", "Rack 1", {"channel": "pm1_0", "field": "current"})
        states(db, "rack1.pm1_0_current", [(0, 10.0)])
        gen_fill(db)
        spl_rev(db, 1, T0 - 60, [{**limit("a", 100.0), "source": "=HYPERLINK(1)"}])
    config = cfg([meter("rack1", ["pm1_0"], [ShellyChannelConfig(key="pm1_0", rating_a=16, breaker_a=13)])])
    d = make(mkdb(tmp_path, fill), config, **HOUR)
    z = zipfile.ZipFile(io.BytesIO(csv_zip.make_zip(d)))
    assert {"power.csv", "leakage_1min.csv", "generator.csv", "spl_limit_steps.csv", "report.html"} <= set(z.namelist())

    def table(name):
        return list(csv.reader(io.StringIO(z.read(name).decode("utf-8-sig"))))
    power = table("power.csv")
    head = power[0]
    leak = next(r for r in power if r[0] == "leakage")
    assert leak[1].startswith("'@evil")                                         # text starting with @ is made safe
    assert leak[head.index("Leakage trend")] == "falling"
    assert leak[head.index("Leakage change (mA)")] == "-2.0"                    # a number stays a number: no quote prefix
    rack = next(r for r in power if r[1].endswith("=cmd") or r[1].startswith("Rack 1"))
    assert rack[head.index("Limit (A)")] == "13.0" and rack[head.index("Limit is the")] == "breaker"
    assert rack[head.index("Highest as % of limit")] == "76.9"                  # 10 / 13
    assert table("spl_limit_steps.csv")[1][0] == "'=HYPERLINK(1) FOH LAeq 15"     # the hostile limit label
    gen = {r[0] + r[2]: r[1] for r in table("generator.csv")[1:]}
    assert gen["Smallest generator suggestedkVA"] == "15.0" and gen["Your generator sizekVA"] == "15.0"
    assert len(table("leakage_1min.csv")) == 1 + 60
    for name in z.namelist():
        if name.endswith(".csv"):
            for row in table(name)[1:]:
                for c in row:
                    assert not c.startswith(("=", "+", "@", "\t", "\r")), (name, c)


# ------------------------------------------------------------------ past crash days
def crash_db(tmp_path):
    def fill(db):
        db.execute("DELETE FROM hub_runs")
        db.execute("INSERT INTO shows (id, name, started, ended, event_id, day) VALUES (2, 'Day 2', ?, ?, 1, '2026-10-04')",
                   (T0 + 86400, T0 + 86400 + 6.5 * 3600))
        for rid, started, last_seen, stopped, reason in (
                (1, T0, T0 + 1800, T0 + 1800, "update"),                         # clean: not listed
                (2, T0 + 1900, T0 + 3600, None, ""),                             # crash during day 1
                (3, T0 + 3720, T0 + 86400 + 600, None, "power_or_restart"),      # power cut during day 2
                (4, T0 + 86400 + 1200, T0 + 86400 + 1300, None, ""),             # the run that is going now
                (5, T0 + 5 * 86400, T0 + 5 * 86400 + 60, None, "")):             # a crash on a day with no show
            db.execute("INSERT INTO hub_runs (id, started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?,NULL)",
                       (rid, started, last_seen, stopped, reason, "0.7.0"))
    return mkdb(tmp_path, fill)


def test_crash_days_list_only_runs_without_a_clean_stop(tmp_path):
    conn = open_readonly(crash_db(tmp_path))
    try:
        days = crash_days(conn, SITE, live_run_id=4)
    finally:
        conn.close()
    # Newest last-seen first.  Run 4 is live and run 1 stopped cleanly, so neither is listed.
    assert [d["stopped_at"] for d in days] == [T0 + 5 * 86400 + 60, T0 + 86400 + 600, T0 + 3600]
    nodays, day2, day1 = days
    assert nodays["show_id"] is None and nodays["day"] == "2026-10-08" and nodays["restarted_at"] is None and nodays["down_s"] is None
    assert (day2["day"], day2["show_id"], day2["kind"]) == ("2026-10-04", 2, "restart")
    assert day2["restarted_at"] == T0 + 86400 + 1200 and day2["down_s"] == pytest.approx(600.0)     # back 10 minutes later
    assert (day1["day"], day1["show_id"], day1["kind"], day1["show_name"], day1["event"]) == ("2026-10-03", 1, "unexpected", "Day 1", "Test Festival")
    assert day1["restarted_at"] == T0 + 3720 and day1["down_s"] == pytest.approx(120.0)             # back 2 minutes later
    # Without a live run id the newest run is also listed (the command line has none).
    conn = open_readonly(crash_db(tmp_path / "again" if (tmp_path / "again").mkdir() is None else tmp_path))
    try:
        assert len(crash_days(conn, SITE, None, limit=2)) == 2
    finally:
        conn.close()


def test_crash_days_of_a_history_without_run_records(tmp_path):
    conn = open_readonly(mkdb(tmp_path, lambda db: db.execute("DROP TABLE hub_runs")))
    try:
        assert crash_days(conn, SITE, None) == []
    finally:
        conn.close()


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from stagewatch.core.hub import Hub
    from stagewatch.web.server import create_app
    h = Hub(tmp_path, emulate=True)
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        r = c.post("/api/admin/report/demo")
        assert r.status_code == 200, r.text
        c.show = r.json()["show_id"]
        yield c


CRASH = "/api/admin/report/crash-days"


def test_crash_days_route_needs_admin(client):
    from fastapi.testclient import TestClient
    assert TestClient(client.app).get(CRASH).status_code == 401


def test_crash_days_route_lists_the_demo_stop_without_leaking(client):
    db = sqlite3.connect(str(client.hub.recorder.path))
    db.execute("UPDATE shows SET name = 'Night 10.20.30.40 aa:bb:cc:dd:ee:ff' WHERE id = ?", (client.show,))
    db.commit()
    db.close()
    r = client.get(CRASH)
    assert r.status_code == 200
    body = r.json()
    assert body["capped"] is False and len(body["days"]) == 1          # the live run is not listed
    d = body["days"][0]
    assert set(d) == {"day", "show_id", "show_name", "event", "stopped_at", "restarted_at", "down_s", "kind"}
    assert d["show_id"] == client.show and d["kind"] == "restart" and d["down_s"] == pytest.approx(420.0)   # 21:40 to 21:47
    assert "10.20.30.40" not in r.text and "aa:bb:cc" not in r.text.lower()


def test_demo_day_report_has_the_new_sections(client):
    html = client.get("/api/admin/report", params={"show_id": client.show}).text
    assert "Limit over time" in html and "100 dB" in html and "98 dB" in html and "95 dB" in html
    assert "Earth leakage" in html and "rising" in html and "<h3>Generator</h3>" in html
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/admin/report/data.zip", params={"show_id": client.show}).content))
    assert {"spl_limit_steps.csv", "leakage_1min.csv", "generator.csv"} <= set(z.namelist())
