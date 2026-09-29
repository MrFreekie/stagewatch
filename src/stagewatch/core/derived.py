"""Derived values: robust averaging across sensors and time smoothing."""

from __future__ import annotations

import math
import statistics

OUTLIER_LIMITS = {
    "temperature": 3.0,   # degC from the median
    "humidity": 15.0,     # % RH
    "pressure": 500.0,    # Pa (~5 hPa: a sensor that far off is broken or mis-set)
}


def robust_mean(values: list[float], max_deviation: float | None) -> tuple[float | None, int]:
    """Mean of values, dropping any further than max_deviation from the
    median. Rejection needs at least three sensors -- with two there is no
    way to tell which one is wrong. Returns (mean, number of values used)."""
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None, 0
    if max_deviation is not None and len(vals) >= 3:
        med = statistics.median(vals)
        kept = [v for v in vals if abs(v - med) <= max_deviation]
        vals = kept or vals
    return sum(vals) / len(vals), len(vals)


class Ema:
    """Exponential moving average for irregularly spaced samples.

    tau_s = 0 passes values straight through. A gap longer than 5*tau
    resets the average so a sensor coming back online doesn't drag stale
    history into the new value."""

    def __init__(self, tau_s: float) -> None:
        self.tau_s = tau_s
        self.value: float | None = None
        self._ts: float | None = None

    def update(self, value: float, ts: float) -> float:
        if self.value is None or self._ts is None or self.tau_s <= 0:
            self.value = value
        else:
            dt = max(ts - self._ts, 0.0)
            if dt > 5 * self.tau_s:
                self.value = value
            else:
                alpha = 1.0 - math.exp(-dt / self.tau_s)
                self.value += alpha * (value - self.value)
        self._ts = ts
        return self.value
