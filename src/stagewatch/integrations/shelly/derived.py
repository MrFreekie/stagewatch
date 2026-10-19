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
from .replay import Rebuild

log = logging.getLogger(__name__)

SITE_ID = "power_site"
SITE_NAME = "Site power (calculated)"
HEARTBEAT_S = 10.0
TREE_PERIOD_S = 2.0       # the power tree is worked out at most this often
LINES = ("L1", "L2", "L3")


class DerivedPower:
    def __init__(self, integ) -> None:
        self.integ = integ
        self.hub = integ.hub
        self._last: dict = {}        # entity id -> (value, recorded at, signature)
        self._tree_at = -1e18        # when the power tree was last worked out
        self._demand: dict = {}      # entity id of the load series -> (day, Demand)
        self._rebuilds: dict = {}    # entity id of the load series -> Rebuild, while the day so far is being read back

    # ------------------------------------------------------------------ plumbing
    def _put(self, eid: str, name: str, kind: Kind, unit: str, dec: int, value, ts: float, device_id: str,
             labels: dict, day: dict | None = None, keep_empty: bool = False, step: float = 0.0, sig=None) -> None:
        """Register (once) and record one calculated entity. An unchanged value is re-recorded every 10 s.
        ``keep_empty`` registers it even with no value, for figures whose reason for being empty is shown.
        ``step``: a change smaller than this is not recorded before the heartbeat; ``sig``: a change of it always is."""
        if eid not in self.hub.entities:
            if value is None and not keep_empty:
                return
            self.hub.register_entity(Entity(eid, device_id, name, kind, unit, dec, labels={**labels, "derived": "1"},
                                            locked_equipment=True))
        ent = self.hub.entities.get(eid)
        if ent is None:
            return
        ent.day = day
        if ent.name != name:
            ent.name = name
        prev = self._last.get(eid)
        if prev is None or prev[2] != sig or (prev[0] is None) != (value is None):
            moved = True
        elif value is None:
            moved = False
        else:
            moved = abs(value - prev[0]) >= step if step > 0 else value != prev[0]
        if moved or ts - prev[1] >= min(HEARTBEAT_S, self.hub.config.site.stale_after_s / 2):
            self.hub.update_state(eid, value, ts)
            self._last[eid] = (value, ts, sig)

    def _tracker(self, load_eid: str, ts: float, gap_s: float) -> tuple[str, calc.Demand]:
        """The demand tracker for a load series: a new one each show day, rebuilt from what was recorded."""
        site = self.hub.config.site
        day = sitetime.show_day(ts, site).isoformat()
        window_s = self.hub.config.power_site.demand_window_min * 60
        cur = self._demand.get(load_eid)
        if cur is None or cur[0] != day or cur[1].window_s != window_s or cur[1].gap_s != gap_s:
            d = calc.Demand(window_s, gap_s)
            self._rebuilds[load_eid] = Rebuild(self.hub.recorder, load_eid, lambda: sitetime.resolve(day, site.day_rollover, site), ts, d)
            cur = self._demand[load_eid] = (day, d)
        return cur

    def ready_demand(self, load_eid: str):
        """(day, Demand) of a load series, or None while its day so far is still being read back (no figure yet)."""
        cur = self._demand.get(load_eid)
        rb = self._rebuilds.get(load_eid)
        if cur is None or (rb is not None and not rb.ready):
            return None
        self._rebuilds.pop(load_eid, None)
        return cur

    def _series(self, eid_base: str, device_id: str, label: str, kind: Kind, unit: str, dec: int,
                value, ts: float, gap_s: float, channel: bool = True) -> None:
        """Record a load series (``<base>_load``, hidden from cards and graphs) and its demand (``<base>_demand``)."""
        load_eid, dem_eid = f"{eid_base}_load", f"{eid_base}_demand"
        day, tr = self._tracker(load_eid, ts, gap_s)
        # The load first, so a restart finds it in the history; the tracker is fed from the same value.
        self._put(load_eid, f"{label} (calculated)", kind, unit, dec, value, ts, device_id, {"field": "load"})
        rb = self._rebuilds.get(load_eid)
        if value is not None:
            (rb or tr).feed(value, ts)
        ready = self.ready_demand(load_eid) is not None
        self._put(dem_eid, f"{label} demand (calculated)", kind, unit, dec, tr.avg if value is not None and ready else None, ts,
                  device_id, {"channel": "calc", "field": "demand" if kind == Kind.POWER else "demand_va", "calculated": "average over the window"},
                  tr.to_dict(day) if ready else None)

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
                      {"channel": "calc", "field": "unbalance", "calculated": "NEMA MG 1 voltage-unbalance method applied to current"}, u)
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
        if ts - self._tree_at < TREE_PERIOD_S:
            return      # at most every couple of seconds, however many meters report
        self._tree_at = ts
        cfg = self.hub.config
        nodes = power_tree.build_nodes(cfg)
        live: set[str] = set()
        if any(n.role or n.upstream for n in nodes):
            self._tree_figures(cfg, nodes, ts, live)
        # entities of nodes that have left the tree (renamed ids, deleted points) are removed, not left stale
        for eid in [k for k, e in self.hub.entities.items() if k.startswith(SITE_ID + ".tree")
                    and e.labels.get("field") in ("tree_gap", "tree_total") and k not in live]:
            self.hub.remove_entity(eid)
            self._last.pop(eid, None)

    def _tree_figures(self, cfg, nodes: list, ts: float, live: set) -> None:
        tn = [treecalc.TNode(n.ref, n.role, n.kind, n.upstream, self._node_power(cfg, n, ts)) for n in nodes]
        names = {n.ref: power_tree.display_name(n.role, n.unit_no) or n.label or "Node" for n in nodes}
        roles = {n.ref: n.role for n in nodes}
        self._ensure_site()
        step = lambda v: max(5.0, 0.01 * abs(v)) if v is not None else 0.0   # noqa: E731 - W: ignore tiny wobble between beats
        for ref, g in treecalc.gaps(tn).items():
            ups = roles[ref] == "ups"
            what = "loss and charging" if ups else "unmonitored load"
            eid = f"{SITE_ID}.treegap_{treecalc.node_slug(ref)}"
            live.add(eid)
            self._put(eid, f"{names[ref]} {what} (calculated)", Kind.POWER, "W", 1, g.watts, ts, SITE_ID,
                      {"field": "tree_gap", "calculated": "node power minus the power of what it feeds", "ref": ref},
                      {"ref": ref, "state": g.state, "node_w": g.node_w, "children_w": g.children_w, "children": g.children,
                       "reason": g.reason, "ups": ups, "efficiency": g.efficiency}, keep_empty=True, step=step(g.watts), sig=g.state)
        r = treecalc.rollup(tn)

        def total(eid: str, name: str, t: treecalc.Total | None, ref: str) -> None:
            if t is None:
                return
            live.add(f"{SITE_ID}.{eid}")
            # An incomplete total is never recorded as a figure (a graph would show a false drop): its value is
            # empty and the monitored part rides along for the card to show, marked incomplete.
            value = t.watts if t.complete else None
            self._put(f"{SITE_ID}.{eid}", name, Kind.POWER, "W", 1, value, ts, SITE_ID,
                      {"field": "tree_total", "calculated": "monitored load only", "ref": ref},
                      {"ref": ref, "complete": t.complete, "unmonitored": t.unmonitored,
                       "partial_w": None if t.complete else t.watts}, keep_empty=True, step=step(value), sig=t.complete)
        for ref, t in r.sources.items():
            total(f"treesrc_{treecalc.node_slug(ref)}", f"{names[ref]} monitored load (calculated)", t, ref)
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
        # Which supply each line-assigned circuit is on. Amps are never added across supplies (or across a
        # UPS output, which is its own supply): with more than one, there is no site-wide line figure and each
        # supply gets its own. With no tree set up everything is one group, as before.
        groups = self.supply_groups()
        multi = len({g for g in groups.values()}) > 1
        lines_known = all(any(ch.phase == l for c in cfgs for ch in c.channels) for l in LINES)
        if ticked:
            line_peaks = None
            if lines_known and not multi:
                for i, line in enumerate(LINES):
                    mem = [(c, ch.key) for c in cfgs for ch in c.channels if ch.phase == line]
                    vals = [self._circuit(c, k, "apparent", ts) for c, k in mem]
                    self._series(f"{SITE_ID}.site_line_{i + 1}_apparent", SITE_ID, f"Line {i + 1} apparent power",
                                 Kind.APPARENT_POWER, "VA", 1, None if any(v is None for v in vals) else sum(vals), ts, gap_s)
                peaks = [self.ready_demand(f"{SITE_ID}.site_line_{i + 1}_apparent_load") for i in range(3)]
                if all(p is not None and p[1].peak is not None for p in peaks):
                    line_peaks = max(p[1].peak for p in peaks)
            self._generator(ts, line_peaks)
        # Lines: the amps of the single-phase circuits assigned to each line (all-or-nothing per line).
        amps = {}
        for i, line in enumerate(LINES):
            members = [(c, ch.key) for c in cfgs for ch in c.channels if ch.phase == line]
            vals = [self._circuit(c, k, "current", ts) for c, k in members]
            amps[line] = None if not members or multi or any(v is None for v in vals) else sum(vals)
            self._put(f"{SITE_ID}.line_{i + 1}_current", f"Line {i + 1} current (calculated)", Kind.CURRENT, "A", 3, amps[line], ts,
                      SITE_ID, {"field": "line_current", "calculated": "sum of the circuits set to this line"})
        if multi:
            self._supply_lines(cfgs, groups, ts)
        if any(ch.phase for c in cfgs for ch in c.channels):
            u = calc.unbalance(*(amps[l] for l in LINES))
            self._put(f"{SITE_ID}.unbalance", "Site phase unbalance (calculated)", Kind.GENERIC, "%", 1, u["pct"] if u else None, ts,
                      SITE_ID, {"channel": "calc", "field": "unbalance", "calculated": "NEMA MG 1 voltage-unbalance method applied to current"}, u)

    def supply_groups(self) -> dict:
        """(device id, circuit key) -> the supply it is on, for every circuit assigned to a line ("" when the
        tree does not place it; everything is "" until a tree is set up)."""
        cfg = self.hub.config
        nodes = power_tree.build_nodes(cfg)
        info = {n.ref: (n.role, n.upstream) for n in nodes}
        out = {}
        for c in cfg.shelly_devices:
            for ch in c.channels:
                if not ch.phase:
                    continue
                cref = power_tree.circuit_ref(c.id, ch.key)
                key = treecalc.supply_key(cref, info) if ch.upstream else treecalc.supply_key(power_tree.meter_ref(c.id), info)
                out[(c.id, ch.key)] = key
        return out

    def _supply_lines(self, cfgs, groups: dict, ts: float) -> None:
        """Per supply: the amps on each line and the unbalance (never added across supplies)."""
        names = {n.ref: power_tree.display_name(n.role, n.unit_no) or n.label or "Supply" for n in power_tree.build_nodes(self.hub.config)}
        for key in sorted(set(groups.values())):
            slug = treecalc.node_slug(key) if key else "unplaced"
            who = names.get(key, "Not on a supply yet")
            amps = {}
            for i, line in enumerate(LINES):
                mem = [(c, ch.key) for c in cfgs for ch in c.channels if ch.phase == line and groups.get((c.id, ch.key)) == key]
                vals = [self._circuit(c, k, "current", ts) for c, k in mem]
                amps[line] = None if not mem or any(v is None for v in vals) else sum(vals)
                self._put(f"{SITE_ID}.supply_{slug}_line_{i + 1}_current", f"{who} line {i + 1} current (calculated)", Kind.CURRENT, "A", 3,
                          amps[line], ts, SITE_ID, {"field": "supply_line_current", "calculated": "sum of the circuits on this supply and line", "ref": key})
            u = calc.unbalance(*(amps[l] for l in LINES))
            self._put(f"{SITE_ID}.supply_{slug}_unbalance", f"{who} phase unbalance (calculated)", Kind.GENERIC, "%", 1,
                      u["pct"] if u else None, ts, SITE_ID,
                      {"field": "supply_unbalance", "calculated": "NEMA MG 1 voltage-unbalance method applied to current", "ref": key}, u)

    def _generator(self, ts: float, line_peak_va: float | None = None) -> None:
        """The generator suggestion from this show day's peak site demand in VA (see calc.generator_suggestion)."""
        ps = self.hub.config.power_site
        cur = self.ready_demand(f"{SITE_ID}.site_apparent_load")
        va = self.hub.entities.get(f"{SITE_ID}.site_apparent_load")
        sug = None
        if cur is not None:
            tr = cur[1]
            sug = calc.generator_suggestion(tr.peak, ps.gen_loading_pct, ps.gen_power_factor, ps.gen_size_kva,
                                            self._live(f"{SITE_ID}.site_apparent_load", ts) if va else None,
                                            line_peak_va=line_peak_va)
            if sug is not None:
                sug.update(day=cur[0], window_min=tr.window_s / 60, peak_incomplete=tr.peak_incomplete)
        self._put(f"{SITE_ID}.generator", "Suggested generator size (calculated)", Kind.GENERIC, "kVA", 1,
                  sug["min_kva"] if sug else None, ts, SITE_ID,
                  {"field": "generator", "calculated": "peak demand / loading limit"}, sug)
