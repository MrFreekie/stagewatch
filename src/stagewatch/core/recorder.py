"""SQLite recorder: shows, entity state history, markers, alarm log.

Writes are buffered and flushed on a timer so a burst of sensor updates
costs one transaction, and WAL mode keeps readers (history queries from
dashboards) from blocking the writer.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..version import DB_SCHEMA_VERSION
from .model import Marker

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


class Recorder:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._pending: list[tuple[int, float, str, float | None]] = []
        self.show_id, self.show_name, self.show_started = self._current_or_new_show()

    def _migrate(self) -> None:
        """Schema version lives in PRAGMA user_version. Add a step here
        whenever DB_SCHEMA_VERSION is bumped; never edit old steps."""
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > DB_SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {version} is newer than this build "
                               f"({DB_SCHEMA_VERSION}); upgrade Stagewatch")
        # e.g. if version < 2: self._db.execute("ALTER TABLE ..."); version = 2
        self._db.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
        self._db.commit()

    # ------------------------------------------------------------------ shows
    def _current_or_new_show(self) -> tuple[int, str, float]:
        row = self._db.execute(
            "SELECT id, name, started FROM shows WHERE ended IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return row[0], row[1], row[2]
        return self._insert_show("First show")

    def _insert_show(self, name: str) -> tuple[int, str, float]:
        now = time.time()
        cur = self._db.execute("INSERT INTO shows (name, started) VALUES (?, ?)", (name, now))
        self._db.commit()
        return cur.lastrowid, name, now

    def start_show(self, name: str) -> dict:
        self.flush()
        self._db.execute("UPDATE shows SET ended = ? WHERE ended IS NULL", (time.time(),))
        self.show_id, self.show_name, self.show_started = self._insert_show(name)
        return self.current_show()

    def current_show(self) -> dict:
        return {"id": self.show_id, "name": self.show_name, "started": self.show_started}

    def shows(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, name, started, ended FROM shows ORDER BY id DESC").fetchall()
        return [{"id": r[0], "name": r[1], "started": r[2], "ended": r[3]} for r in rows]

    # ----------------------------------------------------------------- states
    def record_state(self, entity_id: str, value: float | None, ts: float) -> None:
        self._pending.append((self.show_id, ts, entity_id, value))

    def flush(self) -> None:
        if not self._pending:
            return
        rows, self._pending = self._pending, []
        self._db.executemany(
            "INSERT INTO states (show_id, ts, entity_id, value) VALUES (?, ?, ?, ?)", rows)
        self._db.commit()

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

    def close(self) -> None:
        self.flush()
        self._db.close()
