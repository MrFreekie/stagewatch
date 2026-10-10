"""Header connection indicator on the user dashboards: source checks and the node logic test."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
STATIC = ROOT.parent / "src" / "stagewatch" / "web" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_conn_status_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "js" / "conn_status_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_button_sits_by_the_bell_and_is_accessible():
    html, css = read("dashboard.html"), read("style.css")
    assert html.index('id="msg-btn"') < html.index('id="cs-btn"') < html.index('id="theme-toggle"')
    btn = re.search(r'<button[^>]*id="cs-btn"[^>]*>', html).group(0)
    assert 'type="button"' in btn and 'aria-expanded="false"' in btn and 'aria-controls="cs-panel"' in btn
    assert 'aria-live="polite"' in html and 'id="cs-panel"' in html
    assert re.search(r"\.cs-btn \{[^}]*min-width: 44px; min-height: 44px", css)
    assert ".cs-btn:focus-visible" in css


def test_wall_hides_it_when_online_and_nothing_animates():
    css = read("style.css")
    assert "body.layout-wall .cs-btn.cs-ok { display: none; }" in css
    cs = "\n".join(line for line in css.splitlines() if "cs-" in line)
    assert "animation" not in cs and "@keyframes" not in cs


def test_closes_three_ways_and_no_technical_text():
    js = read("dashboard.js")
    assert '"Escape"' in js and "$(\"cs-panel\").contains(ev.target)" in js and "aria-expanded" in js
    assert "SW.connStatus" in js and "Date.now()" in js
    common = read("common.js")
    block = common[common.index("SW.connStatus = "):common.index("SW.card = ")]
    assert "err.message" not in block and "location" not in block
