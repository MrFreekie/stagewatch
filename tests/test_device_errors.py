"""Device errors: problems per device for a show (plans/device-error-list.md). Expected numbers are counted
by hand from the alarm lines written here and from the two LA Network Manager fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import device_errors as de
from stagewatch.core.config import SiteConfig
from stagewatch.core.hub import Hub
from stagewatch.core.recorder import Recorder
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.integrations.lanwm import importer
from stagewatch.web.server import create_app

FIX = Path(__file__).parent / "fixtures" / "lanwm"
SITE = SiteConfig(timezone="Europe/London")
T = 1_800_000_000.0
DEVS = {"node1": {"name": "FOH node", "kind": "Sensor", "status": "ok"},
        "quiet": {"name": "Quiet node", "kind": "Sensor", "status": "ok"}}


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "t.sqlite3")
    yield r
    r.close()


def alarm(rec, ts, alarm_id, event, level=1, msg="FOH node: missing", show_id=None):
    rec._db.execute("INSERT INTO alarm_log (show_id, ts, alarm_id, event, level, message) VALUES (?, ?, ?, ?, ?, ?)",
                    (show_id or rec.show_id, ts, alarm_id, event, level, msg))
    rec._db.commit()


def run(rec, started, stopped=None, last_seen=None):
    return rec._db.execute("INSERT INTO hub_runs (started, last_seen, stopped) VALUES (?, ?, ?)",
                           (started, last_seen or stopped or started, stopped)).lastrowid


def rows(out):
    return {r["key"]: r for r in out["rows"]}


def go(rec, devs=DEVS, now=T + 10000, current=None, ids=None):
    return de.summarise(rec, ids or [rec.show_id], devs, now, current)


# ------------------------------------------------------------ Stagewatch devices
def test_offline_and_back_is_one_dropout_with_its_time(rec):
    alarm(rec, T, "device:node1", "raise")
    alarm(rec, T + 12, "device:node1", "clear")
    out = go(rec)
    r = rows(out)["device:node1"]
    assert (r["dropouts"], r["problems"], r["seconds_in_fault"]) == (1, 1, 12.0)
    assert r["episodes"][0]["ended_by"] == "clear" and r["last"]["level"] == "Offline"
    quiet = rows(out)["device:quiet"]
    assert quiet["problems"] == 0 and quiet["last"] is None
    assert out["rows"][-1]["key"] == "device:quiet"          # worst first, quiet last


def test_silent_alarm_counts_and_ack_does_not(rec):
    alarm(rec, T, "device:node1", "raise", msg="FOH node: missing (silent)")
    alarm(rec, T + 5, "device:node1", "ack (dashboard)")
    alarm(rec, T + 9, "device:node1", "clear")
    r = rows(go(rec))["device:node1"]
    assert r["problems"] == 1 and r["episodes"][0]["text"] == "FOH node: missing"


def test_threshold_and_software_lines_are_not_device_errors(rec):
    alarm(rec, T, "threshold:hot", "raise", 3, "Stage too hot")
    alarm(rec, T, "software", "update_failed", 1, "nope")
    out = go(rec)
    assert out["totals"]["problems"] == 0 and "threshold:hot" not in rows(out)


def test_a_problem_open_when_stagewatch_stopped_ends_there(rec):
    run(rec, T - 100, stopped=T + 60)
    run2 = run(rec, T + 300)
    alarm(rec, T, "device:node1", "raise")                # never cleared: the hub stopped at T+60
    out = go(rec, current=run2)
    ep = rows(out)["device:node1"]["episodes"][0]
    assert (ep["ended_by"], ep["end"], ep["seconds"]) == ("restart", T + 60, 60.0)
    assert out["gaps"] == [(T + 60, T + 300)]


def test_still_open_in_the_running_hub_counts_to_now(rec):
    r1 = run(rec, T - 100)
    alarm(rec, T, "device:node1", "raise")
    ep = rows(go(rec, now=T + 90, current=r1))["device:node1"]["episodes"][0]
    assert (ep["ended_by"], ep["seconds"]) == ("open", 90.0)


def test_raised_again_after_a_restart_is_two_problems(rec):
    run(rec, T - 100, stopped=T + 30)
    r2 = run(rec, T + 100)
    alarm(rec, T, "device:node1", "raise")
    alarm(rec, T + 200, "device:node1", "raise")
    alarm(rec, T + 230, "device:node1", "clear")
    r = rows(go(rec, current=r2))["device:node1"]
    assert r["problems"] == 2 and [e["ended_by"] for e in r["episodes"]] == ["restart", "clear"]
    assert r["seconds_in_fault"] == 30.0 + 30.0


def test_a_level_that_rises_keeps_one_problem_at_the_top_level(rec):
    events = [(T, "device:n", "raise", 1, "N: missing"), (T + 4, "device:n", "change", 3, "N: fault"),
              (T + 8, "device:n", "clear", 3, "N: fault")]
    eps = de.device_episodes(events, [], T + 99, None, T + 99)["n"][1]
    assert len(eps) == 1 and eps[0].level == 3 and eps[0].text == "N: fault"


@pytest.mark.parametrize("starts, flaps", [([0, 300, 590], True), ([0, 300, 601], False), ([0, 5], False),
                                           ([0, 1000, 1100, 1200], True)])
def test_flapping_is_three_inside_ten_minutes(starts, flaps):
    assert de.flapping([float(s) for s in starts]) is flaps


def test_flapping_tag_on_a_row(rec):
    for i, t in enumerate((0, 100, 200)):
        alarm(rec, T + t, "device:node1", "raise")
        alarm(rec, T + t + 10, "device:node1", "clear")
    r = rows(go(rec))["device:node1"]
    assert r["flapping"] is True and r["problems"] == 3


def test_a_removed_device_keeps_its_history(rec):
    alarm(rec, T, "device:gone", "raise", msg="Old node: missing")
    alarm(rec, T + 5, "device:gone", "clear", msg="Old node: missing")
    r = rows(go(rec))["device:gone"]
    assert (r["name"], r["kind"], r["problems"]) == ("Old node", "Removed device", 1)


def test_shows_do_not_mix_and_an_event_adds_them_up(rec):
    first = rec.show_id
    alarm(rec, T, "device:node1", "raise")
    alarm(rec, T + 5, "device:node1", "clear")
    rec.start_show("Day 2")
    second = rec.show_id
    alarm(rec, T + 90000, "device:node1", "raise", show_id=second)
    alarm(rec, T + 90010, "device:node1", "clear", show_id=second)
    alarm(rec, T + 90100, "device:node1", "raise", show_id=second)
    alarm(rec, T + 90130, "device:node1", "clear", show_id=second)
    assert rows(go(rec, ids=[first]))["device:node1"]["problems"] == 1
    assert rows(go(rec, ids=[second]))["device:node1"]["problems"] == 2
    both = rows(go(rec, ids=rec.event_show_ids(rec.show_row(second)["event_id"])))["device:node1"]
    assert (both["problems"], both["seconds_in_fault"]) == (3, 45.0)


# ------------------------------------------------------------ imported LA units
def imported(rec, name, day, file):
    rec.set_show_day(day)
    text = (FIX / file).read_text(encoding="utf-8")
    p = importer.plan_import(rec, SITE, fmt="lanwm", text=text, file_name=name, show_id=rec.show_id)
    return importer.apply_import(rec, p)


def test_first_log_units(rec):
    imported(rec, "Log_a.xml", "2026-08-08", "sample_2026-08-08.xml")
    out = go(rec, devs={})
    r = rows(out)
    u33 = r["lanwm:033"]
    assert (u33["stops"], u33["alerts"], u33["advisories"], u33["problems"]) == (1, 1, 0, 2)
    assert u33["seconds_in_fault"] == pytest.approx(5.818 + 0.631)      # 21:30:29.853-35.671 and 35.497-36.128
    assert u33["name"] == "LA unit 033" and u33["kind"] == "L-Acoustics (imported)" and u33["imported_from"] == ["Log_a.xml"]
    u29 = r["lanwm:029"]                                              # waiting for SMPS, mains, disconnected: none cleared
    assert (u29["alerts"], u29["advisories"], u29["never_cleared"], u29["seconds_in_fault"]) == (2, 1, 3, 0.0)
    assert r["lanwm:030"]["problems"] == 2                            # one mains warning, ONE disconnect (written twice)
    assert out["rows"][0]["key"] == "lanwm:033"                       # the only Stop comes first
    assert all(not k.endswith("---") for k in r)                      # session-wide actions are not a unit


def test_second_log_units_and_total(rec):
    imported(rec, "Log_b.xml", "2026-10-15", "sample_2026-10-15.xml")
    out = go(rec, devs={})
    r = rows(out)
    assert out["totals"] == {"stops": 0, "alerts": 24, "advisories": 10, "dropouts": 0, "problems": 34}
    assert {k: v["problems"] for k, v in r.items()} == {
        "lanwm:101": 4, "lanwm:102": 4, "lanwm:103": 5, "lanwm:104": 7, "lanwm:105": 6,
        "lanwm:201": 2, "lanwm:202": 2, "lanwm:203": 2, "lanwm:204": 1, "lanwm:205": 1}
    assert not any(v["flapping"] for v in r.values())
    assert all(v["never_cleared"] == 0 for v in r.values())


def test_undoing_an_import_removes_its_units(rec):
    done = imported(rec, "Log_b.xml", "2026-10-15", "sample_2026-10-15.xml")
    assert go(rec, devs={})["totals"]["problems"] == 34
    rec.undo_vendor_import(rec.show_id, done["import_id"])
    assert go(rec, devs={})["rows"] == []


def test_stagewatch_devices_and_la_units_share_one_list(rec):
    imported(rec, "Log_b.xml", "2026-10-15", "sample_2026-10-15.xml")
    alarm(rec, T, "device:node1", "raise")
    alarm(rec, T + 3, "device:node1", "clear")
    out = go(rec)
    assert out["totals"]["problems"] == 35 and out["totals"]["dropouts"] == 1
    assert {"device:node1", "device:quiet", "lanwm:104"} <= set(rows(out))


# --------------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def test_api_lists_devices_with_their_counts(client):
    rec = client.hub.recorder
    dev = next(d for d in client.hub.devices.values() if d.id != "site")
    alarm(rec, T, f"device:{dev.id}", "raise", msg=f"{dev.name}: missing")
    alarm(rec, T + 20, f"device:{dev.id}", "clear", msg=f"{dev.name}: missing")
    j = client.get("/api/admin/devices/errors").json()
    assert j["show"]["id"] == rec.show_id and j["scope"] == "show" and j["days"] == 1
    row = next(r for r in j["rows"] if r["key"] == f"device:{dev.id}")
    assert (row["dropouts"], row["seconds_in_fault"], row["name"]) == (1, 20.0, dev.name)
    assert j["totals"]["dropouts"] == 1 and isinstance(j["gaps"], list)
    assert client.get(f"/api/admin/devices/errors?scope=event&show_id={rec.show_id}").json()["scope"] == "event"


def test_api_unknown_show_bad_scope_and_login(client):
    assert client.get("/api/admin/devices/errors?show_id=99999").status_code == 404
    assert client.get("/api/admin/devices/errors?scope=world").status_code == 422
    assert client.get("/api/admin/devices/errors?show_id=0").status_code == 422
    client.post("/api/admin/logout")
    assert client.get("/api/admin/devices/errors").status_code in (401, 403)


def test_device_names_never_reach_the_public_snapshot(client):
    snap = client.get("/api/snapshot").text
    assert "device:" not in snap and "seconds_in_fault" not in snap


def test_csv_and_time_helpers_in_node():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(Path(__file__).parent / "js" / "device_errors_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr


def test_admin_page_loads_the_card_and_its_helpers():
    static = Path(__file__).parents[1] / "src" / "stagewatch" / "web" / "static"
    html = (static / "admin.html").read_text(encoding="utf-8")
    assert html.index("schedule-export.js") < html.index("device-errors.js") < html.index("admin.js")   # csvCell first
    js = (static / "admin.js").read_text(encoding="utf-8")
    assert 'foldCard(deviceErrorsCard(), "deviceerrors"' in js and "/api/admin/devices/errors" in js
