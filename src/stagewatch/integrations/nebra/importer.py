"""Import a Meyer Sound Nebra Events CSV into a show: the same preview-then-write path as the LA Network
Manager import (``lanwm.importer``), which this reuses for the plan, the show day, the zone, the refusals
and the write. Rows go to ``device_log`` (proto ``vendor_log``, source ``nebra:<file>``).

Time. The file has the Nebra computer's local time and no zone; ``tz`` says which (blank = the site's zone)
and ``offset_s`` corrects a wrong clock. Twice a year that local time is not one instant:

* Clocks go back (25 Oct 2026, 01:00-02:00 in London): a time in that hour happens twice. The file is
  oldest first, so the first pass through the hour is read as the first one, and a time that goes back
  into the hour is read as the second. The preview says how many times were in that hour.
* Clocks go forward: a time in the missing hour is read with the offset from before the change (as the rest
  of Stagewatch does). The preview says how many.

Nothing is assumed beyond the file: a disconnect with no reconnect stays open to the end of the file.
Lock changes that the LA Network Manager log already holds for the same unit within 10 s are left out so
the Device errors card never counts one event twice.
"""

from __future__ import annotations

import json
import zoneinfo
from datetime import datetime

from . import mapping, parse
from ..lanwm.importer import (  # noqa: F401  (re-exported: one set of refusals and helpers for every format)
    MAX_OFFSET_S, MAX_ROWS, EPISODES_SHOWN, MARKER_LABEL_MAX, ImportRefused, Plan, _show, _zone, clean_file_name,
    show_window)
from ...core import device_errors

APP = "Meyer Sound Nebra"
LANWM_PREFIX = device_errors.LANWM_PREFIX
NOTE = "Imported from the Meyer Sound Nebra log"


def resolve_times(events: list[parse.NebraEvent], zone: zoneinfo.ZoneInfo | None) -> tuple[list[float], int, int]:
    """File time -> UTC epoch for each event, in file order. Returns (epochs, ambiguous, nonexistent)."""
    out: list[float] = []
    ambiguous = nonexistent = 0
    prev: float | None = None
    for e in events:
        if zone is None:
            ts = e.when.replace(fold=0).timestamp()
        else:
            a, b = e.when.replace(tzinfo=zone, fold=0), e.when.replace(tzinfo=zone, fold=1)
            ta, tb = a.timestamp(), b.timestamp()
            if ta < tb:                                   # clocks went back: this time happens twice
                ambiguous += 1
                ts = ta if prev is None or ta >= prev else tb
            else:
                if ta > tb:                               # clocks went forward: this time does not exist
                    nonexistent += 1
                ts = ta
        out.append(ts)
        prev = ts
    return out, ambiguous, nonexistent


def _la_unlock_starts(recorder, show_id: int) -> dict[str, list[float]]:
    """unit number -> when LA Network Manager's imported log raised a stream unlock on it."""
    out: dict[str, list[float]] = {}
    for unit, (_files, eps) in device_errors.la_episodes(recorder.vendor_log_rows(show_id, LANWM_PREFIX)).items():
        starts = [e.start for e in eps if "unlock" in e.text.lower() and e.text.upper().startswith("AVB")]
        if starts:
            out[unit.lower()] = starts
    return out


