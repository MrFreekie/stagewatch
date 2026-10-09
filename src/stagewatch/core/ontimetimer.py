"""Ontime Timer: what Ontime's main timer says right now, for the ``ontime_timer`` dashboard card.

The timer comes from the same read-only Ontime connection the Wall Clock card can use (one
shared connection, never a second one): the Ontime integration keeps the latest merged timer
state and this service publishes it. The service:

* asks the Ontime source to run **only while some dashboard has the ``ontime_timer`` card**
  (re-checked whenever the config is saved); the source stays up while either card needs it;
* publishes the bus topic ``ontime_timer`` at most once a second;
* never sounds or raises an alarm by itself. The source's device status raises the one *silent*
  alarm (shared with the Wall Clock).

Advisory only: this is Ontime's timer shown on a screen, not a Stagewatch timer, not a cue
source and not a show-control or safety timer. Nothing here sends anything to Ontime.
"""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .wallclock import card_assigned

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

CARD_ID = "ontime_timer"
CONSUMER = "ontime_timer"      # the name this service holds the shared Ontime source under
PUBLISH_EVERY_S = 1.0
TITLE_MAX = 80
MAX_MARKS = 2                  # combining marks kept per base character

# Values Stagewatch understands. Anything else is passed on as "unknown" and shown neutral.
PLAYBACKS = ("play", "roll", "pause", "armed", "stop")
RUNNING = ("play", "roll")
PHASES = ("default", "warning", "danger", "overtime", "pending", "none")
TIMER_TYPES = ("count-down", "count-up", "clock", "none")

Status = Literal["ok", "offline", "error"]


@dataclass(frozen=True)
class TimerState:
    """Ontime's main timer plus the event it is running, merged from the messages seen so far.

    All times are whole milliseconds. ``current_ms`` is negative in overtime (Ontime's
    documentation; not yet seen on a real instance). None means Ontime sent null."""

    playback: str                 # one of PLAYBACKS, or "unknown"
    phase: str                    # one of PHASES, or "unknown"
    current_ms: int | None
    duration_ms: int | None
    elapsed_ms: int | None
    added_ms: int
    has_event: bool               # an event is loaded (eventNow is not null)
    title: str                    # cleaned, at most TITLE_MAX characters
    timer_type: str               # one of TIMER_TYPES, or "unknown"
    warn_ms: int | None           # the event's own warning time (eventNow.timeWarning)
    danger_ms: int | None         # the event's own danger time (eventNow.timeDanger)


@dataclass(frozen=True)
class TimerReading:
    """What the source last told us about the timer. ``state`` is None unless ``status`` is "ok"."""

    state: TimerState | None
    received_at: float
    status: Status
    detail: str = ""


def clean_title(value: object, limit: int = TITLE_MAX) -> str:
    """Rundown text from Ontime, made safe to show: no control, format (bidi, zero-width) or
    line-break characters (so a zero-width joiner is dropped and a joined emoji family shows as
    separate emoji; that is accepted), at most two combining marks per character, runs of spaces collapsed, at most ``limit`` (TITLE_MAX) characters."""
    if not isinstance(value, str):
        return ""
    out = []
    marks = 0   # combining marks in a row after one base character
    for ch in value[:4 * limit]:   # bounded work on a hostile, huge string
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "Mc"):
            marks += 1
            if marks <= MAX_MARKS:    # "Zalgo" text: keep the first few marks, drop the pile
                out.append(ch)
            continue
        marks = 0
        if ch in "\t\r\n" or cat in ("Zs", "Zl", "Zp"):
            out.append(" ")
        elif cat not in ("Cc", "Cf", "Cs", "Co", "Cn"):
            out.append(ch)
    return " ".join("".join(out).split())[:limit].rstrip()


def timer_message(label: str, reading: TimerReading, show_title: bool = True) -> dict:
    """The public shape (snapshot and live feed). No address, error text, ids, notes, cues or
    custom fields. ``title`` is empty when the admin has hidden event titles."""
    s = reading.state if reading.status == "ok" else None
    running = s is not None and s.playback in RUNNING and s.current_ms is not None and s.current_ms > 0
    return {
        "status": reading.status,
        "label": label,
        "received_at": reading.received_at,
        "playback": s.playback if s else None,
        "phase": s.phase if s else None,
        "current_ms": s.current_ms if s else None,
        "duration_ms": s.duration_ms if s else None,
        "elapsed_ms": s.elapsed_ms if s else None,
        "added_ms": s.added_ms if s else 0,
        "finish_in_ms": s.current_ms if (s and running and s.timer_type == "count-down") else None,
        "has_event": s.has_event if s else False,
        # Where the card's warning steps come from: Ontime's own times for the event, or (none sent)
        # the site's warning minutes. The card says so in the second case.
        "thresholds": (None if s is None else "ontime" if (s.warn_ms is not None or s.danger_ms is not None) else "site"),
        "title": s.title if (s and show_title) else "",
        "timer_type": s.timer_type if s else None,
        "warn_ms": s.warn_ms if s else None,
        "danger_ms": s.danger_ms if s else None,
    }


