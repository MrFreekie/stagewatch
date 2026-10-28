"""RME interfaces card and Admin tile: registration, safe rendering, wiring (the logic itself is in tests/js/rme_*_test.js)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from stagewatch.core import cards

ROOT = Path(__file__).resolve().parent
STATIC = ROOT.parent / "src" / "stagewatch" / "web" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def code(name: str) -> str:
    return re.sub(r"//.*", "", read(name))


@pytest.mark.parametrize("script", ["rme_test.js", "rme_admin_test.js"])
def test_card_and_admin_logic_in_node(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "js" / script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_card_is_known_off_by_default_and_not_half_capable():
    assert "rme_units" in cards.KNOWN_CARDS
    for layout in ("tablet", "phone", "wall"):
        assert "rme_units" not in cards.default_cards(layout)
    assert "rme_units" not in cards.HALF_CAPABLE
    assert cards.strict_cards_error(["rme_units"]) is None


def test_page_has_one_hidden_section_and_loads_the_script_before_the_dashboard():
    html = read("dashboard.html")
    assert html.count('data-card="rme_units"') == 1 and 'id="rme-card"' in html
    assert re.search(r'data-card="rme_units"[^>]*\bhidden\b', html)
    order = [html.index(f"/static/{n}.js") for n in ("common", "rme", "dashboard")]
    assert order == sorted(order)


def test_card_registry_and_live_message_are_wired():
    js = read("dashboard.js")
    assert re.search(r"rme_units:\s*\{[^}]*render: renderRme", js)
    assert 'case "rme_units"' in js and "msg.rme_units" in js
    assert ".rme-has-warning" in js          # the wall alarm edge
    assert 'if (!has("rme_units")) stopRme();' in js
    assert 'rme_units: ["RME interfaces"' in read("cards-editor.js")
    assert "rme:" in read("common.js") and "SW.sourceInput" in read("common.js")


def test_scripts_use_only_safe_dom_and_make_no_requests():
    for name in ("rme.js", "rme-admin.js"):
        js = code(name)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "requestAnimationFrame",
                    "fetch(", "XMLHttpRequest", "toLocale", "Intl.", "setInterval"):
            assert bad not in js, (name, bad)
        assert "http://" not in js and "https://" not in js


def test_public_card_never_reads_private_fields():
    js = code("rme.js").lower()
    for private in ("serial", ".host", "group_name", "address", ".mac"):
        assert private not in js, private


def test_wall_and_phone_layouts_and_reduced_motion():
    css = read("style.css")
    assert "body.layout-wall .rme-temp { font-size: 88px; }" in css
    assert "body.layout-phone .rme-grid" in css
    assert "animation" not in "".join(re.findall(r"^[^\n]*\.rme-[^\n]*$", css, re.M))
    assert "transition" not in "".join(re.findall(r"^[^\n]*\.rme-[^\n]*$", css, re.M))


def test_equipment_card_leaves_the_rme_power_supply_contacts_to_the_rme_card():
    js = read("dashboard.js")
    assert 'e.kind === "contact" && String(e.device_id).indexOf("rme_") === 0' in js


def test_admin_has_the_tile_the_add_form_and_the_safety_lines():
    admin = read("admin.js")
    ra = read("rme-admin.js")
    assert '<script src="/static/rme-admin.js"></script>' in read("admin.html")
    assert 'key: "rme", name: "RME interface"' in admin and "rmeAddForm" in admin
    assert "/api/admin/rme/test" in admin and '"/api/admin/rme"' in admin
    assert "Replace unit" in admin and "Record input changes (not available yet)" in admin
    assert re.search(r'type: "checkbox", disabled: true', admin)          # the P2 option is shown but cannot be switched on
    assert "RME units have no password: anyone on the network can change them. Stagewatch only reads." in ra
    assert 'chip(foldCard(rmeCard(), "rme", false), "rme")' in admin
    assert "port" in admin.lower() and "Advanced" in admin


def test_admin_never_shows_a_serial_or_mac():
    admin = code("admin.js")
    section = admin[admin.index("function rmeAddForm"):admin.index("function rmeCard") + 2500].lower()
    assert "serial" not in section and ".mac" not in section
