"""Version and build information shown in the UI, API and logs.

- version: semantic version of the release (MAJOR.MINOR.PATCH).
- commit / dirty: the git commit the server is running from, so a report
  from a show can be traced to exact code. Falls back to "unknown" when not
  running from a git checkout.
- schema versions: bumped whenever the on-disk format of config.yaml or the
  SQLite database changes, with a migration in the matching module.
"""

from __future__ import annotations

import functools
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from . import __version__
from .updater_common import ENV_SUPERVISED, read_head_file

CONFIG_SCHEMA_VERSION = 1
DB_SCHEMA_VERSION = 1

_REPO = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    if not (_REPO / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", *args], cwd=_REPO, capture_output=True, text=True,
                             timeout=3, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


_DESCRIBE_RE = re.compile(r"^v?(?P<tag>\d+\.\d+\.\d+)-(?P<n>\d+)-g(?P<sha>[0-9a-f]+)$")


def parse_describe(text: str | None) -> tuple[str, int, str] | None:
    """``git describe --tags --long`` output ("v0.2.0-14-gabc1234") -> (tag version, commits since, sha)."""
    m = _DESCRIBE_RE.match((text or "").strip())
    return (m.group("tag"), int(m.group("n")), m.group("sha")) if m else None


def format_describe(version: str, commits_since_tag: int | None, commit: str, dirty: bool = False) -> str:
    """SemVer build metadata (never a pre-release suffix, which would sort *before* the release):
    ``0.2.0+abc1234`` on a tag, ``0.2.0+14.gabc1234`` past it, ``.dirty`` appended if modified."""
    if commit in ("", "unknown"):
        return version
    meta = commit if not commits_since_tag else f"{commits_since_tag}.g{commit}"
    return f"{version}+{meta}{'.dirty' if dirty else ''}"


def _marker_channel() -> str | None:
    try:
        d = json.loads((_REPO / ".git" / "stagewatch-managed.json").read_text(encoding="utf-8-sig"))
        return d.get("channel") if d.get("channel") in ("stable", "nightly") else None
    except (OSError, ValueError, AttributeError):
        return None


@functools.lru_cache(maxsize=1)
def build_info() -> dict:
    commit = _git("rev-parse", "--short", "HEAD")
    if not commit:  # no git on PATH (e.g. a SYSTEM service): read a detached HEAD directly
        head = read_head_file(_REPO)
        commit = head[:7] if head else None
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no")) if commit else False
    desc = parse_describe(_git("describe", "--tags", "--long", "--match", "v[0-9]*.[0-9]*.[0-9]*"))
    since = desc[1] if desc else None
    marker = _REPO / ".git" / "stagewatch-managed.json"
    return {
        "version": __version__,
        "commit": commit or "unknown",
        "dirty": dirty,
        "describe": format_describe(__version__, since, commit or "unknown", dirty),
        "commits_since_tag": since,
        "config_schema": CONFIG_SCHEMA_VERSION,
        "db_schema": DB_SCHEMA_VERSION,
        "channel": _marker_channel(),  # the installer's channel; /api/info reports the effective one
        "managed": marker.is_file(),
        "supervised": os.environ.get(ENV_SUPERVISED) == "1",
        "python": platform.python_version(),
        "platform": sys.platform,
    }


def version_string() -> str:
    return build_info()["describe"]
