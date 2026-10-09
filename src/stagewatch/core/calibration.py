"""Hardware identity and calibration records (plan §1.9): calibration follows the board.

Keys:
- node: ``mac:<12 lowercase hex>`` (ESPHome ``DeviceInfo.mac_address``, a Shelly's MAC, ...);
- sensor: ``<node key>/<object_id>``;
- fallback for sources with no identity: ``dev:<device_id>/<object_id>``.

Everything here is pure (works on a ``Config`` and plain values, no I/O), so any integration
can use it, not only ESPHome. The caller saves the config when a function reports a change.

MACs are not secrets, but they stay off everything public: the dashboard snapshot, device
status text, alarm text and the alarm log. Public FAULT text never names a MAC or another
device; the full detail is only on the admin page.

A MAC is only a claim: any device on the show network can pretend to be another board. This
identity check catches mistakes (a swapped board, a board adopted twice), not attackers. The
real protection against a spoofed node is an ESPHome API encryption key on every node.

Rollback mirror (0.3.0 only): config v1 builds (0.2.0) read offsets only from the legacy
``entities`` map and ignore ``calibrations``. So that going back to 0.2.0 still shows the right
values, offsets are *copied* to the hardware records and the legacy entry is kept as a mirror
(and kept in step when the admin edits an offset). The hardware record always wins. The mirror
can be dropped in 0.4.0, once a rollback can no longer land on a config v1 build.
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
from .model import normalise_mac

__all__ = ["normalise_mac", "format_mac", "node_key", "sensor_key", "fallback_key", "Identity",
           "check_identity", "calibration_for", "records_of", "move_legacy", "copy_node",
           "drop_node", "drop_legacy", "set_calibration", "utc_now_iso", "change_text"]

log = logging.getLogger(__name__)

_HEX12 = re.compile(r"[0-9a-f]{12}")
_OBJECT_ID = re.compile(r"[a-z0-9_]+")

# Public FAULT text (device status, alarm bar, alarm log): never a MAC or another device's id.
DETAIL_DIFFERENT = "different hardware at this address"
DETAIL_DUPLICATE = "same board as another device"
DETAIL_UNREADABLE = "could not read the board's hardware address"
DETAIL_KNOWN_BOARD = "board needs checking in Admin"


# ------------------------------------------------------------------ identity
def format_mac(mac: str) -> str:
    """``aabbccddeeff`` -> ``aa:bb:cc:dd:ee:ff`` for the admin page (anything else as is)."""
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2)) if _HEX12.fullmatch(mac or "") else mac


def node_key(mac: str) -> str:
    """Hardware key of a board from its MAC."""
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
    (refuse: register nothing, accept no readings). ``detail`` is the public FAULT text (no
    MACs, no other device ids); ``expected``/``mac``/``other_id`` are for the admin page only.
    """
    action: Literal["ok", "first", "no_mac", "fault"]
    mac: str = ""
    detail: str = ""
    reason: Literal["", "duplicate", "different", "unreadable", "known_board"] = ""
    other_id: str = ""
    expected: str = ""


def check_identity(device_id: str, expected_mac: str, found: object,
                   known: Iterable[tuple[str, str]]) -> Identity:
    """Decide what to do with a board that just connected.

    ``expected_mac`` is the MAC already recorded for ``device_id`` ("" before the first
    connect); ``found`` is what the board reported; ``known`` lists (device_id, mac) for every
    adopted device of the same kind, so one board can't be in use twice.
    """
    mac = normalise_mac(found)
    if mac is None:
        if expected_mac:
            return Identity("fault", "", DETAIL_UNREADABLE, "unreadable", expected=expected_mac)
        return Identity("no_mac")
    for other_id, other_mac in known:
        if other_id != device_id and other_mac and other_mac == mac:
            return Identity("fault", mac, DETAIL_DUPLICATE, "duplicate", other_id, expected_mac)
    if not expected_mac:
        return Identity("first", mac)
    if expected_mac != mac:
        return Identity("fault", mac, DETAIL_DIFFERENT, "different", expected=expected_mac)
    return Identity("ok", mac, expected=expected_mac)


