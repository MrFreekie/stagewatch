"""Day schedule / setlist (phase 1): the running order of the current show day.

Pure functions (validation, the paste/CSV/TSV import parser, ``now_next``, ``rebase``, the demo
day) plus a small ``ScheduleService`` the hub owns (storage via the Recorder, the revision, the
bus event, the public shapes).

Times
    The admin types wall-clock ``HH:MM`` (24-hour, site time). They are stored as **UTC epoch
    seconds** (``planned_start`` / ``planned_end``).
    * A new item (no ``date``) is resolved on the current show's day with the day rollover: a
      time earlier than the rollover belongs to the next morning, so 23:00-00:30 is valid.
    * An item sent back with its ``date`` (the local calendar date of its start, as every public
      item carries) is resolved on exactly that date, so saving an unchanged list never moves
      anything, even after the rollover setting changed. Its end is the first matching time
      after the start (possibly the next day).
    * A time the clocks skip (the spring-forward gap) is refused with fixed text naming the
      first valid time. Repeated times on the night the clocks go back resolve to the first
      occurrence (PEP 495 fold=0); a range that ends in the *second* occurrence (e.g. 01:45
      BST to 01:15 GMT) therefore can't be entered. That is accepted.
    Only planned times exist in phase 1 (the ``actual_*`` columns stay empty).

Rebase
    When the site's time zone or the show's day changes, every item keeps its local ``HH:MM``
    and its place relative to the show day (an item the morning after stays the morning after),
    except where the target date has no such time (see ``rebase``).

Revision
    Each show's schedule has an integer ``revision`` that changes on every write (replace,
    append, demo, rebase). Writes from the admin page must send the revision they started from;
    a mismatch means someone else changed it, and the write is refused (no lost updates).

CSV export (not built)
    Titles may start with ``= + - @``, a tab or a carriage return. Any future CSV export must
    neutralise such cells on output (e.g. prefix a single quote), or a spreadsheet may run them
    as formulas.

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
from datetime import time as dtime
from typing import Any, Iterable

from . import sitetime

log = logging.getLogger(__name__)

# In logical running order (the order the admin page lists them).
KINDS: tuple[str, ...] = ("venue_access", "load_in", "crew_call", "soundcheck", "doors", "act",
                          "changeover", "curfew", "load_out", "other")
# Kinds that put a marker on the timeline unless the item says otherwise (schedule_items.marker).
MARKER_KINDS: tuple[str, ...] = ("soundcheck", "doors", "act")
MARKER_SOURCE = "schedule"
MARKER_LABEL_MAX = 120                # the hub's marker label limit
MAX_ITEMS = 300
TITLE_MAX = 120                       # characters
STAGE_MAX = 40                        # characters (same as Dashboard.stage)
SETLIST_MAX_BYTES = 8 * 1024          # per item, UTF-8
SETLIST_TOTAL_MAX_BYTES = 256 * 1024  # per show, UTF-8
IMPORT_MAX_BYTES = 64 * 1024          # pasted / file text, UTF-8
IMPORT_MAX_ROWS = 300
IMPORT_ERRORS_MAX = 100               # line errors listed (the count is always exact)
MAX_COMBINING_RUN = 3                 # accent marks stacked on one letter, at most

# Fixed-text messages: never the submitted value.
MSG_TIME = "Times must be 24-hour HH:MM, for example 19:30"
MSG_END_TIME = "End times must be 24-hour HH:MM, for example 21:15, or left empty"
MSG_END_BEFORE_START = ("The end time must be after the start time (times before the day "
                        "rollover count as the next morning)")
MSG_DATE = "The date must be the show day or the morning after, written YYYY-MM-DD"
MSG_TITLE_LEN = f"Titles must be 1 to {TITLE_MAX} characters"
MSG_TITLE_HIDDEN = "Titles can't contain hidden or control characters, line breaks or stacked accent marks"
MSG_STAGE_LEN = f"Stage names can be up to {STAGE_MAX} characters"
MSG_STAGE_HIDDEN = "Stage names can't contain hidden or control characters, line breaks or stacked accent marks"
MSG_KIND = ("Kind must be venue access, load in, crew call, soundcheck, doors, act, changeover, "
            "curfew, load out or other")
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
MSG_CHANGED = "The schedule was changed elsewhere. Reload to see it, then make your change again."


def msg_gap(first_valid: dtime | None) -> str:
    """Fixed text for a time the clocks skip; ``first_valid`` comes from the zone's rules."""
    after = f" Use {first_valid:%H:%M} or later." if first_valid is not None else ""
    return "That time doesn't exist on this date because the clocks go forward." + after


