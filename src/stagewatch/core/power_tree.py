"""Power distribution tree: roles, display names and the rules for who may feed whom.

Step 1 only: the data model and its checks. Nothing here reads a meter or does any power maths.

Everything is pure (no I/O, no config import) so it can be unit tested by hand-worked cases.

A node is a distribution point (a virtual node for a generator, grid supply or intake unit that has no
meter), a Shelly meter, or one circuit of a meter. Each node has a role, a number and one upstream node.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace

log = logging.getLogger("stagewatch.power_tree")

# --------------------------------------------------------------------------- roles
# Stable keys are what is saved. Labels and abbreviations are shown only, and can be renamed without a
# data change. The owner confirmed (19/10/2026) the terms ISU intake switch unit, CDU central distribution
# unit and FDU final distribution unit; the words live in this one table.
ROLE_TABLE: dict[str, tuple[str, str]] = {
    # key: (plain-English label, abbreviation used in the display name)
    "source": ("Generator or grid supply", "SRC"),
    "isu": ("Intake switch unit", "ISU"),
    "cdu": ("Central distribution unit", "CDU"),
    "fdu": ("Final distribution unit", "FDU"),
    "equipment": ("Equipment", "EQP"),
    "ups": ("UPS", "UPS"),
    "other": ("Other", "OTH"),
}
ROLES: tuple[str, ...] = tuple(ROLE_TABLE)

# Order down the tree. A ranked node may only be fed by a node of a LOWER rank number (higher up the tree).
# "ups" and "other" have no rank: they may sit between any two levels, and anything may sit under them.
ROLE_RANK: dict[str, int] = {"source": 0, "isu": 1, "cdu": 2, "fdu": 3, "equipment": 4}

# UPS types (IEC 62040-3 classes) as stable keys. Stored only for now; the boundary rules come with the tree card.
UPS_TYPE_TABLE: dict[str, str] = {
    "online": "Online, double conversion (VFI)",
    "line_interactive": "Line-interactive (VI)",
    "standby": "Standby, offline (VFD)",
    "unknown": "Not known",
}
UPS_TYPES: tuple[str, ...] = tuple(UPS_TYPE_TABLE)

UNIT_NO_MIN, UNIT_NO_MAX = 1, 99
MAX_POINTS = 40
ALLOWANCE_UNITS = ("a_per_line", "kw", "unknown")


def role_label(role: str) -> str:
    """Plain English first, abbreviation after: 'Intake unit (ISU)'. Empty for no role."""
    if role not in ROLE_TABLE:
        return ""
    label, abbr = ROLE_TABLE[role]
    return label if label == abbr else f"{label} ({abbr})"


def display_name(role: str, unit_no: int | None) -> str:
    """'ISU01'. Empty without a role; the bare abbreviation when there is a role but no number."""
    if role not in ROLE_TABLE:
        return ""
    abbr = ROLE_TABLE[role][1]
    if isinstance(unit_no, int) and not isinstance(unit_no, bool) and UNIT_NO_MIN <= unit_no <= UNIT_NO_MAX:
        return f"{abbr}{unit_no:02d}"
    return abbr


# --------------------------------------------------------------------------- node references
# A reference is how one node names another as its upstream.
def point_ref(point_id: str) -> str:
    return f"p:{point_id}"


def meter_ref(device_id: str) -> str:
    return f"m:{device_id}"


def circuit_ref(device_id: str, key: str) -> str:
    return f"c:{device_id}:{key}"


@dataclass(frozen=True)
class Node:
    ref: str
    kind: str                # "point" | "meter" | "circuit"
    role: str = ""
    unit_no: int | None = None
    upstream: str = ""
    label: str = ""          # a name already stored, used only in messages
    way: int | None = None   # the way (circuit number) of the upstream unit this one is fed from
    line: str = ""           # "L1", "L2", "L3" or "3P" for a metered circuit; "" when not known


@dataclass(frozen=True)
class Problem:
    code: str
    ref: str
    message: str


def _who(n: Node) -> str:
    return display_name(n.role, n.unit_no) or n.label or "A node"


def _is_cycle_member(ref: str, parent: dict[str, str]) -> bool:
    """True when walking upstream from ``ref`` comes back to ``ref``."""
    seen, cur = set(), parent.get(ref, "")
    while cur and cur not in seen:
        if cur == ref:
            return True
        seen.add(cur)
        cur = parent.get(cur, "")
    return False


def validate(nodes: list[Node]) -> list[Problem]:
    """Every rule breach in the tree, in node order (a node can appear more than once). Empty = valid.

    Rules:
    * one upstream only (a field, so by construction); a node is never its own upstream;
    * the upstream must exist; no loops;
    * a 'source' is a root: it has no upstream. Several sources are allowed (several roots);
    * a ranked role (isu, cdu, fdu, equipment) must be fed by a ranked node of a higher level
      (source > isu > cdu > fdu > equipment) or by an 'ups' or 'other' node. A level may skip (an FDU may
      hang straight off an ISU), but never feeds the same or a higher level;
    * 'ups' and 'other' may be fed by anything and may feed anything: a UPS can sit between any two levels.
      The level check is made edge by edge, so a ranked node under a ups is not compared with what feeds the ups;
    * a number needs a role, is 1 to 99, and is unique within a role.
    """
    out: list[Problem] = []
    by_ref = {n.ref: n for n in nodes}
    parent = {n.ref: n.upstream for n in nodes if n.upstream}
    for n in nodes:
        who = _who(n)
        if n.upstream:
            if n.upstream == n.ref:
                out.append(Problem("self_parent", n.ref, f"{who} cannot be fed from itself."))
            elif n.upstream not in by_ref:
                out.append(Problem("unknown_upstream", n.ref, f"{who} is fed from something that does not exist."))
            elif _is_cycle_member(n.ref, parent):
                out.append(Problem("cycle", n.ref, f"{who} is part of a loop. Each unit must be fed from higher up."))
            else:
                up = by_ref[n.upstream]
                if n.role == "source":
                    pass    # reported below as source_has_upstream
                elif n.role in ROLE_RANK and up.role in ROLE_RANK and ROLE_RANK[up.role] >= ROLE_RANK[n.role]:
                    out.append(Problem("role_order", n.ref, f"{who} cannot be fed from {_who(up)}. The order is "
                                       "supply, intake unit, main distribution, final distribution, equipment."))
            if n.role == "source":
                out.append(Problem("source_has_upstream", n.ref, f"{who} is a supply, so it has nothing above it. Clear its Upstream."))
        if n.unit_no is not None:
            if not n.role:
                out.append(Problem("number_without_role", n.ref, "A number needs a role. Choose a role or clear the number."))
            elif isinstance(n.unit_no, bool) or not UNIT_NO_MIN <= n.unit_no <= UNIT_NO_MAX:
                out.append(Problem("number_range", n.ref, f"The number must be {UNIT_NO_MIN} to {UNIT_NO_MAX}."))
    ways: dict[tuple[str, int], str] = {}
    for n in nodes:
        if n.upstream and n.way is not None and UNIT_NO_MIN <= n.way <= UNIT_NO_MAX:
            k = (n.upstream, n.way)
            if k in ways:
                up = by_ref.get(n.upstream)
                out.append(Problem("duplicate_way", n.ref, f"{_who(n)} and {_who(by_ref[ways[k]])} are both on way {n.way} of "
                                   f"{_who(up) if up else 'the unit above'}. Each way feeds one thing."))
            else:
                ways[k] = n.ref
    seen: dict[tuple[str, int], str] = {}
    for n in nodes:
        if n.role and n.unit_no is not None and UNIT_NO_MIN <= n.unit_no <= UNIT_NO_MAX:
            k = (n.role, n.unit_no)
            if k in seen:
                out.append(Problem("duplicate_number", n.ref, f"{display_name(n.role, n.unit_no)} is already used. Each number is used once per role."))
            else:
                seen[k] = n.ref
    return out


def warnings(nodes: list[Node]) -> list[Problem]:
    """Soft advice (never blocks a save): a meter or circuit with no role, or with no upstream.
    A supply needs no upstream. Circuits are only mentioned when they have a role of their own."""
    out: list[Problem] = []
    for n in nodes:
        if n.kind == "meter":
            if not n.role:
                out.append(Problem("no_role", n.ref, f"{_who(n)} has no role yet."))
            elif n.role != "source" and not n.upstream:
                out.append(Problem("no_upstream", n.ref, f"{_who(n)} has no Upstream yet."))
        elif n.kind == "circuit" and n.role and n.role != "source" and not n.upstream:
            out.append(Problem("no_upstream", n.ref, f"{_who(n)} has no Upstream yet."))
    return out


def tidy_on_load(nodes: list[Node]) -> dict[str, str]:
    """Salvage: the refs whose upstream must be cleared so the saved tree is structurally sound
    (an upstream that does not exist, a node fed from itself, a loop). Returns {ref: ""}. Role order,
    numbering and 'source has an upstream' are left as saved and show up in validate() for the admin to fix.
    Loops are cut once, at the node with the smallest reference, so as little as possible is lost."""
    by_ref = {n.ref: n for n in nodes}
    parent = {n.ref: n.upstream for n in nodes if n.upstream}
    cut: dict[str, str] = {}
    for ref, up in list(parent.items()):
        if up == ref or up not in by_ref:
            cut[ref] = ""
            del parent[ref]
    while True:
        loop = sorted(r for r in parent if _is_cycle_member(r, parent))
        if not loop:
            break
        cut[loop[0]] = ""
        del parent[loop[0]]
    return cut


# --------------------------------------------------------------------------- reading a config
# These functions use the config by duck typing (power_points, shelly_devices and their channels), so this
# module never imports config.py.
class TreeError(Exception):
    """A change the tree cannot take. ``message`` is fixed text made from display names, never raw input."""

    def __init__(self, message: str, problems: list[Problem] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.problems = problems or []


channel_factory = None     # set by config.py to ShellyChannelConfig (this module never imports config)

# Settings of a meter with exactly one circuit are stored ONCE, on that circuit. These are the ones that
# older files may also hold on the meter itself: read as a fallback, and moved to the circuit when written.
_EMPTY = {"role": "", "unit_no": None, "upstream": "", "way": None, "rating_a": None, "breaker_a": None}
_ON_CIRCUIT = ("phase", "label", "in_total")


def single_key(dev) -> str:
    """The key of a meter's only circuit, or "" when it has none known or more than one."""
    keys = list(getattr(dev, "circuit_keys", None) or [])
    return keys[0] if len(keys) == 1 else ""


