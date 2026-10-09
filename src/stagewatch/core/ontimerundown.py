"""Ontime Rundown: how far through the day Ontime is and whether it is ahead or behind, for the
``ontime_rundown`` dashboard card.

It rides on the same read-only Ontime connection as the Wall Clock and Ontime Timer cards (one
shared, reference counted source, never a second connection). Only counters, times and the
offset are read: no event titles, notes, cues or lists (those would need a request outside the
three paths we use, which is deliberately not built). The service:

* asks the Ontime source to run **only while some dashboard has the ``ontime_rundown`` card**;
* publishes the bus topic ``ontime_rundown`` at most once a second;
* never sounds or raises an alarm by itself (the source's silent alarm is shared).

Advisory only: Ontime's numbers shown on a screen. Nothing here sends anything to Ontime, and
nothing is copied into Stagewatch's own schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ontimetimer import OntimeTimerService, Status

if TYPE_CHECKING:
    from .hub import Hub

CARD_ID = "ontime_rundown"
CONSUMER = "ontime_rundown"

HOUR_MS = 3_600_000
MAX_TIME_MS = 72 * HOUR_MS        # a time of day (or a few days on) from Ontime: 0 to 72 h
MAX_OFFSET_MS = 72 * HOUR_MS      # |offset|
MAX_EVENTS = 10_000               # numEvents, and the selected index
MAX_DAY = 366                     # currentDay
EVENT_TITLE_MAX = 120             # characters kept of the current event's title
EVENT_NOTE_MAX = 400              # and of its note

OFFSET_MODES = ("absolute", "relative")


@dataclass(frozen=True)
class RundownState:
    """What Ontime's ``rundown`` and ``offset`` blocks said, merged from the messages seen so far.

    All times are whole milliseconds on Ontime's own clock (since its midnight). None means
    "not sent" or null. Two shapes are read: the documented one (``rundown.offset`` and
    ``rundown.expectedEnd``) and the one real Ontime 4.14.0 sends (a separate top-level ``offset``
    block with ``absolute``, ``relative``, ``mode`` and ``expectedRundownEnd``). The top-level
    block wins when both are present."""

    selected_index: int | None = None
    num_events: int | None = None
    planned_start_ms: int | None = None
    planned_end_ms: int | None = None
    actual_start_ms: int | None = None
    current_day: int | None = None
    doc_offset_ms: int | None = None          # documented shape: rundown.offset
    doc_expected_end_ms: int | None = None    # documented shape: rundown.expectedEnd
    offset_absolute_ms: int | None = None     # real shape: offset.absolute
    offset_relative_ms: int | None = None     # real shape: offset.relative
    offset_mode: str | None = None            # real shape: offset.mode, one of OFFSET_MODES, or None
    offset_expected_end_ms: int | None = None  # real shape: offset.expectedRundownEnd
    event_title: str = ""                     # eventNow.title, cleaned (shown on dashboards)
    event_note: str = ""                      # eventNow.note, cleaned (shown on dashboards)

    @property
    def offset_kind(self) -> str | None:
        """"absolute" or "relative" (Ontime's mode), "unknown" (a bare number, mode not given) or None."""
        if self._block_offset() is not None:
            return self.offset_mode
        return "unknown" if self.doc_offset_ms is not None else None

    def _block_offset(self) -> int | None:
        if self.offset_mode == "absolute":
            return self.offset_absolute_ms
        if self.offset_mode == "relative":
            return self.offset_relative_ms
        return None

    @property
    def offset_ms(self) -> int | None:
        """Ontime's offset in the mode Ontime reports (never a guess when the mode is not given)."""
        block = self._block_offset()
        return block if block is not None else self.doc_offset_ms

    @property
    def expected_end_ms(self) -> int | None:
        """None when Ontime sent none: the planned end is never substituted."""
        return self.offset_expected_end_ms if self.offset_expected_end_ms is not None else self.doc_expected_end_ms


@dataclass(frozen=True)
class RundownReading:
    """What the source last told us about the rundown. ``state`` is None unless ``status`` is "ok"
    and Ontime has sent a rundown block. ``clock_ms`` is Ontime's own clock at ``received_at``."""

    state: RundownState | None
    received_at: float
    status: Status
    detail: str = ""
    clock_ms: int | None = None
    # True when Ontime's latest rundown or offset block could not be read. ``state`` then still
    # holds the last readable figures (nothing is guessed), and the card shows them struck
    # through with "can't read" until a readable block merges in.
    unreadable: bool = False


def rundown_message(label: str, reading: RundownReading) -> dict:
    """The public shape (snapshot and live feed). No address, error text, ids or event text.
    ``current_day`` (only used to say "a later day") and ``ontime_clock_ms`` (Ontime's own clock, for
    the day bar) are sent on purpose; both are plain numbers. ``event_title`` and ``event_note`` are
    the current event's cleaned text from Ontime's ``eventNow``: anyone who can open a dashboard
    with this card sees them. Nothing else of the event (ids, colours, custom fields, triggers,
    the next event) is kept."""
    s = reading.state if reading.status == "ok" else None
    has_total = s is not None and s.num_events is not None
    return {
        "status": reading.status,
        "label": label,
        "received_at": reading.received_at,
        "position": {"index": s.selected_index, "total": s.num_events} if has_total else None,
        "offset_ms": s.offset_ms if s else None,
        "offset_mode": s.offset_kind if s else None,
        "planned_start_ms": s.planned_start_ms if s else None,
        "planned_end_ms": s.planned_end_ms if s else None,
        "expected_end_ms": s.expected_end_ms if s else None,
        "actual_start_ms": s.actual_start_ms if s else None,
        "current_day": s.current_day if s else None,
        "ontime_clock_ms": reading.clock_ms if s else None,
        "unreadable": bool(s is not None and reading.unreadable),
        "event_title": s.event_title if s else "",
        "event_note": s.event_note if s else "",
    }


class OntimeRundownService(OntimeTimerService):
    """Owned by the hub. Holds the shared Ontime source while a dashboard has the card."""

    CARD_ID = CARD_ID
    CONSUMER = CONSUMER
    TOPIC = "ontime_rundown"
    SOURCE_ATTR = "latest_rundown"

    def __init__(self, hub: "Hub") -> None:
        super().__init__(hub)

    def _reading(self, source):
        return source.latest_rundown()

    def _message(self, source) -> dict:
        return rundown_message(source.label, self._reading(source))
