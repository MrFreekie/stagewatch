"""FastAPI app: static dashboards, REST API, and a WebSocket live feed.

Roles:
  user  -- anyone on the network: view dashboards; add markers / ack alarms
           only where that dashboard allows it.
  admin -- PIN-authenticated: configure devices, thresholds, dashboards,
           outputs and shows.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import platform
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, ValidationError, field_validator, model_validator

from .. import __version__, acoustics
from ..core.config import (
    ACCURACY_MAX, ACCURACY_MIN, CALIBRATION_HISTORY_MAX, CalibrationEntry, CLOCK_STYLES, PASSWORD_MAX, AlarmsConfig, BarometerConfig, Dashboard, EntitySettings, EsphomeDeviceConfig, GLOBCON_RANGES, GlobconConfig, OntimeTimerConfig, OscOutConfig,
    ContactsSettings, SiteConfig, SplConfig, SplSlot, Threshold, WallClockConfig, spl_host_error, spl_password_error,
)
from ..core.calibration import change_text, set_calibration, utc_now_iso
from ..core import calibration_io
from ..core.hub import Hub
from ..core.model import Device, Entity, Kind, Marker, slugify
from ..core.recorder import clean_note, valid_day
from .. import diagnostics, netinfo
from ..core import barometer as baro_mod
from ..core import cards as cards_mod
from ..core import device_errors as errors_mod
from ..core import actions as actions_mod
from ..core import schedule as sched
from ..core import spl as spl_mod
from ..core import spl_limits as limits_mod
from ..integrations.lanwm import importer as logimport
from ..integrations.ontime.client import check_connection
from ..integrations.shelly import TEXT as SHELLY_TEXT, InUse
from ..integrations.shelly.http import ShellyError
from ..core.updater import RateLimited, Updater, message_for
from ..updater_common import UpdaterError
from ..version import build_info
from .auth import COOKIE, SESSION_S, SessionSigner, hash_pin, verify_pin
from . import branding as brand
from ..core import documents as docs_mod
from ..core import contacts as contacts_mod
from .limits import DEFAULT_BODY_LIMIT, WS_MAX_MESSAGE, BodySizeLimitMiddleware

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
STATE_FLUSH_S = 0.5
MIN_PIN_LEN = 4
# HTTP status per updater error category (default 409: "the request is fine, the state isn't")
UPDATER_STATUS = {"rate_limited": 429, "history_not_found": 404, "bad_channel": 422, "bad_sha": 422}
# Per-path request-body limits above the 64 KiB default (plan §1.11), merged under the
# ``body_limit_overrides`` argument of create_app. Sized to the real need, never unlimited.
DEFAULT_BODY_OVERRIDES: dict[str, int] = {
    # 300 items with up to 256 KB of setlists, which JSON escaping can double: the legal worst
    # case must reach validation (422), not be cut off as too large (413).
    "/api/admin/schedule": 640 * 1024,
    "/api/admin/schedule/import": 256 * 1024,  # 64 KB of text, JSON-escaped
    # Calibration import (plan §1.9): read by the browser's FileReader and sent as JSON without
    # the file's history (the page strips it), so 500 records fit well inside this.
    "/api/admin/calibrations/import": 1024 * 1024,
    # Vendor log import: the file's text as JSON. A real LA Network Manager session is well under 1 MB.
    "/api/admin/logs/import": 6 * 1024 * 1024,
}


# Largest offset an import accepts, in canonical units (degC, %RH, Pa); "" = sensor not live now.
OFFSET_MAX = {"temperature": 50.0, "humidity": 50.0, "pressure": 20000.0, "": 20000.0}


# ---------------------------------------------------------------- payloads
_STATIC_REF = re.compile(r'((?:src|href)="/static/)([^"?#]+)(")')
_page_cache: dict[str, tuple[tuple, str]] = {}


def versioned_page(name: str) -> HTMLResponse:
    """A page with each /static/ file it loads tagged by a hash of that file's contents
    ("/static/dashboard.js?v=3f9a1c2b7e"). After an update the page asks for new URLs, so a
    browser can never mix a new page with old cached scripts (v0.2.0 sent no cache headers, so
    browsers may hold its files for days)."""
    html_path = STATIC / name
    html = html_path.read_text(encoding="utf-8")
    refs = [m.group(2) for m in _STATIC_REF.finditer(html)]
    key = tuple((r, (STATIC / r).stat().st_mtime_ns if (STATIC / r).is_file() else 0)
                for r in [name, *refs])
    cached = _page_cache.get(name)
    if cached is None or cached[0] != key:
        def tag(m: re.Match) -> str:
            f = STATIC / m.group(2)
            if not f.is_file():
                return m.group(0)
            v = hashlib.sha256(f.read_bytes()).hexdigest()[:10]
            return f"{m.group(1)}{m.group(2)}?v={v}{m.group(3)}"
        cached = (key, _STATIC_REF.sub(tag, html))
        _page_cache[name] = cached
    return HTMLResponse(cached[1], headers={"Cache-Control": "no-cache"})


class RevalidatingStaticFiles(StaticFiles):
    """Static files the browser must re-check every time (cheap: ETag, 304 on a LAN). Without it,
    after an update a tablet can run a new admin.js against a cached old common.js."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class PinBody(BaseModel):
    pin: str = Field(min_length=MIN_PIN_LEN, max_length=64)


class ChangePinBody(BaseModel):
    current: str
    new: str = Field(min_length=MIN_PIN_LEN, max_length=64)


class MarkerBody(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    label: str = Field("Marker", max_length=120)
    dashboard: str = Field("", max_length=64)
    note: str = Field("", max_length=4000)  # characters before trimming; clean_note checks the rest

    @field_validator("note")
    @classmethod
    def _note(cls, v: str) -> str:
        return clean_note(v)


class MarkerPatch(BaseModel):
    """PATCH /api/markers/{id}: change the note and/or hide or un-hide. ``dashboard`` is the
    dashboard asking (its "Add markers" permission covers this); admins don't need it."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    note: str | None = Field(None, max_length=4000)
    hidden: StrictBool | None = None
    dashboard: str = Field("", max_length=64)

    @field_validator("note")
    @classmethod
    def _note(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("Send the note as text (an empty note clears it)")
        return clean_note(v)

    @model_validator(mode="after")
    def _something(self):
        if self.note is None and self.hidden is None:
            raise ValueError("Nothing to change: send a note or hidden")
        return self


class ScheduleSettingsBody(BaseModel):
    """PUT /api/admin/schedule/settings: the schedule-wide switches."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    auto_markers: StrictBool


class AckBody(BaseModel):
    dashboard: str = ""


# A host name or IPv4 address: dot-separated labels of letters, digits, '-' and '_' (fullmatch).
_HOSTNAME_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
                          r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*\.?")
HOST_ERROR = "The address must be a host name or IP address, with no spaces"


def clean_host(v: str) -> str:
    """A node address: a host name, an IPv4 address or a bare IPv6 address. No brackets, no
    ``%`` zone, no scheme (``http:``), no port, no spaces. Fixed error text, never the input."""
    v = (v or "").strip()
    if not v or len(v) > 253:
        raise ValueError(HOST_ERROR)
    if ":" in v:
        if any(ch in v for ch in "[]%"):
            raise ValueError(HOST_ERROR)
        try:
            ipaddress.IPv6Address(v)
        except ValueError:
            raise ValueError(HOST_ERROR) from None
        return v
    if not _HOSTNAME_RE.fullmatch(v):
        raise ValueError(HOST_ERROR)
    return v


class AdoptBody(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)  # carries the encryption key

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(6053, ge=1, le=65535)
    id: str = ""
    name: str = ""
    area: str = ""
    noise_psk: str = ""
    role: Literal["environment", "equipment"] = "environment"

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        return clean_host(v)


class IgnoreBody(BaseModel):
    key: str = Field(min_length=1, max_length=80)


class ResolveBody(BaseModel):
    """POST /api/admin/esphome/{id}/resolve: settle a node held by the hardware check."""
    model_config = ConfigDict(extra="forbid")

    action: Literal["new_hardware", "move_calibration", "forget_mac"]


class CalibrationImportBody(BaseModel):
    """POST /api/admin/calibrations/import: the parsed export file, and whether to only look."""
    model_config = ConfigDict(extra="forbid")

    data: dict
    dry_run: bool = True   # the safe way round: a caller that forgets it changes nothing


class CalibrationNoteBody(BaseModel):
    """POST /api/admin/calibrations/{entity_id}/note: a line in the sensor's calibration history."""
    model_config = ConfigDict(extra="forbid")

    reference: str = Field("", max_length=120)   # e.g. the instrument it was checked against
    note: str = Field("", max_length=200)


SERVICE_DEVICE = "This device is managed by its integration. Nothing has been changed."
RESOLVE_REFUSED = "There is nothing to resolve this way for this node. Nothing has been changed."


class DevicePatch(BaseModel):
    """PATCH /api/admin/devices/{id}. A new host or port reconnects the node; its recorded MAC is
    kept, so the same board at the new address keeps its calibration. The MAC itself can't be
    set here (the hub records it from the board)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    area: str | None = None
    role: Literal["environment", "equipment"] | None = None
    sleep_minutes: int | None = Field(None, ge=0, le=1440)   # 0 = the node does not sleep
    host: str | None = Field(None, max_length=253)
    port: int | None = Field(None, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str | None) -> str | None:
        return v if v is None else clean_host(v)


class OntimeTimerBody(BaseModel):
    """PUT /api/admin/ontime-timer. The field is required and must be a real true/false, so an empty
    body or a misspelt name is refused instead of silently turning the titles back on."""

    model_config = ConfigDict(extra="forbid")

    show_title: StrictBool


class WallClockTestBody(BaseModel):
    """POST /api/admin/wall-clock/test. ``ontime_url`` tries an address that is not saved yet;
    left out, the saved one is used."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    ontime_url: str | None = Field(None, max_length=300)


class AltitudeFromPressureBody(BaseModel):
    """POST /api/admin/site/altitude-from-pressure: today's sea-level pressure (QNH) in hPa."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    qnh_hpa: float = Field(allow_inf_nan=False)


class BarometerBody(BaseModel):
    """PUT /api/admin/barometer: strict (no coercion, no unknown keys). BarometerConfig itself stays
    lenient so an older or damaged file still loads."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    hemisphere: Literal["north", "south"] = "north"
    rapid_fall_alarm: StrictBool = False
    rapid_fall_hpa_3h: float = Field(3.6, ge=1.5, le=10, allow_inf_nan=False, strict=True)


class AlarmsBody(BaseModel):
    """PUT /api/admin/alarms: how long advisory notices stay on dashboards (minutes, 0 = never).
    Strict: whole numbers only, no unknown keys."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    hide_acked_min: int = Field(ge=0, le=1440, strict=True)
    fold_old_min: int = Field(ge=0, le=10080, strict=True)


class SplSlotBody(BaseModel):
    """One recorded value: Smaart's own metric text, and the input it is read from ("" = the first
    input Smaart lists)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    metric: str = Field(min_length=1, max_length=spl_mod.METRIC_NAME_MAX)
    source: str = Field("", max_length=spl_mod.INPUT_NAME_MAX)


class SplLimitsBody(BaseModel):
    """PUT /api/admin/spl-limits: a whole limit set for the event (every day) or this show only.
    Strict (unknown fields refused); public wording only, so no secrets, but errors never echo input."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, populate_by_name=True)
    scope: Literal["event", "show"]
    limit_set: limits_mod.SplLimitSet = Field(alias="set")
    entered_by: str = Field("", max_length=limits_mod.ENTERED_BY_MAX)
    change_note: str = Field("", max_length=limits_mod.CHANGE_NOTE_MAX)


class SplLimitsCopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    from_event_id: StrictInt = Field(ge=1, le=2**31)


class SplLimitsSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    change_warning_min: StrictInt = Field(ge=0, le=60)
    exceed_marker: StrictBool
    exceed_alarm: StrictBool


class SplBody(BaseModel):
    """PUT /api/admin/spl: the sound level (Smaart) settings. Strict: real true/false, a whole-number
    port, at most three values. The address is checked by SplConfig (host name or a local network IP
    address). ``password`` is Smaart's API password and is write-only: empty leaves the saved one
    alone, ``clear_password`` removes it. ``meters`` (input + metric) is the current form; ``slots``
    (older metric keys) is still accepted when ``meters`` is not sent."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enabled: StrictBool
    host: str = Field("", max_length=253)
    port: int | None = Field(None, ge=1, le=65535, strict=True)
    slots: list[str] = Field(default_factory=list, max_length=spl_mod.MAX_SLOTS)
    meters: list[SplSlotBody] | None = Field(None, max_length=spl_mod.MAX_SLOTS)
    password: str = Field("", max_length=PASSWORD_MAX)
    clear_password: StrictBool = False
    # A label for the card ("FOH"); checked in put_spl with fixed-text errors. Generous cap here only
    # to bound the request; the real limit is spl_mod.LOCATION_MAX.
    # Not sent = keep the saved one; "" clears it.
    location: str | None = Field(None, max_length=200)
    # Per-input labels (Smaart's input text -> label); not sent = keep, sent = replaces the whole map.
    locations: dict[str, str] | None = Field(None, max_length=40)
    # The graph range: not sent = keep. Real numbers only (checked in put_spl with fixed text).
    chart_range: Literal["auto", "custom"] | None = None
    chart_min_db: StrictInt | StrictFloat | None = None
    chart_max_db: StrictInt | StrictFloat | None = None


class GlobconBody(BaseModel):
    """PUT /api/admin/globcon: where GLOBCON is. Strict: a whole-number port, no unknown keys.
    ``password`` is GLOBCON's controller password and is write-only: empty leaves the saved one alone,
    ``clear_password`` removes it."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    host: str = Field(max_length=253)
    port: int = Field(9091, ge=1, le=65535, strict=True)
    password: str = Field("", max_length=PASSWORD_MAX)
    clear_password: StrictBool = False


class ShellyTestBody(BaseModel):
    """POST /api/admin/shelly/test: the address of a meter to look at. An IP address only."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    host: str = Field(min_length=1, max_length=64)


class ShellyAddBody(ShellyTestBody):
    """POST /api/admin/shelly/add. The meter's MAC is never taken from the client: it is read from the meter."""
    name: str = Field(min_length=1, max_length=60)
    area: str = Field("", max_length=40)
    interval_s: float = Field(3.0, ge=2.0, le=5.0, allow_inf_nan=False)

    @field_validator("name", "area")
    @classmethod
    def _plain(cls, v: str) -> str:
        v = v.strip()
        if _has_hidden_chars(v):
            raise ValueError("Use plain text")
        return v


