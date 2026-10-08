"""Barometer: sea-level pressure, the 3-hour tendency, a rough Zambretti outlook, a rapid-fall flag.

Advisory only. It says what the pressure at this site is doing; it never claims to forecast the
weather. All pressures are Pa inside Stagewatch (the card shows hPa).

Pure maths first (no I/O, easy to test), then a small service that keeps one averaged sample per
minute for four hours, and the emulate weather profiles.

Sea level: ``acoustics.msl_from_station_pa`` (the hypsometric reduction with the site temperature
smoothed over the last hour; International Standard Atmosphere when there is no temperature).
Tendency: the change of the *station* pressure over 3 hours (a change at fixed height equals the
sea-level change to well under 1 %, and needs no temperature). Words: Met Office "coast and sea"
guide. Outlook: the Zambretti forecaster, see ``zambretti`` for exactly which published version.

Nothing is assumed or back-filled: a gap in the readings is shown as a gap, and the tendency waits
until 3 hours of unbroken readings exist again (emulate mode alone fakes history, for the demo).
"""

from __future__ import annotations

import logging
import math
from collections import deque
from typing import NamedTuple

from .. import acoustics
from . import sitetime
from .model import Kind

log = logging.getLogger(__name__)

HOUR_S = 3600.0
TENDENCY_S = 3 * HOUR_S
CURRENT_WINDOW_S = 600.0       # "now" is the mean of the last 10 minutes of samples
REFERENCE_HALF_S = 600.0       # the reference is the mean within 10 minutes of 3 h ago
GAP_S = 3 * 60.0               # no samples for longer than this is a gap (nothing is assumed across it)
MIN_WINDOW_SAMPLES = 5         # each 10-minute window needs at least this many minute samples
PRESSURE_MIN_PA, PRESSURE_MAX_PA = 30000.0, 110000.0   # a station reading outside this is not weather
REARM_S = 10 * 60.0            # the silent notice cannot raise again sooner than this after it cleared
KEEP_S = 4 * HOUR_S            # how long samples are kept (and how far back the recorder is read)
TEMP_MEAN_S = HOUR_S           # the sea-level reduction uses the site temperature mean of this long
TEMP_MAX_AGE_S = 30 * 60.0     # ... if its newest reading is not older than this

# Met Office "coast and sea" tendency words, in tenths of a hPa per 3 h (rounded value, so the
# printed gaps 1.5/1.6 and 3.5/3.6 close): steady 0, slowly 1..15, plain 16..35, quickly 36..60,
# very rapidly 61 and more.
STEADY_MAX = 0
SLOW_MAX = 15
PLAIN_MAX = 35
QUICK_MAX = 60
ZAMBRETTI_TREND_TENTHS = 16    # 1.6 hPa in 3 h: the original instrument's rising/falling rule
DEFAULT_RAPID_FALL_HPA = 3.6

OUTLOOK_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATES = ("ok", "collecting", "gap", "sparse", "stale", "no_sensor")
WORDS = ("steady", "rising_slowly", "rising", "rising_quickly", "rising_very_rapidly",
         "falling_slowly", "falling", "falling_quickly", "falling_very_rapidly")


# ------------------------------------------------------------------ pure maths
def plausible_pressure(pa) -> bool:
    """True for a finite station pressure between 30 and 110 kPa (anything else is a broken reading)."""
    return isinstance(pa, (int, float)) and not isinstance(pa, bool) and math.isfinite(pa) \
        and PRESSURE_MIN_PA <= pa <= PRESSURE_MAX_PA


def tenths(delta_hpa: float) -> int:
    """A change in hPa as whole tenths of a hPa, rounded half away from zero (so 1.55 is 16, -1.55
    is -16, whatever the float noise of 1.55 * 10)."""
    n = math.floor(abs(delta_hpa) * 10.0 + 0.5 + 1e-9)
    return -n if delta_hpa < 0 else n


def tendency_word(delta_tenths: int) -> str:
    """The Met Office words for a 3-hour change given in tenths of a hPa."""
    n = abs(delta_tenths)
    if n <= STEADY_MAX:
        return "steady"
    way = "rising" if delta_tenths > 0 else "falling"
    if n <= SLOW_MAX:
        return f"{way}_slowly"
    if n <= PLAIN_MAX:
        return way
    if n <= QUICK_MAX:
        return f"{way}_quickly"
    return f"{way}_very_rapidly"


