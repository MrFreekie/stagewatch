"""Self-protection against anything that holds the event loop for too long.

A server whose event loop is blocked answers nothing: no dashboard, no health check. The launcher then kills it
(the freeze of 19 October 2026). Two cheap guards put the culprit in the log, so a diagnostics bundle names it:

* ``timed(name)``: wrap a handler; a WARNING names it when it held the loop for more than ``SLOW_S``.
* ``LoopWatchdog``: a thread that notices the loop has not run for ``STALL_S`` and logs where the loop thread is
  stuck right now (the top of its call stack), once per stall. It only reads; it never touches the loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from contextlib import contextmanager

log = logging.getLogger(__name__)

SLOW_S = 1.0             # a section holding the loop longer than this is logged
STALL_S = 2.0            # the watchdog reports a loop that has not run for this long
BEAT_S = 0.25            # how often the loop proves it is alive
REPEAT_S = 30.0          # the same section name is logged at most this often
_last_logged: dict[str, float] = {}


@contextmanager
def timed(name: str, limit_s: float = SLOW_S):
    """``with timed("Shelly poll m1"):`` logs a WARNING if the block takes longer than ``limit_s``."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        took = time.perf_counter() - t0
        if took > limit_s:
            now = time.monotonic()
            if now - _last_logged.get(name, -1e9) >= REPEAT_S:
                _last_logged[name] = now
                log.warning("Slow section: %s held the event loop for %.1f s", name, took)


class LoopWatchdog:
    def __init__(self, stall_s: float = STALL_S) -> None:
        self.stall_s = stall_s
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._loop_thread_id: int | None = None
        self.reports = 0

    def start(self) -> None:
        self._loop_thread_id = threading.get_ident()
        self._beat = time.monotonic()
        self._task = asyncio.create_task(self._pulse(), name="loop-watchdog")
        self._thread = threading.Thread(target=self._watch, name="sw-loop-watchdog", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _pulse(self) -> None:
        while True:
            self._beat = time.monotonic()
            await asyncio.sleep(BEAT_S)

    def _watch(self) -> None:
        reported = False
        while not self._stop.wait(BEAT_S):
            late = time.monotonic() - self._beat
            if late < self.stall_s:
                reported = False
                continue
            if reported:
                continue
            reported = True
            self.reports += 1
            frame = sys._current_frames().get(self._loop_thread_id)
            where = "".join(traceback.format_stack(frame, limit=6)).strip() if frame is not None else "unknown"
            log.warning("The event loop has not run for %.1f s; it is busy in:\n%s", late, where)
