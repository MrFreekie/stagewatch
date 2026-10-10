"""Read-only GLOBCON client over WebSocket (``ws://host:9091/api/v1``, binary protobuf).

Written from the generated protocol code in GLOBCON's own web app and checked against a real capture
of that app (see protocol.py). NOT tested against a live GLOBCON by Stagewatch.

What it does: opens one connection, subscribes to ``/general/`` (controller names), and for each
controller a dashboard shows, to ``/controller/N/`` (strip labels, layer and layer labels) and the
meter stream ``/controller/N/meters`` (about 10 updates a second). It sends a small Ping every 2
seconds like the web app does.

What it never does: every outgoing frame goes through ``_send``, which refuses anything that
``protocol.allowed`` does not accept, so only Ping, GET, SUBSCRIBE, UNSUBSCRIBE (and AUTH, only if a
password is set and GLOBCON asks for one) can leave. SET, UPDATE and ACTION, which move faders, mute,
solo, change layers and run functions, cannot be sent, and a test pins that.

Hostile or damaged input is dropped: frames over 256 KiB close the connection, at most 2,000 frames a
second are looked at, text frames and undecodable frames are ignored. It reconnects with a growing wait
(1 s to 30 s), follows no redirects, connects only to this computer or a local-network address, never
logs a frame or the password, and cannot stop the hub.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from . import mapping, protocol
from .source import GlobconSource, LinkCallback, MetersCallback, ValuesCallback

log = logging.getLogger(__name__)

# The websockets library logs every frame at DEBUG level, which would write the password into the log.
_WS_LOG = logging.getLogger(__name__ + ".ws")
_WS_LOG.propagate = False
_WS_LOG.addHandler(logging.NullHandler())

BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
STABLE_S = 30.0
OPEN_TIMEOUT_S = 5.0
PING_EVERY_S = 2.0           # the web app's rate
SILENCE_S = 10.0             # nothing at all (not even a Pong) for this long: the link is dead
MAX_BYTES = 256 * 1024
MAX_FRAMES_PER_S = 2000
TICK_S = 0.5

TEXT = {
    "no_address": "No address set",
    "unreachable": "Can't reach GLOBCON",
    "timeout": "GLOBCON did not answer",
    "closed": "GLOBCON closed the connection",
    "silent": "GLOBCON stopped answering",
    "too_large": "GLOBCON sent more data than we accept",
    "not_globcon": "That address did not answer like GLOBCON's remote controller",
    "not_local": "That name does not point to a computer on the local network",
    "refused": "Can't reach GLOBCON",
    "connected": "Connected, waiting for levels",
}

# Admin page only (never sent to dashboards): why the link is down, in words a crew member can act on.
# {where} is the address as saved; {port} the port; {secs} the silence limit.
HINTS = {
    "no_address": "No GLOBCON address is saved yet. Type the computer GLOBCON runs on (127.0.0.1 if it is this computer) and Save.",
    "timeout": "Nothing answered at {where}. Check that GLOBCON is running and that the Windows firewall on its computer allows port {port}.",
    "refused": "The computer at {where} answered, but GLOBCON is not listening on port {port}. Check that GLOBCON is running and that the port is the one GLOBCON uses.",
    "unreachable": "Could not reach {where}. Check the address is spelt correctly and that this computer is on the same network as GLOBCON.",
    "not_local": "The name {where} does not point to a computer on the local network, so Stagewatch will not connect to it. Use the address of the GLOBCON computer on your show network.",
    "not_globcon": "Something answered at {where}, but it did not reply like GLOBCON's remote control. Check the address and port.",
    "silent": "Connected to {where}, but GLOBCON has sent nothing for {secs} seconds. Check that GLOBCON is still running and responding on its computer.",
    "closed": "GLOBCON at {where} closed the connection. Stagewatch will keep trying; check GLOBCON has not been restarted or closed.",
    "too_large": "GLOBCON at {where} sent more data than Stagewatch accepts, so it dropped the connection. Check that this really is GLOBCON.",
}


class _NoRedirectConnect(connect):
    """websockets follows up to ten redirects by default. We follow none."""

    def process_redirect(self, exc):
        return exc


class NotLocal(Exception):
    """A host name resolved to an address that is not on the local network."""


class Silent(Exception):
    """Nothing arrived for SILENCE_S."""


class Closed(Exception):
    """The connection ended normally."""


def describe(exc: BaseException) -> str:
    if isinstance(exc, ConnectionClosed):
        for frame in (exc.rcvd, exc.sent):
            if frame is not None and frame.code == 1009:
                return "too_large"
        return "closed"
    if isinstance(exc, NotLocal):
        return "not_local"
    if isinstance(exc, (InvalidHandshake, InvalidURI)):
        return "not_globcon"
    if isinstance(exc, Silent):
        return "silent"
    if isinstance(exc, Closed):
        return "closed"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    return "unreachable"


def is_acceptable_address(ip: ipaddress._BaseAddress) -> bool:
    """This computer or a local network. Public, multicast and unspecified addresses are refused, and
    IPv6 forms that carry another address inside (IPv4-mapped, 6to4, Teredo)."""
    if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
        return False
    return not (ip.is_global or ip.is_multicast or ip.is_unspecified)


async def resolve_local(host: str, port: int) -> str:
    """The address to connect to: an IP typed in is used as it is; a name is looked up here and EVERY
    answer must be acceptable, and we connect to the checked address, so the name cannot change under us."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    infos = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), OPEN_TIMEOUT_S)
    addrs = [i[4][0].split("%")[0] for i in infos]
    if not addrs or not all(is_acceptable_address(ipaddress.ip_address(a)) for a in addrs):
        raise NotLocal()
    return addrs[0]