# ------------------------------------------------------------------ text checks
def has_hidden_chars(text: str, allow_newlines: bool = False, allow_tabs: bool = False) -> bool:
    """Unicode control (Cc) or format (Cf) characters: zero-width, bidi overrides, NUL, tabs.
    With ``allow_newlines`` a plain ``\\n`` is allowed (multi-line setlists); with
    ``allow_tabs`` a tab is (import text, where it separates columns)."""
    for ch in text:
        if (allow_newlines and ch == "\n") or (allow_tabs and ch == "\t"):
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            return True
    return False


def bad_name_chars(text: str) -> bool:
    """For one-line names (titles, stages): hidden/control characters, line or paragraph
    separators (Zl, Zp), or more than MAX_COMBINING_RUN combining marks in a row."""
    run = 0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Zl", "Zp"):
            return True
        run = run + 1 if cat.startswith("M") else 0
        if run > MAX_COMBINING_RUN:
            return True
    return False


def normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


_TIME_RE = re.compile(r"^([01]?[0-9]|2[0-3])[:.]([0-5][0-9])$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def norm_hhmm(text: str) -> str | None:
    """'19:30', '9:30' or '19.30' -> 'HH:MM'; None if it isn't a time of day."""
    m = _TIME_RE.fullmatch((text or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def clean_title(v: Any) -> str:
    v = (v if isinstance(v, str) else "").strip()
    if not 1 <= len(v) <= TITLE_MAX:
        raise ValueError(MSG_TITLE_LEN)
    if bad_name_chars(v):
        raise ValueError(MSG_TITLE_HIDDEN)
    return v


def clean_stage(v: Any) -> str:
    v = (v if isinstance(v, str) else "").strip()
    if len(v) > STAGE_MAX:
        raise ValueError(MSG_STAGE_LEN)
    if bad_name_chars(v):
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


def clean_date(v: Any) -> str | None:
    """None, or a real 'YYYY-MM-DD' (whether it is the show day or the next is checked later)."""
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not _DATE_RE.fullmatch(v):
        raise ValueError(MSG_DATE)
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(MSG_DATE) from None
    return v


def infer_kind(title: str, default: str = "act") -> str:
    """Changeover / doors / curfew from the title's first word, then venue access, load in, load
    out, crew call and soundcheck as whole words anywhere in it, otherwise ``default``."""
    t = title.strip().casefold()
    if re.match(r"^(change[\s-]?over|c/o)\b", t):
        return "changeover"
    first = re.split(r"[^a-z]+", t, maxsplit=1)[0]
    if first in ("doors", "door"):
        return "doors"
    if first == "curfew":
        return "curfew"
    for kind, pattern in _KIND_WORDS:
        if pattern.search(t):
            return kind
    return default


# Whole-word phrases anywhere in a title (checked in this order; the first match wins).
_KIND_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(rf"(?<![a-z0-9])(?:{words})(?![a-z0-9])"))
    for kind, words in (
        ("venue_access", r"venue[\s-]+access"),
        ("load_in", r"load[\s-]+in|get[\s-]+in"),
        ("load_out", r"load[\s-]+out|get[\s-]+out"),
        ("crew_call", r"crew[\s-]+call"),
        ("soundcheck", r"sound[\s-]?check"),
    ))


def stage_matches(item_stage: str, dash_stage: str | None) -> bool:
    """An item with no stage shows on every dashboard; a dashboard with no stage shows every item."""
    a = (item_stage or "").strip().casefold()
    b = (dash_stage or "").strip().casefold()
    return not a or not b or a == b


# ------------------------------------------------------------------ resolving
class TimeError(ValueError):
    """A time or date problem with an item (fixed text); ``field`` says which input."""

    def __init__(self, field: str, msg: str) -> None:
        super().__init__(msg)
        self.field = field


def _as_date(d: date | str) -> date:
    return d if isinstance(d, date) else date.fromisoformat(d)


def _hhmm_time(hhmm: str) -> dtime:
    return dtime(int(hhmm[:2]), int(hhmm[3:]))


def _at(d: date, t: dtime, site, field: str) -> float:
    """Epoch seconds for wall time ``t`` on date ``d``; refuses a time the clocks skip."""
    if not sitetime.wall_exists(d, t, site):
        raise TimeError(field, msg_gap(sitetime.gap_end(d, t, site)))
    return sitetime.wall_ts(d, t, site)


def resolve_times(day: date | str, start: str, end: str, site,
                  on_date: str | None = None) -> tuple[float, float | None]:
    """Epoch seconds for an item's HH:MM on show day ``day``. Raises TimeError (fixed text).

    Without ``on_date`` the day rollover decides the date of each time, and the end must come
    after the start. With ``on_date`` (show day or the day after) the start is on exactly that
    date and the end is the first matching time after the start."""
    s = norm_hhmm(start)
    if s is None:
        raise TimeError("start", MSG_TIME)
    show_day = _as_date(day)
    st = _hhmm_time(s)
    if on_date:
        d = _as_date(on_date)
        if d not in (show_day, show_day + timedelta(days=1)):
            raise TimeError("date", MSG_DATE)
    else:
        d = sitetime.resolve_date(show_day, s, site)
    ps = _at(d, st, site, "start")
    if not (end or "").strip():
        return ps, None
    e = norm_hhmm(end)
    if e is None:
        raise TimeError("end", MSG_END_TIME)
    et = _hhmm_time(e)
    if on_date:
        de = d if sitetime.wall_ts(d, et, site) > ps else d + timedelta(days=1)
    else:
        de = sitetime.resolve_date(show_day, e, site)
    pe = _at(de, et, site, "end")
    if pe <= ps:
        raise TimeError("end", MSG_END_BEFORE_START)
    return ps, pe


@dataclass
class FieldError:
    index: int   # item index in the submitted list
    field: str   # which field ("" = the item as a whole)
    msg: str     # fixed text


def build_rows(items: Iterable[dict], day: date | str, site) -> tuple[list[dict], list[FieldError]]:
    """Validate and resolve submitted items (dicts with stage, kind, title, start, end, setlist,
    optional id and date) into storable rows: {id, sort, stage, kind, title, planned_start,
    planned_end, setlist}. Every problem is reported; rows are only usable with no errors."""
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
        marker = it.get("marker")
        row["marker"] = None if marker is None else int(bool(marker))
        try:
            on_date = clean_date(it.get("date"))
            row["planned_start"], row["planned_end"] = resolve_times(
                day, it.get("start", ""), it.get("end", ""), site, on_date)
        except TimeError as e:
            errors.append(FieldError(i, e.field, str(e)))
            bad = True
        except ValueError as e:
            errors.append(FieldError(i, "date", str(e)))
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
    """What dashboards get: no sort order, no write times, no (unused) actual times. ``date`` is
    the local calendar date of the start; send it back with an edit to keep the item in place."""
    ps, pe = item["planned_start"], item.get("planned_end")
    return {
        "id": item["id"], "stage": item["stage"], "kind": item["kind"], "title": item["title"],
        "date": sitetime.local(ps, site).date().isoformat(),
        "start": sitetime.local_hhmm(ps, site), "end": sitetime.local_hhmm(pe, site) if pe is not None else "",
        "planned_start": ps, "planned_end": pe, "setlist": item["setlist"],
        "marker": marker_on(item),
    }


# ------------------------------------------------------------------ timeline markers
def marker_on(item: dict) -> bool:
    """Whether the item puts markers on the timeline: its own setting, else its kind's default."""
    m = item.get("marker")
    return item.get("kind") in MARKER_KINDS if m is None else bool(m)


def _with_stage(text: str, stage: str) -> str:
    stage = (stage or "").strip()
    return (f"{text} ({stage})" if stage else text)[:MARKER_LABEL_MAX]


_SOUNDCHECK_RE = re.compile(r"sound[\s-]?check", re.IGNORECASE)


@dataclass(frozen=True)
class Moment:
    item_id: int
    edge: str      # "start" | "end"
    ts: float      # the planned instant (UTC epoch seconds)
    label: str


def act_end(item: dict, items: list[dict]) -> float | None:
    """When an act comes off stage: its own planned end, or the first curfew after its start (for
    its stage) if that is earlier. None when it has neither (it just runs into the next item)."""
    start = item["planned_start"]
    cut = min((c["planned_start"] for c in items if c["kind"] == "curfew" and c["planned_start"] > start
               and stage_matches(c.get("stage", ""), item.get("stage", ""))), default=None)
    ends = [t for t in (item.get("planned_end"), cut) if t is not None]
    return min(ends) if ends else None


def marker_moments(items: list[dict]) -> list[Moment]:
    """Every moment of the schedule that could put a marker on the timeline, in time order,
    whether or not the item's marker setting is on (the caller decides). Labels:

    * soundcheck start: "Soundcheck: <title>", or just the title if it already says soundcheck;
    * doors: "Doors";
    * act start "<title> on stage"; act end "<title> off stage", only when the act has its own
      end or a curfew cuts it off;
    * any other kind (only if its marker is switched on): the title, at its start.

    The stage, if the item has one, follows in brackets: "Doors (Main stage)"."""
    out: list[Moment] = []
    for it in items:
        if not isinstance(it.get("id"), int):
            continue
        kind, title, stage = it["kind"], it["title"], it.get("stage", "")
        if kind == "soundcheck":
            label = title if _SOUNDCHECK_RE.search(title) else f"Soundcheck: {title}"
        elif kind == "doors":
            label = "Doors"
        elif kind == "act":
            label = f"{title} on stage"
            end = act_end(it, items)
            if end is not None:
                out.append(Moment(it["id"], "end", end, _with_stage(f"{title} off stage", stage)))
        else:
            label = title
        out.append(Moment(it["id"], "start", it["planned_start"], _with_stage(label, stage)))
    out.sort(key=lambda m: (m.ts, m.item_id, m.edge))
    return out


# ------------------------------------------------------------------ now / next
def now_next(items: list[dict], now: float, stage: str | None = None) -> dict:
    """NOW / NEXT / CURFEW for one dashboard at ``now``.

    * ``current``: the latest-starting non-curfew item with start <= now < its end (or, with no
      end, the next later item's start, else open-ended). Cut off at the first curfew after its
      start; an item starting at or after a curfew (Load Out) runs normally.
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
    for idx, it in enumerate(acts):
        start = it["planned_start"]
        if start > now:
            break
        # An item is cut off at the first curfew after its start; one that starts at or after a
        # curfew (typically Load Out) runs normally.
        cut = next((c["planned_start"] for c in curfews if c["planned_start"] > start), math.inf)
        end = it.get("planned_end")
        if end is None:
            later = next((a["planned_start"] for a in acts[idx + 1:] if a["planned_start"] > start), None)
            end = later if later is not None else math.inf
        end = min(end, cut)
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
    day (e.g. a 00:30 curfew stays on the morning after).

    The local HH:MM is kept **except where the target date has no such time**: a time the clocks
    skip on the new date (spring forward) lands on the instant ``sitetime.wall_ts`` gives it
    (PEP 495 fold=0, so 01:30 on Sun 29 Mar 2026 in London is 01:30Z, shown as 02:30 BST). A
    repeated time (clocks going back) is its first occurrence. An end that no longer falls after
    its start (only possible inside a DST gap) is dropped rather than stored back to front."""
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
    """tsv if the first real line has a tab (a paste from Excel or Google Sheets); csv if it is
    comma-separated with a time or a column name in its first cell; otherwise lines."""
    for line in text.split("\n"):
        if _skip(line):
            continue
        if "\t" in line:
            return "tsv"
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
        if has_hidden_chars(line, allow_tabs=True):  # a tab inside a title is still refused there
            res.add_error(n, MSG_LINE_HIDDEN)
            continue
        m = _LINE_RE.fullmatch(line.strip())
        if not m:
            res.add_error(n, MSG_LINE_UNREADABLE)
            continue
        title = (m.group("title") or "").strip().lstrip("-–—:").strip()
        _make_row(res, n, m.group("start"), m.group("end") or "", title, "", "", day, site)
    return rows


def _parse_csv(text: str, res: ImportResult, day, site, delimiter: str = ",") -> int:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
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
        hidden = any(has_hidden_chars(c, allow_tabs=True) for c in cells)  # titles refuse tabs later
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
    * ``tsv``: the same columns separated by tabs (a paste from a spreadsheet).
    * ``lines``: ``19:00 Doors`` or ``19:30-20:15 Support: Band`` (an en dash works too).
    * ``auto``: tsv if the first line has a tab, csv when it looks like CSV, else lines.

    One leading byte-order mark (Excel's "CSV UTF-8" files start with one) is removed. Blank
    lines and lines starting with ``#`` are skipped. A missing kind is guessed from the title
    (Doors, Curfew, Changeover), otherwise "act". Bad lines are reported by line number with
    fixed text, never their content. Raises ``ImportTooLarge`` over the size/row caps."""
    if utf8_len(text) > IMPORT_MAX_BYTES:
        raise ImportTooLarge(MSG_IMPORT_TOO_BIG)
    text = normalise_newlines(text)
    if text.startswith("﻿"):
        text = text[1:]
    if fmt not in ("csv", "tsv", "lines"):
        fmt = detect_format(text)
    res = ImportResult(format=fmt)
    if fmt == "lines":
        _parse_lines(text, res, day, site)
    else:
        _parse_csv(text, res, day, site, "\t" if fmt == "tsv" else ",")
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


# (kind, title, start, end) in minutes from now (end None = no end), and the setlist
DEMO_PLAN: tuple[tuple[str, str, int, int | None, str], ...] = (
    ("doors", "Doors", -40, None, ""),
    ("act", "Support: The Harbour Lights", -10, 30, "1. Open Water\n2. Lighthouse\n3. Tidal\n4. Grey Skies"),
    ("changeover", "Changeover", 30, 45, ""),
    ("act", "Headliner: Kestrel Road", 45, 90, DEMO_SETLIST),
    ("curfew", "Curfew", 95, None, ""),
)


def demo_base(now: float) -> float:
    return math.floor(now / 60) * 60


def demo_items(now: float, site) -> list[dict]:
    """The demo day, relative to ``now``: doors 40 min ago, the support act on now, a
    changeover, the headliner in about 45 min (with a Markdown setlist), curfew in about 95 min.
    Returned as submitted items (local HH:MM), so they go through the same checks as an edit."""
    base = demo_base(now)

    def hm(minutes: int | None) -> str:
        return "" if minutes is None else sitetime.local_hhmm(base + minutes * 60, site)

    return [{"kind": k, "title": t, "start": hm(s), "end": hm(e), "stage": "", "setlist": sl}
            for k, t, s, e, sl in DEMO_PLAN]


def demo_fits(rows: list[dict], now: float) -> bool:
    """True if every demo row resolved to exactly its intended instant: none crossed the day
    rollover (e.g. a curfew landing on the morning before) and the show day is today."""
    base = demo_base(now)
    if len(rows) != len(DEMO_PLAN):
        return False
    for row, (_k, _t, s, e, _sl) in zip(rows, DEMO_PLAN):
        if row["planned_start"] != base + s * 60:
            return False
        if row["planned_end"] != (None if e is None else base + e * 60):
            return False
    return True


# ------------------------------------------------------------------ hub service
class StaleShow(Exception):
    """The request was made for a show that is no longer the current one."""


class DemoDoesNotFit(Exception):
    """The demo day (40 min ago to 95 min ahead) would cross the day rollover, or the current
    show's day is not today."""


class StaleRevision(Exception):
    """The write was based on an older revision: someone else changed the schedule since."""


class ScheduleService:
    """The current show's schedule. Every change publishes bus ``schedule`` with
    ``{show_id, revision}`` (small on purpose: clients fetch the list with GET /api/schedule).

    ``revision`` is an integer that only grows within a show: the write time in milliseconds
    (bumped by one if two writes share a millisecond). Each write stores that time as the rows'
    ``updated``, so after a restart the revision is read back from the database unchanged (an
    emptied schedule restarts at 0, which simply makes any older tab reload). No schema change."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self._cache: tuple[int, list[dict]] | None = None
        self._rev: dict[int, int] = {}

    # ---- revision
    def revision(self) -> int:
        show_id = self.hub.recorder.show_id
        if show_id not in self._rev:
            stamps = [i["updated"] for i in self.items() if i.get("updated") is not None]
            self._rev[show_id] = round(max(stamps) * 1000) if stamps else 0
        return self._rev[show_id]

    def _check_revision(self, revision: int | None) -> None:
        if revision is not None and revision != self.revision():
            raise StaleRevision()

    def _next_stamp(self) -> float:
        """The write time to store; its milliseconds become the new revision."""
        rev = max(round(time.time() * 1000), self.revision() + 1)
        self._rev[self.hub.recorder.show_id] = rev
        return rev / 1000

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

    def public(self, stage: str | None = None) -> dict:
        """The full list (GET /api/schedule and admin write responses):
        {show_id, day, revision, items}."""
        return {"show_id": self.hub.recorder.show_id, "day": self.hub.show_info()["day"],
                "revision": self.revision(), "items": self.public_items(stage)}

    def summary(self) -> dict:
        """``snapshot().schedule``: {show_id, day, revision}, no items."""
        return {"show_id": self.hub.recorder.show_id, "day": self.hub.show_info()["day"],
                "revision": self.revision()}

    def stages(self) -> list[str]:
        return [i["stage"] for i in self.items() if i["stage"]]

    # ---- writing
    def _check_show(self, show_id: int | None) -> None:
        if show_id is not None and show_id != self.hub.recorder.show_id:
            raise StaleShow()

    def _publish(self) -> None:
        self.invalidate()
        self.hub.bus.publish("schedule", {"show_id": self.hub.recorder.show_id, "revision": self.revision()})

    def _write(self, rows: list[dict]) -> dict:
        try:
            self.hub.recorder.replace_schedule(self.hub.recorder.show_id, rows, now=self._next_stamp())
        except BaseException:
            self._rev.pop(self.hub.recorder.show_id, None)  # re-read from the database
            raise
        self._publish()
        return self.public()

    def replace(self, show_id: int | None, rows: list[dict], revision: int | None = None) -> dict:
        """Replace the whole list (validated rows from ``build_rows``) in one transaction.
        ``revision`` (if given) must be the current one, else ``StaleRevision``."""
        self._check_show(show_id)
        self._check_revision(revision)
        return self._write(rows)

    def append(self, show_id: int | None, rows: list[dict], revision: int | None = None) -> dict:
        """Add imported rows after the existing ones (one transaction). Caps apply to the total."""
        self._check_show(show_id)
        self._check_revision(revision)
        existing = self.items()
        if len(existing) + len(rows) > MAX_ITEMS:
            raise ImportTooLarge(MSG_TOO_MANY)
        keep = [{**i} for i in existing]
        n = len(keep)
        return self._write(keep + [{**r, "id": None, "sort": n + k} for k, r in enumerate(rows)])

    def load_demo(self, show_id: int | None = None, now: float | None = None) -> dict:
        self._check_show(show_id)
        now = time.time() if now is None else now
        site = self.hub.config.site
        rows, errors = build_rows(demo_items(now, site), self.hub.show_info()["day"], site)
        # The demo runs from 40 min ago to 95 min ahead. Every row must land on its intended
        # instant: none may cross the day rollover, and the show day must be today.
        if errors or not demo_fits(rows, now):
            raise DemoDoesNotFit()
        return self._write(rows)

    def rebase(self, old_site, old_day: str, new_site, new_day: str) -> int:
        """Re-base the current show's items (see ``rebase``). Returns how many moved."""
        items = self.items()
        if not items or not old_day or not new_day:
            return 0
        moved = rebase(items, old_day, old_site, new_day, new_site)
        changes = [(m["id"], m["planned_start"], m["planned_end"]) for m, o in zip(moved, items)
                   if (m["planned_start"], m["planned_end"]) != (o["planned_start"], o.get("planned_end"))]
        if changes:
            try:
                self.hub.recorder.set_schedule_times(self.hub.recorder.show_id, changes, now=self._next_stamp())
            except BaseException:
                self._rev.pop(self.hub.recorder.show_id, None)
                raise
            log.info("Schedule re-based to keep its local times (%d items moved)", len(changes))
        self._publish()
        return len(changes)


class ScheduleMarkers:
    """Puts the current show's schedule on the timeline: a marker (source "schedule") at each
    planned moment (``marker_moments``) as it arrives, for items whose marker setting is on, while
    the site's "Add markers from the schedule" switch is on.

    Each moment is settled exactly once, keyed by (show, item id, start|end) in the database, so a
    restart or a re-save never adds it twice. A moment that passes while its marker is switched
    off is settled without a marker (switching on later doesn't fill in the past). If Stagewatch
    was off when a moment passed, the first check after start-up adds it at its planned time.
    Only the current show is ever looked at. An item moved before its moment gets its marker at
    the new time; markers already placed stay where they are. ``check(now)`` is called once a
    second by the hub (and takes ``now`` so tests can drive the clock)."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self._moments: tuple[tuple[int, int], list[Moment]] | None = None
        self._done: tuple[int, set[tuple[int, str]]] | None = None

    def invalidate(self) -> None:
        self._moments = None
        self._done = None

    def check(self, now: float | None = None) -> list:
        now = time.time() if now is None else now
        rec = self.hub.recorder
        show_id = rec.show_id
        key = (show_id, self.hub.schedule.revision())
        if self._moments is None or self._moments[0] != key:
            self._moments = (key, marker_moments(self.hub.schedule.items()))
        if self._done is None or self._done[0] != show_id:
            self._done = (show_id, rec.schedule_marks(show_id))
        done = self._done[1]
        moments = self._moments[1]
        if not moments or moments[0].ts > now:
            return []
        on = bool(getattr(self.hub.config.site, "schedule_auto_markers", True))
        items = {i["id"]: i for i in self.hub.schedule.items()}
        added = []
        for m in moments:
            if m.ts > now:
                break
            if (m.item_id, m.edge) in done:
                continue
            item = items.get(m.item_id)
            label = m.label if on and item is not None and marker_on(item) else None
            marker = rec.settle_schedule_moment(m.item_id, m.edge, m.ts, label, MARKER_SOURCE, now)
            done.add((m.item_id, m.edge))
            if marker is not None:
                added.append(marker)
                self.hub.bus.publish("marker", marker)
        if added:
            log.info("Added %d marker%s from the schedule", len(added), "" if len(added) == 1 else "s")
        return added
