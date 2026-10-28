"""Reader for the Nebra System Status Report (``<date>_<time>_SystemStatusReport.txt``): a snapshot, not a log.

Phase 1: a preview only. ``inventory`` turns the report into a plain-words list (which units are Available,
firmware versions, streams that report a failure, and, if the Events CSV is also given, units that file knows
but the report does not list). Nothing is stored.

Privacy. Serial numbers, MAC addresses and computer names are never read into the result and never printed:
the entity and connection lines are split on a fixed list of the keys Stagewatch wants, so every other key
(the serial, both MACs, the stream ID) is simply skipped, and anything printed goes through ``scrub``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import parse

MAX_LINES = 20_000
MAX_LINE = 2000
ENTITY_KEYS = ("entity_id", "MAC", "entity_name", "group_name", "serial#", "firmwareVersion", "status",
               "#sources", "#sinks", "as_path_map")
CONNECTION_KEYS = ("Talker location", "Listener location", "Talker", "Listener", "State", "Flags",
                   "MSRP Stream ID", "MSRP Latency nS", "Listener MAC", "Failure Code", "Failure Bridge", "VLAN")
KEEP_ENTITY = ("entity_name", "group_name", "firmwareVersion", "status")
KEEP_CONNECTION = ("Talker location", "Listener location", "Talker", "Listener", "State", "Failure Code")
OK_CODES = ("", "no error", "0", "none")
_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
_HEX_ID = re.compile(r"\b(?:0x)?[0-9A-Fa-f]{12,}\b")
_PC = re.compile(r"Hive\.\S+")
_VERSION = re.compile(r"Nebra\s+v\.?\s*([0-9][0-9A-Za-z.\-]{0,20})", re.IGNORECASE)
_AS_OF = re.compile(r"\bas of (\d{4}[/-]\d{2}[/-]\d{2} \d{2}:\d{2}:\d{2})")
_CLIENTS = re.compile(r"(\d{1,5})\s+Connected clients?", re.IGNORECASE)


def _key_regex(keys: tuple[str, ...]) -> re.Pattern:
    ordered = sorted(keys, key=len, reverse=True)
    return re.compile(r"(?<![\w#])(" + "|".join(re.escape(k) for k in ordered) + r")\s*=\s*", re.IGNORECASE)


_ENTITY_RE = _key_regex(ENTITY_KEYS)
_CONNECTION_RE = _key_regex(CONNECTION_KEYS)


def scrub(text: str) -> str:
    """Text safe to print: no MAC addresses, long hex ids or computer names."""
    return _PC.sub("Hive client", _HEX_ID.sub("(id)", _MAC.sub("(address)", text)))


def split_pairs(line: str, regex: re.Pattern, keep: tuple[str, ...]) -> dict[str, str]:
    """``key=value`` pairs for the wanted keys only. A value runs to the next known key."""
    marks = list(regex.finditer(line))
    canon = {k.lower(): k for k in keep}
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        key = canon.get(m.group(1).lower())
        if key:
            value = line[m.end():end].strip().rstrip(",;|").strip()
            if value.startswith("[") and value.endswith("]"):   # Nebra writes every value as [value]
                value = value[1:-1].strip()
            out[key] = parse.clean(value, 120)
    return out


@dataclass
class StatusReport:
    version: str = ""
    as_of: str = ""
    clients: int | None = None
    alerts: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    lines_not_read: int = 0


def looks_like_status(text: str) -> bool:
    head = text[:4096]
    return "System Status Report" in head or "AVB Entities" in head


def parse_status(text: str) -> StatusReport:
    r = StatusReport()
    section = ""
    for n, raw in enumerate(text.splitlines()):
        if n >= MAX_LINES:
            break
        line = raw.strip()
        if not line:
            continue
        if len(line) > MAX_LINE:
            r.lines_not_read += 1
            continue
        low = line.lower()
        if low.startswith("avb entities"):
            section = "entities"
            continue
        if low.startswith("avb connections"):
            section = "connections"
            continue
        if low.startswith("active alerts"):
            section = "alerts"
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest and rest.lower() not in ("none", "-"):
                r.alerts.append(scrub(parse.clean(rest, 160)))
            continue
        if not section:
            m = _VERSION.search(line)
            if m and not r.version:
                r.version = m.group(1)
            m = _AS_OF.search(line)
            if m and not r.as_of:
                r.as_of = m.group(1)
            m = _CLIENTS.search(line)
            if m and r.clients is None:
                r.clients = int(m.group(1))
            continue
        if section == "alerts":
            r.alerts.append(scrub(parse.clean(line, 160)))
        elif section == "entities":
            d = split_pairs(line, _ENTITY_RE, KEEP_ENTITY)
            if d:
                r.entities.append(d)
            else:
                r.lines_not_read += 1
        elif section == "connections":
            d = split_pairs(line, _CONNECTION_RE, KEEP_CONNECTION)
            if d:
                r.connections.append(d)
            else:
                r.lines_not_read += 1
    if r.version == "" or r.as_of == "":                       # a header that sits after the first lines
        m = _VERSION.search(text[:4096])
        r.version = r.version or (m.group(1) if m else "")
        m = _AS_OF.search(text[:4096])
        r.as_of = r.as_of or (m.group(1) if m else "")
    if r.clients is None:
        m = _CLIENTS.search(text[:4096])
        r.clients = int(m.group(1)) if m else None
    return r


def entity_label(e: dict) -> str:
    name, group = e.get("entity_name", ""), e.get("group_name", "")
    if name.startswith(parse.CLIENT_PREFIX) or group.startswith(parse.CLIENT_PREFIX):
        return "Hive client"
    return scrub(f"{group}.{name}" if group and name else name or group or "(unnamed)")


def inventory(report: StatusReport, events: parse.ParseResult | None = None) -> dict:
    """A plain summary of the snapshot. No serials, MACs or computer names."""
    units = [e for e in report.entities if entity_label(e) != "Hive client"]
    available = [e for e in units if e.get("status", "").lower() == "available"]
    away = [entity_label(e) for e in units if e.get("status", "").lower() != "available"]
    firmware: dict[str, int] = {}
    for e in units:
        v = scrub(e.get("firmwareVersion", "") or "unknown")
        firmware[v] = firmware.get(v, 0) + 1
    failing = []
    for c in report.connections:
        code = c.get("Failure Code", "")
        if code.strip().lower() not in OK_CODES:
            failing.append({"talker": scrub(c.get("Talker location", c.get("Talker", "?"))),
                            "listener": scrub(c.get("Listener location", c.get("Listener", "?"))),
                            "code": scrub(code)})
    missing: list[dict] = []
    if events is not None:
        listed = set()
        for e in units:
            listed.add(entity_label(e).lower())
            listed.add(e.get("entity_name", "").lower())
        last: dict[str, str] = {}
        for ev in events.events:
            if ev.name and not ev.client and ev.family == "connection":
                last[ev.name] = ev.kind
        names = {ev.name for ev in events.events if ev.name and not ev.client}
        for name in sorted(names):
            if name.lower() not in listed:
                missing.append({"name": scrub(name), "disconnected_at_end_of_events": last.get(name) == "disconnect"})
    return {"version": report.version, "as_of": report.as_of, "clients": report.clients,
            "alerts": report.alerts[:20], "alert_count": len(report.alerts),
            "units": len(units), "available": len(available), "not_available": away[:50],
            "firmware": dict(sorted(firmware.items())), "streams": len(report.connections),
            "failing_streams": failing[:50], "failing_total": len(failing),
            "missing_from_report": missing[:50], "lines_not_read": report.lines_not_read,
            "events_given": events is not None}


def describe(inv: dict) -> list[str]:
    """The inventory in plain words, one line each."""
    out = [f"Nebra {inv['version'] or '(version not found)'}, report as of {inv['as_of'] or '(time not found)'}"
           + ("" if inv["clients"] is None else f", {inv['clients']} connected {'client' if inv['clients'] == 1 else 'clients'}")]
    out.append(f"Units: {inv['available']} of {inv['units']} Available."
               + ("" if not inv["not_available"] else " Not available: " + ", ".join(inv["not_available"]) + "."))
    out.append("Alerts: " + ("none active." if not inv["alert_count"] else
                             f"{inv['alert_count']} active: " + "; ".join(inv["alerts"][:5])))
    out.append("Firmware: " + (", ".join(f"{v} on {n}" for v, n in inv["firmware"].items()) or "no units listed") + ".")
    if inv["failing_total"]:
        out.append(f"Streams with a failure: {inv['failing_total']} of {inv['streams']}.")
        out += [f"  {f['talker']} to {f['listener']}: {f['code']}" for f in inv["failing_streams"]]
    else:
        out.append(f"Streams: {inv['streams']}, none with a failure code.")
    if inv["events_given"]:
        if inv["missing_from_report"]:
            out.append("In the events file but not in this report:")
            out += [f"  {m['name']}" + (" (its last event was a disconnect, with no reconnect)"
                                         if m["disconnected_at_end_of_events"] else "")
                    for m in inv["missing_from_report"]]
        else:
            out.append("Every unit named in the events file is in this report.")
    if inv["lines_not_read"]:
        out.append(f"{inv['lines_not_read']} lines were not in a shape Stagewatch reads and were skipped.")
    out.append("Serial numbers, MAC addresses and computer names are not read or shown.")
    return out
