"""Shelly H&T (temperature and humidity) readings from the status a Shelly pushes. Pure: no network, no hub.

Source: Shelly Gen2+ API documentation, components Temperature (``temperature:0``, ``tC``), Humidity
(``humidity:0``, ``rh``), DevicePower (``devicepower:0``: ``battery.percent``, ``battery.V``,
``external.present``) and Sys (``sys.wakeup_period``). Temperature is read in degrees C (``tC``); ``tF`` is
never used. A field that is missing, null or not a number is None: never zero, never guessed.

Unverified (no real H&T Gen3 capture yet): the wake period of a real device, the field that gives Wi-Fi
signal (read optionally from ``wifi.rssi``; shown only if the device sends it), and the event names.
"""

from __future__ import annotations

from ...core.model import Kind
from .readings import num

# object_id -> (Kind, decimals, component the value comes from)
ENTITIES = {
    "temperature": (Kind.TEMPERATURE, 1, "temperature:0"),
    "humidity": (Kind.HUMIDITY, 1, "humidity:0"),
    "battery": (Kind.BATTERY, 0, "devicepower:0"),
    "signal": (Kind.SIGNAL, 0, "wifi"),
}
NAMES = {"temperature": "Temperature", "humidity": "Humidity", "battery": "Battery", "signal": "Wi-Fi signal"}
RANGE_C = (0.0, 40.0)            # the H&T Gen3 is rated 0 to 40 degrees C (indoor only)
LOW_BATTERY_ON, LOW_BATTERY_OFF = 20.0, 25.0
BATTERY_STALE_S = 9000.0         # 2.5 hours: a report is due every 2 hours on batteries
USB_STALE_S = 720.0              # 12 minutes: a report about every 5 minutes on USB power
STALE_MIN_S, STALE_MAX_S = 120.0, 21600.0


def _get(d: object, *path: str) -> object:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def values(status: dict, names: set | None = None) -> dict:
    """object_id -> value (or None) for the readings whose component is in ``names`` (all of them when None). A
    component that is not in the status gives no entry at all, so a partial status never blanks another reading."""
    out: dict = {}

    def want(comp: str) -> bool:
        return (names is None or comp in names) and isinstance(status.get(comp), dict)

    if want("temperature:0"):
        out["temperature"] = num(_get(status, "temperature:0", "tC"))
    if want("humidity:0"):
        rh = num(_get(status, "humidity:0", "rh"))
        out["humidity"] = rh if rh is None or 0.0 <= rh <= 100.0 else None
    if want("devicepower:0"):
        pct = num(_get(status, "devicepower:0", "battery", "percent"))
        out["battery"] = pct if pct is None or 0.0 <= pct <= 100.0 else None
    if want("wifi") and "rssi" in status["wifi"]:
        out["signal"] = num(_get(status, "wifi", "rssi"))
    return out


def info(status: dict) -> dict:
    """What the admin page shows about the device (not recorded): wake period (s), external power, battery volts."""
    period = num(_get(status, "sys", "wakeup_period"))
    cause = _get(status, "sys", "wakeup_reason", "cause")
    ext = _get(status, "devicepower:0", "external", "present")
    return {"wake_period_s": period if period and period > 0 else None,
            "wake_cause": cause[:24] if isinstance(cause, str) else "",
            "external_power": ext if isinstance(ext, bool) else None,
            "battery_v": num(_get(status, "devicepower:0", "battery", "V"))}


def stale_limit_s(wake_period_s: float | None, external_power: bool | None) -> float:
    """How long a reading counts as current: min(2.2 x the wake period, the period + 30 minutes), kept between 2
    minutes and 6 hours. With no period known: 2.5 hours on batteries, 12 minutes on USB power."""
    if wake_period_s and wake_period_s > 0:
        return max(STALE_MIN_S, min(STALE_MAX_S, min(2.2 * wake_period_s, wake_period_s + 1800.0)))
    return USB_STALE_S if external_power else BATTERY_STALE_S


def battery_low(percent: float | None, external_power: bool | None, was_low: bool) -> bool:
    """The low-battery advisory: on at 20 % or less, off at 25 % or more, held in between, never on USB power."""
    if percent is None or external_power:
        return False
    if percent <= LOW_BATTERY_ON:
        return True
    if percent >= LOW_BATTERY_OFF:
        return False
    return was_low


def out_of_range(temp_c: float | None) -> bool:
    return temp_c is not None and not (RANGE_C[0] <= temp_c <= RANGE_C[1])
