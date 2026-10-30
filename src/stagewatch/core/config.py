"""Show/site configuration: pydantic models persisted as YAML.

Loading is tolerant: unknown keys are ignored and missing keys fall back to
defaults, so a config written by an older version still loads. Saving is
atomic (write to a temp file, then replace) so a power cut mid-save can't
leave a truncated config on a show day.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import os
import re
import tempfile
import time
import unicodedata
import zoneinfo
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_serializer, model_validator

from ..updater_common import atomic_write_bytes, fsync_dir, remove_stale_temps, replace_with_retry
from ..version import CONFIG_SCHEMA_VERSION
from . import power_tree, spl
from .cards import CARD_ID_RE, MAX_CARDS, clean_card_sizes, clean_tiles, default_cards, legacy_cards
from .model import normalise_mac, slugify

log = logging.getLogger(__name__)

# The default dashboards of config v1 (0.2.0), used when a v1 file has no dashboards key.
_V1_DEFAULT_DASHBOARDS = [
    {"slug": "foh", "title": "FOH", "layout": "tablet", "allow_marker": True, "allow_ack": True},
    {"slug": "phone", "title": "Phone", "layout": "phone", "allow_marker": True},
    {"slug": "wall", "title": "Wall", "layout": "wall", "allow_marker": False},
]


def _v1_to_v2(raw: dict) -> dict:
    """Config v1 -> v2 (pure, idempotent).  Only dashboards need work: each existing dashboard
    gets exactly what it showed in 0.2.0 as its ``cards`` (plus the schedule card, which stays
    hidden until there is a schedule).  Everything else new arrives through model defaults.
    Keys the models no longer have (e.g. an ESPHome ``password``) are left for validation to
    ignore and disappear at the next save; they are never logged."""
    out = dict(raw)
    dashboards = out.get("dashboards", None)
    if "dashboards" not in out:
        dashboards = [dict(d) for d in _V1_DEFAULT_DASHBOARDS]
    if isinstance(dashboards, list):
        migrated = []
        for d in dashboards:
            if isinstance(d, dict) and "cards" not in d:
                layout = d.get("layout", "tablet")
                d = {**d, "cards": legacy_cards(layout if isinstance(layout, str) else "tablet")}
            migrated.append(d)
        out["dashboards"] = migrated
    return out


def migrate(raw: dict) -> dict:
    """Upgrade a raw config dict from an older schema_version, one step at a
    time. Add a branch here whenever CONFIG_SCHEMA_VERSION is bumped."""
    version = int(raw.get("schema_version", 1))
    if version > CONFIG_SCHEMA_VERSION:
        log.warning("config.yaml is schema %s, newer than this build (%s); "
                    "unknown settings will be ignored", version, CONFIG_SCHEMA_VERSION)
    if version < 2:
        raw = _v1_to_v2(raw)
        version = 2
    raw["schema_version"] = CONFIG_SCHEMA_VERSION
    return raw


class _Model(BaseModel):
    # hide_input_in_errors: a bad noise_psk must never be echoed in an error message.
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


_TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
_HHMM_RE = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")  # ASCII digits only; fullmatch


def valid_timezone(v: str) -> str:
    """'' (use this computer's zone) or an IANA zone name that zoneinfo can load."""
    v = (v or "").strip()
    if v == "":
        return ""
    if not _TZ_RE.fullmatch(v) or v.startswith("/") or ".." in v:
        raise ValueError("not a time zone name")
    try:
        zoneinfo.ZoneInfo(v)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("unknown time zone") from None
    return v


class SiteConfig(_Model):
    name: str = "Stagewatch"
    altitude_m: float = Field(0.0, ge=-500, le=6000)
    reference_distance_m: float = Field(30.0, gt=0, le=2000)
    stale_after_s: float = Field(60.0, ge=5, le=3600)
    smoothing_tau_s: float = Field(30.0, ge=0, le=900)
    outlier_reject: bool = True
    # "Weight the average by accuracy" (admin, Sensors card). Off by default: equal weights, as
    # before. Additive with a default: no config schema bump; older builds ignore the key.
    weight_by_accuracy: bool = False
    timezone: str = ""          # "" = not set: use this computer's zone
    day_rollover: str = "06:00"  # a show day runs from this time to the same time next morning
    # "Add markers from the schedule" (schedule page): a marker at doors, soundchecks and act
    # changes as they happen. Additive with a default: no config schema bump.
    schedule_auto_markers: bool = True
    # Schedule card warning steps, in minutes before an item ends (NOW) or starts (NEXT). The first
    # (largest) step is amber, every later one orange. One list for the whole site. Additive with
    # a default that reproduces the old fixed 15 / 5: no config schema bump. Visual only.
    schedule_warn_minutes: list[int] = Field(default_factory=lambda: [15, 5])
    # The steps (a subset of the minutes above) that pulse slowly; NEXT never does.
    schedule_warn_flash_minutes: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _legacy_warn_flash(cls, data):
        """Nightly builds had one ``schedule_warn_flash: true`` tick box: flash at the smallest step."""
        if isinstance(data, dict) and "schedule_warn_flash" in data:
            data = dict(data)
            old = data.pop("schedule_warn_flash")
            mins = data.get("schedule_warn_minutes", [15, 5])
            if (old is True and "schedule_warn_flash_minutes" not in data and isinstance(mins, list)
                    and mins and all(isinstance(n, int) and not isinstance(n, bool) for n in mins)):
                data["schedule_warn_flash_minutes"] = [min(mins)]
        return data

    @field_validator("schedule_warn_flash_minutes", mode="before")
    @classmethod
    def _warn_flash(cls, v):
        if (not isinstance(v, list) or len(v) > 8 or len(set(map(repr, v))) != len(v)
                or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 240 for n in v)):
            raise ValueError("flashing times must be some of the warning times")
        return sorted(v, reverse=True)

    @model_validator(mode="after")
    def _flash_is_a_warning_time(self):
        if not set(self.schedule_warn_flash_minutes) <= set(self.schedule_warn_minutes):
            raise ValueError("flashing times must be some of the warning times")
        return self

    @field_validator("schedule_warn_minutes", mode="before")
    @classmethod
    def _warn_minutes(cls, v):
        if not isinstance(v, list) or not 1 <= len(v) <= 8:
            raise ValueError("warning times must be 1 to 8 whole minutes")
        for n in v:
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 240:
                raise ValueError("warning times must be 1 to 8 whole minutes")
        if len(set(v)) != len(v):
            raise ValueError("warning times must be 1 to 8 whole minutes")
        return sorted(v, reverse=True)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return valid_timezone(v)

    @field_validator("day_rollover")
    @classmethod
    def _rollover(cls, v: str) -> str:
        m = _HHMM_RE.fullmatch((v or "").strip())
        if not m or int(m.group(1)) > 11:
            raise ValueError("day rollover must be HH:MM between 00:00 and 11:59")
        return m.group(0)


class AdminConfig(_Model):
    pin_hash: str = ""




# Sensor roles. "environment" = air conditions at the site (feeds the site average, the speed of
# sound, the dew point and the barometer); "equipment" = readings about gear (an amp rack, a power
# supply), never averaged. Additive: a config without a role means "environment", so every older
# install behaves exactly as before. An older build ignores the key and averages everything again.
ROLES = ("environment", "equipment")
ROLE_ENVIRONMENT = "environment"
ROLE_EQUIPMENT = "equipment"


class EsphomeDeviceConfig(_Model):
    id: str
    host: str
    port: int = Field(6053, ge=1, le=65535)
    name: str = ""
    area: str = ""
    noise_psk: str = ""
    mac: str = ""  # the board's MAC (12 lowercase hex), set by the hub on first connect
    role: Literal["environment", "equipment"] = "environment"   # the node's role; a sensor can override it
    # "Sleeps between readings": the minutes between wake-ups of a deep-sleep node, 0 = off (the
    # default, nothing changes). Additive with a default: no config schema bump; an older build
    # ignores the key and treats the node as always on.
    sleep_minutes: int = Field(0, ge=0, le=1440)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v)

    @field_validator("mac")
    @classmethod
    def _mac(cls, v: str) -> str:
        if not (v or "").strip():
            return ""
        mac = normalise_mac(v)
        if mac is None:
            raise ValueError("MAC must be 12 hex characters")
        return mac


SHELLY_POLL_MIN_S = 2.0
SHELLY_POLL_MAX_S = 5.0
SHELLY_POLL_DEFAULT_S = 3.0
SHELLY_MAX_METERS = 16


class _TreeFields(_Model):
    """Where a meter or circuit sits in the power distribution tree (see core/power_tree.py). All optional.
    Additive with defaults: no schema bump; an older build ignores them and drops them on its next save.
    On load, a role this build does not know becomes "" and a number out of range becomes None, so a file
    from a newer build never costs the meter."""
    role: str = ""
    unit_no: int | None = None
    upstream: str = Field("", max_length=80)
    # The way (circuit number) of the UPSTREAM unit this one is fed from: "fed from way 2 of CDU02". Belongs
    # to the link, not to this unit. Optional; additive, no schema bump; an older build ignores it.
    way: int | None = None

    @field_validator("way", mode="before")
    @classmethod
    def _way_range(cls, v):
        ok = isinstance(v, int) and not isinstance(v, bool) and power_tree.UNIT_NO_MIN <= v <= power_tree.UNIT_NO_MAX
        return v if ok else None

    @field_validator("role", mode="before")
    @classmethod
    def _known_role(cls, v):
        return v if v in power_tree.ROLES else ""

    @field_validator("unit_no", mode="before")
    @classmethod
    def _unit_range(cls, v):
        ok = isinstance(v, int) and not isinstance(v, bool) and power_tree.UNIT_NO_MIN <= v <= power_tree.UNIT_NO_MAX
        return v if ok else None

    @field_validator("upstream", mode="before")
    @classmethod
    def _upstream_text(cls, v):
        return v.strip()[:80] if isinstance(v, str) else ""


