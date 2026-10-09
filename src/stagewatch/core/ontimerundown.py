"""Ontime Rundown: how far through the day Ontime is and whether it is ahead or behind, for the
``ontime_rundown`` dashboard card.

It rides on the same read-only Ontime connection as the Wall Clock and Ontime Timer cards (one
shared, reference counted source, never a second connection). Counters, times and the
offset come from the runtime data; the current event's title and note from its ``eventNow``; and the
event list (cue, title, times, skipped) from one extra read-only request, ``GET /data/rundowns/current``,
made only while a dashboard has this card. Titles and notes are hidden by the same admin switch as the
Ontime Timer's ("Show the event title on dashboards"). The service:

* asks the Ontime source to run **only while some dashboard has the ``ontime_rundown`` card**;
* publishes the bus topic ``ontime_rundown`` at most once a second;
* never sounds or raises an alarm by itself (the source's silent alarm is shared).

Advisory only: Ontime's numbers shown on a screen. Nothing here sends anything to Ontime, and
nothing is copied into Stagewatch's own schedule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
EVENT_ID_MAX = 64                 # an event id is matched, never shown
LIST_MAX_EVENTS = 200             # events kept from the rundown list
LIST_CUE_MAX = 12                 # characters of an event's cue
LIST_PAST = 4                     # rows sent before the current event
LIST_AHEAD = 14                   # rows sent after it (the browser trims further per layout)

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
    event_id: str = ""                        # eventNow.id, only to find the current row in the list (never public)
    text_unreadable: bool = False             # the last eventNow title or note could not be read: the old text is kept

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


@dataclass(frozen=True)
class EventRow:
    """One event of Ontime's rundown list, reduced to what the card shows. ``start_ms`` and ``end_ms``
    are Ontime-clock milliseconds, or None when Ontime sent nothing usable."""

    id: str
    cue: str
    title: str
    start_ms: int | None
    end_ms: int | None
    skip: bool


@dataclass(frozen=True)
class EventsReading:
    """The last good event list. ``rows`` is None until one has been read. ``stale`` is True while
    the latest fetch failed or the connection is lost: the old rows stay, marked, never invented."""

    rows: tuple[EventRow, ...] | None = None
    stale: bool = False


def _find_current(rows: tuple[EventRow, ...], state: RundownState | None) -> tuple[int | None, bool]:
    """(index of the current row, unplaced). The current event is the row whose id equals
    ``eventNow.id``; only when Ontime sent no id is ``selectedEventIndex`` (0-based, confirmed on a
    real 4.14.0, counted among the listed events) used. A given id that is not in the list (an id
    over the length cap, an event past the 200 kept, any mismatch) or an index past the list is
    **unplaced**: nothing is guessed."""
    if state is None:
        return None, False
    if state.event_id:
        cur = next((i for i, r in enumerate(rows) if r.id and r.id == state.event_id), None)
        return cur, cur is None
    if state.selected_index is not None:
        ok = 0 <= state.selected_index < len(rows)
        return (state.selected_index if ok else None), not ok
    return None, False


def events_view(rows: tuple[EventRow, ...] | None, state: RundownState | None, show_text: bool = True) -> tuple[list[dict], bool]:
    """(the rows around the current event as public dicts ``{cue, title, start, end, state}``, unplaced).

    state is "current", "next" (the first event after it that is not skipped), "later", "past" or
    "skipped". Before the start every row is "later" (the first is "next"); once finished every row is
    "past". At most LIST_PAST rows before and LIST_AHEAD after the current one are sent. When the
    show is running and the current event cannot be found the list is empty and ``unplaced`` is True:
    no row is ever called "next" on a guess. ``show_text`` False blanks every title (the admin's
    "show the event title" switch), keeping cue, times and state."""
    if not rows:
        return [], False
    cur, unplaced = _find_current(rows, state)
    if unplaced:
        return [], True
    started = state is not None and state.actual_start_ms is not None
    finished = cur is None and started and state is not None and state.selected_index is None
    out = []
    next_found = False
    for i, r in enumerate(rows):
        if r.skip:
            st = "skipped"
        elif cur is not None and i == cur:
            st = "current"
        elif finished or (cur is not None and i < cur):
            st = "past"
        elif not next_found:
            st, next_found = "next", True
        else:
            st = "later"
        out.append((i, {"cue": r.cue, "title": r.title if show_text else "", "start": r.start_ms, "end": r.end_ms, "state": st}))
    if cur is not None:
        lo, hi = cur - LIST_PAST, cur + LIST_AHEAD
    elif finished:
        lo, hi = len(rows) - LIST_PAST - 1, len(rows)
    else:
        lo, hi = 0, LIST_AHEAD
    return [d for i, d in out if lo <= i <= hi], False


def events_window(rows: tuple[EventRow, ...] | None, state: RundownState | None) -> list[dict]:
    """The rows of ``events_view`` (without the unplaced flag)."""
    return events_view(rows, state)[0]


def rundown_message(label: str, reading: RundownReading, events: EventsReading | None = None,
                    show_text: bool = True) -> dict:
    """The public shape (snapshot and live feed). No address, error text or ids.
    ``current_day`` (only used to say "a later day") and ``ontime_clock_ms`` (Ontime's own clock, for
    the day bar) are sent on purpose; both are plain numbers. ``event_title`` and ``event_note`` are
    the current event's cleaned text from Ontime's ``eventNow``: anyone who can open a dashboard
    with this card sees them. Nothing else of the event (ids, colours, custom fields, triggers,
    the next event) is kept. ``show_text`` is the admin switch "Show the event title on dashboards" (shared
    with the Ontime Timer card): when off, the title, the note and every list title are blank."""
    s = reading.state if reading.status == "ok" else None
    has_total = s is not None and s.num_events is not None
    ev_rows, ev_unplaced = events_view(events.rows, s, show_text) if (s is not None and events is not None) else ([], False)
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
        "event_title": s.event_title if (s and show_text) else "",
        "event_note": s.event_note if (s and show_text) else "",
        "event_text_unreadable": bool(s is not None and s.text_unreadable and show_text),
        "events": ev_rows,
        "events_unplaced": ev_unplaced,
        "events_stale": bool(events.stale) if (events is not None and events.rows is not None) else False,
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
        events = source.latest_events() if hasattr(source, "latest_events") else None
        return rundown_message(source.label, self._reading(source), events, self.hub.config.ontime_timer.show_title)
