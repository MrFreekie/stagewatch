"""Read-only access to a Stagewatch history database, and every query the report makes.

The report never writes and never migrates: it opens the file ``mode=ro`` with
``PRAGMA query_only``, detects what the file has (a v1 database, a backup copy and a v2 database
all work), and treats anything missing as "not recorded".  Readings are never back-filled: a
reading holds for a short time (``HOLD_S``) and after that the time is a gap.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import sitetime

HOLD_S = 120.0          # a reading counts for this long at most; after that it is a gap
BUCKET_S = 60.0         # means are the mean of one-minute bucket means
MAX_CUSTOM_S = 24 * 3600
DEFAULT_DEADLINE_S = 30.0


class ReportError(Exception):
    """A problem the user can act on.  ``code`` is stable (tests, API); ``message`` is crew wording."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ReportScope:
    show_id: int
    preset: str = "day"                 # day | show_time | markers | custom
    from_hhmm: str = ""
    to_hhmm: str = ""
    from_marker: int | None = None
    to_marker: int | None = None
    sections: set[str] | None = None    # None = every section
    share: bool = False                 # the Sharing excerpt defaults (redact.py)


@dataclass
class Window:
    show: dict
    start: float
    end: float
    preset: str
    label: str                          # e.g. "planned doors to planned curfew"
    day: str                            # YYYY-MM-DD show day
    full_day: bool
    clipped: bool = False
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ connection
def open_readonly(path: str | Path, deadline_s: float | None = DEFAULT_DEADLINE_S) -> sqlite3.Connection:
    """A read-only connection.  Aborts a query that runs past ``deadline_s`` (so it cannot hold a
    read snapshot, and a growing WAL, for long)."""
    p = Path(path)
    if not p.is_file():
        raise ReportError("no_database", "There is no history file there.")
    try:
        conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
        conn.execute("PRAGMA query_only = 1")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        raise ReportError("damaged", "The history file could not be opened. Try a backup.") from None
    if deadline_s:
        limit = time.monotonic() + deadline_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > limit else 0, 50000)
    return conn


def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # table is a literal from this module


