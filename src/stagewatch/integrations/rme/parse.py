"""What an RME unit's status socket says, turned into plain Python values. Pure functions: no I/O, no
clock, no logging of frame text, so every rule here can be tested with a string.

Shapes are from two owner captures of an RME 12Mic (firmware 2.0.1) web remote. The unit pushes one full
``{"msg":"status","data":{...}}`` when the socket opens, then JSON deltas without ``msg`` that carry
only what changed. Anything the captures do not show is marked UNVERIFIED below and is shown as
"unknown" rather than guessed. Only a whitelist of subtrees is kept; the large ``osc`` schema tree, the
serial number's neighbours (group name, gPTP grandmaster, stream ids and addresses) and every analogue
input detail are dropped on the way in, so they can never reach a dashboard.

Every number is checked: booleans are never numbers, numbers are never booleans, NaN and infinity are
refused, strings are length-capped, and depth and node counts are bounded.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

# ------------------------------------------------------------------ limits (per frame)
MAX_TEXT_CHARS = 256 * 1024     # a text frame longer than this is dropped as malformed
MAX_DEPTH = 12                  # nesting of objects and arrays
MAX_NODES = 5000                # every value counts one (outside the osc schema tree)
MAX_OSC_NODES = 20000           # the osc schema tree is thrown away; it has its own, looser budget
MAX_STR = 200                   # string values
MAX_KEY = 64
MAX_WILDCARD_KEYS = 32          # per level, for the "*" parts of the whitelist
MAX_LIST = 4

LOCAL_NAME_MAX = 40
WORD_MAX = 24

# ------------------------------------------------------------------ unit conversion
KELVIN_OFFSET = 273.15
KELVIN_MIN = 200.0
KELVIN_MAX = 450.0
RATE_MIN_HZ = 8000
RATE_MAX_HZ = 400000


class BadFrame(Exception):
    """A frame that fails a check. ``code`` is a short fixed word; the frame's text is never kept."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _no_constant(_name: str):
    raise ValueError("constant")


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):      # "1e999" is infinity to Python's float(); refuse it like NaN
        raise ValueError("float")
    return value


def decode_text(text: str) -> dict:
    """The JSON object in a text frame, or BadFrame. Enforces every per-frame cap."""
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise BadFrame("too_large")
    try:
        obj = json.loads(text, parse_constant=_no_constant, parse_float=_parse_float)
    except (ValueError, RecursionError, TypeError):
        raise BadFrame("json") from None
    if not isinstance(obj, dict):
        raise BadFrame("shape")
    _check_limits(obj)
    return obj


def _check_limits(root: dict) -> None:
    nodes = osc_nodes = 0
    stack: list[tuple[object, int, bool]] = [(root, 1, False)]
    while stack:
        node, depth, in_osc = stack.pop()
        if depth > MAX_DEPTH:
            raise BadFrame("depth")
        if isinstance(node, dict):
            children = []
            for key, value in node.items():
                if not isinstance(key, str) or len(key) > MAX_KEY:
                    raise BadFrame("key")
                children.append((value, depth + 1, in_osc or (key == "osc" and depth <= 2)))
        elif isinstance(node, list):
            children = [(value, depth + 1, in_osc) for value in node]
        else:
            children = []
        for child, child_depth, child_osc in children:
            if child_osc:
                osc_nodes += 1
                if osc_nodes > MAX_OSC_NODES:
                    raise BadFrame("nodes")
            else:
                nodes += 1
                if nodes > MAX_NODES:
                    raise BadFrame("nodes")
            if isinstance(child, str):
                if len(child) > MAX_STR:
                    raise BadFrame("string")
            elif isinstance(child, (dict, list)):
                stack.append((child, child_depth, child_osc))


# ------------------------------------------------------------------ the whitelist
_S = "scalar"
_L = "list"
_SNODE = {"state": _S, "cause": _S}
_PATHS = {"pri": {"soundness": _SNODE}, "sec": {"soundness": _SNODE}}

