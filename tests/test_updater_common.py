import json
import os
import sys

import pytest

from stagewatch import updater_common as uc
from updater_env import commit, git, make_env


@pytest.fixture()
def env(tmp_path):
    return make_env(tmp_path)


def test_sha_and_id_validation():
    assert uc.is_valid_sha("a" * 40)
    for bad in ("A" * 40, "a" * 39, "a" * 41, "g" * 40, "", None, 5, "HEAD"):
        assert not uc.is_valid_sha(bad)
    assert uc.is_valid_backup_id("20260930T010203Z-abc1234-def5678")
    for bad in ("../x", "a/b", "a\\b", "", "displaced-1", ".hidden", "x" * 81):
        assert not uc.is_valid_backup_id(bad)


def test_git_flags_and_env(env):
    ctx = env.ctx
    flags = " ".join(ctx.config_flags())
    assert "safe.directory=" in flags and "\\" not in flags.split("safe.directory=")[1].split(" ")[0]
    for f in ("core.fsmonitor=false", "credential.helper=", "protocol.allow=never", "core.hooksPath="):
        assert f in flags
    e = ctx.env()
    assert e["GIT_TERMINAL_PROMPT"] == "0" and e["GCM_INTERACTIVE"] == "never"
    assert e["GIT_CONFIG_NOSYSTEM"] == "1" and e["GIT_CONFIG_GLOBAL"].endswith("gitconfig")
    assert "GIT_ASKPASS" not in e
    assert (ctx.state_dir / "gitconfig").read_bytes() == b""


def test_git_env_strips_inherited_git_vars(env, monkeypatch):
    monkeypatch.setenv("GIT_ASKPASS", "evil")
    monkeypatch.setenv("GIT_DIR", "elsewhere")
    e = env.ctx.env()
    assert "GIT_ASKPASS" not in e and "GIT_DIR" not in e


def test_hooks_do_not_run(env):
    hook = env.clone / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    marker = env.root / "hook-ran"
    hook.write_text(f"#!/bin/sh\ntouch '{marker.as_posix()}'\n")
    os.chmod(hook, 0o755)
    uc.checkout_detach(env.ctx, env.shas["c2"])
    assert not marker.exists()


def test_head_detached_dirty_and_origin(env):
    ctx = env.ctx
    assert uc.head_sha(ctx) == env.shas["c1"]
    assert uc.is_detached(ctx)
    assert not uc.is_dirty(ctx)
    (env.clone / "untracked.txt").write_text("x")
    assert not uc.is_dirty(ctx)  # untracked files are ignored
    (env.clone / "mode.txt").write_text("modified")
    assert uc.is_dirty(ctx)
    with pytest.raises(uc.UpdaterError) as ei:
        uc.require_clean(ctx)
    assert ei.value.category == "dirty_tree"
    uc.check_origin(ctx, str(env.bare))
    with pytest.raises(uc.UpdaterError) as ei:
        uc.check_origin(ctx, "https://example.invalid/other.git")
    assert ei.value.category == "origin_mismatch"
    git(env.clone, "checkout", "-q", "main")
    assert not uc.is_detached(ctx)


def test_is_ancestor_exit_codes(env):
    ctx = env.ctx
    s = env.shas
    assert uc.is_ancestor(ctx, s["c1"], s["c3"]) is True
    assert uc.is_ancestor(ctx, s["c3"], s["c1"]) is False
    # exit >= 2 (unknown object) must be an error, never "no"
    with pytest.raises(uc.GitError) as ei:
        uc.is_ancestor(ctx, "f" * 40, s["c1"])
    assert ei.value.category == "ancestry_error"


def test_checkout_failure_is_error(env):
    (env.clone / "src" / "stagewatch" / "__init__.py").write_text("dirty")
    with pytest.raises(uc.GitError):
        uc.checkout_detach(env.ctx, env.shas["c3"])  # local change would be overwritten
    with pytest.raises(uc.UpdaterError):
        uc.checkout_detach(env.ctx, "not-a-sha")


