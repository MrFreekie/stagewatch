"""Power distribution: save to a file, read a file (pure functions on a ``Config`` and plain data, no I/O).

The file holds the layout (points, roles, numbers, what feeds what, ways, breaker ratings, device limits,
labels, lines) so it can move to another hub, be kept as a backup or be reused for the next event. Meters are
identified by MAC, never by address; there are no passwords, hashes, tokens or addresses in it.

Reading a file never changes anything until the caller applies the plan: ``plan_import`` only works out what
would change and builds the body the normal Save uses (``PUT /api/admin/power-tree``), so the same power-tree
rules decide whether the result is allowed. Errors are fixed text and never repeat anything from the file.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import power_tree as ptree
from .calibration import utc_now_iso
from .model import normalise_mac

__all__ = ["SCHEMA", "ImportError_", "build_export", "plan_import", "MAX_METERS"]

SCHEMA = 1
MAX_METERS = 60
_NOTHING = " Nothing has been changed."
BAD_FILE = "That is not a Stagewatch power distribution file." + _NOTHING
NEWER = ("This power distribution file was saved by a newer version of Stagewatch. "
         "Update this hub, or save the file again from the newer one." + _NOTHING)
BAD_RECORD = "That power distribution file has an entry that could not be read." + _NOTHING
BAD_LINK = "That power distribution file feeds something from a unit that is not in the file." + _NOTHING

_REF = re.compile(r"(p:[a-z0-9_]{1,40}|m:[0-9a-f]{12}|c:[0-9a-f]{12}:[a-z0-9_]{1,24})?")


def _plain(v: str) -> str:
    v = v.strip()
    if any(unicodedata.category(ch).startswith("C") for ch in v):
        raise ValueError("plain text only")
    return v


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class _Tree(_Strict):
    role: str = Field("", max_length=16)
    unit_no: int | None = Field(None, ge=ptree.UNIT_NO_MIN, le=ptree.UNIT_NO_MAX)
    upstream: str = Field("", max_length=80)
    way: int | None = Field(None, ge=ptree.UNIT_NO_MIN, le=ptree.UNIT_NO_MAX)

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v and v not in ptree.ROLES:
            raise ValueError("role")
        return v

    @field_validator("upstream")
    @classmethod
    def _up(cls, v: str) -> str:
        if not _REF.fullmatch(v):
            raise ValueError("upstream")
        return v


class FilePoint(_Tree):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,35}$")
    name: str = Field(min_length=1, max_length=60)
    rating_a: float | None = Field(None, ge=0, le=5000, allow_inf_nan=False)
    rating_kva: float | None = Field(None, ge=0, le=10000, allow_inf_nan=False)
    ups_type: str = "unknown"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _plain(v)
        if not v:
            raise ValueError("name")
        return v

    @field_validator("ups_type")
    @classmethod
    def _ups(cls, v: str) -> str:
        if v not in ptree.UPS_TYPES:
            raise ValueError("ups_type")
        return v


class _Limits(_Strict):
    breaker_a: float | None = Field(None, ge=0, le=1000, allow_inf_nan=False)
    device_limit_a: float | None = Field(None, ge=0, le=200, allow_inf_nan=False)
    label: str = Field("", max_length=40)
    line: Literal["", "L1", "L2", "L3"] = ""
    in_total: bool = False

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return _plain(v)


class FileCircuit(_Tree, _Limits):
    key: str = Field(pattern=r"^[a-z0-9_]{1,24}$")


class FileMeter(_Tree, _Limits):
    mac: str
    name: str = Field("", max_length=60)           # shown in the preview only; the MAC is what matches
    single_circuit: bool = False
    combined_bar: bool = False
    circuits: list[FileCircuit] = Field(default_factory=list, max_length=16)

    @field_validator("mac")
    @classmethod
    def _mac(cls, v: str) -> str:
        mac = normalise_mac(v)
        if mac is None:
            raise ValueError("mac")
        return mac

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _plain(v)


class FileSite(_Strict):
    demand_window_min: Literal[5, 15, 30] = 15
    gen_loading_pct: float = Field(80.0, ge=50, le=100, allow_inf_nan=False)
    gen_power_factor: float = Field(0.8, ge=0.5, le=1.0, allow_inf_nan=False)
    gen_size_kva: float | None = Field(None, gt=0, le=5000, allow_inf_nan=False)
    show_advanced: bool = False


class PowerFile(_Strict):
    schema_: int = Field(alias="schema", ge=1)
    saved_at: str = Field("", max_length=40)
    stagewatch_version: str = Field("", max_length=40)
    power_site: FileSite = Field(default_factory=FileSite)
    points: list[FilePoint] = Field(default_factory=list, max_length=ptree.MAX_POINTS)
    meters: list[FileMeter] = Field(default_factory=list, max_length=MAX_METERS)


class ImportError_(ValueError):
    """The file can't be used. ``str()`` is the fixed plain-words message for the admin."""