SCHEMA: dict = {
    "device": {
        "entity_name": _S, "model_name": _S, "firmware_version": _S, "serial_number": _S,
        "soundness": _SNODE,
        "psu": {"state": _L, "redundancy_alert": _S, "soundness": _SNODE},
        "health": {"temperatures": {"core": _S}, "soundness": _SNODE},
        "preset": {"active": _S, "loaded": _S, "names": {"*": _S}, "soundness": _SNODE},
    },
    "clock": {
        "source": _S, "sampling_rate": _S, "current_sampling_rate": _S, "soundness": _SNODE,
        "sources": {"*": {"rate": _S, "sync": _S, "soundness": _SNODE}},
    },
    "input": {"avb": {"*": _PATHS}},
    "output": {"avb": {"*": _PATHS}},
}

_WILD_KEY = re.compile(r"^[A-Za-z0-9_]{1,24}$")


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_scalar(value: object) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    return is_number(value)


def _valid_kelvin(value: object) -> bool:
    return is_number(value) and KELVIN_MIN <= value <= KELVIN_MAX


# A leaf with a validator keeps its OLD value when the new one fails the check (an out-of-range
# temperature is ignored, not shown and not zeroed).
_KEEP_OLD_IF_INVALID = {("device", "health", "temperatures", "core"): _valid_kelvin}


def _same(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if is_number(a) and is_number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _clean_leaf(kind: str, value: object):
    """The value to keep, or a sentinel for "not usable here"."""
    if kind == _S:
        return value if _is_scalar(value) else _SKIP
    if kind == _L:
        if isinstance(value, list) and len(value) <= MAX_LIST and all(_is_scalar(v) for v in value):
            return list(value)
        return None          # a list that is not a list: unknown (the view treats None as "unknown", never the old value)
    return _SKIP


_SKIP = object()


def _sub_schema(schema: dict, key: str, existing: dict):
    if "*" in schema:
        if not _WILD_KEY.match(key) or (key not in existing and len(existing) >= MAX_WILDCARD_KEYS):
            return None
        return schema["*"]
    return schema.get(key)


def _merge(dst: dict, src: dict, schema: dict, path: tuple[str, ...]) -> bool:
    """Fold ``src`` into ``dst`` keeping only whitelisted keys of the right shape. True if anything changed."""
    changed = False
    for key, value in src.items():
        sub = _sub_schema(schema, key, dst)
        if sub is None:
            continue
        here = path + (key,)
        if isinstance(sub, dict):
            if not isinstance(value, dict):
                continue
            child = dst.get(key)
            created = not isinstance(child, dict)
            if created:
                child = {}
            inner = _merge(child, value, sub, here)
            if created and child:
                dst[key] = child
            changed = changed or inner
            continue
        clean = _clean_leaf(sub, value)
        if clean is _SKIP:
            continue
        check = _KEEP_OLD_IF_INVALID.get(here)
        if check is not None and not check(clean):
            continue
        if key not in dst or not _same(dst[key], clean):
            dst[key] = clean
            changed = True
    return changed


@dataclass
class FrameResult:
    """What a text frame did. ``kind``: baseline (first full status), resync (a later full status on
    the same socket), delta, or ignored (a delta before any baseline, or a message we do not use)."""
    kind: str
    changed: bool = False


class RmeState:
    """The whitelisted picture of one unit, built from its frames. One per socket (a new connection
    starts empty, so the first status after a reconnect is a baseline again)."""

    def __init__(self) -> None:
        self.data: dict | None = None

    def ingest(self, text: str) -> FrameResult:
        obj = decode_text(text)
        if "msg" in obj:
            if obj.get("msg") != "status":
                return FrameResult("ignored")
            data = obj.get("data")
            if not isinstance(data, dict):
                raise BadFrame("shape")
            fresh: dict = {}
            _merge(fresh, data, SCHEMA, ())
            kind = "baseline" if self.data is None else "resync"
            self.data = fresh
            return FrameResult(kind, True)
        if self.data is None:
            return FrameResult("ignored")
        return FrameResult("delta", _merge(self.data, obj, SCHEMA, ()))


# ------------------------------------------------------------------ text cleaning
_PLAIN = re.compile(r"[^A-Za-z0-9_ ]")


def clean_text(value: object, limit: int) -> str:
    """Text from a unit made safe to show: control and format characters removed, spaces tidied, capped."""
    if not isinstance(value, str):
        return ""
    import unicodedata
    text = "".join(" " if ch.isspace() else ch for ch in value
                   if ch.isspace() or unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())[:limit].strip()


_DIGIT_RUN = re.compile(r"\d{5,}")
_SERIAL_LOOK = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9._-]{8,}$")


