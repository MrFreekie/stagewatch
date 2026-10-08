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
import zoneinfo
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..updater_common import atomic_write_bytes, fsync_dir, remove_stale_temps, replace_with_retry
from ..version import CONFIG_SCHEMA_VERSION
from .cards import CARD_ID_RE, MAX_CARDS, default_cards, legacy_cards
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




class EsphomeDeviceConfig(_Model):
    id: str
    host: str
    port: int = Field(6053, ge=1, le=65535)
    name: str = ""
    area: str = ""
    noise_psk: str = ""
    mac: str = ""  # the board's MAC (12 lowercase hex), set by the hub on first connect

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


class EntitySettings(_Model):
    """Legacy per-entity calibration, keyed by entity id.  Kept as the pending map: entries move to
    ``Config.calibrations`` once the hardware behind the entity is known."""
    offset: float = 0.0
    include_in_average: bool = True


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
    colon_blink: bool = False     # the colons blink once a second (never under reduced motion)


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

    @field_validator("clock_style", mode="before")
    @classmethod
    def _clock_style(cls, v):
        return v if isinstance(v, str) and v in CLOCK_STYLES else "digits"

    @model_validator(mode="after")
    def _default_cards(self):
        if "cards" not in self.model_fields_set:
            self.__dict__["cards"] = default_cards(self.layout)  # a default: leave model_fields_set alone
        return self


class OscDestination(_Model):
    host: str
    port: int = Field(9000, ge=1, le=65535)
    enabled: bool = True


class OscOutConfig(_Model):
    destinations: list[OscDestination] = Field(default_factory=list)
    per_node: bool = False
    rate_hz: float = Field(1.0, gt=0, le=20)


class UpdaterConfig(_Model):
    """In-app updater settings.  ``channel`` is only the admin's *request*; the launcher
    re-validates every target itself (see core/updater.py).  None = use the installer's marker."""
    channel: Literal["stable", "nightly"] | None = None


MAX_IGNORED = 200


class BarometerConfig(_Model):
    """Barometer card settings. Additive with defaults: no config schema bump. An older build
    ignores the section and forgets it on its next save."""
    hemisphere: Literal["north", "south"] = "north"   # which months count as summer for the outlook
    # Optional silent notice (never audible) plus one marker when pressure is falling quickly.
    rapid_fall_alarm: bool = False
    rapid_fall_hpa_3h: float = Field(3.6, ge=1.5, le=10)   # Met Office "quickly": 3.6 hPa in 3 h


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
    updater: UpdaterConfig = Field(default_factory=UpdaterConfig)
    wall_clock: WallClockConfig = Field(default_factory=WallClockConfig)
    ontime_timer: OntimeTimerConfig = Field(default_factory=OntimeTimerConfig)
    barometer: BarometerConfig = Field(default_factory=BarometerConfig)

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
                for k, item in val.items():
                    try:
                        got = getattr(Config.model_validate({key: {k: item}}), key)
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
