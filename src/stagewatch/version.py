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
import platform
import subprocess
import sys
from pathlib import Path

from . import __version__

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


@functools.lru_cache(maxsize=1)
def build_info() -> dict:
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no")) if commit else False
    return {
        "version": __version__,
        "commit": commit or "unknown",
        "dirty": dirty,
        "config_schema": CONFIG_SCHEMA_VERSION,
        "db_schema": DB_SCHEMA_VERSION,
        "python": platform.python_version(),
        "platform": sys.platform,
    }


def version_string() -> str:
    info = build_info()
    suffix = "" if info["commit"] == "unknown" else f"+{info['commit']}{'.dirty' if info['dirty'] else ''}"
    return f"{info['version']}{suffix}"
