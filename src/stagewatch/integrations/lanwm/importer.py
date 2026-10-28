"""Import a vendor log file into a show: preview it, then write it, or take it back out.

First format: L-Acoustics Network Manager (``lanwm``). Every entry is stored in ``device_log`` (proto
``vendor_log``) and the serious ones, plus engineer actions such as "Standby All Units", become timeline
markers. Nothing is guessed: entries outside the show day are listed and left out, never moved, and the
file's own time is kept beside the corrected one.

Time: the file has the laptop's local time and no zone. ``tz`` says which zone it was in (blank = the
site's zone) and ``offset_s`` corrects a laptop clock that was wrong. Both are the caller's to set; the
preview shows the file's span beside the show day so a mistake is visible before anything is written.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import zoneinfo
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import mapping, parse
from ...core import sitetime
from ...core.config import valid_timezone
from ...core.recorder import Recorder

FORMATS = ("lanwm", "nebra")
MAX_ROWS = 50_000               # entries kept from one file
MAX_OFFSET_S = 24 * 3600
APP = "LA Network Manager"
MARKER_LABEL_MAX = 120
EPISODES_SHOWN = 200
_NAME_BAD = re.compile(r"[^A-Za-z0-9._ -]")


class ImportRefused(ValueError):
    """The file or settings cannot be imported. The message is safe to show: it never repeats file text."""


def clean_file_name(name: str | None) -> str:
    """A short label for where the rows came from: the file name's characters kept to a safe set."""
    name = _NAME_BAD.sub("", (name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip()
    return name[:60] or "log"


@dataclass
class Plan:
    fmt: str
    source: str
    show_id: int
    window: tuple[float, float]             # the show day, UTC epoch
    first: float | None = None              # the file's span, corrected, UTC epoch
    last: float | None = None
    entries: int = 0                        # in the file (after exact repeats were folded)
    duplicates_in_file: int = 0
    skipped_lines: int = 0
    session_left_out: int = 0
    already_present: int = 0
    outside_day: int = 0
    closed: bool = False
    rows: list[tuple] = field(default_factory=list)
    markers: list[tuple[float, str, str]] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    open_episodes: int = 0
    covered_by_lanwm: int = 0               # Nebra lines that LA Network Manager's own log already holds
    notes: list[str] = field(default_factory=list)      # plain-words things to know before importing

    def summary(self) -> dict:
        return {
            "format": self.fmt, "source": self.source, "show_id": self.show_id,
            "file": {"first": self.first, "last": self.last, "entries": self.entries,
                     "repeats_folded": self.duplicates_in_file, "lines_not_read": self.skipped_lines,
                     "closed": self.closed},
            "show_day": {"from": self.window[0], "to": self.window[1]},
            "new": len(self.rows), "already_present": self.already_present,
            "outside_day": self.outside_day, "session_left_out": self.session_left_out,
            "markers": len(self.markers), "open_episodes": self.open_episodes,
            "covered_by_lanwm": self.covered_by_lanwm, "notes": self.notes,
            "episodes": self.episodes[:EPISODES_SHOWN], "episodes_total": len(self.episodes),
        }


def parse_for(fmt: str, text: str):
    """Read a file's text in the chosen format. Pure and slow on a big file, so callers run it off the loop."""
    if fmt == "nebra":
        from ..nebra import parse as nebra_parse
        return nebra_parse.parse_text(text)
    return parse.parse_text(text)


def detect_format(text: str) -> str | None:
    """Which log format a file's text is, by its content: ``lanwm``, ``nebra``, ``nebra-status`` or None."""
    head = text[:4096].lstrip("﻿")
    if "<ITEM" in head or head.lstrip().startswith("<DATA"):
        return "lanwm"
    from ..nebra import parse as nebra_parse, status as nebra_status
    if nebra_parse.looks_like_events(head):
        return "nebra"
    if nebra_status.looks_like_status(head):
        return "nebra-status"
    return None


def _zone(tz: str, site) -> zoneinfo.ZoneInfo | None:
    try:
        name = valid_timezone(tz)
    except ValueError:
        raise ImportRefused("That time zone is not one this computer knows.") from None
    return zoneinfo.ZoneInfo(name) if name else sitetime.zone(site)


def _epoch(naive: datetime, zone: zoneinfo.ZoneInfo | None) -> float:
    """The file's wall-clock time as UTC epoch. A time in the clocks-back hour takes the first one."""
    naive = naive.replace(fold=0)
    return naive.timestamp() if zone is None else naive.replace(tzinfo=zone).timestamp()


def show_window(show: dict, site) -> tuple[float, float]:
    """UTC epoch start and end of the show's day (rollover to rollover)."""
    day = show.get("day") or sitetime.show_day(show["started"], site).isoformat()
    rollover = (getattr(site, "day_rollover", "") or "06:00")
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return sitetime.resolve(day, rollover, site), sitetime.resolve(nxt, rollover, site)


def _show(recorder: Recorder, show_id: int) -> dict:
    for s in recorder.shows():
        if s["id"] == show_id:
            return s
    raise ImportRefused("That show does not exist.")


def plan_import(recorder: Recorder, site, *, fmt: str, text: str, file_name: str, show_id: int,
                tz: str = "", offset_s: float = 0.0, session_markers: bool = False,
                load_checker_markers: bool = False, parsed: parse.ParseResult | None = None) -> Plan:
    """Read the file and work out what an import would do. Writes nothing. ``parsed`` lets a caller do
    the (slow, pure) reading off the event loop and pass it in; ``text`` is then ignored."""
    if fmt not in FORMATS:
        raise ImportRefused("That log format is not supported.")
    if fmt == "nebra":
        from ..nebra import importer as nebra_importer
        return nebra_importer.plan_import(recorder, site, text=text, file_name=file_name, show_id=show_id, tz=tz,
                                          offset_s=offset_s, session_markers=session_markers, parsed=parsed)
    if not -MAX_OFFSET_S <= offset_s <= MAX_OFFSET_S:
        raise ImportRefused("The clock offset must be within 24 hours.")
    show = _show(recorder, show_id)
    zone = _zone(tz, site)
    parsed = parsed if parsed is not None else parse.parse_text(text)
    if not parsed.events:
        raise ImportRefused("No LA Network Manager entries were found in that file.")
    if parsed.truncated or len(parsed.events) > MAX_ROWS:
        raise ImportRefused("That file has too many entries to import at once.")

    window = show_window(show, site)
    source = f"lanwm:{clean_file_name(file_name)}"
    plan = Plan(fmt, source, show_id, window, entries=len(parsed.events),
                duplicates_in_file=parsed.duplicates, skipped_lines=parsed.skipped, closed=parsed.closed)
    present = recorder.vendor_log_keys(show_id, "lanwm:")

    def aligned(e: parse.LaEvent) -> tuple[float, float]:
        dts = _epoch(e.when, zone)
        return dts, dts + offset_s

    first_last = [aligned(e)[1] for e in parsed.events]
    plan.first, plan.last = min(first_last), max(first_last)

    fresh: list[parse.LaEvent] = []
    in_day: list[parse.LaEvent] = []
    for e in parsed.events:
        if not mapping.keep(e, session_markers, load_checker_markers):
            plan.session_left_out += 1
            continue
        dts, ts = aligned(e)
        if not window[0] <= ts < window[1]:
            plan.outside_day += 1
            continue
        in_day.append(e)
        msg = mapping.line(e)
        if (msg, round(dts * 1000)) in present:
            plan.already_present += 1
            continue
        fresh.append(e)
        plan.rows.append((ts, dts, mapping.SYSLOG_SEVERITY.get(e.level, 4), APP, msg, json.dumps({
            "level": e.level, "stagewatch_level": mapping.severity(e), "unit": e.unit,
            "category": e.category, "cleared": e.cleared, "acknowledged": e.acked})))

    eps = mapping.episodes(in_day)
    plan.open_episodes = sum(1 for ep in eps if ep.end is None)
    for ep in eps:
        start = aligned_ts(ep.start, zone, offset_s)
        plan.episodes.append({
            "unit": mapping.unit_name(ep.unit), "what": ep.description, "level": mapping.LEVEL_NAMES[ep.severity],
            "start": start, "seconds": None if ep.end is None
            else round((ep.end - ep.start).total_seconds(), 3)})

    # Markers come only from entries that are new, so an overlapping import adds none twice.
    new_eps = mapping.episodes(fresh)
    for b in mapping.bursts([ep for ep in new_eps if ep.severity >= 2]):
        plan.markers.append((aligned_ts(b.start, zone, offset_s), b.label()[:MARKER_LABEL_MAX], "Imported from the LA Network Manager log"))
    for e in fresh:
        if mapping.is_action(e) or (e.unit == parse.UNIT_SESSION and e.category.lower() == "session"):
            plan.markers.append((aligned_ts(e.when, zone, offset_s), e.description[:MARKER_LABEL_MAX],
                                 "Imported from the LA Network Manager log"))
    plan.markers.sort(key=lambda m: m[0])
    return plan


def aligned_ts(naive: datetime, zone: zoneinfo.ZoneInfo | None, offset_s: float) -> float:
    return _epoch(naive, zone) + offset_s


def apply_import(recorder: Recorder, plan: Plan) -> dict:
    """Write a plan as one import. Returns its id and counts."""
    if not plan.rows:
        raise ImportRefused("There is nothing new to import from that file.")
    import_id = f"{plan.fmt}-{int(time.time())}-{secrets.token_hex(3)}"
    recorder.add_vendor_import(plan.show_id, import_id, plan.source, plan.rows, plan.markers)
    return {"import_id": import_id, "rows": len(plan.rows), "markers": len(plan.markers)}
