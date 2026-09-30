"""Show/site configuration: pydantic models persisted as YAML.

Loading is tolerant: unknown keys are ignored and missing keys fall back to
defaults, so a config written by an older version still loads. Saving is
atomic (write to a temp file, then replace) so a power cut mid-save can't
leave a truncated config on a show day.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..updater_common import atomic_write_bytes, fsync_dir, remove_stale_temps, replace_with_retry
from ..version import CONFIG_SCHEMA_VERSION
from .model import slugify

log = logging.getLogger(__name__)


def migrate(raw: dict) -> dict:
    """Upgrade a raw config dict from an older schema_version, one step at a
    time. Add a branch here whenever CONFIG_SCHEMA_VERSION is bumped."""
    version = int(raw.get("schema_version", 1))
    if version > CONFIG_SCHEMA_VERSION:
        log.warning("config.yaml is schema %s, newer than this build (%s); "
                    "unknown settings will be ignored", version, CONFIG_SCHEMA_VERSION)
    # e.g. if version < 2: raw = _v1_to_v2(raw); version = 2
    raw["schema_version"] = CONFIG_SCHEMA_VERSION
    return raw


class _Model(BaseModel):
    # hide_input_in_errors: a bad noise_psk/password must never be echoed in an error message.
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class SiteConfig(_Model):
    name: str = "Stagewatch"
    altitude_m: float = Field(0.0, ge=-500, le=6000)
    reference_distance_m: float = Field(30.0, gt=0, le=2000)
    stale_after_s: float = Field(60.0, ge=5, le=3600)
    smoothing_tau_s: float = Field(30.0, ge=0, le=900)
    outlier_reject: bool = True


class AdminConfig(_Model):
    pin_hash: str = ""


class EsphomeDeviceConfig(_Model):
    id: str
    host: str
    port: int = Field(6053, ge=1, le=65535)
    name: str = ""
    area: str = ""
    noise_psk: str = ""
    password: str = ""

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v)


class EntitySettings(_Model):
    offset: float = 0.0
    include_in_average: bool = True


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

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v)


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


class Config(_Model):
    schema_version: int = CONFIG_SCHEMA_VERSION
    site: SiteConfig = Field(default_factory=SiteConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    mdns_name: str = "stagewatch"
    esphome_devices: list[EsphomeDeviceConfig] = Field(default_factory=list)
    entities: dict[str, EntitySettings] = Field(default_factory=dict)
    thresholds: list[Threshold] = Field(default_factory=list)
    dashboards: list[Dashboard] = Field(default_factory=lambda: [
        Dashboard(slug="foh", title="FOH", layout="tablet", allow_marker=True, allow_ack=True),
        Dashboard(slug="phone", title="Phone", layout="phone", allow_marker=True),
        Dashboard(slug="wall", title="Wall", layout="wall", allow_marker=False),
    ])
    osc_out: OscOutConfig = Field(default_factory=OscOutConfig)
    updater: UpdaterConfig = Field(default_factory=UpdaterConfig)

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
                        kept.update(getattr(Config.model_validate({key: {k: item}}), key))
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
