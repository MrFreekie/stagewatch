"""Read-only Smaart client over WebSocket.  UNVERIFIED: written without the Smaart SDK.

What is certain (Rational Acoustics' integration page): the Smaart API is JSON over a WebSocket and
is switched on in Smaart under Options > Preferences > API. What is NOT known to us: the port, the
log-in, whether a client must ask for values or just listens, and every message and field name. So:

* The client only LISTENS. It never sends a data frame, not a log-in, not a subscribe, not a command
  (the library's own ping/pong keep-alive is the only traffic we originate). A test holds it to
  this. Anything Smaart needs to be asked for will be added, as a fixed allow-list, once the SDK
  says what it is.
* Field names are not here. They are in ``mapping.py`` (empty until the SDK is read), so with the
  shipped table every value is "not available" and the device says so, plainly.
* Hostile or damaged input is dropped: messages over 64 KiB close the connection, at most 50 a
  second are looked at, NaN, infinity, strings and out-of-range numbers are "not available".
* It reconnects with a growing wait (1 s to 30 s), follows no redirects, and cannot stop the hub.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Callable, Mapping as MappingType

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from ...core.spl import SplReading
from .mapping import BY_MAJOR, DEFAULT, MAX_BYTES, VERIFIED, Mapping, parse_frame
from .source import LinkCallback, ReadingCallback, SplSource

log = logging.getLogger(__name__)

BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
STABLE_S = 30.0   # the wait starts over only after the link has stayed up this long
OPEN_TIMEOUT_S = 5.0
MAX_FRAMES_PER_S = 50

# Fixed, plain wording for crew (never raw error text, which can carry addresses).
TEXT = {
    "no_address": "No address set",
    "unreachable": "Can't reach Smaart",
    "timeout": "Smaart did not answer",
    "closed": "Smaart closed the connection",
    "too_large": "Smaart sent more data than we accept",
    "not_smaart": "That address did not answer like Smaart",
    "not_local": "That name does not point to a computer on the local network",
    "connected": "Connected, waiting for values",
    "connected_unverified": "Connected, but Smaart's field names are not verified yet, so no values can be read",
}


class _NoRedirectConnect(connect):
    """websockets follows up to ten redirects by default. We follow none."""

    def process_redirect(self, exc):
        return exc


def describe(exc: BaseException) -> str:
    """A fixed category for a failure (the key into TEXT)."""
    if isinstance(exc, ConnectionClosed):
        for frame in (exc.rcvd, exc.sent):
            if frame is not None and frame.code == 1009:
                return "too_large"
        return "closed"
    if isinstance(exc, NotLocal):
        return "not_local"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, (InvalidHandshake, InvalidURI)):
        return "not_smaart"
    return "unreachable"


class NotLocal(Exception):
    """A host name resolved to an address that is not on the local network."""


def is_local_address(ip: ipaddress._BaseAddress) -> bool:
    """True for a private LAN address. Refuses global, loopback, link-local, multicast and
    unspecified addresses, and IPv6 forms that carry another address inside (IPv4-mapped, 6to4,
    Teredo), which are checked as refused because the real destination is not obvious."""
    if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
        return False
    return not (ip.is_global or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified)


async def resolve_local(host: str, port: int) -> str:
    """The address to connect to. An IP address typed in is used as it is (the settings already
    refused a public one). A name is looked up here and EVERY answer must be a local address; the
    caller connects to the checked address itself, so the name cannot change under us."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addrs = [i[4][0].split("%")[0] for i in infos]
    if not addrs or not all(is_local_address(ipaddress.ip_address(a)) for a in addrs):
        raise NotLocal()
    return addrs[0]


def ws_url(host: str, port: int) -> str:
    """``ws://host:port/`` (an IPv6 address gets its brackets). Plain ws: Smaart on a LAN is not
    known to offer TLS."""
    h = f"[{host}]" if ":" in host else host
    return f"ws://{h}:{port}/"


class SmaartSource(SplSource):
    verified = VERIFIED
    label = "Smaart"

    def __init__(self, target_fn: Callable[[], tuple[str, int] | None], on_reading: ReadingCallback,
                 on_link: LinkCallback, *, table: MappingType[str, Mapping] = BY_MAJOR,
                 default: Mapping = DEFAULT, clock: Callable[[], float] = time.time,
                 backoff_min_s: float = BACKOFF_MIN_S, backoff_max_s: float = BACKOFF_MAX_S,
                 open_timeout_s: float = OPEN_TIMEOUT_S, max_frames_per_s: int = MAX_FRAMES_PER_S,
                 stable_s: float = STABLE_S, sleep=asyncio.sleep) -> None:
        super().__init__(on_reading, on_link)
        self._target_fn = target_fn
        self._table, self._default = table, default
        self._time = clock
        self._bmin, self._bmax = backoff_min_s, backoff_max_s
        self._open_timeout = open_timeout_s
        self._max_fps = max_frames_per_s
        self._stable_s = stable_s
        self._sleep = sleep
        self._up_since: float | None = None
        self._task: asyncio.Task | None = None
        self.dropped_frames = 0   # over the rate cap, for the diagnostics

    # ------------------------------------------------------------ source API
    async def start(self) -> None:
        if self._task is None:
            self.version = ""
            self._task = asyncio.create_task(self._run(), name="smaart-client")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ------------------------------------------------------------- internals
    def _link(self, up: bool, detail: str) -> None:
        try:
            self._on_link(up, detail)
        except Exception:  # noqa: BLE001 - a status callback must not end the client
            log.exception("Smaart link update failed")

    def _deliver(self, reading: SplReading) -> None:
        try:
            self._on_reading(reading)
        except Exception:  # noqa: BLE001
            log.exception("Smaart reading update failed")

    async def _run(self) -> None:
        backoff = self._bmin
        while True:
            category = "unreachable"
            self._up_since = None
            try:
                target = self._target_fn()
                if target is None:
                    category = "no_address"
                else:
                    ip = await resolve_local(*target)
                    await self._session(ws_url(ip, target[1]))
                    category = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means "wait, then try again"
                category = describe(exc)
                log.info("Smaart connection ended (%s)", category)
            self._link(False, TEXT.get(category, TEXT["unreachable"]))
            # Readings before an abnormal drop do not earn a short wait: only a link that stayed up
            # for a while does, so a source that connects and drops at once backs off.
            stable = self._up_since is not None and time.monotonic() - self._up_since >= self._stable_s
            backoff = self._bmin if stable else min(backoff * 2, self._bmax)
            await self._sleep(backoff)

    async def _session(self, url: str) -> None:
        """Listen until the connection ends. Never sends a data frame."""
        window_start, in_window = time.monotonic(), 0
        async with _NoRedirectConnect(url, max_size=MAX_BYTES, proxy=None, open_timeout=self._open_timeout,
                                      ping_interval=20, ping_timeout=20, compression=None,
                                      max_queue=4) as ws:
            self._up_since = time.monotonic()
            self._link(True, TEXT["connected"] if VERIFIED else TEXT["connected_unverified"])
            async for raw in ws:
                now = time.monotonic()
                if now - window_start >= 1.0:
                    window_start, in_window = now, 0
                in_window += 1
                if in_window > self._max_fps:
                    self.dropped_frames += 1
                    continue
                parsed = parse_frame(raw, self._table, self._default)
                if parsed is None:
                    continue
                if parsed.version:
                    self.version = parsed.version
                self._deliver(SplReading(self._time(), parsed.values, parsed.version))
