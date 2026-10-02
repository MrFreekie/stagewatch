"""Core data model: areas, devices, entities, states, markers.

Every value is stored in canonical SI-ish units (degC, %, Pa, m/s) regardless
of what the source device reports; integrations convert on the way in and the
UI converts on the way out.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    """Standard device status, modelled on the Q-SYS status pin so every
    integration reports health the same way."""

    OK = "ok"
    INITIALIZING = "initializing"
    COMPROMISED = "compromised"
    FAULT = "fault"
    MISSING = "missing"
    NOT_PRESENT = "not_present"


class Kind(str, Enum):
    """What an entity measures. Drives units, averaging and dashboard cards."""

    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    SPEED_OF_SOUND = "speed_of_sound"
    DEW_POINT = "dew_point"
    CONTACT = "contact"
    GENERIC = "generic"


UNITS = {
    Kind.TEMPERATURE: "°C",
    Kind.HUMIDITY: "%",
    Kind.PRESSURE: "Pa",
    Kind.SPEED_OF_SOUND: "m/s",
    Kind.DEW_POINT: "°C",
    Kind.CONTACT: "",
}

ENV_KINDS = (Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "unnamed"


_HEX12 = re.compile(r"[0-9a-f]{12}")
_MAC_SEPARATORS = str.maketrans("", "", ":-. ")


def normalise_mac(value: object) -> str | None:
    """``AA:BB:CC:DD:EE:FF``, ``aa-bb-...``, ``aabb.ccdd.eeff`` or ``aabbccddeeff`` -> ``aabbccddeeff``.
    None for anything that isn't exactly 12 hex digits (never guess). The one MAC check used
    everywhere (config, integrations, API)."""
    if not isinstance(value, str):
        return None
    mac = value.strip().lower().translate(_MAC_SEPARATORS)
    return mac if _HEX12.fullmatch(mac) else None


@dataclass
class Device:
    id: str
    name: str
    integration: str
    manufacturer: str = ""
    model: str = ""
    area: str = ""
    status: Status = Status.INITIALIZING
    status_detail: str = ""
    # Hardware key of the board ("mac:<12hex>"), set by the integration once known. Admin only:
    # never in to_dict(), so it stays off the public snapshot.
    hw_id: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "integration": self.integration,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "area": self.area,
            "status": self.status.value,
            "status_detail": self.status_detail,
        }


@dataclass
class Entity:
    id: str
    device_id: str
    name: str
    kind: Kind
    unit: str = ""
    decimals: int = 1
    derived: bool = False
    value: float | None = None
    raw_value: float | None = None
    updated: float | None = None
    # Hardware key of the measurement ("mac:<12hex>/<object_id>"), set by the integration in
    # register_entity; calibration follows it (core/calibration.py). Admin only: never in to_dict().
    hw_key: str = ""

    def to_dict(self, now: float, stale_after_s: float) -> dict:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "name": self.name,
            "kind": self.kind.value,
            "unit": self.unit,
            "decimals": self.decimals,
            "derived": self.derived,
            "value": self.value,
            "raw_value": self.raw_value,
            "updated": self.updated,
            "stale": self.is_stale(now, stale_after_s),
        }

    def is_stale(self, now: float, stale_after_s: float) -> bool:
        return self.updated is None or now - self.updated > stale_after_s


@dataclass
class Marker:
    id: int
    ts: float
    label: str
    source: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "ts": self.ts, "label": self.label, "source": self.source}


@dataclass
class StateEvent:
    entity_id: str
    value: float | None
    ts: float = field(default_factory=time.time)
