"""FOH advice: alignment drift per path and high-frequency air loss per position.

Advisory only, read only. Nothing here changes a delay, an EQ or any other setting; the numbers are
estimates from the air readings Stagewatch already collects (see ``acoustics`` for the physics).

* **Drift** (per path): how much later or earlier sound now arrives over the path's distance than
  at a reference marker ("Aligned"), and the delay that would match. Air comes from the site
  average, or from the Environment sensors of one area (each of temperature, humidity and
  pressure falls back to the site average on its own). Missing humidity counts as 50 % (flagged),
  missing pressure comes from the site altitude (flagged).
* **Air loss** (per position, FOH and Rear): the estimated loss at 4 kHz and above over the
  position's distance now, and the change since a reference marker ("Soundcheck"). Always the site
  average air. No humidity reading means no numbers (it would be a guess), missing pressure comes
  from the altitude (flagged). Positions are never compared with each other.

The reference marker is the newest marker (not in the future) of today's show whose label starts
with the reference text, ignoring case. There is no fallback to an earlier day. Conditions at the
marker are read back from the recorder (``Recorder.value_near``); nothing is back-filled.

The service keeps the resolved references until a marker, the show or the settings change, works
the numbers out on the hub's one-second tick, and publishes the bus topic ``advice`` when
something moved enough to matter (0.05 ms or 0.1 dB), at most every two seconds.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import time
from dataclasses import dataclass, field

from .. import acoustics
from .config import AdviceConfig, AdvicePath, AdvicePosition
from .derived import OUTLIER_LIMITS, robust_mean
from .model import Kind, Marker

log = logging.getLogger(__name__)

Air = tuple  # (temperature degC | None, humidity % | None, pressure Pa | None)

SITE_AIR = ("site.temperature", "site.humidity", "site.pressure")
QUANTITIES = (("t", Kind.TEMPERATURE), ("rh", Kind.HUMIDITY), ("p", Kind.PRESSURE))
NO_AIR: Air = (None, None, None)

RETRY_S = 10.0                 # a missing marker or missing readings are looked for again this often
READINGS_AFTER_S = 120.0       # a reading up to this long after the marker may stand in for one before it
PUBLISH_MIN_S = 2.0
DRIFT_PUBLISH_MS = 0.05
DB_PUBLISH = 0.1
DEMO_MARKER_DELAY_S = 90.0
DEMO_MARKER_LABELS = ("Soundcheck", "Aligned")

# Flags in the snapshot (codes, not wording; dashboards say them in plain words).
FLAG_HUMIDITY_ASSUMED = "humidity_assumed"       # drift: no humidity reading, 50 % used
FLAG_PRESSURE_ALTITUDE = "pressure_altitude"     # no pressure reading, estimated from the site altitude
FLAG_OUT_OF_RANGE = "out_of_range"               # outside the range the formula was checked for
FLAG_NEEDS_HUMIDITY = "needs_humidity"           # air loss: no humidity reading, no numbers
FLAG_NO_TEMPERATURE = "no_temperature"           # no air temperature reading, no numbers
FLAG_REFERENCE_INCOMPLETE = "reference_incomplete"   # the marker has no usable temperature or humidity


# ------------------------------------------------------------------ pure functions
def _num(v) -> float | None:
    """A finite number, else None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def find_reference_marker(markers: list[Marker], text: str, now: float) -> Marker | None:
    """The newest marker (``ts <= now``) whose label starts with ``text``, ignoring case and
    surrounding spaces; None if there is none. ``markers`` must be today's show only."""
    needle = (text or "").strip().casefold()
    if not needle:
        return None
    best = None
    for m in markers:
        if m.ts <= now and m.label.strip().casefold().startswith(needle):
            if best is None or (m.ts, m.id) > (best.ts, best.id):
                best = m
    return best


