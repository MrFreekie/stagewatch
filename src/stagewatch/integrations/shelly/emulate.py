"""A simulated Shelly PM Mini Gen3 for building and demoing a show with no hardware.

It stands in for the network request (``fetch(host, method)``), so everything above it runs for real:
the poller, the common reading layer, entities, status, gaps and the "meter back" marker.

The simulated meter measures a small rack of gear: a steady base load with slow swings and a short
spike now and then. Its energy counter adds up the simulated power, so the kWh figure is consistent.
Like a real PM Mini Gen3 it sends no apparent power and no power factor.

Fault path: for 40 seconds out of every 5 minutes it does not answer (as if its Wi-Fi dropped), so the
card can be seen frozen with its age, and the "meter back" marker appears when it returns.

Simulated MACs start 02:5e:00:00:03 (locally administered), so they can never be a real meter's.
"""

from __future__ import annotations

import math
import time
import zlib
from typing import Callable

from .http import GET_DEVICE_INFO, GET_STATUS, ForbiddenMethod, ShellyError, rpc_path

DEMO_HOST = "192.0.2.10"        # documentation address: never a real device
DEMO_NAME = "Mic rack (sim)"
CYCLE_S = 300.0
OUTAGE_START_S = 240.0
OUTAGE_S = 40.0


def emulated_mac(host: str) -> str:
    """12 lower-case hex digits: a fixed simulated MAC per address."""
    if host == DEMO_HOST:
        return "025e00000301"
    return f"025e000003{zlib.crc32(host.encode()) & 0xFF:02x}"


class EmulatedMeter:
    def __init__(self, host: str, clock: Callable[[], float] = time.time, outages: bool = True) -> None:
        self.host = host
        self.mac = emulated_mac(host)
        self._clock = clock
        self._t0 = clock()
        self._last = self._t0
        self._outages = outages
        self._energy_wh = 15662.781 + (zlib.crc32(host.encode()) % 5000)

    def offline(self, now: float) -> bool:
        if not self._outages:
            return False
        phase = (now - self._t0) % CYCLE_S
        return OUTAGE_START_S <= phase < OUTAGE_START_S + OUTAGE_S

    def power_w(self, now: float) -> float:
        t = now - self._t0
        base = 205.0 + 35.0 * math.sin(t / 90.0) + 8.0 * math.sin(t / 7.0)
        spike = 140.0 if int(t) % 45 in (0, 1) else 0.0     # a short surge every 45 seconds
        return round(base + spike, 1)

    def info(self) -> dict:
        return {"name": None, "id": f"shellypmminig3-{self.mac.upper()}", "mac": self.mac.upper(),
                "slot": 0, "model": "S3PM-001PCEU16", "gen": 3, "fw_id": "20250101-000000/sim",
                "ver": "sim", "app": "MiniPMG3", "auth_en": False, "auth_domain": None}

    def status(self, now: float) -> dict:
        power = self.power_w(now)
        self._energy_wh += power * max(0.0, now - self._last) / 3600.0
        self._last = now
        volts = round(234.0 + 1.5 * math.sin(now / 55.0), 1)
        return {"id": 0, "voltage": volts, "current": round(power / volts, 3), "apower": power,
                "freq": round(50.0 + 0.04 * math.sin(now / 13.0), 1),
                "aenergy": {"total": round(self._energy_wh, 3), "by_minute": [0.0, 0.0, 0.0],
                            "minute_ts": int(now)},
                "ret_aenergy": {"total": 0.0, "by_minute": [0.0, 0.0, 0.0], "minute_ts": int(now)}}


class EmulatedFleet:
    """One simulated meter per address asked for. ``fetch`` has the same shape as http.get_json."""

    def __init__(self, clock: Callable[[], float] = time.time, outages: bool = True) -> None:
        self._clock = clock
        self._outages = outages
        self._meters: dict[str, EmulatedMeter] = {}

    def meter(self, host: str) -> EmulatedMeter:
        if host not in self._meters:
            self._meters[host] = EmulatedMeter(host, self._clock, self._outages)
        return self._meters[host]

    async def fetch(self, host: str, method: str) -> dict:
        rpc_path(method)                                  # the same allow-list as the real client
        meter = self.meter(host)
        now = self._clock()
        if meter.offline(now):
            raise ShellyError("timeout")
        if method == GET_DEVICE_INFO:
            return meter.info()
        if method == GET_STATUS:
            return {"ble": {}, "cloud": {"connected": False}, "sys": {"mac": meter.mac.upper()},
                    "pm1:0": meter.status(now)}
        raise ForbiddenMethod("method not allowed")
