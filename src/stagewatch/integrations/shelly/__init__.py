"""Shelly power meters: watts, volts, amps, frequency and energy from Shelly meters on the local
network, shown on the Power card.

Read-only. Stagewatch asks a meter for its readings (two requests only: Shelly.GetDeviceInfo and
Shelly.GetStatus, or /shelly and /status for a first-generation meter, see http.py) and never sends anything that changes it. It cannot switch a relay,
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
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from ...core import loopguard, power_tree, sitetime
from ...core.config import SHELLY_MAX_METERS, PowerPointConfig, ShellyChannelConfig, ShellyDeviceConfig, Threshold
from ...core.model import UNITS, Device, Entity, Kind, Status, slugify
from ...core.plugin import Integration, Manifest
from ..smaart.client import is_local_address
from . import dayfigures as df
from . import neutral as nt
from .derived import SITE_ID, DerivedPower
from .replay import Rebuild
from . import readings as rd
from .emulate import (DEMO1_HOST, DEMO1_NAME, DEMO3_HOST, DEMO3_NAME, DEMO4_HOST, DEMO4_NAME, DEMO5_HOST, DEMO5_NAME,
                      DEMO_HOST, DEMO_NAME, EmulatedFleet, emulated_mac)
from .http import Fetch, ShellyError, get_json
from .transport import DeviceInfo, HttpPollTransport, Target, Transport, identify, read_status

log = logging.getLogger(__name__)

CARD_ID = "power"
# Mains limits for a 230 V supply in the UK: 230 V +10 % / -6 % (Electricity Safety, Quality and
# Continuity Regulations 2002; BS EN 50160) = 216.2 to 253.0 V. Advisory only. A supply under 180 V
# (110 V site power, say) is not judged against it.
UK_V_LOW, UK_V_HIGH, MAINS_MIN_V = 216.2, 253.0, 180.0
LOAD_HIGH_PCT, LOAD_OVER_PCT = 80.0, 100.0
# Frequency, 50 Hz grid: 49.5 to 50.5 Hz is the statutory band (+/-1 %); 49.8 to 50.2 Hz is the grid
# operator's normal band, so outside that is "close to the limit". Not judged outside 40 to 55 Hz.
UK_F_LOW, UK_F_HIGH, UK_F_NEAR_LOW, UK_F_NEAR_HIGH, F_JUDGED = 49.5, 50.5, 49.8, 50.2, (40.0, 55.0)
SW_NUM = lambda v: f"{v:.2f}".rstrip("0").rstrip(".")  # noqa: E731
HEARTBEAT_S = 10.0          # an unchanged value is re-recorded at most this often
NEUTRAL_ID = "shelly_neutral"      # the one calculated-neutral "meter" (see _update_neutral)
NEUTRAL_ENTITY = f"{NEUTRAL_ID}.neutral_current"
FAILURES_BEFORE_MISSING = 3  # missed polls in a row before the meter counts as not answering

MANIFEST = Manifest(
    domain="shelly",
    name="Shelly power meters",
    version="0.1.0",
    description="Reads watts, volts, amps, frequency and energy (watt-hours, the meter's running "
                "total) from Shelly power meters on the local network, by asking each meter every "
                "few seconds (Shelly.GetDeviceInfo and Shelly.GetStatus, or /shelly and /status for a first-generation meter, only). Read-only: it never "
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
    "not_shelly": "That address did not answer like a Shelly (any generation).",
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


def channel_rating(cfg: ShellyDeviceConfig, key: str) -> float | None:
    """A circuit's own rating, else the meter's."""
    ch = next((c for c in cfg.channels if c.key == key), None)
    return (ch.rating_a if ch and ch.rating_a else None) or cfg.rating_a


def public_extra(cfg: ShellyDeviceConfig) -> dict:
    """The public bits of a meter's settings the Power card needs: ratings, labels, phases. No address."""
    out: dict = {}
    if cfg.rating_a:
        out["rating_a"] = cfg.rating_a
    if cfg.combined_bar and cfg.rating_a:
        out["combined_bar"] = True
    chans = {}
    for c in cfg.channels:
        d = {k: v for k, v in (("label", c.label), ("phase", c.phase), ("rating_a", c.rating_a), ("in_total", c.in_total)) if v}
        if d:
            chans[c.key] = d
    if chans:
        out["channels"] = chans
    return out


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
    freq_level: dict = field(default_factory=dict)    # object_id -> 0 fine, 1 close to a limit, 2 outside
    volts_out: dict = field(default_factory=dict)     # object_id -> outside the UK range now
    day_key: str = ""                                 # the show day the figures below belong to
    figures: dict = field(default_factory=dict)       # object_id -> DayFigures for that show day
    rebuilds: dict = field(default_factory=dict)      # object_id -> Rebuild, while that day so far is read back


