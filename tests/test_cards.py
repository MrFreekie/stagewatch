"""Per-dashboard cards (WP4): the API side, the dashboard page's card sections and registry,
the admin card picker data, and the "outside the formula's tested range" flag."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch import acoustics
from stagewatch.core import cards
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
FIX_V1 = ROOT / "tests" / "fixtures" / "v1"
PIN = "4711"
LAN = ["192.0.2.10", "198.51.100.7"]  # documentation addresses (RFC 5737)
# What a 0.2.0 dashboard showed: tiles, chart, markers + sensors; wall screens also the footer.
CARDS_0_2 = ["env_tiles", "chart", "markers", "sensors"]


def _client(hub: Hub):
    app = create_app(hub, lan_addresses=lambda: list(LAN))
    app.state.port = 8123
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with _client(hub) as c:
        c.hub = hub
        yield c


def _admin(c):
    assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200


def _put(c, dashboards):
    return c.put("/api/admin/dashboards", json=dashboards)


# ---------------------------------------------------------------- API
def test_put_dashboards_refuses_unknown_cards_and_keeps_the_old_list(client):
    _admin(client)
    before = client.hub.config.dashboard("foh").cards
    r = _put(client, [{"slug": "foh", "cards": ["env_tiles", "spl_limits"]}])
    assert r.status_code == 422 and "Unknown card" in r.text and "spl_limits" not in r.text
    for bad in (["env_tiles", "env_tiles"], ["Env-Tiles"], ["env_tiles"] * 17, "env_tiles"):
        assert _put(client, [{"slug": "foh", "cards": bad}]).status_code == 422
    assert client.hub.config.dashboard("foh").cards == before


def test_put_dashboards_saves_cards_and_stage_and_keeps_them_when_omitted(client):
    _admin(client)
    r = _put(client, [{"slug": "foh", "layout": "tablet", "cards": ["sensors", "env_tiles"], "stage": " Main stage "}])
    assert r.status_code == 200
    d = client.hub.config.dashboard("foh")
    assert d.cards == ["sensors", "env_tiles"] and d.stage == "Main stage"
    # A save without cards/stage (an older admin page) keeps what the dashboard has.
    assert _put(client, [{"slug": "foh", "title": "FOH desk"}]).status_code == 200
    d = client.hub.config.dashboard("foh")
    assert d.title == "FOH desk" and d.cards == ["sensors", "env_tiles"] and d.stage == "Main stage"
    # A new dashboard without cards gets its layout's defaults; an explicit empty list stays empty.
    assert _put(client, [{"slug": "foh"}, {"slug": "lobby", "layout": "wall"},
                         {"slug": "alarms", "cards": []}]).status_code == 200
    assert client.hub.config.dashboard("lobby").cards == cards.default_cards("wall")
    assert client.hub.config.dashboard("alarms").cards == []
    assert _put(client, [{"slug": "foh", "stage": "x" * 41}]).status_code == 422


def test_put_dashboards_needs_admin_and_same_origin(client):
    assert _put(client, [{"slug": "foh", "cards": []}]).status_code == 401
    _admin(client)
    r = client.put("/api/admin/dashboards", json=[{"slug": "foh", "cards": []}],
                   headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_address_only_for_dashboards_with_the_connect_footer_card(client):
    _admin(client)
    assert _put(client, [
        {"slug": "foh", "layout": "tablet", "cards": ["env_tiles", "connect_footer"]},
        {"slug": "wall", "layout": "wall", "cards": ["env_tiles", "chart"]},
        {"slug": "phone", "layout": "phone", "cards": ["connect_footer"]},
    ]).status_code == 200
    client.cookies.clear()  # the address endpoint is public
    assert client.get("/api/dashboard/foh/address").json() == {"url": "http://192.0.2.10:8123/d/foh"}
    assert client.get("/api/dashboard/phone/address").json() == {"url": "http://192.0.2.10:8123/d/phone"}
    assert client.get("/api/dashboard/wall/address").json() == {"url": ""}   # wall, but no footer card
    assert client.get("/api/dashboard/nope/address").json() == {"url": ""}


def test_snapshot_carries_the_dashboards_cards_and_stage(client):
    _admin(client)
    assert _put(client, [{"slug": "foh", "cards": ["chart", "env_tiles"], "stage": "Stage L"}]).status_code == 200
    client.cookies.clear()
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        snap = ws.receive_json()
    assert snap["type"] == "snapshot"
    assert snap["dashboard"]["cards"] == ["chart", "env_tiles"] and snap["dashboard"]["stage"] == "Stage L"
    assert client.get("/api/dashboard/foh").json()["cards"] == ["chart", "env_tiles"]
    # nothing admin-only rides along on the public feed
    assert "esphome_devices" not in snap and "admin" not in snap and "stages" not in snap


def test_admin_state_lists_known_cards_defaults_and_stage_suggestions(client):
    assert client.get("/api/admin/state").status_code == 401
    _admin(client)
    hub = client.hub
    hub.register_device(Device("x1", "X", "test", area="main stage", status=Status.OK))  # same name, other case
    hub.register_device(Device("x2", "Y", "test", area="y" * 41, status=Status.OK))      # too long for a stage
    assert _put(client, [{"slug": "foh", "stage": "Main Stage"}]).status_code == 200
    st = client.get("/api/admin/state").json()
    assert st["cards"]["known"] == list(cards.KNOWN_CARDS)
    assert st["cards"]["defaults"] == {k: list(v) for k, v in cards.LAYOUT_DEFAULTS.items()}
    lowered = [s.casefold() for s in st["stages"]]
    assert "main stage" in lowered and len(lowered) == len(set(lowered))
    assert all(len(s) <= 40 for s in st["stages"]) and "FOH" in st["stages"]  # emulated node areas


# ------------------------------------------------- dashboard page (static)
def _html() -> str:
    return (STATIC / "dashboard.html").read_text(encoding="utf-8")


def _registry_ids() -> list[str]:
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    block = re.search(r"const CARDS = \{(.*?)\n  \};", js, re.S).group(1)
    return re.findall(r"^\s{4}([a-z_]+): \{", block, re.M)


def test_every_known_card_has_one_hidden_section_and_a_registry_entry():
    html = _html()
    tags = re.findall(r"<(\w+)[^>]*\sdata-card=\"([a-z_]+)\"[^>]*>", html)
    ids = [i for _, i in tags]
    assert sorted(ids) == sorted(cards.KNOWN_CARDS) and len(ids) == len(set(ids))
    for tag in re.findall(r"<\w+[^>]*\sdata-card=\"[a-z_]+\"[^>]*>", html):
        assert re.search(r"\shidden[\s>]", tag), f"cards start hidden: {tag}"
    assert sorted(_registry_ids()) == sorted(cards.KNOWN_CARDS)


def test_only_the_alarm_banner_is_outside_the_card_system():
    """An empty card list must leave only the alarm banner (and the "no cards" notice, which
    shows only then) in <main>; the header and the disconnected banner are outside <main>."""
    main = re.search(r"<main[^>]*>(.*)</main>", _html(), re.S).group(1)
    main = re.sub(r"<!--.*?-->", "", main, flags=re.S)
    top = []  # direct children of <main>: track nesting depth
    depth = 0
    for m in re.finditer(r"<(/?)(\w+)([^>]*)>", main):
        closing, tag, attrs = m.group(1), m.group(2), m.group(3)
        if tag in ("input", "br", "img"):  # void elements: no closing tag
            continue
        if closing:
            depth -= 1
            continue
        if depth == 0:
            top.append(attrs)
        depth += 1
    assert len(top) == 2 + len(cards.KNOWN_CARDS) - 1   # banner + notice + every card except the footer
    assert 'id="alarms"' in top[0] and "data-card" not in top[0]
    assert 'id="no-cards"' in top[1] and re.search(r"\shidden\b", top[1])
    assert all("data-card" in a and re.search(r"\shidden\b", a) for a in top[2:])
    assert re.search(r"\[data-card\]\[hidden\]\s*\{\s*display:\s*none", (STATIC / "style.css").read_text(encoding="utf-8"))


def _visible_cards(dash_cards: list[str], schedule_items: int = 0) -> list[str]:
    """The cards dashboard.js shows: ids it knows, once each, in order; the schedule only with
    items for the dashboard's stage, and the Wall Clock not before its feature."""
    known = _registry_ids()
    out = [c for i, c in enumerate(dash_cards) if c in known and c not in dash_cards[:i]]
    return [c for c in out if not (c == "schedule" and not schedule_items) and c != "wall_clock"]


