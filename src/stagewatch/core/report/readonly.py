"""Reading a data folder for a report without touching it (used by ``stagewatch report``).

The original history file, its ``-wal`` and ``-shm`` and the config are only ever read.  The history
is copied into a temporary folder and the copy is opened, so a crash-left WAL is recovered on the copy
and never on the original.  Nothing here imports the hub.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from .query import ReportError, open_readonly

DB_NAME = "stagewatch.sqlite3"
COPY_PREFIX = "stagewatch-report-"
STALE_COPY_S = 24 * 3600


def load_config_readonly(data_dir: Path):
    """The config for time zone, names and dashboards.  Pure read: no save, no quarantine, no .bak.
    Returns (config, warning); an unreadable file gives the defaults and a warning."""
    from ..config import Config, migrate
    path = Path(data_dir) / "config.yaml"
    if not path.is_file():
        return Config(), "There is no config.yaml in this folder; default names and time zone are used."
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not raw:
            raise ValueError("empty")
        return Config.model_validate(migrate(raw)), None
    except Exception:  # noqa: BLE001 - nothing from the file is shown
        return Config(), "The config file could not be read; default names and time zone are used."


def remove_stale_copies(max_age_s: float = STALE_COPY_S, temp_root: Path | None = None) -> int:
    """Remove working copies a hard kill left behind in the temporary folder (older than a day).
    Only real folders with this command's own name prefix are touched.  Returns how many went."""
    root = Path(temp_root or tempfile.gettempdir())
    gone = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for p in entries:
        try:
            if not p.name.startswith(COPY_PREFIX) or p.is_symlink() or not p.is_dir():
                continue
            if time.time() - p.stat().st_mtime < max_age_s:
                continue
            shutil.rmtree(p, ignore_errors=True)
            gone += 1
        except OSError:
            continue
    return gone


def _need_free_space(src: Path, tmp_dir: Path) -> None:
    need = sum(f.stat().st_size for f in (src, src.with_name(src.name + "-wal"), src.with_name(src.name + "-shm"))
               if f.is_file())
    try:
        free = shutil.disk_usage(tmp_dir).free
    except OSError:
        return
    if free < need * 2 + 50 * 1024 * 1024:
        raise ReportError("no_space", "There is not enough free space on this computer to make a working copy "
                                      "of the history. Free up some space and try again.")


def _copy_files(src: Path, dst: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        f = src.with_name(src.name + suffix)
        if f.is_file():
            shutil.copyfile(f, dst.with_name(dst.name + suffix))


def prepare_copy(data_dir: Path, tmp_dir: Path, running: bool) -> Path:
    """Copy the history from ``data_dir`` into ``tmp_dir`` and check the copy.  Never writes to the
    originals.  Raises ReportError no_database / damaged."""
    src = Path(data_dir) / DB_NAME
    if not src.is_file():
        raise ReportError("no_database", "There is no history file (stagewatch.sqlite3) in that folder.")
    dst = Path(tmp_dir) / DB_NAME
    _need_free_space(src, Path(tmp_dir))
    try:
        if running:
            # A running Stagewatch holds the file: take a consistent online copy.
            ro = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
            try:
                out = sqlite3.connect(str(dst))
                try:
                    ro.backup(out)
                finally:
                    out.close()
            finally:
                ro.close()
        else:
            _copy_files(src, dst)
        # Recovery of a WAL left by a crash happens here, on the copy.
        conn = sqlite3.connect(str(dst), timeout=5.0)
        try:
            ok = conn.execute("PRAGMA quick_check").fetchone()
            if not ok or ok[0] != "ok":
                raise ReportError("damaged", "The history file is damaged.")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            conn.execute("PRAGMA journal_mode = DELETE").fetchone()
        finally:
            conn.close()
    except ReportError:
        raise
    except (sqlite3.Error, OSError):
        raise ReportError("damaged", "The history file is damaged.") from None
    return dst


@contextmanager
def open_history_copy(data_dir: Path, running: bool = False, deadline_s: float | None = 30.0):
    """Yield a read-only connection to a temporary copy of the history; the copy is removed after."""
    tmp = Path(tempfile.mkdtemp(prefix=COPY_PREFIX))
    conn = None
    try:
        conn = open_readonly(prepare_copy(data_dir, tmp, running), deadline_s)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(tmp, ignore_errors=True)
