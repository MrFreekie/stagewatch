"""Read-only Ontime client.

* Listens on ``ws://host:port/ws`` and never sends a data frame (the library's own ping/pong
  keep-alive is the only traffic we originate). Messages over 1 MiB close the connection.
* If the WebSocket fails, it asks ``GET /api/poll`` once a second (standard library HTTP in a
  thread: no redirects, 1 MiB cap, 3 s timeout) until the next WebSocket attempt.
* Asks only for ``/api/version``, ``/api/poll``, ``/ws`` and, while a dashboard has the Ontime
  Rundown card, one more plain GET: ``/data/rundowns/current`` (the event list). Never a control
  endpoint, never a write.
* Reconnects with a bounded backoff (1 s to 30 s). Nothing here can stop the hub.

What is read from what Ontime sends: the clock, the main timer with a few fields of the loaded
event (for the Ontime Timer card), the rundown counters, times and offset (for the Ontime Rundown
card), and the version string. One connection serves all three cards.
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

from ...core.ontimerundown import EventsReading, RundownReading, RundownState
from ...core.ontimetimer import TimerReading, TimerState
from ...core.wallclock import ClockReading, valid_clock_ms
from .parse import (EVENTS_PATH, HTTP_OK, HTTP_PATHS, MAX_BYTES, parse_events, parse_rundown, parse_timer, parse_version, parse_ws_runtime, poll_payload,
                    split_url, ws_url)

log = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 3.0
POLL_EVERY_S = 1.0
EVENTS_MIN_GAP_S = 5.0     # never fetch the event list more often than this
EVENTS_EVERY_S = 60.0      # and refresh it at least this often while wanted
EVENTS_TICK_S = 1.0        # how often the fetch loop looks at what is due
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
    "bad_rundown": "Ontime sent a rundown we can't read",
    "bad_list": "Ontime sent an event list we can't read",
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
                 no_data_s: float = NO_DATA_S, open_timeout_s: float = HTTP_TIMEOUT_S,
                 events_min_gap_s: float = EVENTS_MIN_GAP_S, events_every_s: float = EVENTS_EVERY_S,
                 events_tick_s: float = EVENTS_TICK_S) -> None:
        self._url_fn = url_fn
        self._on_change = on_change
        self._time = clock
        self._poll_every, self._bmin, self._bmax = poll_every_s, backoff_min_s, backoff_max_s
        self._no_data, self._open_timeout = no_data_s, open_timeout_s
        self._task: asyncio.Task | None = None
        self._reading = ClockReading(None, clock(), "offline", "Connecting")
        self._timer = TimerReading(None, clock(), "offline", "Connecting")
        self._merged: TimerState | None = None   # the last good timer state, for merging partial messages
        self._rundown = RundownReading(None, clock(), "offline", "Connecting")
        self._merged_rd: RundownState | None = None   # likewise for the rundown and offset blocks
        self.version = ""
        self.transport = ""
        self._got = False
        # The event list for the Ontime Rundown card. Fetched only while ``want_events`` is True
        # (the card is on a dashboard): when the connection comes up, when the number of events or
        # the running event changes, and every events_every_s, never more often than events_min_gap_s.
        self.want_events = False
        self._events = EventsReading()
        self._events_task: asyncio.Task | None = None
        self._events_gap, self._events_every, self._events_tick = events_min_gap_s, events_every_s, events_tick_s

    # ------------------------------------------------------------ source API
    async def start(self) -> None:
        if self._task is None:
            self._reading = ClockReading(None, self._time(), "offline", "Connecting")
            self._timer = TimerReading(None, self._time(), "offline", "Connecting")
            self._merged = None
            self._rundown = RundownReading(None, self._time(), "offline", "Connecting")
            self._merged_rd = None
            self.version = self.transport = ""
            self._events = EventsReading()
            self._task = asyncio.create_task(self._run(), name="ontime-client")
            self._events_task = asyncio.create_task(self._events_loop(), name="ontime-events")

    async def stop(self) -> None:
        task, self._task = self._task, None
        events_task, self._events_task = self._events_task, None
        for t in (task, events_task):
            if t:
                t.cancel()
        await asyncio.gather(*[t for t in (task, events_task) if t], return_exceptions=True)

    def latest(self) -> ClockReading:
        return self._reading

    def latest_timer(self) -> TimerReading:
        return self._timer

    def latest_rundown(self) -> RundownReading:
        return self._rundown

    def latest_events(self) -> EventsReading:
        return self._events

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
        self._merged_rd = None
        if self._events.rows is not None and not self._events.stale:
            self._events = EventsReading(self._events.rows, True)   # the list stays, marked stale
        self._timer = TimerReading(None, self._time(), "offline", TEXT.get(category, TEXT["unreachable"]))
        self._rundown = RundownReading(None, self._time(), "offline", TEXT.get(category, TEXT["unreachable"]))
        self._set(ClockReading(None, self._time(), "offline" if category != "bad_clock" else "error",
                               TEXT.get(category, TEXT["unreachable"])))

    def _ingest(self, payload: dict | None, transport: str) -> bool:
        """One runtime-data payload (WebSocket message or poll answer): update the timer and the
        clock separately, so a bad value in one never hides the other. True if either was valid."""
        if payload is None:
            self._lost("bad_clock")
            return False
        timer_ok = False
        if "timer" in payload or "eventNow" in payload:
            state = parse_timer(payload, self._merged)
            if state is None and "timer" not in payload:
                pass   # an event change before any timer: nothing to merge into yet
            elif state is None:
                self._timer = TimerReading(None, self._time(), "error", TEXT["bad_timer"])
            else:
                self._merged, timer_ok = state, True
                self._timer = TimerReading(state, self._time(), "ok", "WebSocket" if transport == "websocket" else "Polling")
        ms = valid_clock_ms(payload.get("clock"))
        self._ingest_rundown(payload, transport, alive=ms is not None or timer_ok, clock_ms=ms)
        if ms is not None:
            self._good(ms, transport)
        else:
            self.transport = transport if timer_ok else ""
            self._set(ClockReading(None, self._time(), "error", TEXT["bad_clock"]))
        return ms is not None or timer_ok

    def _ingest_rundown(self, payload: dict, transport: str, alive: bool, clock_ms: int | None) -> None:
        """Merge the rundown and offset blocks. A message without them (the usual one) just proves
        the connection is alive, so the reading's age restarts. A block that cannot be read keeps
        the last good figures (nothing is guessed) but marks the reading ``unreadable``, until the
        next readable block merges into them."""
        how = "WebSocket" if transport == "websocket" else "Polling"
        if "rundown" in payload or "offset" in payload or "eventNow" in payload:
            state = parse_rundown(payload, self._merged_rd)
            if state is None:
                if self._merged_rd is None:   # nothing readable yet: nothing to keep
                    self._rundown = RundownReading(None, self._time(), "error", TEXT["bad_rundown"])
                else:
                    self._rundown = RundownReading(self._merged_rd, self._time(), "ok", TEXT["bad_rundown"],
                                                   clock_ms, unreadable=True)
            else:
                self._merged_rd = state
                self._rundown = RundownReading(state, self._time(), "ok", how, clock_ms)
        elif alive and self._rundown.status != "error":
            # Alive, no new blocks: same state, newer time. (With nothing seen yet it stays "no data".)
            self._rundown = RundownReading(self._merged_rd, self._time(), "ok", how, clock_ms,
                                           unreadable=self._rundown.unreadable)

    def _events_key(self) -> tuple:
        s = self._merged_rd
        return (s.num_events, s.selected_index, s.event_id) if s is not None else (None, None, "")

    async def _events_loop(self) -> None:
        """Fetch the event list when it is wanted and due. Never raises, never faster than the gap;
        a failed or unreadable answer keeps the last good list and marks it stale."""
        last = None            # monotonic time of the last attempt
        last_key = None        # what the list was last fetched for
        was_up = False
        while True:
            await asyncio.sleep(self._events_tick)
            try:
                up = self._rundown.status == "ok" and self._merged_rd is not None
                if not self.want_events or not up:
                    was_up = False
                    last_key = None
                    continue
                now = time.monotonic()
                key = self._events_key()
                due = (not was_up) or key != last_key or last is None or now - last >= self._events_every
                was_up = True
                if not due or (last is not None and now - last < self._events_gap):
                    continue
                last, last_key = now, key
                await self._fetch_events()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the list is a nicety, the loop must survive
                log.exception("Ontime event list fetch failed")

    async def _fetch_events(self) -> None:
        try:
            body = await asyncio.to_thread(http_get_json, self._url_fn(), EVENTS_PATH)
            rows = parse_events(body)
            if rows is None:
                raise OntimeError("bad_list")
            self._events = EventsReading(rows, False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("Ontime event list not read (%s)", describe(exc))
            self._events = EventsReading(self._events.rows, True)

    async def _run(self) -> None:
        backoff = self._bmin
        while True:
            self._got = False
            self._merged = None   # a new connection starts with a full message
            self._merged_rd = None
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
