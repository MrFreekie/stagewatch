"""Cut a release: bump the version everywhere, date the changelog, commit, tag.

Usage (from the repo root):
    uv run python scripts/bump_version.py patch      # 0.1.0 -> 0.1.1
    uv run python scripts/bump_version.py minor      # 0.1.0 -> 0.2.0
    uv run python scripts/bump_version.py major      # 0.1.0 -> 1.0.0
    uv run python scripts/bump_version.py 0.3.0      # explicit
Options:
    --dry-run   show what would change, touch nothing
    --no-git    update files only (no commit/tag)

It updates pyproject.toml, src/stagewatch/__init__.py and CHANGELOG.md
(the [Unreleased] entries become "## [X.Y.Z] - YYYY-MM-DD" and the compare
links are updated), refreshes uv.lock, then makes a commit and an annotated
tag vX.Y.Z. It never pushes: review, then `git push --follow-tags`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "stagewatch" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def current_version() -> str:
    m = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("version not found in pyproject.toml")
    return m.group(1)


def next_version(current: str, part: str) -> str:
    if SEMVER.match(part):
        return part
    major, minor, patch = map(int, SEMVER.match(current).groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    sys.exit(f"unknown bump '{part}' (use major/minor/patch or X.Y.Z)")


def git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=check).stdout.strip()


def update_changelog(text: str, old: str, new: str, today: str) -> str:
    head, sep, rest = text.partition("## [Unreleased]")
    if not sep:
        sys.exit("CHANGELOG.md has no '## [Unreleased]' section")
    body, nxt, tail = rest.partition("\n## [")
    if not body.strip():
        sys.exit("[Unreleased] is empty. Add changelog entries before releasing")
    text = (f"{head}## [Unreleased]\n\n## [{new}] - {today}{body.rstrip()}\n\n"
            + (f"## [{tail}" if nxt else ""))
    link_re = re.compile(r"^\[Unreleased\]: (.+)/compare/v[^.\s]+\.[^.\s]+\.[^.\s]+\.\.\.HEAD$", re.M)
    m = link_re.search(text)
    if m:
        base = m.group(1)
        text = link_re.sub(f"[Unreleased]: {base}/compare/v{new}...HEAD\n"
                           f"[{new}]: {base}/compare/v{old}...v{new}", text, count=1)
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-git", action="store_true")
    args = ap.parse_args()

    old = current_version()
    new = next_version(old, args.part)
    today = dt.date.today().isoformat()
    if tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
        sys.exit(f"new version {new} must be greater than {old}")

    if not args.no_git:
        if git("status", "--porcelain", "--untracked-files=no"):
            sys.exit("working tree has uncommitted changes; commit or stash first")
        if git("tag", "--list", f"v{new}"):
            sys.exit(f"tag v{new} already exists")

    py = re.sub(r'^version = "[^"]+"', f'version = "{new}"',
                PYPROJECT.read_text(encoding="utf-8"), count=1, flags=re.M)
    init = re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{new}"',
                  INIT.read_text(encoding="utf-8"), count=1, flags=re.M)
    log = update_changelog(CHANGELOG.read_text(encoding="utf-8"), old, new, today)

    print(f"{old} -> {new} ({today})")
    if args.dry_run:
        print("dry run: no files changed")
        return
    PYPROJECT.write_text(py, encoding="utf-8")
    INIT.write_text(init, encoding="utf-8")
    CHANGELOG.write_text(log, encoding="utf-8")
    subprocess.run(["uv", "lock"], cwd=ROOT, check=False)

    if args.no_git:
        return
    git("add", "pyproject.toml", "uv.lock", "src/stagewatch/__init__.py", "CHANGELOG.md")
    git("commit", "-m", f"Release v{new}")
    git("tag", "-a", f"v{new}", "-m", f"Stagewatch v{new}")
    print(f"Committed and tagged v{new}. Push with: git push --follow-tags")


if __name__ == "__main__":
    main()
