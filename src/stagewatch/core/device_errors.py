"""Device errors: for each device, how many problems it had in a show and when (plans/device-error-list.md).

Pure functions over rows the recorder hands over; nothing here writes anything. Two sources:

* Stagewatch's own devices: the show's ``device:<id>`` alarm lines (a device went offline or faulted).
* Imported LA Network Manager units: the ``device_log`` rows an import wrote, grouped by unit.

What counts. One *problem* is one raise, so a burst on many units or a flapping port is a count plus a
"flapping" tag, never forty errors. A device going offline is a **drop-out**; an imported entry counts by
its level: Critical = Stop, Major = Alert, Minor = Advisory. Not counted: Info lines and clears,
acknowledgements, threshold alarms (a hot stage is the environment, not a device fault), software
updates, session lines. A level that rises inside a problem keeps one problem at its highest level.

Time in fault is raise to clear. A problem still open when Stagewatch stopped ends there ("ended when
Stagewatch restarted": alarm state lives in memory, so a restart leaves a raise with no clear) and
nothing is assumed beyond the run: the time Stagewatch was not running is reported as a gap.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

FLAP_COUNT = 3                 # this many problems...
FLAP_WINDOW_S = 600.0          # ...inside this long is "flapping"
GAP_MIN_S = 30.0               # a break between runs shorter than the 30 s heartbeat is not worth reporting
EPISODES_SHOWN = 100           # timeline lines kept per device in a reply
LEVEL_WORD = {1: "Advisory", 2: "Alert", 3: "Stop"}
DEVICE_PREFIX = "device:"
LANWM_PREFIX = "lanwm:"
NEBRA_PREFIX = "nebra:"
NEBRA_COVER_S = 10.0           # a Nebra lock change this close to LA Network Manager's own is the same event
_LA_LINE = re.compile(r"^LA unit (\w{1,8}): (.+)$")
_NEBRA_LINE = re.compile(r"^Nebra unit (.{1,80}?): (.+)$")
_CLEARED = " CLEARED"


@dataclass
class Episode:
    start: float
    end: float | None          # None = no end known (an imported fault never cleared in the file)
    level: int                 # 1 advisory, 2 alert, 3 stop
    text: str
    dropout: bool = False      # a Stagewatch device going offline or faulting
    ended_by: str = "clear"    # clear | restart | open | show_end | file

    @property
    def seconds(self) -> float | None:
        return None if self.end is None else max(0.0, self.end - self.start)


@dataclass
class Subject:
    """One row's identity, kept apart from its problems so shows can be merged."""
    key: str
    name: str
    kind: str                  # a word for the Kind column
    now: str = ""              # the live status of a Stagewatch device; "imported" for an imported unit
    imported_from: set = field(default_factory=set)
    episodes: list[Episode] = field(default_factory=list)


# ---------------------------------------------------------------- the runs
def _run_end(run: dict, now: float, current_run_id: int | None) -> float:
    if run["id"] == current_run_id:
        return now
    return run["stopped"] if run["stopped"] is not None else run["last_seen"]


def _run_at(ts: float, runs: list[dict]) -> dict | None:
    """The run that was going when ``ts`` happened: the latest one that had started."""
    best = None
    for r in runs:
        if r["started"] <= ts + 1.0 and (best is None or r["started"] > best["started"]):
            best = r
    return best


def gaps(runs: list[dict], start: float, end: float, now: float, current_run_id: int | None) -> list[tuple[float, float]]:
    """The stretches of [start, end] when Stagewatch was not running (so nothing could be recorded)."""
    spans = sorted((r["started"], _run_end(r, now, current_run_id)) for r in runs)
    out: list[tuple[float, float]] = []
    for (_, a_end), (b_start, _) in zip(spans, spans[1:]):
        lo, hi = max(a_end, start), min(b_start, end)
        if hi - lo >= GAP_MIN_S:
            out.append((lo, hi))
    return out


# ------------------------------------------------------- Stagewatch devices
def _device_name(message: str) -> str:
    """A removed device's name, from the start of its alarm text ("Name: missing (...)")."""
    return message.split(": ", 1)[0].strip()[:80] if ": " in message else ""


