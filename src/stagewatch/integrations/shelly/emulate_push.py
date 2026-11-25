"""Simulated Shelly devices that push to the receiver, for building and demoing a show with no hardware.

Every simulated frame is raw JSON bytes fed to the same ``Receiver.handle`` a real WebSocket uses, so parsing,
identity, merging, stale handling and alarms all run for real. Addresses are documentation addresses
(192.0.2.x) and MACs start 02:5e (locally administered), so nothing here can be a real device.

* "FOH tent H&T (sim)": on batteries. Wakes every 120 s, sends a full status, battery 24 % falling 1 % a wake,
  so the low-battery notice appears after a few minutes. Every 10th wake it skips three (so it goes missing,
  then recovers).
* "Amp room H&T (sim)": on USB power, wakes every 60 s.
* "Dock 3EM (push sim)": a Pro 3EM shape, a socket that stays open and sends a change every few seconds. Its
  socket drops for 3 minutes in every 10, so the card freezes with its age, goes missing, then a "meter back"
  marker appears.
* An unknown sender with a power-meter component set, so the found list has something to show.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math

from .emulate import EmulatedMeter
from .receiver import Receiver

log = logging.getLogger(__name__)

HT1_MAC, HT1_IP, HT1_NAME = "025e00000401", "192.0.2.21", "FOH tent H&T (sim)"
HT2_MAC, HT2_IP, HT2_NAME = "025e00000402", "192.0.2.22", "Amp room H&T (sim)"
EM_MAC, EM_IP, EM_NAME = "025e00000403", "192.0.2.23", "Dock 3EM (push sim)"
UNK_MAC, UNK_IP = "025e000004ff", "192.0.2.29"
TICK_S = 3.0
DROP_CYCLE_S, DROP_START_S, DROP_LEN_S = 600.0, 420.0, 180.0


def frame(src_prefix: str, mac: str, method: str, params: dict) -> bytes:
    """One notification as a Shelly sends it (frame shape from the Notifications documentation)."""
    return json.dumps({"src": f"{src_prefix}-{mac}", "dst": "user_1", "method": method, "params": params}).encode()


def ht_params(mac: str, t: float, battery: float, usb: bool, period: int) -> dict:
    temp = round(22.0 + 3.0 * math.sin(t / 900.0), 1)
    return {"ts": t, "sys": {"mac": mac.upper(), "wakeup_period": period, "wakeup_reason": {"boot": "deepsleep_wake", "cause": "periodic"}},
            "temperature:0": {"id": 0, "tC": temp, "tF": round(temp * 1.8 + 32, 1)},
            "humidity:0": {"id": 0, "rh": round(45.0 + 8.0 * math.sin(t / 1300.0), 1)},
            "devicepower:0": {"id": 0, "battery": {"V": round(3.0 + battery / 100.0, 2), "percent": round(battery)},
                              "external": {"present": usb}}}


class EmulatedPush:
    def __init__(self, integ) -> None:
        self.integ = integ
        self.rx: Receiver = integ.push.rx
        self.clock = integ._clock
        self._task: asyncio.Task | None = None
        self._t0 = self.clock()
        self._battery = 24.0
        self._wakes = 0
        self._next = {HT1_MAC: self._t0, HT2_MAC: self._t0}
        self._skip = 0
        self._em = EmulatedMeter(EM_IP, self.clock, outages=False)
        self._em_conn = None
        self._sent_full = False
        self._unknown_at = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="shelly-emulate-push")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                self.step(self.clock())
            except Exception:  # noqa: BLE001
                log.exception("Shelly emulate: a simulated frame failed")
            await asyncio.sleep(TICK_S)

    # --------------------------------------------------------------------- one tick
    def _send(self, ip: str, raw: bytes, keep=None):
        """A short visit (the device wakes, sends, sleeps) or on an open connection."""
        conn = keep
        if conn is None:
            if self.rx.admit(ip, False) is not None:
                return None
            conn = self.rx.open(ip)
        code = self.rx.handle(conn, raw)
        if code is not None or keep is None:
            self.rx.release(conn)
            return None
        return conn

    def step(self, now: float) -> None:
        t = now - self._t0
        for mac, ip, usb, period in ((HT1_MAC, HT1_IP, False, 120), (HT2_MAC, HT2_IP, True, 60)):
            if now < self._next[mac]:
                continue
            self._next[mac] = now + period
            if mac == HT1_MAC:
                self._wakes += 1
                if self._skip > 0:
                    self._skip -= 1
                    continue
                if self._wakes % 10 == 0:
                    self._skip = 3        # the fault drill: three missed wakes
                self._battery = max(0.0, self._battery - 1.0)
            raw = frame("shellyhtg3", mac, "NotifyFullStatus", ht_params(mac, now, self._battery if mac == HT1_MAC else 100.0, usb, period))
            self._send(ip, raw)
        # the three-phase meter: a socket that stays open, dropped for a while in every cycle
        phase = t % DROP_CYCLE_S
        if DROP_START_S <= phase < DROP_START_S + DROP_LEN_S:
            if self._em_conn is not None:
                self.rx.release(self._em_conn)
                self._em_conn, self._sent_full = None, False
        else:
            if self._em_conn is None and self.rx.admit(EM_IP, False) is None:
                self._em_conn = self.rx.open(EM_IP)
            if self._em_conn is not None:
                comps = self._em.components(now)
                method = "NotifyStatus" if self._sent_full else "NotifyFullStatus"
                raw = frame("shellypro3em", EM_MAC, method, {"ts": now, **({"sys": {"mac": EM_MAC.upper()}} if not self._sent_full else {}), **comps})
                if self._send(EM_IP, raw, keep=self._em_conn) is None:
                    self._em_conn = None
                self._sent_full = self._em_conn is not None
        if now - self._unknown_at > 120.0:
            self._unknown_at = now
            self._send(UNK_IP, frame("shellyplus1pm", UNK_MAC, "NotifyFullStatus",
                                     {"ts": now, "sys": {"mac": UNK_MAC.upper()}, "switch:0": {"id": 0, "output": False, "apower": 0.0}}))