def drift_for(distance_m: float, ref: Air, now: Air, altitude_m: float) -> tuple[float | None, list[str]]:
    """``(drift_ms, flags)`` over ``distance_m`` since ``ref``. Positive means sound now arrives
    later. None without a temperature at either end. Humidity missing counts as 50 % and pressure
    missing as the altitude's standard pressure, both flagged."""
    if _num(ref[0]) is None or _num(now[0]) is None:
        return None, []
    flags: set[str] = set()
    conditions = []
    for air in (ref, now):
        t, rh, p = _num(air[0]), _num(air[1]), _num(air[2])
        if rh is None:
            rh = 50.0
            flags.add(FLAG_HUMIDITY_ASSUMED)
        if p is None:
            p = acoustics.pressure_at_altitude_pa(altitude_m)
            flags.add(FLAG_PRESSURE_ALTITUDE)
        if acoustics.speed_of_sound_range_issues(t, p):
            flags.add(FLAG_OUT_OF_RANGE)
        conditions.append((t, rh, p))
    c_ref, c_now = (acoustics.speed_of_sound(*c) for c in conditions)
    return acoustics.delay_drift_ms(distance_m, c_ref, c_now), sorted(flags)


def phase_deg(drift_ms: float | None, hz: float | None) -> float | None:
    """The drift as a phase angle at ``hz``, wrapped to -180..180 degrees. Display only."""
    if drift_ms is None or hz is None:
        return None
    deg = (360.0 * hz * drift_ms / 1000.0) % 360.0
    return deg - 360.0 if deg > 180.0 else deg


def air_bands(resolution: str, distance_m: float, now: Air, ref: Air | None,
              altitude_m: float) -> tuple[list[dict], list[str]]:
    """``(bands, flags)`` for one position: per graphic-EQ band above 4 kHz the estimated loss over
    ``distance_m`` now (``now_db``) and the change since the reference (``change_db``, positive =
    more top end lost). Numbers are None when they cannot be worked out honestly: no temperature,
    no humidity, or (for the change) no reference."""
    labels = (acoustics.AIR_LOSS_THIRD_BANDS if resolution == "third"
              else acoustics.AIR_LOSS_OCTAVE_BANDS)
    flags: set[str] = set()
    t, rh, p = _num(now[0]), _num(now[1]), _num(now[2])
    usable = t is not None and rh is not None
    if t is None:
        flags.add(FLAG_NO_TEMPERATURE)
    elif rh is None:
        flags.add(FLAG_NEEDS_HUMIDITY)
    now_c = ref_c = None
    if usable:
        if p is None:
            p = acoustics.pressure_at_altitude_pa(altitude_m)
            flags.add(FLAG_PRESSURE_ALTITUDE)
        now_c = (t, rh, p)
        if acoustics.iso_range_issues(*now_c):
            flags.add(FLAG_OUT_OF_RANGE)
    if ref is not None and usable:
        rt, rrh, rp = _num(ref[0]), _num(ref[1]), _num(ref[2])
        if rt is None or rrh is None:
            flags.add(FLAG_REFERENCE_INCOMPLETE)
        else:
            if rp is None:
                rp = acoustics.pressure_at_altitude_pa(altitude_m)
                flags.add(FLAG_PRESSURE_ALTITUDE)
            ref_c = (rt, rrh, rp)
            if acoustics.iso_range_issues(*ref_c):
                flags.add(FLAG_OUT_OF_RANGE)
    bands = []
    for label in labels:
        now_db = None if now_c is None else acoustics.air_loss_db(label, distance_m, *now_c)
        change = None if now_c is None or ref_c is None else \
            acoustics.air_loss_change_db(label, distance_m, ref_c, now_c)
        bands.append({"band": label, "hz_used": round(acoustics.ISO_MIDBAND_HZ[label], 2),
                      "now_db": now_db, "change_db": change})
    return bands, sorted(flags)


def _split(snap: dict) -> tuple[dict, dict]:
    """(shape, numbers): the snapshot with the numbers that move continuously taken out. Used to
    decide whether anything changed enough to publish."""
    shape = copy.deepcopy(snap)
    nums: dict = {}
    for ref in ("drift_ref", "air_ref"):
        if shape.get(ref):
            shape[ref]["delta"] = {k: v is None for k, v in shape[ref]["delta"].items()}
    for p in shape["paths"]:
        for key in ("drift_ms", "suggested_delay_ms"):
            nums[("path", p["id"], key)] = (p.pop(key), DRIFT_PUBLISH_MS)
        p["phase_deg"] = p["phase_deg"] is None
    for pos in shape["positions"]:
        for b in pos["bands"]:
            for key in ("now_db", "change_db"):
                nums[("pos", pos["id"], b["band"], key)] = (b.pop(key), DB_PUBLISH)
    return shape, nums


