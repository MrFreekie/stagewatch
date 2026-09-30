"""Shared, stdlib-only helpers for the launcher and the in-app updater.

IMPORTANT: this module (like ``launcher.py`` and ``backup.py``) must import
nothing third-party: the launcher runs before, and independently of, the
server's dependencies (tests/test_launcher.py enforces this).

Trust model (see plans/in-app-updater.md, amendments C/D):
- Everything the launcher acts on lives in the admin-only state directory
  ``<repo>/.git/stagewatch/`` (pending.json, history.json, backups/<id>.json,
  handshake.json, an empty gitconfig).  Never in the data directory.
- The managed marker ``<repo>/.git/stagewatch-managed.json`` records absolute
  tool paths and the uv environment, written only by the installer.
- Every git call goes through :class:`GitContext` with hardened flags/env.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

EXIT_APPLY = 75  # server exit code meaning "launcher: apply pending update/rollback"
DEFAULT_ORIGIN = "https://github.com/MrFreekie/stagewatch.git"
CHANNELS = ("stable", "nightly")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BACKUP_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,79}$")
GIT_TIMEOUT = 30
HISTORY_LIMIT = 50

MARKER_NAME = "stagewatch-managed.json"
STATE_DIRNAME = "stagewatch"


class UpdaterError(Exception):
    """Failure with a short category (safe to show in a UI) and private detail (log only)."""

    def __init__(self, category: str, detail: str = ""):
        super().__init__(f"{category}: {detail}" if detail else category)
        self.category = category
        self.detail = detail


class GitError(UpdaterError):
    pass


# ---------------------------------------------------------------------------
# Atomic JSON files
# ---------------------------------------------------------------------------

REPLACE_RETRIES = 40
REPLACE_RETRY_S = 0.05


def replace_with_retry(src, dst, *, retries: int = REPLACE_RETRIES, delay: float = REPLACE_RETRY_S) -> None:
    """``os.replace`` that tolerates a reader briefly holding ``dst`` open.

    On Windows, replacing a file that another process has open (without
    FILE_SHARE_DELETE, which Python's open() never sets) fails with
    PermissionError. The server polls history.json every few seconds while the
    launcher writes it, so a collision is rare but real; retry for up to ~2 s
    instead of letting it abort an update or rollback.
    """
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == retries - 1:
                raise
            time.sleep(delay)


def fsync_dir(directory) -> None:
    """Best-effort fsync of a directory so a rename survives a power cut (POSIX only)."""
    if sys.platform == "win32":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def remove_stale_temps(directory, patterns: Iterable[str], min_age_s: float = 60.0) -> int:
    """Delete leftover temp files (from interrupted atomic writes) older than ``min_age_s``.
    Never raises; returns how many were removed."""
    removed = 0
    now = time.time()
    try:
        candidates = [p for pat in patterns for p in Path(directory).glob(pat)]
    except OSError:
        return 0
    for p in candidates:
        try:
            if p.is_file() and now - p.stat().st_mtime > min_age_s:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, path)
        fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Running-server lock (data dir): lets `reset-admin-pin` refuse while a server is up
# ---------------------------------------------------------------------------

SERVER_LOCK_NAME = "server.lock"
_LOCK_STARTUP_GRACE_S = 120.0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied: exists
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        k32.CloseHandle(ctypes.c_void_p(h))
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _port_open(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.0):
            return True
    except (OSError, ValueError):
        return False


def write_server_lock(data_dir, port: int) -> None:
    try:
        atomic_write_json(Path(data_dir) / SERVER_LOCK_NAME, {"pid": os.getpid(), "port": int(port)})
    except OSError:
        pass


def remove_server_lock(data_dir) -> None:
    """Remove the lock only if it is ours (a newer server may own it)."""
    p = Path(data_dir) / SERVER_LOCK_NAME
    try:
        if read_json(p).get("pid") == os.getpid():
            p.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def server_running(data_dir) -> bool:
    """True if a live Stagewatch server owns this data dir: the lock's PID is alive AND its
    port answers (or the lock is fresh, i.e. the server is still starting).  A stale lock left
    by a crash / reused PID reads as not running."""
    p = Path(data_dir) / SERVER_LOCK_NAME
    try:
        info = read_json(p)
        pid, port = int(info["pid"]), int(info["port"])
        age = time.time() - p.stat().st_mtime
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not _pid_alive(pid):
        return False
    return _port_open(port) or age < _LOCK_STARTUP_GRACE_S


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def is_valid_sha(value) -> bool:
    return isinstance(value, str) and bool(SHA_RE.match(value))


def is_valid_backup_id(value) -> bool:
    return isinstance(value, str) and bool(BACKUP_ID_RE.match(value)) and not value.startswith("displaced-")


# ---------------------------------------------------------------------------
# Admin-only state directory
# ---------------------------------------------------------------------------

def state_dir_for(repo: Path) -> Path:
    return Path(repo) / ".git" / STATE_DIRNAME


def ensure_state_dir(repo: Path) -> Path:
    """Create ``<repo>/.git/stagewatch/`` with an empty gitconfig (used as GIT_CONFIG_GLOBAL)."""
    d = state_dir_for(repo)
    (d / "backups").mkdir(parents=True, exist_ok=True)
    cfg = d / "gitconfig"
    if not cfg.exists():
        cfg.write_bytes(b"")
    return d


def pending_path(sd: Path) -> Path:
    return Path(sd) / "pending.json"


def history_path(sd: Path) -> Path:
    return Path(sd) / "history.json"


def handshake_path(sd: Path) -> Path:
    return Path(sd) / "handshake.json"


def backup_manifest_path(sd: Path, backup_id: str) -> Path:
    if not is_valid_backup_id(backup_id):
        raise UpdaterError("bad_backup_id")
    return Path(sd) / "backups" / f"{backup_id}.json"


# ---- pending (written by the server in part 2, validated by the launcher) ----

PENDING_ACTIONS = ("update", "rollback")


def validate_pending(p) -> dict:
    """Shape check only (no git). Returns a normalised copy or raises UpdaterError."""
    if not isinstance(p, dict) or p.get("format") != 1:
        raise UpdaterError("pending_invalid", "format")
    action = p.get("action")
    if action not in PENDING_ACTIONS:
        raise UpdaterError("pending_invalid", "action")
    for k in ("from_sha", "to_sha"):
        if not is_valid_sha(p.get(k)):
            raise UpdaterError("bad_sha", k)
    backup_id = p.get("backup_id")
    if backup_id is not None and not is_valid_backup_id(backup_id):
        raise UpdaterError("bad_backup_id")
    schema_changed = p.get("schema_changed", False)
    if not isinstance(schema_changed, bool):
        raise UpdaterError("pending_invalid", "schema_changed")
    if schema_changed and backup_id is None:
        raise UpdaterError("pending_invalid", "schema change without backup")
    channel = p.get("channel", "stable")
    if channel not in CHANNELS:
        raise UpdaterError("pending_invalid", "channel")
    out = {"format": 1, "action": action, "from_sha": p["from_sha"], "to_sha": p["to_sha"],
           "backup_id": backup_id, "schema_changed": schema_changed, "channel": channel}
    if action == "rollback":
        hid = p.get("history_id")
        if not isinstance(hid, int) or isinstance(hid, bool) or hid < 0:
            raise UpdaterError("pending_invalid", "history_id")
        out["history_id"] = hid
    return out


def write_pending(sd: Path, pending: dict) -> None:
    """Part 2 (the server, before exiting with EXIT_APPLY) calls this."""
    atomic_write_json(pending_path(sd), validate_pending(pending))


def read_pending(sd: Path) -> dict:
    """Read + shape-validate. Raises UpdaterError('no_pending') if absent."""
    p = pending_path(sd)
    if not p.exists():
        raise UpdaterError("no_pending")
    try:
        return validate_pending(read_json(p))
    except (OSError, ValueError) as e:
        raise UpdaterError("pending_invalid", str(e)) from e


def clear_pending(sd: Path, *, keep_rejected: bool = False) -> None:
    p = pending_path(sd)
    try:
        if keep_rejected:
            os.replace(p, p.with_name("pending.rejected.json"))
        else:
            p.unlink()
    except FileNotFoundError:
        pass


# ---- history ----

def load_history(sd: Path) -> list[dict]:
    p = history_path(sd)
    if not p.exists():
        return []
    try:
        data = read_json(p)
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def append_history(sd: Path, entry: dict) -> dict:
    hist = load_history(sd)
    entry = dict(entry)
    entry["id"] = max((e.get("id", -1) for e in hist if isinstance(e.get("id"), int)), default=-1) + 1
    entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    hist.append(entry)
    atomic_write_json(history_path(sd), hist[-HISTORY_LIMIT:])
    return entry


def find_history(sd: Path, history_id: int) -> dict | None:
    for e in load_history(sd):
        if e.get("id") == history_id:
            return e
    return None


# ---- handshake (child -> launcher health signal) ----

ENV_SUPERVISED = "STAGEWATCH_SUPERVISED"
ENV_STATE_DIR = "STAGEWATCH_STATE_DIR"
ENV_NONCE = "STAGEWATCH_HANDSHAKE_NONCE"


def write_handshake(sd: Path, *, version: str, commit: str, nonce: str, pid: int | None = None) -> None:
    atomic_write_json(handshake_path(sd), {
        "format": 1, "pid": pid if pid is not None else os.getpid(),
        "version": version, "commit": commit, "nonce": nonce,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def write_handshake_from_env(version: str, commit: str, env=None) -> bool:
    """Server startup hook: write the handshake only when the launcher asked for it."""
    env = os.environ if env is None else env
    sd, nonce = env.get(ENV_STATE_DIR), env.get(ENV_NONCE)
    if env.get(ENV_SUPERVISED) != "1" or not sd or not nonce:
        return False
    write_handshake(Path(sd), version=version, commit=commit, nonce=nonce)
    return True


def read_head_file(repo: Path) -> str | None:
    """HEAD commit of a detached checkout by reading .git/HEAD (no git binary needed;
    SYSTEM's PATH may not contain git)."""
    try:
        v = (Path(repo) / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v if is_valid_sha(v) else None


def read_handshake(sd: Path) -> dict | None:
    try:
        h = read_json(handshake_path(sd))
    except (OSError, ValueError):
        return None
    return h if isinstance(h, dict) and h.get("format") == 1 else None


# ---------------------------------------------------------------------------
# Managed marker
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Marker:
    path: Path
    repo_path: Path
    origin_url: str
    git_path: Path
    uv_path: Path
    uv_env: dict = field(default_factory=dict)
    data_dir: Path = Path(".")
    channel: str = "stable"
    port: int = 8080
    emulate: bool = False
    created: str = ""

    @property
    def state_dir(self) -> Path:
        return state_dir_for(self.repo_path)

    @property
    def python_path(self) -> Path:
        if sys.platform == "win32":
            return self.repo_path / ".venv" / "Scripts" / "python.exe"
        return self.repo_path / ".venv" / "bin" / "python"


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def load_marker(path: Path, *, require_https: bool = True) -> Marker:
    """Read and validate ``.git/stagewatch-managed.json``.  Raises UpdaterError('marker_invalid')."""
    path = Path(path)
    try:
        d = read_json(path)
    except (OSError, ValueError) as e:
        raise UpdaterError("marker_invalid", f"unreadable: {e}") from e

    def bad(why: str):
        raise UpdaterError("marker_invalid", why)

    if not isinstance(d, dict) or d.get("format") != 1:
        bad("format")
    paths = {}
    for k in ("repo_path", "git_path", "uv_path", "data_dir"):
        v = d.get(k)
        if not isinstance(v, str) or not os.path.isabs(v):
            bad(f"{k} must be an absolute path")
        paths[k] = Path(v)
    if path.name != MARKER_NAME or path.parent.name != ".git" \
            or not _same_path(path.parent.parent, paths["repo_path"]):
        bad("repo_path does not match marker location")
    origin = d.get("origin_url")
    if not isinstance(origin, str) or not origin or (require_https and not origin.startswith("https://")):
        bad("origin_url")
    uv_env = d.get("uv_env", {})
    if not isinstance(uv_env, dict) or not all(
            isinstance(k, str) and k.startswith("UV_") and isinstance(v, str) for k, v in uv_env.items()):
        bad("uv_env")
    channel = d.get("channel", "stable")
    if channel not in CHANNELS:
        bad("channel")
    port = d.get("port", 8080)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        bad("port")
    return Marker(path=path, repo_path=paths["repo_path"], origin_url=origin, git_path=paths["git_path"],
                  uv_path=paths["uv_path"], uv_env=dict(uv_env), data_dir=paths["data_dir"],
                  channel=channel, port=port, emulate=bool(d.get("emulate", False)),
                  created=str(d.get("created", "")))


def write_marker(path: Path, marker_fields: dict) -> None:
    """Reference writer (the installers write the same JSON themselves; used by tests)."""
    atomic_write_json(Path(path), {"format": 1, **marker_fields})


# ---------------------------------------------------------------------------
# Hardened git runner (amendment D)
# ---------------------------------------------------------------------------

_NULL_HOOKS = "NUL" if sys.platform == "win32" else "/dev/null"


@dataclass(frozen=True)
class GitContext:
    repo: Path
    git_path: str
    state_dir: Path
    # Only "https" in production. Tests use ("file",) against a local bare repo.
    allow_protocols: tuple[str, ...] = ("https",)

    @classmethod
    def from_marker(cls, m: Marker) -> "GitContext":
        return cls(m.repo_path, str(m.git_path), m.state_dir)

    def config_flags(self) -> list[str]:
        safe = str(Path(self.repo).resolve()).replace("\\", "/")
        flags = ["-c", f"safe.directory={safe}", "-c", f"core.hooksPath={_NULL_HOOKS}",
                 "-c", "core.fsmonitor=false", "-c", "credential.helper=",
                 "-c", "protocol.allow=never"]
        for proto in self.allow_protocols:
            flags += ["-c", f"protocol.{proto}.allow=always"]
        return flags

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(Path(self.state_dir) / "gitconfig"),
            "GIT_OPTIONAL_LOCKS": "0",
        })
        return env


def run_git(ctx: GitContext, *args: str, ok: Iterable[int] = (0,), timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
    """Run git; return (returncode, stdout).  Any returncode not in ``ok`` raises GitError.

    Timeouts and a missing binary raise GitError too.  stderr goes in ``.detail`` (log only).
    """
    cmd = [ctx.git_path, *ctx.config_flags(), *args]
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        cp = subprocess.run(cmd, cwd=str(ctx.repo), env=ctx.env(), stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as e:
        raise GitError("git_timeout", f"git {args[0] if args else ''} exceeded {timeout}s") from e
    except OSError as e:
        raise GitError("git_missing", str(e)) from e
    if cp.returncode not in tuple(ok):
        raise GitError("git_error", f"git {' '.join(args[:3])} exit {cp.returncode}: {cp.stderr.strip()[:500]}")
    return cp.returncode, cp.stdout


def git_out(ctx: GitContext, *args: str, **kw) -> str:
    return run_git(ctx, *args, **kw)[1].strip()


def get_origin_url(ctx: GitContext) -> str:
    return git_out(ctx, "config", "--get", "remote.origin.url")


def check_origin(ctx: GitContext, expected: str) -> None:
    try:
        actual = get_origin_url(ctx)
    except GitError as e:
        raise UpdaterError("origin_mismatch", e.detail) from e
    if actual != expected:
        raise UpdaterError("origin_mismatch", "remote.origin.url differs from the marker")


def is_dirty(ctx: GitContext) -> bool:
    return bool(git_out(ctx, "status", "--porcelain", "--untracked-files=no"))


def require_clean(ctx: GitContext) -> None:
    if is_dirty(ctx):
        raise UpdaterError("dirty_tree")


def head_sha(ctx: GitContext) -> str:
    return git_out(ctx, "rev-parse", "--verify", "HEAD^{commit}")


def is_detached(ctx: GitContext) -> bool:
    rc, _ = run_git(ctx, "symbolic-ref", "-q", "HEAD", ok=(0, 1))
    return rc == 1


def commit_exists(ctx: GitContext, sha: str) -> bool:
    if not is_valid_sha(sha):
        return False
    rc, _ = run_git(ctx, "cat-file", "-e", f"{sha}^{{commit}}", ok=(0, 1, 128))
    return rc == 0


def resolve_ref(ctx: GitContext, ref: str) -> str | None:
    """Full commit SHA for a ref (tags dereferenced), or None if it doesn't exist."""
    rc, out = run_git(ctx, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", ok=(0, 1))
    return out.strip() if rc == 0 and is_valid_sha(out.strip()) else None


def is_ancestor(ctx: GitContext, ancestor: str, descendant: str) -> bool:
    """`merge-base --is-ancestor`: 0 = yes, 1 = no, anything else is an ERROR (never 'no')."""
    try:
        rc, _ = run_git(ctx, "merge-base", "--is-ancestor", ancestor, descendant, ok=(0, 1))
    except GitError as e:
        raise GitError("ancestry_error", e.detail) from e
    return rc == 0


def checkout_detach(ctx: GitContext, sha: str) -> None:
    if not is_valid_sha(sha):
        raise UpdaterError("bad_sha")
    run_git(ctx, "checkout", "--detach", "--quiet", sha, "--", timeout=120)


def show_file(ctx: GitContext, sha: str, relpath: str) -> str | None:
    """`git show <sha>:<path>` (None if absent).  Used by part 2 to read the target's metadata."""
    if not is_valid_sha(sha):
        raise UpdaterError("bad_sha")
    rc, out = run_git(ctx, "show", f"{sha}:{relpath}", ok=(0, 128))
    return out if rc == 0 else None


def version_at(ctx: GitContext, sha: str) -> str | None:
    text = show_file(ctx, sha, "src/stagewatch/__init__.py")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text or "", re.M)
    return m.group(1) if m else None


FETCH_REFSPECS = ["+refs/heads/nightly:refs/remotes/origin/nightly",
                  "+refs/heads/main:refs/remotes/origin/main",
                  "refs/tags/v*:refs/tags/v*"]  # NO '+' on tags: a moved tag must fail the fetch


def fetch_updates(ctx: GitContext) -> None:
    """Fetch main, nightly and v* tags. A moved/deleted-and-recreated tag makes this fail."""
    try:
        run_git(ctx, "fetch", "--no-tags", "--quiet", "origin", *FETCH_REFSPECS)
    except GitError as e:
        raise GitError("fetch_failed", e.detail) from e


def latest_stable_tag(ctx: GitContext, main_ref: str = "refs/remotes/origin/main") -> tuple[str, str] | None:
    """Highest strict vX.Y.Z tag whose commit is an ancestor of origin/main -> (tag, sha)."""
    main = resolve_ref(ctx, main_ref)
    if main is None:
        return None
    best = None
    for tag in git_out(ctx, "tag", "--list", "v*").splitlines():
        m = TAG_RE.match(tag.strip())
        if not m:
            continue
        sha = resolve_ref(ctx, f"refs/tags/{tag.strip()}")
        if sha is None or not is_ancestor(ctx, sha, main):
            continue
        key = tuple(int(x) for x in m.groups())
        if best is None or key > best[0]:
            best = (key, tag.strip(), sha)
    return (best[1], best[2]) if best else None


def resolve_channel_target(ctx: GitContext, channel: str) -> str | None:
    """Target SHA for a channel per amendment E, or None if nothing is available.

    stable: latest strict tag on origin/main.  nightly: origin/nightly if it is an ancestor
    of origin/main.  Does NOT check 'newer than HEAD' (see :func:`check_update_target`).
    """
    if channel == "stable":
        t = latest_stable_tag(ctx)
        return t[1] if t else None
    if channel == "nightly":
        main = resolve_ref(ctx, "refs/remotes/origin/main")
        nightly = resolve_ref(ctx, "refs/remotes/origin/nightly")
        if main is None or nightly is None or not is_ancestor(ctx, nightly, main):
            return None
        return nightly
    raise UpdaterError("bad_channel")


def check_update_target(ctx: GitContext, from_sha: str, to_sha: str) -> None:
    """Ancestry rules for an *update* (re-run by the launcher): to_sha is a strict descendant
    of from_sha and reachable from origin/main.  Downgrades are refused (Rollback is separate)."""
    if not is_valid_sha(to_sha):
        raise UpdaterError("bad_sha")
    if not commit_exists(ctx, to_sha):
        raise UpdaterError("unknown_commit")
    if to_sha == from_sha:
        raise UpdaterError("not_newer")
    main = resolve_ref(ctx, "refs/remotes/origin/main")
    if main is None:
        raise UpdaterError("no_main_ref")
    if not is_ancestor(ctx, to_sha, main):
        raise UpdaterError("not_ancestor")
    if not is_ancestor(ctx, from_sha, to_sha):
        raise UpdaterError("downgrade")


# ---------------------------------------------------------------------------
# Permission verification: "no write access for non-admin principals"
# ---------------------------------------------------------------------------

_WRITE_FLAGS = {"F", "M", "W", "WD", "AD", "WDAC", "WO", "DC", "DE", "GA", "GW"}
_INHERIT_FLAGS = {"I", "OI", "CI", "IO", "NP"}
_SAFE_PRINCIPAL = re.compile(
    r"(^|\\)(SYSTEM|Administrators|TrustedInstaller)$|^CREATOR OWNER$"
    r"|^S-1-5-18$|^S-1-5-32-544$|^S-1-5-80-", re.I)
_ACE_RE = re.compile(r"^(?P<who>.+?):(?P<perms>(?:\([^)]*\))+)$")


def _icacls(path: str) -> str:
    """Raw `icacls <path>` output (module-level so tests can mock it)."""
    cp = subprocess.run(["icacls", path], capture_output=True, text=True, timeout=30,
                        creationflags=0x08000000, stdin=subprocess.DEVNULL)
    if cp.returncode != 0:
        raise UpdaterError("acl_unreadable", cp.stderr.strip()[:200])
    return cp.stdout


def parse_icacls(output: str, path: str) -> list[tuple[str, set[str]]]:
    """Parse icacls output into [(principal, write-capable flags)] for allow entries."""
    aces = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("successfully processed") or line.startswith("Failed"):
            continue
        if line.lower().startswith(path.lower()):
            line = line[len(path):].strip()
        m = _ACE_RE.match(line)
        if not m:
            continue
        flags = []
        for grp in re.findall(r"\(([^)]*)\)", m.group("perms")):
            flags += [f.strip().upper() for f in grp.split(",") if f.strip()]
        if "DENY" in flags:
            continue
        aces.append((m.group("who").strip(), {f for f in flags if f in _WRITE_FLAGS}))
    return aces


def path_write_offenders(path) -> list[str]:
    """Return reasons ``path`` is writable by someone other than SYSTEM/Administrators/root.

    Fails closed: an unreadable ACL or an unrecognised (e.g. localised) principal with write
    access is reported.  Empty list means OK.
    """
    p = str(path)
    if not os.path.exists(p):
        return [f"{p}: missing"]
    if sys.platform == "win32":
        try:
            aces = parse_icacls(_icacls(p), p)
        except (UpdaterError, OSError, subprocess.SubprocessError) as e:
            return [f"{p}: cannot read ACL ({e})"]
        return [f"{p}: '{who}' has write access ({','.join(sorted(flags))})"
                for who, flags in aces if flags and not _SAFE_PRINCIPAL.search(who)]
    st = os.stat(p)
    out = []
    if st.st_mode & 0o022:
        out.append(f"{p}: group/world writable (mode {st.st_mode & 0o777:o})")
    if st.st_uid not in (0, os.geteuid()):
        out.append(f"{p}: owned by uid {st.st_uid}")
    return out


_TRUSTED_OWNER_SIDS = {"S-1-5-18", "S-1-5-32-544"}  # SYSTEM, Administrators


def _owner_sid(path: str) -> str:
    """Owner SID string of ``path`` (Windows only; module-level so tests can mock it).
    Raises OSError if it cannot be read."""
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.POINTER(ctypes.c_void_p)
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                          ptr, ptr, ptr, ptr, ptr]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    owner, sd = ctypes.c_void_p(), ctypes.c_void_p()
    rc = adv.GetNamedSecurityInfoW(path, 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(sd))  # SE_FILE_OBJECT, OWNER
    if rc != 0:
        raise OSError(f"GetNamedSecurityInfo failed ({rc})")
    try:
        text = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(owner, ctypes.byref(text)):
            raise OSError("ConvertSidToStringSid failed")
        try:
            return str(text.value)
        finally:
            k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        k32.LocalFree(sd)


def path_owner_offenders(path) -> list[str]:
    """Reasons ``path`` is not owned by SYSTEM/Administrators (Windows).  Fails closed: an owner
    that cannot be read, or is any other principal (e.g. a user who could re-grant themselves
    access), is reported.  POSIX ownership is already covered by :func:`path_write_offenders`."""
    p = str(path)
    if sys.platform != "win32" or not os.path.exists(p):
        return []
    try:
        sid = _owner_sid(p)
    except (OSError, ValueError) as e:
        return [f"{p}: cannot read owner ({e})"]
    return [] if sid.upper() in _TRUSTED_OWNER_SIDS else [f"{p}: owned by {sid}, expected Administrators or SYSTEM"]


def install_paths_to_verify(m: Marker) -> list[Path]:
    r = m.repo_path
    return [m.git_path, m.uv_path, r, r / ".git", r / "src", r / ".venv", m.python_path, m.path]


def install_subpaths_to_verify(m: Marker) -> list[Path]:
    """Key subdirectories to write-check as well (not a recursive walk).  Only those that exist:
    the toolchain layout differs between the Windows and Pi installs."""
    r = m.repo_path
    cands = [r / "src" / "stagewatch", m.python_path.parent, r / ".uv", r / ".uv" / "bin",
             m.uv_path.parent, r / ".git" / STATE_DIRNAME]
    return [c for c in cands if c.is_dir()]


def install_owner_paths(m: Marker) -> list[Path]:
    r = m.repo_path
    return [c for c in (r, r / ".git", r / ".venv", r / ".uv", r / "src") if c.exists()]


def verify_install_permissions(m: Marker, checker: Callable[[str], list[str]] | None = None,
                               owner_checker: Callable[[str], list[str]] | None = None) -> list[str]:
    """Pre-apply check (amendment C): git, uv, repo root, .git, src, .venv, marker (and the key
    subdirectories) must not be writable by non-admins, and the repo root, .git, .venv, .uv and
    src must be owned by Administrators/SYSTEM.  Returns offender strings; the launcher refuses
    if non-empty.  A custom ``checker`` without an ``owner_checker`` skips the owner check
    (tests); production passes neither and gets both."""
    if owner_checker is None and checker is None:
        owner_checker = path_owner_offenders
    checker = checker or path_write_offenders
    out: list[str] = []
    seen: set[str] = set()
    for p in [*install_paths_to_verify(m), *install_subpaths_to_verify(m)]:
        if str(p) not in seen:
            seen.add(str(p))
            out += checker(str(p))
    if owner_checker is not None:
        for p in install_owner_paths(m):
            out += owner_checker(str(p))
    return out


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