def records_of(config: Config, node: str) -> list[str]:
    """Calibration keys stored for one board."""
    prefix = f"{node}/"
    return [k for k in config.calibrations if k.startswith(prefix)]


def known_board_needs_check(config: Config, device_id: str, node: str) -> bool:
    """True when a board connecting for the first time as ``device_id`` already has calibration
    records that are *not* simply this device's own (mirrored) legacy settings, for example a
    board re-adopted under a new name. Those records must not be applied without the admin
    saying so. The normal upgrade path (records that match this device's legacy entries) is fine."""
    for key in records_of(config, node):
        object_id = key[len(node) + 1:]
        legacy = config.entities.get(f"{device_id}.{object_id}")
        rec = config.calibrations[key]
        if legacy is None or legacy.offset != rec.offset or legacy.include_in_average != rec.include_in_average:
            return True
    return False


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


def _mirror(cal: Calibration) -> EntitySettings:
    # Only what a config v1 build (0.2.0) reads. Accuracy lives on the hardware record.
    return EntitySettings(offset=cal.offset, include_in_average=cal.include_in_average)


def move_legacy(config: Config, device_id: str, node: str, now: float | None = None) -> int:
    """Copy legacy offsets of one device (``entities["<device_id>.<object_id>"]``) to its hardware
    records (``calibrations["<node>/<object_id>"]``), with a ``migrated`` history entry.

    The legacy entry stays as a rollback mirror (see the module notes). If both exist, the
    hardware record wins and the mirror is brought in step with it. Entries whose object id can't
    form a valid key are left alone (and keep applying). Returns the number of changes, so the
    caller saves once, and only when something changed. Logs keys and counts, never offsets.
    """
    prefix = f"{device_id}."
    changes = 0
    date = utc_now_iso(now)
    for entity_id in [k for k in config.entities if k.startswith(prefix)]:
        object_id = entity_id[len(prefix):]
        key = sensor_key(node, object_id) if _OBJECT_ID.fullmatch(object_id) else ""
        if not key:
            continue
        legacy = config.entities[entity_id]
        rec = config.calibrations.get(key)
        if rec is not None:
            if (legacy.offset, legacy.include_in_average) != (rec.offset, rec.include_in_average):
                config.entities[entity_id] = _mirror(rec)
                changes += 1
                log.info("Calibration for %s: the hardware record already exists and wins; the old "
                         "entry for %s now mirrors it", key, entity_id)
            continue
        config.calibrations[key] = Calibration(
            offset=legacy.offset, include_in_average=legacy.include_in_average,
            accuracy=legacy.accuracy, accuracy_basis=legacy.accuracy_basis, role=legacy.role,
            history=[CalibrationEntry(offset=legacy.offset, date=date, method="migrated",
                                      reference=entity_id[:120])])
        changes += 1
        log.info("Calibration for %s now follows the hardware (%s)", entity_id, key)
    return changes


def copy_node(config: Config, old_node: str, new_node: str, now: float | None = None) -> int:
    """Copy every record of one board to another (``method: moved``), e.g. when the sensors were
    moved to a replacement board. The old board's records stay, in case it comes back. Returns the
    number of records copied."""
    if old_node == new_node:
        return 0
    date = utc_now_iso(now)
    copied = 0
    for key in records_of(config, old_node):
        cal = config.calibrations[key]
        new_key = sensor_key(new_node, key[len(old_node) + 1:])
        if not new_key:
            continue
        entry = CalibrationEntry(offset=cal.offset, date=date, method="moved", reference=key[:120])
        target = config.calibrations.get(new_key)
        history = _with_entry(target, entry) if target is not None else _with_entry(cal, entry)
        config.calibrations[new_key] = Calibration(
            offset=cal.offset, include_in_average=cal.include_in_average, history=history, chip=cal.chip,
            accuracy=cal.accuracy, accuracy_basis=cal.accuracy_basis, role=cal.role)
        copied += 1
    if copied:
        log.info("Copied %d calibration record(s) from %s to %s", copied, old_node, new_node)
    return copied


