"""What Nebra events mean for Stagewatch: how serious each is, how a disconnect pairs with its reconnect,
and how a burst on many units folds into one marker. Pure functions.

Severity, by what the line says (the file's Level agrees: Error = disconnect, Warning = lock change):
a unit disconnected from the network is an Alert (2); a stream lock lost ("is unlocked") is an Advisory (1);
"reconnected" and "locked again" are clears (0). A Hive client (another computer running Nebra) is not a
device: its lines are system events, an Advisory at most, and never name the computer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parse import NebraEvent

LEVEL_NAMES = {0: "Info", 1: "Advisory", 2: "Alert", 3: "Stop"}
SYSLOG_SEVERITY = {0: 6, 1: 4, 2: 3}
FILE_LEVEL = {"error": 2, "warning": 1}
BURST_S = 2.0                   # raises of the same fault on several units this close are one burst
MAX_NAMED_UNITS = 3
LANWM_COVER_S = 10.0            # a lock change this close to the LA Network Manager's own is the same event
UNIT_LINE = re.compile(r"^Nebra unit (.{1,80}?): (.+)$")
WORDS = {"unlock": "stream unlocked", "lock": "stream locked again",
         "disconnect": "disconnected from the network", "reconnect": "reconnected to the network"}


def level_of(event: NebraEvent) -> int:
    """Stagewatch level 0-2 for one line."""
    if event.kind == "disconnect":
        return 1 if event.client else 2
    if event.kind == "unlock":
        return 1
    if event.kind in ("lock", "reconnect", "db_start", "db_done"):
        return 0
    return FILE_LEVEL.get(event.level.lower(), 0)       # a message shape not seen before: by the file's own Level


def line(event: NebraEvent) -> str:
    """The stored message text for one event."""
    if event.kind in WORDS and not event.client:
        return f"Nebra unit {event.name}: {WORDS[event.kind]}"
    return f"Nebra: {event.text}"


def unit_token(name: str) -> str:
    """The unit's own number or short name: the last word of ``GROUP.DEVICE`` (``LA12X 104`` -> ``104``)."""
    return re.split(r"[ .]", name.strip())[-1].lower()


@dataclass
class Episode:
    name: str
    family: str                 # lock | connection
    kind: str                   # the raising kind
    start: float                # corrected UTC epoch
    end: float | None = None
    fresh: bool = True          # its raise is new to the show
    covered: bool = False       # LA Network Manager already logged it
    events: list = field(default_factory=list)

    @property
    def severity(self) -> int:
        return 2 if self.kind == "disconnect" else 1

    @property
    def description(self) -> str:
        return WORDS[self.kind]


def episodes(items: list[tuple[NebraEvent, float]]) -> list[Episode]:
    """Pair each raise with the next clear of the same unit and family. ``items`` are (event, corrected
    epoch) in file order. A clear with no raise before it in the file is not an episode. A raise with no
    clear stays open (``end`` None) to the end of the file: no recovery is assumed. Clients are not units."""
    open_: dict[tuple, Episode] = {}
    out: list[Episode] = []
    for e, ts in items:
        if e.client or not e.family:
            continue
        key = (e.name, e.family)
        if e.clearing:
            ep = open_.pop(key, None)
            if ep is not None:
                ep.end = max(ts, ep.start)
                ep.events.append((e, ts))
        elif key not in open_:                  # raised again with no clear between: the first stays open
            ep = Episode(e.name, e.family, e.kind, ts, events=[(e, ts)])
            open_[key] = ep
            out.append(ep)
    return out


@dataclass
class Burst:
    description: str
    severity: int
    start: float
    names: list[str] = field(default_factory=list)

    def label(self) -> str:
        n = len(self.names)
        if n == 1:
            return f"Nebra unit {self.names[0]}: {self.description}"
        if n <= MAX_NAMED_UNITS:
            return f"{self.description[0].upper()}{self.description[1:]}: {', '.join(sorted(self.names))}"
        return f"{self.description[0].upper()}{self.description[1:]} on {n} units"


def bursts(eps: list[Episode]) -> list[Burst]:
    """Fold episodes of the same fault whose raises fall within BURST_S of the burst's first into one."""
    out: list[Burst] = []
    open_: dict[tuple, Burst] = {}
    for ep in sorted(eps, key=lambda e: e.start):
        key = (ep.description, ep.severity)
        b = open_.get(key)
        if b is not None and ep.start - b.start <= BURST_S:
            if ep.name not in b.names:
                b.names.append(ep.name)
            continue
        b = Burst(ep.description, ep.severity, ep.start, [ep.name])
        open_[key] = b
        out.append(b)
    return out
