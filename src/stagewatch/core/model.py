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
    BATTERY = "battery"          # node battery level, %; shown per node, never averaged
    SIGNAL = "signal_strength"   # node Wi-Fi signal, dBm; shown per node, never averaged
    # Sound level from measurement software (Smaart and similar), dB. Recorded exactly as received:
    # never averaged, smoothed, rounded or calibrated here, and never part of a site average. The
    # weighting, time constant and metric travel as Entity.labels.
    SOUND_LEVEL = "sound_level"
    GENERIC = "generic"


UNITS = {
    Kind.TEMPERATURE: "°C",
    Kind.HUMIDITY: "%",
    Kind.PRESSURE: "Pa",
    Kind.SPEED_OF_SOUND: "m/s",
    Kind.DEW_POINT: "°C",
    Kind.CONTACT: "",
    Kind.BATTERY: "%",
    Kind.SIGNAL: "dBm",
    Kind.SOUND_LEVEL: "dB",
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
    # "sensor" (measures the site; listed on the Sensors card and the chart) or "service" (a
    # software source such as Ontime; no readings, listed under Integrations in the admin page).
    category: str = "sensor"
    # "environment" (air at the site) or "equipment" (gear). Set by the integration from the
    # node's config. Not in to_dict(): dashboards group by the entity's role.
    role: str = "environment"
    # The input a measurement source is tied to, as the software names it (cleaned text). Only the
    # sound level device sets it; left out of to_dict() while empty.
    input_name: str = ""
    # Extra public fields a service device carries (the sound level graph range). Merged into
    # to_dict(); empty for every other device.
    public: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "name": self.name,
            "integration": self.integration,
            "category": self.category,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "area": self.area,
            "status": self.status.value,
            "status_detail": self.status_detail,
        }
        if self.input_name:
            out["input_name"] = self.input_name
        out.update(self.public)
        return out


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
    # Short text labels that describe the measurement (for sound levels: weighting, time_constant,
    # metric, period, slot). Public, plain text; absent from to_dict() when empty.
    labels: dict[str, str] = field(default_factory=dict)
    # The owner's own label for where a sound level value is measured ("FOH"): cleaned plain text, a
    # label only (never part of an id, key or history). Absent from to_dict() when empty.
    location: str = ""

    def to_dict(self, now: float, stale_after_s: float, offset: float = 0.0, role: str = "environment") -> dict:
        data = {
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
        # Only the amount, in canonical units (C, %, Pa), and only when it is not zero, so
        # dashboards can mark an adjusted sensor. Nothing else about calibration goes out.
        if offset and not self.derived:
            data["offset"] = offset
        # Only for equipment (gear readings, never averaged); absent means environment, so the
        # public shape of every existing sensor is unchanged.
        if role == "equipment" and not self.derived:
            data["role"] = "equipment"
        if self.labels:
            data["labels"] = dict(self.labels)
        if self.location:
            data["location"] = self.location
        return data

    def is_stale(self, now: float, stale_after_s: float) -> bool:
        return self.updated is None or now - self.updated > stale_after_s


@dataclass
class Marker:
    id: int
    ts: float
    label: str
    source: str = ""
    # Hidden markers stay in the history, delta maths and reports; dashboards leave them off the
    # chart and list them only under "Show hidden".
    hidden: bool = False
    note: str = ""  # plain text, new lines allowed (recorder.clean_note)

    def to_dict(self) -> dict:
        return {"id": self.id, "ts": self.ts, "label": self.label, "source": self.source,
                "hidden": self.hidden, "note": self.note}


@dataclass
class StateEvent:
    entity_id: str
    value: float | None
    ts: float = field(default_factory=time.time)
