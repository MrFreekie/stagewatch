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

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .. import __version__
from ..core.config import (
    Dashboard, EntitySettings, EsphomeDeviceConfig, OscOutConfig, SiteConfig, Threshold,
)
from ..core.hub import Hub
from ..core.model import Device, Entity, Marker, slugify
from ..core.recorder import valid_day
from .. import diagnostics, netinfo
from ..core import cards as cards_mod
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
    label: str = Field("Marker", max_length=120)
    dashboard: str = ""


class AckBody(BaseModel):
    dashboard: str = ""


class AdoptBody(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(6053, ge=1, le=65535)
    id: str = ""
    name: str = ""
    area: str = ""
    noise_psk: str = ""


class IgnoreBody(BaseModel):
    key: str = Field(min_length=1, max_length=80)


class DevicePatch(BaseModel):
    name: str | None = None
    area: str | None = None


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
        for q in list(self.clients):
            if q.qsize() > 500:  # dead/slow client; drop rather than grow forever
                continue
            q.put_nowait(msg)

    def _on_event(self, topic: str, payload) -> None:
        if topic in ("state", "entity"):
            self._dirty[payload.id] = payload
        elif topic == "marker" and isinstance(payload, Marker):
            self._send_all({"type": "marker", "marker": payload.to_dict()})
        elif topic == "marker_deleted":
            self._send_all({"type": "marker_deleted", "id": payload})
        elif topic == "alarms":
            self._send_all({"type": "alarms", "alarms": payload,
                            "sounding": self.hub.alarms.sounding})
        elif topic == "device" and isinstance(payload, Device):
            self._send_all({"type": "device", "device": payload.to_dict()})
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
            entities = [e.to_dict(now, stale_after) for e in self._dirty.values()]
            self._dirty.clear()
            self._send_all({"type": "states", "now": now, "entities": entities,
                            "site": {**self.hub.site_meta, "time": self.hub.site_time(now)}})


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
    app.add_middleware(BodySizeLimitMiddleware, default_limit=body_limit, overrides=body_limit_overrides)
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

    # -------------------------------------------------------- public API
    @app.get("/api/info")
    async def info(request: Request):
        return {
            "version": __version__,
            "build": {**build_info(), "channel": updater.effective_channel(),
                      "managed": updater.managed, "supervised": updater.supervised},
            "site": hub.config.site.name,
            "time": hub.site_time(),
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
        return hub.add_marker(body.label, source).to_dict()

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

    @app.get("/api/admin/state", dependencies=[Depends(require_admin)])
    async def admin_state():
        cfg = hub.config.model_dump(mode="json")
        cfg.pop("admin", None)
        for dev in cfg.get("esphome_devices", []):
            dev["noise_psk"] = "set" if dev.get("noise_psk") else ""
        esp = hub.integrations.get("esphome")
        return {
            "config": cfg,
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
            # For the "Edit cards" panel: the cards this build knows, in picker order, and the
            # defaults a new dashboard gets for each layout.
            "cards": {"known": list(cards_mod.KNOWN_CARDS),
                      "defaults": {layout: cards_mod.default_cards(layout) for layout in cards_mod.LAYOUT_DEFAULTS}},
            "stages": known_stages(),
        }

    def known_stages() -> list[str]:
        """Stage names already in use, for the dashboard Stage field's suggestions: device areas
        and other dashboards' stages (schedule item stages join here once schedules exist)."""
        names = [d.area for d in hub.devices.values() if d.id != "site"]
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
        old_tz = hub.config.site.timezone
        hub.config.site = body
        if body.timezone != old_tz:
            hub.site_time_changed(old_tz, body.timezone)  # re-bases the schedule (WP7)
        hub.save_config()
        return body

    @app.post("/api/admin/esphome/adopt", dependencies=admin_deps)
    async def adopt(body: AdoptBody):
        device_id = slugify(body.id or body.name or body.host.split(".")[0])
        if device_id == "site" or device_id in hub.devices:
            raise HTTPException(409, f"Device id '{device_id}' is already in use")
        cfg = EsphomeDeviceConfig(id=device_id, host=body.host.strip(), port=body.port,
                                  name=body.name.strip(), area=body.area.strip(),
                                  noise_psk=body.noise_psk.strip())
        try:
            await esphome().adopt(cfg)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": cfg.id}

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
        await esphome().update(device_id, body.name, body.area)
        return {"ok": True}

    @app.delete("/api/admin/devices/{device_id}", dependencies=admin_deps)
    async def delete_device(device_id: str):
        if device_id not in hub.devices or device_id == "site":
            raise HTTPException(404, "No such device")
        await esphome().remove(device_id)
        return {"ok": True}

    @app.put("/api/admin/entities/{entity_id}", dependencies=admin_deps)
    async def put_entity(entity_id: str, body: EntitySettings):
        if entity_id not in hub.entities:
            raise HTTPException(404, "No such entity")
        hub.config.entities[entity_id] = body
        hub.save_config()
        entity = hub.entities[entity_id]
        if entity.raw_value is not None and not entity.derived:
            hub.update_state(entity_id, entity.raw_value, entity.updated)
        return body

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
            # A save that doesn't send cards/stage (today's admin page) keeps what the dashboard
            # has; only a new dashboard gets its layout's default cards.
            for key in ("cards", "stage"):
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
        queue: asyncio.Queue = asyncio.Queue()
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
                msg = json.loads(raw)
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "add_marker" and (admin or dashboard_allows(dashboard, "marker")):
                    hub.add_marker(str(msg.get("label", "Marker")),
                                   "admin" if admin else f"dashboard:{dashboard}")
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
