"""SQLite recorder: events and shows, entity state history, markers, alarm log, hub runs.

Writes are buffered and flushed on a timer so a burst of sensor updates
costs one transaction, and WAL mode keeps readers (history queries from
dashboards) from blocking the writer.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

from ..version import DB_SCHEMA_VERSION
from .model import Marker

log = logging.getLogger(__name__)

# v1 DDL. Never edit: v1 databases were created with exactly this, and fresh databases start here
# too before the migration steps below bring them up to date.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    started REAL NOT NULL,
    ended REAL
);
CREATE TABLE IF NOT EXISTS states (
    show_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    entity_id TEXT NOT NULL,
    value REAL
);
CREATE INDEX IF NOT EXISTS idx_states_entity_ts ON states (show_id, entity_id, ts);
CREATE TABLE IF NOT EXISTS markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS alarm_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    alarm_id TEXT NOT NULL,
    event TEXT NOT NULL,
    level INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);
"""

# ---- DB v2 (0.3.0): everything 0.3.0-0.5.0 need, created at once so later releases need no bump.
# Tables nothing writes yet: schedule_items (until the schedule ships), spl_limit_revisions (0.4.0),
# device_log (syslog receiver / imported vendor logs). Every statement is idempotent.
_V2_TABLES = [
    """CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created REAL NOT NULL,
    ended REAL,
    logo_on_dark TEXT NOT NULL DEFAULT 'as_is',
    logo_show_on_admin INTEGER NOT NULL DEFAULT 1,
    logo_show_on_index INTEGER NOT NULL DEFAULT 1
)""",
    """CREATE TABLE IF NOT EXISTS schedule_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'other',
    title TEXT NOT NULL,
    planned_start REAL NOT NULL,
    planned_end REAL,
    setlist TEXT NOT NULL DEFAULT '',
    actual_start REAL,
    actual_end REAL,
    updated REAL NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_schedule_show ON schedule_items (show_id, planned_start)",
    """CREATE TABLE IF NOT EXISTS spl_limit_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    show_id INTEGER,
    rev INTEGER NOT NULL,
    saved_at REAL NOT NULL,
    entered_by TEXT NOT NULL DEFAULT '',
    change_note TEXT NOT NULL DEFAULT '',
    copied_from_event INTEGER,
    doc TEXT NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS idx_spl_scope ON spl_limit_revisions (event_id, show_id, rev)",
    """CREATE TABLE IF NOT EXISTS device_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    last_ts REAL,
    repeat INTEGER NOT NULL DEFAULT 1,
    proto TEXT NOT NULL DEFAULT 'syslog',
    source TEXT NOT NULL,
    src_ip TEXT NOT NULL,
    severity INTEGER NOT NULL,
    facility INTEGER,
    device_ts REAL,
    hostname TEXT NOT NULL DEFAULT '',
    app TEXT NOT NULL DEFAULT '',
    msg TEXT NOT NULL,
    rule_id TEXT NOT NULL DEFAULT '',
    doc TEXT,
    import_id TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_device_log_show_ts ON device_log (show_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_device_log_show_src ON device_log (show_id, source, ts)",
    "CREATE INDEX IF NOT EXISTS idx_device_log_import ON device_log (import_id)",
    """CREATE TABLE IF NOT EXISTS hub_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started REAL NOT NULL,
    last_seen REAL NOT NULL,
    stopped REAL,
    stop_reason TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    doc TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_hub_runs_started ON hub_runs (started)",
    """CREATE TABLE IF NOT EXISTS entity_meta (
    entity_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    device_name TEXT NOT NULL DEFAULT '',
    area TEXT NOT NULL DEFAULT '',
    updated REAL NOT NULL,
    doc TEXT
)""",
]
_V2_SHOW_COLUMNS = [
    ("event_id", "ALTER TABLE shows ADD COLUMN event_id INTEGER REFERENCES events(id)"),
    ("day", "ALTER TABLE shows ADD COLUMN day TEXT"),  # 'YYYY-MM-DD' site-local; NULL = derive from started
]

SAFETY_COPY_SUFFIX = ".pre-v2.bak"
SAFETY_COPY_KEEP = 2  # outside the updater's backup retention, so bounded here
UPDATER_BACKUP_MAX_AGE_S = 3600.0
HEARTBEAT_S = 30.0
CHECKPOINT_S = 60.0
MAX_PENDING_ROWS = 200_000  # ~1.5 h of 35 rows/s kept in memory while the DB refuses writes
STOP_REASONS = ("stop", "update", "rollback")
# Written on the NEXT start for a run that never stopped cleanly but ended before the computer
# last started (restart or power loss), so it is not counted as a Stagewatch crash.
REASON_POWER_OR_RESTART = "power_or_restart"
NAME_MAX = 80
_DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def clean_name(name: str, what: str = "name") -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise ValueError(f"{what} must be 1-{NAME_MAX} characters")
    return name


