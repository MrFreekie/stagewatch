"""A simulated RME unit for ``--emulate`` and tests: no hardware, no network.

It makes frames shaped like the ones in the captures (a full ``msg: status`` then small JSON deltas,
and a count of binary meter frames that are thrown away) and feeds them through the SAME parser a real
unit's frames go through, so emulate mode exercises the real code. Everything here is invented; the
names and the serial number are placeholders.

The script repeats every ``cycle_s`` (six minutes by default). For unit 1:

* 90 s  power supply 2 drops for 20 s (the unit's redundancy alert is on, so it is an Alert)
* 150 s the clock source changes from Word clock to Internal, with a Notice (InputNoSync), for 40 s
* 260 s one soundness word nobody has seen before appears for 20 s ("Unrecognised")

Unit 2 closes its socket at 200 s for 30 s (offline, a gap, then a new status and a "back after" marker).
The temperature drifts between 52 and 63 degC. Time comes from an injected clock so tests can run a
whole cycle in a moment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Callable

from .parse import RmeState
from .source import LinkCallback, RmeSource, StateCallback

log = logging.getLogger(__name__)

CYCLE_S = 360.0
TICK_S = 1.0
TEMP_STEP_S = 3.0                 # the temperature changes every few seconds, like the real unit
BINARY_PER_S = 66                 # meter frames a second, counted and thrown away
UNKNOWN_WORD = "Surprise"         # an invented soundness word, to show the "never green" path
PRESET_NAMES = {"1": "Live 1", "2": "Live 2", "3": "Walk-in"}

CHANNEL_NAMES = ("RF Mic 1", "RF Mic 2", "RF Mic 3", "RF Mic 4", "Kick in", "Kick out",
                 "Snare top", "Snare bottom", "Hat", "Tom 1", "Tom 2", "Mic 12")

DEMO_UNITS = (
    {"index": 0, "host": "192.0.2.21", "name": "Stage L pre (simulated)", "serial": "SIM-0001", "model": "12Mic"},
    {"index": 1, "host": "192.0.2.22", "name": "Monitor world (simulated)", "serial": "SIM-0002", "model": "12Mic"},
)


@dataclass(frozen=True)
class Scene:
    online: bool
    temp_c: float
    psu2: bool
    redundancy: bool
    source: str
    clock_state: str
    clock_cause: str
    preset_state: str


def scene(index: int, t: float) -> Scene:
    """The simulated unit at ``t`` seconds into the cycle. Pure and deterministic."""
    t = t % CYCLE_S
    stepped = int(t // TEMP_STEP_S) * TEMP_STEP_S
    temp = round(57.5 + 5.4 * math.sin(2 * math.pi * stepped / CYCLE_S + index), 2)
    online = not (index == 1 and 200 <= t < 230)
    psu2 = not (index == 0 and 90 <= t < 110)
    internal = index == 0 and 150 <= t < 190
    return Scene(
        online=online, temp_c=temp, psu2=psu2, redundancy=(index == 0),
        source="internal" if internal else "wordclock",
        clock_state="Notice" if internal else "Good", clock_cause="InputNoSync" if internal else "ClockMasterNoSync",
        preset_state=UNKNOWN_WORD if index == 0 and 260 <= t < 280 else "Good",
    )


def full_status(sc: Scene, name: str, serial: str, model: str = "12Mic") -> dict:
    """A whole state in the shape of the capture, with the parts Stagewatch throws away (osc schema,
    network ids, analogue input gain and 48 V; channel names are kept) included so the whitelist has something to drop."""
    good = {"state": "Good"}
    avb = {str(n): {"pri": {"soundness": dict(good) if n <= 4 else {"state": "Inactive"},
                            "avb_status": {"stream_id": "00-00-00-00-00-00-00-0%d" % n, "dest_addr": "91-E0-F0-00-00-0%d" % n, "state": 1}},
                    "sec": {"soundness": {"state": "Inactive"}}} for n in range(1, 9)}
    analog = {str(n): {"gain": 30, "label": CHANNEL_NAMES[n - 1], "p48": False, "phase": False} for n in range(1, 13)}
    return {
        "osc": {"version": "1.0", "schema": {"device": {"psu": {"state": None}, "health": {"temperatures": {"core": None}}}}},
        "device": {
            "entity_name": name, "vendor_name": "RME", "model_name": model, "firmware_version": "2.0.1",
            "group_name": "Group A", "serial_number": serial, "identify": False,
            "psu": {"state": [True, sc.psu2], "redundancy_alert": sc.redundancy, "soundness": dict(good)},
            "levelmeters": {"mode": "rms", "over_hold": 2, "over_reset": False},
            "preset": {"active": 1, "loaded": 1, "names": dict(PRESET_NAMES), "soundness": {"state": sc.preset_state}},
            "health": {"temperatures": {"core": round(sc.temp_c + 273.15, 2)}, "soundness": dict(good)},
            "soundness": dict(good),
        },
        "network": {"pri": {"gptp_grandmaster_id": "00-00-00-FFFE-000000"}},
        "clock": {
            "sampling_rate": 48000, "current_sampling_rate": 48000, "source": sc.source,
            "soundness": {"state": sc.clock_state, "cause": sc.clock_cause},
            "sources": {"internal": {"rate": 48000, "sync": True, "soundness": dict(good)},
                        "wordclock": {"rate": 48000, "sync": sc.source == "wordclock", "soundness": dict(good)}},
        },
        "input": {"analog": analog, "avb": avb},
        "output": {"avb": {}},
    }


def diff(old: dict, new: dict) -> dict:
    """The nested changes from ``old`` to ``new`` (a changed list or scalar is sent whole), as a unit's delta."""
    out: dict = {}
    for key, value in new.items():
        before = old.get(key)
        if isinstance(value, dict) and isinstance(before, dict):
            inner = diff(before, value)
            if inner:
                out[key] = inner
        elif before != value or type(before) is not type(value):
            out[key] = value
    return out


