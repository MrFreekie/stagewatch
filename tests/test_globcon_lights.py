"""GLOBCON card, Lights mode: the options model (lenient load, strict API, bounds, round trip, an older build ignoring the
new keys), the colour and icon lists agreeing between Python, the browser and the stylesheet, the contrast of every
palette colour, and the node test of the card's own logic. Hand-made data, labelled synthetic where it matters."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core.config import (
    GLOBCON_LIGHT_COLOURS, GLOBCON_LIGHT_ICONS, GLOBCON_LIGHTS_MAX, Config, ConfigStore, GlobconCardOptions, GlobconLight,
)
from stagewatch.core.hub import Hub
from stagewatch.integrations.globcon import GlobconIntegration
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
CARD_ID = "globcon_meters"


def light(strip=1, **over):
    return {"strip": strip, **over}


# ------------------------------------------------------------------------------------------ the model
def test_defaults_leave_the_card_as_it_was():
    o = GlobconCardOptions()
    assert o.mode == "meters" and o.lights == []
    assert GlobconCardOptions.model_validate({"controller": 3, "strips": 4}).mode == "meters"


def test_a_light_has_the_agreed_defaults():
    g = GlobconLight(strip=3)
    assert (g.threshold_db, g.colour, g.icon, g.hold_s) == (-40.0, "green", "microphone", 1.0)


def test_lists_are_the_agreed_palette_and_icons():
    assert GLOBCON_LIGHT_COLOURS == ("green", "amber", "red", "blue", "cyan", "magenta", "white", "orange")
    assert GLOBCON_LIGHT_ICONS == ("microphone", "speaker", "timecode", "play", "megaphone", "camera", "headset", "radio", "signal")
    assert GLOBCON_LIGHTS_MAX == 16


def test_valid_lights_load_and_fill_in_defaults():
    o = GlobconCardOptions.model_validate({"mode": "lights", "range": "9-16", "lights": [light(9), light(10, colour="red", icon="play", threshold_db=-20.5, hold_s=0)]})
    assert o.mode == "lights" and [x.strip for x in o.lights] == [9, 10]
    assert o.lights[0].colour == "green" and o.lights[1].threshold_db == -20.5 and o.lights[1].hold_s == 0


def test_bad_entries_are_dropped_on_load_and_the_rest_kept(caplog):
    """Lenient load: each unusable entry is dropped (and a warning is logged that never carries a value)."""
    caplog.set_level(logging.WARNING)
    bad = [light(0), light(17), light(True), light("2"), light(3, colour="chartreuse"), light(4, icon="<script>secretword"),
           light(5, threshold_db=1), light(6, threshold_db=-73), light(7, threshold_db=float("nan")), light(8, hold_s=0.2),
           light(9, hold_s=11), light(10, extra=1), "junk", None, 5, light(11, hold_s=True)]
    good = [light(2), light(12, colour="blue", hold_s=0.5)]
    o = GlobconCardOptions.model_validate({"mode": "lights", "lights": bad + good + [light(2, colour="red")]})
    assert [x.strip for x in o.lights] == [2, 12]                                  # the duplicate of 2 is dropped too
    assert o.lights[0].colour == "green"                                           # the first one wins
    assert "secretword" not in caplog.text and "chartreuse" not in caplog.text


def test_more_than_sixteen_lights_keeps_the_first_sixteen():
    many = [light(((i) % 16) + 1) for i in range(40)]
    assert len(GlobconCardOptions.model_validate({"lights": many}).lights) == 16
    assert len(GlobconCardOptions.model_validate({"lights": [light(1)] * 30}).lights) == 1


@pytest.mark.parametrize("bad", ["lights ", "LIGHTS", 1, None, ["lights"], True, ""])
def test_a_bad_mode_falls_back_to_meters(bad):
    assert GlobconCardOptions.model_validate({"mode": bad, "lights": [light(1)]}).mode == "meters"


@pytest.mark.parametrize("bad", ["x", 5, {"strip": 1}, True])
def test_lights_that_are_not_a_list_become_empty(bad):
    o = GlobconCardOptions.model_validate({"mode": "lights", "controller": 4, "lights": bad})
    assert o.lights == [] and o.controller == 4 and o.mode == "lights"


def test_boundaries_of_the_numbers():
    ok = [light(1, threshold_db=-72), light(2, threshold_db=0), light(3, hold_s=0), light(4, hold_s=0.5), light(5, hold_s=10)]
    assert len(GlobconCardOptions.model_validate({"lights": ok}).lights) == 5
    edge = [light(1, threshold_db=0.1), light(2, threshold_db=-72.1), light(3, hold_s=0.49), light(4, hold_s=10.1)]
    assert GlobconCardOptions.model_validate({"lights": edge}).lights == []


def test_round_trip_through_the_config_file(tmp_path):
    cfg = Config()
    cfg.dashboards[0].globcon = GlobconCardOptions.model_validate(
        {"controller": 2, "range": "1-8", "mode": "lights", "lights": [light(1, colour="cyan", icon="timecode", threshold_db=-50, hold_s=3), light(8)]})
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["dashboards"][0]["globcon"]["mode"] == "lights"
    assert raw["dashboards"][0]["globcon"]["lights"][0] == {"strip": 1, "threshold_db": -50.0, "colour": "cyan", "icon": "timecode", "hold_s": 3.0}
    again = ConfigStore(path).load()
    assert again.dashboards[0].globcon == cfg.dashboards[0].globcon
    assert again.schema_version == cfg.schema_version                              # no schema bump


def test_a_damaged_lights_section_resets_only_itself(tmp_path):
    cfg = Config()
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["dashboards"][0]["globcon"] = {"controller": 5, "strips": 4, "range": "5-8", "mode": "lights", "lights": "nonsense"}
    raw["dashboards"][1]["globcon"] = {"controller": 6, "mode": "lights", "lights": [light(1), {"strip": "x"}]}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    store2 = ConfigStore(path)
    loaded = store2.load()
    assert not store2.recovery_required
    assert loaded.dashboards[0].globcon == GlobconCardOptions(controller=5, strips=4, range="5-8", mode="lights")
    assert [x.strip for x in loaded.dashboards[1].globcon.lights] == [1] and loaded.dashboards[1].globcon.controller == 6


def test_an_older_build_ignores_mode_and_lights_and_forgets_them_but_the_old_fields_stay(tmp_path):
    from pydantic import BaseModel, ConfigDict

    class OldOptions(BaseModel):                         # the previous build: controller, strips, range only
        model_config = ConfigDict(extra="ignore")
        controller: int = 1
        strips: int = 8
        range: str | None = None

    cfg = Config()
    cfg.dashboards[0].globcon = GlobconCardOptions.model_validate({"controller": 4, "strips": 4, "range": "13-16", "mode": "lights", "lights": [light(13)]})
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    old = OldOptions.model_validate(raw["dashboards"][0]["globcon"])               # read without complaint
    assert (old.controller, old.strips, old.range) == (4, 4, "13-16")
    raw["dashboards"][0]["globcon"] = old.model_dump()                             # its next save drops mode and lights
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    again = ConfigStore(path).load()
    assert again.dashboards[0].globcon == GlobconCardOptions(controller=4, strips=4, range="13-16")   # shows meters again


# ------------------------------------------------------------------------------------------ the API
@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path, emulate=True)
    h.add_integration(GlobconIntegration(h, emulate=True))
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


def _rows(client, globcon):
    rows = client.get("/api/info").json()["dashboards"]
    rows[0]["cards"] = [*rows[0]["cards"], CARD_ID] if CARD_ID not in rows[0]["cards"] else rows[0]["cards"]
    rows[0]["globcon"] = globcon
    return rows


def test_api_accepts_lights_and_serves_them_back(client):
    g = {"controller": 3, "strips": 8, "range": "9-16", "mode": "lights",
         "lights": [light(9, colour="red", icon="megaphone", threshold_db=-35, hold_s=0), light(12, colour="blue", icon="timecode", hold_s=5)]}
    assert client.put("/api/admin/dashboards", json=_rows(client, g)).status_code == 200
    got = client.get("/api/dashboard/foh").json()["globcon"]
    assert got["mode"] == "lights" and [x["strip"] for x in got["lights"]] == [9, 12]
    assert got["lights"][0] == {"strip": 9, "threshold_db": -35.0, "colour": "red", "icon": "megaphone", "hold_s": 0.0}
    assert got["lights"][1]["threshold_db"] == -40.0                               # default filled in


def test_api_is_strict(client):
    base = {"controller": 3, "strips": 8, "range": "9-16", "mode": "lights"}
    bad_cases = [
        {"lights": [light(9, colour="chartreuse")]}, {"lights": [light(9, icon="<b>")]}, {"lights": [light(9, threshold_db=1)]},
        {"lights": [light(9, threshold_db=-73)]}, {"lights": [light(9, threshold_db="-40")]}, {"lights": [light(9, hold_s=0.2)]},
        {"lights": [light(9, hold_s=11)]}, {"lights": [light(9, hold_s=True)]}, {"lights": [light(9, extra=1)]},
        {"lights": [light(9), light(9)]},                                           # one light per channel
        {"lights": [light(8)]}, {"lights": [light(17)]}, {"lights": [light(0)]},     # 8 is outside the chosen group 9-16
        {"lights": [light(1 + i) for i in range(17)], "range": "1-16"},             # more than 16
        {"lights": "x"}, {"lights": {"strip": 9}}, {"lights": [None]}, {"lights": [5]},
        {"mode": "Lights"}, {"mode": 1}, {"mode": None},
    ]
    for extra in bad_cases:
        r = client.put("/api/admin/dashboards", json=_rows(client, {**base, "lights": [], **extra}))
        assert r.status_code == 422, extra
    assert client.hub.config.dashboards[0].globcon.mode == "meters"               # nothing was half-saved


def test_api_without_the_new_keys_is_still_fine_and_keeps_old_behaviour(client):
    assert client.put("/api/admin/dashboards", json=_rows(client, {"controller": 2, "strips": 4})).status_code == 200
    assert client.hub.config.dashboards[0].globcon == GlobconCardOptions(controller=2, strips=4)


def test_lights_with_no_group_may_use_any_channel(client):
    g = {"controller": 1, "strips": 8, "mode": "lights", "lights": [light(16), light(1)]}
    assert client.put("/api/admin/dashboards", json=_rows(client, g)).status_code == 200


def test_nothing_new_on_the_public_dashboard_payload_beyond_the_card_options(client):
    g = {"controller": 3, "strips": 8, "range": "9-16", "mode": "lights", "lights": [light(9)]}
    assert client.put("/api/admin/dashboards", json=_rows(client, g)).status_code == 200
    body = client.get("/api/dashboard/foh").json()["globcon"]
    assert set(body) == {"controller", "strips", "range", "mode", "lights"}
    assert set(body["lights"][0]) == {"strip", "threshold_db", "colour", "icon", "hold_s"}


# ------------------------------------------------------------------------------------------ the palette
def _lum(hexcol: str) -> float:
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4   # noqa: E731  (WCAG 2.x)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a: str, b: str) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def _css_palette() -> dict[str, tuple[str, str]]:
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    return {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(r"\.gc-c-([a-z]+)\s*\{\s*--lc:\s*(#[0-9a-fA-F]{6});\s*--lf:\s*(#[0-9a-fA-F]{6});", css)}


def test_contrast_maths_against_two_known_pairs():
    """Hand-calculated: black on white is 21:1; white on white is 1:1."""
    assert round(_ratio("#000000", "#ffffff"), 2) == 21.0
    assert _ratio("#ffffff", "#ffffff") == 1.0


def test_every_palette_colour_is_in_the_stylesheet_with_readable_text():
    pal = _css_palette()
    assert set(pal) == set(GLOBCON_LIGHT_COLOURS)
    for name, (bg, fg) in pal.items():
        assert _ratio(bg, fg) >= 4.5, f"{name}: {bg} with {fg} is {_ratio(bg, fg):.2f}:1"


def test_the_colours_are_told_apart_by_more_than_colour():
    """No two lit colours are the same background (the words, tick and name carry the rest)."""
    bgs = [bg.lower() for bg, _ in _css_palette().values()]
    assert len(set(bgs)) == len(bgs)


def test_browser_lists_match_the_server_lists():
    js = (STATIC / "globcon.js").read_text(encoding="utf-8")
    col = re.search(r"gc\.LIGHT_COLOURS = \[(.*?)\];", js).group(1)
    ico = re.search(r"gc\.LIGHT_ICONS = \[(.*?)\];", js).group(1)
    assert tuple(re.findall(r'"([a-z]+)"', col)) == GLOBCON_LIGHT_COLOURS
    assert tuple(re.findall(r'"([a-z]+)"', ico)) == GLOBCON_LIGHT_ICONS
    for name in GLOBCON_LIGHT_ICONS:                                                # every icon is drawn
        assert re.search(rf"^\s+{name}: \[\[", js, re.M), name
    editor = (STATIC / "cards-editor.js").read_text(encoding="utf-8")
    for name in GLOBCON_LIGHT_COLOURS + GLOBCON_LIGHT_ICONS:
        assert f'["{name}", ' in editor, name


def test_lights_code_uses_only_safe_dom_and_makes_no_requests():
    js = (STATIC / "globcon.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "fetch(", "XMLHttpRequest", "WebSocket", "?.", "??"):
        assert bad not in js, bad
    editor = (STATIC / "cards-editor.js").read_text(encoding="utf-8")
    assert "innerHTML" not in editor


def test_nothing_in_lights_flashes_or_animates():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    block = css[css.index("GLOBCON Lights mode"):]
    assert "@keyframes" not in block and "animation" not in block


def test_node_lights_logic():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "globcon_lights_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
