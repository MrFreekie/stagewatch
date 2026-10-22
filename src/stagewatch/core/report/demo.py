"""A finished demo day for the show report (emulate mode only; the caller checks that).

Makes "Demo Festival", "Day 0 (yesterday)": about ten hours of three temperature nodes plus the site
figures, a seven-minute unclean stop at 21:40, a node that went missing for a while, one temperature
alert that was acknowledged and cleared, four markers and a doors/curfew schedule.  The values are
calculated from the clock time (no random numbers), so the same day always looks the same.  Nothing
here is used outside emulate mode.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import timedelta

from .. import sitetime

DEMO_EVENT = "Demo Festival"
DEMO_SHOW = "Day 0 (yesterday)"


def _wave(t: float, base: float, amp: float, period_h: float, phase: float = 0.0) -> float:
    return base + amp * math.sin(2 * math.pi * (t / 3600.0 / period_h) + phase)


def seed_demo_day(recorder, now: float, site=None) -> int:
    """Add the demo day and return its show id (an existing demo day is returned as it is)."""
    day = sitetime.show_day(now - 86400, site) if site is not None else None
    if day is None:
        from datetime import datetime
        day = (datetime.fromtimestamp(now) - timedelta(days=1)).date()

    def at(hhmm: str) -> float:
        return sitetime.resolve(day, hhmm, site) if site is not None else _local_ts(day, hhmm)

    start, data_end, gap_a, gap_b, end = at("12:00"), at("22:00"), at("21:40"), at("21:47"), at("22:15")
    db = sqlite3.connect(str(recorder.path), timeout=15.0)
    try:
        row = db.execute("SELECT id FROM shows WHERE name = ? AND day = ?", (DEMO_SHOW, day.isoformat())).fetchone()
        if row:
            return int(row[0])
        ev = db.execute("INSERT INTO events (name, created, ended) VALUES (?, ?, ?)", (DEMO_EVENT, start, end)).lastrowid
        show = db.execute("INSERT INTO shows (name, started, ended, event_id, day) VALUES (?, ?, ?, ?, ?)",
                          (DEMO_SHOW, start, end, ev, day.isoformat())).lastrowid
        nodes = [("demo.foh.temperature", "demo-foh", "FOH desk node", "FOH", 19.0, 0.0),
                 ("demo.stage_l.temperature", "demo-stage-l", "Stage left node", "Stage left", 21.0, 0.6),
                 ("demo.stage_r.temperature", "demo-stage-r", "Stage right node", "Stage right", 21.5, 1.2)]
        for eid, dev, dname, area, _b, _p in nodes:
            db.execute("INSERT OR REPLACE INTO entity_meta (entity_id, device_id, kind, unit, name, device_name, area, updated, doc) "
                       "VALUES (?,?,?,?,?,?,?,?,NULL)", (eid, dev, "temperature", "°C", "Temperature", dname, area, start))
        db.execute("INSERT OR REPLACE INTO entity_meta (entity_id, device_id, kind, unit, name, device_name, area, updated, doc) "
                   "VALUES ('demo.foh.humidity','demo-foh','humidity','%','Humidity','FOH desk node','FOH',?,NULL)", (start,))
        # Runs: ids below any real run, so the live hub's own run order is never disturbed.
        low = min(0, db.execute("SELECT COALESCE(MIN(id), 0) FROM hub_runs").fetchone()[0]) - 3
        doc = json.dumps({"tz": getattr(site, "timezone", "") or "", "demo": True})
        db.execute("INSERT INTO hub_runs (id, started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?,?)",
                   (low, start, gap_a, None, "power_or_restart", "demo", doc))
        db.execute("INSERT INTO hub_runs (id, started, last_seen, stopped, stop_reason, version, doc) VALUES (?,?,?,?,?,?,?)",
                   (low + 1, gap_b, end, end, "stop", "demo", doc))
        rows = []
        t = start
        while t < data_end:
            if not (gap_a <= t < gap_b):
                for eid, dev, _n, _a, base, ph in nodes:
                    if dev == "demo-stage-r" and at("18:20") <= t < at("18:35"):
                        continue          # this node went missing
                    rows.append((show, t, eid, round(_wave(t - start, base + (t - start) / 3600 * 0.25, 0.7, 2.3, ph), 2)))
            t += 5.0
        t = start
        while t < data_end:
            if not (gap_a <= t < gap_b):
                site_t = round(_wave(t - start, 20.5 + (t - start) / 3600 * 0.25, 0.7, 2.3, 0.6), 2)
                hum = round(_wave(t - start, 58.0, 6.0, 5.0, 1.0), 1)
                rows.append((show, t, "site.temperature", site_t))
                rows.append((show, t, "site.humidity", hum))
                rows.append((show, t, "demo.foh.humidity", round(hum + 1.5, 1)))
                rows.append((show, t, "site.speed_of_sound", round(331.3 * math.sqrt(1 + site_t / 273.15), 2)))
            t += 10.0
        db.executemany("INSERT INTO states (show_id, ts, entity_id, value) VALUES (?,?,?,?)", rows)
        alarms = [(at("18:20"), "device:demo-stage-r", "raise", 1, "Stage right node: missing"),
                  (at("18:35"), "device:demo-stage-r", "clear", 1, "Stage right node: missing"),
                  (at("20:15"), "demo.temp_high", "raise", 2, "Temperature high at stage left"),
                  (at("20:15") + 40, "demo.temp_high", "ack (dashboard:foh)", 2, "Temperature high at stage left"),
                  (at("20:32"), "demo.temp_high", "clear", 2, "Temperature high at stage left"),
                  (gap_b, "hub", "unclean_stop", 0, "Stagewatch restarted after an unexpected stop (down about 7 min)")]
        db.executemany("INSERT INTO alarm_log (show_id, ts, alarm_id, event, level, message) VALUES (?,?,?,?,?,?)",
                       [(show, *a) for a in alarms])
        markers = [(at("15:30"), "Line check", "admin"), (at("17:00"), "Doors", "schedule"),
                   (gap_b, "Stagewatch restarted after an unexpected stop (down about 7 min)", "hub"),
                   (at("20:30"), "Headliner on", "dashboard:foh"), (at("22:00"), "Curfew", "schedule")]
        db.executemany("INSERT INTO markers (show_id, ts, label, source) VALUES (?,?,?,?)",
                       [(show, *m) for m in markers])
        for kind, title, hhmm in (("doors", "Doors", "17:00"), ("curfew", "Curfew", "22:00")):
            db.execute("INSERT INTO schedule_items (show_id, kind, title, planned_start, updated) VALUES (?,?,?,?,?)",
                       (show, kind, title, at(hhmm), now))
        db.commit()
        return int(show)
    finally:
        db.close()


def _local_ts(day, hhmm: str) -> float:
    from datetime import datetime
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m).timestamp()
