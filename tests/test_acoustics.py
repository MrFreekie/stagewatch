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


# Values from the AD0 physics sign-off (independent implementation, 18/10/2026).
@pytest.mark.parametrize("band,t,rh,expected", [
    ("4k", 20, 70, 0.022911), ("8k", 20, 70, 0.076621), ("16k", 20, 70, 0.276238),
    ("8k", 20, 40, 0.128361), ("8k", 15, 50, 0.128573),
    ("4k", 25, 40, 0.030084), ("8k", 25, 40, 0.104493), ("16k", 25, 40, 0.365482),
    ("8k", 15, 74.32, 0.088630),
])
def test_iso9613_alpha_values(band, t, rh, expected):
    assert ac.iso9613_alpha_db_per_m(ac.ISO_MIDBAND_HZ[band], t, rh) == pytest.approx(expected, rel=2e-3)


def test_iso_midbands_and_band_sets():
    assert ac.ISO_MIDBAND_HZ["8k"] == pytest.approx(7943.28, abs=0.01)
    assert ac.ISO_MIDBAND_HZ["5k"] == pytest.approx(5011.87, abs=0.01)
    assert ac.ISO_MIDBAND_HZ["6.3k"] == pytest.approx(6309.57, abs=0.01)
    assert set(ac.AIR_LOSS_OCTAVE_BANDS) <= set(ac.AIR_LOSS_THIRD_BANDS) <= set(ac.ISO_MIDBAND_HZ)


def test_air_loss_changes_since_marker():
    damp = ac.air_loss_change_db("8k", 90, (25, 40, 101325), (15, 74.32, 101325))
    assert damp == pytest.approx(-1.428, abs=0.01)  # cooling at the same moisture: less loss
    dry = ac.air_loss_change_db("16k", 90, (20, 70, 101325), (20, 40, 101325))
    assert dry == pytest.approx(12.359, abs=0.03)
    assert ac.air_loss_db("8k", 40, 20, 70) == pytest.approx(3.065, abs=0.01)


def test_air_loss_properties():
    assert ac.air_loss_db("8k", 0, 20, 50) == 0
    assert ac.air_loss_db("8k", 80, 20, 50) == pytest.approx(2 * ac.air_loss_db("8k", 40, 20, 50))
    a, b = (20, 70, 101325), (20, 40, 101325)
    assert ac.air_loss_change_db("8k", 50, a, b) == pytest.approx(-ac.air_loss_change_db("8k", 50, b, a))
    assert ac.air_loss_change_db("8k", 50, a, a) == 0
    last = 0.0
    for band in ac.AIR_LOSS_THIRD_BANDS:
        v = ac.air_loss_db(band, 50, 20, 50)
        assert v > last
        last = v


def test_iso_range_issues():
    assert ac.iso_range_issues(20, 50, 101325) == []
    assert "temperature_low" in ac.iso_range_issues(-25, 50, 101325)
    assert "temperature_high" in ac.iso_range_issues(55, 50, 101325)
    assert "humidity_high" in ac.iso_range_issues(40, 100, 101325)
    assert "humidity_low" in ac.iso_range_issues(-10, 10, 101325)


def test_delay_drift():
    c_ref = ac.speed_of_sound(20, 50, 101325)
    c_now = ac.speed_of_sound(26, 50, 101325)
    assert ac.delay_drift_ms(60, c_ref, c_now) == pytest.approx(-1.892, abs=0.005)
    assert ac.delay_drift_ms(60, c_now, c_ref) == pytest.approx(1.892, abs=0.005)
    assert ac.delay_drift_ms(120, c_ref, c_now) == pytest.approx(2 * ac.delay_drift_ms(60, c_ref, c_now))
    assert ac.delay_drift_ms(60, c_ref, c_ref) == 0
    # missing pressure assumption barely moves drift (sea level vs 1000 m)
    p1k = ac.pressure_at_altitude_pa(1000)
    d1 = ac.delay_drift_ms(60, ac.speed_of_sound(20, 50, 101325), ac.speed_of_sound(26, 50, 101325))
    d2 = ac.delay_drift_ms(60, ac.speed_of_sound(20, 50, p1k), ac.speed_of_sound(26, 50, p1k))
    assert abs(d1 - d2) < 0.02
