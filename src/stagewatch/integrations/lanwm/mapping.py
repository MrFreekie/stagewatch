"""What LA NWM entries mean for Stagewatch: how serious each is, which become timeline markers, and how a
burst on many units folds into one line. Pure functions.

Severity follows LA NWM's own Level: Critical = Stop (3), Major = Alert (2), Minor = Advisory (1), Info = 0
(a record, not a fault). A Level LA NWM adds later is shown as an Advisory, never dropped.

Session entries (page changes, adapters, IP ranges, the session file name) are not stored: they hold the
laptop's addresses and say nothing about the rig. Only "Going to Online/Offline Mode", "All Messages
Acknowledged" and "Entering/Leaving Load Checker Mode" can be kept, as markers, and only when asked for
(the first two together, Load Checker on its own).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parse import UNIT_SESSION, LaEvent

LEVEL_SEVERITY = {"Info": 0, "Minor": 1, "Major": 2, "Critical": 3}
LEVEL_NAMES = {0: "Info", 1: "Advisory", 2: "Alert", 3: "Stop"}
# The syslog severity (0-7) stored in device_log.severity, so a log viewer sorts it like any other line.
SYSLOG_SEVERITY = {"Info": 6, "Minor": 4, "Major": 3, "Critical": 2}
BURST_S = 2.0                   # raises of the same fault on several units this close are one burst
MAX_NAMED_UNITS = 3             # a burst names its units up to this many, then just counts them
SESSION_MARKERS = re.compile(r"^(Going to (Online|Offline) Mode|All Messages Acknowledged)\b")
LOAD_CHECKER_MARKERS = re.compile(r"^(Entering|Leaving) Load Checker Mode\b")


def severity(event: LaEvent) -> int:
    return LEVEL_SEVERITY.get(event.level, 1)


def unit_name(unit: str) -> str:
    return "LA unit " + unit if unit != UNIT_SESSION else "LA Network Manager"


def keep(event: LaEvent, session_markers: bool = False, load_checker: bool = False) -> bool:
    """Stored at all? Session entries are not, apart from the optional online/offline/acknowledge ones
    and the optional Load Checker on/off ones (two separate choices)."""
    if event.category.lower() != "session":
        return True
    return ((session_markers and bool(SESSION_MARKERS.match(event.description)))
            or (load_checker and bool(LOAD_CHECKER_MARKERS.match(event.description))))


def line(event: LaEvent) -> str:
    """The stored message text for one entry."""
    return event.description if event.unit == UNIT_SESSION else f"{unit_name(event.unit)}: {event.description}"


def is_action(event: LaEvent) -> bool:
    """A power or network action by the engineer ("Standby All Units"), not a fault."""
    return (event.level == "Info" and not event.cleared and event.unit == UNIT_SESSION
            and event.category.lower() in ("power", "network"))


@dataclass
class Episode:
    """One fault on one unit: the raise and, if the file has it, the clear."""
    unit: str
    description: str
    category: str
    level: str
    start: object               # naive datetime in the file's own time
    end: object | None = None   # None = no clear in the file

    @property
    def severity(self) -> int:
        return LEVEL_SEVERITY.get(self.level, 1)


def episodes(events: list[LaEvent]) -> list[Episode]:
    """Pair each raise with the next clear of the same (unit, description). Info lines that are not
    clears (actions, session) are not episodes."""
    open_: dict[tuple, Episode] = {}
    out: list[Episode] = []
    for e in events:
        if e.cleared:
            ep = open_.pop(e.key, None)
            if ep is not None:
                ep.end = e.when
        elif e.level != "Info" and e.unit != UNIT_SESSION:
            prev = open_.get(e.key)
            if prev is not None:        # raised again with no clear between: the first stays open
                continue
            ep = Episode(e.unit, e.base, e.category, e.level, e.when)
            open_[e.key] = ep
            out.append(ep)
    return out


@dataclass
class Burst:
    description: str
    category: str
    level: str
    start: object
    units: list[str] = field(default_factory=list)

    @property
    def severity(self) -> int:
        return LEVEL_SEVERITY.get(self.level, 1)

    def label(self) -> str:
        n = len(self.units)
        if n == 1:
            return f"{unit_name(self.units[0])}: {self.description}"
        if n <= MAX_NAMED_UNITS:
            return f"{self.description} on LA units {', '.join(sorted(self.units))}"
        return f"{self.description} on {n} units"


def bursts(eps: list[Episode]) -> list[Burst]:
    """Fold episodes of the same fault whose raises fall within BURST_S of the burst's first into one."""
    out: list[Burst] = []
    open_: dict[tuple, Burst] = {}
    for ep in sorted(eps, key=lambda e: e.start):
        key = (ep.description, ep.level, ep.category)
        b = open_.get(key)
        if b is not None and (ep.start - b.start).total_seconds() <= BURST_S:
            if ep.unit not in b.units:
                b.units.append(ep.unit)
            continue
        b = Burst(ep.description, ep.category, ep.level, ep.start, [ep.unit])
        open_[key] = b
        out.append(b)
    return out
