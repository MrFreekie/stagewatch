"""From an RME unit's view to what Stagewatch shows: entity values, the bounded public message, alarm
conditions, status text and change markers. Pure functions (plus the marker rate limiter, which takes
its clock as an argument), so every rule can be tested without a socket.

Private by construction: the public message is built field by field from the view. The serial number,
address, group name, stream ids, input labels and raw frames are never passed in, so they cannot leak.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

from ...core.model import Kind
from .parse import SoundItem, View

LEVEL_NONE, LEVEL_ADVISORY, LEVEL_ALERT = 0, 1, 2

# (suffix, name, kind, decimals)
ENTITY_SPECS = (
    ("core_temp", "Core temperature", Kind.TEMPERATURE, 1),
    ("psu_1", "Power supply 1", Kind.CONTACT, 0),
    ("psu_2", "Power supply 2", Kind.CONTACT, 0),
)

STATUS_WORDS = {
    "ok": ("OK", "tick"),
    "check": ("CHECK", "triangle"),
    "warning": ("WARNING", "cross"),
    "offline": ("OFFLINE", "dash"),
    "starting": ("Starting", "dash"),
}

# Fixed device-status wording (the hub shows this on the Integrations page and in alarm lines).
DETAIL_OK = "Receiving status"
DETAIL_CONNECTING = "Connecting"


# ------------------------------------------------------------------ entities
def entity_values(view: View | None) -> dict[str, float]:
    """The numbers to record, by entity suffix. A value the unit does not give (or gave out of range)
    is simply absent: nothing is invented and nothing is zeroed."""
    out: dict[str, float] = {}
    if view is None:
        return out
    if view.temp_c is not None:
        out["core_temp"] = view.temp_c
    if view.psu is not None:
        out["psu_1"] = 1.0 if view.psu[0] else 0.0
        out["psu_2"] = 1.0 if view.psu[1] else 0.0
    return out


# ------------------------------------------------------------------ levels
def psu_off(view: View) -> list[int]:
    return [i + 1 for i, on in enumerate(view.psu) if not on] if view.psu is not None else []


def psu_level(view: View, lost_live: frozenset[int]) -> int:
    """A supply that is off is an Alert when the unit's own redundancy alert is on. Without it, only a
    supply seen going off while Stagewatch was watching counts (Advisory): a unit with one supply fitted
    reports the other as off all day, and that is not a fault."""
    off = psu_off(view)
    if not off:
        return LEVEL_NONE
    if view.redundancy is True:
        return LEVEL_ALERT
    return LEVEL_ADVISORY if any(n in lost_live for n in off) else LEVEL_NONE


def lock_level(view: View) -> int:
    return LEVEL_ADVISORY if view.clock_locked is False else LEVEL_NONE


def overall_level(view: View, lost_live: frozenset[int] = frozenset()) -> int:
    return max([psu_level(view, lost_live), lock_level(view), *[s.level for s in view.sound]], default=0)


def conditions(view: View, lost_live: frozenset[int] = frozenset()) -> dict[str, tuple[int, str]]:
    """The alarm conditions that are on right now, as {key: (level, text without the unit name)}.
    All of them are raised silent: they appear on screen and are logged, and never sound."""
    out: dict[str, tuple[int, str]] = {}
    lvl = psu_level(view, lost_live)
    if lvl:
        out["psu"] = (lvl, "power supply " + " and ".join(str(n) for n in psu_off(view)) + " off")
    if lock_level(view):
        out["lock"] = (LEVEL_ADVISORY, "clock not locked")
    for item in view.sound:
        if item.level:
            text = f"{item.area}: {item.word}" + (f" ({item.cause})" if item.cause else "")
            out["sound:" + item.area] = (item.level, text)
    return out


def status_detail(view: View, lost_live: frozenset[int] = frozenset()) -> str:
    """Short fixed wording for the device status. Never carries text from the unit."""
    lvl = overall_level(view, lost_live)
    if lvl == LEVEL_NONE:
        return DETAIL_OK
    if psu_level(view, lost_live) == lvl:
        return "Power supply off"
    worst = max(view.sound, key=lambda s: s.level, default=None)
    if worst is not None and worst.level == lvl:
        return ("Warning" if lvl == LEVEL_ALERT else "Check") + ": see the RME card"
    return "Clock not locked"


# ------------------------------------------------------------------ the public message
def bucket_age(age: float | None) -> int | None:
    """Ages in coarse steps so the public message does not change every second."""
    if age is None:
        return None
    age = max(0.0, age)
    if age < 10:
        return 0
    if age < 120:
        return int(age // 10) * 10
    return int(age // 60) * 60


def unit_status(view: View | None, phase: str, lost_live: frozenset[int]) -> str:
    """ok / check / warning / offline / starting. ``phase``: starting, waiting, up or down."""
    if phase == "up" and view is not None:
        lvl = overall_level(view, lost_live)
        return "warning" if lvl >= LEVEL_ALERT else "check" if lvl >= LEVEL_ADVISORY else "ok"
    if view is not None or phase == "down":
        return "offline"
    return "starting"


def unit_message(uid: str, name: str, view: View | None, *, phase: str, frame_age_s: float | None,
                 text_age_s: float | None, lost_live: frozenset[int] = frozenset()) -> dict:
    """One unit as the dashboard card gets it. Exact field set, asserted by a test. No serial, MAC, address,
    group name, stream id or input label ever appears here."""
    status = unit_status(view, phase, lost_live)
    word, symbol = STATUS_WORDS[status]
    v = view or View()
    quiet = None
    if phase == "up" and text_age_s is not None and text_age_s >= 120:
        quiet = int(text_age_s // 60)
    return {
        "id": uid,
        "name": name,
        "model": v.model,
        "status": status,
        "status_word": word,
        "status_symbol": symbol,
        "level": overall_level(view, lost_live) if view is not None and phase == "up" else 0,
        "link_up": phase == "up",
        "last_frame_age_s": bucket_age(frame_age_s),
        "no_updates_min": quiet,
        "temp_c": v.temp_c,
        "psu": None if v.psu is None else [{"n": 1, "on": v.psu[0]}, {"n": 2, "on": v.psu[1]}],
        "redundancy": v.redundancy,
        "clock": {"source_label": v.clock_source, "locked": v.clock_locked,
                  "rate_hz": v.clock_rate_hz, "state": v.clock_state},
        "preset": {"active_no": v.preset_active, "loaded_no": v.preset_loaded,
                   "active_name": v.preset_active_name, "loaded_name": v.preset_loaded_name,
                   "changed": v.preset_changed},
        "avb": {"active": v.avb_active, "total": v.avb_total,
                "states": [{"word": w, "count": n} for w, n in v.avb_states]},
        "soundness": [s.public() for s in v.sound],
        "firmware": v.firmware,
    }


UNIT_FIELDS = frozenset(unit_message("x", "x", None, phase="starting", frame_age_s=None, text_age_s=None))


# ------------------------------------------------------------------ markers
@dataclass(frozen=True)
class MarkerEvent:
    key: str
    text: str
    hidden: bool = False
    restored: bool = False      # puts something back to normal: never held back by the 10 s window


def _by_area(items: tuple[SoundItem, ...]) -> dict[str, SoundItem]:
    return {i.area: i for i in items}


def marker_events(old: View, new: View) -> list[MarkerEvent]:
    """Changes between two views seen one after the other on a live connection. Never called for a
    baseline or a resync, and never for the gap while a unit was away."""
    out: list[MarkerEvent] = []
    if old.psu is not None and new.psu is not None:
        for i in (0, 1):
            if old.psu[i] and not new.psu[i]:
                out.append(MarkerEvent(f"psu{i + 1}", f"PSU {i + 1} lost"))
            elif not old.psu[i] and new.psu[i]:
                out.append(MarkerEvent(f"psu{i + 1}", f"PSU {i + 1} restored", restored=True))
    if old.clock_key and new.clock_key and old.clock_key != new.clock_key:
        out.append(MarkerEvent("clock_source", f"clock source {old.clock_source} to {new.clock_source}"))
    if old.clock_locked is True and new.clock_locked is False:
        out.append(MarkerEvent("clock_lock", "clock lost lock"))
    elif old.clock_locked is False and new.clock_locked is True:
        out.append(MarkerEvent("clock_lock", "clock locked", restored=True))
    if old.preset_loaded is not None and new.preset_loaded is not None and old.preset_loaded != new.preset_loaded:
        out.append(MarkerEvent("preset", f"preset loaded changed {old.preset_loaded} to {new.preset_loaded}"))
    before, after = _by_area(old.sound), _by_area(new.sound)
    for area in [*after, *[a for a in before if a not in after]]:
        was, now = before.get(area), after.get(area)
        was_word = was.word if was else "OK"
        now_word = now.word if now else "OK"
        if was_word == now_word:
            continue
        worst = max(was.level if was else 0, now.level if now else 0)
        back = not (now is not None and (now.level or not now.known))
        if back:
            text = f"{area}: back to {now_word}"
        else:
            text = f"{area}: {now.word}" + (f" ({now.cause})" if now.cause else "")
        out.append(MarkerEvent("sound:" + area, text, hidden=worst < LEVEL_ALERT, restored=back))
    return out


def lost_live_after(old: View, new: View, lost: frozenset[int]) -> frozenset[int]:
    """The supplies seen going off during this connection: added on a live loss, removed on return."""
    keep = set(lost)
    if old.psu is not None and new.psu is not None:
        for i in (0, 1):
            if old.psu[i] and not new.psu[i]:
                keep.add(i + 1)
            elif new.psu[i]:
                keep.discard(i + 1)
    return frozenset(keep)


DEDUPE_S = 10.0
RATE_MAX = 20
RATE_WINDOW_S = 60.0
OVERFLOW_TEXT = "further RME changes not marked"


class MarkerPolicy:
    """Dedupe and rate-limit the markers of ONE unit: the same marker (key and text) at most once in 10 s,
    at most 20 a minute and then a single "further changes not marked" until the minute has passed. A marker
    that puts something back to normal (``restore``) is never held back by the 10 s window. Changes held
    back by the cap are remembered, and when the minute has passed the final state of each is marked if it
    differs from the last marker for that key. ``add(text, hidden)`` makes the marker; ``clock`` is
    monotonic seconds (injected in tests); ``overflow_text`` gives the notice wording (names the unit)."""

    def __init__(self, add: Callable[[str, bool], None], clock: Callable[[], float],
                 overflow_text: Callable[[], str] | None = None) -> None:
        self._add, self._clock = add, clock
        self._overflow_text = overflow_text or (lambda: OVERFLOW_TEXT)
        self._last: dict[tuple[str, str], float] = {}
        self._last_text: dict[str, str] = {}
        self._pending: dict[str, tuple[str, bool]] = {}
        self._times: deque[float] = deque()
        self._overflowed = False

    def _expire(self, now: float) -> None:
        while self._times and now - self._times[0] >= RATE_WINDOW_S:
            self._times.popleft()
        if len(self._times) < RATE_MAX:
            self._overflowed = False
        if len(self._last) > 200:
            self._last = {k: t for k, t in self._last.items() if now - t < DEDUPE_S}

    def _send(self, now: float, key: str, text: str, hidden: bool) -> None:
        self._last[(key, text)] = now
        self._last_text[key] = text
        self._pending.pop(key, None)
        self._times.append(now)
        self._add(text, hidden)

    def poll(self) -> None:
        """Call now and then (once a second is plenty): once the minute is over, mark the final state of
        anything the cap held back."""
        now = self._clock()
        self._expire(now)
        for key, (text, hidden) in list(self._pending.items()):
            if len(self._times) >= RATE_MAX:
                break
            if self._last_text.get(key) == text:
                del self._pending[key]
            else:
                self._send(now, key, text, hidden)

    def emit(self, key: str, text: str, hidden: bool = False, restore: bool = False) -> bool:
        now = self._clock()
        self.poll()
        last = self._last.get((key, text))
        if not restore and last is not None and now - last < DEDUPE_S:
            return False
        if len(self._times) >= RATE_MAX:
            self._pending[key] = (text, hidden)
            if not self._overflowed:
                self._overflowed = True
                self._add(self._overflow_text(), False)
            return False
        self._send(now, key, text, hidden)
        return True


def back_text(outage_s: float) -> str:
    """"back after 4 min (readings missing meanwhile)". Whole minutes from one minute, seconds below."""
    if outage_s < 60:
        span = f"{max(1, int(round(outage_s)))} s"
    else:
        span = f"{int(round(outage_s / 60))} min"
    return f"back after {span} (readings missing meanwhile)"
