"""Header messages icon on the user dashboards: source checks and the node logic test."""

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


def test_message_summary_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "js" / "header_messages_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_button_is_accessible_and_sized():
    html, css = read("dashboard.html"), read("style.css")
    btn = re.search(r'<button[^>]*id="msg-btn"[^>]*>', html).group(0)
    assert 'type="button"' in btn and 'aria-expanded="false"' in btn and 'aria-controls="msg-panel"' in btn
    assert "aria-label=" in btn and 'role="status"' in html and 'id="msg-panel"' in html and "<svg" in html
    assert re.search(r"\.msg-btn \{[^}]*min-width: 44px; min-height: 44px", css)
    assert ".msg-btn:focus-visible" in css


def test_header_is_sticky_but_the_wall_is_untouched():
    css = read("style.css")
    assert re.search(r"body:not\(\.layout-wall\) header\.top \{[^}]*position: -webkit-sticky; position: sticky; top: 0", css)
    assert "body.layout-wall .msg-btn" in css


def test_each_kind_has_a_symbol_and_nothing_flashes():
    js, css = read("common.js"), read("style.css")
    for word, symbol in (("Alarm", '"!"'), ("Warning", '"▲"'), ("Information", '"i"')):
        assert word in js and symbol in js
    msg = "\n".join(line for line in css.splitlines() if "msg-" in line)
    assert "animation" not in msg and "@keyframes" not in msg


def test_panel_survives_rerenders_and_closes_three_ways():
    js = read("dashboard.js")
    assert "renderMessages();" in js and "SW.messageSummary" in js
    assert '"Escape"' in js and "contains(ev.target)" in js and "aria-expanded" in js
    # only the rows are rebuilt; the panel element itself is never replaced
    assert '$("msg-list").replaceChildren(' in js


def test_no_new_public_alarm_fields():
    src = (ROOT.parent / "src" / "stagewatch" / "core" / "alarms.py").read_text(encoding="utf-8")
    assert '"silent": self.silent}' in src
