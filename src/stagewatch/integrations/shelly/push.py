"""The Shelly receiver side of the integration: adopted devices that push their readings to Stagewatch.

Two kinds of device can push: power meters (the same readings, entities and Power card as polled meters) and
H&T temperature and humidity sensors. Stagewatch never sends a device anything: the receiver only listens.

Time: a reading is stamped with the moment it was received, never with the device's own clock (a show LAN is
often offline, so a device clock can be wrong). Nothing is back-filled and a value is never shown as fresh
because a frame arrived without it.

Not tested on real hardware. Unverified: the exact frames a real H&T Gen3 and Pro 3EM send, how often a meter
with a steady load sends, and what ``Origin`` header or subprotocol a Shelly uses.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field

from ...core import sitetime
from ...core.config import SHELLY_MAX_METERS, SHELLY_MAX_SENSORS, ShellyDeviceConfig, ShellySensorConfig
from ...core.model import UNITS, Device, Entity, Status, normalise_mac, slugify
from ..smaart.client import is_local_address
from . import ht
from . import readings as rd
from . import rpc
from .receiver import Receiver, peer_ip
from .transport import DeviceInfo

log = logging.getLogger(__name__)

CHECK_S = 30.0
PENDING_KEEP_S = 3600.0          # a different address waits this long for the admin, then is forgotten
METER_STALE_OPEN_S = 600.0       # a meter with its socket open and no frame for this long is not reporting
METER_STALE_CLOSED_S = 120.0     # ... and with its socket closed
WAITING_SENSOR = "Waiting for the first reading. Press the button on the device once."
WAITING_METER = "Waiting for the first reading from the meter"
GONE_SENSOR = "Batteries flat, out of Wi-Fi range, or Outbound WebSocket switched off?"
SUPPORT_TEXT = "This device is not supported yet."


class PushError(Exception):
    """Fixed-text problem for the admin (never repeats what a device sent)."""


@dataclass
class SensorState:
    cfg: ShellySensorConfig
    entities: set = field(default_factory=set)
    last_frame: float | None = None
    last_report: float | None = None
    info: dict = field(default_factory=dict)
    battery: float | None = None
    low: bool = False
    skew: float | None = None
    sensor_error: bool = False
    out_of_range: bool = False
    frames: int = 0
    ip_note: str = ""


class PushSide:
    def __init__(self, integ) -> None:
        self.integ = integ
        self.hub = integ.hub
        self.rx = Receiver(clock=integ._clock, enabled=lambda: self.hub.config.shelly_receiver.enabled,
                           ignored=lambda: set(self.hub.config.shelly_receiver.ignored), sink=self)
        self.sensors: dict[str, SensorState] = {}
        self.pending_ips: dict[str, dict] = {}     # mac -> a different address that sent as an adopted device, waiting for the admin
        self._task: asyncio.Task | None = None
        self.check_every = CHECK_S

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        for cfg in list(self.hub.config.shelly_sensors):
            self.attach_sensor(cfg)
        self._task = asyncio.create_task(self._loop(), name="shelly-push-check")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.check_every)
            try:
                self.check(self.integ._clock())
            except Exception:  # noqa: BLE001
                log.exception("Shelly: the receiver check failed")

    # ---------------------------------------------------------------- sink (from the receiver)
    def known_ip(self, mac: str) -> str:
        m = self.integ._meters.get(mac)
        if m is not None:
            return m.cfg.host
        st = self.sensors.get(mac)
        return st.cfg.last_ip if st else ""

    def adopted_ips(self) -> set:
        """The pinned addresses of the devices that were added (the receiver keeps some connection slots for them)."""
        return {st.cfg.last_ip for st in self.sensors.values() if st.cfg.last_ip} | \
            {m.cfg.host for m in self.integ._meters.values() if m.cfg.transport == "push" and m.cfg.host}

    def pending(self, mac: str, ip: str, now: float) -> None:
        """An adopted device's MAC sent from an address that is not the one it was added at. Nothing is changed: the
        admin is offered "now at ..., accept?" (see accept_address)."""
        ip = peer_ip(ip)
        if not ip or not is_local_address(ipaddress.ip_address(ip)) or mac not in self.adopted_macs():
            return
        entry = self.pending_ips.get(mac)
        if entry is None or entry["ip"] != ip:
            self.pending_ips[mac] = {"ip": ip, "first": now, "last": now, "count": 1}
        else:
            entry["last"], entry["count"] = now, entry["count"] + 1

    def deliver(self, mac: str, frame: rpc.Frame, prev: dict | None, new: dict, ip: str, now: float) -> None:
        """Use one frame. May raise: the receiver counts that as a bad frame and does not keep the status."""
        kind = self.rx.allowed.get(mac)
        if kind == "sensor" and mac in self.sensors:
            self._sensor_frame(self.sensors[mac], frame, new, ip, now)
        elif kind == "meter":
            self._meter_frame(mac, frame, prev, new, ip, now)

    # ---------------------------------------------------------------- power meters
    def _meter_frame(self, mac: str, frame: rpc.Frame, prev: dict | None, new: dict, ip: str, now: float) -> None:
        m = self.integ._meters.get(mac)
        if m is None:
            return
        m.last_frame = now
        skew = rpc.clock_skew(frame, now)
        m.skew = skew
        if frame.method == "NotifyEvent" or not new:
            return      # an event carries no readings; a partial status before any full one is not a status
        if not rd.components(new):
            return      # nothing a power meter reads
        if frame.full or prev is None:
            only = None
        else:
            before = {r.object_id: r.value for r in rd.readings_from_status(prev)}
            only = {r.object_id for r in rd.readings_from_status(new) if r.object_id not in before or before[r.object_id] != r.value}
        if m.info is None:
            self.integ.identified(mac, DeviceInfo(mac, frame.src[:60], rpc.model_label(frame.prefix), "", "", False))
        self.integ.status(mac, new, now, only=only, push=True)

    # ---------------------------------------------------------------- H&T sensors
    def attach_sensor(self, cfg: ShellySensorConfig) -> None:
        self.sensors[cfg.mac] = SensorState(cfg)
        self.rx.allow(cfg.mac, "sensor")
        dev = Device(cfg.id, cfg.name or "Shelly H&T", "shelly", "Shelly", "H&T Gen3", cfg.area, Status.INITIALIZING,
                     hw_id=f"mac:{cfg.mac}", category="sensor", role="environment")
        dev.stale_limit_s = ht.stale_limit_s(None, None)
        self.hub.register_device(dev)
        self.hub.set_device_status(cfg.id, Status.INITIALIZING, WAITING_SENSOR)

    def _sensor_frame(self, st: SensorState, frame: rpc.Frame, new: dict, ip: str, now: float) -> None:
        cfg = st.cfg
        st.last_frame, st.frames = now, st.frames + 1
        ip = peer_ip(ip)
        if ip and not cfg.last_ip:      # added before its address was known: the first address it sends from is pinned
            cfg.last_ip = ip
            self.hub.save_config()
        st.skew = rpc.clock_skew(frame, now)
        if frame.method == "NotifyEvent" or not new:
            return
        st.info = ht.info(new)
        names = set(rpc.component_names(frame.params))
        got = ht.values(new, names)
        if not got:
            return
        for oid, value in got.items():
            eid = f"{cfg.id}.{oid}"
            if eid not in self.hub.entities:
                if value is None:
                    continue
                kind, decimals, _ = ht.ENTITIES[oid]
                is_air = oid in ("temperature", "humidity")
                self.hub.register_entity(Entity(
                    eid, cfg.id, ht.NAMES[oid], kind, UNITS.get(kind, ""), decimals,
                    hw_key=f"mac:{cfg.mac}/{oid}", average_default=not is_air))
                st.entities.add(oid)
            self.hub.update_state(eid, value, now)
        st.last_report = now
        if "temperature" in got:
            st.sensor_error = got["temperature"] is None
            if got["temperature"] is not None:
                st.out_of_range = ht.out_of_range(got["temperature"])
        if "battery" in got:
            st.battery = got["battery"]
        ext = st.info.get("external_power")
        dev = self.hub.devices.get(cfg.id)
        if dev is not None:
            dev.stale_limit_s = ht.stale_limit_s(st.info.get("wake_period_s"), ext)
        self.hub.set_device_status(cfg.id, Status.OK, self._sensor_detail(st, ext))
        low = ht.battery_low(st.battery, ext, st.low)
        if low != st.low or low:
            st.low = low
            self._notice(f"{cfg.id}:battery_low", low, f"{cfg.name or 'Shelly H&T'}: battery low ({(st.battery or 0):.0f} %)")

    @staticmethod
    def _sensor_detail(st: SensorState, ext: bool | None) -> str:
        if st.sensor_error:
            return "Sensor error"
        if st.out_of_range:
            return "Outside the H&T's rated range (0 to 40 °C): treat with care"
        return "Reporting on USB power" if ext else "Sleeps between reports (normal)"

    def _notice(self, key: str, on: bool, text: str) -> None:
        change = self.hub.alarms.set_condition(f"shelly:{key}", on, 1, text if on else "", self.integ._clock(), silent=True)
        if change:
            self.hub._alarm_changed([change])

    # ---------------------------------------------------------------- is anything gone quiet?
    def check(self, now: float) -> None:
        site = self.hub.config.site
        for mac, st in self.sensors.items():
            if st.last_report is None:
                continue
            limit = ht.stale_limit_s(st.info.get("wake_period_s"), st.info.get("external_power"))
            dev = self.hub.devices.get(st.cfg.id)
            if dev is not None and now - st.last_report > limit:
                self.hub.set_device_status(st.cfg.id, Status.MISSING,
                                           f"No report since {sitetime.local_hhmm(st.last_report, site)}. {GONE_SENSOR}",
                                           silent_alarm=True)
        for mac, m in list(self.integ._meters.items()):
            if m.cfg.transport != "push":
                continue
            ref = m.last_frame if m.last_frame is not None else m.started
            limit = METER_STALE_OPEN_S if self.rx.connected(mac) else METER_STALE_CLOSED_S
            dev = self.hub.devices.get(m.cfg.id)
            if dev is None or now - ref <= limit or dev.status == Status.MISSING:
                continue
            m.gap = True
            since = (f"No reading since {sitetime.local_hhmm(m.last_frame, site)}" if m.last_frame else "No reading yet")
            self.hub.set_device_status(m.cfg.id, Status.MISSING, since, silent_alarm=True)

    # ---------------------------------------------------------------- admin
    def set_enabled(self, on: bool) -> None:
        self.hub.config.shelly_receiver.enabled = bool(on)
        self.hub.save_config()

    def ignore(self, mac: str, on: bool) -> None:
        mac = normalise_mac(mac) or ""
        if not mac:
            raise PushError("That is not a device address.")
        ignored = self.hub.config.shelly_receiver.ignored
        if on and mac in self.adopted_macs():
            raise PushError("That device has been added, so it cannot be ignored. Remove it first if you no longer want it.")
        if on and mac not in ignored:
            if len(ignored) >= 64:
                raise PushError("The ignore list is full.")
            ignored.append(mac)
            self.rx.unknown.pop(mac, None)
        elif not on and mac in ignored:
            ignored.remove(mac)
        self.hub.save_config()

    def adopted_macs(self) -> set:
        cfg = self.hub.config
        return {d.mac for d in cfg.shelly_devices} | {s.mac for s in cfg.shelly_sensors}

    def adopt(self, mac: str, name: str, area: str) -> str:
        """Adopt a device currently on the found list. Returns the new id. Raises LookupError (not on the list),
        PushError (fixed text) ."""
        mac = normalise_mac(mac) or ""
        entry = self.rx.unknown.get(mac)
        if entry is None:
            raise LookupError
        cfg = self.hub.config
        if mac in self.adopted_macs():
            raise PushError("That device has already been added.")
        ignored = cfg.shelly_receiver.ignored
        if mac in ignored:
            ignored.remove(mac)          # adding a device the admin once ignored takes it off the ignore list
        kind = rpc.kind_of(entry.components)
        device_id = slugify(name)
        if not device_id or device_id in ("site",) or device_id in self.hub.devices \
                or device_id in {d.id for d in cfg.shelly_devices} | {s.id for s in cfg.shelly_sensors}:
            raise PushError("That name is already in use. Choose another.")
        if kind == rpc.HT:
            if len(cfg.shelly_sensors) >= SHELLY_MAX_SENSORS:
                raise PushError("There is room for 16 sensors. Remove one first.")
            sc = ShellySensorConfig(id=device_id, mac=mac, name=name.strip(), area=area.strip(), last_ip=peer_ip(entry.ip))
            cfg.shelly_sensors.append(sc)
            self.rx.unknown.pop(mac, None)
            self.hub.save_config()
            self.attach_sensor(sc)
            return sc.id
        if kind == rpc.METER:
            if len(cfg.shelly_devices) >= SHELLY_MAX_METERS:
                raise PushError("There is room for 16 power meters. Remove one first.")
            ip = peer_ip(entry.ip)
            if not ip or not is_local_address(ipaddress.ip_address(ip)):
                raise PushError("That device is not on a private network. Stagewatch only reads devices on your local network.")
            dc = ShellyDeviceConfig(id=device_id, host=ip, mac=mac, name=name.strip(), area=area.strip(), transport="push")
            cfg.shelly_devices.append(dc)
            self.rx.unknown.pop(mac, None)
            self.hub.save_config()
            self.integ._attach(dc)
            return dc.id
        raise PushError(SUPPORT_TEXT)

    def pending_for(self, mac: str) -> dict | None:
        """The waiting different address for an adopted device, if it sent within the last hour."""
        entry = self.pending_ips.get(mac)
        if entry is None:
            return None
        if self.integ._clock() - entry["last"] > PENDING_KEEP_S or mac not in self.adopted_macs():
            del self.pending_ips[mac]
            return None
        return entry

    def accept_address(self, device_id: str, ip: str, accept: bool = True) -> None:
        """The admin's answer to "now at <address>, accept?". Accepting pins the new address (one config save);
        declining just forgets the offer. Raises LookupError (no such device), PushError (that address is not waiting)."""
        st = next((s for s in self.sensors.values() if s.cfg.id == device_id), None)
        cfg = st.cfg if st is not None else next((d for d in self.hub.config.shelly_devices if d.id == device_id and d.transport == "push"), None)
        if cfg is None:
            raise LookupError
        entry = self.pending_for(cfg.mac)
        if entry is None or entry["ip"] != peer_ip(ip):
            raise PushError("That address is no longer waiting for approval.")
        del self.pending_ips[cfg.mac]
        self.rx.conflicts.pop(cfg.mac, None)
        if not accept:
            return
        if st is not None:
            cfg.last_ip = entry["ip"]
            st.ip_note = f"now at {entry['ip']}"
        else:
            cfg.host = entry["ip"]
        self.hub.save_config()

    def remove_sensor(self, device_id: str) -> None:
        cfg = self.hub.config
        sc = next((s for s in cfg.shelly_sensors if s.id == device_id), None)
        if sc is None:
            raise LookupError
        cfg.shelly_sensors.remove(sc)
        self.sensors.pop(sc.mac, None)
        self.pending_ips.pop(sc.mac, None)
        self.rx.forget(sc.mac)
        self._notice(f"{sc.id}:battery_low", False, "")
        self.hub.save_config()
        self.hub.remove_device(sc.id)

    def admin(self, urls: list[str]) -> dict:
        now = self.integ._clock()
        site = self.hub.config.site
        sensors = []
        for st in self.sensors.values():
            age = None if st.last_frame is None else max(0.0, now - st.last_frame)
            dev = self.hub.devices.get(st.cfg.id)
            sensors.append({
                "id": st.cfg.id, "name": st.cfg.name, "area": st.cfg.area, "mac": st.cfg.mac, "last_ip": st.cfg.last_ip,
                "status": dev.status.value if dev else "off", "detail": dev.status_detail if dev else "",
                "last_report": st.last_report, "age_s": age, "battery_percent": st.battery, "battery_low": st.low,
                "battery_v": st.info.get("battery_v"), "external_power": st.info.get("external_power"),
                "wake_period_s": st.info.get("wake_period_s"), "wake_cause": st.info.get("wake_cause", ""),
                "clock_skew_s": st.skew, "clock_note": "device clock not set" if st.skew is not None and abs(st.skew) > 120 else "",
                "ip_note": st.ip_note, "frames": st.frames,
                "conflict": bool(self.rx.conflicts.get(st.cfg.mac)),
                "pending_ip": (self.pending_for(st.cfg.mac) or {}).get("ip", ""),
            })
        return {
            "enabled": self.hub.config.shelly_receiver.enabled, "urls": urls, "connections": self.rx.live,
            "counters": dict(self.rx.counters), "unknown": self.rx.unknown_list(), "sensors": sensors,
            "ignored": list(self.hub.config.shelly_receiver.ignored), "max_sensors": SHELLY_MAX_SENSORS,
            "conflicts": sorted(m for m in self.rx.conflicts if m in self.adopted_macs()),
            "notice": "Readings from Shelly devices are not encrypted or signed. Another device on the show network could "
                      "pretend to be your sensor. Stagewatch only accepts devices you have added.",
        }
