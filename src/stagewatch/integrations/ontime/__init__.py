"""Ontime integration: the show clock for the Wall Clock card and the countdown for the Ontime
Timer card and the running-order position and offset for the Ontime Rundown card.

Read-only. It listens to an Ontime server (https://github.com/cpvalente/ontime) for the time of
day and the main timer (with the title and warning times of the loaded event) and sends nothing
to it. It also reads the rundown counters, planned and expected times and the ahead/behind
offset, the title and note of the current event, and (one extra read-only request, only while
a dashboard has the Rundown card) the event list: cue, title, times and skipped. It does not
read the next event's details, colours, custom fields, messages or aux timers.

There is one connection. It runs only while at least one dashboard has a card that needs it: the
Wall Clock card (when the Wall Clock source is Ontime), the Ontime Timer card or the Ontime
Rundown card. Each card's
service "acquires" the source under its own name; it starts with the first and stops with the
last. The "Ontime" device appears under Integrations in the admin page while the source runs; if
Ontime can't be reached it goes MISSING with a *silent* alarm (an on-screen notice, never a sound).
"""

from __future__ import annotations

import asyncio
import logging

from ...core.model import Device, Status
from ...core.plugin import Integration, Manifest
from ...core.ontimerundown import EventsReading, RundownReading
from ...core.ontimetimer import TimerReading
from ...core.wallclock import ClockReading
from .client import OntimeSource
from .emulate import EmulatedClock

log = logging.getLogger(__name__)

DEVICE_ID = "ontime"

MANIFEST = Manifest(
    domain="ontime",
    name="Ontime",
    version="0.2.0",
    description="Reads the time of day and the main timer (countdown, playback state, and the "
                "title and warning times of the loaded event) from an Ontime server over one "
                "WebSocket connection, falling back to HTTP polling, so the Wall Clock and Ontime "
                "Timer cards can show them. For the Ontime Rundown card it also reads the rundown "
                "position (event number and count), planned start and end, expected end, actual "
                "start and the ahead/behind offset, plus the title and note of the current event and, from one "
                "extra read-only request (GET /data/rundowns/current), the event list (cue, title, start and "
                "end time, skipped). Titles and notes are shown on dashboards unless the admin switches "
                "them off. Read-only: sends nothing to Ontime and does not read colours, custom fields, "
                "triggers, messages or aux timers. Runs only while a "
                "dashboard has one of those cards. Tested against Ontime 4.14.0 in the 'roll' "
                "state only; other states are handled but not yet checked against a real Ontime.",
    tier="experimental",
    direction="in",
    protocols=("WebSocket", "HTTP"),
)


class _ManagedSource:
    """What the card services see: the shared Ontime source, reference counted by consumer name
    ("wall_clock", "ontime_timer", "ontime_rundown"), plus the "Ontime" device that exists only while it runs."""

    def __init__(self, owner: "OntimeIntegration", inner) -> None:
        self._owner, self._inner = owner, inner
        self.name, self.label = inner.name, inner.label
        self._holders: set[str] = set()
        self._url = ""                    # the address the running source was started with
        self._lock = asyncio.Lock()

    @property
    def holders(self) -> frozenset[str]:
        return frozenset(self._holders)

    def _current_url(self) -> str:
        return self._owner.hub.config.wall_clock.ontime_url

    def _sync_events(self) -> None:
        """The event list is fetched only while the Ontime Rundown card holds the source."""
        if hasattr(self._inner, "want_events"):
            self._inner.want_events = "ontime_rundown" in self._holders

    async def acquire(self, consumer: str) -> None:
        """Hold the source for ``consumer`` (idempotent). Starts it if nobody held it; if it is
        running against an old address (the admin changed it), restarts it on the new one."""
        async with self._lock:
            url = self._current_url()
            first = not self._holders
            self._holders.add(consumer)
            self._sync_events()
            if first:
                self._owner._register()
                await self._inner.start()
                self._url = url
            elif url != self._url:
                await self._inner.stop()
                await self._inner.start()
                self._url = url

    async def release(self, consumer: str) -> None:
        """Let go for ``consumer``. The source stops when the last holder lets go."""
        async with self._lock:
            if consumer not in self._holders:
                return
            self._holders.discard(consumer)
            self._sync_events()
            if not self._holders:
                await self._inner.stop()
                self._owner._unregister()

    # The Wall Clock service's view (core/wallclock.py ClockSource): start/stop hold/release it.
    async def start(self) -> None:
        await self.acquire("wall_clock")

    async def stop(self) -> None:
        await self.release("wall_clock")

    def latest(self) -> ClockReading:
        return self._inner.latest()

    def latest_timer(self) -> TimerReading:
        return self._inner.latest_timer()

    def latest_rundown(self) -> RundownReading:
        return self._inner.latest_rundown()

    def latest_events(self) -> EventsReading:
        return self._inner.latest_events()

    def details(self) -> dict:
        return self._inner.details()


class OntimeIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, inner=None) -> None:
        super().__init__(hub, emulate)
        if inner is None:
            if emulate:
                inner = EmulatedClock(lambda: hub.config.site, on_change=self._on_reading, cycle=True)
            else:
                inner = OntimeSource(lambda: hub.config.wall_clock.ontime_url, on_change=self._on_reading)
        else:
            inner._on_change = self._on_reading
        self._inner = inner
        self.clock_source = _ManagedSource(self, inner)
        self._registered = False

    # The source is started and stopped by the card services, not by the hub.
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def info(self) -> dict:
        device = self.hub.devices.get(DEVICE_ID) if self._registered else None
        return {**super().info(), "running": self._registered,
                "status": device.status.value if device else "not running"}

    # ------------------------------------------------------------- device
    def _register(self) -> None:
        self.hub.register_device(Device(
            DEVICE_ID, "Ontime", "ontime", "Ontime", "Show clock and timer",
            status=Status.INITIALIZING, status_detail="Connecting", category="service"))
        self._registered = True

    def _unregister(self) -> None:
        if self._registered:
            self._registered = False
            self.hub.remove_device(DEVICE_ID)

    def _on_reading(self, reading: ClockReading) -> None:
        if not self._registered:
            return
        # Healthy if either the clock or the timer is arriving: a bad value in one must not hide the other.
        timer = None
        try:
            timer = self._inner.latest_timer()
        except Exception:  # noqa: BLE001
            log.debug("no timer reading", exc_info=True)
        if reading.status == "ok" or (timer is not None and timer.status == "ok"):
            self.hub.set_device_status(DEVICE_ID, Status.OK, reading.detail if reading.status == "ok" else timer.detail)
        else:
            # An on-screen notice only: a venue without Ontime running must never beep.
            self.hub.set_device_status(DEVICE_ID, Status.MISSING, reading.detail, silent_alarm=True)
