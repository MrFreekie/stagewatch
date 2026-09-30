"""Server side of the in-app updater (plans/in-app-updater.md; part 2 of 0.2.0).

What this module does and does not do
- It CHECKS for updates (git fetch + channel resolution + integrity rules), and on an admin's
  request writes ``pending.json`` (admin-only state dir) and asks the server to exit with code 75.
  The launcher (``stagewatch.launcher``) does the actual checkout / uv sync / health check /
  rollback, and re-validates everything itself.  This module never touches the working tree.
- It is only active on a *managed, supervised* install: a valid ``.git/stagewatch-managed.json``
  written by the installer AND a server started by the launcher.  Anything else (dev checkout,
  manual run) gets read-only version info and ``not_managed`` (HTTP 409) on every mutation.
- Errors reach the UI only as short categories + fixed messages (:data:`MESSAGES`); git/uv
  stderr (``UpdaterError.detail``) is logged only.  Markers and alarm_log entries contain
  versions only.

Channel choice (documented decision): the managed marker is written only by the installer and
stays untouched (the web process must not be able to rewrite an admin-only trust anchor).  The
channel an admin picks in the UI is stored as a *request* in ``config.yaml`` (``updater.channel``);
the effective channel is that value, else the marker's.  Every check resolves the target with
the fixed rules of amendment E, and the launcher independently re-checks the target's ancestry
(descendant of HEAD and reachable from origin/main) before applying, so a forged config value
can at worst choose between the two legitimate targets.

Blocking work (git, backup, disk walks) runs in ``asyncio.to_thread``; one job at a time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .. import updater_common as uc
from .. import backup as backup_mod
from ..updater_common import UpdaterError
from ..version import build_info

log = logging.getLogger(__name__)

CHECK_MIN_INTERVAL_S = 30.0
CHANGELOG_MAX_CHARS = 4000
NIGHTLY_COMMITS = 30
SHUTDOWN_DELAY_S = 1.0  # let the HTTP response reach the browser before uvicorn stops

NOT_MANAGED_MESSAGE = "In-app updates are only available on a managed install."
UNREACHABLE_MESSAGE = "Update source not reachable (repository is private or offline)."

MESSAGES = {
    "not_managed": NOT_MANAGED_MESSAGE,
    "unreachable": UNREACHABLE_MESSAGE,
    "busy": "Another software job is running.",
    "restarting": "Stagewatch is already restarting for an update.",
    "rate_limited": "Please wait a little before checking again.",
    "fetch_rejected": "The update source rejected the fetch (a release tag may have changed); refusing to update.",
    "dirty_tree": "This install has local changes; refusing to update.",
    "origin_mismatch": "The update source does not match the installed origin.",
    "not_detached": "This install is not on a detached release checkout.",
    "not_ancestor": "The update target is not part of the main branch history; refusing to update.",
    "unknown_commit": "The target commit is not available locally; check again.",
    "dependency_sources": "The target adds dependency sources; update manually.",
    "python_changed": "The target changes the Python requirement; update manually.",
    "lock_missing": "The target has no usable dependency lock file.",
    "target_invalid": "The target could not be read; check again later.",
    "target_mismatch": "The target does not match the last check; check again.",
    "no_check": "Check for updates first.",
    "stale_check": "The installed version changed since the last check; check again.",
    "no_update": "No update is available.",
    "disk_space": "Not enough free disk space for a data backup.",
    "backup_failed": "Data backup failed; the update was not started.",
    "backup_missing": "The backup for that update is missing or invalid.",
    "backup_invalid": "The backup for that update is missing or invalid.",
    "backup_hash_mismatch": "The backup for that update failed verification.",
    "history_not_found": "No such update in the history.",
    "rollback_unavailable": "That update cannot be rolled back.",
    "bad_channel": "Unknown update channel.",
    "bad_sha": "Invalid commit id.",
    "pin_required": "Admin PIN required.",
    "git_timeout": "A git operation timed out.",
    "git_error": "A git operation failed (see the server log).",
    "git_missing": "Git could not be run (see the server log).",
    "ancestry_error": "Git could not verify the target history (see the server log).",
    "pending_failed": "Could not schedule the update (see the server log).",
}


def message_for(category: str) -> str:
    return MESSAGES.get(category, f"Update failed ({category}).")


class RateLimited(UpdaterError):
    def __init__(self, retry_after: float):
        super().__init__("rate_limited")
        self.retry_after = max(1, int(retry_after + 0.999))


# ---------------------------------------------------------------------------
# Pure parsing helpers
# ---------------------------------------------------------------------------

def parse_schema_versions(version_py: str | None) -> tuple[int | None, int | None]:
    """(CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION) from the text of version.py (None if absent)."""
    def grab(name: str) -> int | None:
        m = re.search(rf"^{name}\s*=\s*(\d+)\s*(?:#.*)?$", version_py or "", re.M)
        return int(m.group(1)) if m else None
    return grab("CONFIG_SCHEMA_VERSION"), grab("DB_SCHEMA_VERSION")


def schema_changed(current: tuple, target: tuple) -> bool:
    """Conservative: any difference, including 'cannot tell' on one side, counts as changed."""
    return tuple(current) != tuple(target)


def _semver(text: str) -> tuple[int, int, int] | None:
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def changelog_excerpt(text: str | None, current_version: str | None, max_chars: int = CHANGELOG_MAX_CHARS) -> str:
    """Plain-text excerpt of a Keep-a-Changelog file: a non-empty [Unreleased] section plus every
    release newer than ``current_version``.  Never interpreted as HTML/Markdown by the UI."""
    if not text:
        return ""
    current = _semver(current_version or "")
    parts = re.split(r"^(?=##\s+\[)", text, flags=re.M)
    out = []
    for part in parts:
        m = re.match(r"^##\s+\[([^\]]+)\][^\n]*", part)
        if not m:
            continue
        name = m.group(1)
        body = "\n".join(line for line in part[m.end():].splitlines()
                         if not re.match(r"^\[[^\]]+\]:\s", line)).strip()
        if name.lower() == "unreleased":
            if not body:
                continue
        else:
            ver = _semver(name)
            if ver is None or (current is not None and ver <= current):
                continue
        out.append(f"{m.group(0).lstrip('# ').strip()}\n{body}".rstrip())
    result = "\n\n".join(out)
    if len(result) > max_chars:
        result = result[:max_chars].rstrip() + "\n... (truncated)"
    return result


_PYPI = "https://pypi.org/simple"
_REGISTRY_FILE_HOSTS = {"files.pythonhosted.org", "pypi.org"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def dependency_source_findings(pyproject_text: str | None, lock_text: str | None) -> set[str]:
    """Non-default dependency *sources* declared by a pyproject.toml / uv.lock pair.

    Amendment E: a target that adds any is refused in phase 1 (the launcher would resolve them
    with ``uv sync --frozen`` from wherever the lock points).  Raises ValueError on unparsable TOML.
    """
    found: set[str] = set()
    try:
        py = tomllib.loads(pyproject_text or "")
        lock = tomllib.loads(lock_text or "")
    except tomllib.TOMLDecodeError as e:
        raise ValueError(str(e)) from e
    uv = (py.get("tool") or {}).get("uv") or {}
    if not isinstance(uv, dict):
        raise ValueError("tool.uv is not a table")
    for name in (uv.get("sources") or {}):
        found.add(f"pyproject:sources:{name}")
    for i, idx in enumerate(uv.get("index") or []):
        found.add(f"pyproject:index:{idx.get('name') or idx.get('url') or i}" if isinstance(idx, dict)
                  else f"pyproject:index:{i}")
    for key in ("index-url", "extra-index-url", "find-links"):
        if uv.get(key):
            found.add(f"pyproject:{key}")
    project = _norm((py.get("project") or {}).get("name") or "")
    for pkg in lock.get("package") or []:
        if not isinstance(pkg, dict):
            continue
        src = pkg.get("source") or {}
        name = pkg.get("name", "?")
        for kind in ("git", "url", "path", "directory", "editable", "virtual"):
            if kind in src:
                # uv records the project itself as editable/virtual "."; nothing else may be
                if kind in ("editable", "virtual") and src[kind] == "." and _norm(name) == project:
                    continue
                found.add(f"lock:{kind}:{name}")
        reg = src.get("registry")
        if reg is not None and str(reg).rstrip("/") != _PYPI:
            found.add(f"lock:registry:{name}")
        elif reg is not None:
            urls = [w.get("url") for w in pkg.get("wheels") or [] if isinstance(w, dict)]
            sdist = pkg.get("sdist")
            if isinstance(sdist, dict):
                urls.append(sdist.get("url"))
            for u in urls:
                if u is not None and (urlsplit(str(u)).hostname or "").lower() not in _REGISTRY_FILE_HOSTS:
                    found.add(f"lock:url_host:{name}")
                    break
    return found


def requires_python(pyproject_text: str | None) -> str | None:
    try:
        return (tomllib.loads(pyproject_text or "").get("project") or {}).get("requires-python")
    except tomllib.TOMLDecodeError:
        return None


# ---------------------------------------------------------------------------
# Synchronous git-side work (run in a thread)
# ---------------------------------------------------------------------------

def _describe_version(ctx: uc.GitContext, sha: str) -> str | None:
    try:
        return uc.version_at(ctx, sha)
    except UpdaterError:
        return None


def _schemas_at(ctx: uc.GitContext, sha: str) -> tuple[int | None, int | None]:
    return parse_schema_versions(uc.show_file(ctx, sha, "src/stagewatch/version.py"))


def _preflight(ctx: uc.GitContext, marker: uc.Marker) -> str:
    uc.check_origin(ctx, marker.origin_url)
    if not uc.is_detached(ctx):
        raise UpdaterError("not_detached")
    uc.require_clean(ctx)
    return uc.head_sha(ctx)


def _moved_tag(ctx: uc.GitContext) -> str | None:
    """Name of a strict vX.Y.Z tag whose local object differs from the remote's (or None)."""
    try:
        _, out = uc.run_git(ctx, "ls-remote", "--tags", "origin", "refs/tags/v*")
        for line in out.splitlines():
            sha, _, ref = line.partition("\t")
            name = ref.strip().removeprefix("refs/tags/")
            if ref.endswith("^{}") or not uc.TAG_RE.match(name):
                continue
            _, local = uc.run_git(ctx, "rev-parse", "--verify", "--quiet", f"refs/tags/{name}", ok=(0, 1))
            if local.strip() and local.strip() != sha.strip():
                return name
    except UpdaterError:
        pass
    return None


