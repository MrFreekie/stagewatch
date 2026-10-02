"""Server side of the in-app updater: pure parsing, check logic against a fake remote,
update/rollback scheduling, the admin endpoints, and the body-size cap.  No network."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stagewatch import updater_common as uc
from stagewatch.core import updater as up
from stagewatch.core.hub import Hub
from stagewatch.version import format_describe, parse_describe
from stagewatch.web.limits import BodySizeLimitMiddleware
from stagewatch.web.server import create_app
from updater_env import commit, git, make_env

PIN = "1234"


def version_py(config=1, db=1):
    return f"CONFIG_SCHEMA_VERSION = {config}\nDB_SCHEMA_VERSION = {db}\n"


# ---------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------

def test_parse_schema_versions():
    assert up.parse_schema_versions(version_py(2, 3)) == (2, 3)
    assert up.parse_schema_versions("X = 1") == (None, None)
    assert up.parse_schema_versions(None) == (None, None)
    assert up.schema_changed((1, 1), (1, 1)) is False
    assert up.schema_changed((1, 1), (1, 2)) is True
    assert up.schema_changed((1, 1), (None, None)) is True  # cannot tell -> back up
    assert up.schema_changed((None, None), (None, None)) is False


CHANGELOG = """# Changelog

## [Unreleased]

### Added
- upcoming thing

## [0.3.0] - 2026-10-01

### Added
- three <script>alert(1)</script>

## [0.2.0] - 2026-09-30

### Added
- two

## [0.1.0] - 2026-09-01
- one

