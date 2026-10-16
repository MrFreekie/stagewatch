"""The common reading layer for Shelly power meters: pure functions, no network, no hub.

A transport (today: HTTP polling; later possibly an outbound WebSocket) hands this layer a *status*
dictionary in the shape ``Shelly.GetStatus`` returns, where each component is a key such as
``"pm1:0"``. This layer turns it into a flat list of :class:`Reading` values. Components are chosen
by the keys that are present, never by the model name.

Values are kept exactly as the meter sent them (watt-hours stay watt-hours; nothing is averaged or
rounded). A field that is missing, ``null``, not a number or not finite gives ``None``: never zero.

Sources: Shelly Gen2+ API documentation, components PM1, EM, EMData, EM1 and Switch
(https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/PM1 and the pages for the others),
and one real ``Shelly.GetStatus`` from a Shelly PM Mini Gen3 (see tests/fixtures/shelly/).
Not read on purpose: relay state, ``by_minute`` energy, returned energy, error flags, em1 energy
(its field names are not confirmed).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ...core.model import Kind

# Components that can carry power readings. A key like "pm1:0" -> ("pm1", 0).
_COMPONENT_RE = re.compile(r"^(pm1|em1|em|emdata|switch):([0-7])$")   # no real meter has more than 8 of a kind
MAX_READINGS = 64    # per meter: a hostile reply cannot register thousands of entities

_PHASES = ("a", "b", "c")


@dataclass(frozen=True)
class Reading:
    """One value from a meter. ``object_id`` is stable and becomes part of the entity id and of the
    hardware key (``mac:<12hex>/<object_id>``)."""
    object_id: str            # e.g. "pm1_0_power", "em0_a_current"
    channel: str              # the component and number, e.g. "pm1_0" (groups readings on the card)
    group: str                # "", "a", "b", "c", "n" or "total" (em only)
    field: str                # "voltage" "current" "power" "apparent" "pf" "freq" "energy" "device_temp" ...
    kind: Kind
    decimals: int
    value: float | None
    cumulative: bool = False  # a running total (energy): never averaged, shown as received


def num(value: object) -> float | None:
    """A finite number, or None. Booleans, text, NaN and infinity are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _get(d: object, *path: str) -> object:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def components(status: object) -> list[tuple[str, int]]:
    """The power-capable components present in a status, in a fixed order: [("pm1", 0), ...]."""
    if not isinstance(status, dict):
        return []
    found = []
    for key in status:
        m = _COMPONENT_RE.match(key) if isinstance(key, str) else None
        if m and isinstance(status[key], dict):
            found.append((m.group(1), int(m.group(2))))
    return sorted(found)


def _pm1(status: dict, n: int) -> list[Reading]:
    comp = status[f"pm1:{n}"]
    ch = f"pm1_{n}"
    r = lambda field, kind, dec, value, cum=False: Reading(f"{ch}_{field}", ch, "", field, kind, dec, num(value), cum)  # noqa: E731
    return [
        r("voltage", Kind.VOLTAGE, 1, comp.get("voltage")),
        r("current", Kind.CURRENT, 3, comp.get("current")),
        r("power", Kind.POWER, 1, comp.get("apower")),
        r("freq", Kind.FREQUENCY, 1, comp.get("freq")),
        r("energy", Kind.ENERGY, 1, _get(comp, "aenergy", "total"), True),
        # Optional: the PM Mini Gen3 does not send these; shown only when a meter does (the PM1
        # documentation spells apparent power "aprtpower", with no underscore).
        r("apparent", Kind.APPARENT_POWER, 1, comp.get("aprtpower")),
        r("pf", Kind.POWER_FACTOR, 2, comp.get("pf")),
    ]


def _em1(status: dict, n: int) -> list[Reading]:
    comp = status[f"em1:{n}"]
    ch = f"em1_{n}"
    r = lambda field, kind, dec, value: Reading(f"{ch}_{field}", ch, "", field, kind, dec, num(value))  # noqa: E731
    return [
        r("voltage", Kind.VOLTAGE, 1, comp.get("voltage")),
        r("current", Kind.CURRENT, 3, comp.get("current")),
        r("power", Kind.POWER, 1, comp.get("act_power")),
        r("apparent", Kind.APPARENT_POWER, 1, comp.get("aprt_power")),
        r("pf", Kind.POWER_FACTOR, 2, comp.get("pf")),
        r("freq", Kind.FREQUENCY, 1, comp.get("freq")),
    ]


def _em(status: dict, n: int) -> list[Reading]:
    """Three-phase meter: per phase a/b/c, the neutral current and the totals."""
    comp = status[f"em:{n}"]
    ch = f"em{n}"
    out: list[Reading] = []
    for p in _PHASES:
        r = lambda field, kind, dec, key: out.append(  # noqa: E731
            Reading(f"{ch}_{p}_{field}", ch, p, field, kind, dec, num(comp.get(f"{p}_{key}"))))
        r("current", Kind.CURRENT, 3, "current")
        r("voltage", Kind.VOLTAGE, 1, "voltage")
        r("power", Kind.POWER, 1, "act_power")
        r("apparent", Kind.APPARENT_POWER, 1, "aprt_power")
        r("pf", Kind.POWER_FACTOR, 2, "pf")
        r("freq", Kind.FREQUENCY, 1, "freq")
    out.append(Reading(f"{ch}_n_current", ch, "n", "current", Kind.CURRENT, 3, num(comp.get("n_current"))))
    out.append(Reading(f"{ch}_total_current", ch, "total", "current", Kind.CURRENT, 3, num(comp.get("total_current"))))
    out.append(Reading(f"{ch}_total_power", ch, "total", "power", Kind.POWER, 1, num(comp.get("total_act_power"))))
    out.append(Reading(f"{ch}_total_apparent", ch, "total", "apparent", Kind.APPARENT_POWER, 1, num(comp.get("total_aprt_power"))))
    return out


