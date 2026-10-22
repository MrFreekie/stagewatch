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
from .query import (HOLD_S, ReportError, ReportScope, SeriesStats, Window, columns, counter_delta,
                    down_periods, dst_changes, entity_meta, is_deadline, merge_spans, read_rows,
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
    def __init__(self, conn, cfg, site, win: Window, scope: ReportScope, now: float, live: bool) -> None:
        self.conn, self.cfg, self.site, self.win, self.scope, self.now = conn, cfg, site, win, scope, now
        self.S, self.E, self.show_id = win.start, win.end, win.show["id"]
        self.meta = entity_meta(conn)
        self.runs = run_spans(conn, self.show_id, now, live)
        self._series: dict[str, tuple[list, SeriesStats]] = {}
        self.sensor_gaps: list[tuple[str, str, float, float]] = []   # label, device_id, a, b
        self.tables = tables(conn)
        self.distance_m = float(getattr(getattr(cfg, "site", None), "reference_distance_m", 30.0) or 30.0)

    def series(self, entity_id: str) -> tuple[list, SeriesStats]:
        if entity_id not in self._series:
            rows = read_rows(self.conn, self.show_id, entity_id, self.S, self.E)
            self._series[entity_id] = (rows, summarise(rows, self.S, self.E))
        return self._series[entity_id]


# ------------------------------------------------------------------ entry point
def build_report(conn, cfg, scope: ReportScope, now: float, source_label: str = "this hub",
                 live: bool = False) -> ReportData:
    """Make the report for ``scope``.  ``conn`` comes from ``query.open_readonly``.  ``live`` says the
    newest hub run is still going (the running hub is making the report)."""
    cfg_site = getattr(cfg, "site", None)
    warnings: list[str] = []
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
    ctx = _Ctx(conn, cfg, site, win, scope, now, live)
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
        run("spl", lambda: _spl(ctx))
    if "power" in wanted:
        run("power", lambda: _power(ctx))
    if "alarms" in wanted and "alarms" not in sections:
        sections["alarms"] = _alarms_section(ctx, alarms_all["alarms"])
    if "devices" in wanted and "devices" not in sections:
        sections["devices"] = _devices_section(ctx, alarms_all["outages"])
    markers = []
    if "markers" in wanted:
        try:
            markers = _markers(ctx)
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
    data.sections = redact.scrub_tree(data.sections)
    data.header = redact.scrub_tree(data.header)
    data.charts = _scrub_charts(data.charts)
    return data


def _scrub_charts(charts: dict) -> dict:
    for s in charts.get("env", []):
        s["title"] = redact.scrub(s.get("title", ""))
        for line in s.get("lines", []):
            line["label"] = redact.scrub(line.get("label", ""))
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
                            "WHERE show_id = ? AND ts <= ? ORDER BY id", (ctx.show_id, E)).fetchall()
    cur: dict[str, dict] = {}
    done: list[dict] = []
    system: list[dict] = []
    for ts, aid, ev, level, msg in rows:
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
    return {"rows": rows}


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
    rows, st = ctx.series(entity_id)
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
            st = ctx.series(eid)[1]
            lines.append({"label": row["label"], "points": _line(st, scale)})
            for a, b in st.gaps:
                ctx.sensor_gaps.append((row["label"], m["device_id"], a, b))
        if site_row is None and not sensor_rows:
            continue
        kinds.append({"kind": kind, "title": title, "unit": unit, "decimals": dec, "site": site_row,
                      "sensors": sensor_rows})
        chart_list.append({"kind": kind, "title": f"{title} ({unit})", "unit": unit,
                           "band": _line(ctx.series(site_id)[1], scale) if site_row else [], "lines": lines[:8],
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
            for a, b in ctx.series(eid)[1].gaps:
                ctx.sensor_gaps.append((r["label"], ctx.meta[eid]["device_id"], a, b))
    if not rows:
        return None
    return {"rows": rows, "advice": WIND_ADVICE,
            "peak_note": "Gusts are the highest recorded sample, not a rolling 3 s gust."}


# ------------------------------------------------------------------ sound level limits
def _spl(ctx: _Ctx) -> dict | None:
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
        for a, b in ctx.series(eid)[1].gaps:
            ctx.sensor_gaps.append((r["label"], m["device_id"], a, b))
    revisions, at_start = [], []
    if "spl_limit_revisions" in ctx.tables:
        ev = ctx.win.show.get("event_id")
        if ev is not None:
            rev_rows = ctx.conn.execute(
                "SELECT rev, saved_at, entered_by, change_note, doc FROM spl_limit_revisions "
                "WHERE event_id = ? AND (show_id IS NULL OR show_id = ?) AND saved_at <= ? ORDER BY saved_at, id",
                (ev, ctx.show_id, ctx.E)).fetchall()
            before = [r for r in rev_rows if r[1] < ctx.S]
            if before:
                at_start = _limit_lines(before[-1][4])
            for r in rev_rows:
                if r[1] >= ctx.S:
                    revisions.append({"rev": r[0], "ts": r[1], "by": r[2], "note": r[3], "limits": _limit_lines(r[4])})
    if not rows and not revisions and not at_start:
        return None
    return {"measured": rows, "revisions": revisions, "limits_at_start": at_start, "advice": SPL_ADVICE,
            "peak_note": "Highest value is the highest recorded sample. Levels are shown as received.",
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
def _power(ctx: _Ctx) -> dict | None:
    cur, leak, vf, pw = [], [], [], []
    energy = []
    per_device_energy: dict[str, int] = {}
    for eid, m in sorted(ctx.meta.items()):
        kind = m["kind"]
        if kind not in POWER_KINDS and kind != "energy":
            continue
        rows, st = ctx.series(eid)
        if st.n == 0:
            continue
        lab, _area = _label(ctx, eid)
        if kind == "energy":
            res = counter_delta(rows, ctx.S, ctx.E)
            per_device_energy[m["device_id"]] = per_device_energy.get(m["device_id"], 0) + 1
            energy.append({"label": lab, "device_id": m["device_id"], "kwh": res.delta / 1000.0, "method": "meter counter",
                           "estimated": False, "resets": res.resets, "gap_s": res.gap_s})
            continue
        row = _stat_row(ctx, eid, 1.0)
        row["unit"] = m["unit"] or POWER_KINDS[kind]
        row["kind"] = kind
        for a, b in st.gaps:
            ctx.sensor_gaps.append((row["label"], m["device_id"], a, b))
        if kind == "current" and (row["unit"] == "mA" or _LEAK.search(lab)):
            leak.append(row)
        elif kind == "current":
            cur.append(row)
        elif kind in ("voltage", "frequency"):
            vf.append(row)
        else:
            pw.append(row)
        if kind == "power" and m["device_id"] not in {e["device_id"] for e in energy}:
            pass
    have_counter = {e["device_id"] for e in energy}
    for eid, m in sorted(ctx.meta.items()):
        if m["kind"] == "power" and m["device_id"] not in have_counter:
            rows, st = ctx.series(eid)
            if st.n:
                lab, _a = _label(ctx, eid)
                energy.append({"label": lab, "device_id": m["device_id"], "kwh": st.integral / 3600.0 / 1000.0,
                               "method": "estimated from power readings", "estimated": True, "resets": 0,
                               "gap_s": sum(b - a for a, b in st.gaps)})
    if not (cur or leak or vf or pw or energy):
        return None
    multi = any(c > 1 for c in per_device_energy.values()) or len({e["device_id"] for e in energy}) != len(energy)
    total = None if multi else sum(e["kwh"] for e in energy)
    intervals = [r["interval_s"] for r in cur + leak + vf + pw if r.get("interval_s")]
    typical = sorted(intervals)[len(intervals) // 2] if intervals else None
    return {"current": cur, "leakage": leak, "voltage_frequency": vf, "other": pw, "energy": energy,
            "total_kwh": total, "total_note": "Not added up: a meter has more than one counter." if multi and energy else "",
            "interval_s": typical, "advice": POWER_ADVICE,
            "peak_note": ("Peaks are the highest recorded sample"
                          + (f"; readings were recorded about every {fmt.duration(typical)}" if typical else "")
                          + ". Shorter peaks can be missed.")}


# ------------------------------------------------------------------ markers and schedule
def _markers(ctx: _Ctx) -> list[dict]:
    cols = columns(ctx.conn, "markers")
    note = "note" if "note" in cols else "''"
    rows = ctx.conn.execute(f"SELECT id, ts, label, source, {note} FROM markers WHERE show_id = ? "
                            "AND ts >= ? AND ts <= ? ORDER BY ts, id", (ctx.show_id, ctx.S, ctx.E)).fetchall()
    out = []
    first_c = prev_c = None
    for i, (mid, ts, label, source, _note) in enumerate(rows):
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
        zones = {str(r.doc.get("tz", "")) for r in ctx.runs if r.doc.get("tz")}
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
    last = ctx.runs[-1] if ctx.runs else None
    if win.end < (show["ended"] if show["ended"] is not None else ctx.now) - 1.0:
        return "Complete for this range"
    if show["ended"] is None:
        if live or (last is not None and last.live):
            return f"Partial: show still running, up to {sitetime.local_hhmm(win.end, site)}"
        if last is not None and not last.clean:
            return f"Partial: recording stopped unexpectedly at {sitetime.local_hhmm(last.end, site)}"
        if last is not None:
            return f"Partial: Stagewatch was shut down at {sitetime.local_hhmm(last.end, site)}"
        return f"Partial: show still open, up to {sitetime.local_hhmm(win.end, site)}"
    if last is not None and not last.clean and last.started <= win.end:
        return f"Partial: recording stopped unexpectedly at {sitetime.local_hhmm(last.end, site)}"
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
