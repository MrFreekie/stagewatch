"""Refuse to commit secrets, runtime data or local AI/planning documents.

Run by the pre-commit hook (.githooks/pre-commit) against staged files, and
usable by hand:  uv run python scripts/check_secrets.py [--all]
Exit code 1 lists every problem found.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]

BLOCKED_PATHS = [
    re.compile(p) for p in (
        r"(^|/)data/", r"(^|/)config\.ya?ml$", r"(^|/)secret\.key$", r"\.key$", r"\.pem$",
        r"(^|/)\.env(\..*)?$", r"\.sqlite3(-.*)?$", r"(^|/)secrets\.ya?ml$",
        r"(^|/)CLAUDE(\.local)?\.md$", r"(^|/)\.claude/", r"(^|/)plans/", r"\.plan\.md$",
    )
]
# Allow-list for files whose names look risky but are safe templates.
ALLOWED_PATHS = [re.compile(p) for p in (r"^esphome/secrets\.example\.yaml$",)]

SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("ESPHome API key", re.compile(r"\bkey:\s*[\"']?[A-Za-z0-9+/]{43}=[\"']?")),
    ("admin PIN hash", re.compile(r"pbkdf2_sha256\$\d+\$")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Wi-Fi password", re.compile(r"^\s*password:\s*[\"'](?!!secret)[^\"']{6,}[\"']", re.M)),
]


def staged_files() -> list[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def all_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    return [line for line in out.splitlines() if line]


def staged_content(path: str, staged: bool) -> str:
    if staged:
        res = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True)
        data = res.stdout
    else:
        data = (ROOT / path).read_bytes()
    if b"\0" in data[:4096]:
        return ""  # binary
    return data.decode("utf-8", errors="ignore")


def check(paths: list[str], staged: bool) -> list[str]:
    problems = []
    for path in paths:
        posix = str(PurePosixPath(path))
        if any(a.search(posix) for a in ALLOWED_PATHS):
            continue
        if any(b.search(posix) for b in BLOCKED_PATHS):
            problems.append(f"{posix}: file type must never be committed")
            continue
        if posix == "scripts/check_secrets.py" or posix.startswith("tests/"):
            continue
        text = staged_content(path, staged)
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"{posix}: looks like it contains a {name}")
    return problems


def main() -> int:
    use_all = "--all" in sys.argv
    problems = check(all_files() if use_all else staged_files(), staged=not use_all)
    if problems:
        print("Commit blocked: possible secrets or local-only files:\n  " + "\n  ".join(problems))
        print("Unstage them (git restore --staged <file>) or move secrets to a gitignored file.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
