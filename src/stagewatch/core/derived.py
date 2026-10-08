"""Derived values: robust averaging across sensors and time smoothing."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

OUTLIER_LIMITS = {
    "temperature": 3.0,   # degC from the median
    "humidity": 15.0,     # % RH
    "pressure": 500.0,    # Pa (~5 hPa: a sensor that far off is broken or mis-set)
}


def _kept_indices(vals: list[float], max_deviation: float | None) -> list[int]:
    """Indices of the values that stay in the average: all of them, unless outlier rejection is on
    (``max_deviation``), there are at least three, and some are further than ``max_deviation``
    from the median. If rejection would leave nothing, everything stays."""
    idx = list(range(len(vals)))
    if max_deviation is not None and len(vals) >= 3:
        med = statistics.median(vals)
        kept = [i for i in idx if abs(vals[i] - med) <= max_deviation]
        return kept or idx
    return idx


def robust_mean(values: list[float], max_deviation: float | None) -> tuple[float | None, int]:
    """Mean of values, dropping any further than max_deviation from the
    median. Rejection needs at least three sensors -- with two there is no
    way to tell which one is wrong. Returns (mean, number of values used)."""
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None, 0
    vals = [vals[i] for i in _kept_indices(vals, max_deviation)]
    return sum(vals) / len(vals), len(vals)


@dataclass(frozen=True)
class AvgInput:
    """One sensor going into a site average. ``accuracy`` is in canonical units (None = no figure);
    ``basis`` is "typical" or "maximum"."""
    id: str
    value: float
    accuracy: float | None = None
    basis: str = "typical"


@dataclass
class AvgResult:
    mean: float | None
    used: int
    shares: dict[str, float] = field(default_factory=dict)   # sensor id -> share of the average (sums to 1)
    rejected: list[str] = field(default_factory=list)        # ids dropped by outlier rejection
    weighted: bool = False                                    # True only if accuracy weights were used
    reason: str = ""    # why weighting was not used: "off", "missing", "mixed" or "" (n/a or weighted)
    missing: int = 0    # included sensors with no accuracy figure (reason "missing")


def _usable(acc: float | None) -> bool:
    return acc is not None and math.isfinite(acc) and acc > 0


def average(inputs: list[AvgInput], max_deviation: float | None, weight_by_accuracy: bool) -> AvgResult:
    """Site average of one kind, optionally weighted by accuracy.

    1. Outlier rejection works exactly as in ``robust_mean`` (it looks at the values only).
    2. If ``weight_by_accuracy`` is on, and every sensor still in the average has an accuracy
       figure, and the figures all have the same basis (typical or maximum), each gets the
       inverse-variance weight ``w = 1 / accuracy**2``. The mean is ``sum(w*v) / sum(w)`` over the
       sensors that stayed in, so the weights are renormalised after any sensor drops out.
    3. Otherwise every sensor counts equally (``reason`` says why).
    Sensors that were left out earlier (stale, switched off) must not be passed in."""
    live = [i for i in inputs if i.value is not None and math.isfinite(i.value)]
    if not live:
        return AvgResult(None, 0)
    keep = _kept_indices([i.value for i in live], max_deviation)
    kept = [live[i] for i in keep]
    keep_set = set(keep)
    rejected = [live[i].id for i in range(len(live)) if i not in keep_set]
    n = len(kept)
    weights = None
    reason, missing = "", 0
    if not weight_by_accuracy:
        reason = "off"
    elif n >= 2:
        missing = sum(1 for k in kept if not _usable(k.accuracy))
        if missing:
            reason = "missing"
        elif len({k.basis for k in kept}) > 1:
            reason = "mixed"
        else:
            weights = [1.0 / (k.accuracy ** 2) for k in kept]
    if weights is None:
        # Equal weights: the same arithmetic as robust_mean, so nothing changes when weighting is off.
        mean = sum(k.value for k in kept) / n
        shares = {k.id: 1.0 / n for k in kept}
        return AvgResult(mean, n, shares, rejected, False, reason, missing)
    total = sum(weights)
    shares = {k.id: w / total for k, w in zip(kept, weights)}
    mean = sum(shares[k.id] * k.value for k in kept)
    return AvgResult(mean, n, shares, rejected, True, "", 0)


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
