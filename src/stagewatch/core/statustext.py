"""Short, fixed, crew-friendly wording for connection problems.

A device's ``status_detail`` and its alarm line reach public dashboards, so they must never hold
a raw exception text (it can carry an IP address, a socket repr or a host name). A connection
error is only ever *classified* here; the words that come out are always one of a few fixed
phrases. The full technical text belongs in the log and the admin-only state.
"""

from __future__ import annotations

import re

CANT_REACH = "Can't reach the node"
REFUSED = "Connection refused"
TIMED_OUT = "Timed out"
BAD_KEY = "Wrong encryption key"
NAME_NOT_FOUND = "Name not found"
FALLBACK = "Connection problem"

_REFUSED = ("refused", "errno 111", "winerror 10061")
_TIMED_OUT = ("timeout", "timed out", "errno 110", "winerror 10060")
_UNREACHABLE = ("unreachable", "no route", "host is down", "errno 113", "errno 101",
                "winerror 10051", "winerror 10065", "network is")
_NAME = ("resolving", "resolve", "name or service", "getaddrinfo", "nodename nor servname",
         "errno -2", "errno -3", "errno 11001", "winerror 11001")

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"(?i)(?:[0-9a-f]{0,4}:){2,}[0-9a-f]{0,4}")
_HOSTISH = re.compile(r"(?i)\b[a-z0-9-]+\.(?:local|lan|home|internal|com|net|org|io)\b")
MAX_DETAIL = 200


def describe_connect_error(err: BaseException) -> str:
    """One fixed phrase for a connection error. The error's own text is only read to pick the
    phrase; none of it is ever returned."""
    names = {c.__name__ for c in type(err).__mro__}
    try:
        text = str(err).lower()
    except Exception:
        text = ""
    if names & {"InvalidEncryptionKeyAPIError", "RequiresEncryptionAPIError"}:
        return BAD_KEY
    if "ResolveTimeoutAPIError" in names:
        return TIMED_OUT
    if "ResolveAPIError" in names:
        return NAME_NOT_FOUND
    if names & {"TimeoutAPIError", "TimeoutError"}:
        return TIMED_OUT
    if any(s in text for s in _REFUSED):
        return REFUSED
    if any(s in text for s in _TIMED_OUT):
        return TIMED_OUT
    if any(s in text for s in _NAME):
        return NAME_NOT_FOUND
    if any(s in text for s in _UNREACHABLE) or "SocketAPIError" in names or isinstance(err, OSError):
        return CANT_REACH
    return FALLBACK


def safe_status_detail(detail: object) -> str:
    """Last line of defence for a device's public status text: anything that looks like a raw
    exception, an address or a host name, or that is long or not plain text, becomes the fixed
    fallback. Short fixed phrases pass through unchanged."""
    if not isinstance(detail, str) or detail == "":
        return ""
    text = detail.strip()
    if (len(text) > MAX_DETAIL or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)
            or "AddrInfo" in text or "Traceback" in text or "sockaddr" in text
            or _IPV4.search(text) or _IPV6.search(text) or _HOSTISH.search(text)
            or ("[" in text and "]" in text)):
        return FALLBACK
    return text


def alarm_detail(detail: str) -> str:
    """The detail as it reads inside an alarm line: 'Name: missing (can't reach the node)'. The
    first letter is lower-cased when the first word is an ordinary capitalised word."""
    if len(detail) > 1 and detail[0].isupper() and detail[1].islower():
        return detail[0].lower() + detail[1:]
    return detail
