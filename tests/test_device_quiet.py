"""Quiet this unit on the Device errors card: totals, matching, saving, the API and the report line."""

from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core import device_errors as de
from stagewatch.core.config import QUIET_MAX, Config, ConfigStore, salvage
from stagewatch.core.hub import Hub
from stagewatch.core.recorder import Recorder
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.web.server import create_app
from test_device_errors import FIX, SITE, T, alarm, go, imported, rows

NEBRA = "Meyer Sound Nebra"


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "t.sqlite3")
    yield r
    r.close()


def nebra_rows(rec, name, kinds):
    """Nebra disconnect/reconnect lines for one unit: kinds is [(seconds after T, kind)]."""
    words = {"disconnect": "disconnected from the network", "reconnect": "reconnected to the network"}
    rws = [(T + s, T + s, 4, NEBRA, f"Nebra unit {name}: {words[k]}",
            json.dumps({"level": "x", "stagewatch_level": 2 if k == "disconnect" else 0, "unit": name, "kind": k,
                        "cleared": k == "reconnect", "client": False})) for s, k in kinds]
    rec.add_vendor_import(rec.show_id, "n1", "nebra:Events.csv", rws, [])


def quiet_summary(rec, quiet):
    return de.summarise(rec, [rec.show_id], {}, T + 10000, None, quiet)


def test_quiet_unit_is_advisory_and_leaves_totals(rec):
    imported(rec, "Log_a.xml", "2026-08-08", "sample_2026-08-08.xml")
    loud = go(rec, devs={})
    u = rows(loud)["lanwm:033"]
    assert (u["stops"], u["alerts"]) == (1, 1)
    out = quiet_summary(rec, {"lanwm:033"})
    q = rows(out)["lanwm:033"]
    assert (q["stops"], q["alerts"], q["advisories"], q["problems"]) == (0, 0, 2, 2)
    assert q["quieted"] and (q["quieted_stops"], q["quieted_alerts"]) == (1, 1)
    assert q["seconds_in_fault"] == u["seconds_in_fault"]                # time in fault is kept
    assert {e["level"] for e in q["episodes"]} == {"Advisory"} and q["last"]["level"] == "Advisory"
    assert out["totals"]["stops"] == loud["totals"]["stops"] - 1
    assert out["totals"]["alerts"] == loud["totals"]["alerts"] - 1
    assert out["totals"]["problems"] == loud["totals"]["problems"]       # still listed and counted as problems
    assert not rows(out)["lanwm:104"]["quieted"]


def test_nebra_unit_matches_case_and_spacing(rec):
    nebra_rows(rec, "Joyned MU16", [(0, "disconnect"), (30, "reconnect"), (100, "disconnect"), (110, "reconnect")])
    assert rows(go(rec, devs={}))["nebra:Joyned MU16"]["alerts"] == 2
    out = quiet_summary(rec, ["nebra:joyned  mu16"])
    r = rows(out)["nebra:Joyned MU16"]
    assert (r["alerts"], r["advisories"], r["seconds_in_fault"], r["quieted"]) == (0, 2, 40.0, True)
    assert out["totals"]["alerts"] == 0


def test_stagewatch_devices_cannot_be_quieted(rec):
    alarm(rec, T, "device:node1", "raise")
    alarm(rec, T + 5, "device:node1", "clear")
    out = de.summarise(rec, [rec.show_id], {"node1": {"name": "FOH node", "kind": "Sensor", "status": "ok"}},
                       T + 100, None, {"device:node1"})
    r = rows(out)["device:node1"]
    assert not r["quieted"] and r["dropouts"] == 1


def test_default_is_loud(rec):
    nebra_rows(rec, "Joyned MU16", [(0, "disconnect")])
    assert go(rec, devs={})["totals"]["alerts"] == 1


# ------------------------------------------------------------------ config
def test_config_without_the_field_loads_and_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"schema_version": Config().schema_version, "mdns_name": "x"}), encoding="utf-8")
    assert ConfigStore(p).load().device_errors.quiet == []


def test_config_cleans_the_list():
    items = ["LANWM:104", "lanwm:104", "device:x", 5, "nebra:", "nebra:A B"] + [f"lanwm:u{i}" for i in range(300)]
    q = Config.model_validate({"device_errors": {"quiet": items}}).device_errors.quiet
    assert q[:2] == ["lanwm:104", "nebra:a b"] and len(q) == QUIET_MAX and len(set(q)) == len(q)
    assert Config.model_validate({"device_errors": {"quiet": "nope"}}).device_errors.quiet == []


def test_salvage_keeps_valid_quiet_entries():
    cfg, _ = salvage({"mdns_name": 5, "device_errors": {"quiet": ["lanwm:104", "x" * 500]}})
    assert cfg.device_errors.quiet == ["lanwm:104"]