class UnmonitoredAllowance(_Model):
    """What the admin TYPED about load that has no meter. Never a measurement; stored only for now
    (nothing uses it in any sum yet). ``unit``: amps per line, kilowatts, or "unknown" (value ignored)."""
    unit: Literal["a_per_line", "kw", "unknown"] = "unknown"
    value: float | None = Field(None, ge=0, le=100000, allow_inf_nan=False)


class PowerPointConfig(_TreeFields):
    """A distribution point with no meter of its own: a generator, grid supply or intake unit. A virtual
    node of the power tree. ``monitored`` False means "nothing here is metered" and ``allowance`` may carry
    a typed figure for it (admin-only for now). Additive: no schema bump."""
    id: str
    name: str = Field("", max_length=60)
    # For a point the amp rating IS the breaker or fuse (a point has no device limit): shown as
    # "Breaker or fuse rating (A)" in Admin. The name rating_a is kept so saved files stay as they are.
    rating_a: float | None = Field(None, gt=0, le=5000, allow_inf_nan=False)
    rating_kva: float | None = Field(None, gt=0, le=10000, allow_inf_nan=False)
    ups_type: str = "unknown"
    monitored: bool = True
    allowance: UnmonitoredAllowance | None = None

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id needed")
        return slugify(v)[:40]

    @field_validator("ups_type", mode="before")
    @classmethod
    def _known_ups(cls, v):
        return v if v in power_tree.UPS_TYPES else "unknown"


class ShellyChannelConfig(_TreeFields):
    """What the admin typed about one circuit of a meter: a label ("Amp rack 3"), the mains line it is on
    (single-phase meters) and its own rating in amps. Advisory only. ``key`` is the channel the card
    groups readings by (for example "pm1_0" or "em0")."""
    key: str = Field(max_length=24, pattern=r"^[a-z0-9_]+$")
    label: str = Field("", max_length=40)
    phase: Literal["", "L1", "L2", "L3"] = ""
    rating_a: float | None = Field(None, gt=0, le=200, allow_inf_nan=False)    # the DEVICE LIMIT: what this circuit of the meter can carry
    # The breaker or fuse that protects the circuit, if the admin knows it. Additive, none = unknown:
    # no schema bump; an older build ignores it. The lower of this and rating_a is the effective limit.
    breaker_a: float | None = Field(None, gt=0, le=1000, allow_inf_nan=False)
    # "Include in site total": the Power card adds the watts of every ticked circuit (across meters) into
    # one total. Additive with a default of off: no schema bump; an older build ignores it.
    in_total: bool = False


power_tree.channel_factory = ShellyChannelConfig


class PowerSiteConfig(_Model):
    """Site-wide settings for the Power card's calculated figures (demand and generator suggestion).
    Additive with defaults: no config schema bump; older builds ignore the key. The allowed values match
    integrations/shelly/calc.py (a test keeps them equal)."""
    demand_window_min: Literal[5, 15, 30] = 15       # rolling window for average demand
    gen_loading_pct: float = Field(80.0, ge=50, le=100, allow_inf_nan=False)    # the loading limit
    gen_power_factor: float = Field(0.8, ge=0.5, le=1.0, allow_inf_nan=False)   # the generator's assumed power factor
    gen_size_kva: float | None = Field(None, gt=0, le=5000, allow_inf_nan=False)   # a generator size typed in, if any
    # Admin switch "Show advanced power options" on the Power distribution card: way, line, breaker rating,
    # device limit and the typed allowance on every row at once. Additive, default off.
    show_advanced: bool = False


class ShellyDeviceConfig(_TreeFields):
    """A Shelly power meter the admin added (read-only: Stagewatch only asks it for its readings).
    Identity is the meter's MAC, read from the meter itself when it was added; ``host`` is only where
    to ask now (a private-network address, checked again at every connection). Additive with
    defaults: no schema bump. An older build ignores the list and drops it on its next save."""
    id: str
    host: str = Field(max_length=64)
    mac: str
    name: str = Field("", max_length=60)
    area: str = Field("", max_length=40)
    poll_interval_s: float = Field(SHELLY_POLL_DEFAULT_S, ge=SHELLY_POLL_MIN_S, le=SHELLY_POLL_MAX_S)
    # The DEVICE LIMIT (amps): what the meter itself can carry. Shown on the Power card as a load bar with
    # warnings at 80 % and 100 % (against the lower of this and breaker_a). None = no bar. Advisory only.
    rating_a: float | None = Field(None, gt=0, le=200, allow_inf_nan=False)
    # The breaker or fuse that protects the meter's supply, if known. Additive, none = unknown: no schema
    # bump; an older build ignores it (and drops it on its next save).
    breaker_a: float | None = Field(None, gt=0, le=1000, allow_inf_nan=False)
    # "Combined load bar": for a meter with two or more single-phase circuits, one extra bar on the Power
    # card for the SUM of their currents against rating_a (the whole meter or its supply breaker). The
    # circuits keep their own bars. Never applied to three-phase meters. Additive, default off: no schema
    # bump; an older build ignores it.
    combined_bar: bool = False
    channels: list[ShellyChannelConfig] = Field(default_factory=list, max_length=16)
    # The circuits the meter itself has reported (written by Stagewatch from its readings, never typed): the
    # keys, and which of them are three-phase. A meter with exactly ONE circuit is one node in the power
    # tree and its settings are stored once, on that circuit (core/power_tree.py). Additive: no schema bump.
    circuit_keys: list[str] = Field(default_factory=list, max_length=16)
    three_phase_keys: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v)

    @field_validator("mac")
    @classmethod
    def _mac(cls, v: str) -> str:
        mac = normalise_mac(v)
        if mac is None:
            raise ValueError("MAC must be 12 hex characters")
        return mac

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        try:
            return str(ipaddress.ip_address((v or "").strip()))
        except ValueError:
            raise ValueError("address must be an IP address") from None

    @field_validator("poll_interval_s", mode="before")
    @classmethod
    def _interval(cls, v):
        """On load, a number outside 2 to 5 s is brought into range rather than costing the meter."""
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
            return min(SHELLY_POLL_MAX_S, max(SHELLY_POLL_MIN_S, float(v)))
        return v


RME_MAX_UNITS = 8
_RME_SERIAL_RE = re.compile(r"[^A-Za-z0-9._-]")


def _plain_text(v, limit: int) -> str:
    """Text typed by the admin or read from a unit, made plain: control and format characters removed,
    spaces tidied, cut to ``limit``. Anything that is not text becomes empty."""
    if not isinstance(v, str):
        return ""
    text = "".join(" " if ch.isspace() else ch for ch in v if ch.isspace() or unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())[:limit].strip()


class RmeDeviceConfig(_Model):
    """An RME unit (12Mic and similar) the admin added. Read-only: Stagewatch only listens to its status
    socket and never sends it anything. ``host`` is where to listen (an IP address; the private-network check
    is made again at every connection). ``serial`` is read from the unit when it was added and kept only to
    notice a different unit at the same address: admin-only, never on a dashboard. ``record_input_changes``
    is reserved for a later phase and does nothing yet. Additive with defaults: no schema bump. An older
    build ignores the list and drops it on its next save."""
    id: str = Field(max_length=40)
    host: str = Field(max_length=64)
    port: int = Field(80, ge=1, le=65535)
    name: str = Field("", max_length=40)
    area: str = Field("", max_length=40)
    serial: str = Field("", max_length=64)
    record_input_changes: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _slug(cls, v):
        if not isinstance(v, str) or not v.strip():
            raise ValueError("id needed")
        return slugify(v)[:40]

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        try:
            return str(ipaddress.ip_address((v or "").strip()))
        except ValueError:
            raise ValueError("address must be an IP address") from None

    @field_validator("port", mode="before")
    @classmethod
    def _port(cls, v):
        if isinstance(v, bool):
            raise ValueError("port must be a number")
        return v

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        return _plain_text(v, 40)

    @field_validator("area", mode="before")
    @classmethod
    def _area(cls, v):
        return _plain_text(v, 40)

    @field_validator("serial", mode="before")
    @classmethod
    def _serial(cls, v):
        return _RME_SERIAL_RE.sub("", v)[:64] if isinstance(v, str) else ""


# Sensor accuracy ("Accuracy ±"), stored in canonical units (degC, %RH, Pa) per kind. The upper
# bounds are what the admin API accepts (a datasheet figure above this is a typing slip). A value
# outside them in a saved file is ignored when averaging.
ACCURACY_MAX = {"temperature": 20.0, "humidity": 30.0, "pressure": 5000.0}
# Smallest figure accepted per kind (a datasheet never claims better; tiny typed figures would
# otherwise swamp every other sensor, or underflow). Same canonical units.
ACCURACY_MIN = {"temperature": 0.01, "humidity": 0.1, "pressure": 1.0}
ACCURACY_MAX_ANY = max(ACCURACY_MAX.values())
ACCURACY_MIN_ANY = min(ACCURACY_MIN.values())
ACCURACY_BASES = ("typical", "maximum")


class EntitySettings(_Model):
    """Legacy per-entity calibration, keyed by entity id.  Kept as the pending map: entries move to
    ``Config.calibrations`` once the hardware behind the entity is known."""
    offset: float = 0.0
    include_in_average: bool = True
    # Datasheet accuracy (canonical units) and whether it is a typical or a maximum figure.
    # None = not set. Additive: older builds ignore both keys.
    accuracy: float | None = Field(None, ge=ACCURACY_MIN_ANY, le=ACCURACY_MAX_ANY, allow_inf_nan=False)
    accuracy_basis: Literal["typical", "maximum"] = "typical"
    # Per-sensor role override: "" = follow the node's role. Additive (older builds ignore it).
    role: Literal["", "environment", "equipment"] = ""


CALIBRATION_KEY_RE = re.compile(r"(mac:[0-9a-f]{12}|dev:[a-z0-9_]+)/[a-z0-9_]+")  # fullmatch
# Loose shape a newer release's key could have; such entries are kept on load (forward compatible).
_CALIBRATION_KEY_LOOSE_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")


