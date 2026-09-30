"""Pre-0.2.0 hardening: durable saves, .bak fallback, temp cleanup, log hygiene, running-server
lock, launcher console-log rotation and hung-server watchdog."""

import logging
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
import yaml

from stagewatch import launcher as lc
from stagewatch import updater_common as uc
from stagewatch.core import config as cfgmod
from stagewatch.core.config import Config, ConfigStore, EsphomeDeviceConfig, reset_admin_pin
from stagewatch.web.auth import hash_pin
from updater_env import make_env


# ---------------------------------------------------------------- 1. durable saves + .bak

def test_save_fsyncs_file_and_directory(tmp_path, monkeypatch):
    synced, dirs = [], []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    monkeypatch.setattr(cfgmod, "fsync_dir", lambda d: dirs.append(d))
    store = ConfigStore(tmp_path / "config.yaml")
    store.save()
    assert synced and dirs == [tmp_path]
    uc.atomic_write_bytes(tmp_path / "x.json", b"{}")
    assert len(synced) >= 2


def test_old_config_with_esphome_password_loads_and_drops_it(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "esphome_devices": [
        {"id": "n1", "host": "n1.local", "noise_psk": "KEEPME", "password": "OLDPW-XYZ"}]}), encoding="utf-8")
    store = ConfigStore(p)
    cfg = store.load()
    assert cfg.esphome_devices[0].host == "n1.local" and cfg.esphome_devices[0].noise_psk == "KEEPME"
    store.save()
    text = p.read_text(encoding="utf-8")
    assert "OLDPW-XYZ" not in text and "password" not in text and "KEEPME" in text


def test_fsync_dir_is_best_effort(tmp_path):
    uc.fsync_dir(tmp_path)
    uc.fsync_dir(tmp_path / "missing")  # must not raise