class ShellyChannelBody(BaseModel):
    """One circuit's label, line and rating inside PATCH /api/admin/shelly/{id}. Rating 0 means none."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    key: str = Field(max_length=24, pattern=r"^[a-z0-9_]+$")
    label: str = Field("", max_length=40)
    phase: Literal["", "L1", "L2", "L3"] = ""
    rating_a: float | None = Field(None, ge=0, le=200, allow_inf_nan=False)

    @field_validator("label")
    @classmethod
    def _plain(cls, v: str) -> str:
        v = v.strip()
        if _has_hidden_chars(v):
            raise ValueError("Use plain text")
        return v


class ShellyPatchBody(BaseModel):
    """PATCH /api/admin/shelly/{id}. A new address must be the same meter (checked against its MAC)."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str | None = Field(None, min_length=1, max_length=60)
    area: str | None = Field(None, max_length=40)
    interval_s: float | None = Field(None, ge=2.0, le=5.0, allow_inf_nan=False)
    rating_a: float | None = Field(None, ge=0, le=200, allow_inf_nan=False)   # 0 clears the rating
    channels: list[ShellyChannelBody] | None = Field(None, max_length=16)
    host: str | None = Field(None, min_length=1, max_length=64)

    @field_validator("name", "area")
    @classmethod
    def _plain(cls, v):
        if v is not None:
            v = v.strip()
            if _has_hidden_chars(v):
                raise ValueError("Use plain text")
        return v


class BaroDemoBody(BaseModel):
    """POST /api/admin/barometer/demo (emulate only)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    scenario: Literal["steady", "slow_fall", "front", "storm", "rising", "dropout", "none"]


QNH_MIN_HPA, QNH_MAX_HPA = 940.0, 1060.0   # record extremes are about 870 and 1,085 hPa: a typo is caught
ALTITUDE_RANGE_M = (-500.0, 6000.0)        # the same as SiteConfig.altitude_m


WALL_CLOCK_TEST_TEXT = {
    "unreachable": "Can't reach Ontime at that address.",
    "timeout": "Ontime did not answer in time.",
    "not_ontime": "Something answered, but not like Ontime.",
    "too_large": "Something answered, but not like Ontime.",
}


def _has_hidden_chars(text: str) -> bool:
    """Control or format characters (newlines, zero-width, bidi overrides such as U+202E)."""
    return any(unicodedata.category(ch).startswith("C") for ch in text)


class DashboardBody(Dashboard):
    """PUT /api/admin/dashboards: strict card list, title and stage (loading a saved config is lenient)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @field_validator("title")
    @classmethod
    def _title_strict(cls, v: str) -> str:
        if len(v) > 80:
            raise ValueError("Dashboard titles can be up to 80 characters")
        if _has_hidden_chars(v):
            raise ValueError("Dashboard titles can't contain hidden or control characters")
        return v

    @field_validator("stage")
    @classmethod
    def _stage_strict(cls, v: str) -> str:
        if _has_hidden_chars(v):
            raise ValueError("Stage names can't contain hidden or control characters")
        return v

    @field_validator("contacts_area")
    @classmethod
    def _contacts_area_strict(cls, v: str) -> str:
        if _has_hidden_chars(v):
            raise ValueError("Area names can't contain hidden or control characters")
        return v

    @field_validator("clock_style", mode="before")
    @classmethod
    def _clock_style(cls, v):
        """Overrides the lenient loader: only a style this build knows can be saved."""
        if v not in CLOCK_STYLES:
            raise ValueError("Clock style must be digits, ring or segments")
        return v

    @field_validator("card_sizes", mode="before")
    @classmethod
    def _card_sizes(cls, v):
        problem = cards_mod.strict_card_sizes_error(v)
        if problem:
            raise ValueError(problem)
        return v

    @field_validator("globcon", mode="before")
    @classmethod
    def _globcon(cls, v):
        """Overrides the lenient loader: the controller must be a whole number 1 to 16, the strips 4 or 8 and the
        range (optional) one of the channel groups."""
        if not isinstance(v, dict) or set(v) - {"controller", "strips", "range"}:
            raise ValueError("GLOBCON card options are not valid")
        c, s = v.get("controller", 1), v.get("strips", 8)
        if type(c) is not int or not 1 <= c <= 16:
            raise ValueError("The GLOBCON controller must be a whole number from 1 to 16")
        if type(s) is not int or s not in (4, 8):
            raise ValueError("The GLOBCON card shows 4 or 8 strips")
        r = v.get("range")
        if r is not None and (not isinstance(r, str) or r not in GLOBCON_RANGES):
            raise ValueError("That GLOBCON channel group is not one of the choices")
        return v

    @field_validator("cards", mode="before")
    @classmethod
    def _cards(cls, v):
        problem = cards_mod.strict_cards_error(v)
        if problem:
            raise ValueError(problem)
        return v


NAME_MAX = 80  # event and show names (recorder.NAME_MAX)
EVENTS_MAX = 100  # admin state: newest events listed
SHOWS_MAX = 1000  # admin state: newest shows listed


def _clean_label(v: str, what: str) -> str:
    """Event and show names: trimmed, 1-80 characters, no hidden or control characters.
    Fixed error text: the submitted value is never echoed."""
    v = v.strip()
    if not 1 <= len(v) <= NAME_MAX:
        raise ValueError(f"{what} names must be 1 to {NAME_MAX} characters")
    if _has_hidden_chars(v):
        raise ValueError(f"{what} names can't contain hidden or control characters")
    return v


def _check_day(v: str | None) -> str | None:
    try:
        return valid_day(v)
    except (ValueError, TypeError):
        raise ValueError("The day must be a real date written YYYY-MM-DD") from None


