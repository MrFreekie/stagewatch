"""Site time (F4): the show site's time zone, show days and wall-clock times.

Pure functions, no I/O and no logging. The canonical form everywhere else stays
**UTC epoch seconds**; these helpers only convert to and from the site's wall clock.

Zone
    ``site.timezone`` is an IANA name (validated by ``config.valid_timezone``) or ``""``.
    ``""`` means "not set: use this computer's zone". Then the OS local zone is used through
    Python's own local-time support (``datetime.astimezone()`` / naive ``datetime.timestamp()``),
    which follows the same PEP 495 ``fold`` rules as ``zoneinfo``.

Show day
    A show day runs from ``day_rollover`` (``HH:MM``, 00:00-11:59) to the same time next
    morning. A show started at 01:30 with a 06:00 rollover belongs to the previous day.

Resolving a wall-clock time (``resolve``)
    * A time earlier than ``day_rollover`` belongs to ``day + 1`` (a 00:30 curfew is after
      midnight, on the next calendar date).
    * Spring-forward gap (the time doesn't exist): PEP 495 ``fold=0``, i.e. the offset in
      force *before* the change is used. 02:30 in a missing 02:00-03:00 hour resolves to the
      instant that displays as 03:30.
    * Fall-back overlap (the time happens twice): ``fold=0``, the first occurrence (the
      offset in force before the change, i.e. summer time).
"""

from __future__ import annotations

import zoneinfo
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Protocol


class _Site(Protocol):  # SiteConfig, or anything with these two fields
    timezone: str
    day_rollover: str


def zone(site: _Site) -> zoneinfo.ZoneInfo | None:
    """The site's zone, or None for "this computer's zone" (unset, or not loadable here)."""
    name = (getattr(site, "timezone", "") or "").strip()
    if not name:
        return None
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return None  # validated on load; only reachable if tzdata vanished afterwards


def _parse_hhmm(hhmm: str) -> dtime:
    h, _, m = (hhmm or "").strip().partition(":")
    if not (len(h) == 2 and len(m) == 2 and h.isascii() and m.isascii() and h.isdigit() and m.isdigit()):
        raise ValueError("time must be HH:MM")
    hour, minute = int(h), int(m)
    if hour > 23 or minute > 59:
        raise ValueError("time must be HH:MM")
    return dtime(hour, minute)


def _rollover(site: _Site) -> dtime:
    try:
        return _parse_hhmm(getattr(site, "day_rollover", "") or "06:00")
    except ValueError:
        return dtime(6, 0)


def _as_date(day: date | str) -> date:
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    return date.fromisoformat(day)


def local(ts: float, site: _Site) -> datetime:
    """The aware local datetime at ``ts`` on the site's wall clock."""
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(zone(site))


def show_day(started_ts: float, site: _Site, override: str | date | None = None) -> date:
    """The show day of a show started at ``started_ts``. ``override`` (YYYY-MM-DD) wins if set."""
    if override:
        return _as_date(override)
    lt = local(started_ts, site)
    d = lt.date()
    if lt.time().replace(tzinfo=None) < _rollover(site):
        d -= timedelta(days=1)
    return d


def resolve(day: date | str, hhmm: str, site: _Site) -> float:
    """UTC epoch seconds for the wall-clock time ``hhmm`` on show day ``day`` (see module doc)."""
    return wall_ts(resolve_date(day, hhmm, site), _parse_hhmm(hhmm), site)


def resolve_date(day: date | str, hhmm: str, site: _Site) -> date:
    """The calendar date ``resolve`` puts ``hhmm`` on: ``day``, or ``day + 1`` before the rollover."""
    d = _as_date(day)
    return d + timedelta(days=1) if _parse_hhmm(hhmm) < _rollover(site) else d


def wall_exists(d: date | str, t: dtime, site: _Site) -> bool:
    """False for a wall-clock time the clocks skip on that date (the spring-forward gap)."""
    d = _as_date(d)
    lt = local(wall_ts(d, t, site), site)
    return lt.date() == d and (lt.hour, lt.minute, lt.second) == (t.hour, t.minute, t.second)


def gap_end(d: date | str, t: dtime, site: _Site) -> dtime | None:
    """The first wall-clock minute at or after ``t`` on date ``d`` that exists (None if ``t``
    exists). Found from the zone's own rules, not assumed to be a whole hour."""
    d = _as_date(d)
    if wall_exists(d, t, site):
        return None
    start = datetime.combine(d, t.replace(tzinfo=None))
    for minutes in range(1, 24 * 60):
        cand = start + timedelta(minutes=minutes)
        if cand.date() != d:
            break
        if wall_exists(d, cand.time(), site):
            return cand.time()
    return None


def wall_ts(d: date | str, t: dtime, site: _Site) -> float:
    """UTC epoch seconds for wall-clock time ``t`` on calendar date ``d`` (no day rollover).
    Gap and overlap follow the same PEP 495 ``fold=0`` rules as ``resolve``."""
    naive = datetime.combine(_as_date(d), t.replace(tzinfo=None)).replace(fold=0)
    tz = zone(site)
    if tz is None:
        return naive.timestamp()  # naive = OS local time; PEP 495 fold=0 rules apply
    return naive.replace(tzinfo=tz).timestamp()


def local_hhmm(ts: float, site: _Site) -> str:
    """'HH:MM' (24-hour) on the site's wall clock."""
    return local(ts, site).strftime("%H:%M")


def utc_offset_s(ts: float, site: _Site) -> int:
    """The site's UTC offset at ``ts`` in seconds (east positive; BST = 3600)."""
    off = local(ts, site).utcoffset()
    return int(off.total_seconds()) if off is not None else 0


def ms_since_local_midnight(ts: float, site: _Site) -> int:
    """Milliseconds since 00:00 as the site's wall clock reads it (H*3600000 + M*60000 + ...).

    This is the wall-clock reading, not the elapsed time: on a DST change day it jumps with
    the clock, which is what clock sources such as Ontime report too.
    """
    lt = local(ts, site)
    return ((lt.hour * 60 + lt.minute) * 60 + lt.second) * 1000 + lt.microsecond // 1000


def time_block(site: _Site, now: float) -> dict:
    """The public ``time`` block for ``/api/info`` and ``snapshot().site``."""
    return {
        "timezone": (getattr(site, "timezone", "") or "").strip(),
        "utc_offset_s": utc_offset_s(now, site),
        "day_rollover": _rollover(site).strftime("%H:%M"),
    }
