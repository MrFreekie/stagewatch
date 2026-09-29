"""Emulated ESPHome nodes for building and demoing a show with no hardware.

Values follow a slow evening temperature drift with a little sensor noise.
One node periodically drops offline so status/stale handling can be seen.
"""

from __future__ import annotations

import asyncio
import math
import random
import time

from ...core.model import Device, Entity, Kind, Status

UPDATE_S = 5.0


class EmulatedNode:
    def __init__(self, hub, device_id: str, name: str, area: str, temp_offset: float,
                 has_pressure: bool = True, flaky: bool = False) -> None:
        self.hub = hub
        self.device_id = device_id
        self.name = name
        self.area = area
        self.temp_offset = temp_offset
        self.has_pressure = has_pressure
        self.flaky = flaky
        self._task: asyncio.Task | None = None
        self._t0 = time.time()

    @classmethod
    def defaults(cls, hub) -> list["EmulatedNode"]:
        return [
            cls(hub, "sim_stage_l", "Stage L (sim)", "Stage L", 0.8),
            cls(hub, "sim_foh", "FOH (sim)", "FOH", 0.0),
            cls(hub, "sim_delay_1", "Delay tower 1 (sim)", "Delay tower 1", -0.6,
                has_pressure=False, flaky=True),
        ]

    async def start(self) -> None:
        self.hub.register_device(Device(self.device_id, self.name, "esphome",
                                        "ESPHome (emulated)", "BME280", self.area, Status.OK))
        self.hub.register_entity(Entity(f"{self.device_id}.temperature", self.device_id,
                                        "Temperature", Kind.TEMPERATURE, "°C", 1))
        self.hub.register_entity(Entity(f"{self.device_id}.humidity", self.device_id,
                                        "Humidity", Kind.HUMIDITY, "%", 0))
        if self.has_pressure:
            self.hub.register_entity(Entity(f"{self.device_id}.pressure", self.device_id,
                                            "Pressure", Kind.PRESSURE, "Pa", 0))
        self.publish()
        self._task = asyncio.create_task(self._run(), name=f"emulate-{self.device_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _offline(self, now: float) -> bool:
        # Flaky node: offline for 90 s out of every 10 minutes.
        return self.flaky and (now - self._t0) % 600 > 510

    def publish(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        if self._offline(now):
            self.hub.set_device_status(self.device_id, Status.MISSING, "emulated dropout")
            return
        self.hub.set_device_status(self.device_id, Status.OK)
        hours = (now - self._t0) / 3600.0
        temp = 24.0 - 3.0 * math.sin(min(hours, 6.0) / 6.0 * math.pi / 2) + self.temp_offset
        temp += 0.4 * math.sin(now / 420.0) + random.gauss(0, 0.05)
        rh = 48.0 + 6.0 * math.sin(min(hours, 6.0) / 6.0 * math.pi / 2) + random.gauss(0, 0.4)
        self.hub.update_state(f"{self.device_id}.temperature", round(temp, 2), now)
        self.hub.update_state(f"{self.device_id}.humidity", round(rh, 1), now)
        if self.has_pressure:
            p = 101325.0 - 180.0 * hours / 6.0 + random.gauss(0, 8)
            self.hub.update_state(f"{self.device_id}.pressure", round(p, 0), now)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(UPDATE_S)
            self.publish()