def test_migrated_dashboards_show_the_same_cards_as_0_2_0(tmp_path):
    shutil.copyfile(FIX_V1 / "config.yaml", tmp_path / "config.yaml")
    hub = Hub(tmp_path)
    with _client(hub) as c:
        layouts = {d.slug: d.layout for d in hub.config.dashboards}
        assert set(layouts.values()) == {"tablet", "phone", "wall"}
        for slug, layout in layouts.items():
            with c.websocket_connect(f"/ws?dashboard={slug}") as ws:
                dash = ws.receive_json()["dashboard"]
            expected = CARDS_0_2 + (["connect_footer"] if layout == "wall" else [])
            assert _visible_cards(dash["cards"]) == expected, slug
            assert bool(c.get(f"/api/dashboard/{slug}/address").json()["url"]) == (layout == "wall")
    hub.recorder.close()


def test_renderer_skips_cards_from_a_newer_release():
    assert _visible_cards(["spl_limits", "env_tiles", "contacts", "chart"]) == ["env_tiles", "chart"]
    assert _visible_cards([]) == []


def test_dashboard_js_gates_work_on_assigned_cards():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert 'if (has("chart")) loadHistory();' in js            # no history fetch without the chart
    assert 'if (has("sensors")) renderSensors();' in js        # no sensor table work without the card
    assert 'layout !== "wall"' not in js and "layout === \"wall\" &&" in js  # wall only hides the sound button
    assert "innerHTML" not in js