def test_fetch_and_channel_targets(env):
    ctx = env.ctx
    s = env.shas
    uc.fetch_updates(ctx)
    assert uc.latest_stable_tag(ctx) == ("v0.2.0", s["c2"])
    assert uc.resolve_channel_target(ctx, "stable") == s["c2"]
    assert uc.resolve_channel_target(ctx, "nightly") == s["c3"]
    assert uc.version_at(ctx, s["c2"]) == "0.2.0"
    assert "__version__" in uc.show_file(ctx, s["c2"], "src/stagewatch/__init__.py")
    assert uc.show_file(ctx, s["c2"], "no/such/file") is None
    with pytest.raises(uc.UpdaterError):
        uc.resolve_channel_target(ctx, "main")


def test_stable_ignores_rc_and_off_main_tags(env):
    ctx, work, s = env.ctx, env.work, env.shas
    c4 = commit(work, "0.3.0-rc.1")
    git(work, "tag", "-a", "v0.3.0-rc.1", "-m", "rc", c4)
    git(work, "checkout", "-q", "-b", "side", s["c1"])
    side = commit(work, "9.9.9", message="off main")
    git(work, "tag", "-a", "v9.9.9", "-m", "off main", side)
    git(work, "checkout", "-q", "main")
    env.push("main", "--tags")
    uc.fetch_updates(ctx)
    assert uc.latest_stable_tag(ctx) == ("v0.2.0", s["c2"])


def test_nightly_must_be_ancestor_of_main(env):
    ctx, work, s = env.ctx, env.work, env.shas
    git(work, "checkout", "-q", "-b", "evil", s["c1"])
    evil = commit(work, "6.6.6", message="not on main")
    git(work, "push", "-q", "-f", "origin", "evil:refs/heads/nightly")
    uc.fetch_updates(ctx)
    assert uc.resolve_ref(ctx, "refs/remotes/origin/nightly") == evil
    assert uc.resolve_channel_target(ctx, "nightly") is None


def test_moved_tag_fails_fetch(env):
    ctx, work, s = env.ctx, env.work, env.shas
    uc.fetch_updates(ctx)
    git(work, "tag", "-f", "-a", "v0.2.0", "-m", "moved", s["c3"])
    git(work, "push", "-q", "-f", "origin", "refs/tags/v0.2.0")
    with pytest.raises(uc.GitError) as ei:
        uc.fetch_updates(ctx)
    assert ei.value.category == "fetch_failed"
    # and the local tag was not moved
    assert uc.resolve_ref(ctx, "refs/tags/v0.2.0") == s["c2"]


def test_check_update_target_rules(env):
    ctx, work, s = env.ctx, env.work, env.shas
    uc.fetch_updates(ctx)
    uc.check_update_target(ctx, s["c1"], s["c2"])
    uc.check_update_target(ctx, s["c1"], s["c3"])
    with pytest.raises(uc.UpdaterError) as ei:
        uc.check_update_target(ctx, s["c2"], s["c1"])
    assert ei.value.category == "downgrade"
    with pytest.raises(uc.UpdaterError) as ei:
        uc.check_update_target(ctx, s["c2"], s["c2"])
    assert ei.value.category == "not_newer"
    with pytest.raises(uc.UpdaterError) as ei:
        uc.check_update_target(ctx, s["c1"], "0" * 40)
    assert ei.value.category == "unknown_commit"
    with pytest.raises(uc.UpdaterError) as ei:
        uc.check_update_target(ctx, s["c1"], "zz")
    assert ei.value.category == "bad_sha"
    # a commit that exists locally but is not on origin/main
    git(env.clone, "fetch", "-q", "origin")
    git(work, "checkout", "-q", "-b", "side", s["c1"])
    side = commit(work, "8.8.8", message="side")
    git(work, "push", "-q", "origin", "side")
    git(env.clone, "fetch", "-q", "origin", "side")
    with pytest.raises(uc.UpdaterError) as ei:
        uc.check_update_target(ctx, s["c1"], side)
    assert ei.value.category == "not_ancestor"


