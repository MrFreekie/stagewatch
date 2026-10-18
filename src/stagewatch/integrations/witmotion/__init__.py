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
from collections import deque

from ...core.model import Device, Entity, Kind, Status
from ...core.plugin import Integration, Manifest
from . import parser
from . import scan as scan_mod
from .smoothing import Smoother
from .emulate import EmulatedReader
from .reader import WitmotionReader

log = logging.getLogger(__name__)

DEVICE_ID = "witmotion"
MARKER_SOURCE = "witmotion"
TICK_S = 1.0
SILENCE_LIMIT_S = 15.0  # the reader drops a link silent this long (reader.SILENCE_S)
STALE_S = 5.0          # no frame this long: the readings are a gap (the link allows ~0.3 s between frames)
SLOW_VALID_S = 180.0   # battery and temperature are asked for every ~45 s; older than this is a gap
EMULATE_SCAN_S = 1.5   # the "Find sensors" button in emulate: a short pause, then two made-up sensors
RADIO_WAIT_S = 25.0    # a search waits this long for a sensor reader that is using the radio

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
AXIS_TEXT = {"both": "both axes", "x": "X (side to side)", "y": "Y (front to back)"}
AXIS_HELP = ("X is the sensor's roll and Y is its pitch. Which way each points in the room depends on how the "
             "sensor is fixed, so check by tilting it and watching the reading.")
BATTERY_UNKNOWN = "Not available for this sensor model"


def entity_specs(axis: str, smoothing_s: float) -> dict:
    """The entities a sensor gets. A tilt axis that is not watched has no entity, so it is never recorded.
    With smoothing on, the tilt entities hold the smoothed value (alarms act on it) and the raw value is
    kept beside it, so the recorded raw history is never smoothed."""
    out = {}
    smooth = smoothing_s > 0
    for key, label, ax in (("tilt_roll", "X (roll)", "x"), ("tilt_pitch", "Y (pitch)", "y")):
        if axis in ("both", ax):
            out[key] = (f"Tilt {label}{', smoothed' if smooth else ''} (advisory)", Kind.GENERIC, "°", 1)
    if smooth:
        for key, label, ax in (("tilt_roll_raw", "X (roll)", "x"), ("tilt_pitch_raw", "Y (pitch)", "y")):
            if axis in ("both", ax):
                out[key] = (f"Tilt {label}, unsmoothed (advisory)", Kind.GENERIC, "°", 1)
    for key in ("accel_peak", "link_age", "battery_volts", "battery_pct", "body_temp"):
        out[key] = ENTITIES[key]
    return out


# Fixed words for the status line (never text built from an exception or an address).
STATUS_TEXT = {
    "searching": "Looking for the sensor",
    "not found": "Sensor not found",
    "connecting": "Connecting",
    "connected": "Connected",
    "lost": "Link lost",
    "error": "Bluetooth problem",
}


def target_bands(cfg) -> dict:
    """The target angles that are set, with the bands. Display only: nothing here raises an alarm."""
    out = {}
    for key, t in (("tilt_roll", cfg.target_x), ("tilt_pitch", cfg.target_y)):
        if t is not None:
            out[key] = {"target": t, "on": cfg.on_target_deg, "close": cfg.close_deg}
    return out


