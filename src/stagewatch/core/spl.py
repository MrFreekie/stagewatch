"""Sound level (SPL) from measurement software: the shared vocabulary.

Pure data, no I/O. The measurement software (Smaart today) does all the sound-level maths. Stagewatch
only carries the numbers across, exactly as received: no averaging, smoothing, rounding, rollup or
calibration, and a value the software did not give is "not available" (``None``), never zero.

``METRICS`` is the list of values an admin can choose to record (up to ``MAX_SLOTS`` at a time, all
on one timeline). The keys are Stagewatch's own names. How a key maps to a field in a particular
program's messages lives with that program (integrations/smaart/mapping.py), nowhere else.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

MAX_SLOTS = 3
DEFAULT_SLOTS = ("a_slow", "c_slow", "laeq_15m")   # what an owner normally records

# Sanity bounds for one reading, in dB. Anything outside (or not a plain finite number) is treated
# as "not available": it is a damaged or hostile message, not a measurement.
LEVEL_MIN_DB = -50.0
LEVEL_MAX_DB = 250.0

_VERSION_RE = re.compile(r"[A-Za-z0-9 .+_()\-]{1,24}")

ENTITY_PREFIX = "spl."
DEVICE_ID = "spl"


@dataclass(frozen=True)
class Metric:
    key: str          # Stagewatch's own name; also the entity id suffix ("spl.a_slow")
    name: str         # short label on the card ("A Slow")
    weighting: str    # "A", "C" or "Z" (Z = unweighted)
    metric: str       # "SPL" (time-weighted), "Leq" (equivalent continuous) or "Peak"
    time_constant: str = ""   # "Slow" or "Fast" for time-weighted levels
    period: str = ""          # the Leq period, as the owner has set it in the software ("15 min")
    hint: str = ""

    @property
    def entity_id(self) -> str:
        return ENTITY_PREFIX + self.key

    def labels(self, slot: int) -> dict[str, str]:
        """The public labels carried on the entity (plain text; empty ones left out)."""
        out = {"weighting": self.weighting, "metric": self.metric, "slot": str(slot)}
        if self.time_constant:
            out["time_constant"] = self.time_constant
        if self.period:
            out["period"] = self.period
        return out


_LEQ_HINT = ("The software's own Leq, not worked out here. It is only a 15 minute figure if the "
             "software's Leq period is set to 15 minutes; Stagewatch cannot check that.")

METRICS: tuple[Metric, ...] = (
    Metric("a_slow", "A Slow", "A", "SPL", "Slow", hint="A-weighted sound level, Slow response"),
    Metric("c_slow", "C Slow", "C", "SPL", "Slow", hint="C-weighted sound level, Slow response"),
    Metric("z_slow", "Z Slow", "Z", "SPL", "Slow", hint="Unweighted (Z) sound level, Slow response"),
    Metric("a_fast", "A Fast", "A", "SPL", "Fast", hint="A-weighted sound level, Fast response"),
    Metric("c_fast", "C Fast", "C", "SPL", "Fast", hint="C-weighted sound level, Fast response"),
    Metric("z_fast", "Z Fast", "Z", "SPL", "Fast", hint="Unweighted (Z) sound level, Fast response"),
    Metric("laeq_15m", "LAeq 15 min", "A", "Leq", period="15 min", hint="A-weighted Leq. " + _LEQ_HINT),
    Metric("lceq_15m", "LCeq 15 min", "C", "Leq", period="15 min", hint="C-weighted Leq. " + _LEQ_HINT),
    Metric("lzeq_15m", "LZeq 15 min", "Z", "Leq", period="15 min", hint="Unweighted (Z) Leq. " + _LEQ_HINT),
    Metric("a_peak", "A Peak", "A", "Peak", hint="A-weighted peak level"),
    Metric("c_peak", "C Peak", "C", "Peak", hint="C-weighted peak level"),
    Metric("z_peak", "Z Peak", "Z", "Peak", hint="Unweighted (Z) peak level"),
)
METRIC_BY_KEY: dict[str, Metric] = {m.key: m for m in METRICS}


def clean_slots(value: object) -> list[str]:
    """A saved slot list, made safe: known keys only, each once, in order, at most MAX_SLOTS.
    Anything else is dropped. (The API is strict and refuses instead; see ``slots_error``.)"""
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for k in value:
        if isinstance(k, str) and k in METRIC_BY_KEY and k not in out:
            out.append(k)
    return out[:MAX_SLOTS]


def slots_error(value: object) -> str | None:
    """For the API: a fixed-text problem with a submitted slot list, or None."""
    if not isinstance(value, list) or len(value) > MAX_SLOTS:
        return f"Choose up to {MAX_SLOTS} values"
    if any(not isinstance(k, str) or k not in METRIC_BY_KEY for k in value):
        return "Unknown sound level value"
    if len(set(value)) != len(value):
        return "Each value can only be chosen once"
    return None


def clean_level(value: object) -> float | None:
    """One reading exactly as received, or None ("not available"). Only a real number counts:
    not a bool, a string, NaN, infinity or something outside the sanity bounds. The number itself
    is never changed (an int becomes the same float)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        v = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(v) or not LEVEL_MIN_DB <= v <= LEVEL_MAX_DB:
        return None
    return v


@dataclass(frozen=True)
class SplReading:
    """One update from a source: the time it was received and the value of each metric the source
    could give, keyed by the software's own metric name. A metric that is not in ``values`` (or is
    None) is not available. ``source`` is the input the values belong to, as the software names it
    ("" = the source does not say, so the values apply to every slot)."""
    ts: float
    values: dict[str, float | None] = field(default_factory=dict)
    version: str = ""   # the software's version as it reported it, cleaned (see clean_version)
    source: str = ""


