"""Read-only RME client: one WebSocket per unit (``ws://host:port/ws_v2``), listen only.

Written from two captures of an RME 12Mic web remote (firmware 2.0.1): the unit pushes its whole state
as soon as the socket opens, then small JSON changes, and about 66 binary level-meter frames a second.
Tried on one 12Mic only; other RME units are untested.

What it never does: SEND. There is no send call in this module. No hello, no subscribe, no ping frame,
no request of any kind; the library answers the unit's own pings and closes the socket normally, which
is all that ever leaves. Nothing the unit says can make it send, so it cannot change gain, 48 V, a
preset, routing or the clock. (RME's web remote changes things with ``POST /api/v2/self``; Stagewatch
never calls it, and never calls any other HTTP address either.) A test pins this.

Hostile or damaged input is dropped: binary frames are counted and dropped before any decoding, text
frames are size, depth, node and string checked, more than 20 bad frames in a minute or a text flood
drops the link, a silent socket is dropped, redirects are not followed, the address must be on a local
network, and the library's frame logging (which would write the serial number) is silenced.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import time
from collections import deque
from typing import Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus, InvalidURI

from ..smaart.client import is_local_address
from . import parse
from .parse import BadFrame, RmeState
from .source import LinkCallback, RmeSource, StateCallback

log = logging.getLogger(__name__)

# The websockets library logs every frame at DEBUG level, and frames contain the serial number.
_WS_LOG = logging.getLogger(__name__ + ".ws")
_WS_LOG.propagate = False
_WS_LOG.addHandler(logging.NullHandler())

WS_PATH = "/ws_v2"
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
STABLE_S = 30.0
JITTER_S = 0.25
OPEN_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 1.0         # a unit that floods us cannot keep us waiting to close the socket
FIRST_FRAME_S = 8.0           # nothing at all this long after opening: connected but not sending
SILENCE_S = 10.0              # no frame of any kind this long (meters normally arrive ~66 a second)
MAX_BYTES = 512 * 1024        # a whole status is about 17 KB
MAX_QUEUE = 16
MAX_TEXT_PER_S = 100
MAX_TEXT_BYTES_PER_S = 256 * 1024     # a whole status is about 17 KB; deltas are tiny
MAX_BINARY_PER_S = 400                # the meters send about 66 a second
MIN_STATUS_GAP_S = 3.0                # a full status again sooner than this is a flood
FLOOD_SECONDS = 5
BAD_PER_MINUTE = 20
TICK_S = 0.5
PROBE_MAX_FRAMES = 40

TEXT = {
    "not_local": "That address is not on a private network",
    "unreachable": "Nothing answered at that address",
    "not_rme": "Something answered, but not like an RME unit",
    "silent": f"Connected, but the unit sent nothing in {FIRST_FRAME_S:g} seconds",
    "quiet": "The unit stopped sending",
    "other_unit": "A different RME unit answered here",
    "redirect": "The unit tried to redirect Stagewatch, so it stopped",
    "too_large": "The unit sent more data than Stagewatch accepts",
    "closed": "The unit closed the connection",
    "bad_frames": "The unit sent data Stagewatch could not read",
    "flood": "The unit sent far more data than expected, so Stagewatch stopped",
    "lost_update": "An update from the unit was lost, so Stagewatch reconnected to read it afresh",
    "waiting": "Connected, waiting for the unit's status",
}

# Admin page only (never sent to dashboards). {where} is the address as saved.
HINTS = {
    "unreachable": "Nothing answered at {where}. Check the unit is switched on, that the address is right and that this computer can reach the AVB or control network the unit is on.",
    "not_rme": "Something answered at {where}, but not like an RME unit. Check the address.",
    "silent": "Connected to {where}, but the unit sent nothing for {secs} seconds. Check the unit is working and try again.",
    "quiet": "The unit at {where} stopped sending. Stagewatch will keep trying; check the unit and the network cable.",
    "other_unit": "A different RME unit answered at {where}. If you swapped the unit on purpose, open Advanced and choose Replace unit.",
    "closed": "The unit at {where} closed the connection. Stagewatch will keep trying.",
}


class RmeError(Exception):
    """A problem with a unit, as a short fixed category (a key of TEXT). Never carries text from the unit."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class _Quiet(Exception):
    """Nothing arrived for the allowed time. ``first`` is True before any frame."""

    def __init__(self, first: bool) -> None:
        super().__init__("quiet")
        self.first = first