def is_deadline(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "interrupt" in str(exc).lower()


# ------------------------------------------------------------------ shows and the window
def _show_dict(conn, row, have_cols: set[str], site) -> dict:
    d = {"id": row[0], "name": row[1], "started": row[2], "ended": row[3], "event_id": row[4], "day": row[5]}
    if not d["day"]:
        d["day"] = sitetime.show_day(d["started"], site).isoformat()
    d["event_name"] = ""
    if d["event_id"] is not None and "events" in tables(conn):
        r = conn.execute("SELECT name FROM events WHERE id = ?", (d["event_id"],)).fetchone()
        d["event_name"] = r[0] if r else ""
    return d


def _show_select(conn) -> str:
    cols = columns(conn, "shows")
    ev = "event_id" if "event_id" in cols else "NULL"
    day = "day" if "day" in cols else "NULL"
    return f"SELECT id, name, started, ended, {ev}, {day} FROM shows"


def list_shows(conn: sqlite3.Connection, site) -> list[dict]:
    sel = _show_select(conn)
    return [_show_dict(conn, r, set(), site) for r in conn.execute(sel + " ORDER BY id")]


def get_show(conn: sqlite3.Connection, show_id: int, site) -> dict:
    r = conn.execute(_show_select(conn) + " WHERE id = ?", (show_id,)).fetchone()
    if r is None:
        raise ReportError("no_show", "No such show.")
    return _show_dict(conn, r, set(), site)


def resolve_window(conn: sqlite3.Connection, site, scope: ReportScope, now: float) -> Window:
    """The time range to report on, inside one show.  Raises ReportError with crew wording."""
    show = get_show(conn, scope.show_id, site)
    s_start = show["started"]
    s_end = show["ended"] if show["ended"] is not None else max(now, s_start)
    day = show["day"]
    preset = scope.preset or "day"
    notes: list[str] = []
    label = "whole show day"
    if preset == "day":
        start, end = s_start, s_end
        if s_end - s_start > 7 * 86400:
            notes.append("This show was left open for several days; the whole of it is reported.")
    elif preset == "show_time":
        if "schedule_items" not in tables(conn):
            raise ReportError("no_doors_curfew", "No doors/curfew in the schedule.")
        d = conn.execute("SELECT MIN(planned_start) FROM schedule_items WHERE show_id = ? AND kind = 'doors'",
                         (show["id"],)).fetchone()[0]
        c = conn.execute("SELECT MAX(planned_start) FROM schedule_items WHERE show_id = ? AND kind = 'curfew'",
                         (show["id"],)).fetchone()[0]
        if d is None or c is None:
            raise ReportError("no_doors_curfew", "No doors/curfew in the schedule.")
        start, end = d, c
        label = "planned doors to planned curfew"
    elif preset == "markers":
        ids = [scope.from_marker, scope.to_marker]
        ts = []
        for mid in ids:
            r = conn.execute("SELECT ts FROM markers WHERE id = ? AND show_id = ?", (mid, show["id"])).fetchone() \
                if isinstance(mid, int) else None
            if r is None:
                raise ReportError("no_marker", "Pick two markers from this show.")
            ts.append(r[0])
        start, end = min(ts), max(ts)
        label = "between two markers"
    elif preset == "custom":
        try:
            start = sitetime.resolve(day, scope.from_hhmm, site)
            end = sitetime.resolve(day, scope.to_hhmm, site)
        except (ValueError, TypeError):
            raise ReportError("bad_time", "Times must be written as HH:MM, for example 19:30.") from None
        if end <= start:
            raise ReportError("end_before_start",
                              "End is before start. Times before 06:00 count as the next morning.")
        label = "custom range"
    else:
        raise ReportError("bad_preset", "Unknown range.")
    if end <= start:
        raise ReportError("empty_range", "That range is empty.")
    if preset != "day" and end - start > MAX_CUSTOM_S:
        raise ReportError("too_long", "A range can be at most 24 hours. Use the whole day for longer.")
    clipped = False
    if start < s_start or end > s_end:
        start, end, clipped = max(start, s_start), min(end, s_end), True
        if end <= start:
            raise ReportError("outside_show", "That range is outside this show.")
        notes.append("The range was cut to the times this show was open.")
    full = preset == "day" and not clipped
    return Window(show, start, end, preset, label, day, full, clipped, notes)


def dst_changes(site, start: float, end: float) -> bool:
    """True when the site's UTC offset is not the same all through the range."""
    base = sitetime.utc_offset_s(start, site)
    step = max(1800.0, (end - start) / 200.0)
    t = start
    while t <= end:
        if sitetime.utc_offset_s(t, site) != base:
            return True
        t += step
    return sitetime.utc_offset_s(end, site) != base


# ------------------------------------------------------------------ series maths
@dataclass
class SeriesStats:
    n: int = 0
    min: float | None = None
    min_ts: float | None = None
    max: float | None = None
    max_ts: float | None = None
    mean: float | None = None           # mean of one-minute bucket means (time-weighted inside each)
    covered_s: float = 0.0
    coverage: float = 0.0               # covered_s / range length
    integral: float = 0.0               # value x seconds over the covered time
    interval_s: float | None = None     # median time between readings
    gaps: list[tuple[float, float]] = field(default_factory=list)
    buckets: list[tuple[float, float, float, float]] = field(default_factory=list)  # (t, mean, min, max)


def summarise(rows: list[tuple[float, float | None]], start: float, end: float,
              hold: float = HOLD_S, bucket: float = BUCKET_S) -> SeriesStats:
    """Statistics of one entity over [start, end].  ``rows`` are (ts, value) in time order; a None
    value means "no reading" and is a gap.  A reading holds until the next one but never longer
    than ``hold``; the rest is a gap and is never filled in."""
    st = SeriesStats()
    span = max(end - start, 1e-9)
    pts = [(t, v) for t, v in rows if start <= t <= end]
    if not pts:
        st.gaps = [(start, end)]
        return st
    nb = int(math.ceil(span / bucket))
    wsum = [0.0] * nb
    cov = [0.0] * nb
    bmin: list[float | None] = [None] * nb
    bmax: list[float | None] = [None] * nb
    gaps: list[tuple[float, float]] = []
    if pts[0][0] - start > hold:
        gaps.append((start, pts[0][0]))
    dts = []
    for i, (t, v) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else end
        if i + 1 < len(pts):
            dts.append(nxt - t)
        good = v is not None and v == v and abs(v) != math.inf
        if not good:
            gaps.append((t, nxt))
            continue
        st.n += 1
        if st.min is None or v < st.min:
            st.min, st.min_ts = v, t
        if st.max is None or v > st.max:
            st.max, st.max_ts = v, t
        bi = min(nb - 1, int((t - start) // bucket))
        bmin[bi] = v if bmin[bi] is None else min(bmin[bi], v)
        bmax[bi] = v if bmax[bi] is None else max(bmax[bi], v)
        seg_end = t + min(nxt - t, hold)
        if nxt - t > hold:
            gaps.append((seg_end, nxt))
        a = t
        while a < seg_end - 1e-9:
            bi = min(nb - 1, int((a - start) // bucket))
            b = min(seg_end, start + (bi + 1) * bucket) if bi < nb - 1 else seg_end
            w = b - a
            wsum[bi] += v * w
            cov[bi] += w
            st.integral += v * w
            st.covered_s += w
            a = b
    st.coverage = min(1.0, st.covered_s / span)
    means = []
    for i in range(nb):
        if cov[i] > 0:
            m = wsum[i] / cov[i]
            means.append(m)
            st.buckets.append((start + i * bucket, m, bmin[i] if bmin[i] is not None else m,
                               bmax[i] if bmax[i] is not None else m))
    st.mean = sum(means) / len(means) if means else None
    if dts:
        st.interval_s = statistics.median(dts)
    st.gaps = merge_spans(gaps)
    return st


def merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(s for s in spans if s[1] > s[0]):
        if out and a <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract_spans(spans: list[tuple[float, float]], cut: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for a, b in spans:
        pieces = [(a, b)]
        for c0, c1 in cut:
            nxt = []
            for p0, p1 in pieces:
                if c1 <= p0 or c0 >= p1:
                    nxt.append((p0, p1))
                    continue
                if c0 > p0:
                    nxt.append((p0, c0))
                if c1 < p1:
                    nxt.append((c1, p1))
            pieces = nxt
        out.extend(pieces)
    return out


@dataclass
class CounterResult:
    delta: float = 0.0          # sum of increases, counter units
    resets: int = 0             # drops of the counter; the step across a reset is not counted
    gap_s: float = 0.0          # time with no reading between counter readings (hold exceeded)
    n: int = 0


def counter_delta(rows: list[tuple[float, float | None]], start: float, end: float,
                  hold: float = HOLD_S) -> CounterResult:
    """Energy used from a cumulative counter: the sum of its increases.  When the counter drops
    (a reset) the step across it is not counted, because what happened between the two readings is
    not recorded; the count of resets is returned so the report can say so."""
    res = CounterResult()
    prev = None
    for t, v in rows:
        if t < start or t > end or v is None or v != v:
            continue
        res.n += 1
        if prev is not None:
            if t - prev[0] > hold:
                res.gap_s += t - prev[0]
            if v >= prev[1]:
                res.delta += v - prev[1]
            else:
                res.resets += 1
        prev = (t, v)
    return res


def read_rows(conn: sqlite3.Connection, show_id: int, entity_id: str, start: float, end: float
              ) -> list[tuple[float, float | None]]:
    return conn.execute("SELECT ts, value FROM states WHERE show_id = ? AND entity_id = ? "
                        "AND ts >= ? AND ts <= ? ORDER BY ts", (show_id, entity_id, start, end)).fetchall()


def entities_with_data(conn: sqlite3.Connection, show_id: int, start: float, end: float) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT entity_id FROM states WHERE show_id = ? AND ts >= ? AND ts <= ? ORDER BY entity_id",
        (show_id, start, end))]


def value_near(conn, show_id: int, entity_id: str, ts: float, lo: float, max_age_s: float = 600.0) -> float | None:
    """The last reading at or before ``ts`` (and not before ``lo`` or older than ``max_age_s``)."""
    r = conn.execute("SELECT value FROM states WHERE show_id = ? AND entity_id = ? AND ts <= ? AND ts >= ? "
                     "ORDER BY ts DESC LIMIT 1", (show_id, entity_id, ts, max(lo, ts - max_age_s))).fetchone()
    return r[0] if r else None


def entity_meta(conn: sqlite3.Connection) -> dict[str, dict]:
    import json
    if "entity_meta" not in tables(conn):
        return {}
    out = {}
    for r in conn.execute("SELECT entity_id, device_id, kind, unit, name, device_name, area, doc FROM entity_meta"):
        try:
            doc = json.loads(r[7]) if r[7] else {}
        except ValueError:
            doc = {}
        out[r[0]] = {"device_id": r[1], "kind": r[2], "unit": r[3], "name": r[4], "device_name": r[5],
                     "area": r[6], "doc": doc if isinstance(doc, dict) else {}}
    return out


# ------------------------------------------------------------------ hub runs and downtime
@dataclass
class RunSpan:
    id: int
    started: float
    end: float
    clean: bool
    reason: str
    version: str
    doc: dict
    clock_jump: bool = False
    live: bool = False


def run_spans(conn: sqlite3.Connection, show_id: int, now: float, live: bool = False) -> list[RunSpan]:
    """Every hub run, oldest first, with the time each one ended.  A run with no clean stop ends at
    the later of its last heartbeat and the last row the show has before the next run.  ``live``
    says the newest run is still going (the report is being made by the running hub)."""
    import json
    if "hub_runs" not in tables(conn):
        return []
    rows = conn.execute("SELECT id, started, last_seen, stopped, stop_reason, version, doc FROM hub_runs "
                        "ORDER BY id").fetchall()
    out: list[RunSpan] = []
    for i, r in enumerate(rows):
        nxt_start = rows[i + 1][1] if i + 1 < len(rows) else None
        is_live = live and i == len(rows) - 1 and r[3] is None
        if r[3] is not None:
            end, clean = r[3], True
        elif is_live:
            end, clean = max(now, r[2]), False
        else:
            end, clean = r[2], False
            hi = nxt_start if nxt_start is not None else float("inf")
            for sql in ("SELECT MAX(ts) FROM states WHERE show_id = ? AND ts >= ? AND ts < ?",
                        "SELECT MAX(ts) FROM markers WHERE show_id = ? AND ts >= ? AND ts < ?",
                        "SELECT MAX(ts) FROM alarm_log WHERE show_id = ? AND ts >= ? AND ts < ?"):
                v = conn.execute(sql, (show_id, r[1], hi)).fetchone()[0]
                if v is not None and v > end:
                    end = v
        try:
            doc = json.loads(r[6]) if r[6] else {}
        except ValueError:
            doc = {}
        out.append(RunSpan(r[0], r[1], max(end, r[1]), clean, r[4] or "", r[5] or "",
                           doc if isinstance(doc, dict) else {}, live=is_live))
    for i in range(1, len(out)):
        if out[i].started < out[i - 1].end - 1.0:
            out[i].clock_jump = True
    return out


@dataclass
class DownPeriod:
    start: float
    end: float
    how_ended: str          # how the run before it ended
    kind: str               # clean | update | rollback | unclean | restart | before_first | unknown


def down_periods(runs: list[RunSpan], start: float, end: float) -> list[DownPeriod]:
    """The parts of [start, end] when no hub run was going."""
    out: list[DownPeriod] = []
    cursor = start
    prev: RunSpan | None = None
    for r in runs:
        if r.end <= start:
            prev = r
            continue
        if r.started >= end:
            break
        if r.started > cursor:
            out.append(_down(cursor, min(r.started, end), prev))
        cursor = max(cursor, r.end)
        prev = r
    if cursor < end:
        out.append(_down(cursor, end, prev))
    return [d for d in out if d.end > d.start]


def _down(a: float, b: float, prev: RunSpan | None) -> DownPeriod:
    if prev is None:
        return DownPeriod(a, b, "No earlier run is recorded.", "before_first")
    if prev.clean:
        if prev.reason == "update":
            return DownPeriod(a, b, "Stopped for a software update.", "update")
        if prev.reason == "rollback":
            return DownPeriod(a, b, "Stopped for a software rollback.", "rollback")
        return DownPeriod(a, b, "Stopped cleanly.", "clean")
    if prev.reason == "power_or_restart":
        return DownPeriod(a, b, "The computer restarted or lost power.", "restart")
    return DownPeriod(a, b, "Ended without a clean stop: crash, power cut or the PC shutting down.", "unclean")


def silent_periods(conn: sqlite3.Connection, show_id: int, start: float, end: float,
                   min_s: float = 180.0) -> list[tuple[float, float]]:
    """For a file with no hub runs: stretches with no reading from any entity (Stagewatch may have
    been off).  Works in whole minutes."""
    mins = [r[0] for r in conn.execute(
        "SELECT DISTINCT CAST(ts / 60 AS INTEGER) FROM states WHERE show_id = ? AND ts >= ? AND ts <= ? "
        "ORDER BY 1", (show_id, start, end))]
    out = []
    prev = start
    for m in mins:
        t = m * 60.0
        if t - prev >= min_s:
            out.append((prev, t))
        prev = t + 60.0
    if end - prev >= min_s:
        out.append((prev, end))
    return out
