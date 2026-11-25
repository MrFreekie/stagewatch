"""The Shelly WebSocket receiver: who may connect, how fast, and what happens to each frame.

Shelly devices with "Outbound WebSocket" switched on connect to Stagewatch and send their readings. Nothing in
the Shelly protocol lets Stagewatch check who is really sending, so the receiver is built to be safe anyway:

* it is off until the admin switches it on, and a browser (which always sends an ``Origin`` header) is refused;
* at most 32 connections (4 from one address) and 10 new connections a minute from one address;
* a frame limit of 64 KiB, 10 frames a second from one connection (a burst of 30) and 200 a second in all;
* only devices the admin adopted are read. Any other sender goes on a short in-memory list (names of its
  components, never its readings), is told goodbye and is forgotten at the next restart;
* the server never sends a data frame: it only accepts, pings (done by the web server) and closes.

Readings are not signed. A device on the same network could pretend to be one the admin adopted. This is said
plainly in the admin page and the guide.

Pure of the web framework except ``serve``, which only uses ``accept``, ``receive`` and ``close``. The clock and
every limit are injectable for tests.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import rpc

log = logging.getLogger(__name__)

MAX_TOTAL = 32
MAX_PER_IP = 4
HANDSHAKES_PER_MIN = 10
FRAME_RATE = 10.0
FRAME_BURST = 30.0
GLOBAL_RATE = 200.0
FIRST_FRAME_S = 10.0
IDLE_S = 30 * 60.0
MAX_UNKNOWN = 32
MAX_BAD_FRAMES = 20          # unusable frames on one connection before it is closed
MAX_TRACKED_IPS = 256
LOG_EVERY_S = 3600.0         # one "unknown device" line per MAC per hour
COUNTERS_EVERY_S = 60.0
NORMAL, POLICY, TOO_BIG, GOING_AWAY = 1000, 1008, 1009, 1001


class Sink(Protocol):
    def known_ip(self, mac: str) -> str: ...
    def deliver(self, mac: str, frame: "rpc.Frame", prev: dict | None, new: dict, ip: str, now: float) -> None: ...


class Bucket:
    """A token bucket: ``rate`` a second, up to ``burst`` at once."""

    def __init__(self, rate: float, burst: float, clock: Callable[[], float]) -> None:
        self.rate, self.burst, self._clock = rate, burst, clock
        self._tokens, self._at = burst, clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + max(0.0, now - self._at) * self.rate)
        self._at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


@dataclass(eq=False)
class Conn:
    ip: str
    bucket: Bucket
    mac: str = ""
    open: bool = True
    frames: int = 0
    bad: int = 0
    superseded: bool = False


@dataclass
class Unknown:
    mac: str
    src: str
    ip: str
    model: str
    components: list
    label: str
    first: float
    last: float
    count: int = 1

    def to_dict(self) -> dict:
        return {"mac": self.mac, "src": self.src, "ip": self.ip, "model": self.model, "components": list(self.components),
                "label": self.label, "supported": self.label != "Not supported yet", "first_seen": self.first,
                "last_seen": self.last, "count": self.count}


@dataclass
class Receiver:
    clock: Callable[[], float] = time.time
    enabled: Callable[[], bool] = lambda: False
    ignored: Callable[[], set] = lambda: set()
    sink: Sink | None = None
    max_total: int = MAX_TOTAL
    max_per_ip: int = MAX_PER_IP
    handshakes_per_min: int = HANDSHAKES_PER_MIN
    frame_rate: float = FRAME_RATE
    frame_burst: float = FRAME_BURST
    first_frame_s: float = FIRST_FRAME_S
    idle_s: float = IDLE_S
    allowed: dict = field(default_factory=dict)         # adopted mac -> "meter" or "sensor"
    unknown: OrderedDict = field(default_factory=OrderedDict)
    counters: Counter = field(default_factory=Counter)
    conflicts: dict = field(default_factory=dict)       # mac -> time of the last second-sender claim

    def __post_init__(self) -> None:
        self._conns: list[Conn] = []
        self._owner: dict[str, Conn] = {}
        self._status: dict[str, dict] = {}
        self._handshakes: OrderedDict = OrderedDict()
        self._global = Bucket(GLOBAL_RATE, GLOBAL_RATE, self.clock)
        self._logged: OrderedDict = OrderedDict()
        self._counters_logged = (0.0, 0)

    # ------------------------------------------------------------- who may be read
    def allow(self, mac: str, kind: str) -> None:
        self.allowed[mac] = kind

    def forget(self, mac: str) -> None:
        self.allowed.pop(mac, None)
        self._status.pop(mac, None)
        self._owner.pop(mac, None)

    def clear_unknown(self) -> None:
        self.unknown.clear()

    def status_of(self, mac: str) -> dict | None:
        return self._status.get(mac)

    def connected(self, mac: str) -> bool:
        owner = self._owner.get(mac)
        return owner is not None and owner.open

    # ------------------------------------------------------------------ admission
    def admit(self, ip: str, has_origin: bool) -> int | None:
        """None to accept, or the close code to refuse with. Counts why."""
        if not self.enabled():
            self.counters["refused_off"] += 1
            return POLICY
        if has_origin:
            self.counters["refused_origin"] += 1      # a web page, not a Shelly
            return POLICY
        now = self.clock()
        times = self._handshakes.pop(ip, None) or deque()
        while times and now - times[0] >= 60.0:
            times.popleft()
        self._handshakes[ip] = times
        while len(self._handshakes) > MAX_TRACKED_IPS:
            self._handshakes.popitem(last=False)
        if len(times) >= self.handshakes_per_min:
            self.counters["refused_rate"] += 1
            return POLICY
        times.append(now)
        live = [c for c in self._conns if c.open]
        if len(live) >= self.max_total:
            self.counters["refused_total"] += 1
            return POLICY
        if sum(1 for c in live if c.ip == ip) >= self.max_per_ip:
            self.counters["refused_per_ip"] += 1
            return POLICY
        return None

    def open(self, ip: str) -> Conn:
        conn = Conn(ip, Bucket(self.frame_rate, self.frame_burst, self.clock))
        self._conns.append(conn)
        return conn

    def release(self, conn: Conn) -> None:
        conn.open = False
        if conn in self._conns:
            self._conns.remove(conn)
        if conn.mac and self._owner.get(conn.mac) is conn:
            del self._owner[conn.mac]

    @property
    def live(self) -> int:
        return sum(1 for c in self._conns if c.open)

    # --------------------------------------------------------------------- frames
    def handle(self, conn: Conn, raw: object) -> int | None:
        """What to do with one frame: None to carry on, or a close code. Never raises."""
        try:
            return self._handle(conn, raw)
        except Exception:  # noqa: BLE001 - a strange frame must never stop the hub
            log.exception("Shelly receiver: could not handle a frame")
            self.counters["error"] += 1
            return None

    def _handle(self, conn: Conn, raw: object) -> int | None:
        now = self.clock()
        if not self.enabled():
            return POLICY
        conn.frames += 1
        if raw is not None and len(raw) > rpc.MAX_FRAME:
            self.counters["too_big"] += 1
            return TOO_BIG
        if not conn.bucket.take() or not self._global.take():
            self.counters["rate"] += 1
            return POLICY
        frame = rpc.parse_frame(raw)
        if isinstance(frame, rpc.Reject):
            self.counters[frame.reason] += 1
            conn.bad += 1
            return POLICY if conn.bad > MAX_BAD_FRAMES else None
        if conn.mac and conn.mac != frame.mac:
            self.counters["mac_change"] += 1       # one connection speaks for one device
            return POLICY
        conn.mac = frame.mac
        mac = frame.mac
        if mac in self.ignored():
            self.counters["ignored"] += 1
            return NORMAL
        if mac not in self.allowed:
            self._note_unknown(frame, conn.ip, now)
            return NORMAL
        if conn.superseded:
            return NORMAL                           # a newer connection speaks for this device now
        if not self._claim(conn, mac):
            return POLICY
        prev = self._status.get(mac)
        new = rpc.merge_status(prev, frame)
        if frame.method != "NotifyEvent":
            self._status[mac] = new
        self.counters["frames"] += 1
        if self.sink is not None:
            self.sink.deliver(mac, frame, prev, new, conn.ip, now)
        self._log_counters(now)
        return None

    def _claim(self, conn: Conn, mac: str) -> bool:
        """One live socket speaks for one adopted device. A second sender from another address is refused unless
        it comes from the address the device was last at (then it replaces the old one)."""
        owner = self._owner.get(mac)
        if owner is None or owner is conn or not owner.open or owner.ip == conn.ip:
            if owner is not None and owner is not conn:
                owner.superseded = True
            self._owner[mac] = conn
            return True
        known = self.sink.known_ip(mac) if self.sink is not None else ""
        if known and conn.ip == known:
            owner.superseded = True
            self._owner[mac] = conn
            return True
        self.counters["conflict"] += 1
        self.conflicts[mac] = self.clock()
        return False

    def _note_unknown(self, frame: "rpc.Frame", ip: str, now: float) -> None:
        names = [n[:24] for n in rpc.component_names(frame.params)][:24]
        entry = self.unknown.pop(frame.mac, None)
        if entry is None:
            entry = Unknown(frame.mac, frame.src[:64], ip[:45], rpc.model_label(frame.prefix), names,
                            rpc.kind_label(names), now, now, 0)
        entry.last, entry.ip, entry.count = now, ip[:45], entry.count + 1
        if names:
            entry.components, entry.label = names, rpc.kind_label(names)
        self.unknown[frame.mac] = entry
        while len(self.unknown) > MAX_UNKNOWN:
            self.unknown.popitem(last=False)
        self.counters["unknown"] += 1
        last = self._logged.pop(frame.mac, None)
        self._logged[frame.mac] = last if last is not None and now - last < LOG_EVERY_S else now
        if last is None or now - last >= LOG_EVERY_S:
            # No address or hardware address in the log (house rule): the model and a count, at most hourly per device
            log.info("Shelly: a device that is not on your list sent something (%s, %d on the found list)",
                     rpc.model_label(frame.prefix), len(self.unknown))
        while len(self._logged) > 64:
            self._logged.popitem(last=False)

    def unknown_list(self) -> list[dict]:
        return [u.to_dict() for u in reversed(self.unknown.values())]

    def _log_counters(self, now: float) -> None:
        at, _ = self._counters_logged
        total = sum(v for k, v in self.counters.items() if k != "frames")
        if total and now - at >= COUNTERS_EVERY_S and total != self._counters_logged[1]:
            self._counters_logged = (now, total)
            log.info("Shelly receiver counters: %s", dict(self.counters))

    # --------------------------------------------------------------- the web socket
    async def serve(self, ws) -> None:
        """Run one connection. ``ws`` needs ``client``, ``headers``, ``scope``, ``accept``, ``receive`` and
        ``close``. Never sends a message: accept, then listen, then close."""
        ip = ws.client.host if getattr(ws, "client", None) else ""
        code = self.admit(ip, bool(ws.headers.get("origin")))
        if code is not None:
            await _close(ws, code)
            return
        conn = self.open(ip)
        try:
            offered = ws.scope.get("subprotocols") or []
            await ws.accept(subprotocol="json-rpc" if "json-rpc" in offered else None)
            timeout = self.first_frame_s
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout)
                except asyncio.TimeoutError:
                    self.counters["timeout"] += 1
                    await _close(ws, POLICY if conn.frames == 0 else GOING_AWAY)
                    return
                if msg.get("type") == "websocket.disconnect":
                    return
                raw = msg.get("text") if msg.get("text") is not None else msg.get("bytes")
                code = self.handle(conn, raw if raw is not None else b"")
                if code is None and conn.superseded:
                    code = NORMAL
                if code is not None:
                    await _close(ws, code)
                    return
                timeout = self.idle_s
        except Exception:  # noqa: BLE001 - a dropped connection is normal; nothing here may reach the hub
            return
        finally:
            self.release(conn)


async def _close(ws, code: int) -> None:
    try:
        await ws.close(code=code)
    except Exception:  # noqa: BLE001 - already closed
        pass


def peer_ip(value: object) -> str:
    """A valid IP address as text, or "" (a test client's peer name is not one)."""
    try:
        return str(ipaddress.ip_address(value)) if isinstance(value, str) else ""
    except ValueError:
        return ""
