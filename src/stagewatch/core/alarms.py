"""Threshold alarms with hysteresis, hold time and acknowledge.

Levels: 1 = advisory, 2 = alert, 3 = stop. An alarm raises once its
condition has held for hold_s, clears only after the value moves back past
the threshold by the hysteresis margin, and stays latched as "sounding"
until acknowledged. Ack silences; it never clears an active condition.
A stale input neither raises nor clears -- device loss is reported by its
own status alarm instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Threshold

LEVEL_NAMES = {1: "advisory", 2: "alert", 3: "stop"}


@dataclass
class ActiveAlarm:
    id: str
    level: int
    message: str
    since: float
    acked: bool = False
    silent: bool = False  # on-screen notice only: never sounds, never counts toward max_level

    def to_dict(self) -> dict:
        return {"id": self.id, "level": self.level, "level_name": LEVEL_NAMES.get(self.level, ""),
                "message": self.message, "since": self.since, "acked": self.acked, "silent": self.silent}


@dataclass
class AlarmChange:
    alarm: ActiveAlarm
    event: str  # "raise" | "clear"


ValueLookup = Callable[[str], "tuple[float | None, bool]"]
"""entity_id -> (value, is_stale)"""


def _describe(t: Threshold, value: float) -> str:
    label = t.label or t.entity
    if t.above is not None and value > t.above:
        return f"{label}: {value:.1f} above {t.above:g}"
    if t.below is not None and value < t.below:
        return f"{label}: {value:.1f} below {t.below:g}"
    return f"{label}: {value:.1f}"


class AlarmEngine:
    def __init__(self) -> None:
        self.active: dict[str, ActiveAlarm] = {}
        self._pending_since: dict[str, float] = {}

    @staticmethod
    def _tripped(t: Threshold, v: float) -> bool:
        return ((t.above is not None and v > t.above)
                or (t.below is not None and v < t.below))

    @staticmethod
    def _cleared(t: Threshold, v: float) -> bool:
        ok_above = t.above is None or v <= t.above - t.hysteresis
        ok_below = t.below is None or v >= t.below + t.hysteresis
        return ok_above and ok_below

    def evaluate(self, thresholds: list[Threshold], lookup: ValueLookup,
                 now: float) -> list[AlarmChange]:
        changes: list[AlarmChange] = []
        live_ids = set()
        for t in thresholds:
            if not t.enabled or (t.above is None and t.below is None):
                continue
            alarm_id = f"threshold:{t.id}"
            live_ids.add(alarm_id)
            value, stale = lookup(t.entity)
            if value is None or stale:
                self._pending_since.pop(alarm_id, None)
                continue
            active = self.active.get(alarm_id)
            if active is None:
                if self._tripped(t, value):
                    started = self._pending_since.setdefault(alarm_id, now)
                    if now - started >= t.hold_s:
                        self._pending_since.pop(alarm_id, None)
                        alarm = ActiveAlarm(alarm_id, t.level, _describe(t, value), now)
                        self.active[alarm_id] = alarm
                        changes.append(AlarmChange(alarm, "raise"))
                else:
                    self._pending_since.pop(alarm_id, None)
            else:
                if self._cleared(t, value):
                    del self.active[alarm_id]
                    changes.append(AlarmChange(active, "clear"))
                else:
                    active.message = _describe(t, value)
        # Thresholds deleted or disabled in config: drop their alarms.
        for alarm_id in [a for a in self.active if a.startswith("threshold:") and a not in live_ids]:
            changes.append(AlarmChange(self.active.pop(alarm_id), "clear"))
            self._pending_since.pop(alarm_id, None)
        return changes

    def set_condition(self, alarm_id: str, active: bool, level: int, message: str,
                      now: float, silent: bool = False) -> AlarmChange | None:
        """Raise/clear a non-threshold alarm (e.g. device missing)."""
        current = self.active.get(alarm_id)
        if active and current is None:
            alarm = ActiveAlarm(alarm_id, level, message, now, silent=silent)
            self.active[alarm_id] = alarm
            return AlarmChange(alarm, "raise")
        if not active and current is not None:
            del self.active[alarm_id]
            return AlarmChange(current, "clear")
        return None

    def ack_all(self) -> list[ActiveAlarm]:
        acked = [a for a in self.active.values() if not a.acked and not a.silent]
        for a in acked:
            a.acked = True
        return acked

    @property
    def sounding(self) -> bool:
        return any(not a.acked and not a.silent for a in self.active.values())

    @property
    def max_level(self) -> int:
        return max((a.level for a in self.active.values() if not a.silent), default=0)

    def to_list(self) -> list[dict]:
        return [a.to_dict() for a in sorted(self.active.values(),
                                             key=lambda a: (-a.level, a.since))]
