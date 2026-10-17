"""WitMotion WT901BLE tilt sensor over Bluetooth: roll and pitch, acceleration, battery and the sensor's
own temperature, as Equipment readings. Advisory only: it is a "has it moved since the marker?" trend,
never a safety system, never a load or array-angle measurement. Read-only: it only listens to the
sensor and asks it for two registers (battery, temperature); it never writes a setting, calibrates,
changes the output rate or saves anything to the sensor, and it never pairs (the sensor drops the link
if you try).

No assumed data: when the link is lost or goes quiet every reading becomes a gap (no value, never the
last one, never a zero), a marker is made as it happens, and a second one when the sensor is back.
Nothing is back-filled. The battery percentage is an approximation from the cell voltage and is labelled
so; the temperature is the sensor chip's, not the air's, so it is never used for the environment cards
or the speed of sound.

Readings are stored once a second (the latest roll and pitch, and the peak acceleration of that
second), not at the sensor's 10 Hz.

STATUS: written from WitMotion's BLE 5.0 protocol page and checked against real captures of one
WT901BLECL (advertising as WT901BLE67, firmware 10080.1.8). The Bluetooth link itself (bleak) has not
yet been run on a Raspberry Pi; Windows (including the SYSTEM boot task) is untested. Use ``--emulate``
to see it with no hardware.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from ...core.model import Device, Entity, Kind, Status
from ...core.plugin import Integration, Manifest
from . import parser
from .emulate import EmulatedReader
from .reader import WitmotionReader

log = logging.getLogger(__name__)

DEVICE_ID = "witmotion"
MARKER_SOURCE = "witmotion"
TICK_S = 1.0
STALE_S = 5.0          # no frame this long: the readings are a gap (the link allows ~0.3 s between frames)
SLOW_VALID_S = 180.0   # battery and temperature are asked for every ~45 s; older than this is a gap

MANIFEST = Manifest(
    domain="witmotion",
    name="WitMotion tilt sensor (Bluetooth)",
    version="0.1.0",
    description="Reads a WitMotion WT901BLE (BLE 5.0) tilt and motion sensor over Bluetooth: roll and "
                "pitch, acceleration, link age, battery and the sensor's own temperature. Advisory only: "
                "a trend for 'has it moved since the marker?', never a safety system. Only listens (and "
                "asks for the battery and temperature); it never changes a setting and never pairs. When "
                "the link is lost every reading is a gap and a marker is made, with nothing filled in. "
                "The battery percentage is approximate; the temperature is the sensor chip's, not the "
                "air's. Checked against real captures of one WT901BLECL; the Bluetooth link has not yet "
                "been run on a Raspberry Pi and Windows is untested. Needs the 'bleak' package. "
                "Equipment: never part of the site averages.",
    tier="experimental",
    direction="in",
    iot_class="local_push",
    protocols=("Bluetooth LE GATT",),
    entity_kinds=("generic", "battery", "voltage", "temperature"),
    vendors=("WitMotion",),
)

# Entity id suffix -> (name, kind, unit, decimals). The temperature is the chip's, and says so.
ENTITIES = {
    "tilt_roll": ("Tilt roll (advisory)", Kind.GENERIC, "°", 1),
    "tilt_pitch": ("Tilt pitch (advisory)", Kind.GENERIC, "°", 1),
    "accel_peak": ("Acceleration, peak of each second", Kind.GENERIC, "g", 2),
    "link_age": ("Time since last data", Kind.GENERIC, "s", 1),
    "battery_volts": ("Sensor battery voltage", Kind.VOLTAGE, "V", 2),
    "battery_pct": ("Sensor battery (approximate)", Kind.BATTERY, "%", 0),
    "body_temp": ("Sensor body temperature (chip, not air)", Kind.TEMPERATURE, "°C", 1),
}
FAST = ("tilt_roll", "tilt_pitch", "accel_peak", "link_age")
SLOW = ("battery_volts", "battery_pct", "body_temp")

# Fixed words for the status line (never text built from an exception or an address).
STATUS_TEXT = {
    "searching": "Looking for the sensor",
    "not found": "Sensor not found",
    "connecting": "Connecting",
    "connected": "Connected",
    "lost": "Link lost",
    "error": "Bluetooth problem",
}


class Sensor:
    """One sensor: its device, entities, state and gap handling. Found by its configured Bluetooth
    address (its own name can be changed by crew, so the name is never what finds it); the label is a
    Stagewatch setting and is never written to the sensor."""

    def __init__(self, hub, device_id: str, address: str, label: str) -> None:
        self.hub = hub
        self.device_id = device_id
        self.address = address
        self.label = label
        self.title = label or "Tilt sensor"
        self.reader = None
        self.task: asyncio.Task | None = None
        self.link = "off"
        self._up = False                      # connected right now
        self._had_loss = False                # a loss was marked, so the return gets its own marker
        self._last_frame: float | None = None
        self._roll: float | None = None
        self._pitch: float | None = None
        self._peak_g: float | None = None     # peak total acceleration since the last tick
        self._slow: dict[str, float] = {}     # key -> time its last value arrived
        self._status_sent: tuple[Status, str] | None = None

    def register(self) -> None:
        self.hub.register_device(Device(self.device_id, self.label or "WitMotion tilt sensor", MANIFEST.domain,
                                        "WitMotion", "WT901BLE", "", Status.INITIALIZING, role="equipment"))
        for key, (name, kind, unit, dec) in ENTITIES.items():
            self.hub.register_entity(Entity(f"{self.device_id}.{key}", self.device_id, name, kind, unit, dec,
                                            locked_equipment=True))

    def callbacks(self) -> dict:
        return dict(on_frame=self._on_frame, on_connected=self._on_connected,
                    on_disconnected=self._on_disconnected, on_status=self._on_status)

    def _put(self, key: str, value: float | None, ts: float) -> None:
        self.hub.update_state(f"{self.device_id}.{key}", value, ts)

    # ------------------------------------------------------------------ reader callbacks
    def _on_status(self, text: str) -> None:
        self.link = text
        if not self._up:
            status = Status.INITIALIZING if text in ("searching", "connecting") and not self._had_loss else Status.MISSING
            self._set_status(status, STATUS_TEXT.get(text, ""))

    def _on_connected(self, ts: float) -> None:
        self._up = True
        if self._had_loss:
            self.hub.add_marker(f"{self.title} back (readings missed while it was away are not filled in)", MARKER_SOURCE, ts)
            self._had_loss = False

    def _on_disconnected(self, ts: float) -> None:
        self._up = False
        self._link_lost(ts)

    def _link_lost(self, ts: float) -> None:
        """Every reading becomes a gap, once, and a marker says when it went (as it happens)."""
        for key in ENTITIES:
            self._put(key, None, ts)
        self._slow.clear()
        self._roll = self._pitch = self._peak_g = self._last_frame = None
        if not self._had_loss:
            self._had_loss = True
            self.hub.add_marker(f"{self.title} link lost", MARKER_SOURCE, ts)
        self._set_status(Status.MISSING, "Link lost")

    def _on_frame(self, frame, ts: float) -> None:
        if isinstance(frame, parser.DataFrame):
            self._last_frame = ts
            self._roll, self._pitch = frame.roll, frame.pitch
            g = math.sqrt(frame.ax ** 2 + frame.ay ** 2 + frame.az ** 2)
            self._peak_g = g if self._peak_g is None else max(self._peak_g, g)
        elif isinstance(frame, parser.RegisterReply):
            if frame.reg == parser.REG_BATTERY:
                v = parser.battery_volts(frame)
                self._slow_value("battery_volts", v, ts)
                self._slow_value("battery_pct", parser.battery_percent_approx(v), ts)
            elif frame.reg in (parser.REG_MAG_ANGLE_TEMP, parser.REG_TEMPERATURE):
                self._slow_value("body_temp", parser.temperature_c(frame), ts)

    def _slow_value(self, key: str, value: float | None, ts: float) -> None:
        """Battery and temperature arrive every ~45 s: stored when they arrive, a gap when they get old."""
        if value is None:
            return
        self._slow[key] = ts
        self._put(key, value, ts)

    # ------------------------------------------------------------------ once a second
    def tick(self, now: float) -> None:
        """Store this second's readings, or a gap when the data has gone quiet."""
        fresh = self._last_frame is not None and now - self._last_frame <= STALE_S
        if self._up and not fresh and self._last_frame is not None:
            self._link_lost(now)   # connected but silent: the same as a lost link (no stale numbers)
            return
        if fresh:
            vals = {"tilt_roll": self._roll, "tilt_pitch": self._pitch, "accel_peak": self._peak_g,
                    "link_age": max(0.0, now - self._last_frame)}
            self._peak_g = None
            for key, v in vals.items():
                if v is not None:
                    self._put(key, v, now)
            self._set_status(Status.OK, "")
        for key, got in list(self._slow.items()):
            if now - got > SLOW_VALID_S:
                self._put(key, None, now)
                del self._slow[key]

    def _set_status(self, status: Status, detail: str) -> None:
        if self._status_sent != (status, detail):
            self._status_sent = (status, detail)
            self.hub.set_device_status(self.device_id, status, detail, silent_alarm=True)   # advisory: a quiet notice only


class WitmotionIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, reader_factory=None) -> None:
        super().__init__(hub, emulate)
        self._factory = reader_factory   # tests: factory(address=..., **callbacks) -> reader
        self.sensors: list[Sensor] = []
        self._ticker: asyncio.Task | None = None
        self._active = False
        self._scan_lock: asyncio.Lock | None = None

    def wanted(self) -> bool:
        cfg = getattr(self.hub.config, "witmotion", None)
        return self.emulate or bool(cfg and cfg.enabled)

    def configured(self) -> list[tuple[str, str]]:
        """[(address, label)] for each sensor. No address at all means one sensor found by its factory
        name (WT901BLE...), a convenience for the first try; with two sensors each needs its address."""
        cfg = self.hub.config.witmotion
        if cfg.sensors:
            return [(s.address, s.label) for s in cfg.sensors]
        return [(cfg.address, "")]

    async def start(self) -> None:
        if not self.wanted():
            return
        self._active = True
        self._scan_lock = asyncio.Lock()   # one scan or connect at a time across all sensors (BlueZ needs it)
        entries = [("", "")] if self.emulate else self.configured()
        for i, (address, label) in enumerate(entries):
            s = Sensor(self.hub, DEVICE_ID if i == 0 else f"{DEVICE_ID}_{i + 1}", address, label)
            s.register()
            if self._factory is not None:
                s.reader = self._factory(address=address, **s.callbacks())
            elif self.emulate:
                s.reader = EmulatedReader(**s.callbacks())
            else:
                s.reader = WitmotionReader(address, scan_lock=self._scan_lock, **s.callbacks())
            s.task = asyncio.create_task(self._run_reader(s))
            self.sensors.append(s)
        self._ticker = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        for t in [s.task for s in self.sensors] + [self._ticker]:
            if t:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._ticker = None
        self.sensors = []

    async def _run_reader(self, s: Sensor) -> None:
        try:
            await s.reader.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:   # e.g. bleak is not installed: say so once and stay quiet
            log.warning("WitMotion: reader stopped (%s)", exc if isinstance(exc, RuntimeError) else type(exc).__name__)
            s._set_status(Status.FAULT, "Bluetooth problem")

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self.tick(time.time())
            except Exception:   # a strange value must never stop the hub
                log.exception("WitMotion: tick failed")

    def tick(self, now: float) -> None:
        for s in self.sensors:
            s.tick(now)

    def info(self) -> dict:
        d = super().info()
        d["active"] = self._active
        d["links"] = [s.link for s in self.sensors]
        return d