class _SingleMeter:
    """One node standing for a meter with exactly one circuit: its role, number, upstream, way, limits, line and
    label are the CIRCUIT's, so nothing is ever held twice. Reads fall back to the meter's own older value."""
    __slots__ = ("dev", "key")

    def __init__(self, dev, key: str) -> None:
        object.__setattr__(self, "dev", dev)
        object.__setattr__(self, "key", key)

    def _ch(self, create: bool = False):
        ch = next((c for c in self.dev.channels if c.key == self.key), None)
        if ch is None and create and channel_factory is not None:
            ch = channel_factory(key=self.key)
            self.dev.channels.append(ch)
        return ch

    def __getattr__(self, name: str):
        if name in _EMPTY:
            ch = self._ch()
            v = getattr(ch, name, None) if ch is not None else None
            return v if v not in ("", None) else getattr(self.dev, name, _EMPTY[name])
        if name in _ON_CIRCUIT:
            ch = self._ch()
            return getattr(ch, name) if ch is not None else (False if name == "in_total" else "")
        if name == "name":
            return self.dev.name
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        if name in _EMPTY:
            empty = value in ("", None)
            ch = self._ch(create=not empty)
            if ch is not None:
                setattr(ch, name, _EMPTY[name] if empty else value)
            setattr(self.dev, name, _EMPTY[name])      # one source of truth: clear the older copy on the meter
        elif name in _ON_CIRCUIT:
            ch = self._ch(create=bool(value))
            if ch is not None:
                setattr(ch, name, value)
        else:
            raise AttributeError(name)


