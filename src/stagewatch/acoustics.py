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


_R_DRY_AIR = _ISA_R / _ISA_M
"""Specific gas constant of dry air, J/(kg K) (287.053)."""


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def msl_from_station_pa(station_pa: float, altitude_m: float, temp_c: float | None = None) -> float:
    """Mean sea-level pressure from the pressure at the station, Pa.

    With a measured temperature this is the hypsometric reduction: the pressure ratio is
    ``exp(g h / (Rd Tm))``, where Tm is the mean temperature of the air column between the
    station and sea level, taken as the measured temperature plus half the standard lapse over
    the height (``Tm = T + L h / 2``). Humidity is ignored. Without a temperature it falls back to
    the International Standard Atmosphere (the "QNH" style reduction), which is the exact inverse
    of :func:`pressure_at_altitude_pa`. The caller says which one applied.
    """
    if not (_finite(station_pa) and station_pa > 0 and _finite(altitude_m)):
        return math.nan
    if temp_c is not None and _finite(temp_c):
        tm = temp_c + 273.15 + _ISA_LAPSE * altitude_m / 2.0
        if tm > 100.0:
            return station_pa * math.exp(_ISA_G * altitude_m / (_R_DRY_AIR * tm))
    return station_pa * STANDARD_PRESSURE_PA / pressure_at_altitude_pa(altitude_m)


def altitude_from_msl_pa(station_pa: float, msl_pa: float, temp_c: float | None = None) -> float:
    """The altitude at which :func:`msl_from_station_pa` turns ``station_pa`` into ``msl_pa``, m.

    Exact inverse of the reduction, for the same temperature. Hypsometric: with
    ``x = ln(msl/p) Rd / g`` the height is ``h = x T / (1 - x L / 2)`` (T in kelvin). ISA:
    ``h = (T0 / L) (1 - (p / msl)^(Rd L / g))``.
    """
    if not (_finite(station_pa) and _finite(msl_pa) and station_pa > 0 and msl_pa > 0):
        return math.nan
    if temp_c is not None and (not _finite(temp_c) or temp_c + 273.15 <= 100.0):
        temp_c = None
    if temp_c is None:
        expo = _R_DRY_AIR * _ISA_LAPSE / _ISA_G
        ratio = station_pa / msl_pa   # pressure_at_altitude_pa(h) / 101325 Pa
        return (_ISA_T0 / _ISA_LAPSE) * (1.0 - ratio ** expo)
    x = math.log(msl_pa / station_pa) * _R_DRY_AIR / _ISA_G
    denom = 1.0 - x * _ISA_LAPSE / 2.0
    if denom <= 0:
        return math.nan
    return x * (temp_c + 273.15) / denom


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


def delay_drift_ms(distance_m: float, c_ref_m_s: float, c_now_m_s: float) -> float:
    """Change in travel time over ``distance_m`` since the reference, milliseconds.

    Positive means sound now arrives later (cooler air). Add it to the current delay to get
    the suggested setting.
    """
    return 1000.0 * distance_m * (1.0 / c_now_m_s - 1.0 / c_ref_m_s)


# Air absorption of sound, ISO 9613-1:1993 (pure tone, dB per metre). The ISO saturation
# ratio below differs from the Davis vapour pressure used for speed of sound by under 0.1 %.
ISO_T0_K = 293.15
ISO_T01_K = 273.16
ISO_PR_KPA = 101.325

ISO_MIDBAND_HZ = {
    "4k": 10 ** 3.6, "5k": 10 ** 3.7, "6.3k": 10 ** 3.8, "8k": 10 ** 3.9,
    "10k": 10 ** 4.0, "12.5k": 10 ** 4.1, "16k": 10 ** 4.2,
}
"""Exact base-10 one-third-octave midbands (IEC 61260) for the nominal graphic-EQ labels."""
AIR_LOSS_OCTAVE_BANDS = ("4k", "8k", "16k")
AIR_LOSS_THIRD_BANDS = ("4k", "5k", "6.3k", "8k", "10k", "12.5k", "16k")