def test_git_timeout_and_missing_binary(env, monkeypatch):
    bad = uc.GitContext(env.clone, str(env.root / "nogit.exe"), env.ctx.state_dir)
    with pytest.raises(uc.GitError) as ei:
        uc.head_sha(bad)
    assert ei.value.category == "git_missing"
    with pytest.raises(uc.GitError) as ei:
        uc.run_git(env.ctx, "rev-parse", "HEAD", timeout=0.0001)
    assert ei.value.category == "git_timeout"


# ---- pending / history / handshake ----

def _pending(**kw):
    p = {"format": 1, "action": "update", "from_sha": "a" * 40, "to_sha": "b" * 40,
         "backup_id": None, "schema_changed": False, "channel": "stable"}
    p.update(kw)
    return p


def test_pending_validation():
    assert uc.validate_pending(_pending())["to_sha"] == "b" * 40
    for bad in (_pending(to_sha="abc"), _pending(from_sha="B" * 40), _pending(action="format"),
                _pending(format=2), _pending(schema_changed=True), _pending(backup_id="../x"),
                _pending(channel="main"), _pending(action="rollback"),
                _pending(action="rollback", history_id="1"), "nope", None):
        with pytest.raises(uc.UpdaterError):
            uc.validate_pending(bad)
    ok = uc.validate_pending(_pending(action="rollback", history_id=3, backup_id="b1", schema_changed=True))
    assert ok["history_id"] == 3


def test_pending_roundtrip_and_history(tmp_path):
    sd = uc.ensure_state_dir(tmp_path)
    with pytest.raises(uc.UpdaterError) as ei:
        uc.read_pending(sd)
    assert ei.value.category == "no_pending"
    uc.write_pending(sd, _pending())
    assert uc.read_pending(sd)["from_sha"] == "a" * 40
    uc.pending_path(sd).write_text("{not json")
    with pytest.raises(uc.UpdaterError):
        uc.read_pending(sd)
    uc.clear_pending(sd, keep_rejected=True)
    assert (sd / "pending.rejected.json").exists() and not uc.pending_path(sd).exists()
    a = uc.append_history(sd, {"result": "ok"})
    b = uc.append_history(sd, {"result": "ok"})
    assert (a["id"], b["id"]) == (0, 1)
    assert uc.find_history(sd, 1)["result"] == "ok" and uc.find_history(sd, 9) is None
    assert not list(sd.glob("*.tmp"))


def test_handshake_env_gate(tmp_path):
    sd = uc.ensure_state_dir(tmp_path)
    assert uc.write_handshake_from_env("1.0", "abc", env={}) is False
    assert uc.write_handshake_from_env("1.0", "abc", env={uc.ENV_SUPERVISED: "1"}) is False
    env = {uc.ENV_SUPERVISED: "1", uc.ENV_STATE_DIR: str(sd), uc.ENV_NONCE: "n1"}
    assert uc.write_handshake_from_env("1.0", "abc1234", env=env) is True
    h = uc.read_handshake(sd)
    assert h["nonce"] == "n1" and h["pid"] == os.getpid() and h["version"] == "1.0"


def test_read_head_file(env):
    assert uc.read_head_file(env.clone) == env.shas["c1"]
    git(env.clone, "checkout", "-q", "main")
    assert uc.read_head_file(env.clone) is None  # symbolic ref


# ---- marker ----

