"""Emulated Ontime clock: no network. It reports site time plus half a second, and drops out
for 20 seconds every 15 minutes so the offline path (silent alarm, "Ontime offline" on the
card) can be seen and tested.

With ``cycle=True`` (emulate mode) it instead repeats a two-minute story so every state of the
Wall Clock card can be watched: live, then 15 s running 3.2 s ahead (differs), 15 s with nothing
received (stale), 15 s offline, then live again."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ...core import sitetime
from ...core.wallclock import ClockReading

log = logging.getLogger(__name__)

LEAD_S = 0.5
DROPOUT_EVERY_S = 900.0
DROPOUT_S = 20.0
CYCLE_S = 120.0
# (start, end) in seconds into the cycle.
CYCLE_DIFFERS = (50.0, 65.0)
CYCLE_STALE = (65.0, 80.0)
CYCLE_OFFLINE = (80.0, 95.0)
CYCLE_AHEAD_S = 3.2


class EmulatedClock:
    name = "ontime"
    label = "Ontime"

    def __init__(self, site_fn: Callable[[], object], on_change: Callable[[ClockReading], None] | None = None,
                 clock: Callable[[], float] = time.time, dropout_every_s: float = DROPOUT_EVERY_S,
                 dropout_s: float = DROPOUT_S, lead_s: float = LEAD_S, cycle: bool = False) -> None:
        self._site_fn = site_fn
        self._on_change = on_change
        self._time = clock
        self._every, self._down, self._lead = dropout_every_s, dropout_s, lead_s
        self._cycle = cycle
        self._t0 = clock()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._t0 = self._time()
        if self._on_change:
            self._on_change(self.latest())
        self._task = asyncio.create_task(self._run(), name="ontime-emulated")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def latest(self) -> ClockReading:
        now = self._time()
        if self._cycle:
            return self._cycle_reading(now)
        if (now - self._t0) % self._every >= self._every - self._down:
            return ClockReading(None, now, "offline", "Emulated dropout")
        ms = sitetime.ms_since_local_midnight(now + self._lead, self._site_fn())
        return ClockReading(ms, now, "ok", "Emulated")

    def _cycle_reading(self, now: float) -> ClockReading:
        t = (now - self._t0) % CYCLE_S
        if CYCLE_OFFLINE[0] <= t < CYCLE_OFFLINE[1]:
            return ClockReading(None, now, "offline", "Emulated dropout")
        if CYCLE_STALE[0] <= t < CYCLE_STALE[1]:
            # Nothing new arrived since the stale window began: the last reading, ageing.
            began = now - (t - CYCLE_STALE[0])
            # (It was running ahead when it stopped.)
            return ClockReading(sitetime.ms_since_local_midnight(began + self._lead + CYCLE_AHEAD_S, self._site_fn()),
                                began, "ok", "Emulated")
        ahead = CYCLE_AHEAD_S if CYCLE_DIFFERS[0] <= t < CYCLE_DIFFERS[1] else 0.0
        ms = sitetime.ms_since_local_midnight(now + self._lead + ahead, self._site_fn())
        return ClockReading(ms, now, "ok", "Emulated")

    def details(self) -> dict:
        return {"version": "emulated", "transport": "emulated"}

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                if self._on_change:
                    self._on_change(self.latest())
            except Exception:  # noqa: BLE001
                log.exception("Emulated Ontime clock update failed")
