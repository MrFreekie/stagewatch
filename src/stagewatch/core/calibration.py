"""Hardware identity and calibration records (plan §1.9): calibration follows the board.

Keys:
- node: ``mac:<12 lowercase hex>`` (ESPHome ``DeviceInfo.mac_address``, a Shelly's MAC, ...);
- sensor: ``<node key>/<object_id>``;
- fallback for sources with no identity: ``dev:<device_id>/<object_id>``.

Everything here is pure (works on a ``Config`` and plain values, no I/O), so any integration
can use it, not only ESPHome. The caller saves the config when a function reports a change.
MACs are not secrets, but they stay off the public dashboard snapshot: they are only shown on
the admin page.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from .config import (
    CALIBRATION_HISTORY_MAX,
    Calibration,
    CalibrationEntry,
    Config,
    EntitySettings,
    valid_calibration_key,
)

log = logging.getLogger(__name__)

_HEX12 = re.compile(r"[0-9a-f]{12}")
_SEPARATORS = str.maketrans("", "", ":-. ")
_OBJECT_ID = re.compile(r"[a-z0-9_]+")


# ------------------------------------------------------------------ identity
def normalise_mac(value: object) -> str | None:
    """``AA:BB:CC:DD:EE:FF``, ``aa-bb-...``, ``aabb.ccdd.eeff`` or ``aabbccddeeff`` -> ``aabbccddeeff``.
    None for anything that isn't exactly 12 hex digits (never guess)."""
    if not isinstance(value, str):
        return None
    mac = value.strip().lower().translate(_SEPARATORS)
    return mac if _HEX12.fullmatch(mac) else None


def format_mac(mac: str) -> str:
    """``aabbccddeeff`` -> ``aa:bb:cc:dd:ee:ff`` for messages (anything else is returned as is)."""
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2)) if _HEX12.fullmatch(mac or "") else mac


def node_key(mac: str) -> str:
    """Hardware key of a board from its (normalised) MAC."""
    norm = normalise_mac(mac)
    if norm is None:
        raise ValueError("not a MAC address")
    return f"mac:{norm}"


def sensor_key(node: str, object_id: str) -> str:
    """``<node key>/<object_id>``, or "" when the result wouldn't be a valid calibration key."""
    key = f"{node}/{object_id}"
    return key if valid_calibration_key(key) else ""


def fallback_key(device_id: str, object_id: str) -> str:
    """``dev:<device_id>/<object_id>`` for sources that can't say which hardware they are."""
    return sensor_key(f"dev:{device_id}", object_id)


@dataclass(frozen=True)
class Identity:
    """Result of checking a board's MAC against what the hub already knows.

    ``action``: ``ok`` (known board), ``first`` (record the MAC now), ``no_mac`` (the source
    didn't report a MAC and none was expected: carry on without a hardware key), or ``fault``
    (refuse: register nothing, accept no readings). ``detail`` is the FAULT text.
    """
    action: Literal["ok", "first", "no_mac", "fault"]
    mac: str = ""
    detail: str = ""
    reason: Literal["", "duplicate", "different", "unreadable"] = ""
    other_id: str = ""


def check_identity(device_id: str, expected_mac: str, found: object,
                   known: Iterable[tuple[str, str]]) -> Identity:
    """Decide what to do with a board that just connected.

    ``expected_mac`` is the MAC already recorded for ``device_id`` ("" before the first
    connect); ``found`` is what the board reported; ``known`` lists (device_id, mac) for every
    adopted device of the same kind, so one board can't be adopted twice.
    """
    mac = normalise_mac(found)
    if mac is None:
        if expected_mac:
            return Identity("fault", "", "could not read the board's hardware address", "unreadable")
        return Identity("no_mac")
    for other_id, other_mac in known:
        if other_id != device_id and other_mac and other_mac == mac:
            return Identity("fault", mac, f"same board as '{other_id}'", "duplicate", other_id)
    if not expected_mac:
        return Identity("first", mac)
    if expected_mac != mac:
        return Identity("fault", mac, f"different hardware at this address (expected "
                                      f"{format_mac(expected_mac)}, found {format_mac(mac)})", "different")
    return Identity("ok", mac)


