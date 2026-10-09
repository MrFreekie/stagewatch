"""Half-size cards: Dashboard.card_sizes (core/cards.py HALF_CAPABLE), the API, the public
payload, an older config, a damaged one, and the static pieces (layout CSS, admin control)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core import cards
from stagewatch.core.config import Config, ConfigStore, Dashboard
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
DASHBOARD_FIELDS = {"slug", "title", "layout", "allow_marker", "allow_ack", "cards", "stage", "clock_style", "card_sizes"}


def test_default_is_full_everywhere_and_the_allow_list_is_small():
    assert Dashboard(slug="x").card_sizes == {}
    assert cards.HALF_CAPABLE == ("wall_clock", "ontime_timer", "ontime_rundown")
    assert set(cards.HALF_CAPABLE) <= set(cards.KNOWN_CARDS)


def test_load_keeps_good_entries_and_drops_the_rest_with_a_count_only_log(caplog):
    caplog.set_level(logging.WARNING)
    d = Dashboard.model_validate({"slug": "x", "card_sizes": {
        "wall_clock": "half", "ontime_timer": "full", "chart": "half", "schedule": "half",
        "wall_clock_secret_name": "half", "env_tiles": "huge"}})
    assert d.card_sizes == {"wall_clock": "half", "ontime_timer": "full"}
    assert "4 entries" in caplog.text and "chart" not in caplog.text and "huge" not in caplog.text
    for junk in ("half", 5, ["wall_clock"], None):
        assert Dashboard.model_validate({"slug": "x", "card_sizes": junk}).card_sizes == {}
    assert Dashboard.model_validate({"slug": "x", "card_sizes": {"wall_clock": "Half", "ontime_timer": 1, 3: "half"}}).card_sizes == {}


def test_an_older_config_without_the_field_loads_unchanged(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = Config()
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for row in raw["dashboards"]:
        row.pop("card_sizes", None)          # what 0.3.x wrote
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    loaded = ConfigStore(path).load()
    assert [d.card_sizes for d in loaded.dashboards] == [{}, {}, {}]
    assert [d.cards for d in loaded.dashboards] == [d.cards for d in Config().dashboards]


def test_damaged_sizes_in_a_saved_file_reset_only_themselves(tmp_path):
    cfg = Config()
    cfg.site.name = "Kept venue"
    cfg.dashboards[0].card_sizes = {"wall_clock": "half"}
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["dashboards"][0]["card_sizes"] == {"wall_clock": "half"}
    raw["dashboards"][0]["card_sizes"] = ["nonsense", 4]
    raw["dashboards"][1]["card_sizes"] = {"ontime_timer": "half", "bogus": "half"}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    store2 = ConfigStore(path)
    loaded = store2.load()
    assert not store2.recovery_required and loaded.site.name == "Kept venue" and len(loaded.dashboards) == 3
    assert loaded.dashboards[0].card_sizes == {}
    assert loaded.dashboards[1].card_sizes == {"ontime_timer": "half"}
    assert loaded.dashboards[0].cards == cfg.dashboards[0].cards


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


def test_put_round_trips_is_strict_and_is_kept_when_not_sent(client):
    rows = client.get("/api/info").json()["dashboards"]
    assert all(r["card_sizes"] == {} for r in rows)
    rows[0]["card_sizes"] = {"wall_clock": "half", "ontime_timer": "full"}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    assert client.hub.config.dashboards[0].card_sizes == {"wall_clock": "half", "ontime_timer": "full"}
    for bad in ({"chart": "half"}, {"wall_clock": "tiny"}, {"wall_clock": None}, ["wall_clock"], "half", {"nope": "full"}):
        r = client.put("/api/admin/dashboards", json=[{**rows[0], "card_sizes": bad}] + rows[1:])
        assert r.status_code == 422, bad
        assert "tiny" not in r.text
    assert client.hub.config.dashboards[0].card_sizes == {"wall_clock": "half", "ontime_timer": "full"}
    without = [{k: v for k, v in row.items() if k != "card_sizes"} for row in rows]   # an older admin page
    assert client.put("/api/admin/dashboards", json=without).status_code == 200
    assert client.hub.config.dashboards[0].card_sizes == {"wall_clock": "half", "ontime_timer": "full"}
    new = client.put("/api/admin/dashboards", json=without + [{"slug": "extra"}])
    assert new.status_code == 200 and client.hub.config.dashboard("extra").card_sizes == {}
    rows[0]["card_sizes"] = {}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200      # back to full
    assert client.hub.config.dashboards[0].card_sizes == {}


def test_public_dashboard_payload_has_exactly_these_fields(client):
    rows = client.get("/api/info").json()["dashboards"]
    rows[0]["card_sizes"] = {"ontime_timer": "half"}
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    pub = client.get(f"/api/dashboard/{rows[0]['slug']}").json()
    assert set(pub) == DASHBOARD_FIELDS and pub["card_sizes"] == {"ontime_timer": "half"}
    assert set(client.get("/api/info").json()["dashboards"][0]) == DASHBOARD_FIELDS


def test_admin_state_lists_the_cards_that_can_be_half(client):
    assert client.get("/api/admin/state").json()["cards"]["half_capable"] == ["wall_clock", "ontime_timer", "ontime_rundown"]


# ------------------------------------------------------------------ static pieces
def test_layout_css_spans_full_by_default_and_collapses_when_narrow():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    m = re.search(r"@media \(min-width: 900px\) \{(.*?)\n\}", css, re.S)
    assert m, "the two-column grid is only inside the 900 px media query"
    block = m.group(1)
    assert "repeat(2, minmax(0, 1fr))" in block and "grid-column: 1 / -1" in block and 'main > [data-size="half"]' in block
    assert "layout-phone" not in block
    assert "min(160px, 9.5vw)" in css


def test_dashboard_js_marks_half_cards_and_admin_has_the_size_control():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert 'setAttribute("data-size", "half")' in js and "SW.cardIsHalf" in js
    admin = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert "card_sizes" in admin and "half_capable" in admin and "Half-size cards sit side by side" in admin
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert re.search(r'SW\.HALF_CAPABLE = \["wall_clock", "ontime_timer", "ontime_rundown"\]', common)   # same list as core/cards.py


def test_card_size_helper_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "card_size_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
