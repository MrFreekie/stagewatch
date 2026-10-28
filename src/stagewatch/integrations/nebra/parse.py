"""Reader for the Events CSV that Meyer Sound Nebra exports: ``<date>_<time>_EventsReport.csv``.

Pure functions, no I/O. No header row; three fields separated by ``;``: ``Level;Message;Timestamp``.
Level is General, Warning or Error. The timestamp is the Nebra computer's local time with no zone
(``2026-10-24 13:50:16``), so it comes out here as a naive ``datetime`` and the caller says the zone.

Each line is read on its own with strict patterns. A line that does not fit is counted and skipped,
never fatal. A message of a shape not seen before is kept as written (cleaned of control characters),
never dropped. The text is never parsed as a spreadsheet, so a cell starting with ``=`` is only text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..lanwm.parse import decode  # noqa: F401  (same file-bytes reader: UTF-8, UTF-16, Latin-1)

MAX_LINE = 2000                 # a longer line is not a Nebra event
MAX_ITEMS = 500_000             # one session is a few hundred; this only stops a runaway file
NAME_MAX = 80
TEXT_MAX = 300
LEVELS = ("General", "Warning", "Error")
CLIENT_PREFIX = "Hive."         # a Nebra client app on another computer, not a device; the rest is a PC name

_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\x80-\x9f  �]")
_LEVEL_LINE = re.compile(r"^(General|Warning|Error);", re.IGNORECASE)

# message shape -> kind. The name is everything before the fixed words.
_SHAPES = (
    (re.compile(r"^(?P<name>.{1,200}?) is unlocked\.?$"), "unlock"),
    (re.compile(r"^(?P<name>.{1,200}?) is locked again\.?$"), "lock"),
    (re.compile(r"^(?P<name>.{1,200}?) has been disconnected from the Network\.?$"), "disconnect"),
    (re.compile(r"^(?P<name>.{1,200}?) has been reconnected to the Network\.?$"), "reconnect"),
)
_DB_UPDATE = re.compile(r"^Database update (in progress\.*|is done\.?)$", re.IGNORECASE)


@dataclass(frozen=True)
class NebraEvent:
    when: datetime              # the file's own local time, no zone
    level: str                  # General / Warning / Error (anything else kept as written, cut short)
    kind: str                   # unlock | lock | disconnect | reconnect | db_start | db_done | other
    name: str                   # the unit's name as Nebra shows it; "" for db_* and other
    client: bool                # the name is a Nebra client app (Hive.<PC>), not a device
    text: str                   # the cleaned message; for a client the PC name is replaced

    @property
    def raising(self) -> bool:
        return self.kind in ("unlock", "disconnect")

    @property
    def clearing(self) -> bool:
        return self.kind in ("lock", "reconnect")

    @property
    def family(self) -> str:
        """The two pairs: a stream lock (unlock/lock) or the network connection (disconnect/reconnect)."""
        return "lock" if self.kind in ("unlock", "lock") else "connection" if self.kind in ("disconnect", "reconnect") else ""


@dataclass
class ParseResult:
    events: list[NebraEvent] = field(default_factory=list)
    skipped: int = 0            # lines that were not an event
    duplicates: int = 0         # exact repeats (same level, message and second) dropped
    truncated: bool = False     # more events than MAX_ITEMS: the rest were not read
    unknown: int = 0            # events whose message shape is new: kept as information

    @property
    def first(self) -> datetime | None:
        return min((e.when for e in self.events), default=None)

    @property
    def last(self) -> datetime | None:
        return max((e.when for e in self.events), default=None)


def clean(value: str, limit: int = TEXT_MAX) -> str:
    return " ".join(_CONTROL.sub(" ", value).split())[:limit]


def parse_time(text: str) -> datetime | None:
    m = _TIME.match(text.strip())
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def classify(message: str) -> tuple[str, str, bool]:
    """(kind, name, is_client) for one message."""
    for pattern, kind in _SHAPES:
        m = pattern.match(message)
        if m:
            name = clean(m.group("name"), NAME_MAX)
            if not name:
                break
            return kind, name, name.startswith(CLIENT_PREFIX)
    if _DB_UPDATE.match(message):
        return ("db_done" if message.lower().rstrip(".").endswith("done") else "db_start"), "", False
    return "other", "", False


def parse_line(line: str) -> NebraEvent | None:
    if len(line) > MAX_LINE or ";" not in line:
        return None
    level, rest = line.split(";", 1)
    if ";" not in rest:
        return None
    message, stamp = rest.rsplit(";", 1)
    when = parse_time(stamp)
    message = clean(message)
    if when is None or not message:
        return None
    level = clean(level, 20) or "General"
    level = next((x for x in LEVELS if x.lower() == level.lower()), level)
    kind, name, client = classify(message)
    if client:                                                    # the PC name is private: never kept
        text = "Hive client " + {"unlock": "is unlocked.", "lock": "is locked again.",
                                 "disconnect": "has been disconnected from the Network.",
                                 "reconnect": "has been reconnected to the Network."}[kind]
    else:
        text = message
    return NebraEvent(when, level, kind, "Hive client" if client else name, client, text[:TEXT_MAX])


def parse_text(text: str) -> ParseResult:
    """Every event in the file text, in file order, exact repeats within the same second dropped."""
    out = ParseResult()
    seen: set[tuple] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        event = parse_line(line)
        if event is None:
            out.skipped += 1
            continue
        if len(out.events) >= MAX_ITEMS:
            out.truncated = True
            break
        key = (event.level, event.text, event.when)
        if key in seen:
            out.duplicates += 1
            continue
        seen.add(key)
        if event.kind == "other":
            out.unknown += 1
        out.events.append(event)
    return out


def looks_like_events(text: str) -> bool:
    """True when the first non-empty line is ``General|Warning|Error;...``."""
    for line in text.splitlines()[:5]:
        if line.strip():
            return bool(_LEVEL_LINE.match(line.strip().lstrip("﻿")))
    return False
