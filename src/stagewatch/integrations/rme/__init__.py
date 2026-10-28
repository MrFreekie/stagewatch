"""RME interfaces (12Mic and similar AVB units): the health of each unit the admin added, on the RME
interfaces card.

Read-only. Stagewatch listens to each unit's status socket and sends it nothing at all: it cannot change
gain, 48 V, a preset, routing or the clock. It records the unit's core temperature and its two power
supplies as Equipment readings (never part of a site average), and keeps the clock source, preset, AVB
streams and the unit's own "soundness" words for the card. Changes are noted on the timeline as they
happen while Stagewatch is watching; nothing is filled in for a time the unit was away.

STATUS: written from two captures of one RME 12Mic (firmware 2.0.1). Other RME units are untested. RME
units have no login, so anyone on their network can change them: keep that network separate. Use
``--emulate`` to see two simulated units, including a power supply drop, a clock change, a gap and an
unknown soundness word.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Callable

from ...core.config import RME_MAX_UNITS, RmeDeviceConfig, _plain_text
from ...core.model import Device, Entity, Kind, Status, UNITS, slugify
from ...core.plugin import Integration, Manifest
from ..smaart.client import is_local_address
from . import mapping, parse
from .client import RmeClient, RmeError, TEXT, probe as real_probe, validate_host
from .emulate import DEMO_UNITS, EmulatedRmeSource
from .mapping import MarkerPolicy
from .parse import FrameResult, RmeState, View
from .source import RmeSource

log = logging.getLogger(__name__)

CARD_ID = "rme_units"
TOPIC = "rme_units"
TICK_S = 1.0
CONFIRM_S = 30.0              # an unchanged reading is recorded again this often while frames flow
MAX_UNKNOWN_LOGGED = 50

MANIFEST = Manifest(
    domain="rme",
    name="RME interfaces",
    version="0.1.0",
    description="Shows the health of RME 12Mic units on the RME interfaces card: core temperature, the two "
                "power supplies, clock source and lock, preset (active and loaded), AVB streams and the unit's "
                "own soundness words. It only listens to each unit's status socket and never sends it anything, "
                "so it cannot change gain, 48 V, presets, routing or the clock. Changes are marked on the "
                "timeline live; nothing is back-filled. Tried on one 12Mic (firmware 2.0.1) only; other RME "
                "units are untested. RME units have no login, so anyone on their network can change them. "
                "Equipment: never part of the site averages. Input gain, labels and level meters are not read.",
    tier="experimental",
    direction="in",
    iot_class="local_push",
    protocols=("WebSocket (JSON)",),
    entity_kinds=("temperature", "contact"),
    vendors=("RME",),
)


class InUse(Exception):
    """The unit, its name or the room for another unit is not available. The message is fixed text."""


@dataclass
class _Unit:
    cfg: RmeDeviceConfig
    key: tuple
    policy: MarkerPolicy | None = None
    source: RmeSource | None = None
    view: View | None = None
    phase: str = "starting"          # starting, waiting, up, down
    detail: str = ""
    lost_live: frozenset = frozenset()
    ever_up: bool = False
    outage_at: float | None = None
    last_seen: float | None = None   # clock seconds of the last frame while connected
    registered: tuple | None = None  # (name, model, area) last sent to the hub
    entities: set = field(default_factory=set)
    pushed: dict = field(default_factory=dict)      # suffix -> (value, clock seconds)
    alarms: set = field(default_factory=set)
    last_status: tuple | None = None

    @property
    def device_id(self) -> str:
        return f"rme_{self.cfg.id}"

    def name(self) -> str:
        return self.cfg.name or (self.view.name if self.view else "") or "RME unit"


SourceFactory = Callable[["RmeIntegration", _Unit], RmeSource]


def seed_emulate_rme(config) -> bool:
    """Emulate mode only (called from __main__): a fresh emulate config gets the two simulated units and the
    RME interfaces card on the stock wall dashboard. Does nothing once units have been saved or any
    dashboard has the card. Returns True if it changed the config."""
    if "rme_devices" in config.model_fields_set or config.rme_devices \
            or any(CARD_ID in d.cards for d in config.dashboards):
        return False
    config.rme_devices = [
        RmeDeviceConfig(id=slugify(u["name"]), host=u["host"], name=u["name"], area=("Stage L", "Monitors")[u["index"]],
                        serial=u["serial"]) for u in DEMO_UNITS]
    for d in config.dashboards:
        if d.slug == "wall":
            d.cards = [*d.cards[:1], CARD_ID, *d.cards[1:]]
    return True


def probe_message(info: dict) -> str:
    """"RME 12Mic, firmware 2.0.1, named 'Stage L'. Reading works." Only the model, firmware and name the unit
    gave (already cleaned): no serial number, no address."""
    model = info.get("model") or "unit"
    parts = [model if model.upper().startswith("RME") else f"RME {model}"]
    if info.get("firmware"):
        parts.append(f"firmware {info['firmware']}")
    if info.get("name"):
        parts.append(f"named '{info['name']}'")
    return ", ".join(parts) + ". Reading works."


def emulated_probe(ip: str) -> dict:
    """What "Test" says in emulate mode: a simulated unit, with no network at all."""
    return {"model": "12Mic", "firmware": "2.0.1 (simulated)", "name": "Simulated 12Mic",
            "serial": "SIM-" + "".join(ch for ch in ip if ch.isdigit())[:20]}


class RmeIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, source_factory: SourceFactory | None = None,
                 probe_fn=None, address_ok: Callable = is_local_address,
                 clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep) -> None:
        super().__init__(hub, emulate)
        self._factory = source_factory or self._default_source
        self._probe_fn = probe_fn or real_probe
        self._address_ok = address_ok
        self._clock, self._sleep = clock, sleep
        self._units: dict[str, _Unit] = {}
        self._lock = asyncio.Lock()
        self._last_sent: dict | None = None
        self._ticker: asyncio.Task | None = None
        self._apply_task: asyncio.Task | None = None
        self._dirty = False
        self._stopped = True
        self._unknown_seen: set[str] = set()
        hub.bus.subscribe("config", self._on_config)

    # ------------------------------------------------------------- lifecycle
    def _default_source(self, owner: "RmeIntegration", unit: _Unit) -> RmeSource:
        on_state = partial(self._on_state, unit)
        on_link = partial(self._on_link, unit)
        if self.emulate:
            idx = list(self.hub.config.rme_devices).index(unit.cfg) if unit.cfg in self.hub.config.rme_devices else 0
            return EmulatedRmeSource(on_state, on_link, index=idx % 2, name=unit.cfg.name or "Simulated 12Mic",
                                     serial=unit.cfg.serial or "SIM-0000", clock=self._clock, sleep=self._sleep)
        return RmeClient(lambda: (unit.cfg.host, unit.cfg.port), on_state, on_link,
                         expected_serial_fn=lambda: unit.cfg.serial, address_ok=self._address_ok,
                         clock=self._clock, sleep=self._sleep)

    async def start(self) -> None:
        self._stopped = False
        await self.apply()
        self._ticker = asyncio.create_task(self._tick_loop(), name="rme-tick")

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._ticker, self._apply_task):
            if task:
                task.cancel()
        await asyncio.gather(*[t for t in (self._ticker, self._apply_task) if t], return_exceptions=True)
        self._ticker = self._apply_task = None
        async with self._lock:
            for unit in self._units.values():
                await self._stop_source(unit)

    def _on_config(self, _topic: str, _payload) -> None:
        if self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._dirty = True
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = loop.create_task(self._apply_loop(), name="rme-apply")

    async def _apply_loop(self) -> None:
        while self._dirty and not self._stopped:
            self._dirty = False
            await self.apply()

    async def apply(self) -> None:
        """Make the running units match the saved list. Safe to call any time; never raises."""
        async with self._lock:
            try:
                await self._reconcile()
            except Exception:  # noqa: BLE001 - this card must never stop the hub
                log.exception("Could not start or stop the RME sources")
        self.publish_once()

    async def _reconcile(self) -> None:
        wanted = {c.id: c for c in self.hub.config.rme_devices}
        for uid in [u for u in self._units if u not in wanted]:
            await self._drop(uid)
        for uid, cfg in wanted.items():
            unit = self._units.get(uid)
            key = (self.emulate, cfg.host, cfg.port)
            if unit is None:
                unit = self._units[uid] = _Unit(cfg, key)
                unit.policy = MarkerPolicy(partial(self._make_marker, unit), self._clock)
                self._register_device(unit)
                await self._start_source(unit)
            elif unit.key != key:
                await self._stop_source(unit)
                self._reset_unit(unit)
                unit.cfg, unit.key = cfg, key
                self._register_device(unit)
                await self._start_source(unit)
            else:
                unit.cfg = cfg
                if unit.view is not None:
                    self._refresh(unit)
                else:
                    self._register_device(unit)

    def _reset_unit(self, unit: _Unit) -> None:
        self._clear_alarms(unit)
        unit.view, unit.phase, unit.detail = None, "starting", ""
        unit.lost_live, unit.outage_at, unit.ever_up, unit.last_seen = frozenset(), None, False, None
        unit.pushed.clear()
        unit.last_status = None

    async def _start_source(self, unit: _Unit) -> None:
        unit.source = self._factory(self, unit)
        await unit.source.start()

    async def _stop_source(self, unit: _Unit) -> None:
        source, unit.source = unit.source, None
        if source is not None:
            try:
                await source.stop()
            except Exception:  # noqa: BLE001
                log.exception("Could not stop an RME source")

    async def _drop(self, uid: str) -> None:
        unit = self._units.pop(uid)
        await self._stop_source(unit)
        self._clear_alarms(unit)
        self.hub.remove_device(unit.device_id)        # its history stays

    # --------------------------------------------------------------- hub side
    def _register_device(self, unit: _Unit) -> None:
        model = (unit.view.model if unit.view else "") or ("Simulated 12Mic" if self.emulate else "")
        shown = (unit.name(), model, unit.cfg.area)
        if unit.registered == shown and unit.device_id in self.hub.devices:
            return
        self.hub.register_device(Device(
            unit.device_id, shown[0], "rme", "RME" if not self.emulate else "Stagewatch", model,
            area=unit.cfg.area, status=Status.INITIALIZING, status_detail=mapping.DETAIL_CONNECTING,
            category="service", role="equipment"))
        unit.registered = shown

    def _make_marker(self, unit: _Unit, text: str, hidden: bool) -> None:
        try:
            marker = self.hub.add_marker(text, "rme")
            if hidden:
                self.hub.update_marker(marker.id, hidden=True, current_show_only=False)
        except Exception:  # noqa: BLE001 - a marker must never stop the readings
            log.exception("Could not add an RME marker")

    def _emit(self, unit: _Unit, key: str, text: str, hidden: bool = False) -> None:
        if unit.policy is not None:
            unit.policy.emit(key, f"RME {unit.name()}: {text}", hidden)

    def _register_entities(self, unit: _Unit, values: dict) -> None:
        for suffix, name, kind, decimals in mapping.ENTITY_SPECS:
            if suffix in values and suffix not in unit.entities:
                self.hub.register_entity(Entity(f"{unit.device_id}.{suffix}", unit.device_id, name, kind,
                                                UNITS.get(kind, ""), decimals, locked_equipment=True))
                unit.entities.add(suffix)

    def _push_entities(self, unit: _Unit, now: float, force: bool = False) -> None:
        values = mapping.entity_values(unit.view)
        self._register_entities(unit, values)
        beat = min(CONFIRM_S, self.hub.config.site.stale_after_s / 2)
        for suffix, value in values.items():
            prev = unit.pushed.get(suffix)
            if force or prev is None or prev[0] != value or now - prev[1] >= beat:
                self.hub.update_state(f"{unit.device_id}.{suffix}", value)
                unit.pushed[suffix] = (value, now)

    def _clear_alarms(self, unit: _Unit) -> None:
        self._set_alarms(unit, {})

    def _set_alarms(self, unit: _Unit, wanted: dict) -> None:
        changes = []
        name = unit.name()
        now = time.time()
        for key, (level, text) in wanted.items():
            change = self.hub.alarms.set_condition(f"rme:{unit.cfg.id}:{key}", True, level, f"{name}: {text}", now, silent=True)
            if change:
                changes.append(change)
        for key in unit.alarms - set(wanted):
            change = self.hub.alarms.set_condition(f"rme:{unit.cfg.id}:{key}", False, 0, "", now)
            if change:
                changes.append(change)
        unit.alarms = set(wanted)
        if changes:
            self.hub._alarm_changed(changes)

    def _refresh(self, unit: _Unit, force: bool = False) -> None:
        """After a frame that changed something: device name, readings, status and alarms."""
        view = unit.view
        if view is None:
            return
        self._register_device(unit)
        now = self._clock()
        if unit.phase == "up":
            self._push_entities(unit, now, force)
            lvl = mapping.overall_level(view, unit.lost_live)
            status = (Status.OK, mapping.DETAIL_OK) if lvl == mapping.LEVEL_NONE else \
                (Status.COMPROMISED, mapping.status_detail(view, unit.lost_live))
            if (status, unit.phase) != unit.last_status:
                unit.last_status = (status, unit.phase)
                self.hub.set_device_status(unit.device_id, status[0], status[1], silent_alarm=True)
            self._set_alarms(unit, mapping.conditions(view, unit.lost_live))

    # -------------------------------------------------------------- callbacks
    def _on_link(self, unit: _Unit, up: bool, detail: str = "", waiting: bool = False) -> None:
        if self._units.get(unit.cfg.id) is not unit:
            return
        if up:
            unit.phase = "up"
            unit.detail = ""
            return
        if waiting:
            if unit.phase != "up":
                unit.phase = "waiting"
            unit.detail = detail
            return
        was_up = unit.phase == "up"
        unit.phase, unit.detail = "down", detail
        unit.last_status = None
        self._clear_alarms(unit)                # the last answer is old now; the offline notice takes over
        if was_up:
            unit.outage_at = self._clock()
            self._emit(unit, "link", "went offline")
        elif unit.ever_up and unit.outage_at is None:
            unit.outage_at = self._clock()
        self.hub.set_device_status(unit.device_id, Status.MISSING, detail or TEXT["unreachable"], silent_alarm=True)
        self.publish_once()

    def _on_state(self, unit: _Unit, state: RmeState, result: FrameResult) -> None:
        if self._units.get(unit.cfg.id) is not unit:
            return
        now = self._clock()
        unit.last_seen = now
        if result.kind == "ignored" or (result.kind == "delta" and not result.changed):
            return
        view = parse.make_view(state.data)
        if view is None:
            return
        old = unit.view
        unit.view = view
        unit.phase = "up"
        if result.kind == "baseline":
            unit.lost_live = frozenset()
            if unit.outage_at is not None:
                self._emit(unit, "link", mapping.back_text(now - unit.outage_at))
                unit.outage_at = None
            unit.ever_up = True
        elif result.kind == "delta" and old is not None:
            unit.lost_live = mapping.lost_live_after(old, view, unit.lost_live)
            for ev in mapping.marker_events(old, view):
                self._emit(unit, ev.key, ev.text, ev.hidden)
        self._note_unknown(view)
        self._refresh(unit, force=result.kind != "delta")
        self.publish_once()

    def _note_unknown(self, view: View) -> None:
        for item in view.sound:
            if not item.known and item.token not in self._unknown_seen and len(self._unknown_seen) < MAX_UNKNOWN_LOGGED:
                self._unknown_seen.add(item.token)
                log.info("RME: an unrecognised soundness word (%s), %d different seen this run", item.token or "-", len(self._unknown_seen))

    # ------------------------------------------------------------- publishing
    def _message(self) -> dict:
        now = self._clock()
        units = []
        for cfg in self.hub.config.rme_devices:
            unit = self._units.get(cfg.id)
            if unit is None:
                continue
            frame_age = None if unit.last_seen is None else now - unit.last_seen
            text_age = None
            if unit.source is not None and unit.source.last_text_at is not None:
                text_age = now - unit.source.last_text_at
            units.append(mapping.unit_message(cfg.id, unit.name(), unit.view, phase=unit.phase,
                                              frame_age_s=frame_age, text_age_s=text_age, lost_live=unit.lost_live))
        return {"units": units}

    def snapshot(self) -> dict | None:
        """The public message now, or None while no unit is set up."""
        if not self._units:
            return None
        try:
            return self._message()
        except Exception:  # noqa: BLE001 - never break the snapshot over a card
            log.exception("RME snapshot failed")
            return None

    def publish_once(self) -> None:
        msg = self.snapshot()
        if msg is None:
            if self._last_sent is not None:
                self._last_sent = None
                self.hub.bus.publish(TOPIC, {"units": []})
            return
        if msg != self._last_sent:
            self._last_sent = msg
            self.hub.bus.publish(TOPIC, msg)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("RME tick failed")

    def tick(self) -> None:
        """Once a second: note the last frame of each connected unit, re-confirm quiet readings (only while
        frames are flowing, which the client guarantees by dropping a silent link) and republish."""
        now = self._clock()
        for unit in self._units.values():
            source = unit.source
            if unit.phase == "up" and source is not None and source.last_frame_at is not None:
                unit.last_seen = max(unit.last_seen or 0.0, source.last_frame_at)
                self._push_entities(unit, now)
        self.publish_once()

    # ----------------------------------------------------------------- admin
    async def _probe(self, ip: str, port: int) -> dict:
        if self.emulate:
            return emulated_probe(ip)
        return await self._probe_fn(ip, port, address_ok=self._address_ok)

    async def test(self, host: object, port: int = 80) -> dict:
        """Admin "Test": model, firmware and name of what is at this address (never the serial number). The
        address is checked first. Raises RmeError."""
        ip = validate_host(host, self._address_ok)
        info = await self._probe(ip, port)
        known = any(c.host == ip for c in self.hub.config.rme_devices)
        return {"model": info["model"], "firmware": info["firmware"], "name": info["name"], "already_added": known}

    async def add(self, host: object, port: int, name: str, area: str) -> RmeDeviceConfig:
        """Admin "Add": reads the unit once (for its name and serial number) and starts listening. Raises
        RmeError or InUse (fixed text)."""
        async with self._lock:
            cfgs = self.hub.config.rme_devices
            if len(cfgs) >= RME_MAX_UNITS:
                raise InUse(f"There is room for {RME_MAX_UNITS} RME units. Remove one first")
            ip = validate_host(host, self._address_ok)
            if any(c.host == ip for c in cfgs):
                raise InUse("That unit has already been added")
            info = await self._probe(ip, port)
            if info["serial"] and any(c.serial == info["serial"] for c in cfgs):
                raise InUse("That unit has already been added")
            typed = _plain_text(name, 40)
            display = typed or _plain_text(info["name"], 40) or "RME unit"
            base = slugify(display)[:36]
            uid, n = base, 2
            taken = {c.id for c in cfgs}
            while uid in taken or f"rme_{uid}" in self.hub.devices:
                uid, n = f"{base}_{n}", n + 1
            cfg = RmeDeviceConfig(id=uid, host=ip, port=port, name=typed, area=_plain_text(area, 40),
                                  serial=info["serial"])
            cfgs.append(cfg)
            self.hub.save_config()
        await self.apply()
        return cfg

    async def update(self, uid: str, *, name: str | None = None, area: str | None = None,
                     record_input_changes: bool | None = None, host: object = None, port: int | None = None,
                     replace_unit: bool = False) -> None:
        """Admin "Save". A new address or port must answer as the same unit unless ``replace_unit`` is set.
        Raises LookupError, RmeError or InUse (nothing is changed)."""
        async with self._lock:
            cfg = next((c for c in self.hub.config.rme_devices if c.id == uid), None)
            if cfg is None:
                raise LookupError
            new_host = cfg.host if host is None else validate_host(host, self._address_ok)
            new_port = cfg.port if port is None else port
            serial = cfg.serial
            if new_host != cfg.host and any(c.host == new_host for c in self.hub.config.rme_devices if c is not cfg):
                raise InUse("Another unit already uses that address")
            if (new_host, new_port) != (cfg.host, cfg.port) or replace_unit:
                info = await self._probe(new_host, new_port)
                if replace_unit:
                    serial = info["serial"]
                elif cfg.serial and info["serial"] and info["serial"] != cfg.serial:
                    raise RmeError("other_unit")
            if name is not None:
                cfg.name = _plain_text(name, 40)
            if area is not None:
                cfg.area = _plain_text(area, 40)
            if record_input_changes is not None:
                cfg.record_input_changes = record_input_changes
            cfg.host, cfg.port, cfg.serial = new_host, new_port, serial
            self.hub.save_config()
        await self.apply()

    async def remove(self, uid: str) -> None:
        async with self._lock:
            cfg = next((c for c in self.hub.config.rme_devices if c.id == uid), None)
            if cfg is None:
                raise LookupError
            self.hub.config.rme_devices.remove(cfg)
            self.hub.save_config()
            if uid in self._units:
                await self._drop(uid)
        self.publish_once()

    def admin_status(self) -> dict:
        """Admin page: each unit with its address (admin only; never in a public message)."""
        units = []
        now = self._clock()
        for cfg in self.hub.config.rme_devices:
            unit = self._units.get(cfg.id)
            device = self.hub.devices.get(f"rme_{cfg.id}")
            hint = ""
            if unit is not None and unit.source is not None and unit.phase != "up":
                try:
                    hint = unit.source.hint()
                except Exception:  # noqa: BLE001
                    log.exception("RME hint failed")
            view = unit.view if unit else None
            units.append({
                "id": cfg.id, "name": unit.name() if unit else (cfg.name or "RME unit"), "area": cfg.area,
                "host": cfg.host, "port": cfg.port, "serial_set": bool(cfg.serial),
                "record_input_changes": cfg.record_input_changes,
                "status": device.status.value if device else "off",
                "detail": device.status_detail if device else "",
                "phase": unit.phase if unit else "off", "hint": hint,
                "model": view.model if view else "", "firmware": view.firmware if view else "",
                "last_seen_age_s": mapping.bucket_age(None if not unit or unit.last_seen is None else now - unit.last_seen),
            })
        return {"units": units, "max_units": RME_MAX_UNITS, "emulate": self.emulate}

    def info(self) -> dict:
        return {**super().info(), "units": len(self._units)}