# --------------------------------------------------------------- calibration
def utc_now_iso(now: float | None = None) -> str:
    """ISO-8601 UTC to the second, e.g. ``2026-10-02T14:10:00Z``."""
    ts = time.time() if now is None else now
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def calibration_for(config: Config, entity_id: str, hw_key: str = "") -> Calibration | EntitySettings:
    """The calibration that applies to an entity: the hardware record when there is one, else the
    legacy entry keyed by entity id (devices that haven't connected since the upgrade), else the
    defaults (no offset, included in the site average). Never modify the returned object."""
    if hw_key:
        cal = config.calibrations.get(hw_key)
        if cal is not None:
            return cal
    legacy = config.entities.get(entity_id)
    if legacy is not None:
        return legacy
    return Calibration()


def _with_entry(cal: Calibration, entry: CalibrationEntry) -> list[CalibrationEntry]:
    return [entry, *cal.history][:CALIBRATION_HISTORY_MAX]


def move_legacy(config: Config, device_id: str, node: str, now: float | None = None) -> int:
    """Move legacy offsets of one device (``entities["<device_id>.<object_id>"]``) to its hardware
    records (``calibrations["<node>/<object_id>"]``), with a ``migrated`` history entry.

    If both exist, the hardware record wins and the legacy entry is dropped. Entries whose object
    id can't form a valid key stay where they are (and keep applying). Returns the number of
    changes, so the caller saves once, and only when something changed. Logs keys and counts,
    never offsets.
    """
    prefix = f"{device_id}."
    changes = 0
    date = utc_now_iso(now)
    for entity_id in [k for k in config.entities if k.startswith(prefix)]:
        object_id = entity_id[len(prefix):]
        key = sensor_key(node, object_id) if _OBJECT_ID.fullmatch(object_id) else ""
        if not key:
            continue
        legacy = config.entities.pop(entity_id)
        changes += 1
        if key in config.calibrations:
            log.info("Calibration for %s: the hardware record already exists, so the old entry "
                     "for %s was dropped", key, entity_id)
            continue
        config.calibrations[key] = Calibration(
            offset=legacy.offset, include_in_average=legacy.include_in_average,
            history=[CalibrationEntry(offset=legacy.offset, date=date, method="migrated",
                                      reference=entity_id[:120])])
        log.info("Calibration for %s now follows the hardware (%s)", entity_id, key)
    return changes


def copy_node(config: Config, old_node: str, new_node: str, now: float | None = None) -> int:
    """Copy every record of one board to another (``method: moved``), e.g. when a sensor was moved
    to a replacement board. The old board's records stay, in case it comes back. Returns the
    number of records copied."""
    if old_node == new_node:
        return 0
    prefix = f"{old_node}/"
    date = utc_now_iso(now)
    copied = 0
    for key, cal in list(config.calibrations.items()):
        if not key.startswith(prefix):
            continue
        new_key = sensor_key(new_node, key[len(prefix):])
        if not new_key:
            continue
        entry = CalibrationEntry(offset=cal.offset, date=date, method="moved", reference=key[:120])
        target = config.calibrations.get(new_key)
        history = _with_entry(target, entry) if target is not None else [entry, *cal.history][:CALIBRATION_HISTORY_MAX]
        config.calibrations[new_key] = Calibration(
            offset=cal.offset, include_in_average=cal.include_in_average, history=history, chip=cal.chip)
        copied += 1
    if copied:
        log.info("Copied %d calibration record(s) from %s to %s", copied, old_node, new_node)
    return copied


def set_calibration(config: Config, entity_id: str, hw_key: str, offset: float,
                    include_in_average: bool, *, method: str = "manual", note: str = "",
                    reference: str = "", now: float | None = None) -> None:
    """The admin's write path: the hardware record when the entity has a hardware key (adding a
    history entry when the offset changes), else the legacy entry keyed by entity id."""
    if not hw_key:
        config.entities[entity_id] = EntitySettings(offset=offset, include_in_average=include_in_average)
        return
    cal = config.calibrations.get(hw_key)
    if cal is None:
        legacy = config.entities.get(entity_id)
        cal = Calibration(offset=legacy.offset if legacy else 0.0,
                          include_in_average=legacy.include_in_average if legacy else True)
    history = list(cal.history)
    if offset != cal.offset or (not history and offset != 0.0):
        history = _with_entry(cal, CalibrationEntry(offset=offset, date=utc_now_iso(now), method=method,
                                                    reference=reference[:120], note=note[:200]))
    config.calibrations[hw_key] = cal.model_copy(update={
        "offset": offset, "include_in_average": include_in_average, "history": history})
    config.entities.pop(entity_id, None)  # the hardware record is the one that applies now
