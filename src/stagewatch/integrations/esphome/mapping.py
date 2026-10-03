"""Map ESPHome sensor metadata onto Stagewatch entity kinds and SI units."""

from __future__ import annotations

from ...acoustics import pressure_to_pa, temperature_to_c
from ...core.model import UNITS, Kind

_DEVICE_CLASS_KIND = {
    "temperature": Kind.TEMPERATURE,
    "humidity": Kind.HUMIDITY,
    "pressure": Kind.PRESSURE,
    "atmospheric_pressure": Kind.PRESSURE,
    "battery": Kind.BATTERY,
    "signal_strength": Kind.SIGNAL,
}


def sensor_kind(device_class: str, unit: str) -> Kind:
    kind = _DEVICE_CLASS_KIND.get((device_class or "").lower())
    if kind:
        return kind
    # Older YAMLs often omit device_class; fall back to the unit.
    u = (unit or "").replace("°", "").strip().lower()
    if u in ("c", "f"):
        return Kind.TEMPERATURE
    if u in ("hpa", "pa", "kpa", "mbar", "inhg", "mmhg"):
        return Kind.PRESSURE
    if u == "dbm":
        return Kind.SIGNAL
    return Kind.GENERIC


def canonical_unit(kind: Kind, source_unit: str) -> str:
    return UNITS.get(kind, source_unit or "")


def to_canonical(kind: Kind, value: float, source_unit: str) -> float | None:
    """Convert a reading to the canonical unit for its kind; None if the
    source unit can't be interpreted (safer than recording a wrong number)."""
    if kind == Kind.TEMPERATURE:
        return temperature_to_c(value, source_unit or "°C")
    if kind == Kind.PRESSURE:
        return pressure_to_pa(value, source_unit or "hPa")
    return value