def advice_changed(old: dict | None, new: dict) -> bool:
    """True if ``new`` differs from the last published snapshot by a state, a flag, a missing or
    returning number, 0.05 ms of drift or 0.1 dB of loss."""
    if old is None:
        return True
    old_shape, old_nums = _split(old)
    new_shape, new_nums = _split(new)
    if old_shape != new_shape or old_nums.keys() != new_nums.keys():
        return True
    for key, (value, tol) in new_nums.items():
        before = old_nums[key][0]
        if (value is None) != (before is None):
            return True
        if value is not None and abs(value - before) >= tol:
            return True
    return False


def empty_advice(resolution: str = "octave") -> dict:
    return {"drift_ref": None, "air_ref": None, "resolution": resolution, "paths": [], "positions": []}


# ------------------------------------------------------------------ the service
@dataclass
class _Ref:
    """One resolved reference: the marker and the site air at its time. Kept until a marker, the
    show or the settings change."""
    show_id: int
    marker: Marker | None = None
    site: Air = NO_AIR
    complete: bool = False       # False: look again after ``retry_at`` (no marker yet, or no readings yet)
    retry_at: float = 0.0
    areas: dict = field(default_factory=dict)   # (area, entity ids) -> air at the marker, or None


class AdviceService:
    def __init__(self, hub) -> None:
        self.hub = hub
        self._refs: dict[str, _Ref] = {}
        self._last: dict | None = None
        self._published: dict | None = None
        self._published_at = 0.0
        self._demo_task: asyncio.Task | None = None
        for topic in ("marker", "marker_deleted", "show", "config"):
            hub.bus.subscribe(topic, self._invalidate)

    def _invalidate(self, _topic: str = "", _payload=None) -> None:
        self._refs.clear()
        self._last = None

    # ----------------------------------------------------------- the air
    def _site_air(self) -> tuple[Air, bool]:
        """(temperature, humidity, pressure) of the site average now (None = no value) and whether
        any of them is stale."""
        values, stale = [], False
        for entity_id in SITE_AIR:
            value, is_stale = self.hub.lookup(entity_id)
            value = _num(value)
            values.append(value)
            if value is not None and is_stale:
                stale = True
        return tuple(values), stale

    def _area_entities(self, area: str, now: float) -> list[list]:
        """Per quantity, the fresh, included Environment sensors of the nodes in ``area``."""
        key = area.strip().casefold()
        out = []
        for _q, kind in QUANTITIES:
            ents = [e for e in self.hub._env_inputs(kind, now)
                    if (d := self.hub.devices.get(e.device_id)) is not None
                    and d.area.strip().casefold() == key]
            out.append(sorted(ents, key=lambda e: e.id))
        return out

    def _limit(self, kind: Kind) -> float | None:
        return OUTLIER_LIMITS.get(kind.value) if self.hub.config.site.outlier_reject else None

    # ----------------------------------------------------------- references
    def _reference(self, text: str, now: float) -> _Ref:
        ref = self._refs.get(text)
        if ref is not None and (ref.complete or now < ref.retry_at):
            return ref
        rec = self.hub.recorder
        show_id = rec.show_id
        marker = find_reference_marker(rec.markers(show_id), text, now)
        if marker is None:
            ref = _Ref(show_id, retry_at=now + RETRY_S)
        else:
            site = tuple(_num(rec.value_near(e, marker.ts, show_id)) for e in SITE_AIR)
            ready = site[0] is not None and (None not in site or now > marker.ts + READINGS_AFTER_S)
            ref = _Ref(show_id, marker, site, ready, now + RETRY_S)
        self._refs[text] = ref
        return ref

    def _ref_dict(self, ref: _Ref, site_now: Air) -> dict | None:
        if ref.marker is None:
            return None

        def delta(i: int):
            a, b = ref.site[i], site_now[i]
            return None if a is None or b is None else b - a

        return {"label": ref.marker.label, "ts": ref.marker.ts, "marker_id": ref.marker.id,
                "has_readings": ref.site[0] is not None,
                "delta": {"t_c": delta(0), "rh": delta(1), "p_pa": delta(2)}}

    def _area_at_marker(self, ref: _Ref, area: str, ents: list[list]) -> Air:
        key = (area.strip().casefold(), tuple(tuple(e.id for e in q) for q in ents))
        if key not in ref.areas:
            rec = self.hub.recorder
            values = []
            for (_q, kind), q_ents in zip(QUANTITIES, ents):
                got = [_num(rec.value_near(e.id, ref.marker.ts, ref.show_id)) for e in q_ents]
                values.append(robust_mean([v for v in got if v is not None], self._limit(kind))[0])
            ref.areas[key] = tuple(values)
        return ref.areas[key]

    # ----------------------------------------------------------- the numbers
    def _path_air(self, path: AdvicePath, ref: _Ref, site_now: Air, now: float):
        """(reference air, current air, source, fallback quantities) for a drift path. Area
        values are used per quantity, and only when both the marker and now have one."""
        area = path.source_area.strip()
        if not area:
            return ref.site, site_now, "site", []
        ents = self._area_entities(area, now)
        area_now = tuple(robust_mean([e.value for e in q_ents], self._limit(kind))[0]
                         for (_q, kind), q_ents in zip(QUANTITIES, ents))
        area_ref = self._area_at_marker(ref, area, ents)
        ref_air, now_air, fallback = [], [], []
        for i, (q, _kind) in enumerate(QUANTITIES):
            if area_now[i] is not None and area_ref[i] is not None:
                ref_air.append(area_ref[i])
                now_air.append(area_now[i])
            else:
                ref_air.append(ref.site[i])
                now_air.append(site_now[i])
                fallback.append(q)
        return tuple(ref_air), tuple(now_air), \
            ("area" if len(fallback) < len(QUANTITIES) else "site"), fallback

    def _path(self, p: AdvicePath, ref: _Ref, site_now: Air, site_stale: bool,
              altitude: float, now: float) -> dict:
        drift, flags, source, fallback = None, [], "site", []
        if ref.marker is not None and ref.site[0] is not None:
            ref_air, now_air, source, fallback = self._path_air(p, ref, site_now, now)
            drift, flags = drift_for(p.distance_m, ref_air, now_air, altitude)
        suggested = None if drift is None or p.current_delay_ms is None else p.current_delay_ms + drift
        advisory = (drift is not None and p.drift_threshold_ms is not None
                    and abs(drift) >= p.drift_threshold_ms)
        return {"id": p.id, "name": p.name, "distance_m": p.distance_m,
                "source": source, "source_fallback": fallback,
                "drift_ms": drift, "suggested_delay_ms": suggested,
                "current_delay_ms": p.current_delay_ms, "threshold_ms": p.drift_threshold_ms,
                "phase_deg": phase_deg(drift, p.phase_hint_hz), "phase_hint_hz": p.phase_hint_hz,
                "level": "advisory" if advisory else "ok", "flags": flags,
                "stale": site_stale and (source != "area" or bool(fallback))}

    def _position(self, pos: AdvicePosition, resolution: str, ref: _Ref, site_now: Air,
                  site_stale: bool, altitude: float) -> dict:
        usable_ref = ref.site if ref.marker is not None and ref.site[0] is not None else None
        bands, flags = air_bands(resolution, pos.distance_m, site_now, usable_ref, altitude)
        return {"id": pos.id, "name": pos.name, "distance_m": pos.distance_m,
                "bands": bands, "flags": flags, "stale": site_stale}

    def compute(self, now: float) -> dict:
        cfg: AdviceConfig = self.hub.config.advice
        paths = [p for p in cfg.paths if p.enabled]
        positions = [p for p in cfg.positions if p.enabled]
        out = empty_advice(cfg.air_loss_resolution)
        if not paths and not positions:
            return out
        site_now, site_stale = self._site_air()
        altitude = self.hub.config.site.altitude_m
        if paths:
            ref = self._reference(cfg.drift_reference, now)
            out["drift_ref"] = self._ref_dict(ref, site_now)
            out["paths"] = [self._path(p, ref, site_now, site_stale, altitude, now) for p in paths]
        if positions:
            ref = self._reference(cfg.air_loss_reference, now)
            out["air_ref"] = self._ref_dict(ref, site_now)
            out["positions"] = [self._position(p, cfg.air_loss_resolution, ref, site_now,
                                               site_stale, altitude) for p in positions]
        return out

    # ----------------------------------------------------------- hub hooks
    def tick(self, now: float) -> None:
        """Once a second. Never raises: a failure is logged and the last numbers stay."""
        try:
            snap = self.compute(now)
        except Exception:  # noqa: BLE001 - never stop the hub tick over the advice
            log.exception("FOH advice failed")
            return
        self._last = snap
        if self._published is None and not snap["paths"] and not snap["positions"]:
            self._published = snap
            return
        if now - self._published_at >= PUBLISH_MIN_S and advice_changed(self._published, snap):
            self._published, self._published_at = snap, now
            self.hub.bus.publish("advice", copy.deepcopy(snap))

    def snapshot(self) -> dict:
        """The public ``advice`` block: drift paths and air-loss positions (names, distances and
        numbers; no device ids, addresses or settings)."""
        if self._last is None:
            try:
                self._last = self.compute(time.time())
            except Exception:  # noqa: BLE001 - never break the snapshot over the advice
                log.exception("FOH advice snapshot failed")
                return empty_advice(self.hub.config.advice.air_loss_resolution)
        return copy.deepcopy(self._last)

    # ----------------------------------------------------------- emulate
    def start_demo(self) -> None:
        """Emulate mode: with nothing set up, three drift paths and the FOH and Rear positions, and
        the "Soundcheck" and "Aligned" markers a little after start (once the site has readings)."""
        cfg = self.hub.config.advice
        if not cfg.paths and not cfg.positions:
            cfg.paths = [
                AdvicePath(id="delay_tower_1", name="Delay tower 1", distance_m=85,
                           source_area="Delay tower 1", drift_threshold_ms=0.2),
                AdvicePath(id="front_fill", name="Front fill", distance_m=8, drift_threshold_ms=0.2),
                AdvicePath(id="sub_to_tops", name="Sub to tops", distance_m=2.5, drift_threshold_ms=0.2),
            ]
            cfg.positions = [AdvicePosition(id="foh", name="FOH", distance_m=40),
                             AdvicePosition(id="rear", name="Rear", distance_m=90)]
            try:
                self.hub.save_config()
            except OSError:
                log.exception("Could not save the demo FOH advice settings")
        self._demo_task = asyncio.create_task(self._demo_markers(), name="advice-demo-markers")

    def demo_markers(self, now: float) -> list[Marker]:
        """Add the demo markers that today's show does not have yet, once the site has a reading."""
        if self.hub.lookup("site.temperature")[0] is None:
            return []
        have = self.hub.recorder.markers()
        return [self.hub.add_marker(label, "emulate", now) for label in DEMO_MARKER_LABELS
                if find_reference_marker(have, label, now) is None]

    async def _demo_markers(self) -> None:
        await asyncio.sleep(DEMO_MARKER_DELAY_S)
        for _ in range(60):   # wait up to five minutes more for the first site reading
            try:
                if self.hub.lookup("site.temperature")[0] is not None:
                    self.demo_markers(time.time())
                    return
            except Exception:  # noqa: BLE001 - a demo must never stop the hub
                log.exception("Could not add the demo advice markers")
                return
            await asyncio.sleep(5.0)

    async def stop(self) -> None:
        if self._demo_task is not None:
            self._demo_task.cancel()
            await asyncio.gather(self._demo_task, return_exceptions=True)
            self._demo_task = None
