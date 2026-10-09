"""Read-only Smaart client over WebSocket.

UNVERIFIED: written from the script Smaart's own SPL web page uses (the protocol notes), NOT tested
against a live Smaart, and no real data message has been seen. See mapping.py for the shapes.

How it talks to Smaart (all plain ``ws://``, the same host and port the SPL web page is served from):

1. *Probe socket* at ``/api/v4/``: ask whether a password is needed, log in if it is, then ask which
   inputs are active and which metrics exist. It is asked again every 15 seconds (every 5 while there
   are no inputs) so a newly started input appears without a restart.
2. *One meter stream per input in use*, at the path Smaart gives for that input. Two chosen values on
   the same input share one stream. On opening, it asks for one update a second.

The client sends exactly four kinds of message and nothing else (outbound.py is the one place, and
``_send`` refuses anything not on that list; a test pins it): the probe, the inputs question, the
password (log-in only) and the one-update-a-second request. It never starts or stops anything in
Smaart, never touches calibration, gain, logging, alarms or the mix, and never reads Smaart's history
streams (no back-fill: a gap stays a gap).

Hostile or damaged input is dropped: messages over 64 KiB close the connection, at most 50 a second
per socket are looked at, NaN, infinity, strings and out-of-range numbers are "not available". It
reconnects with a growing wait (1 s to 30 s), follows no redirects, connects only to a local-network
address, never logs a message or the password, and cannot stop the hub. A wrong password is not
retried (it would only risk locking the API); a missing one is checked again once a minute.
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

from ...core.spl import SplReading
from . import mapping, outbound
from .mapping import MAX_BYTES, VERIFIED
from .source import CatalogCallback, LinkCallback, ReadingCallback, SplSource

log = logging.getLogger(__name__)

# The websockets library logs every frame it sends or receives at DEBUG level, which would write the
# API password into the log when "verbose" is on. Its connections get this logger instead: it goes
# nowhere, whatever the log level.
_WS_LOG = logging.getLogger(__name__ + ".ws")
_WS_LOG.propagate = False
_WS_LOG.addHandler(logging.NullHandler())

BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
STABLE_S = 30.0   # the wait starts over only after the link has stayed up this long
OPEN_TIMEOUT_S = 5.0
REPLY_TIMEOUT_S = 5.0
MAX_FRAMES_PER_S = 50
POLL_S = 15.0           # ask for the input list again this often
NO_INPUTS_POLL_S = 5.0  # ... or this often while Smaart has none
AUTH_RETRY_S = 60.0     # check again this often when Smaart wants a password and we have none
QUEUE_MAX = 16

# Fixed, plain wording for crew (never raw error text, which can carry addresses).
TEXT = {
    "no_address": "No address set",
    "unreachable": "Can't reach Smaart",
    "timeout": "Smaart did not answer",
    "closed": "Smaart closed the connection",
    "too_large": "Smaart sent more data than we accept",
    "not_smaart": "That address did not answer like Smaart's API. This build only knows the v4 API (/api/v4/): "
                  "check the API is switched on in Smaart and the port is right",
    "not_local": "That name does not point to a computer on the local network",
    "auth_needed": "Smaart's API has a password. Enter it in the Sound level settings",
    "wrong_password": "Smaart did not accept the password. Check it in the Sound level settings; "
                      "Stagewatch will not try again until you save it",
    "no_inputs": "Connected, but Smaart has no active inputs. Start logging in Smaart",
    "connected": "Connected, waiting for values (not yet tested against a live Smaart)",
}


class _NoRedirectConnect(connect):
    """websockets follows up to ten redirects by default. We follow none."""

    def process_redirect(self, exc):
        return exc


class NotLocal(Exception):
    """A host name resolved to an address that is not on the local network."""


class NotSmaart(Exception):
    """The address answered, but not like Smaart's v4 API."""


class AuthNeeded(Exception):
    """Smaart wants a password and none is set."""


class WrongPassword(Exception):
    """Smaart did not accept the password."""


class Closed(Exception):
    """The connection ended normally."""


def describe(exc: BaseException) -> str:
    """A fixed category for a failure (the key into TEXT)."""
    if isinstance(exc, ConnectionClosed):
        for frame in (exc.rcvd, exc.sent):
            if frame is not None and frame.code == 1009:
                return "too_large"
        return "closed"
    if isinstance(exc, NotLocal):
        return "not_local"
    if isinstance(exc, AuthNeeded):
        return "auth_needed"
    if isinstance(exc, WrongPassword):
        return "wrong_password"
    if isinstance(exc, (NotSmaart, InvalidHandshake, InvalidURI)):
        return "not_smaart"
    if isinstance(exc, Closed):
        return "closed"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    return "unreachable"


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


def ws_url(host: str, port: int, path: str = "/") -> str:
    """``ws://host:port<path>`` (an IPv6 address gets its brackets). Plain ws: Smaart on a LAN is not
    known to offer TLS."""
    h = f"[{host}]" if ":" in host else host
    return f"ws://{h}:{port}{path}"