def run_check(ctx: uc.GitContext, marker: uc.Marker, channel: str) -> dict:
    """Fetch, resolve the channel's target and apply the integrity rules.  Raises UpdaterError.

    Returns a dict; ``available`` False means "nothing to do" (up to date / nothing published).
    """
    if channel not in uc.CHANNELS:
        raise UpdaterError("bad_channel")
    head = _preflight(ctx, marker)
    try:
        uc.fetch_updates(ctx)
    except UpdaterError as e:
        # fetch runs --quiet, so its stderr can't tell "offline" from "a tag moved upstream".
        # If the remote still answers, the fetch was *rejected* (e.g. a moved tag): a tamper signal.
        try:
            uc.run_git(ctx, "ls-remote", "--heads", "origin", "main")
        except UpdaterError:
            raise UpdaterError("unreachable", e.detail) from e
        tag = _moved_tag(ctx)
        log.error("Update fetch rejected%s: a release tag may have been re-created upstream. "
                  "See README > Updating for recovery. git said: %s", f" (tag {tag})" if tag else "", e.detail)
        err = UpdaterError("fetch_rejected", e.detail)
        err.tag = tag
        raise err from e
    current_version = _describe_version(ctx, head)
    result = {"channel": channel, "from_sha": head, "from_version": current_version, "available": False,
              "target_sha": None, "target_version": None, "changelog": "", "commits": [],
              "schema_changed": False}
    target = uc.resolve_channel_target(ctx, channel)
    if target is None and channel == "nightly":
        # A nightly branch that exists but is not part of main's history is not "up to date":
        # someone moved it off main (CI never does).  Say so instead of hiding it.
        nightly = uc.resolve_ref(ctx, "refs/remotes/origin/nightly")
        if nightly is not None and uc.resolve_ref(ctx, "refs/remotes/origin/main") is not None:
            raise UpdaterError("not_ancestor", "origin/nightly is not an ancestor of origin/main")
    if target is None or target == head:
        return result
    try:
        uc.check_update_target(ctx, head, target)
    except UpdaterError as e:
        if e.category in ("not_newer", "downgrade"):
            return result  # e.g. running something newer than the channel offers: not an error
        raise

    target_py = uc.show_file(ctx, target, "pyproject.toml")
    target_lock = uc.show_file(ctx, target, "uv.lock")
    if target_py is None or target_lock is None:
        raise UpdaterError("lock_missing")
    cur_py = uc.show_file(ctx, head, "pyproject.toml")
    cur_lock = uc.show_file(ctx, head, "uv.lock")
    try:
        added = dependency_source_findings(target_py, target_lock) - dependency_source_findings(cur_py, cur_lock)
    except ValueError as e:
        raise UpdaterError("target_invalid", str(e)) from e
    if added:
        raise UpdaterError("dependency_sources", ", ".join(sorted(added)))
    if requires_python(cur_py) != requires_python(target_py):
        raise UpdaterError("python_changed")

    cur_schema, tgt_schema = _schemas_at(ctx, head), _schemas_at(ctx, target)
    result.update({
        "available": True, "target_sha": target, "target_version": _describe_version(ctx, target),
        "changelog": changelog_excerpt(uc.show_file(ctx, target, "CHANGELOG.md"), current_version),
        "schema_changed": schema_changed(cur_schema, tgt_schema),
        "config_schema": [cur_schema[0], tgt_schema[0]], "db_schema": [cur_schema[1], tgt_schema[1]],
    })
    if channel == "nightly":
        out = uc.git_out(ctx, "log", f"--format=%s", "-n", str(NIGHTLY_COMMITS), f"{head}..{target}")
        result["commits"] = [line[:200] for line in out.splitlines() if line.strip()]
    return result


