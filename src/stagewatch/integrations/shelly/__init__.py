"""Shelly power meters: watts, volts, amps, frequency and energy from Shelly meters on the local
network, shown on the Power card.

Read-only. Stagewatch asks a meter for its readings (two requests only: Shelly.GetDeviceInfo and
Shelly.GetStatus, see http.py) and never sends anything that changes it. It cannot switch a relay,
change a setting, restart, update or reset a meter. A Shelly that has a relay (a Plug or a Plus 1PM,
for example) is read for its power only; its relay is never touched, and Stagewatch cannot stop the
Shelly's own button, schedule or app from switching it.

All readings are Equipment readings: they describe a circuit, never the air, so they can never reach
a site average, the speed of sound or the barometer. They are recorded exactly as the meter sends
them. Energy is the meter's running total (watt-hours), not a level. When a meter stops answering its
last readings stay on screen, frozen and marked with their age; nothing is filled in afterwards and
nothing is shown as zero.

Phase 1: HTTP polling only. Tested on one Shelly PM Mini Gen3; other models are read by the
components they report (pm1, em, em1, and a switch that measures power) but are untested.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from ...core import sitetime
from ...core.config import SHELLY_MAX_METERS, ShellyDeviceConfig
from ...core.model import UNITS, Device, Entity, Status, slugify
from ...core.plugin import Integration, Manifest
from ..smaart.client import is_local_address
from . import readings as rd
from .emulate import DEMO_HOST, DEMO_NAME, EmulatedFleet, emulated_mac
from .http import GET_DEVICE_INFO, GET_STATUS, Fetch, ShellyError, get_json
from .transport import DeviceInfo, HttpPollTransport, Target, Transport, parse_device_info

log = logging.getLogger(__name__)

CARD_ID = "power"
HEARTBEAT_S = 10.0          # an unchanged value is re-recorded at most this often
FAILURES_BEFORE_MISSING = 3  # missed polls in a row before the meter counts as not answering

MANIFEST = Manifest(
    domain="shelly",
    name="Shelly power meters",
    version="0.1.0",
    description="Reads watts, volts, amps, frequency and energy (watt-hours, the meter's running "
                "total) from Shelly power meters on the local network, by asking each meter every "
                "few seconds (Shelly.GetDeviceInfo and Shelly.GetStatus only). Read-only: it never "
                "switches a relay, changes a setting, restarts, updates or resets a meter. Apparent "
                "power and power factor are shown only when a meter sends them. All readings are "
                "Equipment: never part of a site average. Phase 1 uses HTTP polling only and has "
                "been tried on one Shelly PM Mini Gen3; other models are untested. A meter with a "
                "password is not supported yet.",
    tier="experimental",
    direction="in",
    iot_class="local_poll",
    protocols=("HTTP (Shelly RPC)",),
    entity_kinds=("power", "apparent_power", "current", "voltage", "energy", "power_factor",
                  "frequency", "temperature"),
    vendors=("Shelly",),
)

# Fixed wording for a failed request (admin Test and Add). Nothing from the meter is ever shown.
TEXT = {
    "not_local": "That address is not on a private network. Stagewatch only talks to meters on your local network.",
    "timeout": "The meter did not answer in time.",
    "refused": "Something is at that address, but it is not accepting requests.",
    "unreachable": "Could not reach that address. Check the meter is switched on and on the same network.",
    "redirect": "The meter tried to send Stagewatch somewhere else, so Stagewatch stopped.",
    "needs_password": "This meter needs a password: not supported yet.",
    "not_shelly": "That address did not answer like a Shelly (second generation or newer).",
    "too_large": "The reply was too big to be from a Shelly meter.",
    "bad_reply": "The reply was not what a Shelly sends.",
    "other_meter": "A different meter answered at that address.",
    "no_power": "This Shelly has no power readings to show.",
}
# Public device status text (short, fixed). A silent notice goes with every problem state.
DETAIL = {
    "needs_password": "Needs a password: not supported yet",
    "other_meter": "A different meter answered at the saved address",
    "not_shelly": "Did not answer like a Shelly",
    "not_local": "Address is not on the local network",
    "redirect": "Meter tried to redirect: refused",
    "too_large": "Unexpected reply from the meter",
    "bad_reply": "Unexpected reply from the meter",
    "no_power": "No power readings found on this device",
}
IMMEDIATE = frozenset(DETAIL) - {"no_power"}   # problems that are not a passing blip: show at once


def validate_host(host: object) -> str:
    """A private-network IP address, as text. Raises ShellyError("not_local") for a host name, a
    public address or anything else (the project's local-address check, shared with Smaart)."""
    try:
        if not isinstance(host, str):
            raise ValueError    # a whole number would otherwise be read as an address
        ip = ipaddress.ip_address(host.strip())
    except ValueError:
        raise ShellyError("not_local") from None
    if not is_local_address(ip):
        raise ShellyError("not_local")
    return str(ip)


class InUse(Exception):
    """The meter, the name or the room for another meter is not available. The message is fixed text."""


@dataclass
class _Meter:
    cfg: ShellyDeviceConfig
    info: DeviceInfo | None = None
    entities: set = field(default_factory=set)
    last: dict = field(default_factory=dict)         # object_id -> (value, recorded at)
    energy: dict = field(default_factory=dict)       # object_id -> last energy total seen
    last_ok: float | None = None
    ok_polls: int = 0
    failed_polls: int = 0
    gap: bool = False                                 # went quiet while we were watching: say "back" on return
    problem: str = ""
    energy_reset_at: float | None = None


def seed_emulate_shelly(config) -> bool:
    """Emulate mode only (called from __main__): a fresh emulate config gets one simulated PM Mini and
    the Power card on the stock wall dashboard so they can be seen offline. Does nothing once meters
    have been saved or any dashboard has the card. Returns True if it changed the config."""
    if "shelly_devices" in config.model_fields_set or config.shelly_devices \
            or any(CARD_ID in d.cards for d in config.dashboards):
        return False
    config.shelly_devices = [ShellyDeviceConfig(id="mic_rack_sim", host=DEMO_HOST, mac=emulated_mac(DEMO_HOST),
                                                name=DEMO_NAME, area="Stage L")]
    for d in config.dashboards:
        if d.slug == "wall":
            d.cards = [*d.cards[:1], CARD_ID, *d.cards[1:]]
    return True


class ShellyIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, fetch: Fetch | None = None,
                 transport: Transport | None = None, clock: Callable[[], float] = time.time) -> None:
        super().__init__(hub, emulate)
        self._clock = clock
        self._fleet = EmulatedFleet(clock) if emulate and fetch is None else None
        self._fetch: Fetch = fetch or (self._fleet.fetch if self._fleet else get_json)
        self._transport: Transport = transport or HttpPollTransport(self._fetch, clock)
        self._meters: dict[str, _Meter] = {}          # by MAC
        self._lock = asyncio.Lock()
        self._started = False

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self._transport.start(self)
        self._started = True
        for cfg in list(self.hub.config.shelly_devices):
            self._attach(cfg)

    async def stop(self) -> None:
        self._started = False
        await self._transport.stop()

    def _attach(self, cfg: ShellyDeviceConfig) -> None:
        self._meters[cfg.mac] = _Meter(cfg)
        self.hub.register_device(self._device(cfg, None, Status.INITIALIZING))
        self.hub.set_device_status(cfg.id, Status.INITIALIZING, "Waiting for the first reading")
        self._transport.add(Target(cfg.mac, cfg.host, cfg.poll_interval_s))

    def _device(self, cfg: ShellyDeviceConfig, info: DeviceInfo | None, status: Status) -> Device:
        model = (info.app or info.model) if info else ""
        return Device(cfg.id, cfg.name or "Power meter", MANIFEST.domain, "Shelly", model, cfg.area,
                      status, hw_id=f"mac:{cfg.mac}", category="meter", role="equipment")

    def _meter_of(self, mac: str) -> _Meter | None:
        return self._meters.get(mac)

    # ----------------------------------------------------- sink (from the transport)
    def identified(self, mac: str, info: DeviceInfo) -> None:
        m = self._meter_of(mac)
        if m is None:
            return
        m.info = info
        dev = self.hub.devices.get(m.cfg.id)
        if dev is not None and dev.model != (info.app or info.model):
            self.hub.register_device(self._device(m.cfg, info, dev.status))

    def status(self, mac: str, status: dict, ts: float) -> None:
        m = self._meter_of(mac)
        if m is None:
            return
        try:
            self._apply_status(m, status, ts)
        except Exception:  # noqa: BLE001 - a strange reply must never stop the hub
            log.exception("Shelly %s: could not use a status reply", m.cfg.id)

    def lost(self, mac: str, category: str, failures: int) -> None:
        m = self._meter_of(mac)
        if m is None:
            return
        m.failed_polls += 1
        m.problem = category
        did = m.cfg.id
        if category in IMMEDIATE:
            m.gap = True
            self.hub.set_device_status(did, Status.FAULT, DETAIL[category], silent_alarm=True)
        elif failures >= FAILURES_BEFORE_MISSING:
            m.gap = True
            since = (f"No reply since {sitetime.local_hhmm(m.last_ok, self.hub.config.site)}"
                     if m.last_ok else "No reply yet")
            self.hub.set_device_status(did, Status.MISSING, since, silent_alarm=True)
        log.info("Shelly %s: poll failed (%s, %d in a row)", did, category, failures)

    # ------------------------------------------------------------------ readings
    def _apply_status(self, m: _Meter, status: dict, ts: float) -> None:
        cfg = m.cfg
        found = rd.readings_from_status(status)
        if not found:
            m.problem = "no_power"
            m.gap = True
            self.hub.set_device_status(cfg.id, Status.FAULT, DETAIL["no_power"], silent_alarm=True)
            return
        channels = rd.channel_counts(found)
        for r in found:
            eid = f"{cfg.id}.{r.object_id}"
            if r.object_id not in m.entities:
                if r.value is None:
                    continue    # never seen with a value: an optional field this meter does not send
                self._register(m, r, eid, channels)
            self._record(m, r, eid, ts)
        m.ok_polls += 1
        m.last_ok = ts
        m.problem = ""
        was_gap, m.gap = m.gap, False
        dev = self.hub.devices.get(cfg.id)
        detail = f"Reading every {cfg.poll_interval_s:g} s"
        self.hub.set_device_status(cfg.id, Status.OK, detail)
        if was_gap and dev is not None:
            # Only a gap seen live gets a marker, made now: nothing is filled in for the time between.
            self.hub.add_marker(f"{dev.name}: meter back", "shelly")

    def _register(self, m: _Meter, r: rd.Reading, eid: str, channels: int) -> None:
        labels = {"channel": r.channel, "field": r.field}
        if r.group:
            labels["group"] = r.group
        self.hub.register_entity(Entity(
            eid, m.cfg.id, rd.display_name(r, channels), r.kind, UNITS.get(r.kind, ""), r.decimals,
            hw_key=f"mac:{m.cfg.mac}/{r.object_id}", labels=labels, locked_equipment=True))
        m.entities.add(r.object_id)

    def _record(self, m: _Meter, r: rd.Reading, eid: str, ts: float) -> None:
        if r.cumulative and r.value is not None:
            before = m.energy.get(r.object_id)
            if before is not None and r.value < before - 1e-6:
                m.energy_reset_at = ts      # someone reset the meter's counter: record as received
                log.info("Shelly %s: an energy counter went back", m.cfg.id)
            m.energy[r.object_id] = r.value
        prev = m.last.get(r.object_id)
        # An unchanged value is re-recorded every 10 s, or twice inside the site's stale limit if that is short.
        beat = min(HEARTBEAT_S, self.hub.config.site.stale_after_s / 2)
        if prev is None or prev[0] != r.value or ts - prev[1] >= beat:
            self.hub.update_state(eid, r.value, ts)
            m.last[r.object_id] = (r.value, ts)

    # -------------------------------------------------------------------- admin
    async def _identify(self, host: str) -> tuple[DeviceInfo, dict]:
        """Ask a meter who it is and what it reads (the two allowed requests). Raises ShellyError."""
        ip = validate_host(host)
        info = parse_device_info(await self._fetch(ip, GET_DEVICE_INFO))
        if info.auth_enabled:
            raise ShellyError("needs_password")
        status = await self._fetch(ip, GET_STATUS)
        if not isinstance(status, dict) or not rd.readings_from_status(status):
            raise ShellyError("no_power")
        return info, status

    def _by_mac(self, mac: str) -> ShellyDeviceConfig | None:
        return next((c for c in self.hub.config.shelly_devices if c.mac == mac), None)

    async def test(self, host: str) -> dict:
        """Admin "Test": what is at this address. Never changes anything. Raises ShellyError."""
        async with self._lock:
            info, status = await self._identify(host)
        found = rd.readings_from_status(status)
        channels = rd.channel_counts(found)
        known = self._by_mac(info.mac)
        return {
            "model": info.app or info.model, "model_code": info.model, "firmware": info.firmware,
            "mac": info.mac, "components": rd.component_words(status),
            "readings": [{"name": rd.display_name(r, channels), "value": r.value,
                          "unit": UNITS.get(r.kind, ""), "decimals": r.decimals}
                         for r in found if r.value is not None],
            "already_added": known.id if known else None,
        }

    async def add(self, host: str, name: str, area: str, interval_s: float) -> ShellyDeviceConfig:
        """Admin "Save" for a new meter. The MAC comes from the meter itself. Raises ShellyError, or
        InUse (fixed text) when the meter or the name is already in use."""
        async with self._lock:
            if len(self.hub.config.shelly_devices) >= SHELLY_MAX_METERS:
                raise InUse("There is room for 16 power meters. Remove one first")
            info, _status = await self._identify(host)
            if self._by_mac(info.mac) is not None:
                raise InUse("That meter has already been added")
            device_id = slugify(name)
            if device_id == "site" or device_id in self.hub.devices:
                raise InUse("That name is already in use. Choose another")
            cfg = ShellyDeviceConfig(id=device_id, host=validate_host(host), mac=info.mac, name=name.strip(),
                                     area=area.strip(), poll_interval_s=interval_s)
            self.hub.config.shelly_devices.append(cfg)
            self.hub.save_config()
            self._attach(cfg)
            self.identified(cfg.mac, info)
            return cfg

    async def update(self, device_id: str, name: str | None, area: str | None, interval_s: float | None,
                     host: str | None) -> None:
        async with self._lock:
            cfg = next((c for c in self.hub.config.shelly_devices if c.id == device_id), None)
            if cfg is None:
                raise LookupError
            new_host = cfg.host
            if host is not None:
                new_host = validate_host(host)
                if new_host != cfg.host:
                    info = parse_device_info(await self._fetch(new_host, GET_DEVICE_INFO))
                    if info.mac != cfg.mac:
                        raise ShellyError("other_meter")    # the saved meter is not at that address
            if name is not None:
                cfg.name = name.strip()
            if area is not None:
                cfg.area = area.strip()
            if interval_s is not None:
                cfg.poll_interval_s = interval_s
            cfg.host = new_host
            self.hub.save_config()
            m = self._meters.get(cfg.mac)
            dev = self.hub.devices.get(cfg.id)
            if dev is not None:
                self.hub.register_device(self._device(cfg, m.info if m else None, dev.status))
            self._transport.add(Target(cfg.mac, cfg.host, cfg.poll_interval_s))

    async def remove(self, device_id: str) -> None:
        async with self._lock:
            cfg = next((c for c in self.hub.config.shelly_devices if c.id == device_id), None)
            if cfg is None:
                raise LookupError
            self._transport.remove(cfg.mac)
            self._meters.pop(cfg.mac, None)
            self.hub.config.shelly_devices.remove(cfg)
            self.hub.save_config()
            self.hub.remove_device(cfg.id)      # its history stays

    def admin_status(self) -> dict:
        """Admin page only: addresses, MACs and firmware are here and nowhere public."""
        site = self.hub.config.site
        meters = []
        for cfg in self.hub.config.shelly_devices:
            m = self._meters.get(cfg.mac)
            dev = self.hub.devices.get(cfg.id)
            meters.append({
                "id": cfg.id, "name": cfg.name, "area": cfg.area, "host": cfg.host, "mac": cfg.mac,
                "interval_s": cfg.poll_interval_s,
                "model": (m.info.app or m.info.model) if m and m.info else "",
                "firmware": m.info.firmware if m and m.info else "",
                "status": dev.status.value if dev else "off", "detail": dev.status_detail if dev else "",
                "problem": TEXT.get(m.problem, "") if m and m.problem else "",
                "last_ok": m.last_ok if m else None,
                "ok_polls": m.ok_polls if m else 0, "failed_polls": m.failed_polls if m else 0,
                "energy_went_back": sitetime.local_hhmm(m.energy_reset_at, site) if m and m.energy_reset_at else "",
            })
        return {"transport": self._transport.name, "meters": meters,
                "max_meters": SHELLY_MAX_METERS, "emulate": self.emulate}

    def info(self) -> dict:
        return {**super().info(), "meters": len(self.hub.config.shelly_devices)}
