"""`stagewatch report` on a copied data folder: a clean one, a WAL-only crash copy, a damaged file,
a data folder with a running server's lock, and proof that the originals are never written."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from stagewatch.core.recorder import Recorder
from stagewatch.core.report.demo import seed_demo_day

ROOT = Path(__file__).resolve().parent.parent
SITE = SimpleNamespace(timezone="Europe/London", day_rollover="06:00")


def cli(*args, cwd=None):
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run([sys.executable, "-m", "stagewatch", "report", *map(str, args)], capture_output=True, text=True,
                          cwd=cwd, env=env, timeout=120)


def hashes(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    rec = Recorder(d / "stagewatch.sqlite3")
    rec.begin_run("test")
    sid = seed_demo_day(rec, time.time(), SITE)
    rec.close()
    (d / "config.yaml").write_text("site:\n  timezone: Europe/London\n", encoding="utf-8")
    return d, sid


def test_list_and_report_leave_the_originals_alone(folder, tmp_path):
    d, sid = folder
    before = hashes(d)
    out = tmp_path / "out"
    out.mkdir()
    r = cli("--data-dir", d, "--list")
    assert r.returncode == 0, r.stderr
    assert f"show {sid}" in r.stdout and "Demo Festival" in r.stdout and "had an unexpected stop" in r.stdout
    r = cli("--data-dir", d, "--show", sid, "--share", "--out", out / "r.html", "--csv", out / "r.zip")
    assert r.returncode == 0, r.stderr
    html = (out / "r.html").read_text(encoding="utf-8")
    assert "Line check" in html and "<script" not in html.lower()
    assert "report.html" in zipfile.ZipFile(out / "r.zip").namelist()
    assert hashes(d) == before


def test_day_range_and_ambiguity(folder, tmp_path):
    d, sid = folder
    # The demo day's own row, not the first row: the first is today's live show, which only overlaps 19:30 to 23:00
    # when the test happens to run in the evening.
    day = next(line.split()[2] for line in cli("--data-dir", d, "--list").stdout.splitlines()
               if line.strip().startswith(f"show {sid} "))
    r = cli("--data-dir", d, "--day", day, "--from", "19:30", "--to", "23:00", "--out", tmp_path / "a.html")
    assert r.returncode == 0, r.stderr
    r = cli("--data-dir", d, "--day", "1999-01-01", "--out", tmp_path / "b.html")
    assert r.returncode == 1 and "no show on that day" in r.stderr and not (tmp_path / "b.html").exists()
    r = cli("--data-dir", d, "--show", sid, "--sections", "nonsense", "--out", tmp_path / "c.html")
    assert r.returncode == 1 and "Unknown section" in r.stderr


def test_wal_only_crash_copy(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    rec = Recorder(live / "stagewatch.sqlite3")
    rec.begin_run("test")
    now = time.time()
    for i in range(30):
        rec.record_state("esp1.temp", 20.0 + i / 10, now - 600 + i * 20)
    rec.flush()
    sid = rec.show_id
    crash = tmp_path / "crash"          # copied while the writer is still open: the rows are only in the -wal
    crash.mkdir()
    for suffix in ("", "-wal"):
        shutil.copyfile(str(live / "stagewatch.sqlite3") + suffix, str(crash / "stagewatch.sqlite3") + suffix)
    rec.close()
    assert (crash / "stagewatch.sqlite3-wal").stat().st_size > 0
    before = hashes(crash)
    r = cli("--data-dir", crash, "--show", sid, "--out", tmp_path / "crash.html")
    assert r.returncode == 0, r.stderr
    html = (tmp_path / "crash.html").read_text(encoding="utf-8")
    assert "stopped unexpectedly" in html
    assert hashes(crash) == before


def test_damaged_file_is_said_plainly(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "stagewatch.sqlite3").write_bytes(b"this is not a database" * 500)
    before = hashes(d)
    r = cli("--data-dir", d, "--list")
    assert r.returncode == 1 and "damaged" in r.stderr and "backups" in r.stderr
    assert "Traceback" not in r.stderr
    assert hashes(d) == before


def test_missing_database_and_bad_output_names(folder, tmp_path):
    d, sid = folder
    empty = tmp_path / "empty"
    empty.mkdir()
    r = cli("--data-dir", empty, "--list")
    assert r.returncode == 1 and "no history file" in r.stderr.lower()
    r = cli("--data-dir", d, "--show", sid, "--out", d / "config.yaml")
    assert r.returncode == 1 and "must end in .html" in r.stderr
    assert (d / "config.yaml").read_text(encoding="utf-8").startswith("site:")


def test_unreadable_config_is_a_warning_not_a_change(folder, tmp_path):
    d, sid = folder
    (d / "config.yaml").write_text(": : not yaml [", encoding="utf-8")
    before = hashes(d)
    r = cli("--data-dir", d, "--show", sid, "--out", tmp_path / "w.html")
    assert r.returncode == 0, r.stderr
    assert "could not be read" in (tmp_path / "w.html").read_text(encoding="utf-8")
    assert hashes(d) == before          # no .bak, no renamed copy


# ------------------------------------------------------------------ output files, --list, copies (security review)
def test_output_inside_the_data_folder_is_refused(folder, tmp_path):
    d, sid = folder
    before = hashes(d)
    r = cli("--data-dir", d, "--show", sid, "--out", d / "report.html")
    assert r.returncode == 1 and "outside the Stagewatch data folder" in r.stderr
    r = cli("--data-dir", d, "--show", sid, "--csv", d / "sub" / ".." / "r.zip")
    assert r.returncode == 1 and "outside the Stagewatch data folder" in r.stderr
    r = cli("--data-dir", d, "--show", sid, cwd=d)       # the default name lands in the cwd: also refused
    assert r.returncode == 1 and "outside the Stagewatch data folder" in r.stderr
    assert hashes(d) == before


def test_existing_file_needs_force(folder, tmp_path):
    d, sid = folder
    out = tmp_path / "keep.html"
    out.write_text("old", encoding="utf-8")
    r = cli("--data-dir", d, "--show", sid, "--out", out)
    assert r.returncode == 1 and "--force" in r.stderr
    assert out.read_text(encoding="utf-8") == "old"
    r = cli("--data-dir", d, "--show", sid, "--out", out, "--force")
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_default_name_comes_from_the_shared_file_name(folder, tmp_path):
    d, sid = folder
    work = tmp_path / "work"
    work.mkdir()
    r = cli("--data-dir", d, "--show", sid, "--show-time", cwd=work)
    assert r.returncode == 0, r.stderr
    names = [p.name for p in work.iterdir()]
    assert len(names) == 1 and re.fullmatch(r"stagewatch-report-[a-z0-9-]+-\d{4}-\d{2}-\d{2}-showtime\.html", names[0])


def test_list_scrubs_names_and_control_characters(folder):
    import sqlite3
    d, sid = folder
    db = sqlite3.connect(str(d / "stagewatch.sqlite3"))
    db.execute("UPDATE shows SET name = ? WHERE id = ?", ("Hall \x1b[31mred\x07 10.1.2.3\nFAKE LINE", sid))
    db.commit()
    db.close()
    r = cli("--data-dir", d, "--list")
    assert r.returncode == 0, r.stderr
    assert "\x1b" not in r.stdout and "\x07" not in r.stdout and "10.1.2.3" not in r.stdout
    assert "FAKE LINE" in r.stdout and "\nFAKE LINE" not in r.stdout


def test_timeout_option_is_accepted(folder, tmp_path):
    d, sid = folder
    r = cli("--data-dir", d, "--show", sid, "--timeout", "120", "--out", tmp_path / "t.html")
    assert r.returncode == 0, r.stderr


def test_stale_working_copies_are_removed_and_fresh_ones_kept(tmp_path):
    import os
    from stagewatch.core.report.readonly import remove_stale_copies
    old, new, other = tmp_path / "stagewatch-report-old", tmp_path / "stagewatch-report-new", tmp_path / "other-old"
    for p in (old, new, other):
        p.mkdir()
        (p / "stagewatch.sqlite3").write_bytes(b"x")
    two_days = time.time() - 2 * 86400
    os.utime(old, (two_days, two_days))
    os.utime(other, (two_days, two_days))
    assert remove_stale_copies(temp_root=tmp_path) == 1
    assert not old.exists() and new.exists() and other.exists()


def test_not_enough_free_space_is_a_plain_message(folder, tmp_path, monkeypatch):
    from collections import namedtuple
    from stagewatch.core.report import readonly
    from stagewatch.core.report.query import ReportError
    d, _ = folder
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(readonly.shutil, "disk_usage", lambda p: usage(10, 10, 0))
    with pytest.raises(ReportError) as ei:
        readonly.prepare_copy(d, tmp_path, False)
    assert ei.value.code == "no_space" and "free space" in ei.value.message
