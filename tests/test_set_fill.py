"""Progress and load bars scale (transform) instead of changing width: the node logic test and the CSS."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
STATIC = ROOT.parent / "src" / "stagewatch" / "web" / "static"


def test_set_fill_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "js" / "set_fill_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_bars_scale_and_nothing_sets_a_fill_width():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "transform: scaleX(0); transform-origin: left" in css
    for js in ("dashboard.js", "ontimerundown.js", "ontimetimer.js", "power-tree.js", "power.js"):
        text = (STATIC / js).read_text(encoding="utf-8")
        assert "SW.setFill(" in text and "fill.style.width" not in text, js