def valid_calibration_key(key: str) -> bool:
    """Strict check for keys this build writes (use at the API; loading is lenient)."""
    return isinstance(key, str) and CALIBRATION_KEY_RE.fullmatch(key) is not None
CALIBRATION_HISTORY_MAX = 20


class CalibrationEntry(_Model):
    offset: float = Field(allow_inf_nan=False)
    date: str  # ISO-8601, UTC
    method: Literal["manual", "reference", "import", "migrated", "moved"]
    reference: str = Field("", max_length=120)
    note: str = Field("", max_length=200)

    @field_validator("date")
    @classmethod
    def _utc(cls, v: str) -> str:
        try:
            d = datetime.fromisoformat(v)
        except (TypeError, ValueError):
            raise ValueError("date must be ISO-8601") from None
        if d.tzinfo is None or d.utcoffset() != timedelta(0):
            raise ValueError("date must be in UTC")
        return v


class Calibration(_Model):
    """Calibration of one sensor, keyed by hardware (``mac:<12hex>/<object_id>``) so it follows
    the board when it is re-adopted.  Canonical units: degC, %RH, Pa."""
    offset: float = Field(0.0, allow_inf_nan=False)
    include_in_average: bool = True
    accuracy: float | None = Field(None, ge=ACCURACY_MIN_ANY, le=ACCURACY_MAX_ANY, allow_inf_nan=False)
    accuracy_basis: Literal["typical", "maximum"] = "typical"
    role: Literal["", "environment", "equipment"] = ""   # "" = follow the node's role
    history: list[CalibrationEntry] = Field(default_factory=list)  # newest first
    chip: str = Field("", max_length=64)  # reserved
    applied_on_node: bool = False  # reserved; ignored

    @field_validator("history", mode="before")
    @classmethod
    def _cap(cls, v):
        """Newest first, at most CALIBRATION_HISTORY_MAX.  An entry this version can't read (e.g. a
        method added by a newer release) is dropped from the history with a warning; it never
        costs the calibration itself."""
        if not isinstance(v, list):
            return v
        kept = []
        for item in v[:CALIBRATION_HISTORY_MAX]:
            try:
                kept.append(CalibrationEntry.model_validate(item))
            except ValidationError:
                pass
        if len(kept) < len(v[:CALIBRATION_HISTORY_MAX]):
            log.warning("Calibration history: %d entr%s this version can't read were left out",
                        len(v[:CALIBRATION_HISTORY_MAX]) - len(kept),
                        "y" if len(v[:CALIBRATION_HISTORY_MAX]) - len(kept) == 1 else "ies")
        return kept


CLOCK_STYLES = ("digits", "ring", "segments")   # Dashboard.clock_style
_ONTIME_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


class WallClockDisplay(_Model):
    """Cosmetic options for the Wall Clock card, the same on every dashboard.  The look itself
    (``digits``, ``ring``, ``segments``) is per dashboard: ``Dashboard.clock_style``."""
    hour12: bool = False          # 12-hour clock with am/pm; default 24-hour
    show_date: bool = False       # the date under the time (Stagewatch's site date)
    ring: Literal["sweep", "fill"] = "sweep"   # ring style: one moving LED, or LEDs filling up to :59


class WallClockConfig(_Model):
    # One time source for the whole installation.  "pc" (this computer's clock) is the default for
    # new installs; a saved config keeps whatever it stored, so an installation already using
    # Ontime stays on Ontime.  No automatic fallback from one source to the other.
    source: Literal["pc", "ontime"] = "pc"
    display: WallClockDisplay = Field(default_factory=WallClockDisplay)
    ontime_url: str = Field("http://127.0.0.1:4001", max_length=300)
    warn_offset_s: float = Field(2.0, ge=1.0, le=60)   # 1.0 absorbs the source's 1 s granularity

    @field_validator("ontime_url")
    @classmethod
    def _url(cls, v: str) -> str:
        """Only ``http://host[:port]``: no https, user info, path, query or fragment.  Stored as
        rebuilt from the parsed parts (lower-case scheme and host, port without leading zeros)."""
        bad = "address must look like http://host:port"
        v = (v or "").strip()
        if not v or any(not 0x21 <= ord(ch) <= 0x7E for ch in v):
            raise ValueError(bad)  # urlsplit silently drops tabs/newlines: refuse them up front
        try:
            u = urlsplit(v)
            port = u.port
        except ValueError:
            raise ValueError(bad) from None
        host = u.hostname or ""
        if (u.scheme.lower() != "http" or "@" in u.netloc or u.path not in ("", "/") or u.query
                or u.fragment or "#" in v or "?" in v or not host or port == 0):
            raise ValueError(bad)
        if ":" in host:  # IPv6 literal
            try:
                host = f"[{ipaddress.IPv6Address(host).compressed}]"
            except ValueError:
                raise ValueError(bad) from None
        elif not _ONTIME_HOST_RE.fullmatch(host):
            raise ValueError(bad)
        return f"http://{host}" + (f":{port}" if port is not None else "")


class OntimeTimerConfig(_Model):
    """The Ontime Timer card.  It reads Ontime at ``WallClockConfig.ontime_url`` (one address for
    both cards).  The event title is rundown text (often an artist), so the admin can hide it
    from every dashboard; the default is to show it."""
    show_title: bool = True


class GlobconConfig(_Model):
    """Where the DirectOut GLOBCON remote-controller interface is (read-only: levels and names only).
    Additive with defaults, so no schema bump; an older build ignores the section and forgets it on its
    next save. Used only while a dashboard has the GLOBCON levels card. ``password`` is GLOBCON's
    controller password, if it has one: a secret, kept like the Smaart one (admin write-only, shown only
    as set / not set, never logged)."""
    host: str = Field("127.0.0.1", max_length=253)
    port: int | None = Field(9091, ge=1, le=65535)
    password: str = Field("", max_length=128, repr=False)

    @model_serializer(mode="wrap")
    def _compact(self, handler):
        data = handler(self)
        if not data.get("password"):
            data.pop("password", None)
        return data


class WitmotionSensorConfig(_Model):
    """One WitMotion sensor. ``address`` is its Bluetooth address: it finds the sensor even after crew
    rename it. ``label`` is the name shown on dashboards ("Stage Right hang"); it is a Stagewatch setting
    only and is never written to the sensor."""
    address: str = Field(max_length=64)
    label: str = Field("", max_length=60)
    axis: Literal["both", "x", "y"] = "both"        # which tilt to watch: X (roll), Y (pitch) or both
    smoothing_s: float = Field(2.0, ge=0, le=10)    # noise removal time constant, seconds (0 = off)
    target_x: Optional[float] = Field(None, ge=-180, le=180)   # optional target angle (degrees) for X (roll)
    target_y: Optional[float] = Field(None, ge=-180, le=180)   # ... and for Y (pitch)
    on_target_deg: float = Field(0.2, ge=0, le=10)             # "on target" within this many degrees
    close_deg: float = Field(1.0, ge=0, le=45)                 # "close" within this many (display only, never an alarm)
    accuracy_deg: Optional[float] = Field(None, ge=0.01, le=10)   # datasheet tilt accuracy, shown as "± x°" (display only)

    @model_validator(mode="after")
    def _bands_in_order(self):
        if self.close_deg < self.on_target_deg:
            self.close_deg = self.on_target_deg   # "close" can never be tighter than "on target"
        return self


class WitmotionConfig(_Model):
    """WitMotion WT901BLE tilt sensors read over Bluetooth (read-only, advisory). Additive with
    defaults, so no schema bump; an older build ignores the section and forgets it on its next save.
    ``sensors`` lists each one by address. With none listed, ``address`` (or, if empty too, the first
    sensor whose factory name starts WT901BLE) is used for a single sensor."""
    enabled: bool = False
    temp_on_equipment: bool = False   # also show each sensor's body temperature on the Equipment card
    markers: bool = True   # "link lost" / "back" markers; off for a fragile link. The gap is recorded either way
    address: str = Field("", max_length=64)
    axis: Literal["both", "x", "y"] = "both"        # for the single sensor used when ``sensors`` is empty
    smoothing_s: float = Field(2.0, ge=0, le=10)
    target_x: Optional[float] = Field(None, ge=-180, le=180)   # optional target angle (degrees) for X (roll)
    target_y: Optional[float] = Field(None, ge=-180, le=180)   # ... and for Y (pitch)
    on_target_deg: float = Field(0.2, ge=0, le=10)             # "on target" within this many degrees
    close_deg: float = Field(1.0, ge=0, le=45)                 # "close" within this many (display only, never an alarm)

    @model_validator(mode="after")
    def _bands_in_order(self):
        if self.close_deg < self.on_target_deg:
            self.close_deg = self.on_target_deg   # "close" can never be tighter than "on target"
        return self
    sensors: list[WitmotionSensorConfig] = Field(default_factory=list, max_length=4)


class Threshold(_Model):
    id: str
    entity: str
    label: str = ""
    above: float | None = None
    below: float | None = None
    level: int = Field(2, ge=1, le=3)
    hysteresis: float = Field(0.0, ge=0)
    hold_s: float = Field(0.0, ge=0, le=3600)
    enabled: bool = True
    # OSC actions on raised / cleared / acknowledged (core/actions.py validates the messages).
    # Additive with defaults: no config schema bump; an older build ignores and drops it on save.
    actions: "ThresholdActions" = Field(default_factory=lambda: ThresholdActions())


# The channel groups a GLOBCON levels card can show: strip numbers as on the controller (1-based fader
# positions on its current layer) -> (first, last). "range" unset keeps the older "first N strips with a level".
GLOBCON_RANGES: dict[str, tuple[int, int]] = {
    "1-4": (1, 4), "5-8": (5, 8), "1-8": (1, 8), "9-12": (9, 12), "13-16": (13, 16), "9-16": (9, 16), "1-16": (1, 16),
}


