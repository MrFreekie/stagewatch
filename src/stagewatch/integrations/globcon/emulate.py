"""A simulated GLOBCON for ``--emulate`` and tests: no network, no hardware.

It looks like the real capture: 16 controllers, each with 16 strips; the first strips carry a level and
the last eight (USB) do not. Levels move in whole dB at 10 updates a second, with now and then a strip
at "no signal" (-250). Controller 2 sits on a second layer so a dashboard pointed at it shows a different
layer label. All names and labels are SYNTHETIC (nothing from a real show).

Fault path: every ``dropout_every_s`` the simulated link drops for ``dropout_s``, so the card can be seen
frozen with its age and the device goes "missing" (a silent notice), then comes back.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Callable

from . import protocol
from .protocol import Value
from .source import GlobconSource, LinkCallback, MetersCallback, ValuesCallback

METER_HZ = 10.0
DROPOUT_EVERY_S = 90.0
DROPOUT_S = 8.0

# (label, shows a level)
_LAYER_A = [("Input Manager #1", True), ("Input Manager #2", True)] + \
           [(f"Flex Channel {i}", True) for i in range(3, 9)] + [(f"USB {i}", False) for i in range(1, 9)]
_LAYER_B = [(f"Monitor {i}", True) for i in range(1, 9)] + [(f"USB {i}", False) for i in range(1, 9)]
_LAYER_LABELS = ["Inputs", "Monitors", "Effects", "", "", "", "", "", "", "", "", ""]


def _values_for(n: int, layer: int) -> list[Value]:
    strips = _LAYER_B if layer == 1 else _LAYER_A
    out = [Value(f"/controller/{n}/controls/layer", "i", layer)]
    out += [Value(f"/controller/{n}/controls/layerLabel/{k}", "s", t) for k, t in enumerate(_LAYER_LABELS)]
    for i, (label, has) in enumerate(strips):
        out.append(Value(f"/controller/{n}/faders/{i}/label", "s", label))
        out.append(Value(f"/controller/{n}/faders/{i}/hasLevel", "b", has))
    return out


def _general() -> list[Value]:
    out = [Value("/general/channelsNumber", "i", protocol.MAX_CONTROLLERS)]
    for n in range(protocol.MAX_CONTROLLERS):
        out.append(Value(f"/general/name/{n}", "s", f"Controller {n + 1}"))
        out.append(Value(f"/general/requiresPassword/{n}", "b", False))
    return out


def level_block(n: int, t: float, layer: int = 0) -> bytes:
    """16 float32 dB for controller ``n`` at time ``t``: whole dB, -250 for unused strips and, for a
    moment every so often, one strip with no signal."""
    strips = _LAYER_B if layer == 1 else _LAYER_A
    vals: list[float | None] = []
    for i in range(protocol.MAX_STRIPS):
        has = strips[i][1]
        if not has:
            vals.append(None)
            continue
        phase = (n * 1.3 + i * 0.9)
        db = -22.0 + 12.0 * math.sin(t * (0.5 + 0.11 * i) + phase) + 4.0 * math.sin(t * 3.1 + phase)
        if i == 3 and int(t / 7) % 3 == 0:      # strip 4 drops to "no signal" now and then
            vals.append(None)
        else:
            vals.append(float(round(max(-70.0, min(-1.0, db)))))
    return protocol.encode_meters(vals)


class EmulatedGlobconSource(GlobconSource):
    verified = False
    label = "Simulated GLOBCON"

    def __init__(self, on_values: ValuesCallback, on_meters: MetersCallback, on_link: LinkCallback, *,
                 clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep,
                 meter_hz: float = METER_HZ, dropout_every_s: float = DROPOUT_EVERY_S,
                 dropout_s: float = DROPOUT_S) -> None:
        super().__init__(on_values, on_meters, on_link)
        self._clock, self._sleep = clock, sleep
        self._period = 1.0 / meter_hz
        self._every, self._drop_s = dropout_every_s, dropout_s
        self._wanted: list[int] = []
        self._task: asyncio.Task | None = None
        self._announced: set[int] = set()
        self._force_down_until = 0.0

    def set_wanted(self, controllers: list[int]) -> None:
        self._wanted = sorted({c for c in controllers if 0 <= c < protocol.MAX_CONTROLLERS})

    def dropout(self, seconds: float | None = None) -> None:
        """Take the simulated link down now for a while (the admin page and tests use this)."""
        self._force_down_until = self._clock() + (self._drop_s if seconds is None else seconds)

    def layer_of(self, n: int, t: float) -> int:
        """Controller 2 (number 1) changes layer every 40 s so a layer label change can be seen."""
        return int(t / 40) % 2 if n == 1 else 0

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="globcon-emulated")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _is_down(self, now: float, elapsed: float) -> bool:
        if now < self._force_down_until:
            return True
        return self._every > 0 and (elapsed % self._every) >= self._every - self._drop_s

    async def _run(self) -> None:
        up = False
        layers: dict[int, int] = {}
        t0 = self._clock()
        while True:
            t = self._clock()
            down = self._is_down(t, t - t0)
            if down and up:
                up = False
                self._on_link(False, "Simulated dropout")
                self._announced.clear()
            if not down:
                if not up:
                    up = True
                    self._on_link(True, "Connected, waiting for levels")
                    self._on_values(_general())
                for n in self._wanted:
                    layer = self.layer_of(n, t - t0)
                    if n not in self._announced or layers.get(n) != layer:
                        layers[n] = layer
                        self._announced.add(n)
                        self._on_values(_values_for(n, layer))
                    self._on_meters(n, level_block(n, t - t0, layer))
            await self._sleep(self._period)
