"""Ontime messages and addresses: pure functions, no I/O.

What is read (verified on Ontime 4.14.0, fixtures in tests/fixtures/ontime/):

* WebSocket ``/ws``: ``{"tag": "runtime-data", "payload": {"clock": <ms>, ...}}`` about once a
  second. ``clock`` is whole milliseconds since midnight on the **Ontime computer's own clock**.
  Every other ``tag`` (``log``, ``client-init``, ``client-list``, ...) is ignored.
* ``GET /api/poll``: ``{"payload": {"clock": <ms>, ...}}`` (HTTP 202).
* ``GET /api/version``: ``{"payload": "4.14.0"}`` (HTTP 202).

From ``runtime-data`` we keep the clock and, for the Ontime Timer card, the main timer
(``timer``) and a few fields of the loaded event (``eventNow``: title, timer type, warning and
danger times). Nothing else in a message is read or kept: no notes, cues, colours, custom fields,
messages or other events. The first message is full; later ones carry only ``timer`` and
``clock``, so the timer state is merged with the last ``eventNow`` we saw.

For the Ontime Rundown card we also keep the counters and times of the ``rundown`` block and the
``offset`` block (never a title, id or list), merged the same way. The documented shape
(``rundown.offset``, ``rundown.expectedEnd``) and the one real 4.14.0 sends (a top-level ``offset``
block) are both read.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from ...core.ontimerundown import (MAX_DAY, MAX_EVENTS, MAX_OFFSET_MS, MAX_TIME_MS, OFFSET_MODES,
                                   RundownState)
from ...core.ontimetimer import PHASES, PLAYBACKS, TIMER_TYPES, TimerState, clean_title
from ...core.wallclock import valid_clock_ms

HOUR_MS = 3_600_000
MAX_SIGNED_MS = 100 * HOUR_MS      # |current| and |elapsed| must stay under 100 h
MAX_DURATION_MS = 24 * HOUR_MS     # duration, added time and the warning times: within a day

MAX_BYTES = 1024 * 1024           # largest message or response we accept (1 MiB)
HTTP_PATHS = ("/api/version", "/api/poll")   # the only HTTP paths we ever request
WS_PATH = "/ws"                   # and the only WebSocket path
HTTP_OK = (200, 202)              # Ontime 4.14 answers its read endpoints with 202

_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}")

Kind = Literal["clock", "ignored", "invalid"]


def parse_ws_runtime(raw: str | bytes | bytearray) -> tuple[Kind, int | None, dict | None]:
    """One WebSocket message, decoded once -> (kind, clock ms, the ``runtime-data`` payload).

    kind is "clock" (valid clock), "ignored" (not ``runtime-data``, too big or not JSON; payload
    None) or "invalid" (``runtime-data`` whose clock is missing or out of range; the payload is
    still returned if it is a dict, because its timer may be fine)."""
    if len(raw) > MAX_BYTES:
        return "ignored", None, None
    try:
        msg = json.loads(raw)
    except (ValueError, RecursionError):  # includes bad UTF-8
        return "ignored", None, None
    if not isinstance(msg, dict) or msg.get("tag") != "runtime-data":
        return "ignored", None, None
    payload = msg.get("payload")
    if not isinstance(payload, dict):
        return "invalid", None, None
    ms = valid_clock_ms(payload.get("clock"))
    return ("clock", ms, payload) if ms is not None else ("invalid", None, payload)


def parse_ws_message(raw: str | bytes | bytearray) -> tuple[Kind, int | None]:
    """One WebSocket message -> ("clock", ms), ("ignored", None) for anything that is not
    ``runtime-data`` (or is too big or not JSON), or ("invalid", None) for ``runtime-data`` whose
    ``clock`` is missing or out of range."""
    kind, ms, _ = parse_ws_runtime(raw)
    return kind, ms


def poll_payload(body: object) -> dict | None:
    """The decoded ``/api/poll`` body -> its ``payload`` dict, or None."""
    payload = body.get("payload") if isinstance(body, dict) else None
    return payload if isinstance(payload, dict) else None


def parse_poll(body: object) -> int | None:
    """The decoded ``/api/poll`` body -> clock ms, or None if it isn't a valid reading."""
    payload = poll_payload(body)
    return valid_clock_ms(payload.get("clock")) if payload is not None else None


