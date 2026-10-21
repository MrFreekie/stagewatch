"""How readings reach the common layer: the transport interface and the HTTP polling transport.

A transport finds out what each adopted meter is saying and tells a *sink* (the integration):

* ``identified(mac, info)``: the meter answered and says who it is;
* ``status(mac, status, ts)``: a status dictionary in the shape of ``Shelly.GetStatus``;
* ``lost(mac, category, failures)``: the meter could not be read (a fixed category, never text from
  the meter).

Everything above this line (readings, entities, status, gaps, markers) is the same whatever the
transport is. A WebSocket receiver can be added later as another :class:`Transport` that calls the
same three sink methods; the reading layer does not change.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ...core.model import normalise_mac
from . import readings as rd
from .http import GEN1_INFO, GEN1_SETTINGS, GEN1_STATUS, GET_CONFIG, GET_DEVICE_INFO, GET_STATUS, Fetch, ShellyError, get_json

log = logging.getLogger(__name__)

BACKOFF_MAX_S = 60.0
NAMES_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class Target:
    """A meter to read: its MAC (identity), where to ask now, and how often."""
    mac: str
    host: str
    interval_s: float


@dataclass(frozen=True)
class DeviceInfo:
    """What a meter says about itself in Shelly.GetDeviceInfo. ``mac``, ``firmware`` and ``name`` are
    for the admin page only."""
    mac: str
    model: str
    app: str
    firmware: str
    name: str
    auth_enabled: bool
    gen1: bool = False


def parse_device_info(data: object) -> DeviceInfo:
    """Raises ShellyError("not_shelly") unless this looks like a Shelly generation 2 or newer, and
    ShellyError("needs_password") when the meter says a password is set."""
    if not isinstance(data, dict):
        raise ShellyError("not_shelly")
    mac = normalise_mac(data.get("mac"))
    gen = data.get("gen")
    if mac is None or isinstance(gen, bool) or not isinstance(gen, int) or gen < 2:
        raise ShellyError("not_shelly")

    def text(key: str, limit: int = 60) -> str:
        v = data.get(key)
        return "".join(ch for ch in v if ch.isprintable())[:limit] if isinstance(v, str) else ""

    return DeviceInfo(mac=mac, model=text("model"), app=text("app"), firmware=text("ver"),
                      name=text("name"), auth_enabled=data.get("auth_en") is True)


def parse_gen1_info(data: object) -> DeviceInfo:
    """A first-generation ``GET /shelly`` reply (``type``, ``mac``, ``fw``, ``auth``). Raises
    ShellyError("not_shelly") unless it looks like one; a reply with a ``gen`` field is not Gen1."""
    if not isinstance(data, dict) or "gen" in data:
        raise ShellyError("not_shelly")
    mac = normalise_mac(data.get("mac"))
    if mac is None or not isinstance(data.get("type"), str):
        raise ShellyError("not_shelly")

    def text(key: str) -> str:
        v = data.get(key)
        return "".join(ch for ch in v if ch.isprintable())[:60] if isinstance(v, str) else ""

    return DeviceInfo(mac=mac, model=text("type"), app="", firmware=text("fw"), name="",
                      auth_enabled=data.get("auth") is True, gen1=True)


async def identify(fetch: Fetch, host: str) -> tuple[DeviceInfo, bool]:
    """Ask who is at ``host``. Tries the Gen2+ way first; a meter with no such interface (404) is then
    asked the Gen1 way. Returns (info, is_gen1). Raises ShellyError."""
    try:
        return parse_device_info(await fetch(host, GET_DEVICE_INFO)), False
    except ShellyError as exc:
        if exc.category != "not_shelly":
            raise
    return parse_gen1_info(await fetch(host, GEN1_INFO)), True


async def read_status(fetch: Fetch, host: str, gen1: bool) -> dict:
    """The meter's readings as a status dictionary the reading layer understands."""
    if gen1:
        return rd.gen1_status(await fetch(host, GEN1_STATUS))
    return await fetch(host, GET_STATUS)


NAME_LIMIT = 40     # the longest circuit label the card takes
_NAMED = re.compile(r"^(switch|em1|em|pm1):([0-7])$")


@dataclass(frozen=True)
class DeviceNames:
    """The names typed into a meter itself (read only). Untrusted text: printable characters only, cut to
    NAME_LIMIT. ``channels`` is keyed like the reading channels ("em1_0", "switch_1", "em0")."""
    device: str = ""
    channels: dict = field(default_factory=dict)


def _name(v: object) -> str:
    if not isinstance(v, str):
        return ""
    return " ".join("".join(ch for ch in v if ch.isprintable()).split())[:NAME_LIMIT]


