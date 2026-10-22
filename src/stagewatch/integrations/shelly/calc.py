"""Calculated power figures for the Power card: reactive power, phase unbalance, average and peak demand
with load factor, and a generator size suggestion. Pure: no network, no hub, no clock.

All of these are worked out by Stagewatch from the meters' readings. They are advisory, always labelled
"calculated", and follow the project's "no assumed data" rule: an input that is missing or old gives no
figure (None), never zero and never a guess, and a silence in the readings is a gap, never back-filled.
"""

from __future__ import annotations

import math
from collections import deque

# ---------------------------------------------------------------- settings in one place
DEMAND_WINDOWS_MIN = (5, 15, 30)    # the rolling windows an admin can choose, in minutes
DEFAULT_WINDOW_MIN = 15             # what electricity suppliers commonly use for demand
GAP_FACTOR = 3.0                    # a silence longer than this many poll intervals is a gap
GAP_MIN_S = 30.0                    # ... but never less than this (same as the show-day figures)
UNBALANCE_FLOOR_A = 1.0             # below this mean line current no unbalance is shown (noise at near-zero load)
REACTIVE_TOLERANCE = 0.05           # apparent power may sit this far below real power (meter rounding) before the pair is rejected
GEN_LOADING_RANGE = (50.0, 100.0)   # generator loading limit, % (default 80)
GEN_LOADING_DEFAULT = 80.0
GEN_PF_RANGE = (0.5, 1.0)           # the generator's assumed power factor (default 0.8)
GEN_PF_DEFAULT = 0.8
# Example generator sizes, kVA (not from any source; prime/standby ratings differ: the supplier's rating applies).
GEN_SIZES_KVA = (20, 30, 40, 50, 60, 80, 100, 125, 150, 200, 250, 300, 400, 500, 630, 800, 1000)


# ---------------------------------------------------------------- 1. reactive power
def reactive_var(watts: float | None, apparent_va: float | None) -> float | None:
    """Reactive power size in var: Q = sqrt(S^2 - P^2) from apparent (S) and real (P) power. A magnitude only:
    it cannot say lagging or leading, so no sign is given. None if either input is missing. If S is more than
    5 % below |P| the two readings disagree and there is no figure; a smaller shortfall is rounding, giving 0."""
    if watts is None or apparent_va is None:
        return None
    s, p = abs(apparent_va), abs(watts)
    if s < p * (1 - REACTIVE_TOLERANCE):
        return None
    return math.sqrt(max(0.0, s * s - p * p))


# ---------------------------------------------------------------- 2. phase unbalance
def unbalance(a: float | None, b: float | None, c: float | None) -> dict | None:
    """Current unbalance of three line currents, by the NEMA MG 1 voltage-unbalance method applied to current:
    (largest deviation of any line current from the mean of the three) / mean x 100.
    Returns {"pct", "mean", "shares"} where shares are each line's % of the three currents added up
    (L1, L2, L3), or None when any line is missing or the mean is below UNBALANCE_FLOOR_A."""
    if a is None or b is None or c is None:
        return None
    mean = (a + b + c) / 3
    if mean < UNBALANCE_FLOOR_A:
        return None
    pct = 100 * max(abs(a - mean), abs(b - mean), abs(c - mean)) / mean
    total = a + b + c
    return {"pct": pct, "mean": mean, "shares": [100 * x / total for x in (a, b, c)]}


