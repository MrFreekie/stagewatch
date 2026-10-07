"""Ontime messages and addresses: pure functions, no I/O.

What is read (verified on Ontime 4.14.0, fixtures in tests/fixtures/ontime/):

* WebSocket ``/ws``: ``{"tag": "runtime-data", "payload": {"clock": <ms>, ...}}`` about once a
  second. ``clock`` is whole milliseconds since midnight on the **Ontime computer's own clock**.
  Every other ``tag`` (``log``, ``client-init``, ``client-list``, ...) is ignored.
* ``GET /api/poll``: ``{"payload": {"clock": <ms>, ...}}`` (HTTP 202).
* ``GET /api/version``: ``{"payload": "4.14.0"}`` (HTTP 202).

Nothing else in a message is read or kept, so a rundown with names in it never reaches us.
"""

from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urlsplit

from ...core.wallclock import valid_clock_ms

MAX_BYTES = 1024 * 1024           # largest message or response we accept (1 MiB)
HTTP_PATHS = ("/api/version", "/api/poll")   # the only HTTP paths we ever request
WS_PATH = "/ws"                   # and the only WebSocket path
HTTP_OK = (200, 202)              # Ontime 4.14 answers its read endpoints with 202

_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}")

Kind = Literal["clock", "ignored", "invalid"]


def parse_ws_message(raw: str | bytes | bytearray) -> tuple[Kind, int | None]:
    """One WebSocket message -> ("clock", ms), ("ignored", None) for anything that is not
    ``runtime-data`` (or is too big or not JSON), or ("invalid", None) for ``runtime-data`` whose
    ``clock`` is missing or out of range."""
    if len(raw) > MAX_BYTES:
        return "ignored", None
    try:
        msg = json.loads(raw)
    except (ValueError, RecursionError):  # includes bad UTF-8
        return "ignored", None
    if not isinstance(msg, dict) or msg.get("tag") != "runtime-data":
        return "ignored", None
    payload = msg.get("payload")
    ms = valid_clock_ms(payload.get("clock")) if isinstance(payload, dict) else None
    return ("clock", ms) if ms is not None else ("invalid", None)


def parse_poll(body: object) -> int | None:
    """The decoded ``/api/poll`` body -> clock ms, or None if it isn't a valid reading."""
    payload = body.get("payload") if isinstance(body, dict) else None
    return valid_clock_ms(payload.get("clock")) if isinstance(payload, dict) else None


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