def plan_import(recorder, site, *, text: str, file_name: str, show_id: int, tz: str = "", offset_s: float = 0.0,
                session_markers: bool = False, parsed: parse.ParseResult | None = None) -> Plan:
    """Read the file and work out what an import would do. Writes nothing."""
    if not -MAX_OFFSET_S <= offset_s <= MAX_OFFSET_S:
        raise ImportRefused("The clock offset must be within 24 hours.")
    show = _show(recorder, show_id)
    zone = _zone(tz, site)
    parsed = parsed if parsed is not None else parse.parse_text(text)
    if not parsed.events:
        raise ImportRefused("No Nebra events were found in that file.")
    if parsed.truncated or len(parsed.events) > MAX_ROWS:
        raise ImportRefused("That file has too many entries to import at once.")

    window = show_window(show, site)
    source = f"nebra:{clean_file_name(file_name)}"
    plan = Plan("nebra", source, show_id, window, entries=len(parsed.events),
                duplicates_in_file=parsed.duplicates, skipped_lines=parsed.skipped, closed=True)
    epochs, ambiguous, nonexistent = resolve_times(parsed.events, zone)
    corrected = [t + offset_s for t in epochs]
    plan.first, plan.last = min(corrected), max(corrected)
    if ambiguous:
        plan.notes.append(f"{ambiguous} {'time' if ambiguous == 1 else 'times'} fell in the hour when the clocks went back, "
                          "so each could be read two ways. They are read in file order: the first pass through "
                          "that hour first, then the second.")
    if nonexistent:
        plan.notes.append(f"{nonexistent} {'time' if nonexistent == 1 else 'times'} fell in the hour that does not exist "
                          "when the clocks go forward. They are read with the offset from before the change.")
    if parsed.unknown:
        plan.notes.append(f"{parsed.unknown} {'line' if parsed.unknown == 1 else 'lines'} had wording Stagewatch does not "
                          "know. They are kept as information and counted as no fault.")

    present = recorder.vendor_log_keys(show_id, "nebra:")
    items: list[tuple[parse.NebraEvent, float, float]] = []      # (event, file time as UTC, corrected)
    for e, dts, ts in zip(parsed.events, epochs, corrected):
        if e.kind in ("db_start", "db_done") and not session_markers:
            plan.session_left_out += 1
            continue
        if not window[0] <= ts < window[1]:
            plan.outside_day += 1
            continue
        items.append((e, dts, ts))

    eps = mapping.episodes([(e, ts) for e, _dts, ts in items])
    # Already in the show from an earlier import of this file (or an overlapping one).
    fresh_ids: set[int] = set()
    for i, (e, dts, _ts) in enumerate(items):
        if (mapping.line(e), round(dts * 1000)) in present:
            plan.already_present += 1
        else:
            fresh_ids.add(i)
    index = {id(e_ts[0]): i for i, e_ts in enumerate(items)}      # event object -> position

    # Lock changes LA Network Manager already logged: the same event, so not counted twice.
    la = _la_unlock_starts(recorder, show_id)
    covered_ids: set[int] = set()
    for ep in eps:
        if ep.family != "lock":
            continue
        starts = la.get(mapping.unit_token(ep.name), ())
        if any(abs(ep.start - s) <= mapping.LANWM_COVER_S for s in starts):
            ep.covered = True
            covered_ids.update(index[id(ev)] for ev, _ in ep.events)
    plan.covered_by_lanwm = len(covered_ids & fresh_ids)
    fresh_ids -= covered_ids
    if plan.covered_by_lanwm:
        plan.notes.append(f"{plan.covered_by_lanwm} lock changes are already covered by the LA Network Manager log "
                          "and were left out, so nothing is counted twice.")

    for i in sorted(fresh_ids):
        e, dts, ts = items[i]
        lvl = mapping.level_of(e)
        plan.rows.append((ts, dts, mapping.SYSLOG_SEVERITY[lvl], APP, mapping.line(e), json.dumps({
            "level": e.level, "stagewatch_level": lvl, "unit": e.name, "kind": e.kind,
            "cleared": e.clearing, "client": e.client})))

    shown = [ep for ep in eps if not ep.covered]
    plan.open_episodes = sum(1 for ep in shown if ep.end is None)
    for ep in shown:
        plan.episodes.append({
            "unit": f"Nebra unit {ep.name}", "what": ep.description, "level": mapping.LEVEL_NAMES[ep.severity],
            "start": ep.start, "seconds": None if ep.end is None else round(ep.end - ep.start, 3),
            "note": "no reconnect in the file" if ep.end is None and ep.kind == "disconnect"
            else "not locked again in the file" if ep.end is None else ""})

    # Markers come only from entries that are new, so an overlapping import adds none twice.
    fresh_events = {id(items[i][0]) for i in fresh_ids}
    new_eps = [ep for ep in shown if id(ep.events[0][0]) in fresh_events]
    for b in mapping.bursts([ep for ep in new_eps if ep.severity >= 2]):
        plan.markers.append((b.start, b.label()[:MARKER_LABEL_MAX], NOTE))
    if session_markers:
        for i in sorted(fresh_ids):
            e, _dts, ts = items[i]
            if e.kind in ("db_start", "db_done"):
                plan.markers.append((ts, ("Nebra database update started" if e.kind == "db_start"
                                          else "Nebra database update finished"), NOTE))
    plan.markers.sort(key=lambda m: m[0])
    return plan
