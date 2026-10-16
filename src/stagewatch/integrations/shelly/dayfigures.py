"""One meter's figures for the current show day: highest power, highest current, highest and lowest
voltage (each with its time) and the energy used. Pure: no network, no hub.

Rules (the project's "no assumed data"):

* Highest and lowest come only from readings Stagewatch actually received that show day, so they can
  miss a surge between two readings. Nothing is filled in for time Stagewatch was not reading.
* Energy is the meter's own running total: the last total minus the first total Stagewatch saw that
  show day. If a total ever goes back (the meter was reset), the energy before the reset is kept and
  counting carries on from the new number; whatever the meter used around the reset is unknown and
  is not invented.
* A silence longer than ``GAP_S`` between two readings is a gap, kept as (from, to) and shown on the
  card. Peaks are never back-filled and energy is never estimated across it.
* A new show day (the site's day rollover) starts everything afresh.
"""

from __future__ import annotations

GAP_S = 30.0         # silence longer than this between two readings is a gap
MAX_GAPS = 5         # the most recent few are kept
MAX_FIELDS = ("power", "current", "voltage", "freq", "voltage_ll", "thd_v", "thd_i")


class DayFigures:
    """The figures for one reading (one entity). Feed it readings in time order."""

    def __init__(self, field: str, cumulative: bool, day: str) -> None:
        self.field, self.cumulative, self.day = field, cumulative, day
        self.count = 0
        self.last_ts: float | None = None
        self.gaps: list[list[float]] = []
        self.high: tuple[float, float] | None = None     # (value, ts)
        self.low: tuple[float, float] | None = None
        # energy
        self.first_ts: float | None = None
        self.base = self.last = 0.0
        self.carried = 0.0
        self.went_back_at: float | None = None
        # A peak register the meter keeps itself ("meter_max_*"): we only note when it goes back (reset).
        self.register = field.startswith("meter_max_")

    def feed(self, value: float, ts: float) -> None:
        if self.last_ts is not None:
            if ts < self.last_ts:
                return                       # an older reading than one already used: ignore it
            if ts - self.last_ts > GAP_S:
                self.gaps = (self.gaps + [[self.last_ts, ts]])[-MAX_GAPS:]
        self.last_ts = ts
        self.count += 1
        if self.register:
            if self.first_ts is None:
                self.first_ts = ts
            elif value < self.last - 1e-6:
                self.went_back_at = ts
            self.last = value
            return
        if self.cumulative:
            if self.first_ts is None:
                self.first_ts, self.base = ts, value
            elif value < self.last - 1e-6:
                self.carried += self.last - self.base
                self.base = value
                self.went_back_at = ts
            self.last = value
            return
        if self.field in MAX_FIELDS and (self.high is None or value > self.high[0]):
            self.high = (value, ts)
        if self.field in ("voltage", "freq", "voltage_ll") and (self.low is None or value < self.low[0]):
            self.low = (value, ts)

    def to_dict(self) -> dict | None:
        """The public figures, or None while no reading has arrived this show day."""
        if not self.count:
            return None
        out: dict = {"day": self.day}
        if self.register:
            if self.went_back_at is not None:
                out["went_back_at"] = self.went_back_at
        elif self.cumulative:
            out.update(wh=self.carried + (self.last - self.base), since=self.first_ts)
            if self.went_back_at is not None:
                out["went_back_at"] = self.went_back_at
        else:
            if self.high is not None:
                out.update(max=self.high[0], max_at=self.high[1])
            if self.low is not None:
                out.update(min=self.low[0], min_at=self.low[1])
        if self.gaps:
            out["gaps"] = [list(g) for g in self.gaps]
        return out
