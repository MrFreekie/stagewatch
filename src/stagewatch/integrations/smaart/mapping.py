"""Where Stagewatch's sound-level values live in Smaart's messages: the ONE place with Smaart field
names. Nothing else in Stagewatch knows how a Smaart message is shaped.

STATUS: UNVERIFIED, and deliberately empty. Rational Acoustics' message format is only in the Smaart
SDK, which is free on request but not public (see plans/research-smaart-api.md), and we have not
been given it yet. Nothing here may be guessed: every metric maps to ``None`` ("not available")
until the SDK documents the real field. When it arrives, fill in the tables below (one ``Mapping``
per Smaart major version the SDK describes) and nothing else has to change.

Everything below the tables is pure parsing. Numbers are passed on exactly as received.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping as MappingType

from ...core.spl import METRIC_BY_KEY, clean_level, clean_version

MAX_BYTES = 64 * 1024   # a message bigger than this is dropped (the socket also refuses it)
MAX_DEPTH = 8           # longest field path accepted in a table

PathPart = str | int
Path = tuple[PathPart, ...]

# Set to True only after the field names have been checked against the SDK and a real session.
VERIFIED = False


@dataclass(frozen=True)
class Mapping:
    """Field paths for one Smaart version. A path is a tuple of object keys and list positions,
    for example ``("levels", "a_slow")``. ``None`` = not known / not available in this version."""
    version: Path | None                     # where the program says its version
    metrics: MappingType[str, Path | None]   # Stagewatch metric key -> path of its value


# ---------------------------------------------------------------- THE TABLE (unverified, empty)
# Keys of BY_MAJOR are Smaart major versions as the program reports them ("8", "9", ...), once known.
BY_MAJOR: dict[str, Mapping] = {}

# Used until the version is known, and for versions with no entry above.
DEFAULT = Mapping(version=None, metrics={key: None for key in METRIC_BY_KEY})
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Parsed:
    version: str                      # cleaned; "" if the message did not say
    values: dict[str, float | None]   # only the metrics this message carried a mapped field for


_MISSING = object()


def dig(obj: object, path: Path | None) -> object:
    """The value at ``path`` inside decoded JSON, or ``_MISSING``. Never raises."""
    if not path or len(path) > MAX_DEPTH:
        return _MISSING
    cur = obj
    for part in path:
        if isinstance(part, int) and not isinstance(part, bool):
            if not isinstance(cur, list) or not 0 <= part < len(cur):
                return _MISSING
            cur = cur[part]
        elif isinstance(part, str):
            if not isinstance(cur, dict) or part not in cur:
                return _MISSING
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _no_constants(_name: str):
    raise ValueError("NaN and infinity are not JSON")


def decode(raw: str | bytes, max_bytes: int = MAX_BYTES) -> object | None:
    """Decode one message. None for anything oversize, not valid UTF-8 / JSON, containing NaN or
    infinity, or nested so deeply that it would be an attack rather than a message."""
    if len(raw) > max_bytes:
        return None
    try:
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        return json.loads(text, parse_constant=_no_constants)
    except (ValueError, RecursionError):
        return None


def mapping_for(version: str, table: MappingType[str, Mapping] = BY_MAJOR,
                default: Mapping = DEFAULT) -> Mapping:
    """The table for a version string like "9.0.1" (by its major number), else the default."""
    major = version.split(".", 1)[0] if version else ""
    return table.get(major, default)


def parse_frame(raw: str | bytes, table: MappingType[str, Mapping] = BY_MAJOR,
                default: Mapping = DEFAULT, max_bytes: int = MAX_BYTES) -> Parsed | None:
    """One message -> the metrics it carries, or None when it carries none we know (ignored).

    The version is read with the default table's path, then that version's own table is used for the
    values. A mapped field that is present but not a plain number in range gives None ("not
    available"), never a guess. Fields nobody asked for are ignored."""
    doc = decode(raw, max_bytes)
    if not isinstance(doc, (dict, list)):
        return None
    ver = dig(doc, default.version)
    version = clean_version(ver) if ver is not _MISSING else ""
    mapping = mapping_for(version, table, default)
    values: dict[str, float | None] = {}
    for key, path in mapping.metrics.items():
        if key not in METRIC_BY_KEY or path is None:
            continue
        got = dig(doc, path)
        if got is not _MISSING:
            values[key] = clean_level(got)
    if not values:
        return None
    return Parsed(version, values)