def test_save_keeps_previous_good_config_as_bak(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.site.name = "One"
    store.save()
    assert not store.bak_path.exists()  # nothing to back up yet
    store.config.site.name = "Two"
    store.save()
    assert yaml.safe_load(store.bak_path.read_text(encoding="utf-8"))["site"]["name"] == "One"
    assert yaml.safe_load(store.path.read_text(encoding="utf-8"))["site"]["name"] == "Two"


def test_bad_current_file_never_overwrites_good_bak(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.site.name = "Good"
    store.save()
    store.config.site.name = "Newer"
    store.save()
    store.path.write_text("", encoding="utf-8")
    store.save()
    assert yaml.safe_load(store.bak_path.read_text(encoding="utf-8"))["site"]["name"] == "Good"


@pytest.mark.parametrize("bad", ["", "site: [unclosed\n", "- a\n- b\n"])
def test_load_falls_back_to_bak_when_config_empty_or_unparseable(tmp_path, bad, caplog):
    pin = hash_pin("1234")
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.admin.pin_hash = pin
    store.config.site.name = "Arena"
    store.save()
    store.save()  # now .bak exists
    store.path.write_text(bad, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        again = ConfigStore(tmp_path / "config.yaml")
        cfg = again.load()
    assert cfg.site.name == "Arena" and cfg.admin.pin_hash == pin
    assert not again.recovery_required
    assert "config.yaml.bak" in caplog.text
    assert yaml.safe_load(again.path.read_text(encoding="utf-8"))["site"]["name"] == "Arena"  # restored
    assert (tmp_path / "config.invalid.yaml").exists()  # bad file kept


def test_reset_admin_pin_also_clears_bak(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.config.admin.pin_hash = hash_pin("1234")
    store.save()
    store.save()
    reset_admin_pin(tmp_path)
    assert yaml.safe_load(store.bak_path.read_text(encoding="utf-8"))["admin"]["pin_hash"] == ""


def test_bak_is_not_in_site_config_allow_list_and_is_gitignored():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    site = (root / "scripts" / "site_config.py").read_text(encoding="utf-8")
    allow = [ln for ln in site.split("GITIGNORE = ")[1].split('"""')[1].splitlines() if ln.startswith("!")]
    assert not any("bak" in ln for ln in allow)
    ignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.bak" in ignore and "data/" in ignore


# ---------------------------------------------------------------- 2. stray temp files

def _old(path, age=3600):
    path.write_text("x")
    t = time.time() - age
    os.utime(path, (t, t))


def test_config_store_removes_only_old_stray_temps(tmp_path):
    old, fresh, other = tmp_path / ".config-abc.yaml", tmp_path / ".config-new.yaml", tmp_path / "keep.yaml"
    _old(old)
    fresh.write_text("x")
    other.write_text("x")
    ConfigStore(tmp_path / "config.yaml").load()
    assert not old.exists() and fresh.exists() and other.exists()


def test_launcher_removes_stale_tmp_in_state_dir(tmp_path):
    env = make_env(tmp_path)
    sd = uc.ensure_state_dir(env.clone)
    old, fresh = sd / "history.json.aaa.tmp", sd / "history.json.bbb.tmp"
    _old(old)
    fresh.write_text("x")
    lc.Launcher(env.marker(), git_protocols=("file",))
    assert not old.exists() and fresh.exists()


# ---------------------------------------------------------------- 3. secrets in logs

def test_bad_secret_values_never_reach_the_log(tmp_path, caplog):
    secret_psk, secret_pw = "SUPERSECRETPSK123", "SUPERSECRETPASSWORD456"
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({
        "esphome_devices": [{"id": "a", "host": "h", "noise_psk": [secret_psk],
                             "port": secret_pw}],
    }), encoding="utf-8")
    with caplog.at_level(logging.DEBUG):
        ConfigStore(p).load()
    assert "Config" in caplog.text and "invalid" in caplog.text
    assert secret_psk not in caplog.text and secret_pw not in caplog.text
    # unparseable YAML: only line/column are logged
    p2 = tmp_path / "b" / "config.yaml"
    p2.parent.mkdir()
    p2.write_text(f"esphome_devices:\n  - noise_psk: {secret_psk}\n    password: [{secret_pw}\n", encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        ConfigStore(p2).load()
    assert "line" in caplog.text
    assert secret_psk not in caplog.text and secret_pw not in caplog.text
    assert not any(r.exc_info for r in caplog.records)  # no traceback text


def test_validation_errors_hide_input():
    with pytest.raises(Exception) as ei:
        EsphomeDeviceConfig.model_validate({"id": "a", "host": "h", "noise_psk": ["TOPSECRET"]})
    assert "TOPSECRET" not in str(ei.value)
    with pytest.raises(Exception) as ei:
        Config.model_validate({"admin": {"pin_hash": ["TOPSECRET"]}})
    assert "TOPSECRET" not in str(ei.value)


def test_all_config_models_hide_input():
    for name in dir(cfgmod):
        obj = getattr(cfgmod, name)
        if isinstance(obj, type) and issubclass(obj, cfgmod.BaseModel) and obj is not cfgmod.BaseModel:
            assert obj.model_config.get("hide_input_in_errors") is True, name


# ---------------------------------------------------------------- 4. running-server lock

def _listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def test_server_lock_detects_live_server_and_ignores_stale(tmp_path):
    assert not uc.server_running(tmp_path)
    s, port = _listener()
    try:
        uc.write_server_lock(tmp_path, port)
        assert uc.server_running(tmp_path)
        uc.remove_server_lock(tmp_path)
        assert not (tmp_path / uc.SERVER_LOCK_NAME).exists()
    finally:
        s.close()
    # stale: dead pid
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    uc.atomic_write_json(tmp_path / uc.SERVER_LOCK_NAME, {"pid": p.pid, "port": 1})
    assert not uc.server_running(tmp_path)
    # live pid but old lock and nothing listening: reused PID / crashed server
    uc.atomic_write_json(tmp_path / uc.SERVER_LOCK_NAME, {"pid": os.getpid(), "port": 1})
    t = time.time() - 3600
    os.utime(tmp_path / uc.SERVER_LOCK_NAME, (t, t))
    assert not uc.server_running(tmp_path)
    (tmp_path / uc.SERVER_LOCK_NAME).write_text("garbage")
    assert not uc.server_running(tmp_path)


def test_remove_server_lock_leaves_someone_elses_lock(tmp_path):
    uc.atomic_write_json(tmp_path / uc.SERVER_LOCK_NAME, {"pid": os.getpid() + 1, "port": 1})
    uc.remove_server_lock(tmp_path)
    assert (tmp_path / uc.SERVER_LOCK_NAME).exists()


def test_reset_admin_pin_cli_refuses_while_running_unless_forced(tmp_path, capsys):
    from stagewatch.__main__ import reset_admin_pin_cli
    s, port = _listener()
    try:
        uc.write_server_lock(tmp_path, port)
        with pytest.raises(SystemExit) as ei:
            reset_admin_pin_cli(["--data-dir", str(tmp_path)])
        assert ei.value.code == 1
        assert "stop the stagewatch service first" in capsys.readouterr().err.lower()
        assert not (tmp_path / cfgmod.ALLOW_ONBOARDING_FLAG).exists()
        reset_admin_pin_cli(["--data-dir", str(tmp_path), "--force"])
        assert (tmp_path / cfgmod.ALLOW_ONBOARDING_FLAG).exists()
        assert "anyone on the network" in capsys.readouterr().out
    finally:
        s.close()


# ---------------------------------------------------------------- 5. console log rotation

def test_rotate_log_keeps_at_most_three(tmp_path):
    log = tmp_path / "server-console.log"
    for i in range(6):
        log.write_text(f"session {i}")
        lc.rotate_log(log)
    assert not log.exists()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["server-console.log.1", "server-console.log.2", "server-console.log.3"]
    assert (tmp_path / "server-console.log.1").read_text() == "session 5"
    assert (tmp_path / "server-console.log.3").read_text() == "session 3"
    lc.rotate_log(tmp_path / "nothing.log")  # missing file: no error


def test_launcher_rotates_console_log_at_start_and_keeps_only_stderr(tmp_path):
    env = make_env(tmp_path)
    marker = env.marker()
    logs = marker.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "server-console.log").write_text("previous session")
    code = "import sys; print('to-stdout'); print('to-stderr', file=sys.stderr); sys.stderr.flush()"
    L = lc.Launcher(marker, child_cmd=[sys.executable, "-c", code], git_protocols=("file",),
                    backoff_min=30, backoff_max=30, poll=0.05, graceful_stop=0.5)
    child = L._start()
    child.popen.wait(10)
    L._out.flush()
    L.current.cleanup()
    L._out.close()
    assert (logs / "server-console.log.1").read_text() == "previous session"
    text = (logs / "server-console.log").read_text()
    assert "to-stderr" in text and "to-stdout" not in text


# ---------------------------------------------------------------- 8. watchdog

def _watchdog_launcher(tmp_path, probe, **kw):
    env = make_env(tmp_path)
    return lc.Launcher(env.marker(), child_cmd=[sys.executable, "-c", "import time; time.sleep(120)"],
                       git_protocols=("file",), backoff_min=0.1, backoff_max=0.1, poll=0.02, graceful_stop=0.5,
                       watchdog_probe=probe, **kw)


def _run(L):
    t = threading.Thread(target=L.run, daemon=True)
    t.start()
    return t


def _stop(L, t):
    L.stop_event.set()
    t.join(30)
    assert not t.is_alive()


def test_watchdog_restarts_hung_server(tmp_path):
    L = _watchdog_launcher(tmp_path, lambda: False, watchdog_grace=0.2, watchdog_interval=0.05,
                           watchdog_failures=3)
    t = _run(L)
    try:
        end = time.monotonic() + 30
        first = None
        while time.monotonic() < end:
            cur = L.current
            if cur is not None:
                first = first or cur
                if cur is not first:
                    break
            time.sleep(0.05)
        assert first is not None and L.current is not first
        assert first.poll() is not None  # the hung one was killed
    finally:
        _stop(L, t)


def test_watchdog_is_quiet_during_grace_and_when_healthy(tmp_path):
    calls = []
    L = _watchdog_launcher(tmp_path, lambda: calls.append(1) or False, watchdog_grace=60,
                           watchdog_interval=0.05, watchdog_failures=1)
    t = _run(L)
    try:
        time.sleep(1.0)
        first = L.current
        assert first is not None and first.poll() is None and calls == []  # still in startup grace
    finally:
        _stop(L, t)


def test_watchdog_leaves_a_healthy_server_alone(tmp_path):
    L = _watchdog_launcher(tmp_path, lambda: True, watchdog_grace=0.1, watchdog_interval=0.05,
                           watchdog_failures=1)
    t = _run(L)
    try:
        time.sleep(1.0)
        first = L.current
        assert first.poll() is None and L.current is first
    finally:
        _stop(L, t)


def test_watchdog_skips_while_update_pending(tmp_path):
    L = _watchdog_launcher(tmp_path, lambda: False, watchdog_grace=0.1, watchdog_interval=0.05,
                           watchdog_failures=2)
    uc.pending_path(L.sd).write_text("{}")
    L.reject_stale_pending = lambda why: None  # keep the file for this test
    t = _run(L)
    try:
        time.sleep(1.0)
        first = L.current
        assert first is not None and first.poll() is None
    finally:
        _stop(L, t)


def test_http_probe_counts_any_http_answer_as_alive():
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        assert lc.http_probe(srv.server_address[1], timeout=2)
    finally:
        srv.shutdown()
        srv.server_close()
    assert not lc.http_probe(srv.server_address[1], timeout=1)
