"""The rough battery estimate: nothing until half an hour of readings, "charging" on a clear rise, a time left
worked out by hand for a steady fall, and nothing carried across a reset."""
import pytest

from stagewatch.integrations.witmotion.battery import BatteryTrend


def feed(trend, start_v, dv_per_sample, n, every_s=45.0, t0=0.0):
    for i in range(n):
        trend.add(t0 + i * every_s, round(start_v + i * dv_per_sample, 2))


def test_nothing_until_half_an_hour():
    t = BatteryTrend()
    feed(t, 3.95, -0.002, 30)                  # 30 samples, 21.75 min
    assert t.view() == {"state": "", "hours_left": None}


def test_steady_fall_gives_time_left():
    t = BatteryTrend()
    # 3.70 V -> 3.60 V over 60 min: 28 % -> 14 % on the curve, so 14 %/h and about 1 h left at 14 %.
    for i in range(81):
        t.add(i * 45.0, round(3.70 - 0.10 * i / 80, 2))
    v = t.view()
    assert v["state"] == "discharging"
    assert v["hours_left"] == pytest.approx(1.0, abs=0.15)


def test_flat_is_steady():
    t = BatteryTrend()
    feed(t, 3.85, 0.0, 50)
    assert t.view() == {"state": "steady", "hours_left": None}


def test_clear_rise_is_charging_straight_away():
    t = BatteryTrend()
    feed(t, 3.80, 0.01, 6)                     # +0.05 V in under 4 minutes
    assert t.view()["state"] == "charging"


def test_reset_forgets_everything():
    t = BatteryTrend()
    feed(t, 3.70, -0.001, 80)
    t.reset()
    t.add(10_000.0, 3.6)
    assert t.view() == {"state": "", "hours_left": None}