class ShowBody(BaseModel):
    """POST /api/admin/shows. `{name}` alone keeps the 0.2.0 behaviour: a new show (day) in the
    current event. `from_show_id` (optional) is the show the page was looking at: if another
    click or admin already started a new show, the request is refused (409) instead of
    starting a second one."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(max_length=400)
    event: Literal["current", "new"] = "current"
    event_name: str | None = Field(None, max_length=400)
    day: str | None = Field(None, max_length=10)
    from_show_id: int | None = Field(None, ge=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_label(v, "Show")

    @field_validator("event_name")
    @classmethod
    def _event_name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_label(v, "Event")

    @field_validator("day")
    @classmethod
    def _day(cls, v: str | None) -> str | None:
        return _check_day(v)

    @model_validator(mode="after")
    def _event_needs_name(self):
        if self.event == "new" and self.event_name is None:
            raise ValueError("A new event needs a name")
        if self.event == "current" and self.event_name is not None:
            raise ValueError("An event name is only used when starting a new event")
        return self


class ShowPatch(BaseModel):
    """PATCH /api/admin/shows/current: rename the current show and/or change its day.
    `day: null` goes back to the date it started on (after the day rollover)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str | None = Field(None, max_length=400)
    day: str | None = Field(None, max_length=10)
    show_id: int | None = Field(None, ge=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("Show names must be 1 to 80 characters")
        return _clean_label(v, "Show")

    @field_validator("day")
    @classmethod
    def _day(cls, v: str | None) -> str | None:
        return _check_day(v)

    @model_validator(mode="after")
    def _something(self):
        if not {"name", "day"} & self.model_fields_set:
            raise ValueError("Nothing to change: send a name or a day")
        return self


# Logo upload (plans/custom-logo-upload.md): raw-body PUT, capped by path pattern because the
# path carries the event id. Headroom over the 1 MiB file cap only; the handler checks the file.
LOGO_PATH = re.compile(r"/api/admin/events/[0-9]{1,12}/logo/(light|dark)")
LOGO_BODY_LIMIT = brand.LOGO_MAX_BYTES + 4096


# Document upload (plans/policy-documents.md): the same pattern, sized to the PDF cap.
DOC_FILE_PATH = re.compile(r"/api/admin/events/[0-9]{1,12}/documents/[0-9a-f]{12}/file")
DOC_BODY_LIMIT = docs_mod.PDF_MAX_BYTES + 4096


def _logo_limit(scope) -> int | None:
    path = scope.get("path", "")
    if LOGO_PATH.fullmatch(path):
        return LOGO_BODY_LIMIT
    return DOC_BODY_LIMIT if DOC_FILE_PATH.fullmatch(path) else None


DocCategory = Literal["lightning", "wind", "heat", "evacuation", "electrical", "site_rules", "medical",
                      "noise", "other"]


class DocumentNew(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=docs_mod.TITLE_MAX * 2)
    category: DocCategory
    dashboards: list[str] = Field(default_factory=list, max_length=64)


class ContactIn(contacts_mod.Contact):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ContactsBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    contacts: list[ContactIn] = Field(max_length=contacts_mod.MAX_CONTACTS)


class ContactsSettingsBody(BaseModel):
    """PUT /api/admin/contacts/settings. ``reveal_pin`` sets the dashboard PIN for phone numbers;
    ``clear_reveal_pin`` removes it."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    phones_enabled: StrictBool
    reveal_pin: str | None = Field(None, min_length=6, max_length=12, pattern=r"^[0-9]+$")
    clear_reveal_pin: StrictBool = False


class ContactsCopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_event_id: int = Field(ge=1)


class ContactsRevealBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    dashboard: str = Field(max_length=64)
    pin: str = Field(max_length=64)


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=docs_mod.TITLE_MAX * 2)
    category: DocCategory | None = None
    dashboards: list[str] | None = Field(None, max_length=64)


class BrandingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_dark: Literal["as_is", "invert", "plate"] = "as_is"
    show_on_admin: StrictBool = True
    show_on_index: StrictBool = True


class LogoCopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_event_id: StrictInt = Field(ge=1)


class EventPatch(BaseModel):
    """PATCH /api/admin/events/current: rename the current event."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(max_length=400)
    event_id: int | None = Field(None, ge=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_label(v, "Event")


# ---------------------------------------------------------------- schedule
class ScheduleItemBody(BaseModel):
    """One schedule item as the admin page sends it: local 24-hour HH:MM times on the current
    show's day (``end`` may be empty). ``id`` keeps an existing item's id. ``date`` (the item's
    ``date`` as the API gave it: the show day or the morning after) pins the start to that date,
    so an unchanged item never moves; without it the day rollover decides. The time checks that
    need the site's zone (end after start, clock-change gaps) run in the endpoint."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: int | None = Field(None, ge=1, le=2**53)
    date: str | None = Field(None, max_length=10)
    stage: str = Field("", max_length=400)
    kind: Literal["venue_access", "load_in", "crew_call", "soundcheck", "doors", "act", "changeover",
                  "curfew", "load_out", "other", "action"] = "other"
    title: str = Field(max_length=1000)
    start: str = Field(max_length=5)
    end: str = Field("", max_length=5)
    setlist: str = Field("", max_length=sched.SETLIST_MAX_BYTES)  # characters; bytes checked below
    # Timeline marker: true/false, or null for the kind's default (on for soundcheck, doors and
    # act). Not sent: an existing item keeps its setting, a new one gets the default.
    marker: StrictBool | None = None
    # OSC action (admin only, never on dashboards): an "action" row's message(s), or messages at
    # another item's start / end. Not sent: an existing item keeps its actions.
    action: actions_mod.ScheduleActionBody | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return sched.clean_title(v)

    @field_validator("stage")
    @classmethod
    def _stage(cls, v: str) -> str:
        return sched.clean_stage(v)

    @field_validator("setlist")
    @classmethod
    def _setlist(cls, v: str) -> str:
        return sched.clean_setlist(v)

    @field_validator("date")
    @classmethod
    def _date(cls, v: str | None) -> str | None:
        return sched.clean_date(v)

    @field_validator("start")
    @classmethod
    def _start(cls, v: str) -> str:
        t = sched.norm_hhmm(v)
        if t is None:
            raise ValueError(sched.MSG_TIME)
        return t

    @field_validator("end")
    @classmethod
    def _end(cls, v: str) -> str:
        if not v.strip():
            return ""
        t = sched.norm_hhmm(v)
        if t is None:
            raise ValueError(sched.MSG_END_TIME)
        return t


class SchedulePut(BaseModel):
    """PUT /api/admin/schedule: replace the current show's whole schedule. ``show_id`` is the show
    the page was editing; if another show has started since, the request is refused (409).
    ``revision`` is the schedule revision the page started from; if the schedule has changed
    since (another tab, an import), the request is refused (409) so no change is lost."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    show_id: int = Field(ge=1)
    revision: int = Field(ge=0, le=2**62)
    items: list[ScheduleItemBody]
    # The page knows about actions (lists action rows). Without it, stored action rows are kept.
    with_actions: bool = False

    @field_validator("items", mode="before")
    @classmethod
    def _count(cls, v):
        if isinstance(v, list) and len(v) > sched.MAX_ITEMS:
            raise ValueError(sched.MSG_TOO_MANY)
        return v

    @model_validator(mode="after")
    def _totals(self):
        if sum(sched.utf8_len(i.setlist) for i in self.items) > sched.SETLIST_TOTAL_MAX_BYTES:
            raise ValueError(sched.MSG_SETLIST_TOTAL)
        ids = [i.id for i in self.items if i.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Each item id can only be used once")
        return self


class ActionsArmedBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    armed: StrictBool


class ScheduleImportBody(BaseModel):
    """POST /api/admin/schedule/import. ``dry_run`` (the default) only parses and returns the rows
    and line errors for a preview. ``dry_run: false`` adds the rows after the current items; it
    needs ``show_id`` and ``revision`` and changes nothing if any line has a problem."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    text: str = Field(max_length=sched.IMPORT_MAX_BYTES)  # characters; bytes checked below
    format: Literal["auto", "csv", "tsv", "lines"] = "auto"
    dry_run: bool = True
    show_id: int | None = Field(None, ge=1)
    revision: int | None = Field(None, ge=0, le=2**62)

    @field_validator("text")
    @classmethod
    def _size(cls, v: str) -> str:
        if sched.utf8_len(v) > sched.IMPORT_MAX_BYTES:
            raise ValueError(sched.MSG_IMPORT_TOO_BIG)
        return v

    @model_validator(mode="after")
    def _commit_needs_show(self):
        if not self.dry_run and (self.show_id is None or self.revision is None):
            raise ValueError("To add the rows to the schedule, send the show_id and revision you are editing")
        return self


class LogImportBody(BaseModel):
    """POST /api/admin/logs/import: a vendor log file's text. ``dry_run`` (the default) only reads it and
    returns what an import would do; ``dry_run: false`` writes it."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    format: Literal["lanwm"] = "lanwm"
    text: str = Field(max_length=5_000_000)
    file_name: str = Field("", max_length=200)
    show_id: int | None = Field(None, ge=1)
    tz: str = Field("", max_length=64)
    offset_s: float = Field(0.0, ge=-86400, le=86400)
    session_markers: bool = False
    load_checker_markers: bool = False
    dry_run: bool = True


class ScheduleDemoBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    show_id: int | None = Field(None, ge=1)


class UpdateBody(BaseModel):
    channel: Literal["stable", "nightly"]
    target_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    pin: str = Field(min_length=1, max_length=64)  # step-up: the admin PIN is re-entered


class RollbackBody(BaseModel):
    history_id: int = Field(ge=0)
    pin: str = Field(min_length=1, max_length=64)


class ChannelBody(BaseModel):
    channel: Literal["stable", "nightly"]


# ------------------------------------------------------------- live feed
CLIENT_QUEUE_MAX_MESSAGES = 500
CLIENT_QUEUE_MAX_BYTES = 4 * 1024 * 1024  # JSON bytes waiting for one slow client


class ClientQueue(asyncio.Queue):
    """A WebSocket client's outgoing queue that also counts the JSON bytes waiting in it, so a
    stalled client can't make the server hold an unbounded amount of memory."""

    def _init(self, maxsize):
        super()._init(maxsize)
        self.bytes = 0
        self._size_hint: int | None = None

    def put_sized(self, item, size: int) -> None:
        self._size_hint = size
        try:
            self.put_nowait(item)
        finally:
            self._size_hint = None

    def _put(self, item):
        size = self._size_hint if self._size_hint is not None else len(json.dumps(item, default=str))
        self._queue.append((item, size))
        self.bytes += size

    def _get(self):
        item, size = self._queue.popleft()
        self.bytes -= size
        return item


class LiveFeed:
    """Fans hub events out to WebSocket clients. Entity state updates are
    coalesced so a burst of sensor readings becomes one message per client."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.clients: set[asyncio.Queue] = set()
        self._dirty: dict[str, Entity] = {}
        self._task: asyncio.Task | None = None
        hub.bus.subscribe("*", self._on_event)

    def start(self) -> None:
        self._task = asyncio.create_task(self._flush_loop(), name="live-feed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _send_all(self, msg: dict) -> None:
        clients = list(self.clients)
        if not clients:
            return
        size = len(json.dumps(msg, default=str))
        for q in clients:
            # dead/slow client: drop its messages rather than grow forever (by count or bytes)
            if q.qsize() > CLIENT_QUEUE_MAX_MESSAGES or getattr(q, "bytes", 0) + size > CLIENT_QUEUE_MAX_BYTES:
                continue
            if isinstance(q, ClientQueue):
                q.put_sized(msg, size)
            else:
                q.put_nowait(msg)

    def _on_event(self, topic: str, payload) -> None:
        if topic in ("state", "entity"):
            self._dirty[payload.id] = payload
        elif topic == "marker" and isinstance(payload, Marker):
            self._send_all({"type": "marker", "marker": payload.to_dict()})
        elif topic == "marker_updated" and isinstance(payload, Marker):
            self._send_all({"type": "marker_updated", "marker": payload.to_dict()})
        elif topic == "marker_deleted":
            self._send_all({"type": "marker_deleted", "id": payload})
        elif topic == "alarms":
            self._send_all({"type": "alarms", "alarms": payload,
                            "sounding": self.hub.alarms.sounding})
        elif topic == "device" and isinstance(payload, Device):
            self._send_all({"type": "device", "device": payload.to_dict()})
        elif topic == "wall_clock" and isinstance(payload, dict):
            self._send_all({"type": "wall_clock", **payload})
        elif topic == "ontime_timer" and isinstance(payload, dict):
            self._send_all({"type": "ontime_timer", **payload})
        elif topic == "ontime_rundown" and isinstance(payload, dict):
            self._send_all({"type": "ontime_rundown", **payload})
        elif topic == "globcon_meters" and isinstance(payload, dict):
            self._send_all({"type": "globcon_meters", **payload})
        elif topic == "schedule" and isinstance(payload, dict):
            # Small on purpose: clients fetch GET /api/schedule?stage= for the list itself.
            self._send_all({"type": "schedule", "show_id": payload.get("show_id"),
                            "revision": payload.get("revision")})
        elif topic == "contacts":
            self._send_all({"type": "contacts_changed"})   # no data: dashboards refetch their own list
        elif topic == "documents":
            self._send_all({"type": "documents_changed"})   # no data: dashboards refetch their own list
        elif topic == "spl_limits":
            self._send_all({"type": "spl_limits_changed"})   # no payload: clients refetch GET /api/spl-limits
        elif topic in ("device_removed", "config", "show"):
            self._send_all({"type": "reload"})

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(STATE_FLUSH_S)
            if not self._dirty or not self.clients:
                self._dirty.clear()
                continue
            now = time.time()
            stale_after = self.hub.config.site.stale_after_s
            entities = [self.hub.entity_dict(e, now, stale_after) for e in self._dirty.values()]
            self._dirty.clear()
            self._send_all({"type": "states", "now": now, "entities": entities,
                            "site": {**self.hub.site_meta, "time": self.hub.site_time(now),
                                     "schedule_warn": self.hub.schedule_warn()}})


# -------------------------------------------------------------------- app
def create_app(hub: Hub, manage_hub: bool = True, updater: Updater | None = None,
               body_limit: int = DEFAULT_BODY_LIMIT, body_limit_overrides: dict[str, int] | None = None,
               lan_addresses=None) -> FastAPI:
    updater = updater or Updater.detect(hub)
    lan_addresses = lan_addresses or netinfo.local_ipv4  # callable -> list[str]; injectable for tests
    started_at = time.time()
    signer = SessionSigner(hub.data_dir / "secret.key")
    def event_owner(eid: int) -> str:
        row = hub.recorder.event_branding(eid)
        return repr(row["created"]) if row else ""
    logos = brand.BrandingStore(hub.data_dir, event_owner)
    documents = docs_mod.DocumentStore(hub.data_dir, event_owner)
    contacts = contacts_mod.ContactStore(hub.data_dir, event_owner)
    failed_reveals: list[float] = []
    try:
        documents.clean(hub.recorder.event_id)   # files a crash left without an index entry
    except (OSError, docs_mod.DocError):
        pass
    feed = LiveFeed(hub)
    failed_logins: list[float] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if manage_hub:
            await hub.start()
        feed.start()
        watch = asyncio.create_task(updater.watch_results(), name="updater-results")
        yield
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
        await feed.stop()
        if manage_hub:
            await hub.stop()

    app = FastAPI(title="Stagewatch", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.hub = hub
    app.state.updater = updater
    app.add_middleware(BodySizeLimitMiddleware, default_limit=body_limit,
                       overrides={**DEFAULT_BODY_OVERRIDES, **(body_limit_overrides or {})},
                       limit_for=_logo_limit)
    app.mount("/static", RevalidatingStaticFiles(directory=STATIC), name="static")

    # ---------------------------------------------------------- helpers
    def is_admin(request: Request) -> bool:
        return signer.valid(request.cookies.get(COOKIE))

    def require_admin(request: Request) -> None:
        if not is_admin(request):
            raise HTTPException(401, "Admin login required")

    def require_same_origin(request: Request) -> None:
        # Defence in depth against cross-site requests from a browser on
        # the show network (cookies are also SameSite=Strict).
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Cross-origin request refused")

    def dashboard_allows(slug: str, action: str) -> bool:
        d = hub.config.dashboard(slug)
        if d is None:
            return False
        return d.allow_marker if action == "marker" else d.allow_ack

    def set_session(response: Response) -> None:
        response.set_cookie(COOKIE, signer.issue(), max_age=SESSION_S, httponly=True,
                            samesite="strict")

    def esphome():
        integ = hub.integrations.get("esphome")
        if integ is None:
            raise HTTPException(404, "ESPHome integration not loaded")
        return integ

    # ------------------------------------------------------------ pages
    @app.get("/", include_in_schema=False)
    async def index():
        return versioned_page("index.html")

    @app.get("/d/{slug}", include_in_schema=False)
    async def dashboard_page(slug: str):
        if hub.config.dashboard(slug) is None:
            return RedirectResponse("/")
        return versioned_page("dashboard.html")

    @app.get("/admin", include_in_schema=False)
    async def admin_page():
        return versioned_page("admin.html")

    @app.get("/schedule", include_in_schema=False)
    async def schedule_page():
        # Static page; every API call it makes needs the admin session (it sends you to Admin's
        # login, and back, when there isn't one).
        return versioned_page("schedule.html")

    @app.get("/dashboard-editor", include_in_schema=False)
    async def dashboard_editor_page():
        # Static page (Edit cards for one dashboard); every API call it makes needs the admin session,
        # as on /schedule.
        return versioned_page("dashboard-editor.html")

    # -------------------------------------------------------- public API
    @app.get("/api/info")
    async def info(request: Request):
        return {
            "version": __version__,
            "build": {**build_info(), "channel": updater.effective_channel(),
                      "managed": updater.managed, "supervised": updater.supervised},
            "site": hub.config.site.name,
            "time": hub.site_time(),
            "schedule_warn": hub.schedule_warn(),
            "emulate": hub.emulate,
            "admin_setup_required": not hub.config.admin.pin_hash and not hub.store.recovery_required,
            "recovery_required": hub.store.recovery_required,
            "is_admin": is_admin(request),
            "dashboards": [d.model_dump() for d in hub.config.dashboards],
            "branding": public_branding(),
        }

    @app.get("/api/dashboard/{slug}")
    async def dashboard_config(slug: str):
        d = hub.config.dashboard(slug)
        if d is None:
            raise HTTPException(404, "No such dashboard")
        return d.model_dump()

    @app.get("/api/snapshot")
    async def snapshot():
        return hub.snapshot()

    @app.get("/api/schedule")
    async def get_schedule(stage: str | None = Query(None, max_length=sched.STAGE_MAX)):
        """The current show's running order (read-only, public: anyone on the show network can
        read it). ``stage`` keeps items for that stage plus items with no stage. ``now_next``
        holds item ids as of ``now``; dashboards count down themselves."""
        now = time.time()
        pub = hub.schedule.public(stage)
        nn = sched.now_next(pub["items"], now, None)
        ids = {k: (nn[k]["id"] if nn[k] else None) for k in ("current", "next", "curfew")}
        return {**pub, "now": now, "stage": (stage or "").strip(),
                "now_next": {"state": nn["state"], "current_id": ids["current"], "next_id": ids["next"],
                             "curfew_id": ids["curfew"], "seconds_to_curfew": nn["seconds_to_curfew"]}}

    @app.get("/api/history")
    async def history(entities: str, since: float | None = None, until: float | None = None,
                      points: int = 600):
        ids = [e for e in entities.split(",") if e in hub.entities][:20]
        since = since if since is not None else time.time() - 3600
        return hub.recorder.history(ids, since, until, max(10, min(points, 2000)))

    @app.get("/api/markers/{marker_id}/delta")
    async def marker_delta(marker_id: int):
        delta = hub.marker_delta(marker_id)
        if delta is None:
            raise HTTPException(404, "No such marker")
        return delta

    @app.post("/api/markers", dependencies=[Depends(require_same_origin)])
    async def add_marker(body: MarkerBody, request: Request):
        admin = is_admin(request)
        if not admin and not dashboard_allows(body.dashboard, "marker"):
            raise HTTPException(403, "Markers are not enabled on this dashboard")
        source = "admin" if admin else f"dashboard:{body.dashboard}"
        return hub.add_marker(body.label, source, note=body.note).to_dict()

    @app.patch("/api/markers/{marker_id}", dependencies=[Depends(require_same_origin)])
    async def patch_marker(marker_id: int, body: MarkerPatch, request: Request):
        """Edit a marker's note, or hide / un-hide it. Admin, or a dashboard allowed to add
        markers (any marker of the current show, not only its own). Deleting stays admin-only."""
        if not is_admin(request) and not dashboard_allows(body.dashboard, "marker"):
            raise HTTPException(403, "Markers are not enabled on this dashboard")
        if hub.marker_alarm_active(marker_id):
            raise HTTPException(409, "This marker belongs to an alarm that is still active")
        who = "admin" if is_admin(request) else f"dashboard:{body.dashboard}"
        marker = hub.update_marker(marker_id, note=body.note, hidden=body.hidden, source=who)
        if marker is None:
            raise HTTPException(404, "No such marker in the current show")
        return marker.to_dict()

    @app.post("/api/alarms/ack", dependencies=[Depends(require_same_origin)])
    async def ack(body: AckBody, request: Request):
        admin = is_admin(request)
        if not admin and not dashboard_allows(body.dashboard, "ack"):
            raise HTTPException(403, "Acknowledge is not enabled on this dashboard")
        return {"acked": hub.ack_alarms("admin" if admin else f"dashboard:{body.dashboard}")}

    # ------------------------------------------------------------- auth
    @app.post("/api/admin/setup", dependencies=[Depends(require_same_origin)])
    async def setup(body: PinBody, response: Response):
        if hub.config.admin.pin_hash:
            raise HTTPException(409, "Admin PIN already set")
        if hub.store.recovery_required:
            # The saved config was unreadable and its PIN could not be recovered: never let
            # whoever reaches the port claim admin.  Needs filesystem access (CLI / backup).
            raise HTTPException(409, "Recovery required: the saved settings could not be read. Restore "
                                     "config.yaml from a backup, or run 'stagewatch reset-admin-pin' "
                                     "on this computer.")
        hub.config.admin.pin_hash = hash_pin(body.pin)
        hub.save_config()
        set_session(response)
        return {"ok": True}

    async def check_pin(pin: str) -> None:
        """Verify the admin PIN; failures share the login rate limiter."""
        now = time.time()
        failed_logins[:] = [t for t in failed_logins if now - t < 60]
        if len(failed_logins) >= 5:
            raise HTTPException(429, "Too many attempts; wait a minute")
        if not hub.config.admin.pin_hash or not verify_pin(pin, hub.config.admin.pin_hash):
            failed_logins.append(now)
            await asyncio.sleep(1.0)
            raise HTTPException(401, "Wrong PIN")

    @app.post("/api/admin/login", dependencies=[Depends(require_same_origin)])
    async def login(body: PinBody, response: Response):
        await check_pin(body.pin)
        set_session(response)
        return {"ok": True}

    @app.post("/api/admin/logout")
    async def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.put("/api/admin/pin", dependencies=[Depends(require_admin), Depends(require_same_origin)])
    async def change_pin(body: ChangePinBody, response: Response):
        if not verify_pin(body.current, hub.config.admin.pin_hash):
            raise HTTPException(401, "Current PIN is wrong")
        hub.config.admin.pin_hash = hash_pin(body.new)
        hub.save_config()
        signer.rotate()
        set_session(response)
        return {"ok": True}

    # ------------------------------------------------------------ admin
    admin_deps = [Depends(require_admin), Depends(require_same_origin)]

    def spl_admin() -> dict:
        integ = hub.integrations.get("smaart")
        state = integ.admin_status() if integ is not None else {"running": False, "status": "off"}
        # The drop-downs come from what Smaart lists (state["inputs"] and state["metrics"], its own
        # text, empty until it has answered). The password itself is never sent, only whether it is set.
        return {"max_slots": spl_mod.MAX_SLOTS, "password_set": bool(hub.config.spl.password),
                "default_metrics": dict(spl_mod.LEGACY_SMAART_NAMES), "inputs": [], "metrics": [], **state}

    def shelly_integ():
        integ = hub.integrations.get("shelly")
        if integ is None:
            raise HTTPException(404, "Shelly integration not loaded")
        return integ

    def shelly_admin() -> dict:
        integ = hub.integrations.get("shelly")
        return integ.admin_status() if integ is not None else {"meters": [], "transport": "", "max_meters": 0, "emulate": hub.emulate}

    def globcon_admin() -> dict:
        integ = hub.integrations.get("globcon")
        state = integ.admin_status() if integ is not None else {"running": False, "status": "off"}
        extra = integ.admin_detail() if integ is not None and hasattr(integ, "admin_detail") else {}
        return {"password_set": bool(hub.config.globcon.password), **state, **extra}

    @app.get("/api/admin/state", dependencies=[Depends(require_admin)])
    async def admin_state():
        cfg = hub.config.model_dump(mode="json")
        cfg.pop("admin", None)
        if "contacts" in cfg:   # the reveal PIN: only "set / not set"
            cfg["contacts"]["reveal_pin_set"] = bool(cfg["contacts"].pop("reveal_pin_hash", ""))
        cfg.get("globcon", {}).pop("password", None)   # GLOBCON's controller password: only "set / not set" (globcon.password_set)
        for dev in cfg.get("esphome_devices", []):
            dev["noise_psk"] = "set" if dev.get("noise_psk") else ""
        cfg.get("spl", {}).pop("password", None)   # Smaart's API password: only "set / not set" (spl.password_set)
        esp =hub.integrations.get("esphome")
        return {
            "config": cfg,
            "emulate": hub.emulate,
            "hardware": hardware_state(esp),
            "integrations": [i.info() for i in hub.integrations.values()],
            "actions": hub.actions.status(),   # OSC actions: armed or off (admin only)
            "discovered": esp.discovered_list() if esp else [],
            "ignored": esp.ignored_list() if esp else [],
            "shows": hub.recorder.shows()[:SHOWS_MAX],
            # Event & show card: the current event and show (day resolved in site time), all
            # events with their shows, and the dates the Next day / New event dialogs suggest.
            "event": hub.recorder.current_event(),
            "show": hub.show_info(),
            "events": events_with_shows(),
            "contact_reminders": len(contact_reminders()),   # a count only: opens the Onsite contacts card
            "show_days": hub.show_days(),
            "alarm_log": hub.recorder.alarm_log(),
            # Wall Clock card: is the source running, and what is it saying (no addresses).
            "wall_clock": hub.wall_clock.admin_status(),
            "ontime_timer": hub.ontime_timer.admin_status(),
            "ontime_rundown": hub.ontime_rundown.admin_status(),
            # GLOBCON levels card: is it in use and what is running (no addresses, no password).
            "globcon": globcon_admin(),
            # Shelly power meters card: the meters (addresses, MACs and firmware are admin-only), no passwords exist.
            "shelly": shelly_admin(),
            # Sound level card: the values that can be chosen, and what is running (no addresses).
            "spl": spl_admin(),
            # For the "Edit cards" panel: the cards this build knows, in picker order, and the
            # defaults a new dashboard gets for each layout.
            "cards": {"known": list(cards_mod.KNOWN_CARDS), "half_capable": list(cards_mod.HALF_CAPABLE),
                      "defaults": {layout: cards_mod.default_cards(layout) for layout in cards_mod.LAYOUT_DEFAULTS}},
            "stages": known_stages(),
            # For the Schedule card (WP8): the limits the server enforces, and whether
            # "Load demo day" is allowed right now.
            "schedule_limits": {
                "kinds": list(sched.KINDS), "marker_kinds": list(sched.MARKER_KINDS),
                "max_items": sched.MAX_ITEMS, "title_max": sched.TITLE_MAX,
                "stage_max": sched.STAGE_MAX, "setlist_max_bytes": sched.SETLIST_MAX_BYTES,
                "setlist_total_max_bytes": sched.SETLIST_TOTAL_MAX_BYTES,
                "import_max_bytes": sched.IMPORT_MAX_BYTES, "import_max_rows": sched.IMPORT_MAX_ROWS,
                "demo_allowed": demo_allowed(),
                "actions": {"max_args": actions_mod.MAX_ARGS, "max_targets": actions_mod.MAX_TARGETS,
                            "address_max": actions_mod.ADDRESS_MAX, "string_max": actions_mod.STRING_MAX,
                            "arg_types": ["i", "f", "s"], "icons": list(actions_mod.ICONS),
                            "placeholders": list(actions_mod.PLACEHOLDERS),
                            "jitter_s": actions_mod.JITTER_S},
            },
        }

    def hardware_state(esp) -> dict:
        """Admin only (never in the public snapshot): which hardware each sensor and device is,
        and devices held in FAULT by the hardware check (with the MACs and other device id that
        the public status text leaves out)."""
        conflicts = esp.hardware_conflicts() if esp is not None and hasattr(esp, "hardware_conflicts") else {}
        where = esp.node_addresses() if esp is not None and hasattr(esp, "node_addresses") else {}
        sensors = [e for e in hub.entities.values() if not e.derived]
        return {
            "entities": {e.id: e.hw_key for e in sensors if e.hw_key},
            # The calibration that applies to each sensor now (hardware record, else legacy entry,
            # else defaults): the admin card shows these, so an offset kept only in a hardware
            # record (no legacy mirror) is never shown as 0.
            "settings": {e.id: {"offset": (c := hub.calibration_for(e)).offset,
                                "include_in_average": c.include_in_average,
                                "accuracy": c.accuracy, "accuracy_basis": c.accuracy_basis,
                                "role": c.role,          # the sensor's own override ("" = follows the node)
                                "role_now": hub.role_of(e)} for e in sensors},
            # Calibration log per sensor (newest first): date, method, offset, reference, note.
            "calibrations": {e.id: [h.model_dump(mode="json") for h in cal.history]
                             for e in sensors if e.hw_key and (cal := hub.config.calibrations.get(e.hw_key))},
            # Each sensor's live share of its kind's site average, and why weighting is not in use.
            "averages": hub.average_info,
            "devices": {d.id: {"hw_id": d.hw_id, "conflict": conflicts.get(d.id), "role": d.role,
                               "sleep_minutes": d.sleep_minutes,
                               "host": where.get(d.id, {}).get("host", ""),
                               "address": where.get(d.id, {}).get("address", "")}
                        for d in hub.devices.values() if d.id != "site"},
        }

    def known_stages() -> list[str]:
        """Stage names already in use, for the dashboard Stage field's suggestions: device areas
        and other dashboards' stages, and the stages in the current show's schedule."""
        names = [d.area for d in hub.devices.values() if d.id != "site"]
        names += hub.schedule.stages()
        names += [d.area for d in hub.config.esphome_devices]
        names += [d.stage for d in hub.config.dashboards]
        seen: dict[str, str] = {}
        for n in names:
            n = (n or "").strip()
            if n and len(n) <= 40 and n.casefold() not in seen:
                seen[n.casefold()] = n
        return sorted(seen.values(), key=str.casefold)[:100]

    @app.put("/api/admin/site", dependencies=admin_deps)
    async def put_site(body: SiteConfig):
        if "schedule_auto_markers" not in body.model_fields_set:
            # Set on the schedule page; the Site card doesn't send it, so keep it as it is.
            body = body.model_copy(update={"schedule_auto_markers": hub.config.site.schedule_auto_markers})
        # Callers that don't send the warning times keep what is set.
        keep = {k: getattr(hub.config.site, k)
                for k in ("schedule_warn_minutes", "schedule_warn_flash_minutes", "weight_by_accuracy")
                if k not in body.model_fields_set}
        if keep:
            body = body.model_copy(update=keep)
        hub.set_site(body)  # a time zone (or show day) change re-bases the schedule
        hub.save_config()
        return body

    @app.post("/api/admin/esphome/adopt", dependencies=admin_deps)
    async def adopt(body: AdoptBody):
        device_id = slugify(body.id or body.name or body.host.split(".")[0])
        if device_id == "site" or device_id in hub.devices:
            raise HTTPException(409, f"Device id '{device_id}' is already in use")
        cfg = EsphomeDeviceConfig(id=device_id, host=body.host.strip(), port=body.port,
                                  name=body.name.strip(), area=body.area.strip(),
                                  noise_psk=body.noise_psk.strip(), role=body.role)
        try:
            warning = await esphome().adopt(cfg)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": cfg.id, "warning": warning} if warning else {"id": cfg.id}

    @app.post("/api/admin/esphome/{device_id}/resolve", dependencies=admin_deps)
    async def resolve_hardware(device_id: str, body: ResolveBody):
        try:
            await esphome().resolve_hardware(device_id, body.action)
        except LookupError:
            raise HTTPException(409, RESOLVE_REFUSED) from None
        return {"ok": True}

    @app.post("/api/admin/esphome/ignore", dependencies=admin_deps)
    async def ignore_discovered(body: IgnoreBody):
        try:
            found = esphome().ignore(body.key)
        except ValueError:
            raise HTTPException(409, "The ignore list is full. Unignore some nodes first") from None
        if not found:
            raise HTTPException(404, "That node is not on the network any more")
        return {"ok": True}

    @app.post("/api/admin/esphome/unignore", dependencies=admin_deps)
    async def unignore_discovered(body: IgnoreBody):
        if not esphome().unignore(body.key):
            raise HTTPException(404, "That node is not in the ignore list")
        return {"ok": True}

    @app.patch("/api/admin/devices/{device_id}", dependencies=admin_deps)
    async def patch_device(device_id: str, body: DevicePatch):
        if device_id not in hub.devices or device_id == "site":
            raise HTTPException(404, "No such device")
        if hub.devices[device_id].category in ("service", "meter", "host"):
            raise HTTPException(409, SERVICE_DEVICE)
        esp = esphome()
        if (body.host is not None or body.port is not None) and esp.config_of(device_id) is None:
            raise HTTPException(409, "This device has no network address to change")
        await esp.update(device_id, body.name, body.area, body.host, body.port, body.role, body.sleep_minutes)
        return {"ok": True}

    @app.delete("/api/admin/devices/{device_id}", dependencies=admin_deps)
    async def delete_device(device_id: str):
        if device_id not in hub.devices or device_id == "site":
            raise HTTPException(404, "No such device")
        if hub.devices[device_id].category in ("service", "meter", "host"):
            raise HTTPException(409, SERVICE_DEVICE)
        await esphome().remove(device_id)
        return {"ok": True}

    @app.put("/api/admin/entities/{entity_id}", dependencies=admin_deps)
    async def put_entity(entity_id: str, body: EntitySettings):
        if entity_id not in hub.entities:
            raise HTTPException(404, "No such entity")
        entity = hub.entities[entity_id]
        if entity.kind == Kind.SOUND_LEVEL:
            raise HTTPException(409, "Sound levels are recorded exactly as received and cannot be adjusted. "
                                     "Nothing has been changed.")
        if entity.locked_equipment:
            raise HTTPException(409, "Power meter readings are recorded exactly as received and are always "
                                     "Equipment, so they cannot be adjusted. Nothing has been changed.")
        # Accuracy: only for the three kinds that make the site average, within a sane limit for
        # the kind. Fixed text; the submitted number is never echoed.
        extra = {}
        if "accuracy" in body.model_fields_set or "accuracy_basis" in body.model_fields_set:
            limit = ACCURACY_MAX.get(entity.kind.value)
            if body.accuracy is not None and (limit is None or entity.derived):
                raise HTTPException(422, "Accuracy can only be set for temperature, humidity and pressure sensors")
            if body.accuracy is not None and body.accuracy > limit:
                raise HTTPException(422, "That accuracy figure is too large for this kind of sensor")
            if body.accuracy is not None and body.accuracy < ACCURACY_MIN[entity.kind.value]:
                raise HTTPException(422, "That accuracy figure is too small for this kind of sensor")
            if "accuracy" in body.model_fields_set:
                extra["accuracy"] = body.accuracy
            if "accuracy_basis" in body.model_fields_set:
                extra["accuracy_basis"] = body.accuracy_basis
        if "role" in body.model_fields_set:
            if entity.derived:
                raise HTTPException(422, "A role can only be set for a sensor")
            extra["role"] = body.role
        # Hardware record when the sensor's board is known (a "manual" history entry when the
        # offset changes), else the legacy entry keyed by entity id.
        before = hub.calibration_for(entity)
        old_offset, old_included = before.offset, before.include_in_average
        set_calibration(hub.config, entity_id, entity.hw_key, body.offset, body.include_in_average, **extra)
        hub.save_config()
        # One system marker per save that really changed the offset or the average setting, so the
        # crew can see why a site value stepped. Equipment is never averaged, so its tick is no change.
        if hub.role_of(entity) != "environment":
            old_included = body.include_in_average
        text = change_text(entity.name, entity.unit, old_offset, body.offset, old_included, body.include_in_average)
        if text:
            hub.add_marker(text, "hub")
        if entity.raw_value is not None and not entity.derived:
            hub.update_state(entity_id, entity.raw_value, entity.updated)
        else:
            hub.bus.publish("entity", entity)   # screens still learn the new offset
        return body

    @app.post("/api/admin/calibrations/{entity_id}/note", dependencies=admin_deps)
    async def add_calibration_note(entity_id: str, body: CalibrationNoteBody):
        """Log a check against a reference, or a note, on a sensor's calibration (offset unchanged)."""
        entity = hub.entities.get(entity_id)
        cal = hub.config.calibrations.get(entity.hw_key) if entity is not None and entity.hw_key else None
        if cal is None:
            raise HTTPException(404, "This sensor has no saved calibration to add a note to. Save an offset first.")
        reference, note = body.reference.strip(), body.note.strip()
        if not reference and not note:
            raise HTTPException(422, "Type a reference or a note")
        if _has_hidden_chars(reference) or _has_hidden_chars(note):
            raise HTTPException(422, "Notes cannot contain control characters")
        entry = CalibrationEntry(offset=cal.offset, date=utc_now_iso(), method="reference" if reference else "manual",
                                 reference=reference, note=note)
        hub.config.calibrations[entity.hw_key] = cal.model_copy(
            update={"history": [entry, *cal.history][:CALIBRATION_HISTORY_MAX]})
        hub.save_config()
        return {"ok": True}

    @app.get("/api/admin/calibrations/export", dependencies=[Depends(require_admin)])
    async def export_calibrations():
        data = json.dumps(calibration_io.build_export(hub.config, hub.config.site.name), indent=2).encode()
        return Response(data, media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="stagewatch-calibration.json"',
            "Content-Length": str(len(data)), "Cache-Control": "no-store"})

    @app.post("/api/admin/calibrations/import", dependencies=admin_deps)
    async def import_calibrations(body: CalibrationImportBody):
        nodes = {k.rsplit("/", 1)[0] for k in (e.hw_key for e in hub.entities.values()) if k}
        nodes |= {f"mac:{d.mac}" for d in hub.config.esphome_devices if d.mac}
        by_key = {e.hw_key: e for e in hub.entities.values() if e.hw_key}
        def refuse(key: str) -> bool:
            e = by_key.get(key)
            return e is not None and (e.kind == Kind.SOUND_LEVEL or e.locked_equipment)
        def acceptable(key: str, cal) -> bool:
            # The limits the normal save enforces: a sane offset, accuracy only where it is allowed.
            e = by_key.get(key)
            kind = e.kind.value if e is not None else ""
            if abs(cal.offset) > OFFSET_MAX.get(kind, OFFSET_MAX[""]):
                return False
            if cal.accuracy is not None and e is not None:
                lo, hi = ACCURACY_MIN.get(kind), ACCURACY_MAX.get(kind)
                if lo is None or hi is None or not lo <= cal.accuracy <= hi:
                    return False
            return True
        try:
            plan = calibration_io.plan_import(hub.config, body.data, nodes, refuse, acceptable)
        except calibration_io.ImportError_ as exc:
            raise HTTPException(422, str(exc)) from None
        result = {"dry_run": body.dry_run, **plan.summary()}
        if body.dry_run or not plan.records:
            return result
        written = calibration_io.apply_import(hub.config, plan)
        for key in written:   # keep the rollback mirror in step and refresh live values
            e = by_key.get(key)
            if e is None:
                continue
            if e.id in hub.config.entities:
                cal = hub.config.calibrations[key]
                hub.config.entities[e.id] = EntitySettings(offset=cal.offset, include_in_average=cal.include_in_average)
        hub.save_config()
        hub.add_marker(f"Calibration imported from a file: {len(plan.added)} added, {len(plan.changed)} changed", "hub")
        for key in written:
            e = by_key.get(key)
            if e is not None and e.raw_value is not None and not e.derived:
                hub.update_state(e.id, e.raw_value, e.updated)
        return result

    @app.get("/api/admin/averages", dependencies=[Depends(require_admin)])
    async def admin_averages():
        """Admin only: each sensor's share of the site average now (the page refreshes it live)."""
        return {"weight_by_accuracy": hub.config.site.weight_by_accuracy, "averages": hub.average_info}

    @app.put("/api/admin/thresholds", dependencies=admin_deps)
    async def put_thresholds(body: list[Threshold]):
        ids = [t.id for t in body]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Threshold ids must be unique")
        old = {t.id: t for t in hub.config.thresholds}
        targets = {t.id for t in hub.config.actions.targets}
        for t in body:
            if "actions" not in t.model_fields_set:  # an older page keeps the threshold's actions
                if t.id in old:
                    t.actions = old[t.id].actions
                continue
            try:
                actions_mod.ThresholdActionsBody.model_validate(t.actions.model_dump())
            except ValueError:
                raise HTTPException(422, "Check the threshold's actions: " + actions_mod.MSG_ADDRESS) from None
            if any(m is not None and m.target_id not in targets
                   for m in (t.actions.raised, t.actions.cleared, t.actions.acked)):
                raise HTTPException(422, "Check the threshold's actions: " + actions_mod.MSG_TARGET)
        hub.config.thresholds = body
        hub.save_config()
        return body

    @app.put("/api/admin/dashboards", dependencies=admin_deps)
    async def put_dashboards(body: list[DashboardBody]):
        slugs = [d.slug for d in body]
        if not body or len(slugs) != len(set(slugs)):
            raise HTTPException(422, "Need at least one dashboard, with unique slugs")
        saved = []
        for d in body:
            existing = hub.config.dashboard(d.slug)
            data = d.model_dump()
            # A save that doesn't send cards/stage/clock_style/card_sizes (today's admin page) keeps what the
            # dashboard has; only a new dashboard gets its layout's default cards.
            for key in ("cards", "stage", "clock_style", "card_sizes", "globcon", "show_dew_point",
                        "show_contact_phones", "contacts_area"):
                if key not in d.model_fields_set and existing is not None:
                    data[key] = getattr(existing, key)
            if "cards" not in d.model_fields_set and existing is None:
                data.pop("cards")
            saved.append(Dashboard.model_validate(data))
        body = saved
        hub.config.dashboards = body
        hub.save_config()
        return body

    @app.put("/api/admin/osc", dependencies=admin_deps)
    async def put_osc(body: OscOutConfig):
        hub.config.osc_out = body
        hub.save_config()
        return body

    # Wall Clock (Ontime). Saving re-checks the source: the config topic reaches the Wall Clock
    # service, which restarts it if the address changed. Read-only toward Ontime.
    @app.put("/api/admin/wall-clock", dependencies=admin_deps)
    async def put_wall_clock(body: WallClockConfig):
        hub.config.wall_clock = body
        hub.save_config()
        return body

    # Ontime Timer card options (today only: show the event title). The address is the Wall
    # Clock's. Saving goes through the config topic like every save.
    @app.put("/api/admin/ontime-timer", dependencies=admin_deps)
    async def put_ontime_timer(body: OntimeTimerBody):
        hub.config.ontime_timer = OntimeTimerConfig(show_title=body.show_title)
        body = hub.config.ontime_timer
        hub.save_config()
        return body
    # Alarm notice timers (dashboard list only; the state behind a notice is untouched).
    @app.put("/api/admin/alarms", dependencies=admin_deps)
    async def put_alarms(body: AlarmsBody):
        hub.config.alarms = AlarmsConfig(**body.model_dump())
        hub.save_config()
        hub.bus.publish("alarms", hub.alarm_notices())   # open screens take the new timers at once
        return hub.config.alarms

    # Barometer card settings (hemisphere, optional silent rapid-fall notice).
    @app.put("/api/admin/barometer", dependencies=admin_deps)
    async def put_barometer(body: BarometerBody):
        hub.config.barometer = BarometerConfig(**body.model_dump())
        hub.save_config()
        return hub.config.barometer

    # Sound level (Smaart): which values to record (up to three) and where Smaart is. Read-only
    # toward Smaart. The running source is brought in line before the save is announced, so open
    # screens reload into the new state.
    @app.put("/api/admin/spl", dependencies=admin_deps)
    async def put_spl(body: SplBody):
        old = hub.config.spl
        extra: dict = {}
        if body.meters is None:
            err = spl_mod.slots_error(body.slots)
            if err:
                raise HTTPException(422, err)
            extra = {"slots": body.slots}
        else:
            pairs = [(m.source, m.metric) for m in body.meters]
            if any(spl_mod.clean_metric_name(m) != m or spl_mod.clean_input_name(s) != s for s, m in pairs):
                raise HTTPException(422, "A name has characters that cannot be used")
            if len(set(pairs)) != len(pairs):
                raise HTTPException(422, "Each value can only be chosen once")
            extra = {"meters": [SplSlot(metric=m, source=s) for s, m in pairs],
                     "slots": spl_mod.legacy_keys_for(pairs)}
        password = old.password
        if body.clear_password:
            if body.password:
                raise HTTPException(422, "Either enter a new password or clear it, not both")
            password = ""
        elif body.password:
            if spl_password_error(body.password):
                raise HTTPException(422, "The password must be 1 to 128 characters, with no control characters")
            password = body.password
        def tidy_location(text: str) -> str:
            if _has_hidden_chars(text):   # before any tidying: a tab or newline is refused, not rewritten
                raise HTTPException(422, "A location can't contain hidden or control characters")
            text = " ".join(text.split())
            if len(text) > spl_mod.LOCATION_MAX:
                raise HTTPException(422, f"A location can be up to {spl_mod.LOCATION_MAX} characters")
            return text

        location = old.location if body.location is None else tidy_location(body.location)
        locations = dict(old.locations)
        if body.locations is not None:
            locations = {}
            for name, label in body.locations.items():
                if spl_mod.clean_input_name(name) != name or not name:
                    raise HTTPException(422, "An input name has characters that cannot be used")
                label = tidy_location(label)
                if label:
                    locations[name] = label
            if len(locations) > spl_mod.LOCATIONS_MAX:
                raise HTTPException(422, f"Locations can be set for up to {spl_mod.LOCATIONS_MAX} inputs")
        chart_range = old.chart_range if body.chart_range is None else body.chart_range
        chart_min = old.chart_min_db if body.chart_min_db is None else body.chart_min_db
        chart_max = old.chart_max_db if body.chart_max_db is None else body.chart_max_db
        if body.chart_min_db is not None or body.chart_max_db is not None or chart_range == "custom":
            err = spl_mod.chart_range_error(chart_min, chart_max)   # on the raw numbers: a huge whole number is refused, not converted
            if err:
                raise HTTPException(422, err)
        chart_min, chart_max = float(chart_min), float(chart_max)
        try:
            # an empty port means Smaart's usual one (the page shows 26000 as its placeholder)
            new = SplConfig(enabled=body.enabled, host=body.host, port=body.port or 26000, password=password,
                            location=location, locations=locations,
                            chart_range=chart_range, chart_min_db=chart_min, chart_max_db=chart_max,
                            limits_alert=old.limits_alert, **extra)
        except ValidationError:
            raise HTTPException(422, "The address must be a host name or an IP address on the local network") from None
        if new.enabled and not hub.emulate and not (new.host and new.port):
            raise HTTPException(422, "Enter the address and port of the Smaart computer first")
        hub.config.spl = new
        integ = hub.integrations.get("smaart")
        if integ is not None:
            try:
                # A password sent with the save (even the same one) or a refused one earlier means
                # "try logging in again now": the client does not retry a refused password by itself.
                again = bool(body.password) or integ.admin_status().get("problem") == "wrong_password"
                await integ.apply(restart=again)
            except Exception:  # noqa: BLE001 - the settings are saved either way
                log.exception("Could not apply the sound level settings")
        hub.save_config()
        n = len(new.effective_slots())
        log.info("Sound level settings saved (%s, %d value%s, password %s)", "on" if new.enabled else "off", n,
                 "" if n == 1 else "s", "set" if new.password else "not set")
        return {"ok": True, "changed": old != new, "password_set": bool(new.password)}

    # GLOBCON levels card: where GLOBCON is. Read-only toward GLOBCON. Saving goes through the config
    # topic like every save; the integration restarts its connection when the address or password changed.
    @app.put("/api/admin/globcon", dependencies=admin_deps)
    async def put_globcon(body: GlobconBody):
        old = hub.config.globcon
        host = body.host.strip()
        if not host or spl_host_error(host):
            raise HTTPException(422, "The address must be a host name or an IP address on this computer or the local network")
        password = old.password
        if body.clear_password:
            if body.password:
                raise HTTPException(422, "Either enter a new password or clear it, not both")
            password = ""
        elif body.password:
            if spl_password_error(body.password):
                raise HTTPException(422, "The password must be 1 to 128 characters, with no control characters")
            password = body.password
        hub.config.globcon = GlobconConfig(host=host, port=body.port, password=password)
        hub.save_config()
        log.info("GLOBCON settings saved (password %s)", "set" if password else "not set")
        return {"ok": True, "changed": old != hub.config.globcon, "password_set": bool(password)}

    # Shelly power meters. Read-only toward the meters: Stagewatch asks for readings (two GET requests,
    # see integrations/shelly/http.py) and sends nothing else. Addresses must be on a private network.
    shelly_calls: list[float] = []

    def shelly_rate_limit() -> None:
        """At most 10 Test or Save requests a minute: each one makes the server contact an address."""
        now = time.time()
        shelly_calls[:] = [t for t in shelly_calls if now - t < 60]
        if len(shelly_calls) >= 10:
            raise HTTPException(429, "Too many requests. Wait a minute and try again. Nothing has been changed.")
        shelly_calls.append(now)

    @app.post("/api/admin/shelly/test", dependencies=admin_deps)
    async def shelly_test(body: ShellyTestBody):
        shelly_rate_limit()
        """Look at a meter without saving it: model, firmware, what it measures and live readings.
        Fixed-text result; nothing the meter says is repeated."""
        try:
            return {"ok": True, **await shelly_integ().test(body.host)}
        except ShellyError as exc:
            return {"ok": False, "category": exc.category, "message": SHELLY_TEXT.get(exc.category, SHELLY_TEXT["unreachable"])}

    @app.post("/api/admin/shelly/add", dependencies=admin_deps)
    async def shelly_add(body: ShellyAddBody):
        shelly_rate_limit()
        try:
            cfg = await shelly_integ().add(body.host, body.name, body.area, body.interval_s)
        except ShellyError as exc:
            raise HTTPException(422, SHELLY_TEXT.get(exc.category, SHELLY_TEXT["unreachable"])) from None
        except InUse as exc:
            raise HTTPException(409, f"{exc}. Nothing has been changed.") from None
        return {"id": cfg.id}

    @app.patch("/api/admin/shelly/{device_id}", dependencies=admin_deps)
    async def shelly_patch(device_id: str, body: ShellyPatchBody):
        try:
            await shelly_integ().update(device_id, body.name, body.area, body.interval_s, body.host, body.rating_a,
                                       None if body.channels is None else [c.model_dump() for c in body.channels])
        except LookupError:
            raise HTTPException(404, "No such power meter") from None
        except ShellyError as exc:
            raise HTTPException(422, f"{SHELLY_TEXT.get(exc.category, SHELLY_TEXT['unreachable'])} Nothing has been changed.") from None
        return {"ok": True}

    @app.post("/api/admin/shelly/{device_id}/warnings", dependencies=admin_deps)
    async def shelly_warnings(device_id: str):
        """Make the 80 % and 100 % load warnings from the rating typed in (ordinary thresholds)."""
        try:
            return {"ok": True, "made": shelly_integ().apply_warnings(device_id)}
        except LookupError:
            raise HTTPException(404, "No such meter")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.delete("/api/admin/shelly/{device_id}", dependencies=admin_deps)
    async def shelly_delete(device_id: str):
        try:
            await shelly_integ().remove(device_id)
        except LookupError:
            raise HTTPException(404, "No such power meter") from None
        return {"ok": True}

    # ------------------------------------------------------- sound level limits
    def _clean_line(text: str, cap: int) -> str:
        return " ".join(spl_mod._clean_text(text, cap).split())

    @app.get("/api/spl-limits")
    async def get_spl_limits(stage: str | None = Query(None, max_length=sched.STAGE_MAX)):
        """Public: the limits in force now, the next change, and who set them (public wording only:
        no admin data, no addresses). The schedule's curfew rides along for the Limits card."""
        now = time.time()
        out = hub.spl_limits.public(now)
        try:
            nn = sched.now_next(hub.schedule.public(stage)["items"], now, None)
            cur = nn.get("curfew")
            out["curfew_ts"] = cur["planned_start"] if cur else None
        except Exception:  # noqa: BLE001 - the curfew line is a nicety
            out["curfew_ts"] = None
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    def _limits_admin() -> dict:
        rec = hub.recorder
        ev = rec._spl_latest(rec.event_id, None)
        doc = json.loads(ev[5]) if ev and ev[5] else None
        sh = rec._spl_latest(rec.event_id, rec.show_id)
        sdoc = json.loads(sh[5]) if sh and sh[5] else None
        pairs = hub.config.spl.effective_slots()
        return {
            "event_id": rec.event_id, "show_id": rec.show_id,
            "event_set": doc, "show_set": sdoc, "override_active": sdoc is not None,
            "copied": bool(ev and ev[4]) and not sdoc,
            "slots": [{"source": s, "metric": m} for s, m in pairs],
            "events": [{"id": e["id"], "name": e["name"]} for e in rec.events() if e["id"] != rec.event_id][:50],
            "revisions": rec.spl_limits_revisions(rec.event_id, 20),
            "settings": hub.config.spl.limits_alert.model_dump(),
            "max_limits": limits_mod.MAX_LIMITS,
        }

    @app.get("/api/admin/spl-limits", dependencies=[Depends(require_admin)])
    async def admin_spl_limits():
        return JSONResponse(_limits_admin(), headers={"Cache-Control": "no-store"})

    @app.get("/api/admin/spl-limits/revisions", dependencies=[Depends(require_admin)])
    async def admin_spl_limit_revisions():
        return {"revisions": hub.recorder.spl_limits_revisions(hub.recorder.event_id, 50)}

    @app.put("/api/admin/spl-limits", dependencies=admin_deps)
    async def put_spl_limits(body: SplLimitsBody):
        err = limits_mod.set_errors(body.limit_set, hub.config.site)
        if err:
            raise HTTPException(422, err)
        rec = hub.recorder
        show_id = rec.show_id if body.scope == "show" else None
        latest = rec._spl_latest(rec.event_id, show_id)
        doc = limits_mod.dump_set(body.limit_set)
        if latest is not None and latest[5] == doc and not latest[4]:
            return {"ok": True, "rev": latest[0], "unchanged": True}   # same set again: no revision, no marker
        rev = rec.spl_limits_save(rec.event_id, show_id, limits_mod.dump_set(body.limit_set),
                                  _clean_line(body.entered_by, limits_mod.ENTERED_BY_MAX),
                                  _clean_line(body.change_note, limits_mod.CHANGE_NOTE_MAX))
        hub.spl_limits.changed(_clean_line(body.change_note, limits_mod.CHANGE_NOTE_MAX))
        log.info("Sound level limits saved (rev %d, %s, %d limit%s)", rev, body.scope, len(body.limit_set.limits),
                 "" if len(body.limit_set.limits) == 1 else "s")
        return {"ok": True, "rev": rev}

    @app.delete("/api/admin/spl-limits/override", dependencies=admin_deps)
    async def delete_spl_limits_override():
        rec = hub.recorder
        if not rec.spl_limits_override_active(rec.event_id, rec.show_id):
            return {"ok": True, "changed": False}
        rev = rec.spl_limits_save(rec.event_id, rec.show_id, None, "", "Back to the event's limits")
        hub.spl_limits.changed("back to the event's limits")
        return {"ok": True, "changed": True, "rev": rev}

    @app.post("/api/admin/spl-limits/copy", dependencies=admin_deps)
    async def copy_spl_limits(body: SplLimitsCopyBody):
        """Copy another event's limits as this event's default, flagged "copied, not confirmed"
        until the next save."""
        rec = hub.recorder
        if body.from_event_id == rec.event_id or not rec.event_exists(body.from_event_id):
            raise HTTPException(404, "No such event")
        row = rec.spl_limits_effective(body.from_event_id, None)
        if row is None:
            raise HTTPException(409, "That event has no sound level limits")
        try:
            got = limits_mod.SplLimitSet.model_validate(row["set"])
        except ValidationError:
            raise HTTPException(409, "That event's limits could not be read") from None
        if limits_mod.set_errors(got, hub.config.site):
            raise HTTPException(409, "That event's limits do not fit this site's show day; enter them again")
        rev = rec.spl_limits_save(rec.event_id, None, limits_mod.dump_set(got), "", "Copied from another event",
                                  copied_from_event=body.from_event_id)
        hub.spl_limits.changed("copied from another event, not yet confirmed")
        return {"ok": True, "rev": rev}

    @app.put("/api/admin/spl-limits/settings", dependencies=admin_deps)
    async def put_spl_limits_settings(body: SplLimitsSettingsBody):
        from ..core.config import SplLimitsAlert
        hub.config.spl.limits_alert = SplLimitsAlert(**body.model_dump())
        hub.save_config()
        hub.spl_limits.tick()
        return {"ok": True}

    @app.post("/api/admin/spl/refresh", dependencies=admin_deps)
    async def refresh_spl():
        """The Refresh button on the Sound level card: ask Smaart for its input and metric names again.
        No body. Fixed-text result only (no addresses, passwords or Smaart text). Changes nothing in
        Smaart; the drop-downs read the new lists from /api/admin/state."""
        integ = hub.integrations.get("smaart")
        if integ is None:
            raise HTTPException(409, "Sound level is not switched on")
        done, text = await integ.refresh_catalog()
        if not done:
            raise HTTPException(429 if text == "Refreshed too recently" else 409, text)
        return {"ok": True, "result": text}

    @app.post("/api/admin/barometer/demo", dependencies=admin_deps)
    async def barometer_demo(body: BaroDemoBody):
        """Emulate only: start a weather scenario (the card is complete at once)."""
        if not hub.emulate:
            raise HTTPException(409, "Demo weather only works in emulate mode. Nothing has been changed.")
        hub.baro.start_demo(body.scenario, time.time())
        hub.bus.publish("config", None)   # open screens reload: a sensor may have appeared or gone
        return {"ok": True}

    @app.post("/api/admin/site/altitude-from-pressure", dependencies=admin_deps)
    async def altitude_from_pressure(body: AltitudeFromPressureBody):
        """Preview only, nothing is saved: the altitude that makes the barometer read the sea-level
        pressure the admin typed, with the same reduction the card uses (so saving it makes the
        dial show that figure). The admin page then saves it with PUT /api/admin/site."""
        if not QNH_MIN_HPA <= body.qnh_hpa <= QNH_MAX_HPA:
            raise HTTPException(422, "Sea-level pressure must be between 940 and 1,060 hPa. Nothing has been changed.")
        now = time.time()
        site = hub.site_meta
        entity = hub.entities.get("site.pressure")
        if site.get("pressure_source") != "measured" or entity is None or entity.value is None:
            raise HTTPException(409, "No pressure sensor reading, so there is nothing to work from. Nothing has been changed.")
        age = now - (entity.updated or 0.0)
        if age > hub.config.site.stale_after_s:
            raise HTTPException(409, f"The pressure reading is {round(age)} s old. Wait for a fresh reading. Nothing has been changed.")
        station_pa = entity.value
        if not baro_mod.plausible_pressure(station_pa):
            raise HTTPException(409, "The pressure reading is not plausible. Check the pressure sensors. Nothing has been changed.")
        temp = hub.baro.temp_mean_c(now)
        altitude = acoustics.altitude_from_msl_pa(station_pa, body.qnh_hpa * 100.0, temp)
        lo, hi = ALTITUDE_RANGE_M
        if not (isinstance(altitude, float) and altitude == altitude and lo <= altitude <= hi):
            raise HTTPException(422, "That pressure gives an altitude outside -500 to 6,000 m. Check the figure. Nothing has been changed.")
        altitude_m = round(altitude)
        warnings: list[str] = []
        if altitude_m > 2000 or altitude_m < -50:
            warnings.append("This altitude is unusual for an event site. Check the figure you typed.")
        current = hub.config.site.altitude_m
        if abs(altitude_m - current) > 50:
            warnings.append("This differs from the saved altitude by more than 50 m. A slip of 1 hPa is about 8 m.")
        values = [e.value for e in hub._env_inputs(Kind.PRESSURE, now)]
        spread = (max(values) - min(values)) / 100.0 if len(values) > 1 else 0.0
        if spread > 2.0:
            warnings.append("The pressure sensors disagree by more than 2 hPa. Check them first.")
        return {"altitude_m": altitude_m, "current_altitude_m": current, "station_hpa": round(station_pa / 100.0, 1),
                "station_age_s": round(age), "sensors_used": len(values), "spread_hpa": round(spread, 1),
                "warnings": warnings}

    test_lock = asyncio.Lock()

    @app.post("/api/admin/wall-clock/test", dependencies=admin_deps)
    async def test_wall_clock(body: WallClockTestBody):
        """One ``GET /api/version`` to the saved (or given) address. Answers {ok, version} or
        {ok: false, category, message}; never the address or any text from the other end."""
        if hub.emulate:
            return {"ok": True, "version": "emulated"}   # no network in emulate mode
        url = hub.config.wall_clock.ontime_url
        if body.ontime_url is not None:
            try:
                url = WallClockConfig(ontime_url=body.ontime_url).ontime_url
            except ValueError:
                raise HTTPException(422, "The address must look like http://host:4001") from None
        if test_lock.locked():
            raise HTTPException(409, "A test is already running. Wait a few seconds and try again.")
        async with test_lock:
            result = await asyncio.to_thread(check_connection, url)
        if result["ok"]:
            return result
        category = result["category"]
        return {"ok": False, "category": category, "message": WALL_CLOCK_TEST_TEXT.get(category, WALL_CLOCK_TEST_TEXT["unreachable"])}

    # Events and shows. These handlers never await, so two clicks can't interleave: the
    # stale-id checks below make a double submit start exactly one show.
    STALE_SHOW = ("A new show was already started (another click or another admin page). "
                  "Nothing more has been changed. Reload to see it.")

    @app.post("/api/admin/shows", dependencies=admin_deps)
    async def new_show(body: ShowBody):
        if body.from_show_id is not None and body.from_show_id != hub.recorder.show_id:
            raise HTTPException(409, STALE_SHOW)
        return hub.start_show(body.name, new_event_name=body.event_name if body.event == "new" else None,
                              day=body.day)

    @app.patch("/api/admin/shows/current", dependencies=admin_deps)
    async def patch_show(body: ShowPatch):
        if body.show_id is not None and body.show_id != hub.recorder.show_id:
            raise HTTPException(409, STALE_SHOW)
        return hub.update_show(name=body.name if "name" in body.model_fields_set else None,
                               day=body.day, set_day="day" in body.model_fields_set)

    @app.patch("/api/admin/events/current", dependencies=admin_deps)
    async def patch_event(body: EventPatch):
        if body.event_id is not None and body.event_id != hub.recorder.event_id:
            raise HTTPException(409, "A new event was already started on another admin page. "
                                     "Nothing has been changed. Reload to see it.")
        return hub.rename_event(body.name)

    # ------------------------------------------------------------ event logo
    def public_branding() -> dict:
        """The current event's logo for page headers. The URL carries the file's hash, so a
        browser keeps a logo until it changes."""
        eid = hub.recorder.event_id
        opts = hub.recorder.event_branding(eid) or {}
        meta = logos.info(eid)

        def url(v: str) -> str | None:
            return f"/branding/event/{eid}/{v}?v={meta[v]['sha']}" if meta[v] else None
        return {"light_url": url("light"), "dark_url": url("dark"),
                "on_dark": opts.get("on_dark", "as_is"),
                "show_on_admin": opts.get("show_on_admin", True),
                "show_on_index": opts.get("show_on_index", True)}

    def logo_event(event_id: str) -> int:
        try:
            eid = brand.valid_id(event_id)
        except brand.LogoError:
            raise HTTPException(404, "No such event") from None
        if hub.recorder.event_branding(eid) is None:
            raise HTTPException(404, "No such event")
        return eid

    def logo_variant(variant: str) -> str:
        if variant not in brand.VARIANTS:
            raise HTTPException(404, "No such logo")
        return variant

    def admin_branding(eid: int) -> dict:
        meta = logos.info(eid)
        previous = None   # the newest other event with a logo ("Use previous event's logo")
        for e in hub.recorder.events()[:EVENTS_MAX]:
            if e["id"] != eid and logos.has_logo(e["id"]):
                previous = {"id": e["id"], "name": e["name"]}
                break
        variants = {v: ({**brand.public_meta(meta[v]),
                         "url": f"/api/admin/events/{eid}/logo/{v}?v={meta[v]['sha']}"} if meta[v] else None)
                    for v in brand.VARIANTS}
        return {**hub.recorder.event_branding(eid), "current": eid == hub.recorder.event_id,
                "previous": previous, **variants}

    def logo_changed(eid: int) -> None:
        if eid == hub.recorder.event_id:
            hub.bus.publish("config", None)   # open screens reload and pick up the new logo

    def logo_file(eid: int, variant: str, v: str | None) -> FileResponse:
        meta = logos.info(eid)[variant]
        if meta is None or not meta["path"].is_file():
            raise HTTPException(404, "No logo")
        cache = "public, max-age=31536000, immutable" if v == meta["sha"] else "no-cache"
        return FileResponse(meta["path"], media_type=meta["content_type"], headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": f'inline; filename="logo.{meta["ext"]}"',
            "Cache-Control": cache})

    @app.get("/branding/event/{event_id}/{variant}", include_in_schema=False)
    async def public_logo(event_id: str, variant: str, v: str | None = Query(None, max_length=16)):
        # Only the current event's logo is public; older events' logos are admin-only.
        if event_id != str(hub.recorder.event_id):
            raise HTTPException(404, "No logo")
        return logo_file(hub.recorder.event_id, logo_variant(variant), v)

    @app.get("/api/admin/events/{event_id}/logo/{variant}", dependencies=[Depends(require_admin)])
    async def admin_logo(event_id: str, variant: str, v: str | None = Query(None, max_length=16)):
        return logo_file(logo_event(event_id), logo_variant(variant), v)

    @app.get("/api/admin/events/{event_id}/branding", dependencies=[Depends(require_admin)])
    async def get_branding(event_id: str):
        return admin_branding(logo_event(event_id))

    @app.put("/api/admin/events/{event_id}/branding", dependencies=admin_deps)
    async def put_branding(event_id: str, body: BrandingBody):
        eid = logo_event(event_id)
        hub.recorder.set_event_branding(eid, body.on_dark, body.show_on_admin, body.show_on_index)
        logo_changed(eid)
        return admin_branding(eid)

    @app.put("/api/admin/events/{event_id}/logo/{variant}", dependencies=admin_deps)
    async def put_logo(event_id: str, variant: str, request: Request):
        # Takes only the Request: the admin and same-origin checks run before any byte is read.
        eid, variant = logo_event(event_id), logo_variant(variant)
        declared = request.headers.get("content-length")
        if declared is None:
            raise HTTPException(411, "Send the file with a Content-Length")
        if not declared.isdigit() or int(declared) > brand.LOGO_MAX_BYTES:
            raise HTTPException(413, brand.MSG_LARGE)
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > brand.LOGO_MAX_BYTES:
                raise HTTPException(413, brand.MSG_LARGE)
        try:
            await asyncio.to_thread(logos.save, eid, variant, bytes(data))
        except brand.LogoError as e:
            raise HTTPException(e.status, e.message) from None
        logo_changed(eid)
        return admin_branding(eid)

    @app.delete("/api/admin/events/{event_id}/logo/{variant}", dependencies=admin_deps)
    async def delete_logo(event_id: str, variant: str):
        eid, variant = logo_event(event_id), logo_variant(variant)
        await asyncio.to_thread(logos.delete, eid, variant)
        logo_changed(eid)
        return admin_branding(eid)

    @app.post("/api/admin/events/{event_id}/logo/copy", dependencies=admin_deps)
    async def copy_logo(event_id: str, body: LogoCopyBody):
        eid, src = logo_event(event_id), logo_event(str(body.from_event_id))
        if src == eid:
            raise HTTPException(422, "Choose a different event to copy from")
        try:
            await asyncio.to_thread(logos.copy, src, eid)
        except brand.LogoError as e:
            raise HTTPException(e.status, e.message) from None
        o = hub.recorder.event_branding(src)
        hub.recorder.set_event_branding(eid, o["on_dark"], o["show_on_admin"], o["show_on_index"])
        logo_changed(eid)
        return admin_branding(eid)

    # ------------------------------------------------------------ documents
    # Per-event policy and risk documents. Admin-only until ticked for a dashboard; public routes
    # see only the current event's documents shared with the named dashboard, and answer the same
    # 404 for hidden and missing ones. Never in the snapshot, /api/info, admin state or OSC.
    NO_STORE = {"Cache-Control": "no-store"}

    def doc_event(event_id) -> int:
        try:
            eid = docs_mod.valid_event(event_id)
        except docs_mod.DocError:
            raise HTTPException(404, "No such event") from None
        if hub.recorder.event_branding(eid) is None:
            raise HTTPException(404, "No such event")
        return eid

    def doc_call(fn, *args):
        try:
            return fn(*args)
        except docs_mod.DocError as e:
            raise HTTPException(e.status, e.message) from None
        except OSError:
            log.warning("documents: file system error", exc_info=True)
            raise HTTPException(503, "The document could not be saved. Check the disk has space, then try again.") from None

    def check_slugs(slugs: list[str]) -> list[str]:
        known = {d.slug for d in hub.config.dashboards}
        if any(s not in known for s in slugs):
            raise HTTPException(422, "Unknown dashboard. Reload the page and try again.")
        return list(dict.fromkeys(slugs))

    def docs_changed(eid: int) -> None:
        if eid == hub.recorder.event_id:
            hub.bus.publish("documents", None)

    def shared_doc(doc_id: str, slug: str) -> tuple[int, dict]:
        eid = hub.recorder.event_id
        try:
            doc = documents.get(eid, doc_id)
        except docs_mod.DocError:
            raise HTTPException(404, "No such document") from None
        dash = hub.config.dashboard(slug)
        # A wall shows titles only, so it never serves the content (same 404 as hidden or missing).
        if dash is None or dash.layout == "wall" or slug not in doc["dashboards"] or not doc["ext"]:
            raise HTTPException(404, "No such document")
        return eid, doc

    def admin_docs(eid: int) -> dict:
        return {"event_id": eid, "current": eid == hub.recorder.event_id,
                "max_docs": docs_mod.MAX_DOCS,
                "documents": [docs_mod.admin(x) for x in doc_call(documents.docs, eid)]}

    @app.get("/api/documents")
    async def list_documents(dashboard: str = Query(..., max_length=64)):
        found = []
        if hub.config.dashboard(dashboard) is not None:
            found = [docs_mod.public(x) for x in documents.docs(hub.recorder.event_id)
                     if dashboard in x["dashboards"] and x["ext"]]
        return JSONResponse({"documents": found}, headers=NO_STORE)

    @app.get("/api/documents/{doc_id}")
    async def read_document(doc_id: str, dashboard: str = Query(..., max_length=64)):
        eid, doc = shared_doc(doc_id, dashboard)
        text = None
        if doc["ext"] != "pdf":
            try:
                text = documents.path(eid, doc).read_text(encoding="utf-8")
            except OSError:
                raise HTTPException(404, "No such document") from None
        return JSONResponse({**docs_mod.public(doc), "text": text}, headers=NO_STORE)

    def doc_file(eid: int, doc: dict) -> FileResponse:
        path = documents.path(eid, doc)
        if path is None or not path.is_file():
            raise HTTPException(404, "No such document")
        ctype = "application/pdf" if doc["ext"] == "pdf" else "text/plain; charset=utf-8"
        return FileResponse(path, media_type=ctype, headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Content-Disposition": f'inline; filename="document.{doc["ext"]}"',
            "Cross-Origin-Resource-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store"})

    @app.get("/api/documents/{doc_id}/file")
    async def document_file(doc_id: str, dashboard: str = Query(..., max_length=64)):
        return doc_file(*shared_doc(doc_id, dashboard))

    @app.get("/api/admin/events/{event_id}/documents", dependencies=[Depends(require_admin)])
    async def admin_list_documents(event_id: str):
        return JSONResponse(admin_docs(doc_event(event_id)), headers=NO_STORE)

    @app.get("/api/admin/events/{event_id}/documents/{doc_id}/file", dependencies=[Depends(require_admin)])
    async def admin_document_file(event_id: str, doc_id: str):
        eid = doc_event(event_id)
        return doc_file(eid, doc_call(documents.get, eid, doc_id))

    @app.post("/api/admin/events/{event_id}/documents", dependencies=admin_deps)
    async def create_document(event_id: str, body: DocumentNew):
        eid = doc_event(event_id)
        slugs = check_slugs(body.dashboards)
        doc = await asyncio.to_thread(doc_call, documents.create, eid, body.title, body.category, slugs)
        docs_changed(eid)
        return {"id": doc["id"], **admin_docs(eid)}

    @app.put("/api/admin/events/{event_id}/documents/{doc_id}/file", dependencies=admin_deps)
    async def put_document_file(event_id: str, doc_id: str, request: Request,
                                kind: Literal["md", "txt", "pdf"] = Query("txt")):
        # Takes only the Request: the admin and same-origin checks run before any byte is read.
        eid = doc_event(event_id)
        doc_call(documents.get, eid, doc_id)
        declared = request.headers.get("content-length")
        if declared is None:
            raise HTTPException(411, "Send the file with a Content-Length")
        if not declared.isdigit() or int(declared) > docs_mod.PDF_MAX_BYTES:
            raise HTTPException(413, docs_mod.MSG_PDF_LARGE)
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > docs_mod.PDF_MAX_BYTES:
                raise HTTPException(413, docs_mod.MSG_PDF_LARGE)
        await asyncio.to_thread(doc_call, documents.upload, eid, doc_id, bytes(data), kind)
        docs_changed(eid)
        return admin_docs(eid)

    @app.patch("/api/admin/events/{event_id}/documents/{doc_id}", dependencies=admin_deps)
    async def patch_document(event_id: str, doc_id: str, body: DocumentPatch):
        eid = doc_event(event_id)
        slugs = check_slugs(body.dashboards) if body.dashboards is not None else None
        await asyncio.to_thread(doc_call, documents.patch, eid, doc_id, body.title, body.category, slugs)
        docs_changed(eid)
        return admin_docs(eid)

    @app.post("/api/admin/documents/demo", dependencies=admin_deps)
    async def documents_demo():
        """Emulate only: three example documents in the current event (one shared with the
        first dashboard, so the reader and the warning can be seen)."""
        if not hub.emulate:
            raise HTTPException(409, "Demo documents only work in emulate mode. Nothing has been changed.")
        eid = hub.recorder.event_id
        first = [hub.config.dashboards[0].slug] if hub.config.dashboards else []
        have = {x["title"] for x in documents.docs(eid)}
        for title, cat, slugs, kind, body in docs_mod.demo_documents(first):
            if title in have:
                continue   # loaded already
            doc = await asyncio.to_thread(doc_call, documents.create, eid, title, cat, slugs)
            await asyncio.to_thread(doc_call, documents.upload, eid, doc["id"], body, kind)
        docs_changed(eid)
        return admin_docs(eid)

    @app.delete("/api/admin/events/{event_id}/documents/{doc_id}", dependencies=admin_deps)
    async def delete_document(event_id: str, doc_id: str):
        eid = doc_event(event_id)
        await asyncio.to_thread(doc_call, documents.delete, eid, doc_id)
        docs_changed(eid)
        return admin_docs(eid)

    # ------------------------------------------------------------ onsite contacts
    # Personal data. Per event, admin-only except what a dashboard with the Contacts card may see:
    # job title, name, radio and area, and a phone number only when the installation, the dashboard
    # and the contact all allow it (and after the reveal PIN, if one is set). Never in the
    # snapshot, /api/info, admin state, diagnostics, logs or a bus payload.
    def contact_event(event_id) -> int:
        try:
            eid = contacts_mod.valid_event(event_id)
        except contacts_mod.ContactError:
            raise HTTPException(404, "No such event") from None
        if hub.recorder.event_branding(eid) is None:
            raise HTTPException(404, "No such event")
        return eid

    def contact_call(fn, *args):
        try:
            return fn(*args)
        except contacts_mod.ContactError as e:
            raise HTTPException(e.status, e.message) from None
        except OSError as e:
            log.warning("contacts: file system error (%s)", type(e).__name__)   # no path, no traceback
            raise HTTPException(503, "The contacts could not be saved. Check the disk has space, then try again.") from None

    def contacts_changed(eid: int) -> None:
        if eid == hub.recorder.event_id:
            hub.bus.publish("contacts", None)

    def contact_reminders() -> list[dict]:
        """Ended events that still hold contacts (GDPR: storage limitation), unless dismissed."""
        out = []
        current = hub.recorder.event_id
        for e in hub.recorder.events():
            if e["id"] == current or e["ended"] is None:
                continue
            st = contact_call(contacts.state, e["id"])
            if st["contacts"] and not st["reminder_dismissed"]:
                out.append({"event_id": e["id"], "name": e["name"], "count": len(st["contacts"])})
        return out[:20]

    def admin_contacts(eid: int) -> dict:
        st = contact_call(contacts.state, eid)
        edited = st["edited"]
        return {"event_id": eid, "current": eid == hub.recorder.event_id,
                "max_contacts": contacts_mod.MAX_CONTACTS, "contacts": st["contacts"], "edited": edited,
                "has_files": contacts.has_files(eid),
                "stale": bool(st["contacts"] and edited and time.time() - edited > contacts_mod.STALE_DAYS * 86400),
                "roles": contacts.roles(), "reminders": contact_reminders(),
                "phones_enabled": hub.config.contacts.phones_enabled,
                "reveal_pin_set": bool(hub.config.contacts.reveal_pin_hash)}

    def public_contacts(slug: str, with_phones: bool) -> dict:
        dash = hub.config.dashboard(slug)
        if dash is None:
            raise HTTPException(404, "No such dashboard")
        rows = contacts_mod.contacts_for(contacts.contacts(hub.recorder.event_id), dash,
                                         hub.config.contacts.phones_enabled, with_phones)
        return {"contacts": rows, "pin_needed": any(r.get("has_phone") for r in rows)}

    @app.get("/api/contacts")
    async def list_contacts(dashboard: str = Query(..., max_length=64)):
        return JSONResponse(public_contacts(dashboard, not hub.config.contacts.reveal_pin_hash), headers=NO_STORE)

    @app.post("/api/contacts/reveal", dependencies=[Depends(require_same_origin)])
    async def reveal_contacts(body: ContactsRevealBody):
        """The phone numbers this dashboard may show, after the reveal PIN. Its own rate limiter."""
        pin_hash = hub.config.contacts.reveal_pin_hash
        if pin_hash:
            now = time.time()
            failed_reveals[:] = [t for t in failed_reveals if now - t < 60]
            if len(failed_reveals) >= 5:
                raise HTTPException(429, "Too many attempts; wait a minute")
            if not verify_pin(body.pin, pin_hash):
                failed_reveals.append(now)
                await asyncio.sleep(1.0)
                raise HTTPException(401, "Wrong PIN")
        return JSONResponse(public_contacts(body.dashboard, True), headers=NO_STORE)

    @app.get("/api/admin/events/{event_id}/contacts", dependencies=[Depends(require_admin)])
    async def admin_list_contacts(event_id: str):
        return JSONResponse(admin_contacts(contact_event(event_id)), headers=NO_STORE)

    @app.put("/api/admin/events/{event_id}/contacts", dependencies=admin_deps)
    async def put_contacts(event_id: str, body: ContactsBody):
        eid = contact_event(event_id)
        await asyncio.to_thread(contact_call, contacts.replace, eid, list(body.contacts))
        contacts_changed(eid)
        return JSONResponse(admin_contacts(eid), headers=NO_STORE)

    @app.delete("/api/admin/events/{event_id}/contacts", dependencies=admin_deps)
    async def clear_contacts(event_id: str):
        eid = contact_event(event_id)
        await asyncio.to_thread(contact_call, contacts.clear, eid)
        contacts_changed(eid)
        return JSONResponse(admin_contacts(eid), headers=NO_STORE)

    @app.post("/api/admin/events/{event_id}/contacts/copy", dependencies=admin_deps)
    async def copy_contacts(event_id: str, body: ContactsCopyBody):
        eid, src = contact_event(event_id), contact_event(str(body.from_event_id))
        if src == eid:
            raise HTTPException(422, "Choose a different event to copy from")
        await asyncio.to_thread(contact_call, contacts.copy, src, eid)
        contacts_changed(eid)
        return JSONResponse(admin_contacts(eid), headers=NO_STORE)

    @app.post("/api/admin/events/{event_id}/contacts/dismiss-reminder", dependencies=admin_deps)
    async def dismiss_contacts_reminder(event_id: str):
        eid = contact_event(event_id)
        await asyncio.to_thread(contact_call, contacts.dismiss_reminder, eid)
        return JSONResponse(admin_contacts(eid), headers=NO_STORE)

    @app.put("/api/admin/contacts/settings", dependencies=admin_deps)
    async def put_contacts_settings(body: ContactsSettingsBody):
        if body.reveal_pin is not None and body.clear_reveal_pin:
            raise HTTPException(422, "Set a new PIN or remove it, not both")
        pin_hash = hub.config.contacts.reveal_pin_hash
        if body.clear_reveal_pin:
            pin_hash = ""
        elif body.reveal_pin:
            if hub.config.admin.pin_hash and verify_pin(body.reveal_pin, hub.config.admin.pin_hash):
                raise HTTPException(422, "Use a different PIN from the admin PIN: crew will know this one")
            pin_hash = hash_pin(body.reveal_pin)
        hub.config.contacts = ContactsSettings(phones_enabled=body.phones_enabled, reveal_pin_hash=pin_hash)
        hub.save_config()
        hub.bus.publish("contacts", None)
        return {"ok": True, "phones_enabled": body.phones_enabled, "reveal_pin_set": bool(pin_hash)}

    @app.post("/api/admin/contacts/demo", dependencies=admin_deps)
    async def contacts_demo():
        """Emulate only: made-up contacts in the current event, with phone numbers allowed on the
        first dashboard so the whole path can be seen."""
        if not hub.emulate:
            raise HTTPException(409, "Demo contacts only work in emulate mode. Nothing has been changed.")
        eid = hub.recorder.event_id
        if not contacts.contacts(eid):
            rows = [contacts_mod.Contact.model_validate(c) for c in contacts_mod.DEMO]
            await asyncio.to_thread(contact_call, contacts.replace, eid, rows)
        hub.config.contacts = ContactsSettings(phones_enabled=True, reveal_pin_hash=hub.config.contacts.reveal_pin_hash)
        if hub.config.dashboards:
            d = hub.config.dashboards[0]
            d.show_contact_phones = True
            if "contacts" not in d.cards:
                d.cards = [*d.cards, "contacts"]
        hub.save_config()
        contacts_changed(eid)
        return JSONResponse(admin_contacts(eid), headers=NO_STORE)

    # Schedule (admin writes). Like the show handlers these never await, so the stale-show check
    # and the write can't interleave with another request.
    STALE_SCHEDULE = ("This schedule belongs to a show day that has ended (a new show was started "
                      "on another page). Nothing has been changed. Reload to see the current day.")

    def demo_allowed() -> bool:
        return hub.emulate or not hub.schedule.items()

    def schedule_422(errors: list[sched.FieldError]) -> JSONResponse:
        detail = [{"loc": ["body", "items", e.index, e.field] if e.index >= 0 else ["body", "items"],
                   "msg": e.msg, "type": "value_error"} for e in errors[:20]]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.put("/api/admin/schedule", dependencies=admin_deps)
    async def put_schedule(body: SchedulePut):
        if body.show_id != hub.recorder.show_id:
            raise HTTPException(409, STALE_SCHEDULE)
        if body.revision != hub.schedule.revision():
            raise HTTPException(409, sched.MSG_CHANGED)
        items = [i.model_dump() for i in body.items]
        current = {i["id"]: i for i in hub.schedule.items()}
        for item, d in zip(body.items, items):  # an item sent without "action" keeps its actions
            if "action" not in item.model_fields_set and item.id in current:
                d["action"] = current[item.id].get("action")
        targets = {t.id for t in hub.config.actions.targets}
        for n, d in enumerate(items):
            a = d.get("action") or {}
            if any(m and m.get("target_id") not in targets for m in (a.get("start"), a.get("end")))                     and "action" in body.items[n].model_fields_set:
                return schedule_422([sched.FieldError(n, "action", actions_mod.MSG_TARGET)])
        rows, errors = sched.build_rows(items, hub.show_info()["day"], hub.config.site)
        if errors:
            return schedule_422(errors)
        # An item sent without "marker" (e.g. by a page loaded before this setting existed) keeps
        # the setting it has.
        for item, row in zip(body.items, rows):
            if "marker" not in item.model_fields_set and item.id in current:
                row["marker"] = current[item.id].get("marker")
        if not body.with_actions:  # an older page never deletes action rows it doesn't show
            sent = {i.id for i in body.items}
            rows += [{**i, "sort": len(rows) + k} for k, i in enumerate(
                x for x in current.values() if x["kind"] == sched.ACTION_KIND and x["id"] not in sent)]
        hub.schedule.replace(body.show_id, rows, body.revision)
        return hub.schedule.admin()

    @app.get("/api/admin/schedule", dependencies=[Depends(require_admin)])
    async def get_admin_schedule():
        """The schedule page's list: every row with its actions and send status (admin only)."""
        return JSONResponse(hub.schedule.admin(), headers=NO_STORE)

    # ---- OSC actions (admin only; see core/actions.py for the safeguards)
    def actions_admin() -> dict:
        return {**hub.actions.status(), "targets": [t.model_dump() for t in hub.config.actions.targets],
                "log": hub.recorder.action_log(100), "warning": actions_mod.SAFETY_WARNING}

    @app.get("/api/admin/actions", dependencies=[Depends(require_admin)])
    async def get_actions():
        return JSONResponse(actions_admin(), headers=NO_STORE)

    @app.put("/api/admin/actions/targets", dependencies=admin_deps)
    async def put_action_targets(body: actions_mod.TargetsPut):
        from ..core.config import ActionTarget
        hub.config.actions = hub.config.actions.model_copy(
            update={"targets": [ActionTarget(**t.model_dump()) for t in body.targets]})
        hub.save_config()
        return actions_admin()

    @app.put("/api/admin/actions/armed", dependencies=admin_deps)
    async def put_actions_armed(body: ActionsArmedBody):
        hub.actions.set_armed(body.armed, "admin")
        return hub.actions.status()

    @app.post("/api/admin/actions/test", dependencies=admin_deps)
    async def test_action(body: actions_mod.MessageBody):
        if body.target_id not in {t.id for t in hub.config.actions.targets}:
            raise HTTPException(422, actions_mod.MSG_TARGET)
        return hub.actions.test(body.model_dump(), "admin")

    @app.put("/api/admin/schedule/settings", dependencies=admin_deps)
    async def put_schedule_settings(body: ScheduleSettingsBody):
        hub.config.site = hub.config.site.model_copy(update={"schedule_auto_markers": body.auto_markers})
        hub.save_config()
        return {"auto_markers": hub.config.site.schedule_auto_markers}

    @app.post("/api/admin/schedule/import", dependencies=admin_deps)
    async def import_schedule(body: ScheduleImportBody):
        if body.show_id is not None and body.show_id != hub.recorder.show_id:
            raise HTTPException(409, STALE_SCHEDULE)
        if not body.dry_run and body.revision != hub.schedule.revision():
            raise HTTPException(409, sched.MSG_CHANGED)
        try:
            res = sched.parse_import(body.text, body.format, hub.show_info()["day"], hub.config.site)
        except sched.ImportTooLarge as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        if body.dry_run:
            return {"format": res.format, "rows": res.rows, "errors": res.errors, "error_count": res.error_count}
        if res.error_count:  # never the parsed rows here: only where the problems are
            return JSONResponse({"detail": "Some lines could not be read. Nothing has been added.",
                                 "errors": res.errors, "error_count": res.error_count}, status_code=422)
        out = {"format": res.format, "rows": res.rows, "errors": [], "error_count": 0}
        try:
            schedule = hub.schedule.append(body.show_id, res.rows, body.revision)
        except sched.ImportTooLarge as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except sched.StaleShow:
            raise HTTPException(409, STALE_SCHEDULE) from None
        except sched.StaleRevision:
            raise HTTPException(409, sched.MSG_CHANGED) from None
        return {**out, "schedule": schedule}

    @app.get("/api/admin/devices/errors", dependencies=[Depends(require_admin)])
    async def device_errors(show_id: int | None = Query(None, ge=1), scope: Literal["show", "event"] = "show"):
        """Problems per device for a show (or all of its event's days). Read-only; admin only, because
        it names equipment and imported units."""
        rec = hub.recorder
        show = rec.show_row(show_id if show_id is not None else rec.show_id)
        if show is None:
            raise HTTPException(404, "There is no such show.")
        ids = rec.event_show_ids(show["event_id"]) if scope == "event" and show["event_id"] else [show["id"]]
        words = {"sensor": "Sensor", "service": "Service", "meter": "Power meter", "host": "This Raspberry Pi"}
        devices = {d.id: {"name": d.name, "kind": words.get(d.category, d.category.title() or "Device"),
                          "status": d.status.value} for d in hub.devices.values() if d.id != "site"}
        out = errors_mod.summarise(rec, ids, devices, time.time(), rec.run_id)
        return {"show": {k: show[k] for k in ("id", "name", "day", "started", "ended")}, "scope": scope,
                "days": len(ids), **out, "gaps": [{"from": a, "to": b} for a, b in out["gaps"]]}

    @app.post("/api/admin/logs/import", dependencies=admin_deps)
    async def import_log(body: LogImportBody):
        show_id = body.show_id if body.show_id is not None else hub.recorder.show_id
        try:
            parsed = await asyncio.to_thread(logimport.parse.parse_text, body.text)   # slow part, off the loop
            plan = logimport.plan_import(
                hub.recorder, hub.config.site, fmt=body.format, text="", parsed=parsed,
                file_name=body.file_name, show_id=show_id, tz=body.tz, offset_s=body.offset_s,
                session_markers=body.session_markers, load_checker_markers=body.load_checker_markers)
            result = {"dry_run": body.dry_run, **plan.summary()}
            if body.dry_run:
                return result
            return {**result, **logimport.apply_import(hub.recorder, plan)}
        except logimport.ImportRefused as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/admin/logs/imports", dependencies=[Depends(require_admin)])
    async def list_log_imports(show_id: int | None = Query(None, ge=1)):
        return {"show_id": show_id or hub.recorder.show_id,
                "imports": hub.recorder.vendor_imports(show_id or hub.recorder.show_id)}

    @app.delete("/api/admin/logs/imports/{import_id}", dependencies=admin_deps)
    async def undo_log_import(import_id: str, show_id: int | None = Query(None, ge=1)):
        removed = hub.recorder.undo_vendor_import(show_id or hub.recorder.show_id, import_id[:64])
        if removed is None:
            raise HTTPException(404, "There is no such import in this show. Nothing has been changed.")
        return {"ok": True, **removed}

    @app.post("/api/admin/schedule/demo", dependencies=admin_deps)
    async def schedule_demo(body: ScheduleDemoBody | None = None):
        show_id = body.show_id if body is not None else None
        if show_id is not None and show_id != hub.recorder.show_id:
            raise HTTPException(409, STALE_SCHEDULE)
        if not demo_allowed():
            raise HTTPException(409, "The demo day only loads when the schedule is empty (or in "
                                     "emulate mode). Nothing has been changed.")
        try:
            return hub.schedule.load_demo(show_id)
        except sched.DemoDoesNotFit:
            raise HTTPException(409, "The demo day can't load this close to the day rollover, or "
                                     "on a show day that isn't today. Nothing has been changed.") from None

    def events_with_shows() -> list[dict]:
        """Events, newest first, each with its shows (days) newest first. Bounded: the newest
        EVENTS_MAX events and SHOWS_MAX shows."""
        events = hub.recorder.events()[:EVENTS_MAX]
        by_event: dict[int, list[dict]] = {e["id"]: [] for e in events}
        for s in hub.recorder.shows()[:SHOWS_MAX]:
            if s["event_id"] in by_event:
                by_event[s["event_id"]].append({**s, "day": hub.show_day(s["started"], s["day"]),
                                                "day_set": s["day"] is not None})
        return [{**e, "shows": by_event[e["id"]]} for e in events]

    @app.delete("/api/markers/{marker_id}", dependencies=admin_deps)
    async def delete_marker(marker_id: int):
        if not hub.delete_marker(marker_id):
            raise HTTPException(404, "No such marker")
        return {"ok": True}

    # FastAPI's default 422 body echoes the submitted value ("input") and pydantic's context
    # ("ctx"), which can include a PIN or key. Return only where and what kind of problem.
    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        detail = [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "Invalid value")),
                   "type": str(e.get("type", ""))} for e in exc.errors()[:20]]
        return JSONResponse({"detail": detail}, status_code=422)

    # ---------------------------------------------------------- software
    @app.exception_handler(UpdaterError)
    async def updater_error(_request: Request, exc: UpdaterError):
        body = {"detail": message_for(exc.category), "category": exc.category}
        headers = {}
        if isinstance(exc, RateLimited):
            body["retry_after"] = exc.retry_after
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(body, status_code=UPDATER_STATUS.get(exc.category, 409), headers=headers)

    @app.get("/api/admin/software", dependencies=[Depends(require_admin)])
    async def software_status():
        return await updater.status()

    @app.post("/api/admin/software/check", dependencies=admin_deps)
    async def software_check():
        return await updater.check()

    @app.put("/api/admin/software/channel", dependencies=admin_deps)
    async def software_channel(body: ChannelBody):
        return {"channel": await updater.set_channel(body.channel)}

    @app.post("/api/admin/software/update", dependencies=admin_deps)
    async def software_update(body: UpdateBody):
        if not updater.mutable:  # 409 not_managed before asking for a PIN
            raise UpdaterError("not_managed")
        await check_pin(body.pin)
        return await updater.update(body.channel, body.target_sha)

    @app.post("/api/admin/software/rollback", dependencies=admin_deps)
    async def software_rollback(body: RollbackBody):
        if not updater.mutable:
            raise UpdaterError("not_managed")
        await check_pin(body.pin)
        return await updater.rollback(body.history_id)

    # -------------------------------------------------- connect a tablet
    def server_port(request: Request) -> int:
        return int(getattr(app.state, "port", None) or request.url.port or 8080)

    @app.get("/api/admin/connect", dependencies=[Depends(require_admin)])
    async def connect_info(request: Request):
        """Addresses tablets should use.  Admin-only: it lists every LAN address of the host."""
        port = server_port(request)
        addrs = await asyncio.to_thread(lan_addresses)
        name = hub.config.mdns_name or "stagewatch"
        return {
            "port": port, "addresses": addrs, "mdns_name": f"{name}.local",
            "home": netinfo.connect_urls(addrs, port, "", name),
            "dashboards": [{"slug": d.slug, "title": d.title or d.slug, "layout": d.layout,
                            **netinfo.connect_urls(addrs, port, d.slug, name)} for d in hub.config.dashboards],
        }

    @app.get("/api/dashboard/{slug}/address")
    async def dashboard_address(slug: str, request: Request):
        """For the "Open on a tablet" footer card only: one LAN address + the dashboard path (no
        list of interfaces, nothing else).  Dashboards without the connect_footer card (any
        layout) get nothing."""
        d = hub.config.dashboard(slug)
        if d is None or "connect_footer" not in d.cards:
            return {"url": ""}
        addrs = await asyncio.to_thread(lan_addresses)
        # Prefer the address this request arrived on, so a PC with several network cards (or a
        # VPN / virtual adapter) never hands out an address from another network.
        here = (request.scope.get("server") or ("", 0))[0]
        pick = [here] if here in addrs else addrs[:1]
        return {"url": netinfo.connect_urls(pick, server_port(request), d.slug)["ip"][0] if pick else ""}

    # ------------------------------------------------------- diagnostics
    def _diag_sources() -> list[Path]:
        dirs = [Path(hub.data_dir) / "logs"]
        if updater.marker is not None:
            dirs.append(Path(updater.marker.data_dir) / "logs")
        return dirs

    def _build_diagnostics(status: dict | None) -> bytes:
        raw_cfg = hub.config.model_dump(mode="json")
        known = diagnostics.known_secrets(raw_cfg)
        cfg = diagnostics.redact_config(raw_cfg, known)
        cfg["admin"] = {"pin_set": bool(raw_cfg.get("admin", {}).get("pin_hash"))}  # never the hash
        info = {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": __version__, "build": build_info(),
            "platform": platform.platform(), "python": sys.version.split()[0],
            "uptime_s": round(time.time() - started_at), "emulate": hub.emulate,
            "managed": updater.managed, "supervised": updater.supervised,
            "site": hub.config.site.name,
        }
        devices = [d.to_dict() for d in hub.devices.values()]
        files = {
            "README.txt": diagnostics.README,
            "info.json": diagnostics.dumps(diagnostics.redact_config(info, known)),
            "devices.json": diagnostics.dumps(diagnostics.redact_config(devices, known)),
            "config.json": diagnostics.dumps(cfg),
            "updater.json": diagnostics.dumps(diagnostics.redact_config(status or {}, known)),
            "alarm_log.json": diagnostics.dumps(diagnostics.redact_config(hub.recorder.alarm_log(), known)),
        }
        logs: dict[str, list[str]] = {}
        for name in ("stagewatch.log", "launcher.log", "server-console.log"):
            for d in _diag_sources():
                lines = diagnostics.tail_lines(d / name)
                if lines is not None:
                    logs[name] = lines
                    break
        return diagnostics.assemble_zip(files, logs, known)

    @app.get("/api/admin/diagnostics", dependencies=admin_deps)
    async def download_diagnostics():
        try:
            status = await updater.status()
        except Exception:  # a broken updater must not stop the support bundle
            log.exception("diagnostics: updater status unavailable")
            status = {"error": "updater status unavailable"}
        data = await asyncio.to_thread(_build_diagnostics, status)
        name = time.strftime("stagewatch-diagnostics-%Y%m%d-%H%M%S.zip", time.gmtime())

        def chunks():
            for i in range(0, len(data), 64 * 1024):
                yield data[i:i + 64 * 1024]
        return StreamingResponse(chunks(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Content-Length": str(len(data)),
            "Cache-Control": "no-store"})

    # -------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket, dashboard: str = ""):
        origin = websocket.headers.get("origin")
        if origin and urlparse(origin).netloc != websocket.headers.get("host"):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        admin = signer.valid(websocket.cookies.get(COOKIE))
        queue: ClientQueue = ClientQueue()
        feed.clients.add(queue)
        dash = hub.config.dashboard(dashboard)
        await websocket.send_json({
            "type": "snapshot", **hub.snapshot(), "is_admin": admin,
            "dashboard": dash.model_dump() if dash else None,
        })

        async def sender():
            while True:
                await websocket.send_json(await queue.get())

        send_task = asyncio.create_task(sender())
        try:
            while True:
                frame = await websocket.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                raw = frame.get("text") if frame.get("text") is not None else frame.get("bytes") or b""
                if len(raw) > WS_MAX_MESSAGE:  # uvicorn enforces this too; this covers other servers
                    await websocket.close(code=1009)
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:  # one bad frame is ignored; the client keeps its connection
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "add_marker" and (admin or dashboard_allows(dashboard, "marker")):
                    try:  # the same note rules as POST /api/markers; a bad note adds nothing
                        note = clean_note(msg.get("note", ""))
                    except ValueError:
                        continue
                    hub.add_marker(str(msg.get("label", "Marker")),
                                   "admin" if admin else f"dashboard:{dashboard}", note=note)
                elif kind == "ack" and (admin or dashboard_allows(dashboard, "ack")):
                    hub.ack_alarms("admin" if admin else f"dashboard:{dashboard}")
                elif kind == "ping":
                    queue.put_nowait({"type": "pong"})
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            feed.clients.discard(queue)
            send_task.cancel()

    return app
