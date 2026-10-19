"""Rebuilding a calculated figure from what was already recorded for the show day, without stopping the hub.

After a restart (or a new show day) a running figure (demand, a day's highest) is rebuilt from the readings
recorded so far. A show day can hold tens of thousands of readings for each of dozens of figures, which on the
event loop froze the whole server for seconds to minutes (the freeze of 19 October 2026). Now:

* a short history (``SYNC_ROWS`` or fewer readings) is replayed at once, as before;
* a long one is replayed on a worker thread with its own read-only database connection. Readings that arrive
  meanwhile are held and fed in order afterwards, so nothing is lost or invented. Until it is done the figure
  has no value (``ready`` is False): never a partial number.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

SYNC_ROWS = 300          # at most this many recorded readings are replayed on the event loop
MAX_HELD = 20_000        # readings held while a rebuild runs (far more than a rebuild ever takes to finish)
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sw-replay")


class Rebuild:
    """Feeds ``target`` (anything with ``feed(value, ts)`` and ``last_ts``) from the recorder, then live readings."""

    def __init__(self, recorder, entity_id: str, since, until: float, target) -> None:
        """``since`` is a function giving the start of the show day (it may fail: then the figure starts afresh)."""
        self.target = target
        self._held: list[tuple[float, float]] = []
        self._fut = None
        self._done = False
        try:
            since = since()
            if recorder.count_raw_states(entity_id, since, until, SYNC_ROWS + 1) <= SYNC_ROWS:
                for t, v in recorder.raw_states(entity_id, since, until):
                    target.feed(v, t)
                self._done = True
            else:
                self._fut = _POOL.submit(self._load, recorder, entity_id, since, until)
        except Exception:  # noqa: BLE001 - no history just means the figure starts from the next reading
            log.exception("Shelly: could not read the day so far")
            self._done = True

    def _load(self, recorder, entity_id: str, since: float, until: float) -> None:
        for t, v in recorder.raw_states_threaded(entity_id, since, until):
            self.target.feed(v, t)

    @property
    def ready(self) -> bool:
        if self._done:
            return True
        if self._fut is None or not self._fut.done():
            return False
        try:
            self._fut.result()
        except Exception:  # noqa: BLE001
            log.exception("Shelly: could not read the day so far")
        last = self.target.last_ts
        for v, ts in self._held:
            if last is None or ts > last:
                self.target.feed(v, ts)
                last = ts
        self._held = []
        self._done = True
        return True

    def feed(self, value: float, ts: float) -> None:
        """A live reading: straight to the target once the rebuild is done, else held."""
        if self.ready:
            self.target.feed(value, ts)
        elif len(self._held) < MAX_HELD:
            self._held.append((value, ts))
        elif len(self._held) == MAX_HELD:
            self._held.append((value, ts))
            log.warning("Shelly: the day so far is taking very long to read back; later readings are not kept for it")