# ------------------------------------------------------------------ the Ontime Timer
def _int_or_none(value: object, lo: int, hi: int):
    """(ok, value): a whole number within lo..hi, or null. bool, float, str and out-of-range are not ok."""
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        return False, None
    return True, value


def _choice(value: object, known: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in known else "unknown"


def _event_fields(event: object) -> dict:
    """The few ``eventNow`` fields we keep, validated. A bad single field just becomes unknown/None:
    a rundown quirk must not hide the timer."""
    if not isinstance(event, dict):
        return {"has_event": False, "title": "", "timer_type": "none", "warn_ms": None, "danger_ms": None}
    return {
        "has_event": True,
        "title": clean_title(event.get("title")),
        "timer_type": _choice(event.get("timerType"), TIMER_TYPES),
        "warn_ms": _int_or_none(event.get("timeWarning"), 0, MAX_DURATION_MS)[1],
        "danger_ms": _int_or_none(event.get("timeDanger"), 0, MAX_DURATION_MS)[1],
    }


def parse_timer(payload: object, prev: TimerState | None = None) -> TimerState | None:
    """Merge one ``runtime-data`` payload into the last timer state.

    * No ``timer`` key (a clock-only message): ``prev`` unchanged; with an ``eventNow`` key the
      event fields (title, thresholds) are merged into ``prev``.
    * ``timer`` present and valid: its fields replace the old ones; the event part comes from
      ``eventNow`` if the message has that key (null clears it), else from ``prev``.
    * ``timer`` present but unreadable (not an object, or a number out of range, a float, a bool,
      a string where a number belongs): None. The caller shows "can't read the timer".

    Unseen ``playback`` / ``phase`` / ``timerType`` values become "unknown" (shown neutral).
    Real Ontime 4.14.0 output has only been seen with playback "roll" and phase "default"."""
    if not isinstance(payload, dict):
        return prev
    if "timer" not in payload:
        # An event change without a timer: merge just the event fields into the last state.
        if "eventNow" in payload and prev is not None:
            return dataclasses.replace(prev, **_event_fields(payload["eventNow"]))
        return prev
    timer = payload["timer"]
    if not isinstance(timer, dict):
        return None
    checks = (_int_or_none(timer.get("current"), -MAX_SIGNED_MS, MAX_SIGNED_MS),
              _int_or_none(timer.get("duration"), 0, MAX_DURATION_MS),
              _int_or_none(timer.get("elapsed"), -MAX_SIGNED_MS, MAX_SIGNED_MS),
              _int_or_none(timer.get("addedTime"), -MAX_DURATION_MS, MAX_DURATION_MS))
    if not all(ok for ok, _ in checks):
        return None
    current, duration, elapsed, added = (v for _, v in checks)
    if "eventNow" in payload:
        ev = _event_fields(payload["eventNow"])
    elif prev is not None:
        ev = {"has_event": prev.has_event, "title": prev.title, "timer_type": prev.timer_type,
              "warn_ms": prev.warn_ms, "danger_ms": prev.danger_ms}
    else:
        ev = _event_fields(None)   # no event seen yet
    return TimerState(
        playback=_choice(timer.get("playback"), PLAYBACKS), phase=_choice(timer.get("phase"), PHASES),
        current_ms=current, duration_ms=duration, elapsed_ms=elapsed, added_ms=added or 0, **ev)


# ---------------------------------------------------------------- the Ontime Rundown
_RUNDOWN_KEYS = {   # Ontime key -> (RundownState field, lo, hi)
    "selectedEventIndex": ("selected_index", 0, MAX_EVENTS),
    "numEvents": ("num_events", 0, MAX_EVENTS),
    "plannedStart": ("planned_start_ms", 0, MAX_TIME_MS),
    "plannedEnd": ("planned_end_ms", 0, MAX_TIME_MS),
    "actualStart": ("actual_start_ms", 0, MAX_TIME_MS),
    "currentDay": ("current_day", 0, MAX_DAY),
    "offset": ("doc_offset_ms", -MAX_OFFSET_MS, MAX_OFFSET_MS),          # documented shape
    "expectedEnd": ("doc_expected_end_ms", 0, MAX_TIME_MS),              # documented shape
}
_OFFSET_KEYS = {    # the real 4.14.0 shape: the top-level "offset" block
    "absolute": ("offset_absolute_ms", -MAX_OFFSET_MS, MAX_OFFSET_MS),
    "relative": ("offset_relative_ms", -MAX_OFFSET_MS, MAX_OFFSET_MS),
    "expectedRundownEnd": ("offset_expected_end_ms", 0, MAX_TIME_MS),
}
_RUNDOWN_FIELDS = tuple(v[0] for v in _RUNDOWN_KEYS.values())
_OFFSET_FIELDS = tuple(v[0] for v in _OFFSET_KEYS.values()) + ("offset_mode",)


def _merge_block(block: dict, keys: dict) -> dict | None:
    """The fields a block sets (only the keys it contains; null clears), or None if any is unreadable."""
    out = {}
    for key, (field, lo, hi) in keys.items():
        if key in block:
            ok, value = _int_or_none(block[key], lo, hi)
            if not ok:
                return None
            out[field] = value
    return out


def parse_rundown(payload: object, prev: RundownState | None = None) -> RundownState | None:
    """Merge one ``runtime-data`` payload into the last rundown state.

    * Neither a ``rundown`` nor an ``offset`` key (the usual clock-and-timer message): ``prev``
      unchanged (which may be None).
    * A block merges key by key into ``prev`` (a key it lacks keeps its old value; null clears it).
      ``"rundown": null`` or ``"offset": null`` clears that block.
    * Anything unreadable (not an object, a float or bool or string where a whole number belongs,
      a number out of range, a selected index past ``numEvents``): None. The caller shows
      "can't read the rundown". A mode other than absolute/relative is kept as no mode, so the
      offset it labels is not used (never guessed).

    Real Ontime 4.14.0 output has only been seen with the first message carrying both blocks."""
    if not isinstance(payload, dict) or ("rundown" not in payload and "offset" not in payload):
        return prev
    state = prev or RundownState()
    changes: dict = {}
    if "rundown" in payload:
        block = payload["rundown"]
        if block is None:
            changes.update({f: None for f in _RUNDOWN_FIELDS})
        elif isinstance(block, dict):
            got = _merge_block(block, _RUNDOWN_KEYS)
            if got is None:
                return None
            changes.update(got)
        else:
            return None
    if "offset" in payload:
        block = payload["offset"]
        if block is None:
            changes.update({f: None for f in _OFFSET_FIELDS})
        elif isinstance(block, dict):
            got = _merge_block(block, _OFFSET_KEYS)
            if got is None:
                return None
            changes.update(got)
            if "mode" in block:
                changes["offset_mode"] = block["mode"] if block["mode"] in OFFSET_MODES else None
        else:
            # A bare number (a layout we have not seen): treat it like the documented rundown.offset.
            ok, value = _int_or_none(block, -MAX_OFFSET_MS, MAX_OFFSET_MS)
            if not ok:
                return None
            changes.update({f: None for f in _OFFSET_FIELDS})
            changes["doc_offset_ms"] = value
    new = dataclasses.replace(state, **changes)
    # Deliberately strict until a real capture shows whether selectedEventIndex is 0- or 1-based
    # (and what Ontime sends when it is finished): an index past numEvents is treated as unreadable
    # rather than guessed. Revisit after the owner's capture session.
    if new.selected_index is not None and new.num_events is not None and new.selected_index > new.num_events:
        return None
    return new


def parse_version(body: object) -> str | None:
    """The decoded ``/api/version`` body -> a short version string, or None."""
    payload = body.get("payload") if isinstance(body, dict) else None
    return payload if isinstance(payload, str) and _VERSION_RE.fullmatch(payload) else None


def split_url(base_url: str) -> tuple[str, int]:
    """``http://host[:port]`` (as WallClockConfig stores it) -> (host, port); IPv6 loses its brackets."""
    u = urlsplit(base_url)
    return (u.hostname or ""), (u.port or 80)


def ws_url(base_url: str) -> str:
    """``http://host:port`` -> ``ws://host:port/ws``."""
    host, port = split_url(base_url)
    host = f"[{host}]" if ":" in host else host
    return f"ws://{host}:{port}{WS_PATH}"