def timer_assigned(config) -> bool:
    return card_assigned(config, CARD_ID)


def _ontime_source(hub: "Hub", attr: str = "latest_timer"):
    """The Ontime integration's shared source (the real one or its emulated stand-in), if any."""
    for integration in hub.integrations.values():
        source = getattr(integration, "clock_source", None)
        if source is not None and getattr(source, "name", "") == "ontime" and hasattr(source, attr):
            return source
    return None


class OntimeTimerService:
    """Owned by the hub. Holds the shared Ontime source while a dashboard has the card.

    The Ontime Rundown card's service (core/ontimerundown.py) subclasses this and changes only the
    class attributes and ``_message``: same refcounted source, same one connection."""

    CARD_ID = CARD_ID
    CONSUMER = CONSUMER
    TOPIC = "ontime_timer"
    SOURCE_ATTR = "latest_timer"      # the reading method the source must offer

    def __init__(self, hub: "Hub") -> None:
        self.hub = hub
        self._source = None
        self._task: asyncio.Task | None = None
        self._apply_task: asyncio.Task | None = None
        self._dirty = False   # a config save arrived that the running apply may not have seen
        self._lock = asyncio.Lock()
        self._last_sent: dict | None = None
        self._stopped = True
        hub.bus.subscribe("config", self._on_config)

    @property
    def active(self) -> bool:
        return self._source is not None

    # --------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._stopped = False
        await self.evaluate()
        self._task = asyncio.create_task(self._publish_loop(), name=f"{self.TOPIC}-publish")

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._task, self._apply_task):
            if task:
                task.cancel()
        await asyncio.gather(*[t for t in (self._task, self._apply_task) if t], return_exceptions=True)
        self._task = self._apply_task = None
        async with self._lock:
            await self._release()

    def _on_config(self, _topic: str, _payload) -> None:
        """A config save: look again (the card may have been added or removed, or the address
        changed). Coalesced; harmless when the hub isn't running."""
        if self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._dirty = True
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = loop.create_task(self._apply_loop(), name=f"{self.TOPIC}-apply")

    async def _apply_loop(self) -> None:
        """Evaluate until no save arrived meanwhile, so a second save during a slow start is not lost."""
        while self._dirty and not self._stopped:
            self._dirty = False
            await self.evaluate()

    async def evaluate(self) -> None:
        """Hold or release the shared source to match the config. Never raises."""
        async with self._lock:
            try:
                if not card_assigned(self.hub.config, self.CARD_ID):
                    await self._release()
                    return
                source = self._source or _ontime_source(self.hub, self.SOURCE_ATTR)
                if source is None:
                    return
                if self._source is None:
                    self._last_sent = None
                # Remember it BEFORE acquiring: if we are cancelled part-way, stop() still releases it.
                self._source = source
                try:
                    await source.acquire(self.CONSUMER)   # idempotent; also restarts it if the address changed
                except Exception:
                    self._source = None
                    await source.release(self.CONSUMER)
                    raise
            except Exception:  # noqa: BLE001 - a timer card must never stop the hub
                log.exception("Could not start or stop the Ontime timer")

    async def _release(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            try:
                await source.release(self.CONSUMER)
            except Exception:  # noqa: BLE001
                log.exception("Could not release the Ontime source")
        self._last_sent = None

    # ---------------------------------------------------------- readings
    def snapshot(self) -> dict | None:
        """The public message for the current reading, or None while no dashboard has the card."""
        source = self._source
        if source is None:
            return None
        try:
            return self._message(source)
        except Exception:  # noqa: BLE001 - never break the snapshot over a card
            log.exception("Ontime %s reading failed", self.TOPIC)
            return None

    def _reading(self, source):
        return source.latest_timer()

    def _message(self, source) -> dict:
        return timer_message(source.label, self._reading(source), self.hub.config.ontime_timer.show_title)

    def admin_status(self) -> dict:
        """For the admin page: is the card assigned, and is the shared source running (no addresses)."""
        out: dict = {"card_assigned": card_assigned(self.hub.config, self.CARD_ID), "active": self._source is not None}
        source = self._source
        if source is not None:
            reading = self._reading(source)
            out.update(status=reading.status, detail=reading.detail,
                       last_message=reading.received_at if reading.status == "ok" else None)
        return out

    def _publish_once(self) -> None:
        msg = self.snapshot()
        if msg is not None and msg != self._last_sent:
            self._last_sent = msg
            self.hub.bus.publish(self.TOPIC, msg)

    async def _publish_loop(self) -> None:
        while True:
            await asyncio.sleep(PUBLISH_EVERY_S)
            try:
                self._publish_once()
            except Exception:  # noqa: BLE001
                log.exception("Ontime %s publish failed", self.TOPIC)