def parse_names(config: object, gen1: bool) -> DeviceNames:
    """Names from a ``Shelly.GetConfig`` reply (Gen2+) or a ``/settings`` reply (Gen1). Anything odd gives no name."""
    if not isinstance(config, dict):
        return DeviceNames()
    if gen1:
        return DeviceNames(_name(config.get("name")))
    out = {}
    for key, comp in config.items():
        m = _NAMED.match(key) if isinstance(key, str) else None
        name = _name(comp.get("name")) if m and isinstance(comp, dict) else ""
        if name:
            out[f"em{m.group(2)}" if m.group(1) == "em" else f"{m.group(1)}_{m.group(2)}"] = name
    sys_ = config.get("sys")
    dev = sys_.get("device") if isinstance(sys_, dict) else None
    return DeviceNames(_name(dev.get("name")) if isinstance(dev, dict) else "", out)


async def read_names(fetch: Fetch, host: str, gen1: bool) -> DeviceNames:
    """One read-only request for the names typed into the meter. Never raises: a meter that does not answer, or
    has no names, gives no names and the default labels stay."""
    try:
        return parse_names(await asyncio.wait_for(fetch(host, GEN1_SETTINGS if gen1 else GET_CONFIG), NAMES_TIMEOUT_S), gen1)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - names are a convenience
        return DeviceNames()


class Sink(Protocol):
    def identified(self, mac: str, info: DeviceInfo) -> None: ...
    def status(self, mac: str, status: dict, ts: float) -> None: ...
    def lost(self, mac: str, category: str, failures: int) -> None: ...


class Transport(ABC):
    """Source of readings for a set of adopted meters."""
    name = "transport"

    @abstractmethod
    async def start(self, sink: Sink) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def add(self, target: Target) -> None:
        """Start (or restart with new settings) reading this meter."""

    @abstractmethod
    def remove(self, mac: str) -> None: ...


class HttpPollTransport(Transport):
    """Asks each meter who it is (when it first connects, and again after every failure, to check it
    is still the same meter) and for its readings every few seconds: ``Shelly.GetDeviceInfo`` and
    ``Shelly.GetStatus``, or ``/shelly`` and ``/status`` for a first-generation meter. Nothing else is
    ever requested (see http.py)."""
    name = "HTTP polling"

    def __init__(self, fetch: Fetch = get_json, clock: Callable[[], float] = time.time,
                 sleep=asyncio.sleep, max_parallel: int = 4) -> None:
        self._fetch = fetch
        self._clock = clock
        self._sleep = sleep
        self._gate = asyncio.Semaphore(max_parallel)
        self._sink: Sink | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._targets: dict[str, Target] = {}

    async def start(self, sink: Sink) -> None:
        self._sink = sink
        for target in list(self._targets.values()):
            self._spawn(target)

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._sink = None

    def add(self, target: Target) -> None:
        self.remove(target.mac)
        self._targets[target.mac] = target
        if self._sink is not None:
            self._spawn(target)

    def remove(self, mac: str) -> None:
        self._targets.pop(mac, None)
        task = self._tasks.pop(mac, None)
        if task is not None:
            task.cancel()

    def _spawn(self, target: Target) -> None:
        self._tasks[target.mac] = asyncio.create_task(self._run(target), name=f"shelly-poll-{target.mac[-4:]}")

    async def _request(self, host: str, method: str) -> dict:
        async with self._gate:   # at most a few meters are being asked at the same moment
            return await self._fetch(host, method)

    async def _run(self, target: Target) -> None:
        sink = self._sink
        failures = 0
        verified = False
        gen1 = False
        named = False
        while True:
            try:
                if not verified:
                    info, gen1 = await identify(self._request, target.host)
                    if info.auth_enabled:
                        raise ShellyError("needs_password")
                    if info.mac != target.mac:
                        raise ShellyError("other_meter")
                    sink.identified(target.mac, info)
                    verified = True
                    named = False
                status = await read_status(self._request, target.host, gen1)
                failures = 0
                sink.status(target.mac, status, self._clock())
                if not named:       # once per connection, after the first reading, so it never delays one
                    named = True
                    names = await read_names(self._request, target.host, gen1)
                    if hasattr(sink, "names"):
                        sink.names(target.mac, names)
                delay = target.interval_s
            except asyncio.CancelledError:
                raise
            except ShellyError as exc:
                verified = False
                failures += 1
                self._tell_lost(sink, target.mac, exc.category, failures)
                delay = min(BACKOFF_MAX_S, target.interval_s * (2 ** min(failures, 6)))
            except Exception:  # noqa: BLE001 - one bad meter must never stop the others
                verified = False
                failures += 1
                log.exception("Shelly poll failed unexpectedly")
                self._tell_lost(sink, target.mac, "bad_reply", failures)
                delay = min(BACKOFF_MAX_S, target.interval_s * (2 ** min(failures, 6)))
            await self._sleep(delay)

    @staticmethod
    def _tell_lost(sink: Sink, mac: str, category: str, failures: int) -> None:
        try:
            sink.lost(mac, category, failures)
        except Exception:  # noqa: BLE001
            log.exception("Shelly could not record a failed poll")
