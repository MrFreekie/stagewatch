import math

import pytest

from stagewatch import acoustics as ac


def test_speed_of_sound_dry_zero_c_matches_cramer():
    # Cramer (1993): c0 = 331.5024 m/s at 0 degC, dry, 101.325 kPa (with 314 ppm CO2
    # the paper's tabulated value is ~331.46). Allow the CO2/pressure terms.
    c = ac.speed_of_sound(0.0, 0.0, ac.STANDARD_PRESSURE_PA)
    assert c == pytest.approx(331.46, abs=0.05)


def test_speed_of_sound_20c_50rh():
    # Widely quoted value for 20 degC / 50 % RH / sea level: ~343.99 m/s
    assert ac.speed_of_sound(20.0, 50.0) == pytest.approx(343.99, abs=0.1)


def test_temperature_dominates():
    # ~0.6 m/s per degC around room temperature
    slope = ac.speed_of_sound(21.0, 50.0) - ac.speed_of_sound(20.0, 50.0)
    assert 0.55 < slope < 0.65


def test_humidity_raises_speed():
    assert ac.speed_of_sound(20.0, 100.0) > ac.speed_of_sound(20.0, 0.0)


def test_measured_pressure_equals_isa_at_sea_level():
    assert ac.pressure_at_altitude_pa(0.0) == pytest.approx(101325.0)
    assert ac.speed_of_sound(15, 40, ac.pressure_at_altitude_pa(0)) == pytest.approx(
        ac.speed_of_sound(15, 40, 101325.0))


def test_isa_pressure_at_1000m():
    assert ac.pressure_at_altitude_pa(1000.0) == pytest.approx(89875, rel=1e-3)


def test_dew_point():
    assert ac.dew_point_c(20.0, 100.0) == pytest.approx(20.0, abs=0.05)
    assert ac.dew_point_c(20.0, 50.0) == pytest.approx(9.26, abs=0.1)


def test_travel_time():
    assert ac.travel_time_ms(343.0, 343.0) == pytest.approx(1000.0)


@pytest.mark.parametrize("value,unit,expected", [
    (1013.25, "hPa", 101325.0), (101.325, "kPa", 101325.0), (1013.25, "mbar", 101325.0),
    (29.92, "inHg", 101320.4),
])
def test_pressure_units(value, unit, expected):
    assert ac.pressure_to_pa(value, unit) == pytest.approx(expected, rel=1e-4)


def test_unknown_units_return_none():
    assert ac.pressure_to_pa(1.0, "furlongs") is None
    assert ac.temperature_to_c(1.0, "rankine") is None


def test_temperature_units():
    assert ac.temperature_to_c(68.0, "°F") == pytest.approx(20.0)
    assert ac.temperature_to_c(293.15, "K") == pytest.approx(20.0)
    assert ac.temperature_to_c(20.0, "°C") == 20.0
    assert not math.isnan(ac.speed_of_sound(-10, 0))
