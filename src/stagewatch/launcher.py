"""Stagewatch launcher: a small stdlib-only supervisor for the server process.

    python -P <repo>/src/stagewatch/launcher.py --marker <repo>/.git/stagewatch-managed.json
    python -m stagewatch.launcher --marker ...            (with PYTHONPATH=<repo>/src)

Why it exists (plans/in-app-updater.md, amendment C):
- Task Scheduler does not restart a process that has exited, so this restarts the server
  (backoff 5 s -> 60 s).
- On Windows the server runs inside a kill-on-close Job Object, so stopping the launcher
  (or the scheduled task) never leaves an orphaned server holding the port (the venv
  python.exe is a redirector that starts a grandchild).
- Server exit code 75 means "apply the pending update/rollback".  The launcher NEVER trusts
  files it did not create: pending/history/backup manifests live in the admin-only
  ``<repo>/.git/stagewatch/``; it acts only if the child exited 75 in this session, re-checks
  the SHA and ancestry rules itself, and verifies backup hashes against the manifest before
  restoring.
- After an update it starts the server and waits for a handshake file (written by the child
  after startup) in the admin-only dir; on failure it rolls back (and restores the pre-update
  backup if the schema changed).

Imports stdlib only (plus sibling stdlib-only modules): it must work when the server's
dependencies are broken.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):  # run as a script: make `stagewatch` importable from <repo>/src
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stagewatch import backup as backup_mod  # noqa: E402
from stagewatch import updater_common as uc  # noqa: E402

log = logging.getLogger("stagewatch.launcher")

BACKOFF_MIN = 5.0
BACKOFF_MAX = 60.0
HEALTH_TIMEOUT = 120.0
STABLE_SECONDS = 30.0
UV_SYNC_TIMEOUT = 900
GRACEFUL_STOP = 15.0


class StopRequested(Exception):
    pass


# ---------------------------------------------------------------------------
# Child process (with a kill-on-close Job Object on Windows)
# ---------------------------------------------------------------------------

class _WinJob:
    """Kill-on-close Job Object via ctypes; closing the handle (or launcher death) kills members."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IOC(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32
        self._h = k32.CreateJobObjectW(None, None)
        if not self._h:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self._h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            self.close()
            raise OSError(err, "SetInformationJobObject failed")

    def assign(self, popen: subprocess.Popen) -> None:
        import ctypes
        if not self._k32.AssignProcessToJobObject(self._h, int(popen._handle)):  # type: ignore[attr-defined]
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def terminate(self) -> None:
        if self._h:
            self._k32.TerminateJobObject(self._h, 1)

    def close(self) -> None:
        if self._h:
            self._k32.CloseHandle(self._h)
            self._h = None