def device_episodes(events: list[tuple], runs: list[dict], now: float, current_run_id: int | None,
                    limit_end: float) -> dict[str, tuple[str, list[Episode]]]:
    """device id -> (name from the alarm text, its problems), from one show's device alarm lines."""
    open_: dict[str, Episode] = {}
    out: dict[str, list[Episode]] = {}
    names: dict[str, str] = {}

    def close(dev: str, ep: Episode, end: float, how: str) -> None:
        ep.end, ep.ended_by = max(end, ep.start), how

    for ts, alarm_id, event, level, message in events:
        dev = alarm_id[len(DEVICE_PREFIX):]
        text = re.sub(r" \(silent\)$", "", message or "")
        names[dev] = _device_name(text) or names.get(dev, "")
        if event == "raise":
            prev = open_.pop(dev, None)
            if prev is not None:    # raised again with no clear between: Stagewatch restarted in between
                run = _run_at(prev.start, runs)
                close(dev, prev, _run_end(run, now, current_run_id) if run else ts, "restart")
            ep = Episode(ts, None, max(1, int(level)), text, dropout=True, ended_by="open")
            open_[dev] = ep
            out.setdefault(dev, []).append(ep)
        elif event == "change" and dev in open_:
            open_[dev].level = max(open_[dev].level, int(level))
            open_[dev].text = text
        elif event == "clear" and dev in open_:
            close(dev, open_.pop(dev), ts, "clear")
    for dev, ep in open_.items():
        run = _run_at(ep.start, runs)
        if run is None or run["id"] == current_run_id:
            if limit_end < now:
                close(dev, ep, limit_end, "show_end")
            else:
                ep.end, ep.ended_by = now, "open"
        else:
            close(dev, ep, _run_end(run, now, current_run_id), "restart")
    return {d: (names.get(d, ""), eps) for d, eps in out.items()}


# --------------------------------------------------------- imported LA units
def la_episodes(rows: list[tuple]) -> dict[str, tuple[set, list[Episode]]]:
    """LA unit -> (the file names it came from, its problems), from one show's imported rows."""
    open_: dict[tuple, Episode] = {}
    out: dict[str, list[Episode]] = {}
    files: dict[str, set] = {}
    for ts, source, msg, doc in rows:
        m = _LA_LINE.match(msg or "")
        if not m:
            continue                   # an action ("Standby All Units") or anything not about one unit
        unit, desc = m.group(1), m.group(2)
        try:
            d = json.loads(doc) if doc else {}
        except ValueError:
            d = {}
        cleared = bool(d.get("cleared")) or desc.endswith(_CLEARED)
        base = desc[:-len(_CLEARED)] if desc.endswith(_CLEARED) else desc
        level = int(d.get("stagewatch_level", 1) or 0)
        files.setdefault(unit, set()).add(source[len(LANWM_PREFIX):] if source.startswith(LANWM_PREFIX) else source)
        key = (unit, base)
        if cleared:
            ep = open_.pop(key, None)
            if ep is not None:
                ep.end, ep.ended_by = max(ts, ep.start), "clear"
        elif level >= 1 and key not in open_:
            ep = Episode(ts, None, min(level, 3), base, ended_by="file")
            open_[key] = ep
            out.setdefault(unit, []).append(ep)
    return {u: (files.get(u, set()), eps) for u, eps in out.items()}


def la_unlock_starts(la: dict[str, tuple[set, list[Episode]]]) -> dict[str, list[float]]:
    """LA unit number -> when its AVB stream unlock problems began (what Nebra's lock lines duplicate)."""
    out: dict[str, list[float]] = {}
    for unit, (_files, eps) in la.items():
        starts = [e.start for e in eps if e.text.upper().startswith("AVB") and "unlock" in e.text.lower()]
        if starts:
            out[unit.lower()] = starts
    return out


# ------------------------------------------------- imported Meyer Sound Nebra units
def nebra_episodes(rows: list[tuple], la_unlocks: dict[str, list[float]] | None = None
                   ) -> dict[str, tuple[set, list[Episode]]]:
    """Nebra unit name -> (the file names it came from, its problems), from one show's imported rows.
    A disconnect is an Alert until its reconnect; a lost stream lock is an Advisory until locked again.
    A lock problem that LA Network Manager's log already holds for the same unit (within NEBRA_COVER_S) is
    the same event and is not counted a second time, whichever file was imported first."""
    open_: dict[tuple, Episode] = {}
    out: dict[str, list[Episode]] = {}
    files: dict[str, set] = {}
    for ts, source, msg, doc in rows:
        m = _NEBRA_LINE.match(msg or "")
        if not m:
            continue                   # a client, a database update, or wording not seen before
        name = m.group(1)
        try:
            d = json.loads(doc) if doc else {}
        except ValueError:
            d = {}
        kind = d.get("kind", "")
        family = "lock" if kind in ("unlock", "lock") else "connection" if kind in ("disconnect", "reconnect") else ""
        if not family:
            continue
        files.setdefault(name, set()).add(source[len(NEBRA_PREFIX):] if source.startswith(NEBRA_PREFIX) else source)
        key = (name, family)
        if kind in ("lock", "reconnect"):
            ep = open_.pop(key, None)
            if ep is not None:
                ep.end, ep.ended_by = max(ts, ep.start), "clear"
        elif key not in open_:
            if family == "lock" and any(abs(ts - s) <= NEBRA_COVER_S for s in
                                        (la_unlocks or {}).get(re.split(r"[ .]", name.strip())[-1].lower(), ())):
                open_[key] = Episode(ts, None, 1, "", ended_by="file")        # swallow its clear; not counted
                continue
            level = int(d.get("stagewatch_level", 1) or 0) or (2 if kind == "disconnect" else 1)
            ep = Episode(ts, None, min(level, 3), "disconnected from the network" if kind == "disconnect"
                         else "stream unlocked", ended_by="file")
            open_[key] = ep
            out.setdefault(name, []).append(ep)
    return {n: (files.get(n, set()), eps) for n, eps in out.items()}


