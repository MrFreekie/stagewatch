"""FOH advice cards (AD4): the card ids, the dashboard page's sections, the admin card, and the
extra band view the Octave / Third-octave pick on the HF air loss card uses."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import cards
from stagewatch.core.config import AdviceConfig, AdvicePosition
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
PIN = "4711"
NEW_CARDS = ("alignment_drift", "hf_air_loss")
BANNED = re.compile(r"\b(safe|compliant|certified|accurate|guaranteed)\b", re.I)


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    app = create_app(hub, lan_addresses=lambda: ["192.0.2.10"])
    app.state.port = 8123
    with TestClient(app) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
        yield c


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- ids and defaults
def test_card_ids_are_known_and_in_no_default_list():
    for cid in NEW_CARDS:
        assert cid in cards.KNOWN_CARDS and cards.CARD_ID_RE.fullmatch(cid)
        assert cid not in cards.LEGACY_CARDS
        for layout in ("tablet", "phone", "wall", "anything-else"):
            assert cid not in cards.default_cards(layout)
    assert cards.unknown_cards(list(NEW_CARDS)) == []
    assert cards.strict_cards_error(["env_tiles", *NEW_CARDS]) is None


def test_new_dashboards_do_not_get_the_advice_cards(client):
    r = client.put("/api/admin/dashboards", json=[{"slug": "foh"}, {"slug": "lobby", "layout": "wall"}])
    assert r.status_code == 200
    for d in client.hub.config.dashboards:
        assert not set(NEW_CARDS) & set(d.cards)


def test_both_cards_round_trip_through_the_dashboards_api(client):
    r = client.put("/api/admin/dashboards", json=[{"slug": "foh", "cards": ["env_tiles", "alignment_drift", "hf_air_loss"]}])
    assert r.status_code == 200
    assert client.hub.config.dashboard("foh").cards == ["env_tiles", "alignment_drift", "hf_air_loss"]
    assert client.get("/api/dashboard/foh").json()["cards"] == ["env_tiles", "alignment_drift", "hf_air_loss"]
    assert client.get("/api/admin/state").json()["cards"]["known"] == list(cards.KNOWN_CARDS)
    # an id from some other release is still refused
    assert client.put("/api/admin/dashboards", json=[{"slug": "foh", "cards": ["alignment_drift_2"]}]).status_code == 422


# ---------------------------------------------------------------- the dashboard page (static)
def test_dashboard_page_has_a_hidden_section_and_a_registry_entry_for_each():
    html, js = _read("dashboard.html"), _read("dashboard.js")
    for cid in NEW_CARDS:
        assert len(re.findall(rf'<section[^>]*\sdata-card="{cid}"[^>]*\shidden', html)) == 1
        entry = re.search(rf"^\s{{4}}{cid}: \{{(.*)\}},$", js, re.M).group(1)
        assert "empty:" in entry          # hides itself while nothing enabled uses it
    assert html.index('src="/static/advice.js"') < html.index('src="/static/dashboard.js"')


def test_the_cards_have_no_timers_of_their_own_and_only_work_while_assigned():
    js = _read("dashboard.js")
    assert 'if (!has("alignment_drift") || !adv || !adv.paths.length) { card.hidden = true; return; }' in js
    assert 'if (!has("hf_air_loss") || !adv || !adv.positions.length) { card.hidden = true; return; }' in js
    assert "setInterval(() => { if (has(" in js
    # every use of the render functions outside their own definition is behind has()
    for fn, cid in (("renderDrift", "alignment_drift"), ("renderAir", "hf_air_loss")):
        for m in re.finditer(rf"(?<!function ){fn}\(\)", js):
            assert f'has("{cid}")' in js[max(0, m.start() - 70):m.start()] or "render: " + fn in js[max(0, m.start() - 20):m.start()] \
                or "onclick" in js[max(0, m.start() - 80):m.start()], js[m.start() - 70:m.end()]
    adv = _read("advice.js")
    assert "setInterval" not in adv and "setTimeout" not in adv and "requestAnimationFrame" not in adv


def test_advice_js_is_safe_to_render_and_uses_plain_words():
    js = _read("advice.js")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "toLocale", "Intl."):
        assert bad not in js
    assert not BANNED.search(js)
    for text in ("Estimated from air temperature and humidity. Advisory, not a measurement.",
                 "Advice only. Stagewatch never changes your delays.",
                 "Straight-line, uniform air only. Wind, temperature layers, ground and crowd are not included. Stagewatch never changes your system.",
                 "Needs a humidity reading", "Outside the model's range: treat as rough"):
        assert text in js
    css = _read("style.css")
    assert re.search(r"\.adv-status\.adv-check \{ color: var\(--warn\); \}", css)
    block = css[css.index("FOH advice cards"):]
    # Only the advice card's own rules (selectors with .adv-): later cards may use alarm colours.
    own = [body for sel, body in re.findall(r"([^{}]*\.adv-[^{}]*)\{([^}]*)\}", block)]
    assert own and not any("var(--stop)" in b or "var(--alert)" in b for b in own)   # never alarm colours
    assert "font-variant-numeric: tabular-nums" in block and "body.layout-wall .adv-drift" in block


def test_admin_names_for_the_cards_are_plain_and_say_advice_only():
    js = _read("cards-editor.js")
    for cid in NEW_CARDS:
        line = re.search(rf"^\s{{4}}{cid}: \[(.*)\],$", js, re.M).group(1)
        assert not BANNED.search(line) and "—" not in line
    assert "never changes your delays" in js


# ---------------------------------------------------------------- the admin card
def _admin_card() -> str:
    js = _read("admin.js")
    return js[js.index("// ------------------------------------------------------------ FOH advice"):js.index("// Environment (air at the site")]


def test_admin_card_is_a_collapsible_card_that_saves_with_put_advice():
    js = _read("admin.js")
    assert 'foldCard(adviceCard(), "advice", false)' in js
    src = _admin_card()
    assert 'card("FOH advice"' in src and 'api("PUT", "/api/admin/advice"' in src
    for label in ("Delay paths (Alignment drift)", "Positions (HF air loss)", "Add path", "Add position", "Alignment marker name",
                  "Soundcheck marker name", "Air loss bands", "Drift notice", "Silent notice", "Beep", "Hold time (s)",
                  "Add a marker when a notice shows", "Distance (m)", "Current delay (ms)", "Warn at (ms)", "Site average",
                  "advice-areas", "Move up", "Move down", "Remove"):
        assert label in src, label
    assert "the air is the same all along the throw" in src and "site average" in src
    assert "Not yet tested on hardware" in src
    assert "include_advice" in js


def test_admin_card_wording_is_crew_english():
    src = _admin_card()
    assert not BANNED.search(src)
    assert "—" not in src and "–" not in src          # no em or en dashes
    assert "innerHTML" not in src
    # touch targets: every row button and number box is 44 px
    assert src.count('class: "small touch"') >= 2 and 'class: "small danger touch"' in src


def test_admin_state_gives_the_card_what_it_draws_from(client):
    hub = client.hub
    hub.register_device(Device("tower", "Tower", "test", area="Delay tower 1", status=Status.OK))
    state = client.get("/api/admin/state").json()
    assert state["config"]["advice"]["paths"] == [] or isinstance(state["config"]["advice"]["paths"], list)
    assert "Delay tower 1" in state["stages"]               # the Area suggestions
    assert state["config"]["osc_out"]["include_advice"] is False


def test_what_the_admin_card_sends_saves_and_comes_back(client):
    """The card sends no id for a new row (it makes one from the name) and keeps the id of an old one."""
    body = {
        "paths": [{"id": "delay_tower_l", "name": "Delay tower L", "distance_m": 85, "source_area": "", "current_delay_ms": 12.5,
                   "drift_threshold_ms": 0.5, "phase_hint_hz": None, "enabled": True}],
        "positions": [{"id": "foh", "name": "FOH", "distance_m": 40, "enabled": True},
                      {"id": "rear", "name": "Rear", "distance_m": 90, "enabled": False}],
        "drift_reference": "Aligned", "air_loss_reference": "Soundcheck", "air_loss_resolution": "third",
        "alarm": "silent", "hold_s": 120, "marker_on_alert": True,
    }
    r = client.put("/api/admin/advice", json=body)
    assert r.status_code == 200, r.text
    saved = client.get("/api/admin/state").json()["config"]["advice"]
    assert [p["id"] for p in saved["paths"]] == ["delay_tower_l"] and saved["air_loss_resolution"] == "third"
    assert [p["enabled"] for p in saved["positions"]] == [True, False]
    # the card drops a row's hint (null) and sends the threshold empty when the notice is off
    body["paths"][0]["drift_threshold_ms"] = None
    assert client.put("/api/admin/advice", json=body).status_code == 200


def test_the_admin_card_ids_match_the_servers_slug():
    """advice.js and admin.js make an id from a name the same way as the server."""
    from stagewatch.core.model import slugify
    for name in ("Delay tower L", "Front fill", "Sub to tops", "  Rear  balcony "):
        js_like = re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", name.lower()))[:32]
        assert js_like == slugify(name)[:32]


# ---------------------------------------------------------------- the other band view
def _air_hub(tmp_path, resolution):
    hub = Hub(tmp_path)
    hub.config.site.smoothing_tau_s = 0
    hub.register_device(Device("env", "env", "test", status=Status.OK))
    for kind in (Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE):
        hub.register_entity(Entity(f"env.{kind.value}", "env", kind.value, kind))
    now = time.time()
    for ts, t, rh in ((now - 30, 25.0, 40.0), (now - 3, 15.0, 74.32)):
        for kind, v in ((Kind.TEMPERATURE, t), (Kind.HUMIDITY, rh), (Kind.PRESSURE, 101325.0)):
            hub.update_state(f"env.{kind.value}", v, ts)
        hub.compute_site(ts)
        if ts < now - 10:
            hub.add_marker("Soundcheck", "test", now - 25)
    hub.config.advice = AdviceConfig(positions=[AdvicePosition(id="foh", name="FOH", distance_m=40)],
                                     air_loss_resolution=resolution)
    hub.bus.publish("config", None)
    return hub, now


def test_snapshot_carries_both_band_views_and_the_marker_conditions(tmp_path):
    octave, now = _air_hub(tmp_path / "o", "octave")
    third, _ = _air_hub(tmp_path / "t", "third")
    a, b = octave.advice.compute(now), third.advice.compute(now)
    pos_o, pos_t = a["positions"][0], b["positions"][0]
    assert [x["band"] for x in pos_o["bands"]] == ["4k", "8k", "16k"]
    assert [x["band"] for x in pos_o["other_bands"]] == ["4k", "5k", "6.3k", "8k", "10k", "12.5k", "16k"]
    # the other view is exactly what the other system setting would have shown
    assert pos_o["other_bands"] == pos_t["bands"] and pos_t["other_bands"] == pos_o["bands"]
    ref = a["air_ref"]
    assert ref["was"]["t_c"] == pytest.approx(25.0) and ref["was"]["rh"] == pytest.approx(40.0)
    assert ref["delta"]["t_c"] == pytest.approx(-10.0)          # 15.0 now, 25.0 then
    assert set(ref) == {"label", "ts", "marker_id", "has_readings", "was", "delta"}
    for pos in (pos_o, pos_t):
        assert set(pos) == {"id", "name", "distance_m", "bands", "other_bands", "flags", "stale"}
    octave.recorder.close(); third.recorder.close()


def test_the_other_view_moves_the_published_numbers_too(tmp_path):
    from stagewatch.core.advice import advice_changed
    hub, now = _air_hub(tmp_path, "octave")
    snap = hub.advice.compute(now)
    moved = __import__("copy").deepcopy(snap)
    moved["positions"][0]["other_bands"][1]["change_db"] += 0.5   # 5k only: not in the shown bands
    assert advice_changed(snap, moved)
    quiet = __import__("copy").deepcopy(snap)
    quiet["positions"][0]["other_bands"][1]["change_db"] += 0.01
    assert not advice_changed(snap, quiet)
    hub.recorder.close()


# ---------------------------------------------------------------- node
def test_js_advice_words_and_in_place_updates_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "advice_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
