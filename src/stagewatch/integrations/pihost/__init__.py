"""This Raspberry Pi: the health of the computer Stagewatch runs on (temperature, under-voltage and
throttling, load, memory, disk, uptime, Wi-Fi signal), on an Admin card, an optional dashboard card
and as a quiet header warning.

Pi only: on anything that is not a Raspberry Pi the integration does nothing. Read-only: it reads
files under /proc and /sys and asks ``vcgencmd get_throttled``; it never changes a clock, a fan or
a power setting, and never reboots. Every reading is Equipment (never in a site average), recorded
as read. A reading that cannot be taken is a gap, never a zero, and an unknown throttle state is
"unknown", never "OK". Markers are made only as a change is seen live.

Addresses and the Wi-Fi network name are admin only (info()); dashboards get the numbers.

STATUS: written from the Raspberry Pi documentation (vcgencmd get_throttled bits) and the Linux
/proc and /sys files; not yet tested on a real Pi. Use ``--emulate`` to see the cards.
"""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from ...core.model import Device, Entity, Kind, Status, UNITS
from ...core.plugin import Integration, Manifest

log = logging.getLogger(__name__)

DEVICE_ID = "pi_host"
POLL_S = 10.0
MARKER_SOURCE = "pihost"
TEMP_WARN_C = 80.0          # the Pi 4 starts to slow itself down from 80 °C (soft limit), hard limit 85 °C
TEMP_CLEAR_C = 77.0         # clears with a little margin so a reading on the edge does not flicker
DISK_WARN_BYTES = 1_000_000_000
DISK_WARN_SHARE = 0.05

MANIFEST = Manifest(
    domain="pihost",
    name="This Raspberry Pi",
    version="0.1.0",
    description="Shows the health of the Raspberry Pi that Stagewatch runs on: CPU temperature, "
                "under-voltage and throttling (as the Pi itself reports them), load, memory, free disk "
                "space, uptime and Wi-Fi signal. Only reads; never changes a setting. Runs only on a "
                "Raspberry Pi. Not yet tested on a real Pi.",
    tier="experimental",
    direction="in",
    iot_class="local_poll",
    protocols=("Linux /proc and /sys", "vcgencmd"),
    entity_kinds=("temperature", "generic", "signal_strength"),
    vendors=("Raspberry Pi",),
)

# get_throttled bits (Raspberry Pi documentation, "vcgencmd get_throttled").
THROTTLE_BITS = {
    0: "under_voltage", 1: "freq_capped", 2: "throttled", 3: "soft_temp_limit",
    16: "under_voltage_seen", 17: "freq_capped_seen", 18: "throttled_seen", 19: "soft_temp_limit_seen",
}

# Entity id suffix -> (name, kind, unit, decimals)
ENTITIES = {
    "cpu_temp": ("CPU temperature", Kind.TEMPERATURE, UNITS[Kind.TEMPERATURE], 1),
    "cpu_load": ("CPU load (1 min)", Kind.GENERIC, "", 2),
    "memory_used": ("Memory used", Kind.GENERIC, "%", 0),
    "disk_free": ("Disk free", Kind.GENERIC, "GB", 1),
    "wifi_signal": ("Wi-Fi signal", Kind.SIGNAL, UNITS[Kind.SIGNAL], 0),
    "under_voltage": ("Under-voltage now", Kind.CONTACT, "", 0),
    "throttled": ("Throttled now", Kind.CONTACT, "", 0),
}


def decode_throttled(text: str) -> dict[str, bool] | None:
    """``throttled=0x50005`` -> the flags by name; None when the text is not that."""
    t = (text or "").strip()
    if not t.startswith("throttled="):
        return None
    try:
        bits = int(t.split("=", 1)[1], 16)
    except ValueError:
        return None
    return {name: bool(bits >> b & 1) for b, name in THROTTLE_BITS.items()}


@dataclass
class Stats:
    model: str = ""
    cpu_temp: float | None = None
    cpu_load: float | None = None
    memory_used: float | None = None
    disk_free: float | None = None          # GB
    disk_share_free: float | None = None    # 0..1
    uptime_s: float | None = None
    wifi_signal: float | None = None        # dBm
    wifi_name: str = ""                     # admin only
    addresses: dict[str, list[str]] = field(default_factory=dict)   # admin only
    throttle: dict[str, bool] | None = None