class _NoRedirectConnect(connect):
    """websockets follows up to ten redirects by default. We follow none."""

    def process_redirect(self, exc):
        return exc


def validate_host(host: object, address_ok: Callable = is_local_address) -> str:
    """A private-network IP address as text, or RmeError("not_local"). Host names are refused (no lookup is
    ever made), and so is anything that is not plain text."""
    try:
        if not isinstance(host, str):
            raise ValueError
        ip = ipaddress.ip_address(host.strip())
    except ValueError:
        raise RmeError("not_local") from None
    if _never_a_unit(ip) or not address_ok(ip):
        raise RmeError("not_local")
    return str(ip)


_REFUSED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",       # carrier-grade NAT: not a rack network
    "64:ff9b:1::/48",      # NAT64 local use: carries another address inside
    "0.0.0.0/8",           # "this network"
    "255.255.255.255/32",  # broadcast
))


def _never_a_unit(ip) -> bool:
    """Addresses refused whatever the caller allows: a scoped IPv6 address (it names a local interface),
    carrier-grade NAT, NAT64 local-use, all of 0.x and the broadcast address. Private RFC 1918 and fc00::/7
    addresses are fine."""
    if isinstance(ip, ipaddress.IPv6Address) and getattr(ip, "scope_id", None):
        return True
    return any(ip.version == n.version and ip in n for n in _REFUSED_NETS)


def ws_url(host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"ws://{h}:{port}{WS_PATH}"


def describe(exc: BaseException) -> str:
    """The TEXT category for what went wrong."""
    if isinstance(exc, RmeError):
        return exc.category
    if isinstance(exc, ConnectionClosed):
        for frame in (exc.rcvd, exc.sent):
            if frame is not None and frame.code == 1009:
                return "too_large"
        return "closed"
    if isinstance(exc, InvalidStatus):
        code = getattr(getattr(exc, "response", None), "status_code", 0)
        return "redirect" if 300 <= code < 400 else "not_rme"
    if isinstance(exc, (InvalidHandshake, InvalidURI)):
        return "not_rme"
    if isinstance(exc, _Quiet):
        return "silent" if exc.first else "quiet"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionRefusedError)):
        return "unreachable"
    return "unreachable"


class _Session:
    def __init__(self, now: float) -> None:
        self.opened = now
        self.last_rx = now
        self.frames = 0
        self.up = False
        self.failure: BaseException | None = None
        self.failed = asyncio.Event()


def _jitter() -> float:
    return random.random() * JITTER_S


