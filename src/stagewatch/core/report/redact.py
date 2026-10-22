"""What must never be in a report, and what a Sharing excerpt leaves out.

``scrub`` runs over every free-text field (alarm messages, device status detail, marker labels,
titles): addresses, hardware addresses and ``.local`` names become "[address]", and secrets are
removed with the diagnostics redaction.  ``SHARING_OMIT`` lists what a Sharing excerpt hides.
Data gaps are never hidden.
"""

from __future__ import annotations

import re

from ... import diagnostics

ADDRESS = "[address]"

_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\w.]*\w)", re.I)
_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])"
                  r"|(?<![0-9A-Fa-f.])(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}(?![0-9A-Fa-f])")
_MAC_PLAIN = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{12}(?![0-9A-Fa-f])")
# IPv6: a run of hex and colons that holds "::" or is a full eight-group address. A time such as
# 19:30:00 has neither, so it is left alone.
_V6_RUN = re.compile(r"(?<![\w:])\[?[0-9A-Fa-f:]{3,}(?:%[\w.\-]+)?\]?(?::\d{1,5})?(?![\w:])")
_LOCAL = re.compile(r"(?<![\w-])[\w-]+(?:\.[\w-]+)*\.(?:local|lan|home|internal)(?![\w-])", re.I)
# A serial number written as "serial 1234ABCD" / "S/N: ...".
_SERIAL = re.compile(r"(?i)\b(?:serial(?: number| no\.?)?|s/n|sn)\b\s*[:=#]?\s*(?=[A-Za-z0-9\-_.]*\d)[A-Za-z0-9\-_.]{4,}")

# Sections a Sharing excerpt leaves out, and the per-field hiding it applies (see build.apply_sharing).
SHARING_OMIT_SECTIONS = ("system", "schedule")
SHARING_HIDE_FIELDS = ("ack_from", "marker_source", "device_detail")


def _v6(m: re.Match) -> str:
    run = m.group(0)
    core = run.strip("[]").split("%")[0]
    if "::" in core or (core.count(":") == 7 and all(0 < len(g) <= 4 for g in core.split(":"))):
        return ADDRESS
    return run


def scrub(text: object) -> str:
    """Free text with addresses, hardware addresses, .local names, serials and secrets removed."""
    s = "" if text is None else str(text)
    s = diagnostics.redact_text(s)
    s = _SERIAL.sub(ADDRESS, s)
    s = _V6_RUN.sub(_v6, s)
    s = _IPV4.sub(ADDRESS, s)
    s = _MAC.sub(ADDRESS, s)
    s = _MAC_PLAIN.sub(lambda m: ADDRESS if re.search(r"[A-Fa-f]", m.group(0)) and re.search(r"\d", m.group(0))
                       else m.group(0), s)
    s = _LOCAL.sub(ADDRESS, s)
    return s


def scrub_tree(obj):
    """``scrub`` over every string in nested lists and dicts (keys are left alone)."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [scrub_tree(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_tree(v) for v in obj)
    if isinstance(obj, dict):
        return {k: scrub_tree(v) for k, v in obj.items()}
    return obj