class Reader:
    """Reads one set of stats. ``root`` is "/" on a real Pi; tests point it at a fake tree."""

    def __init__(self, root: Path = Path("/"), data_dir: Path | None = None, vcgencmd: str | None = None) -> None:
        self.root = Path(root)
        self.data_dir = data_dir
        self.vcgencmd = vcgencmd if vcgencmd is not None else (shutil.which("vcgencmd") or "")

    def _text(self, rel: str) -> str | None:
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def model(self) -> str:
        for rel in ("proc/device-tree/model", "sys/firmware/devicetree/base/model"):
            t = self._text(rel)
            if t:
                return t.replace("\x00", "").strip()
        return ""

    def is_pi(self) -> bool:
        return "Raspberry Pi" in self.model()

    def cpu_temp(self) -> float | None:
        t = self._text("sys/class/thermal/thermal_zone0/temp")
        try:
            v = int(t.strip()) / 1000.0 if t else None
        except ValueError:
            return None
        return v if v is not None and -40 < v < 150 else None

    def cpu_load(self) -> float | None:
        t = self._text("proc/loadavg")
        try:
            return float(t.split()[0]) if t else None
        except (ValueError, IndexError):
            return None

    def memory_used(self) -> float | None:
        t = self._text("proc/meminfo")
        if not t:
            return None
        vals = {}
        for line in t.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in ("MemTotal:", "MemAvailable:"):
                try:
                    vals[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        total, avail = vals.get("MemTotal:"), vals.get("MemAvailable:")
        if not total or avail is None:
            return None
        return round(100.0 * (total - avail) / total, 1)

    def uptime(self) -> float | None:
        t = self._text("proc/uptime")
        try:
            return float(t.split()[0]) if t else None
        except (ValueError, IndexError):
            return None

    def disk(self) -> tuple[float | None, float | None]:
        if self.data_dir is None:
            return None, None
        try:
            u = shutil.disk_usage(self.data_dir)
        except OSError:
            return None, None
        return round(u.free / 1e9, 1), (u.free / u.total if u.total else None)

    def wifi(self) -> tuple[float | None, str]:
        """(signal dBm, interface) from /proc/net/wireless; (None, "") when there is no Wi-Fi."""
        t = self._text("proc/net/wireless")
        if not t:
            return None, ""
        for line in t.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    dbm = float(parts[3].rstrip("."))
                except ValueError:
                    continue
                if -120 < dbm < 0:
                    return dbm, parts[0].rstrip(":")
        return None, ""

    def addresses(self) -> dict[str, list[str]]:
        """IPv4 addresses per interface (admin only). Best effort; empty when unknown."""
        out: dict[str, list[str]] = {}
        try:
            import fcntl   # Linux only
            import struct
        except ImportError:
            return out
        try:
            names = [n for _, n in socket.if_nameindex() if n != "lo"]
        except OSError:
            return out
        for name in names:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                raw = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))   # SIOCGIFADDR
                out[name] = [socket.inet_ntoa(raw[20:24])]
            except OSError:
                pass
            finally:
                s.close()
        return out

    async def throttle(self) -> dict[str, bool] | None:
        if not self.vcgencmd:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.vcgencmd, "get_throttled", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        except (OSError, asyncio.TimeoutError):
            return None
        return decode_throttled(out.decode("utf-8", "replace"))

    async def read(self) -> Stats:
        s = Stats(model=self.model(), cpu_temp=self.cpu_temp(), cpu_load=self.cpu_load(),
                  memory_used=self.memory_used(), uptime_s=self.uptime())
        s.disk_free, s.disk_share_free = self.disk()
        s.wifi_signal, iface = self.wifi()
        s.wifi_name = iface
        s.addresses = self.addresses()
        s.throttle = await self.throttle()
        return s


class EmulatedReader(Reader):
    """A simulated Pi 4 for --emulate: warm, a little load, and a brief under-voltage now and then."""

    def __init__(self) -> None:
        super().__init__(Path("/nonexistent"), None, "")
        self._t0 = time.time()

    def is_pi(self) -> bool:
        return True

    async def read(self) -> Stats:
        t = time.time() - self._t0
        under = (t % 600) < 20   # 20 s of under-voltage every 10 minutes
        flags = {name: False for name in THROTTLE_BITS.values()}
        flags.update(under_voltage=under, under_voltage_seen=True)   # it starts with one, so "since boot" is always true
        return Stats(model="Raspberry Pi 4 Model B Rev 1.5 (simulated)",
                     cpu_temp=round(55 + 6 * math.sin(t / 120), 1), cpu_load=round(0.6 + 0.3 * math.sin(t / 45), 2),
                     memory_used=round(38 + 4 * math.sin(t / 300), 1), disk_free=21.4, disk_share_free=0.37,
                     uptime_s=86_400 + t, wifi_signal=round(-58 + 4 * math.sin(t / 30)), wifi_name="wlan0",
                     addresses={"eth0": ["192.0.2.20"], "wlan0": ["198.51.100.20"]}, throttle=flags)


