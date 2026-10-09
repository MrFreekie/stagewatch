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
from ...core.ontimerundown import RundownReading, RundownState
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


# The emulated rundown repeats a 240 second story (seconds into the cycle). 12 events, planned
# 11:30 to 22:30 on Ontime's clock. Ontime's offset sign, confirmed by the owner on a real
# 4.14.0: positive is BEHIND, negative is AHEAD.
#   0-20 not started | 20-50 on time (a few seconds out) | 50-85 falling behind, 0 to 6:00
#   (amber after 0:30, orange after the default 5 minute step) | 85-100 holding 6:00 behind
#   | 100-115 nothing arrives (stale, still orange underneath) | 115-155 catching up, 6:00 behind
#   to 1:30 ahead | 155-175 ahead, easing back to 0 | 175-195 Ontime has no rundown loaded
#   | 195-225 finished | 225-240 Ontime offline.
RUNDOWN_CYCLE_S = 240.0
RUNDOWN_EVENTS = 12
RUNDOWN_PLANNED_START_MS = 41_400_000     # 11:30
RUNDOWN_PLANNED_END_MS = 81_000_000       # 22:30
RUNDOWN_PEAK_BEHIND_MS = 360_000          # 6:00 behind, past the default 5 minute orange step
RUNDOWN_STALE = (100.0, 115.0)
RUNDOWN_OFFLINE = (225.0, 240.0)


def emulated_rundown_clock(t: float) -> int:
    """Ontime's clock in the emulated story: the planned day squeezed into one cycle."""
    span = RUNDOWN_PLANNED_END_MS - RUNDOWN_PLANNED_START_MS
    return RUNDOWN_PLANNED_START_MS + int((t % RUNDOWN_CYCLE_S) / RUNDOWN_CYCLE_S * span)


def emulated_rundown_state(t: float) -> RundownState:
    """The emulated rundown ``t`` seconds into its 240 s cycle. Pure and deterministic.
    Offset in ms: negative is behind."""
    t = t % RUNDOWN_CYCLE_S
    base = dict(num_events=RUNDOWN_EVENTS, planned_start_ms=RUNDOWN_PLANNED_START_MS,
                planned_end_ms=RUNDOWN_PLANNED_END_MS, current_day=0, offset_mode="absolute")

    def state(index: int | None, ahead_ms: int | None, started: bool = True) -> RundownState:
        """``ahead_ms`` is the story's ahead (+) / behind (-); Ontime's own offset is the other way round."""
        offset = None if ahead_ms is None else -ahead_ms
        return RundownState(
            selected_index=index, actual_start_ms=RUNDOWN_PLANNED_START_MS if started else None,
            offset_absolute_ms=offset, offset_relative_ms=offset,
            offset_expected_end_ms=None if offset is None else RUNDOWN_PLANNED_END_MS + offset,
            event_title="" if index is None else "Emulated: Support act",
            event_note="" if index is None or index % 2 == 0 else "Emulated note: IEM check before the changeover.", **base)

    if t < 20:
        return state(None, None, started=False)
    if t < 50:
        return state(1, int((t - 35) * 1000))                          # within a few seconds either way
    if t < 85:
        return state(3 + int((t - 50) // 12), -int((t - 50) / 35 * RUNDOWN_PEAK_BEHIND_MS))
    if t < 115:
        return state(6, -RUNDOWN_PEAK_BEHIND_MS)
    if t < 155:
        return state(7 + int((t - 115) // 20), int(-RUNDOWN_PEAK_BEHIND_MS + (t - 115) / 40 * (RUNDOWN_PEAK_BEHIND_MS + 90_000)))
    if t < 175:
        return state(9, int(90_000 - (t - 155) / 20 * 90_000))
    if t < 195:
        return RundownState(num_events=0, current_day=0)               # Ontime has no rundown loaded
    return state(None, 0)                                              # finished


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

    def latest_rundown(self) -> RundownReading:
        """The emulated Ontime rundown (see RUNDOWN_* above). With ``cycle`` it also goes stale and
        offline in its own windows; without, it is offline whenever the clock drops out."""
        now = self._time()
        if self._cycle:
            t = (now - self._t0) % RUNDOWN_CYCLE_S
            if RUNDOWN_OFFLINE[0] <= t < RUNDOWN_OFFLINE[1]:
                return RundownReading(None, now, "offline", "Emulated dropout")
            if RUNDOWN_STALE[0] <= t < RUNDOWN_STALE[1]:
                age = t - RUNDOWN_STALE[0]
                return RundownReading(emulated_rundown_state(RUNDOWN_STALE[0]), now - age, "ok", "Emulated",
                                      emulated_rundown_clock(RUNDOWN_STALE[0]))
            return RundownReading(emulated_rundown_state(t), now, "ok", "Emulated", emulated_rundown_clock(t))
        if self.latest().status != "ok":
            return RundownReading(None, now, "offline", "Emulated dropout")
        t = (now - self._t0) % RUNDOWN_CYCLE_S
        return RundownReading(emulated_rundown_state(t), now, "ok", "Emulated", emulated_rundown_clock(t))

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