def holder_of_meter(dev):
    """What holds a meter's settings: the meter itself, or its one circuit through a proxy."""
    key = single_key(dev)
    return _SingleMeter(dev, key) if key else dev


def _holders(cfg) -> dict[str, object]:
    """ref -> the config object that holds role, unit_no and upstream for that node. A meter with exactly one
    circuit is ONE node (m:<id>): its circuit is not a second one."""
    out: dict[str, object] = {}
    for p in cfg.power_points:
        out[point_ref(p.id)] = p
    for d in cfg.shelly_devices:
        key = single_key(d)
        out[meter_ref(d.id)] = _SingleMeter(d, key) if key else d
        for c in d.channels:
            if not (key and c.key == key):
                out[circuit_ref(d.id, c.key)] = c
    return out


def is_three_phase(dev, key: str) -> bool:
    return key in (getattr(dev, "three_phase_keys", None) or [])


def merge_single_circuits(cfg) -> int:
    """For every meter with exactly one circuit: move any role, number, upstream, way and limits held on the
    meter itself onto that circuit (the circuit's own value wins when both are set), and re-point anything
    fed from "c:<meter>:<circuit>" at the meter's node. Safe to run again. Returns how many settings moved;
    logs a count only."""
    moved = 0
    for d in cfg.shelly_devices:
        key = single_key(d)
        if not key:
            continue
        proxy = _SingleMeter(d, key)
        for f in _EMPTY:
            if getattr(d, f, _EMPTY[f]) not in ("", None):
                setattr(proxy, f, getattr(proxy, f))     # writes it on the circuit, clears the meter's copy
                moved += 1
        old, new = circuit_ref(d.id, key), meter_ref(d.id)
        for obj in _holders(cfg).values():
            if obj.upstream == old:
                obj.upstream = new
    if moved:
        log.info("Power tree: %d setting%s of single-circuit meters moved onto their circuit", moved, "" if moved == 1 else "s")
    return moved


