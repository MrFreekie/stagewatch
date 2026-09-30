import ast
import json
import sqlite3
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from stagewatch import backup as bk
from stagewatch import launcher as lc
from stagewatch import updater_common as uc
from updater_env import make_env

SRC = Path(__file__).resolve().parents[1] / "src" / "stagewatch"

# The fake "server": behaviour is chosen by mode.txt in the checked-out repo, so a checkout
# changes what the "new version" does. Control files in <ctl>/ let the test make it exit.
FAKE_SERVER = textwrap.dedent('''
    import json, os, subprocess, sys, time
    repo, ctl = sys.argv[1], sys.argv[2]
    mode = open(os.path.join(repo, "mode.txt")).read().strip()
    if mode == "crash":
        sys.exit(3)
    if mode == "good":
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        sd = os.environ["STAGEWATCH_STATE_DIR"]
        with open(os.path.join(sd, "handshake.json"), "w") as f:
            json.dump({"format": 1, "pid": os.getpid(), "version": "x", "commit": head[:7],
                       "nonce": os.environ["STAGEWATCH_HANDSHAKE_NONCE"]}, f)
    while True:  # mode "bad": alive but never writes the handshake
        for name, code in (("exit75", 75), ("crash", 1)):
            p = os.path.join(ctl, name)
            if os.path.exists(p):
                os.remove(p)
                sys.exit(code)
        time.sleep(0.05)
''')


