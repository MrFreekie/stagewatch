"""FastAPI app: static dashboards, REST API, and a WebSocket live feed.

Roles:
  user  -- anyone on the network: view dashboards; add markers / ack alarms
           only where that dashboard allows it.
  admin -- PIN-authenticated: configure devices, thresholds, dashboards,
           outputs and shows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .. import __version__
from ..core.config import (
    Dashboard, EntitySettings, EsphomeDeviceConfig, OscOutConfig, SiteConfig, Threshold,
)
from ..core.hub import Hub
from ..core.model import Device, Entity, Marker, slugify
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


class DashboardBody(Dashboard):
    """PUT /api/admin/dashboards: strict card list (loading a saved config is lenient)."""

    @field_validator("cards", mode="before")
    @classmethod
    def _cards(cls, v):
        problem = cards_mod.strict_cards_error(v)
        if problem:
            raise ValueError(problem)
        return v


class ShowBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


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
                            "site": {**self.hub.site_meta}})


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
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

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
        return FileResponse(STATIC / "index.html")

    @app.get("/d/{slug}", include_in_schema=False)
    async def dashboard_page(slug: str):
        if hub.config.dashboard(slug) is None:
            return RedirectResponse("/")
        return FileResponse(STATIC / "dashboard.html")

    @app.get("/admin", include_in_schema=False)
    async def admin_page():
        return FileResponse(STATIC / "admin.html")

    # -------------------------------------------------------- public API
    @app.get("/api/info")
    async def info(request: Request):
        return {
            "version": __version__,
            "build": {**build_info(), "channel": updater.effective_channel(),
                      "managed": updater.managed, "supervised": updater.supervised},
            "site": hub.config.site.name,
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
            "shows": hub.recorder.shows(),
            "alarm_log": hub.recorder.alarm_log(),
        }

    @app.put("/api/admin/site", dependencies=admin_deps)
    async def put_site(body: SiteConfig):
        hub.config.site = body
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

    @app.post("/api/admin/shows", dependencies=admin_deps)
    async def new_show(body: ShowBody):
        show = hub.recorder.start_show(body.name.strip())
        hub.bus.publish("show", show)
        return show

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
        """For the wall display footer only: one LAN address + the dashboard path (no list of
        interfaces, nothing else).  Other layouts get nothing."""
        d = hub.config.dashboard(slug)
        if d is None or d.layout != "wall":
            return {"url": ""}
        addrs = await asyncio.to_thread(lan_addresses)
        return {"url": netinfo.connect_urls(addrs[:1], server_port(request), d.slug)["ip"][0] if addrs else ""}

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