def ws_url(host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"ws://{h}:{port}{protocol.API_PATH}"


class GlobconClient(GlobconSource):
    verified = False
    label = "GLOBCON"

    def __init__(self, target_fn: Callable[[], tuple[str, int] | None], on_values: ValuesCallback,
                 on_meters: MetersCallback, on_link: LinkCallback, *,
                 password_fn: Callable[[], str] = lambda: "", clock: Callable[[], float] = time.monotonic,
                 backoff_min_s: float = BACKOFF_MIN_S, backoff_max_s: float = BACKOFF_MAX_S,
                 open_timeout_s: float = OPEN_TIMEOUT_S, ping_every_s: float = PING_EVERY_S,
                 silence_s: float = SILENCE_S, stable_s: float = STABLE_S,
                 max_frames_per_s: int = MAX_FRAMES_PER_S, sleep=asyncio.sleep) -> None:
        super().__init__(on_values, on_meters, on_link)
        self._target_fn = target_fn
        self._password_fn = password_fn          # a function, so the password is never in a log-able attribute
        self._clock = clock
        self._bmin, self._bmax = backoff_min_s, backoff_max_s
        self._open_timeout, self._ping_every, self._silence = open_timeout_s, ping_every_s, silence_s
        self._stable_s, self._max_fps = stable_s, max_frames_per_s
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        self._wanted: list[int] = []
        self._wake: asyncio.Event | None = None
        self._up_since: float | None = None
        self.dropped_frames = 0
        self._down_kind = ""                      # why the link is down, while it is (for hint())
        self.sent_methods: dict[str, int] = {}    # for tests and diagnostics: how many of each method went out

    # ------------------------------------------------------------ source API
    def set_wanted(self, controllers: list[int]) -> None:
        self._wanted = sorted({c for c in controllers if 0 <= c < protocol.MAX_CONTROLLERS})
        if self._wake is not None:
            self._wake.set()

    async def start(self) -> None:
        if self._task is None:
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="globcon-client")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def hint(self) -> str:
        """Admin page only: the address being tried and why it cannot connect, in plain words. Empty while
        the link is up. The address is the one saved, so this must never reach a dashboard."""
        kind = self._down_kind
        if not kind:
            return ""
        target = self._target_fn()
        if target is None:
            return HINTS["no_address"]
        host, port = target
        where = f"{f'[{host}]' if ':' in host else host}:{port}"
        return HINTS.get(kind, HINTS["unreachable"]).format(where=where, port=port, secs=f"{self._silence:g}")

    # ------------------------------------------------------------- internals
    def _link(self, up: bool, detail: str) -> None:
        try:
            self._on_link(up, detail)
        except Exception:  # noqa: BLE001
            log.exception("GLOBCON link update failed")

    async def _send(self, ws, frame: bytes) -> None:
        """The only place anything is sent to GLOBCON. Refuses a frame not on the read-only list."""
        if not protocol.allowed(frame):
            raise RuntimeError("blocked: not a read-only message")
        name = protocol.METHOD_NAMES.get(protocol.decode_container(frame).method, "?")
        self.sent_methods[name] = self.sent_methods.get(name, 0) + 1
        await ws.send(frame)

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
                    await self._session(ip, target[1])
                    category = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means "wait, then try again"
                category = describe(exc)
                log.info("GLOBCON connection ended (%s)", category)
            self.problem = "api" if category == "not_globcon" else ""
            self._down_kind = category
            self._link(False, TEXT.get(category, TEXT["unreachable"]))
            stable = self._up_since is not None and self._clock() - self._up_since >= self._stable_s
            backoff = self._bmin if stable else min(backoff * 2, self._bmax)
            await self._sleep(backoff)

    def _open(self, url: str):
        return _NoRedirectConnect(url, max_size=MAX_BYTES, proxy=None, open_timeout=self._open_timeout,
                                  ping_interval=None, compression=None, max_queue=64, logger=_WS_LOG)

    async def _session(self, ip: str, port: int) -> None:
        async with self._open(ws_url(ip, port)) as ws:
            self._up_since = self._clock()
            self._down_kind = ""
            self._link(True, TEXT["connected"])
            subscribed: set[int] = set()
            auth = {"requires": set(), "authorized": set(), "tried": set()}
            last_rx = [self._clock()]
            failure: list[BaseException] = []
            failed = asyncio.Event()
            reader = asyncio.create_task(self._read(ws, auth, last_rx, failure, failed))
            try:
                await self._send(ws, protocol.subscribe_value(protocol.GENERAL_PATH))
                await self._send(ws, protocol.get_value(protocol.GENERAL_PATH))
                next_ping = self._clock()
                assert self._wake is not None
                while True:
                    self._wake.clear()
                    if failed.is_set():
                        raise failure[0]
                    await self._reconcile(ws, subscribed)
                    await self._login(ws, auth, subscribed)
                    now = self._clock()
                    if now - last_rx[0] > self._silence:
                        raise Silent()
                    if now >= next_ping:
                        await self._send(ws, protocol.ping())
                        next_ping = now + self._ping_every
                    waiters = [asyncio.create_task(self._wake.wait()), asyncio.create_task(failed.wait())]
                    try:
                        await asyncio.wait(waiters, timeout=TICK_S, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for w in waiters:
                            w.cancel()
                        await asyncio.gather(*waiters, return_exceptions=True)
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)

    async def _reconcile(self, ws, subscribed: set[int]) -> None:
        want = set(self._wanted)
        for n in sorted(subscribed - want):
            await self._send(ws, protocol.unsubscribe_value(protocol.controller_path(n)))
            await self._send(ws, protocol.unsubscribe_meters(n))
            subscribed.discard(n)
        for n in sorted(want - subscribed):
            await self._send(ws, protocol.subscribe_value(protocol.controller_path(n)))
            await self._send(ws, protocol.subscribe_meters(n))
            await self._send(ws, protocol.get_value(protocol.controller_path(n)))
            subscribed.add(n)

    async def _login(self, ws, auth: dict, subscribed: set[int]) -> None:
        """Log in to a controller GLOBCON says needs a password, once per connection, only if one is set.
        A refused password is not retried (it could lock the controller)."""
        password = self._password_fn()
        if not password:
            return
        for n in sorted(subscribed & auth["requires"]):
            if n not in auth["authorized"] and n not in auth["tried"]:
                auth["tried"].add(n)
                await self._send(ws, protocol.login(n, password))

    async def _read(self, ws, auth: dict, last_rx: list, failure: list, failed: asyncio.Event) -> None:
        window_start, in_window = self._clock(), 0
        try:
            async for raw in ws:
                last_rx[0] = self._clock()
                if self._clock() - window_start >= 1.0:
                    window_start, in_window = self._clock(), 0
                in_window += 1
                if in_window > self._max_fps:
                    self.dropped_frames += 1
                    continue
                if isinstance(raw, str):
                    continue      # GLOBCON speaks binary; text is not for us
                self._handle(raw, auth)
            failure.append(Closed())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            failure.append(exc)
        failed.set()

    def _handle(self, raw: bytes, auth: dict) -> None:
        try:
            c = protocol.decode_container(raw)
            if c.type_name == "Value":
                values = [protocol.decode_value(c.payload)]
            elif c.type_name == "ValueList":
                values = protocol.decode_value_list(c.payload)
            elif c.type_name == "RTValue":
                rt = protocol.decode_rtvalue(c.payload)
                n = mapping.meters_pair(rt.path)
                if n is not None:
                    self._on_meters(n, rt.blob)
                return
            else:
                return        # Pong and anything else: it only counts as "still alive"
        except protocol.DecodeError:
            return
        except Exception:  # noqa: BLE001 - a bad frame must not end the connection
            log.exception("GLOBCON meter update failed")
            return
        for v in values:
            self._note_auth(v, auth)
        try:
            self._on_values(values)
        except Exception:  # noqa: BLE001
            log.exception("GLOBCON value update failed")

    @staticmethod
    def _note_auth(v: protocol.Value, auth: dict) -> None:
        parts = v.path.split("/")      # ['', 'general', 'requiresPassword', '0']
        if len(parts) == 4 and parts[1] == "general" and parts[3].isdigit() and v.kind == "b" \
                and parts[2] in ("requiresPassword", "authorized"):
            n = int(parts[3])
            key = "requires" if parts[2] == "requiresPassword" else "authorized"
            (auth[key].add if v.value else auth[key].discard)(n)
