"""Reader for the log file L-Acoustics Network Manager (LA NWM) writes: ``Log_YYYY_MM_DD_HHhMMmSS.xml``.

Pure functions, no I/O. The file is a ``<DATA>`` root with one self-closing ``<ITEM .../>`` per line, and
LA NWM writes it live, so while a session runs it has no closing ``</DATA>`` and the last line may be
cut short. So it is never read as one XML document: each line is matched on its own with a strict
attribute pattern. That needs no XML parser and has nothing to expand, so entity and DTD tricks have no
way in. Anything that does not look like an ``ITEM`` line is counted and skipped, never fatal.

``Time`` is the laptop's local time with no zone (``8 Aug 2026 09:14:13.830``), so it comes out here as a
naive ``datetime``; the caller says which zone it was in.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime

MAX_LINE = 4096                 # a longer line is not an LA NWM entry
MAX_ITEMS = 500_000             # one session is a few thousand; this only stops a runaway file
DUPLICATE_WINDOW_S = 0.05       # the same entry twice within 50 ms is one entry written twice
UNIT_SESSION = "---"            # LA NWM's Unit value for entries about the session, not about a unit
CLEARED_SUFFIX = " CLEARED"

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_ITEM = re.compile(r"^\s*<ITEM\s+(.*?)\s*/>\s*$")   # a line is at most MAX_LINE long, so this cannot run away
_ATTR = re.compile(r'([A-Za-z]{1,20})="([^"]{0,1000})"')
_TIME = re.compile(r"^(\d{1,2}) ([A-Za-z]{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f�]")   # controls, and what a bad character reference becomes
_LEVELS = ("Info", "Minor", "Major", "Critical")


@dataclass(frozen=True)
class LaEvent:
    when: datetime              # the file's own local time, no zone
    level: str                  # Info / Minor / Major / Critical (anything else is kept as written)
    unit: str                   # three-digit L-NET unit id, or "---" for the session
    category: str               # Session / Power / Network / ...
    description: str            # as written, " CLEARED" included
    acked: bool

    @property
    def cleared(self) -> bool:
        return self.description.endswith(CLEARED_SUFFIX)

    @property
    def base(self) -> str:
        """The description without " CLEARED": a raise and its clear share it."""
        return self.description[:-len(CLEARED_SUFFIX)] if self.cleared else self.description

    @property
    def key(self) -> tuple[str, str]:
        return (self.unit, self.base)


@dataclass
class ParseResult:
    events: list[LaEvent] = field(default_factory=list)
    skipped: int = 0            # lines that were not an entry (a cut-short last line counts here)
    duplicates: int = 0         # exact repeats dropped
    closed: bool = False        # the file ends with </DATA>: the session was closed cleanly
    truncated: bool = False     # more entries than MAX_ITEMS: the rest were not read

    @property
    def first(self) -> datetime | None:
        return min((e.when for e in self.events), default=None)

    @property
    def last(self) -> datetime | None:
        return max((e.when for e in self.events), default=None)


def parse_time(text: str) -> datetime | None:
    """``8 Aug 2026 09:14:13.830`` -> naive datetime. English month names by table (a locale-dependent
    strptime would fail on a German or French computer's names). None if it is not a real date."""
    m = _TIME.match(text.strip())
    if not m:
        return None
    day, mon, year, hh, mm, ss, frac = m.groups()
    month = _MONTHS.get(mon.lower())
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day), int(hh), int(mm), int(ss),
                        int((frac or "0").ljust(3, "0")) * 1000)
    except ValueError:
        return None


def _clean(value: str) -> str:
    return " ".join(_CONTROL.sub(" ", html.unescape(value)).split())


def parse_line(line: str) -> LaEvent | None:
    """One ``<ITEM .../>`` line, or None if it is not a complete, well-formed entry."""
    if len(line) > MAX_LINE:
        return None
    m = _ITEM.match(line)
    if not m:
        return None
    attrs = {k: v for k, v in _ATTR.findall(m.group(1))}
    when = parse_time(attrs.get("Time", ""))
    description = _clean(attrs.get("Description", ""))
    if when is None or not description:
        return None
    unit = _clean(attrs.get("Unit", "")) or UNIT_SESSION
    if unit != UNIT_SESSION and not (unit.isascii() and unit.isalnum() and len(unit) <= 8):
        return None
    level = _clean(attrs.get("Level", "")) or "Info"
    return LaEvent(when, level[:20], unit, _clean(attrs.get("Category", ""))[:40] or "Other",
                   description[:300], attrs.get("Acknowledged", "").upper() == "YES")


def parse_text(text: str) -> ParseResult:
    """Every entry in the file text, in file order, repeats within 50 ms dropped."""
    out = ParseResult()
    last_seen: dict[tuple, datetime] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "</DATA>":
            out.closed = True
            continue
        if stripped == "<DATA>" or stripped.startswith("<?xml"):
            continue
        event = parse_line(line)
        if event is None:
            out.skipped += 1
            continue
        if len(out.events) >= MAX_ITEMS:
            out.truncated = True
            break
        same = (event.level, event.unit, event.category, event.description)
        prev = last_seen.get(same)
        if prev is not None and abs((event.when - prev).total_seconds()) <= DUPLICATE_WINDOW_S:
            out.duplicates += 1
            continue
        last_seen[same] = event.when
        out.events.append(event)
    return out


def decode(raw: bytes) -> str:
    """File bytes -> text: UTF-8 (with or without a BOM), else UTF-16 if it has a BOM, else Latin-1."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")
