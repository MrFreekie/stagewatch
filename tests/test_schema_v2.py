"""Config v2 / DB v2 (0.3.0 schema freeze): migration from the 0.2.0 fixtures, events and show
days, hub runs, entity descriptions and the hardened flush."""

import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from stagewatch import backup, updater_common as uc
from stagewatch.core import cards
from stagewatch.core import recorder as rec_mod
from stagewatch.core.config import (Calibration, CalibrationEntry, Config, ConfigStore, Dashboard,
                                    EsphomeDeviceConfig, SiteConfig, WallClockConfig, _v1_to_v2, migrate)
from stagewatch.core.hub import Hub, down_text
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.core.recorder import Recorder, updater_backup_covers
from stagewatch.core.updater import parse_schema_versions
from stagewatch.version import CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION
from stagewatch.web.auth import verify_pin

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "v1"
LEGACY_PASSWORD = "fixture-legacy-api-password"
FIXTURE_PSK = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
V1_TABLES = {"shows", "states", "markers", "alarm_log"}
V2_TABLES = {"events", "schedule_items", "spl_limit_revisions", "device_log", "hub_runs", "entity_meta"}
V2_INDEXES = {"idx_schedule_show", "idx_spl_scope", "idx_device_log_show_ts", "idx_device_log_show_src",
              "idx_device_log_import", "idx_hub_runs_started"}


def _counts(path: Path) -> dict:
    db = sqlite3.connect(str(path))
    try:
        return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(V1_TABLES)}
    finally:
        db.close()


def _q(path: Path, sql: str, *args):
    db = sqlite3.connect(str(path))
    try:
        return db.execute(sql, args).fetchall()
    finally:
        db.close()


def _dump(path: Path) -> tuple[int, list[str]]:
    db = sqlite3.connect(str(path))
    try:
        return db.execute("PRAGMA user_version").fetchone()[0], list(db.iterdump())
    finally:
        db.close()


@pytest.fixture
def v1_db(tmp_path) -> Path:
    p = tmp_path / "stagewatch.sqlite3"
    shutil.copyfile(FIX / "stagewatch.sqlite3", p)
    return p


@pytest.fixture
def v1_dir(tmp_path) -> Path:
    for name in ("stagewatch.sqlite3", "config.yaml"):
        shutil.copyfile(FIX / name, tmp_path / name)
    return tmp_path


@pytest.fixture(autouse=True)
def _not_supervised(monkeypatch):
    for name in (rec_mod.ENV_UPDATE_TRIAL, uc.ENV_SUPERVISED, uc.ENV_STATE_DIR):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------ constants
def test_version_constants_keep_the_line_format_the_updater_parses():
    text = (ROOT / "src" / "stagewatch" / "version.py").read_text(encoding="utf-8")
    assert parse_schema_versions(text) == (2, 2) == (CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION)
    assert "\nCONFIG_SCHEMA_VERSION = 2\n" in text and "\nDB_SCHEMA_VERSION = 2\n" in text


