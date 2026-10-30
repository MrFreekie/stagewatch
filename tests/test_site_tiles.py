"""Site readings tile choice (Dashboard.tiles) and today's highs and lows (/api/site/extremes)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import cards, sitetime
from stagewatch.core.config import Dashboard
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

PIN = "4821"


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub, lan_addresses=lambda: ["192.168.1.10"])) as c:
        c.hub = hub
        yield c


def _put(c, **extra):
    return c.put("/api/admin/dashboards", json=[{"slug": "foh", **extra}])


def test_default_is_empty_and_loading_is_lenient():
    assert Dashboard(slug="x").tiles == []
    assert Dashboard(slug="x", tiles=["pressure", "pressure", "Bad!", "newer_tile", 5]).tiles == ["pressure", "newer_tile"]
    assert Dashboard(slug="x", tiles="nope").tiles == []
    assert set(cards.DEFAULT_TILES) < set(cards.SITE_TILES) and "highs_lows" not in cards.DEFAULT_TILES


def test_api_is_strict_saves_and_keeps_tiles(client):
    client.post("/api/admin/setup", json={"pin": PIN})
    for bad in (["nope"], ["pressure", "pressure"], "pressure", list(cards.SITE_TILES) + ["temperature"]):
        assert _put(client, tiles=bad).status_code == 422
    assert _put(client, tiles=["highs_lows", "temperature"]).status_code == 200
    assert client.get("/api/dashboard/foh").json()["tiles"] == ["highs_lows", "temperature"]
    assert _put(client, title="Renamed").status_code == 200       # an older page that sends no tiles keeps them
    assert client.hub.config.dashboard("foh").tiles == ["highs_lows", "temperature"]


def test_extremes_are_the_site_local_day_only_and_gaps_stay_gaps(client):
    rec, now = client.hub.recorder, time.time()
    start = sitetime.wall_ts(sitetime.local(now, client.hub.config.site).date(), __import__("datetime").time(0, 0), client.hub.config.site)
    rec.record_state("site.temperature", 99.0, start - 60)     # yesterday: ignored
    rec.record_state("site.temperature", 12.5, start + 10)
    rec.record_state("site.temperature", 21.0, start + 20)
    rec.record_state("site.temperature", 17.0, start + 30)
    r = client.get("/api/site/extremes").json()
    assert r["temperature"] == {"low": [pytest.approx(start + 10), 12.5], "high": [pytest.approx(start + 20), 21.0]}
    assert r["since"] == pytest.approx(start)
    assert r["pressure"] is None or r["pressure"]["low"][0] >= start        # never back-filled
    assert client.hub.recorder.extremes("site.nothing", start, now) is None