def prepare_update(ctx: uc.GitContext, marker: uc.Marker, last: dict, data_dir: Path) -> dict:
    """Re-verify the checked target right before scheduling it.  Returns the facts to record."""
    head = _preflight(ctx, marker)
    if head != last["from_sha"]:
        raise UpdaterError("stale_check")
    to = last["target_sha"]
    uc.check_update_target(ctx, head, to)  # commit exists, strict descendant, reachable from main
    changed = schema_changed(_schemas_at(ctx, head), _schemas_at(ctx, to))
    if changed:
        db = Path(data_dir) / backup_mod.DB_NAME
        need = 2 * (db.stat().st_size if db.is_file() else 0)
        try:
            if need and shutil.disk_usage(data_dir).free < need:
                raise UpdaterError("disk_space")
        except OSError:
            pass
    return {"from_sha": head, "to_sha": to, "schema_changed": changed,
            "from_version": _describe_version(ctx, head), "to_version": _describe_version(ctx, to)}


def prepare_rollback(ctx: uc.GitContext, marker: uc.Marker, entry: dict, data_dir: Path) -> dict:
    head = _preflight(ctx, marker)
    if entry.get("result") != "ok" or entry.get("action") != "update" or entry.get("to_sha") != head \
            or not uc.is_valid_sha(entry.get("from_sha")) or not uc.commit_exists(ctx, entry["from_sha"]):
        raise UpdaterError("rollback_unavailable")
    changed = bool(entry.get("schema_changed"))
    if changed:
        if not entry.get("backup_id"):
            raise UpdaterError("rollback_unavailable")
        backup_mod.verify_backup(data_dir, marker.state_dir, entry["backup_id"])
    return {"from_sha": head, "to_sha": entry["from_sha"], "schema_changed": changed,
            "backup_id": entry.get("backup_id") if changed else None,
            "from_version": _describe_version(ctx, head), "to_version": _describe_version(ctx, entry["from_sha"])}