# --------------------------------------------------------------------------- reading this hub
def _circuit_keys(dev) -> set[str]:
    return {c.key for c in dev.channels} | set(dev.circuit_keys or [])


def _num(v) -> float | None:
    return float(v) if v else None


def _tree_part(o) -> dict:
    return {"role": o.role, "unit_no": o.unit_no, "upstream": o.upstream, "way": o.way}


def _limit_part(o, label: bool = True) -> dict:
    return {"breaker_a": _num(o.breaker_a), "device_limit_a": _num(o.rating_a),
            "label": o.label if label else "", "line": o.phase if label else "", "in_total": bool(o.in_total) if label else False}


def _point_fields(p) -> dict:
    return {**_tree_part(p), "name": p.name, "rating_a": _num(p.rating_a), "rating_kva": _num(p.rating_kva),
            "ups_type": p.ups_type if p.role == "ups" else "unknown"}


def _meter_fields(dev) -> dict:
    single = bool(ptree.single_key(dev))
    held = ptree.holder_of_meter(dev)
    out = {**_tree_part(held), **_limit_part(held, label=single), "single_circuit": single,
           "combined_bar": False if single else bool(dev.combined_bar)}
    out["circuits"] = {} if single else {c.key: {**_tree_part(c), **_limit_part(c)} for c in dev.channels}
    return out


def _to_file_ref(cfg, ref: str) -> str:
    if ref.startswith("p:"):
        return ref
    parts = ref.split(":")
    dev = next((d for d in cfg.shelly_devices if d.id == parts[1]), None) if len(parts) > 1 else None
    if dev is None:
        return ""
    return f"m:{dev.mac}" if parts[0] == "m" else f"c:{dev.mac}:{parts[2]}"


def build_export(cfg, version: str, now: float | None = None) -> dict:
    """The file's content for this hub. No addresses, passwords, hashes or tokens are ever read here."""
    def tree(d: dict) -> dict:
        return {**d, "upstream": _to_file_ref(cfg, d["upstream"])}
    site = cfg.power_site
    meters = []
    for dev in cfg.shelly_devices:
        f = _meter_fields(dev)
        circuits = [{"key": k, **tree(v)} for k, v in sorted(f.pop("circuits").items())]
        meters.append({"mac": dev.mac, "name": dev.name, **tree(f), "circuits": circuits})
    return {
        "schema": SCHEMA, "saved_at": utc_now_iso(now), "stagewatch_version": version,
        "power_site": {"demand_window_min": site.demand_window_min, "gen_loading_pct": site.gen_loading_pct,
                       "gen_power_factor": site.gen_power_factor, "gen_size_kva": site.gen_size_kva,
                       "show_advanced": site.show_advanced},
        "points": [{"id": p.id, **tree(_point_fields(p))} for p in cfg.power_points],
        "meters": meters,
    }


