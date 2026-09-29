"""Show/site configuration: pydantic models persisted as YAML.

Loading is tolerant: unknown keys are ignored and missing keys fall back to
defaults, so a config written by an older version still loads. Saving is
atomic (write to a temp file, then replace) so a power cut mid-save can't
leave a truncated config on a show day.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    model_config = ConfigDict(extra="ignore")


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

    def entity_settings(self, entity_id: str) -> EntitySettings:
        return self.entities.get(entity_id) or EntitySettings()

    def dashboard(self, slug: str) -> Dashboard | None:
        return next((d for d in self.dashboards if d.slug == slug), None)


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.config = Config()

    def load(self) -> Config:
        if self.path.exists():
            try:
                raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                if not isinstance(raw, dict):
                    raise ValueError("config root must be a mapping")
                self.config = Config.model_validate(migrate(raw))
            except Exception:
                # Keep the bad file for inspection and start from defaults
                # rather than refusing to boot on show day.
                backup = self.path.with_suffix(".invalid.yaml")
                log.exception("Config %s is invalid; moved to %s and using defaults",
                              self.path, backup)
                os.replace(self.path, backup)
                self.config = Config()
        return self.config

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self.config.model_dump(mode="json")
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-", suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