def zambretti_trend(delta_tenths: int) -> int:
    """-1 falling, 0 steady, +1 rising: the original rule, 1.6 hPa or more in 3 h."""
    if abs(delta_tenths) < ZAMBRETTI_TREND_TENTHS:
        return 0
    return 1 if delta_tenths > 0 else -1


# Zambretti forecaster, northern hemisphere, pinned version. Number Z from the sea-level pressure P
# in hPa, by trend, with floor() (not rounding):
#     falling  Z = 127 - 0.12 P     Z 1..9
#     steady   Z = 144 - 0.13 P     Z 10..19
#     rising   Z = 185 - 0.16 P     Z 20..32
# Z is kept inside the trend own range and looked up in that trend row of letters (falling
# ABDHORUVX, steady ABEKNPSWXZ, rising ABCFGIJLMQTYZ; the 26 outcomes A settled fine ... Z stormy,
# much rain). The constants, the floor() and the Z 1-32 letter rows are from the README of
# github.com/sassoftware/iot-zambretti-weather-forcasting. Only the winter rule is from an openHAB
# community post: outside April-September a falling Z is lowered by 1 and a rising Z raised by 1.
# Written out here as facts of the instrument, no source code copied. Quirk of the published
# winter rule (documented in docs/using-stagewatch.md): near 965 and 980 hPa a falling trend can
# read better than steady, and a rising trend can read worse than steady between about 966 and
# 1034 hPa. The card says it is not a forecast.
_ZAMBRETTI = {
    -1: (127.0, 0.12, 0, "ABDHORUVX"),
    0: (144.0, 0.13, 9, "ABEKNPSWXZ"),
    1: (185.0, 0.16, 19, "ABCFGIJLMQTYZ"),
}


def is_summer(month: int, hemisphere: str = "north") -> bool:
    """April to September in the north; October to March in the south (a mirrored guess: the
    method was built for the UK)."""
    north = 4 <= month <= 9
    return north if hemisphere != "south" else not north


def zambretti(msl_hpa: float, trend: int, month: int, hemisphere: str = "north",
              wind_deg: float | None = None) -> str:
    """The outlook letter A..Z for a sea-level pressure, a trend (-1, 0, +1) and the month.
    ``wind_deg`` is accepted for later (the full forecaster uses wind) and ignored now."""
    a, b, offset, row = _ZAMBRETTI[trend]
    z = math.floor(a - b * msl_hpa + 1e-9)     # floor; the 1e-9 only stops 13.999999999 for a true 14
    if not is_summer(month, hemisphere):
        z += -1 if trend < 0 else (1 if trend > 0 else 0)
    index = min(max(z - offset, 1), len(row))
    return row[index - 1]


# ------------------------------------------------------------------ the service
class Sample(NamedTuple):
    ts: float
    pa: float                 # station pressure, Pa
    temp_c: float | None      # site temperature at the same time, if any


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)


