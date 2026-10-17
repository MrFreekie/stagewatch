"""Onsite contacts (plans/onsite-contacts.md): validation, the per-event store, the three phone
switches and the reveal PIN, the GDPR reminder, and that contacts leak nowhere else."""

from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core import contacts as cm
from stagewatch.core.config import Config
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

PIN = "4711"
EVIL = {"Origin": "http://evil.example"}
NAME, PHONE = "Zed Quux", "07700 900987"


# ---------------------------------------------------------------- model and rules
def test_phone_validation():
    assert cm.Contact(role="SM", phone="+44 (0)7700-900.123").phone == "+44 (0)7700-900.123"
    for bad in ("12", "call me", "1" * 16, "+44 7700 900123 ext 4"):
        with pytest.raises(ValueError):
            cm.Contact(role="SM", phone=bad)
    assert cm.tel_href("+44 (0)7700 900123") == "+4407700900123"
    assert cm.tel_href("07700 900123") == "07700900123"


def test_text_cleaned_and_role_required():
    c = cm.Contact(role="  Stage‮  Manager ", name="A​l\nex")
    assert (c.role, c.name) == ("Stage Manager", "Al ex")
    with pytest.raises(ValueError):
        cm.Contact(role=" ")


def test_errors_never_echo_values():
    with pytest.raises(ValueError) as e:
        cm.Contact(role="SM", phone=PHONE + " x")
    assert PHONE not in str(e.value)


def _dash(phones=True, area="", cards=("contacts",)):
    return SimpleNamespace(cards=list(cards), show_contact_phones=phones, contacts_area=area)


ROW = {"id": "aaaaaaaa", "role": "SM", "name": NAME, "phone": PHONE, "show_phone": True, "radio": "", "area": "", "on_duty": True}


@pytest.mark.parametrize("enabled,dash_on,contact_on", [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)])
def test_three_switches(enabled, dash_on, contact_on):
    row = {**ROW, "show_phone": bool(contact_on)}
    out = cm.contacts_for([row], _dash(bool(dash_on)), bool(enabled), True)[0]
    assert ("phone" in out) == bool(enabled and dash_on and contact_on)
    hidden = cm.contacts_for([row], _dash(bool(dash_on)), bool(enabled), False)[0]
    assert "phone" not in hidden and "tel" not in hidden
    assert hidden.get("has_phone", False) == bool(enabled and dash_on and contact_on)


def test_duty_area_and_card():
    rows = [ROW, {**ROW, "id": "bbbbbbbb", "on_duty": False}, {**ROW, "id": "cccccccc", "area": "Stage 2"}]
    assert [r["id"] for r in cm.contacts_for(rows, _dash(area="Main"), True, True)] == ["aaaaaaaa"]
    assert len(cm.contacts_for(rows, _dash(area="Stage 2"), True, True)) == 2
    assert cm.contacts_for(rows, _dash(cards=("chart",)), True, True) == []


# ---------------------------------------------------------------- store
def test_store_round_trip_and_clear(tmp_path):
    s = cm.ContactStore(tmp_path)
    rows = s.replace(1, [cm.Contact(role="Custom Boss", name=NAME, id="deadbeef")])
    assert rows[0]["id"] != "deadbeef"   # ids are the server's
    assert s.contacts(1)[0]["name"] == NAME and "Custom Boss" in s.roles()
    s.replace(1, [cm.Contact(role="SM", id=rows[0]["id"])])
    assert s.contacts(1)[0]["id"] == rows[0]["id"]
    (tmp_path / "contacts" / "1.invalid.yaml").write_text("x")
    (tmp_path / "contacts" / ".1.yaml.tmp").write_text("x")
    s.clear(1)
    assert sorted(f.name for f in (tmp_path / "contacts").iterdir()) == ["roles.yaml"]
    assert "Custom Boss" in s.roles()   # job titles are kept installation-wide


def test_damaged_file_set_aside(tmp_path):
    (tmp_path / "contacts").mkdir()
    (tmp_path / "contacts" / "3.yaml").write_text("{not: [yaml")
    assert cm.ContactStore(tmp_path).contacts(3) == []
    assert (tmp_path / "contacts" / "3.invalid.yaml").exists()