INPUT_NAME_MAX = 80
METRIC_NAME_MAX = 64


def _clean_text(value: object, cap: int) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(" " if ch.isspace() else ch for ch in value if ch.isspace() or unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())[:cap].strip()


def clean_input_name(value: object) -> str:
    """The name of the input a meter is tied to, as the software wrote it, made safe to show: text
    only, control characters removed, spaces tidied, at most INPUT_NAME_MAX characters, "" if unknown.
    Markup is not stripped here because it is only ever shown as plain text (textContent)."""
    return _clean_text(value, INPUT_NAME_MAX)


LOCATION_MAX = 40
LOCATIONS_MAX = 8   # per-input locations kept

# The timeline's vertical range: "auto" fits the data, "custom" is exactly min..max (dB).
CHART_MIN_DEFAULT, CHART_MAX_DEFAULT = 22.0, 145.0   # the prefilled custom values
CHART_LIMIT_MIN, CHART_LIMIT_MAX = 0.0, 200.0
CHART_MIN_SPAN = 10.0


def chart_range_error(lo: object, hi: object) -> str | None:
    """Fixed-text problem with a custom graph range, or None. Real numbers only (no bool, text, NaN)."""
    nums = [v for v in (lo, hi) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    if len(nums) != 2:
        return "The graph range must be two numbers"
    if not (CHART_LIMIT_MIN <= lo <= CHART_LIMIT_MAX and CHART_LIMIT_MIN <= hi <= CHART_LIMIT_MAX):
        return f"The graph range must be between {CHART_LIMIT_MIN:g} and {CHART_LIMIT_MAX:g} dB"
    if hi - lo < CHART_MIN_SPAN:
        return f"The graph range must span at least {CHART_MIN_SPAN:g} dB, with Min below Max"
    return None


def clean_location(value: object) -> str:
    """The owner's own label for where the meter is ("FOH", "Stage left"): plain text, control and
    format characters removed, spaces tidied, at most LOCATION_MAX characters, "" if not text. Markup
    is not stripped because it is only ever shown as plain text (textContent). Display only: it is
    never part of an entity id, a stored key or a series."""
    return _clean_text(value, LOCATION_MAX)


def clean_metric_name(value: object) -> str:
    """A metric name as the software wrote it ("SPL A Slow"), made safe in the same way as an input
    name, at most METRIC_NAME_MAX characters. The cleaned text is what is stored and compared, on both
    sides, so it always matches itself."""
    return _clean_text(value, METRIC_NAME_MAX)


# ------------------------------------------------------------------ the three slots
# A slot is (input source, metric): both are Smaart's own text, chosen from Smaart's lists. The source
# "" means "the first input Smaart lists". Older settings held only a Stagewatch metric key per slot;
# these are the three that have a known Smaart name (the wording is from Smaart's own web page; the
# "LAeq 15" one is the owner's expectation and is checked against Smaart's metric list at run time).
# The Smaart wording of everything else lives in integrations/smaart/mapping.py.
LEGACY_SMAART_NAMES: dict[str, str] = {"a_slow": "SPL A Slow", "c_slow": "SPL C Slow", "laeq_15m": "LAeq 15"}
_LEGACY_BY_NAME = {v: k for k, v in LEGACY_SMAART_NAMES.items()}


def slots_from_legacy(keys: object) -> list[tuple[str, str]]:
    """Older metric-only slots as (source, metric) pairs, source "" (the first input). A key with no
    known Smaart name keeps its own text as the metric: Smaart never lists it, so it shows as "not
    available" rather than being guessed at."""
    return [("", LEGACY_SMAART_NAMES.get(k, k)) for k in clean_slots(keys)]


def legacy_keys_for(pairs: list[tuple[str, str]]) -> list[str]:
    """The older-style key list for slots on the first input (kept in the file so an older build
    still shows what it can)."""
    return [_LEGACY_BY_NAME[m] for s, m in pairs if s == "" and m in _LEGACY_BY_NAME]


def slot_ids(pairs: list[tuple[str, str]]) -> list[str]:
    """Stable, unique entity ids for the slots. The three older defaults on the first input keep
    their old ids (spl.a_slow ...) so their history carries on; anything else is spl.<metric> with
    the source appended when one is chosen. Two slots never share an id."""
    from .model import slugify
    out: list[str] = []
    for source, metric in pairs:
        key = _LEGACY_BY_NAME.get(metric) or slugify(metric)
        eid = ENTITY_PREFIX + key + (("." + slugify(source)[:40]) if source else "")
        base, n = eid, 2
        while eid in out:
            eid, n = f"{base}_{n}", n + 1
        out.append(eid)
    return out


def slot_labels(n: int, metric: str, source: str) -> dict[str, str]:
    """Public labels for slot ``n`` (1 to 3). ``smaart_name`` is Smaart's own text for the metric, which
    the card shows as it is; the weighting and so on are added only for the metrics Stagewatch knows."""
    key = _LEGACY_BY_NAME.get(metric)
    out = dict(METRIC_BY_KEY[key].labels(n)) if key else {"slot": str(n)}
    out["smaart_name"] = metric
    if source:
        out["source"] = source
    return out


def clean_version(value: object) -> str:
    """A version string from the software, made safe to show: at most 24 characters of letters,
    digits, spaces and . + _ ( ) -  (so nothing that could be markup or a control character)."""
    if not isinstance(value, str):
        return ""
    v = value.strip()
    return v if _VERSION_RE.fullmatch(v) else ""
