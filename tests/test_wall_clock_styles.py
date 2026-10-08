"""Wall Clock styles (digits, ring, segments), the PC clock source and the per-dashboard look.

Config and validation (lenient on load, strict at the API), an older build reading a config that
says ``source: pc``, PcClock, the source registry (one source, no silent fallback), the emulate
cycle and demo, and static checks on the page. The view maths (SW.wc.view and friends) is tested
in node: tests/js/wall_clock_test.js, run from here when node is installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import Field, create_model

from stagewatch.core import config as config_mod
from stagewatch.core import wallclock
from stagewatch.core.config import (CLOCK_STYLES, Config, ConfigStore, Dashboard, SiteConfig, WallClockConfig,
                                    WallClockDisplay)
from stagewatch.core.hub import Hub
from stagewatch.integrations.ontime import OntimeIntegration
from stagewatch.integrations.ontime.emulate import EmulatedClock
from stagewatch.web.auth import hash_pin, verify_pin
from stagewatch.web.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
UTC = SiteConfig(timezone="UTC")


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


def assign_card(hub, on=True):
    for d in hub.config.dashboards:
        d.cards = [c for c in d.cards if c != "wall_clock"] + (["wall_clock"] if on and d is hub.config.dashboards[0] else [])


# ------------------------------------------------------------------ config
def test_defaults_pc_digits_and_no_led_colour_choice():
    w = WallClockConfig()
    assert w.source == "pc" and w.display == WallClockDisplay()
    assert w.display.model_dump() == {"hour12": False, "show_date": False, "ring": "sweep", "colon_blink": False}
    assert "led" not in WallClockDisplay.model_fields           # LED looks are fixed red on black
    assert "led" not in Dashboard.model_fields and Dashboard(slug="x").clock_style == "digits"
    assert CLOCK_STYLES == ("digits", "ring", "segments")


def test_a_saved_ontime_source_is_kept_and_a_missing_section_is_the_new_default():
    assert Config.model_validate({"wall_clock": {"source": "ontime", "ontime_url": "http://ontime.local:4001"}}).wall_clock.source == "ontime"
    assert Config.model_validate({}).wall_clock.source == "pc"          # new install
    cfg = Config.model_validate({"wall_clock": {"ontime_url": "http://ontime.local:4001"}})
    assert cfg.wall_clock.source == "pc"                                 # no source stored: the new default


def test_loading_is_lenient_about_looks_this_build_does_not_know():
    d = Dashboard.model_validate({"slug": "x", "clock_style": "hologram"})
    assert d.clock_style == "digits"
    assert Dashboard.model_validate({"slug": "x", "clock_style": 7}).clock_style == "digits"
    assert Dashboard.model_validate({"slug": "x", "clock_style": "segments"}).clock_style == "segments"
    cfg = Config.model_validate({"wall_clock": {"source": "pc", "display": {"ring": "pulse", "hour12": True}}})
    assert cfg.wall_clock.display.ring == "sweep" and cfg.wall_clock.display.hour12 is True
    assert Config.model_validate({"wall_clock": {"display": "nonsense"}}).wall_clock.display == WallClockDisplay()


def test_the_model_itself_is_strict():
    for bad in ({"ring": "pulse"}, {"hour12": "maybe"}, {"colon_blink": 7}):
        with pytest.raises(ValueError):
            WallClockDisplay(**bad)
    with pytest.raises(ValueError):
        WallClockConfig(source="ntp")


# ------------------------------------------------- downgrade: an older build
class _OldWallClock(config_mod._Model):
    """WallClockConfig as the previous release had it: the source can only be "ontime"."""
    source: Literal["ontime"] = "ontime"
    ontime_url: str = "http://127.0.0.1:4001"
    warn_offset_s: float = Field(2.0, ge=1.0, le=60)


def test_an_older_build_reading_source_pc_keeps_the_pin_and_the_rest(tmp_path, monkeypatch, caplog):
    """Downgrade: the old model refuses ``source: pc``. That one section must fall back to its
    defaults through salvage; the PIN, onboarding and every other section stay."""
    OldConfig = create_model("OldConfig", __base__=Config, wall_clock=(_OldWallClock, Field(default_factory=_OldWallClock)))
    cfg = Config()
    cfg.admin.pin_hash = hash_pin("1234")
    cfg.site.name = "Kept venue"
    cfg.wall_clock = WallClockConfig(source="pc", display=WallClockDisplay(hour12=True))
    cfg.dashboards[0].clock_style = "ring"
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.config = cfg
    store.save()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["wall_clock"]["source"] == "pc"

    monkeypatch.setattr(config_mod, "Config", OldConfig)
    caplog.set_level(logging.DEBUG)
    old = ConfigStore(path)
    loaded = old.load()
    assert verify_pin("1234", loaded.admin.pin_hash) and not old.recovery_required   # onboarding stays closed
    assert loaded.site.name == "Kept venue" and len(loaded.dashboards) == 3
    assert loaded.wall_clock.source == "ontime"                    # only this section was reset
    assert (tmp_path / "config.invalid.yaml").exists()             # the original is kept aside
    assert hash_pin("1234") not in caplog.text and "1234" not in caplog.text

    monkeypatch.undo()                                             # upgrade again: the new build is fine
    again = ConfigStore(path).load()
    assert verify_pin("1234", again.admin.pin_hash) and again.site.name == "Kept venue"
    assert again.wall_clock.source == "ontime"                     # what the old build saved
    # (A real older build also forgets the dashboards' clock_style on its next save: it has no such field.)


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def test_put_dashboards_clock_style_round_trips_is_strict_and_is_kept_when_not_sent(client):
    rows = client.get("/api/info").json()["dashboards"]
    assert all(r["clock_style"] == "digits" for r in rows)
    rows[0]["clock_style"] = "ring"
    rows[1]["clock_style"] = "segments"
    r = client.put("/api/admin/dashboards", json=rows)
    assert r.status_code == 200, r.text
    assert [d.clock_style for d in client.hub.config.dashboards] == ["ring", "segments", "digits"]
    assert client.get(f"/api/dashboard/{rows[0]['slug']}").json()["clock_style"] == "ring"      # public: the look only
    for bad in ("hologram", "", None, 5, "Ring"):
        r = client.put("/api/admin/dashboards", json=[{**rows[0], "clock_style": bad}] + rows[1:])
        assert r.status_code == 422, bad
    assert client.hub.config.dashboards[0].clock_style == "ring"
    without = [{k: v for k, v in row.items() if k != "clock_style"} for row in rows]   # an older admin page
    assert client.put("/api/admin/dashboards", json=without).status_code == 200
    assert [d.clock_style for d in client.hub.config.dashboards] == ["ring", "segments", "digits"]
    new = client.put("/api/admin/dashboards", json=without + [{"slug": "extra"}])
    assert new.status_code == 200 and client.hub.config.dashboard("extra").clock_style == "digits"
    assert client.put("/api/admin/dashboards", json=[{**without[0], "led": "green"}]).status_code == 422   # no LED colour field


def test_put_wall_clock_accepts_pc_and_validates_the_look(client):
    ok = {"source": "pc", "ontime_url": "http://127.0.0.1:4001", "warn_offset_s": 2,
          "display": {"hour12": True, "show_date": True, "ring": "fill", "colon_blink": True}}
    r = client.put("/api/admin/wall-clock", json=ok)
    assert r.status_code == 200 and r.json()["display"]["ring"] == "fill"
    assert client.hub.config.wall_clock.source == "pc" and client.hub.config.wall_clock.display.hour12 is True
    state = client.get("/api/admin/state").json()
    assert state["config"]["wall_clock"]["display"]["show_date"] is True
    for bad in ({"source": "ntp"}, {"source": "gps"}, {"display": {"ring": "pulse"}}, {"display": {"hour12": "yes please"}},
                {"display": {"colon_blink": 3}}, {"display": "wide"}):
        assert client.put("/api/admin/wall-clock", json={**ok, **bad}).status_code == 422, bad
    assert client.hub.config.wall_clock.display.ring == "fill"           # nothing changed by the refusals
    r = client.put("/api/admin/wall-clock", json={"source": "ontime", "display": {"ring": "x" * 500}})
    assert r.status_code == 422 and "x" * 50 not in r.text              # the value is never echoed


def test_wall_clock_writes_need_admin_and_same_origin(client):
    body = {"source": "pc"}
    evil = {"origin": "http://evil.example"}
    assert client.put("/api/admin/wall-clock", json=body, headers=evil).status_code == 403
    assert client.put("/api/admin/dashboards", json=[{"slug": "foh"}], headers=evil).status_code == 403
    client.cookies.clear()
    assert client.put("/api/admin/wall-clock", json=body).status_code == 401
    assert client.put("/api/admin/dashboards", json=[{"slug": "foh"}]).status_code == 401


# ------------------------------------------------------------------ PcClock
def test_pc_clock_is_site_time_with_no_offset_and_never_warns():
    now = [1_790_000_000.123]
    pc = wallclock.PcClock(lambda: UTC, clock=lambda: now[0])
    r = pc.latest()
    assert r.status == "ok" and r.detail == ""
    assert r.clock_ms == int(now[0] % 86400 * 1000) or abs(r.clock_ms - now[0] % 86400 * 1000) < 1
    msg = wallclock.reading_message(pc, r, UTC, 1.0)
    assert msg["offset_s"] == 0.0 and msg["warn"] is False and msg["source"] == "pc" and msg["label"] == "Stagewatch PC"
    zone = SiteConfig(timezone="Europe/London")      # still no offset in a zone with daylight saving
    msg = wallclock.reading_message(pc, wallclock.PcClock(lambda: zone, clock=lambda: now[0]).latest(), zone, 1.0)
    assert msg["offset_s"] == 0.0 and msg["warn"] is False


async def test_pc_source_runs_while_the_card_is_assigned_with_no_device_and_no_alarm(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    await hub.start()
    try:
        assert hub.config.wall_clock.source == "pc" and hub.snapshot()["wall_clock"] is None
        before = set(hub.devices)
        assign_card(hub)
        hub.save_config()
        await until(lambda: hub.wall_clock.active)
        snap = hub.snapshot()["wall_clock"]
        assert snap["source"] == "pc" and snap["status"] == "ok" and snap["warn"] is False and snap["offset_s"] == 0.0
        assert set(snap) == {"source", "label", "clock_ms", "received_at", "status", "offset_s", "warn", "display"}
        assert snap["display"] == WallClockDisplay().model_dump()
        text = json.dumps(snap)
        assert "ontime_url" not in text and "127.0.0.1" not in text
        assert set(hub.devices) == before and hub.alarms.to_list() == []          # no device, no alarm
        assert hub.wall_clock.admin_status()["source"] == "pc"
        hub.config.wall_clock.display.hour12 = True                                 # a look change reaches the feed
        assert hub.wall_clock.snapshot()["display"]["hour12"] is True
        assign_card(hub, on=False)
        hub.save_config()
        await until(lambda: not hub.wall_clock.active)
    finally:
        await hub.stop()


async def test_one_source_per_installation_and_no_silent_fallback(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.config.wall_clock.source = "ontime"
    now = [1_790_000_000.0]
    inner = EmulatedClock(lambda: UTC, clock=lambda: now[0], cycle=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True, inner=inner))
    assign_card(hub)
    await hub.start()
    try:
        assert hub.wall_clock.snapshot()["source"] == "ontime"
        now[0] += 85                                           # the emulated source drops out
        snap = hub.wall_clock.snapshot()
        assert snap["source"] == "ontime" and snap["status"] == "offline" and snap["clock_ms"] is None
        assert hub.wall_clock.admin_status()["source"] == "ontime"    # still Ontime: never the PC
        # Choosing the PC is an explicit admin change and stops Ontime.
        hub.config.wall_clock.source = "pc"
        hub.save_config()
        await until(lambda: hub.wall_clock.admin_status()["source"] == "pc")
        assert "ontime" not in hub.devices
        # Ontime chosen but not installed: nothing runs and nothing else stands in.
        hub.config.wall_clock.source = "ontime"
        hub.integrations.pop("ontime")
        hub.save_config()
        await until(lambda: not hub.wall_clock.active)
        assert hub.wall_clock.snapshot() is None
    finally:
        await hub.stop()


# ------------------------------------------------------------------ emulate
def test_emulated_ontime_cycle_shows_live_differs_stale_and_offline():
    now = [1_790_006_400.0]       # a whole second
    clock = EmulatedClock(lambda: UTC, clock=lambda: now[0], cycle=True)
    t0 = now[0]

    def at(sec):
        now[0] = t0 + sec
        r = clock.latest()
        return r, (wallclock.compute_offset_s(r.clock_ms, r.received_at, UTC) if r.clock_ms is not None else None)

    r, off = at(10)
    assert r.status == "ok" and off == pytest.approx(0.5)
    r, off = at(55)
    assert r.status == "ok" and off == pytest.approx(3.7)                 # 0.5 s lead plus 3.2 s ahead
    msg = wallclock.reading_message(clock, r, UTC, 2.0)
    assert msg["warn"] is True
    r, _ = at(72)
    assert r.status == "ok" and 70.0 - 65.0 <= now[0] - r.received_at <= 8.0   # nothing new for a while: stale
    r, _ = at(88)
    assert r.status == "offline" and r.clock_ms is None
    r, off = at(100)
    assert r.status == "ok" and off == pytest.approx(0.5)
    r, off = at(120 + 10)                                                   # and round again
    assert r.status == "ok" and off == pytest.approx(0.5)


def test_emulate_demo_gives_each_stock_dashboard_a_style_once():
    cfg = Config()
    assert wallclock.seed_emulate_demo(cfg) is True
    assert {d.slug: d.clock_style for d in cfg.dashboards} == {"foh": "segments", "phone": "digits", "wall": "ring"}
    assert all("wall_clock" in d.cards for d in cfg.dashboards) and cfg.wall_clock.source == "ontime"
    assert wallclock.seed_emulate_demo(cfg) is False                        # never twice
    custom = Config(dashboards=[Dashboard(slug="mine")])
    assert wallclock.seed_emulate_demo(custom) is False and custom.wall_clock.source == "pc"
    assert not wallclock.card_assigned(Config())                            # a plain hub never gets the card by itself


# ------------------------------------------------------------------ static
def test_page_loads_the_clock_script_between_common_and_dashboard():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    order = [html.index(f'/static/{n}.js') for n in ("common", "wallclock", "dashboard")]
    assert order == sorted(order)


def test_led_colours_are_one_fixed_red_on_black_with_no_theme_override():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert len(re.findall(r"--led-on\s*:", css)) == 1                       # defined once, never per theme
    assert "data-led" not in css and "data-led" not in (STATIC / "wallclock.js").read_text(encoding="utf-8")
    assert "--led-bg: #000" in css


def test_clock_script_uses_only_safe_dom_and_svg_and_no_animation_loop():
    js = re.sub(r"//.*", "", (STATIC / "wallclock.js").read_text(encoding="utf-8"))
    assert "requestAnimationFrame" not in js and "<canvas" not in js and "innerHTML" not in js
    assert "createElementNS" in js and "prefers-reduced-motion" in js
    assert "http://" not in js.replace("http://www.w3.org/2000/svg", "")    # nothing is fetched from anywhere


def test_admin_has_the_source_choice_the_look_options_and_the_per_dashboard_look():
    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert 'value: "pc"' in js and "colon_blink" in js and "show_date" in js and "hour12" in js
    assert "clock_style: clockStyle.value" in js and '"segments"' in js


def test_view_maths_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "wall_clock_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
