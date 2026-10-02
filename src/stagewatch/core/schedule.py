"""Day schedule / setlist (phase 1): the running order of the current show day.

Pure functions (validation, the paste/CSV import parser, ``now_next``, ``rebase``, the demo day)
plus a small ``ScheduleService`` the hub owns (storage via the Recorder, the bus event, the
public snapshot shape).

Times
    The admin types wall-clock ``HH:MM`` (24-hour, site time). They are stored as **UTC epoch
    seconds** (``planned_start`` / ``planned_end``), resolved on the current show's day with
    ``sitetime.resolve``: a time earlier than the day rollover belongs to the next morning, so
    23:00-00:30 is a valid range. An end must fall after its start. Only planned times exist in
    phase 1 (the ``actual_*`` columns stay empty).

Rebase
    When the site's time zone or the show's day changes, every item keeps its local ``HH:MM``
    and its place relative to the show day (an item the morning after stays the morning after).

Nothing here sounds, alarms or runs a timer: dashboards count down from the item times.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from . import sitetime

log = logging.getLogger(__name__)

KINDS: tuple[str, ...] = ("doors", "act", "changeover", "curfew", "other")
MAX_ITEMS = 300
TITLE_MAX = 120                       # characters
STAGE_MAX = 40                        # characters (same as Dashboard.stage)
SETLIST_MAX_BYTES = 8 * 1024          # per item, UTF-8
SETLIST_TOTAL_MAX_BYTES = 256 * 1024  # per show, UTF-8
IMPORT_MAX_BYTES = 64 * 1024          # pasted / file text, UTF-8
IMPORT_MAX_ROWS = 300
IMPORT_ERRORS_MAX = 100               # line errors listed (the count is always exact)

# Fixed-text messages: never the submitted value.
MSG_TIME = "Times must be 24-hour HH:MM, for example 19:30"
MSG_END_TIME = "End times must be 24-hour HH:MM, for example 21:15, or left empty"
MSG_END_BEFORE_START = ("The end time must be after the start time (times before the day "
                        "rollover count as the next morning)")
MSG_TITLE_LEN = f"Titles must be 1 to {TITLE_MAX} characters"
MSG_TITLE_HIDDEN = "Titles can't contain hidden or control characters"
MSG_STAGE_LEN = f"Stage names can be up to {STAGE_MAX} characters"
MSG_STAGE_HIDDEN = "Stage names can't contain hidden or control characters"
MSG_KIND = "Kind must be doors, act, changeover, curfew or other"
MSG_SETLIST_LEN = "Each setlist can be up to 8 KB"
MSG_SETLIST_HIDDEN = "Setlists can't contain hidden or control characters (new lines are fine)"
MSG_SETLIST_TOTAL = "All the setlists together can be up to 256 KB"
MSG_TOO_MANY = f"A schedule can have up to {MAX_ITEMS} items"
MSG_IMPORT_TOO_BIG = "The text to import can be up to 64 KB"
MSG_IMPORT_TOO_MANY = f"The text to import can have up to {IMPORT_MAX_ROWS} rows"
MSG_LINE_HIDDEN = "This line contains hidden or control characters"
MSG_LINE_UNREADABLE = "Could not read this line. Write it like 19:00 Doors or 19:30-20:15 Support"
MSG_ROW_SHORT = "Each row needs at least a start time and a title"
MSG_ROW_LONG = "Too many columns: use start, end, title, kind, stage"
MSG_HEADER = "Unknown column name. Use start, end, title, kind and stage"
MSG_CSV = "Could not read this row (check the quote marks)"


# ------------------------------------------------------------------ text checks
def has_hidden_chars(text: str, allow_newlines: bool = False) -> bool:
    """Unicode control (Cc) or format (Cf) characters: zero-width, bidi overrides, NUL, tabs.
    With ``allow_newlines`` a plain ``\\n`` is allowed (multi-line setlists)."""
    for ch in text:
        if allow_newlines and ch == "\n":
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            return True
    return False


def normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


_TIME_RE = re.compile(r"^([01]?[0-9]|2[0-3])[:.]([0-5][0-9])$")


def norm_hhmm(text: str) -> str | None:
    """'19:30', '9:30' or '19.30' -> 'HH:MM'; None if it isn't a time of day."""
    m = _TIME_RE.fullmatch((text or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def clean_title(v: Any) -> str:
    v = (v if isinstance(v, str) else "").strip()
    if not 1 <= len(v) <= TITLE_MAX:
        raise ValueError(MSG_TITLE_LEN)
    if has_hidden_chars(v):
        raise ValueError(MSG_TITLE_HIDDEN)
    return v


def clean_stage(v: Any) -> str:
    v = (v if isinstance(v, str) else "").strip()
    if len(v) > STAGE_MAX:
        raise ValueError(MSG_STAGE_LEN)
    if has_hidden_chars(v):
        raise ValueError(MSG_STAGE_HIDDEN)
    return v


def clean_setlist(v: Any) -> str:
    v = normalise_newlines(v if isinstance(v, str) else "")
    if v.strip() == "":
        return ""
    if utf8_len(v) > SETLIST_MAX_BYTES:
        raise ValueError(MSG_SETLIST_LEN)
    if has_hidden_chars(v, allow_newlines=True):
        raise ValueError(MSG_SETLIST_HIDDEN)
    return v


def infer_kind(title: str, default: str = "act") -> str:
    """Doors / curfew / changeover from the title's first word, otherwise ``default``."""
    t = title.strip().casefold()
    if re.match(r"^(change[\s-]?over|c/o)\b", t):
        return "changeover"
    first = re.split(r"[^a-z]+", t, maxsplit=1)[0]
    if first in ("doors", "door"):
        return "doors"
    if first == "curfew":
        return "curfew"
    return default


def stage_matches(item_stage: str, dash_stage: str | None) -> bool:
    """An item with no stage shows on every dashboard; a dashboard with no stage shows every item."""
    a = (item_stage or "").strip().casefold()
    b = (dash_stage or "").strip().casefold()
    return not a or not b or a == b


# ------------------------------------------------------------------ resolving
def resolve_times(day: date | str, start: str, end: str, site) -> tuple[float, float | None]:
    """Epoch seconds for an item's HH:MM on show day ``day``. Raises ValueError (fixed text)."""
    s = norm_hhmm(start)
    if s is None:
        raise ValueError(MSG_TIME)
    ps = sitetime.resolve(day, s, site)
    if not (end or "").strip():
        return ps, None
    e = norm_hhmm(end)
    if e is None:
        raise ValueError(MSG_END_TIME)
    pe = sitetime.resolve(day, e, site)
    if pe <= ps:
        raise ValueError(MSG_END_BEFORE_START)
    return ps, pe


@dataclass
class FieldError:
    index: int   # item index in the submitted list
    field: str   # which field ("" = the item as a whole)
    msg: str     # fixed text


def build_rows(items: Iterable[dict], day: date | str, site) -> tuple[list[dict], list[FieldError]]:
    """Validate and resolve submitted items (dicts with stage, kind, title, start, end, setlist,
    optional id) into storable rows: {id, sort, stage, kind, title, planned_start, planned_end,
    setlist}. Every problem is reported; rows are only usable when the error list is empty."""
    rows: list[dict] = []
    errors: list[FieldError] = []
    total = 0
    for i, it in enumerate(items):
        row: dict[str, Any] = {"id": it.get("id"), "sort": i}
        bad = False
        for name, fn in (("title", clean_title), ("stage", clean_stage), ("setlist", clean_setlist)):
            try:
                row[name] = fn(it.get(name, ""))
            except ValueError as e:
                errors.append(FieldError(i, name, str(e)))
                bad = True
        kind = it.get("kind") or "other"
        if kind not in KINDS:
            errors.append(FieldError(i, "kind", MSG_KIND))
            bad = True
        row["kind"] = kind
        try:
            row["planned_start"], row["planned_end"] = resolve_times(day, it.get("start", ""), it.get("end", ""), site)
        except ValueError as e:
            msg = str(e)
            errors.append(FieldError(i, "start" if msg == MSG_TIME else "end", msg))
            bad = True
        if not bad:
            total += utf8_len(row["setlist"])
            rows.append(row)
    if total > SETLIST_TOTAL_MAX_BYTES:
        errors.append(FieldError(-1, "items", MSG_SETLIST_TOTAL))
    if len(rows) > MAX_ITEMS:
        errors.append(FieldError(-1, "items", MSG_TOO_MANY))
    return rows, errors


def sort_key(item: dict) -> tuple:
    return (item["planned_start"], item.get("sort", 0), item.get("id") or 0)


# ------------------------------------------------------------------ public shape
def public_item(item: dict, site) -> dict:
    """What dashboards get: no sort order, no write times, no (unused) actual times."""
    ps, pe = item["planned_start"], item.get("planned_end")
    return {
        "id": item["id"], "stage": item["stage"], "kind": item["kind"], "title": item["title"],
        "start": sitetime.local_hhmm(ps, site), "end": sitetime.local_hhmm(pe, site) if pe is not None else "",
        "planned_start": ps, "planned_end": pe, "setlist": item["setlist"],
    }


# ------------------------------------------------------------------ now / next
def now_next(items: list[dict], now: float, stage: str | None = None) -> dict:
    """NOW / NEXT / CURFEW for one dashboard at ``now``.

    * ``current``: the latest-starting non-curfew item with start <= now < its end (or, with no
      end, the next later item's start, else the curfew). Never past the curfew.
    * ``next``: the first non-curfew item starting after ``now``.
    * ``curfew``: the next curfew item; once all have passed, the last one (so the dashboard can
      say "past curfew"). ``seconds_to_curfew`` is negative after it.
    * ``state``: empty | before | running | between | over.
    """
    mine = sorted((i for i in items if stage_matches(i.get("stage", ""), stage)), key=sort_key)
    curfews = [i for i in mine if i["kind"] == "curfew"]
    acts = [i for i in mine if i["kind"] != "curfew"]
    upcoming_curfew = next((c for c in curfews if c["planned_start"] > now), None)
    curfew = upcoming_curfew or (curfews[-1] if curfews else None)
    past_curfew = upcoming_curfew is None and curfew is not None

    current = None
    if not past_curfew:
        for idx, it in enumerate(acts):
            start = it["planned_start"]
            if start > now:
                break
            end = it.get("planned_end")
            if end is None:
                later = next((a["planned_start"] for a in acts[idx + 1:] if a["planned_start"] > start), None)
                end = later if later is not None else (upcoming_curfew["planned_start"] if upcoming_curfew else math.inf)
            if now < end:
                current = it  # keep looking: a later-starting overlapping item wins
    nxt = next((a for a in acts if a["planned_start"] > now), None)

    if not mine:
        state = "empty"
    elif current is not None:
        state = "running"
    elif now < mine[0]["planned_start"]:
        state = "before"
    elif nxt is None and (past_curfew or upcoming_curfew is None):
        state = "over"
    else:
        state = "between"
    return {
        "state": state, "current": current, "next": nxt, "curfew": curfew,
        "seconds_to_curfew": None if curfew is None else curfew["planned_start"] - now,
    }


# ------------------------------------------------------------------ rebase
def rebase(items: list[dict], old_day: date | str, old_site, new_day: date | str, new_site) -> list[dict]:
    """Copies of ``items`` moved from show day ``old_day`` in ``old_site``'s zone to ``new_day``
    in ``new_site``'s zone, keeping each item's local HH:MM and its calendar offset from the show
    day (e.g. a 00:30 curfew stays on the morning after). DST gaps and overlaps resolve as in
    ``sitetime.resolve`` (PEP 495 fold=0). An end that no longer falls after its start (only
    possible inside a DST gap) is dropped rather than stored back to front."""
    od = old_day if isinstance(old_day, date) else date.fromisoformat(old_day)
    nd = new_day if isinstance(new_day, date) else date.fromisoformat(new_day)

    def move(ts: float) -> float:
        lt = sitetime.local(ts, old_site)
        return sitetime.wall_ts(nd + timedelta(days=(lt.date() - od).days), lt.time(), new_site)

    out = []
    for it in items:
        ps = move(it["planned_start"])
        pe = move(it["planned_end"]) if it.get("planned_end") is not None else None
        if pe is not None and pe <= ps:
            pe = None
        out.append({**it, "planned_start": ps, "planned_end": pe})
    return out


# ------------------------------------------------------------------ import
@dataclass
class ImportResult:
    format: str
    rows: list[dict] = field(default_factory=list)      # resolved, storable (no id)
    errors: list[dict] = field(default_factory=list)    # [{line, error}], at most IMPORT_ERRORS_MAX
    error_count: int = 0

    def add_error(self, line: int, msg: str) -> None:
        self.error_count += 1
        if len(self.errors) < IMPORT_ERRORS_MAX:
            self.errors.append({"line": line, "error": msg})


class ImportTooLarge(ValueError):
    """Over a cap: refuse the whole import (fixed text)."""


_LINE_RE = re.compile(
    r"^(?P<start>\d{1,2}[:.]\d{2})\s*(?:[-–—]\s*(?P<end>\d{1,2}[:.]\d{2}))?(?:\s+(?P<title>.*))?$")
_COLUMNS = ("start", "end", "title", "kind", "stage")


def _skip(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def detect_format(text: str) -> str:
    """csv if the first real line is comma-separated with a time (or 'start') in its first cell."""
    for line in text.split("\n"):
        if _skip(line):
            continue
        if "," not in line:
            return "lines"
        try:
            first = next(csv.reader([line]))[0].strip()
        except (csv.Error, StopIteration, IndexError):
            return "lines"
        return "csv" if (norm_hhmm(first) or first.casefold() in _COLUMNS) else "lines"
    return "lines"


def _make_row(res: ImportResult, line: int, start: str, end: str, title: str, kind: str, stage: str,
              day, site) -> None:
    try:
        title = clean_title(title)
        stage = clean_stage(stage)
    except ValueError as e:
        res.add_error(line, str(e))
        return
    kind = (kind or "").strip().casefold()
    if not kind:
        kind = infer_kind(title)
    elif kind not in KINDS:
        res.add_error(line, MSG_KIND)
        return
    try:
        ps, pe = resolve_times(day, start, end, site)
    except ValueError as e:
        res.add_error(line, str(e))
        return
    res.rows.append({"stage": stage, "kind": kind, "title": title, "start": norm_hhmm(start),
                     "end": norm_hhmm(end) if (end or "").strip() else "", "setlist": "",
                     "planned_start": ps, "planned_end": pe})


def _parse_lines(text: str, res: ImportResult, day, site) -> int:
    rows = 0
    for n, line in enumerate(text.split("\n"), start=1):
        if _skip(line):
            continue
        rows += 1
        if rows > IMPORT_MAX_ROWS:
            raise ImportTooLarge(MSG_IMPORT_TOO_MANY)
        if has_hidden_chars(line):
            res.add_error(n, MSG_LINE_HIDDEN)
            continue
        m = _LINE_RE.fullmatch(line.strip())
        if not m:
            res.add_error(n, MSG_LINE_UNREADABLE)
            continue
        title = (m.group("title") or "").strip().lstrip("-–—:").strip()
        _make_row(res, n, m.group("start"), m.group("end") or "", title, "", "", day, site)
    return rows


def _parse_csv(text: str, res: ImportResult, day, site) -> int:
    reader = csv.reader(io.StringIO(text))
    rows = 0  # data rows (a header row doesn't count)
    header: list[str] | None = None
    first = True
    while True:
        line = reader.line_num + 1  # the line this row starts on
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:
            res.add_error(line, MSG_CSV)
            break  # the reader can't resync reliably after a broken quote
        if _skip(",".join(cells)):
            continue
        is_first, first = first, False
        hidden = any(has_hidden_chars(c) for c in cells)
        cells = [c.strip() for c in cells]
        names = [c.casefold() for c in cells]
        named = [n for n in names if n]
        if is_first and not hidden and ("start" in named or "title" in named) and not any(norm_hhmm(c) for c in cells):
            if any(n not in _COLUMNS for n in named) or not {"start", "title"} <= set(named) \
                    or len(set(named)) != len(named):
                res.add_error(line, MSG_HEADER)
                return rows
            header = names
            continue
        rows += 1
        if rows > IMPORT_MAX_ROWS:
            raise ImportTooLarge(MSG_IMPORT_TOO_MANY)
        if hidden:
            res.add_error(line, MSG_LINE_HIDDEN)
            continue
        if header is not None:
            v = {name: (cells[i] if i < len(cells) else "") for i, name in enumerate(header) if name}
        else:
            while cells and cells[-1] == "":
                cells.pop()
            if len(cells) < 2:
                res.add_error(line, MSG_ROW_SHORT)
                continue
            if len(cells) > len(_COLUMNS):
                res.add_error(line, MSG_ROW_LONG)
                continue
            v = {"start": cells[0], "title": cells[1]} if len(cells) == 2 else dict(zip(_COLUMNS, cells))
        _make_row(res, line, v.get("start", ""), v.get("end", ""), v.get("title", ""),
                  v.get("kind", ""), v.get("stage", ""), day, site)
    return rows


def parse_import(text: str, fmt: str, day: date | str, site) -> ImportResult:
    """Parse pasted text or a CSV file into schedule rows for show day ``day``.

    * ``csv``: ``start,end,title,kind,stage`` (end, kind and stage may be empty; ``start,title``
      also works), with an optional header row naming the columns in any order.
    * ``lines``: ``19:00 Doors`` or ``19:30-20:15 Support: Band`` (an en dash works too).
    * ``auto``: csv when the first line looks like it, else lines.

    Blank lines and lines starting with ``#`` are skipped. A missing kind is guessed from the
    title (Doors, Curfew, Changeover), otherwise "act". Bad lines are reported by line number
    with fixed text, never their content. Raises ``ImportTooLarge`` over the size/row caps."""
    if utf8_len(text) > IMPORT_MAX_BYTES:
        raise ImportTooLarge(MSG_IMPORT_TOO_BIG)
    text = normalise_newlines(text)
    if fmt not in ("csv", "lines"):
        fmt = detect_format(text)
    res = ImportResult(format=fmt)
    (_parse_csv if fmt == "csv" else _parse_lines)(text, res, day, site)
    return res


# ------------------------------------------------------------------ demo day
DEMO_SETLIST = """## Main set
1. Northern Line
2. Paper Lanterns
3. Slow Burn
4. **Harbour Walls** (acoustic)
5. Static

## Encore
- Last Train Home
- *Kestrel* (with the support band)

Notes: `click` on 3 and 5. Confetti cue at the end of the encore."""


def demo_items(now: float, site) -> list[dict]:
    """The demo day, relative to ``now``: doors 40 min ago, the support act on now, a
    changeover, the headliner in about 45 min (with a Markdown setlist), curfew in about 95 min.
    Returned as submitted items (local HH:MM), so they go through the same checks as an edit."""
    base = math.floor(now / 60) * 60

    def hm(minutes: int) -> str:
        return sitetime.local_hhmm(base + minutes * 60, site)

    return [
        {"kind": "doors", "title": "Doors", "start": hm(-40), "end": "", "stage": "", "setlist": ""},
        {"kind": "act", "title": "Support: The Harbour Lights", "start": hm(-10), "end": hm(30), "stage": "",
         "setlist": "1. Open Water\n2. Lighthouse\n3. Tidal\n4. Grey Skies"},
        {"kind": "changeover", "title": "Changeover", "start": hm(30), "end": hm(45), "stage": "", "setlist": ""},
        {"kind": "act", "title": "Headliner: Kestrel Road", "start": hm(45), "end": hm(90), "stage": "",
         "setlist": DEMO_SETLIST},
        {"kind": "curfew", "title": "Curfew", "start": hm(95), "end": "", "stage": "", "setlist": ""},
    ]


# ------------------------------------------------------------------ hub service
class StaleShow(Exception):
    """The request was made for a show that is no longer the current one."""


class DemoDoesNotFit(Exception):
    """The demo day (40 min ago to 95 min ahead) would cross the day rollover, or the current
    show's day is not today."""


class ScheduleService:
    """The current show's schedule. Every change publishes bus ``schedule`` with ``public()``."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self._cache: tuple[int, list[dict]] | None = None

    # ---- reading
    def items(self) -> list[dict]:
        """Stored rows of the current show, in running order (cached per show)."""
        show_id = self.hub.recorder.show_id
        if self._cache is None or self._cache[0] != show_id:
            rows = sorted(self.hub.recorder.schedule_items(show_id), key=sort_key)
            self._cache = (show_id, rows)
        return self._cache[1]

    def invalidate(self) -> None:
        self._cache = None

    def public_items(self, stage: str | None = None) -> list[dict]:
        site = self.hub.config.site
        return [public_item(i, site) for i in self.items() if stage is None or stage_matches(i["stage"], stage)]

    def public(self) -> dict:
        """``snapshot().schedule`` and the WS ``schedule`` message: {show_id, day, items}."""
        return {"show_id": self.hub.recorder.show_id, "day": self.hub.show_info()["day"],
                "items": self.public_items()}

    def stages(self) -> list[str]:
        return [i["stage"] for i in self.items() if i["stage"]]

    # ---- writing
    def _check_show(self, show_id: int | None) -> None:
        if show_id is not None and show_id != self.hub.recorder.show_id:
            raise StaleShow()

    def _publish(self) -> None:
        self.invalidate()
        self.hub.bus.publish("schedule", self.public())

    def replace(self, show_id: int | None, rows: list[dict]) -> dict:
        """Replace the whole list (validated rows from ``build_rows``) in one transaction."""
        self._check_show(show_id)
        self.hub.recorder.replace_schedule(self.hub.recorder.show_id, rows)
        self._publish()
        return self.public()

    def append(self, show_id: int | None, rows: list[dict]) -> dict:
        """Add imported rows after the existing ones (one transaction). Caps apply to the total."""
        self._check_show(show_id)
        existing = self.items()
        if len(existing) + len(rows) > MAX_ITEMS:
            raise ImportTooLarge(MSG_TOO_MANY)
        keep = [{**i} for i in existing]
        n = len(keep)
        merged = keep + [{**r, "id": None, "sort": n + k} for k, r in enumerate(rows)]
        self.hub.recorder.replace_schedule(self.hub.recorder.show_id, merged)
        self._publish()
        return self.public()

    def load_demo(self, show_id: int | None = None, now: float | None = None) -> dict:
        self._check_show(show_id)
        now = time.time() if now is None else now
        site = self.hub.config.site
        rows, errors = build_rows(demo_items(now, site), self.hub.show_info()["day"], site)
        # The demo runs from 40 min ago to 95 min ahead: it can't cross the rollover, and on a
        # show day that isn't today its times would land on another date.
        if errors or rows[0]["planned_start"] != math.floor(now / 60) * 60 - 40 * 60:
            raise DemoDoesNotFit()
        return self.replace(None, rows)

    def rebase(self, old_site, old_day: str, new_site, new_day: str) -> int:
        """Re-base the current show's items (see ``rebase``). Returns how many moved."""
        items = self.items()
        if not items or not old_day or not new_day:
            return 0
        moved = rebase(items, old_day, old_site, new_day, new_site)
        changes = [(m["id"], m["planned_start"], m["planned_end"]) for m, o in zip(moved, items)
                   if (m["planned_start"], m["planned_end"]) != (o["planned_start"], o.get("planned_end"))]
        if changes:
            self.hub.recorder.set_schedule_times(self.hub.recorder.show_id, changes)
            log.info("Schedule re-based to keep its local times (%d items moved)", len(changes))
        self._publish()
        return len(changes)
