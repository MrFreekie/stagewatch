"""A rough battery trend for a WitMotion sensor: "about 5 h left" while it runs down, "Charging" while the
voltage clearly rises. Worked out only from the battery voltages heard since the sensor last connected (nothing is
carried across a lost link) and only once there is enough of them. An estimate for the card, never recorded and
never used by an alarm. A lithium cell is flat in the middle, so mid-charge it reads optimistic; it firms up as
the battery runs down, which is when it matters."""
from __future__ import annotations

from collections import deque

from . import parser

MIN_SPAN_S = 30 * 60       # no "time left" until the readings cover half an hour
WINDOW_S = 60 * 60         # the trend uses the last hour
MIN_SAMPLES = 20           # the sensor answers about every 45 s: ~40 in half an hour
CHARGE_WINDOW_S = 10 * 60  # "Charging" from the last ten minutes
CHARGE_RISE_V = 0.05       # a clear rise, well above the 0.01 V steps the sensor reports in
STEADY_PCT_H = 0.5         # slower than this is "steady": no time left is shown
MAX_HOURS = 48.0


class BatteryTrend:
    def __init__(self) -> None:
        self._v: deque[tuple[float, float]] = deque()

    def reset(self) -> None:
        self._v.clear()

    def add(self, ts: float, volts: float | None) -> None:
        if volts is None:
            return
        self._v.append((ts, volts))
        while self._v and ts - self._v[0][0] > WINDOW_S:
            self._v.popleft()

    def view(self) -> dict:
        """{"state": "charging" | "discharging" | "steady" | "", "hours_left": float | None}. "" = not enough yet."""
        if len(self._v) < 2:
            return {"state": "", "hours_left": None}
        last_ts = self._v[-1][0]
        recent = [v for t, v in self._v if last_ts - t <= CHARGE_WINDOW_S]
        if len(recent) >= 5 and recent[-1] - min(recent) >= CHARGE_RISE_V - 1e-9:
            return {"state": "charging", "hours_left": None}
        if last_ts - self._v[0][0] < MIN_SPAN_S or len(self._v) < MIN_SAMPLES:
            return {"state": "", "hours_left": None}
        pts = [(t, parser.battery_percent_approx(v)) for t, v in self._v]
        n = len(pts)
        mt = sum(t for t, _ in pts) / n
        mp = sum(p for _, p in pts) / n
        den = sum((t - mt) ** 2 for t, _ in pts)
        slope_h = sum((t - mt) * (p - mp) for t, p in pts) / den * 3600 if den else 0.0   # % per hour
        if slope_h > -STEADY_PCT_H:
            return {"state": "steady", "hours_left": None}
        now_pct = max(0.0, mp + slope_h / 3600 * (last_ts - mt))
        return {"state": "discharging", "hours_left": round(min(MAX_HOURS, now_pct / -slope_h), 1)}
