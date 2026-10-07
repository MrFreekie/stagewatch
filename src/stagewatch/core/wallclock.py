"""Wall Clock: the show's time of day from one source (this computer, or Ontime), compared with ours.

A *clock source* reports "milliseconds since midnight on the source's own wall clock" (what
Ontime calls ``clock``). The service here compares that with Stagewatch's site time and tells
dashboards, so a card can show the source's time as received and warn when the two differ.
One source serves the whole installation (``WallClockConfig.source``). A source that drops out
shows as stale, then offline; Stagewatch never switches to another source by itself.

Pure functions (``wrap_offset_s``, ``compute_offset_s``, ``reading_message``) and a small
``WallClockService`` the hub owns. The service:

* runs a source **only while some dashboard has the ``wall_clock`` card** (re-checked whenever
  the config is saved), so a venue without Ontime never even opens a connection;
* publishes the bus topic ``wall_clock`` at most once a second, and only when something changed;
* never sounds or alarms by itself. The source's device status raises a *silent* alarm.

Read-only: sources only receive. Nothing here sends anything to Ontime or any other system.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from . import sitetime

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

DAY_MS = 86_400_000
HALF_DAY_S = 43_200.0
PUBLISH_EVERY_S = 1.0

Status = Literal["ok", "offline", "error"]


@dataclass(frozen=True)
class ClockReading:
    """What a source last told us. ``clock_ms`` is None unless ``status`` is "ok"."""

    clock_ms: int | None
    received_at: float
    status: Status
    detail: str = ""


class ClockSource(Protocol):
    name: str    # stable id, e.g. "ontime"
    label: str   # shown on the card, e.g. "Ontime"

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def latest(self) -> ClockReading: ...


def valid_clock_ms(value: object) -> int | None:
    """``value`` if it is a whole number of milliseconds within one day (0 to 86,400,000), else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= DAY_MS else None


def wrap_offset_s(seconds: float) -> float:
    """Wrap a difference between two times of day into -12 h .. +12 h, so 23:59:59.8 against
    00:00:00.3 is -0.5 s, not +86,399.5 s."""
    return (seconds + HALF_DAY_S) % 86_400.0 - HALF_DAY_S


def compute_offset_s(clock_ms: int, received_at: float, site) -> float:
    """Source time of day minus Stagewatch's site time of day, at the moment it was received.
    Positive means the source is ahead of us."""
    return round(wrap_offset_s((clock_ms - sitetime.ms_since_local_midnight(received_at, site)) / 1000.0), 3)


def reading_message(source: ClockSource, reading: ClockReading, site, warn_offset_s: float) -> dict:
    """The public shape (snapshot and live feed): no addresses, no error text."""
    ok = reading.status == "ok" and reading.clock_ms is not None
    offset = compute_offset_s(reading.clock_ms, reading.received_at, site) if ok else None
    return {
        "source": source.name,
        "label": source.label,
        "clock_ms": reading.clock_ms if ok else None,
        "received_at": reading.received_at,
        "status": reading.status,
        "offset_s": offset,
        "warn": offset is not None and abs(offset) > warn_offset_s,
    }


def card_assigned(config) -> bool:
    return any("wall_clock" in d.cards for d in config.dashboards)


class PcClock:
    """The Stagewatch computer's own clock, as site time of day. Always "ok" and never differs
    from Stagewatch (it *is* Stagewatch's time), so there is no warning, no device and no alarm.
    It makes no network call. Tablets show it on the server-corrected clock, so a wrong tablet
    clock doesn't matter."""

    name = "pc"
    label = "Stagewatch PC"

    def __init__(self, site_fn: Callable[[], object], clock: Callable[[], float] = time.time) -> None:
        self._site_fn = site_fn
        self._time = clock

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def latest(self) -> ClockReading:
        now = self._time()
        return ClockReading(sitetime.ms_since_local_midnight(now, self._site_fn()), now, "ok", "")

    def details(self) -> dict:
        return {"transport": "local clock"}


def _pc_source(hub: "Hub") -> ClockSource | None:
    return PcClock(lambda: hub.config.site)


def _ontime_source(hub: "Hub") -> ClockSource | None:
    """The clock source an integration offers under the name "ontime" (the real one or its emulated stand-in)."""
    for integration in hub.integrations.values():
        source = getattr(integration, "clock_source", None)
        if source is not None and getattr(source, "name", "") == "ontime":
            return source
    return None


# name -> factory. The name is WallClockConfig.source. NTP and GPS sources would be added here.
SOURCES: dict[str, Callable[["Hub"], ClockSource | None]] = {"pc": _pc_source, "ontime": _ontime_source}