def link_label(up_display: str, way: int | None, line: str) -> str:
    """How a unit is fed: 'CDU02-C2-L1' (way 2, line 1), 'CDU01-C1-3P' (three-phase), 'CDU02-C2' (no line known),
    or just 'CDU02' when no way is set. Empty when there is nothing above."""
    if not up_display:
        return ""
    if way is None:
        return up_display
    return f"{up_display}-C{way}" + (f"-{line}" if line else "")


def _line_of(cfg, ref: str, obj) -> str:
    if ref.startswith("p:"):
        return ""
    if ref.startswith("m:"):
        dev = next((d for d in cfg.shelly_devices if d.id == ref[2:]), None)
        key = single_key(dev) if dev is not None else ""
        if not key:
            return ""
        return "3P" if is_three_phase(dev, key) else (obj.phase or "")
    dev_id, _, key = ref[2:].partition(":")
    dev = next((d for d in cfg.shelly_devices if d.id == dev_id), None)
    if dev is not None and is_three_phase(dev, key):
        return "3P"
    return getattr(obj, "phase", "") or ""


def _kind(ref: str) -> str:
    return {"p": "point", "m": "meter"}.get(ref[0], "circuit")


def _label(cfg, ref: str, obj) -> str:
    if ref.startswith("c:"):
        dev = next((d for d in cfg.shelly_devices if ref.startswith(f"c:{d.id}:")), None)
        return f"{dev.name if dev else 'A meter'} {obj.label or obj.key}".strip()
    return getattr(obj, "name", "") or ""


def build_nodes(cfg) -> list[Node]:
    return [Node(ref, _kind(ref), obj.role, obj.unit_no, obj.upstream, _label(cfg, ref, obj), obj.way, _line_of(cfg, ref, obj))
            for ref, obj in _holders(cfg).items()]


def feed_text(nodes_by_ref: dict[str, Node], n: Node) -> str:
    """The link label for a node ('CDU02-C2-L1'); "" when nothing is above it."""
    up = nodes_by_ref.get(n.upstream)
    if up is None:
        return ""
    return link_label(display_name(up.role, up.unit_no) or up.label, n.way, n.line)


def tidy_config(cfg) -> int:
    """On load: clear upstreams that point nowhere, at themselves or round a loop. Keeps every node.
    Logs a count only. Returns how many were cleared."""
    merge_single_circuits(cfg)
    holders = _holders(cfg)
    cut = tidy_on_load(build_nodes(cfg))
    for ref in cut:
        holders[ref].upstream = ""
    if cut:
        log.warning("Power tree: %d upstream link%s cleared (missing, self-referencing or looping)",
                    len(cut), "" if len(cut) == 1 else "s")
    return len(cut)


def detach_children(cfg, ref: str) -> int:
    """Clear the upstream of everything fed from ``ref`` (used when a meter is removed). Returns the count."""
    n = 0
    for obj in _holders(cfg).values():
        if obj.upstream == ref:
            obj.upstream = ""
            n += 1
    return n


