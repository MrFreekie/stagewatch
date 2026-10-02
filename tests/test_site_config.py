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
    monkeypatch.setattr(site_config, "ensure_private", lambda remote: None)
    site_config.cmd_push(work, "test")
    assert (work / ".gitignore").read_text(encoding="utf-8") == site_config.GITIGNORE
    files = _git(work, "ls-files").stdout.split()
    assert "config.yaml" in files and "secret.key" not in files