def seed_emulate_shelly(config) -> bool:
    """Emulate mode only (called from __main__): a fresh emulate config gets one simulated PM Mini and
    the Power card on the stock wall dashboard so they can be seen offline. Does nothing once meters
    have been saved or any dashboard has the card. Returns True if it changed the config."""
    if "shelly_devices" in config.model_fields_set or config.shelly_devices \
            or any(CARD_ID in d.cards for d in config.dashboards):
        return False
    config.shelly_devices = [
        ShellyDeviceConfig(id="mic_rack_sim", host=DEMO_HOST, mac=emulated_mac(DEMO_HOST),
                           name=DEMO_NAME, area="Stage L", rating_a=16, role="equipment", unit_no=1, upstream="m:distro_sim",
                           channels=[ShellyChannelConfig(key="pm1_0", label="Mic rack", phase="L1", in_total=True)]),
        ShellyDeviceConfig(id="amp_rack_sim", host=DEMO4_HOST, mac=emulated_mac(DEMO4_HOST),
                           name=DEMO4_NAME, area="Stage R", rating_a=16, role="equipment", unit_no=2, upstream="m:distro_sim",
                           channels=[ShellyChannelConfig(key="pm1_0", phase="L2")]),
        ShellyDeviceConfig(id="lighting_sim", host=DEMO5_HOST, mac=emulated_mac(DEMO5_HOST),
                           name=DEMO5_NAME, area="Truss", rating_a=16, role="equipment", unit_no=3, upstream="c:em_sim:gen1em_0",
                           channels=[ShellyChannelConfig(key="pm1_0", phase="L3")]),
        ShellyDeviceConfig(id="distro_sim", host=DEMO3_HOST, mac=emulated_mac(DEMO3_HOST),
                           name=DEMO3_NAME, area="Dock", rating_a=32, role="isu", unit_no=1, upstream="p:gen_foh"),
        ShellyDeviceConfig(id="em_sim", host=DEMO1_HOST, mac=emulated_mac(DEMO1_HOST),
                           name=DEMO1_NAME, area="FOH", rating_a=16, combined_bar=True, role="isu", unit_no=2, upstream="p:gen_tower",
                           channels=[ShellyChannelConfig(key="gen1em_0", label="FOH L1", phase="L1", rating_a=10, in_total=True,
                                                         role="ups", unit_no=1, upstream="m:em_sim"),
                                     ShellyChannelConfig(key="gen1em_1", label="FOH L2", phase="L2", rating_a=10, in_total=True,
                                                         role="equipment", unit_no=4, upstream="m:em_sim")]),
    ]
    # Two independent supplies (a main generator and a delay-tower generator), unmetered points, and a UPS.
    config.power_points = [
        PowerPointConfig(id="gen_foh", name="Main generator", role="source", unit_no=1, rating_kva=60),
        PowerPointConfig(id="gen_tower", name="Delay tower generator", role="source", unit_no=2, rating_kva=20),
        PowerPointConfig(id="stage_left", name="Stage left distribution", role="cdu", unit_no=1, upstream="p:gen_foh"),
        PowerPointConfig(id="video_wall", name="Video wall", role="equipment", unit_no=5, upstream="p:stage_left"),
    ]
    # Short demand window and a generator size, so the calculated figures appear within minutes in emulate mode.
    config.power_site.demand_window_min = 5
    config.power_site.gen_size_kva = 20.0
    for d in config.dashboards:
        if d.slug == "wall":
            d.cards = [*d.cards[:1], CARD_ID, "power_tree", *d.cards[1:]]
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
        self._neutral_last: tuple[float | None, float] | None = None
        self._derived = DerivedPower(self)    # calculated figures: demand, unbalance, site total, generator

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self._transport.start(self)
        self._started = True
        for cfg in list(self.hub.config.shelly_devices):
            self._attach(cfg)
        self._update_neutral(self._clock())

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
        dev = Device(cfg.id, cfg.name or "Power meter", MANIFEST.domain, "Shelly", model, cfg.area,
                     status, hw_id=f"mac:{cfg.mac}", category="meter", role="equipment")
        dev.public = public_extra(cfg)      # public: the Power card draws its load bars and labels from it
        return dev

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
            with loopguard.timed(f"Shelly poll {m.cfg.id}"):
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
        self._update_neutral(self._clock())

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
        self._update_neutral(ts)
        try:
            self._derived.meter_polled(m, found, ts)
        except Exception:  # noqa: BLE001 - a calculated figure must never stop the readings
            log.exception("Shelly %s: could not work out the calculated figures", cfg.id)

    def _register(self, m: _Meter, r: rd.Reading, eid: str, channels: int) -> None:
        labels = {"channel": r.channel, "field": r.field}
        if r.group:
            labels["group"] = r.group
        if r.calculated:
            labels["calculated"] = r.calculated   # worked out by Stagewatch, not the meter's own reading
        self.hub.register_entity(Entity(
            eid, m.cfg.id, rd.display_name(r, channels), r.kind, UNITS.get(r.kind, ""), r.decimals,
            hw_key=f"mac:{m.cfg.mac}/{r.object_id}", labels=labels, locked_equipment=True))
        m.entities.add(r.object_id)

    def _day_figures(self, m: _Meter, r: rd.Reading, eid: str, ts: float) -> None:
        """Add one reading to this show day's figures and hand them to the entity (see dayfigures.py).
        After a restart the day so far is rebuilt from the readings already recorded, never invented."""
        entity = self.hub.entities.get(eid)
        if entity is None or r.value is None or not (r.cumulative or r.field in df.MAX_FIELDS or r.field.startswith("meter_max_")):
            return
        site = self.hub.config.site
        day = sitetime.show_day(ts, site).isoformat()
        if m.day_key != day:
            m.day_key, m.figures, m.rebuilds = day, {}, {}
        fig = m.figures.get(r.object_id)
        if fig is None:
            fig = m.figures[r.object_id] = df.DayFigures(r.field, r.cumulative, day)
            m.rebuilds[r.object_id] = Rebuild(self.hub.recorder, eid, lambda: sitetime.resolve(day, site.day_rollover, site), ts, fig)
        rb = m.rebuilds.get(r.object_id)
        (rb or fig).feed(r.value, ts)
        if rb is not None and not rb.ready:
            return      # the day so far is still being read back: no figure yet, never a partial one
        m.rebuilds.pop(r.object_id, None)
        entity.day = fig.to_dict()

    def _set_notice(self, key: str, on: bool, level: int, text: str) -> None:
        """A quiet on-screen notice (never sounds) for a load or voltage warning."""
        change = self.hub.alarms.set_condition(f"power:{key}", on, level, text, time.time(), silent=True)
        if change:
            self.hub._alarm_changed([change])

    def _check_limits(self, m: _Meter, r: rd.Reading, ts: float) -> None:
        """Quiet warnings from the latest readings: mains voltage and frequency against the UK ranges.
        (Load warnings are normal thresholds the admin creates with "Add warnings", see apply_warnings.) Advisory only; they clear with a little
        margin so a reading on the edge does not flicker."""
        if r.value is None:
            return
        name = m.cfg.name or "Power meter"
        if r.field == "voltage" and r.group in ("", "a", "b", "c") and r.value >= MAINS_MIN_V:
            was = m.volts_out.get(r.object_id, False)
            out = not (UK_V_LOW <= r.value <= UK_V_HIGH)
            if was and not out:
                out = not (UK_V_LOW + 0.5 <= r.value <= UK_V_HIGH - 0.5)
            m.volts_out[r.object_id] = out
            bad = any(m.volts_out.values())
            self._set_notice(m.cfg.id + ".volts", bad, 1,
                             f"{name}: voltage {r.value:.1f} V is outside the UK range ({UK_V_LOW:g} to {UK_V_HIGH:g} V)")

        elif r.field == "freq" and F_JUDGED[0] <= r.value <= F_JUDGED[1]:
            was = m.freq_level.get(r.object_id, 0)
            v = r.value
            level = 2 if not (UK_F_LOW <= v <= UK_F_HIGH) else 1 if not (UK_F_NEAR_LOW <= v <= UK_F_NEAR_HIGH) else 0
            if was and level < was:      # clear with a little margin (0.02 Hz) so the edge does not flicker
                if was == 2 and UK_F_LOW + 0.02 <= v <= UK_F_HIGH - 0.02:
                    level = max(level, 1)
                elif was == 1 and UK_F_NEAR_LOW + 0.02 <= v <= UK_F_NEAR_HIGH - 0.02:
                    level = 0
                else:
                    level = was
            m.freq_level[r.object_id] = level
            worst = max(m.freq_level.values(), default=0)
            word = "outside" if worst == 2 else "close to the limit of"
            self._set_notice(m.cfg.id + ".freq", worst > 0, max(worst, 1),
                             f"{m.cfg.name or 'Power meter'}: frequency {v:.2f} Hz is {word} the UK range ({UK_F_LOW:g} to {UK_F_HIGH:g} Hz)")

    def _record(self, m: _Meter, r: rd.Reading, eid: str, ts: float) -> None:
        self._day_figures(m, r, eid, ts)
        self._check_limits(m, r, ts)
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

    # ------------------------------------------------------- calculated neutral current
    def _fresh(self, eid: str, now: float) -> float | None:
        """An entity's value if it has one and it is not old; else None (never a stale or assumed number)."""
        e = self.hub.entities.get(eid)
        if e is None or e.value is None or e.is_stale(now, self.hub.stale_after_for(e)):
            return None
        return e.value

    def _neutral_circuits(self, now: float) -> list[nt.Circuit]:
        """Every single-phase circuit the admin put on a line, with its readings as of now. A circuit of a
        three-phase meter is left out: that meter has its own phases and (Pro 3EM) its own measured neutral."""
        out = []
        groups = self._derived.supply_groups()
        for cfg in self.hub.config.shelly_devices:
            for ch in cfg.channels:
                if ch.phase not in nt.LINES:
                    continue
                if any(e.device_id == cfg.id and e.labels.get("channel") == ch.key and e.labels.get("group") in ("a", "b", "c")
                       for e in self.hub.entities.values()):
                    continue
                base = f"{cfg.id}.{ch.key}_"
                rx = self.hub.entities.get(base + "reactive")
                out.append(nt.Circuit(ch.phase, self._fresh(base + "current", now), self._fresh(base + "power", now),
                                      self._fresh(base + "pf", now), self._fresh(base + "reactive", now),
                                      bool(rx is not None and rx.labels.get("calculated")), groups.get((cfg.id, ch.key), "")))
        return out

    def _update_neutral(self, ts: float) -> None:
        """Work out the neutral current implied by the circuits on L1, L2 and L3 (see neutral.py) and record it
        like any other reading, as "Neutral current (calculated)". Calculated, never measured. With no
        circuit on a line, or any circuit not reading, there is no figure (None): nothing is assumed or filled in.
        Does nothing at all until the admin has put a circuit on a line."""
        try:
            circuits = self._neutral_circuits(self._clock())   # age is judged against now, whatever the reading's own time
            if not circuits:
                if NEUTRAL_ID in self.hub.devices:
                    self.hub.remove_device(NEUTRAL_ID)
                self._neutral_last = None
                return
            result = nt.neutral_current(circuits)
            if NEUTRAL_ID not in self.hub.devices:
                dev = Device(NEUTRAL_ID, "Calculated neutral", MANIFEST.domain, "Stagewatch", "", "",
                             Status.OK, category="meter", role="equipment")
                self.hub.register_device(dev)
                self.hub.set_device_status(NEUTRAL_ID, Status.OK, "Calculated from the line currents")
            note = (f"Calculated from the line currents, not measured: {result.method}." if result.amps is not None
                    else f"No figure: {result.reason}.")
            self.hub.set_device_public(NEUTRAL_ID, {"calc_note": note})
            method = result.method or nt.METHOD_PHASORS
            ent = self.hub.entities.get(NEUTRAL_ENTITY)
            if ent is None or (result.method and ent.labels.get("calculated") != method):
                self.hub.register_entity(Entity(
                    NEUTRAL_ENTITY, NEUTRAL_ID, "Neutral current (calculated)", Kind.CURRENT, UNITS.get(Kind.CURRENT, "A"), 2,
                    labels={"channel": "neutral_calc", "field": "current", "group": "n", "calculated": method},
                    locked_equipment=True))
            value = result.amps
            prev = self._neutral_last
            beat = min(HEARTBEAT_S, self.hub.config.site.stale_after_s / 2)
            if prev is None or prev[0] != value or ts - prev[1] >= beat:
                self.hub.update_state(NEUTRAL_ENTITY, value, ts)
                self._neutral_last = (value, ts)
        except Exception:  # noqa: BLE001 - a calculation problem must never stop the meters
            log.exception("Shelly: could not work out the neutral current")

    # -------------------------------------------------------------------- admin
    async def _identify(self, host: str) -> tuple[DeviceInfo, dict]:
        """Ask a meter who it is and what it reads (the two allowed requests). Raises ShellyError."""
        ip = validate_host(host)
        info, gen1 = await identify(self._fetch, ip)
        if info.auth_enabled:
            raise ShellyError("needs_password")
        status = await read_status(self._fetch, ip, gen1)
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
            if device_id in ("site", SITE_ID) or device_id in self.hub.devices:
                raise InUse("That name is already in use. Choose another")
            cfg = ShellyDeviceConfig(id=device_id, host=validate_host(host), mac=info.mac, name=name.strip(),
                                     area=area.strip(), poll_interval_s=interval_s)
            self.hub.config.shelly_devices.append(cfg)
            self.hub.save_config()
            self._attach(cfg)
            self.identified(cfg.mac, info)
            return cfg

    async def update(self, device_id: str, name: str | None, area: str | None, interval_s: float | None,
                     host: str | None, rating_a: float | None = None,
                     channels: list[dict] | None = None, combined_bar: bool | None = None) -> None:
        async with self._lock:
            cfg = next((c for c in self.hub.config.shelly_devices if c.id == device_id), None)
            if cfg is None:
                raise LookupError
            new_host = cfg.host
            if host is not None:
                new_host = validate_host(host)
                if new_host != cfg.host:
                    info, _ = await identify(self._fetch, new_host)
                    if info.mac != cfg.mac:
                        raise ShellyError("other_meter")    # the saved meter is not at that address
            if name is not None:
                cfg.name = name.strip()
            if area is not None:
                cfg.area = area.strip()
            if interval_s is not None:
                cfg.poll_interval_s = interval_s
            if rating_a is not None:
                cfg.rating_a = rating_a or None      # 0 clears it
            if combined_bar is not None:
                cfg.combined_bar = combined_bar
            if channels is not None:
                seen, kept = set(), []
                old = {x.key: x for x in cfg.channels}
                for c in channels:
                    # The power tree fields (role, number, upstream) are edited on their own card: keep them.
                    prev = old.get(c.get("key"))
                    tree = {"role": prev.role, "unit_no": prev.unit_no, "upstream": prev.upstream} if prev else {}
                    ch = ShellyChannelConfig(**{**tree, **c, "rating_a": c.get("rating_a") or None})
                    if ch.key not in seen and (ch.label or ch.phase or ch.rating_a or ch.in_total or ch.role or ch.upstream):
                        seen.add(ch.key)
                        kept.append(ch)
                cfg.channels = kept
            cfg.host = new_host
            self.hub.save_config()
            m = self._meters.get(cfg.mac)
            dev = self.hub.devices.get(cfg.id)
            if dev is not None:
                self.hub.register_device(self._device(cfg, m.info if m else None, dev.status))
            self.hub.set_device_public(cfg.id, public_extra(cfg))
            self._transport.add(Target(cfg.mac, cfg.host, cfg.poll_interval_s))
            self._update_neutral(self._clock())

    def apply_warnings(self, device_id: str) -> int:
        """Admin button "Add warnings at 80 % and 100 %": for each circuit's current reading, two ordinary
        thresholds from the rating the admin typed in (level 1 at 80 %, level 2 at 100 %, held 5 s, 2 %
        hysteresis). Running it again replaces only the ones made here. Returns how many were made.
        Raises LookupError (no such meter) or ValueError (fixed text: no rating, or no reading yet)."""
        cfg = next((c for c in self.hub.config.shelly_devices if c.id == device_id), None)
        if cfg is None:
            raise LookupError
        currents = [e for e in self.hub.entities.values() if e.device_id == cfg.id
                    and e.labels.get("field") == "current" and e.labels.get("group", "") in ("", "a", "b", "c")]
        rated = [(e, channel_rating(cfg, e.labels.get("channel", ""))) for e in sorted(currents, key=lambda x: x.id)]
        rated = [(e, r) for e, r in rated if r]
        if not rated:
            raise ValueError("Enter a rating in amps and save first" if not currents else
                             "Enter the meter's rating in amps and save first")
        prefix = f"shelly_{cfg.id}_"
        keep = [t for t in self.hub.config.thresholds if not t.id.startswith(prefix)]
        made = []
        for e, rating in rated:
            tag = e.id.split(".", 1)[1]
            chan = next((c for c in cfg.channels if c.key == e.labels.get("channel")), None)
            who = f"{cfg.name} {chan.label}" if chan and chan.label else cfg.name
            for pct, level in ((80, 1), (100, 2)):
                made.append(Threshold(id=f"{prefix}{tag}_{pct}", entity=e.id, label=f"{who}: load {pct}% of {rating:g} A",
                                      above=round(rating * pct / 100, 3), level=level,
                                      hysteresis=round(rating * 0.02, 3), hold_s=5.0))
        self.hub.config.thresholds = keep + made
        self.hub.save_config()
        return len(made)

    async def remove(self, device_id: str) -> None:
        async with self._lock:
            cfg = next((c for c in self.hub.config.shelly_devices if c.id == device_id), None)
            if cfg is None:
                raise LookupError
            self.hub.config.thresholds = [t for t in self.hub.config.thresholds if not t.id.startswith(f"shelly_{cfg.id}_")]
            self._set_notice(cfg.id + ".volts", False, 0, "")
            self._set_notice(cfg.id + ".freq", False, 0, "")
            self._transport.remove(cfg.mac)
            self._meters.pop(cfg.mac, None)
            self.hub.config.shelly_devices.remove(cfg)
            # whatever was fed from this meter or its circuits has no upstream any more
            power_tree.detach_children(self.hub.config, power_tree.meter_ref(cfg.id))
            for ch in cfg.channels:
                power_tree.detach_children(self.hub.config, power_tree.circuit_ref(cfg.id, ch.key))
            self.hub.save_config()
            self.hub.remove_device(cfg.id)      # its history stays
            self._update_neutral(self._clock())

    def _admin_channels(self, cfg: ShellyDeviceConfig) -> list[dict]:
        """The circuits Stagewatch has seen on a meter, with what the admin typed about each."""
        seen: dict[str, bool] = {}
        for e in self.hub.entities.values():
            key = e.labels.get("channel") if e.device_id == cfg.id and not e.labels.get("derived") else None
            if key:
                seen[key] = seen.get(key, False) or e.labels.get("group", "") in ("a", "b", "c")
        out = []
        for i, (key, three) in enumerate(sorted(seen.items())):
            m = re.search(r"_(\d+)$", key)
            name = f"Channel {int(m.group(1)) + 1}" if (m and len(seen) > 1) else ("Three-phase" if three else "Circuit")
            c = next((x for x in cfg.channels if x.key == key), None)
            out.append({"key": key, "name": name, "three_phase": three, "label": c.label if c else "",
                        "phase": c.phase if c else "", "rating_a": c.rating_a if c else None,
                        "in_total": bool(c and c.in_total),
                        "role": c.role if c else "", "unit_no": c.unit_no if c else None,
                        "upstream": c.upstream if c else ""})
        return out

    def admin_status(self) -> dict:
        """Admin page only: addresses, MACs and firmware are here and nowhere public."""
        site = self.hub.config.site
        meters = []
        for cfg in self.hub.config.shelly_devices:
            m = self._meters.get(cfg.mac)
            dev = self.hub.devices.get(cfg.id)
            meters.append({
                "id": cfg.id, "name": cfg.name, "area": cfg.area, "host": cfg.host, "mac": cfg.mac,
                "interval_s": cfg.poll_interval_s, "rating_a": cfg.rating_a, "combined_bar": cfg.combined_bar,
                "role": cfg.role, "unit_no": cfg.unit_no, "upstream": cfg.upstream,
                "channels": self._admin_channels(cfg),
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