class SmaartSource(SplSource):
    verified = VERIFIED
    label = "Smaart"

    def __init__(self, target_fn: Callable[[], tuple[str, int] | None], on_reading: ReadingCallback,
                 on_link: LinkCallback, on_catalog: CatalogCallback | None = None, *,
                 password_fn: Callable[[], str] = lambda: "", clock: Callable[[], float] = time.time,
                 backoff_min_s: float = BACKOFF_MIN_S, backoff_max_s: float = BACKOFF_MAX_S,
                 open_timeout_s: float = OPEN_TIMEOUT_S, reply_timeout_s: float = REPLY_TIMEOUT_S,
                 max_frames_per_s: int = MAX_FRAMES_PER_S, stable_s: float = STABLE_S,
                 poll_s: float = POLL_S, no_inputs_poll_s: float = NO_INPUTS_POLL_S,
                 auth_retry_s: float = AUTH_RETRY_S, sleep=asyncio.sleep) -> None:
        super().__init__(on_reading, on_link, on_catalog)
        self._target_fn = target_fn
        self._password_fn = password_fn      # a function, so the password is never held in a log-able attribute
        self._time = clock
        self._bmin, self._bmax = backoff_min_s, backoff_max_s
        self._open_timeout, self._reply_timeout = open_timeout_s, reply_timeout_s
        self._max_fps = max_frames_per_s
        self._stable_s = stable_s
        self._poll_s, self._no_inputs_poll_s, self._auth_retry_s = poll_s, no_inputs_poll_s, auth_retry_s
        self._sleep = sleep
        self._up_since: float | None = None
        self._task: asyncio.Task | None = None
        self._wanted: list[str] = []
        self._wake: asyncio.Event | None = None
        self.dropped_frames = 0   # over the rate cap, for the diagnostics

    # ------------------------------------------------------------ source API
    def set_wanted(self, sources: list[str]) -> None:
        self._wanted = list(sources)
        if self._wake is not None:
            self._wake.set()

    async def start(self) -> None:
        if self._task is None:
            self.version = ""   # Smaart's reply does not say; the API path is the only version we know
            self.input_name = ""
            self._wake = asyncio.Event()
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

    def _publish_catalog(self, inputs: list[str], metrics: list[str]) -> None:
        try:
            self._catalog(inputs, metrics)
        except Exception:  # noqa: BLE001
            log.exception("Smaart input list update failed")

    @staticmethod
    async def _send(ws, text: str) -> None:
        """The only place anything is sent to Smaart. Refuses a message not on the fixed list."""
        if not outbound.allowed(text):
            raise RuntimeError("blocked: not one of the four allowed messages")
        await ws.send(text)

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
                log.info("Smaart connection ended (%s)", category)
            self.problem = category if category in ("auth_needed", "wrong_password") else (
                "api" if category == "not_smaart" else "")
            self._link(False, TEXT.get(category, TEXT["unreachable"]))
            if category == "wrong_password":
                await asyncio.Event().wait()   # no retry at all: a save with a new password restarts us
            if category == "auth_needed":
                await self._sleep(self._auth_retry_s)
                continue
            # Readings before an abnormal drop do not earn a short wait: only a link that stayed up
            # for a while does, so a source that connects and drops at once backs off.
            stable = self._up_since is not None and time.monotonic() - self._up_since >= self._stable_s
            backoff = self._bmin if stable else min(backoff * 2, self._bmax)
            await self._sleep(backoff)

    def _open(self, url: str):
        return _NoRedirectConnect(url, max_size=MAX_BYTES, proxy=None, open_timeout=self._open_timeout,
                                  ping_interval=20, ping_timeout=20, compression=None, max_queue=4,
                                  logger=_WS_LOG)

    async def _session(self, ip: str, port: int) -> None:
        async with self._open(ws_url(ip, port, mapping.API_PATH)) as ws:
            failure: list[BaseException] = []
            failed = asyncio.Event()
            queue: asyncio.Queue = asyncio.Queue(QUEUE_MAX)
            pump = asyncio.create_task(self._pump(ws, queue, failure, failed))
            streams: dict[str, tuple[str, asyncio.Task]] = {}
            try:
                self._up_since = time.monotonic()
                await self._login(ws, queue, failure)
                catalog = await self._ask_inputs(ws, queue, failure, first=True)
                self._apply_catalog(catalog)
                next_poll = time.monotonic() + self._poll_for(catalog)
                while True:
                    assert self._wake is not None
                    self._wake.clear()   # before the check, so a change made meanwhile is not lost
                    self._reconcile(streams, catalog, ip, port, failure, failed)
                    if failed.is_set():
                        raise failure[0]
                    if time.monotonic() >= next_poll:
                        try:
                            fresh = await self._ask_inputs(ws, queue, failure, first=False)
                        except (TimeoutError, asyncio.TimeoutError):
                            fresh = None   # a slow answer is not a dead link: the keep-alive decides that
                        if fresh is not None:
                            if fresh != catalog:
                                catalog = fresh
                                self._apply_catalog(catalog)
                            next_poll = time.monotonic() + self._poll_for(catalog)
                        else:
                            next_poll = time.monotonic() + self._poll_for(catalog)
                        continue
                    await self._idle(failed, max(0.0, next_poll - time.monotonic()))
            finally:
                for _endpoint, task in streams.values():
                    task.cancel()
                pump.cancel()
                await asyncio.gather(pump, *[t for _e, t in streams.values()], return_exceptions=True)

    def _poll_for(self, catalog: mapping.Catalog) -> float:
        return self._poll_s if catalog.inputs else self._no_inputs_poll_s

    async def _idle(self, failed: asyncio.Event, timeout: float) -> None:
        """Wait for a change of wanted inputs, a failure, or the next poll."""
        assert self._wake is not None
        waiters =[asyncio.create_task(self._wake.wait()), asyncio.create_task(failed.wait())]
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _pump(self, ws, queue: asyncio.Queue, failure: list, failed: asyncio.Event) -> None:
        """Read the probe socket for the whole session, so a closed connection is noticed at once."""
        window_start, in_window = time.monotonic(), 0
        try:
            async for raw in ws:
                now = time.monotonic()
                if now - window_start >= 1.0:
                    window_start, in_window = now, 0
                in_window += 1
                if in_window > self._max_fps:
                    self.dropped_frames += 1
                    continue
                try:
                    queue.put_nowait(raw)
                except asyncio.QueueFull:
                    self.dropped_frames += 1
            failure.append(Closed())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            failure.append(exc)
        failed.set()
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def _expect(self, queue: asyncio.Queue, failure: list, wanted, strict: bool):
        """The next decoded message that ``wanted`` accepts. When ``strict`` (the set-up questions) the
        very next message must be it, else this is not Smaart's v4 API; otherwise other messages are
        skipped (at most 20). Raises TimeoutError when Smaart does not answer, or the failure that
        ended the connection."""
        async def go():
            for _ in range(20):
                raw = await queue.get()
                if raw is None:
                    raise failure[0] if failure else Closed()
                doc = mapping.decode(raw)
                if doc is not None and wanted(doc):
                    return doc
                if strict:
                    raise NotSmaart()
            raise TimeoutError()
        return await asyncio.wait_for(go(), self._reply_timeout)

    async def _login(self, ws, queue, failure) -> None:
        await self._send(ws, outbound.probe())
        doc = await self._expect(queue, failure, mapping.is_probe_reply, True)
        if not mapping.parse_probe_reply(doc):
            return
        password = self._password_fn()
        if not password:
            raise AuthNeeded()
        await self._send(ws, outbound.login(password))
        doc = await self._expect(queue, failure, lambda d: mapping.parse_auth_reply(d) is not None, True)
        if not mapping.parse_auth_reply(doc):
            raise WrongPassword()

    async def _ask_inputs(self, ws, queue, failure, first: bool) -> mapping.Catalog:
        await self._send(ws, outbound.inputs())
        doc = await self._expect(queue, failure, lambda d: mapping.parse_inputs_reply(d) is not None, first)
        return mapping.parse_inputs_reply(doc)

    def _apply_catalog(self, catalog: mapping.Catalog) -> None:
        self._publish_catalog([i.label for i in catalog.inputs], list(catalog.metrics))
        if catalog.inputs:
            self.problem = ""
            self._link(True, TEXT["connected"])
        else:
            self.problem = "no_inputs"
            self._link(True, TEXT["no_inputs"])

    # ----------------------------------------------------------- meter streams
    def _reconcile(self, streams: dict, catalog: mapping.Catalog, ip: str, port: int,
                   failure: list, failed: asyncio.Event) -> None:
        """One stream per input that a chosen value uses (a shared input shares its stream)."""
        by_label = {i.label: i.endpoint for i in catalog.inputs}
        first = catalog.inputs[0].label if catalog.inputs else None
        want = {}
        for s in self._wanted:
            label = first if s == "" else s
            if label in by_label:
                want[label] = by_label[label]
        for label in [k for k, (ep, _t) in streams.items() if want.get(k) != ep]:
            streams.pop(label)[1].cancel()
        for label, endpoint in want.items():
            if label not in streams:
                task = asyncio.create_task(self._stream(label, ws_url(ip, port, endpoint), failure, failed),
                                           name="smaart-stream")
                streams[label] = (endpoint, task)

    async def _stream(self, label: str, url: str, failure: list, failed: asyncio.Event) -> None:
        window_start, in_window = time.monotonic(), 0
        try:
            async with self._open(url) as ws:
                await self._send(ws, outbound.fps())
                async for raw in ws:
                    now = time.monotonic()
                    if now - window_start >= 1.0:
                        window_start, in_window = now, 0
                    in_window += 1
                    if in_window > self._max_fps:
                        self.dropped_frames += 1
                        continue
                    values = mapping.parse_stream_message(raw)
                    if values is not None:
                        self._deliver(SplReading(self._time(), values, source=label))
            failure.append(Closed())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            failure.append(exc)
        failed.set()
