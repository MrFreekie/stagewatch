"""Timeline marker tabs: lane layout, colour by source, colour contrast (tests/js/marker_layout_test.js)."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_marker_layout_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = Path(__file__).resolve().parent / "js" / "marker_layout_test.js"
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
