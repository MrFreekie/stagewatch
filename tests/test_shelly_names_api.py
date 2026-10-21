"""POST /api/admin/shelly/{id}/names: admin only, same origin, suggests names and saves nothing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from stagewatch.core.config import ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.integrations.shelly import ShellyIntegration
from stagewatch.web.server import create_app
from test_shelly import NullTransport
from test_shelly_names import MAC, EmFetch


def test_names_endpoint_needs_admin_and_suggests_without_saving(tmp_path):
    h = Hub(tmp_path)
    h.config.shelly_devices.append(ShellyDeviceConfig(id="workshop", host="192.0.2.60", mac=MAC, name="Workshop",
                                                      channels=[ShellyChannelConfig(key="em1_0", label="Dimmers")]))
    integ = ShellyIntegration(h, fetch=EmFetch(), transport=NullTransport())
    h.add_integration(integ)
    with TestClient(create_app(h)) as c:
        assert TestClient(c.app).post("/api/admin/shelly/workshop/names").status_code == 401
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        assert c.post("/api/admin/shelly/workshop/names", headers={"Origin": "http://evil.example"}).status_code == 403
        r = c.post("/api/admin/shelly/workshop/names")
        assert r.status_code == 200 and r.json()["channels"]["em1_0"] == "CU-10-All-Lights" and r.json()["device"] == "Shelly EM Gen3"
        assert h.config.shelly_devices[0].channels[0].label == "Dimmers"          # nothing saved
        assert c.post("/api/admin/shelly/nope/names").status_code == 404
    h.recorder.close()
