"""Bounds on admin PUT bodies: NaN and infinity, over-long or hidden-character text, too many OSC destinations."""

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app


@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def raw_put(client, path, text):
    return client.put(path, content=text, headers={"content-type": "application/json"})


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "1e999", "20001", "-20001"])
def test_entity_offset_must_be_finite_and_bounded(client, bad):
    eid = next(iter(client.hub.entities))
    r = raw_put(client, f"/api/admin/entities/{eid}", '{"offset": %s}' % bad)
    assert r.status_code == 422, bad
    assert client.put(f"/api/admin/entities/{eid}", json={"offset": 0.5}).status_code != 422


def test_threshold_fields_refuse_nan(client):
    from stagewatch.core.config import Threshold
    for field in ("above", "below", "hysteresis"):
        with pytest.raises(ValueError):
            Threshold.model_validate({"id": "t", "entity": "site.temperature", field: float("nan")})
    assert Threshold.model_validate({"id": "t", "entity": "site.temperature", "above": 30.0}).above == 30.0


def test_site_name_is_short_plain_text(client):
    site = client.hub.config.site.model_dump()
    assert client.put("/api/admin/site", json={**site, "name": "A" * 101}).status_code == 422
    assert client.put("/api/admin/site", json={**site, "name": "Line\nBreak"}).status_code == 422
    assert client.put("/api/admin/site", json={**site, "name": "bad‮name"}).status_code == 422
    ok = client.put("/api/admin/site", json={**site, "name": "A" * 100})
    assert ok.status_code == 200 and ok.json()["name"] == "A" * 100
    assert client.hub.config.site.name == "A" * 100


def test_osc_needs_a_host_and_caps_the_destinations(client):
    def put(dests):
        return client.put("/api/admin/osc", json={"destinations": dests, "per_node": False, "rate_hz": 1.0})
    assert put([{"host": "", "port": 9000}]).status_code == 422
    assert put([{"host": "   ", "port": 9000}]).status_code == 422
    assert put([{"host": "a" * 254, "port": 9000}]).status_code == 422
    assert put([{"host": "bad host", "port": 9000}]).status_code == 422
    assert put([{"host": f"10.0.0.{i}", "port": 9000} for i in range(17)]).status_code == 422
    r = put([{"host": f"10.0.0.{i}", "port": 9000} for i in range(16)])
    assert r.status_code == 200 and len(client.hub.config.osc_out.destinations) == 16
    keep = client.hub.config.osc_out.include_advice
    assert put([{"host": "192.168.1.9", "port": 9000}]).status_code == 200
    assert client.hub.config.osc_out.include_advice == keep
