"""Every static .js file must parse. The web UI has no build step, so a stray quote would otherwise
only show up as a blank page in the browser."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "stagewatch" / "web" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH, so JavaScript cannot be syntax-checked")


def syntax_error(path: Path) -> str | None:
    """Node's message if the file does not parse, else None."""
    r = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    return None if r.returncode == 0 else (r.stderr or r.stdout).strip()


@needs_node
@pytest.mark.parametrize("path", sorted(STATIC.rglob("*.js")), ids=lambda p: p.name)
def test_static_js_parses(path):
    error = syntax_error(path)
    assert error is None, f"{path.name} does not parse:\n{error}"


@needs_node
def test_syntax_check_catches_a_broken_file(tmp_path):
    good = tmp_path / "good.js"
    good.write_text('const a = "fine";\n', encoding="utf-8")
    bad = tmp_path / "bad.js"
    bad.write_text('const a = "a "quoted" word";\n', encoding="utf-8")
    assert syntax_error(good) is None
    assert "SyntaxError" in syntax_error(bad)