def seed_emulate_demo(config) -> bool:
    """Emulate mode only (called from __main__, never by the hub): a fresh emulate config with the
    three stock dashboards gets the Wall Clock card on each, one in each style, and the emulated
    Ontime as the source, so every style and every state (live, differs, stale, offline) can be
    seen offline. Does nothing once any dashboard has the card or the dashboards were changed.
    Returns True if it changed the config."""
    stock = {"foh": "segments", "phone": "digits", "wall": "ring"}
    if card_assigned(config) or {d.slug for d in config.dashboards} != set(stock):
        return False
    for d in config.dashboards:
        d.cards = [*d.cards[:1], "wall_clock", *d.cards[1:]]
        d.clock_style = stock[d.slug]
    config.wall_clock.source = "ontime"
    return True


class WallClockService:
    """Owned by the hub. ``source_for(hub)`` finds the source: an integration that offers a
    ``clock_source`` (the Ontime one, or its emulated stand-in)."""

    def __init__(self, hub: "Hub") -> None:
        self.hub = hub
        self._source: ClockSource | None = None
        self._source_key: tuple | None = None   # what the running source was started with
        self._task: asyncio.Task | None = None
        self._apply_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_sent: dict | None = None
        self._stopped = True
        hub.bus.subscribe("config", self._on_config)

    # ------------------------------------------------------------ source
    def _find_source(self) -> ClockSource | None:
        """The one source for the whole installation, as chosen in the config. Never another one."""
        factory = SOURCES.get(self.hub.config.wall_clock.source)
        return factory(self.hub) if factory else None

    def _key(self) -> tuple:
        """What the running source depends on; a change restarts it."""
        w = self.hub.config.wall_clock
        return (w.source, w.ontime_url if w.source == "ontime" else "")

    @property
    def active(self) -> bool:
        return self._source is not None

    # --------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._stopped = False
        await self.evaluate()
        self._task = asyncio.create_task(self._publish_loop(), name="wall-clock")

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._task, self._apply_task):
            if task:
                task.cancel()
        await asyncio.gather(*[t for t in (self._task, self._apply_task) if t], return_exceptions=True)
        self._task = self._apply_task = None
        async with self._lock:
            await self._stop_source()

    def _on_config(self, _topic: str, _payload) -> None:
        """A config save: look again (the card may have been added or removed, or the address
        changed). Coalesced; harmless when the hub isn't running."""
        if self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = loop.create_task(self.evaluate(), name="wall-clock-apply")

    async def evaluate(self) -> None:
        """Start, restart or stop the source to match the config. Never raises."""
        async with self._lock:
            try:
                wanted = card_assigned(self.hub.config)
                key = self._key()
                if self._source is not None and (not wanted or key != self._source_key):
                    await self._stop_source()
                if wanted and self._source is None:
                    source = self._find_source()
                    if source is None:
                        return
                    await source.start()
                    self._source, self._source_key = source, key
                    self._last_sent = None
            except Exception:  # noqa: BLE001 - a clock must never stop the hub
                log.exception("Could not start or stop the wall clock source")

    async def _stop_source(self) -> None:
        source, self._source, self._source_key = self._source, None, None
        if source is not None:
            try:
                await source.stop()
            except Exception:  # noqa: BLE001
                log.exception("Could not stop the wall clock source")
        self._last_sent = None

    # ----------------------------------------------------------- readings
    def snapshot(self) -> dict | None:
        """The public message for the current reading, or None while no dashboard has the card."""
        source = self._source
        if source is None:
            return None
        try:
            w = self.hub.config.wall_clock
            # "display" is only on/off and enum choices (no addresses): the cosmetic options.
            return {**reading_message(source, source.latest(), self.hub.config.site, w.warn_offset_s),
                    "display": w.display.model_dump()}
        except Exception:  # noqa: BLE001 - never break the snapshot over the clock
            log.exception("Wall clock reading failed")
            return None

    def admin_status(self) -> dict:
        """For the admin card: is it running, and what is the source saying."""
        source = self._source
        out: dict = {"active": source is not None, "card_assigned": card_assigned(self.hub.config),
                     "source": source.name if source is not None else None}
        if source is not None:
            reading = source.latest()
            out.update(status=reading.status, detail=reading.detail, last_message=(
                reading.received_at if reading.status == "ok" else None))
            details = getattr(source, "details", None)
            if callable(details):
                out.update(details())
        return out

    def _publish_once(self) -> None:
        msg = self.snapshot()
        if msg is None:
            return
        # Compare without received_at drift: a new reading always differs, so a 1 Hz source
        # publishes once a second and an offline source only when its state changes.
        if msg != self._last_sent:
            self._last_sent = msg
            self.hub.bus.publish("wall_clock", msg)

    async def _publish_loop(self) -> None:
        while True:
            await asyncio.sleep(PUBLISH_EVERY_S)
            try:
                self._publish_once()
            except Exception:  # noqa: BLE001
                log.exception("Wall clock publish failed")
