"""Several GLOBCON cards on one dashboard ("globcon_meters", "globcon_meters#2" ...): card id rules, per-card options,
the API, a round trip, an older build ignoring the extras, one subscription per controller, the node test of the id
helpers, and the static pieces. Hand-made data."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core import cards
from stagewatch.core.config import Config, ConfigStore, Dashboard, GlobconCardOptions
from stagewatch.core.hub import Hub
from stagewatch.integrations.globcon import seed_emulate_globcon, wanted_controllers
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
G = "globcon_meters"


# ------------------------------------------------------------------------------------- card ids
def test_instance_ids_and_the_lookups_that_ignore_the_suffix():
    assert cards.card_base(f"{G}#2") == G and cards.card_base("chart") == "chart"
    for ok in (G, f"{G}#2", f"{G}#9", f"{G}#10", f"{G}#16", "chart"):
        assert cards.valid_card_id(ok), ok
    for bad in (f"{G}#1", f"{G}#0", f"{G}#17", f"{G}#02", f"{G}#", "chart#2", "#2", f"{G}#2#3", f"{G} #2", 5, None):
        assert not cards.valid_card_id(bad), bad
    assert cards.is_instance_id(f"{G}#2") and not cards.is_instance_id(G)
    assert cards.unknown_cards([G, f"{G}#3", "bogus#2"]) == ["bogus#2"]


def test_card_list_validation_counts_instances_and_keeps_the_limit():
    assert cards.strict_cards_error(["env_tiles", G, f"{G}#2", f"{G}#3"]) is None
    assert cards.strict_cards_error([f"{G}#2"]) is None
    for bad in (["chart", "chart#2"], [G, f"{G}#2", f"{G}#2"], [f"{G}#1"], [f"{G}#17"]):
        assert cards.strict_cards_error(bad) is not None, bad
    assert cards.strict_cards_error([G] + [f"{G}#{n}" for n in range(2, 17)]) is None            # exactly 16
    assert cards.strict_cards_error(["chart"] + [G] + [f"{G}#{n}" for n in range(2, 17)]) is not None   # 17


def test_sizes_follow_the_card_kind():
    out, dropped = cards.clean_card_sizes({G: "half", f"{G}#2": "half", "chart#2": "half", f"{G}#2x": "half"})
    assert out == {G: "half", f"{G}#2": "half"} and dropped == 2
    assert cards.strict_card_sizes_error({f"{G}#2": "half"}) is None
    assert cards.strict_card_sizes_error({"chart#2": "half"}) is not None
    assert cards.strict_card_sizes_error({f"{G}#2": "tiny"}) is not None


# ------------------------------------------------------------------------------------- options model
def test_lenient_load_of_cards_and_extra_options(caplog):
    d = Dashboard.model_validate({"slug": "x", "cards": [G, f"{G}#2", "chart#2", f"{G}#2", f"{G}#40"],
                                  "globcon_more": {f"{G}#2": {"controller": 4, "mode": "lights", "lights": [{"strip": 1}]},
                                                   f"{G}#3": {"controller": 99}, "chart#2": {}, "junk": {}}})
    assert d.cards == [G, f"{G}#2"]
    assert d.globcon_more[f"{G}#2"].controller == 4 and d.globcon_more[f"{G}#2"].mode == "lights"
    assert d.globcon_more[f"{G}#3"].controller == 1            # unusable options fall back to the defaults
    assert "chart#2" not in d.globcon_more and "junk" not in d.globcon_more
    assert "99" not in caplog.text
    for junk in ("x", 5, [1], None):
        assert Dashboard.model_validate({"slug": "x", "globcon_more": junk}).globcon_more == {}


def test_options_belong_to_their_own_card():
    d = Dashboard(slug="x", cards=[G, f"{G}#2", f"{G}#3"], globcon=GlobconCardOptions(controller=1),
                  globcon_more={f"{G}#2": GlobconCardOptions(controller=5, mode="lights")})
    assert d.globcon_for(G).controller == 1
    assert d.globcon_for(f"{G}#2").controller == 5 and d.globcon_for(f"{G}#2").mode == "lights"
    assert d.globcon_for(f"{G}#3") == GlobconCardOptions()            # listed but never set: the defaults
    assert Dashboard(slug="x").globcon_more == {}


def test_one_subscription_per_controller_however_many_cards():
    cfg = Config()
    cfg.dashboards[0].cards = [G, f"{G}#2", f"{G}#3"]
    cfg.dashboards[0].globcon = GlobconCardOptions(controller=3)
    cfg.dashboards[0].globcon_more = {f"{G}#2": GlobconCardOptions(controller=3, mode="lights"), f"{G}#3": GlobconCardOptions(controller=7)}
    cfg.dashboards[1].cards = [f"{G}#2"]
    cfg.dashboards[1].globcon_more = {f"{G}#2": GlobconCardOptions(controller=7)}
    assert wanted_controllers(cfg) == [2, 6]
    cfg.dashboards[0].cards = [G]                                     # the extra cards go: only their controllers go with them
    cfg.dashboards[1].cards = []
    assert wanted_controllers(cfg) == [2]


def test_emulate_seed_shows_two_cards_and_keeps_the_rules():
    cfg = Config()
    assert seed_emulate_globcon(cfg) is True
    wall = cfg.dashboard("wall")
    assert G in wall.cards and f"{G}#2" in wall.cards and wall.globcon_for(f"{G}#2").mode == "lights"
    assert cards.strict_cards_error(wall.cards) is None
    assert seed_emulate_globcon(cfg) is False
    assert wanted_controllers(cfg) == [0, 1]


# ------------------------------------------------------------------------------------- saved file
def _save(tmp_path, cfg):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    return path


def test_round_trip_keeps_the_extra_cards_and_their_options(tmp_path):
    cfg = Config()
    cfg.dashboards[0].cards = ["env_tiles", G, f"{G}#2"]
    cfg.dashboards[0].card_sizes = {G: "half", f"{G}#2": "half"}
    cfg.dashboards[0].globcon_more = {f"{G}#2": GlobconCardOptions.model_validate({"controller": 6, "range": "9-16", "mode": "lights", "lights": [{"strip": 9, "colour": "red"}]})}
    path = _save(tmp_path, cfg)
    loaded = ConfigStore(path).load().dashboards[0]
    assert loaded.cards == ["env_tiles", G, f"{G}#2"] and loaded.card_sizes == {G: "half", f"{G}#2": "half"}
    assert loaded.globcon_more == cfg.dashboards[0].globcon_more
    assert loaded.globcon_for(f"{G}#2").lights[0].colour == "red"


def test_an_older_file_without_the_key_loads_unchanged(tmp_path):
    path = _save(tmp_path, Config())
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for row in raw["dashboards"]:
        row.pop("globcon_more", None)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert [d.globcon_more for d in ConfigStore(path).load().dashboards] == [{}, {}, {}]


def test_schema_versions_are_unchanged():
    from stagewatch.version import CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION
    assert (CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION) == (2, 4)


def test_an_older_build_ignores_the_extras_and_keeps_everything_else(tmp_path):
    """The previous build's loader, written out: card ids are kept only if [a-z_]{1,32}, unknown keys are ignored.
    So it drops 'globcon_meters#2', ignores 'globcon_more', and shows the first card exactly as before."""
    cfg = Config()
    cfg.site.name = "Kept venue"
    cfg.dashboards[0].cards = ["env_tiles", G, f"{G}#2"]
    cfg.dashboards[0].globcon = GlobconCardOptions(controller=2, range="1-8")
    cfg.dashboards[0].globcon_more = {f"{G}#2": GlobconCardOptions(controller=9, mode="lights")}
    raw = yaml.safe_load(_save(tmp_path, cfg).read_text(encoding="utf-8"))
    row = raw["dashboards"][0]
    old_cards = [c for c in row["cards"] if cards.CARD_ID_RE.fullmatch(c)]
    assert old_cards == ["env_tiles", G]
    assert row["globcon"]["controller"] == 2 and "globcon_more" in row and raw["site"]["name"] == "Kept venue"


# ------------------------------------------------------------------------------------- the API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


def _more(**kw):
    return {"controller": 4, "strips": 8, "range": "1-8", "mode": "lights", "lights": [{"strip": 1, "threshold_db": -30, "colour": "red", "icon": "camera", "hold_s": 2}], **kw}


def test_put_saves_several_cards_prunes_removed_ones_and_keeps_them_when_not_sent(client):
    rows = client.get("/api/info").json()["dashboards"]
    rows[0].update(cards=[G, f"{G}#2", f"{G}#3"], card_sizes={f"{G}#2": "half"}, globcon_more={f"{G}#2": _more(), f"{G}#3": _more(controller=5, mode="meters", lights=[])})
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    d = client.hub.config.dashboards[0]
    assert d.cards == [G, f"{G}#2", f"{G}#3"] and d.globcon_for(f"{G}#2").controller == 4 and d.card_sizes == {f"{G}#2": "half"}
    pub = client.get(f"/api/dashboard/{rows[0]['slug']}").json()
    assert pub["globcon_more"][f"{G}#2"]["lights"][0]["colour"] == "red"
    without = [{k: v for k, v in r.items() if k != "globcon_more"} for r in rows]                 # an older admin page
    assert client.put("/api/admin/dashboards", json=without).status_code == 200
    assert set(client.hub.config.dashboards[0].globcon_more) == {f"{G}#2", f"{G}#3"}
    rows[0]["cards"] = [G, f"{G}#3"]                                                                # #2 removed from the list
    assert client.put("/api/admin/dashboards", json=rows).status_code == 200
    assert set(client.hub.config.dashboards[0].globcon_more) == {f"{G}#3"}


def test_put_is_strict_about_ids_and_options_without_echoing_them(client):
    rows = client.get("/api/info").json()["dashboards"]
    base = rows[0]
    for cards_bad in ([G, G], [f"{G}#1"], ["chart#2"], [G, f"{G}#2", f"{G}#2"], [f"{G}#17"]):
        assert client.put("/api/admin/dashboards", json=[{**base, "cards": cards_bad}] + rows[1:]).status_code == 422, cards_bad
    for more in ({f"{G}#2": _more(controller=17)}, {f"{G}#2": _more(range="3-5")}, {"chart#2": _more()}, {"bad": _more()},
                 {f"{G}#2": _more(mode="rainbow-xyz")}, {f"{G}#2": _more(lights=[{"strip": 1}, {"strip": 1}])},
                 {f"{G}#2": _more(range="9-16")}, {f"{G}#2": "text"}, ["x"], {f"{G}#2": {**_more(), "extra": 1}}):
        r = client.put("/api/admin/dashboards", json=[{**base, "cards": [G, f"{G}#2"], "globcon_more": more}] + rows[1:])
        assert r.status_code == 422, more
        assert "rainbow-xyz" not in r.text
    assert client.hub.config.dashboards[0].globcon_more == {}


# ------------------------------------------------------------------------------------- static pieces
def test_dashboard_page_ids_and_styles_cover_every_card():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    assert html.count('id="globcon-card"') == 1
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "#globcon-card" not in css and ".globcon-card.gc-s-frozen" in css
    assert "ensureInstance" in (STATIC / "dashboard.js").read_text(encoding="utf-8")


def test_old_ipad_syntax_in_the_changed_scripts():
    for name in ("dashboard.js", "common.js", "cards-editor.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        code = re.sub(r"//[^\n]*|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`", "", text)
        assert not re.search(r"\?\.[a-zA-Z_(\[]|\?\?|\bstructuredClone\b|\.at\(|\.replaceAll\(", code), name


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_test_of_the_id_helpers():
    r = subprocess.run(["node", str(ROOT / "tests" / "js" / "globcon_multi_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
