"""Emulated Smaart sound level: made-up numbers in the shape of the Smaart protocol, no hardware.

Two things live here, both built from the same message makers so they behave alike:

* ``EmulatedSplSource`` is what ``--emulate`` runs: no network at all. It behaves like a Smaart with
  two active inputs and the metrics "SPL A Slow", "SPL C Slow", "LAeq 1" and "LAeq 10" (and no
  "LAeq 15", so choosing it shows how a metric Smaart does not list looks). Its messages go through the
  real stream parser. A point on the first input is flagged ``overload`` now and then (shown as a dash,
  never zero), and about every five minutes the connection drops for ten seconds, so the gap, the
  missing-data look and the "resumed after a gap" marker can all be seen offline.
* ``FakeSmaartServer`` is a real WebSocket server speaking the same protocol (password on or off, two
  inputs, the four metrics, an overload point, a dropped stream, a no-inputs mode). The tests run the
  real client against it. It also records every message it receives, so a test can assert exactly what
  the client sent.

The numbers are synthetic: not worked out from each other, not real measurements, and they mean
nothing for a licence limit. The shapes follow the protocol notes read from Smaart's own web page and
are not yet tested against a live Smaart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from typing import Callable

from websockets.asyncio.server import serve

from ...core.spl import SplReading
from . import mapping, outbound
from .source import CatalogCallback, LinkCallback, ReadingCallback, SplSource

log = logging.getLogger(__name__)

VERSION = "emulated"
DEVICE_NAME = "ASIO MADIface USB"
CHANNELS = ("Channel 7 (1)", "Channel 8 (2)")
INPUT_LABELS = tuple(f"{DEVICE_NAME} : {c}" for c in CHANNELS)
INPUT_NAME = INPUT_LABELS[0]   # as Smaart shows a meter's input
METRIC_NAMES = ("SPL A Slow", "SPL C Slow", "LAeq 1", "LAeq 10")
RAW_METRICS = (*METRIC_NAMES, "FS Peak")   # what Smaart lists; the client leaves "FS Peak" out


def levels(elapsed: float, rng: random.Random, input_index: int = 0) -> dict[str, float]:
    """One simulated set of values for an input (the second one is a little quieter)."""
    song = 94.0 + 6.0 * math.sin(elapsed / 37.0) + 2.0 * math.sin(elapsed / 9.0) - 3.0 * input_index
    a_slow = song + rng.uniform(-0.4, 0.4)
    return {
        "SPL A Slow": a_slow,
        "SPL C Slow": a_slow + 5.0 + rng.uniform(-0.3, 0.3),
        "LAeq 1": 95.0 + 1.5 * math.sin(elapsed / 60.0) - 3.0 * input_index,     # just another slow wander
        "LAeq 10": 95.0 + 1.5 * math.sin(elapsed / 240.0) - 3.0 * input_index,
    }


def stream_message(values: dict[str, float], overload: frozenset[str] = frozenset()) -> str:
    """A meter-stream message as the protocol notes describe it: ``metrics`` is a list of objects with
    the metric name as the first key. A metric in ``overload`` carries the overload flag."""
    items = []
    for name, v in values.items():
        item: dict = {name: v}
        if name in overload:
            item["overload"] = True
        else:
            item["violation"] = False
        items.append(item)
    return json.dumps({"metrics": items})


class EmulatedSplSource(SplSource):
    verified = False
    label = "Simulated Smaart"

    def __init__(self, on_reading: ReadingCallback, on_link: LinkCallback, on_catalog: CatalogCallback | None = None,
                 *, period_s: float = 1.0, first_outage_s: float = 60.0, outage_every_s: float = 300.0,
                 outage_s: float = 10.0, overload_every_s: float = 90.0, overload_s: float = 3.0,
                 clock: Callable[[], float] = time.time, seed: int = 7) -> None:
        super().__init__(on_reading, on_link, on_catalog)
        self._period = period_s
        self._first, self._every, self._outage = first_outage_s, outage_every_s, outage_s
        self._ov_every, self._ov_len = overload_every_s, overload_s
        self._time = clock
        self._rng = random.Random(seed)
        self._task: asyncio.Task | None = None
        self._t0 = clock()
        self._up = False
        self._wanted: list[str] = []

    def set_wanted(self, sources: list[str]) -> None:
        self._wanted = list(sources)

    async def start(self) -> None:
        if self._task is None:
            self._t0 = self._time()
            self._up = False
            self.version = VERSION
            self._task = asyncio.create_task(self._run(), name="smaart-emulated")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def refresh(self) -> bool:
        """The simulated Smaart "answers" at once while it is up (its lists never change)."""
        if not self._up:
            return False
        self._call(self._catalog, list(INPUT_LABELS), [m for m in RAW_METRICS if m.lower() not in mapping.DROPPED_METRICS])
        return True

    def in_outage(self, elapsed: float) -> bool:
        """True while the simulated Smaart is unreachable (elapsed seconds since start)."""
        if elapsed < self._first:
            return False
        return (elapsed - self._first) % self._every < self._outage

    def in_overload(self, elapsed: float) -> bool:
        """True while the first input's "SPL A Slow" is flagged overload (a dash on the card)."""
        return elapsed >= 30.0 and (elapsed - 30.0) % self._ov_every < self._ov_len

    def messages(self, elapsed: float) -> dict[str, str]:
        """{input label: stream message} for the inputs the chosen values use (all, if none are asked
        for yet)."""
        first = INPUT_LABELS[0]
        wanted = {first if s == "" else s for s in self._wanted} or set(INPUT_LABELS)
        out = {}
        for n, label in enumerate(INPUT_LABELS):
            if label in wanted:
                ov = frozenset({"SPL A Slow"}) if n == 0 and self.in_overload(elapsed) else frozenset()
                out[label] = stream_message(levels(elapsed, self._rng, n), ov)
        return out

    @staticmethod
    def _call(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a callback must not end the simulated source
            log.exception("Simulated sound level callback failed")

    async def _run(self) -> None:
        while True:
            elapsed = self._time() - self._t0
            down = self.in_outage(elapsed)
            if down and self._up:
                self._up = False
                self._call(self._on_link, False, "Can't reach Smaart (simulated dropout)")
            elif not down and not self._up:
                self._up = True
                self._call(self._catalog, list(INPUT_LABELS),
                           [m for m in RAW_METRICS if m.lower() not in mapping.DROPPED_METRICS])
                self._call(self._on_link, True, "Connected (simulated)")
            if self._up:
                for label, raw in self.messages(elapsed).items():
                    values = mapping.parse_stream_message(raw)
                    if values is not None:
                        self._call(self._on_reading, SplReading(self._time(), values, VERSION, label))
            await asyncio.sleep(self._period)


# ---------------------------------------------------------------- a fake Smaart for the tests
class FakeSmaartServer:
    """A WebSocket server that speaks the protocol (as the notes describe it). Use as
    ``async with FakeSmaartServer(...) as fake:`` and connect to ``fake.port`` on 127.0.0.1.

    ``password``: None = no password needed, else the password it wants.
    ``inputs``: how many of the two inputs are active (0 = none: Smaart is not logging).
    ``drop_stream_after``: close the first stream connection after this many messages (a dropped link).
    ``overload``: metric names flagged overload in every message of the first input.
    ``stream_frames``: if given, these exact strings are sent on a stream instead of generated messages.
    ``probe_frames``: if given, the probe socket answers every message with these strings (a server that
    is not Smaart's API).
    ``probe_close_after_s``: close the probe socket this long after answering the input list (a Smaart
    that goes away). ``inputs`` may be changed while it runs (Smaart starting to log).
    Everything received is in ``received`` as (path, text)."""

    def __init__(self, *, password: str | None = None, inputs: int = 2, period_s: float = 0.02,
                 drop_stream_after: int | None = None, overload: frozenset[str] = frozenset(),
                 stream_frames: list[str] | None = None, probe_frames: list[str] | None = None,
                 metrics: tuple[str, ...] = RAW_METRICS, probe_close_after_s: float | None = None) -> None:
        self.probe_close_after_s = probe_close_after_s
        self.password = password
        self.inputs = inputs
        self.period_s = period_s
        self.drop_stream_after = drop_stream_after
        self.overload = overload
        self.stream_frames = stream_frames
        self.probe_frames = probe_frames
        self.metrics = metrics
        self.channel_names: tuple[str, ...] | None = None
        self.received: list[tuple[str, str]] = []
        self.paths: list[str] = []
        self._dropped = False
        self._server = None
        self.port = 0

    # ---- protocol
    def inputs_reply(self) -> str:
        names = self.channel_names or CHANNELS   # a test may rename an input while the server runs
        chans = [{"channelName": names[n], "streamEndpoint": f"/stream/{n}",
                  "logEndpointPrefix": f"/log/{n}", "alarms": [{"level": "red", "metric": "SPL A Slow"}]}
                 for n in range(self.inputs)]
        devices = [{"deviceName": DEVICE_NAME, "activeCalibratedChannels": chans}] if chans else []
        return json.dumps({"response": {"devices": devices, "metrics": list(self.metrics),
                                        "colorThresholds": [{"greenAboveLevel": 0, "yellowAboveLevel": 90,
                                                             "redAboveLevel": 100} for _ in self.metrics]}})

    async def _probe(self, ws) -> None:
        authed = self.password is None
        async for text in ws:
            self.received.append(("/api/v4/", text))
            if self.probe_frames is not None:
                for f in self.probe_frames:
                    await ws.send(f)
                continue
            kind = outbound.kind_of(text)
            if kind == outbound.PROBE:
                await ws.send(json.dumps({"response": {"authenticationRequired": self.password is not None}}))
            elif kind == outbound.LOGIN:
                got = json.loads(text)["properties"][0]["password"]
                authed = got == self.password
                await ws.send(json.dumps({"response": {"status": authed}}))
            elif kind == outbound.INPUTS and authed:
                await ws.send(self.inputs_reply())
                if self.probe_close_after_s is not None:
                    await asyncio.sleep(self.probe_close_after_s)
                    await ws.close(1011)
                    return

    async def _stream(self, ws, n: int) -> None:
        rng = random.Random(n)
        t0 = time.monotonic()
        sent = 0
        reader = asyncio.create_task(self._read_stream(ws, f"/stream/{n}"))
        try:
            while True:
                if self.stream_frames is not None:
                    frames = self.stream_frames
                else:
                    ov = self.overload if n == 0 else frozenset()
                    frames = [stream_message(levels(time.monotonic() - t0, rng, n), ov)]
                for f in frames:
                    await ws.send(f)
                    sent += 1
                if self.drop_stream_after is not None and not self._dropped and sent >= self.drop_stream_after:
                    self._dropped = True
                    await ws.close(1011)
                    return
                await asyncio.sleep(self.period_s)
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _read_stream(self, ws, path: str) -> None:
        async for text in ws:
            self.received.append((path, text))

    async def _handler(self, ws) -> None:
        path = ws.request.path
        self.paths.append(path)
        try:
            if path == mapping.API_PATH:
                await self._probe(ws)
            elif path.startswith("/stream/") and path[8:].isdigit() and int(path[8:]) < self.inputs:
                await self._stream(ws, int(path[8:]))
            else:
                await ws.close(1008)
        except Exception:  # noqa: BLE001 - a closed test connection
            pass

    async def __aenter__(self) -> "FakeSmaartServer":
        self._server = await serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()
