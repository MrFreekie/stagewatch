"""Data backup and restore for updates and rollbacks (stdlib only; amendment F).

Layout:
- Backup files:  ``<data>/backups/<id>/``   (config.yaml, secret.key, contacts/, branding/, DB)
- Manifest:      ``<repo>/.git/stagewatch/backups/<id>.json``  (admin-only; holds the sha256s
  the launcher verifies before restoring, so a tampered backup in the data dir is refused)
- Displaced:     ``<data>/backups/displaced-<ts>/``  (what a restore moved out of the way;
  never deleted automatically, never counted against retention)

Restore must only run with the server stopped.  Data written between the backup and the
shutdown for the update is lost on restore by design (it is set aside in displaced-<ts>/).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path, PurePosixPath

from .updater_common import (UpdaterError, atomic_write_json, backup_manifest_path, dir_size,
                             is_valid_backup_id, read_json)

log = logging.getLogger(__name__)

DB_NAME = "stagewatch.sqlite3"
FILES = ("config.yaml", "secret.key")
DIRS = ("contacts", "branding")
RETENTION = 10
BACKUPS_DIRNAME = "backups"


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def make_backup_id(from_sha: str = "", to_sha: str = "") -> str:
    parts = [utc_stamp()]
    for s in (from_sha, to_sha):
        if s:
            parts.append(s[:7])
    return "-".join(parts)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backups_root(data_dir: Path) -> Path:
    return Path(data_dir) / BACKUPS_DIRNAME


def backup_db(src_path: Path, dst_path: Path) -> None:
    """Consistent snapshot of a (possibly WAL, possibly being written) SQLite DB.

    Uses the online backup API with the default ``pages=-1`` (single step: one consistent
    snapshot, no checkpoint needed) into a *fresh* file, then converts the copy to a
    rollback-journal (non-WAL) database and runs ``PRAGMA integrity_check``.
    """
    if dst_path.exists():
        raise UpdaterError("backup_failed", "destination exists")
    src = sqlite3.connect(str(src_path), timeout=30)
    try:
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst)  # pages=-1 default
            mode = dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise UpdaterError("backup_failed", f"could not leave WAL mode ({mode})")
            res = dst.execute("PRAGMA integrity_check").fetchall()
            if res != [("ok",)]:
                raise UpdaterError("backup_failed", f"integrity_check: {res[:3]}")
        finally:
            dst.close()
    finally:
        src.close()
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(str(dst_path) + suffix)
        except FileNotFoundError:
            pass


def _iter_dir_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def create_backup(data_dir: Path, state_dir: Path, backup_id: str | None = None, *,
                  from_sha: str = "", to_sha: str = "", keep: int = RETENTION) -> dict:
    """Back up the data dir; write the sha256 manifest into the admin-only state dir."""
    data_dir, state_dir = Path(data_dir), Path(state_dir)
    backup_id = backup_id or make_backup_id(from_sha, to_sha)
    manifest_file = backup_manifest_path(state_dir, backup_id)  # validates the id
    dest = backups_root(data_dir) / backup_id
    if dest.exists() or manifest_file.exists():
        raise UpdaterError("backup_failed", "backup id already exists")
    dest.mkdir(parents=True)
    try:
        files: dict[str, str] = {}
        for name in FILES:
            src = data_dir / name
            if src.is_file():
                shutil.copyfile(src, dest / name)
                files[name] = sha256_file(dest / name)
        for dname in DIRS:
            src_dir = data_dir / dname
            if src_dir.is_dir():
                for f in _iter_dir_files(src_dir):
                    rel = PurePosixPath(dname, *f.relative_to(src_dir).parts)
                    target = dest.joinpath(*rel.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(f, target)
                    files[str(rel)] = sha256_file(target)
        db = data_dir / DB_NAME
        if db.is_file():
            backup_db(db, dest / DB_NAME)
            files[DB_NAME] = sha256_file(dest / DB_NAME)
        manifest = {"format": 1, "id": backup_id, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "from_sha": from_sha, "to_sha": to_sha, "files": files, "size": dir_size(dest)}
        atomic_write_json(manifest_file, manifest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    prune_backups(data_dir, state_dir, keep)
    return manifest


def load_manifest(state_dir: Path, backup_id: str) -> dict:
    p = backup_manifest_path(state_dir, backup_id)
    try:
        m = read_json(p)
    except (OSError, ValueError) as e:
        raise UpdaterError("backup_missing", str(e)) from e
    if not isinstance(m, dict) or m.get("format") != 1 or m.get("id") != backup_id \
            or not isinstance(m.get("files"), dict):
        raise UpdaterError("backup_invalid")
    for rel, digest in m["files"].items():
        if not _allowed_rel(rel) or not isinstance(digest, str) or len(digest) != 64:
            raise UpdaterError("backup_invalid", f"bad entry {rel!r}")
    return m


def _allowed_rel(rel) -> bool:
    if not isinstance(rel, str) or "\\" in rel or "\x00" in rel:
        return False
    parts = PurePosixPath(rel).parts
    if not parts or PurePosixPath(rel).is_absolute() or any(p in ("..", ".", "") for p in parts):
        return False
    if len(parts) == 1:
        return parts[0] in FILES or parts[0] == DB_NAME
    return parts[0] in DIRS


def list_backups(state_dir: Path) -> list[dict]:
    out = []
    d = Path(state_dir) / "backups"
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                out.append(load_manifest(state_dir, p.stem))
            except UpdaterError:
                continue
    return sorted(out, key=lambda m: (m.get("created", ""), m["id"]))


def verify_backup(data_dir: Path, state_dir: Path, backup_id: str) -> dict:
    """Check every manifest file exists in the backup dir with a matching sha256."""
    m = load_manifest(state_dir, backup_id)
    root = backups_root(data_dir) / backup_id
    for rel, digest in m["files"].items():
        f = root.joinpath(*PurePosixPath(rel).parts)
        if not f.is_file() or f.is_symlink():
            raise UpdaterError("backup_invalid", f"missing {rel}")
        if sha256_file(f) != digest:
            raise UpdaterError("backup_hash_mismatch", rel)
    return m


def prune_backups(data_dir: Path, state_dir: Path, keep: int = RETENTION) -> list[str]:
    keep = max(1, min(int(keep), RETENTION))
    ms = list_backups(state_dir)
    removed = []
    for m in ms[:-keep] if len(ms) > keep else []:
        shutil.rmtree(backups_root(data_dir) / m["id"], ignore_errors=True)
        try:
            backup_manifest_path(state_dir, m["id"]).unlink()
        except FileNotFoundError:
            pass
        removed.append(m["id"])
    return removed


def _move(src: Path, displaced: Path, rel: str) -> None:
    if src.exists():
        target = displaced / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))


def restore_backup(data_dir: Path, state_dir: Path, backup_id: str) -> Path:
    """Restore a verified backup (server must be stopped).  Returns the displaced-<ts> dir.

    Order: verify hashes first (nothing is touched if that fails), then move the current
    DB *and its -wal/-shm*, replaced files and replaced dirs into displaced-<ts>/, then copy
    the backup in.  Things absent from the backup are left alone.
    """
    data_dir = Path(data_dir)
    m = verify_backup(data_dir, state_dir, backup_id)
    root = backups_root(data_dir) / backup_id
    displaced = backups_root(data_dir) / f"displaced-{utc_stamp()}"
    n = 1
    while displaced.exists():
        n += 1
        displaced = backups_root(data_dir) / f"displaced-{utc_stamp()}-{n}"
    displaced.mkdir(parents=True)

    top = {PurePosixPath(rel).parts[0] for rel in m["files"]}
    if DB_NAME in top:
        for suffix in ("", "-wal", "-shm"):
            _move(data_dir / (DB_NAME + suffix), displaced, DB_NAME + suffix)
    for name in FILES:
        if name in top:
            _move(data_dir / name, displaced, name)
    for dname in DIRS:
        if dname in top:
            _move(data_dir / dname, displaced, dname)

    for rel, digest in m["files"].items():
        parts = PurePosixPath(rel).parts
        dst = data_dir.joinpath(*parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root.joinpath(*parts), dst)
        if sha256_file(dst) != digest:
            raise UpdaterError("restore_failed", f"copy of {rel} does not match")
    log.info("Restored backup %s (previous data set aside in %s)", backup_id, displaced.name)
    return displaced
