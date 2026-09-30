import json
import sqlite3
import threading

import pytest

from stagewatch import backup as bk
from stagewatch import updater_common as uc


@pytest.fixture()
def dirs(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    sd = uc.ensure_state_dir(tmp_path / "repo")
    return data, sd


def _make_db(path, rows=50, wal=True):
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    con.commit()
    con.close()


def _populate(data):
    (data / "config.yaml").write_text("config_schema: 1\n")
    (data / "secret.key").write_bytes(b"k" * 32)
    (data / "contacts").mkdir()
    (data / "contacts" / "a.json").write_text("{}")
    (data / "branding").mkdir()
    (data / "branding" / "sub").mkdir()
    (data / "branding" / "sub" / "logo.png").write_bytes(b"png")
    _make_db(data / bk.DB_NAME)


def test_backup_manifest_and_files(dirs):
    data, sd = dirs
    _populate(data)
    m = bk.create_backup(data, sd, "b1", from_sha="a" * 40, to_sha="b" * 40)
    assert set(m["files"]) == {"config.yaml", "secret.key", "contacts/a.json", "branding/sub/logo.png", bk.DB_NAME}
    assert (sd / "backups" / "b1.json").exists()
    assert not list((data / "backups" / "b1").glob("*-wal"))
    # the backup DB is a fresh non-WAL file (header bytes 18/19 == 1 means rollback journal)
    header = (data / "backups" / "b1" / bk.DB_NAME).read_bytes()[:20]
    assert header[18] == 1 and header[19] == 1
    assert bk.verify_backup(data, sd, "b1")["id"] == "b1"
    assert bk.backup_manifest_path(sd, "b1").parent == sd / "backups"  # never in the data dir


def test_optional_parts_absent(dirs):
    data, sd = dirs
    (data / "config.yaml").write_text("x: 1\n")
    m = bk.create_backup(data, sd, "b1")
    assert set(m["files"]) == {"config.yaml"}


def test_duplicate_and_bad_ids(dirs):
    data, sd = dirs
    bk.create_backup(data, sd, "b1")
    with pytest.raises(uc.UpdaterError):
        bk.create_backup(data, sd, "b1")
    with pytest.raises(uc.UpdaterError):
        bk.create_backup(data, sd, "../evil")


def test_round_trip_restore_displaces_current(dirs):
    data, sd = dirs
    _populate(data)
    bk.create_backup(data, sd, "b1")
    # mutate everything after the backup
    (data / "config.yaml").write_text("changed\n")
    shutil_rm = __import__("shutil").rmtree
    shutil_rm(data / "contacts")
    con = sqlite3.connect(data / bk.DB_NAME)
    con.execute("INSERT INTO t (v) VALUES ('after backup')")
    con.commit()
    con.close()
    (data / (bk.DB_NAME + "-wal")).write_bytes(b"stale wal")
    (data / (bk.DB_NAME + "-shm")).write_bytes(b"stale shm")

    displaced = bk.restore_backup(data, sd, "b1")

    assert displaced.name.startswith("displaced-") and displaced.parent == data / "backups"
    assert (data / "config.yaml").read_text() == "config_schema: 1\n"
    assert (data / "contacts" / "a.json").read_text() == "{}"
    con = sqlite3.connect(data / bk.DB_NAME)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    con.close()
    # old DB + wal/shm and the changed config were kept, not deleted
    assert (displaced / bk.DB_NAME).exists()
    assert (displaced / (bk.DB_NAME + "-wal")).read_bytes() == b"stale wal"
    assert (displaced / (bk.DB_NAME + "-shm")).exists()
    assert (displaced / "config.yaml").read_text() == "changed\n"
    assert not (data / (bk.DB_NAME + "-wal")).exists()


def test_restore_refuses_tampered_backup_and_touches_nothing(dirs):
    data, sd = dirs
    _populate(data)
    bk.create_backup(data, sd, "b1")
    (data / "config.yaml").write_text("live\n")
    (data / "backups" / "b1" / "config.yaml").write_text("tampered\n")
    with pytest.raises(uc.UpdaterError) as ei:
        bk.restore_backup(data, sd, "b1")
    assert ei.value.category == "backup_hash_mismatch"
    assert (data / "config.yaml").read_text() == "live\n"
    assert not [p for p in (data / "backups").iterdir() if p.name.startswith("displaced-")]


def test_restore_refuses_missing_file_and_bad_manifest(dirs):
    data, sd = dirs
    _populate(data)
    bk.create_backup(data, sd, "b1")
    (data / "backups" / "b1" / "secret.key").unlink()
    with pytest.raises(uc.UpdaterError):
        bk.restore_backup(data, sd, "b1")
    with pytest.raises(uc.UpdaterError) as ei:
        bk.restore_backup(data, sd, "nope")
    assert ei.value.category == "backup_missing"
    # a manifest pointing outside the data dir is rejected
    bad = {"format": 1, "id": "b2", "files": {"../../evil": "0" * 64}}
    (sd / "backups" / "b2.json").write_text(json.dumps(bad))
    with pytest.raises(uc.UpdaterError) as ei:
        bk.load_manifest(sd, "b2")
    assert ei.value.category == "backup_invalid"


def test_manifest_lives_outside_data_dir_so_data_dir_edits_cannot_forge_it(dirs):
    data, sd = dirs
    _populate(data)
    bk.create_backup(data, sd, "b1")
    # attacker with data-dir write access replaces a file AND drops a fake manifest next to it
    (data / "backups" / "b1" / "config.yaml").write_text("evil\n")
    (data / "backups" / "b1.json").write_text(json.dumps({"format": 1, "id": "b1", "files": {}}))
    with pytest.raises(uc.UpdaterError):
        bk.verify_backup(data, sd, "b1")


def test_retention_keeps_ten_and_never_prunes_displaced(dirs):
    data, sd = dirs
    (data / "config.yaml").write_text("x")
    (data / "backups" / "displaced-keep").mkdir(parents=True)
    for i in range(13):
        bk.create_backup(data, sd, f"20260101T0000{i:02d}Z", keep=99)  # keep is capped at 10
    ids = [m["id"] for m in bk.list_backups(sd)]
    assert len(ids) == 10 and ids[-1] == "20260101T000012Z" and ids[0] == "20260101T000003Z"
    assert not (data / "backups" / "20260101T000000Z").exists()
    assert (data / "backups" / "displaced-keep").exists()
    assert bk.prune_backups(data, sd, 3) == ids[:7]


def test_wal_db_written_during_backup_is_consistent(dirs):
    data, sd = dirs
    db = data / bk.DB_NAME
    _make_db(db, rows=2000)
    stop = threading.Event()
    errors = []
    written = [0]

    def writer():
        con = sqlite3.connect(db, timeout=30)
        try:
            while not stop.is_set():
                with con:
                    con.execute("INSERT INTO t (v) VALUES (?)", ("x" * 200,))
                written[0] += 1
        except Exception as e:  # pragma: no cover - would fail the test below
            errors.append(e)
        finally:
            con.close()

    th = threading.Thread(target=writer)
    th.start()
    try:
        for i in range(3):
            bk.create_backup(data, sd, f"w{i}")
    finally:
        stop.set()
        th.join(10)
    assert not errors and written[0] > 0
    for i in range(3):
        f = data / "backups" / f"w{i}" / bk.DB_NAME
        con = sqlite3.connect(f)
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] >= 2000
        con.close()
        bk.verify_backup(data, sd, f"w{i}")