def test_schedule_card_is_built_and_hides_itself_without_items():
    """WP8: the schedule card is no longer a stub. It hides while this dashboard's stage has no
    items, fetches its items only while assigned, and has phone and wall rules."""
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    entry = re.search(r"^\s{4}schedule: \{(.*)\},$", js, re.M).group(1)
    assert "render: renderSchedule" in entry and "empty: () => schedItems().length === 0" in entry
    assert "render: nothing" not in entry
    assert _visible_cards(["schedule", "env_tiles"], schedule_items=0) == ["env_tiles"]
    assert _visible_cards(["schedule", "env_tiles"], schedule_items=3) == ["schedule", "env_tiles"]
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "body.layout-phone .sched-strip { display: flex; }" in css
    assert "body.layout-wall .sched-more { display: none; }" in css
    html = _html()
    assert re.search(r'<section class="card" data-card="schedule" id="schedule-card" hidden>\s*<h2>Schedule</h2>', html)


def test_schedule_card_uses_the_dashboard_stage(client):
    _admin(client)
    assert _put(client, [{"slug": "foh", "cards": ["schedule"], "stage": "Main"}]).status_code == 200
    client.cookies.clear()
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        snap = ws.receive_json()
    assert snap["dashboard"]["stage"] == "Main" and "schedule" in snap
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "state.dash && state.dash.stage ? state.dash.stage" in js
    assert "SW.scheduleOrder((state.schedule && state.schedule.items) || [], schedStage())" in js


def test_admin_card_names_cover_every_known_card():
    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    block = re.search(r"const CARD_INFO = \{(.*?)\n  \};", js, re.S).group(1)
    assert sorted(re.findall(r"^\s{4}([a-z_]+): \[", block, re.M)) == sorted(cards.KNOWN_CARDS)
    assert "Wall Clock is never switched on by default" in js


# --------------------------------------------- speed of sound: tested range
@pytest.mark.parametrize(("t", "p", "issues"), [
    (0.0, 75_000.0, []), (30.0, 102_000.0, []), (15.0, 101_325.0, []),   # bounds are inside
    (-0.1, 101_325.0, ["temperature_low"]), (30.1, 101_325.0, ["temperature_high"]),
    (20.0, 74_999.0, ["pressure_low"]), (20.0, 102_001.0, ["pressure_high"]),
    (35.0, 70_000.0, ["temperature_high", "pressure_low"]), (-5.0, 103_000.0, ["temperature_low", "pressure_high"]),
])
def test_speed_of_sound_range_issues(t, p, issues):
    assert acoustics.speed_of_sound_range_issues(t, p) == issues