def valid_day(day: str | None) -> str | None:
    """'YYYY-MM-DD' (a real calendar date) or None (= derive the day from the show's start)."""
    if day is None:
        return None
    if not isinstance(day, str) or not _DAY_RE.fullmatch(day):
        raise ValueError("day must be YYYY-MM-DD")
    date.fromisoformat(day)  # ValueError for 2026-02-30
    return day


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def updater_backup_covers(db_path: Path, env=None, now: float | None = None) -> bool:
    """True only when the updater has just taken a backup of exactly this database for the code
    now running, so the v1 -> v2 safety copy can be skipped (it costs time and disk inside the
    launcher's health window).  Anything unsure means "make the safety copy".  All of:

    - a supervised start (the launcher's state dir is in the environment);
    - no pending update file (the launcher removes it before a trial start);
    - the newest backup record contains the database, was made for an update *to* the commit
      we are running, and is less than an hour old;
    - no update/rollback result has been recorded since that backup (a result means the backup
      belongs to an update that already ran, e.g. update, roll back, then check out again);
    - the backup is in this database's own data folder (the emulate subfolder is never backed
      up), passes ``backup.verify_backup`` (sha256 against the admin-only record, no symlinks)
      and is a v1 database."""
    from .. import updater_common as uc
    env = os.environ if env is None else env
    sd = env.get(uc.ENV_STATE_DIR)
    if env.get(uc.ENV_SUPERVISED) != "1" or not sd:
        return False
    try:
        from ..backup import DB_NAME, list_backups, verify_backup
        sd = Path(sd)
        head = uc.read_head_file(_repo_root())
        manifests = list_backups(sd)
        if not head or not manifests or uc.pending_path(sd).exists():
            return False
        m = manifests[-1]
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        created = calendar.timegm(time.strptime(m.get("created", ""), fmt))
        now = time.time() if now is None else now
        if DB_NAME not in m["files"] or m.get("to_sha") != head                 or not -300 <= now - created <= UPDATER_BACKUP_MAX_AGE_S:
            return False
        for e in uc.load_history(sd):
            ts = e.get("ts")
            if not isinstance(ts, str) or calendar.timegm(time.strptime(ts, fmt)) >= created:
                return False
        data_dir = Path(db_path).parent
        verify_backup(data_dir, sd, m["id"])
        copy = data_dir / "backups" / m["id"] / DB_NAME
        ro = sqlite3.connect(copy.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return ro.execute("PRAGMA user_version").fetchone()[0] == 1
        finally:
            ro.close()
    except Exception:  # noqa: BLE001 - unsure means "make the safety copy"
        return False


class Recorder:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self._remove_torn_safety_copies()
        self._migrate()
        self._pending: list[tuple[int, float, str, float | None]] = []
        self._dropped = 0
        self.run_id: int | None = None
        self._last_heartbeat = 0.0
        self._last_checkpoint = time.monotonic()
        self._meta: dict[str, tuple] = {
            r[0]: tuple(r[1:]) for r in self._db.execute(
                "SELECT entity_id, device_id, kind, unit, name, device_name, area, doc FROM entity_meta")}
        self._closed = False
        self._load_current_show()

    # -------------------------------------------------------------- migration
    def _migrate(self) -> None:
        """Schema version lives in PRAGMA user_version. Add a step here
        whenever DB_SCHEMA_VERSION is bumped; never edit old steps."""
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > DB_SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {version} is newer than this build "
                               f"({DB_SCHEMA_VERSION}); upgrade Stagewatch")
        if version < 2:
            if version >= 1 and self._holds_data() and not updater_backup_covers(self.path):
                self._safety_copy()
            self._v1_to_v2()
            version = 2

    def _holds_data(self) -> bool:
        return bool(self._db.execute("SELECT EXISTS (SELECT 1 FROM shows)").fetchone()[0])

    def _remove_torn_safety_copies(self) -> None:
        """A safety copy interrupted by a kill or power cut stays a .tmp file: never a .bak."""
        for p in self.path.parent.glob(self.path.name + ".pre-v2*.tmp*"):
            try:
                p.unlink()
                log.warning("Removed an unfinished safety copy of the database (%s)", p.name)
            except OSError:
                pass

    def _safety_copy(self) -> Path:
        """Consistent, integrity-checked copy of the v1 database next to it, before the first v2
        migration (protects dev installs and the emulate folder, which no updater backup covers).
        Written as .tmp and renamed only when complete; only the newest SAFETY_COPY_KEEP are kept."""
        from ..backup import backup_db
        from ..updater_common import fsync_dir, replace_with_retry
        dst = self.path.with_name(self.path.name + SAFETY_COPY_SUFFIX)
        if dst.exists():  # an older copy (e.g. update, roll back, update again): keep both
            dst = self.path.with_name(f"{self.path.name}.pre-v2-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.bak")
        tmp = dst.with_name(dst.name + ".tmp")
        began = time.monotonic()
        try:
            backup_db(self.path, tmp)  # online backup + PRAGMA integrity_check
            with open(tmp, "rb+") as f:
                os.fsync(f.fileno())
            replace_with_retry(tmp, dst)
            fsync_dir(dst.parent)
        except BaseException as e:
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    os.unlink(str(tmp) + suffix)
                except OSError:
                    pass
            log.error("Could not save a safety copy of the database before upgrading it (%s); "
                      "the database was not changed", type(e).__name__)
            raise
        log.info("Saved a safety copy of the database as %s before upgrading it (%.1f s)",
                 dst.name, time.monotonic() - began)
        self._prune_safety_copies(dst)
        return dst

    def _prune_safety_copies(self, newest: Path) -> None:
        copies = sorted(self.path.parent.glob(self.path.name + ".pre-v2*.bak"),
                        key=lambda p: (p == newest, p.stat().st_mtime), reverse=True)
        for old in copies[SAFETY_COPY_KEEP:]:
            try:
                old.unlink()
                log.info("Removed an older safety copy of the database (%s)", old.name)
            except OSError:
                pass

    def _v1_to_v2(self) -> None:
        """DB v2, in one transaction: on any error the database stays exactly at v1."""
        db = self._db
        began = time.monotonic()
        db.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _V2_TABLES:
                db.execute(stmt)
            cols = {r[1] for r in db.execute("PRAGMA table_info(shows)")}
            for name, stmt in _V2_SHOW_COLUMNS:
                if name not in cols:
                    db.execute(stmt)
            self._v2_backfill()
            db.execute("PRAGMA user_version = 2")
            db.commit()
        except BaseException as e:
            db.rollback()
            log.error("Database upgrade to v2 failed (%s); the database was left unchanged",
                      type(e).__name__)
            raise
        log.info("Database upgraded to v2 (%.2f s)", time.monotonic() - began)

    def _v2_backfill(self) -> None:
        """Every existing show goes into one open event, "Event 1" (created at the first show)."""
        db = self._db
        if db.execute("SELECT EXISTS (SELECT 1 FROM shows WHERE event_id IS NULL)").fetchone()[0]:
            row = db.execute("SELECT id FROM events WHERE ended IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                event_id = row[0]
            else:
                event_id = db.execute(
                    "INSERT INTO events (name, created) VALUES ('Event 1', COALESCE((SELECT MIN(started) FROM shows), ?))",
                    (time.time(),)).lastrowid
            db.execute("UPDATE shows SET event_id = ? WHERE event_id IS NULL", (event_id,))

    # ----------------------------------------------------------------- events
    def _open_event(self) -> int:
        """The one open event; creates one if there is none (fresh database). No commit."""
        row = self._db.execute("SELECT id FROM events WHERE ended IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            return row[0]
        n = self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return self._db.execute("INSERT INTO events (name, created) VALUES (?, ?)",
                                (f"Event {n + 1}", time.time())).lastrowid

    def current_event(self) -> dict:
        row = self._db.execute("SELECT id, name, created FROM events WHERE id = ?", (self.event_id,)).fetchone()
        return {"id": row[0], "name": row[1], "created": row[2]}

    def events(self) -> list[dict]:
        rows = self._db.execute("SELECT id, name, created, ended FROM events ORDER BY id DESC").fetchall()
        return [{"id": r[0], "name": r[1], "created": r[2], "ended": r[3]} for r in rows]

    def rename_event(self, name: str) -> dict:
        name = clean_name(name, "event name")
        self._db.execute("UPDATE events SET name = ? WHERE id = ?", (name, self.event_id))
        self._db.commit()
        self.event_name = name
        return self.current_event()

    # ------------------------------------------------------------------ shows
    def _load_current_show(self) -> None:
        row = self._db.execute(
            "SELECT s.id, s.name, s.started, s.day, e.id, e.name FROM shows s "
            "JOIN events e ON e.id = s.event_id "
            "WHERE s.ended IS NULL AND e.ended IS NULL ORDER BY s.id DESC LIMIT 1").fetchone()
        if row is None:
            self._db.execute("UPDATE shows SET ended = ? WHERE ended IS NULL", (time.time(),))
            self._insert_show("First show")
            self._db.commit()
            return self._load_current_show()
        (self.show_id, self.show_name, self.show_started, self.show_day,
         self.event_id, self.event_name) = row

    def _insert_show(self, name: str, day: str | None = None) -> int:
        """Insert a show into the open event (created if needed). The caller commits."""
        event_id = self._open_event()
        return self._db.execute("INSERT INTO shows (name, started, event_id, day) VALUES (?, ?, ?, ?)",
                                (name, time.time(), event_id, day)).lastrowid

    def start_show(self, name: str, *, new_event_name: str | None = None, day: str | None = None) -> dict:
        """Close the current show and start the next one: the next day of the current event, or
        (``new_event_name``) the first day of a new event, which closes the current event."""
        name = clean_name(name, "show name")
        day = valid_day(day)
        if new_event_name is not None:
            new_event_name = clean_name(new_event_name, "event name")
        self.flush()
        now = time.time()
        try:
            self._db.execute("UPDATE shows SET ended = ? WHERE ended IS NULL", (now,))
            if new_event_name is not None:
                self._db.execute("UPDATE events SET ended = ? WHERE ended IS NULL", (now,))
                self._db.execute("INSERT INTO events (name, created) VALUES (?, ?)", (new_event_name, now))
            self._insert_show(name, day)
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
        self._load_current_show()
        return self.current_show()

    def rename_show(self, name: str) -> dict:
        name = clean_name(name, "show name")
        self._db.execute("UPDATE shows SET name = ? WHERE id = ?", (name, self.show_id))
        self._db.commit()
        self.show_name = name
        return self.current_show()

    def set_show_day(self, day: str | None) -> dict:
        day = valid_day(day)
        self._db.execute("UPDATE shows SET day = ? WHERE id = ?", (day, self.show_id))
        self._db.commit()
        self.show_day = day
        return self.current_show()

    def current_show(self) -> dict:
        return {"id": self.show_id, "name": self.show_name, "started": self.show_started,
                "event_id": self.event_id, "event_name": self.event_name, "day": self.show_day}

    def shows(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, name, started, ended, event_id, day FROM shows ORDER BY id DESC").fetchall()
        return [{"id": r[0], "name": r[1], "started": r[2], "ended": r[3], "event_id": r[4], "day": r[5]}
                for r in rows]

    # ----------------------------------------------------------------- states
    def record_state(self, entity_id: str, value: float | None, ts: float) -> None:
        self._pending.append((self.show_id, ts, entity_id, value))

    def flush(self) -> None:
        """Write buffered readings (and, every 30 s, the run heartbeat) in one commit.  If the
        write fails (e.g. "database is locked") the rows are kept for the next flush, up to
        MAX_PENDING_ROWS; only the oldest beyond that are dropped, and that is logged."""
        if self._closed:
            return
        now = time.time()
        rows, self._pending = self._pending, []
        heartbeat = self.run_id is not None and now - self._last_heartbeat >= HEARTBEAT_S
        if rows or heartbeat:
            try:
                if rows:
                    self._db.executemany(
                        "INSERT INTO states (show_id, ts, entity_id, value) VALUES (?, ?, ?, ?)", rows)
                if heartbeat:
                    self._db.execute("UPDATE hub_runs SET last_seen = ? WHERE id = ?", (now, self.run_id))
                self._db.commit()
            except sqlite3.Error as e:
                try:
                    self._db.rollback()
                except sqlite3.Error:
                    pass
                if not isinstance(e, sqlite3.OperationalError):
                    # Not a busy/locked/disk condition that may pass: retrying the same batch would
                    # block all later history, so drop it (logged without values).
                    self._dropped += len(rows)
                    log.error("Could not save readings (%s); %d readings were dropped",
                              type(e).__name__, len(rows))
                    return
                self._pending = rows + self._pending
                over = len(self._pending) - MAX_PENDING_ROWS
                if over > 0:
                    del self._pending[:over]
                    self._dropped += over
                log.error("Could not save readings (%s); keeping %d to retry%s", type(e).__name__,
                          len(self._pending), f", {self._dropped} oldest dropped so far" if over > 0 else "")
                return
            if heartbeat:
                self._last_heartbeat = now
        if time.monotonic() - self._last_checkpoint >= CHECKPOINT_S:
            self.checkpoint()

    def checkpoint(self) -> None:
        """Sync the history to disk (a WAL checkpoint), so a power cut loses at most about a minute."""
        self._last_checkpoint = time.monotonic()
        try:
            self._db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        except sqlite3.Error as e:
            log.warning("Could not sync the history to disk (%s)", type(e).__name__)

    def history(self, entity_ids: list[str], since: float, until: float | None = None,
                max_points: int = 600, show_id: int | None = None) -> dict[str, list[list[float]]]:
        """Per-entity [[ts, value], ...], averaged into at most max_points
        time buckets so a 12-hour show still draws quickly on a tablet."""
        self.flush()
        until = until if until is not None else time.time()
        show_id = show_id if show_id is not None else self.show_id
        bucket = max((until - since) / max(max_points, 1), 0.001)
        out: dict[str, list[list[float]]] = {}
        for entity_id in entity_ids:
            rows = self._db.execute(
                "SELECT CAST((ts - ?) / ? AS INTEGER) AS b, AVG(ts), AVG(value) "
                "FROM states WHERE show_id = ? AND entity_id = ? AND ts >= ? AND ts <= ? "
                "AND value IS NOT NULL GROUP BY b ORDER BY b",
                (since, bucket, show_id, entity_id, since, until),
            ).fetchall()
            out[entity_id] = [[r[1], r[2]] for r in rows]
        return out

    def value_at(self, entity_id: str, ts: float, max_age_s: float = 600.0) -> float | None:
        """Last recorded value of entity_id at or before ts (current show)."""
        self.flush()
        row = self._db.execute(
            "SELECT value FROM states WHERE show_id = ? AND entity_id = ? AND ts <= ? "
            "AND ts >= ? AND value IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (self.show_id, entity_id, ts, ts - max_age_s),
        ).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- markers
    def add_marker(self, label: str, source: str, ts: float | None = None) -> Marker:
        ts = ts if ts is not None else time.time()
        cur = self._db.execute(
            "INSERT INTO markers (show_id, ts, label, source) VALUES (?, ?, ?, ?)",
            (self.show_id, ts, label, source))
        self._db.commit()
        return Marker(cur.lastrowid, ts, label, source)

    def markers(self, show_id: int | None = None) -> list[Marker]:
        show_id = show_id if show_id is not None else self.show_id
        rows = self._db.execute(
            "SELECT id, ts, label, source FROM markers WHERE show_id = ? ORDER BY ts",
            (show_id,)).fetchall()
        return [Marker(*r) for r in rows]

    def marker(self, marker_id: int) -> Marker | None:
        row = self._db.execute(
            "SELECT id, ts, label, source FROM markers WHERE id = ?", (marker_id,)).fetchone()
        return Marker(*row) if row else None

    def delete_marker(self, marker_id: int) -> bool:
        cur = self._db.execute("DELETE FROM markers WHERE id = ?", (marker_id,))
        self._db.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ alarms
    def log_alarm(self, alarm_id: str, event: str, level: int, message: str) -> None:
        self._db.execute(
            "INSERT INTO alarm_log (show_id, ts, alarm_id, event, level, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.show_id, time.time(), alarm_id, event, level, message))
        self._db.commit()

    def alarm_log(self, limit: int = 200) -> list[dict]:
        rows = self._db.execute(
            "SELECT ts, alarm_id, event, level, message FROM alarm_log WHERE show_id = ? "
            "ORDER BY id DESC LIMIT ?", (self.show_id, limit)).fetchall()
        return [{"ts": r[0], "alarm_id": r[1], "event": r[2], "level": r[3], "message": r[4]}
                for r in rows]

    # -------------------------------------------------------------- hub runs
    def begin_run(self, version: str, doc: dict | None = None) -> dict | None:
        """Record that the hub started.  Returns the previous run if it never stopped cleanly
        (crash, kill, power cut, PC shut down): {"id", "started", "last_seen", "down_s"}."""
        now = time.time()
        prev = self._db.execute(
            "SELECT id, started, last_seen, stopped FROM hub_runs ORDER BY id DESC LIMIT 1").fetchone()
        self.run_id = self._db.execute(
            "INSERT INTO hub_runs (started, last_seen, version, doc) VALUES (?, ?, ?, ?)",
            (now, now, version or "", json.dumps(doc, separators=(",", ":")) if doc is not None else None)).lastrowid
        self._db.commit()
        self._last_heartbeat = now
        self.previous_clean_stop = None
        if prev is not None and prev[3] is not None:
            # The previous run stopped cleanly: say when and why, so the hub can tell a
            # computer restart (e.g. systemd stopping Stagewatch on reboot) from a quiet stop.
            row = self._db.execute("SELECT stopped, stop_reason FROM hub_runs WHERE id = ?", (prev[0],)).fetchone()
            try:
                stopped = float(row[0])
                if stopped == stopped and stopped not in (float("inf"), float("-inf")):
                    self.previous_clean_stop = {"id": prev[0], "stopped": stopped, "reason": row[1] or "",
                                                "down_s": max(0.0, now - stopped)}
            except (TypeError, ValueError):
                pass
            return None
        if prev is None:
            return None
        try:  # a damaged or hand-edited row must never stop the hub starting
            last_seen = float(prev[2])
            if last_seen != last_seen or last_seen in (float("inf"), float("-inf")):
                return None
            return {"id": prev[0], "started": prev[1], "last_seen": last_seen, "down_s": max(0.0, now - last_seen)}
        except (TypeError, ValueError):
            return None

    def set_run_stop_reason(self, run_id: int, reason: str) -> None:
        """Say why an earlier run that never stopped cleanly ended (``stopped`` stays empty: the
        exact time is not known, ``last_seen`` is the last sign of life)."""
        try:
            self._db.execute("UPDATE hub_runs SET stop_reason = ? WHERE id = ? AND stopped IS NULL",
                             (reason, run_id))
            self._db.commit()
        except sqlite3.Error as e:
            log.error("Could not record why the previous run ended (%s)", type(e).__name__)

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self._db.execute("SELECT id, started, last_seen, stopped, stop_reason, version, doc "
                                "FROM hub_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "started": r[1], "last_seen": r[2], "stopped": r[3], "stop_reason": r[4],
                 "version": r[5], "doc": json.loads(r[6]) if r[6] else None} for r in rows]

    # ------------------------------------------------------------ entity meta
    def upsert_entity_meta(self, entity_id: str, device_id: str, kind: str, unit: str = "",
                           name: str = "", device_name: str = "", area: str = "",
                           doc: dict | None = None) -> bool:
        """Describe an entity for history and reports.  Writes only when something changed
        (compared in memory), so reconnects cost nothing.  Returns True if it wrote."""
        doc_text = json.dumps(doc, sort_keys=True, separators=(",", ":")) if doc is not None else None
        row = (device_id, kind, unit or "", name or "", device_name or "", area or "", doc_text)
        if self._meta.get(entity_id) == row:
            return False
        self._db.execute(
            "INSERT INTO entity_meta (entity_id, device_id, kind, unit, name, device_name, area, updated, doc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(entity_id) DO UPDATE SET "
            "device_id = excluded.device_id, kind = excluded.kind, unit = excluded.unit, name = excluded.name, "
            "device_name = excluded.device_name, area = excluded.area, updated = excluded.updated, "
            "doc = excluded.doc", (entity_id, *row[:6], time.time(), doc_text))
        self._db.commit()
        self._meta[entity_id] = row
        return True

    def entity_meta(self) -> dict[str, dict]:
        rows = self._db.execute("SELECT entity_id, device_id, kind, unit, name, device_name, area, updated, doc "
                                "FROM entity_meta").fetchall()
        return {r[0]: {"device_id": r[1], "kind": r[2], "unit": r[3], "name": r[4], "device_name": r[5],
                       "area": r[6], "updated": r[7], "doc": json.loads(r[8]) if r[8] else None} for r in rows}

    # ------------------------------------------------------------------ close
    def close(self, reason: str = "stop") -> None:
        """Clean stop: flush, mark the run stopped (``reason``: stop | update | rollback), close."""
        if self._closed:
            return
        try:
            self.flush()
            if self.run_id is not None:
                now = time.time()
                self._db.execute("UPDATE hub_runs SET stopped = ?, last_seen = ?, stop_reason = ? WHERE id = ?",
                                 (now, now, reason if reason in STOP_REASONS else "stop", self.run_id))
                self._db.commit()
        except sqlite3.Error as e:
            log.error("Could not record the clean stop (%s)", type(e).__name__)
        finally:
            self._closed = True
            self._db.close()
