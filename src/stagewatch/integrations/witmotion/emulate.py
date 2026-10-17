"""A simulated WT901BLE for --emulate: no Bluetooth, no hardware. It has the same callbacks as
WitmotionReader and sends the same parsed frames at 10 Hz: a slightly tilted sensor that sways a little,
a battery that slowly runs down, and a 20 second link loss every 5 minutes so the gap and the restart
marker can be seen."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Callable

from . import parser

CYCLE_S = 300.0
OUTAGE_AT_S = 240.0
OUTAGE_S = 20.0


class EmulatedReader:
    def __init__(self, *, on_frame: Callable = lambda f, t: None, on_connected: Callable = lambda t: None,
                 on_disconnected: Callable = lambda t: None, on_status: Callable = lambda s: None,
                 rate_hz: float = 10.0, **_ignored) -> None:
        self.on_frame, self.on_connected, self.on_disconnected, self.on_status = (
            on_frame, on_connected, on_disconnected, on_status)
        self.period = 1.0 / rate_hz
        self._t0 = time.time()

    def frame(self, t: float) -> parser.DataFrame:
        roll = 2.1 + 0.15 * math.sin(t / 40.0)
        pitch = -1.3 + 0.1 * math.sin(t / 55.0 + 1.0)
        ax = -math.sin(math.radians(pitch))
        ay = math.sin(math.radians(roll)) * math.cos(math.radians(pitch))
        az = math.cos(math.radians(roll)) * math.cos(math.radians(pitch))
        return parser.DataFrame(ax, ay, az, 0.0, 0.0, 0.0, roll, pitch, 100.0)

    def registers(self, t: float) -> list[parser.RegisterReply]:
        raw = round(412 - 30 * (t / 36000.0))   # about 4.12 V, down 0.3 V over ten hours
        return [parser.RegisterReply(parser.REG_BATTERY, (raw, 0, 0, 0, 0, 0, 0, 0)),
                parser.RegisterReply(parser.REG_RATE, (6, 0, 0, 0, 0, 0, 0, 0)),
                parser.RegisterReply(parser.REG_BANDWIDTH, (4, 0, 0, 0, 0, 1, 0, 0)),
                parser.RegisterReply(parser.REG_MAG_ANGLE_TEMP, (0, 0, 0, 0, 0, 0, 2940 + round(30 * math.sin(t / 600.0)), 0))]

    def is_out(self, t: float) -> bool:
        return OUTAGE_AT_S <= t % CYCLE_S < OUTAGE_AT_S + OUTAGE_S

    async def run(self) -> None:
        up = False
        last_regs = -1e9
        while True:
            now = time.time()
            t = now - self._t0
            if self.is_out(t):
                if up:
                    up = False
                    self.on_status("lost")
                    self.on_disconnected(now)
            else:
                if not up:
                    up = True
                    self.on_status("connected")
                    self.on_connected(now)
                self.on_frame(self.frame(t), now)
                if t - last_regs >= 30.0:
                    last_regs = t
                    for r in self.registers(t):
                        self.on_frame(r, now)
            await asyncio.sleep(self.period)
