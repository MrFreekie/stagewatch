"""SW.renderMarkdown (common.js): the safe Markdown subset used for setlists and documents."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_markdown_renderer_in_node():
    """Safety and rendering cases in tests/js/markdown_test.js (a fake DOM in a vm context), when
    node is installed. tests/js/markdown_test.html runs the same idea in a real browser."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = Path(__file__).resolve().parent / "js" / "markdown_test.js"
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