class GlobconCardOptions(_Model):
    """Which GLOBCON controller (1 to 16) and which channels this dashboard's GLOBCON levels card shows.
    ``range`` is one of GLOBCON_RANGES; unset (None) keeps the older behaviour: the first ``strips`` (4 or 8)
    strips that GLOBCON says have a level. Loading is lenient (anything unusable falls back to the default,
    with a warning that never carries the value); the API is strict."""
    controller: int = Field(1, ge=1, le=16)
    strips: Literal[4, 8] = 8
    range: Optional[Literal["1-4", "5-8", "1-8", "9-12", "13-16", "9-16", "1-16"]] = None

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data):
        if not isinstance(data, dict):
            return {}
        out = {}
        c, s, r = data.get("controller", 1), data.get("strips", 8), data.get("range")
        out["controller"] = c if isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 16 else 1
        out["strips"] = s if isinstance(s, int) and not isinstance(s, bool) and s in (4, 8) else 8
        out["range"] = r if isinstance(r, str) and r in GLOBCON_RANGES else None
        if out["controller"] != c or out["strips"] != s or out["range"] != r:
            log.warning("A dashboard's GLOBCON card options were not usable and were reset to the defaults")
        return out


class Dashboard(_Model):
    slug: str
    title: str = ""
    layout: Literal["tablet", "phone", "wall"] = "tablet"
    allow_marker: bool = True
    allow_ack: bool = False
    # Ordered card ids (core/cards.py).  Not given -> the layout's default (new dashboards).
    cards: list[str] = Field(default_factory=list)
    stage: str = Field("", max_length=40)  # which stage this screen follows ("" = all)
    # The Wall Clock card's look on this screen.  Loading is lenient (unknown -> "digits"); the API is strict.
    clock_style: str = "digits"
    # Optional card id -> "full" | "half" (core/cards.py HALF_CAPABLE).  Empty = every card full width.
    card_sizes: dict[str, str] = Field(default_factory=dict)
    # Whether the site tiles include Dew point on this screen (on unless the admin unticks it).
    show_dew_point: bool = True
    # Which tiles the Site readings card shows, in order (core/cards.py SITE_TILES).  Empty = the default set.
    tiles: list[str] = Field(default_factory=list)
    # The GLOBCON levels card's controller and channel group on this screen.
    globcon: GlobconCardOptions = Field(default_factory=GlobconCardOptions)
    # Onsite contacts card: phone numbers on this screen (needs ContactsSettings.phones_enabled and the
    # contact's own switch too), and which area's contacts it lists ("" = all).
    show_contact_phones: bool = False
    contacts_area: str = Field("", max_length=40)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v)

    @field_validator("cards", mode="before")
    @classmethod
    def _cards(cls, v):
        """Keep every well-formed id (also ids from a newer release), in order, once each."""
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for c in v:
            if isinstance(c, str) and CARD_ID_RE.fullmatch(c) and c not in out:
                out.append(c)
        if len(out) > MAX_CARDS:  # a newer release may allow more: keep the first ones, don't fail
            log.warning("A dashboard lists %d cards; only the first %d are kept", len(out), MAX_CARDS)
            out = out[:MAX_CARDS]
        return out

    @field_validator("stage", mode="before")
    @classmethod
    def _stage(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("contacts_area", mode="before")
    @classmethod
    def _contacts_area(cls, v):
        return " ".join(v.split())[:40] if isinstance(v, str) else ""

    @field_validator("tiles", mode="before")
    @classmethod
    def _tiles(cls, v):
        return clean_tiles(v)

    @field_validator("clock_style", mode="before")
    @classmethod
    def _clock_style(cls, v):
        return v if isinstance(v, str) and v in CLOCK_STYLES else "digits"

    @field_validator("card_sizes", mode="before")
    @classmethod
    def _card_sizes(cls, v):
        out, dropped = clean_card_sizes(v)
        if dropped:
            log.warning("A dashboard's card sizes: %d entr%s this version can't use were left out", dropped, "y" if dropped == 1 else "ies")
        return out

    @model_validator(mode="after")
    def _default_cards(self):
        if "cards" not in self.model_fields_set:
            self.__dict__["cards"] = default_cards(self.layout)  # a default: leave model_fields_set alone
        return self


class OscDestination(_Model):
    host: str
    port: int = Field(9000, ge=1, le=65535)
    enabled: bool = True
    alarm_messages: bool = False  # also send /stagewatch/alarm/event on each threshold alarm change


class ActionArg(_Model):
    t: Literal["i", "f", "s"] = "s"
    v: int | float | str = ""


class ActionMessage(_Model):
    """One OSC message to an action target (checked strictly by core/actions.py on the API)."""
    target_id: str = ""
    address: str = ""
    args: list[ActionArg] = Field(default_factory=list)


class ThresholdActions(_Model):
    raised: ActionMessage | None = None
    cleared: ActionMessage | None = None
    acked: ActionMessage | None = None
    cooldown_s: int = Field(60, ge=0, le=3600)


class ActionTarget(_Model):
    id: str
    name: str = ""
    host: str
    port: int = Field(53000, ge=1, le=65535)
    enabled: bool = True


class ActionsConfig(_Model):
    """Outbound OSC action targets (QLab, Companion ...). Additive: no config schema bump. The armed
    switch is never saved: it is off after every start."""
    targets: list[ActionTarget] = Field(default_factory=list)


class OscOutConfig(_Model):
    destinations: list[OscDestination] = Field(default_factory=list)
    per_node: bool = False
    rate_hz: float = Field(1.0, gt=0, le=20)
    # Also send the FOH advice numbers (drift, suggested delay, air-loss change). Values only, never
    # commands. Additive with a default: no config schema bump.
    include_advice: bool = False


class UpdaterConfig(_Model):
    """In-app updater settings.  ``channel`` is only the admin's *request*; the launcher
    re-validates every target itself (see core/updater.py).  None = use the installer's marker."""
    channel: Literal["stable", "nightly"] | None = None
    # Header internet icon: one small check every 5 minutes. None = on only for a managed install
    # (where updates are used). Additive with a default: no config schema bump.
    check_internet: bool | None = None


MAX_IGNORED = 200


class AlarmsConfig(_Model):
    """How long advisory notices stay on dashboards. Additive with defaults: no config schema
    bump. An older build ignores the section and forgets it on its next save. Only the
    dashboard list is affected; the underlying state (a node still MISSING) is untouched, and
    alert and stop alarms never time out. 0 means never."""
    hide_acked_min: int = Field(2, ge=0, le=1440)     # hide an acknowledged advisory this long after the ack
    fold_old_min: int = Field(30, ge=0, le=10080)     # move an unacknowledged, unchanged advisory into "older notices"


class BarometerConfig(_Model):
    """Barometer card settings. Additive with defaults: no config schema bump. An older build
    ignores the section and forgets it on its next save."""
    hemisphere: Literal["north", "south"] = "north"   # which months count as summer for the outlook
    # Optional silent notice (never audible) plus one marker when pressure is falling quickly.
    rapid_fall_alarm: bool = False
    rapid_fall_hpa_3h: float = Field(3.6, ge=1.5, le=10)   # Met Office "quickly": 3.6 hPa in 3 h


def numeric_host_name(host: str) -> bool:
    """True for a "host name" that an address parser could read as a number: its last label is all
    digits or starts with 0x (134744072, 0x8.0x8.0x8.0x8, 8.8.2056, 010.010.010.010). Such names
    can mean a public IPv4 address, so they are refused."""
    last = host.rstrip(".").rsplit(".", 1)[-1].lower()
    return last.isdigit() or last.startswith("0x")


def spl_host_error(v) -> str | None:
    """Fixed-text problem with a Smaart address, or None. '' is fine (not set)."""
    bad = "address must be a host name or IP address"
    if not isinstance(v, str):
        return "address must be text"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in v):
        return bad
    v = v.strip()
    if v == "":
        return None
    if any(not 0x21 <= ord(ch) <= 0x7E for ch in v):
        return bad
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        return bad if (not _ONTIME_HOST_RE.fullmatch(v) or numeric_host_name(v)) else None
    if ip.is_global or ip.is_multicast or ip.is_unspecified:
        return "address must be on the local network"
    if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
        return "address must be on the local network"   # carries another address inside: not obvious where it goes
    return None


class SplSlot(_Model):
    """One of the (up to three) recorded values: an input source and a metric, both Smaart's own
    text, chosen from Smaart's lists. ``source`` "" means "the first input Smaart lists"."""
    metric: str = Field(max_length=spl.METRIC_NAME_MAX)
    source: str = Field("", max_length=spl.INPUT_NAME_MAX)

    @field_validator("metric", "source", mode="before")
    @classmethod
    def _text(cls, v, info):
        clean = spl.clean_metric_name(v) if info.field_name == "metric" else spl.clean_input_name(v)
        if info.field_name == "metric" and not clean:
            raise ValueError("a metric name is needed")
        return clean

    @property
    def pair(self) -> tuple[str, str]:
        return (self.source, self.metric)


PASSWORD_MAX = 128


def spl_password_error(v) -> str | None:
    """Fixed-text problem with Smaart's API password, or None. Any printable text is fine."""
    if not isinstance(v, str) or not 1 <= len(v) <= PASSWORD_MAX:
        return f"password must be 1 to {PASSWORD_MAX} characters"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in v):
        return "password must not contain control characters"
    return None


class SplLimitsAlert(_Model):
    """Sound level limits (0.4.0): the optional notices. Additive, no schema bump; the limits
    themselves live in the database, so a downgrade loses only these three options."""
    change_warning_min: int = Field(10, ge=0, le=60)   # silent warning before a limit tightens; 0 = off
    exceed_marker: bool = True
    exceed_alarm: bool = True