def public_name(name: str, serial: str = "") -> str:
    """A unit's own name made fit to be the default public name: any word that holds the serial number, a
    run of five or more digits, or looks like a serial number (letters and digits mixed, eight or more
    characters) is dropped. The unit may well have its serial number in its name."""
    serial = (serial or "").strip().lower()
    kept = []
    for word in name.split():
        if serial and len(serial) >= 4 and serial in word.lower():
            continue
        if _DIGIT_RUN.search(word) or _SERIAL_LOOK.match(word):
            continue
        kept.append(word)
    return " ".join(kept)


def clean_word(value: object, limit: int = WORD_MAX) -> str:
    """A token reduced to letters, digits, underscore and space (for soundness words and clock keys)."""
    if not isinstance(value, str):
        return ""
    return " ".join(_PLAIN.sub("", value).split())[:limit].strip()


_SERIAL_BAD = re.compile(r"[^A-Za-z0-9._-]")


def clean_serial(value: object) -> str:
    """A serial number as the config keeps it (same characters as RmeDeviceConfig.serial). Admin-only."""
    return _SERIAL_BAD.sub("", value)[:64] if isinstance(value, str) else ""


def kelvin_to_celsius(kelvin: object) -> float | None:
    """C = K - 273.15, one decimal; None unless a finite number (not a bool) in 200..450 K."""
    if not _valid_kelvin(kelvin):
        return None
    return round(float(kelvin) - KELVIN_OFFSET, 1)


# ------------------------------------------------------------------ soundness
@dataclass(frozen=True)
class Sound:
    word: str        # what Stagewatch shows
    symbol: str      # tick, dash, triangle, cross, circle or question: colour is never the only cue
    level: int       # 0 none, 1 advisory, 2 alert. All silent: nothing here ever sounds or stops anything.
    known: bool


# Keys are the token with everything but letters and digits removed and lower-cased, so
# "Notice in progress", "NoticeInProgress" and "notice_in_progress" are one. The captures show only
# Good, Inactive and Notice; the multi-word spellings are UNVERIFIED (RME's manual names the states).
_WORDS = {
    "good": Sound("OK", "tick", 0, True),
    "inactive": Sound("Not monitored", "dash", 0, True),
    "notice": Sound("CHECK", "triangle", 1, True),
    "noticeinprogress": Sound("CHECK (in progress)", "triangle", 1, True),
    "warning": Sound("WARNING", "cross", 2, True),
    "norouting": Sound("Info", "circle", 0, True),
    "issueswithinput": Sound("Info", "circle", 0, True),
}

