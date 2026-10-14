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
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Protocol

from ...core.model import normalise_mac
from .http import GET_DEVICE_INFO, GET_STATUS, Fetch, ShellyError, get_json

log = logging.getLogger(__name__)

BACKOFF_MAX_S = 60.0


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
    """Asks each meter for ``Shelly.GetDeviceInfo`` (when it first connects, and again after every
    failure, to check it is still the same meter) and ``Shelly.GetStatus`` every few seconds.
    Nothing else is ever requested (see http.py)."""
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
        while True:
            try:
                if not verified:
                    info = parse_device_info(await self._request(target.host, GET_DEVICE_INFO))
                    if info.auth_enabled:
                        raise ShellyError("needs_password")
                    if info.mac != target.mac:
                        raise ShellyError("other_meter")
                    sink.identified(target.mac, info)
                    verified = True
                status = await self._request(target.host, GET_STATUS)
                failures = 0
                sink.status(target.mac, status, self._clock())
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
