"""'Sensor in the sun' advisory: spot an air-temperature sensor that direct sun is heating.

A temperature sensor whose housing is in direct sun reads the housing, not the air: several degrees
too warm, and it climbs far faster than the air around it. A sensor reading well above the median of all sensors
is flagged and kept out of the site temperature average (never half or more of them). This is advice only: no alarm, sound or marker, and nothing is ever
back-filled or assumed.
"""

from __future__ import annotations

import statistics
from collections import deque

# All the thresholds live here. Air temperature at a venue seldom moves more than 1-2 degC in ten
# minutes, and sensors a few metres apart agree within about 1-2 degC in shade. A housing in direct
# sun commonly reads 5-10 degC high and warms by several degC in minutes, so these sit well clear of
# ordinary weather and ordinary sensor-to-sensor spread, and the flag needs the condition to hold.
SUN_ABOVE_OTHERS_C = 4.0     # reads this far above the median of ALL the site sensors (itself included)...
SUN_HOLD_S = 600.0           # ...for this long (10 min) of daytime before it is flagged
SUN_CLEAR_ABOVE_C = 2.0      # back within this of the median...
SUN_CLEAR_HOLD_S = 300.0     # ...for this long (5 min) before the flag clears (no clearing on one reading)
SUN_MIN_SENSORS = 3          # the level rule needs three: with two there is no telling which one is odd
SUN_RISE_C = 3.0             # or: warmed by this much...
SUN_RISE_WINDOW_S = 600.0    # ...within this window (0.3 degC/min), while the others did not follow...
SUN_RISE_END_ABOVE_C = 2.0   # ...and ending at least this far above the others (not just warming from below)
SUN_CLEAR_RISE_C = 1.0       # cleared once the rise over the window is under this
# No site location is configured, so the daytime window is wide and fixed, in minutes after midnight of
# site time: 05:00 to 21:30 covers sunrise and sunset anywhere Stagewatch is likely to be used. A
# sunrise/sunset calculation from a site location could replace it later.
SUN_DAY_START_MIN = 5 * 60
SUN_DAY_END_MIN = 21 * 60 + 30


def is_daytime(hour: int, minute: int = 0) -> bool:
    return SUN_DAY_START_MIN <= hour * 60 + minute < SUN_DAY_END_MIN


def max_kept_out(n: int) -> int:
    """Never keep out half or more of the sensors."""
    return (n - 1) // 2


class SunWatch:
    """Remembers a short history per temperature sensor and says which ones look sun-heated."""

    def __init__(self) -> None:
        self._hist: dict[str, deque] = {}     # id -> deque of (ts, value), at most one window long
        self._since: dict[str, float] = {}    # id -> when it first read SUN_ABOVE_OTHERS_C above the others
        self._clear_since: dict[str, float] = {}   # id -> when a flagged sensor first looked normal again
        self._gone_since: dict[str, float] = {}    # id -> when a flagged sensor stopped reporting
        self._full_n = 0                      # how many sensors there were when last there were enough to judge...
        self._full_at = -1e9                  # ...and when
        self.flagged: set[str] = set()

    def _rise(self, sid: str, now: float) -> float | None:
        """Change over the window, or None until the history covers 90 % of it."""
        h = self._hist.get(sid)
        if not h or now - h[0][0] < 0.9 * SUN_RISE_WINDOW_S:
            return None
        return h[-1][1] - h[0][1]

    def update(self, readings: dict[str, float], now: float, daytime: bool) -> set[str]:
        """``readings``: the live environment temperature sensors (id -> degC). Returns the ids
        flagged now. A sensor that drops out (stale, ticked off) loses its history, and its flag once it has
        been away for SUN_CLEAR_HOLD_S. A flag also survives a short dropout of another sensor (fewer than
        SUN_MIN_SENSORS left): it is judged against the count before, and clears only after the usual hold."""
        for sid in list(self._hist):
            if sid in readings:
                self._gone_since.pop(sid, None)
                continue
            if sid in self.flagged:
                if now - self._gone_since.setdefault(sid, now) < SUN_CLEAR_HOLD_S:
                    continue                 # a short dropout: keep the flag for when it comes back
            for d in (self._hist, self._since, self._clear_since, self._gone_since):
                d.pop(sid, None)
            self.flagged.discard(sid)
        for sid, v in readings.items():
            h = self._hist.setdefault(sid, deque())
            h.append((now, v))
            while len(h) > 1 and now - h[1][0] >= SUN_RISE_WINDOW_S:
                h.popleft()   # keep the newest sample at or before the window start as its anchor
        n = len(readings)
        if n >= SUN_MIN_SENSORS:
            self._full_n, self._full_at = n, now
        n_cap = self._full_n if n < SUN_MIN_SENSORS and now - self._full_at <= SUN_CLEAR_HOLD_S else n
        rises = {sid: self._rise(sid, now) for sid in readings}
        mid = statistics.median(readings.values()) if readings else 0.0
        excesses: dict[str, float | None] = {}
        for sid, v in readings.items():
            excess = v - mid if n >= SUN_MIN_SENSORS else None
            excesses[sid] = excess
            if daytime and excess is not None and excess >= SUN_ABOVE_OTHERS_C:
                self._since.setdefault(sid, now)
            else:
                self._since.pop(sid, None)   # the timer only runs in daytime
            rise = rises[sid]
            others = [x for o, x in readings.items() if o != sid]
            other_rises = [r for o, r in rises.items() if o != sid and r is not None]
            peers_followed = bool(other_rises) and statistics.median(other_rises) >= rise / 2 if rise is not None else False
            if sid in self.flagged:
                still_hot = excess is not None and excess >= SUN_CLEAR_ABOVE_C
                still_rising = rise is not None and rise >= SUN_CLEAR_RISE_C and not peers_followed
                if still_hot or still_rising:
                    self._clear_since.pop(sid, None)
                else:
                    t0 = self._clear_since.setdefault(sid, now)
                    if now - t0 >= SUN_CLEAR_HOLD_S:
                        self.flagged.discard(sid)
                        self._clear_since.pop(sid, None)
            elif daytime:
                held = sid in self._since and now - self._since[sid] >= SUN_HOLD_S
                above = v - statistics.median(others) if others else None
                fast = (rise is not None and rise >= SUN_RISE_C and not peers_followed
                        and (above is None or above >= SUN_RISE_END_ABOVE_C))
                if held or fast:
                    self.flagged.add(sid)
                    self._clear_since.pop(sid, None)
        present = self.flagged & readings.keys()
        if n >= 2 and len(present) > max_kept_out(n_cap):
            keep = sorted(present, key=lambda i: excesses.get(i) or 0.0, reverse=True)[:max_kept_out(n_cap)]
            for sid in present - set(keep):
                self._clear_since.pop(sid, None)
            self.flagged = (self.flagged - present) | set(keep)
            present = set(keep)
        return present