CAUSES = {
    "ClockMasterNoSync": "Clock master, not following another source",
    "InputNoSync": "Input not in sync",
    "PresetChanged": "Preset changed since it was loaded",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def classify(state: object) -> Sound:
    """A unit's soundness state word as Stagewatch shows it. An unknown word is never green."""
    if isinstance(state, str):
        key = re.sub(r"[^a-z0-9]", "", state.lower())
        got = _WORDS.get(key)
        if got is not None:
            return got
        token = clean_word(state) or "-"
    else:
        token = "-"
    return Sound(f"Unrecognised: {token}", "question", 1, False)


def cause_text(cause: object) -> str:
    """Plain words for a cause. A cause we have no text for is split into words and tagged as RME's."""
    if not isinstance(cause, str):
        return ""
    token = re.sub(r"[^A-Za-z0-9]", "", cause)[:40]
    if not token:
        return ""
    if token in CAUSES:
        return CAUSES[token]
    return " ".join(_CAMEL.sub(" ", token).split()) + " (RME's wording)"


@dataclass(frozen=True)
class SoundItem:
    area: str
    word: str
    symbol: str
    level: int
    cause: str
    known: bool
    token: str = ""          # the sanitised unknown word, for the once-per-run log line only

    def public(self) -> dict:
        return {"area": self.area, "word": self.word, "symbol": self.symbol, "level": self.level,
                "cause": self.cause}


def _item(area: str, node: object) -> SoundItem | None:
    if not isinstance(node, dict) or "state" not in node:
        return None
    s = classify(node.get("state"))
    token = "" if s.known else s.word.removeprefix("Unrecognised: ")
    return SoundItem(area, s.word, s.symbol, s.level, cause_text(node.get("cause")), s.known, token)


# ------------------------------------------------------------------ the typed view
CLOCK_LABELS = {"internal": "Internal", "wordclock": "Word clock"}


def clock_label(key: object) -> str:
    if isinstance(key, bool) or not isinstance(key, (str, int)):
        return ""
    if isinstance(key, int):
        return f"Source {key}" if 0 <= key <= 99 else ""
    if key in CLOCK_LABELS:
        return CLOCK_LABELS[key]
    m = re.fullmatch(r"avb_stream_(\d{1,2})", key)
    if m:
        return f"AVB stream {int(m.group(1))}"
    if key == "avb_stream_crf":
        return "AVB CRF"
    m = re.fullmatch(r"madi_(\w{1,20})", key)
    if m:
        return "MADI " + m.group(1).replace("_", " ")
    return clean_word(key.replace("_", " "))


@dataclass(frozen=True)
class View:
    """Everything the tile needs, from one unit's whitelisted state. Frozen, so two views compare."""
    name: str = ""
    model: str = ""
    firmware: str = ""
    serial: str = ""
    temp_c: float | None = None
    psu: tuple[bool, bool] | None = None
    redundancy: bool | None = None
    clock_key: str = ""
    clock_source: str = ""
    clock_locked: bool | None = None
    clock_rate_hz: int | None = None
    clock_state: str = ""
    preset_active: int | None = None
    preset_loaded: int | None = None
    preset_active_name: str = ""
    preset_loaded_name: str = ""
    preset_changed: bool | None = None
    avb_active: int = 0
    avb_total: int = 0
    avb_states: tuple[tuple[str, int], ...] = ()
    sound: tuple[SoundItem, ...] = field(default_factory=tuple)


def _get(data: dict, *path: str):
    cur: object = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _small_int(value: object, lo: int, hi: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value) if float(value).is_integer() and lo <= value <= hi else None


def _stream_item(node: object) -> Sound | None:
    """One AVB stream from its two paths: the worst notable word, else OK if either path is good,
    else not monitored. None if neither path says anything."""
    words = []
    for path in ("pri", "sec"):
        item = _item("", _get(node, path, "soundness") if isinstance(node, dict) else None)
        if item is not None:
            words.append(item)
    if not words:
        return None
    worst = max(words, key=lambda w: w.level)
    if worst.level > 0:
        return classify_item(worst)
    if any(w.symbol == "tick" for w in words):
        return _WORDS["good"]
    return classify_item(words[0])


def classify_item(item: SoundItem) -> Sound:
    return Sound(item.word, item.symbol, item.level, item.known)


def _avb_numbered(branch: object) -> list[tuple[int, object]]:
    out = []
    if isinstance(branch, dict):
        for key, node in branch.items():
            if isinstance(key, str) and key.isdigit() and 1 <= int(key) <= 64:
                out.append((int(key), node))
    return sorted(out, key=lambda x: x[0])


def make_view(data: dict | None) -> View | None:
    """The typed view of a baseline-or-later state, or None before a baseline."""
    if not isinstance(data, dict):
        return None
    name = clean_text(_get(data, "device", "entity_name"), LOCAL_NAME_MAX)
    model = clean_text(_get(data, "device", "model_name"), LOCAL_NAME_MAX)
    firmware = clean_text(_get(data, "device", "firmware_version"), WORD_MAX)
    serial = clean_serial(_get(data, "device", "serial_number"))
    name = public_name(name, serial)

    psu_raw = _get(data, "device", "psu", "state")
    psu = (psu_raw[0], psu_raw[1]) if isinstance(psu_raw, list) and len(psu_raw) == 2 \
        and all(isinstance(x, bool) for x in psu_raw) else None
    red = _get(data, "device", "psu", "redundancy_alert")
    redundancy = red if isinstance(red, bool) else None

    src_key = _get(data, "clock", "source")
    key = src_key if isinstance(src_key, str) else ""
    label = clock_label(src_key)
    sync = _get(data, "clock", "sources", key, "sync") if key else None
    locked = sync if isinstance(sync, bool) and key != "internal" else None
    rate_raw = _get(data, "clock", "current_sampling_rate")
    if not is_number(rate_raw):
        rate_raw = _get(data, "clock", "sampling_rate")
    rate = int(round(rate_raw)) if is_number(rate_raw) and RATE_MIN_HZ <= rate_raw <= RATE_MAX_HZ else None
    clock_node = _item("Clock", _get(data, "clock", "soundness"))

    active = _small_int(_get(data, "device", "preset", "active"), 0, 99)
    loaded = _small_int(_get(data, "device", "preset", "loaded"), 0, 99)
    names = _get(data, "device", "preset", "names")

    def preset_name(n: int | None) -> str:
        if n is None or n == 0 or not isinstance(names, dict):
            return ""
        return clean_text(names.get(str(n)), WORD_MAX)

    changed = (active != loaded) if active is not None and loaded is not None else None

    # soundness items: the unit's own areas always, sub-areas only when notable
    items: list[SoundItem] = []
    for area, node in (("Unit", _get(data, "device", "soundness")),
                       ("Power", _get(data, "device", "psu", "soundness")),
                       ("Health", _get(data, "device", "health", "soundness")),
                       ("Preset", _get(data, "device", "preset", "soundness"))):
        item = _item(area, node)
        if item is not None:
            items.append(item)
    if clock_node is not None:
        items.append(clock_node)
    sources = _get(data, "clock", "sources")
    if isinstance(sources, dict):
        for skey in sorted(sources)[:MAX_WILDCARD_KEYS]:
            item = _item(f"Clock source {clock_label(skey)}", _get(sources, skey, "soundness"))
            if item is not None and (item.level > 0 or not item.known):
                items.append(item)

    states: dict[str, int] = {}
    total = active_n = 0
    for n, node in _avb_numbered(_get(data, "input", "avb")):
        s = _stream_item(node)
        if s is None:
            continue
        total += 1
        active_n += 1 if s.symbol == "tick" else 0
        states[s.word] = states.get(s.word, 0) + 1
        if s.level > 0:
            items.append(SoundItem(f"AVB in {n}", s.word, s.symbol, s.level, "", s.known,
                                   "" if s.known else s.word.removeprefix("Unrecognised: ")))
    for n, node in _avb_numbered(_get(data, "output", "avb")):
        s = _stream_item(node)
        if s is not None and s.level > 0:
            items.append(SoundItem(f"AVB out {n}", s.word, s.symbol, s.level, "", s.known,
                                   "" if s.known else s.word.removeprefix("Unrecognised: ")))

    return View(
        name=name, model=model, firmware=firmware, serial=serial,
        temp_c=kelvin_to_celsius(_get(data, "device", "health", "temperatures", "core")),
        psu=psu, redundancy=redundancy,
        clock_key=key, clock_source=label, clock_locked=locked, clock_rate_hz=rate,
        clock_state=clock_node.word if clock_node else "",
        preset_active=active, preset_loaded=loaded,
        preset_active_name=preset_name(active), preset_loaded_name=preset_name(loaded),
        preset_changed=changed,
        avb_active=active_n, avb_total=total, avb_states=tuple(sorted(states.items())),
        sound=tuple(items[:40]),
    )
