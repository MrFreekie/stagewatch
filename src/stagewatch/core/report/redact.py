"""What must never be in a report, and what a Sharing excerpt leaves out.

``scrub`` runs over every free-text field (alarm messages, device status detail, marker labels,
titles): IP and hardware addresses (with or without separators), host names (``node.local``,
``host.example.com``) and serial numbers become "[address]", UK phone numbers become "[phone]",
ESPHome-style name tails (``-a1b2c3``) are dropped, and secrets are removed with the diagnostics
redaction (including the secrets set in the config).  Names that are not one of these are not
recognised and stay as written.  ``SHARING_OMIT`` lists what a Sharing excerpt hides.
Data gaps are never hidden.
"""

from __future__ import annotations

import re

from ... import diagnostics

ADDRESS = "[address]"
PHONE = "[phone]"

_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\w.]*\w)", re.I)
_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])"
                  r"|(?<![0-9A-Fa-f.])(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}(?![0-9A-Fa-f])")
_MAC_PLAIN = re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{12}(?![0-9A-Za-z])")   # six bytes written with no separators
# A full host name: two or more dots and a letters-only last part (host.example.com), or one dot and a
# common top-level domain (example.com).  Version numbers (0.4.0) and file names (report.html) are left alone.
_HOST = re.compile(r"(?<![\w@.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.){2,}[A-Za-z]{2,24}(?![\w-])"
                   r"|(?<![\w@.-])[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\."
                   r"(?:com|net|org|io|uk|dev|app|eu|de|info|co|cloud|online|site)(?![\w.-])", re.I)
# ESPHome names end in a hex tail from the hardware address: foh-node-a1b2c3.
_ESP_TAIL = re.compile(r"(?<=[A-Za-z0-9])-[0-9a-f]{6}(?![\w-])", re.I)
# UK numbers: mobiles (07700 900123, +44 7700 900123) and landlines (01632 960123, +44 1632 960123).
_PHONE = re.compile(r"(?<![\w.+])(?:\+44[\s-]?\(?0?\)?[\s-]?|0044[\s-]?|0)(?:7\d{3}|[123]\d{2,4})[\s-]?\d{3}[\s-]?\d{3,4}(?![\w.])")
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


def _esp_tail(m: re.Match) -> str:
    tail = m.group(0)[1:]
    return "" if re.search(r"[A-Fa-f]", tail) and re.search(r"\d", tail) else m.group(0)


def scrub(text: object, known: tuple[str, ...] = ()) -> str:
    """Free text with addresses, hardware addresses, host names, serials, phone numbers and
    secrets removed.  ``known`` are the literal secrets from the config."""
    s = "" if text is None else str(text)
    s = diagnostics.redact_text(s, known)
    s = _SERIAL.sub(ADDRESS, s)
    s = _V6_RUN.sub(_v6, s)
    s = _IPV4.sub(ADDRESS, s)
    s = _MAC.sub(ADDRESS, s)
    s = _MAC_PLAIN.sub(ADDRESS, s)
    s = _PHONE.sub(PHONE, s)
    s = _LOCAL.sub(ADDRESS, s)
    s = _HOST.sub(ADDRESS, s)
    s = _ESP_TAIL.sub(_esp_tail, s)
    return s


def known_secrets(cfg: object) -> tuple[str, ...]:
    """The literal secrets in the config (PIN hash, keys, tokens), so they can be removed from free text."""
    try:
        return diagnostics.known_secrets(cfg.model_dump(mode="json"))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - a config we cannot read adds no extra secrets
        return ()


def scrub_tree(obj, known: tuple[str, ...] = ()):
    """``scrub`` over every string in nested lists and dicts (keys are left alone)."""
    if isinstance(obj, str):
        return scrub(obj, known)
    if isinstance(obj, list):
        return [scrub_tree(v, known) for v in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_tree(v, known) for v in obj)
    if isinstance(obj, dict):
        return {k: scrub_tree(v, known) for k, v in obj.items()}
    return obj
