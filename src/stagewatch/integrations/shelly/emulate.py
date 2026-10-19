"""A simulated Shelly PM Mini Gen3 for building and demoing a show with no hardware.

It stands in for the network request (``fetch(host, method)``), so everything above it runs for real:
the poller, the common reading layer, entities, status, gaps and the "meter back" marker.

The simulated meter measures a small rack of gear: a steady base load with slow swings and a short
spike now and then. Its energy counter adds up the simulated power, so the kWh figure is consistent.
Like a real PM Mini Gen3 it sends no apparent power and no power factor (Stagewatch calculates apparent power as volts x amps; the Gen1 EM below sends a power factor but no current, so it uses watts / PF).

Fault path: for 40 seconds out of every 5 minutes it does not answer (as if its Wi-Fi dropped), so the
card can be seen frozen with its age, and the "meter back" marker appears when it returns.

Simulated MACs start 02:5e:00:00:03 (locally administered), so they can never be a real meter's.
"""

from __future__ import annotations

import math
import time
import zlib
from typing import Callable

from .http import GEN1_INFO, GEN1_STATUS, GET_DEVICE_INFO, GET_STATUS, ForbiddenMethod, ShellyError, rpc_path

DEMO_HOST = "192.0.2.10"        # documentation address: never a real device
DEMO_NAME = "Mic rack (sim)"
DEMO3_HOST = "192.0.2.11"       # a simulated three-phase meter (a Pro 3EM shape: em:0 and emdata:0)
DEMO3_NAME = "Distro (sim)"
DEMO1_HOST = "192.0.2.12"        # a simulated first-generation Shelly EM (two channels, one relay)
DEMO1_NAME = "FOH power (sim, Gen1)"
CYCLE_S = 300.0
OUTAGE_START_S = 240.0
OUTAGE_S = 40.0


def neutral_a(a: float, b: float, c: float) -> float:
    """Neutral current of three line currents 120 degrees apart: sqrt(a2+b2+c2-ab-bc-ca)."""
    return math.sqrt(max(0.0, a * a + b * b + c * c - a * b - b * c - c * a))


def emulated_mac(host: str) -> str:
    """12 lower-case hex digits: a fixed simulated MAC per address."""
    if host == DEMO_HOST:
        return "025e00000301"
    if host == DEMO3_HOST:
        return "025e00000302"
    if host == DEMO1_HOST:
        return "025e00000303"
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
        self.three = host == DEMO3_HOST
        self.gen1 = host == DEMO1_HOST
        self._gen1_wh = [1615941.4, 52310.2]
        self._phase_wh = {"a": 40000.0, "b": 31000.0, "c": 26000.0}

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
        if self.three:
            return {"name": None, "id": f"shellypro3em-{self.mac.upper()}", "mac": self.mac.upper(),
                    "slot": 0, "model": "SPEM-003CEBEU", "gen": 2, "fw_id": "20250101-000000/sim",
                    "ver": "sim", "app": "Pro3EM", "auth_en": False, "auth_domain": None}
        return {"name": None, "id": f"shellypmminig3-{self.mac.upper()}", "mac": self.mac.upper(),
                "slot": 0, "model": "S3PM-001PCEU16", "gen": 3, "fw_id": "20250101-000000/sim",
                "ver": "sim", "app": "MiniPMG3", "auth_en": False, "auth_domain": None}

    def info1(self) -> dict:
        """What a first-generation Shelly EM says at GET /shelly (anonymised)."""
        return {"type": "SHEM", "mac": self.mac.upper(), "auth": False, "fw": "20230913-000000/v1.14.0-sim",
                "num_outputs": 1, "num_meters": 0, "num_emeters": 2}

    def status1(self, now: float) -> dict:
        """GET /status of a Shelly EM: two channels, watt-hours totals, and the relay (never read)."""
        dt = max(0.0, now - self._last)
        self._last = now
        out = []
        for i, base in enumerate((228.0, 61.0)):
            w = round(base + 20.0 * math.sin(now / (70.0 + 9 * i)), 2)
            self._gen1_wh[i] += w * dt / 3600.0
            out.append({"power": w, "reactive": round(-0.77 * w, 2), "pf": round(-0.79 + 0.02 * i, 2),
                        "voltage": round(242.0 + 0.8 * math.sin(now / 50.0), 2), "is_valid": True,
                        "total": round(self._gen1_wh[i], 1), "total_returned": 97.8})
        return {"relays": [{"ison": False}], "emeters": out}

    def components(self, now: float) -> dict:
        """The status components this meter sends: pm1:0, or em:0 and emdata:0 for the three-phase one."""
        return self.status3(now) if self.three else {"pm1:0": self.status(now)}

    def status3(self, now: float) -> dict:
        """Three lines, unevenly loaded (about 14, 11 and 9 A), with the neutral that follows from it."""
        dt = max(0.0, now - self._last)
        self._last = now
        amps = {"a": 14.0 + 2.0 * math.sin(now / 80.0), "b": 11.0 + 1.5 * math.sin(now / 70.0 + 1.0),
                "c": 9.0 + 1.0 * math.sin(now / 90.0 + 2.0)}
        out: dict = {}
        total_w = total_va = total_a = 0.0
        for i, p in enumerate("abc"):
            volts = 232.0 + 1.2 * math.sin(now / (55.0 + 7 * i))
            va, w = volts * amps[p], volts * amps[p] * 0.95
            self._phase_wh[p] += w * dt / 3600.0
            out.update({f"{p}_current": round(amps[p], 3), f"{p}_voltage": round(volts, 1), f"{p}_act_power": round(w, 1),
                        f"{p}_aprt_power": round(va, 1), f"{p}_pf": 0.95, f"{p}_freq": round(50.0 + 0.04 * math.sin(now / 13.0), 2)})
            total_w, total_va, total_a = total_w + w, total_va + va, total_a + amps[p]
        out.update({"n_current": round(neutral_a(**{k: v for k, v in amps.items()}), 3), "total_current": round(total_a, 3),
                    "total_act_power": round(total_w, 1), "total_aprt_power": round(total_va, 1)})
        emdata = {f"{p}_total_act_energy": round(self._phase_wh[p], 3) for p in "abc"}
        emdata.update({f"{p}_total_act_ret_energy": 0.0 for p in "abc"})
        emdata.update(total_act=round(sum(self._phase_wh.values()), 3), total_act_ret=0.0)
        return {"em:0": {"id": 0, **out}, "emdata:0": {"id": 0, **emdata}}

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
        if method in (GET_DEVICE_INFO, GET_STATUS) and meter.gen1:
            raise ShellyError("not_shelly")               # a first-generation meter has no /rpc (404)
        if method in (GEN1_INFO, GEN1_STATUS):
            if not meter.gen1:
                raise ShellyError("not_shelly")
            return meter.info1() if method == GEN1_INFO else meter.status1(now)
        if method == GET_DEVICE_INFO:
            return meter.info()
        if method == GET_STATUS:
            return {"ble": {}, "cloud": {"connected": False}, "sys": {"mac": meter.mac.upper()},
                    **meter.components(now)}
        raise ForbiddenMethod("method not allowed")
