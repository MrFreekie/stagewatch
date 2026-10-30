"""Condensation risk card: dew point values (hand-calculated), registry, site-only inputs, node words."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from stagewatch import acoustics
from stagewatch.core import cards
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"


# (id, upper edge in degC, word); the JS BANDS and the ABOUT text must agree with this.
CONDENSATION_BANDS_C = [
    ("condensing", 1, "Condensing"),
    ("near", 4, "Near saturation"),
    ("moderate", 10, "Moderate"),
    ("low", None, "Low risk"),
]


@pytest.mark.parametrize("temp, rh, dew", [
    (20.0, 50.0, 9.26),    # 243.04 * 0.647006 / (17.625 - 0.647006)
    (25.0, 100.0, 25.0),   # saturated air: dew point = air temperature
    (10.0, 80.0, 6.71),    # 243.04 * 0.473386 / (17.625 - 0.473386)
])
def test_dew_point_hand_values(temp, rh, dew):
    assert acoustics.dew_point_c(temp, rh) == pytest.approx(dew, abs=0.01)


def test_margin_matches_the_band_thresholds():
    # 20 C at 50 % RH: margin 10.74 C is Low risk (10 C or more); 20 C at 80 % RH: dew 16.44, margin 3.56 is Near saturation.
    assert 20.0 - acoustics.dew_point_c(20.0, 50.0) >= 10.0
    assert 1.0 <= 20.0 - acoustics.dew_point_c(20.0, 80.0) < 4.0
    assert 20.0 - acoustics.dew_point_c(20.0, 100.0) < 1.0


def test_frost_point_hand_values():
    # 0 C at 50 % RH: ln(0.5) = -0.693147. Water: 243.04 * -0.693147 / (17.625 + 0.693147) = -9.20.
    # Ice: 272.62 * -0.693147 / (22.46 + 0.693147) = -8.16.
    assert acoustics.dew_point_c(0.0, 50.0) == pytest.approx(-9.2, abs=0.01)
    assert acoustics.frost_point_c(0.0, 50.0) == pytest.approx(-8.16, abs=0.01)
    assert acoustics.frost_point_c(0.0, 100.0) == pytest.approx(0.0, abs=0.001)


def test_js_bands_and_about_match_the_python_bands():
    js = (STATIC / "condensation.js").read_text(encoding="utf-8")
    rows = re.findall(r'\{ id: "(\w+)", below: ([\w.]+), word: "([^"]+)"', js)
    assert rows == [(i, "Infinity" if e is None else str(e), w) for i, e, w in CONDENSATION_BANDS_C]
    about = re.search(r'c\.ABOUT = "([^"]*)"', js).group(1)
    lower = 0
    for _id, edge, word in CONDENSATION_BANDS_C:
        if lower == 0:
            span = f"under {edge}"
        elif edge is None:
            span = f"{lower}"
        else:
            span = f"{lower} to {edge}"
        assert f"{word}: {span} °C" in about
        lower = edge or lower
    assert "KIT_CLOSE_C = 3;" in js and "within 3 °C" in about


def test_card_is_registered_and_never_a_default():
    assert "condensation" in cards.KNOWN_CARDS and cards.CARD_ID_RE.fullmatch("condensation")
    for layout in ("tablet", "phone", "wall"):
        assert "condensation" not in cards.default_cards(layout)
    assert "condensation" not in cards.legacy_cards("wall")
    assert cards.strict_cards_error(["env_tiles", "condensation"]) is None


def test_page_registers_the_card_once():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    editor = (STATIC / "cards-editor.js").read_text(encoding="utf-8")
    assert html.count('data-card="condensation"') == 1
    assert '/static/condensation.js' in html
    assert 'condensation: { el: cardEl("condensation"), render: renderCondensation' in js
    assert "condensation: [" in editor
    assert "?." not in (STATIC / "condensation.js").read_text(encoding="utf-8")


def test_old_ipad_syntax_stays_out_of_the_card_script():
    src = (STATIC / "condensation.js").read_text(encoding="utf-8")
    for bad in ("?.", "??", "replaceAll", "innerHTML"):
        assert bad not in src


def test_equipment_readings_never_reach_the_site_dew_point(tmp_path):
    hub = Hub(tmp_path)
    hub.config.site.smoothing_tau_s = 0
    hub.register_device(Device("room", "room", "test", status=Status.OK))
    hub.register_device(Device("rack", "rack", "test", status=Status.OK))
    hub.set_node_role("rack", "equipment")
    for dev in ("room", "rack"):
        hub.register_entity(Entity(f"{dev}.temperature", dev, "t", Kind.TEMPERATURE))
        hub.register_entity(Entity(f"{dev}.humidity", dev, "h", Kind.HUMIDITY))
    hub.update_state("room.temperature", 20.0)
    hub.update_state("room.humidity", 50.0)
    hub.update_state("rack.temperature", 45.0)
    hub.update_state("rack.humidity", 90.0)
    hub.compute_site()
    assert hub.entities["site.temperature"].value == pytest.approx(20.0)
    assert hub.entities["site.dew_point"].value == pytest.approx(9.26, abs=0.01)
    hub.recorder.close()


def test_js_condensation_words_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "condensation_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