class Sensor:
    """One sensor: its device, entities, state and gap handling. Found by its configured Bluetooth
    address (its own name can be changed by crew, so the name is never what finds it); the label is a
    Stagewatch setting and is never written to the sensor."""

    def __init__(self, hub, device_id: str, address: str, label: str, axis: str = "both",
                 smoothing_s: float = 0.0, targets: dict | None = None) -> None:
        self.hub = hub
        self.device_id = device_id
        self.spec = (address, label, axis, smoothing_s, targets or {})   # what it was set up with: a settings change compares this
        self.closed = False                   # stopped by Stagewatch (a settings change), not lost: it says nothing more
        self.address = address
        self.label = label
        self.title = label or "Tilt sensor"
        self.axis, self.smoothing_s = axis, smoothing_s
        self.targets = targets or {}   # {"tilt_roll": {"target", "on", "close"}} for watched axes that have a target
        self.specs = entity_specs(axis, smoothing_s)
        self._roll_f = Smoother(smoothing_s, circular=True)
        self._pitch_f = Smoother(smoothing_s)
        self._roll_raw: float | None = None
        self._pitch_raw: float | None = None
        self._arrivals: deque[float] = deque(maxlen=10)   # recent frame times: the sensor's own return rate
        self._battery_note = ""
        self._setup: dict[str, str] = {}
        self._rate_period_s: float | None = None
        self._battery_v: float | None = None
        self._body_c: float | None = None
        self._readout_sent: dict | None = None
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
        for key, (name, kind, unit, dec) in self.specs.items():
            self.hub.register_entity(Entity(f"{self.device_id}.{key}", self.device_id, name, kind, unit, dec,
                                            locked_equipment=True))
        self._public()

    def _public(self) -> None:
        """What reaches the public snapshot: fixed words and numbers only (no addresses, no sensor name)."""
        self.hub.set_device_public(self.device_id, {"witmotion": {
            "axis": self.axis, "axis_text": AXIS_TEXT[self.axis], "axis_help": AXIS_HELP,
            "smoothing_s": self.smoothing_s, "battery_note": self._battery_note,
            "temp_on_equipment": bool(getattr(self.hub.config.witmotion, "temp_on_equipment", False)),
            "targets": {k: v for k, v in self.targets.items() if k in self.specs}, **self.readout()}})

    def limits(self) -> dict:
        """For each tilt reading that has an enabled threshold: the level of its alarm now (0 = within limit,
        1-3 = advisory, alert, stop). Absent = no threshold, so the card shows no limit colour."""
        out = {}
        for key in ("tilt_roll", "tilt_pitch"):
            eid = f"{self.device_id}.{key}"
            if key not in self.specs or not any(t.enabled and t.entity == eid for t in self.hub.config.thresholds):
                continue
            level = 0
            for t in self.hub.config.thresholds:
                a = self.hub.alarms.active.get(f"threshold:{t.id}") if t.enabled and t.entity == eid else None
                if a is not None:
                    level = max(level, a.level)
            out[key] = level
        return out

    def readout(self) -> dict:
        """The small-print lines for the card: what the sensor said about itself, "unknown" where it did not
        answer, never a guess. Information only: nothing here is recorded or used by an alarm."""
        u = self._setup
        line1 = " · ".join([
            u.get("rate", "unknown"),
            f"{u['bandwidth']} bandwidth" if "bandwidth" in u else "bandwidth unknown",
            u.get("orient", "install direction unknown"),
            u.get("axis_mode", "axis mode unknown")])
        if self._battery_note:
            batt = "battery: " + self._battery_note.lower()
        elif self._battery_v is not None:
            batt = f"battery {self._battery_v:.2f} V (about {parser.battery_percent_approx(self._battery_v):.0f} %, approximate)"
        else:
            batt = "battery unknown"
        temp = f"sensor body {self._body_c:.1f} °C" if self._body_c is not None else "sensor body temperature unknown"
        out = {"limits": self.limits(), "setup_line": line1, "info_line": f"WT901BLE family · firmware not available over Bluetooth · {batt} · {temp}"}
        if self._rate_period_s is not None and 3 * self._rate_period_s >= SILENCE_LIMIT_S:
            out["warning"] = ("The sensor sends data too slowly (every %g s) for Stagewatch to tell a quiet sensor from "
                              "a lost link. Raise its return rate in the WitMotion app." % self._rate_period_s)
        return out

    def callbacks(self) -> dict:
        return dict(on_frame=self._on_frame, on_connected=self._on_connected,
                    on_disconnected=self._on_disconnected, on_status=self._on_status)

    def _put(self, key: str, value: float | None, ts: float) -> None:
        self.hub.update_state(f"{self.device_id}.{key}", value, ts)

    # ------------------------------------------------------------------ reader callbacks
    def _on_status(self, text: str) -> None:
        if self.closed:
            return
        self.link = text
        if not self._up:
            status = Status.INITIALIZING if text in ("searching", "connecting") and not self._had_loss else Status.MISSING
            self._set_status(status, STATUS_TEXT.get(text, ""))

    def _on_connected(self, ts: float) -> None:
        if self.closed:
            return
        self._up = True
        self._reset_filters()
        self._setup, self._rate_period_s = {}, None   # read again on every connection: the app may have changed them
        if self._had_loss:
            if self._markers_on():
                self.hub.add_marker(f"{self.title} back (readings missed while it was away are not filled in)", MARKER_SOURCE, ts)
            self._had_loss = False

    def _on_disconnected(self, ts: float) -> None:
        if self.closed:
            return   # we closed it ourselves: that is not a lost link, so no gap and no marker
        self._up = False
        self._link_lost(ts)

    def _link_lost(self, ts: float) -> None:
        """Every reading becomes a gap, once, and a marker says when it went (as it happens)."""
        for key in self.specs:
            self._put(key, None, ts)
        self._slow.clear()
        self._battery_v = self._body_c = None
        self._setup, self._rate_period_s = {}, None
        self._reset_filters()
        self._peak_g = self._last_frame = None
        if not self._had_loss:
            self._had_loss = True
            if self._markers_on():
                self.hub.add_marker(f"{self.title} link lost", MARKER_SOURCE, ts)
        self._set_status(Status.MISSING, "Link lost")

    def _markers_on(self) -> bool:
        """Admin can turn the link markers off for a fragile link; the readings still become gaps."""
        cfg = getattr(self.hub.config, "witmotion", None)
        return cfg is None or cfg.markers

    def _reset_filters(self) -> None:
        """After any gap or reconnect the filters start empty: nothing is carried across."""
        self._roll_f.reset()
        self._pitch_f.reset()
        self._roll = self._pitch = self._roll_raw = self._pitch_raw = None
        self._arrivals.clear()

    def stale_after(self) -> float:
        """Seconds without a frame before the link counts as quiet: at least 3 frame periods, never under STALE_S."""
        if len(self._arrivals) >= 3:
            period = (self._arrivals[-1] - self._arrivals[0]) / (len(self._arrivals) - 1)
            return max(STALE_S, 3.0 * period)
        return STALE_S

    def _on_frame(self, frame, ts: float) -> None:
        if self.closed:
            return
        if isinstance(frame, parser.DataFrame):
            self._last_frame = ts
            self._arrivals.append(ts)
            self._roll_raw, self._pitch_raw = frame.roll, frame.pitch
            self._roll = self._roll_f.update(frame.roll, ts)
            self._pitch = self._pitch_f.update(frame.pitch, ts)
            g = math.sqrt(frame.ax ** 2 + frame.ay ** 2 + frame.az ** 2)
            self._peak_g = g if self._peak_g is None else max(self._peak_g, g)
        elif isinstance(frame, parser.RegisterReply):
            if frame.reg == parser.REG_RATE:
                text = parser.rate_text(frame)
                if text:
                    self._setup["rate"] = text
                self._rate_period_s = parser.rate_period_s(frame)
            elif frame.reg == parser.REG_BANDWIDTH:
                self._setup.update(parser.setup_texts(frame))
            elif frame.reg == parser.REG_BATTERY:
                v = parser.battery_volts(frame)
                self._battery_v = v
                note = "" if v is not None else BATTERY_UNKNOWN   # a raw value we cannot read is not a voltage
                if note != self._battery_note:
                    self._battery_note = note
                    self._public()
                self._slow_value("battery_volts", v, ts)
                self._slow_value("battery_pct", parser.battery_percent_approx(v), ts)
            elif frame.reg in (parser.REG_MAG_ANGLE_TEMP, parser.REG_TEMPERATURE):
                self._body_c = parser.temperature_c(frame)
                self._slow_value("body_temp", self._body_c, ts)

    def _slow_value(self, key: str, value: float | None, ts: float) -> None:
        """Battery and temperature arrive every ~45 s: stored when they arrive, a gap when they get old."""
        if value is None:
            return
        self._slow[key] = ts
        self._put(key, value, ts)

    # ------------------------------------------------------------------ once a second
    def tick(self, now: float) -> None:
        """Store this second's readings, or a gap when the data has gone quiet."""
        if self.closed:
            return
        fresh = self._last_frame is not None and now - self._last_frame <= self.stale_after()
        if self._up and not fresh and self._last_frame is not None:
            self._link_lost(now)   # connected but silent: the same as a lost link (no stale numbers)
            return
        if fresh:
            vals = {"tilt_roll": self._roll, "tilt_pitch": self._pitch, "accel_peak": self._peak_g,
                    "link_age": max(0.0, now - self._last_frame)}
            if self.smoothing_s > 0:
                vals.update(tilt_roll_raw=self._roll_raw, tilt_pitch_raw=self._pitch_raw)
            self._peak_g = None
            for key, v in vals.items():
                if v is not None and key in self.specs:
                    self._put(key, v, now)
            self._set_status(Status.OK, "")
        for key, got in list(self._slow.items()):
            if now - got > SLOW_VALID_S:
                self._put(key, None, now)
                del self._slow[key]
                if key == "body_temp":
                    self._body_c = None
                elif key == "battery_volts":
                    self._battery_v = None
        if self.readout() != self._readout_sent:
            self._readout_sent = self.readout()
            self._public()

    def _set_status(self, status: Status, detail: str) -> None:
        if self.closed:
            return
        if self._status_sent != (status, detail):
            self._status_sent = (status, detail)
            self.hub.set_device_status(self.device_id, status, detail, silent_alarm=True)   # advisory: a quiet notice only


class WitmotionIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, reader_factory=None, scan_fn=None) -> None:
        super().__init__(hub, emulate)
        self._factory = reader_factory   # tests: factory(address=..., **callbacks) -> reader
        self._scan_fn = scan_fn          # tests: async () -> list of found sensors, in place of the Bluetooth search
        self.sensors: list[Sensor] = []
        self._ticker: asyncio.Task | None = None
        self._active = False
        self._scan_lock = asyncio.Lock()   # one scan or connect at a time across all sensors (BlueZ needs it)
        self._apply_lock = asyncio.Lock()
        self._apply_task: asyncio.Task | None = None
        self._dirty = False
        self._stopped = True
        self._unsubscribe = None
        self._scanning = False             # a "Find sensors" search is running (one at a time)

    def wanted(self) -> bool:
        cfg = getattr(self.hub.config, "witmotion", None)
        return self.emulate or bool(cfg and cfg.enabled)

    def configured(self) -> list[tuple[str, str, str, float, dict]]:
        """[(address, label, axis, smoothing_s, targets)] for each sensor. No address at all means one sensor found by its factory
        name (WT901BLE...), a convenience for the first try; with two sensors each needs its address."""
        cfg = self.hub.config.witmotion
        if cfg.sensors:
            return [(s.address, s.label, s.axis, s.smoothing_s, target_bands(s)) for s in cfg.sensors]
        return [(cfg.address, "", cfg.axis, cfg.smoothing_s, target_bands(cfg))]

    def desired(self) -> dict[str, tuple]:
        """device id -> how that sensor should be set up now (nothing while switched off)."""
        if not self.wanted():
            return {}
        entries = [("", "", "both", 2.0, {"tilt_roll": {"target": 2.0, "on": 0.2, "close": 1.0},
                                         "tilt_pitch": {"target": -2.0, "on": 0.2, "close": 1.0}})] if self.emulate else self.configured()
        return {DEVICE_ID if i == 0 else f"{DEVICE_ID}_{i + 1}": e for i, e in enumerate(entries)}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._stopped = False
        self._unsubscribe = self.hub.bus.subscribe("config", self._on_config)   # Admin changes take effect with no restart
        await self.apply()

    async def stop(self) -> None:
        self._stopped = True
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._apply_task:
            self._apply_task.cancel()
            await asyncio.gather(self._apply_task, return_exceptions=True)
            self._apply_task = None
        async with self._apply_lock:
            await self._cancel([s.task for s in self.sensors] + [self._ticker])
            self._ticker = None
            self.sensors = []

    def _on_config(self, _topic: str, _payload) -> None:
        """A settings save: look again. Coalesced, and harmless when nothing about the sensors changed."""
        if self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._dirty = True
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = loop.create_task(self._apply_loop(), name="witmotion-apply")

    async def _apply_loop(self) -> None:
        while self._dirty and not self._stopped:
            self._dirty = False
            await self.apply()

    @staticmethod
    async def _cancel(tasks) -> None:
        tasks = [t for t in tasks if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def apply(self) -> None:
        """Make the running sensors match the saved settings. A sensor whose settings did not change is left
        running (its link is not dropped); one that changed or was removed is stopped quietly (that is not a
        lost link, so no gap and no marker) and its device goes; a new one starts. Safe to call at any time."""
        async with self._apply_lock:
            if self._stopped:
                return
            want = self.desired()
            gone = [s for s in self.sensors if want.get(s.device_id) != s.spec]
            for s in gone:
                s.closed = True   # before the cancel: the reader reports a disconnect as it stops
            await self._cancel([s.task for s in gone])
            for s in gone:
                self.hub.remove_device(s.device_id)
            self.sensors = [s for s in self.sensors if s not in gone]
            have = {s.device_id for s in self.sensors}
            for device_id, (address, label, axis, smoothing, targets) in want.items():
                if device_id not in have:
                    self.sensors.append(self._add(device_id, address, label, axis, smoothing, targets))
            for s in self.sensors:
                s._public()   # a card-only setting (temperature on the Equipment card) may have changed
            order = list(want)
            self.sensors.sort(key=lambda s: order.index(s.device_id))
            self._active = bool(self.sensors)
            if self.sensors and self._ticker is None:
                self._ticker = asyncio.create_task(self._tick_loop())
            elif not self.sensors and self._ticker is not None:
                await self._cancel([self._ticker])
                self._ticker = None

    def _add(self, device_id: str, address: str, label: str, axis: str, smoothing: float, targets: dict) -> Sensor:
        s = Sensor(self.hub, device_id, address, label, axis, smoothing, targets)
        s.register()
        if self._factory is not None:
            s.reader = self._factory(address=address, **s.callbacks())
        elif self.emulate:
            s.reader = EmulatedReader(**s.callbacks())
        else:
            s.reader = WitmotionReader(address, scan_lock=self._scan_lock, **s.callbacks())
        s.task = asyncio.create_task(self._run_reader(s))
        return s

    # ------------------------------------------------------------------ Admin: status and "Find sensors"
    def admin_status(self) -> dict:
        """For the Admin card only (it carries no addresses). One entry per running sensor, in order."""
        now = time.time()
        rows = []
        for s in self.sensors:
            fresh = s._up and s._last_frame is not None and now - s._last_frame <= s.stale_after()
            if fresh:
                text = "Connected"
            elif s._had_loss:
                text = "Link lost"   # a lost or silent link: the readings are a gap until it is back
            elif s._up:
                text = "Connected, waiting for data"
            else:
                text = STATUS_TEXT.get(s.link, "Starting")
            r = s.readout()
            rows.append({"index": int(s.device_id.rsplit("_", 1)[1]) - 1 if "_" in s.device_id else 0,
                         "connected": bool(fresh), "text": text,
                         "age_s": round(now - s._last_frame, 1) if s._last_frame is not None else None,
                         "setup_line": r["setup_line"], "info_line": r["info_line"], "warning": r.get("warning", "")})
        return {"running": self._active, "scanning": self._scanning, "sensors": rows}

    async def find_sensors(self) -> list[dict]:
        """The "Find sensors" button: listen for WitMotion sensors for a few seconds (name starts WT). Listening
        only: never connects, never pairs. One search at a time (ScanBusy otherwise); ScanUnavailable when
        Bluetooth cannot be used. Shares the radio with the sensor readers, which also scan and connect."""
        if self._scanning:
            raise scan_mod.ScanBusy()
        self._scanning = True
        try:
            if self._scan_fn is not None:
                return await self._scan_fn()
            if self.emulate:
                await asyncio.sleep(EMULATE_SCAN_S)
                return scan_mod.emulated()
            try:
                await asyncio.wait_for(self._scan_lock.acquire(), RADIO_WAIT_S)
            except asyncio.TimeoutError:
                raise scan_mod.ScanUnavailable(scan_mod.BUSY) from None
            try:
                return await scan_mod.listen()
            finally:
                self._scan_lock.release()
        finally:
            self._scanning = False
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
