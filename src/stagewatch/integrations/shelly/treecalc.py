"""Calculated figures for the power tree card: the unmonitored gap at each node, the monitored load of each
supply, the site total and the UPS loss. Pure: no network, no hub, no clock.

Everything follows "no assumed data": a node that is not reading, or a child that is unmetered or not
reading, gives no figure (the state is "incomplete"), never a partial sum shown as complete and never zero.
A negative gap beyond the meters' accuracy is a "mismatch" (probably a wrong Upstream, line or channel) and
is never shown as a negative load. Advisory and labelled "calculated".
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Meter accuracy allowance for the sign check: a gap may be this far below zero before it is a mismatch.
# Shelly meters are about 1 to 2 % of reading, and the parent and every child each add their own error.
TOLERANCE_PCT = 2.0         # % of (parent power + sum of children power)
TOLERANCE_W = 5.0           # plus this much per meter, for readings near zero
MAX_DEPTH = 12              # deeper than any real site; stops a runaway on bad data

GAP_OK, GAP_INCOMPLETE, GAP_MISMATCH = "ok", "incomplete", "mismatch"


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
        if gap < -tol:
            out[n.ref] = Gap(GAP_MISMATCH, None, n.power, total, len(ch),
                             "What is fed from it reads more than it does. Check the Upstream, the line and the channel.")
        else:
            out[n.ref] = Gap(GAP_OK, max(0.0, gap), n.power, total, len(ch))
    return out


def monitored(nodes: list[TNode], ref: str, kids: dict[str, list[TNode]] | None = None, _depth: int = 0) -> Total:
    """The monitored load of the subtree at ``ref``. A node that is metered and reading counts as its own
    reading (it already includes everything below). An unmetered node adds up its children. A silent meter,
    an unmetered leaf and an unmetered UPS (its losses are unknown) leave the total incomplete."""
    kids = kids if kids is not None else _kids(nodes)
    by_ref = {n.ref: n for n in nodes}
    n = by_ref[ref]
    if n.metered:
        if n.power is None:
            return Total(None, False, 1)
        return Total(n.power, True)
    ch = kids.get(ref, [])
    if not ch or _depth >= MAX_DEPTH:
        return Total(None, False, 1)
    parts = [monitored(nodes, c.ref, kids, _depth + 1) for c in ch]
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
    by_ref = {n.ref for n in nodes}
    r = Rollup()
    for n in nodes:
        if n.role == "source":
            r.sources[n.ref] = monitored(nodes, n.ref, kids)
    loose = [n for n in nodes if n.role != "source" and (not n.upstream or n.upstream not in by_ref)
             and _is_listed(n, kids)]
    if loose:
        parts = [monitored(nodes, n.ref, kids) for n in loose]
        vals = [p.watts for p in parts if p.watts is not None]
        r.unassigned = Total(sum(vals) if vals else None, all(p.complete for p in parts), sum(p.unmonitored for p in parts))
        r.unassigned_refs = [n.ref for n in loose]
    groups = list(r.sources.values()) + ([r.unassigned] if r.unassigned else [])
    if groups:
        vals = [g.watts for g in groups if g.watts is not None]
        r.site = Total(sum(vals) if vals else None, all(g.complete for g in groups), sum(g.unmonitored for g in groups))
    return r
