"""The Schedule page's CSV export (schedule-export.js, built in the browser): the CSV builder's own
checks run in Node, and a file it writes must read back through core/schedule.py parse_import."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stagewatch.core import schedule as sched
from stagewatch.core.config import SiteConfig

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "js" / "schedule_export_test.js"
LON = SiteConfig(timezone="Europe/London", day_rollover="06:00")
DAY = "2026-10-02"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return node


def test_csv_builder_quotes_neutralises_formulas_and_names_files_safely():
    r = subprocess.run([_node(), str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr


def test_exported_csv_imports_again(tmp_path):
    # Kinds that core/schedule.py knows today; the export writes whatever kind value the row has.
    rows = [
        {"start": "19:00", "end": "", "kind": "doors", "stage": "", "title": "Doors"},
        {"start": "19:30", "end": "20:15", "kind": "act", "stage": "Main", "title": 'Support: "The Harbour Lights", live'},
        {"start": "20:15", "end": "20:45", "kind": "changeover", "stage": "Main", "title": "Changeover"},
        {"start": "20:45", "end": "22:15", "kind": "act", "stage": "Second, small", "title": "Headliner – Zoë"},
        {"start": "23:00", "end": "00:30", "kind": "other", "stage": "", "title": "Late set"},
        {"start": "23:00", "end": "", "kind": "curfew", "stage": "", "title": "Curfew"},
    ]
    src = tmp_path / "rows.json"
    out = tmp_path / "schedule.csv"
    src.write_text(json.dumps(rows), encoding="utf-8")
    r = subprocess.run([_node(), str(SCRIPT), "--csv", str(src), str(out)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    text = raw.decode("utf-8")  # keeps the BOM: parse_import has to drop it
    assert text.lstrip("﻿").splitlines()[0] == "start,end,kind,stage,title"
    res = sched.parse_import(text, "auto", DAY, LON)
    assert res.format == "csv" and res.error_count == 0, res.errors
    got = [(x["start"], x["end"], x["title"], x["stage"], x["kind"]) for x in res.rows]
    assert got == [(r["start"], r["end"], r["title"], r["stage"], r["kind"]) for r in rows]


def test_formula_cells_come_back_marked_not_run(tmp_path):
    rows = [{"start": "19:00", "end": "", "kind": "act", "stage": "", "title": "=2+2"}]
    src, out = tmp_path / "r.json", tmp_path / "o.csv"
    src.write_text(json.dumps(rows), encoding="utf-8")
    subprocess.run([_node(), str(SCRIPT), "--csv", str(src), str(out)], check=True, timeout=60)
    res = sched.parse_import(out.read_text(encoding="utf-8"), "csv", DAY, LON)
    assert [x["title"] for x in res.rows] == ["'=2+2"]
