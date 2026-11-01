"""Stored readings are thinned: unchanged values repeat only as a heartbeat, changes are written at once."""
from stagewatch.core.recorder import (SITE_MIN_INTERVAL_S, UNCHANGED_HEARTBEAT_S, Recorder)
from stagewatch.core.report import query

T0 = 1_800_000_000.0


def _rec(tmp_path):
    return Recorder(tmp_path / "h.db")


def _rows(rec, eid):
    rec.flush()
    return rec._db.execute("SELECT ts, value FROM states WHERE entity_id = ? ORDER BY ts", (eid,)).fetchall()


def test_unchanged_value_written_once_per_heartbeat(tmp_path):
    rec = _rec(tmp_path)
    for i in range(61):                       # 61 s at 1 Hz, always 20.0
        rec.record_state("n.temp", 20.0, T0 + i)
    # rows at 0, 30 and 60 s
    assert [r[0] - T0 for r in _rows(rec, "n.temp")] == [0, 30, 60]


def test_change_is_written_at_once(tmp_path):
    rec = _rec(tmp_path)
    for i, v in enumerate([20.0, 20.0, 20.5, 20.5, 20.0]):
        rec.record_state("n.temp", v, T0 + i)
    assert _rows(rec, "n.temp") == [(T0, 20.0), (T0 + 2, 20.5), (T0 + 4, 20.0)]


def test_site_values_at_most_every_ten_seconds_and_latest_not_stuck(tmp_path):
    rec = _rec(tmp_path)
    for i in range(25):                       # changes every second
        rec.record_state("site.temperature", 20.0 + i * 0.01, T0 + i)
    got = [r[0] - T0 for r in _rows(rec, "site.temperature")]
    assert got == [0, 10, 20]                 # not every second; each is the value of that second
    assert SITE_MIN_INTERVAL_S == 10.0


def test_gap_stays_a_gap_and_none_is_kept(tmp_path):
    rec = _rec(tmp_path)
    rec.record_state("n.temp", 20.0, T0)
    rec.record_state("n.temp", 20.0, T0 + 30)
    rec.record_state("n.temp", 20.0, T0 + 600)     # nothing offered for 570 s: nothing stored
    rec.record_state("n.temp", None, T0 + 601)
    rec.record_state("n.temp", None, T0 + 602)
    assert _rows(rec, "n.temp") == [(T0, 20.0), (T0 + 30, 20.0), (T0 + 600, 20.0), (T0 + 601, None)]


def test_clock_step_back_and_new_show_write(tmp_path):
    rec = _rec(tmp_path)
    rec.record_state("n.temp", 20.0, T0 + 100)
    rec.record_state("n.temp", 20.0, T0 + 50)      # clock stepped back
    assert len(_rows(rec, "n.temp")) == 2
    rec.start_show("Two")
    rec.record_state("n.temp", 20.0, T0 + 51)      # first row of the new show
    assert len(_rows(rec, "n.temp")) == 3


def test_extremes_history_and_report_match_dense(tmp_path):
    thin, dense = _rec(tmp_path), None
    vals = [20.0] * 40 + [22.5] * 40 + [19.0] * 40 + [20.0] * 40     # 160 s at 1 Hz
    for i, v in enumerate(vals):
        thin.record_state("n.temp", v, T0 + i)
    ex = thin.extremes("n.temp", T0, T0 + 200)
    assert ex == {"low": [T0 + 80, 19.0], "high": [T0 + 40, 22.5]}   # as dense data would give
    # time-weighted report mean over 0..160 s: (40*20 + 40*22.5 + 40*19 + 40*20) / 160 = 20.375
    rows = _rows(thin, "n.temp")
    st = query.summarise(rows, T0, T0 + 160)
    assert st.gaps == []
    assert abs(st.integral / st.covered_s - 20.375) < 1e-9
    assert st.min == 19.0 and st.max == 22.5


def test_growth_per_hour_one_hertz_feed(tmp_path):
    rec = _rec(tmp_path)
    for i in range(3600):
        for e in ("site.temperature", "site.humidity", "site.pressure", "site.speed_of_sound", "site.dew_point"):
            rec.record_state(e, 1.0, T0 + i)       # unchanged site values
        rec.record_state("n.temp", 20.0 + (i // 600) * 0.1, T0 + i)   # steps every 10 min
    rec.flush()
    n = rec._db.execute("SELECT COUNT(*) FROM states").fetchone()[0]
    # five unchanged site values: 120 rows each; node: 6 steady spells of 600 s = 20 rows each (the first
    # row of a spell is the change itself) -> 720 instead of 21 600
    assert n == 5 * 120 + 6 * 20
    assert UNCHANGED_HEARTBEAT_S == 30.0