# --------------------------------------------------------------------------- reading a file
@dataclass
class ImportPlan:
    new: list[dict] = field(default_factory=list)          # {kind, name}
    changed: list[dict] = field(default_factory=list)      # {kind, name, fields: [plain words]}
    unchanged: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)      # {name, why}: in the file, not on this hub
    site_changed: bool = False
    site: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)               # a PowerTreeBody, as plain data

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.changed or self.site_changed)

    def summary(self) -> dict:
        return {"new": self.new, "changed": self.changed, "unchanged": self.unchanged, "skipped": self.skipped,
                "site_changed": self.site_changed}


_WORDS = {"role": "what it is", "unit_no": "number", "upstream": "what feeds it", "way": "way",
          "breaker_a": "breaker or fuse rating", "device_limit_a": "device limit", "label": "name on the Power card",
          "line": "line", "in_total": "include in site total", "combined_bar": "combined load bar",
          "name": "name", "rating_a": "breaker or fuse rating", "rating_kva": "kVA rating", "ups_type": "UPS type"}


def parse(data: Any) -> PowerFile:
    if not isinstance(data, dict) or not isinstance(data.get("schema"), int) or isinstance(data.get("schema"), bool):
        raise ImportError_(BAD_FILE)
    if data["schema"] > SCHEMA:
        raise ImportError_(NEWER)
    if data["schema"] < 1:
        raise ImportError_(BAD_FILE)
    try:
        f = PowerFile.model_validate(data)
    except ValidationError:
        raise ImportError_(BAD_RECORD) from None
    if (len({p.id for p in f.points}) != len(f.points) or len({m.mac for m in f.meters}) != len(f.meters)
            or any(len({c.key for c in m.circuits}) != len(m.circuits) for m in f.meters)):
        raise ImportError_(BAD_RECORD)
    return f


def _diff(now: dict, want: dict, keys: tuple[str, ...]) -> list[str]:
    return [_WORDS[k] for k in keys if now.get(k) != want.get(k)]