def make_backup(data_dir: Path, marker: uc.Marker, from_sha: str, to_sha: str) -> str:
    try:
        m = backup_mod.create_backup(data_dir, marker.state_dir, from_sha=from_sha, to_sha=to_sha)
    except UpdaterError:
        raise
    except (OSError, sqlite3.Error) as e:
        raise UpdaterError("backup_failed", str(e)) from e
    return m["id"]


# ---------------------------------------------------------------------------
# The updater object
# ---------------------------------------------------------------------------

def _version_label(version: str | None, sha: str, other: str | None) -> str:
    version = version or "unknown"
    return f"{version}+{sha[:7]}" if version == (other or "unknown") else version


@dataclass
class Updater:
    hub: object  # core.hub.Hub (duck-typed to keep this module import-light)
    marker: uc.Marker | None = None
    supervised: bool = False
    reason: str | None = None  # why not managed (a short code), for the UI
    git_protocols: tuple[str, ...] = ("https",)
    check_interval_s: float = CHECK_MIN_INTERVAL_S

    def __post_init__(self):
        self._lock = asyncio.Lock()
        self._job: str | None = None
        self._last_check_at: float | None = None  # monotonic
        self.last_check: dict | None = None
        self.restarting = False

    # ---- detection ----
    @classmethod
    def detect(cls, hub, env=None, repo: Path | None = None, **kw) -> "Updater":
        env = os.environ if env is None else env
        repo = repo or Path(__file__).resolve().parents[3]
        marker_path = repo / ".git" / uc.MARKER_NAME
        if not marker_path.is_file():
            return cls(hub, None, False, "no_marker", **kw)
        try:
            marker = uc.load_marker(marker_path)
        except UpdaterError as e:
            log.warning("Managed marker is invalid: %s", e)
            return cls(hub, None, False, "marker_invalid", **kw)
        # Emulate mode runs in <data_dir>/emulate (see __main__.resolve_data_dir); that is still
        # this install, so updates stay available.  Backups/restores always use the real data_dir.
        if not (uc._same_path(marker.data_dir, hub.data_dir)
                or (getattr(hub, "emulate", False)
                    and uc._same_path(marker.data_dir / "emulate", hub.data_dir))):
            log.warning("Managed marker data_dir differs from the running data dir; updates disabled")
            return cls(hub, None, False, "data_dir_mismatch", **kw)
        supervised = env.get(uc.ENV_SUPERVISED) == "1" and bool(env.get(uc.ENV_STATE_DIR)) \
            and uc._same_path(Path(env[uc.ENV_STATE_DIR]), marker.state_dir)
        return cls(hub, marker, supervised, None if supervised else "not_supervised", **kw)

    # ---- properties ----
    @property
    def data_dir(self) -> Path:
        """Folder that backups/restores act on: the marker's real data folder (the launcher
        restores there), never the ``emulate`` subfolder a simulated run uses.  Backups copy
        an explicit list of files, so ``emulate/`` is never included."""
        return Path(self.marker.data_dir) if self.marker else Path(self.hub.data_dir)

    @property
    def managed(self) -> bool:
        return self.marker is not None

    @property
    def mutable(self) -> bool:
        return self.marker is not None and self.supervised

    def effective_channel(self) -> str:
        chosen = self.hub.config.updater.channel
        if chosen in uc.CHANNELS:
            return chosen
        return self.marker.channel if self.marker else "stable"

    def _ctx(self) -> uc.GitContext:
        assert self.marker is not None
        return uc.GitContext(self.marker.repo_path, str(self.marker.git_path), uc.ensure_state_dir(self.marker.repo_path),
                             self.git_protocols)

    def _require_mutable(self) -> None:
        if not self.mutable:
            raise UpdaterError("not_managed")
        if self.restarting:
            raise UpdaterError("restarting")

    # ---- status ----
    def _head_sha_cheap(self) -> str | None:
        return uc.read_head_file(self.marker.repo_path) if self.marker else None

    def _history_view(self, head: str | None) -> list[dict]:
        if not self.marker:
            return []
        out = []
        for e in reversed(uc.load_history(self.marker.state_dir)):
            out.append({
                "id": e.get("id"), "ts": e.get("ts"), "action": e.get("action"), "result": e.get("result"),
                "reason": e.get("reason") or "", "channel": e.get("channel"),
                "from_version": e.get("from_version"), "to_version": e.get("to_version"),
                "schema_changed": bool(e.get("schema_changed")),
                "restored_backup": bool(e.get("restored_backup")), "data_returned": bool(e.get("data_returned")),
                "can_rollback": bool(head and e.get("action") == "update" and e.get("result") == "ok"
                                     and e.get("to_sha") == head and isinstance(e.get("id"), int)),
            })
        return out

    def _disk_view(self) -> tuple[list[dict], list[dict]]:
        data_dir = self.data_dir
        backups = []
        if self.marker:
            for m in backup_mod.list_backups(self.marker.state_dir):
                backups.append({"id": m["id"], "created": m.get("created"), "size": m.get("size", 0),
                                "from_sha": m.get("from_sha", ""), "to_sha": m.get("to_sha", "")})
        displaced = []
        root = backup_mod.backups_root(data_dir)
        if root.is_dir():
            for p in sorted(root.glob("displaced-*")):
                if p.is_dir():
                    displaced.append({"name": p.name, "size": uc.dir_size(p)})
        return backups, displaced

    async def status(self) -> dict:
        info = build_info()
        head = self._head_sha_cheap()
        backups, displaced = await asyncio.to_thread(self._disk_view)
        last = self.last_check
        return {
            "managed": self.managed, "supervised": self.supervised, "mutable": self.mutable,
            "reason": None if self.mutable else self.reason or "not_managed",
            "message": None if self.mutable else NOT_MANAGED_MESSAGE,
            "version": info["version"], "describe": info["describe"], "commit": info["commit"],
            "commit_full": head or None, "dirty": info["dirty"],
            "channel": self.effective_channel(), "channels": list(uc.CHANNELS),
            "job": {"running": self._job is not None, "kind": self._job},
            "restarting": self.restarting,
            "check_min_interval_s": self.check_interval_s,
            "last_check": last,
            "update_available": bool(last and last.get("ok") and last.get("available")),
            "history": self._history_view(head), "backups": backups, "displaced": displaced,
        }

    # ---- job plumbing ----
    class _Job:
        def __init__(self, owner: "Updater", kind: str):
            self.owner, self.kind = owner, kind

        async def __aenter__(self):
            if self.owner._lock.locked():
                raise UpdaterError("busy")
            await self.owner._lock.acquire()
            self.owner._job = self.kind

        async def __aexit__(self, *exc):
            self.owner._job = None
            self.owner._lock.release()

    def _job_ctx(self, kind: str) -> "_Job":
        return Updater._Job(self, kind)

    # ---- check ----
    async def check(self) -> dict:
        """Returns the check result dict (also kept as ``last_check``).  Failures of the check
        itself (offline, private repo, refused target...) are results with ``ok: False`` and a
        category; only 'not managed', 'busy' and 'rate limited' raise."""
        self._require_mutable()
        async with self._job_ctx("check"):
            now = time.monotonic()
            if self._last_check_at is not None and now - self._last_check_at < self.check_interval_s:
                raise RateLimited(self.check_interval_s - (now - self._last_check_at))
            self._last_check_at = now
            channel = self.effective_channel()
            base = {"ts": time.time(), "channel": channel}
            try:
                res = await asyncio.to_thread(run_check, self._ctx(), self.marker, channel)
                result = {**base, "ok": True, "category": None, "message": None, **res}
            except UpdaterError as e:
                log.warning("Update check failed: %s (%s)", e.category, e.detail)
                message = message_for(e.category)
                tag = getattr(e, "tag", None)
                if e.category == "fetch_rejected" and tag:  # strict vX.Y.Z only: safe to show
                    message += f" Changed tag: {tag}. An administrator must review it (see README, Updating)."
                result = {**base, "ok": False, "category": e.category, "message": message,
                          "available": False}
            except Exception:  # never crash the hub
                log.exception("Update check crashed")
                result = {**base, "ok": False, "category": "git_error", "message": message_for("git_error"),
                          "available": False}
            self.last_check = result
            return result

    # ---- channel ----
    async def set_channel(self, channel: str) -> str:
        self._require_mutable()
        if channel not in uc.CHANNELS:
            raise UpdaterError("bad_channel")
        self.hub.config.updater.channel = channel
        self.hub.save_config()
        self.last_check = None  # a result for the other channel must never be applied
        return channel

    # ---- update / rollback ----
    def _announce_start(self, kind: str, from_label: str, to_label: str) -> None:
        text = f"{from_label} → {to_label}"
        self.hub.recorder.log_alarm("software", kind, 0, text)
        self.hub.add_marker(f"{'Updating' if kind == 'update' else 'Rolling back'} software {text}", "updater")

    def _shutdown_for_apply(self) -> None:
        self.restarting = True
        self.hub.exit_code = uc.EXIT_APPLY
        request = self.hub.request_shutdown
        if request is None:
            return
        try:
            asyncio.get_running_loop().call_later(SHUTDOWN_DELAY_S, request)
        except RuntimeError:
            request()

    async def update(self, channel: str, target_sha: str) -> dict:
        self._require_mutable()
        async with self._job_ctx("update"):
            last = self.last_check
            if not last or not last.get("ok"):
                raise UpdaterError("no_check")
            if not last.get("available"):
                raise UpdaterError("no_update")
            if channel != last["channel"] or channel != self.effective_channel() \
                    or not uc.is_valid_sha(target_sha) or target_sha != last["target_sha"]:
                raise UpdaterError("target_mismatch")
            try:
                facts = await asyncio.to_thread(prepare_update, self._ctx(), self.marker, last, self.data_dir)
                backup_id = None
                if facts["schema_changed"]:
                    self.hub.recorder.flush()  # everything recorded so far is in the DB we back up
                    backup_id = await asyncio.to_thread(make_backup, self.data_dir, self.marker,
                                                        facts["from_sha"], facts["to_sha"])
                await asyncio.to_thread(uc.write_pending, self.marker.state_dir, {
                    "format": 1, "action": "update", "from_sha": facts["from_sha"], "to_sha": facts["to_sha"],
                    "backup_id": backup_id, "schema_changed": facts["schema_changed"], "channel": channel})
            except UpdaterError as e:
                log.warning("Update refused: %s (%s)", e.category, e.detail)
                raise
            except OSError as e:
                log.error("Could not write pending update: %s", e)
                raise UpdaterError("pending_failed", str(e)) from e
            fv, tv = facts["from_version"], facts["to_version"]
            self._announce_start("update", _version_label(fv, facts["from_sha"], tv),
                                 _version_label(tv, facts["to_sha"], fv))
            log.info("Update %s -> %s scheduled; restarting for the launcher", fv, tv)
            self._shutdown_for_apply()
            return {"ok": True, "restarting": True, "from_version": fv, "to_version": tv,
                    "backup": bool(backup_id)}

    async def rollback(self, history_id: int) -> dict:
        self._require_mutable()
        async with self._job_ctx("rollback"):
            entry = uc.find_history(self.marker.state_dir, history_id)
            if entry is None:
                raise UpdaterError("history_not_found")
            try:
                facts = await asyncio.to_thread(prepare_rollback, self._ctx(), self.marker, entry, self.data_dir)
                if facts["schema_changed"]:
                    self.hub.recorder.flush()
                await asyncio.to_thread(uc.write_pending, self.marker.state_dir, {
                    "format": 1, "action": "rollback", "from_sha": facts["from_sha"], "to_sha": facts["to_sha"],
                    "backup_id": facts["backup_id"], "schema_changed": facts["schema_changed"],
                    "channel": self.effective_channel(), "history_id": history_id})
            except UpdaterError as e:
                log.warning("Rollback refused: %s (%s)", e.category, e.detail)
                raise
            except OSError as e:
                log.error("Could not write pending rollback: %s", e)
                raise UpdaterError("pending_failed", str(e)) from e
            fv, tv = facts["from_version"], facts["to_version"]
            self._announce_start("rollback", _version_label(fv, facts["from_sha"], tv),
                                 _version_label(tv, facts["to_sha"], fv))
            self._shutdown_for_apply()
            return {"ok": True, "restarting": True, "from_version": fv, "to_version": tv}

    # ---- result of the previous run's update/rollback (called once at server start) ----
    def announce_last_result(self) -> None:
        """Log the launcher's newest history entry (once) as an alarm_log event + marker."""
        if not self.marker:
            return
        sd = self.marker.state_dir
        seen_path = sd / "announced.json"
        try:
            seen = uc.read_json(seen_path).get("id", -1)
        except (OSError, ValueError, AttributeError):
            seen = -1
        hist = [e for e in uc.load_history(sd) if isinstance(e.get("id"), int) and e["id"] > seen]
        if not hist:
            return
        for e in hist:
            self._announce_entry(e)
        try:
            uc.atomic_write_json(seen_path, {"id": max(e["id"] for e in hist)})
        except OSError:
            log.exception("could not record the announced update result")

    async def watch_results(self, interval: float = 5.0) -> None:
        """Run for the life of the server: the launcher records an update's result only after its
        health check (well after this server started), so poll the history file for new entries."""
        while True:
            try:
                self.announce_last_result()
            except Exception:
                log.exception("could not announce the last update result")
            await asyncio.sleep(interval)

    def _announce_entry(self, e: dict) -> None:
        fv, tv = e.get("from_version") or "unknown", e.get("to_version") or "unknown"
        result, action = e.get("result"), e.get("action")
        reason = str(e.get("reason") or "")[:60]
        rec = self.hub.recorder
        if result == "ok" and action == "update":
            rec.log_alarm("software", "updated", 0, f"{fv} → {tv}")
            self.hub.add_marker(f"Software updated to {tv}", "updater")
        elif result == "ok" and action == "rollback":
            rec.log_alarm("software", "rolled_back", 0, f"{fv} → {tv}")
            self.hub.add_marker(f"Rolled back to {tv}", "updater")
        elif result == "rolled_back":
            rec.log_alarm("software", "update_failed", 1, f"{reason}; still on {fv}")
            self.hub.add_marker(f"Software {action or 'update'} failed; running {fv}", "updater")
        elif result in ("failed", "refused", "rejected"):
            rec.log_alarm("software", f"update_{result}", 1 if result == "failed" else 0, reason)
            if result == "failed":
                self.hub.add_marker("Software update failed; check the launcher log", "updater")
            else:
                # Close the "Updating software X -> Y" marker: versions + category only.
                what = "rollback" if action == "rollback" else "update"
                span = f" ({fv} → {tv})" if fv != "unknown" or tv != "unknown" else ""
                self.hub.add_marker(f"Software {what} {result}{span}: {reason or 'no reason recorded'}; "
                                    f"still running the previous version", "updater")