def test_other_owner_ignored_and_cap(tmp_path):
    cm.ContactStore(tmp_path, lambda _id: "old").replace(2, [cm.Contact(role="SM")])
    assert cm.ContactStore(tmp_path, lambda _id: "new").contacts(2) == []
    with pytest.raises(cm.ContactError):
        cm.ContactStore(tmp_path).replace(1, [cm.Contact(role="SM")] * (cm.MAX_CONTACTS + 1))


def test_old_config_loads_with_defaults():
    cfg = Config.model_validate({"dashboards": [{"slug": "foh"}]})
    assert cfg.contacts.phones_enabled is False and cfg.dashboards[0].show_contact_phones is False


# ---------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
        yield c


def base(c, eid=None) -> str:
    return f"/api/admin/events/{eid or c.hub.recorder.event_id}/contacts"


def setup_phones(c, pin=None):
    rows = c.get("/api/info").json()["dashboards"]
    rows[0]["cards"] = rows[0]["cards"] + ["contacts"]
    rows[0]["show_contact_phones"] = True
    assert c.put("/api/admin/dashboards", json=rows).status_code == 200
    body = {"phones_enabled": True, **({"reveal_pin": pin} if pin else {})}
    assert c.put("/api/admin/contacts/settings", json=body).status_code == 200
    r = c.put(base(c), json={"contacts": [{"role": "SM", "name": NAME, "phone": PHONE, "show_phone": True}]})
    assert r.status_code == 200, r.text
    return rows[0]["slug"]


def test_auth_and_origin(client):
    assert client.put(base(client), json={"contacts": []}, headers=EVIL).status_code == 403
    client.post("/api/admin/logout")
    client.cookies.clear()
    assert client.get(base(client)).status_code == 401
    assert client.put(base(client), json={"contacts": []}).status_code == 401
    assert client.put("/api/admin/contacts/settings", json={"phones_enabled": True}).status_code == 401


def test_public_list_and_phones(client):
    slug = setup_phones(client)
    r = client.get(f"/api/contacts?dashboard={slug}")
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["contacts"][0]["phone"] == PHONE and r.json()["contacts"][0]["tel"] == "07700900987"
    assert client.get("/api/contacts?dashboard=nope").status_code == 404
    other = [d for d in client.get("/api/info").json()["dashboards"] if d["slug"] != slug][0]["slug"]
    assert client.get(f"/api/contacts?dashboard={other}").json()["contacts"] == []   # card not on it
    client.put("/api/admin/contacts/settings", json={"phones_enabled": False})
    assert "phone" not in client.get(f"/api/contacts?dashboard={slug}").json()["contacts"][0]


def test_reveal_pin(client):
    slug = setup_phones(client, pin="246813")
    r = client.get(f"/api/contacts?dashboard={slug}").json()
    assert r["pin_needed"] and "phone" not in r["contacts"][0] and PHONE not in json.dumps(r)
    assert client.post("/api/contacts/reveal", json={"dashboard": slug, "pin": "246813"}, headers=EVIL).status_code == 403
    assert client.post("/api/contacts/reveal", json={"dashboard": slug, "pin": "0000"}).status_code == 401
    ok = client.post("/api/contacts/reveal", json={"dashboard": slug, "pin": "246813"})
    assert ok.json()["contacts"][0]["phone"] == PHONE
    state = client.get("/api/admin/state").json()["config"]["contacts"]
    assert state == {"phones_enabled": True, "reveal_pin_set": True}


def test_reveal_rate_limited(client):
    slug = setup_phones(client, pin="246813")
    codes = [client.post("/api/contacts/reveal", json={"dashboard": slug, "pin": "1"}).status_code for _ in range(6)]
    assert codes[-1] == 429