def test_saved_and_a_file_from_the_previous_version(tmp_path):
    p = tmp_path / "config.yaml"
    store = ConfigStore(p)
    cfg = store.load()
    cfg.device_errors.quiet = ["lanwm:104"]
    store.config = cfg
    store.save()
    assert ConfigStore(p).load().device_errors.quiet == ["lanwm:104"]
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["device_errors"] == {"quiet": ["lanwm:104"]}
    del raw["device_errors"]                      # the previous version wrote no such field, same schema_version
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert ConfigStore(p).load().device_errors.quiet == []


# --------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


URL = "/api/admin/devices/errors/quiet"


def test_api_quiet_and_unquiet_persist_and_show_in_the_card(client):
    rec = client.hub.recorder
    nebra_rows(rec, "Joyned MU16", [(0, "disconnect")])
    sid = rec.show_id
    assert client.get(f"/api/admin/devices/errors?show_id={sid}").json()["totals"]["alerts"] == 1
    r = client.put(URL, json={"key": "nebra:Joyned MU16", "quiet": True})
    assert r.status_code == 200 and r.json() == {"quiet": ["nebra:joyned mu16"]}
    j = client.get(f"/api/admin/devices/errors?show_id={sid}").json()
    row = next(x for x in j["rows"] if x["key"] == "nebra:Joyned MU16")
    assert row["quieted"] and j["totals"]["alerts"] == 0 and row["episodes"][0]["level"] == "Advisory"
    assert ConfigStore(client.hub.data_dir / "config.yaml").load().device_errors.quiet == ["nebra:joyned mu16"]
    assert client.put(URL, json={"key": "nebra:Joyned MU16", "quiet": True}).json()["quiet"] == ["nebra:joyned mu16"]
    assert client.put(URL, json={"key": "NEBRA:joyned mu16", "quiet": False}).json() == {"quiet": []}
    assert client.get(f"/api/admin/devices/errors?show_id={sid}").json()["totals"]["alerts"] == 1


def test_api_validation_has_fixed_text_and_no_echo(client):
    r = client.put(URL, json={"key": "device:SECRETNAME", "quiet": True})
    assert r.status_code == 422 and "SECRETNAME" not in r.text
    for body in ({"key": "x" * 101, "quiet": True}, {"key": "lanwm:1", "quiet": "yes"}, {"key": "lanwm:1"},
                 {"key": "lanwm:1", "quiet": True, "extra": 1}):
        r = client.put(URL, json=body)
        assert r.status_code == 422 and "x" * 20 not in r.text
    assert client.hub.config.device_errors.quiet == []


def test_api_limit(client):
    client.hub.config.device_errors.quiet = [f"lanwm:{i}" for i in range(QUIET_MAX)]
    r = client.put(URL, json={"key": "lanwm:new", "quiet": True})
    assert r.status_code == 409 and len(client.hub.config.device_errors.quiet) == QUIET_MAX
    assert client.put(URL, json={"key": "lanwm:0", "quiet": False}).status_code == 200


def test_api_needs_admin_and_same_origin(client):
    ok = {"key": "lanwm:1", "quiet": True}
    assert client.put(URL, json=ok, headers={"Origin": "http://evil.example"}).status_code in (401, 403)
    client.post("/api/admin/logout")
    assert client.put(URL, json=ok).status_code in (401, 403)


def test_quiet_list_is_not_on_public_payloads(client):
    client.put(URL, json={"key": "nebra:Secret Unit", "quiet": True})
    client.post("/api/admin/logout")
    text = client.get("/api/snapshot").text
    assert "secret unit" not in text.lower() and "device_errors" not in text


# ---------------------------------------------------------------- import markers
def test_a_quieted_unit_makes_no_import_marker(rec):
    from stagewatch.integrations.lanwm import importer
    text = (FIX / "sample_2026-08-08.xml").read_text(encoding="utf-8")
    rec.set_show_day("2026-08-08")
    kw = dict(fmt="lanwm", text=text, file_name="a.xml", show_id=rec.show_id)
    base = importer.plan_import(rec, SITE, **kw)
    units = {f"lanwm:{e['unit'].replace('LA unit ', '')}".casefold() for e in base.episodes}
    assert base.markers
    quiet = importer.plan_import(rec, SITE, quiet=frozenset(units), **kw)
    assert len(quiet.markers) < len(base.markers) and len(quiet.rows) == len(base.rows)


# ------------------------------------------------------------------- report
def test_report_section_lists_quieted_units():
    from stagewatch.core.report import html
    out = html._devices({"rows": [], "quieted": ["Joyned MU16", "104"]}, None)
    assert "No device outages" in out and "Joyned MU16, 104 (quieted)" in out
    assert "quieted" not in html._devices({"rows": []}, None)


def test_report_build_carries_the_quiet_list():
    from types import SimpleNamespace

    from stagewatch.core.report import build
    cfg = SimpleNamespace(device_errors=SimpleNamespace(quiet=["nebra:joyned mu16", "lanwm:104"]))
    assert build._devices_section(SimpleNamespace(meta={}, cfg=cfg), [])["quieted"] == ["joyned mu16", "104"]
    assert build._devices_section(SimpleNamespace(meta={}, cfg=None), [])["quieted"] == []
