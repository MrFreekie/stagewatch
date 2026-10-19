"""The Power tree dashboard card: registry, safe rendering, public data, the emulate demo and the JS cases."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import cards, power_tree
from stagewatch.core.config import Config
from stagewatch.core.hub import Hub
from stagewatch.integrations.shelly import seed_emulate_shelly
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"


def test_the_card_is_known_never_a_default_and_not_half_width():
    assert "power_tree" in cards.KNOWN_CARDS
    assert all("power_tree" not in cards.default_cards(layout) for layout in ("tablet", "phone", "wall"))
    assert "power_tree" not in cards.HALF_CAPABLE
    assert not cards.strict_cards_error(["env_tiles", "power_tree"])


def test_card_html_registry_and_admin_name_are_in_step():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert len(re.findall(r'data-card="power_tree"', html)) == 1 and "power-tree.js" in html
    assert "power_tree: {" in (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "power_tree:" in (STATIC / "cards-editor.js").read_text(encoding="utf-8")


def test_power_tree_js_is_safe_and_has_no_read_only_tag():
    js = (STATIC / "power-tree.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "toLocale", "Intl.", "?.", "??"):
        assert bad not in js
    section = (STATIC / "dashboard.html").read_text(encoding="utf-8").split('data-card="power_tree"')[1].split("</section>")[0]
    assert "read only" not in section.lower() and "read only" not in js.lower()


def test_wall_alarm_edge_covers_a_gap_that_needs_checking():
    assert ".pt-gap-check" in (STATIC / "dashboard.js").read_text(encoding="utf-8")


def test_the_emulate_demo_has_two_supplies_a_ups_and_unmonitored_points_and_is_valid():
    cfg = Config()
    assert seed_emulate_shelly(cfg)
    nodes = power_tree.build_nodes(cfg)
    assert power_tree.validate(nodes) == []
    roles = [n.role for n in nodes]
    assert roles.count("source") == 2 and "ups" in roles
    assert any(n.kind == "point" and n.role != "source" for n in nodes)            # an unmetered unit
    assert "power_tree" in next(d for d in cfg.dashboards if d.slug == "wall").cards


def test_the_public_structure_has_no_addresses_or_typed_allowances(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    seed_emulate_shelly(hub.config)
    with TestClient(create_app(hub, lan_addresses=lambda: ["192.0.2.10"])) as c:
        body = c.get("/api/power-tree").text
    assert "192.0.2" not in body and "mac" not in body.lower() and "allowance" not in body


def test_js_cases_run_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "power_tree_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
