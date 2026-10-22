"""OSC output: stream site-average environment (and optionally each node)
to any OSC receiver -- delay/alignment calculators, TouchOSC, show control.

Messages (sent at osc_out.rate_hz):
  /stagewatch/avg/env    temp_c rh_pct pressure_pa c_m_s n_temp_sensors
  /stagewatch/node/<device_id>/env  temp_c rh_pct pressure_pa   (per_node only)
  /stagewatch/alarm      max_level(0-3) sounding(0/1)
Only with osc_out.include_advice (FOH advice; values, not commands):
  /stagewatch/path/<id>/drift            ms since the reference marker (+ = sound arrives later)
  /stagewatch/position/<id>/change_db    dB since the reference marker, one float per band in band
                                         order (4k, 8k, 16k or 4k ... 16k), + = more top end lost
NaN means "not measured" (or unknown, or stale).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re

from pythonosc.udp_client import SimpleUDPClient

from ..core.config import ADVICE_ID_RE as _ADVICE_ID_PATTERN
from ..core.model import Kind
from ..core.plugin import Integration, Manifest

log = logging.getLogger(__name__)

NAN = math.nan
ADVICE_ID_RE = re.compile(_ADVICE_ID_PATTERN)

MANIFEST = Manifest(
    domain="osc_out",
    name="OSC output",
    version="0.1.0",
    description="Sends site-average temperature, humidity, pressure and speed of sound "
                "(plus alarm level) to OSC receivers.",
    tier="experimental",
    direction="out",
    protocols=("OSC/UDP",),
)


def _num(v: float | None) -> float:
    return NAN if v is None else float(v)


def _live(value: float | None, stale: bool) -> float:
    """A number to send: NaN when unknown, or when the reading behind it has gone stale."""
    return NAN if value is None or stale else float(value)


def advice_messages(advice: dict) -> list[tuple[str, list]]:
    """The FOH advice numbers as OSC messages (opt-in with ``osc_out.include_advice``). Values, not
    commands: nothing here asks a receiver to change anything. Addresses use only the slug ids,
    never a name or a device id. NaN means unknown (no reference marker, no readings, stale)."""
    msgs: list[tuple[str, list]] = []
    for p in advice.get("paths", []):
        if not ADVICE_ID_RE.fullmatch(str(p.get("id", ""))):
            continue
        base = f"/stagewatch/path/{p['id']}"
        msgs.append((f"{base}/drift", [_live(p.get("drift_ms"), p.get("stale", False))]))
    for pos in advice.get("positions", []):
        if not ADVICE_ID_RE.fullmatch(str(pos.get("id", ""))):
            continue
        msgs.append((f"/stagewatch/position/{pos['id']}/change_db",
                     [_live(b.get("change_db"), pos.get("stale", False)) for b in pos.get("bands", [])]))
    return msgs


class OscOutIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False) -> None:
        super().__init__(hub, emulate)
        self._task: asyncio.Task | None = None
        self._clients: dict[tuple[str, int], SimpleUDPClient] = {}
        self.sent = 0
        self.last_error = ""

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="osc-out")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _client(self, host: str, port: int) -> SimpleUDPClient:
        key = (host, port)
        if key not in self._clients:
            self._clients[key] = SimpleUDPClient(host, port, allow_broadcast=True)
        return self._clients[key]

    def build_messages(self) -> list[tuple[str, list]]:
        hub = self.hub
        e = hub.entities
        msgs: list[tuple[str, list]] = [(
            "/stagewatch/avg/env",
            [_num(e["site.temperature"].value), _num(e["site.humidity"].value),
             _num(e["site.pressure"].value), _num(e["site.speed_of_sound"].value),
             int(hub.site_meta.get("sensors", {}).get("temperature", 0))],
        ), ("/stagewatch/alarm", [hub.alarms.max_level, int(hub.alarms.sounding)])]
        if hub.config.osc_out.per_node:
            for device in hub.devices.values():
                if device.id == "site":
                    continue
                vals = {Kind.TEMPERATURE: None, Kind.HUMIDITY: None, Kind.PRESSURE: None}
                for ent in e.values():
                    if ent.device_id == device.id and ent.kind in vals and not ent.locked_equipment:
                        value, stale = hub.lookup(ent.id)
                        vals[ent.kind] = None if stale else value
                msgs.append((f"/stagewatch/node/{device.id}/env",
                             [_num(vals[Kind.TEMPERATURE]), _num(vals[Kind.HUMIDITY]),
                              _num(vals[Kind.PRESSURE])]))
        if hub.config.osc_out.include_advice:
            msgs.extend(advice_messages(hub.advice.snapshot()))
        return msgs

    def send_once(self) -> None:
        dests = [d for d in self.hub.config.osc_out.destinations if d.enabled]
        if not dests:
            return
        msgs = self.build_messages()
        for d in dests:
            try:
                client = self._client(d.host, d.port)
                for address, args in msgs:
                    client.send_message(address, args)
                self.sent += 1
            except OSError as exc:
                self.last_error = f"{d.host}:{d.port} {exc}"
                log.warning("OSC send to %s:%s failed: %s", d.host, d.port, exc)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(1.0 / self.hub.config.osc_out.rate_hz)
            self.send_once()

    def info(self) -> dict:
        return {**super().info(), "sent": self.sent, "last_error": self.last_error}