class Child:
    """The supervised server process (and, on Windows, everything in its job)."""

    def __init__(self, cmd: list[str], env: dict, cwd: str, out_file=None):
        self.cmd = cmd
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP (for Ctrl-Break)
        else:
            kwargs["start_new_session"] = True
        self.popen = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                      stdout=out_file or subprocess.DEVNULL,
                                      stderr=subprocess.STDOUT if out_file else subprocess.DEVNULL, **kwargs)
        self.job: _WinJob | None = None
        if sys.platform == "win32":
            try:  # tiny window before assignment; the redirector has not spawned its child yet
                self.job = _WinJob()
                self.job.assign(self.popen)
            except OSError as e:
                log.warning("Job Object unavailable (%s); falling back to taskkill /T", e)
                if self.job:
                    self.job.close()
                self.job = None

    @property
    def pid(self) -> int:
        return self.popen.pid

    def poll(self) -> int | None:
        return self.popen.poll()

    def _kill_tree(self) -> None:
        if sys.platform == "win32":
            if self.job:
                self.job.terminate()
            else:
                subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True,
                               creationflags=0x08000000)
        else:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def stop(self, graceful: float = GRACEFUL_STOP) -> None:
        """Ask nicely (Ctrl-Break / SIGTERM), then kill the whole tree."""
        if self.popen.poll() is None:
            try:
                if sys.platform == "win32":
                    self.popen.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    os.killpg(self.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                self.popen.wait(timeout=graceful)
            except subprocess.TimeoutExpired:
                pass
        self.cleanup()

    def cleanup(self) -> None:
        """Kill leftovers (grandchildren) and release the job; safe to call repeatedly."""
        self._kill_tree()
        try:
            self.popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.error("server process %s did not exit after kill", self.pid)
        if self.job:
            self.job.close()
            self.job = None


# ---------------------------------------------------------------------------
# Pending validation (pure-ish; used by the launcher and tests)
# ---------------------------------------------------------------------------

def validate_for_apply(ctx: uc.GitContext, marker: uc.Marker, p: dict,
                       acl_checker: Callable[[str], list[str]] | None = None) -> None:
    """Independently re-check a shape-valid pending dict.  Raises UpdaterError (short category)."""
    sd = marker.state_dir
    uc.check_origin(ctx, marker.origin_url)
    if not uc.is_detached(ctx):
        raise uc.UpdaterError("not_detached")
    head = uc.head_sha(ctx)
    if p["from_sha"] != head:
        raise uc.UpdaterError("from_mismatch")
    if not uc.commit_exists(ctx, p["to_sha"]):
        raise uc.UpdaterError("unknown_commit")
    if p["action"] == "update":
        uc.check_update_target(ctx, head, p["to_sha"])
    else:  # rollback: the target must be the recorded predecessor of a successful update
        entry = uc.find_history(sd, p["history_id"])
        if not entry or entry.get("result") != "ok" or entry.get("action") != "update":
            raise uc.UpdaterError("rollback_not_in_history")
        if entry.get("from_sha") != p["to_sha"] or entry.get("to_sha") != head:
            raise uc.UpdaterError("rollback_not_in_history", "history entry does not match")
        if bool(entry.get("schema_changed")) != p["schema_changed"] or entry.get("backup_id") != p["backup_id"]:
            raise uc.UpdaterError("rollback_not_in_history", "backup/schema mismatch")
    if p["schema_changed"]:
        backup_mod.verify_backup(marker.data_dir, sd, p["backup_id"])  # sha256 vs admin-only manifest
    bad = uc.verify_install_permissions(marker, acl_checker)
    if bad:
        raise uc.UpdaterError("unsafe_permissions", "; ".join(bad))
    uc.require_clean(ctx)


def run_uv_sync(marker: uc.Marker) -> None:
    env = dict(os.environ)
    env.update(marker.uv_env)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    try:
        cp = subprocess.run([str(marker.uv_path), "sync", "--frozen", "--no-dev", "--no-install-project"],
                            cwd=str(marker.repo_path), env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=UV_SYNC_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise uc.UpdaterError("uv_failed", str(e)) from e
    if cp.returncode != 0:
        raise uc.UpdaterError("uv_failed", cp.stderr.strip()[-800:])


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

class Launcher:
    def __init__(self, marker: uc.Marker, *, child_cmd: list[str] | None = None,
                 sync_fn: Callable[[uc.Marker], None] | None = None,
                 acl_checker: Callable[[str], list[str]] | None = None,
                 health_timeout: float = HEALTH_TIMEOUT, stable_seconds: float = STABLE_SECONDS,
                 backoff_min: float = BACKOFF_MIN, backoff_max: float = BACKOFF_MAX,
                 poll: float = 0.5, graceful_stop: float = GRACEFUL_STOP,
                 git_protocols: tuple[str, ...] = ("https",), child_env: dict | None = None):
        self.marker = marker
        self.sd = uc.ensure_state_dir(marker.repo_path)
        self.ctx = uc.GitContext(marker.repo_path, str(marker.git_path), self.sd, git_protocols)
        self.child_cmd = child_cmd
        self.sync_fn = sync_fn or run_uv_sync
        self.acl_checker = acl_checker
        self.health_timeout, self.stable_seconds = health_timeout, stable_seconds
        self.backoff_min, self.backoff_max = backoff_min, backoff_max
        self.poll, self.graceful_stop = poll, graceful_stop
        self.child_env = child_env or {}
        self.stop_event = threading.Event()
        self.nonce = ""
        self.current: Child | None = None
        self._out = None

    # ---- child management ----
    def _cmd(self) -> list[str]:
        if self.child_cmd:
            return list(self.child_cmd)
        m = self.marker
        cmd = [str(m.python_path), "-m", "stagewatch", "--data-dir", str(m.data_dir), "--port", str(m.port)]
        if m.emulate:
            cmd.append("--emulate")
        return cmd

    def _env(self) -> dict:
        env = dict(os.environ)
        env.update(self.child_env)
        env["PYTHONPATH"] = str(self.marker.repo_path / "src")
        env[uc.ENV_SUPERVISED] = "1"
        env[uc.ENV_STATE_DIR] = str(self.sd)
        env[uc.ENV_NONCE] = self.nonce
        return env

    def _start(self) -> Child:
        self.nonce = secrets.token_hex(16)
        try:
            uc.handshake_path(self.sd).unlink()
        except FileNotFoundError:
            pass
        if self._out is None:
            logs = self.marker.data_dir / "logs"
            try:
                logs.mkdir(parents=True, exist_ok=True)
                self._out = open(logs / "server-console.log", "ab")
            except OSError:
                self._out = None
        log.info("Starting server: %s", " ".join(self._cmd()))
        self.current = Child(self._cmd(), self._env(), str(self.marker.repo_path), self._out)
        return self.current

    def _wait(self, child: Child) -> int:
        while True:
            rc = child.poll()
            if rc is not None:
                child.cleanup()
                return rc
            if self.stop_event.wait(self.poll):
                raise StopRequested

    # ---- health check ----
    def _healthy(self, child: Child, to_sha: str, require_handshake: bool) -> bool:
        def alive_wait(seconds: float) -> bool:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if child.poll() is not None:
                    return False
                if self.stop_event.wait(self.poll):
                    raise StopRequested
            return child.poll() is None

        if require_handshake:
            end = time.monotonic() + self.health_timeout
            while True:
                if child.poll() is not None:
                    return False
                h = uc.read_handshake(self.sd)
                commit = h.get("commit") if h else None
                if h and h.get("nonce") == self.nonce and isinstance(commit, str) and len(commit) >= 7 \
                        and to_sha.startswith(commit):
                    break
                if time.monotonic() >= end:
                    return False
                if self.stop_event.wait(self.poll):
                    raise StopRequested
        return alive_wait(self.stable_seconds)

    # ---- apply ----
    def _record(self, p: dict | None, result: str, reason: str = "", **extra) -> dict:
        entry = {"result": result, "reason": reason}
        if p:
            entry.update({k: p.get(k) for k in ("action", "channel", "from_sha", "to_sha", "backup_id",
                                                "schema_changed", "history_id")})
            for key, sha in (("from_version", p["from_sha"]), ("to_version", p["to_sha"])):
                try:
                    entry[key] = uc.version_at(self.ctx, sha)
                except uc.UpdaterError:
                    entry[key] = None
        entry.update(extra)
        entry = uc.append_history(self.sd, entry)
        log.info("Update history #%s: %s %s", entry["id"], result, reason)
        return entry

    def reject_stale_pending(self, why: str) -> None:
        if uc.pending_path(self.sd).exists():
            log.warning("Ignoring pending update file: %s", why)
            uc.clear_pending(self.sd, keep_rejected=True)
            self._record(None, "rejected", why)

    def _apply(self) -> Child:
        try:
            p = uc.read_pending(self.sd)
        except uc.UpdaterError as e:
            log.error("Exit 75 but pending file unusable: %s", e)
            uc.clear_pending(self.sd, keep_rejected=True)
            self._record(None, "refused", e.category)
            return self._start()
        try:
            validate_for_apply(self.ctx, self.marker, p, self.acl_checker)
        except uc.UpdaterError as e:
            log.error("Refusing %s: %s", p["action"], e)
            uc.clear_pending(self.sd, keep_rejected=True)
            self._record(p, "refused", e.category)
            return self._start()
        uc.clear_pending(self.sd)  # one-shot
        log.info("Applying %s %s -> %s", p["action"], p["from_sha"][:7], p["to_sha"][:7])
        displaced = None  # what a manual rollback's data restore moved aside
        try:
            uc.checkout_detach(self.ctx, p["to_sha"])
            self.sync_fn(self.marker)
            if p["action"] == "rollback" and p["schema_changed"]:
                displaced = backup_mod.restore_backup(self.marker.data_dir, self.sd, p["backup_id"])
        except uc.UpdaterError as e:
            log.error("Apply failed: %s", e)
            return self._revert(p, e.category)
        child = self._start()
        if self._healthy(child, p["to_sha"], require_handshake=p["action"] == "update"):
            # a manual rollback that restored a data backup must say so (displaced = what it moved aside)
            self._record(p, "ok", restored_backup=displaced is not None)
            return child
        log.error("Health check failed after %s", p["action"])
        child.stop(0)
        return self._revert(p, "health_check_failed", displaced)

    def _revert(self, p: dict, reason: str, displaced: Path | None = None) -> Child:
        """Automatic return to from_sha.  For a schema-changing update, restore the pre-update
        backup; for a manual rollback whose data restore already ran, put the displaced (newer)
        data back so the user is not left on reverted code with the newer data moved aside."""
        restored = False
        data_returned = False
        try:
            uc.checkout_detach(self.ctx, p["from_sha"])
            self.sync_fn(self.marker)
            if p["action"] == "update" and p["schema_changed"]:
                backup_mod.restore_backup(self.marker.data_dir, self.sd, p["backup_id"])
                restored = True
            if displaced is not None:
                backup_mod.undo_restore(self.marker.data_dir, displaced)
                data_returned = True
            self._record(p, "rolled_back", reason, restored_backup=restored, data_returned=data_returned)
        except uc.UpdaterError as e:
            log.error("Automatic rollback failed: %s", e)
            if displaced is not None and not data_returned:
                try:  # data first: never leave the newer data set aside if we can avoid it
                    backup_mod.undo_restore(self.marker.data_dir, displaced)
                    data_returned = True
                except (uc.UpdaterError, OSError):
                    log.exception("could not return displaced data from %s", displaced)
            self._record(p, "failed", f"{reason}; rollback_failed:{e.category}", restored_backup=restored,
                         data_returned=data_returned)
        return self._start()

    # ---- main loop ----
    def run(self) -> int:
        self.reject_stale_pending("pending file present at launcher start (no exit 75 this session)")
        backoff = self.backoff_min
        try:
            child = self._start()
            started = time.monotonic()
            while True:
                rc = self._wait(child)
                ran = time.monotonic() - started
                if rc == uc.EXIT_APPLY:
                    child = self._apply()
                    backoff = self.backoff_min
                else:
                    self.reject_stale_pending(f"server exited {rc}, not {uc.EXIT_APPLY}")
                    if ran > 60:
                        backoff = self.backoff_min
                    log.warning("Server exited with code %s after %.0fs; restarting in %.0fs", rc, ran, backoff)
                    if self.stop_event.wait(backoff):
                        raise StopRequested
                    backoff = min(backoff * 2, self.backoff_max)
                    child = self._start()
                started = time.monotonic()
        except StopRequested:
            log.info("Stop requested; stopping server")
            if self.current is not None:
                self.current.stop(self.graceful_stop)
            return 0
        finally:
            if self._out:
                self._out.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging(data_dir: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            data_dir / "logs" / "launcher.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
    root.handlers[:] = handlers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stagewatch.launcher", description=__doc__.split("\n")[0])
    ap.add_argument("--marker", required=True, type=Path, help="path to .git/stagewatch-managed.json")
    args = ap.parse_args(argv)
    try:
        marker = uc.load_marker(args.marker)
    except uc.UpdaterError as e:
        print(f"stagewatch launcher: {e}", file=sys.stderr)
        return 2
    _setup_logging(marker.data_dir)
    launcher = Launcher(marker)

    def _stop(_signum, _frame):
        launcher.stop_event.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _stop)
    return launcher.run()


if __name__ == "__main__":
    sys.exit(main())