def drop_node(config: Config, node: str) -> int:
    """Remove every record of one board (the admin chose to start that board afresh)."""
    keys = records_of(config, node)
    for key in keys:
        del config.calibrations[key]
    if keys:
        log.info("Removed %d calibration record(s) of %s", len(keys), node)
    return len(keys)


def drop_legacy(config: Config, device_id: str) -> int:
    """Remove a device's legacy entries (its rollback mirror), e.g. when a new board replaces the
    old one at its address and must not inherit the old board's offsets."""
    keys = [k for k in config.entities if k.startswith(f"{device_id}.")]
    for key in keys:
        del config.entities[key]
    return len(keys)


_KEEP = object()   # "leave this as it is" (a caller that doesn't send accuracy must not clear it)


def set_calibration(config: Config, entity_id: str, hw_key: str, offset: float,
                    include_in_average: bool, *, method: str = "manual", note: str = "",
                    reference: str = "", now: float | None = None,
                    accuracy=_KEEP, accuracy_basis=_KEEP, role=_KEEP) -> None:
    """The admin's write path: the hardware record when the entity has a hardware key (adding a
    history entry when the offset changes), else the legacy entry keyed by entity id. A legacy
    entry that already exists is kept in step as the rollback mirror.

    ``accuracy`` (canonical units, or None to clear) and ``accuracy_basis`` are stored next to the
    offset, so they follow the board; left out, they keep what is stored."""
    if not hw_key:
        old = config.entities.get(entity_id)
        acc = (old.accuracy if old else None) if accuracy is _KEEP else accuracy
        basis = (old.accuracy_basis if old else "typical") if accuracy_basis is _KEEP else accuracy_basis
        own = (old.role if old else "") if role is _KEEP else role
        config.entities[entity_id] = EntitySettings(
            offset=offset, include_in_average=include_in_average, accuracy=acc, accuracy_basis=basis,
            role=own)
        return
    cal = config.calibrations.get(hw_key)
    if cal is None:
        legacy = config.entities.get(entity_id)
        cal = Calibration(offset=legacy.offset if legacy else 0.0,
                          include_in_average=legacy.include_in_average if legacy else True,
                          accuracy=legacy.accuracy if legacy else None,
                          accuracy_basis=legacy.accuracy_basis if legacy else "typical",
                          role=legacy.role if legacy else "")
    history = list(cal.history)
    if offset != cal.offset or (not history and offset != 0.0):
        history = _with_entry(cal, CalibrationEntry(offset=offset, date=utc_now_iso(now), method=method,
                                                    reference=reference[:120], note=note[:200]))
    update = {"offset": offset, "include_in_average": include_in_average, "history": history}
    if accuracy is not _KEEP:
        update["accuracy"] = accuracy
    if accuracy_basis is not _KEEP:
        update["accuracy_basis"] = accuracy_basis
    if role is not _KEEP:
        update["role"] = role
    config.calibrations[hw_key] = cal.model_copy(update=update)
    if entity_id in config.entities:  # rollback mirror (drop in 0.4.0)
        config.entities[entity_id] = EntitySettings(offset=offset, include_in_average=include_in_average)


def _amount(value: float, unit: str, signed: bool) -> str:
    text = f"{value:+.3f}" if signed else f"{value:.3f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", "+0"):
        text = "0"
    return f"{text} {unit}".strip()


def change_text(name: str, unit: str, old_offset: float, new_offset: float,
                old_included: bool, new_included: bool) -> str:
    """The timeline marker text for a saved calibration change, or "" when nothing changed.
    Plain words, the sensor's display name only (never an address or key); an offset change and
    an include change in one save share one line."""
    parts = []
    if new_offset != old_offset:
        parts.append(f"Calibration changed: {name} offset {_amount(new_offset, unit, True)} "
                     f"(was {_amount(old_offset, unit, True)})")
    if new_included != old_included:
        now, was = ("included in", "left out") if new_included else ("left out of", "included")
        parts.append((f"{name} now " if not parts else "now ") + f"{now} the site average (was {was})")
    return "; ".join(parts)
