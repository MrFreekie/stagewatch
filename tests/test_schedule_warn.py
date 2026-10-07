"""Schedule warning times (site setting): validation, default, admin endpoint, public payload,
and what an older build / a damaged file does with the new keys."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core.config import ConfigStore, SiteConfig, _Model
from stagewatch.core.hub import Hub
from stagewatch.version import CONFIG_SCHEMA_VERSION
from stagewatch.web.server import create_app


def test_default_reproduces_the_old_fixed_steps():
    s = SiteConfig()
    assert s.schedule_warn_minutes == [15, 5] and s.schedule_warn_flash is False


def test_valid_lists_are_sorted_descending():
    assert SiteConfig(schedule_warn_minutes=[5, 15, 10]).schedule_warn_minutes == [15, 10, 5]
    assert SiteConfig(schedule_warn_minutes=[1]).schedule_warn_minutes == [1]
    assert SiteConfig(schedule_warn_minutes=[240]).schedule_warn_minutes == [240]
    assert len(SiteConfig(schedule_warn_minutes=list(range(1, 9))).schedule_warn_minutes) == 8


@pytest.mark.parametrize("bad", [[], list(range(1, 10)), [0], [241], [-5], [5, 5], [5.5], ["5"], [True], "15,5", None, [None]])
def test_bad_lists_are_refused(bad):
    with pytest.raises(ValueError):
        SiteConfig(schedule_warn_minutes=bad)


def test_old_config_without_the_keys_loads_with_defaults_and_no_schema_bump(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"schema_version": CONFIG_SCHEMA_VERSION, "site": {"name": "Old"}}), encoding="utf-8")
    cfg = ConfigStore(path).load()
    assert cfg.site.name == "Old" and cfg.site.schedule_warn_minutes == [15, 5] and cfg.site.schedule_warn_flash is False


def test_keys_are_saved_and_survive_a_reload(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    cfg = store.load()
    cfg.site.schedule_warn_minutes = [20, 10, 5]
    cfg.site.schedule_warn_flash = True
    store.config = cfg
    store.save()
    again = ConfigStore(tmp_path / "config.yaml").load()
    assert again.site.schedule_warn_minutes == [20, 10, 5] and again.site.schedule_warn_flash is True


def test_older_build_ignores_the_new_keys():
    # An older build's site model has no such fields; the shared base ignores unknown keys.
    class OldSite(_Model):
        name: str = "x"

    old = OldSite.model_validate({"name": "n", "schedule_warn_minutes": [15, 10, 5], "schedule_warn_flash": True})
    assert old.name == "n" and not hasattr(old, "schedule_warn_minutes")


def test_a_bad_list_in_the_file_is_salvaged_without_losing_the_pin(tmp_path, caplog):
    pin = "pbkdf2_sha256$600000$" + "a" * 32 + "$" + "b" * 64
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"schema_version": CONFIG_SCHEMA_VERSION, "admin": {"pin_hash": pin},
                                    "site": {"name": "Hand edited", "schedule_warn_minutes": [0, 99999]}}), encoding="utf-8")
    cfg = ConfigStore(path).load()
    assert cfg.admin.pin_hash == pin  # never back to first-run setup
    assert cfg.site.schedule_warn_minutes == [15, 5]  # the damaged site section falls back to defaults
    assert "99999" not in caplog.text


@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path)
    with TestClient(create_app(h, manage_hub=False)) as c:
        c.hub = h
        yield c
    h.recorder.close()


SITE = {"name": "Field", "altitude_m": 10, "reference_distance_m": 30, "stale_after_s": 60,
        "smoothing_tau_s": 30, "outlier_reject": True, "timezone": "", "day_rollover": "06:00"}


def test_public_payload_carries_the_steps(client):
    assert client.get("/api/info").json()["schedule_warn"] == {"minutes": [15, 5], "flash": False}
    assert client.get("/api/snapshot").json()["site"]["schedule_warn"] == {"minutes": [15, 5], "flash": False}


def test_admin_site_endpoint_rules(client):
    assert client.put("/api/admin/site", json=SITE).status_code == 401
    assert client.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
    body = {**SITE, "schedule_warn_minutes": [5, 15, 10], "schedule_warn_flash": True}
    assert client.put("/api/admin/site", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.hub.config.site.schedule_warn_minutes == [15, 5]
    r = client.put("/api/admin/site", json=body)
    assert r.status_code == 200 and r.json()["schedule_warn_minutes"] == [15, 10, 5]
    assert client.get("/api/info").json()["schedule_warn"] == {"minutes": [15, 10, 5], "flash": True}
    # callers that don't send the keys keep them
    assert client.put("/api/admin/site", json=SITE).status_code == 200
    assert client.hub.config.site.schedule_warn_minutes == [15, 10, 5] and client.hub.config.site.schedule_warn_flash is True
    for bad in ([], [0], [241], [5, 5], list(range(1, 10)), ["x"]):
        r = client.put("/api/admin/site", json={**SITE, "schedule_warn_minutes": bad})
        assert r.status_code == 422, bad
        assert "schedule_warn_minutes" in r.text
    assert client.hub.config.site.schedule_warn_minutes == [15, 10, 5]
