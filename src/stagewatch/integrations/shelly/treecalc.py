"""Calculated figures for the power tree card: the unmonitored gap at each node, the monitored load of each
supply, the site total and the UPS loss. Pure: no network, no hub, no clock.

Everything follows "no assumed data": a node that is not reading, or a child that is unmetered or not
reading, gives no figure (the state is "incomplete"), never a partial sum shown as complete and never zero.
A negative gap beyond the meters' accuracy is a "mismatch" (probably a wrong Upstream, line or channel) and
is never shown as a negative load. Advisory and labelled "calculated".
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

# Meter accuracy allowance for the sign check: a gap may be this far below zero before it is a mismatch.
# Shelly meters are about 1 to 2 % of reading, and the parent and every child each add their own error.
TOLERANCE_PCT = 2.0         # % of (parent power + sum of children power)
TOLERANCE_W = 5.0           # plus this much per meter, for readings near zero
MAX_DEPTH = 12


def node_slug(ref: str) -> str:
    """A safe, collision-free piece of an entity id for a node reference (the browser makes the same one):
    the reference with ':' as '_', then the CRC-32 of the reference in eight hex digits."""
    return f"{ref.replace(':', '_')}_{zlib.crc32(ref.encode()) & 0xFFFFFFFF:08x}"


def supply_key(ref: str, info: dict[str, tuple[str, str]]) -> str:
    """The supply a node belongs to: the nearest unit above it that is a source or a UPS (a UPS output is
    its own supply), or "" when none. ``info`` maps ref -> (role, upstream)."""
    seen = set()
    cur = info.get(ref, ("", ""))[1]
    while cur and cur in info and cur not in seen:
        seen.add(cur)
        role, up = info[cur]
        if role in ("source", "ups"):
            return cur
        cur = up
    return ""              # deeper than any real site; stops a runaway on bad data

GAP_OK, GAP_INCOMPLETE, GAP_MISMATCH, GAP_BATTERY = "ok", "incomplete", "mismatch", "battery"
EFFICIENCY_MIN_W = 100.0   # a UPS efficiency figure needs at least this much going in (near zero it is only noise)


@dataclass(frozen=True)
class TNode:
    ref: str
    role: str = ""
    kind: str = "meter"          # "point" (no meter), "meter" or "circuit"
    upstream: str = ""
    power: float | None = None   # live watts now; None = not reading (old, offline or never received)

    @property
    def metered(self) -> bool:
        return self.kind != "point"


@dataclass(frozen=True)
class Gap:
    """Parent power minus the sum of its children's power."""
    state: str                   # GAP_*
    watts: float | None          # only with GAP_OK
    node_w: float | None = None
    children_w: float | None = None
    children: int = 0
    reason: str = ""             # crew words for incomplete / mismatch
    efficiency: float | None = None   # UPS only: output / input, %, when both are reading and the load is real


@dataclass(frozen=True)
class Total:
    watts: float | None          # monitored load; None when nothing is read
    complete: bool               # False if any meter below is not reading or any load below is unmetered
    unmonitored: int = 0         # how many unmetered leaves or silent meters left it incomplete


def _kids(nodes: list[TNode]) -> dict[str, list[TNode]]:
    out: dict[str, list[TNode]] = {}
    for n in nodes:
        if n.upstream:
            out.setdefault(n.upstream, []).append(n)
    return out


def gaps(nodes: list[TNode]) -> dict[str, Gap]:
    """The gap at every metered node that has children. A UPS node's gap is its loss (input minus output),
    the caller words it differently; the maths is the same."""
    kids = _kids(nodes)
    out: dict[str, Gap] = {}
    for n in nodes:
        ch = kids.get(n.ref, [])
        if not n.metered or not ch:
            continue
        if n.kind == "meter" and all(c.ref.startswith(f"c:{n.ref[2:]}:") for c in ch):
            continue    # a meter against its own circuits is always 0 W by construction: not a measurement
        if n.power is None:
            out[n.ref] = Gap(GAP_INCOMPLETE, None, None, None, len(ch), "This one is not reading.")
            continue
        unmetered = [c for c in ch if not c.metered]
        silent = [c for c in ch if c.metered and c.power is None]
        if unmetered or silent:
            why = "Something fed from it is not monitored." if unmetered else "Something fed from it is not reading."
            out[n.ref] = Gap(GAP_INCOMPLETE, None, n.power, None, len(ch), why)
            continue
        total = sum(c.power for c in ch)
        gap = n.power - total
        tol = TOLERANCE_PCT / 100 * (abs(n.power) + abs(total)) + TOLERANCE_W * (1 + len(ch))
        if gap < -tol and n.role == "ups":
            out[n.ref] = Gap(GAP_BATTERY, None, n.power, total, len(ch),
                             "It gives out more than it takes in. It may be running on its battery.")
        elif gap < -tol:
            out[n.ref] = Gap(GAP_MISMATCH, None, n.power, total, len(ch),
                             "What is fed from it reads more than it does. Check the Upstream, the line and the channel.")
        else:
            eff = 100 * total / n.power if n.role == "ups" and n.power >= EFFICIENCY_MIN_W else None
            out[n.ref] = Gap(GAP_OK, max(0.0, gap), n.power, total, len(ch), efficiency=eff)
    return out


