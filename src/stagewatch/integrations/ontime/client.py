"""Read-only Ontime client.

* Listens on ``ws://host:port/ws`` and never sends a data frame (the library's own ping/pong
  keep-alive is the only traffic we originate). Messages over 1 MiB close the connection.
* If the WebSocket fails, it asks ``GET /api/poll`` once a second (standard library HTTP in a
  thread: no redirects, 1 MiB cap, 3 s timeout) until the next WebSocket attempt.
* Asks only for ``/api/version``, ``/api/poll`` and ``/ws``; never a control endpoint.
* Reconnects with a bounded backoff (1 s to 30 s). Nothing here can stop the hub.

What is read from what Ontime sends: the clock, the main timer with a few fields of the loaded
event (for the Ontime Timer card), and the version string. One connection serves both cards.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import socket
import threading
import time
from typing import Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from ...core.ontimetimer import TimerReading, TimerState
from ...core.wallclock import ClockReading, valid_clock_ms
from .parse import (HTTP_OK, HTTP_PATHS, MAX_BYTES, parse_timer, parse_version, parse_ws_runtime, poll_payload,
                    split_url, ws_url)

log = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 3.0
POLL_EVERY_S = 1.0
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
NO_DATA_S = 10.0   # a WebSocket that says nothing for this long is treated as lost

# Fixed, plain wording shown to crew (never raw error text, which can carry addresses).
TEXT = {
    "unreachable": "Can't reach Ontime",
    "timeout": "Ontime did not answer",
    "not_ontime": "That address did not answer like Ontime",
    "too_large": "Ontime sent more data than we accept",
    "bad_clock": "Ontime sent a time we can't read",
    "bad_timer": "Ontime sent a timer we can't read",
    "closed": "Ontime closed the connection",
}


class OntimeError(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def http_get_json(base_url: str, path: str, timeout: float = HTTP_TIMEOUT_S, max_bytes: int = MAX_BYTES):
    """Blocking GET of one of the two allowed read paths; returns the decoded JSON.

    Never follows a redirect (http.client does not), refuses a body over ``max_bytes`` and a
    status other than 200/202. Raises OntimeError(category). Run it with ``asyncio.to_thread``."""
    if path not in HTTP_PATHS:
        raise ValueError("path not allowed")
    host, port = split_url(base_url)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    deadline = time.monotonic() + timeout   # for the whole request, not per read: a slow drip must not hold us

    def cut() -> None:   # watchdog: also covers a host that drips its headers
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    watchdog = threading.Timer(timeout, cut)
    watchdog.daemon = True
    watchdog.start()
    try:
        conn.request("GET", path, headers={"Accept": "application/json", "Connection": "close"})
        resp = conn.getresponse()
        if resp.status not in HTTP_OK:
            raise OntimeError("not_ontime")   # includes redirects: they are refused, not followed
        chunks, size = [], 0
        while size <= max_bytes:
            if time.monotonic() >= deadline:
                raise OntimeError("timeout")
            chunk = resp.read(min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        data = b"".join(chunks)
    except OntimeError:
        raise
    except (TimeoutError, socket.timeout):
        raise OntimeError("timeout") from None
    except (OSError, http.client.HTTPException):
        raise OntimeError("unreachable") from None
    finally:
        watchdog.cancel()
        conn.close()
    if len(data) > max_bytes:
        raise OntimeError("too_large")
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        raise OntimeError("not_ontime") from None


def check_connection(base_url: str) -> dict:
    """One ``GET /api/version``: {"ok": True, "version": "4.14.0"} or {"ok": False, "category": ...}.
    Blocking; run it with ``asyncio.to_thread``."""
    try:
        version = parse_version(http_get_json(base_url, "/api/version"))
    except OntimeError as exc:
        return {"ok": False, "category": exc.category}
    if version is None:
        return {"ok": False, "category": "not_ontime"}
    return {"ok": True, "version": version}


class _NoRedirectConnect(connect):
    """websockets follows up to ten redirects by default. We follow none."""

    def process_redirect(self, exc):
        return exc


def describe(exc: BaseException) -> str:
    """A fixed category for a failure (the key into TEXT)."""
    if isinstance(exc, OntimeError):
        return exc.category
    if isinstance(exc, ConnectionClosed):
        for frame in (exc.rcvd, exc.sent):
            if frame is not None and frame.code == 1009:
                return "too_large"
        return "closed"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, (InvalidHandshake, InvalidURI)):
        return "not_ontime"
    return "unreachable"


class OntimeSource:
    """The clock source for the Wall Clock service (core/wallclock.py ClockSource)."""

    name = "ontime"
    label = "Ontime"

    def __init__(self, url_fn: Callable[[], str], on_change: Callable[[ClockReading], None] | None = None,
                 clock: Callable[[], float] = time.time, poll_every_s: float = POLL_EVERY_S,
                 backoff_min_s: float = BACKOFF_MIN_S, backoff_max_s: float = BACKOFF_MAX_S,
                 no_data_s: float = NO_DATA_S, open_timeout_s: float = HTTP_TIMEOUT_S) -> None:
        self._url_fn = url_fn
        self._on_change = on_change
        self._time = clock
        self._poll_every, self._bmin, self._bmax = poll_every_s, backoff_min_s, backoff_max_s
        self._no_data, self._open_timeout = no_data_s, open_timeout_s
        self._task: asyncio.Task | None = None
        self._reading = ClockReading(None, clock(), "offline", "Connecting")
        self._timer = TimerReading(None, clock(), "offline", "Connecting")
        self._merged: TimerState | None = None   # the last good timer state, for merging partial messages
        self.version = ""
        self.transport = ""
        self._got = False

    # ------------------------------------------------------------ source API
    async def start(self) -> None:
        if self._task is None:
            self._reading = ClockReading(None, self._time(), "offline", "Connecting")
            self._timer = TimerReading(None, self._time(), "offline", "Connecting")
            self._merged = None
            self.version = self.transport = ""
            self._task = asyncio.create_task(self._run(), name="ontime-client")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def latest(self) -> ClockReading:
        return self._reading

    def latest_timer(self) -> TimerReading:
        return self._timer

    def details(self) -> dict:
        return {"version": self.version, "transport": self.transport}

    # ------------------------------------------------------------- internals
    def _set(self, reading: ClockReading) -> None:
        self._reading = reading
        if self._on_change:
            try:
                self._on_change(reading)
            except Exception:  # noqa: BLE001 - a status callback must not end the client
                log.exception("Ontime status update failed")

    def _good(self, ms: int, transport: str) -> None:
        self.transport = transport
        self._set(ClockReading(ms, self._time(), "ok", "WebSocket" if transport == "websocket" else "Polling"))

    def _lost(self, category: str) -> None:
        """The connection or the data is gone: both the clock and the timer have nothing to show."""
        self.transport = ""
        self._merged = None
        self._timer = TimerReading(None, self._time(), "offline", TEXT.get(category, TEXT["unreachable"]))
        self._set(ClockReading(None, self._time(), "offline" if category != "bad_clock" else "error",
                               TEXT.get(category, TEXT["unreachable"])))

    def _ingest(self, payload: dict | None, transport: str) -> bool:
        """One runtime-data payload (WebSocket message or poll answer): update the timer and the
        clock separately, so a bad value in one never hides the other. True if either was valid."""
        if payload is None:
            self._lost("bad_clock")
            return False
        timer_ok = False
        if "timer" in payload:
            state = parse_timer(payload, self._merged)
            if state is None:
                self._timer = TimerReading(None, self._time(), "error", TEXT["bad_timer"])
            else:
                self._merged, timer_ok = state, True
                self._timer = TimerReading(state, self._time(), "ok", "WebSocket" if transport == "websocket" else "Polling")
        ms = valid_clock_ms(payload.get("clock"))
        if ms is not None:
            self._good(ms, transport)
        else:
            self.transport = transport if timer_ok else ""
            self._set(ClockReading(None, self._time(), "error", TEXT["bad_clock"]))
        return ms is not None or timer_ok

    async def _run(self) -> None:
        backoff = self._bmin
        while True:
            self._got = False
            self._merged = None   # a new connection starts with a full message
            try:
                await self._read_version()
                await self._ws_session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means "try the fallback"
                log.info("Ontime WebSocket unavailable (%s); polling instead", describe(exc))
            backoff = self._bmin if self._got else min(backoff * 2, self._bmax)
            await self._poll_for(backoff)

    async def _read_version(self) -> None:
        try:
            body = await asyncio.to_thread(http_get_json, self._url_fn(), "/api/version")
            self.version = parse_version(body) or self.version
        except Exception as exc:  # noqa: BLE001 - the version is a nicety
            log.debug("Ontime version not read (%s)", describe(exc))

    async def _ws_session(self) -> None:
        """Listen until the connection ends (recv raises); ``self._got`` records whether it
        delivered at least one valid clock. Never sends a data frame."""
        async with _NoRedirectConnect(ws_url(self._url_fn()), max_size=MAX_BYTES, proxy=None,
                                      open_timeout=self._open_timeout, ping_interval=20, ping_timeout=20,
                                      compression=None, max_queue=4) as ws:
            last_valid = time.monotonic()
            while True:
                # The no-data timer follows the last VALID clock, not any frame: a server that
                # keeps sending logs but no clock is treated as lost.
                left = self._no_data - (time.monotonic() - last_valid)
                if left <= 0:
                    raise TimeoutError("no valid clock")
                raw = await asyncio.wait_for(ws.recv(), left)
                kind, _ms, payload = parse_ws_runtime(raw)
                if kind == "ignored":
                    continue
                if self._ingest(payload, "websocket"):
                    last_valid = time.monotonic()
                    self._got = True

    async def _poll_for(self, seconds: float) -> None:
        """Poll ``/api/poll`` every second for about ``seconds`` (at least once), then return so
        the WebSocket is tried again."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                body = await asyncio.to_thread(http_get_json, self._url_fn(), "/api/poll")
                self._ingest(poll_payload(body), "polling")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._lost(describe(exc))
            left = deadline - time.monotonic()
            if left <= 0:
                return
            await asyncio.sleep(min(self._poll_every, left))
