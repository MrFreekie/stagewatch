"""DB v3: marker notes and hiding, the per-item schedule marker setting and the schedule_marks
table. A v2 database (made here by migrating the 0.2.0 v1 fixture with the v2 step only, then
adding schedule rows the way 0.3.x stores them) upgrades in one step and keeps every row."""

import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from stagewatch import backup, updater_common as uc
from stagewatch.core import recorder as rec_mod
from stagewatch.core.recorder import Recorder, updater_backup_covers
from stagewatch.version import DB_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "v1"
ALL_TABLES = ("shows", "states", "markers", "alarm_log", "events", "schedule_items", "spl_limit_revisions",
              "device_log", "hub_runs", "entity_meta")


def _q(path: Path, sql: str, *args):
    db = sqlite3.connect(str(path))
    try:
        return db.execute(sql, args).fetchall()
    finally:
        db.close()


def _counts(path: Path) -> dict:
    return {t: _q(path, f"SELECT COUNT(*) FROM {t}")[0][0] for t in ALL_TABLES}


def _dump(path: Path) -> tuple[int, list[str]]:
    db = sqlite3.connect(str(path))
    try:
        return db.execute("PRAGMA user_version").fetchone()[0], list(db.iterdump())
    finally:
        db.close()


def _cols(path: Path, table: str) -> dict:
    return {r[1]: (r[2], r[3], r[4]) for r in _q(path, f"PRAGMA table_info({table})")}


@pytest.fixture(autouse=True)
def _not_supervised(monkeypatch):
    for name in ("STAGEWATCH_UPDATE_TRIAL", uc.ENV_SUPERVISED, uc.ENV_STATE_DIR):
        monkeypatch.delenv(name, raising=False)


def make_v2(path: Path) -> Path:
    """The v1 fixture migrated with the v2 step only, plus a 0.3.x-style schedule and a hub run."""
    shutil.copyfile(FIX / "stagewatch.sqlite3", path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Recorder, "_v2_to_v3", lambda self: None)  # stop at v2
        Recorder(path).close()
    for f in path.parent.glob(path.name + ".pre-v*.bak"):
        f.unlink()
    db = sqlite3.connect(str(path))
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "marker" not in {r[1] for r in db.execute("PRAGMA table_info(schedule_items)")}
        assert "hidden" not in {r[1] for r in db.execute("PRAGMA table_info(markers)")}
        now = 1_790_000_000.0
        for i, (kind, title, start, end) in enumerate((("doors", "Doors", 0, None),
                                                       ("act", "Support", 1800, 4200),
                                                       ("curfew", "Curfew", 9000, None))):
            db.execute("INSERT INTO schedule_items (show_id, sort, kind, title, planned_start, planned_end, updated) "
                       "VALUES (2, ?, ?, ?, ?, ?, ?)", (i, kind, title, now + start,
                                                        None if end is None else now + end, now))
        db.execute("INSERT INTO hub_runs (started, last_seen, stopped, stop_reason) VALUES (?, ?, ?, 'stop')",
                   (now, now + 60, now + 60))
        db.commit()
    finally:
        db.close()
    return path


@pytest.fixture
def v2_db(tmp_path) -> Path:
    return make_v2(tmp_path / "stagewatch.sqlite3")


def test_v2_fixture_upgrades_to_v3_and_keeps_every_row(v2_db):
    before = _counts(v2_db)
    markers_before = _q(v2_db, "SELECT id, show_id, ts, label, source FROM markers ORDER BY id")
    items_before = _q(v2_db, "SELECT * FROM schedule_items ORDER BY id")
    assert before["states"] > 1000 and before["markers"] >= 2 and before["schedule_items"] == 3
    rec = Recorder(v2_db)
    assert all(m.hidden is False and m.note == "" for m in rec.markers(1) + rec.markers(2))
    rec.close()
    assert _q(v2_db, "PRAGMA user_version") == [(3,)] == [(DB_SCHEMA_VERSION,)]
    assert _counts(v2_db) == before
    # existing rows unchanged; new columns take their defaults (visible, no note, kind default)
    assert _q(v2_db, "SELECT id, show_id, ts, label, source FROM markers ORDER BY id") == markers_before
    assert _q(v2_db, "SELECT DISTINCT hidden, note FROM markers") == [(0, "")]
    assert [r[:-1] for r in _q(v2_db, "SELECT * FROM schedule_items ORDER BY id")] == items_before
    assert _q(v2_db, "SELECT DISTINCT marker FROM schedule_items") == [(None,)]
    assert _cols(v2_db, "markers")["hidden"] == ("INTEGER", 1, "0")
    assert _cols(v2_db, "markers")["note"] == ("TEXT", 1, "''")
    assert _cols(v2_db, "schedule_items")["marker"] == ("INTEGER", 0, None)
    assert set(_cols(v2_db, "schedule_marks")) == {"show_id", "item_id", "edge", "ts", "marker_id", "settled"}
    assert _q(v2_db, "SELECT COUNT(*) FROM schedule_marks") == [(0,)]
    # the safety copy is the untouched v2 database, named for the version it was upgraded to
    copy = v2_db.with_name("stagewatch.sqlite3.pre-v3.bak")
    assert copy.is_file() and _q(copy, "PRAGMA user_version") == [(2,)] and _counts(copy) == before
    assert _q(copy, "PRAGMA integrity_check") == [("ok",)]