def test_water_vapour_stays_inside_cramers_limit_within_the_range():
    # Why only temperature and pressure are flagged: saturated air at the warm, low-pressure
    # corner is still below the formula's water-vapour limit of 0.06.
    assert acoustics.water_vapor_mole_fraction(30.0, 75_000.0, 100.0) < 0.06


def _site_hub(tmp_path, temp_c, pressure_pa=None, altitude_m=0.0):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    h.config.site.altitude_m = altitude_m
    h.register_device(Device("n", "n", "test", status=Status.OK))
    h.register_entity(Entity("n.temperature", "n", "t", Kind.TEMPERATURE))
    h.update_state("n.temperature", temp_c)
    if pressure_pa is not None:
        h.register_entity(Entity("n.pressure", "n", "p", Kind.PRESSURE))
        h.update_state("n.pressure", pressure_pa)
    h.compute_site()
    return h


def test_site_flags_speed_of_sound_outside_the_tested_range(tmp_path):
    hot = _site_hub(tmp_path / "hot", 34.0, 101_000.0)
    snap = hot.snapshot()["site"]
    assert snap["c_out_of_range"] is True and snap["c_out_of_range_bounds"] == ["temperature_high"]
    assert hot.entities["site.speed_of_sound"].value == pytest.approx(acoustics.speed_of_sound(34.0, 50.0, 101_000.0))
    assert hot.alarms.to_list() == []  # a note, never an alarm
    hot.recorder.close()

    ok = _site_hub(tmp_path / "ok", 21.0, 101_000.0)
    assert ok.snapshot()["site"]["c_out_of_range"] is False and ok.site_meta["c_out_of_range_bounds"] == []
    ok.recorder.close()

    high_site = _site_hub(tmp_path / "alt", 10.0, altitude_m=3000.0)  # ISA pressure about 70 kPa
    assert high_site.site_meta["c_out_of_range_bounds"] == ["pressure_low"]
    high_site.recorder.close()


def test_site_flag_is_off_without_a_temperature(tmp_path):
    h = Hub(tmp_path)
    assert h.snapshot()["site"]["c_out_of_range"] is False
    h.compute_site()
    assert h.site_meta["c_out_of_range"] is False and h.entities["site.speed_of_sound"].value is None
    h.recorder.close()


def test_dashboard_shows_the_quiet_range_note():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "Outside the formula's tested range (" in js and "): figures are approximate" in js
    assert "0–30 °C" in js and "75–102 kPa" in js


def test_address_prefers_the_network_the_request_arrived_on(tmp_path):
    # A PC with two network cards must hand out the address on the tablet's own network.
    hub = Hub(tmp_path, emulate=True)
    app = create_app(hub, lan_addresses=lambda: list(LAN))
    app.state.port = 8123
    with TestClient(app, base_url="http://198.51.100.7:8123") as c:
        c.post("/api/admin/setup", json={"pin": PIN})
        assert c.put("/api/admin/dashboards",
                     json=[{"slug": "foh", "cards": ["env_tiles", "connect_footer"]}]).status_code == 200
        c.cookies.clear()
        assert c.get("/api/dashboard/foh/address").json() == {"url": "http://198.51.100.7:8123/d/foh"}


@pytest.mark.parametrize("field,value", [
    ("stage", "Main‮Stage"), ("stage", "Main\nStage"), ("stage", "Main​Stage"),
    ("title", "FOH‮"), ("title", "x" * 81)])
def test_dashboard_put_rejects_hidden_characters_and_long_titles(client, field, value):
    _admin(client)
    r = _put(client, [{"slug": "foh", "cards": ["env_tiles"], field: value}])
    assert r.status_code == 422
    assert value not in r.text


def test_dashboard_put_refuses_unknown_fields(client):
    _admin(client)
    assert _put(client, [{"slug": "foh", "cards": ["env_tiles"], "surprise": 1}]).status_code == 422
