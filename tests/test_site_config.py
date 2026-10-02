"""site_config allow-list: checked with real `git check-ignore` on a temp repo."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "site_config", Path(__file__).resolve().parents[1] / "scripts" / "site_config.py")
site_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(site_config)

TRACKED = ["config.yaml", "README.md", "branding/events/1/logo-light.png", "branding/events/1/logo-dark.webp",
           "contacts/1.yaml", "documents/1/abc/r3.md", "documents/1/index.json",
           "documents/templates/xyz/r1.md", "documents/templates/index.json"]
IGNORED = ["config.yaml.bak", "contacts/.contacts-x.yaml", ".config-abc.yaml", "emulate/config.yaml",
           "emulate/contacts/1.yaml", "backups/x/contacts/1.yaml", "secret.key", "stagewatch.sqlite3",
           "stagewatch.sqlite3-wal", "stagewatch.sqlite3-shm", "logs/stagewatch.log",
           "documents/1/abc/r3.md.tmp", "documents/1/.documents-x.json", "config.invalid-1.yaml",
           "stagewatch.sqlite3.pre-v2-20260101T000000Z.bak", "updater.json", "branding/events/1/notes.txt"]


def _git(d, *a, check=True):
    return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    site_config.refresh_gitignore(tmp_path)
    return tmp_path


def _ignored(repo, rel):
    return _git(repo, "check-ignore", "-q", rel, check=False).returncode == 0


@pytest.mark.parametrize("rel", TRACKED)
def test_tracked(repo, rel):
    assert not _ignored(repo, rel)


@pytest.mark.parametrize("rel", IGNORED)
def test_ignored(repo, rel):
    assert _ignored(repo, rel)


def test_real_files_are_staged_only_when_allowed(repo):
    for rel in TRACKED + IGNORED:
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    staged = set(_git(repo, "ls-files").stdout.split()) - {".gitignore"}
    assert staged == set(TRACKED)


def test_refresh_rewrites_tampered_gitignore_and_untracks(repo):
    (repo / ".gitignore").write_text("# nothing ignored\n", encoding="utf-8")
    (repo / "config.yaml.bak").write_text("pin", encoding="utf-8")
    _git(repo, "add", "-A")
    assert "config.yaml.bak" in _git(repo, "ls-files").stdout
    site_config.refresh_gitignore(repo)
    assert (repo / ".gitignore").read_text(encoding="utf-8") == site_config.GITIGNORE
    assert "config.yaml.bak" not in _git(repo, "ls-files", "--cached").stdout


def test_push_rewrites_tampered_gitignore(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@example.invalid")
    _git(work, "config", "user.name", "t")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-q", "-m", "seed")
    _git(work, "push", "-q", "-u", "origin", "main")
    (work / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    (work / ".gitignore").write_text("# tampered\n", encoding="utf-8")
    (work / "secret.key").write_text("k", encoding="utf-8")
    monkeypatch.setattr(site_config, "ensure_private", lambda *a, **k: None)
    site_config.cmd_push(work, "test")
    assert (work / ".gitignore").read_text(encoding="utf-8") == site_config.GITIGNORE
    files = _git(work, "ls-files").stdout.split()
    assert "config.yaml" in files and "secret.key" not in files


@pytest.mark.parametrize("rel", ["sub/config.yaml", "deep/a/README.md", "contacts/.gitignore",
                                 "sub/.gitignore", "branding/logo-light.png", "contacts/sub/1.yaml"])
def test_allow_rules_are_anchored(repo, rel):
    assert _ignored(repo, rel)


def test_untracks_non_ascii_and_bracket_names(repo):
    (repo / ".gitignore").write_text("# nothing ignored\n", encoding="utf-8")
    names = ["café.sqlite3", "données/secret.key", "we[ird].sqlite3", "plain.sqlite3"]
    for rel in names:
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    site_config.refresh_gitignore(repo)
    tracked = _git(repo, "-c", "core.quotePath=false", "ls-files").stdout.splitlines()
    assert not set(names) & set(tracked)
    assert "config.yaml" not in tracked


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/owner/repo.git", ("owner", "repo")),
    ("https://GitHub.com/owner/my.repo", ("owner", "my.repo")),
    ("https://www.github.com/owner/repo/tree/main", ("owner", "repo")),
    ("https://user@github.com:443/owner/repo.git", ("owner", "repo")),
    ("ssh://git@github.com/owner/repo.git", ("owner", "repo")),
    ("ssh://git@github.com:22/owner/repo.git", ("owner", "repo")),
    ("git@github.com:owner/repo.git", ("owner", "repo")),
    ("github.com:owner/repo", ("owner", "repo")),
    ("https://evil.example/github.com/owner/repo", None),
    ("https://github.com.evil.example/owner/repo", None),
    ("https://github.com@evil.example/owner/repo", None),
    ("git@mygithub-alias:owner/repo.git", None),
    ("https://gitlab.com/owner/repo.git", None),
    ("https://github.com/owner", None),
    ("/some/local/path.git", None),
])
def test_parse_github(url, expected):
    assert site_config.parse_github(url) == expected


def _fake_gh(monkeypatch, private, calls=None):
    real = site_config._run

    def run(cmd, cwd=None, text=True):
        if cmd[0] == "gh":
            if calls is not None:
                calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"private": %s}' % private, stderr="")
        return real(cmd, cwd, text)
    monkeypatch.setattr(site_config, "_run", run)


def test_unrecognised_remote_refused_unless_allowed(monkeypatch, capsys):
    for url in ["https://evil.example/github.com/o/r", "git@alias:o/r.git", "https://gitlab.com/o/r"]:
        with pytest.raises(SystemExit) as e:
            site_config.ensure_private(url)
        assert "REFUSING" in str(e.value)
        site_config.ensure_private(url, allow_unverified=True)
    assert "warning" in capsys.readouterr().out


def test_public_and_unconfirmed_github_refused(monkeypatch):
    _fake_gh(monkeypatch, "false")
    with pytest.raises(SystemExit):
        site_config.ensure_private("https://github.com/o/r.git", allow_unverified=True)
    _fake_gh(monkeypatch, "null")
    with pytest.raises(SystemExit):
        site_config.ensure_private("git@github.com:o/r.git")
    calls = []
    _fake_gh(monkeypatch, "true", calls)
    site_config.ensure_private("https://www.github.com/o/r.dot.git/tree/main")
    assert calls[0][-1] == "repos/o/r.dot"


def test_every_push_url_is_checked(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/o/private-one.git")
    _git(tmp_path, "remote", "set-url", "--push", "--add", "origin", "https://github.com/o/public-one.git")
    seen = []
    monkeypatch.setattr(site_config, "ensure_private", lambda url, allow=False: seen.append(url))
    site_config.check_push_targets(tmp_path, False)
    assert seen == ["https://github.com/o/public-one.git"]
    _git(tmp_path, "remote", "set-url", "--push", "--add", "origin", "https://github.com/o/second.git")
    seen.clear()
    site_config.check_push_targets(tmp_path, False)
    assert len(seen) == 2


def test_public_pushurl_blocks_push(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/o/private-one.git")
    _git(tmp_path, "remote", "set-url", "--push", "origin", "https://github.com/o/public-one.git")
    real = site_config._run

    def run(cmd, cwd=None, text=True):
        if cmd[0] == "gh":
            private = "public-one" not in cmd[-1]
            return subprocess.CompletedProcess(cmd, 0, stdout='{"private": %s}' % str(private).lower(), stderr="")
        return real(cmd, cwd, text)
    monkeypatch.setattr(site_config, "_run", run)
    with pytest.raises(SystemExit) as e:
        site_config.cmd_push(tmp_path, "x")
    assert "PUBLIC" in str(e.value)


def test_init_refuses_to_overwrite_differing_local_files(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.invalid")
    _git(seed, "config", "user.name", "t")
    (seed / "contacts").mkdir()
    (seed / "contacts" / "1.yaml").write_text("remote: 1\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "push", "-q", str(remote), "main")
    monkeypatch.setattr(site_config, "ensure_private", lambda *a, **k: None)

    local = tmp_path / "local"
    (local / "contacts").mkdir(parents=True)
    (local / "contacts" / "1.yaml").write_text("local: 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        site_config.cmd_init(local, str(remote), True)
    assert "contacts/1.yaml" in str(e.value)
    assert (local / "contacts" / "1.yaml").read_text(encoding="utf-8") == "local: 1\n"


def test_missing_git_or_gh_is_a_clear_message(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(site_config.subprocess, "run", boom)
    with pytest.raises(SystemExit) as e:
        site_config.ensure_private("https://github.com/o/r.git")
    assert "gh" in str(e.value) and "not found" in str(e.value)
    with pytest.raises(SystemExit) as e:
        site_config.git(Path("."), "status")
    assert "git" in str(e.value) and "not found" in str(e.value)