def test_validation_quiet(client, caplog):
    caplog.set_level(logging.DEBUG)
    r = client.put(base(client), json={"contacts": [{"role": "SM", "name": NAME, "phone": PHONE + " ext"}]})
    assert r.status_code == 422 and PHONE not in r.text
    assert client.put(base(client), json={"contacts": [{"role": "SM", "extra": 1}]}).status_code == 422
    setup_phones(client)
    assert NAME not in caplog.text and PHONE not in caplog.text


def test_no_leaks(client):
    slug = setup_phones(client)
    for url in ("/api/snapshot", "/api/info", "/api/admin/state", f"/api/dashboard/{slug}"):
        text = client.get(url).text
        assert NAME not in text and PHONE not in text, url
    with client.websocket_connect(f"/ws?dashboard={slug}") as ws:
        assert NAME not in json.dumps(ws.receive_json())
    cfg = (client.hub.data_dir / "config.yaml").read_text(encoding="utf-8")
    assert NAME not in cfg and PHONE not in cfg


def test_change_message_has_no_data(client):
    slug = setup_phones(client)
    with client.websocket_connect(f"/ws?dashboard={slug}") as ws:
        ws.receive_json()
        client.put(base(client), json={"contacts": [{"role": "SM", "name": NAME}]})
        for _ in range(20):
            msg = ws.receive_json()
            if msg.get("type") == "contacts_changed":
                assert msg == {"type": "contacts_changed"}
                break
        else:
            pytest.fail("no contacts_changed message")


def test_new_event_reminder_copy_and_clear(client):
    old = client.hub.recorder.event_id
    setup_phones(client)
    client.post("/api/admin/shows", json={"name": "Day 1", "event": "new", "event_name": "Next fest"})
    new = client.hub.recorder.event_id
    r = client.get(base(client)).json()
    assert r["contacts"] == [] and r["reminders"][0]["event_id"] == old
    assert client.post(f"{base(client)}/copy", json={"from_event_id": old}).json()["contacts"][0]["name"] == NAME
    assert client.post(f"{base(client)}/copy", json={"from_event_id": old}).status_code == 409
    assert client.post(f"{base(client, old)}/dismiss-reminder").json()["reminders"] == []
    assert client.delete(base(client, old)).status_code == 200
    assert not (client.hub.data_dir / "contacts" / f"{old}.yaml").exists()
    assert (client.hub.data_dir / "contacts" / f"{new}.yaml").exists()


def test_stale_nudge(client):
    setup_phones(client)
    p = client.hub.data_dir / "contacts" / f"{client.hub.recorder.event_id}.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    raw["edited"] = time.time() - 15 * 86400
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert client.get(base(client)).json()["stale"] is True


def test_demo(client):
    r = client.post("/api/admin/contacts/demo")
    assert r.status_code == 200 and all("(DEMO)" in c["role"] for c in r.json()["contacts"])
    slug = client.hub.config.dashboards[0].slug
    shown = client.get(f"/api/contacts?dashboard={slug}").json()["contacts"]
    assert len(shown) == 4 and sum("phone" in c for c in shown) == 3


def test_diagnostics_redacts_reveal_pin(client):
    from stagewatch import diagnostics
    setup_phones(client, pin="246813")
    cfg = client.hub.config.model_dump(mode="json")
    assert diagnostics.redact_config(cfg)["contacts"]["reveal_pin_hash"] == diagnostics.REDACTED


def test_reveal_pin_rules(client):
    for bad in ("1234", PIN + "00"[:0]):
        assert client.put("/api/admin/contacts/settings", json={"phones_enabled": True, "reveal_pin": bad}).status_code == 422
    client.put("/api/admin/pin", json={"current": PIN, "new": "135792"})
    r = client.put("/api/admin/contacts/settings", json={"phones_enabled": True, "reveal_pin": "135792"})
    assert r.status_code == 422 and "135792" not in r.text


def test_locked_file_not_set_aside(tmp_path, monkeypatch):
    s = cm.ContactStore(tmp_path)
    s.replace(1, [cm.Contact(role="SM")])
    monkeypatch.setattr(cm.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    assert s.contacts(1) == []
    monkeypatch.undo()
    assert s.contacts(1) and s.has_files(1)