def test_fixtures_are_v1():
    assert _q(FIX / "stagewatch.sqlite3", "PRAGMA user_version") == [(1,)]
    raw = yaml.safe_load((FIX / "config.yaml").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1 and any("password" in d for d in raw["esphome_devices"])


# ------------------------------------------------------------- DB migration
def test_v1_fixture_migrates_to_v2(v1_db):
    before = _counts(v1_db)
    shows = _q(v1_db, "SELECT id, started FROM shows ORDER BY id")
    rec = Recorder(v1_db)
    rec.close()
    assert _q(v1_db, "PRAGMA user_version") == [(2,)]
    assert _counts(v1_db) == before and before["states"] > 1000 and before["shows"] == 2
    events = _q(v1_db, "SELECT id, name, created, ended, logo_on_dark, logo_show_on_admin, logo_show_on_index "
                       "FROM events")
    assert events == [(1, "Event 1", min(s[1] for s in shows), None, "as_is", 1, 1)]
    assert _q(v1_db, "SELECT id, event_id, day FROM shows ORDER BY id") == [(1, 1, None), (2, 1, None)]
    tables = {r[0] for r in _q(v1_db, "SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {r[0] for r in _q(v1_db, "SELECT name FROM sqlite_master WHERE type='index'")}
    assert V1_TABLES | V2_TABLES <= tables and V2_INDEXES <= indexes
    # the reserved device_log table carries the import_id column for imported vendor logs
    cols = [r[1] for r in _q(v1_db, "PRAGMA table_info(device_log)")]
    assert cols[-2:] == ["doc", "import_id"] and {"show_id", "ts", "source", "src_ip", "msg"} <= set(cols)
    assert {r[1] for r in _q(v1_db, "PRAGMA table_info(schedule_items)")} >= {"actual_start", "actual_end"}
    # safety copy: the untouched v1 data, next to the database
    copy = v1_db.with_name("stagewatch.sqlite3.pre-v2.bak")
    assert copy.is_file() and _q(copy, "PRAGMA user_version") == [(1,)] and _counts(copy) == before


def test_migration_never_reads_or_writes_states(v1_db):
    """The step is O(1) in history size: ALTER ADD COLUMN, CREATE, and the tiny shows table."""
    seen: list[str] = []
    real = Recorder._v1_to_v2

    def traced(self):
        self._db.set_trace_callback(seen.append)
        try:
            real(self)
        finally:
            self._db.set_trace_callback(None)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Recorder, "_v1_to_v2", traced)
        Recorder(v1_db).close()
    assert seen and not [s for s in seen if "states" in s.replace("idx_states", "")]


def test_current_show_after_migration_is_in_event_1(v1_db):
    rec = Recorder(v1_db)
    show = rec.current_show()
    assert (show["id"], show["name"], show["event_id"], show["event_name"], show["day"]) == (2, "Day 2", 1, "Event 1", None)
    assert rec.current_event()["name"] == "Event 1" and len(rec.markers()) == 2
    rec.close()


def test_fresh_db_gets_an_event_and_no_safety_copy(tmp_path):
    rec = Recorder(tmp_path / "db.sqlite3")
    assert rec.current_event()["name"] == "Event 1"
    assert rec.current_show()["name"] == "First show" and rec.current_show()["event_id"] == rec.current_event()["id"]
    rec.close()
    assert _q(tmp_path / "db.sqlite3", "PRAGMA user_version") == [(2,)]
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize("where", ["ddl", "backfill"])
def test_error_mid_migration_leaves_v1_untouched(v1_db, monkeypatch, caplog, where):
    before = _dump(v1_db)
    if where == "ddl":  # fails after the events table and the first indexes were created
        monkeypatch.setattr(rec_mod, "_V2_TABLES", rec_mod._V2_TABLES[:3] + ["CREATE TABLE broken ("])
    else:  # fails after all DDL and the ALTERs
        monkeypatch.setattr(Recorder, "_v2_backfill", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises((sqlite3.Error, RuntimeError)):
        Recorder(v1_db)
    assert _dump(v1_db) == before  # still v1, same schema, same rows
    assert "left unchanged" in caplog.text
    monkeypatch.undo()
    Recorder(v1_db).close()  # restart after the failure: migrates cleanly
    assert _q(v1_db, "PRAGMA user_version") == [(2,)]


def test_v2_db_reopens_without_migrating_again(v1_db, monkeypatch, caplog):
    Recorder(v1_db).close()
    monkeypatch.setattr(Recorder, "_v1_to_v2", lambda self: pytest.fail("migrated twice"))
    monkeypatch.setattr(Recorder, "_safety_copy", lambda self: pytest.fail("copied twice"))
    rec = Recorder(v1_db)
    assert len(rec.events()) == 1
    rec.close()
    assert len(list(v1_db.parent.glob("*.bak"))) == 1


def test_newer_db_is_refused(tmp_path):
    p = tmp_path / "db.sqlite3"
    db = sqlite3.connect(str(p))
    db.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(RuntimeError, match="newer than this build"):
        Recorder(p)


def test_second_safety_copy_does_not_overwrite_the_first(v1_db):
    first = v1_db.with_name(v1_db.name + ".pre-v2.bak")
    first.write_bytes(b"older copy")
    Recorder(v1_db).close()
    assert first.read_bytes() == b"older copy"
    others = list(v1_db.parent.glob("stagewatch.sqlite3.pre-v2-*.bak"))
    assert len(others) == 1 and _q(others[0], "PRAGMA user_version") == [(1,)]


def test_failed_safety_copy_stops_before_migrating(v1_db, monkeypatch):
    before = _dump(v1_db)

    def no_space(src, dst):
        Path(dst).write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(backup, "backup_db", no_space)
    with pytest.raises(OSError):
        Recorder(v1_db)
    assert _dump(v1_db) == before and not list(v1_db.parent.glob("*.bak"))


def test_update_trial_skips_the_safety_copy(v1_db, monkeypatch):
    monkeypatch.setenv(rec_mod.ENV_UPDATE_TRIAL, "1")
    Recorder(v1_db).close()
    assert _q(v1_db, "PRAGMA user_version") == [(2,)] and not list(v1_db.parent.glob("*.bak"))


def _updater_world(tmp_path, monkeypatch, *, data_dir: Path, to_sha: str):
    """A managed repo (HEAD = head) with an updater backup of data_dir made for an update to to_sha."""
    head = "a" * 40
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text(head + "\n", encoding="utf-8")
    sd = uc.ensure_state_dir(repo)
    backup.create_backup(data_dir, sd, from_sha="b" * 40, to_sha=to_sha)
    monkeypatch.setattr(rec_mod, "_repo_root", lambda: repo)
    monkeypatch.setenv(uc.ENV_SUPERVISED, "1")
    monkeypatch.setenv(uc.ENV_STATE_DIR, str(sd))
    return head


def test_supervised_start_after_an_updater_backup_skips_the_safety_copy(v1_dir, tmp_path, monkeypatch):
    head = _updater_world(tmp_path, monkeypatch, data_dir=v1_dir, to_sha="a" * 40)
    db = v1_dir / "stagewatch.sqlite3"
    assert updater_backup_covers(db)
    assert not updater_backup_covers(db, now=time.time() + 2 * 3600)  # too old: not this update's backup
    emulate = v1_dir / "emulate" / "stagewatch.sqlite3"
    assert not updater_backup_covers(emulate)  # the emulate folder is never in the updater's backup
    Recorder(db).close()
    assert not list(v1_dir.glob("*.bak")) and head


def test_supervised_start_with_an_unrelated_backup_makes_the_safety_copy(v1_dir, tmp_path, monkeypatch):
    _updater_world(tmp_path, monkeypatch, data_dir=v1_dir, to_sha="c" * 40)
    Recorder(v1_dir / "stagewatch.sqlite3").close()
    assert (v1_dir / "stagewatch.sqlite3.pre-v2.bak").is_file()


# ------------------------------------------------------------- events/days
def test_next_day_and_new_event(tmp_path):
    rec = Recorder(tmp_path / "db.sqlite3")
    ev1 = rec.current_event()["id"]
    day2 = rec.start_show("Day 2", day="2026-10-02")
    assert (day2["event_id"], day2["day"], day2["name"]) == (ev1, "2026-10-02", "Day 2")
    new = rec.start_show("Day 1", new_event_name="Autumn Festival")
    assert new["event_id"] != ev1 and new["event_name"] == "Autumn Festival" and new["day"] is None
    events = rec.events()
    assert [e["name"] for e in events] == ["Autumn Festival", "Event 1"]
    assert events[0]["ended"] is None and events[1]["ended"] is not None
    assert sum(1 for s in rec.shows() if s["ended"] is None) == 1
    assert [s["event_id"] for s in rec.shows()] == [new["event_id"], ev1, ev1]
    rec.close()


def test_rename_event_and_set_day(tmp_path):
    rec = Recorder(tmp_path / "db.sqlite3")
    assert rec.rename_event("  Summer Tour  ")["name"] == "Summer Tour"
    assert rec.set_show_day("2026-07-04")["day"] == "2026-07-04"
    assert rec.set_show_day(None)["day"] is None
    for bad in ("2026-7-4", "2026-02-30", "tomorrow", "2026-07-04T00:00"):
        with pytest.raises(ValueError):
            rec.set_show_day(bad)
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(ValueError):
            rec.rename_event(bad)
        with pytest.raises(ValueError):
            rec.start_show(bad)
    rec.close()
    again = Recorder(tmp_path / "db.sqlite3")  # persisted
    assert again.current_show()["event_name"] == "Summer Tour" and again.current_show()["day"] is None
    again.close()


def test_snapshot_show_carries_event_and_day(tmp_path):
    hub = Hub(tmp_path)
    show = hub.snapshot()["show"]
    assert {"id", "name", "started", "event_id", "event_name", "day"} == set(show)
    hub.recorder.close()


# ----------------------------------------------------------------- hub runs
def test_run_row_and_clean_stop(tmp_path):
    hub = Hub(tmp_path)
    run = hub.recorder.runs()[0]
    assert run["stopped"] is None and run["version"] and set(run["doc"]) == {"tz", "utc_offset_s", "day_rollover"}
    assert run["doc"]["day_rollover"] == "06:00"
    hub.recorder.close()
    run = Recorder(tmp_path / "stagewatch.sqlite3").runs()[0]
    assert run["stopped"] is not None and run["stop_reason"] == "stop"


@pytest.mark.parametrize("reason", ["update", "rollback"])
async def test_stop_for_an_update_is_recorded(tmp_path, reason):
    hub = Hub(tmp_path)
    hub.exit_code = 75
    hub.stop_reason = reason
    await hub.stop()
    rec = Recorder(tmp_path / "stagewatch.sqlite3")
    assert rec.runs()[0]["stop_reason"] == reason and rec.runs()[0]["stopped"] is not None
    assert not [m for m in rec.markers() if m.source == "hub"]  # a clean stop is not an unexpected one
    rec.close()


def test_heartbeat_every_30_s_inside_flush(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(rec_mod.time, "time", lambda: clock[0])
    rec = Recorder(tmp_path / "db.sqlite3")
    rec.begin_run("test")

    def last_seen():
        return _q(tmp_path / "db.sqlite3", "SELECT last_seen FROM hub_runs WHERE id = ?", rec.run_id)[0][0]

    seen = []
    for step in (10, 10, 11, 9, 22):  # t = 10, 20, 31, 40, 62
        clock[0] += step
        rec.flush()  # no readings buffered: the heartbeat still runs
        seen.append(last_seen() - 1_800_000_000.0)
    assert seen == [0, 0, 31, 31, 62]
    rec.close()


def test_history_is_synced_to_disk_every_minute(tmp_path, monkeypatch):
    mono = [1000.0]
    monkeypatch.setattr(rec_mod.time, "monotonic", lambda: mono[0])
    rec = Recorder(tmp_path / "db.sqlite3")
    calls = []
    monkeypatch.setattr(rec, "checkpoint", lambda: (calls.append(mono[0]), setattr(rec, "_last_checkpoint", mono[0])))
    for _ in range(13):
        mono[0] += 10
        rec.flush()
    assert calls == [1060.0, 1120.0]
    monkeypatch.undo()
    rec.close()


def test_killed_process_leaves_an_open_run_and_the_next_start_marks_it(tmp_path):
    script = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from stagewatch.core.hub import Hub
        hub = Hub(Path({str(tmp_path)!r}))
        hub.update_state("site.temperature", 20.0)
        hub.recorder.record_state("x.y", 1.0, time.time())
        hub.recorder.flush()
        print(os.getpid(), flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", "import os\n" + script], stdout=subprocess.PIPE, text=True)
    try:
        pid = int(proc.stdout.readline().strip())
        # TerminateProcess / SIGKILL on the interpreter itself (on Windows the venv python.exe is a
        # redirector that runs it as a child): no clean stop, no Python cleanup.
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    finally:
        proc.wait(timeout=30)
        proc.stdout.close()
    run = Recorder(tmp_path / "stagewatch.sqlite3")
    row = run.runs()[0]
    assert row["stopped"] is None and time.time() - row["last_seen"] <= 30 + 5
    assert _q(tmp_path / "stagewatch.sqlite3", "SELECT COUNT(*) FROM states WHERE entity_id='x.y'") == [(1,)]
    run.close()  # (a run-less Recorder close does not touch hub_runs)
    hub = Hub(tmp_path)
    markers = [m for m in hub.recorder.markers() if m.source == "hub"]
    assert len(markers) == 1 and markers[0].label.startswith("Stagewatch restarted after an unexpected stop (down ")
    assert any(a["alarm_id"] == "hub" and a["event"] == "unclean_stop" for a in hub.recorder.alarm_log())
    hub.recorder.close()
    hub2 = Hub(tmp_path)  # after a clean stop: no new marker
    assert len([m for m in hub2.recorder.markers() if m.source == "hub"]) == 1
    hub2.recorder.close()


def test_down_text():
    assert down_text(20) == "down less than a minute"
    assert down_text(4 * 60 + 10) == "down about 4 min"
    assert down_text(3 * 3600) == "down about 3 h"
    assert down_text(5 * 86400) == "down about 5 days"


# -------------------------------------------------------------- entity meta
def test_site_entities_are_described(tmp_path):
    hub = Hub(tmp_path)
    meta = hub.recorder.entity_meta()
    site = {k: v for k, v in meta.items() if k.startswith("site.")}
    assert set(site) == {"site.temperature", "site.humidity", "site.pressure", "site.speed_of_sound", "site.dew_point"}
    assert site["site.temperature"]["kind"] == "temperature" and site["site.temperature"]["unit"] == "°C"
    assert site["site.pressure"]["device_name"] == "Site average"
    assert site["site.temperature"]["doc"] == {"decimals": 1, "category": "sensor"}
    hub.recorder.close()


def test_entity_meta_is_written_only_on_change(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    writes = []
    real = hub.recorder.upsert_entity_meta
    monkeypatch.setattr(hub.recorder, "upsert_entity_meta", lambda *a, **k: writes.append(real(*a, **k)))
    hub.register_device(Device("foh", "FOH", "esphome", area="FOH", status=Status.OK))
    hub.register_entity(Entity("foh.temperature", "foh", "Temperature", Kind.TEMPERATURE, "°C"))
    assert writes == [True]
    hub.register_device(Device("foh", "FOH", "esphome", area="FOH"))  # a reconnect: nothing changed
    hub.register_entity(Entity("foh.temperature", "foh", "Temperature", Kind.TEMPERATURE, "°C"))
    hub.set_device_status("foh", Status.MISSING, "connection lost")
    assert writes == [True, False, False, False]
    first = hub.recorder.entity_meta()["foh.temperature"]["updated"]
    hub.devices["foh"].area = "Delay tower"  # what the ESPHome integration does on an area change
    hub.bus.publish("device", hub.devices["foh"])
    meta = hub.recorder.entity_meta()["foh.temperature"]
    assert meta["area"] == "Delay tower" and meta["device_name"] == "FOH" and writes[-1] is True
    hub.recorder.close()
    # a restart re-registers the same entity without writing (the cache is loaded from the DB)
    rec = Recorder(tmp_path / "stagewatch.sqlite3")
    assert rec.upsert_entity_meta("foh.temperature", "foh", "temperature", "°C", "Temperature", "FOH",
                                  "Delay tower", {"decimals": 1, "category": "sensor"}) is False
    assert rec.entity_meta()["foh.temperature"]["updated"] >= first
    rec.close()


# ------------------------------------------------------------------- flush
def test_locked_db_during_flush_loses_no_rows(tmp_path, caplog):
    p = tmp_path / "db.sqlite3"
    rec = Recorder(p)
    rec._db.execute("PRAGMA busy_timeout = 50")
    other = sqlite3.connect(str(p), isolation_level=None)
    other.execute("BEGIN EXCLUSIVE")
    for i in range(100):
        rec.record_state("a.b", float(i), 1000.0 + i)
    rec.flush()  # database is locked: the rows are kept
    assert len(rec._pending) == 100 and "Could not save readings (OperationalError)" in caplog.text
    rec.record_state("a.b", 100.0, 1100.0)
    other.execute("ROLLBACK")
    other.close()
    rec.flush()
    assert rec._pending == []
    assert _q(p, "SELECT COUNT(*), MIN(value), MAX(value) FROM states") == [(101, 0.0, 100.0)]
    rec.close()


def test_requeue_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "MAX_PENDING_ROWS", 50)
    rec = Recorder(tmp_path / "db.sqlite3")
    real = rec._db

    class Locked:
        def __getattr__(self, name):
            return getattr(real, name)

        def executemany(self, *a):
            raise sqlite3.OperationalError("database is locked")

    rec._db = Locked()
    for i in range(80):
        rec.record_state("a.b", float(i), float(i))
    rec.flush()
    assert len(rec._pending) == 50 and rec._pending[0][3] == 30.0  # the newest are kept
    rec._db = real
    rec.close()


# ------------------------------------------------------------------- config
def test_config_fixture_migrates_to_v2(v1_dir, caplog):
    caplog.set_level(logging.DEBUG)
    store = ConfigStore(v1_dir / "config.yaml")
    cfg = store.load()
    assert verify_pin("1234", cfg.admin.pin_hash) and not store.recovery_required
    assert {d.slug: d.cards for d in cfg.dashboards} == {
        "foh": ["env_tiles", "schedule", "chart", "markers", "sensors"],
        "monitors": ["env_tiles", "schedule", "chart", "markers", "sensors"],
        "lobby": ["env_tiles", "schedule", "chart", "markers", "sensors", "connect_footer"],
    }
    assert all(d.stage == "" for d in cfg.dashboards)
    assert cfg.entities["foh.temperature"].offset == -0.4 and not cfg.entities["stage_l.humidity"].include_in_average
    assert cfg.calibrations == {} and [t.id for t in cfg.thresholds] == ["foh_hot", "site_damp"]
    assert cfg.site.timezone == "" and cfg.site.day_rollover == "06:00" and cfg.wall_clock == WallClockConfig()
    assert [d.mac for d in cfg.esphome_devices] == ["", "", ""]
    assert not (v1_dir / "config.invalid.yaml").exists()  # a normal load, not a salvage
    store.save()
    text = (v1_dir / "config.yaml").read_text(encoding="utf-8")
    assert "schema_version: 2" in text and "password" not in text and LEGACY_PASSWORD not in text
    for secret in (LEGACY_PASSWORD, FIXTURE_PSK, cfg.admin.pin_hash):
        assert secret not in caplog.text
    again = ConfigStore(v1_dir / "config.yaml").load()  # and the saved v2 file reloads the same
    assert again.model_dump() == cfg.model_dump()


def test_config_migration_is_pure_and_idempotent():
    raw = yaml.safe_load((FIX / "config.yaml").read_text(encoding="utf-8"))
    snapshot = json.dumps(raw, sort_keys=True)
    once = _v1_to_v2(raw)
    assert json.dumps(raw, sort_keys=True) == snapshot  # input untouched
    assert _v1_to_v2(once) == once
    assert migrate(json.loads(json.dumps(once | {"schema_version": 2}))) == once | {"schema_version": 2}
    custom = {"schema_version": 2, "dashboards": [{"slug": "x", "layout": "wall"}]}
    assert migrate(dict(custom))["dashboards"] == custom["dashboards"]  # v2 is never re-migrated


def test_v1_config_without_dashboards_keeps_the_old_default_dashboards():
    cfg = Config.model_validate(migrate({"schema_version": 1, "site": {"name": "X"}}))
    assert [(d.slug, d.layout, d.cards) for d in cfg.dashboards] == [
        ("foh", "tablet", list(cards.LEGACY_CARDS)),
        ("phone", "phone", list(cards.LEGACY_CARDS)),
        ("wall", "wall", list(cards.LEGACY_CARDS) + ["connect_footer"]),
    ]


def test_fresh_install_and_new_dashboards_use_layout_defaults():
    assert {d.slug: d.cards for d in Config().dashboards} == {
        "foh": ["env_tiles", "schedule", "chart", "markers", "sensors"],
        "phone": ["env_tiles", "schedule", "markers", "chart"],
        "wall": ["env_tiles", "schedule", "chart", "connect_footer"],
    }
    assert Dashboard(slug="x", layout="phone").cards == cards.default_cards("phone")
    assert Dashboard(slug="x", layout="wall", cards=[]).cards == []  # explicitly empty stays empty
    assert "wall_clock" not in sum(cards.LAYOUT_DEFAULTS.values(), ())
    assert set(sum(cards.LAYOUT_DEFAULTS.values(), ())) <= set(cards.KNOWN_CARDS_0_3)


def test_unknown_card_ids_are_kept_on_load():
    d = Dashboard.model_validate({"slug": "x", "cards": ["env_tiles", "spl_limits", "contacts", "env_tiles",
                                                         "Bad-Id", 5, "x" * 33]})
    assert d.cards == ["env_tiles", "spl_limits", "contacts"]
    assert cards.unknown_cards(d.cards) == ["spl_limits", "contacts"]
    with pytest.raises(ValueError):
        Dashboard(slug="x", cards=[f"c_{chr(97 + i)}" for i in range(17)])
    assert Dashboard(slug="x", stage="  Main stage ").stage == "Main stage"
    with pytest.raises(ValueError):
        Dashboard(slug="x", stage="x" * 41)


def test_salvage_path_migrates(v1_dir):
    raw = yaml.safe_load((v1_dir / "config.yaml").read_text(encoding="utf-8"))
    raw["osc_out"] = {"rate_hz": "fast"}  # one invalid section forces the salvage path
    (v1_dir / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    store = ConfigStore(v1_dir / "config.yaml")
    cfg = store.load()
    assert (v1_dir / "config.invalid.yaml").exists() and verify_pin("1234", cfg.admin.pin_hash)
    assert cfg.dashboard("lobby").cards[-1] == "connect_footer" and cfg.dashboard("foh").cards == list(cards.LEGACY_CARDS)
    assert "password" not in (v1_dir / "config.yaml").read_text(encoding="utf-8")


def test_bak_path_migrates(v1_dir):
    shutil.copyfile(v1_dir / "config.yaml", v1_dir / "config.yaml.bak")
    (v1_dir / "config.yaml").write_text("site: [torn", encoding="utf-8")
    cfg = ConfigStore(v1_dir / "config.yaml").load()
    assert verify_pin("1234", cfg.admin.pin_hash)
    assert cfg.dashboard("lobby").cards == list(cards.LEGACY_CARDS) + ["connect_footer"]


def test_newer_config_warns_and_ignores_unknown_keys(tmp_path, caplog):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "schema_version": CONFIG_SCHEMA_VERSION + 1, "site": {"name": "Later"}, "spl_limits": {"x": 1},
        "dashboards": [{"slug": "foh", "cards": ["env_tiles", "spl_limits"], "show_contact_phones": True}]}),
        encoding="utf-8")
    cfg = ConfigStore(tmp_path / "config.yaml").load()
    assert "newer than this build" in caplog.text
    assert cfg.site.name == "Later" and cfg.dashboard("foh").cards == ["env_tiles", "spl_limits"]


def test_site_time_validators():
    for ok in ("", "Europe/London", "America/New_York", "UTC", "Etc/GMT+5"):
        assert SiteConfig(timezone=ok).timezone == ok
    for bad in ("../etc/passwd", "Europe/Nowhere", "/etc/localtime", "Europe/London; rm", "x" * 65, "C:\\tz"):
        with pytest.raises(ValueError):
            SiteConfig(timezone=bad)
    for ok in ("00:00", "06:00", "11:59"):
        assert SiteConfig(day_rollover=ok).day_rollover == ok
    for bad in ("12:00", "6:00", "23:30", "06:60", "", "0600"):
        with pytest.raises(ValueError):
            SiteConfig(day_rollover=bad)


def test_wall_clock_address_rules():
    for ok, norm in (("http://127.0.0.1:4001", "http://127.0.0.1:4001"), ("http://ontime.local:4001/", "http://ontime.local:4001"),
                     ("http://[::1]:4001", "http://[::1]:4001"), ("http://ontime", "http://ontime")):
        assert WallClockConfig(ontime_url=ok).ontime_url == norm
    for bad in ("https://127.0.0.1:4001", "http://user:pw@host:4001", "http://host:4001/api", "http://host:4001?x=1",
                "http://host:4001#x", "ftp://host", "host:4001", "http://:4001", "http://host:0", "http://host:99999",
                "http://ho st:4001"):
        with pytest.raises(ValueError):
            WallClockConfig(ontime_url=bad)
    with pytest.raises(ValueError):
        WallClockConfig(warn_offset_s=0.1)


def test_calibration_models():
    assert EsphomeDeviceConfig(id="a", host="h", mac="02:5E:00:00:00:01").mac == "025e00000001"
    with pytest.raises(ValueError):
        EsphomeDeviceConfig(id="a", host="h", mac="xyz")
    entry = {"offset": -0.4, "date": "2026-10-01T12:00:00Z", "method": "migrated"}
    cal = Calibration(offset=-0.4, history=[entry] * 25)
    assert len(cal.history) == 20 and cal.include_in_average and not cal.applied_on_node
    for bad in ({**entry, "date": "2026-10-01T12:00:00"}, {**entry, "date": "2026-10-01T12:00:00+01:00"},
                {**entry, "method": "guess"}, {**entry, "offset": float("nan")}, {**entry, "note": "x" * 201}):
        with pytest.raises(ValueError):
            CalibrationEntry.model_validate(bad)
    ok = Config.model_validate({"calibrations": {"mac:025e00000001/temperature": {"offset": 1},
                                                 "dev:foh/humidity": {}}})
    assert set(ok.calibrations) == {"mac:025e00000001/temperature", "dev:foh/humidity"}
    for key in ("mac:025E00000001/temperature", "mac:025e0000/temperature", "foh.temperature", "dev:foh/"):
        with pytest.raises(ValueError):
            Config.model_validate({"calibrations": {key: {}}})


def test_admin_state_masks_secrets_after_migration(v1_dir):
    from fastapi.testclient import TestClient

    from stagewatch.web.server import create_app
    hub = Hub(v1_dir)
    with TestClient(create_app(hub, manage_hub=False)) as client:
        client.post("/api/admin/login", json={"pin": "1234"})
        r = client.get("/api/admin/state")
        assert r.status_code == 200
        body = r.text
        assert FIXTURE_PSK not in body and LEGACY_PASSWORD not in body and "pbkdf2" not in body
        assert r.json()["config"]["dashboards"][2]["cards"][-1] == "connect_footer"
        assert LEGACY_PASSWORD not in client.get("/api/snapshot").text
    hub.recorder.close()
