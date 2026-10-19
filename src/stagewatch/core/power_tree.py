"""Power distribution tree: roles, display names and the rules for who may feed whom.

Step 1 only: the data model and its checks. Nothing here reads a meter or does any power maths.

Everything is pure (no I/O, no config import) so it can be unit tested by hand-worked cases.

A node is a distribution point (a virtual node for a generator, grid supply or intake unit that has no
meter), a Shelly meter, or one circuit of a meter. Each node has a role, a number and one upstream node.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

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


def children_of(nodes: list[Node], ref: str) -> list[str]:
    return [n.ref for n in nodes if n.upstream == ref]


# --------------------------------------------------------------------------- reading a config
# These functions use the config by duck typing (power_points, shelly_devices and their channels), so this
# module never imports config.py.
class TreeError(Exception):
    """A change the tree cannot take. ``message`` is fixed text made from display names, never raw input."""

    def __init__(self, message: str, problems: list[Problem] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.problems = problems or []


def _holders(cfg) -> dict[str, object]:
    """ref -> the config object that holds role, unit_no and upstream for that node."""
    out: dict[str, object] = {}
    for p in cfg.power_points:
        out[point_ref(p.id)] = p
    for d in cfg.shelly_devices:
        out[meter_ref(d.id)] = d
        for c in d.channels:
            out[circuit_ref(d.id, c.key)] = c
    return out


def _kind(ref: str) -> str:
    return {"p": "point", "m": "meter"}.get(ref[0], "circuit")


def _label(cfg, ref: str, obj) -> str:
    if ref.startswith("c:"):
        dev = next((d for d in cfg.shelly_devices if ref.startswith(f"c:{d.id}:")), None)
        return f"{dev.name if dev else 'A meter'} {obj.label or obj.key}".strip()
    return getattr(obj, "name", "") or ""


def build_nodes(cfg) -> list[Node]:
    return [Node(ref, _kind(ref), obj.role, obj.unit_no, obj.upstream, _label(cfg, ref, obj))
            for ref, obj in _holders(cfg).items()]


def tidy_config(cfg) -> int:
    """On load: clear upstreams that point nowhere, at themselves or round a loop. Keeps every node.
    Logs a count only. Returns how many were cleared."""
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
    nodes = [Node(n.ref, n.kind, n.role, n.unit_no, new_up if n.upstream == ref else n.upstream, n.label)
             for n in build_nodes(cfg) if n.ref != ref]
    bad = validate(nodes)
    if bad:
        raise TreeError("Moving them up a level would break the order. Leave them with no Upstream instead.", bad)
    for r in kids:
        holders[r].upstream = new_up
    cfg.power_points.remove(point)


# --------------------------------------------------------------------------- what the screens get
def public_structure(cfg) -> dict:
    """The tree for dashboards: names, roles, display names, upstream links and ratings. No addresses, no
    MACs, no typed allowances. A circuit appears only when it has a role or something is fed from it."""
    holders = _holders(cfg)
    fed = {o.upstream for o in holders.values() if o.upstream}
    nodes = []
    for ref, obj in holders.items():
        if ref.startswith("c:") and not obj.role and ref not in fed:
            continue
        nodes.append({
            "ref": ref, "kind": _kind(ref), "name": _label(cfg, ref, obj), "role": obj.role,
            "role_label": role_label(obj.role), "display": display_name(obj.role, obj.unit_no),
            "unit_no": obj.unit_no, "upstream": obj.upstream if obj.upstream in holders else "",
            "rating_a": getattr(obj, "rating_a", None), "rating_kva": getattr(obj, "rating_kva", None),
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
                   "unit_no": n.unit_no, "upstream": n.upstream, "display": display_name(n.role, n.unit_no)} for n in nodes],
        "problems": [{"code": p.code, "ref": p.ref, "message": p.message} for p in validate(nodes)],
        "warnings": [{"code": p.code, "ref": p.ref, "message": p.message} for p in warnings(nodes)],
    }
