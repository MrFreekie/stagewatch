"""Calculated figures that need more than one reading: a meter's total load and its demand, the lines of the
site (circuits assigned to L1/L2/L3) with their unbalance, the site total with its demand, and the generator
suggestion. The sums and rules are in calc.py; this file keeps the running state and records the results as
entities, so they have history like any other reading.

* Every figure is all-or-nothing: if any reading it needs is missing, old, or from a meter that is not
  answering, it has no value. Nothing is filled in.
* Entities that belong to no meter sit on the pseudo-device ``power_site`` ("Site power (calculated)").
* After a restart the demand is rebuilt from the load readings already recorded for the show day.
"""

from __future__ import annotations

import logging

from ...core import power_tree, sitetime
from ...core.model import UNITS, Device, Entity, Kind, Status
from . import calc, treecalc

log = logging.getLogger(__name__)

SITE_ID = "power_site"
SITE_NAME = "Site power (calculated)"
HEARTBEAT_S = 10.0
LINES = ("L1", "L2", "L3")


class DerivedPower:
    def __init__(self, integ) -> None:
        self.integ = integ
        self.hub = integ.hub
        self._last: dict = {}        # entity id -> (value, recorded at)
        self._demand: dict = {}      # entity id of the load series -> (day, Demand)

    # ------------------------------------------------------------------ plumbing
    def _put(self, eid: str, name: str, kind: Kind, unit: str, dec: int, value, ts: float, device_id: str,
             labels: dict, day: dict | None = None, keep_empty: bool = False) -> None:
        """Register (once) and record one calculated entity. An unchanged value is re-recorded every 10 s.
        ``keep_empty`` registers it even with no value, for figures whose reason for being empty is shown."""
        if eid not in self.hub.entities:
            if value is None and not keep_empty:
                return
            self.hub.register_entity(Entity(eid, device_id, name, kind, unit, dec, labels={**labels, "derived": "1"},
                                            locked_equipment=True))
        ent = self.hub.entities.get(eid)
        if ent is None:
            return
        ent.day = day
        prev = self._last.get(eid)
        if prev is None or prev[0] != value or ts - prev[1] >= min(HEARTBEAT_S, self.hub.config.site.stale_after_s / 2):
            self.hub.update_state(eid, value, ts)
            self._last[eid] = (value, ts)

    def _tracker(self, load_eid: str, ts: float, gap_s: float) -> tuple[str, calc.Demand]:
        """The demand tracker for a load series: a new one each show day, rebuilt from what was recorded."""
        site = self.hub.config.site
        day = sitetime.show_day(ts, site).isoformat()
        window_s = self.hub.config.power_site.demand_window_min * 60
        cur = self._demand.get(load_eid)
        if cur is None or cur[0] != day or cur[1].window_s != window_s or cur[1].gap_s != gap_s:
            d = calc.Demand(window_s, gap_s)
            try:
                for t, v in self.hub.recorder.raw_states(load_eid, sitetime.resolve(day, site.day_rollover, site), ts):
                    d.feed(v, t)
            except Exception:  # noqa: BLE001 - no history just means the day starts from this reading
                log.exception("Shelly: could not read the day so far for demand")
            cur = self._demand[load_eid] = (day, d)
        return cur

    def _series(self, eid_base: str, device_id: str, label: str, kind: Kind, unit: str, dec: int,
                value, ts: float, gap_s: float, channel: bool = True) -> None:
        """Record a load series (``<base>_load``, hidden from cards and graphs) and its demand (``<base>_demand``)."""
        load_eid, dem_eid = f"{eid_base}_load", f"{eid_base}_demand"
        day, tr = self._tracker(load_eid, ts, gap_s)
        # The load first, so a restart finds it in the history; the tracker is fed from the same value.
        self._put(load_eid, f"{label} (calculated)", kind, unit, dec, value, ts, device_id, {"field": "load"})
        if value is not None:
            tr.feed(value, ts)
        self._put(dem_eid, f"{label} demand (calculated)", kind, unit, dec, tr.avg if value is not None else None, ts,
                  device_id, {"channel": "calc", "field": "demand" if kind == Kind.POWER else "demand_va", "calculated": "average over the window"},
                  tr.to_dict(day))

    # ------------------------------------------------------------------ one meter
    @staticmethod
    def _load(found: list, field: str) -> float | None:
        """A meter's total of one field (power or apparent) over its circuits: the meter's own total group when
        it has one, else the circuit. None (no sum) if any circuit has no value."""
        chans: dict = {}
        for r in found:
            if r.field == field:
                chans.setdefault(r.channel, {})[r.group] = r.value
        if not chans:
            return None
        total = 0.0
        for groups in chans.values():
            v = groups.get("total", groups.get(""))
            if v is None:
                return None
            total += v
        return total

    def meter_polled(self, m, found: list, ts: float) -> None:
        """Called after a good reading of a meter: its total load and demand, its three-phase unbalance."""
        cfg = m.cfg
        gap_s = calc.gap_seconds(cfg.poll_interval_s)
        name = cfg.name or "Power meter"
        self._series(f"{cfg.id}.meter_power", cfg.id, name, Kind.POWER, "W", 1, self._load(found, "power"), ts, gap_s)
        self._series(f"{cfg.id}.meter_apparent", cfg.id, name, Kind.APPARENT_POWER, "VA", 1, self._load(found, "apparent"), ts, gap_s)
        cur = {(r.channel, r.group): r.value for r in found if r.field == "current"}
        for ch in sorted({c for c, g in cur if g == "a"}):
            u = calc.unbalance(cur.get((ch, "a")), cur.get((ch, "b")), cur.get((ch, "c")))
            self._put(f"{cfg.id}.{ch}_unbalance", f"{name} phase unbalance (calculated)", Kind.GENERIC, "%", 1,
                      u["pct"] if u else None, ts, cfg.id,
                      {"channel": "calc", "field": "unbalance", "calculated": "NEMA MG 1 current unbalance"}, u)
        self.site_polled(ts)
        self.tree_polled(ts)

    # ------------------------------------------------------------------ the power tree
    def _ensure_site(self) -> None:
        if SITE_ID not in self.hub.devices:
            dev = Device(SITE_ID, SITE_NAME, "shelly", "Stagewatch", "", "", Status.OK, category="meter", role="equipment")
            dev.public = {"derived": True}
            self.hub.register_device(dev)

    def _node_power(self, cfg, node: power_tree.Node, ts: float) -> float | None:
        """A tree node's live watts: a meter's total, or one circuit's power. None when not reading."""
        if node.kind == "meter":
            return self._live(f"{node.ref[2:]}.meter_power_load", ts)
        if node.kind == "circuit":
            dev_id, _, key = node.ref[2:].partition(":")
            dev = next((d for d in cfg.shelly_devices if d.id == dev_id), None)
            return None if dev is None else self._circuit(dev, key, "power", ts)
        return None

    def tree_polled(self, ts: float) -> None:
        """The power tree figures (treecalc.py): the gap at each node, the monitored load of each supply, the
        unassigned group and the site total. Only when the tree is in use (any role or upstream is set)."""
        cfg = self.hub.config
        nodes = power_tree.build_nodes(cfg)
        if not any(n.role or n.upstream for n in nodes):
            return
        tn = [treecalc.TNode(n.ref, n.role, n.kind, n.upstream, self._node_power(cfg, n, ts)) for n in nodes]
        names = {n.ref: power_tree.display_name(n.role, n.unit_no) or n.label or "Node" for n in nodes}
        self._ensure_site()
        slug = lambda ref: ref.replace(":", "_")   # noqa: E731
        for ref, g in treecalc.gaps(tn).items():
            ups = next(n.role for n in nodes if n.ref == ref) == "ups"
            what = "loss and charging" if ups else "unmonitored load"
            self._put(f"{SITE_ID}.treegap_{slug(ref)}", f"{names[ref]} {what} (calculated)", Kind.POWER, "W", 1, g.watts, ts, SITE_ID,
                      {"field": "tree_gap", "calculated": "node power minus the power of what it feeds", "ref": ref},
                      {"ref": ref, "state": g.state, "node_w": g.node_w, "children_w": g.children_w, "children": g.children,
                       "reason": g.reason, "ups": ups}, keep_empty=True)
        r = treecalc.rollup(tn)
        def total(eid: str, name: str, t: treecalc.Total | None, ref: str) -> None:
            if t is not None:
                self._put(f"{SITE_ID}.{eid}", name, Kind.POWER, "W", 1, t.watts, ts, SITE_ID,
                          {"field": "tree_total", "calculated": "monitored load only", "ref": ref},
                          {"ref": ref, "complete": t.complete, "unmonitored": t.unmonitored}, keep_empty=True)
        for ref, t in r.sources.items():
            total(f"treesrc_{slug(ref)}", f"{names[ref]} monitored load (calculated)", t, ref)
        total("tree_unassigned", "Unassigned monitored load (calculated)", r.unassigned, "")
        total("tree_site", "Site monitored load (calculated)", r.site, "")

    # ------------------------------------------------------------------ the site
    def _live(self, eid: str, ts: float) -> float | None:
        e = self.hub.entities.get(eid)
        if e is None or e.value is None or e.is_stale(ts, self.hub.config.site.stale_after_s):
            return None
        dev = self.hub.devices.get(e.device_id)
        return e.value if dev is not None and dev.status == Status.OK else None

    def _circuit(self, cfg, key: str, what: str, ts: float) -> float | None:
        """A circuit's live power / apparent / current: its own reading, else the meter's total group."""
        for eid in (f"{cfg.id}.{key}_{what}", f"{cfg.id}.{key}_total_{what}"):
            if eid in self.hub.entities:
                return self._live(eid, ts)
        return None

    def site_polled(self, ts: float) -> None:
        cfgs = self.hub.config.shelly_devices
        ticked = [(c, ch.key) for c in cfgs for ch in c.channels if ch.in_total]
        if not ticked and not any(ch.phase for c in cfgs for ch in c.channels):
            return
        if SITE_ID not in self.hub.devices:
            dev = Device(SITE_ID, SITE_NAME, "shelly", "Stagewatch", "", "", Status.OK, category="meter", role="equipment")
            dev.public = {"derived": True}
            self.hub.register_device(dev)
        gap_s = calc.gap_seconds(max((c.poll_interval_s for c in cfgs), default=3.0))
        if ticked:
            for what, kind, unit in (("power", Kind.POWER, "W"), ("apparent", Kind.APPARENT_POWER, "VA")):
                vals = [self._circuit(c, k, what, ts) for c, k in ticked]
                total = None if any(v is None for v in vals) else sum(vals)
                self._series(f"{SITE_ID}.site_{what}", SITE_ID, "Site " + ("power" if what == "power" else "apparent power"),
                             kind, unit, 1, total, ts, gap_s)
            self._generator(ts)
        # Lines: the amps of the single-phase circuits assigned to each line (all-or-nothing per line).
        amps = {}
        for i, line in enumerate(LINES):
            members = [(c, ch.key) for c in cfgs for ch in c.channels if ch.phase == line]
            vals = [self._circuit(c, k, "current", ts) for c, k in members]
            amps[line] = None if not members or any(v is None for v in vals) else sum(vals)
            self._put(f"{SITE_ID}.line_{i + 1}_current", f"Line {i + 1} current (calculated)", Kind.CURRENT, "A", 3, amps[line], ts,
                      SITE_ID, {"field": "line_current", "calculated": "sum of the circuits set to this line"})
        if any(ch.phase for c in cfgs for ch in c.channels):
            u = calc.unbalance(*(amps[l] for l in LINES))
            self._put(f"{SITE_ID}.unbalance", "Site phase unbalance (calculated)", Kind.GENERIC, "%", 1, u["pct"] if u else None, ts,
                      SITE_ID, {"channel": "calc", "field": "unbalance", "calculated": "NEMA MG 1 current unbalance"}, u)

    def _generator(self, ts: float) -> None:
        """The generator suggestion from this show day's peak site demand in VA (see calc.generator_suggestion)."""
        ps = self.hub.config.power_site
        cur = self._demand.get(f"{SITE_ID}.site_apparent_load")
        va = self.hub.entities.get(f"{SITE_ID}.site_apparent_load")
        sug = None
        if cur is not None:
            tr = cur[1]
            sug = calc.generator_suggestion(tr.peak, ps.gen_loading_pct, ps.gen_power_factor, ps.gen_size_kva,
                                            self._live(f"{SITE_ID}.site_apparent_load", ts) if va else None)
            if sug is not None:
                sug.update(day=cur[0], window_min=tr.window_s / 60, peak_incomplete=tr.peak_incomplete)
        self._put(f"{SITE_ID}.generator", "Suggested generator size (calculated)", Kind.GENERIC, "kVA", 1,
                  sug["min_kva"] if sug else None, ts, SITE_ID,
                  {"field": "generator", "calculated": "peak demand / loading limit"}, sug)
