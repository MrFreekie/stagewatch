"""Barometer card: sea-level maths, the 3-hour tendency, the Zambretti outlook, the rapid-fall
notice, the service (gaps, restarts, no back-fill), the admin endpoints and the altitude helper.

Every expected number below is worked out by hand in the docstring beside it (not by calling the
code under test). The words are the Met Office "coast and sea" guide; the outlook is the pinned
Zambretti version described in core/barometer.py. The page maths (wording, dial angles) is tested
in node: tests/js/barometer_test.js, run from here when node is installed.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch import acoustics
from stagewatch.core import barometer as bm
from stagewatch.core import cards
from stagewatch.core import config as config_mod
from stagewatch.core.config import BarometerConfig, Config, ConfigStore, SiteConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.version import CONFIG_SCHEMA_VERSION
from stagewatch.web.auth import hash_pin, verify_pin
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
PIN = "4711"
T0 = 1688169600.0   # Sat 1 Jul 2023 00:00:00 UTC, a whole minute; July is "summer"
MIN = 60.0


# --------------------------------------------------------------- sea-level maths
def test_isa_reference_pressures():
    """Standard atmosphere, worked by hand: p = 101325 (1 - 0.0065 h / 288.15)^5.25588.
    1000 m: 1 - 0.022558 = 0.977442; ln = -0.022817; x 5.25588 = -0.119922; exp = 0.88699; 89,875 Pa,
    published as 898.75 hPa. The other heights are the tabulated ISA values."""
    for h, hpa in ((0, 1013.25), (500, 954.6), (1000, 898.7), (2000, 795.0)):
        assert acoustics.pressure_at_altitude_pa(h) / 100 == pytest.approx(hpa, abs=0.1)


def test_hypsometric_reduction_hand_check():
    """Station 1000.0 hPa at 110 m, 15 degC.
    Mean column temperature Tm = 288.15 + 0.0065 x 110 / 2 = 288.5075 K.
    Rd = 8.3144598 / 0.0289644 = 287.0531 J/(kg K); g h = 9.80665 x 110 = 1078.7315.
    g h / (Rd Tm) = 1078.7315 / 82816.97 = 0.0130255; exp = 1 + 0.0130255 + 0.0000848 + 0.0000004 = 1.0131107.
    Sea-level pressure = 100000 Pa x 1.0131107 = 101311.1 Pa = 1013.11 hPa."""
    assert acoustics.msl_from_station_pa(100000.0, 110.0, 15.0) == pytest.approx(101311.1, abs=1.0)


def test_reduction_at_zero_altitude_changes_nothing():
    for t in (None, -5.0, 15.0, 30.0):
        assert acoustics.msl_from_station_pa(98765.0, 0.0, t) == pytest.approx(98765.0, abs=1e-6)


def test_warmer_air_gives_a_higher_sea_level_figure_by_the_expected_amount():
    """d(msl)/dT = -msl g h / (Rd Tm^2). At 500 m, 954.6 hPa station, 15 degC: Tm = 289.775 K,
    so 954.6 x 9.80665 x 500 / (287.0531 x 289.775^2) = 4,680,000 / 24,102,000... worked: 9.80665 x 500 =
    4903.3; x 954.6 hPa = 4,680,700; Rd Tm^2 = 287.0531 x 83,969.5 = 24,103,000; ratio 0.1942 hPa per K
    (ignoring the exp factor 1.06). So +10 K lowers the figure by about 2 hPa: warmer air means a LOWER
    sea-level figure for the same station reading."""
    cold = acoustics.msl_from_station_pa(95460.0, 500.0, 15.0)
    warm = acoustics.msl_from_station_pa(95460.0, 500.0, 25.0)
    assert (cold - warm) / 100 == pytest.approx(2.0, abs=0.15)


@pytest.mark.parametrize("temp", [None, 15.0, -3.0])
@pytest.mark.parametrize("alt", [-100.0, 0.0, 110.0, 500.0, 2000.0])
def test_altitude_is_the_exact_inverse_of_the_reduction(alt, temp):
    station = 97000.0
    msl = acoustics.msl_from_station_pa(station, alt, temp)
    assert acoustics.altitude_from_msl_pa(station, msl, temp) == pytest.approx(alt, abs=1e-6)


def test_isa_helper_example_from_the_spec():
    """No temperature (ISA): station 1000.0 hPa, QNH 1013.2 hPa.
    (1000 / 1013.2) = 0.986972; ln = -0.013114; x 0.190263 (= 1 / 5.25588) = -0.0024952;
    exp = 0.9975079; 1 - that = 0.0024921; x 288.15 / 0.0065 (= 44,330.8 m) = 110.5 m."""
    assert acoustics.altitude_from_msl_pa(100000.0, 101320.0, None) == pytest.approx(110.5, abs=0.1)


def test_hypsometric_helper_example():
    """Station 1000.0 hPa, QNH 1013.2 hPa, 15 degC: x = ln(1.0132) x 287.0531 / 9.80665
    = 0.0131138 x 29.2705 = 0.38385 m/K; h = x T / (1 - x L / 2) = 0.38385 x 288.15 / (1 - 0.001248)
    = 110.60 / 0.998752 = 110.74 m."""
    assert acoustics.altitude_from_msl_pa(100000.0, 101320.0, 15.0) == pytest.approx(110.74, abs=0.05)


# --------------------------------------------------------------- tendency words
@pytest.mark.parametrize("hpa, tenths_, word", [
    (0.0, 0, "steady"), (0.04, 0, "steady"), (-0.04, 0, "steady"),
    (0.05, 1, "rising_slowly"), (0.1, 1, "rising_slowly"), (1.5, 15, "rising_slowly"),
    (1.55, 16, "rising"), (1.6, 16, "rising"), (3.5, 35, "rising"),
    (3.55, 36, "rising_quickly"), (3.6, 36, "rising_quickly"), (6.0, 60, "rising_quickly"),
    (6.05, 61, "rising_very_rapidly"), (6.1, 61, "rising_very_rapidly"), (12.0, 120, "rising_very_rapidly"),
    (-0.1, -1, "falling_slowly"), (-1.5, -15, "falling_slowly"), (-1.6, -16, "falling"),
    (-3.5, -35, "falling"), (-3.6, -36, "falling_quickly"), (-6.0, -60, "falling_quickly"),
    (-6.1, -61, "falling_very_rapidly"),
])
def test_met_office_words_at_every_boundary(hpa, tenths_, word):
    """Met Office guide: slowly 0.1-1.5, plain 1.6-3.5, quickly 3.6-6.0, very rapidly over 6.0 hPa in 3 h.
    The change is rounded to 0.1 first (half away from zero), which closes the printed gaps:
    1.55 rounds to 1.6 (plain), 3.55 to 3.6 (quickly), 6.05 to 6.1 (very rapidly), 0.04 to 0.0 (steady)."""
    assert bm.tenths(hpa) == tenths_
    assert bm.tendency_word(tenths_) == word


def test_zambretti_trend_threshold_is_1_6_hpa():
    assert [bm.zambretti_trend(n) for n in (0, 15, -15, 16, -16, 100, -100)] == [0, 0, 0, 1, -1, 1, -1]


# --------------------------------------------------------------- Zambretti
def test_there_are_26_letters_and_every_row_is_inside_them():
    assert bm.OUTLOOK_LETTERS == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    rows = [row for (_a, _b, _o, row) in bm._ZAMBRETTI.values()]
    assert [len(r) for r in rows] == [9, 10, 13]
    assert set("".join(rows)) == set(bm.OUTLOOK_LETTERS)


@pytest.mark.parametrize("p, trend, month, hemi, letter", [
    # steady, July, 1013.2: Z = 144 - 0.13 x 1013.2 = 144 - 131.716 = 12.284 -> 12; index 12 - 9 = 3 -> "ABEKNPSWXZ"[2]
    (1013.2, 0, 7, "north", "E"),
    # steady, 1010: Z = 144 - 131.3 = 12.7 -> 13; index 4 -> K
    (1010.0, 0, 7, "north", "K"),
    # falling, July, 1013.2: Z = 127 - 0.12 x 1013.2 = 127 - 121.584 = 5.416 -> 5; index 5 -> "ABDHORUVX"[4]
    (1013.2, -1, 7, "north", "O"),
    # falling, January (winter) lowers Z by 1: 4 -> "ABDHORUVX"[3]
    (1013.2, -1, 1, "north", "H"),
    # rising, July: Z = 185 - 0.16 x 1013.2 = 185 - 162.112 = 22.888 -> 23; index 23 - 19 = 4 -> "ABCFGIJLMQTYZ"[3]
    (1013.2, 1, 7, "north", "F"),
    # rising, January (winter) raises Z by 1: 24 -> index 5 -> G
    (1013.2, 1, 1, "north", "G"),
    # the south mirrors the seasons: June is winter there
    (1013.2, -1, 6, "south", "H"),
    (1013.2, -1, 1, "south", "O"),
    # ends of each row (clamped): rising at 1100 -> Z = 9 -> first letter; falling at 900 -> Z = 19 -> last letter;
    # steady at 900 -> Z = 27 -> last letter
    (1100.0, 1, 7, "north", "A"), (900.0, -1, 7, "north", "X"), (900.0, 0, 7, "north", "Z"),
    (1100.0, 0, 7, "north", "A"), (1100.0, -1, 7, "north", "A"),
])
def test_zambretti_hand_worked_letters(p, trend, month, hemi, letter):
    assert bm.zambretti(p, trend, month, hemi) == letter


def test_zambretti_is_monotonic_in_pressure():
    """Higher pressure is never a worse letter, for each trend, at every half hPa from 930 to 1070."""
    for trend in (-1, 0, 1):
        for month in (1, 7):
            letters = [bm.zambretti(p / 2, trend, month) for p in range(1860, 2141)]
            assert letters == sorted(letters, reverse=True), (trend, month)


def test_falling_is_never_better_than_steady_at_the_same_pressure():
    """Over the normal range, 990 to 1050 hPa. (In the pinned tables, in winter below about 983 hPa a
    falling Z lowered by 1 can land one letter before the steady one; that is how the published
    instrument behaves at gale-low pressures, and the card never shows a letter on its own.)"""
    for month in (1, 7):
        for p in range(990, 1051):
            assert bm.zambretti(float(p), -1, month) >= bm.zambretti(float(p), 0, month), (p, month)


def test_summer_months():
    assert [m for m in range(1, 13) if bm.is_summer(m, "north")] == [4, 5, 6, 7, 8, 9]
    assert [m for m in range(1, 13) if bm.is_summer(m, "south")] == [1, 2, 3, 10, 11, 12]


# --------------------------------------------------------------- helpers for the service
def make_hub(tmp_path, altitude=0.0, sensors=("n1",)):
    hub = Hub(tmp_path)
    hub.config.site.timezone = "UTC"
    hub.config.site.altitude_m = altitude
    for n in sensors:
        hub.register_device(Device(n, n, "esphome", "x", "BME280", "Area", Status.OK))
        hub.register_entity(Entity(f"{n}.pressure", n, "Pressure", Kind.PRESSURE, "Pa", 0))
        hub.register_entity(Entity(f"{n}.temperature", n, "Temperature", Kind.TEMPERATURE, "C", 1))
    return hub


def feed(hub, start_min, end_min, pressure, temp=15.0):
    """One reading a minute from minute start_min up to and including end_min after T0;
    ``pressure`` is a number or a function of the minute. Returns the last block."""
    block = None
    for m in range(start_min, end_min + 1):
        p = pressure(m) if callable(pressure) else pressure
        block = hub.baro.update(T0 + m * MIN, p, temp)
    return block


def linear(delta_pa_per_3h, base=101000.0):
    return lambda m: base + delta_pa_per_3h * m / 180.0


# --------------------------------------------------------------- the service
def test_steady_pressure(tmp_path):
    """101,000 Pa all the time, alt 0: sea level = 101,000 Pa (the exponent is 0). Change 0.0 -> steady.
    July, steady, 1010.0 hPa: Z = 144 - 0.13 x 1010 = 12.7 -> 13; index 4 -> K."""
    hub = make_hub(tmp_path)
    b = feed(hub, 0, 200, 101000.0)
    assert b["state"] == "ok" and b["tendency_word"] == "steady" and b["tendency_pa_3h"] == 0.0
    assert b["msl_pa"] == pytest.approx(101000.0) and b["set_pa"] == pytest.approx(101000.0)
    assert b["outlook"] == "K" and b["rapid_fall"] is False and b["reduction"] == "temperature" and b["approx"] == "ok"


@pytest.mark.parametrize("delta_pa, word, rapid", [
    (0.0, "steady", False), (-4.0, "steady", False),           # -0.04 hPa rounds to 0.0
    (-10.0, "falling_slowly", False), (-150.0, "falling_slowly", False),
    (-162.0, "falling", False), (-350.0, "falling", False),
    (-360.0, "falling_quickly", True), (-600.0, "falling_quickly", True),
    (-610.0, "falling_very_rapidly", True),
    (10.0, "rising_slowly", False), (240.0, "rising", False), (360.0, "rising_quickly", False),
    (610.0, "rising_very_rapidly", False),
])
def test_tendency_from_a_straight_line(tmp_path, delta_pa, word, rapid):
    """A straight-line change of D Pa per 3 h gives exactly D over 3 h (the two 10-minute means are
    scaled by the true time between them), so -150 Pa is -1.5 hPa, -360 Pa is -3.6 hPa, and so on."""
    hub = make_hub(tmp_path)
    b = feed(hub, 0, 200, linear(delta_pa))
    assert b["state"] == "ok"
    assert b["tendency_word"] == word and b["rapid_fall"] is rapid
    assert b["tendency_pa_3h"] == pytest.approx(bm.tenths(delta_pa / 100) * 10.0)


def test_the_set_hand_is_the_pressure_three_hours_ago(tmp_path):
    """Falling 360 Pa in 3 h from 101,000 Pa: 3 h before the last reading (minute 200) is minute 20,
    where the line reads 101,000 + (-360 x 20 / 180) = 100,960 Pa."""
    hub = make_hub(tmp_path)
    b = feed(hub, 0, 200, linear(-360.0))
    assert b["set_pa"] == pytest.approx(100960.0, abs=0.01)     # the 10-minute mean around minute 20 is minute 20


def test_collecting_until_three_hours_have_passed(tmp_path):
    """First reading at minute 0. At minute 179 the unbroken readings span 2 h 59 min: collecting, ready
    at minute 180 (first reading + 3 h). At minute 181 they span 3 h 01 min: ok."""
    hub = make_hub(tmp_path)
    b = feed(hub, 0, 179, linear(-100.0))
    assert b["state"] == "collecting" and b["ready_ts"] == pytest.approx(T0 + 180 * MIN)
    assert b["tendency_word"] is None and b["outlook"] is None and b["set_pa"] is None and b["rapid_fall"] is False
    b = feed(hub, 180, 181, linear(-100.0))
    assert b["state"] == "ok"


def test_provisional_last_hour_after_an_hour(tmp_path):
    """After 90 minutes of a fall of 60 Pa per hour the last hour is -60 Pa = -0.6 hPa, shown without a word."""
    hub = make_hub(tmp_path)
    assert "last_hour_pa" not in feed(hub, 0, 50, lambda m: 101000.0 - m)      # not an hour yet
    b = feed(hub, 51, 90, lambda m: 101000.0 - m)
    assert b["state"] == "collecting" and b["last_hour_pa"] == pytest.approx(-60.0)
    assert b["tendency_word"] is None


def test_a_gap_over_15_minutes_restarts_the_wait(tmp_path):
    """Readings for minutes 0-100, nothing for minutes 101-120 (20 min), then readings from 121.
    The unbroken run now starts at minute 121: gap, ready at 121 + 180 = minute 301, the gap shown as
    minute 100 to minute 121. No guess is made across the gap."""
    hub = make_hub(tmp_path)
    feed(hub, 0, 100, 101000.0)
    b = feed(hub, 121, 130, 101000.0)
    assert b["state"] == "gap" and b["gap"] == [T0 + 100 * MIN, T0 + 121 * MIN]
    assert b["ready_ts"] == pytest.approx(T0 + 301 * MIN)
    assert b["tendency_word"] is None and b["outlook"] is None
    b = feed(hub, 131, 300, 101000.0)
    assert b["state"] == "gap"
    b = feed(hub, 301, 302, 101000.0)
    assert b["state"] == "ok" and b["tendency_word"] == "steady"


def test_a_gap_of_14_minutes_is_not_a_gap(tmp_path):
    hub = make_hub(tmp_path)
    feed(hub, 0, 100, 101000.0)
    b = feed(hub, 114, 200, 101000.0)         # minutes 101-113 missing: 14 min between readings
    assert b["state"] == "ok"


def test_a_reference_eleven_minutes_away_is_not_good_enough(tmp_path):
    """The only readings near 3 h ago are 11 minutes away from it, with more than 15 minutes
    between them and the next: not enough, so no tendency."""
    hub = make_hub(tmp_path)
    now_min = 200
    hub.baro.add_reading(T0 + (now_min - 180 - 11) * MIN, 101000.0, 15.0)
    b = feed(hub, now_min - 170, now_min, 101000.0)
    assert b["state"] != "ok" and b["tendency_word"] is None


def test_stale_pressure_is_dim_with_no_outlook(tmp_path):
    hub = make_hub(tmp_path)
    ok = feed(hub, 0, 200, 101000.0)
    b = hub.baro.update(T0 + 205 * MIN, None, 15.0)
    assert b["state"] == "stale" and b["msl_pa"] == ok["msl_pa"] and b["last_ts"] == pytest.approx(T0 + 200 * MIN)
    assert b["outlook"] is None and b["tendency_word"] is None and b["rapid_fall"] is False


def test_no_pressure_sensor(tmp_path):
    hub = make_hub(tmp_path, sensors=())
    assert hub.baro.update(T0, None, 15.0) == {"state": "no_sensor"}
    hub.compute_site(T0)
    assert hub.site_meta["baro"] == {"state": "no_sensor"}
    assert hub.site_meta["pressure_source"] == "altitude"      # the altitude fallback is never shown as weather


def test_noise_does_not_flip_steady(tmp_path):
    """0.05 hPa (5 Pa) of random jitter on every reading: the 10-minute means keep it steady."""
    import random
    rng = random.Random(7)
    hub = make_hub(tmp_path)
    b = feed(hub, 0, 200, lambda m: 101000.0 + rng.gauss(0, 5.0))
    assert b["tendency_word"] == "steady"


def test_altitude_changes_the_sea_level_figure_and_flags_the_approximation(tmp_path):
    hub = make_hub(tmp_path, altitude=600.0)
    b = feed(hub, 0, 5, 94000.0, temp=15.0)
    assert b["msl_pa"] == pytest.approx(acoustics.msl_from_station_pa(94000.0, 600.0, 15.0))
    assert b["approx"] == "approx"
    hub.config.site.altitude_m = 2500.0
    assert hub.baro.update(T0 + 6 * MIN, 74000.0, 15.0)["approx"] == "rough"
    hub.config.site.altitude_m = 0.0
    assert hub.baro.update(T0 + 7 * MIN, 101000.0, None)["approx"] == "ok"


def test_no_temperature_falls_back_to_isa_and_says_so(tmp_path):
    hub = make_hub(tmp_path, altitude=100.0)
    b = feed(hub, 0, 3, 100000.0, temp=None)
    assert b["reduction"] == "isa" and b["approx"] == "approx"
    assert b["msl_pa"] == pytest.approx(acoustics.msl_from_station_pa(100000.0, 100.0, None))


def test_temperature_is_a_one_hour_mean(tmp_path):
    """Temperature 10 for the first 30 minutes then 20 for 30 minutes: the mean at minute 59 is 15, not 20."""
    hub = make_hub(tmp_path)
    for m in range(60):
        hub.baro.update(T0 + m * MIN, 100000.0, 10.0 if m < 30 else 20.0)
    assert hub.baro.temp_mean_c(T0 + 59 * MIN) == pytest.approx(15.0)


def test_the_public_block_carries_only_numbers_a_state_and_a_letter(tmp_path):
    hub = make_hub(tmp_path)
    b = feed(hub, 0, 200, linear(-400.0))
    allowed = {"state", "msl_pa", "set_pa", "tendency_pa_3h", "tendency_word", "rapid_fall", "outlook", "ready_ts",
               "first_ts", "last_ts", "gap", "last_hour_pa", "approx", "reduction"}
    assert set(b) <= allowed
    assert not re.search(r"n1|sim_|\.pressure|\d+\.\d+\.\d+\.\d+", json.dumps(b))
    assert b["outlook"] in bm.OUTLOOK_LETTERS and b["state"] in bm.STATES and b["tendency_word"] in bm.WORDS


# --------------------------------------------------------------- history: restarts and show rollover
def record(hub, first_ts, minutes, p=101000.0, t=15.0, skip=()):
    for m in range(minutes):
        if m in skip:
            continue
        ts = first_ts + m * MIN
        hub.recorder.record_state("site.pressure", p, ts)
        hub.recorder.record_state("site.temperature", t, ts)


def test_series_reads_across_shows(tmp_path):
    """Recorder.series joins every show that overlaps the window; history() alone would stop at the
    show boundary."""
    hub = make_hub(tmp_path)
    now = math.ceil(time.time() / 60) * 60      # not before the show started
    first = now - 200 * MIN
    record(hub, first, 100)
    hub.recorder.start_show("Day 2")
    record(hub, first + 100 * MIN, 100)
    rows = hub.recorder.series("site.pressure", now - 4 * 3600, now)
    assert len(rows) == 200 and [r[0] for r in rows] == sorted(r[0] for r in rows)
    assert rows[0][0] == pytest.approx(first) and rows[-1][0] == pytest.approx(first + 199 * MIN)
    assert len(hub.recorder.history(["site.pressure"], now - 4 * 3600, now)["site.pressure"]) <= 200


def test_a_restart_keeps_the_tendency_across_a_show_rollover(tmp_path):
    hub = make_hub(tmp_path)
    now = math.ceil(time.time() / 60) * 60      # not before the show started
    first = now - 200 * MIN
    record(hub, first, 100, p=101000.0)
    hub.recorder.start_show("Day 2")
    record(hub, first + 100 * MIN, 101, p=101000.0)         # up to and including "now"
    svc = bm.BarometerService(hub)                           # a fresh service on the same recorder = a restart
    svc.seed(now)
    assert len(svc.samples) == 201                            # every minute, across both shows
    hub.baro = svc
    b = svc.update(now + 30, 101000.0, 15.0)
    assert b["state"] == "ok" and b["tendency_word"] == "steady"


def test_a_restart_after_a_long_stop_shows_a_gap_and_does_not_back_fill(tmp_path):
    hub = make_hub(tmp_path)
    now = math.ceil(time.time() / 60) * 60      # not before the show started
    first = now - 150 * MIN
    record(hub, first, 61)                                    # 60 minutes of readings, then Stagewatch was off
    svc = bm.BarometerService(hub)
    svc.seed(now)
    hub.baro = svc
    b = svc.update(now, 101000.0, 15.0)                       # first reading after 90 minutes off
    assert b["state"] == "gap" and b["gap"][0] == pytest.approx(first + 60 * MIN)
    assert b["tendency_word"] is None and b["ready_ts"] == pytest.approx(now + 3 * 3600)
    assert len(svc.samples) == 61                             # nothing invented for the missing 90 minutes


# --------------------------------------------------------------- the quiet rapid-fall notice
def test_rapid_fall_notice_is_off_by_default(tmp_path):
    hub = make_hub(tmp_path)
    b = feed(hub, 0, 200, linear(-500.0))
    assert b["rapid_fall"] is True                              # the card flag is always on
    assert "barometer:rapid_fall" not in hub.alarms.active
    assert not [m for m in hub.recorder.markers() if m.source == "barometer"]


def test_rapid_fall_notice_is_silent_with_one_marker_and_hysteresis(tmp_path):
    hub = make_hub(tmp_path)
    hub.config.barometer.rapid_fall_alarm = True
    svc = hub.baro
    ok = {"state": "ok", "rapid_fall": True, "tendency_pa_3h": -380.0}
    svc._rapid_fall(ok, T0)
    alarm = hub.alarms.active["barometer:rapid_fall"]
    assert alarm.silent and alarm.level == 1 and not hub.alarms.sounding and hub.alarms.max_level == 0
    markers = [m for m in hub.recorder.markers() if m.source == "barometer"]
    assert [m.label for m in markers] == ["Pressure falling quickly: −3.8 hPa in 3 h (advisory)"]
    # between the threshold (3.6) and 70 % of it (2.52) the notice stays on
    svc._rapid_fall({"state": "ok", "rapid_fall": False, "tendency_pa_3h": -300.0}, T0 + 60)
    assert "barometer:rapid_fall" in hub.alarms.active
    # below 70 % it clears
    svc._rapid_fall({"state": "ok", "rapid_fall": False, "tendency_pa_3h": -250.0}, T0 + 120)
    assert "barometer:rapid_fall" not in hub.alarms.active
    # raised again within 30 minutes: the alarm returns, but no second marker
    svc._rapid_fall(ok, T0 + 600)
    assert "barometer:rapid_fall" in hub.alarms.active
    assert len([m for m in hub.recorder.markers() if m.source == "barometer"]) == 1
    svc._rapid_fall({"state": "ok", "rapid_fall": False, "tendency_pa_3h": -100.0}, T0 + 700)
    svc._rapid_fall(ok, T0 + 2000)                              # more than 30 minutes after the first marker
    assert len([m for m in hub.recorder.markers() if m.source == "barometer"]) == 2
    assert not hub.alarms.sounding


def test_rapid_fall_notice_clears_when_switched_off_or_data_is_lost(tmp_path):
    hub = make_hub(tmp_path)
    hub.config.barometer.rapid_fall_alarm = True
    svc = hub.baro
    svc._rapid_fall({"state": "ok", "rapid_fall": True, "tendency_pa_3h": -380.0}, T0)
    svc._rapid_fall({"state": "stale"}, T0 + 10)                # no reading: left as it is
    assert "barometer:rapid_fall" in hub.alarms.active
    svc._rapid_fall({"state": "gap"}, T0 + 20)                  # a gap: cleared, nothing is assumed
    assert "barometer:rapid_fall" not in hub.alarms.active
    svc._rapid_fall({"state": "ok", "rapid_fall": True, "tendency_pa_3h": -380.0}, T0 + 4000)
    hub.config.barometer.rapid_fall_alarm = False
    svc._rapid_fall({"state": "ok", "rapid_fall": True, "tendency_pa_3h": -380.0}, T0 + 4010)
    assert "barometer:rapid_fall" not in hub.alarms.active


# --------------------------------------------------------------- emulate weather
def test_emulated_scenarios_have_the_promised_tendency():
    for name, delta in (("steady", 0.0), ("slow_fall", -1.0), ("front", -4.0), ("storm", -7.0), ("rising", 2.4)):
        w = bm.EmulatedWeather(name, 1_000_000.0)
        assert w.msl_hpa(1_000_000.0) - w.msl_hpa(1_000_000.0 - 3 * 3600) == pytest.approx(delta, abs=0.01), name
    assert bm.EmulatedWeather("front", 0.0).msl_hpa(0.0) == pytest.approx(1013.0)
    assert bm.EmulatedWeather("steady", 0.0).msl_hpa(0.0) == pytest.approx(1028.0)


def test_emulated_station_pressure_reduces_back_to_the_profile_at_any_altitude():
    w = bm.EmulatedWeather("front", 0.0)
    for alt in (0.0, 120.0, 800.0):
        station = w.station_pa(0.0, alt)
        assert acoustics.msl_from_station_pa(station, alt, bm.EMULATED_TEMP_C) == pytest.approx(101300.0, abs=0.01)


def test_dropout_and_no_sensor_report_nothing():
    assert not bm.EmulatedWeather("none", 0.0).reporting(5.0)
    d = bm.EmulatedWeather("dropout", 0.0)
    assert d.reporting(30.0) and not d.reporting(61.0) and not d.reporting(60.0 + 1199.0) and d.reporting(60.0 + 1201.0)


def test_demo_back_fills_three_hours_so_the_card_is_complete_at_once(tmp_path):
    hub = make_hub(tmp_path)
    now = T0 + 10 * 3600
    hub.baro.start_demo("front", now)
    b = hub.baro.update(now, hub.baro.emulated_pa(now), 22.0)
    assert b["state"] == "ok" and b["tendency_word"] == "falling_quickly" and b["rapid_fall"] is True
    assert b["msl_pa"] == pytest.approx(101300.0, abs=1.0)


def test_demo_dropout_leaves_a_real_gap(tmp_path):
    hub = make_hub(tmp_path)
    now = T0 + 10 * 3600
    hub.baro.start_demo("dropout", now)
    last = None
    for s in range(0, 40 * 60, 30):
        t = now + s
        p = hub.baro.emulated_pa(t)
        last = hub.baro.update(t, p, 22.0)
    assert last["state"] == "gap" and last["tendency_word"] is None


# --------------------------------------------------------------- config
def test_config_defaults_and_bounds():
    b = BarometerConfig()
    assert (b.hemisphere, b.rapid_fall_alarm, b.rapid_fall_hpa_3h) == ("north", False, 3.6)
    for bad in ({"hemisphere": "east"}, {"rapid_fall_hpa_3h": 1.4}, {"rapid_fall_hpa_3h": 10.1}, {"rapid_fall_alarm": "maybe"}):
        with pytest.raises(Exception):
            BarometerConfig(**bad)
    assert BarometerConfig(rapid_fall_hpa_3h=1.5).rapid_fall_hpa_3h == 1.5 and BarometerConfig(hemisphere="south").hemisphere == "south"
    assert BarometerConfig(unknown_future_key=1).hemisphere == "north"       # a newer file's key is ignored


def test_no_schema_bump_and_no_migration_invents_the_section():
    assert Config().schema_version == CONFIG_SCHEMA_VERSION
    raw = {"schema_version": CONFIG_SCHEMA_VERSION, "site": {"name": "X"}}
    assert "barometer" not in config_mod.migrate(dict(raw))


def test_a_config_saved_before_the_barometer_loads_with_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.admin.pin_hash = hash_pin("1234")
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.pop("barometer", None)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = ConfigStore(path).load()
    assert loaded.barometer == BarometerConfig() and verify_pin("1234", loaded.admin.pin_hash)


def test_the_section_round_trips_and_an_older_build_forgets_it_but_keeps_everything_else(tmp_path):
    """Downgrade: an older build has no ``barometer`` field, so it ignores the section (the shared
    base ignores unknown keys) and its next save leaves it out; the PIN and the rest are untouched.
    Upgrading again gives the defaults."""
    path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.admin.pin_hash = hash_pin("1234")
    cfg.site.name = "Kept venue"
    cfg.barometer = BarometerConfig(hemisphere="south", rapid_fall_alarm=True, rapid_fall_hpa_3h=4.2)
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    assert ConfigStore(path).load().barometer == cfg.barometer
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["barometer"]["hemisphere"] == "south"
    older = {k: v for k, v in Config.model_fields.items() if k != "barometer"}
    assert "barometer" not in older and "site" in older
    # what the older model keeps of this file
    kept = {k: raw[k] for k in older if k in raw}
    again = Config.model_validate(kept)
    assert again.site.name == "Kept venue" and again.barometer == BarometerConfig()


def test_a_bad_barometer_section_resets_only_itself(tmp_path, caplog):
    """Salvage: a damaged section (here a hemisphere this build does not know) falls back to its defaults;
    the PIN, onboarding and every other section stay, and the original file is kept aside."""
    path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.admin.pin_hash = hash_pin("1234")
    cfg.site.name = "Kept venue"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["barometer"] = {"hemisphere": "sideways", "rapid_fall_hpa_3h": 99}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    old = ConfigStore(path)
    loaded = old.load()
    assert verify_pin("1234", loaded.admin.pin_hash) and not old.recovery_required
    assert loaded.site.name == "Kept venue" and loaded.barometer == BarometerConfig()
    assert (tmp_path / "config.invalid.yaml").exists()
    assert "sideways" not in caplog.text and "1234" not in caplog.text


# --------------------------------------------------------------- the card id
def test_barometer_card_is_known_but_never_a_default():
    assert "barometer" in cards.KNOWN_CARDS and cards.CARD_ID_RE.fullmatch("barometer")
    for layout in cards.LAYOUT_DEFAULTS:
        assert "barometer" not in cards.default_cards(layout)
    assert "barometer" not in cards.legacy_cards("wall")
    assert cards.strict_cards_error(["env_tiles", "barometer"]) is None


# --------------------------------------------------------------- endpoints
def sub(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def sensor_hub(tmp_path, pressures=(100000.0,), temp=15.0, altitude=0.0):
    hub = make_hub(tmp_path, altitude=altitude, sensors=tuple(f"n{i}" for i in range(len(pressures))))
    now = time.time()
    for i, p in enumerate(pressures):
        hub.update_state(f"n{i}.pressure", p, now)
        hub.update_state(f"n{i}.temperature", temp, now)
    hub.compute_site(now)
    return hub


def admin_client(hub, **kw):
    app = create_app(hub, manage_hub=False, lan_addresses=lambda: ["192.0.2.10"])
    c = TestClient(app)
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
    return c


HELPER = "/api/admin/site/altitude-from-pressure"


def test_helper_needs_admin_and_the_same_origin(tmp_path):
    hub = sensor_hub(tmp_path)
    c = TestClient(create_app(hub, manage_hub=False))
    assert c.post(HELPER, json={"qnh_hpa": 1013.2}).status_code == 401
    c = admin_client(hub)
    assert c.post(HELPER, json={"qnh_hpa": 1013.2}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.put("/api/admin/barometer", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/admin/barometer/demo", json={"scenario": "front"}, headers={"Origin": "http://evil.example"}).status_code == 403


@pytest.mark.parametrize("bad", [939.9, 1060.1, 10132, 101.3, 0, -5])
def test_helper_refuses_figures_outside_940_to_1060(tmp_path, bad):
    c = admin_client(sensor_hub(tmp_path))
    r = c.post(HELPER, json={"qnh_hpa": bad})
    assert r.status_code == 422      # the same fixed sentence every time: it never echoes the figure
    assert r.json()["detail"] == "Sea-level pressure must be between 940 and 1,060 hPa. Nothing has been changed."


def test_helper_refuses_junk_without_echoing_it(tmp_path):
    c = admin_client(sensor_hub(tmp_path))
    for body in ({"qnh_hpa": "hello"}, {"qnh_hpa": None}, {}, {"qnh_hpa": 1013, "extra": 1}, {"qnh_hpa": "NaN"}):
        r = c.post(HELPER, json=body)
        assert r.status_code == 422 and "hello" not in r.text


def test_helper_without_a_sensor_is_a_409(tmp_path):
    c = admin_client(sensor_hub(tmp_path, pressures=()))
    r = c.post(HELPER, json={"qnh_hpa": 1013.2})
    assert r.status_code == 409 and "No pressure sensor" in r.json()["detail"]


def test_helper_with_an_old_reading_is_a_409(tmp_path):
    hub = sensor_hub(tmp_path)
    hub.entities["site.pressure"].updated = time.time() - 1000
    r = admin_client(hub).post(HELPER, json={"qnh_hpa": 1013.2})
    assert r.status_code == 409 and "old" in r.json()["detail"]


def test_helper_previews_and_writes_nothing(tmp_path):
    """Station 1000.0 hPa, 15 degC, QNH 1013.2 hPa: 110.74 m by hand (see test_hypsometric_helper_example),
    so 111 m. Nothing is saved: the altitude and the file stay as they were."""
    hub = sensor_hub(tmp_path)
    c = admin_client(hub)
    saved = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    r = c.post(HELPER, json={"qnh_hpa": 1013.2})
    assert r.status_code == 200
    d = r.json()
    assert d["altitude_m"] == 111 and d["current_altitude_m"] == 0.0 and d["station_hpa"] == 1000.0
    assert d["sensors_used"] == 1 and d["spread_hpa"] == 0.0 and d["station_age_s"] <= 2
    assert any("differs from the saved altitude" in w for w in d["warnings"])
    assert hub.config.site.altitude_m == 0.0 and (tmp_path / "config.yaml").read_text(encoding="utf-8") == saved
    assert set(d) == {"altitude_m", "current_altitude_m", "station_hpa", "station_age_s", "sensors_used", "spread_hpa", "warnings"}


def test_helper_warns_when_the_sensors_disagree_or_the_height_is_odd(tmp_path):
    hub = sensor_hub(tmp_path, pressures=(100000.0, 100300.0))          # 3 hPa apart
    d = admin_client(hub).post(HELPER, json={"qnh_hpa": 1013.2}).json()
    assert d["sensors_used"] == 2 and d["spread_hpa"] == 3.0
    assert any("disagree" in w for w in d["warnings"])
    hub = sensor_hub(sub(tmp_path, "b"), pressures=(80000.0,))   # about 2,040 m
    d = admin_client(hub).post(HELPER, json={"qnh_hpa": 1013.2}).json()
    assert d["altitude_m"] > 2000 and any("unusual" in w for w in d["warnings"])


def test_helper_refuses_an_altitude_outside_the_allowed_range(tmp_path):
    hub = sensor_hub(tmp_path, pressures=(100000.0,))
    r = admin_client(hub).post(HELPER, json={"qnh_hpa": 1060.0})        # 1000 vs 1060 hPa is about 480 m: inside
    assert r.status_code == 200
    hub = sensor_hub(sub(tmp_path, "c"), pressures=(45000.0,))
    r = admin_client(hub).post(HELPER, json={"qnh_hpa": 940.0})         # 450 vs 940 hPa is about 6,700 m
    assert r.status_code == 422 and "6,000" in r.json()["detail"]


def test_saving_the_previewed_altitude_makes_the_dial_read_the_figure_typed(tmp_path):
    """Round trip: preview, save through the ordinary site endpoint, and the dial's sea-level figure is the
    QNH typed to within 0.07 hPa (the altitude is rounded to 1 m, and 1 m is 0.12 hPa, so at most 0.06)."""
    hub = sensor_hub(tmp_path, pressures=(100000.0, 100000.0))
    c = admin_client(hub)
    for qnh in (1013.2, 1007.9, 1021.4):
        h = c.post(HELPER, json={"qnh_hpa": qnh}).json()["altitude_m"]
        assert c.put("/api/admin/site", json={"altitude_m": h, "timezone": "UTC"}).status_code == 200
        hub.compute_site(time.time())
        assert hub.site_meta["baro"]["msl_pa"] / 100 == pytest.approx(qnh, abs=0.07)


def test_put_barometer_validates_and_saves(tmp_path):
    hub = sensor_hub(tmp_path)
    c = admin_client(hub)
    r = c.put("/api/admin/barometer", json={"hemisphere": "south", "rapid_fall_alarm": True, "rapid_fall_hpa_3h": 4.0})
    assert r.status_code == 200 and hub.config.barometer.hemisphere == "south" and hub.config.barometer.rapid_fall_alarm
    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert on_disk["barometer"] == {"hemisphere": "south", "rapid_fall_alarm": True, "rapid_fall_hpa_3h": 4.0}
    for bad in ({"hemisphere": "east"}, {"rapid_fall_hpa_3h": 1.0}, {"rapid_fall_hpa_3h": 11}, {"rapid_fall_alarm": "perhaps"}):
        r = c.put("/api/admin/barometer", json=bad)
        assert r.status_code == 422 and "perhaps" not in r.text and "east" not in r.text
    assert hub.config.barometer.hemisphere == "south"
    state = c.get("/api/admin/state").json()
    assert state["config"]["barometer"]["rapid_fall_hpa_3h"] == 4.0 and state["emulate"] is False


def test_demo_endpoint_is_emulate_only(tmp_path):
    hub = sensor_hub(tmp_path)
    c = admin_client(hub)
    r = c.post("/api/admin/barometer/demo", json={"scenario": "front"})
    assert r.status_code == 409 and hub.baro.demo is None
    emu = Hub(sub(tmp_path, "emu"), emulate=True)
    ec = admin_client(emu)
    assert ec.post("/api/admin/barometer/demo", json={"scenario": "storm"}).status_code == 200
    assert emu.baro.demo.scenario == "storm"
    assert ec.post("/api/admin/barometer/demo", json={"scenario": "volcano"}).status_code == 422


def test_dashboards_can_list_the_barometer_card(tmp_path):
    c = admin_client(sensor_hub(tmp_path))
    r = c.put("/api/admin/dashboards", json=[{"slug": "foh", "cards": ["env_tiles", "barometer"]}])
    assert r.status_code == 200
    assert "barometer" in c.get("/api/admin/state").json()["cards"]["known"]


def test_the_public_snapshot_has_the_block_and_no_private_fields(tmp_path):
    hub = Hub(sub(tmp_path, "e"), emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        hub.compute_site(time.time())
        snap = c.get("/api/snapshot").json()
        baro = snap["site"]["baro"]
        assert baro["state"] == "ok" and baro["tendency_word"] == "falling_quickly" and baro["rapid_fall"] is True
        assert baro["outlook"] in "ABDHORUVX"          # falling: that row of the Zambretti table
        assert not re.search(r"sim_|\.pressure|02:5e", json.dumps(baro))
        assert snap["site"]["pressure_source"] == "measured"


# --------------------------------------------------------------- the page
def test_page_tables_have_the_same_26_letters_as_the_server():
    js = (STATIC / "barometer.js").read_text(encoding="utf-8")
    block = re.search(r"baro\.OUTLOOK = \{(.*?)\n  \};", js, re.S).group(1)
    assert "".join(re.findall(r"^\s{4}([A-Z]): \[", block, re.M)) == bm.OUTLOOK_LETTERS
    words = re.search(r"baro\.TENDENCY = \{(.*?)\n  \};", js, re.S).group(1)
    assert re.findall(r"^\s{4}([a-z_]+): \[", words, re.M) == list(bm.WORDS)


def test_page_wires_the_card_in_one_place_each():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert html.count('data-card="barometer"') == 1 and '/static/barometer.js' in html
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert 'barometer: { el: cardEl("barometer"), render: renderBarometer' in js
    assert "toLocale" not in js and "Intl." not in js
    assert 'if (has("barometer")) renderBarometer();' in js
    bjs = (STATIC / "barometer.js").read_text(encoding="utf-8")
    assert "?." not in bjs and "??" not in bjs                      # iOS 12
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce) { .baro-needle" in css and "body.layout-wall .baro-dial-host { display: none; }" in css


def test_js_view_cases_run_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "barometer_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