def plan_import(cfg, data: Any) -> ImportPlan:
    """Check a file and work out what reading it would do. Changes nothing."""
    f = parse(data)
    plan = ImportPlan()
    hub_points = {p.id: p for p in cfg.power_points}
    file_points = {p.id for p in f.points}
    by_mac = {d.mac: d for d in cfg.shelly_devices}
    file_macs = {m.mac for m in f.meters}
    dropped = 0

    def remap(ref: str, matched: dict[str, Any]) -> str:
        """The file's reference as this hub names it. A link to something skipped is left empty."""
        nonlocal dropped
        if not ref:
            return ""
        if ref.startswith("p:"):
            if ref[2:] in file_points or ref[2:] in hub_points:
                return ref
            raise ImportError_(BAD_LINK)
        parts = ref.split(":")
        if parts[1] not in file_macs:
            raise ImportError_(BAD_LINK)
        dev = matched.get(parts[1])
        if dev is None:
            dropped += 1
            return ""
        if parts[0] == "m":
            return ptree.meter_ref(dev.id)
        if ptree.single_key(dev) == parts[2]:
            return ptree.meter_ref(dev.id)
        if parts[2] in _circuit_keys(dev):
            return ptree.circuit_ref(dev.id, parts[2])
        dropped += 1
        return ""

    matched = {m.mac: by_mac[m.mac] for m in f.meters if m.mac in by_mac}
    points_body, meters_body = [], []
    tree_keys = ("role", "unit_no", "upstream", "way")
    for p in f.points:
        want = {**_tree_part(p), "name": p.name, "rating_a": _num(p.rating_a), "rating_kva": _num(p.rating_kva),
                "ups_type": p.ups_type if p.role == "ups" else "unknown"}
        want["upstream"] = remap(p.upstream, matched)
        have = hub_points.get(p.id)
        if have is None:
            plan.new.append({"kind": "point", "name": p.name})
        else:
            fields = _diff(_point_fields(have), want, ("name",) + tree_keys + ("rating_a", "rating_kva", "ups_type"))
            if not fields:
                plan.unchanged.append({"kind": "point", "name": have.name})
                continue
            plan.changed.append({"kind": "point", "name": have.name, "fields": fields})
        pb = {"id": p.id, "name": p.name, "role": want["role"], "unit_no": want["unit_no"], "upstream": want["upstream"],
              "way": want["way"], "rating_a": want["rating_a"] or 0, "rating_kva": want["rating_kva"] or 0,
              "ups_type": want["ups_type"], "monitored": True}
        if have is not None:        # what Stagewatch keeps about a point that the file does not carry
            pb["monitored"] = have.monitored
            pb["allowance"] = have.allowance.model_dump(mode="json") if have.allowance else None
        points_body.append(pb)
    for m in f.meters:
        dev = matched.get(m.mac)
        if dev is None:
            plan.skipped.append({"name": m.name or "A meter", "why": "This hub does not have that meter."})
            continue
        have = _meter_fields(dev)
        if have["single_circuit"] != m.single_circuit:
            plan.skipped.append({"name": dev.name, "why": "Its circuits are not the same as in the file."})
            continue
        want = {**_tree_part(m), "breaker_a": _num(m.breaker_a), "device_limit_a": _num(m.device_limit_a),
                "label": m.label if m.single_circuit else "", "line": m.line if m.single_circuit else "",
                "in_total": m.in_total if m.single_circuit else False,
                "combined_bar": False if m.single_circuit else m.combined_bar}
        want["upstream"] = remap(m.upstream, matched)
        mkeys = tree_keys + ("breaker_a", "device_limit_a") + (("label", "line", "in_total") if m.single_circuit else ("combined_bar",))
        fields = _diff(have, want, mkeys)
        cbody, known = [], _circuit_keys(dev)
        for c in ([] if m.single_circuit else m.circuits):
            if c.key not in known:
                plan.skipped.append({"name": f"{dev.name}, circuit {c.key}", "why": "This meter has no such circuit."})
                continue
            cwant = {**_tree_part(c), "breaker_a": _num(c.breaker_a), "device_limit_a": _num(c.device_limit_a),
                     "label": c.label, "line": c.line, "in_total": c.in_total}
            cwant["upstream"] = remap(c.upstream, matched)
            cnow = have["circuits"].get(c.key) or {"role": "", "unit_no": None, "upstream": "", "way": None, "breaker_a": None,
                                                   "device_limit_a": None, "label": "", "line": "", "in_total": False}
            cf = _diff(cnow, cwant, tree_keys + ("breaker_a", "device_limit_a", "label", "line", "in_total"))
            if cf:
                fields.append(f"circuit {c.label or c.key}: " + ", ".join(cf))
                cbody.append({"key": c.key, "role": cwant["role"], "unit_no": cwant["unit_no"], "upstream": cwant["upstream"],
                              "way": cwant["way"], "label": cwant["label"], "phase": cwant["line"],
                              "rating_a": cwant["device_limit_a"] or 0, "breaker_a": cwant["breaker_a"] or 0, "in_total": cwant["in_total"]})
        if not fields:
            plan.unchanged.append({"kind": "meter", "name": dev.name})
            continue
        plan.changed.append({"kind": "meter", "name": dev.name, "fields": fields})
        mb = {"id": dev.id, "role": want["role"], "unit_no": want["unit_no"], "upstream": want["upstream"], "way": want["way"],
              "rating_a": want["device_limit_a"] or 0, "breaker_a": want["breaker_a"] or 0, "channels": cbody}
        if m.single_circuit:
            mb.update(label=want["label"], phase=want["line"], in_total=want["in_total"])
        else:
            mb["combined_bar"] = want["combined_bar"]
        meters_body.append(mb)
    site, cur = f.power_site.model_dump(), cfg.power_site.model_dump()
    plan.site = site
    plan.site_changed = any(cur[k] != site[k] for k in site)
    if dropped:
        plan.skipped.append({"name": f"{dropped} link{'s' if dropped != 1 else ''} from a skipped meter",
                             "why": "Left empty, because that meter is not on this hub."})
    plan.body = {"points": points_body, "meters": meters_body}
    return plan