def test_marker_loads_and_validates(env):
    m = env.marker()
    assert m.channel == "stable" and m.port == 18080 and m.state_dir == env.clone / ".git" / "stagewatch"
    assert m.uv_env["UV_CACHE_DIR"]

    def rewrite(**changes):
        d = json.loads(env.marker_path.read_text())
        d.update(changes)
        env.marker_path.write_text(json.dumps(d))

    original = env.marker_path.read_text()
    for changes in ({"repo_path": str(env.root / "elsewhere")}, {"git_path": "git"}, {"channel": "main"},
                    {"uv_env": {"PATH": "x"}}, {"port": 0}, {"format": 2}):
        rewrite(**changes)
        with pytest.raises(uc.UpdaterError) as ei:
            uc.load_marker(env.marker_path, require_https=False)
        assert ei.value.category == "marker_invalid"
        env.marker_path.write_text(original)
    with pytest.raises(uc.UpdaterError):
        uc.load_marker(env.marker_path)  # non-https origin refused by default
    with pytest.raises(uc.UpdaterError):
        uc.load_marker(env.root / "missing.json")


# ---- permission checks (mocked icacls) ----

ICACLS_GOOD = """C:\\Stagewatch NT AUTHORITY\\SYSTEM:(OI)(CI)(F)
             BUILTIN\\Administrators:(OI)(CI)(F)
             BUILTIN\\Users:(OI)(CI)(RX)

Successfully processed 1 files; Failed processing 0 files
"""
ICACLS_BAD = ICACLS_GOOD.replace("BUILTIN\\Users:(OI)(CI)(RX)", "BUILTIN\\Users:(OI)(CI)(M)")
ICACLS_AUTH = ICACLS_GOOD + "             NT AUTHORITY\\Authenticated Users:(I)(OI)(CI)(M)\n"
ICACLS_DENY = ICACLS_GOOD + "             BUILTIN\\Users:(DENY)(W)\n"
ICACLS_MULTI = ICACLS_GOOD + "             Everyone:(RX,WD)\n"


def test_parse_icacls():
    aces = uc.parse_icacls(ICACLS_GOOD, "C:\\Stagewatch")
    assert [w for w, _ in aces] == ["NT AUTHORITY\\SYSTEM", "BUILTIN\\Administrators", "BUILTIN\\Users"]
    assert aces[0][1] == {"F"} and aces[2][1] == set()
    assert dict(uc.parse_icacls(ICACLS_MULTI, "C:\\Stagewatch"))["Everyone"] == {"WD"}


@pytest.mark.skipif(sys.platform != "win32", reason="icacls path")
@pytest.mark.parametrize("out,expect_bad", [(ICACLS_GOOD, False), (ICACLS_BAD, True), (ICACLS_AUTH, True),
                                            (ICACLS_DENY, False), (ICACLS_MULTI, True)])
def test_path_write_offenders_windows_mocked(monkeypatch, tmp_path, out, expect_bad):
    monkeypatch.setattr(uc, "_icacls", lambda p: out)
    assert bool(uc.path_write_offenders(tmp_path)) is expect_bad


@pytest.mark.skipif(sys.platform != "win32", reason="icacls path")
def test_path_write_offenders_fails_closed(monkeypatch, tmp_path):
    def boom(p):
        raise uc.UpdaterError("acl_unreadable")
    monkeypatch.setattr(uc, "_icacls", boom)
    assert uc.path_write_offenders(tmp_path)
    assert uc.path_write_offenders(tmp_path / "missing")


@pytest.mark.skipif(sys.platform == "win32", reason="posix mode bits")
def test_path_write_offenders_posix(tmp_path):
    os.chmod(tmp_path, 0o755)
    assert uc.path_write_offenders(tmp_path) == []
    os.chmod(tmp_path, 0o777)
    assert uc.path_write_offenders(tmp_path)


def test_verify_install_permissions_uses_checker(env):
    m = env.marker()
    seen = []

    def checker(p):
        seen.append(p)
        return ["bad"] if p.endswith(".venv") else []

    out = uc.verify_install_permissions(m, checker)
    assert out == ["bad"]
    assert str(m.git_path) in seen and str(m.uv_path) in seen and str(m.repo_path) in seen
    assert uc.verify_install_permissions(m, lambda p: []) == []