def delete_point(cfg, point_id: str, children: str | None) -> None:
    """Remove a distribution point. If things are fed from it, ``children`` must say what to do:
    'reparent' (they take the point's own upstream; refused if that would break the order rules) or
    'detach' (they are left with no upstream, which the admin sees as a warning). Changes nothing on error."""
    ref = point_ref(point_id)
    point = next((p for p in cfg.power_points if p.id == point_id), None)
    if point is None:
        raise LookupError
    holders = _holders(cfg)
    kids = [r for r, o in holders.items() if o.upstream == ref]
    if kids and children not in ("reparent", "detach"):
        raise TreeError("Other units are fed from this one. Choose to move them up a level or to leave them with no Upstream.")
    new_up = point.upstream if children == "reparent" else ""
    nodes = [replace(n, upstream=new_up if n.upstream == ref else n.upstream)
             for n in build_nodes(cfg) if n.ref != ref]
    bad = validate(nodes)
    if bad:
        raise TreeError("Moving them up a level would break the order. Leave them with no Upstream instead.", bad)
    for r in kids:
        holders[r].upstream = new_up
    cfg.power_points.remove(point)


# --------------------------------------------------------------------------- limits
def effective_limit(breaker_a, device_a) -> tuple[float | None, str]:
    """The amps a load is judged against: the LOWER of the breaker or fuse and the device limit when both
    are set, the one that is set when only one is, none when neither. Returns (amps, "breaker" | "device"
    | ""): which of the two is limiting. A tie counts as the breaker (the breaker trips first in practice).
    Advisory only; the same rule is in static/power.js (SW.pw.limit), kept equal by a test."""
    def ok(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
    b = breaker_a if ok(breaker_a) else None
    d = device_a if ok(device_a) else None
    if b is not None and (d is None or b <= d):
        return float(b), "breaker"
    if d is not None:
        return float(d), "device"
    return None, ""


def breaker_over_device(breaker_a, device_a) -> bool:
    """True when the breaker is bigger than the device can carry: the meter or relay is the weak point."""
    return bool(breaker_a and device_a and breaker_a > device_a)


# --------------------------------------------------------------------------- what the screens get
def public_structure(cfg) -> dict:
    """The tree for dashboards: names, roles, display names, upstream links and ratings. No addresses, no
    MACs, no typed allowances. A circuit appears only when it has a role or something is fed from it."""
    holders = _holders(cfg)
    by_ref = {n.ref: n for n in build_nodes(cfg)}
    fed = {o.upstream for o in holders.values() if o.upstream}
    nodes = []
    for ref, obj in holders.items():
        if ref.startswith("c:") and not obj.role and ref not in fed:
            continue
        nodes.append({
            "ref": ref, "kind": _kind(ref), "name": _label(cfg, ref, obj), "role": obj.role,
            "role_label": role_label(obj.role), "display": display_name(obj.role, obj.unit_no),
            "unit_no": obj.unit_no, "upstream": obj.upstream if obj.upstream in holders else "",
            "way": obj.way if obj.upstream in holders else None, "line": by_ref[ref].line,
            "feed": feed_text(by_ref, by_ref[ref]) if obj.upstream in holders else "",
            "rating_a": getattr(obj, "rating_a", None), "breaker_a": getattr(obj, "breaker_a", None), "rating_kva": getattr(obj, "rating_kva", None),
            "ups_type": getattr(obj, "ups_type", None) if obj.role == "ups" else None,
        })
    return {"nodes": nodes}


def admin_view(cfg) -> dict:
    """For the admin card: the tables, every node with its display name, and the current problems and advice."""
    nodes = build_nodes(cfg)
    return {
        "roles": [{"key": k, "label": role_label(k), "abbr": ROLE_TABLE[k][1], "rank": ROLE_RANK.get(k)} for k in ROLES],
        "ups_types": [{"key": k, "label": v} for k, v in UPS_TYPE_TABLE.items()],
        "max_points": MAX_POINTS,
        "nodes": [{"ref": n.ref, "kind": n.kind, "name": n.label, "role": n.role,
                   "unit_no": n.unit_no, "upstream": n.upstream, "way": n.way, "line": n.line,
                   "feed": feed_text({x.ref: x for x in nodes}, n), "display": display_name(n.role, n.unit_no)} for n in nodes],
        "show_advanced": bool(getattr(getattr(cfg, "power_site", None), "show_advanced", False)),
        "problems": [{"code": p.code, "ref": p.ref, "message": p.message} for p in validate(nodes)],
        "warnings": [{"code": p.code, "ref": p.ref, "message": p.message} for p in warnings(nodes)],
    }
