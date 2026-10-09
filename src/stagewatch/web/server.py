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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator

from .. import __version__, acoustics
from ..core.config import (
    ACCURACY_MAX, ACCURACY_MIN, CLOCK_STYLES, PASSWORD_MAX, BarometerConfig, Dashboard, EntitySettings, EsphomeDeviceConfig, OntimeTimerConfig, OscOutConfig,
    SiteConfig, SplConfig, SplSlot, Threshold, WallClockConfig, spl_password_error,
)
from ..core.calibration import set_calibration
from ..core.hub import Hub
from ..core.model import Device, Entity, Kind, Marker, slugify
from ..core.recorder import clean_note, valid_day
from .. import diagnostics, netinfo
from ..core import barometer as baro_mod
from ..core import cards as cards_mod
from ..core import schedule as sched
from ..core import spl as spl_mod
from ..integrations.ontime.client import check_connection
from ..core.updater import RateLimited, Updater, message_for
from ..updater_common import UpdaterError
from ..version import build_info
from .auth import COOKIE, SESSION_S, SessionSigner, hash_pin, verify_pin
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
}


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


class SplSlotBody(BaseModel):
    """One recorded value: Smaart's own metric text, and the input it is read from ("" = the first
    input Smaart lists)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    metric: str = Field(min_length=1, max_length=spl_mod.METRIC_NAME_MAX)
    source: str = Field("", max_length=spl_mod.INPUT_NAME_MAX)


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
    return any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


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

    @field_validator("clock_style", mode="before")
    @classmethod
    def _clock_style(cls, v):
        """Overrides the lenient loader: only a style this build knows can be saved."""
        if v not in CLOCK_STYLES:
            raise ValueError("Clock style must be digits, ring or segments")
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
                  "curfew", "load_out", "other"] = "other"
    title: str = Field(max_length=1000)
    start: str = Field(max_length=5)
    end: str = Field("", max_length=5)
    setlist: str = Field("", max_length=sched.SETLIST_MAX_BYTES)  # characters; bytes checked below
    # Timeline marker: true/false, or null for the kind's default (on for soundcheck, doors and
    # act). Not sent: an existing item keeps its setting, a new one gets the default.
    marker: StrictBool | None = None

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
        elif topic == "schedule" and isinstance(payload, dict):
            # Small on purpose: clients fetch GET /api/schedule?stage= for the list itself.
            self._send_all({"type": "schedule", "show_id": payload.get("show_id"),
                            "revision": payload.get("revision")})
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
                       overrides={**DEFAULT_BODY_OVERRIDES, **(body_limit_overrides or {})})
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

    @app.get("/api/admin/state", dependencies=[Depends(require_admin)])
    async def admin_state():
        cfg = hub.config.model_dump(mode="json")
        cfg.pop("admin", None)
        for dev in cfg.get("esphome_devices", []):
            dev["noise_psk"] = "set" if dev.get("noise_psk") else ""
        cfg.get("spl", {}).pop("password", None)   # Smaart's API password: only "set / not set" (spl.password_set)
        esp =hub.integrations.get("esphome")
        return {
            "config": cfg,
            "emulate": hub.emulate,
            "hardware": hardware_state(esp),
            "integrations": [i.info() for i in hub.integrations.values()],
            "discovered": esp.discovered_list() if esp else [],
            "ignored": esp.ignored_list() if esp else [],
            "shows": hub.recorder.shows()[:SHOWS_MAX],
            # Event & show card: the current event and show (day resolved in site time), all
            # events with their shows, and the dates the Next day / New event dialogs suggest.
            "event": hub.recorder.current_event(),
            "show": hub.show_info(),
            "events": events_with_shows(),
            "show_days": hub.show_days(),
            "alarm_log": hub.recorder.alarm_log(),
            # Wall Clock card: is the source running, and what is it saying (no addresses).
            "wall_clock": hub.wall_clock.admin_status(),
            "ontime_timer": hub.ontime_timer.admin_status(),
            "ontime_rundown": hub.ontime_rundown.admin_status(),
            # Sound level card: the values that can be chosen, and what is running (no addresses).
            "spl": spl_admin(),
            # For the "Edit cards" panel: the cards this build knows, in picker order, and the
            # defaults a new dashboard gets for each layout.
            "cards": {"known": list(cards_mod.KNOWN_CARDS),
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
            # Each sensor's live share of its kind's site average, and why weighting is not in use.
            "averages": hub.average_info,
            "devices": {d.id: {"hw_id": d.hw_id, "conflict": conflicts.get(d.id), "role": d.role,
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
        if hub.devices[device_id].category == "service":
            raise HTTPException(409, SERVICE_DEVICE)
        esp = esphome()
        if (body.host is not None or body.port is not None) and esp.config_of(device_id) is None:
            raise HTTPException(409, "This device has no network address to change")
        await esp.update(device_id, body.name, body.area, body.host, body.port, body.role)
        return {"ok": True}

    @app.delete("/api/admin/devices/{device_id}", dependencies=admin_deps)
    async def delete_device(device_id: str):
        if device_id not in hub.devices or device_id == "site":
            raise HTTPException(404, "No such device")
        if hub.devices[device_id].category == "service":
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
        set_calibration(hub.config, entity_id, entity.hw_key, body.offset, body.include_in_average, **extra)
        hub.save_config()
        if entity.raw_value is not None and not entity.derived:
            hub.update_state(entity_id, entity.raw_value, entity.updated)
        else:
            hub.bus.publish("entity", entity)   # screens still learn the new offset
        return body

    @app.get("/api/admin/averages", dependencies=[Depends(require_admin)])
    async def admin_averages():
        """Admin only: each sensor's share of the site average now (the page refreshes it live)."""
        return {"weight_by_accuracy": hub.config.site.weight_by_accuracy, "averages": hub.average_info}

    @app.put("/api/admin/thresholds", dependencies=admin_deps)
    async def put_thresholds(body: list[Threshold]):
        ids = [t.id for t in body]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Threshold ids must be unique")
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
            # A save that doesn't send cards/stage/clock_style (today's admin page) keeps what the
            # dashboard has; only a new dashboard gets its layout's default cards.
            for key in ("cards", "stage", "clock_style"):
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
        try:
            new = SplConfig(enabled=body.enabled, host=body.host, port=body.port, password=password, **extra)
        except ValidationError:
            raise HTTPException(422, "The address must be a host name or an IP address on the local network") from None
        if new.enabled and not hub.emulate and not (new.host and new.port):
            raise HTTPException(422, "Enter the address and port of the Smaart computer first")
        hub.config.spl = new
        integ = hub.integrations.get("smaart")
        if integ is not None:
            try:
                await integ.apply()
            except Exception:  # noqa: BLE001 - the settings are saved either way
                log.exception("Could not apply the sound level settings")
        hub.save_config()
        n = len(new.effective_slots())
        log.info("Sound level settings saved (%s, %d value%s, password %s)", "on" if new.enabled else "off", n,
                 "" if n == 1 else "s", "set" if new.password else "not set")
        return {"ok": True, "changed": old != new, "password_set": bool(new.password)}

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
        rows, errors = sched.build_rows([i.model_dump() for i in body.items], hub.show_info()["day"],
                                        hub.config.site)
        if errors:
            return schedule_422(errors)
        # An item sent without "marker" (e.g. by a page loaded before this setting existed) keeps
        # the setting it has.
        stored = {i["id"]: i.get("marker") for i in hub.schedule.items()}
        for item, row in zip(body.items, rows):
            if "marker" not in item.model_fields_set and item.id in stored:
                row["marker"] = stored[item.id]
        return hub.schedule.replace(body.show_id, rows, body.revision)

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
