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
    could give. A metric that is not in ``values`` (or is None) is not available."""
    ts: float
    values: dict[str, float | None] = field(default_factory=dict)
    version: str = ""   # the software's version as it reported it, cleaned (see clean_version)


def clean_version(value: object) -> str:
    """A version string from the software, made safe to show: at most 24 characters of letters,
    digits, spaces and . + _ ( ) -  (so nothing that could be markup or a control character)."""
    if not isinstance(value, str):
        return ""
    v = value.strip()
    return v if _VERSION_RE.fullmatch(v) else ""