[Unreleased]: https://example.invalid/compare
[0.1.0]: https://example.invalid/0.1.0
"""


def test_changelog_excerpt_is_newer_sections_plain_text():
    out = up.changelog_excerpt(CHANGELOG, "0.2.0")
    assert "upcoming thing" in out and "[0.3.0] - 2026-10-01" in out.replace("0.3.0]", "0.3.0]")
    assert "- two" not in out and "- one" not in out
    assert "example.invalid" not in out  # link definitions dropped
    assert "<script>" in out  # untouched text: the UI renders it as text, never HTML
    assert up.changelog_excerpt("## [Unreleased]\n\n## [0.2.0] - x\n- a\n", "0.2.0") == ""
    assert up.changelog_excerpt(None, "0.1.0") == ""
    long = "## [9.0.0] - x\n" + "- line\n" * 2000
    assert up.changelog_excerpt(long, "0.1.0").endswith("(truncated)")


PY_CLEAN = '[project]\nname="x"\nrequires-python=">=3.12"\n'
LOCK_CLEAN = 'version = 1\n[[package]]\nname="a"\nsource = { registry = "https://pypi.org/simple" }\n' \
             '[[package]]\nname="x"\nsource = { virtual = "." }\n'


def test_dependency_source_findings():
    assert up.dependency_source_findings(PY_CLEAN, LOCK_CLEAN) == set()
    f = up.dependency_source_findings(PY_CLEAN + '[tool.uv.sources]\nfoo = { git = "https://e.invalid/f" }\n',
                                      LOCK_CLEAN)
    assert f == {"pyproject:sources:foo"}
    f = up.dependency_source_findings(PY_CLEAN + '[[tool.uv.index]]\nname="priv"\nurl="https://e.invalid/simple"\n',
                                      LOCK_CLEAN)
    assert f == {"pyproject:index:priv"}
    for kind, val in (("git", "https://e.invalid/f"), ("url", "https://e.invalid/f.whl"), ("path", "../f"),
                      ("directory", "f")):
        lock = LOCK_CLEAN + f'[[package]]\nname="evil"\nsource = {{ {kind} = "{val}" }}\n'
        assert up.dependency_source_findings(PY_CLEAN, lock) == {f"lock:{kind}:evil"}
    lock = LOCK_CLEAN + '[[package]]\nname="evil"\nsource = { registry = "https://e.invalid/simple" }\n'
    assert up.dependency_source_findings(PY_CLEAN, lock) == {"lock:registry:evil"}
    with pytest.raises(ValueError):
        up.dependency_source_findings("not = [toml", LOCK_CLEAN)


def test_version_describe_formatting():
    assert parse_describe("v0.2.0-14-gabc1234") == ("0.2.0", 14, "abc1234")
    assert parse_describe("v0.2.0-0-gabc1234") == ("0.2.0", 0, "abc1234")
    assert parse_describe("garbage") is None and parse_describe(None) is None
    assert format_describe("0.2.0", 14, "abc1234") == "0.2.0+14.gabc1234"
    assert format_describe("0.2.0", 0, "abc1234") == "0.2.0+abc1234"
    assert format_describe("0.2.0", 3, "abc1234", dirty=True) == "0.2.0+3.gabc1234.dirty"
    assert format_describe("0.2.0", None, "unknown") == "0.2.0"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

class Rig:
    def __init__(self, tmp_path):
        self.env = make_env(tmp_path)
        self.hub = Hub(self.env.data, emulate=True)
        self.shutdowns = []
        self.hub.request_shutdown = lambda: self.shutdowns.append(1)
        self.updater = up.Updater(self.hub, self.env.marker(), supervised=True, git_protocols=("file",),
                                  check_interval_s=0)

    def release(self, version, tag=None, mode="good", files=None, ff_nightly=False):
        git(self.env.work, "checkout", "-q", "main")
        sha = commit(self.env.work, version, mode=mode, files=files, message=f"release {version}")
        if tag:
            git(self.env.work, "tag", "-a", tag, "-m", tag)
        self.env.push("main", *(["--tags"] if tag else []))
        if ff_nightly:
            self.env.push(f"{sha}:refs/heads/nightly")
        return sha

    def pending(self):
        return json.loads(uc.pending_path(self.env.marker().state_dir).read_text())


@pytest.fixture()
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.hub.recorder.close()


def run_check(rig, channel="stable"):
    return up.run_check(rig.env.ctx, rig.env.marker(), channel)


# ---------------------------------------------------------------------------
# check logic
# ---------------------------------------------------------------------------

def test_check_stable_finds_latest_tag(rig):
    r = run_check(rig)
    assert r["available"] and r["target_sha"] == rig.env.shas["c2"]
    assert (r["from_version"], r["target_version"]) == ("0.1.0", "0.2.0")
    assert r["schema_changed"] is False


def test_check_nightly_targets_nightly_branch_with_commit_subjects(rig):
    r = run_check(rig, "nightly")
    assert r["target_sha"] == rig.env.shas["c3"]
    assert "post-release work" in r["commits"]


def test_check_up_to_date_and_downgrade_are_not_errors(rig):
    uc.checkout_detach(rig.env.ctx, rig.env.shas["c2"])
    assert run_check(rig)["available"] is False  # stable target == HEAD
    uc.checkout_detach(rig.env.ctx, rig.env.shas["c3"])
    assert run_check(rig)["available"] is False  # stable tag is behind HEAD: never a silent downgrade


def test_check_reports_changelog_and_schema_change(rig):
    rig.release("0.3.0", "v0.3.0", files={"src/stagewatch/version.py": version_py(1, 2),
                                          "CHANGELOG.md": CHANGELOG})
    git(rig.env.clone, "checkout", "-q", "--detach", rig.env.shas["c1"])
    r = run_check(rig)
    assert r["target_version"] == "0.3.0" and r["schema_changed"] is True
    assert r["db_schema"] == [None, 2]
    assert "three" in r["changelog"]


def test_check_same_schema_is_not_a_change(rig):
    first = rig.release("0.2.5", "v0.2.5", files={"src/stagewatch/version.py": version_py(1, 1)})
    uc.fetch_updates(rig.env.ctx)
    uc.checkout_detach(rig.env.ctx, first)
    rig.release("0.2.6", "v0.2.6", files={"src/stagewatch/version.py": version_py(1, 1)})
    r = run_check(rig)
    assert r["target_version"] == "0.2.6" and r["schema_changed"] is False


@pytest.mark.parametrize("files, expect", [
    ({"pyproject.toml": PY_CLEAN + '[tool.uv.sources]\nfoo = { git = "https://e.invalid/f" }\n'},
     "pyproject:sources:foo"),
    ({"pyproject.toml": PY_CLEAN + '[[tool.uv.index]]\nname="p"\nurl="https://e.invalid/s"\n'},
     "pyproject:index:p"),
    ({"uv.lock": LOCK_CLEAN + '[[package]]\nname="evil"\nsource = { git = "https://e.invalid/x" }\n'},
     "lock:git:evil"),
    ({"uv.lock": LOCK_CLEAN + '[[package]]\nname="evil"\nsource = { path = "../x" }\n'}, "lock:path:evil"),
])
def test_check_refuses_added_dependency_sources(rig, files, expect):
    rig.release("0.3.0", "v0.3.0", files=files)
    with pytest.raises(uc.UpdaterError) as ei:
        run_check(rig)
    assert ei.value.category == "dependency_sources" and expect in ei.value.detail


def test_check_allows_dependency_sources_already_present(rig):
    files = {"pyproject.toml": PY_CLEAN + '[tool.uv.sources]\nfoo = { git = "https://e.invalid/f" }\n'}
    rig.release("0.2.5", "v0.2.5", files=files)
    uc.checkout_detach(rig.env.ctx, rig.env.shas["c2"])
    uc.fetch_updates(rig.env.ctx)
    uc.checkout_detach(rig.env.ctx, uc.resolve_ref(rig.env.ctx, "refs/tags/v0.2.5"))
    rig.release("0.2.6", "v0.2.6", files=files)  # the source is already in the running version
    assert run_check(rig)["target_version"] == "0.2.6"


def test_check_refuses_missing_lock_and_python_change(rig):
    rig.release("0.3.0", "v0.3.0", files={"pyproject.toml": PY_CLEAN.replace(">=3.12", ">=3.13")})
    with pytest.raises(uc.UpdaterError) as ei:
        run_check(rig)
    assert ei.value.category == "python_changed"
    git(rig.env.work, "rm", "-q", "uv.lock")
    git(rig.env.work, "commit", "-q", "-m", "drop lock")
    git(rig.env.work, "tag", "-a", "v0.4.0", "-m", "v0.4.0")
    rig.env.push("main", "--tags")
    with pytest.raises(uc.UpdaterError) as ei:
        run_check(rig)
    assert ei.value.category == "lock_missing"


def test_check_refusals_dirty_origin_moved_tag(rig):
    p = rig.env.clone / "src" / "stagewatch" / "__init__.py"
    original = p.read_text()
    p.write_text("local edit")
    with pytest.raises(uc.UpdaterError) as ei:
        run_check(rig)
    assert ei.value.category == "dirty_tree"
    p.write_text(original)

    git(rig.env.clone, "remote", "set-url", "origin", "file:///elsewhere.git")
    with pytest.raises(uc.UpdaterError) as ei:
        run_check(rig)
    assert ei.value.category == "origin_mismatch"
    git(rig.env.clone, "remote", "set-url", "origin", str(rig.env.bare))

    git(rig.env.work, "tag", "-f", "-a", "v0.2.0", "-m", "moved", rig.env.shas["c3"])
    git(rig.env.work, "push", "-q", "-f", "origin", "refs/tags/v0.2.0")
    with pytest.raises(uc.UpdaterError) as ei:
        run_check(rig)
    assert ei.value.category == "fetch_rejected"


async def test_check_offline_reports_unreachable_result(rig, tmp_path):
    rig.env.bare.rename(tmp_path / "gone.git")
    res = await rig.updater.check()
    assert res["ok"] is False and res["category"] == "unreachable"
    assert res["message"] == "Update source not reachable (repository is private or offline)."
    assert (await rig.updater.status())["update_available"] is False


# ---------------------------------------------------------------------------
# updater actions
# ---------------------------------------------------------------------------

async def test_check_rate_limit_and_busy(rig):
    rig.updater.check_interval_s = 30
    await rig.updater.check()
    with pytest.raises(up.RateLimited) as ei:
        await rig.updater.check()
    assert 1 <= ei.value.retry_after <= 30

    rig.updater.check_interval_s = 0
    gate, entered = threading.Event(), threading.Event()
    real = up.run_check

    def slow(*a, **k):
        entered.set()
        gate.wait(10)
        return real(*a, **k)

    up.run_check = slow
    try:
        task = asyncio.create_task(rig.updater.check())
        await asyncio.to_thread(entered.wait, 10)
        with pytest.raises(uc.UpdaterError) as ei:
            await rig.updater.check()
        assert ei.value.category == "busy"
        with pytest.raises(uc.UpdaterError) as ei:
            await rig.updater.update("stable", "a" * 40)
        assert ei.value.category == "busy"
        gate.set()
        assert (await task)["ok"]
    finally:
        up.run_check = real
        gate.set()


async def test_update_writes_pending_sets_exit_code_and_logs(rig):
    res = await rig.updater.check()
    out = await rig.updater.update("stable", res["target_sha"])
    assert out["restarting"] and (out["from_version"], out["to_version"]) == ("0.1.0", "0.2.0")
    p = rig.pending()
    assert p == {"format": 1, "action": "update", "from_sha": rig.env.shas["c1"], "to_sha": rig.env.shas["c2"],
                 "backup_id": None, "schema_changed": False, "channel": "stable"}
    assert rig.hub.exit_code == uc.EXIT_APPLY
    await asyncio.sleep(up.SHUTDOWN_DELAY_S + 0.5)
    assert rig.shutdowns == [1]
    labels = [m.label for m in rig.hub.recorder.markers()]
    assert "Updating software 0.1.0 → 0.2.0" in labels
    assert any(a["event"] == "update" and a["message"] == "0.1.0 → 0.2.0" for a in rig.hub.recorder.alarm_log())
    with pytest.raises(uc.UpdaterError) as ei:  # already on its way down
        await rig.updater.check()
    assert ei.value.category == "restarting"


async def test_update_with_schema_change_makes_a_verified_backup(rig):
    rig.release("0.3.0", "v0.3.0", files={"src/stagewatch/version.py": version_py(1, 2)})
    (rig.env.data / "config.yaml").write_text("site: x\n")
    res = await rig.updater.check()
    await rig.updater.update("stable", res["target_sha"])
    p = rig.pending()
    assert p["schema_changed"] is True and p["backup_id"]
    from stagewatch import backup as bk
    bk.verify_backup(rig.env.data, rig.env.marker().state_dir, p["backup_id"])
    status = await rig.updater.status()
    assert status["backups"][0]["id"] == p["backup_id"] and status["backups"][0]["size"] > 0


async def test_update_reverifies_the_target(rig):
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.update("stable", "a" * 40)
    assert ei.value.category == "no_check"
    res = await rig.updater.check()
    for channel, sha in (("stable", "b" * 40), ("nightly", res["target_sha"])):
        with pytest.raises(uc.UpdaterError) as ei:
            await rig.updater.update(channel, sha)
        assert ei.value.category == "target_mismatch"
    # local edit after the check: refused right before scheduling
    (rig.env.clone / "src" / "stagewatch" / "__init__.py").write_text("edit")
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.update("stable", res["target_sha"])
    assert ei.value.category == "dirty_tree"
    assert not uc.pending_path(rig.env.marker().state_dir).exists() and rig.hub.exit_code == 0


async def test_update_refused_if_checkout_moved_since_check(rig):
    res = await rig.updater.check()
    uc.checkout_detach(rig.env.ctx, rig.env.shas["c2"])
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.update("stable", res["target_sha"])
    assert ei.value.category == "stale_check"


async def test_channel_is_a_config_request_and_invalidates_the_check(rig):
    marker_before = rig.env.marker_path.read_bytes()
    await rig.updater.check()
    assert await rig.updater.set_channel("nightly") == "nightly"
    assert rig.hub.config.updater.channel == "nightly" and rig.updater.last_check is None
    res = await rig.updater.check()
    assert res["channel"] == "nightly" and res["target_sha"] == rig.env.shas["c3"]
    assert rig.env.marker_path.read_bytes() == marker_before  # the admin-only marker is never rewritten
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.set_channel("main")
    assert ei.value.category == "bad_channel"


def _ok_history(rig, **kw):
    e = {"action": "update", "result": "ok", "from_sha": rig.env.shas["c1"], "to_sha": rig.env.shas["c2"],
         "backup_id": None, "schema_changed": False, "channel": "stable",
         "from_version": "0.1.0", "to_version": "0.2.0"}
    e.update(kw)
    return uc.append_history(rig.env.marker().state_dir, e)


async def test_rollback_schedules_pending_from_history(rig):
    uc.checkout_detach(rig.env.ctx, rig.env.shas["c2"])
    h = _ok_history(rig)
    st = await rig.updater.status()
    assert st["history"][0]["can_rollback"] is True
    # short commits let the UI tell apart two builds with the same version number (Nightly)
    assert st["history"][0]["from_commit"] == rig.env.shas["c1"][:7]
    assert st["history"][0]["to_commit"] == rig.env.shas["c2"][:7]
    out = await rig.updater.rollback(h["id"])
    assert out["restarting"] and out["to_version"] == "0.1.0"
    assert rig.pending() == {"format": 1, "action": "rollback", "from_sha": rig.env.shas["c2"],
                             "to_sha": rig.env.shas["c1"], "backup_id": None, "schema_changed": False,
                             "channel": "stable", "history_id": h["id"]}
    assert rig.hub.exit_code == uc.EXIT_APPLY
    assert "Rolling back software 0.2.0 → 0.1.0" in [m.label for m in rig.hub.recorder.markers()]


async def test_roll_back_offered_on_newest_matching_entry_only(rig):
    # update -> rollback -> update again leaves two ok updates ending at the same HEAD
    uc.checkout_detach(rig.env.ctx, rig.env.shas["c2"])
    old = _ok_history(rig)
    uc.append_history(rig.env.marker().state_dir, {"action": "rollback", "result": "ok",
                                                   "from_sha": rig.env.shas["c2"], "to_sha": rig.env.shas["c1"]})
    new = _ok_history(rig)
    st = await rig.updater.status()
    flags = {e["id"]: e["can_rollback"] for e in st["history"]}
    assert flags[new["id"]] is True and flags[old["id"]] is False
    assert sum(flags.values()) == 1


async def test_rollback_refusals(rig):
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.rollback(7)
    assert ei.value.category == "history_not_found"
    h = _ok_history(rig)  # HEAD is still c1, not the entry's to_sha
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.rollback(h["id"])
    assert ei.value.category == "rollback_unavailable"
    uc.checkout_detach(rig.env.ctx, rig.env.shas["c2"])
    h2 = _ok_history(rig, result="rolled_back")
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.rollback(h2["id"])
    assert ei.value.category == "rollback_unavailable"
    h3 = _ok_history(rig, schema_changed=True, backup_id="nope")  # its backup does not exist
    with pytest.raises(uc.UpdaterError) as ei:
        await rig.updater.rollback(h3["id"])
    assert ei.value.category in ("backup_missing", "backup_invalid")


def test_announce_last_result_logs_once(rig):
    sd = rig.env.marker().state_dir
    uc.append_history(sd, {"action": "update", "result": "ok", "from_version": "0.1.0", "to_version": "0.2.0"})
    rig.updater.announce_last_result()
    rig.updater.announce_last_result()  # idempotent
    labels = [m.label for m in rig.hub.recorder.markers()]
    assert labels.count("Software updated to 0.2.0") == 1
    uc.append_history(sd, {"action": "update", "result": "rolled_back", "reason": "health_check_failed",
                           "from_version": "0.2.0", "to_version": "0.3.0"})
    rig.updater.announce_last_result()
    assert any(a["event"] == "update_failed" for a in rig.hub.recorder.alarm_log())
    uc.append_history(sd, {"action": "rollback", "result": "ok", "from_version": "0.2.0", "to_version": "0.1.0"})
    rig.updater.announce_last_result()
    assert "Rolled back to 0.1.0" in [m.label for m in rig.hub.recorder.markers()]


def test_detect_requires_marker_and_supervision(tmp_path):
    r = Rig(tmp_path)
    hub = r.hub
    repo = r.env.clone
    env_ok = {uc.ENV_SUPERVISED: "1", uc.ENV_STATE_DIR: str(r.env.marker().state_dir)}
    # marker_valid needs an https origin by default: the fake remote is a local path
    d = up.Updater.detect(hub, env=env_ok, repo=repo)
    assert not d.managed and d.reason == "marker_invalid" and not d.mutable
    d = up.Updater.detect(hub, env={}, repo=tmp_path / "nowhere")
    assert not d.managed and d.reason == "no_marker"
    uc.write_marker(repo / ".git" / uc.MARKER_NAME, {
        "repo_path": str(repo), "origin_url": "https://example.invalid/x.git", "git_path": str(r.env.ctx.git_path),
        "uv_path": str(r.env.ctx.git_path), "data_dir": str(r.env.data)})
    d = up.Updater.detect(hub, env=env_ok, repo=repo)
    assert d.managed and d.supervised and d.mutable
    d = up.Updater.detect(hub, env={}, repo=repo)
    assert d.managed and not d.supervised and not d.mutable and d.reason == "not_supervised"
    other = Hub(tmp_path / "otherdata", emulate=True)
    assert up.Updater.detect(other, env=env_ok, repo=repo).reason == "data_dir_mismatch"
    other.recorder.close()
    hub.recorder.close()


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@pytest.fixture()
def api(rig):
    with TestClient(create_app(rig.hub, manage_hub=False, updater=rig.updater)) as c:
        assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
        c.rig = rig
        yield c


def post_update(api, sha, pin=PIN, channel="stable"):
    return api.post("/api/admin/software/update", json={"channel": channel, "target_sha": sha, "pin": pin})


def test_endpoints_require_admin_and_same_origin(rig):
    with TestClient(create_app(rig.hub, manage_hub=False, updater=rig.updater)) as c:
        for method, url, body in (("get", "/api/admin/software", None),
                                  ("post", "/api/admin/software/check", None),
                                  ("put", "/api/admin/software/channel", {"channel": "stable"}),
                                  ("post", "/api/admin/software/update",
                                   {"channel": "stable", "target_sha": "a" * 40, "pin": "x"}),
                                  ("post", "/api/admin/software/rollback", {"history_id": 0, "pin": "x"})):
            kw = {"json": body} if body is not None else {}
            assert getattr(c, method)(url, **kw).status_code == 401, url
        assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
        evil = {"origin": "http://evil.example"}
        assert c.post("/api/admin/software/check", headers=evil).status_code == 403
        assert c.put("/api/admin/software/channel", json={"channel": "nightly"}, headers=evil).status_code == 403
        assert c.post("/api/admin/software/update", headers=evil,
                      json={"channel": "stable", "target_sha": "a" * 40, "pin": PIN}).status_code == 403
        assert c.post("/api/admin/software/rollback", headers=evil,
                      json={"history_id": 0, "pin": PIN}).status_code == 403


def test_status_shape(api):
    s = api.get("/api/admin/software").json()
    assert s["managed"] and s["supervised"] and s["mutable"] and s["channel"] == "stable"
    assert s["commit_full"] == api.rig.env.shas["c1"] and s["last_check"] is None
    assert s["update_available"] is False and s["job"] == {"running": False, "kind": None}
    info = api.get("/api/info").json()["build"]
    assert info["managed"] is True and info["supervised"] is True and info["channel"] == "stable"
    assert "describe" in info


def test_not_managed_is_read_only(rig, tmp_path):
    plain = Hub(tmp_path / "plain", emulate=True)
    with TestClient(create_app(plain, manage_hub=False, updater=up.Updater(plain, None, False, "no_marker"))) as c:
        c.post("/api/admin/setup", json={"pin": PIN})
        s = c.get("/api/admin/software").json()
        assert s["managed"] is False and s["mutable"] is False
        assert s["message"] == "In-app updates are only available on a managed install."
        assert s["version"] and s["describe"]
        for method, url, body in (("post", "/api/admin/software/check", None),
                                  ("put", "/api/admin/software/channel", {"channel": "nightly"}),
                                  ("post", "/api/admin/software/update",
                                   {"channel": "stable", "target_sha": "a" * 40, "pin": PIN}),
                                  ("post", "/api/admin/software/rollback", {"history_id": 0, "pin": PIN})):
            kw = {"json": body} if body is not None else {}
            r = getattr(c, method)(url, **kw)
            assert r.status_code == 409 and r.json()["category"] == "not_managed", url
    plain.recorder.close()


def test_check_endpoint_then_rate_limit(api):
    api.rig.updater.check_interval_s = 30
    r = api.post("/api/admin/software/check")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["available"] and body["target_sha"] == api.rig.env.shas["c2"]
    assert api.get("/api/admin/software").json()["update_available"] is True
    r = api.post("/api/admin/software/check")
    assert r.status_code == 429 and r.json()["category"] == "rate_limited"
    assert 1 <= r.json()["retry_after"] <= 30 and int(r.headers["retry-after"]) >= 1


def test_check_endpoint_409_while_a_job_runs(api):
    class Busy:
        def locked(self):
            return True

    api.rig.updater._lock = Busy()
    r = api.post("/api/admin/software/check")
    assert r.status_code == 409 and r.json()["category"] == "busy"


def test_update_endpoint_validation_pin_and_target(api):
    sha = api.post("/api/admin/software/check").json()["target_sha"]
    assert post_update(api, "not-a-sha").status_code == 422
    assert post_update(api, "A" * 40).status_code == 422  # lowercase hex only
    assert api.post("/api/admin/software/update", json={"channel": "main", "target_sha": sha, "pin": PIN}).status_code == 422
    r = post_update(api, "b" * 40)  # right shape, not the checked target
    assert r.status_code == 409 and r.json()["category"] == "target_mismatch"
    assert api.rig.hub.exit_code == 0
    r = post_update(api, sha, pin="9999")
    assert r.status_code == 401 and "pending" not in str(r.json())
    assert api.rig.hub.exit_code == 0 and not uc.pending_path(api.rig.env.marker().state_dir).exists()
    r = post_update(api, sha)
    assert r.status_code == 200 and r.json()["restarting"] is True
    assert api.rig.hub.exit_code == uc.EXIT_APPLY
    assert api.rig.pending()["to_sha"] == sha


def test_wrong_pins_share_the_login_rate_limiter(api):
    sha = api.post("/api/admin/software/check").json()["target_sha"]
    for _ in range(5):
        assert post_update(api, sha, pin="0000").status_code == 401
    assert post_update(api, sha, pin=PIN).status_code == 429  # locked out, even with the right PIN
    assert api.post("/api/admin/login", json={"pin": PIN}).status_code == 429


def test_rollback_endpoint_pin_and_flow(api):
    uc.checkout_detach(api.rig.env.ctx, api.rig.env.shas["c2"])
    h = _ok_history(api.rig)
    assert api.post("/api/admin/software/rollback", json={"history_id": h["id"], "pin": "0000"}).status_code == 401
    assert api.post("/api/admin/software/rollback", json={"history_id": -1, "pin": PIN}).status_code == 422
    r = api.post("/api/admin/software/rollback", json={"history_id": 99, "pin": PIN})
    assert r.status_code == 404 and r.json()["category"] == "history_not_found"
    r = api.post("/api/admin/software/rollback", json={"history_id": h["id"], "pin": PIN})
    assert r.status_code == 200 and api.rig.pending()["action"] == "rollback"


def test_channel_endpoint(api):
    assert api.put("/api/admin/software/channel", json={"channel": "main"}).status_code == 422
    r = api.put("/api/admin/software/channel", json={"channel": "nightly"})
    assert r.status_code == 200 and r.json() == {"channel": "nightly"}
    assert api.get("/api/admin/software").json()["channel"] == "nightly"
    assert api.post("/api/admin/software/check").json()["target_sha"] == api.rig.env.shas["c3"]


def test_errors_never_leak_paths_or_stderr(api, tmp_path):
    api.rig.env.bare.rename(tmp_path / "gone.git")
    body = api.post("/api/admin/software/check").text
    assert str(tmp_path).replace("\\", "/") not in body.replace("\\\\", "/").replace("\\", "/")
    assert "fatal" not in body.lower()


# ---------------------------------------------------------------------------
# request-body cap
# ---------------------------------------------------------------------------

def _echo_app(limit=1000, overrides=None):
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, default_limit=limit, overrides=overrides)

    @app.post("/echo")
    async def echo(request: dict):
        return {"n": len(str(request))}

    @app.post("/big")
    async def big(request: dict):
        return {"ok": True}

    return app


def test_body_cap_content_length_and_stream():
    c = TestClient(_echo_app(limit=1000, overrides={"/big": 5000}))
    assert c.post("/echo", json={"a": "x" * 100}).status_code == 200
    r = c.post("/echo", json={"a": "x" * 2000})
    assert r.status_code == 413
    # no Content-Length: chunked stream is capped as it arrives
    def gen():
        for _ in range(10):
            yield b'{"a": "' + b"x" * 200 + b'"}'
    r = c.post("/echo", content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert c.post("/big", json={"a": "x" * 2000}).status_code == 200  # per-route override
    assert c.post("/big", json={"a": "x" * 6000}).status_code == 413
    assert c.get("/echo").status_code == 405  # no body: untouched


def test_global_cap_applies_to_the_real_app(api):
    r = api.post("/api/admin/login", content=b"{" + b" " * (64 * 1024 + 1) + b"}",
                 headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert api.put("/api/admin/thresholds", json=[{"id": "t", "entity": "site.temperature", "label": "x" * 70000}]
                   ).status_code == 413
    assert api.post("/api/admin/software/check").status_code == 200  # ordinary requests unaffected


# ---------------------------------------------------------------------------
# QA fixes: L1 not_ancestor, L3 markers, L4 lock sources, L2 drain, re-created tag naming
# ---------------------------------------------------------------------------

def _push_nightly_off_main(rig):
    git(rig.env.work, "checkout", "-q", "-b", "evil", rig.env.shas["c1"])
    commit(rig.env.work, "6.6.6", message="not on main")
    git(rig.env.work, "push", "-q", "-f", "origin", "evil:refs/heads/nightly")


def test_check_nightly_off_main_is_not_ancestor_not_up_to_date(rig):
    _push_nightly_off_main(rig)
    with pytest.raises(uc.UpdaterError) as ei:
        run_check(rig, "nightly")
    assert ei.value.category == "not_ancestor"


async def test_check_result_for_nightly_off_main_is_an_error_result(rig):
    _push_nightly_off_main(rig)
    await rig.updater.set_channel("nightly")
    res = await rig.updater.check()
    assert res["ok"] is False and res["category"] == "not_ancestor" and res["available"] is False
    assert "main branch history" in res["message"]


def test_announce_refused_and_rejected_close_the_marker(rig):
    sd = rig.env.marker().state_dir
    uc.append_history(sd, {"action": "update", "result": "refused", "reason": "unsafe_permissions",
                           "from_version": "0.1.0", "to_version": "0.2.0",
                           "detail": "C:\\secret\\path"})
    uc.append_history(sd, {"action": "rollback", "result": "rejected", "reason": "pending_invalid"})
    rig.updater.announce_last_result()
    labels = [m.label for m in rig.hub.recorder.markers()]
    assert any("update refused (0.1.0 → 0.2.0): unsafe_permissions" in x for x in labels)
    assert any("rollback rejected" in x and "pending_invalid" in x for x in labels)
    assert not any("secret" in x or "\\" in x for x in labels)


def test_dependency_findings_editable_virtual_and_hosts():
    py = '[project]\nname="x"\n'
    ok = ('version = 1\n[[package]]\nname="x"\nsource = { editable = "." }\n'
          '[[package]]\nname="a"\nsource = { registry = "https://pypi.org/simple" }\n'
          'sdist = { url = "https://files.pythonhosted.org/packages/a.tar.gz", hash = "sha256:00" }\n'
          'wheels = [{ url = "https://files.pythonhosted.org/packages/a.whl", hash = "sha256:00" }]\n')
    assert up.dependency_source_findings(py, ok) == set()  # the project itself is fine
    assert up.dependency_source_findings(py, ok.replace("editable", "virtual")) == set()
    assert up.dependency_source_findings(py, ok + '[[package]]\nname="e"\nsource = { editable = "../e" }\n') \
        == {"lock:editable:e"}
    assert up.dependency_source_findings(py, ok + '[[package]]\nname="v"\nsource = { virtual = "v" }\n') \
        == {"lock:virtual:v"}
    assert up.dependency_source_findings(py, ok.replace('editable = "."', 'editable = "../x"')) \
        == {"lock:editable:x"}
    evil = ok.replace("files.pythonhosted.org/packages/a.whl", "evil.invalid/a.whl")
    assert up.dependency_source_findings(py, evil) == {"lock:url_host:a"}
    evil = ok.replace("files.pythonhosted.org/packages/a.tar.gz", "evil.invalid/a.tar.gz")
    assert up.dependency_source_findings(py, evil) == {"lock:url_host:a"}


def test_dependency_findings_pass_on_the_real_repo_lock():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert up.dependency_source_findings((root / "pyproject.toml").read_text(encoding="utf-8"),
                                         (root / "uv.lock").read_text(encoding="utf-8")) == set()


def test_body_cap_drains_a_bounded_amount_before_413():
    from stagewatch.web import limits

    async def scenario(chunks, chunk_size, declared):
        events = []
        queue = [{"type": "http.request", "body": b"x" * chunk_size, "more_body": i < chunks - 1}
                 for i in range(chunks)]

        async def receive():
            if queue:
                events.append("recv")
                return queue.pop(0)
            await asyncio.sleep(10)

        async def send(msg):
            events.append(msg["type"])

        async def app(scope, receive, send):
            raise AssertionError("must not reach the app")

        mw = BodySizeLimitMiddleware(app, default_limit=100)
        scope = {"type": "http", "path": "/x", "headers": [(b"content-length", str(declared).encode())]}
        await mw(scope, receive, send)
        return events, len(queue)

    events, left = asyncio.run(scenario(5, 1000, 5000))
    assert left == 0  # the whole (small) body was consumed ...
    assert events.index("http.response.start") > max(i for i, e in enumerate(events) if e == "recv")  # ... first
    big_chunks = limits.DRAIN_MAX_BYTES // 65536 + 20
    events, left = asyncio.run(scenario(big_chunks, 65536, big_chunks * 65536))
    assert left > 0  # beyond the bound it stops reading (and closes) instead of draining forever
    assert "http.response.start" in events


async def test_moved_tag_is_named_in_check_result_and_log(rig, caplog):
    git(rig.env.work, "tag", "-f", "-a", "v0.2.0", "-m", "moved", rig.env.shas["c3"])
    git(rig.env.work, "push", "-q", "-f", "origin", "refs/tags/v0.2.0")
    res = await rig.updater.check()
    assert res["category"] == "fetch_rejected" and "v0.2.0" in res["message"]
    assert str(rig.env.clone) not in res["message"] and str(rig.env.bare) not in res["message"]
    assert "v0.2.0" in caplog.text
    # recovery documented in the README: delete the local tag, then it works again
    git(rig.env.clone, "tag", "-d", "v0.2.0")
    rig.updater._last_check_at = None
    res = await rig.updater.check()
    assert res["ok"] is True