class Harness:
    def __init__(self, tmp_path, **kw):
        self.env = make_env(tmp_path)
        self.ctl = tmp_path / "ctl"
        self.ctl.mkdir()
        fake = tmp_path / "fake_server.py"
        fake.write_text(FAKE_SERVER)
        self.sync_calls = []
        self.acl_offenders = []
        self.launcher = lc.Launcher(
            self.env.marker(), child_cmd=[sys.executable, str(fake), str(self.env.clone), str(self.ctl)],
            sync_fn=lambda m: self.sync_calls.append(uc.head_sha(self.env.ctx)),
            acl_checker=lambda p: list(self.acl_offenders),
            health_timeout=4.0, stable_seconds=0.5, backoff_min=0.2, backoff_max=0.4, poll=0.05,
            graceful_stop=0.5, git_protocols=("file",))
        self.thread = None
        self.sd = self.launcher.sd

    def start(self):
        self.thread = threading.Thread(target=self.launcher.run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.launcher.stop_event.set()
        if self.thread:
            self.thread.join(30)
            assert not self.thread.is_alive()

    def wait(self, cond, timeout=30.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            v = cond()
            if v:
                return v
            time.sleep(0.05)
        raise AssertionError("timed out waiting for condition")

    def wait_handshake(self):
        return self.wait(lambda: (h := uc.read_handshake(self.sd)) and h["nonce"] == self.launcher.nonce and h)

    def history(self):
        return uc.load_history(self.sd)

    def wait_history(self, n=1):
        return self.wait(lambda: len(self.history()) >= n and self.history())

    def request_exit75(self, **pending):
        p = {"format": 1, "action": "update", "from_sha": self.env.shas["c1"], "to_sha": self.env.shas["c2"],
             "backup_id": None, "schema_changed": False, "channel": "stable"}
        p.update(pending)
        # write raw (not via write_pending) so invalid content can be tested too
        uc.pending_path(self.sd).write_text(json.dumps(p))
        (self.ctl / "exit75").write_text("")

    def head(self):
        return uc.head_sha(self.env.ctx)


@pytest.fixture()
def h(tmp_path):
    hh = Harness(tmp_path)
    yield hh
    hh.stop()


def _fetch_and_start(h):
    uc.fetch_updates(h.env.ctx)
    return h.start()


def _set_mode(h, sha_key, mode):
    """Rewrite a commit so its mode.txt differs: build a new commit on top of c2 (used as target)."""
    from updater_env import commit, git
    git(h.env.work, "checkout", "-q", "main")
    new = commit(h.env.work, "0.3.0", mode=mode, message=f"target {mode}")
    h.env.push("main")
    uc.fetch_updates(h.env.ctx)
    return new


def _db_backup(h, rows_before=5):
    con = sqlite3.connect(h.env.data / bk.DB_NAME)
    con.execute("CREATE TABLE t (n INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows_before)])
    con.commit()
    con.close()
    (h.env.data / "config.yaml").write_text("v: pre-update\n")
    bk.create_backup(h.env.data, h.sd, "pre", from_sha=h.env.shas["c1"], to_sha=h.env.shas["c2"])


# ---- import hygiene ----

@pytest.mark.parametrize("name", ["launcher.py", "updater_common.py", "backup.py", "__init__.py"])
def test_launcher_side_modules_are_stdlib_only(name):
    tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module.split(".")[0]]
        for m in mods:
            assert m in sys.stdlib_module_names or m in ("stagewatch", "__future__"), f"{name} imports {m}"


# ---- pending validation ----

def test_stale_pending_at_start_is_rejected(h):
    h.start()
    h.wait_handshake()
    h.stop()
    uc.pending_path(h.sd).write_text(json.dumps({"format": 1}))
    h2 = Harness.__new__(Harness)
    h2.__dict__.update(h.__dict__)
    h2.launcher = lc.Launcher(h.env.marker(), child_cmd=h.launcher.child_cmd, sync_fn=h.launcher.sync_fn,
                              acl_checker=h.launcher.acl_checker, health_timeout=4, stable_seconds=0.5,
                              backoff_min=0.2, backoff_max=0.4, poll=0.05, graceful_stop=0.5)
    h2.start()
    entries = h2.wait_history()
    h2.stop()
    assert entries[-1]["result"] == "rejected"
    assert not uc.pending_path(h.sd).exists() and (h.sd / "pending.rejected.json").exists()
    assert h.head() == h.env.shas["c1"] and h.sync_calls == []


def test_pending_without_exit75_is_rejected_when_child_crashes(h):
    h.start()
    h.wait_handshake()
    uc.pending_path(h.sd).write_text(json.dumps({"format": 1, "action": "update",
                                                 "from_sha": h.env.shas["c1"], "to_sha": h.env.shas["c2"]}))
    (h.ctl / "crash").write_text("")  # exits 1, not 75
    entries = h.wait_history()
    assert entries[0]["result"] == "rejected"
    assert h.head() == h.env.shas["c1"] and h.sync_calls == []
    h.wait_handshake()  # and the server was restarted


def test_bad_sha_in_pending_is_refused(h):
    _fetch_and_start(h)
    h.wait_handshake()
    h.request_exit75(to_sha="not-a-sha")
    e = h.wait_history()[0]
    assert (e["result"], e["reason"]) == ("refused", "bad_sha")
    assert h.head() == h.env.shas["c1"] and h.sync_calls == []
    h.wait_handshake()


def test_unknown_or_non_ancestor_target_is_refused(h):
    _fetch_and_start(h)
    h.wait_handshake()
    h.request_exit75(to_sha="e" * 40)
    assert h.wait_history()[0]["reason"] == "unknown_commit"
    h.wait_handshake()
    h.request_exit75(from_sha=h.env.shas["c2"])  # not what is checked out
    assert h.wait_history(2)[1]["reason"] == "from_mismatch"
    assert h.sync_calls == []


def test_manifest_hash_mismatch_refuses_update(h):
    _db_backup(h)
    (h.env.data / "backups" / "pre" / "config.yaml").write_text("tampered\n")
    _fetch_and_start(h)
    h.wait_handshake()
    h.request_exit75(backup_id="pre", schema_changed=True)
    e = h.wait_history()[0]
    assert (e["result"], e["reason"]) == ("refused", "backup_hash_mismatch")
    assert h.head() == h.env.shas["c1"] and h.sync_calls == []


def test_unsafe_permissions_refuse_apply(h):
    h.acl_offenders = ["C:/x: 'BUILTIN\\Users' has write access (M)"]
    _fetch_and_start(h)
    h.wait_handshake()
    h.request_exit75()
    e = h.wait_history()[0]
    assert (e["result"], e["reason"]) == ("refused", "unsafe_permissions")
    assert h.head() == h.env.shas["c1"] and h.sync_calls == []


def test_dirty_tree_refuses_apply(h):
    _fetch_and_start(h)
    h.wait_handshake()
    (h.env.clone / "src" / "stagewatch" / "__init__.py").write_text("local edit")
    h.request_exit75()
    assert h.wait_history()[0]["reason"] == "dirty_tree"
    assert h.sync_calls == []


# ---- apply, auto-rollback, manual rollback ----

def test_update_applies_and_records_history(h):
    _fetch_and_start(h)
    h.wait_handshake()
    h.request_exit75()
    e = h.wait_history()[0]
    assert e["result"] == "ok" and e["from_version"] == "0.1.0" and e["to_version"] == "0.2.0"
    assert h.head() == h.env.shas["c2"]
    assert h.sync_calls == [h.env.shas["c2"]]  # uv sync ran after checkout
    assert not uc.pending_path(h.sd).exists()
    hs = h.wait_handshake()
    assert h.env.shas["c2"].startswith(hs["commit"])


def test_failed_health_check_rolls_back_and_restores_backup(h):
    _db_backup(h)
    bad = _set_mode(h, "c4", "bad")  # new version never writes its handshake
    con = sqlite3.connect(h.env.data / bk.DB_NAME)  # "migrated" DB after the failed update started
    con.execute("INSERT INTO t VALUES (999)")
    con.commit()
    con.close()
    (h.env.data / "config.yaml").write_text("v: post-update\n")
    h.launcher.health_timeout = 1.5
    h.start()
    h.wait_handshake()
    h.request_exit75(to_sha=bad, backup_id="pre", schema_changed=True)
    e = h.wait_history()[0]
    assert (e["result"], e["reason"]) == ("rolled_back", "health_check_failed")
    assert e["restored_backup"] is True
    assert h.head() == h.env.shas["c1"]
    assert h.sync_calls == [bad, h.env.shas["c1"]]
    assert (h.env.data / "config.yaml").read_text() == "v: pre-update\n"
    con = sqlite3.connect(h.env.data / bk.DB_NAME)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 5
    con.close()
    displaced = [p for p in (h.env.data / "backups").iterdir() if p.name.startswith("displaced-")]
    assert len(displaced) == 1 and (displaced[0] / bk.DB_NAME).exists()
    h.wait_handshake()  # the old version is running again


def test_crash_loop_after_update_rolls_back_without_restore_when_no_schema_change(h):
    bad = _set_mode(h, "c4", "crash")
    h.start()
    h.wait_handshake()
    h.request_exit75(to_sha=bad)
    e = h.wait_history()[0]
    assert (e["result"], e["reason"], e["restored_backup"]) == ("rolled_back", "health_check_failed", False)
    assert h.head() == h.env.shas["c1"]


def test_sync_failure_rolls_back(h):
    def failing_sync(m):
        if uc.head_sha(h.env.ctx) == h.env.shas["c2"]:
            raise uc.UpdaterError("uv_failed", "boom")
    h.launcher.sync_fn = failing_sync
    _fetch_and_start(h)
    h.wait_handshake()
    h.request_exit75()
    e = h.wait_history()[0]
    assert (e["result"], e["reason"]) == ("rolled_back", "uv_failed")
    assert h.head() == h.env.shas["c1"]


def test_manual_rollback_requires_history_and_restores(h):
    _db_backup(h)
    _fetch_and_start(h)
    h.wait_handshake()
    # a rollback that is not in the history is refused
    h.request_exit75(action="rollback", history_id=42, from_sha=h.env.shas["c1"], to_sha=h.env.shas["c2"])
    assert h.wait_history()[0]["reason"] == "rollback_not_in_history"
    h.wait_handshake()
    # real update (with a schema-change backup), then roll it back via its history id
    h.request_exit75(backup_id="pre", schema_changed=True)
    upd = h.wait_history(2)[1]
    assert upd["result"] == "ok" and upd["id"] == 1
    h.wait_handshake()
    con = sqlite3.connect(h.env.data / bk.DB_NAME)
    con.execute("INSERT INTO t VALUES (123)")
    con.commit()
    con.close()
    h.request_exit75(action="rollback", history_id=1, from_sha=h.env.shas["c2"], to_sha=h.env.shas["c1"],
                     backup_id="pre", schema_changed=True)
    rb = h.wait_history(3)[2]
    assert rb["result"] == "ok" and rb["action"] == "rollback"
    assert rb["restored_backup"] is True  # data was restored, so say so
    assert not upd.get("restored_backup")
    assert rb["restored_backup"] is True  # data was restored, so say so
    assert not upd.get("restored_backup")
    assert h.head() == h.env.shas["c1"]
    con = sqlite3.connect(h.env.data / bk.DB_NAME)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 5
    con.close()
    # the same history entry cannot be replayed (we are no longer on its to_sha)
    h.wait_handshake()
    h.request_exit75(action="rollback", history_id=1, from_sha=h.env.shas["c1"], to_sha=h.env.shas["c1"],
                     backup_id="pre", schema_changed=True)
    assert h.wait_history(4)[3]["result"] == "refused"


def test_failed_manual_rollback_returns_displaced_data(h):
    """A rollback whose target code fails its health check must not leave the user on the
    newer code with the newer data set aside: the displaced data comes back."""
    from updater_env import commit, git
    git(h.env.work, "checkout", "-q", "main")
    old_bad = commit(h.env.work, "0.3.0", mode="crash", message="old, crashes")
    new_good = commit(h.env.work, "0.3.1", mode="good", message="new, good")
    h.env.push("main")
    uc.fetch_updates(h.env.ctx)
    uc.checkout_detach(h.env.ctx, new_good)
    # data as of the (schema-changing) update's backup, then "newer" data written afterwards
    con = sqlite3.connect(h.env.data / bk.DB_NAME)
    con.execute("CREATE TABLE t (n INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    (h.env.data / "config.yaml").write_text("v: old\n")
    bk.create_backup(h.env.data, h.sd, "pre", from_sha=old_bad, to_sha=new_good)
    con = sqlite3.connect(h.env.data / bk.DB_NAME)
    con.execute("INSERT INTO t VALUES (2)")
    con.commit()
    con.close()
    (h.env.data / "config.yaml").write_text("v: newer\n")
    hist = uc.append_history(h.sd, {"action": "update", "result": "ok", "from_sha": old_bad,
                                    "to_sha": new_good, "backup_id": "pre", "schema_changed": True})
    h.launcher.health_timeout = 1.5
    h.start()
    h.wait_handshake()
    h.request_exit75(action="rollback", history_id=hist["id"], from_sha=new_good, to_sha=old_bad,
                     backup_id="pre", schema_changed=True)
    e = h.wait_history(2)[1]
    assert (e["result"], e["reason"]) == ("rolled_back", "health_check_failed")
    assert e["data_returned"] is True
    assert h.head() == new_good
    assert (h.env.data / "config.yaml").read_text() == "v: newer\n"
    con = sqlite3.connect(h.env.data / bk.DB_NAME)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 2
    con.close()
    h.wait_handshake()  # newer code is running again


def test_launcher_restarts_crashed_server_with_backoff(h):
    h.start()
    first = h.wait_handshake()
    (h.ctl / "crash").write_text("")
    h.wait(lambda: (x := uc.read_handshake(h.sd)) and x["pid"] != first["pid"] and h.launcher.nonce == x["nonce"])
    assert h.history() == []  # plain crash: no update history noise


def test_stop_kills_child(h):
    h.start()
    h.wait_handshake()
    child = h.launcher.current
    h.stop()
    assert child.poll() is not None


def test_main_rejects_invalid_marker(tmp_path, capsys):
    assert lc.main(["--marker", str(tmp_path / "nope.json")]) == 2
    assert "marker_invalid" in capsys.readouterr().err


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_server_writes_handshake_only_when_supervised(tmp_path):
    """Starts the real server in emulate mode: handshake appears only with the launcher's env."""
    import os
    import subprocess
    sd = uc.ensure_state_dir(tmp_path / "repo")
    base = {k: v for k, v in os.environ.items() if not k.startswith("STAGEWATCH_")}

    def run(extra, expect):
        data = tmp_path / ("data-" + str(bool(extra)))
        proc = subprocess.Popen(
            [sys.executable, "-m", "stagewatch", "--emulate", "--no-mdns", "--host", "127.0.0.1",
             "--port", str(_free_port()), "--data-dir", str(data)],
            env={**base, **extra}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            end = time.monotonic() + (40 if expect else 8)
            while time.monotonic() < end:
                if uc.read_handshake(sd):
                    break
                assert proc.poll() is None
                time.sleep(0.2)
            return uc.read_handshake(sd)
        finally:
            proc.kill()
            proc.wait(10)

    assert run({}, expect=False) is None
    h = run({uc.ENV_SUPERVISED: "1", uc.ENV_STATE_DIR: str(sd), uc.ENV_NONCE: "abc"}, expect=True)
    assert h and h["nonce"] == "abc" and h["version"]
