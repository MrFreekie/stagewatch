"""A battery gauge can read a little over 100 % when full: Stagewatch shows 0 to 100, and never turns "not a number" into 0 %."""
import math

from stagewatch.core.model import Kind
from stagewatch.integrations.esphome.mapping import to_canonical


def test_battery_is_clamped_to_0_to_100():
    assert to_canonical(Kind.BATTERY, 101.0, "%") == 100.0
    assert to_canonical(Kind.BATTERY, 100.0, "%") == 100.0
    assert to_canonical(Kind.BATTERY, 57.5, "%") == 57.5
    assert to_canonical(Kind.BATTERY, -2.0, "%") == 0.0


def test_battery_not_a_number_is_no_reading():
    assert to_canonical(Kind.BATTERY, math.nan, "%") is None
    assert to_canonical(Kind.BATTERY, math.inf, "%") is None