# ------------------------------------------------------------------- rows
def flapping(starts: list[float]) -> bool:
    s = sorted(starts)
    return any(s[i + FLAP_COUNT - 1] - s[i] <= FLAP_WINDOW_S for i in range(len(s) - FLAP_COUNT + 1))


def build_row(sub: Subject) -> dict:
    eps = sorted(sub.episodes, key=lambda e: e.start)
    stops = sum(1 for e in eps if not e.dropout and e.level >= 3)
    alerts = sum(1 for e in eps if not e.dropout and e.level == 2)
    advisories = sum(1 for e in eps if not e.dropout and e.level <= 1)
    dropouts = sum(1 for e in eps if e.dropout)
    last = eps[-1] if eps else None
    return {
        "key": sub.key, "name": sub.name, "kind": sub.kind, "now": sub.now,
        "stops": stops, "alerts": alerts, "advisories": advisories, "dropouts": dropouts,
        "problems": len(eps), "seconds_in_fault": round(sum(e.seconds or 0.0 for e in eps), 3),
        "never_cleared": sum(1 for e in eps if e.ended_by == "file"),
        "flapping": flapping([e.start for e in eps]),
        "last": None if last is None else {"ts": last.start, "level": "Offline" if last.dropout else LEVEL_WORD[last.level],
                                           "text": last.text},
        "imported_from": sorted(sub.imported_from)[:3],
        "episodes_total": len(eps),
        "episodes": [{"start": e.start, "end": e.end, "seconds": None if e.seconds is None else round(e.seconds, 3),
                      "level": "Offline" if e.dropout else LEVEL_WORD[e.level], "text": e.text, "ended_by": e.ended_by}
                     for e in eps[-EPISODES_SHOWN:]],
    }


def _order(row: dict) -> tuple:
    return (-row["stops"], -row["alerts"], -row["dropouts"], -row["advisories"], row["name"].lower())


def summarise(recorder, show_ids: list[int], devices: dict[str, dict], now: float,
              current_run_id: int | None = None) -> dict:
    """Rows for one show, or for several (an event's days) added together, plus the gaps when Stagewatch was
    not running. ``devices`` maps a device id to {"name", "kind", "status"} for the devices there are now."""
    subjects: dict[str, Subject] = {}
    for dev_id, d in devices.items():
        subjects[DEVICE_PREFIX + dev_id] = Subject(DEVICE_PREFIX + dev_id, d["name"], d["kind"], d.get("status", ""))
    runs = recorder.runs(500)
    all_gaps: list[tuple[float, float]] = []
    for sid in show_ids:
        show = recorder.show_row(sid)
        if show is None:
            continue
        limit_end = show["ended"] if show["ended"] is not None else now
        for dev, (name, eps) in device_episodes(recorder.device_alarm_events(sid), runs, now, current_run_id,
                                                limit_end).items():
            sub = subjects.setdefault(DEVICE_PREFIX + dev, Subject(DEVICE_PREFIX + dev, name or dev, "Removed device"))
            sub.episodes += eps
        la = la_episodes(recorder.vendor_log_rows(sid, LANWM_PREFIX))
        for name, (files, eps) in nebra_episodes(recorder.vendor_log_rows(sid, NEBRA_PREFIX),
                                                 la_unlock_starts(la)).items():
            sub = subjects.setdefault(NEBRA_PREFIX + name, Subject(
                NEBRA_PREFIX + name, name, "Meyer Sound (imported)", "imported"))
            sub.episodes += eps
            sub.imported_from |= files
        for unit, (files, eps) in la.items():
            sub = subjects.setdefault(LANWM_PREFIX + unit, Subject(
                LANWM_PREFIX + unit, f"LA unit {unit}", "L-Acoustics (imported)", "imported"))
            sub.episodes += eps
            sub.imported_from |= files
        all_gaps += gaps(runs, show["started"], limit_end, now, current_run_id)
    rows = sorted((build_row(s) for s in subjects.values()), key=_order)
    return {"rows": rows, "gaps": sorted(set(all_gaps)),
            "totals": {k: sum(r[k] for r in rows) for k in ("stops", "alerts", "advisories", "dropouts", "problems")}}