class SplConfig(_Model):
    """Sound level from Smaart (read-only). Additive with defaults (off, the usual three values):
    no config schema bump. An older build ignores the new keys and forgets them on its next save.
    The address is admin only. ``password`` is Smaart's API password: a secret, kept like the ESPHome
    noise key (admin write-only, shown only as set / not set, never logged, redacted from the
    support bundle); it is used for logging in to Smaart's API and nothing else."""
    enabled: bool = False
    # Defaults: Smaart on this computer (127.0.0.1), port 26000 (the SPL web page's port in Smaart's
    # own examples; to be confirmed on a real Smaart). Both can be changed in Admin.
    host: str = Field("127.0.0.1", max_length=253)
    port: int | None = Field(26000, ge=1, le=65535)
    password: str = Field("", max_length=PASSWORD_MAX, repr=False)
    # The recorded values. ``meters`` (input source + metric, Smaart's own text) is the current form;
    # ``slots`` is how older builds stored them (Stagewatch metric keys, core/spl.py) and is still
    # read when there are no ``meters``, and still written for the older form of a first-input slot.
    meters: list[SplSlot] | None = None
    slots: list[str] = Field(default_factory=lambda: list(spl.DEFAULT_SLOTS))
    # The owner's label for where the meter is ("FOH"). A label only: never part of an id or key.
    location: str = Field("", max_length=spl.LOCATION_MAX)
    # Per-input locations: Smaart's exact input text -> label. Labels only, at most LOCATIONS_MAX.
    locations: dict[str, str] = Field(default_factory=dict)
    # The timeline's vertical range: "auto" fits the data; "custom" is exactly min..max dB.
    chart_range: Literal["auto", "custom"] = "auto"
    chart_min_db: float = spl.CHART_MIN_DEFAULT
    chart_max_db: float = spl.CHART_MAX_DEFAULT
    limits_alert: SplLimitsAlert = Field(default_factory=SplLimitsAlert)

    @field_validator("limits_alert", mode="before")
    @classmethod
    def _limits_alert(cls, v):
        """On load: damaged options fall back to the defaults (a notice, not a reason to lose the section)."""
        if v is None:
            return SplLimitsAlert()
        try:
            return SplLimitsAlert.model_validate(v)
        except Exception:  # noqa: BLE001
            log.warning("Sound level: the saved limit notice options were not usable and were reset")
            return SplLimitsAlert()

    @model_validator(mode="before")
    @classmethod
    def _chart_range_on_load(cls, data):
        """On load: a damaged range (not a mode, not numbers, out of 0..200, min not below max, span
        under 10 dB) falls back to automatic with the default numbers. Logs once, never the values."""
        if not isinstance(data, dict) or not any(k in data for k in ("chart_range", "chart_min_db", "chart_max_db")):
            return data
        mode = data.get("chart_range", "auto")
        lo, hi = data.get("chart_min_db", spl.CHART_MIN_DEFAULT), data.get("chart_max_db", spl.CHART_MAX_DEFAULT)
        if mode in ("auto", "custom") and spl.chart_range_error(lo, hi) is None:
            return data
        log.warning("Sound level: the saved graph range was not usable and was reset to automatic")
        return {**data, "chart_range": "auto", "chart_min_db": spl.CHART_MIN_DEFAULT, "chart_max_db": spl.CHART_MAX_DEFAULT}

    @model_serializer(mode="wrap")
    def _compact(self, handler):
        """Leave an unset password, locations and unset meters out of the saved file, so a file that
        never used them stays exactly as it was (and no empty ``password:`` line appears)."""
        data = handler(self)
        if not data.get("password"):
            data.pop("password", None)
        for k in ("location", "locations"):
            if not data.get(k):
                data.pop(k, None)
        if (data.get("chart_range") == "auto" and data.get("chart_min_db") == spl.CHART_MIN_DEFAULT
                and data.get("chart_max_db") == spl.CHART_MAX_DEFAULT):
            for k in ("chart_range", "chart_min_db", "chart_max_db"):
                data.pop(k, None)   # defaults stay out of the file, so an unchanged file stays unchanged
        if data.get("meters") is None:
            data.pop("meters", None)
        if data.get("limits_alert") == SplLimitsAlert().model_dump():
            data.pop("limits_alert", None)   # defaults stay out of the file
        return data

    def effective_slots(self) -> list[tuple[str, str]]:
        """The recorded values as (source, metric) pairs, in slot order, at most three."""
        if self.meters is not None:
            return [m.pair for m in self.meters][:spl.MAX_SLOTS]
        return spl.slots_from_legacy(self.slots)[:spl.MAX_SLOTS]

    @field_validator("meters", mode="before")
    @classmethod
    def _meters(cls, v):
        """On load: usable entries only, each pair once, at most three. Logs a count, never values."""
        if v is None:
            return None
        if not isinstance(v, (list, tuple)):
            log.warning("Sound level: the saved values were not usable and were reset")
            return None
        kept, seen = [], set()
        for item in v:
            try:
                slot = SplSlot.model_validate(item)
            except ValidationError:
                continue
            if slot.pair not in seen:
                seen.add(slot.pair)
                kept.append(slot)
        if len(kept) < len(v) or len(kept) > spl.MAX_SLOTS:
            log.warning("Sound level: %d value%s this version can't use left out", len(v) - min(len(kept), spl.MAX_SLOTS),
                        "" if len(v) - min(len(kept), spl.MAX_SLOTS) == 1 else "s")
        return kept[:spl.MAX_SLOTS]

    @field_validator("location", mode="before")
    @classmethod
    def _location(cls, v):
        """On load: cleaned and capped, never refused (a damaged label must not lose the section)."""
        return spl.clean_location(v)

    @field_validator("locations", mode="before")
    @classmethod
    def _locations(cls, v):
        """On load: usable entries only (cleaned input name -> cleaned label, both non-empty), at most
        LOCATIONS_MAX; the rest is dropped. Logs a count, never the text."""
        if v is None:
            return {}
        if not isinstance(v, dict):
            log.warning("Sound level: the saved locations were not usable and were reset")
            return {}
        out: dict[str, str] = {}
        for k, val in v.items():
            key, label = spl.clean_input_name(k), spl.clean_location(val)
            if key and label and key not in out:
                out[key] = label
        if len(out) > spl.LOCATIONS_MAX or len(out) < len(v):
            dropped = len(v) - min(len(out), spl.LOCATIONS_MAX)
            log.warning("Sound level: %d location%s this version can't use left out", dropped, "" if dropped == 1 else "s")
        return dict(list(out.items())[:spl.LOCATIONS_MAX])

    def location_for(self, input_name: str) -> str:
        """The label for a value read from this input (Smaart's resolved name): that input's own
        location, else the default, else "". An input not yet known ("") gets the default."""
        return (self.locations.get(input_name, "") if input_name else "") or self.location

    @field_validator("password", mode="before")
    @classmethod
    def _password(cls, v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)   # YAML reads `password: 1234` as a number
        if v is None or v == "":
            return ""
        err = spl_password_error(v)
        if err:
            raise ValueError(err)
        return v

    @field_validator("host", mode="before")
    @classmethod
    def _host(cls, v):
        """'' or a host name / IP address of the Smaart computer on the local network: no scheme,
        port, path or user info, and no public IP address (a Smaart machine is on the LAN)."""
        if not isinstance(v, str):
            raise ValueError("address must be text")
        err = spl_host_error(v)
        if err:
            raise ValueError(err)
        v = v.strip()
        try:
            return str(ipaddress.ip_address(v))
        except ValueError:
            return v.lower()

    @field_validator("slots", mode="before")
    @classmethod
    def _slots(cls, v):
        """On load: known values only, each once, at most three (the API is strict and refuses).
        Logs a count, never the values."""
        if not isinstance(v, (list, tuple)):
            return list(spl.DEFAULT_SLOTS)
        kept = spl.clean_slots(v)
        if len(v) > len(kept):
            log.warning("Sound level: %d value%s this version can't use left out", len(v) - len(kept),
                        "" if len(v) - len(kept) == 1 else "s")
        return kept


ADVICE_ID_RE = r"^[a-z0-9_]{1,32}$"
ADVICE_MAX_PATHS = 12
ADVICE_MAX_POSITIONS = 4


def _advice_name(v):
    """Control, format (zero-width, bidi overrides such as U+202E) and line-separator characters are
    dropped, then the name is trimmed before the length rule, so "  " is refused."""
    if not isinstance(v, str):
        return v
    return "".join(c for c in v if unicodedata.category(c) not in ("Zl", "Zp")
                   and not unicodedata.category(c).startswith("C")).strip()


def _advice_id_from_name(data):
    """No id given: the slug of the name (the id is the OSC address and alarm id, so it stays put)."""
    if isinstance(data, dict) and "id" not in data and isinstance(data.get("name"), str):
        data = {**data, "id": slugify(data["name"])[:32]}
    return data


class AdvicePath(_Model):
    """One alignment-drift path (a delay tower, a fill, sub to tops): how far sound now arrives
    from the main PA compared with the reference marker. Advice only; nothing is ever sent to a
    processor. ``source_area`` is the Environment area whose sensors give the air ("" = the site
    average)."""
    id: str = Field(pattern=ADVICE_ID_RE)
    name: str = Field(min_length=1, max_length=40)
    distance_m: float = Field(ge=0.5, le=1000)
    source_area: str = Field("", max_length=40)
    current_delay_ms: Optional[float] = Field(None, ge=0, le=2000)
    drift_threshold_ms: Optional[float] = Field(1.0, ge=0.2, le=10)   # None = no alarm
    phase_hint_hz: Optional[float] = Field(None, ge=20, le=20000)     # display only
    enabled: bool = True

    @field_validator("name", "source_area", mode="before")
    @classmethod
    def _trim(cls, v):
        return _advice_name(v)

    @model_validator(mode="before")
    @classmethod
    def _default_id(cls, data):
        return _advice_id_from_name(data)