def monitored(nodes: list[TNode], ref: str, kids: dict[str, list[TNode]] | None = None, _depth: int = 0,
              by_ref: dict[str, TNode] | None = None) -> Total:
    """The monitored load of the subtree at ``ref``. A node that is metered and reading counts as its own
    reading (it already includes everything below). An unmetered node adds up its children. A silent meter,
    an unmetered leaf and an unmetered UPS (its losses are unknown) leave the total incomplete."""
    kids = kids if kids is not None else _kids(nodes)
    by_ref = by_ref if by_ref is not None else {n.ref: n for n in nodes}
    n = by_ref[ref]
    if n.metered:
        if n.power is None:
            return Total(None, False, 1)
        return Total(n.power, True)
    ch = kids.get(ref, [])
    if not ch or _depth >= MAX_DEPTH:
        return Total(None, False, 1)
    parts = [monitored(nodes, c.ref, kids, _depth + 1, by_ref) for c in ch]
    vals = [p.watts for p in parts if p.watts is not None]
    bad = sum(p.unmonitored for p in parts)
    complete = all(p.complete for p in parts) and n.role != "ups"
    if n.role == "ups":
        bad += 1
    return Total(sum(vals) if vals else None, complete, bad)


def _is_listed(n: TNode, kids: dict[str, list[TNode]]) -> bool:
    """A circuit with no role of its own, nothing above and nothing below is not part of the tree."""
    return n.kind != "circuit" or bool(n.role or n.upstream or kids.get(n.ref))


@dataclass
class Rollup:
    sources: dict[str, Total] = field(default_factory=dict)   # source ref -> its monitored load
    unassigned: Total | None = None                           # meters with no supply above them
    site: Total | None = None                                 # power only, summed across the groups above
    unassigned_refs: list[str] = field(default_factory=list)


def rollup(nodes: list[TNode]) -> Rollup:
    """Per-supply totals, an Unassigned group, and the site total (watts only: amps per line are never added
    across supplies). The site total is incomplete if any supply or the unassigned group is."""
    kids = _kids(nodes)
    node_by = {n.ref: n for n in nodes}
    r = Rollup()
    for n in nodes:
        if n.role == "source":
            r.sources[n.ref] = monitored(nodes, n.ref, kids, 0, node_by)
    loose = [n for n in nodes if n.role != "source" and (not n.upstream or n.upstream not in node_by)
             and _is_listed(n, kids)]
    # A circuit's power is counted once, through its own meter. A loose meter with some circuits placed under a
    # supply is replaced by its circuits that are not placed; a loose circuit whose meter is counted is dropped.
    placed_meters = {f"m:{n.ref.split(':')[1]}" for n in nodes if n.kind == "circuit" and n.upstream in node_by}
    swap = [m for m in loose if m.ref in placed_meters]
    extra = [n for n in nodes if n.kind == "circuit" and not (n.upstream in node_by)
             and f"m:{n.ref.split(':')[1]}" in {m.ref for m in swap}]
    loose = [n for n in loose if n not in swap]
    loose = [n for n in loose if not (n.kind == "circuit" and f"m:{n.ref.split(':')[1]}" in node_by
                                      and f"m:{n.ref.split(':')[1]}" not in {m.ref for m in swap})]
    loose += [n for n in extra if n not in loose]
    if loose:
        parts = [monitored(nodes, n.ref, kids, 0, node_by) for n in loose]
        vals = [p.watts for p in parts if p.watts is not None]
        r.unassigned = Total(sum(vals) if vals else None, all(p.complete for p in parts), sum(p.unmonitored for p in parts))
        r.unassigned_refs = [n.ref for n in loose]
    # A supply placed under another supply is already counted inside its upstream's total: only top-level supplies
    # (no upstream in the tree) go into the site total, so nothing is counted twice.
    top = [r.sources[n.ref] for n in nodes if n.role == "source" and (not n.upstream or n.upstream not in node_by)]
    groups = top + ([r.unassigned] if r.unassigned else [])
    if groups:
        vals = [g.watts for g in groups if g.watts is not None]
        r.site = Total(sum(vals) if vals else None, all(g.complete for g in groups), sum(g.unmonitored for g in groups))
    return r
