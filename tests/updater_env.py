"""Test helpers: a fake bare 'origin' + a managed clone, no network."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from stagewatch import updater_common as uc

GIT = shutil.which("git")

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
}


def git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_ENV)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    cp = subprocess.run([GIT, "-c", "protocol.file.allow=always", *args], cwd=str(cwd), env=env,
                        capture_output=True, text=True, check=True)
    return cp.stdout.strip()


DEFAULT_PYPROJECT = '[project]\nname = "x"\nversion = "0"\nrequires-python = ">=3.12"\ndependencies = []\n'
DEFAULT_LOCK = ('version = 1\n\n[[package]]\nname = "requests"\nversion = "1"\n'
                'source = { registry = "https://pypi.org/simple" }\n')


def commit(work: Path, version: str, mode: str = "good", message: str | None = None,
           files: dict | None = None) -> str:
    """Commit a fake release.  ``files`` maps repo-relative paths to text (overrides the defaults;
    pyproject.toml/uv.lock persist from earlier commits unless given)."""
    files = files or {}
    (work / "src" / "stagewatch").mkdir(parents=True, exist_ok=True)
    (work / "src" / "stagewatch" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (work / "mode.txt").write_text(mode)
    for rel, text in {"pyproject.toml": DEFAULT_PYPROJECT, "uv.lock": DEFAULT_LOCK, **files}.items():
        if rel not in files and (work / rel).exists():
            continue
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", message or f"v{version} {mode}")
    return git(work, "rev-parse", "HEAD")


@dataclass
class Env:
    root: Path
    bare: Path
    work: Path
    clone: Path
    data: Path
    shas: dict

    @property
    def ctx(self) -> uc.GitContext:
        return uc.GitContext(self.clone, GIT, uc.ensure_state_dir(self.clone), ("file",))

    @property
    def marker_path(self) -> Path:
        return self.clone / ".git" / uc.MARKER_NAME

    def marker(self) -> uc.Marker:
        return uc.load_marker(self.marker_path, require_https=False)

    def push(self, *refspecs: str) -> None:
        git(self.work, "push", "-q", "origin", *refspecs)


def make_env(root: Path) -> Env:
    """origin: main = c1(v0.1.0) -> c2(v0.2.0) -> c3 ; nightly = c3.  clone: detached at v0.1.0."""
    bare, work, clone, data = root / "origin.git", root / "work", root / "clone", root / "data"
    git(root, "init", "-q", "--bare", "-b", "main", str(bare))
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "remote", "add", "origin", str(bare))
    shas = {"c1": commit(work, "0.1.0")}
    git(work, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    shas["c2"] = commit(work, "0.2.0")
    git(work, "tag", "-a", "v0.2.0", "-m", "v0.2.0")
    shas["c3"] = commit(work, "0.2.1-dev", message="post-release work")
    git(work, "branch", "nightly")
    git(work, "push", "-q", "origin", "main", "nightly", "--tags")
    git(root, "clone", "-q", str(bare), str(clone))
    git(clone, "checkout", "-q", "--detach", shas["c1"])
    data.mkdir()
    uc.ensure_state_dir(clone)
    uc.write_marker(clone / ".git" / uc.MARKER_NAME, {
        "repo_path": str(clone), "origin_url": str(bare), "git_path": GIT, "uv_path": sys.executable,
        "uv_env": {"UV_CACHE_DIR": str(root / "uvcache")}, "data_dir": str(data),
        "channel": "stable", "port": 18080, "created": "2026-01-01T00:00:00Z"})
    return Env(root, bare, work, clone, data, shas)