ISO_H_RANGE_PCT = (0.05, 5.0)
ISO_TEMP_RANGE_C = (-20.0, 50.0)
ISO_PRESSURE_MAX_PA = 200_000.0


def iso_h_percent(temp_c: float, pressure_pa: float, rh_pct: float) -> float:
    """Molar concentration of water vapour, percent (ISO 9613-1 equations 1 to 3)."""
    t = temp_c + 273.15
    c = -6.8346 * (ISO_T01_K / t) ** 1.261 + 4.6151
    psat_ratio = 10 ** c
    pa_ratio = pressure_pa / 1000.0 / ISO_PR_KPA
    return rh_pct * psat_ratio / pa_ratio


def iso9613_alpha_db_per_m(freq_hz: float, temp_c: float, rh_pct: float,
                           pressure_pa: float = STANDARD_PRESSURE_PA) -> float:
    """Atmospheric absorption of a pure tone, dB per metre (ISO 9613-1:1993)."""
    t = temp_c + 273.15
    pa_ratio = pressure_pa / 1000.0 / ISO_PR_KPA
    t_ratio = t / ISO_T0_K
    h = iso_h_percent(temp_c, pressure_pa, rh_pct)
    fr_o = pa_ratio * (24.0 + 4.04e4 * h * (0.02 + h) / (0.391 + h))
    fr_n = pa_ratio * t_ratio ** -0.5 * (
        9.0 + 280.0 * h * math.exp(-4.170 * (t_ratio ** (-1.0 / 3.0) - 1.0)))
    f2 = freq_hz * freq_hz
    return 8.686 * f2 * (
        1.84e-11 / pa_ratio * t_ratio ** 0.5
        + t_ratio ** -2.5 * (
            0.01275 * math.exp(-2239.1 / t) / (fr_o + f2 / fr_o)
            + 0.1068 * math.exp(-3352.0 / t) / (fr_n + f2 / fr_n)))


def iso_range_issues(temp_c: float, rh_pct: float, pressure_pa: float) -> list[str]:
    """Which limits of :func:`iso9613_alpha_db_per_m` the inputs are outside of.

    ``[]`` inside the range, otherwise any of ``"temperature_low"``, ``"temperature_high"``,
    ``"humidity_low"``, ``"humidity_high"``, ``"pressure_high"``. The value is still usable
    outside the range; only the standard's accuracy claim is lost.
    """
    issues: list[str] = []
    if temp_c < ISO_TEMP_RANGE_C[0]:
        issues.append("temperature_low")
    elif temp_c > ISO_TEMP_RANGE_C[1]:
        issues.append("temperature_high")
    h = iso_h_percent(temp_c, pressure_pa, rh_pct)
    if h < ISO_H_RANGE_PCT[0]:
        issues.append("humidity_low")
    elif h > ISO_H_RANGE_PCT[1]:
        issues.append("humidity_high")
    if pressure_pa > ISO_PRESSURE_MAX_PA:
        issues.append("pressure_high")
    return issues


def air_loss_db(band: str, distance_m: float, temp_c: float, rh_pct: float,
                pressure_pa: float = STANDARD_PRESSURE_PA) -> float:
    """Loss over ``distance_m`` at an ISO midband (label such as ``"8k"``), dB."""
    return iso9613_alpha_db_per_m(ISO_MIDBAND_HZ[band], temp_c, rh_pct, pressure_pa) * distance_m


def air_loss_change_db(band: str, distance_m: float, ref: tuple[float, float, float],
                       now: tuple[float, float, float]) -> float:
    """Extra loss now compared with the reference, dB; positive means more top end lost.

    ``ref`` and ``now`` are ``(temp_c, rh_pct, pressure_pa)``.
    """
    return air_loss_db(band, distance_m, *now) - air_loss_db(band, distance_m, *ref)


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
