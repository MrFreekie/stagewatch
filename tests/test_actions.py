"""OSC actions (0.4.0): schedule actions and threshold actions, checked against a real UDP listener
on loopback. Nothing sends while disarmed, never late, once only, rate limits, cool-down, the
start-up rule, public shapes without actions, and the v3 -> v4 database step."""

import shutil
import socket
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pythonosc.osc_message import OscMessage

from stagewatch.core import actions as act
from stagewatch.core import schedule as sched
from stagewatch.core.config import ActionMessage, ActionTarget, Threshold, ThresholdActions
from stagewatch.core.hub import Hub
from stagewatch.core.recorder import Recorder
from stagewatch.web.server import create_app

T0 = 1_790_000_000.0
PIN = "4321"


class Listener:
    """A fake OSC receiver on loopback."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)

    def getsockname(self):
        return self.sock.getsockname()

    def received(self) -> list:
        out = []
        while True:
            try:
                m = OscMessage(self.sock.recv(4096))
                out.append((m.address, list(m.params)))
            except (socket.timeout, TimeoutError):
                return out


@pytest.fixture
def listener():
    s = Listener()
    yield s
    s.sock.close()


@pytest.fixture
def hub(tmp_path, listener):
    h = Hub(tmp_path)
    h.config.actions.targets = [ActionTarget(id="qlab", name="QLab", host="127.0.0.1",
                                             port=listener.getsockname()[1])]
    h.actions.started = T0 - 3600
    yield h
    if not h.recorder._closed:
        h.recorder.close()


def action_row(title, start, end=None, end_msg=False, id=None):
    msg = {"target_id": "qlab", "address": "/cue/1/start", "args": [{"t": "i", "v": 1}]}
    return {"id": id, "stage": "", "kind": "action", "title": title, "planned_start": T0 + start,
            "planned_end": None if end is None else T0 + end, "setlist": "", "marker": None,
            "action": {"start": msg, "end": {**msg, "address": "/stop"} if end_msg else None,
                       "icon": "music", "enabled": True}}


def load(hub, rows):
    hub.schedule.replace(None, [dict(r) for r in rows])
    hub.actions.invalidate()


def statuses(hub):
    return [(r["source"], r["phase"], r["status"]) for r in reversed(hub.recorder.action_log())]


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("addr", ["go", "/go now", "/cue/*", "/a#", "/" + "x" * 200, "/a\x00"])
def test_bad_addresses_refused(addr):
    with pytest.raises(ValueError):
        act.check_address(addr)


@pytest.mark.parametrize("host", ["255.255.255.255", "192.168.1.255", "224.0.0.1", "8.8.8.8", "0.0.0.0",
                                  "example.com", "qlab"])
def test_bad_hosts_refused(host):
    with pytest.raises(ValueError):
        act.check_host(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.20", "169.254.3.4", "qlab-mac.local"])
def test_local_hosts_allowed(host):
    assert act.check_host(host)


def test_args_checked():
    assert act.check_args([{"t": "i", "v": "3"}, {"t": "f", "v": 1}, {"t": "s", "v": "hi"}]) == \
        [{"t": "i", "v": 3}, {"t": "f", "v": 1.0}, {"t": "s", "v": "hi"}]
    for bad in ([{"t": "i", "v": 2**31}], [{"t": "f", "v": "nan"}], [{"t": "s", "v": "a\nb"}],
                [{"t": "x", "v": 1}], [{"t": "i", "v": 1}] * 9, [{"t": "i", "v": "{value}"}]):
        with pytest.raises(ValueError):
            act.check_args(bad)
    assert act.check_args([{"t": "f", "v": "{value}"}], allow_placeholders=True)[0]["v"] == "{value}"


# ------------------------------------------------------------------ schedule actions
def test_nothing_sends_while_disarmed(hub, listener):
    load(hub, [action_row("Walk-in", 0)])
    hub.check_actions(T0 + 0.5)
    assert listener.received() == []
    assert statuses(hub) == [("schedule", "start", "skipped")]
    hub.actions.set_armed(True)  # arming later never sends the past one
    hub.check_actions(T0 + 1)
    assert listener.received() == []


def test_armed_sends_on_time_once(hub, listener):
    load(hub, [action_row("Walk-in", 0, 60, end_msg=True)])
    hub.actions.set_armed(True)
    hub.check_actions(T0 - 1)
    assert listener.received() == []
    hub.check_actions(T0 + 1)
    hub.check_actions(T0 + 1.5)  # a second tick: still once
    assert listener.received() == [("/cue/1/start", [1])]
    hub.check_actions(T0 + 61)
    assert listener.received() == [("/stop", [1])]
    assert statuses(hub) == [("schedule", "start", "sent"), ("schedule", "end", "sent")]


def test_never_late(hub, listener):
    load(hub, [action_row("Walk-in", 0)])
    hub.actions.set_armed(True)
    hub.check_actions(T0 + act.JITTER_S + 0.5)
    assert listener.received() == []
    assert statuses(hub) == [("schedule", "start", "missed")]


def test_restart_marks_due_as_missed_and_never_refires(tmp_path, listener):
    h = Hub(tmp_path)
    h.config.actions.targets = [ActionTarget(id="qlab", name="QLab", host="127.0.0.1",
                                             port=listener.getsockname()[1])]
    h.actions.started = T0 - 100
    load(h, [action_row("Walk-in", 0), action_row("Record", 10)])
    h.actions.set_armed(True)
    h.check_actions(T0 + 1)
    assert len(listener.received()) == 1
    h.recorder.close()
    h2 = Hub(tmp_path)  # restart at T0 + 5: the record action (T0 + 10) is still ahead
    h2.config.actions.targets = h.config.actions.targets
    assert h2.actions.armed is False  # off after every start
    h2.actions.started = T0 + 12      # ...but Stagewatch was off when it came
    h2.actions.set_armed(True)
    h2.check_actions(T0 + 12)
    assert listener.received() == []
    assert [s for s in statuses(h2) if s[0] == "schedule"] == [("schedule", "start", "sent"),
                                                               ("schedule", "start", "missed")]
    h2.recorder.close()


def test_moving_an_unsent_action_later_waits_again(hub, listener):
    load(hub, [action_row("Walk-in", 0)])
    hub.check_actions(T0 + 1)                      # disarmed: skipped
    item = hub.schedule.items()[0]
    load(hub, [{**item, "planned_start": T0 + 300}])
    hub.actions.set_armed(True)
    hub.check_actions(T0 + 10)
    hub.check_actions(T0 + 300.5)
    assert listener.received() == [("/cue/1/start", [1])]


def test_attached_actions_follow_the_item(hub, listener):
    msg = {"target_id": "qlab", "address": "/record", "args": [{"t": "i", "v": 1}]}
    row = {"id": None, "stage": "", "kind": "act", "title": "Headliner", "planned_start": T0,
           "planned_end": T0 + 60, "setlist": "", "marker": None,
           "action": {"start": msg, "end": {**msg, "args": [{"t": "i", "v": 0}]}, "icon": "record", "enabled": True}}
    load(hub, [row])
    hub.actions.set_armed(True)
    hub.check_actions(T0 + 0.1)
    hub.check_actions(T0 + 60.1)
    assert listener.received() == [("/record", [1]), ("/record", [0])]


def test_rate_limit_per_target(hub, listener):
    load(hub, [action_row(f"A{i}", i * 0.1) for i in range(7)])
    hub.actions.set_armed(True)
    hub.check_actions(T0 + 1)
    assert len(listener.received()) == act.TARGET_MAX
    assert [s[2] for s in statuses(hub)].count("failed") == 2


def test_action_rows_never_in_now_next_or_public(hub):
    load(hub, [{"id": None, "stage": "", "kind": "doors", "title": "Doors", "planned_start": T0 + 600,
                "planned_end": None, "setlist": "", "marker": None}, action_row("Walk-in", 0)])
    nn = sched.now_next(hub.schedule.items(), T0 + 10)
    assert nn["state"] == "before" and nn["current"] is None and nn["next"]["title"] == "Doors"
    pub = hub.schedule.public()
    assert [i["title"] for i in pub["items"]] == ["Doors"]
    assert "action" not in str(pub) and "127.0.0.1" not in str(pub)
    assert all(m.item_id != hub.schedule.items()[0]["id"] for m in sched.marker_moments(hub.schedule.items())
               if hub.schedule.items()[0]["kind"] == "action")


def test_import_never_makes_an_action():
    res = sched.parse_import("19:00 action\n19:05,,/go,action,", "auto", "2026-10-17",
                             __import__("stagewatch.core.config", fromlist=["SiteConfig"]).SiteConfig())
    assert all(r["kind"] != "action" for r in res.rows)


# ------------------------------------------------------------------ threshold actions
def _threshold(hub, cooldown=60):
    t = Threshold(id="wind", entity="site.temperature", label="Wind", above=10, level=2,
                  actions=ThresholdActions(
                      raised=ActionMessage(target_id="qlab", address="/wind", args=[{"t": "f", "v": "{value}"},
                                                                                    {"t": "s", "v": "{label}"}]),
                      cleared=ActionMessage(target_id="qlab", address="/wind/clear"), cooldown_s=cooldown))
    hub.config.thresholds = [t]
    return t


def test_threshold_restart_rule_and_disarmed(hub, listener):
    t = _threshold(hub)
    hub.actions.set_armed(True)
    hub.actions.alarm_event(t, "raised", 12.0, T0)     # active at start-up: never seen clear
    hub.actions.alarm_event(t, "cleared", 5.0, T0 + 1)
    assert listener.received() == []
    hub.actions.threshold_seen("wind", False)
    hub.actions.set_armed(False)
    hub.actions.alarm_event(t, "raised", 12.0, T0 + 2)
    assert listener.received() == []
    hub.actions.set_armed(True)
    hub.actions.alarm_event(t, "cleared", 5.0, T0 + 3)
    assert listener.received() == [("/wind/clear", [])]
    hub.actions.alarm_event(t, "raised", 12.34, T0 + 4)
    got = listener.received()
    assert got[0][0] == "/wind" and got[0][1][0] == pytest.approx(12.3) and got[0][1][1] == "Wind"


def test_threshold_cool_down(hub, listener):
    t = _threshold(hub, cooldown=60)
    hub.actions.threshold_seen("wind", False)
    hub.actions.set_armed(True)
    hub.actions.alarm_event(t, "raised", 12.0, T0)
    hub.actions.alarm_event(t, "cleared", 5.0, T0 + 5)   # its own counter: sent
    hub.actions.alarm_event(t, "raised", 12.0, T0 + 10)  # inside the minute: skipped
    hub.actions.alarm_event(t, "cleared", 5.0, T0 + 15)
    hub.actions.alarm_event(t, "raised", 12.0, T0 + 61)
    assert [m[0] for m in listener.received()] == ["/wind", "/wind/clear", "/wind"]
    assert [s[2] for s in statuses(hub)].count("skipped") == 2


def test_status_messages_only_when_ticked(hub, listener):
    from stagewatch.core.config import OscDestination
    t = _threshold(hub)
    hub.config.thresholds[0].actions = ThresholdActions()
    port = listener.getsockname()[1]
    hub.config.osc_out.destinations = [OscDestination(host="127.0.0.1", port=port)]
    hub.actions.threshold_seen("wind", False)
    hub.actions.alarm_event(t, "raised", 12.0, T0)
    assert listener.received() == []
    hub.config.osc_out.destinations[0].alarm_messages = True
    hub.actions.alarm_event(t, "cleared", 5.0, T0 + 1)
    hub.actions.alarm_event(t, "raised", 12.0, T0 + 2)
    got = listener.received()
    assert got[-1] == (act.STATUS_ADDRESS, ["threshold:wind", 2, "raised", "Wind: 12.0"])


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path, listener):
    hub = Hub(tmp_path)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        c.port = listener.getsockname()[1]
        yield c


def test_admin_routes_need_login_and_same_origin(client):
    for method, path in (("GET", "/api/admin/actions"), ("GET", "/api/admin/schedule"),
                         ("PUT", "/api/admin/actions/armed"), ("POST", "/api/admin/actions/test"),
                         ("PUT", "/api/admin/actions/targets")):
        assert client.request(method, path, json={}).status_code in (401, 403)
    assert client.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
    r = client.put("/api/admin/actions/armed", json={"armed": True}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and client.hub.actions.armed is False


def test_api_flow_and_public_shape(client, listener):
    assert client.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
    bad = client.put("/api/admin/actions/targets", json={"targets": [
        {"id": "qlab", "name": "QLab", "host": "192.168.1.255", "port": 53000}]})
    assert bad.status_code == 422 and "192.168.1.255" not in bad.text
    r = client.put("/api/admin/actions/targets", json={"targets": [
        {"id": "qlab", "name": "QLab", "host": "127.0.0.1", "port": client.port}]})
    assert r.status_code == 200 and r.json()["armed"] is False
    t = client.post("/api/admin/actions/test", json={"target_id": "qlab", "address": "/stagewatch/test", "args": []})
    assert t.json()["status"] == "sent" and listener.received() == [("/stagewatch/test", [])]
    assert client.post("/api/admin/actions/test", json={"target_id": "qlab", "address": "/x"}).json()["status"] == "failed"
    adm = client.get("/api/admin/schedule").json()
    body = {"show_id": adm["show_id"], "revision": adm["revision"], "with_actions": True, "items": [
        {"kind": "doors", "title": "Doors", "start": "19:00"},
        {"kind": "action", "title": "Walk-in", "start": "18:30",
         "action": {"start": {"target_id": "qlab", "address": "/go", "args": []}, "icon": "music"}}]}
    r = client.put("/api/admin/schedule", json=body)
    assert r.status_code == 200, r.text
    assert [i["kind"] for i in r.json()["items"]] == ["action", "doors"]
    pub = client.get("/api/schedule").json()
    assert [i["title"] for i in pub["items"]] == ["Doors"] and "/go" not in str(pub) and "qlab" not in str(pub)
    assert "qlab" not in str(client.get("/api/snapshot").json()["schedule"])
    # an older page (no with_actions, public list only) never deletes the action row
    r = client.put("/api/admin/schedule", json={"show_id": pub["show_id"], "revision": pub["revision"], "items": [
        {"id": pub["items"][0]["id"], "kind": "doors", "title": "Doors", "start": "19:15"}]})
    assert r.status_code == 200
    assert [i["title"] for i in r.json()["items"]] == ["Walk-in", "Doors"]
    # an unknown target is refused with fixed text
    adm = r.json()
    body = {"show_id": adm["show_id"], "revision": adm["revision"], "with_actions": True, "items": [
        {"kind": "action", "title": "X", "start": "18:00",
         "action": {"start": {"target_id": "nope", "address": "/go"}}}]}
    assert client.put("/api/admin/schedule", json=body).status_code == 422
    assert client.put("/api/admin/actions/armed", json={"armed": True}).json()["armed"] is True
    assert any(e["event"] == "armed" for e in client.hub.recorder.alarm_log())


def test_threshold_put_keeps_actions_from_an_older_page(client):
    assert client.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
    client.put("/api/admin/actions/targets", json={"targets": [
        {"id": "qlab", "name": "QLab", "host": "127.0.0.1", "port": client.port}]})
    t = {"id": "w", "entity": "site.temperature", "above": 30,
         "actions": {"raised": {"target_id": "qlab", "address": "/go", "args": []}}}
    assert client.put("/api/admin/thresholds", json=[t]).status_code == 200
    assert client.put("/api/admin/thresholds", json=[{"id": "w", "entity": "site.temperature", "above": 31}]).status_code == 200
    assert client.hub.config.thresholds[0].actions.raised.address == "/go"
    bad = {**t, "actions": {"raised": {"target_id": "qlab", "address": "no slash"}}}
    assert client.put("/api/admin/thresholds", json=[bad]).status_code == 422


# ------------------------------------------------------------------ database v4
def test_v3_upgrades_to_v4_keeping_rows(tmp_path, monkeypatch):
    path = tmp_path / "stagewatch.sqlite3"
    shutil.copyfile("tests/fixtures/v1/stagewatch.sqlite3", path)
    with monkeypatch.context() as mp:
        mp.setattr(Recorder, "_v3_to_v4", lambda self: None)  # stop at v3
        Recorder(path).close()
    db = sqlite3.connect(str(path))
    db.execute("INSERT INTO schedule_items (show_id, kind, title, planned_start, updated) VALUES (1, 'act', 'A', 1, 1)")
    db.commit()
    before = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("shows", "states", "markers", "schedule_items")}
    db.close()
    with monkeypatch.context() as mp:   # an error mid-step leaves v3 untouched
        def boom(self):
            self._db.execute("BEGIN IMMEDIATE")
            self._db.rollback()
            raise RuntimeError("injected")
        mp.setattr(Recorder, "_v3_to_v4", boom)
        with pytest.raises(RuntimeError):
            Recorder(path)
    rec = Recorder(path)
    rec._v3_to_v4()  # idempotent
    rec.close()
    db = sqlite3.connect(str(path))
    assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    assert {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before} == before
    assert db.execute("SELECT DISTINCT action FROM schedule_items").fetchall() == [(None,)]
    assert db.execute("SELECT COUNT(*) FROM action_runs").fetchone()[0] == 0
    db.close()