class EmulatedRmeSource(RmeSource):
    verified = False
    label = "Simulated RME unit"

    def __init__(self, on_state: StateCallback, on_link: LinkCallback, *, index: int = 0,
                 name: str = "Stage L pre (simulated)", serial: str = "SIM-0001",
                 clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep, tick_s: float = TICK_S,
                 start_at_s: float = 0.0) -> None:
        super().__init__(on_state, on_link)
        self._index, self._name, self._serial = index, name, serial
        self._clock, self._sleep, self._tick = clock, sleep, tick_s
        self._start_at = start_at_s
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="rme-emulate")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _link(self, up: bool, detail: str, waiting: bool = False) -> None:
        try:
            self._on_link(up, detail, waiting)
        except Exception:  # noqa: BLE001
            log.exception("RME emulate link update failed")

    def _feed(self, state: RmeState, frame: dict) -> None:
        """Every frame goes through the real parser, as text, exactly as a socket would deliver it."""
        now = self._clock()
        result = state.ingest(json.dumps(frame))
        self.last_text_at = now
        if result.kind == "baseline":
            self._link(True, "")
        self._on_state(state, result)

    async def _run(self) -> None:
        started = self._clock()
        state: RmeState | None = None
        last_full: dict | None = None
        while True:
            now = self._clock()
            t = (now - started) + self._start_at
            sc = scene(self._index, t)
            if not sc.online:
                if state is not None:
                    state = last_full = None
                    self.last_frame_at = self.last_text_at = None
                    self._link(False, "The unit closed the connection")
            else:
                self.last_frame_at = now
                self.binary_frames += max(1, int(BINARY_PER_S * self._tick))
                full = full_status(sc, self._name, self._serial)
                try:
                    if state is None:
                        state = RmeState()
                        self._feed(state, {"msg": "status", "data": full})
                    else:
                        change = diff(last_full or {}, full)
                        if change:
                            self._feed(state, change)
                    last_full = full
                except Exception:  # noqa: BLE001 - a simulated unit must never stop the hub
                    log.exception("RME emulate frame failed")
            await self._sleep(self._tick)
