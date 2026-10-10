"""What GLOBCON told us, kept in plain structures, and the public shape the dashboard card gets.

Pure functions, no I/O. Paths and values are from the real capture of GLOBCON's web app (see
protocol.py). Everything is kept as GLOBCON reports it: levels are not averaged, smoothed, peak-held or
converted, and nothing is said about whether they are peak or RMS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ...core.ontimetimer import clean_title
from . import protocol
from .protocol import Value

LABEL_MAX = 24              # strip and layer labels: GLOBCON's own are short ("Flex Channel 3")
NAME_MAX = 40               # controller name
LEVEL_STALE_S = 3.0         # meters normally arrive every 0.1 s: this long without one is "stale"

_GENERAL_NAME = re.compile(r"^/general/(name|requiresPassword|authorized)/(\d{1,2})$")
_CHANNELS = "/general/channelsNumber"
_FADER = re.compile(r"^/controller/(\d{1,2})/faders/(\d{1,2})/(label|hasLevel)$")
_LAYER = re.compile(r"^/controller/(\d{1,2})/controls/layer$")
_LAYER_LABEL = re.compile(r"^/controller/(\d{1,2})/controls/layerLabel/(\d{1,2})$")

# Fixed, plain wording for the card and the admin page (never raw errors, never addresses).
STATUS_TEXT = {
    "ok": "Receiving levels",
    "waiting": "Connected, waiting for levels",
    "stale": "No new levels arriving",
    "offline": "GLOBCON is not connected",
}


@dataclass
class Controller:
    index: int                                  # 0-based, as GLOBCON numbers them on the wire
    name: str = ""
    layer: int | None = None
    layer_labels: dict[int, str] = field(default_factory=dict)
    labels: dict[int, str] = field(default_factory=dict)       # strip index -> label
    has_level: dict[int, bool] = field(default_factory=dict)   # strip index -> shows a level
    levels: list[float | None] = field(default_factory=list)   # latest block, as received
    meters_at: float | None = None
    requires_password: bool = False
    authorized: bool = False

    @property
    def locked(self) -> bool:
        return self.requires_password and not self.authorized


@dataclass
class GlobconState:
    controllers: dict[int, Controller] = field(default_factory=dict)
    channels: int | None = None                 # how many controllers GLOBCON has

    def ctrl(self, n: int) -> Controller:
        c = self.controllers.get(n)
        if c is None:
            c = self.controllers[n] = Controller(n)
        return c


def _ok_index(n: str, limit: int) -> int | None:
    i = int(n)
    return i if 0 <= i < limit else None


def apply_value(state: GlobconState, v: Value) -> bool:
    """Take one value into ``state``. True if it was one we keep. Bad types and out-of-range indices
    are ignored. Labels are cleaned (control and hidden characters removed, length capped)."""
    path = v.path
    if path == _CHANNELS:
        if v.kind == "i" and 0 <= v.value <= protocol.MAX_CONTROLLERS:
            state.channels = v.value
            return True
        return False
    m = _GENERAL_NAME.match(path)
    if m:
        n = _ok_index(m.group(2), protocol.MAX_CONTROLLERS)
        if n is None:
            return False
        c, what = state.ctrl(n), m.group(1)
        if what == "name" and v.kind == "s":
            c.name = clean_title(v.value, NAME_MAX)
        elif what == "requiresPassword" and v.kind == "b":
            c.requires_password = bool(v.value)
        elif what == "authorized" and v.kind == "b":
            c.authorized = bool(v.value)
        else:
            return False
        return True
    m = _FADER.match(path)
    if m:
        n, i = _ok_index(m.group(1), protocol.MAX_CONTROLLERS), _ok_index(m.group(2), protocol.MAX_STRIPS)
        if n is None or i is None:
            return False
        c = state.ctrl(n)
        if m.group(3) == "label" and v.kind == "s":
            c.labels[i] = clean_title(v.value, LABEL_MAX)
        elif m.group(3) == "hasLevel" and v.kind == "b":
            c.has_level[i] = bool(v.value)
        else:
            return False
        return True
    m = _LAYER.match(path)
    if m:
        n = _ok_index(m.group(1), protocol.MAX_CONTROLLERS)
        if n is None or v.kind != "i" or not 0 <= v.value < protocol.MAX_LAYERS:
            return False
        state.ctrl(n).layer = v.value
        return True
    m = _LAYER_LABEL.match(path)
    if m:
        n, k = _ok_index(m.group(1), protocol.MAX_CONTROLLERS), _ok_index(m.group(2), protocol.MAX_LAYERS)
        if n is None or k is None or v.kind != "s":
            return False
        state.ctrl(n).layer_labels[k] = clean_title(v.value, LABEL_MAX)
        return True
    return False


def apply_meters(state: GlobconState, controller: int, blob: bytes, ts: float) -> bool:
    """Take a meter block for a controller. False if the block is not usable (it is then ignored and
    the last good levels stay, frozen, with their time)."""
    if not 0 <= controller < protocol.MAX_CONTROLLERS:
        return False
    levels = protocol.parse_meters(blob)
    if levels is None:
        return False
    c = state.ctrl(controller)
    c.levels, c.meters_at = levels, ts
    return True


def known_strips(c: Controller) -> list[int]:
    """Every strip (0-based, at most MAX_STRIPS) GLOBCON has told us anything about: a label, whether it has
    a level, or a level. The card picks its channel group from these by index, so a strip that GLOBCON says
    has no level meter is still here (``meter`` False) and is drawn empty with a plain word, never as zero."""
    seen = set(c.labels) | set(c.has_level) | set(range(min(len(c.levels), protocol.MAX_STRIPS)))
    return sorted(i for i in seen if 0 <= i < protocol.MAX_STRIPS)


def meters_pair(path: str) -> int | None:
    """The 0-based controller of a ``/controller/N/meters`` path, else None."""
    m = re.match(r"^/controller/(\d{1,2})/meters$", path or "")
    return _ok_index(m.group(1), protocol.MAX_CONTROLLERS) if m else None


def feed_status(link_up: bool, wanted: Iterable[Controller], now: float) -> str:
    """"offline", "waiting", "stale" or "ok" for the whole feed."""
    if not link_up:
        return "offline"
    stamps = [c.meters_at for c in wanted if c.meters_at is not None]
    if not stamps:
        return "waiting"
    return "ok" if now - max(stamps) <= LEVEL_STALE_S else "stale"


def public_message(state: GlobconState, wanted: list[int], link_up: bool, now: float,
                   label: str = "GLOBCON") -> dict:
    """The shape dashboards get (snapshot and live feed). ``wanted`` are 0-based controller numbers.
    No address, password or error text. A level GLOBCON gave as no signal is ``None`` (the card shows
    it empty, never as zero). Every strip GLOBCON has told us about is included (up to 16, by 0-based
    ``index``, with ``meter`` saying whether GLOBCON gives it a level) and each dashboard picks its own
    channel group from them: one list for all dashboards is simpler than one per dashboard. ``meters_at`` lets the card show how old frozen levels are."""
    controllers = []
    for n in sorted(set(wanted)):
        c = state.controllers.get(n, Controller(n))
        strips = []
        for i in known_strips(c):
            lv = c.levels[i] if i < len(c.levels) else None
            # meter: True / False as GLOBCON says (hasLevel), None while it has not said.
            strips.append({"index": i, "label": c.labels.get(i, ""), "meter": c.has_level.get(i), "db": lv})
        controllers.append({
            "controller": n + 1,
            "name": c.name,
            "layer": c.layer,
            "layer_label": c.layer_labels.get(c.layer, "") if c.layer is not None else "",
            "locked": c.locked,
            "meters_at": c.meters_at,
            "strips": strips,
        })
    cs = [state.controllers[n] for n in set(wanted) if n in state.controllers]
    return {"status": feed_status(link_up, cs, now), "label": label, "controllers": controllers}