class RmeClient(RmeSource):
    verified = False
    label = "RME unit"

    def __init__(self, target_fn: Callable[[], tuple[str, int] | None], on_state: StateCallback,
                 on_link: LinkCallback, *, expected_serial_fn: Callable[[], str] = lambda: "",
                 address_ok: Callable = is_local_address, clock: Callable[[], float] = time.monotonic,
                 sleep=asyncio.sleep, jitter: Callable[[], float] = _jitter,
                 backoff_min_s: float = BACKOFF_MIN_S, backoff_max_s: float = BACKOFF_MAX_S,
                 stable_s: float = STABLE_S, open_timeout_s: float = OPEN_TIMEOUT_S,
                 first_frame_s: float = FIRST_FRAME_S, silence_s: float = SILENCE_S, tick_s: float = TICK_S,
                 max_text_per_s: int = MAX_TEXT_PER_S, flood_s: int = FLOOD_SECONDS,
                 bad_per_minute: int = BAD_PER_MINUTE, max_text_bytes_per_s: int = MAX_TEXT_BYTES_PER_S,
                 max_binary_per_s: int = MAX_BINARY_PER_S, min_status_gap_s: float = MIN_STATUS_GAP_S) -> None:
        super().__init__(on_state, on_link)
        self._target_fn = target_fn
        self._expected_serial = expected_serial_fn
        self._address_ok = address_ok
        self._clock, self._sleep, self._jitter = clock, sleep, jitter
        self._bmin, self._bmax, self._stable_s = backoff_min_s, backoff_max_s, stable_s
        self._open_timeout, self._first_frame, self._silence, self._tick = open_timeout_s, first_frame_s, silence_s, tick_s
        self._max_text, self._flood_s, self._bad_per_min = max_text_per_s, flood_s, bad_per_minute
        self._max_text_bytes, self._max_binary, self._min_status_gap = max_text_bytes_per_s, max_binary_per_s, min_status_gap_s
        self._task: asyncio.Task | None = None
        self._up_since: float | None = None
        self._down_kind = ""
        self.sleeps: list[float] = []      # for tests and diagnostics: the waits between attempts

    # ------------------------------------------------------------ source API
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="rme-client")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def hint(self) -> str:
        kind = self._down_kind
        if not kind:
            return ""
        target = self._target_fn()
        if target is None:
            return ""
        host, port = target
        where = f"{f'[{host}]' if ':' in host else host}:{port}"
        template = HINTS.get(kind)
        return template.format(where=where, secs=f"{self._first_frame:g}") if template else ""

    # ------------------------------------------------------------- internals
    def _link(self, up: bool, detail: str, waiting: bool = False) -> None:
        try:
            self._on_link(up, detail, waiting)
        except Exception:  # noqa: BLE001
            log.exception("RME link update failed")

    async def _run(self) -> None:
        delay = self._bmin
        while True:
            category = "unreachable"
            self._up_since = None
            try:
                target = self._target_fn()
                if target is None:
                    raise RmeError("unreachable")
                ip = validate_host(target[0], self._address_ok)
                await self._session(ip, target[1])
                category = "closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means "wait, then try again"
                category = describe(exc)
                log.info("RME connection ended (%s)", category)
            self.problem = "api" if category == "not_rme" else ""
            self._down_kind = category
            self.last_frame_at = self.last_text_at = None
            self._link(False, TEXT.get(category, TEXT["unreachable"]))
            stable = self._up_since is not None and self._clock() - self._up_since >= self._stable_s
            if stable:
                delay = self._bmin
            wait = delay + self._jitter()
            self.sleeps.append(wait)
            delay = min(delay * 2, self._bmax)
            await self._sleep(wait)

    def _open(self, url: str):
        return _NoRedirectConnect(url, max_size=MAX_BYTES, proxy=None, open_timeout=self._open_timeout,
                                  ping_interval=None, compression=None, max_queue=MAX_QUEUE, close_timeout=CLOSE_TIMEOUT_S,
                                  logger=_WS_LOG)

    async def _session(self, ip: str, port: int) -> None:
        async with self._open(ws_url(ip, port)) as ws:
            ctx = _Session(self._clock())
            self.last_frame_at = self.last_text_at = None
            self._link(False, TEXT["waiting"], waiting=True)
            reader = asyncio.create_task(self._read(ws, ctx))
            try:
                while True:
                    if ctx.failed.is_set():
                        raise ctx.failure or RmeError("closed")
                    now = self._clock()
                    if ctx.frames == 0 and now - ctx.opened > self._first_frame:
                        raise _Quiet(True)
                    if ctx.frames and now - ctx.last_rx > self._silence:
                        raise _Quiet(False)
                    try:
                        await asyncio.wait_for(ctx.failed.wait(), self._tick)
                    except (TimeoutError, asyncio.TimeoutError):
                        pass
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)

    async def _read(self, ws, ctx: _Session) -> None:
        state = RmeState()
        bad: deque[float] = deque()
        window_start, text_n, text_bytes, binary_n, binary_streak = self._clock(), 0, 0, 0, 0
        last_status: float | None = None
        try:
            async for raw in ws:
                now = self._clock()
                ctx.frames += 1
                ctx.last_rx = now
                self.last_frame_at = now
                if now - window_start >= 1.0:
                    binary_streak = binary_streak + 1 if binary_n > self._max_binary else 0
                    window_start, text_n, text_bytes, binary_n = now, 0, 0, 0
                    if binary_streak >= self._flood_s:
                        raise RmeError("flood")
                if not isinstance(raw, str):
                    self.binary_frames += 1        # the meters: counted and dropped before any decoding
                    binary_n += 1
                    if binary_n > self._max_binary:
                        self.binary_dropped += 1   # over the limit: dropped silently, and counted
                    continue
                self.last_text_at = now
                text_n += 1
                text_bytes += len(raw)
                if text_n > self._max_text or text_bytes > self._max_text_bytes:
                    # A text frame we will not read may have been a change. Reconnect to read the unit afresh.
                    self.dropped_frames += 1
                    raise RmeError("flood")
                try:
                    result = state.ingest(raw)
                except Exception:  # noqa: BLE001 - nothing a frame says may end the hub
                    self._count_bad(bad, now)
                    if ctx.up:                       # a change was lost: do not carry on with old values
                        raise RmeError("lost_update") from None
                    continue
                if result.kind == "ignored":
                    self.dropped_frames += 1
                    continue
                if result.kind in ("baseline", "resync"):
                    if last_status is not None and now - last_status < self._min_status_gap:
                        raise RmeError("flood")
                    last_status = now
                if result.kind == "baseline":
                    if not isinstance(state.data.get("device"), dict):
                        raise RmeError("not_rme")
                    view = parse.make_view(state.data)
                    expected = self._expected_serial()
                    if expected and view is not None and view.serial and view.serial != expected:
                        raise RmeError("other_unit")
                    ctx.up = True
                    self._up_since = now
                    self._down_kind = ""
                    self._link(True, "")
                try:
                    self._on_state(state, result)
                except Exception:  # noqa: BLE001
                    log.exception("RME state update failed")
            ctx.failure = RmeError("closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            ctx.failure = exc
        ctx.failed.set()

    def _count_bad(self, bad: deque, now: float) -> None:
        self.bad_frames += 1
        self.dropped_frames += 1
        bad.append(now)
        while bad and now - bad[0] > 60.0:
            bad.popleft()
        if len(bad) > self._bad_per_min:
            raise RmeError("bad_frames")


async def probe(host: object, port: int = 80, *, address_ok: Callable = is_local_address,
                open_timeout_s: float = OPEN_TIMEOUT_S, wait_s: float = FIRST_FRAME_S) -> dict:
    """Admin "Test": open a short-lived socket, read until the first full status, close. Sends no commands or settings.
    Returns ``{"model", "firmware", "name", "serial"}`` (the serial is for the server only: never put it in
    a response). Raises RmeError with a TEXT category. If the unit has a connection limit it may refuse this
    second connection; that shows as "nothing answered" or "closed"."""
    ip = validate_host(host, address_ok)
    try:
        ws = await _NoRedirectConnect(ws_url(ip, port), max_size=MAX_BYTES, proxy=None,
                                      open_timeout=open_timeout_s, ping_interval=None, compression=None,
                                      max_queue=MAX_QUEUE, close_timeout=CLOSE_TIMEOUT_S, logger=_WS_LOG)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RmeError(describe(exc)) from None
    frames = 0
    try:
        state = RmeState()
        try:
            async with asyncio.timeout(wait_s):
                while frames < PROBE_MAX_FRAMES:
                    raw = await ws.recv()
                    frames += 1
                    if not isinstance(raw, str):
                        continue
                    try:
                        result = state.ingest(raw)
                    except BadFrame:
                        raise RmeError("not_rme") from None
                    if result.kind == "baseline":
                        break
                else:
                    raise RmeError("not_rme")
        except (TimeoutError, asyncio.TimeoutError):
            raise RmeError("silent" if frames == 0 else "not_rme") from None
        except ConnectionClosed as exc:
            raise RmeError(describe(exc)) from None
        view = parse.make_view(state.data)
        if view is None or not isinstance(state.data.get("device"), dict) or not (view.model or view.serial):
            raise RmeError("not_rme")
        return {"model": view.model, "firmware": view.firmware, "name": view.name, "serial": view.serial}
    finally:
        try:
            await asyncio.wait_for(ws.close(), 2.0)
        except Exception:  # noqa: BLE001
            pass
