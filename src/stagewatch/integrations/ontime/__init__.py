"""Ontime integration: the show clock for the Wall Clock card.

Read-only. It listens to an Ontime server (https://github.com/cpvalente/ontime) for the time of
day and nothing else, and sends nothing to it. It does not read the rundown, timers or messages.

It runs only while a dashboard has the Wall Clock card: the Wall Clock service
(core/wallclock.py) starts and stops its clock source. The "Ontime" device appears under
Integrations in the admin page while the source runs; if Ontime can't be reached it goes
MISSING with a *silent* alarm (an on-screen notice, never a sound).
"""

from __future__ import annotations

import logging

from ...core.model import Device, Status
from ...core.plugin import Integration, Manifest
from ...core.wallclock import ClockReading
from .client import OntimeSource
from .emulate import EmulatedClock

log = logging.getLogger(__name__)

DEVICE_ID = "ontime"

MANIFEST = Manifest(
    domain="ontime",
    name="Ontime",
    version="0.1.0",
    description="Reads the time of day from an Ontime server (WebSocket, falling back to HTTP "
                "polling) so the Wall Clock card can show it and warn if it differs from "
                "Stagewatch. Read-only: sends nothing to Ontime and does not read the rundown, "
                "timers or messages. Runs only while a dashboard has the Wall Clock card.",
    tier="experimental",
    direction="in",
    protocols=("WebSocket", "HTTP"),
)


class _ManagedSource:
    """What the Wall Clock service sees: the clock source, plus the "Ontime" device that exists
    only while it runs."""

    def __init__(self, owner: "OntimeIntegration", inner) -> None:
        self._owner, self._inner = owner, inner
        self.name, self.label = inner.name, inner.label

    async def start(self) -> None:
        self._owner._register()
        await self._inner.start()

    async def stop(self) -> None:
        await self._inner.stop()
        self._owner._unregister()

    def latest(self) -> ClockReading:
        return self._inner.latest()

    def details(self) -> dict:
        return self._inner.details()


class OntimeIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, inner=None) -> None:
        super().__init__(hub, emulate)
        if inner is None:
            if emulate:
                inner = EmulatedClock(lambda: hub.config.site, on_change=self._on_reading)
            else:
                inner = OntimeSource(lambda: hub.config.wall_clock.ontime_url, on_change=self._on_reading)
        else:
            inner._on_change = self._on_reading
        self.clock_source = _ManagedSource(self, inner)
        self._registered = False

    # The source is started and stopped by the Wall Clock service, not by the hub.
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
            DEVICE_ID, "Ontime", "ontime", "Ontime", "Rundown and show timer",
            status=Status.INITIALIZING, status_detail="Connecting", category="service"))
        self._registered = True

    def _unregister(self) -> None:
        if self._registered:
            self._registered = False
            self.hub.remove_device(DEVICE_ID)

    def _on_reading(self, reading: ClockReading) -> None:
        if not self._registered:
            return
        if reading.status == "ok":
            self.hub.set_device_status(DEVICE_ID, Status.OK, reading.detail)
        else:
            # An on-screen notice only: a venue without Ontime running must never beep.
            self.hub.set_device_status(DEVICE_ID, Status.MISSING, reading.detail, silent_alarm=True)
