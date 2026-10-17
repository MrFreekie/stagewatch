"""Noise removal for the tilt readings. Pure: no clock of its own, times are passed in, so it behaves the
same at 2 Hz and at 10 Hz and is easy to test.

Two steps, in this order:
1. a rolling median over the last few frames, which throws away a single spike without smearing it;
2. a time-based exponential moving average with a time constant in seconds (alpha = 1 - exp(-dt / tau)),
   so the amount of smoothing does not depend on the frame rate.

Roll wraps at ±180°, so an angle that wraps is filtered as a point on a circle (its sine and cosine are
filtered and turned back into an angle) and never jumps across the wrap.

Nothing is carried across a gap: ``reset()`` empties the filter, and the first output after it is
produced only from new readings (the median needs ``MIN_SAMPLES`` before it says anything).
"""

from __future__ import annotations

import math
from collections import deque

MEDIAN_FRAMES = 5     # spike rejection window
MIN_SAMPLES = 3       # nothing is output until the median has this many real readings


def _median(values) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class _Channel:
    """Median then EMA for one number."""

    def __init__(self, tau_s: float) -> None:
        self.tau = tau_s
        self.reset()

    def reset(self) -> None:
        self.window: deque[float] = deque(maxlen=MEDIAN_FRAMES)
        self.value: float | None = None
        self.t: float | None = None

    def update(self, x: float, t: float) -> float | None:
        self.window.append(x)
        if len(self.window) < MIN_SAMPLES:
            return None
        m = _median(self.window)
        if self.tau <= 0 or self.value is None or self.t is None:
            self.value = m
        else:
            alpha = 1.0 - math.exp(-max(0.0, t - self.t) / self.tau)
            self.value += alpha * (m - self.value)
        self.t = t
        return self.value


class Smoother:
    """Smooths one reading. ``circular=True`` for an angle that wraps at ±180°."""

    def __init__(self, tau_s: float, circular: bool = False) -> None:
        self.circular = circular
        self._a = _Channel(tau_s)
        self._b = _Channel(tau_s) if circular else None

    def reset(self) -> None:
        self._a.reset()
        if self._b:
            self._b.reset()

    def update(self, x: float, t: float) -> float | None:
        if not self.circular:
            return self._a.update(x, t)
        r = math.radians(x)
        s, c = self._a.update(math.sin(r), t), self._b.update(math.cos(r), t)
        if s is None or c is None:
            return None
        return math.degrees(math.atan2(s, c))