def _emdata(status: dict, n: int) -> list[Reading]:
    """Running energy totals of a three-phase meter (watt-hours, as received)."""
    comp = status[f"emdata:{n}"]
    ch = f"em{n}"   # the same channel as its em:N component, so the card keeps them together
    out: list[Reading] = []
    for p in _PHASES:
        out.append(Reading(f"emdata{n}_{p}_energy", ch, p, "energy", Kind.ENERGY, 1,
                           num(comp.get(f"{p}_total_act_energy")), True))
        out.append(Reading(f"emdata{n}_{p}_energy_ret", ch, p, "energy_ret", Kind.ENERGY, 1,
                           num(comp.get(f"{p}_total_act_ret_energy")), True))
    out.append(Reading(f"emdata{n}_total_energy", ch, "total", "energy", Kind.ENERGY, 1,
                       num(comp.get("total_act")), True))
    out.append(Reading(f"emdata{n}_total_energy_ret", ch, "total", "energy_ret", Kind.ENERGY, 1,
                       num(comp.get("total_act_ret")), True))
    return out


def _switch(status: dict, n: int) -> list[Reading]:
    """A relay that also measures. A switch with no ``apower`` is a plain relay: nothing to read,
    and its on/off state is deliberately never read here."""
    comp = status[f"switch:{n}"]
    if "apower" not in comp:
        return []
    ch = f"switch_{n}"
    r = lambda field, kind, dec, value, cum=False: Reading(f"{ch}_{field}", ch, "", field, kind, dec, num(value), cum)  # noqa: E731
    return [
        r("power", Kind.POWER, 1, comp.get("apower")),
        r("voltage", Kind.VOLTAGE, 1, comp.get("voltage")),
        r("current", Kind.CURRENT, 3, comp.get("current")),
        r("freq", Kind.FREQUENCY, 1, comp.get("freq")),
        r("energy", Kind.ENERGY, 1, _get(comp, "aenergy", "total"), True),
        r("pf", Kind.POWER_FACTOR, 2, comp.get("pf")),
        # The meter's own internal temperature: about the device, never the air.
        r("device_temp", Kind.TEMPERATURE, 1, _get(comp, "temperature", "tC")),
    ]


_BUILDERS = {"pm1": _pm1, "em1": _em1, "em": _em, "emdata": _emdata, "switch": _switch}


def readings_from_status(status: object) -> list[Reading]:
    """Every reading in a status, in a stable order. Empty when the status has no power component."""
    out: list[Reading] = []
    if not isinstance(status, dict):
        return out
    for prefix, n in components(status):
        try:
            out.extend(_BUILDERS[prefix](status, n))
        except Exception:  # noqa: BLE001 - a strange component must never stop the others
            continue
    return out[:MAX_READINGS]


# --------------------------------------------------------------------- names for people
_FIELD_WORDS = {
    "voltage": "voltage", "current": "current", "power": "power", "apparent": "apparent power",
    "pf": "power factor", "freq": "frequency", "energy": "energy", "energy_ret": "energy returned",
    "device_temp": "meter temperature",
}
_GROUP_WORDS = {"a": "Line 1 (Brown)", "b": "Line 2 (Black)", "c": "Line 3 (Grey)", "n": "Neutral (Blue)", "total": "Total"}


def display_name(reading: Reading, channels_of_kind: int) -> str:
    """A short crew-readable name, e.g. "Power", "Channel 2 power", "Phase A current", "Total power".
    Channel numbers start at 1 (the meter itself counts from 0)."""
    word = _FIELD_WORDS.get(reading.field, reading.field)
    m = re.search(r"_(\d+)$", reading.channel)
    chan = f"Channel {int(m.group(1)) + 1}" if (m and channels_of_kind > 1) else ""
    group = _GROUP_WORDS.get(reading.group, "")
    lead = " ".join(x for x in (chan, group) if x)
    if lead:
        return f"{lead} {word}"
    return word[:1].upper() + word[1:]


def channel_counts(readings: list[Reading]) -> int:
    """How many channels the meter has (so single-channel meters get plain names)."""
    return len({r.channel for r in readings})


def component_words(status: object) -> list[str]:
    """Plain words for the components found, for the Test result: ["Power meter"], ..."""
    names = {"pm1": "Power meter", "em1": "Single-circuit meter", "em": "Three-phase meter",
             "emdata": "Energy totals (three-phase)", "switch": "Metering relay (read only)"}
    seen = []
    for prefix, n in components(status):
        if prefix == "switch" and "apower" not in status[f"switch:{n}"]:
            continue
        seen.append(names[prefix])
    out = []
    for w in seen:
        out.append(w if seen.count(w) == 1 else f"{w} x{seen.count(w)}")
    return sorted(set(out), key=out.index)
