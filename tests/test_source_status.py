"""Per-card source status (Ontime, Smaart, GLOBCON): source checks and the node logic test."""

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


def test_source_status_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "js" / "source_status_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_external_card_has_its_indicator():
    js = read("dashboard.js")
    for needle in ('wc.ui.status = addCardStatus(card, "ontime")', 'ot.status = addCardStatus(card, "ontime")',
                   'rdc.status = addCardStatus(card, "ontime")', 'gcc.status = addCardStatus(card, "globcon")',
                   'SW.sourceStatusUi("smaart")'):
        assert needle in js, needle
    # The wall clock shows it only while its source is Ontime.
    assert 'wc.ui.status.el.hidden = m.source !== "ontime"' in js
    # Only the existing per-source status is read: no new private fields, no addresses.
    common = read("common.js")
    block = common[common.index("SW.SOURCES = "):common.index("SW.card = ")]
    assert not re.search(r"\.(host|address|port|error|detail|status_detail|message)\b", block.replace("src.error", ""))
    assert "location" not in block and "err.message" not in block


def test_indicator_is_accessible():
    block = read("common.js")
    block = block[block.index("SW.sourceStatusUi = "):block.index("SW.card = ")]
    assert '"aria-expanded"' in block and 'type: "button"' in block
    assert 'role: "status", "aria-live": "polite"' in block
    assert "Escape" in block and "el.contains(ev.target)" in block
    css = read("style.css")
    assert re.search(r"\.ss-btn \{[^}]*min-width: 44px; min-height: 44px", css)
    assert ".ss-btn:focus-visible" in css


def test_wall_shows_it_only_when_not_online_and_nothing_flashes():
    css = read("style.css")
    assert "body.layout-wall .ss-btn.ss-ok { display: none; }" in css
    assert "body.layout-wall .ss-panel { display: none; }" in css
    ss = "\n".join(line for line in css.splitlines() if ".ss" in line)
    assert "animation" not in ss and "@keyframes" not in ss and "transition" not in ss


def test_old_header_dot_is_gone():
    assert 'id="conn"' not in read("dashboard.html")
    assert '$("conn")' not in read("dashboard.js")
    assert not re.search(r"^\.conn\b", read("style.css"), re.M)