def warnings_of(s: Stats, prev_hot: bool = False) -> list[dict]:
    """The quiet warnings for this set of stats: [{key, text}]. Plain words for crew."""
    out = []
    th = s.throttle or {}
    if th.get("under_voltage"):
        out.append({"key": "under_voltage", "text": "This Raspberry Pi is short of power (under-voltage). Check its power supply and cable."})
    if th.get("throttled") or th.get("freq_capped"):
        out.append({"key": "throttled", "text": "This Raspberry Pi has slowed itself down (throttled). Check its power supply and cooling."})
    hot = s.cpu_temp is not None and (s.cpu_temp >= TEMP_WARN_C or (prev_hot and s.cpu_temp > TEMP_CLEAR_C))
    if hot:
        out.append({"key": "hot", "text": f"This Raspberry Pi is hot ({s.cpu_temp:.0f} °C). Give it air or a fan."})
    if s.disk_free is not None and (s.disk_free * 1e9 < DISK_WARN_BYTES or (s.disk_share_free or 1) < DISK_WARN_SHARE):
        out.append({"key": "disk", "text": f"This Raspberry Pi is low on disk space ({s.disk_free:.1f} GB free)."})
    return out


class PiHostIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, reader: Reader | None = None) -> None:
        super().__init__(hub, emulate)
        self.reader = reader or (EmulatedReader() if emulate else Reader(Path("/"), getattr(hub, "data_dir", None)))
        self._task: asyncio.Task | None = None
        self.stats: Stats | None = None
        self._active = False
        self._warn: set[str] = set()
        self._flags: dict[str, bool] = {}

    async def start(self) -> None:
        if not self.reader.is_pi():   # on Windows, or any computer that is not a Pi, the files are not there
            return
        self._active = True
        self.hub.register_device(Device(DEVICE_ID, "This Raspberry Pi", MANIFEST.domain, "Raspberry Pi",
                                        self.reader.model(), "", Status.INITIALIZING, category="host", role="equipment"))
        for key, (name, kind, unit, dec) in ENTITIES.items():
            self.hub.register_entity(Entity(f"{DEVICE_ID}.{key}", DEVICE_ID, name, kind, unit, dec, locked_equipment=True))
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - a strange reading must never stop the hub
                log.exception("Pi stats: could not read")
            await asyncio.sleep(POLL_S)

    async def poll_once(self) -> None:
        s = await self.reader.read()
        now = time.time()
        self.stats = s
        th = s.throttle
        vals = {"cpu_temp": s.cpu_temp, "cpu_load": s.cpu_load, "memory_used": s.memory_used,
                "disk_free": s.disk_free, "wifi_signal": s.wifi_signal,
                "under_voltage": None if th is None else float(th["under_voltage"]),
                "throttled": None if th is None else float(th["throttled"] or th["freq_capped"])}
        for key, v in vals.items():
            if v is not None:   # a reading that could not be taken is a gap, never a zero
                self.hub.update_state(f"{DEVICE_ID}.{key}", v, now)
        dev = self.hub.devices.get(DEVICE_ID)
        if dev is not None:
            dev.model = s.model or dev.model
            # Public: numbers, flags and fixed warning words only (addresses stay in info(), admin only).
            self.hub.set_device_public(DEVICE_ID, {"pi": {
                "uptime_s": s.uptime_s, "throttle": th, "disk_share_free": s.disk_share_free,
                "warnings": [w["text"] for w in warnings_of(s, "hot" in self._warn)]}})
        self.hub.set_device_status(DEVICE_ID, Status.OK, "")
        self._markers(th, now)
        self._notices(s, now)

    def _markers(self, th: dict | None, now: float) -> None:
        """A marker when under-voltage or throttling starts or stops, seen live (never back-filled)."""
        if th is None:
            return
        cur = {"under_voltage": th["under_voltage"], "throttled": th["throttled"] or th["freq_capped"]}
        words = {"under_voltage": "under-voltage", "throttled": "throttling"}
        for k, on in cur.items():
            was = self._flags.get(k)
            if was is not None and was != on:
                self.hub.add_marker(f"Raspberry Pi {words[k]} {'started' if on else 'stopped'}", MARKER_SOURCE, now)
            elif was is None and on:
                self.hub.add_marker(f"Raspberry Pi {words[k]} (seen at start)", MARKER_SOURCE, now)
            self._flags[k] = on

    def _notices(self, s: Stats, now: float) -> None:
        """Quiet on-screen warnings (never sound) in the dashboard header's messages."""
        ws = {w["key"]: w["text"] for w in warnings_of(s, "hot" in self._warn)}
        changes = []
        for key in set(ws) | self._warn:
            ch = self.hub.alarms.set_condition(f"pihost:{key}", key in ws, 1, ws.get(key, ""), now, silent=True)
            if ch:
                changes.append(ch)
        self._warn = set(ws)
        if changes:
            self.hub._alarm_changed(changes)

    def info(self) -> dict:
        d = super().info()
        s = self.stats
        d["active"] = self._active
        if s is not None:
            d["pi"] = {"model": s.model, "cpu_temp": s.cpu_temp, "cpu_load": s.cpu_load, "memory_used": s.memory_used,
                       "disk_free": s.disk_free, "disk_share_free": s.disk_share_free, "uptime_s": s.uptime_s,
                       "wifi_signal": s.wifi_signal, "wifi_interface": s.wifi_name, "addresses": s.addresses,
                       "throttle": s.throttle, "warnings": [w["text"] for w in warnings_of(s, "hot" in self._warn)]}
        return d