# ---------------------------------------------------------------- 3. demand
class Demand:
    """Rolling-window average demand, the day's peak of it, and the load factor, for one series (W or VA).

    Feed values in time order. Each value is held until the next one arrives (a meter's value does not
    change between its readings), but only across a gap of up to ``gap_s``: a longer silence is a gap. A gap
    is left out of every average (it is not read as zero) and marks any window that touches it incomplete.
    * average: time-weighted mean over the last ``window_s``; shown only once a full window has passed.
    * peak: the highest such average this show day (peak_incomplete if its window touched a gap).
    * load factor: the show day's time-weighted average / the peak, 0 to 1 (None until a peak exists)."""

    def __init__(self, window_s: float, gap_s: float) -> None:
        self.window_s, self.gap_s = float(window_s), float(gap_s)
        self.first_ts: float | None = None
        self.last_ts: float | None = None
        self.last_v = 0.0
        self.segs: deque = deque()      # (t0, t1, value) held segments inside the window
        self.gaps: deque = deque()      # (t0, t1) silences inside the window
        self._win_ws = 0.0              # running sums over self.segs (value x seconds, seconds): feed is O(1)
        self._win_s = 0.0
        self._fed = 0
        self.day_ws = 0.0               # value x seconds over the whole day, gaps left out
        self.day_s = 0.0
        self.had_gap = False            # any gap this show day
        self.avg: float | None = None
        self.avg_incomplete = False
        self.peak: float | None = None
        self.peak_at: float | None = None
        self.peak_incomplete = False

    def feed(self, value: float, ts: float) -> None:
        if self.last_ts is not None:
            if ts <= self.last_ts:
                return
            if ts - self.last_ts > self.gap_s:
                self.gaps.append((self.last_ts, ts))
                self.had_gap = True
            else:
                self.segs.append((self.last_ts, ts, self.last_v))
                self._win_ws += self.last_v * (ts - self.last_ts)
                self._win_s += ts - self.last_ts
                self.day_ws += self.last_v * (ts - self.last_ts)
                self.day_s += ts - self.last_ts
        else:
            self.first_ts = ts
        self.last_ts, self.last_v = ts, value
        lo = ts - self.window_s
        while self.segs and self.segs[0][1] <= lo:
            t0, t1, v = self.segs.popleft()
            self._win_ws -= v * (t1 - t0)
            self._win_s -= t1 - t0
        while self.gaps and self.gaps[0][1] <= lo:
            self.gaps.popleft()
        self._fed += 1
        if not self.segs or self._fed % 512 == 0:
            # Re-add from the segments now and then (and reset when empty) so rounding cannot build up.
            self._win_ws = sum(v * (t1 - t0) for t0, t1, v in self.segs)
            self._win_s = sum(t1 - t0 for t0, t1, v in self.segs)
        # Only the oldest segment can reach back past the window start: cut that part off.
        ws, s = self._win_ws, self._win_s
        if self.segs and self.segs[0][0] < lo:
            t0, t1, v = self.segs[0]
            ws -= v * (lo - t0)
            s -= lo - t0
        self.avg = ws / s if s > 0 and ts - self.first_ts >= self.window_s else None
        self.avg_incomplete = bool(self.gaps)
        if self.avg is not None and (self.peak is None or self.avg > self.peak):
            self.peak, self.peak_at, self.peak_incomplete = self.avg, ts, self.avg_incomplete

    @property
    def day_avg(self) -> float | None:
        return self.day_ws / self.day_s if self.day_s > 0 else None

    @property
    def load_factor(self) -> float | None:
        if self.peak is None or self.peak <= 0 or self.day_avg is None:
            return None
        return min(1.0, self.day_avg / self.peak)

    def to_dict(self, day: str) -> dict:
        out: dict = {"day": day, "window_min": self.window_s / 60, "avg": self.avg, "incomplete": self.avg_incomplete,
                     "peak": self.peak, "peak_at": self.peak_at, "peak_incomplete": self.peak_incomplete,
                     "day_avg": self.day_avg, "load_factor": self.load_factor, "had_gap": self.had_gap}
        return out


def gap_seconds(poll_interval_s: float) -> float:
    """The silence, in seconds, after which readings count as a gap for a meter polled at this interval."""
    return max(GAP_MIN_S, GAP_FACTOR * poll_interval_s)


# ---------------------------------------------------------------- 4. generator suggestion
def generator_suggestion(peak_va: float | None, loading_pct: float = GEN_LOADING_DEFAULT,
                         pf: float = GEN_PF_DEFAULT, size_kva: float | None = None,
                         now_va: float | None = None, sizes: tuple = GEN_SIZES_KVA,
                         line_peak_va: float | None = None) -> dict | None:
    """Minimum generator size from the measured peak demand (kVA) and the loading limit:
    min kVA = peak kVA / (loading limit / 100); kW = min kVA x generator power factor; next size up = the
    smallest standard size at or above min kVA (None if it is above the list). If the admin typed a generator
    size: loaded_pct = peak / size (the figure to judge by), now_pct = the load now / size, and state is
    "ok", "limit" (at or above the loading limit) or "over" (100 % or more). None without a peak.
    A suggestion from measured demand only: it knows nothing of unmetered loads, starting currents, harmonics
    or future additions."""
    if peak_va is None or peak_va <= 0:
        return None
    peak_kva = peak_va / 1000
    basis = "total"
    # A three-phase generator is limited by its busiest line: each line can give only a third of the rating.
    # When every line is known, size from three times the busiest line's peak if that is more than the total.
    if line_peak_va is not None and line_peak_va > 0 and 3 * line_peak_va > peak_va:
        peak_kva, basis = 3 * line_peak_va / 1000, "line"
    min_kva = peak_kva / (loading_pct / 100)
    out: dict = {"basis": basis, "peak_kva": peak_kva, "loading_pct": loading_pct, "pf": pf, "min_kva": min_kva,
                 "min_kw": min_kva * pf, "next_kva": next((s for s in sizes if s >= min_kva - 1e-9), None)}
    if size_kva:
        pct = 100 * peak_kva / size_kva
        out.update(size_kva=size_kva, loaded_pct=pct,
                   now_pct=None if now_va is None else 100 * now_va / 1000 / size_kva,
                   state="over" if pct >= 100 else "limit" if pct >= loading_pct else "ok")
    return out