class BarometerService:
    """One averaged sample per minute (station pressure and site temperature), four hours deep,
    seeded from the recorder on start. ``update`` runs every hub tick and returns the public
    ``baro`` block (Pa canonical, no sensor ids)."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self.samples: deque[Sample] = deque()
        self._cur: list | None = None       # [minute, n, sum_ts, sum_pa, sum_t, n_t]
        self._last_msl: float | None = None
        self._last_ts: float | None = None
        self._last_reduction = "isa"
        self._rapid_on = False
        self._last_marker_ts: float | None = None
        self._cleared_ts: float | None = None
        self.demo: EmulatedWeather | None = None

    # ---- samples
    def add_reading(self, ts: float, pa: float, temp_c: float | None) -> None:
        minute = int(ts // 60)
        if self._cur is not None and self._cur[0] != minute:
            self._flush_minute()
        if self._cur is None:
            self._cur = [minute, 0, 0.0, 0.0, 0.0, 0]
        c = self._cur
        c[1] += 1
        c[2] += ts
        c[3] += pa
        if temp_c is not None:
            c[4] += temp_c
            c[5] += 1

    def _minute_sample(self) -> Sample | None:
        c = self._cur
        if c is None or c[1] == 0:
            return None
        return Sample(c[2] / c[1], c[3] / c[1], c[4] / c[5] if c[5] else None)

    def _flush_minute(self) -> None:
        s = self._minute_sample()
        self._cur = None
        if s is not None:
            self.samples.append(s)

    def _all(self, now: float) -> list[Sample]:
        while self.samples and self.samples[0].ts < now - KEEP_S:
            self.samples.popleft()
        out = list(self.samples)
        cur = self._minute_sample()
        if cur is not None:
            out.append(cur)
        return out

    def seed(self, now: float) -> None:
        """Fill the buffer from what the recorder saved in the last four hours (all shows). Only
        recorded readings: a hub that was off leaves a gap, which stays a gap."""
        since = now - KEEP_S
        rec = self.hub.recorder
        pressure = rec.series("site.pressure", since, now)
        temps = {int(ts // 60): v for ts, v in rec.series("site.temperature", since, now)}
        self.samples.clear()
        self._cur = None
        for ts, pa in pressure:
            self.samples.append(Sample(ts, pa, temps.get(int(ts // 60))))

    def temp_mean_c(self, now: float) -> float | None:
        """The site temperature averaged over the last hour (so a cloud or a heater blip does not
        swing the dial), or None when there is no recent temperature."""
        temps = [(s.ts, s.temp_c) for s in self._all(now) if s.temp_c is not None and s.ts >= now - TEMP_MEAN_S]
        if not temps or max(t for t, _ in temps) < now - TEMP_MAX_AGE_S:
            return None
        return _mean(v for _, v in temps)

    def msl_pa(self, station_pa: float, now: float) -> tuple[float, str]:
        """(sea-level pressure, "temperature" or "isa") for a station pressure now."""
        alt = self.hub.config.site.altitude_m
        t = self.temp_mean_c(now)
        return acoustics.msl_from_station_pa(station_pa, alt, t), ("temperature" if t is not None else "isa")

    def approx(self, reduction: str) -> str:
        alt = self.hub.config.site.altitude_m
        if alt > 2000:
            return "rough"
        return "approx" if alt > 500 or reduction == "isa" else "ok"

    # ---- the public block
    def update(self, now: float, pressure_pa: float | None, temp_c: float | None) -> dict:
        """Take this tick's site pressure (None unless a sensor reading is fresh) and temperature,
        and return the ``baro`` block."""
        hub = self.hub
        # Equipment pressure sensors (never averaged) do not make a barometer.
        has_sensor = any(e.kind == Kind.PRESSURE and not e.derived and hub.role_of(e) == "environment"
                         for e in hub.entities.values())
        if pressure_pa is not None and not plausible_pressure(pressure_pa):
            pressure_pa = None            # not weather: ignored, never stored or reduced
        if temp_c is not None and not (isinstance(temp_c, (int, float)) and math.isfinite(temp_c) and -90.0 <= temp_c <= 70.0):
            temp_c = None
        if pressure_pa is not None:
            self.add_reading(now, pressure_pa, temp_c)
        block: dict = {"state": "no_sensor"}
        if has_sensor:
            block = self._block(now, pressure_pa)
        self._rapid_fall(block, now)
        return block

    def _block(self, now: float, pressure_pa: float | None) -> dict:
        hemisphere = self.hub.config.barometer.hemisphere
        fresh = pressure_pa is not None
        base = {"state": "stale", "msl_pa": None, "set_pa": None, "tendency_pa_3h": None,
                "tendency_word": None, "rapid_fall": False, "outlook": None, "ready_ts": None,
                "approx": "ok", "reduction": "isa"}
        if not fresh:
            base.update(msl_pa=self._last_msl, last_ts=self._last_ts, approx=self.approx(self._last_reduction),
                        reduction=self._last_reduction)
            return base
        msl, reduction = self.msl_pa(pressure_pa, now)
        if not math.isfinite(msl):
            base.update(msl_pa=self._last_msl, last_ts=self._last_ts, approx=self.approx(self._last_reduction),
                        reduction=self._last_reduction)
            return base
        self._last_msl, self._last_ts, self._last_reduction = msl, now, reduction
        base.update(msl_pa=msl, approx=self.approx(reduction), reduction=reduction, last_ts=now)

        samples = self._all(now)
        # The unbroken run of samples that ends now.
        run_start = len(samples) - 1
        while run_start > 0 and samples[run_start].ts - samples[run_start - 1].ts <= GAP_S:
            run_start -= 1
        first_ts = samples[run_start].ts
        if first_ts > now - TENDENCY_S:
            # Not three hours of unbroken readings yet: collecting (first start) or gap.
            base["state"] = "collecting"
            base["first_ts"] = first_ts
            base["ready_ts"] = first_ts + TENDENCY_S
            if run_start > 0:
                base["state"] = "gap"
                base["gap"] = [samples[run_start - 1].ts, first_ts]
            hour = self._change(samples, now, HOUR_S)
            if first_ts <= now - HOUR_S and hour is not None:
                base["last_hour_pa"] = tenths(hour[0] / 100.0) * 10.0
            return base
        change = self._change(samples, now, TENDENCY_S)
        if change is None:           # too few readings in a window: not enough data, never a guess
            base["state"] = "sparse"
            base["ready_ts"] = None
            return base
        delta_pa, ref_pa = change
        t = tenths(delta_pa / 100.0)
        threshold = self.hub.config.barometer.rapid_fall_hpa_3h
        local = sitetime.local(now, self.hub.config.site)
        base.update(
            state="ok", first_ts=first_ts,
            tendency_pa_3h=t * 10.0, tendency_word=tendency_word(t),
            rapid_fall=-t >= round(threshold * 10),
            set_pa=acoustics.msl_from_station_pa(ref_pa, self.hub.config.site.altitude_m, self.temp_mean_c(now)),
            outlook=zambretti(msl / 100.0, zambretti_trend(t), local.month, hemisphere))
        return base

    @staticmethod
    def _change(samples: list[Sample], now: float, span_s: float) -> tuple[float, float] | None:
        """(change in Pa over exactly ``span_s``, the reference station pressure): the mean of the
        last 10 minutes minus the mean within 10 minutes of ``span_s`` ago, scaled by the true
        time between the two means so the 10-minute windows add no bias."""
        cur = [s for s in samples if s.ts >= now - CURRENT_WINDOW_S]
        ref = [s for s in samples if abs(s.ts - (now - span_s)) <= REFERENCE_HALF_S]
        if len(cur) < MIN_WINDOW_SAMPLES or len(ref) < MIN_WINDOW_SAMPLES:
            return None
        sep = _mean(s.ts for s in cur) - _mean(s.ts for s in ref)
        if sep <= 0:
            return None
        ref_pa = _mean(s.pa for s in ref)
        return (_mean(s.pa for s in cur) - ref_pa) * span_s / sep, ref_pa

    # ---- the optional silent rapid-fall notice
    def _rapid_fall(self, block: dict, now: float) -> None:
        """Silent L1 alarm ``barometer:rapid_fall`` and one marker per episode, when switched on.
        Raised at the threshold, cleared at 70 % of it; markers at least 30 minutes apart. Never
        audible: it is a hint, not something to acknowledge."""
        hub = self.hub
        cfg = hub.config.barometer
        delta = block.get("tendency_pa_3h")
        fall = -delta / 100.0 if delta is not None else 0.0
        state = block.get("state")
        if not cfg.rapid_fall_alarm:
            want = False
        elif state == "stale":
            want = self._rapid_on            # no reading: leave it as it is until readings return
        elif state != "ok":
            want = False
        elif self._rapid_on:
            want = fall >= 0.7 * cfg.rapid_fall_hpa_3h
        else:
            want = bool(block.get("rapid_fall"))
        if want == self._rapid_on:
            return
        if want and self._cleared_ts is not None and now - self._cleared_ts < REARM_S:
            return                           # just cleared: wait before it can raise again
        if not want:
            self._cleared_ts = now
        self._rapid_on = want
        text = f"Pressure falling quickly: {signed_hpa(-fall)} hPa in 3 h (advisory)"
        change = hub.alarms.set_condition("barometer:rapid_fall", want, 1, text, now, silent=True)
        if change:
            hub._alarm_changed([change])
        if want and (self._last_marker_ts is None or now - self._last_marker_ts >= 1800.0):
            self._last_marker_ts = now
            hub.add_marker(text, "barometer")

    # ---- emulate
    def start_demo(self, scenario: str, now: float) -> None:
        """Emulate only: begin a weather scenario. The buffer is back-filled with the three hours
        before now (a demo, so the card is complete at once); a restart does it again."""
        self.demo = EmulatedWeather(scenario, now)
        self.samples.clear()
        self._cur = None
        alt = self.hub.config.site.altitude_m
        ts = now - TENDENCY_S - 900.0
        while ts < now - 5.0:
            self.samples.append(Sample(ts, self.demo.station_pa(ts, alt), EMULATED_TEMP_C))
            ts += 60.0
        self._last_msl = None
        self._rapid_on = False
        self.hub._emas.pop(Kind.PRESSURE.value, None)   # the site average starts afresh on the new weather
        for e in [e for e in self.hub.entities.values() if e.kind == Kind.PRESSURE and not e.derived]:
            if self.demo.scenario == "none":
                self.hub.remove_entity(e.id)             # the sensors are taken away at once
            else:
                self.hub.update_state(e.id, round(self.demo.station_pa(now, alt), 0), now)   # and start on the new weather

    def emulated_pa(self, now: float) -> float | None:
        """The station pressure an emulated node reports now, or None (no pressure sensor, or a
        dropout)."""
        if self.demo is None:
            return None
        return self.demo.station_pa(now, self.hub.config.site.altitude_m) if self.demo.reporting(now) else None


def signed_hpa(hpa: float) -> str:
    """-3.8 as "−3.8" (a real minus sign), 0 as "0.0"."""
    s = f"{abs(hpa):.1f}"
    return s if float(s) == 0 else (f"−{s}" if hpa < 0 else f"+{s}")


# ------------------------------------------------------------------ emulate weather
EMULATED_TEMP_C = 22.0

# name -> (label, [(hours from the start, MSL hPa), ...], hPa per hour after the last point)
SCENARIOS: dict[str, tuple[str, list[tuple[float, float]], float]] = {
    "steady": ("Settled high", [(-4.0, 1028.0), (0.0, 1028.0)], 0.0),
    "slow_fall": ("Slow fall", [(-4.0, 1015.0), (-3.0, 1014.0), (0.0, 1013.0)], -0.33),
    "front": ("Front arriving", [(-4.0, 1017.0), (-3.0, 1017.0), (-1.5, 1015.8), (0.0, 1013.0)], -1.3),
    "storm": ("Storm", [(-4.0, 1006.0), (-3.0, 1006.0), (0.0, 999.0)], -2.3),
    "rising": ("Clearing", [(-4.0, 1005.0), (-3.0, 1005.0), (0.0, 1007.4)], 0.8),
    "dropout": ("Sensor dropout 20 min", [(-4.0, 1028.0), (0.0, 1028.0)], 0.0),
    "none": ("No pressure sensor", [(-4.0, 1013.0), (0.0, 1013.0)], 0.0),
}
DROPOUT_S = 20 * 60.0


class EmulatedWeather:
    """A made-up pressure history and future for emulate mode. ``now0`` is when the scenario
    started; the profile is the sea-level pressure, and ``station_pa`` turns it into what a
    sensor at the site altitude would read."""

    def __init__(self, scenario: str, now0: float) -> None:
        self.scenario = scenario if scenario in SCENARIOS else "front"
        self.now0 = now0

    def msl_hpa(self, ts: float) -> float:
        _, points, tail = SCENARIOS[self.scenario]
        h = (ts - self.now0) / HOUR_S
        if h <= points[0][0]:
            return points[0][1]
        for (h0, p0), (h1, p1) in zip(points, points[1:]):
            if h <= h1:
                return p0 + (p1 - p0) * (h - h0) / (h1 - h0)
        return points[-1][1] + tail * (h - points[-1][0])

    def station_pa(self, ts: float, altitude_m: float) -> float:
        msl_pa = self.msl_hpa(ts) * 100.0
        return msl_pa / acoustics.msl_from_station_pa(1.0, altitude_m, EMULATED_TEMP_C)

    def reporting(self, ts: float) -> bool:
        """False while the emulated sensors say nothing."""
        if self.scenario == "none":
            return False
        if self.scenario == "dropout":
            return not (self.now0 + 60.0 <= ts < self.now0 + 60.0 + DROPOUT_S)
        return True
