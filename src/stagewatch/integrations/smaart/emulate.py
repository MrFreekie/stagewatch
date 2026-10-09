"""Emulated sound-level source: made-up numbers, no network, no Smaart.

Used by ``--emulate`` and by the tests. It behaves like a Smaart that offers the A and C weighted
Slow and Fast levels and the A and C Leq, and does NOT offer the Z weighted values or the peaks, so
choosing one of those shows the "not available" path. About every five minutes it drops out for ten
seconds (the link goes down, then comes back), so the gap, the missing-data look and the
"resumed after a gap" marker can all be seen offline.

The numbers are synthetic. They are not worked out from each other, are not real measurements and
mean nothing for a licence limit. The emulated Leq is just another slow wander, not an average.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Callable

from ...core.spl import SplReading
from .source import LinkCallback, ReadingCallback, SplSource

OFFERED = ("a_slow", "c_slow", "a_fast", "c_fast", "laeq_15m", "lceq_15m")   # the rest are "not available"
VERSION = "9.0 (emulated)"


class EmulatedSplSource(SplSource):
    verified = False
    label = "Simulated Smaart"

    def __init__(self, on_reading: ReadingCallback, on_link: LinkCallback, *, period_s: float = 1.0,
                 first_outage_s: float = 60.0, outage_every_s: float = 300.0, outage_s: float = 10.0,
                 clock: Callable[[], float] = time.time, seed: int = 7) -> None:
        super().__init__(on_reading, on_link)
        self._period = period_s
        self._first, self._every, self._outage = first_outage_s, outage_every_s, outage_s
        self._time = clock
        self._rng = random.Random(seed)
        self._task: asyncio.Task | None = None
        self._t0 = clock()
        self._up = False

    async def start(self) -> None:
        if self._task is None:
            self._t0 = self._time()
            self._up = False
            self.version = VERSION
            self._task = asyncio.create_task(self._run(), name="smaart-emulated")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def in_outage(self, elapsed: float) -> bool:
        """True while the simulated Smaart is unreachable (elapsed seconds since start)."""
        if elapsed < self._first:
            return False
        return (elapsed - self._first) % self._every < self._outage

    def values(self, elapsed: float) -> dict[str, float | None]:
        """One simulated set of values. Only the OFFERED metrics are present."""
        r = self._rng
        song = 94.0 + 6.0 * math.sin(elapsed / 37.0) + 2.0 * math.sin(elapsed / 9.0)
        a_fast = song + r.uniform(-2.0, 2.0)
        a_slow = song + r.uniform(-0.4, 0.4)
        c_slow = a_slow + 5.0 + r.uniform(-0.3, 0.3)
        c_fast = a_fast + 5.0 + r.uniform(-0.3, 0.3)
        leq = 95.0 + 1.5 * math.sin(elapsed / 240.0)
        return {"a_slow": a_slow, "c_slow": c_slow, "a_fast": a_fast, "c_fast": c_fast,
                "laeq_15m": leq, "lceq_15m": leq + 5.0}

    async def _run(self) -> None:
        while True:
            elapsed = self._time() - self._t0
            down = self.in_outage(elapsed)
            if down and self._up:
                self._up = False
                self._on_link(False, "Can't reach Smaart (simulated dropout)")
            elif not down and not self._up:
                self._up = True
                self._on_link(True, "Connected (simulated)")
            if self._up:
                self._on_reading(SplReading(self._time(), self.values(elapsed), VERSION))
            await asyncio.sleep(self._period)
