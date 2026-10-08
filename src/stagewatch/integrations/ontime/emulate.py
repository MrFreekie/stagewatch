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
from ...core.ontimetimer import TimerReading, TimerState
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


# The emulated timer repeats a three-minute story (seconds into the cycle). A 60 s item with
# Ontime-style warning and danger times of 20 s and 10 s:
#   0-20 running | 20-30 paused | 30-120 running again, +0:20 added at 30, out of time at 90
#   (overtime to -0:30) | 120-135 stopped | 135-150 armed (ready) | 150-165 Ontime offline
#   | 165-180 armed. Nothing arrives from 60 to 70 (stale), in the middle of the run.
TIMER_CYCLE_S = 180.0
TIMER_DURATION_MS = 60_000
TIMER_ADDED_MS = 20_000
TIMER_WARN_MS = 20_000
TIMER_DANGER_MS = 10_000
TIMER_STALE = (60.0, 70.0)
TIMER_OFFLINE = (150.0, 165.0)
TIMER_TITLE = "Emulated: Support act"


def emulated_timer_state(t: float) -> TimerState:
    """The emulated timer ``t`` seconds into its 180 s cycle. Pure and deterministic."""
    t = t % TIMER_CYCLE_S
    ms = int(round(t * 1000))

    def state(playback: str, current: int | None, added: int, elapsed: int | None, phase: str = "default") -> TimerState:
        return TimerState(playback=playback, phase=phase, current_ms=current, duration_ms=TIMER_DURATION_MS,
                          elapsed_ms=elapsed, added_ms=added, has_event=True, title=TIMER_TITLE,
                          timer_type="count-down", warn_ms=TIMER_WARN_MS, danger_ms=TIMER_DANGER_MS)

    def running(played_ms: int, added: int) -> TimerState:
        current = TIMER_DURATION_MS + added - played_ms
        phase = ("overtime" if current < 0 else "danger" if current <= TIMER_DANGER_MS
                 else "warning" if current <= TIMER_WARN_MS else "default")
        return state("play", current, added, played_ms, phase)

    if t < 20:
        return running(ms, 0)
    if t < 30:
        return state("pause", TIMER_DURATION_MS - 20_000, 0, 20_000)
    if t < 120:
        return running(20_000 + (ms - 30_000), TIMER_ADDED_MS)
    if t < 135:
        return state("stop", None, 0, None)
    return state("armed", TIMER_DURATION_MS, 0, 0)


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

    def latest_timer(self) -> TimerReading:
        """The emulated Ontime timer (see TIMER_* above). With ``cycle`` it also goes stale and
        offline in its own windows; without, it is offline whenever the clock drops out."""
        now = self._time()
        if self._cycle:
            t = (now - self._t0) % TIMER_CYCLE_S
            if TIMER_OFFLINE[0] <= t < TIMER_OFFLINE[1]:
                return TimerReading(None, now, "offline", "Emulated dropout")
            if TIMER_STALE[0] <= t < TIMER_STALE[1]:
                return TimerReading(emulated_timer_state(TIMER_STALE[0]), now - (t - TIMER_STALE[0]), "ok", "Emulated")
            return TimerReading(emulated_timer_state(t), now, "ok", "Emulated")
        if self.latest().status != "ok":
            return TimerReading(None, now, "offline", "Emulated dropout")
        return TimerReading(emulated_timer_state((now - self._t0) % TIMER_CYCLE_S), now, "ok", "Emulated")

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
