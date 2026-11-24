"""Builds the report: plain data, one dict per section, no HTML and no hub.

``build_report(conn, cfg, scope, now, source_label)`` returns a ``ReportData``.  Each section is
built in its own ``try``: a section that fails says so and the rest of the report still appears.
Times in the data are UTC epoch seconds; the writers turn them into site time.

House rules kept here: a gap stays a gap (nothing is filled in), a peak is "the highest recorded
sample" with the recording interval, and nothing recorded outside the chosen range is used except
the limits that were in force when the range began.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ... import acoustics
from ...version import __version__
from . import fmt, redact
from .. import power_tree
from .query import (DEFAULT_DEADLINE_S, Budget, CounterResult, ReportError, ReportScope, SeriesStats, Window,
                    columns, counter_delta, down_periods, dst_changes, entity_meta, is_deadline, iter_rows, merge_spans,
                    resolve_window, run_spans, silent_periods, subtract_spans, summarise, tables,
                    value_near)
from .. import sitetime

ALL_SECTIONS = ("summary", "env", "wind", "spl", "power", "alarms", "devices", "markers", "schedule", "system")
SHARING_DEFAULT_OFF = ("system", "schedule")
SECTION_NAMES = {
    "summary": "Summary", "env": "Environment", "wind": "Wind", "spl": "Sound level limits",
    "power": "Power", "alarms": "Alarms", "devices": "Device outages", "markers": "Markers",
    "schedule": "Planned running order", "system": "System events", "gaps": "Data gaps",
}
LEVEL_NAMES = {1: "advisory", 2: "alert", 3: "stop"}
ALARM_ROW_CAP = 500
MAX_MARKERS = 5000
MIN_SENSOR_GAP_S = 60.0

ENV_KINDS = (  # kind, title, unit shown, decimals, scale from the stored unit
    ("temperature", "Temperature", "°C", 1, 1.0),
    ("humidity", "Humidity", "%RH", 0, 1.0),
    ("pressure", "Pressure", "hPa", 1, 0.01),
)
POWER_KINDS = {"current": "A", "voltage": "V", "power": "W", "apparent_power": "VA", "frequency": "Hz",
               "power_factor": "", "reactive_power": "var", "thd": "%"}
_LEAK = re.compile(r"(?i)leak|earth|residual|rcd|rcm|\bpe\b")
_DEVICE_MSG = re.compile(r"^(.*?): (\w+)(?: \((.*)\))?$", re.S)

LOAD_HIGH_PCT, LOAD_OVER_PCT = 80.0, 100.0   # the same 80 % and 100 % warnings as the Power card
# Earth leakage trend: the mean of the first quarter of the readings against the mean of the last
# quarter (one-minute averages, covered time only).  Small changes are called steady.
LEAK_MIN_SPAN_S = 1800.0      # fewer than 30 minutes of readings: no trend
LEAK_MIN_BUCKETS = 5          # at least this many one-minute averages in each quarter
LEAK_REL, LEAK_ABS_MA = 0.10, 0.5     # a change of at least 10 % of the start and 0.5 mA
# Standard generator sizes (kVA); a test keeps this equal to integrations/shelly/calc.py.
GEN_SIZES_KVA = (20, 30, 40, 50, 60, 80, 100, 125, 150, 200, 250, 300, 400, 500, 630, 800, 1000)
GEN_DEMAND = "power_site.site_apparent_demand"
GEN_LINE_DEMAND = "power_site.site_line_{}_apparent_demand"
GEN_NOTE = ("Worked out from the highest recorded site demand (an average over the last {w} minutes, as the "
            "window is set in Stagewatch now). It knows nothing of load that is not metered, starting currents "
            "or future additions. A guide for choosing a generator, not a sizing or safety certificate.")
SPL_MAX_REVS = 1000
SPL_MAX_SERIES = 8

POWER_ADVICE = ("Power figures come from the monitoring devices and are not a substitute for the "
                "electrician's certified inspection and testing. Stagewatch never controls breakers or generators.")
ENV_ADVICE = "Readings from Stagewatch sensors as calibrated on the day."
SPL_ADVICE = ("Limits as entered in Stagewatch. The licence or noise management plan is authoritative. "
              "Stagewatch did not measure SPL.")
WIND_ADVICE = ("Stagewatch is a guide. It does not replace the certified monitoring or the duty holder "
               "named in the event's plan.")


@dataclass
class ReportData:
    header: dict
    sections: dict[str, Any]
    site: Any                       # what sitetime needs: timezone, day_rollover
    window: Window
    share: bool = False
    charts: dict = field(default_factory=dict)


class _Ctx:
    def __init__(self, conn, cfg, site, win: Window, scope: ReportScope, now: float, live: bool,
                 budget: Budget | None = None) -> None:
        self.budget = budget or Budget()
        self.conn, self.cfg, self.site, self.win, self.scope, self.now = conn, cfg, site, win, scope, now
        self.S, self.E, self.show_id = win.start, win.end, win.show["id"]
        self.meta = entity_meta(conn)
        self.runs = run_spans(conn, self.show_id, now, live, win.start, win.end)
        self._series: dict[str, SeriesStats] = {}
        self.sensor_gaps: list[tuple[str, str, float, float]] = []   # label, device_id, a, b
        self.tables = tables(conn)
        self.distance_m = float(getattr(getattr(cfg, "site", None), "reference_distance_m", 30.0) or 30.0)

    def series(self, entity_id: str) -> SeriesStats:
        """Running statistics and one-minute buckets of one entity.  The readings themselves are
        read from a cursor and never kept."""
        if entity_id not in self._series:
            rows = iter_rows(self.conn, self.show_id, entity_id, self.S, self.E, self.budget)
            self._series[entity_id] = summarise(rows, self.S, self.E)
        return self._series[entity_id]

    def counter(self, entity_id: str) -> CounterResult:
        """The increases of a cumulative counter, read from a cursor."""
        return counter_delta(iter_rows(self.conn, self.show_id, entity_id, self.S, self.E, self.budget),
                             self.S, self.E)


# ------------------------------------------------------------------ entry point
def build_report(conn, cfg, scope: ReportScope, now: float, source_label: str = "this hub",
                 live: bool = False, notes: list[str] | None = None,
                 deadline_s: float | None = DEFAULT_DEADLINE_S) -> ReportData:
    """Make the report for ``scope``.  ``conn`` comes from ``query.open_readonly``.  ``live`` says the
    newest hub run is still going (the running hub is making the report).  ``deadline_s`` bounds the
    time spent in Python loops as well as in SQLite (None = no limit, for the command line)."""
    cfg_site = getattr(cfg, "site", None)
    warnings: list[str] = list(notes or [])    # e.g. the command line could not read the config
    tz = (getattr(cfg_site, "timezone", "") or "").strip()
    rollover = getattr(cfg_site, "day_rollover", "") or "06:00"
    probe = SimpleNamespace(timezone=tz, day_rollover=rollover)
    if not tz and "hub_runs" in tables(conn):
        for r in conn.execute("SELECT doc FROM hub_runs ORDER BY id DESC"):
            try:
                doc = json.loads(r[0]) if r[0] else {}
            except ValueError:
                continue
            if isinstance(doc, dict) and doc.get("tz"):
                tz = str(doc["tz"])
                break
    site = SimpleNamespace(timezone=tz, day_rollover=rollover)
    if sitetime.zone(site) is None and tz:
        site = SimpleNamespace(timezone="", day_rollover=rollover)
    if not tz:
        warnings.append("Time zone not set; times are shown in this computer's zone.")
    del probe
    win = resolve_window(conn, site, scope, now)
    ctx = _Ctx(conn, cfg, site, win, scope, now, live, Budget(deadline_s=deadline_s))
    wanted = _wanted_sections(scope)
    sections: dict[str, Any] = {}
    charts: dict = {}

    def run(name: str, fn) -> None:
        try:
            sections[name] = fn()
        except ReportError:
            raise
        except Exception as e:  # noqa: BLE001 - one broken section must not stop the report
            if is_deadline(e):
                raise ReportError("deadline", "The report took too long. Try a shorter range.") from None
            sections[name] = {"error": type(e).__name__}

    # Order matters: the alarm and outage pairs and the downtime come before what uses them.
    run("alarms_all", lambda: _alarms_all(ctx))
    alarms_all = sections.pop("alarms_all")
    if isinstance(alarms_all, dict) and "error" in alarms_all:
        alarms_all = {"alarms": [], "outages": [], "system": []}
        sections["alarms"] = {"error": "AlarmError"}
        sections["devices"] = {"error": "AlarmError"}
    downs = _downs(ctx)
    if "env" in wanted:
        run("env", lambda: _env(ctx, charts))
    if "wind" in wanted:
        run("wind", lambda: _wind(ctx))
    if "spl" in wanted:
        run("spl", lambda: _spl(ctx, charts))
    if "power" in wanted:
        run("power", lambda: _power(ctx, charts))
    if "alarms" in wanted and "alarms" not in sections:
        sections["alarms"] = _alarms_section(ctx, alarms_all["alarms"])
    if "devices" in wanted and "devices" not in sections:
        sections["devices"] = _devices_section(ctx, alarms_all["outages"])
    markers = []
    if "markers" in wanted:
        try:
            markers = _markers(ctx)
        except ReportError:
            raise
        except Exception as e:  # noqa: BLE001
            if is_deadline(e):
                raise ReportError("deadline", "The report took too long. Try a shorter range.") from None
            sections["markers"] = {"error": type(e).__name__}
            markers = []
    if "markers" in wanted and "markers" not in sections:
        sections["markers"] = {"rows": markers}
    if "schedule" in wanted:
        run("schedule", lambda: _schedule(ctx))
    if "system" in wanted:
        sections["system"] = {"rows": alarms_all["system"]}
    run("gaps", lambda: _gaps(ctx, downs, alarms_all["outages"]))
    if "summary" in wanted:
        run("summary", lambda: _summary(ctx, sections, downs, alarms_all))
    charts["context"] = {
        "down": [(d["start"], d["end"]) for d in sections.get("gaps", {}).get("down", [])],
        "markers": [(i + 1, m["ts"]) for i, m in enumerate(markers)],
        "alarms": [(a["start"], a["end"], a["level"]) for a in alarms_all["alarms"] if a.get("start") is not None],
    }
    omitted = [SECTION_NAMES[s] for s in ALL_SECTIONS if s not in wanted]
    data = ReportData(_header(ctx, site, win, source_label, omitted, warnings, live, downs), sections, site,
                      win, scope.share, charts)
    if scope.share:
        _apply_sharing(data)
    known = redact.known_secrets(cfg)
    data.sections = redact.scrub_tree(data.sections, known)
    data.header = redact.scrub_tree(data.header, known)
    data.charts = _scrub_charts(data.charts, known)
    return data


def _scrub_charts(charts: dict, known: tuple[str, ...] = ()) -> dict:
    specs = list(charts.get("env", []))
    if isinstance(charts.get("leakage"), dict):
        specs.append(charts["leakage"])
    for s in specs:
        s["title"] = redact.scrub(s.get("title", ""), known)
        for line in s.get("lines", []):
            line["label"] = redact.scrub(line.get("label", ""), known)
    spl = charts.get("spl")
    if isinstance(spl, dict):
        for ser in spl.get("series", []):
            ser["label"] = redact.scrub(ser.get("label", ""), known)
    return charts


def _wanted_sections(scope: ReportScope) -> set[str]:
    if scope.sections is None:
        base = set(ALL_SECTIONS)
        if scope.share:
            base -= set(SHARING_DEFAULT_OFF)
        return base
    unknown = set(scope.sections) - set(ALL_SECTIONS)
    if unknown:
        raise ReportError("bad_sections", "Unknown section.")
    if scope.share and set(scope.sections) & set(SHARING_DEFAULT_OFF):
        raise ReportError("share_sections", "A Sharing excerpt never includes system events or the running order.")
    return set(scope.sections)


# ------------------------------------------------------------------ labels
def _label(ctx: _Ctx, entity_id: str) -> tuple[str, str]:
    m = ctx.meta.get(entity_id, {})
    dev = m.get("device_name") or ""
    name = m.get("name") or ""
    label = f"{dev} {name}".strip() if dev and name and name.lower() not in dev.lower() else (dev or name or entity_id)
    return label, m.get("area", "")


def ack_label(src: str, cfg) -> str:
    src = (src or "").strip()
    if src == "admin":
        return "Admin"
    if src.startswith("dashboard:"):
        slug = src.split(":", 1)[1]
        for d in getattr(cfg, "dashboards", []) or []:
            if getattr(d, "slug", None) == slug and getattr(d, "title", ""):
                return f"Dashboard: {d.title}"
        return f"Dashboard: {slug}"
    return src.capitalize() if src else ""


def source_label(src: str, cfg) -> str:
    s = (src or "").strip()
    low = s.lower()
    if low == "admin":
        return "Admin"
    if low.startswith("dashboard:"):
        return ack_label(s, cfg)
    return {"alarm": "Alarm", "software": "Software", "updater": "Software", "spl": "SPL", "hub": "Stagewatch",
            "schedule": "Schedule", "calibration": "Calibration"}.get(low.split(":")[0], "Other")


# ------------------------------------------------------------------ alarms
def _alarms_all(ctx: _Ctx) -> dict:
    """Pair raise / ack / clear for the show, then cut to the range.  Returns alarms, device
    outages and system events.  Nothing from before the range start is revealed: an alarm already
    active shows no raise time, and an earlier acknowledge is only "acknowledged before this range"."""
    S, E = ctx.S, ctx.E
    rows = ctx.conn.execute("SELECT ts, alarm_id, event, level, message FROM alarm_log "
                            "WHERE show_id = ? AND ts <= ? ORDER BY id", (ctx.show_id, E))
    cur: dict[str, dict] = {}
    done: list[dict] = []
    system: list[dict] = []
    for ts, aid, ev, level, msg in rows:
        ctx.budget.tick()
        silent = str(msg).endswith(" (silent)")
        msg = str(msg)[:-9] if silent else str(msg)
        if aid in ("hub", "software"):
            if ts >= S:
                system.append({"ts": ts, "source": "Stagewatch" if aid == "hub" else "Software",
                               "event": ev, "message": msg})
            continue
        if aid == "actions" or aid.startswith("action:"):
            continue
        if ev == "raise":
            if aid in cur:
                done.append(cur.pop(aid))
            cur[aid] = {"id": aid, "level": level, "message": msg, "silent": silent, "raised": ts,
                        "before": ts < S, "cleared": None, "acks": [], "acked_before": False}
        elif ev == "clear":
            r = cur.pop(aid, None)
            if r is not None:
                r["cleared"] = ts
                done.append(r)
        elif ev.startswith("ack"):
            r = cur.get(aid)
            if r is not None:
                if ts >= S:
                    r["acks"].append((ts, ev[5:-1] if ev.endswith(")") else ""))
                else:
                    r["acked_before"] = True
        else:
            r = cur.get(aid)
            if r is not None:
                r["level"], r["message"], r["silent"] = level, msg, silent
    done.extend(cur.values())
    alarms, outages = [], []
    for r in done:
        ref = S if r["before"] else r["raised"]
        end, ended_by_run = r["cleared"], False
        if end is None:
            run_of = next((x for x in ctx.runs if x.started <= ref <= x.end), None)
            if run_of is None and ctx.runs and r["before"]:
                continue                    # the hub was off at the start: nothing was active then
            if run_of is not None and not run_of.live and run_of.end < E - 1.0:
                end, ended_by_run = run_of.end, True
        elif end < S:
            continue
        s0 = S if r["before"] else r["raised"]
        e0 = min(end if end is not None else E, E)
        out = {"id": r["id"], "level": r["level"], "level_name": LEVEL_NAMES.get(r["level"], "notice"),
               "message": r["message"], "silent": r["silent"], "start": s0, "raised": None if r["before"] else r["raised"],
               "already_active": r["before"], "cleared": r["cleared"] if r["cleared"] is not None and r["cleared"] <= E else None,
               "ended_by_run": ended_by_run, "end": e0, "duration_s": max(0.0, e0 - s0),
               "ack_ts": r["acks"][0][0] if r["acks"] else None,
               "ack_from": ack_label(r["acks"][0][1], ctx.cfg) if r["acks"] else "",
               "acked_before": r["acked_before"],
               "time_to_ack_s": (r["acks"][0][0] - r["raised"]) if r["acks"] and not r["before"] else None}
        (outages if r["id"].startswith("device:") else alarms).append(out)
    alarms.sort(key=lambda a: a["start"])
    outages.sort(key=lambda a: a["start"])
    return {"alarms": alarms, "outages": outages, "system": system}


def _end_text(a: dict) -> str:
    if a["cleared"] is not None:
        return "cleared"
    if a["ended_by_run"]:
        return "ended when Stagewatch stopped"
    return "still active at end of range"


def _alarms_section(ctx: _Ctx, alarms: list[dict]) -> dict:
    rows = []
    for a in alarms:
        rows.append({**a, "end_text": _end_text(a), "notice": a["silent"],
                     "level_text": "notice" if a["silent"] else a["level_name"]})
    by_level = {"advisory": 0, "alert": 0, "stop": 0, "notice": 0}
    never = 0
    for r in rows:
        by_level[r["level_text"]] = by_level.get(r["level_text"], 0) + 1
        if not r["silent"] and r["ack_ts"] is None and not r["acked_before"]:
            never += 1
    return {"rows": rows, "cap": ALARM_ROW_CAP, "by_level": by_level, "never_acked": never,
            "note": "'Admin' means anyone who used the admin PIN."}


def _devices_section(ctx: _Ctx, outages: list[dict]) -> dict:
    rows = []
    area_of = {m["device_id"]: m["area"] for m in ctx.meta.values() if m.get("area")}
    for o in outages:
        m = _DEVICE_MSG.match(o["message"])
        name, status, detail = (m.group(1), m.group(2), m.group(3) or "") if m else (o["message"], "", "")
        rows.append({"device": name, "area": area_of.get(o["id"][7:], ""), "start": o["start"], "end": o["end"],
                     "already_active": o["already_active"], "duration_s": o["duration_s"], "status": status,
                     "detail": detail, "end_text": _end_text(o), "device_id": o["id"][7:]})
    quiet = getattr(getattr(ctx.cfg, "device_errors", None), "quiet", None) or []
    return {"rows": rows, "quieted": [k.split(":", 1)[-1] for k in quiet][:200]}


# ------------------------------------------------------------------ downtime and gaps
def _downs(ctx: _Ctx) -> dict:
    S, E = ctx.S, ctx.E
    overlap = [r for r in ctx.runs if r.end > S and r.started < E]
    if overlap:
        periods = down_periods(ctx.runs, S, E)
        return {"fallback": False, "periods": periods}
    from .query import DownPeriod
    periods = [DownPeriod(a, b, "No readings at all. Stagewatch may have been off (not recorded for this day).",
                          "unknown") for a, b in silent_periods(ctx.conn, ctx.show_id, S, E)]
    return {"fallback": True, "periods": periods}


def _gaps(ctx: _Ctx, downs: dict, outages: list[dict]) -> dict:
    S, E = ctx.S, ctx.E
    out_down = []
    for d in downs["periods"]:
        note = ""
        if d.kind == "unclean":
            note = ("Up to 2 seconds of readings just before this point were never saved. After a power cut, "
                    "up to about the last minute can be lost.")
        elif d.kind == "restart":
            note = "Up to about the last minute before this point may not have been saved."
        out_down.append({"start": d.start, "end": d.end, "length_s": d.end - d.start,
                         "how_ended": d.how_ended, "kind": d.kind, "note": note})
    down_spans = [(d.start, d.end) for d in downs["periods"]]
    sensor = []
    for label, dev_id, a, b in ctx.sensor_gaps:
        for p0, p1 in subtract_spans([(a, b)], down_spans):
            if p1 - p0 < MIN_SENSOR_GAP_S:
                continue
            linked = any(o["id"] == f"device:{dev_id}" and o["start"] <= p1 and o["end"] >= p0 for o in outages)
            sensor.append({"sensor": label, "start": p0, "end": p1, "length_s": p1 - p0,
                           "outage": "Device was reported offline." if linked else ""})
    sensor.sort(key=lambda g: (g["start"], g["sensor"]))
    jumps = [{"ts": r.started} for r in ctx.runs if r.clock_jump and S <= r.started <= E]
    notes = []
    if downs["fallback"]:
        notes.append("This day has no record of when Stagewatch was running, so only stretches with no "
                     "readings at all are listed.")
    if jumps:
        notes.append("The computer's clock moved backwards when Stagewatch started. Times around then "
                     "may be out of order.")
    total_down = sum(b - a for a, b in merge_spans(down_spans))
    return {"down": out_down, "sensor": sensor, "clock_jumps": jumps, "notes": notes,
            "fallback": downs["fallback"], "total_down_s": total_down}


# ------------------------------------------------------------------ environment
def _stat_row(ctx: _Ctx, entity_id: str, scale: float, label: str | None = None) -> dict | None:
    st = ctx.series(entity_id)
    if st.n == 0:
        return None
    lab, area = _label(ctx, entity_id)
    return {"entity": entity_id, "label": label or lab, "area": area,
            "min": st.min * scale, "min_ts": st.min_ts, "max": st.max * scale, "max_ts": st.max_ts,
            "mean": None if st.mean is None else st.mean * scale, "coverage": st.coverage, "n": st.n,
            "interval_s": st.interval_s}


def _line(st: SeriesStats, scale: float) -> list[list[float]]:
    return [[t, m * scale, lo * scale, hi * scale] for t, m, lo, hi in st.buckets]


def _env(ctx: _Ctx, charts: dict) -> dict | None:
    kinds = []
    chart_list = []
    for kind, title, unit, dec, scale in ENV_KINDS:
        site_id = f"site.{kind}"
        site_row = _stat_row(ctx, site_id, scale, "Site average")
        sensor_rows, lines = [], []
        for eid, m in sorted(ctx.meta.items()):
            if m["kind"] != kind or eid.startswith("site.") or (m.get("doc") or {}).get("category") == "service":
                continue
            row = _stat_row(ctx, eid, scale)
            if row is None:
                continue
            sensor_rows.append(row)
            st = ctx.series(eid)
            lines.append({"label": row["label"], "points": _line(st, scale)})
            for a, b in st.gaps:
                ctx.sensor_gaps.append((row["label"], m["device_id"], a, b))
        if site_row is None and not sensor_rows:
            continue
        kinds.append({"kind": kind, "title": title, "unit": unit, "decimals": dec, "site": site_row,
                      "sensors": sensor_rows})
        chart_list.append({"kind": kind, "title": f"{title} ({unit})", "unit": unit,
                           "band": _line(ctx.series(site_id), scale) if site_row else [], "lines": lines[:8],
                           "more": max(0, len(lines) - 8)})
    charts["env"] = chart_list
    extra = {}
    dew = _stat_row(ctx, "site.dew_point", 1.0, "Site dew point")
    if dew:
        extra["dew_point"] = dew
    sos = _stat_row(ctx, "site.speed_of_sound", 1.0, "Site speed of sound")
    if sos:
        sos["travel_change_ms"] = (acoustics.travel_time_ms(ctx.distance_m, sos["min"])
                                   - acoustics.travel_time_ms(ctx.distance_m, sos["max"]))
        sos["distance_m"] = ctx.distance_m
        extra["speed_of_sound"] = sos
    if not kinds and not extra:
        return {"kinds": [], "extra": {}, "advice": ENV_ADVICE,
                "empty": "No temperature, humidity or pressure readings were recorded in this range."}
    return {"kinds": kinds, "extra": extra, "advice": ENV_ADVICE,
            "mean_note": "Mean is the average of one-minute averages, so uneven reading rates do not skew it."}


def _wind(ctx: _Ctx) -> dict | None:
    speed = [e for e, m in ctx.meta.items() if m["kind"] == "wind_speed"]
    gust = [e for e, m in ctx.meta.items() if m["kind"] == "wind_gust"]
    rows = []
    for eid in speed + gust:
        r = _stat_row(ctx, eid, 1.0)
        if r:
            r["gust"] = eid in gust
            r["unit"] = ctx.meta[eid].get("unit") or "m/s"
            rows.append(r)
            for a, b in ctx.series(eid).gaps:
                ctx.sensor_gaps.append((r["label"], ctx.meta[eid]["device_id"], a, b))
    if not rows:
        return None
    return {"rows": rows, "advice": WIND_ADVICE,
            "peak_note": "Gusts are the highest recorded sample, not a rolling 3 s gust."}


# ------------------------------------------------------------------ sound level limits
def _spl_rows(ctx: _Ctx) -> list[tuple]:
    """Every saved limit set for this show's event that was saved up to the end of the range:
    (rev, saved_at, entered_by, change_note, doc, show_id), oldest first.  Nothing saved after the
    range ends is read."""
    ev = ctx.win.show.get("event_id")
    if ev is None or "spl_limit_revisions" not in ctx.tables:
        return []
    return ctx.conn.execute(
        "SELECT rev, saved_at, entered_by, change_note, doc, show_id FROM spl_limit_revisions "
        "WHERE event_id = ? AND (show_id IS NULL OR show_id = ?) AND saved_at <= ? ORDER BY rev LIMIT ?",
        (ev, ctx.show_id, ctx.E, SPL_MAX_REVS)).fetchall()


def _in_force(rows: list[tuple], show_id: int, t: float, strict: bool = False):
    """The saved set in force at ``t``: this show's latest override, else the event's latest default
    (the same rule the live limits use; a cleared override, doc "null", falls back to the default).
    ``strict`` ignores a set saved at exactly ``t``.  None when there is none."""
    def ok(r) -> bool:
        return r[1] < t if strict else r[1] <= t
    mine = [r for r in rows if r[5] == show_id and ok(r)]
    if mine and mine[-1][4] not in (None, "", "null"):
        return mine[-1]
    base = [r for r in rows if r[5] is None and ok(r)]
    if base and base[-1][4] not in (None, "", "null"):
        return base[-1]
    return None


def _spl_steps(ctx: _Ctx, rows: list[tuple]) -> list[dict]:
    """The limit in force over the range, one series per (source, metric, kind): a list of steps
    [from, to, dB].  A time with no limit in force is simply not in a step (a gap).  Only limits are
    used: nothing measured is mixed in.  A set saved before the range gives its values, but its
    save time is never used."""
    from datetime import date
    try:
        from ..spl_limits import SplLimitSet, label_of, windows
    except Exception:  # noqa: BLE001
        return []
    S, E = ctx.S, ctx.E
    try:
        day = date.fromisoformat(str(ctx.win.day))
    except ValueError:
        return []
    cand = {r[0]: r for r in (_in_force(rows, ctx.show_id, S, True),) if r}
    for r in rows:
        if S <= r[1] <= E:
            cand[r[0]] = r
    wins: dict[int, list] = {}
    for rev, r in cand.items():
        if r[4] in (None, "", "null"):
            continue
        try:
            wins[rev] = windows(SplLimitSet.model_validate_json(r[4]), day, ctx.site)
        except Exception:  # noqa: BLE001 - a set we cannot read has no steps
            continue
    cuts = {S, E}
    for r in cand.values():
        if S < r[1] < E:
            cuts.add(r[1])
    for ws in wins.values():
        for a, b, _lim in ws:
            for t in (a, b):
                if S < t < E:
                    cuts.add(t)
    cuts_l = sorted(cuts)
    series: dict[tuple, dict] = {}
    for a, b in zip(cuts_l, cuts_l[1:]):
        ctx.budget.check()
        mid = (a + b) / 2.0
        row = _in_force(rows, ctx.show_id, mid)
        if row is None or row[0] not in wins:
            continue
        for wa, wb, lim in wins[row[0]]:
            if wa <= mid < wb:
                ser = series.get(lim.group)
                if ser is None:
                    name = " ".join(x for x in (lim.source, label_of(lim)) if x)
                    ser = series[lim.group] = {"label": name + (" (guidance)" if lim.kind == "guidance" else ""),
                                               "kind": lim.kind, "steps": []}
                if ser["steps"] and ser["steps"][-1][1] == a and ser["steps"][-1][2] == lim.value_db:
                    ser["steps"][-1][1] = b
                else:
                    ser["steps"].append([a, b, lim.value_db])
    return list(series.values())


def _spl(ctx: _Ctx, charts: dict | None = None) -> dict | None:
    rows = []
    for eid, m in sorted(ctx.meta.items()):
        if m["kind"] != "sound_level":
            continue
        r = _stat_row(ctx, eid, 1.0)
        if r is None:
            continue
        r["mean"] = None            # sound levels are never averaged
        r["labels"] = ", ".join(f"{v}" for v in ((m.get("doc") or {}).get("labels") or {}).values()) \
            if isinstance((m.get("doc") or {}).get("labels"), dict) else ""
        rows.append(r)
        for a, b in ctx.series(eid).gaps:
            ctx.sensor_gaps.append((r["label"], m["device_id"], a, b))
    revisions, at_start, steps = [], [], []
    rev_rows = _spl_rows(ctx)
    if rev_rows:
        first = _in_force(rev_rows, ctx.show_id, ctx.S, True)
        if first is not None:
            at_start = _limit_lines(first[4])
        for r in rev_rows:
            if r[1] >= ctx.S:
                revisions.append({"rev": r[0], "ts": r[1], "by": r[2], "note": r[3], "limits": _limit_lines(r[4])})
        steps = _spl_steps(ctx, rev_rows)
    if not rows and not revisions and not at_start and not steps:
        return None
    if charts is not None and steps:
        charts["spl"] = {"title": "Sound level limit (dB)", "series": steps[:SPL_MAX_SERIES],
                         "more": max(0, len(steps) - SPL_MAX_SERIES),
                         "revisions": [{"rev": r["rev"], "ts": r["ts"]} for r in revisions][:40]}
    return {"measured": rows, "revisions": revisions, "limits_at_start": at_start, "steps": steps,
            "advice": SPL_ADVICE,
            "peak_note": "Highest value is the highest recorded sample. Levels are shown as received.",
            "steps_note": "The limits as entered in Stagewatch, as they were in force at each time. "
                          "Where a line has a break, no limit was set.",
            "measured_note": "" if rows else "Measured SPL: not recorded. Stagewatch did not measure SPL."}


def _limit_lines(doc: str) -> list[str]:
    try:
        from ..spl_limits import SplLimitSet, label_of, fmt_db
        s = SplLimitSet.model_validate_json(doc)
        out = []
        for lim in s.limits:
            when = f" {lim.start or 'start'}-{lim.end or 'end'}" if lim.start or lim.end else ""
            out.append(f"{label_of(lim)}: {fmt_db(lim.value_db)} dB ({lim.kind}){when}")
        return out
    except Exception:  # noqa: BLE001 - a limit we cannot read is simply not listed
        return []


# ------------------------------------------------------------------ power
def _rating_for(ctx: _Ctx, m: dict) -> tuple[float | None, str]:
    """The amps this meter's own current reading is judged against: the lower of the breaker or fuse
    and the device limit, as set in Stagewatch NOW (not what was set on the day).  Only the meter's
    own per-circuit or per-phase current counts: calculated sums, neutral and totals have none, and a
    meter with several circuits never lends its own rating to one of them."""
    labels = (m.get("doc") or {}).get("labels")
    if not isinstance(labels, dict) or labels.get("field") != "current" or labels.get("derived") or labels.get("calculated"):
        return None, ""
    if (labels.get("group") or "") not in ("", "a", "b", "c"):
        return None, ""
    channel = labels.get("channel")
    dev = next((d for d in getattr(ctx.cfg, "shelly_devices", None) or [] if getattr(d, "id", None) == m["device_id"]), None)
    if dev is None or not isinstance(channel, str):
        return None, ""
    try:
        key = power_tree.single_key(dev)
        if key:
            if key != channel:
                return None, ""
            holder = power_tree.holder_of_meter(dev)
            return power_tree.effective_limit(holder.breaker_a, holder.rating_a)
        ch = next((c for c in getattr(dev, "channels", None) or [] if getattr(c, "key", None) == channel), None)
        if ch is None:
            return None, ""
        return power_tree.effective_limit(getattr(ch, "breaker_a", None), getattr(ch, "rating_a", None))
    except Exception:  # noqa: BLE001 - a setting we cannot read means no rating, never a guess
        return None, ""


def _load_state(pct: float, by: str) -> str:
    if pct >= LOAD_OVER_PCT:
        return {"breaker": "over the breaker or fuse rating", "device": "over the device limit"}.get(by, "over the rating")
    return "high load (80% or more)" if pct >= LOAD_HIGH_PCT else ""


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals)


def _leak_trend(st: SeriesStats, scale: float) -> dict | None:
    """Leakage creep: the mean of the first quarter of the readings against the mean of the last
    quarter, from the one-minute averages (time covered only).  None when there is too little to say."""
    b = st.buckets
    if len(b) < 2 * LEAK_MIN_BUCKETS:
        return None
    t0, t1 = b[0][0], b[-1][0]
    span = t1 - t0
    if span < LEAK_MIN_SPAN_S:
        return None
    early = [x[1] for x in b if x[0] < t0 + span / 4]
    late = [x[1] for x in b if x[0] >= t1 - span / 4]
    if len(early) < LEAK_MIN_BUCKETS or len(late) < LEAK_MIN_BUCKETS:
        return None
    e, l_ = _mean(early) * scale, _mean(late) * scale
    change = l_ - e
    guard = max(LEAK_ABS_MA, LEAK_REL * abs(e))
    word = "rising" if change >= guard else "falling" if change <= -guard else "steady"
    return {"word": word, "early": e, "late": l_, "change": change, "span_s": span}


def _generator(ctx: _Ctx) -> dict | None:
    """Generator size hint from the highest recorded site demand in the range (the demand figures the
    Power card records), with the loading limit and size typed in Stagewatch.  Nothing recorded, no row."""
    m = ctx.meta.get(GEN_DEMAND)
    if not m or m["kind"] != "apparent_power":
        return None
    st = ctx.series(GEN_DEMAND)
    if st.n == 0 or st.max is None or st.max <= 0:
        return None
    ps = getattr(ctx.cfg, "power_site", None)
    loading = float(getattr(ps, "gen_loading_pct", None) or 80.0)
    pf = float(getattr(ps, "gen_power_factor", None) or 0.8)
    size = getattr(ps, "gen_size_kva", None)
    window = getattr(ps, "demand_window_min", None) or 15
    peak_kva, basis = st.max / 1000.0, "total"
    names = [GEN_LINE_DEMAND.format(i) for i in (1, 2, 3)]
    if all(n in ctx.meta for n in names):
        lines = [ctx.series(n) for n in names]
        if all(x.n > 0 and x.max is not None for x in lines):
            three = 3 * max(x.max for x in lines) / 1000.0     # a generator gives only a third of its rating per line
            if three > peak_kva:
                peak_kva, basis = three, "line"
    min_kva = peak_kva / (loading / 100.0)
    out = {"peak_kva": peak_kva, "peak_ts": st.max_ts, "basis": basis, "loading_pct": loading, "pf": pf,
           "min_kva": min_kva, "min_kw": min_kva * pf,
           "next_kva": next((s for s in GEN_SIZES_KVA if s >= min_kva - 1e-9), None),
           "size_kva": None, "loaded_pct": None, "state": "", "window_min": window,
           "coverage": st.coverage, "interval_s": st.interval_s, "note": GEN_NOTE.format(w=window),
           "fuel_note": "Fuel used: not recorded. Stagewatch does not read generator fuel."}
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
        pct = 100.0 * peak_kva / size
        out.update(size_kva=float(size), loaded_pct=pct,
                   state="over" if pct >= 100 else "limit" if pct >= loading else "ok")
    return out


def _power(ctx: _Ctx, charts: dict | None = None) -> dict | None:
    cur, leak, vf, pw = [], [], [], []
    energy = []
    per_device_energy: dict[str, int] = {}
    leak_lines: list[dict] = []
    for eid, m in sorted(ctx.meta.items()):
        kind = m["kind"]
        if kind not in POWER_KINDS and kind != "energy":
            continue
        if kind == "energy":
            res = ctx.counter(eid)
            if res.n == 0:
                continue
            lab, _area = _label(ctx, eid)
            per_device_energy[m["device_id"]] = per_device_energy.get(m["device_id"], 0) + 1
            energy.append({"label": lab, "device_id": m["device_id"], "kwh": res.delta / 1000.0, "method": "meter counter",
                           "estimated": False, "resets": res.resets, "gap_s": res.gap_s})
            continue
        st = ctx.series(eid)
        if st.n == 0:
            continue
        lab, _area = _label(ctx, eid)
        is_leak = kind == "current" and (m["unit"] == "mA" or bool(_LEAK.search(lab)))
        scale = 1000.0 if is_leak and m["unit"] == "A" else 1.0     # leakage is shown in milliamps
        row = _stat_row(ctx, eid, scale)
        row["unit"] = "mA" if is_leak and m["unit"] in ("A", "mA") else (m["unit"] or POWER_KINDS[kind])
        row["kind"] = kind
        for a, b in st.gaps:
            ctx.sensor_gaps.append((row["label"], m["device_id"], a, b))
        if is_leak:
            row["trend"] = _leak_trend(st, scale) if m["unit"] in ("A", "mA") else None
            leak.append(row)
            leak_lines.append({"label": row["label"], "points": _line(st, scale)})
        elif kind == "current":
            limit, by = _rating_for(ctx, m)
            row.update(limit_a=None, limit_by="", max_pct=None, mean_pct=None, load_state="")
            if limit and row["unit"] == "A":
                row.update(limit_a=limit, limit_by=by, max_pct=100.0 * row["max"] / limit,
                           mean_pct=None if row["mean"] is None else 100.0 * row["mean"] / limit,
                           load_state=_load_state(100.0 * row["max"] / limit, by))
            cur.append(row)
        elif kind in ("voltage", "frequency"):
            vf.append(row)
        else:
            pw.append(row)
    have_counter = {e["device_id"] for e in energy}
    for eid, m in sorted(ctx.meta.items()):
        if m["kind"] == "power" and m["device_id"] not in have_counter:
            st = ctx.series(eid)
            if st.n:
                lab, _a = _label(ctx, eid)
                energy.append({"label": lab, "device_id": m["device_id"], "kwh": st.integral / 3600.0 / 1000.0,
                               "method": "estimated from power readings", "estimated": True, "resets": 0,
                               "gap_s": sum(b - a for a, b in st.gaps)})
    gen = _generator(ctx)
    if not (cur or leak or vf or pw or energy or gen):
        return None
    if charts is not None and leak_lines:
        charts["leakage"] = {"title": "Earth leakage (mA)", "band": [], "lines": leak_lines[:8],
                             "more": max(0, len(leak_lines) - 8),
                             "legend": "one line per measuring point"}
    multi = any(c > 1 for c in per_device_energy.values()) or len({e["device_id"] for e in energy}) != len(energy)
    total = None if multi else sum(e["kwh"] for e in energy)
    intervals = [r["interval_s"] for r in cur + leak + vf + pw if r.get("interval_s")]
    typical = sorted(intervals)[len(intervals) // 2] if intervals else None
    has_limit = any(r.get("limit_a") for r in cur)
    return {"current": cur, "leakage": leak, "voltage_frequency": vf, "other": pw, "energy": energy,
            "generator": gen, "total_kwh": total,
            "total_note": "Not added up: a meter has more than one counter." if multi and energy else "",
            "interval_s": typical, "advice": POWER_ADVICE,
            "limit_note": ("Percentages use the breaker, fuse or device limit as set in Stagewatch now, not as it "
                           "may have been on the day. The lower of the two is used. Only readings with a limit set "
                           "have a percentage.") if has_limit else "",
            "leak_note": ("Trend compares the average of the first quarter of the readings with the last quarter "
                          "(one-minute averages). It is a guide to spot leakage creeping up, and needs at least "
                          "30 minutes of readings.") if leak else "",
            "peak_note": ("Peaks are the highest recorded sample"
                          + (f"; readings were recorded about every {fmt.duration(typical)}" if typical else "")
                          + ". Shorter peaks can be missed.")}


# ------------------------------------------------------------------ markers and schedule
def _markers(ctx: _Ctx) -> list[dict]:
    cols = columns(ctx.conn, "markers")
    note = "note" if "note" in cols else "''"
    shown = " AND hidden = 0" if "hidden" in cols else ""          # hidden markers are never reported
    rows = ctx.conn.execute(f"SELECT id, ts, label, source, {note} FROM markers WHERE show_id = ? "
                            f"AND ts >= ? AND ts <= ?{shown} ORDER BY ts, id LIMIT ?",
                            (ctx.show_id, ctx.S, ctx.E, MAX_MARKERS + 1)).fetchall()
    if len(rows) > MAX_MARKERS:
        raise ReportError("too_big", "There are too many markers in that range to report on. Try a shorter range.")
    out = []
    first_c = prev_c = None
    for i, (mid, ts, label, source, _note) in enumerate(rows):
        ctx.budget.check()
        vals = {k: value_near(ctx.conn, ctx.show_id, f"site.{k}", ts, ctx.S)
                for k in ("temperature", "humidity", "speed_of_sound")}
        c = vals["speed_of_sound"]
        d_first = d_prev = None
        if c:
            if first_c is None:
                first_c = c
            if i > 0 and first_c:
                d_first = acoustics.travel_time_ms(ctx.distance_m, c) - acoustics.travel_time_ms(ctx.distance_m, first_c)
            if prev_c:
                d_prev = acoustics.travel_time_ms(ctx.distance_m, c) - acoustics.travel_time_ms(ctx.distance_m, prev_c)
            prev_c = c
        out.append({"n": i + 1, "id": mid, "ts": ts, "label": label, "source": source_label(source, ctx.cfg),
                    "temp_c": vals["temperature"], "rh": vals["humidity"],
                    "delta_first_ms": d_first, "delta_prev_ms": d_prev})
    return out


def _schedule(ctx: _Ctx) -> dict | None:
    if "schedule_items" not in ctx.tables:
        return None
    rows = ctx.conn.execute(
        "SELECT planned_start, planned_end, stage, kind, title FROM schedule_items WHERE show_id = ? "
        "AND planned_start <= ? AND COALESCE(planned_end, planned_start) >= ? ORDER BY planned_start, sort",
        (ctx.show_id, ctx.E, ctx.S)).fetchall()
    return {"rows": [{"start": r[0], "end": r[1], "stage": r[2], "kind": r[3], "title": r[4]} for r in rows]}


# ------------------------------------------------------------------ summary and header
def _summary(ctx: _Ctx, sections: dict, downs: dict, alarms_all: dict) -> dict:
    span = ctx.E - ctx.S
    down_s = sum(b - a for a, b in merge_spans([(d.start, d.end) for d in downs["periods"]]))
    env = sections.get("env") if isinstance(sections.get("env"), dict) else None
    ranges = {}
    for k in (env or {}).get("kinds", []):
        if k["site"]:
            ranges[k["kind"]] = {"min": k["site"]["min"], "max": k["site"]["max"], "unit": k["unit"],
                                 "decimals": k["decimals"]}
    al = sections.get("alarms") if isinstance(sections.get("alarms"), dict) else None
    wind = sections.get("wind") if isinstance(sections.get("wind"), dict) else None
    gust = max((r["max"] for r in (wind or {}).get("rows", []) if r.get("gust")), default=None)
    return {"coverage": max(0.0, 1.0 - down_s / span) if span > 0 else None,
            "coverage_known": not downs["fallback"], "down_count": len(downs["periods"]),
            "alarms": len(alarms_all["alarms"]), "alarms_by_level": (al or {}).get("by_level", {}),
            "never_acked": (al or {}).get("never_acked"), "outages": len(alarms_all["outages"]),
            "markers": len(sections["markers"]["rows"]) if isinstance(sections.get("markers"), dict)
            and "rows" in sections["markers"] else None,
            "ranges": ranges, "max_gust": gust, "spl_present": bool(sections.get("spl"))}


def _header(ctx: _Ctx, site, win: Window, source_label: str, omitted: list[str], warnings: list[str],
            live: bool, downs: dict) -> dict:
    S, E = win.start, win.end
    dst = dst_changes(site, S, E)
    notes = list(win.notes) + warnings
    if dst:
        notes.append("The clocks changed during this range. Times show the zone name so the repeated or "
                     "skipped hour is clear.")
    if downs["fallback"]:
        notes.append("This day was recorded before Stagewatch kept a record of when it was running.")
    if ctx.runs:
        zones = {str(r.doc.get("tz", "")) for r in ctx.runs if r.doc.get("tz") and r.end > S and r.started < E}
        if len(zones) > 1 or (zones and site.timezone and site.timezone not in zones):
            notes.append("The time zone setting changed while this was recorded. Times use the current setting.")
    off = sitetime.utc_offset_s(S, site)
    sign = "+" if off >= 0 else "-"
    utc = f"UTC{sign}{abs(off) // 3600:02d}:{abs(off) % 3600 // 60:02d}"
    status = _completeness(ctx, win, live, site)
    return {"site_name": getattr(getattr(ctx.cfg, "site", None), "name", "") or "Stagewatch",
            "event_name": win.show.get("event_name", ""), "show_name": win.show["name"], "day": win.day,
            "date_text": fmt.long_date(S, site), "range_start": S, "range_end": E,
            "range_text": f"{fmt.clock(S, site, dst)} to {fmt.clock(E, site, dst)}" if dst else
            f"{sitetime.local_hhmm(S, site)} to {sitetime.local_hhmm(E, site)}",
            "range_label": win.label, "tz": site.timezone or "this computer's zone", "utc": utc,
            "badge": "Full day" if win.full_day else "Excerpt", "completeness": status,
            "made_at": ctx.now, "version": __version__, "source": source_label, "omitted": omitted,
            "notes": notes, "dst": dst}


def _completeness(ctx: _Ctx, win: Window, live: bool, site) -> str:
    show = win.show

    def at(t: float) -> str:
        """" at HH:MM", but only for a time inside the chosen range."""
        return f" at {sitetime.local_hhmm(t, site)}" if win.start <= t <= win.end else ""

    last = ctx.runs[-1] if ctx.runs else None
    if win.end < (show["ended"] if show["ended"] is not None else ctx.now) - 1.0:
        return "Complete for this range"
    if show["ended"] is None:
        if live or (last is not None and last.live):
            return f"Partial: show still running, up to {sitetime.local_hhmm(win.end, site)}"
        if last is not None and not last.clean:
            return f"Partial: recording stopped unexpectedly{at(last.end)}"
        if last is not None:
            return f"Partial: Stagewatch was shut down{at(last.end)}"
        return f"Partial: show still open, up to {sitetime.local_hhmm(win.end, site)}"
    if last is not None and not last.clean and last.started <= win.end:
        return f"Partial: recording stopped unexpectedly{at(last.end)}"
    return "Complete"


# ------------------------------------------------------------------ sharing excerpt
def _apply_sharing(data: ReportData) -> None:
    """The Sharing defaults: markers show only time and label, no ack sources, no fault detail."""
    sec = data.sections
    al = sec.get("alarms")
    if isinstance(al, dict):
        for r in al.get("rows", []):
            r["ack_from"] = ""
        al["note"] = ""
        al["hide_ack_from"] = True
    mk = sec.get("markers")
    if isinstance(mk, dict) and "rows" in mk:
        for r in mk["rows"]:
            r.update(source="", temp_c=None, rh=None, delta_first_ms=None, delta_prev_ms=None)
        mk["minimal"] = True
    dv = sec.get("devices")
    if isinstance(dv, dict):
        for r in dv.get("rows", []):
            r["detail"] = ""
        dv["hide_detail"] = True
    spl = sec.get("spl")
    if isinstance(spl, dict):
        for r in spl.get("revisions", []):
            r["by"] = ""
            r["note"] = ""
