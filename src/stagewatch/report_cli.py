"""`stagewatch report`: make a show report from a data folder, with Stagewatch stopped or crashed.

It never starts the hub and never writes to the data folder: the history is copied to a temporary
folder (with its -wal, where a crash left one), the copy is checked and read, and the copy is removed.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path

from . import updater_common
from .core import sitetime
from .core.report import ALL_SECTIONS, ReportError, ReportScope, build_report, make_zip, redact, render_html
from .core.report.fmt import file_name, span_label
from .core.report.query import get_show, list_shows, run_spans
from .core.report.readonly import load_config_readonly, open_history_copy, remove_stale_copies


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stagewatch report",
        description="Make a show report from a Stagewatch data folder. Works with Stagewatch stopped, or "
                    "after a crash or power cut. It only reads: your data folder is never changed.")
    p.add_argument("--data-dir", type=Path, default=None, help="the data folder (or a copy, or backups/<id>)")
    p.add_argument("--emulate", action="store_true", help="use the emulate data folder")
    p.add_argument("--list", action="store_true", help="list the shows there are reports for")
    p.add_argument("--show", type=int, default=None, help="show id (see --list)")
    p.add_argument("--day", default=None, help="show day, YYYY-MM-DD")
    p.add_argument("--event", type=int, default=None, help="event id, when two events have the same day")
    p.add_argument("--from", dest="from_hhmm", default="", help="start time HH:MM (with --to)")
    p.add_argument("--to", dest="to_hhmm", default="", help="end time HH:MM (with --from)")
    p.add_argument("--show-time", action="store_true", help="planned doors to planned curfew")
    p.add_argument("--from-marker", type=int, default=None, help="marker id to start at (with --to-marker)")
    p.add_argument("--to-marker", type=int, default=None, help="marker id to end at")
    p.add_argument("--share", action="store_true", help="a Sharing excerpt (leaves out the private details)")
    p.add_argument("--sections", default="", help="comma list of: " + ", ".join(ALL_SECTIONS))
    p.add_argument("--out", type=Path, default=None, help="write the report page here (.html)")
    p.add_argument("--csv", type=Path, default=None, help="write the ZIP of data files here (.zip)")
    p.add_argument("--force", action="store_true", help="replace the output file if it already exists")
    p.add_argument("--timeout", type=float, default=None, metavar="SECONDS",
                   help="stop if the report takes longer than this (default: no limit)")
    return p


def _fail(text: str) -> int:
    print(f"error: {text}", file=sys.stderr)
    return 1


def _flag(conn, show: dict, now: float) -> str:
    end = show["ended"] if show["ended"] is not None else now
    runs = [r for r in run_spans(conn, show["id"], now, False, show["started"], end) if r.end >= show["started"] and r.started <= end]
    if show["ended"] is None:
        if runs and not runs[-1].clean:
            return "open - recording stopped unexpectedly"
        return "open"
    if runs and not runs[-1].clean:
        return "recording stopped unexpectedly"
    return "had an unexpected stop" if any(not r.clean for r in runs) else ""


def _clean(text: object) -> str:
    """Text from the history made safe to print: addresses and secrets scrubbed, control and
    formatting characters (escape codes, new lines, direction marks) removed."""
    s = redact.scrub(text)
    return "".join(c for c in s if not unicodedata.category(c).startswith("C")).strip()


def _hhmm(ts: float | None, site) -> str:
    return sitetime.local_hhmm(ts, site) if ts is not None else "open"


def _list(conn, site) -> None:
    now = time.time()
    shows = list_shows(conn, site)
    if not shows:
        print("There are no shows in this history.")
        return
    last_event = object()
    for s in sorted(shows, key=lambda x: (x["event_id"] or 0, x["id"])):
        if s["event_id"] != last_event:
            print(f"Event {s['event_id'] if s['event_id'] is not None else '-'}: {_clean(s['event_name']) or '(no name)'}")
            last_event = s["event_id"]
        flag = _flag(conn, s, now)
        print(f"  show {s['id']:<4} {_clean(s['day'])}  {_hhmm(s['started'], site)} to {_hhmm(s['ended'], site)}  "
              f"{_clean(s['name'])}{'  [' + flag + ']' if flag else ''}")


def _pick_show(conn, site, args) -> int:
    if args.show is not None:
        get_show(conn, args.show, site)
        return args.show
    if not args.day:
        raise ReportError("no_show", "Say which show: --show ID or --day YYYY-MM-DD (see --list).")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.day):
        raise ReportError("no_show", "The day must be written as YYYY-MM-DD, for example 2026-10-03.")
    hits = [s for s in list_shows(conn, site) if s["day"] == args.day and (args.event is None or s["event_id"] == args.event)]
    if not hits:
        raise ReportError("no_show", "There is no show on that day (see --list).")
    if len(hits) > 1:
        ids = ", ".join(str(s["id"]) for s in hits)
        raise ReportError("no_show", f"More than one show is on that day (ids {ids}). Use --show ID, or --event ID.")
    return hits[0]["id"]


def _scope(show_id: int, args) -> ReportScope:
    preset, kw = "day", {}
    if args.show_time:
        preset = "show_time"
    elif args.from_marker is not None or args.to_marker is not None:
        preset, kw = "markers", {"from_marker": args.from_marker, "to_marker": args.to_marker}
    elif args.from_hhmm or args.to_hhmm:
        preset, kw = "custom", {"from_hhmm": args.from_hhmm, "to_hhmm": args.to_hhmm}
    sections = None
    if args.sections:
        sections = {x.strip() for x in args.sections.split(",") if x.strip()}
        if not sections <= set(ALL_SECTIONS):
            raise ReportError("bad_sections", "Unknown section. Choose from: " + ", ".join(ALL_SECTIONS))
    return ReportScope(show_id, preset, share=args.share, sections=sections, **kw)


def _check_out(path: Path, suffix: str, data_dir: Path, force: bool) -> None:
    if path.suffix.lower() != suffix:
        raise ReportError("bad_preset", f"The file name must end in {suffix}.")
    target = path.resolve()
    root = data_dir.resolve()
    if target == root or root in target.parents:
        raise ReportError("bad_preset", "Choose a place outside the Stagewatch data folder. "
                                        "The report never writes into it.")
    if path.is_dir():
        raise ReportError("bad_preset", "That is a folder. Give a file name.")
    if path.exists() and not force:
        raise ReportError("bad_preset", "That file already exists. Choose another name, or add --force to replace it.")


def _write(path: Path, blob: bytes, force: bool) -> None:
    """Write a new file (never over an existing one unless --force)."""
    with open(path, "wb" if force else "xb") as f:
        f.write(blob)


def run(argv: list[str], resolve_data_dir) -> int:
    args = _parser().parse_args(argv)
    data_dir = resolve_data_dir(args.data_dir, args.emulate)
    if not data_dir.is_dir():
        return _fail("That data folder does not exist.")
    remove_stale_copies()
    try:
        if args.out:
            _check_out(args.out, ".html", data_dir, args.force)
        if args.csv:
            _check_out(args.csv, ".zip", data_dir, args.force)
        if args.out and args.csv and args.out.resolve() == args.csv.resolve():
            raise ReportError("bad_preset", "--out and --csv must be different files.")
        cfg, warning = load_config_readonly(data_dir)
        running = updater_common.server_running(data_dir)
        deadline = args.timeout if args.timeout and args.timeout > 0 else None
        with open_history_copy(data_dir, running, deadline) as conn:
            if args.list:
                _list(conn, cfg.site)
                return 0
            show_id = _pick_show(conn, cfg.site, args)
            scope = _scope(show_id, args)
            source = ("the history files of a running Stagewatch (read as a copy)" if running
                      else "the history files in a data folder (read as a copy, Stagewatch not needed)")
            data = build_report(conn, cfg, scope, time.time(), source, False, [warning] if warning else None,
                                deadline_s=deadline)
            html = render_html(data)
            blob = make_zip(data) if args.csv else None
            show = data.window.show
            window_preset = data.window.preset
    except ReportError as e:
        if e.code == "damaged":
            return _fail("The history file is damaged. Try a backup: --data-dir <your data folder>/backups/<id> "
                         "(see docs/updating-and-backups.md).")
        return _fail(e.message)
    except OSError:
        return _fail("A file could not be read or written. Check the folder and the file name.")
    out = args.out
    if out is None and blob is None:
        out = Path(file_name(show, span_label(window_preset), "html"))
        try:
            _check_out(out, ".html", data_dir, args.force)
        except ReportError as e:
            return _fail(e.message)
    try:
        if out is not None:
            _write(out, html.encode("utf-8"), args.force)
            print(f"Report written: {out}")
        if blob is not None:
            _write(args.csv, blob, args.force)
            print(f"Data files written: {args.csv}")
    except OSError:
        return _fail("The report could not be saved there. Check the folder and the file name.")
    return 0
