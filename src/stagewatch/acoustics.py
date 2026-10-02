"""Air physics for sound: speed of sound, dew point, pressure units.

Pure functions, no I/O. Speed of sound follows Cramer (1993), JASA 93(5):
2510-2516, "The variation of the specific heat ratio and the speed of sound
in air with temperature, pressure, humidity, and CO2 concentration".
Valid 0-30 degC, 75-102 kPa, water vapour mole fraction <= 0.06, CO2 <= 0.01;
accuracy <= 300 ppm inside that range. Outside it the fit still returns a
sensible value but the accuracy guarantee no longer holds.
"""

from __future__ import annotations

import math

_A = (
    331.5024, 0.603055, -0.000528,
    51.471935, 0.1495874, -0.000782,
    -1.82e-7, 3.73e-8, -2.93e-10,
    -85.20931, -0.228525, 5.91e-5,
    -2.835149, -2.15e-13,
    29.179762, 0.000486,
)

CO2_MOLE_FRACTION = 0.0004
"""~400 ppm; its effect on c is well under 0.01 m/s at any outdoor level."""

STANDARD_PRESSURE_PA = 101325.0

_ISA_T0, _ISA_LAPSE = 288.15, 0.0065
_ISA_G, _ISA_M, _ISA_R = 9.80665, 0.0289644, 8.3144598


def saturation_vapor_pressure_pa(temp_c: float) -> float:
    """Davis (1992) saturation vapour pressure of water, Pa."""
    t_k = temp_c + 273.15
    return math.exp(1.2811805e-5 * t_k ** 2 - 1.9509874e-2 * t_k
                    + 34.04926034 - 6.3536311e3 / t_k)


def water_vapor_mole_fraction(temp_c: float, pressure_pa: float, rh_pct: float) -> float:
    """Mole fraction of water vapour in air (xw) from relative humidity."""
    h = max(0.0, min(100.0, rh_pct)) / 100.0
    enhancement = 1.00062 + 3.14e-8 * pressure_pa + 5.6e-7 * temp_c ** 2
    return h * enhancement * saturation_vapor_pressure_pa(temp_c) / pressure_pa


def pressure_at_altitude_pa(altitude_m: float) -> float:
    """International Standard Atmosphere pressure at an elevation. Used only
    when no pressure sensor is reporting; it ignores weather."""
    altitude_m = max(-500.0, min(11000.0, altitude_m))
    base = 1.0 - _ISA_LAPSE * altitude_m / _ISA_T0
    return STANDARD_PRESSURE_PA * base ** (_ISA_G * _ISA_M / (_ISA_R * _ISA_LAPSE))


def speed_of_sound(temp_c: float, rh_pct: float = 50.0,
                   pressure_pa: float = STANDARD_PRESSURE_PA) -> float:
    """Speed of sound in air, m/s (Cramer 1993)."""
    p = pressure_pa
    xw = water_vapor_mole_fraction(temp_c, p, rh_pct)
    xc = CO2_MOLE_FRACTION
    t = temp_c
    a = _A
    return (a[0] + a[1] * t + a[2] * t ** 2
            + (a[3] + a[4] * t + a[5] * t ** 2) * xw
            + (a[6] + a[7] * t + a[8] * t ** 2) * p
            + (a[9] + a[10] * t + a[11] * t ** 2) * xc
            + a[12] * xw ** 2
            + a[13] * p ** 2
            + a[14] * xc ** 2
            + a[15] * xw * p * xc)


CRAMER_TEMP_RANGE_C = (0.0, 30.0)
CRAMER_PRESSURE_RANGE_PA = (75_000.0, 102_000.0)
"""The ranges Cramer (1993) validated the speed-of-sound fit for (bounds included). The water
vapour limit (xw <= 0.06) can't be exceeded inside them: saturated air at 30 degC and 75 kPa
has xw of about 0.057."""


def speed_of_sound_range_issues(temp_c: float, pressure_pa: float) -> list[str]:
    """Which validated bounds of :func:`speed_of_sound` the inputs are outside of.

    Returns ``[]`` inside the range, otherwise any of ``"temperature_low"``,
    ``"temperature_high"``, ``"pressure_low"``, ``"pressure_high"``. The value is still
    usable outside the range; only the accuracy guarantee is lost.
    """
    issues: list[str] = []
    t_lo, t_hi = CRAMER_TEMP_RANGE_C
    p_lo, p_hi = CRAMER_PRESSURE_RANGE_PA
    if temp_c < t_lo:
        issues.append("temperature_low")
    elif temp_c > t_hi:
        issues.append("temperature_high")
    if pressure_pa < p_lo:
        issues.append("pressure_low")
    elif pressure_pa > p_hi:
        issues.append("pressure_high")
    return issues


def dew_point_c(temp_c: float, rh_pct: float) -> float:
    """Dew point via the Magnus formula (Alduchov & Eskridge 1996 constants),
    within ~0.35 degC for -40..50 degC."""
    rh = max(0.1, min(100.0, rh_pct))
    b, c = 17.625, 243.04
    gamma = math.log(rh / 100.0) + b * temp_c / (c + temp_c)
    return c * gamma / (b - gamma)


def travel_time_ms(distance_m: float, c_m_s: float) -> float:
    """Time for sound to cover distance_m at speed c_m_s, milliseconds."""
    return 1000.0 * distance_m / c_m_s


_PRESSURE_TO_PA = {
    "pa": 1.0,
    "hpa": 100.0,
    "mbar": 100.0,
    "kpa": 1000.0,
    "bar": 100000.0,
    "inhg": 3386.389,
    "mmhg": 133.322,
    "psi": 6894.757,
}


def pressure_to_pa(value: float, unit: str) -> float | None:
    """Convert a pressure reading to Pa. Returns None for an unknown unit."""
    factor = _PRESSURE_TO_PA.get(unit.strip().lower())
    return None if factor is None else value * factor


def temperature_to_c(value: float, unit: str) -> float | None:
    """Convert a temperature reading to degC. Returns None for an unknown unit."""
    u = unit.strip().replace("°", "").replace("º", "").lower()
    if u in ("c", "degc", "celsius"):
        return value
    if u in ("f", "degf", "fahrenheit"):
        return (value - 32.0) * 5.0 / 9.0
    if u in ("k", "kelvin"):
        return value - 273.15
    return None
