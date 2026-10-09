"""DirectOut GLOBCON level meters: the live levels GLOBCON reports for the strips of a controller,
shown on the GLOBCON levels card.

Read-only. Stagewatch listens to GLOBCON and sends it only Ping, GET, SUBSCRIBE and UNSUBSCRIBE (and a
log-in, only if you set a password and GLOBCON asks for one); it can never send SET, UPDATE or ACTION,
so it cannot move a fader, mute, solo, change a layer or run a function (protocol.py pins this). Levels
are shown exactly as GLOBCON reports them: no peak or RMS claim, no averaging, no smoothing. A strip
GLOBCON reports as -250 (no signal) is shown empty, never as zero. Live only: nothing is recorded, so
there is no history and no back-fill. If GLOBCON goes quiet or away the last levels stay on screen,
frozen and marked with their age.

The source runs only while some dashboard has the GLOBCON levels card.

STATUS: UNVERIFIED on a real GLOBCON. Written from GLOBCON's own web app and checked against one real
capture of it; never run against a live GLOBCON by Stagewatch. Use ``--emulate`` to see the card.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ...core.model import Device, Status
from ...core.plugin import Integration, Manifest
from . import mapping, protocol
from .client import GlobconClient
from .emulate import EmulatedGlobconSource
from .mapping import GlobconState
from .protocol import Value
from .source import GlobconSource

log = logging.getLogger(__name__)

DEVICE_ID = "globcon"
CARD_ID = "globcon_meters"
TOPIC = "globcon_meters"
PUBLISH_EVERY_S = 0.25        # 4 updates a second to browsers; GLOBCON itself sends 10 a second
TICK_S = 0.25

MANIFEST = Manifest(
    domain="globcon",
    name="DirectOut GLOBCON level meters",
    version="0.1.0",
    description="Shows the live level meters GLOBCON reports for the strips of one controller (and the "
                "controller's name and current layer label) on the GLOBCON levels card. Reads only "
                "GLOBCON's names, strip labels, layer and meter levels. It sends GLOBCON only Ping, GET, "
                "SUBSCRIBE and UNSUBSCRIBE (and a log-in if you set a password and GLOBCON asks for "
                "one), never SET, UPDATE or ACTION, so it cannot move a fader, mute, solo, change a "
                "layer or run a function. Levels are shown as GLOBCON reports them, with no peak or RMS "
                "claim; nothing is recorded. NOT TESTED against a live GLOBCON: written from GLOBCON's "
                "own web app and one real capture of it; the simulated source (--emulate) works. "
                "Equipment: never part of the site averages.",
    tier="experimental",
    direction="in",
    protocols=("WebSocket", "Protobuf"),
    entity_kinds=(),
    vendors=("DirectOut (GLOBCON)",),
)

SourceFactory = Callable[["GlobconIntegration"], GlobconSource]


def seed_emulate_globcon(config) -> bool:
    """Emulate mode only (called from __main__): a fresh emulate config gets the GLOBCON levels card on
    the stock wall dashboard so it can be seen offline. Does nothing once the settings have been saved or any dashboard has it.
    Returns True if it changed the config."""
    if "globcon" in config.model_fields_set or any(CARD_ID in d.cards for d in config.dashboards):
        return False
    changed = False
    for d in config.dashboards:
        if d.slug == "wall":
            d.cards = [*d.cards[:1], CARD_ID, *d.cards[1:]]
            changed = True
    return changed


def wanted_controllers(config) -> list[int]:
    """The 0-based controllers some dashboard with the card shows, in order, once each."""
    out: list[int] = []
    for d in config.dashboards:
        if CARD_ID in d.cards and d.globcon.controller - 1 not in out:
            out.append(d.globcon.controller - 1)
    return out


class GlobconIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, source_factory: SourceFactory | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        super().__init__(hub, emulate)
        self._factory = source_factory or self._default_source
        self._clock = clock
        self._source: GlobconSource | None = None
        self._key: tuple | None = None
        self._lock = asyncio.Lock()
        self._state = GlobconState()
        self._registered = False
        self._link_up = False
        self._detail = ""
        self._wanted: list[int] = []
        self._last_sent: dict | None = None
        self._publisher: asyncio.Task | None = None
        self._apply_task: asyncio.Task | None = None
        self._dirty = False
        self._stopped = True
        self._last_status = ""
        hub.bus.subscribe("config", self._on_config)

    # ------------------------------------------------------------- lifecycle
    def _default_source(self, owner: "GlobconIntegration") -> GlobconSource:
        if self.emulate:
            return EmulatedGlobconSource(self._values, self._meters, self._link)
        cfg = self.hub.config.globcon
        return GlobconClient(lambda: (cfg.host, cfg.port) if cfg.host and cfg.port else None,
                             self._values, self._meters, self._link,
                             password_fn=lambda: self.hub.config.globcon.password)

    async def start(self) -> None:
        self._stopped = False
        await self.apply()
        self._publisher = asyncio.create_task(self._publish_loop(), name="globcon-publish")

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._publisher, self._apply_task):
            if task:
                task.cancel()
        await asyncio.gather(*[t for t in (self._publisher, self._apply_task) if t], return_exceptions=True)
        self._publisher = self._apply_task = None
        async with self._lock:
            await self._stop_source()

    def _on_config(self, _topic: str, _payload) -> None:
        """A config save: look again (the card may have been added or removed, or the address
        changed). Coalesced; harmless when the integration isn't running."""
        if self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._dirty = True
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = loop.create_task(self._apply_loop(), name="globcon-apply")

    async def _apply_loop(self) -> None:
        while self._dirty and not self._stopped:
            self._dirty = False
            await self.apply()

    async def apply(self, restart: bool = False) -> None:
        """Make the running state match the saved settings. Safe to call any time; never raises."""
        async with self._lock:
            try:
                cfg = self.hub.config.globcon
                self._wanted = wanted_controllers(self.hub.config)
                run = bool(self._wanted and (self.emulate or (cfg.host and cfg.port)))
                if not run:
                    await self._stop_source()
                    if self._registered:
                        self._registered = False
                        self.hub.remove_device(DEVICE_ID)
                    self._state = GlobconState()
                    self._last_sent = None
                    return
                key = (self.emulate, cfg.host, cfg.port, cfg.password)
                if self._source is None or key != self._key or restart:
                    await self._stop_source()
                    self._register_device()
                    self._state, self._link_up, self._detail = GlobconState(), False, ""
                    self._source = self._factory(self)
                    self._key = key
                    self._source.set_wanted(self._wanted)
                    await self._source.start()
                else:
                    self._source.set_wanted(self._wanted)
            except Exception:  # noqa: BLE001 - a meter card must never stop the hub
                log.exception("Could not start or stop the GLOBCON source")

    async def _stop_source(self) -> None:
        source, self._source, self._key = self._source, None, None
        if source is not None:
            try:
                await source.stop()
            except Exception:  # noqa: BLE001
                log.exception("Could not stop the GLOBCON source")

    def _register_device(self) -> None:
        self.hub.register_device(Device(
            DEVICE_ID, "GLOBCON (simulated)" if self.emulate else "GLOBCON", "globcon",
            "DirectOut" if not self.emulate else "Stagewatch",
            "GLOBCON" if not self.emulate else "Simulated GLOBCON",
            status=Status.INITIALIZING, status_detail="Connecting", category="service", role="equipment"))
        self._registered = True

    # -------------------------------------------------------------- callbacks
    def _link(self, up: bool, detail: str) -> None:
        if not self._registered:
            return
        self._link_up = up
        self._detail = detail
        self._last_status = ""
        if up:
            if self.hub.devices[DEVICE_ID].status != Status.OK:
                self.hub.set_device_status(DEVICE_ID, Status.INITIALIZING, detail)
        else:
            self.hub.set_device_status(DEVICE_ID, Status.MISSING, detail, silent_alarm=True)   # a notice, never a sound

    def _values(self, values: list[Value]) -> None:
        if not self._registered:
            return
        for v in values:
            mapping.apply_value(self._state, v)

    def _meters(self, controller: int, blob: bytes) -> None:
        if not self._registered or controller not in self._wanted:
            return
        mapping.apply_meters(self._state, controller, blob, self._clock())

    # ------------------------------------------------------------- publishing
    def _message(self) -> dict:
        return mapping.public_message(self._state, self._wanted, self._link_up, self._clock(),
                                      "Simulated GLOBCON" if self.emulate else "GLOBCON")

    def snapshot(self) -> dict | None:
        """The public message now, or None while no dashboard has the card."""
        if not self._registered:
            return None
        try:
            return self._message()
        except Exception:  # noqa: BLE001 - never break the snapshot over a card
            log.exception("GLOBCON snapshot failed")
            return None

    def _update_status(self, msg: dict) -> None:
        """Device status follows the feed: OK only while levels arrive. Only while the link is up (the
        link callback owns the down and up changes)."""
        status = msg["status"]
        if not self._registered or not self._link_up or status == self._last_status:
            return
        self._last_status = status
        note = "" if self.emulate else " (not yet tested against a live GLOBCON)"
        if status == "ok":
            self.hub.set_device_status(DEVICE_ID, Status.OK, mapping.STATUS_TEXT["ok"] + note)
        elif status == "stale":
            self.hub.set_device_status(DEVICE_ID, Status.COMPROMISED, mapping.STATUS_TEXT["stale"], silent_alarm=True)
        elif status == "waiting":
            self.hub.set_device_status(DEVICE_ID, Status.INITIALIZING, mapping.STATUS_TEXT["waiting"])

    def publish_once(self) -> None:
        msg = self.snapshot()
        if msg is None:
            return
        self._update_status(msg)
        if msg != self._last_sent:
            self._last_sent = msg
            self.hub.bus.publish(TOPIC, msg)
    async def _publish_loop(self) -> None:
        while True:
            await asyncio.sleep(PUBLISH_EVERY_S)
            try:
                self.publish_once()
            except Exception:  # noqa: BLE001
                log.exception("GLOBCON publish failed")

    # ----------------------------------------------------------------- admin
    def admin_status(self) -> dict:
        """Admin page: is the card in use and what is running (no addresses)."""
        device = self.hub.devices.get(DEVICE_ID) if self._registered else None
        return {"card_assigned": bool(wanted_controllers(self.hub.config)),
                "running": self._registered,
                "status": device.status.value if device else "off",
                "detail": device.status_detail if device else "",
                "source": self._source.label if self._source else "",
                "verified": bool(self._source.verified) if self._source else False,
                "problem": self._source.problem if self._source else "",
                "controllers": sorted(n + 1 for n in self._wanted)}

    def info(self) -> dict:
        return {**super().info(), **self.admin_status()}
