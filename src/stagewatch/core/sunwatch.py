"""'Sensor in the sun' advisory: spot an air-temperature sensor that direct sun is heating.

A temperature sensor whose housing is in direct sun reads the housing, not the air: several degrees
too warm, and it climbs far faster than the air around it. Such a reading is flagged and kept out of
the site temperature average. This is advice only: no alarm, sound or marker, and nothing is ever
back-filled or assumed.
"""

from __future__ import annotations

import statistics
from collections import deque

# All the thresholds live here. Air temperature at a venue seldom moves more than 1-2 degC in ten
# minutes, and sensors a few metres apart agree within about 1-2 degC in shade. A housing in direct
# sun commonly reads 5-10 degC high and warms by several degC in minutes, so these sit well clear of
# ordinary weather and ordinary sensor-to-sensor spread, and the flag needs the condition to hold.
SUN_ABOVE_OTHERS_C = 4.0     # reads this far above the median of the other site sensors...
SUN_HOLD_S = 600.0           # ...for this long (10 min) before it is flagged
SUN_CLEAR_ABOVE_C = 2.0      # cleared once it is back within this of the others (hysteresis: no flicker)
SUN_MIN_OTHERS = 2           # "the others" needs at least two, so one odd peer cannot be mistaken for the sun
SUN_RISE_C = 3.0             # or: warmed by this much...
SUN_RISE_WINDOW_S = 600.0    # ...within this window (0.3 degC/min), while the others did not follow
SUN_CLEAR_RISE_C = 1.0       # cleared once the rise over the window is under this
SUN_DAY_START_H = 8          # only judged between these site-local hours; sun cannot be the cause at night
SUN_DAY_END_H = 19


def is_daytime(hour: int) -> bool:
    return SUN_DAY_START_H <= hour < SUN_DAY_END_H


class SunWatch:
    """Remembers a short history per temperature sensor and says which ones look sun-heated."""

    def __init__(self) -> None:
        self._hist: dict[str, deque] = {}     # id -> deque of (ts, value), at most one window long
        self._since: dict[str, float] = {}    # id -> when it first read SUN_ABOVE_OTHERS_C above the others
        self.flagged: set[str] = set()

    def _rise(self, sid: str, now: float) -> float | None:
        """Change over the window, or None until the history covers 90 % of it."""
        h = self._hist.get(sid)
        if not h or now - h[0][0] < 0.9 * SUN_RISE_WINDOW_S:
            return None
        return h[-1][1] - h[0][1]

    def update(self, readings: dict[str, float], now: float, daytime: bool) -> set[str]:
        """``readings``: the live environment temperature sensors (id -> degC). Returns the ids
        flagged now. A sensor that drops out (stale, ticked off) loses its history and flag."""
        for sid in list(self._hist):
            if sid not in readings:
                self._hist.pop(sid, None)
                self._since.pop(sid, None)
                self.flagged.discard(sid)
        for sid, v in readings.items():
            h = self._hist.setdefault(sid, deque())
            h.append((now, v))
            while len(h) > 1 and now - h[1][0] >= SUN_RISE_WINDOW_S:
                h.popleft()   # keep the newest sample at or before the window start as its anchor
        rises = {sid: self._rise(sid, now) for sid in readings}
        for sid, v in readings.items():
            others = [x for o, x in readings.items() if o != sid]
            excess = v - statistics.median(others) if len(others) >= SUN_MIN_OTHERS else None
            if excess is not None and excess >= SUN_ABOVE_OTHERS_C:
                self._since.setdefault(sid, now)
            else:
                self._since.pop(sid, None)
            rise = rises[sid]
            other_rises = [r for o, r in rises.items() if o != sid and r is not None]
            peers_followed = bool(other_rises) and statistics.median(other_rises) >= rise / 2 if rise is not None else False
            if sid in self.flagged:
                still_hot = excess is not None and excess >= SUN_CLEAR_ABOVE_C
                still_rising = rise is not None and rise >= SUN_CLEAR_RISE_C and not peers_followed
                if not (still_hot or still_rising):
                    self.flagged.discard(sid)
            elif daytime:
                held = sid in self._since and now - self._since[sid] >= SUN_HOLD_S
                fast = rise is not None and rise >= SUN_RISE_C and not peers_followed
                if held or fast:
                    self.flagged.add(sid)
        return set(self.flagged)