class AdvicePosition(_Model):
    """A named listening position for the high-frequency air loss estimate ("FOH", "Rear"): its
    distance from the main PA, typed in. Each is compared with a marker, never with the other."""
    id: str = Field(pattern=ADVICE_ID_RE)
    name: str = Field(min_length=1, max_length=40)
    distance_m: float = Field(ge=0.5, le=1000)
    enabled: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _trim(cls, v):
        return _advice_name(v)

    @model_validator(mode="before")
    @classmethod
    def _default_id(cls, data):
        return _advice_id_from_name(data)


class AdviceConfig(_Model):
    """FOH advice: alignment drift per path and high-frequency air loss per position (read-only,
    advisory). Additive with defaults, so no schema bump; an older build ignores the section and
    forgets it on its next save. Air loss always uses the site average air, above 4 kHz only."""
    paths: list[AdvicePath] = Field(default_factory=list, max_length=ADVICE_MAX_PATHS)
    positions: list[AdvicePosition] = Field(default_factory=list, max_length=ADVICE_MAX_POSITIONS)
    drift_reference: str = Field("Aligned", min_length=1, max_length=120)
    air_loss_reference: str = Field("Soundcheck", min_length=1, max_length=120)
    air_loss_resolution: Literal["octave", "third"] = "octave"
    alarm: Literal["off", "silent", "beep"] = "silent"   # level 1 always; never 2 or 3
    hold_s: float = Field(120, ge=0, le=900)
    marker_on_alert: bool = True

    @field_validator("drift_reference", "air_loss_reference", mode="before")
    @classmethod
    def _trim(cls, v):
        return _advice_name(v)

    @field_validator("paths", "positions")
    @classmethod
    def _unique_ids(cls, v: list) -> list:
        if len({x.id for x in v}) != len(v):
            raise ValueError("ids must be unique")
        return v


class ContactsSettings(_Model):
    """Onsite contacts (the contacts themselves live in contacts/<event_id>.yaml, never here).
    ``phones_enabled`` is the installation-wide master switch for phone numbers on dashboards;
    ``reveal_pin_hash`` (optional) makes dashboards ask for a PIN before showing them."""
    phones_enabled: bool = False
    reveal_pin_hash: str = ""


QUIET_MAX = 200
QUIET_KEY_MAX = 100
_QUIET_PREFIXES = ("lanwm:", "nebra:")


def valid_quiet_key(v: object) -> str | None:
    """A unit key as the Device errors card groups imported units ("lanwm:104", "nebra:Joyned MU16 3"),
    case-folded; None if it is not one. Only imported units can be quieted, never a Stagewatch device."""
    if not isinstance(v, str):
        return None
    k = " ".join(v.split()).casefold()
    if not k.startswith(_QUIET_PREFIXES) or len(k) > QUIET_KEY_MAX or len(k.split(":", 1)[1]) < 1:
        return None
    if any(ord(c) < 32 or c in "<>\\" for c in k):
        return None
    return k


class DeviceErrorsConfig(_Model):
    """Units the admin quieted on the Device errors card (imported LA / Nebra units). Additive field."""
    quiet: list[str] = Field(default_factory=list)

    @field_validator("quiet", mode="before")
    @classmethod
    def _clean_quiet(cls, v):
        """Unusable and repeated entries are dropped and the list cut to QUIET_MAX (salvage-friendly)."""
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for item in v:
            k = valid_quiet_key(item)
            if k and k not in out and len(out) < QUIET_MAX:
                out.append(k)
        return out


WIFI_MAX_NETWORKS = 6
WIFI_SECURITY = ("WPA", "nopass")


