"""Where Smaart's message shapes and field names live: the ONE place that knows them. Nothing else in
Stagewatch knows how a Smaart message is built.

STATUS: UNVERIFIED. These shapes were read from the script Smaart's own SPL web page uses (the page
the Smaart computer serves to a browser), not from Rational Acoustics' SDK, and they have NOT been
tested against a live Smaart. No real data message has been seen, so every parser here is tolerant:
a field that is missing or the wrong type gives "not available" (``None``), never a guess and never
zero. ``VERIFIED`` goes True only after a session on real Smaart.

What is read (all plain ``ws://`` on the port the SPL web page is served from, path ``/api/v4/``):

* probe reply:      ``{"response": {"authenticationRequired": true|false}}``
* log-in reply:     ``{"response": {"status": <truthy when accepted>}}``
* inputs reply:     ``{"response": {"devices": [{"deviceName": s, "activeCalibratedChannels":
                      [{"channelName": s, "streamEndpoint": "/path", ...}]}], "metrics": [s, ...]}}``
* meter stream:     ``{"metrics": [{"SPL A Slow": 63.2}, {"LAeq 1": 70.1, "violation": false}, ...]}``

Smaart's alarm fields (``alarms``, ``violation``, ``colorThresholds``) are ignored: Stagewatch does
not re-create Smaart's alarms. Numbers are passed on exactly as received.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ...core.spl import clean_input_name, clean_level, clean_metric_name

VERIFIED = False

MAX_BYTES = 64 * 1024          # a message bigger than this is dropped (the socket also refuses it)
API_PATH = "/api/v4/"          # the only API path this build knows; another version would say so
MAX_DEVICES, MAX_CHANNELS, MAX_METRICS = 32, 96, 64

# Smaart's own page leaves this metric out of its lists; so do we.
DROPPED_METRICS = frozenset({"fs peak"})
# Keys that sit beside a metric in a stream object and are not the metric's name.
FLAG_KEYS = frozenset({"violation", "overload"})

# Smaart names its input paths with percent-encoding and brackets ("Channel%207%20(1)"); a leading "//", "@", "?", "#" and "\\" stay refused.
_ENDPOINT_RE = re.compile(r"/(?!/)[A-Za-z0-9_\-./~%:()'!,+=]{0,198}")


@dataclass(frozen=True)
class InputStream:
    label: str       # "deviceName : channelName", cleaned: what the admin picks and the card shows
    endpoint: str    # the path of this input's meter stream


@dataclass(frozen=True)
class Catalog:
    inputs: tuple[InputStream, ...]
    metrics: tuple[str, ...]   # Smaart's own metric names, "FS Peak" left out


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


def _response(doc: object) -> dict | None:
    r = doc.get("response") if isinstance(doc, dict) else None
    return r if isinstance(r, dict) else None


def parse_probe_reply(doc: object) -> bool | None:
    """Does Smaart want a password? None when this is not the reply we expect (not Smaart's v4 API)."""
    r = _response(doc)
    if r is None:
        return None
    flag = r.get("authenticationRequired")
    return flag if isinstance(flag, bool) else False


def is_probe_reply(doc: object) -> bool:
    return _response(doc) is not None


def parse_auth_reply(doc: object) -> bool | None:
    """True when Smaart accepted the password, False when it did not (a reply with no ``status`` is a
    "no" too: Smaart's page asks again), None when this is not a reply at all."""
    r = _response(doc)
    if r is None:
        return None
    return bool(r.get("status"))


def safe_endpoint(value: object) -> str | None:
    """A stream path Smaart named, if it is a plain absolute path (so it cannot change the host)."""
    if isinstance(value, str) and _ENDPOINT_RE.fullmatch(value) and ".." not in value.split("/"):
        return value
    return None


def parse_inputs_reply(doc: object) -> Catalog | None:
    """The active inputs and the metric names. None when this is not an inputs reply."""
    r = _response(doc)
    if r is None or not isinstance(r.get("devices"), list):
        return None
    inputs: list[InputStream] = []
    seen: set[str] = set()
    for dev in r["devices"][:MAX_DEVICES]:
        if not isinstance(dev, dict) or not isinstance(dev.get("deviceName"), str):
            continue
        channels = dev.get("activeCalibratedChannels")
        for ch in (channels if isinstance(channels, list) else [])[:MAX_CHANNELS]:
            if len(inputs) >= MAX_CHANNELS:
                break
            if not isinstance(ch, dict) or not isinstance(ch.get("channelName"), str):
                continue
            endpoint = safe_endpoint(ch.get("streamEndpoint"))
            label = clean_input_name(f"{dev['deviceName']} : {ch['channelName']}")
            if endpoint is None or not label or label in seen:
                continue
            seen.add(label)
            inputs.append(InputStream(label, endpoint))
    metrics: list[str] = []
    for m in (r.get("metrics") if isinstance(r.get("metrics"), list) else [])[:MAX_METRICS * 2]:
        name = clean_metric_name(m)
        if name and name.lower() not in DROPPED_METRICS and name not in metrics:
            metrics.append(name)
    return Catalog(tuple(inputs), tuple(metrics[:MAX_METRICS]))


def parse_stream_message(raw: str | bytes, max_bytes: int = MAX_BYTES) -> dict[str, float | None] | None:
    """One meter-stream message -> {Smaart's metric name: value}, or None when it carries no metric.
    A value that is missing, not a plain number in range, or flagged ``overload`` is None ("not
    available"), never zero. The first key that is not a flag is the metric's name."""
    doc = decode(raw, max_bytes)
    items = doc.get("metrics") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return None
    out: dict[str, float | None] = {}
    for item in items[:MAX_METRICS * 2]:
        if not isinstance(item, dict):
            continue
        key = next((k for k in item if isinstance(k, str) and k not in FLAG_KEYS), None)
        name = clean_metric_name(key)
        if not name or name in out:
            continue
        out[name] = None if _flag(item.get("overload")) else clean_level(item[key])
    return out or None


def _flag(v: object) -> bool:
    return v is True or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v != 0)