def test_v3_step_is_idempotent(v2_db, monkeypatch):
    Recorder(v2_db).close()
    after = _dump(v2_db)
    rec = Recorder(v2_db)
    rec._v2_to_v3()  # run again on a v3 database: nothing changes
    rec.close()
    assert _dump(v2_db) == after
    monkeypatch.setattr(Recorder, "_v2_to_v3", lambda self: pytest.fail("migrated twice"))
    monkeypatch.setattr(Recorder, "_safety_copy", lambda self: pytest.fail("copied twice"))
    Recorder(v2_db).close()
    assert len(list(v2_db.parent.glob("*.bak"))) == 1


@pytest.mark.parametrize("where", ["column", "table"])
def test_error_mid_v3_migration_leaves_v2_untouched(v2_db, monkeypatch, caplog, where):
    before = _dump(v2_db)
    if where == "column":  # fails after the first column was added
        monkeypatch.setattr(rec_mod, "_V3_COLUMNS", rec_mod._V3_COLUMNS[:1] + [("markers", "x", "ALTER TABLE nope")])
    else:  # fails after every column was added
        monkeypatch.setattr(rec_mod, "_V3_TABLES", ["CREATE TABLE broken ("])
    with pytest.raises(sqlite3.Error):
        Recorder(v2_db)
    assert _dump(v2_db) == before  # still v2, same schema, same rows
    assert "upgrade to v3 failed" in caplog.text and "left unchanged" in caplog.text
    monkeypatch.undo()
    Recorder(v2_db).close()  # restart after the failure: migrates cleanly
    assert _q(v2_db, "PRAGMA user_version") == [(3,)]


def test_v3_migration_never_reads_or_writes_states(v2_db):
    seen: list[str] = []
    real = Recorder._v2_to_v3

    def traced(self):
        self._db.set_trace_callback(seen.append)
        try:
            real(self)
        finally:
            self._db.set_trace_callback(None)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Recorder, "_v2_to_v3", traced)
        Recorder(v2_db).close()
    assert seen and not [s for s in seen if "states" in s.replace("idx_states", "")]
    assert not [s for s in seen if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]


def test_v1_goes_to_v3_with_one_safety_copy_of_the_v1_data(tmp_path):
    db = tmp_path / "stagewatch.sqlite3"
    shutil.copyfile(FIX / "stagewatch.sqlite3", db)
    Recorder(db).close()
    assert _q(db, "PRAGMA user_version") == [(3,)]
    copies = list(tmp_path.glob("*.bak"))
    assert [c.name for c in copies] == ["stagewatch.sqlite3.pre-v3.bak"]
    assert _q(copies[0], "PRAGMA user_version") == [(1,)]


def test_older_pre_v2_copies_count_towards_the_limit(v2_db):
    import os
    old = []
    for i, name in enumerate(("pre-v2.bak", "pre-v2-20260101T000000Z.bak")):
        f = v2_db.with_name(f"stagewatch.sqlite3.{name}")
        f.write_bytes(b"old")
        os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))
        old.append(f)
    Recorder(v2_db).close()
    left = sorted(f.name for f in v2_db.parent.glob("stagewatch.sqlite3.pre-v*.bak"))
    assert left == ["stagewatch.sqlite3.pre-v2-20260101T000000Z.bak", "stagewatch.sqlite3.pre-v3.bak"]


def test_torn_v3_safety_copy_is_removed(v2_db):
    v2_db.with_name("stagewatch.sqlite3.pre-v3.bak.tmp").write_bytes(b"SQLite format 3\x00 cut off")
    Recorder(v2_db).close()
    assert not list(v2_db.parent.glob("*.tmp*"))
    assert _q(v2_db.with_name("stagewatch.sqlite3.pre-v3.bak"), "PRAGMA user_version") == [(2,)]


def _updater_world(tmp_path, monkeypatch, data_dir: Path):
    head = "a" * 40
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text(head + "\n", encoding="utf-8")
    sd = uc.ensure_state_dir(repo)
    backup.create_backup(data_dir, sd, from_sha="b" * 40, to_sha=head)
    monkeypatch.setattr(rec_mod, "_repo_root", lambda: repo)
    monkeypatch.setenv(uc.ENV_SUPERVISED, "1")
    monkeypatch.setenv(uc.ENV_STATE_DIR, str(sd))


def test_updater_backup_of_the_v2_database_skips_the_safety_copy(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    db = make_v2(data / "stagewatch.sqlite3")
    _updater_world(tmp_path, monkeypatch, data)
    assert updater_backup_covers(db) and updater_backup_covers(db, version=2)
    assert not updater_backup_covers(db, version=1)  # a v1 backup would be another database
    assert not updater_backup_covers(db, now=time.time() + 2 * 3600)
    Recorder(db).close()
    assert _q(db, "PRAGMA user_version") == [(3,)] and not list(data.glob("*.bak"))


def test_updater_backup_of_a_v3_database_does_not_count(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    db = make_v2(data / "stagewatch.sqlite3")
    Recorder(db).close()  # now v3
    for f in data.glob("*.bak"):
        f.unlink()
    _updater_world(tmp_path, monkeypatch, data)
    assert not updater_backup_covers(db)  # nothing to upgrade
    make_v2(data / "stagewatch.sqlite3")  # the live database is v2 again, the backup is v3
    assert not updater_backup_covers(db)
