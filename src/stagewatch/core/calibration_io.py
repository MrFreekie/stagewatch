"""Calibration export and import (plan §1.9): move the calibration records between hubs, or keep
a copy before a rebuild. Pure functions on a ``Config`` and plain data, no I/O.

The file holds hardware keys (MACs), so it is admin-only like the rest of the hardware map. Reading
a file never changes anything until the caller applies the plan; ``plan_import`` only builds the
diff. Errors are fixed text and never repeat anything from the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from .calibration import _with_entry, utc_now_iso
from .config import Calibration, CalibrationEntry, Config, valid_calibration_key

__all__ = ["FORMAT", "MAX_RECORDS", "ImportError_", "build_export", "plan_import", "apply_import"]

FORMAT = 1
MAX_RECORDS = 500   # a show rig has a few dozen sensors; this only stops a runaway file

# The fields that make two records "the same calibration" (history is the log, not the setting).
_SETTING_FIELDS = ("offset", "include_in_average", "accuracy", "accuracy_basis", "role")

BAD_FILE = "That is not a Stagewatch calibration file. Nothing has been changed."
BAD_VERSION = "This calibration file is from a different version of Stagewatch. Nothing has been changed."
TOO_MANY = "That calibration file has too many sensors. Nothing has been changed."
BAD_RECORD = "That calibration file has a sensor entry that could not be read. Nothing has been changed."


class ImportError_(ValueError):
    """The file can't be used. ``str()`` is the fixed plain-words message for the admin."""


def build_export(config: Config, site: str, now: float | None = None) -> dict:
    return {
        "format": FORMAT,
        "exported": utc_now_iso(now),
        "site": site,
        "calibrations": {k: v.model_dump(mode="json") for k, v in sorted(config.calibrations.items())},
    }


@dataclass
class ImportPlan:
    added: list[str] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)     # {key, fields: {name: {from, to}}}
    unchanged: list[str] = field(default_factory=list)
    unknown_hardware: list[str] = field(default_factory=list)   # keys no known board has (still imported)
    refused: list[str] = field(default_factory=list)      # sensors recorded as received: never adjusted
    records: dict[str, Calibration] = field(default_factory=dict)   # what apply writes (added + changed)

    def summary(self) -> dict:
        return {"added": self.added, "changed": self.changed, "unchanged": self.unchanged,
                "unknown_hardware": self.unknown_hardware, "refused": self.refused}


def plan_import(config: Config, data: Any, known_nodes: set[str],
                refuse: Callable[[str], bool] = lambda key: False) -> ImportPlan:
    """Check a file and work out what importing it would do. Changes nothing.

    ``known_nodes``: node keys (``mac:…`` / ``dev:…``) of boards the hub knows. ``refuse(key)`` is
    true for a sensor that is recorded exactly as received (sound levels, power meters)."""
    if not isinstance(data, dict) or not isinstance(data.get("calibrations"), dict):
        raise ImportError_(BAD_FILE)
    if data.get("format") != FORMAT:
        raise ImportError_(BAD_VERSION)
    items = data["calibrations"]
    if len(items) > MAX_RECORDS:
        raise ImportError_(TOO_MANY)
    plan = ImportPlan()
    for key in sorted(items, key=str):
        if not valid_calibration_key(key):
            raise ImportError_(BAD_RECORD)
        try:
            incoming = Calibration.model_validate(items[key])
        except ValidationError:
            raise ImportError_(BAD_RECORD) from None
        if refuse(key):
            plan.refused.append(key)
            continue
        node = key.rsplit("/", 1)[0]
        if node not in known_nodes:
            plan.unknown_hardware.append(key)
        current = config.calibrations.get(key)
        if current is None:
            plan.added.append(key)
            plan.records[key] = incoming
            continue
        diff = {f: {"from": getattr(current, f), "to": getattr(incoming, f)}
                for f in _SETTING_FIELDS if getattr(current, f) != getattr(incoming, f)}
        if diff:
            plan.changed.append({"key": key, "fields": diff})
            plan.records[key] = incoming
        else:
            plan.unchanged.append(key)
    return plan


def apply_import(config: Config, plan: ImportPlan, now: float | None = None) -> list[str]:
    """Write a plan. Each record gets an ``import`` history entry; the file's own history is not
    copied (this hub's log only holds what happened here). Returns the keys written."""
    date = utc_now_iso(now)
    for key, incoming in plan.records.items():
        current = config.calibrations.get(key)
        base = current if current is not None else Calibration()
        entry = CalibrationEntry(offset=incoming.offset, date=date, method="import", reference="file")
        config.calibrations[key] = incoming.model_copy(update={
            "history": _with_entry(base, entry), "chip": base.chip, "applied_on_node": False})
    return list(plan.records)