class WifiNetwork(_Model):
    """One guest or crew internet network shown on the Wi-Fi card. The password is plain text on purpose (the card
    shows it and draws a join code), so only the crew internet belongs here, never the show network. It stays out of
    diagnostics by its key name, and out of every dashboard that does not have the card. Additive: no schema bump."""
    id: str = Field(max_length=40)
    label: str = Field("", max_length=40)
    ssid: str = Field(max_length=32)
    password: str = Field("", max_length=64)
    security: str = "WPA"
    hidden: bool = False             # the network does not broadcast its name
    hide_password: bool = False      # show the join code only, not the password
    note: str = Field("", max_length=60)

    @field_validator("id", mode="before")
    @classmethod
    def _slug(cls, v):
        if not isinstance(v, str) or not v.strip():
            raise ValueError("id needed")
        return slugify(v)[:40]

    @field_validator("label", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return _plain_text(v, 60)

    @field_validator("ssid", "password", mode="before")
    @classmethod
    def _exact(cls, v):
        """Spaces inside a name or password count, so only control characters are refused."""
        if not isinstance(v, str) or any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("Use plain text")
        return v

    @field_validator("ssid")
    @classmethod
    def _ssid_needed(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("A network name is needed")
        return v

    @field_validator("security", mode="before")
    @classmethod
    def _security(cls, v):
        return v if v in WIFI_SECURITY else "WPA"


class WifiConfig(_Model):
    """The Wi-Fi card's networks (Admin, Wi-Fi). Additive field with defaults: an older build ignores it."""
    networks: list[WifiNetwork] = Field(default_factory=list)

    @field_validator("networks", mode="before")
    @classmethod
    def _lenient(cls, v):
        """On load: an entry that cannot be read, or repeats an id, is left out and the list cut to the limit.
        Logs a count only, never a value."""
        if not isinstance(v, list):
            return []
        kept, seen, dropped = [], set(), 0
        for item in v:
            try:
                net = item if isinstance(item, WifiNetwork) else WifiNetwork.model_validate(item)
            except Exception:  # noqa: BLE001 - any unreadable entry is simply left out
                dropped += 1
                continue
            if net.id in seen or len(kept) >= WIFI_MAX_NETWORKS:
                dropped += 1
                continue
            seen.add(net.id)
            kept.append(net)
        if dropped:
            log.warning("Wi-Fi networks: %d unreadable or surplus entr%s left out", dropped, "y" if dropped == 1 else "ies")
        return kept


class Config(_Model):
    schema_version: int = CONFIG_SCHEMA_VERSION
    site: SiteConfig = Field(default_factory=SiteConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    mdns_name: str = "stagewatch"
    esphome_devices: list[EsphomeDeviceConfig] = Field(default_factory=list)
    # Discovered ESPHome nodes the admin chose to hide (other people's gear on a shared network).
    # Keys come from esphome.discovery_key(): the MAC when the node advertises one, else its name.
    esphome_ignored: list[str] = Field(default_factory=list)  # capped at MAX_IGNORED when adding
    entities: dict[str, EntitySettings] = Field(default_factory=dict)  # legacy/pending calibrations
    calibrations: dict[str, Calibration] = Field(default_factory=dict)
    thresholds: list[Threshold] = Field(default_factory=list)
    dashboards: list[Dashboard] = Field(default_factory=lambda: [
        Dashboard(slug="foh", title="FOH", layout="tablet", allow_marker=True, allow_ack=True),
        Dashboard(slug="phone", title="Phone", layout="phone", allow_marker=True),
        Dashboard(slug="wall", title="Wall", layout="wall", allow_marker=False),
    ])
    osc_out: OscOutConfig = Field(default_factory=OscOutConfig)
    actions: ActionsConfig = Field(default_factory=ActionsConfig)
    updater: UpdaterConfig = Field(default_factory=UpdaterConfig)
    wall_clock: WallClockConfig = Field(default_factory=WallClockConfig)
    ontime_timer: OntimeTimerConfig = Field(default_factory=OntimeTimerConfig)
    barometer: BarometerConfig = Field(default_factory=BarometerConfig)
    alarms: AlarmsConfig = Field(default_factory=AlarmsConfig)
    spl: SplConfig = Field(default_factory=SplConfig)
    globcon: GlobconConfig = Field(default_factory=GlobconConfig)
    witmotion: WitmotionConfig = Field(default_factory=WitmotionConfig)
    # Shelly power meters (read-only). Salvage keeps each valid entry and drops only the bad ones.
    shelly_devices: list[ShellyDeviceConfig] = Field(default_factory=list)
    # RME units (read-only status listening). Additive, no schema bump; salvage keeps each valid entry.
    rme_devices: list[RmeDeviceConfig] = Field(default_factory=list)
    power_site: PowerSiteConfig = Field(default_factory=PowerSiteConfig)
    # Power distribution tree: points with no meter (generator, grid supply, intake units). Meters and circuits
    # carry their own role, number and upstream. Additive: no schema bump; salvage keeps each valid point.
    power_points: list[PowerPointConfig] = Field(default_factory=list)
    contacts: ContactsSettings = Field(default_factory=ContactsSettings)
    advice: AdviceConfig = Field(default_factory=AdviceConfig)
    # Additive (no schema bump): imported units the admin quieted on the Device errors card.
    device_errors: DeviceErrorsConfig = Field(default_factory=DeviceErrorsConfig)
    # Wi-Fi card: guest and crew internet networks (additive, no schema bump).
    wifi: WifiConfig = Field(default_factory=WifiConfig)

    @field_validator("advice", mode="before")
    @classmethod
    def _lenient_advice(cls, v):
        """On load (the API is strict): a path or position that is not usable, or repeats an id,
        is dropped and the list cut to its limit, so one bad entry never costs the rest. Logs a
        count only."""
        if not isinstance(v, dict):
            return v
        out, dropped = dict(v), 0
        for key, model, limit in (("paths", AdvicePath, ADVICE_MAX_PATHS),
                                  ("positions", AdvicePosition, ADVICE_MAX_POSITIONS)):
            items = v.get(key)
            if not isinstance(items, list):
                continue
            kept, seen = [], set()
            for item in items:
                try:
                    got = model.model_validate(item)
                except Exception:  # noqa: BLE001 - any unreadable entry is simply left out
                    dropped += 1
                    continue
                if got.id in seen or len(kept) >= limit:
                    dropped += 1
                    continue
                seen.add(got.id)
                kept.append(got)
            out[key] = kept
        if dropped:
            log.warning("FOH advice: %d unusable or surplus entr%s left out", dropped,
                        "y" if dropped == 1 else "ies")
        return out

    @field_validator("shelly_devices")
    @classmethod
    def _shelly_unique_and_capped(cls, v: list) -> list:
        """Later duplicates (same id or same MAC) are dropped and the list is cut to SHELLY_MAX_METERS, so a
        hand-edited or salvaged file can never stop the hub starting. Logs a count only."""
        seen_id, seen_mac, kept = set(), set(), []
        for d in v:
            if d.id in seen_id or d.mac in seen_mac or len(kept) >= SHELLY_MAX_METERS:
                continue
            seen_id.add(d.id)
            seen_mac.add(d.mac)
            kept.append(d)
        if len(kept) < len(v):
            log.warning("Shelly meters: %d duplicate or surplus entr%s left out", len(v) - len(kept), "y" if len(v) - len(kept) == 1 else "ies")
        return kept

    @field_validator("rme_devices", mode="before")
    @classmethod
    def _lenient_rme(cls, v):
        """On load: an entry that cannot be read is left out (the rest stay), and a list that is not a list
        is empty. Logs a count only, never a value."""
        if not isinstance(v, list):
            if v is not None:
                log.warning("RME units: the saved list was not usable and was reset")
            return []
        kept, dropped = [], 0
        for item in v:
            try:
                kept.append(item if isinstance(item, RmeDeviceConfig) else RmeDeviceConfig.model_validate(item))
            except Exception:  # noqa: BLE001 - any unreadable entry is simply left out
                dropped += 1
        if dropped:
            log.warning("RME units: %d unreadable entr%s left out", dropped, "y" if dropped == 1 else "ies")
        return kept

    @field_validator("rme_devices")
    @classmethod
    def _rme_unique_and_capped(cls, v: list) -> list:
        """Later duplicates (same id or same address) are dropped and the list cut to RME_MAX_UNITS,
        so a hand-edited or salvaged file can never stop the hub starting. Logs a count only."""
        seen_id, seen_host, kept = set(), set(), []   # same address twice would be two sockets to one unit
        for d in v:
            if d.id in seen_id or d.host in seen_host or len(kept) >= RME_MAX_UNITS:
                continue
            seen_id.add(d.id)
            seen_host.add(d.host)
            kept.append(d)
        if len(kept) < len(v):
            log.warning("RME units: %d duplicate or surplus entr%s left out", len(v) - len(kept), "y" if len(v) - len(kept) == 1 else "ies")
        return kept

    @field_validator("power_points")
    @classmethod
    def _points_unique_and_capped(cls, v: list) -> list:
        """Later duplicates (same id) are dropped and the list cut to MAX_POINTS. Logs a count only."""
        seen, kept = set(), []
        for p in v:
            if p.id in seen or len(kept) >= power_tree.MAX_POINTS:
                continue
            seen.add(p.id)
            kept.append(p)
        if len(kept) < len(v):
            log.warning("Power points: %d duplicate or surplus entr%s left out", len(v) - len(kept), "y" if len(v) - len(kept) == 1 else "ies")
        return kept

    @field_validator("globcon", mode="before")
    @classmethod
    def _lenient_globcon(cls, v):
        """On load (the API is strict): an address or password that is not acceptable is cleared with a
        warning, so one bad value never sends the whole file through salvage. Never logs the value."""
        if isinstance(v, dict) and "host" in v and (not v["host"] or spl_host_error(v["host"])):
            if v["host"]:
                log.warning("GLOBCON: the saved address is not usable and was cleared")
            v = {**v, "host": ""}
        if isinstance(v, dict) and isinstance(v.get("password"), (int, float)) and not isinstance(v["password"], bool):
            v = {**v, "password": str(v["password"])}
        if isinstance(v, dict) and v.get("password") not in (None, "") and spl_password_error(v["password"]):
            log.warning("GLOBCON: the saved password is not usable and was cleared")
            v = {**v, "password": ""}
        return v

    @field_validator("spl", mode="before")
    @classmethod
    def _lenient_spl_host(cls, v):
        """On load (the API is strict): an address that is not acceptable is cleared with a warning,
        so one bad value never sends the whole file through salvage. Never logs the value."""
        if isinstance(v, dict) and "host" in v and spl_host_error(v["host"]):
            log.warning("Sound level: the saved address is not usable and was cleared")
            v = {**v, "host": ""}
        if isinstance(v, dict) and isinstance(v.get("password"), (int, float)) and not isinstance(v["password"], bool):
            v = {**v, "password": str(v["password"])}
        if isinstance(v, dict) and v.get("password") not in (None, "") and spl_password_error(v["password"]):
            log.warning("Sound level: the saved password is not usable and was cleared")
            v = {**v, "password": ""}
        return v

    @field_validator("wall_clock", mode="before")
    @classmethod
    def _wall_clock_floor(cls, v):
        """On load only (the API validates WallClockConfig itself and refuses): a saved warning
        limit below the 1.0 s minimum (older builds allowed 0.5) is raised to it, and a display
        option this build does not know (from a newer release) is left out.  No schema bump."""
        if isinstance(v, dict) and isinstance(v.get("warn_offset_s"), (int, float)) \
                and not isinstance(v["warn_offset_s"], bool) and v["warn_offset_s"] < 1.0:
            v = {**v, "warn_offset_s": 1.0}
        if isinstance(v, dict) and "display" in v:
            disp = v["display"]
            if not isinstance(disp, dict):
                v = {k: x for k, x in v.items() if k != "display"}
            elif disp.get("ring") not in (None, "sweep", "fill"):
                v = {**v, "display": {k: x for k, x in disp.items() if k != "ring"}}
        return v

    @field_validator("entities", "calibrations", mode="before")
    @classmethod
    def _lenient_accuracy(cls, v):
        """On load only (the API validates the models strictly and refuses): an accuracy figure or
        basis that can't be read is left out, so one bad value never costs the offset next to it.
        Logs a count, never the value."""
        if not isinstance(v, dict):
            return v
        fixed, bad, bad_role = {}, 0, 0
        for key, rec in v.items():
            if isinstance(rec, dict) and rec.get("role", "") not in ("", *ROLES):
                rec = {k: x for k, x in rec.items() if k != "role"}   # unknown role: follow the node
                bad_role += 1
            if isinstance(rec, dict) and ("accuracy" in rec or "accuracy_basis" in rec):
                acc = rec.get("accuracy")
                ok_acc = acc is None or (isinstance(acc, (int, float)) and not isinstance(acc, bool)
                                         and ACCURACY_MIN_ANY <= acc <= ACCURACY_MAX_ANY)
                ok_basis = rec.get("accuracy_basis", "typical") in ACCURACY_BASES
                if not (ok_acc and ok_basis):
                    bad += 1
                    drop = ({"accuracy"} if not ok_acc else set()) | ({"accuracy_basis"} if not ok_basis else set())
                    rec = {k: x for k, x in rec.items() if k not in drop}
            fixed[key] = rec
        if bad:
            log.warning("Sensor accuracy: %d figure%s this version can't read left out",
                        bad, "" if bad == 1 else "s")
        if bad_role:
            log.warning("Sensor roles: %d value%s this version can't read left out (those sensors follow their node)",
                        bad_role, "" if bad_role == 1 else "s")
        return fixed

    @field_validator("esphome_devices", mode="before")
    @classmethod
    def _lenient_node_role(cls, v):
        """On load: a node role this version doesn't know (from a newer release) counts as
        "environment" rather than costing the node. Logs a count, never the value."""
        if not isinstance(v, list):
            return v
        out, bad, bad_sleep = [], 0, 0
        for rec in v:
            if isinstance(rec, dict) and rec.get("role", ROLE_ENVIRONMENT) not in ROLES:
                rec = {**rec, "role": ROLE_ENVIRONMENT}
                bad += 1
            if isinstance(rec, dict) and "sleep_minutes" in rec:
                m = rec["sleep_minutes"]
                if isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= 1440:
                    rec = {k: x for k, x in rec.items() if k != "sleep_minutes"}   # unreadable: not sleeping
                    bad_sleep += 1
            out.append(rec)
        if bad:
            log.warning("Node roles: %d value%s this version can't read treated as Environment",
                        bad, "" if bad == 1 else "s")
        if bad_sleep:
            log.warning("Node sleep settings: %d value%s this version can't read treated as off",
                        bad_sleep, "" if bad_sleep == 1 else "s")
        return out

    @field_validator("calibrations")
    @classmethod
    def _calibration_keys(cls, v: dict) -> dict:
        """Lenient on load: keys in a format this build doesn't use (e.g. from a newer release)
        are kept but unused; only keys that can't be any key at all are dropped.  Logs counts."""
        unknown = [k for k in v if not valid_calibration_key(k)]
        dropped = [k for k in unknown if not _CALIBRATION_KEY_LOOSE_RE.fullmatch(k)]
        if unknown:
            log.warning("Calibrations: %d entr%s in a format this version doesn't use (kept, not applied), "
                        "%d unreadable entr%s dropped", len(unknown) - len(dropped),
                        "y" if len(unknown) - len(dropped) == 1 else "ies", len(dropped),
                        "y" if len(dropped) == 1 else "ies")
        return {k: c for k, c in v.items() if k not in dropped}

    def entity_settings(self, entity_id: str) -> EntitySettings:
        return self.entities.get(entity_id) or EntitySettings()

    def dashboard(self, slug: str) -> Dashboard | None:
        return next((d for d in self.dashboards if d.slug == slug), None)


_PIN_HASH_RE = re.compile(r"^pbkdf2_sha256\$(\d{1,9})\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)$")
_PIN_HASH_SCAN = re.compile(r"pin_hash:\s*[\"']?(pbkdf2_sha256\$\d{1,9}\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+)")
ALLOW_ONBOARDING_FLAG = ".allow-onboarding"


def valid_pin_hash(value) -> bool:
    """True if ``value`` is a well-formed hash as written by ``web.auth.hash_pin``."""
    if not isinstance(value, str):
        return False
    m = _PIN_HASH_RE.match(value)
    if not m or int(m.group(1)) < 1:
        return False
    try:
        return len(base64.b64decode(m.group(2), validate=True)) >= 8 \
            and len(base64.b64decode(m.group(3), validate=True)) >= 16
    except (ValueError, TypeError):
        return False


_SPL_RANGE_KEYS = ("chart_range", "chart_min_db", "chart_max_db")


def salvage(raw: object, text: str = "") -> tuple[Config, list[str]]:
    """Best-effort config from a config that failed to load as a whole.

    Keeps ``admin.pin_hash`` if it is well formed, and every other top-level section that
    validates on its own (list/dict sections are validated item by item; only the bad items are
    dropped).  ``text`` (the raw file) is scanned for a PIN hash when the YAML did not parse.
    Returns (config, notes); notes name sections only, never values (they may hold secrets).
    """
    notes: list[str] = []
    data: dict = {}
    pin = ""
    if isinstance(raw, dict):
        try:
            raw = migrate(dict(raw))
        except Exception:
            notes.append("schema_version unreadable")
        admin = raw.get("admin")
        cand = admin.get("pin_hash") if isinstance(admin, dict) else None
        if valid_pin_hash(cand):
            pin = cand
        for key in Config.model_fields:
            if key in ("admin", "schema_version") or key not in raw:
                continue
            val = raw[key]
            try:
                data[key] = getattr(Config.model_validate({key: val}), key)
                continue
            except Exception:
                pass
            kept = None
            if isinstance(val, list):
                kept = []
                for item in val:
                    try:
                        kept += getattr(Config.model_validate({key: [item]}), key)
                    except Exception:
                        pass
            elif isinstance(val, dict):
                kept = {}
                # the three graph range keys are only meaningful together: check them as one item
                group = {k: v for k, v in val.items() if key == "spl" and k in _SPL_RANGE_KEYS}
                items = [(k, v) for k, v in val.items() if k not in group]
                if group:
                    items.append((None, group))
                for k, item in items:
                    try:
                        got = getattr(Config.model_validate({key: item if k is None else {k: item}}), key)
                        if isinstance(got, BaseModel):    # a model section, field by field: only what was set
                            got = got.model_dump(exclude_unset=True)
                        kept.update(got)
                    except Exception:
                        pass
            if kept:
                data[key] = kept
                notes.append(f"section '{key}': some entries were invalid and dropped")
            else:
                notes.append(f"section '{key}' was invalid and reset to defaults")
    else:
        m = _PIN_HASH_SCAN.search(text or "")
        if m and valid_pin_hash(m.group(1)):
            pin = m.group(1)
            notes.append("config did not parse; only the admin PIN hash was recovered")
        else:
            notes.append("config did not parse; nothing could be recovered")
    cfg = Config.model_validate(data)
    cfg.admin.pin_hash = pin
    notes.append("admin PIN hash recovered" if pin else "admin PIN hash could NOT be recovered")
    return cfg, notes


def _describe_error(exc: BaseException) -> str:
    """A log-safe description of a config load failure: section names, error types and
    line/column only.  Never the offending input (it may be a password or key)."""
    if isinstance(exc, ValidationError):
        parts = []
        for e in exc.errors(include_input=False, include_url=False, include_context=False):
            parts.append(f"{'.'.join(str(p) for p in e.get('loc', ())) or '<root>'}: {e.get('type', 'error')}")
        return "; ".join(parts[:10]) + ("; ..." if len(parts) > 10 else "")
    if isinstance(exc, yaml.YAMLError):
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        return f"YAML syntax error{where}"
    return type(exc).__name__


def _write_yaml_atomic(path: Path, data: dict) -> None:
    """Write YAML durably: temp file in the same dir, flush + fsync, replace, fsync the dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, path)
        fsync_dir(path.parent)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.config = Config()

    # ---- recovery state ----
    def invalid_files(self) -> list[Path]:
        return sorted(self.path.parent.glob(f"{self.path.stem}.invalid*.yaml"))

    @property
    def recovery_required(self) -> bool:
        """True when there is no admin PIN because a previous config was unreadable (an
        ``*.invalid*.yaml`` exists).  Network onboarding must then stay closed: anyone on the LAN
        could otherwise claim admin.  Reopened only by ``stagewatch reset-admin-pin`` (filesystem
        access), which drops the ALLOW_ONBOARDING_FLAG file."""
        return (not self.config.admin.pin_hash and bool(self.invalid_files())
                and not (self.path.parent / ALLOW_ONBOARDING_FLAG).exists())

    def _quarantine(self) -> Path:
        """Move the bad file aside without overwriting an earlier one."""
        backup = self.path.with_suffix(".invalid.yaml")
        if backup.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = self.path.with_suffix(f".invalid-{stamp}.yaml")
            n = 1
            while backup.exists():
                backup = self.path.with_suffix(f".invalid-{stamp}-{n}.yaml")
                n += 1
        os.replace(self.path, backup)
        return backup

    @property
    def bak_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def _cleanup_temps(self) -> None:
        n = remove_stale_temps(self.path.parent, [".config-*.yaml"], 60.0)
        if n:
            log.info("removed %d stale temp config file(s) left by an interrupted save", n)

    def _load_bak(self) -> Config | None:
        """The previous good config, or None when there is no usable .bak.  Logs no values."""
        try:
            raw = yaml.safe_load(self.bak_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not raw:
                return None
            return Config.model_validate(migrate(raw))
        except Exception:
            return None

    def load(self) -> Config:
        self._cleanup_temps()
        if self.path.exists():
            text = ""
            raw = None
            unparseable = False
            try:
                text = self.path.read_text(encoding="utf-8")
                try:
                    raw = yaml.safe_load(text)
                except yaml.YAMLError:
                    unparseable = True
                    raise
                # An empty file is what a torn write leaves behind, never a valid saved config.
                if not isinstance(raw, dict) or not raw:
                    unparseable = True
                    raise ValueError("config root must be a non-empty mapping")
                self.config = Config.model_validate(migrate(raw))
            except Exception as exc:
                reason = _describe_error(exc)  # locations/types only, never input values
                restored = self._load_bak() if unparseable or isinstance(exc, UnicodeDecodeError) else None
                if restored is not None:
                    self.config = restored
                    try:
                        backup = self._quarantine()
                    except OSError:
                        log.error("could not move the unreadable config aside")
                        backup = self.path
                    log.error("Config %s is unreadable (%s); restored the previous good config from %s. "
                              "The unreadable file was kept as %s", self.path, reason,
                              self.bak_path.name, backup.name)
                    try:
                        self.save()
                    except OSError:
                        log.error("could not write the restored config")
                else:
                    # Never refuse to boot on show day, and never let a bad file reopen onboarding:
                    # keep the bad file, salvage what validates (PIN first) and write that back.
                    self.config, notes = salvage(raw, text)
                    try:
                        backup = self._quarantine()
                    except OSError:
                        log.error("could not move the invalid config aside")
                        backup = self.path
                    log.error("Config %s is invalid (%s); original kept as %s. Salvaged: %s",
                              self.path, reason, backup.name, "; ".join(notes))
                    try:
                        self.save()
                    except OSError:
                        log.error("could not write the salvaged config")
        power_tree.tidy_config(self.config)
        if self.config.admin.pin_hash:
            try:
                (self.path.parent / ALLOW_ONBOARDING_FLAG).unlink()
            except OSError:
                pass
        if self.recovery_required:
            log.error("No admin PIN could be recovered from an invalid config; network onboarding is "
                      "disabled. Restore config.yaml from a backup or run "
                      "'stagewatch reset-admin-pin --data-dir <data folder>' on this computer.")
        return self.config

    def _backup_current(self) -> None:
        """Keep the previous good config as config.yaml.bak (atomic).  Only a config that parses
        as a non-empty mapping is worth keeping; a bad current file never overwrites a good .bak."""
        try:
            data = self.path.read_bytes()
            parsed = yaml.safe_load(data.decode("utf-8"))
            if isinstance(parsed, dict) and parsed:
                atomic_write_bytes(self.bak_path, data)
        except Exception:
            pass  # best effort: never block a save because the backup failed

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self.config.model_dump(mode="json")
        self._backup_current()
        _write_yaml_atomic(self.path, data)
        if self.config.admin.pin_hash:  # onboarding done: the recovery override is spent
            try:
                (self.path.parent / ALLOW_ONBOARDING_FLAG).unlink()
            except OSError:
                pass


def reset_admin_pin(data_dir: Path) -> str:
    """Local recovery (CLI only, never HTTP): clear the admin PIN hash and reopen onboarding.

    Works on the raw YAML so it also works when the config no longer validates.  Returns a short
    human summary.  The server must be restarted afterwards (it holds the old config in memory).
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"data directory not found: {data_dir}")
    path = data_dir / "config.yaml"
    cleared = False
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("admin"), dict) and raw["admin"].get("pin_hash"):
            raw["admin"]["pin_hash"] = ""
            _write_yaml_atomic(path, raw)
            cleared = True
            # The .bak would still hold the old PIN hash and could silently bring it back.
            bak = path.with_name(path.name + ".bak")
            bak_raw = None
            try:
                bak_raw = yaml.safe_load(bak.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError, UnicodeDecodeError):
                pass
            if isinstance(bak_raw, dict) and isinstance(bak_raw.get("admin"), dict) \
                    and bak_raw["admin"].get("pin_hash"):
                bak_raw["admin"]["pin_hash"] = ""
                _write_yaml_atomic(bak, bak_raw)
    (data_dir / ALLOW_ONBOARDING_FLAG).write_text("created by 'stagewatch reset-admin-pin'\n", encoding="utf-8")
    return ("Admin PIN cleared." if cleared else "No admin PIN was stored.") + \
        " Restart Stagewatch, then open /admin to set a new PIN."
