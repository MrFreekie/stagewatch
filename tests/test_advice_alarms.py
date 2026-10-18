"""FOH advice (AD3): the drift notice, the admin route, the live feed and the OSC numbers.

Drift numbers are the AD0 ones (plans/ad0-physics-signoff.md): 60 m, 20 -> 26 degrees C, 50 % RH,
101 325 Pa gives -1.892 ms. Nothing here sleeps: the evaluator and the hub tick are driven with
explicit times."""

import asyncio
import json
import math
import re
import time

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import advice as adv
from stagewatch.core.config import AdviceConfig, AdvicePath, AdvicePosition, ConfigStore
from stagewatch.core.hub import Hub
from stagewatch.core.model import Device, Entity, Kind, Status
from stagewatch.integrations.osc_out import OscOutIntegration, advice_messages
from stagewatch.web.server import LiveFeed, create_app

SECRETISH = "do-not-echo-this-text"
EV = adv.DriftAlarmEvaluator


# ------------------------------------------------------------------ helpers
def make_hub(tmp_path):
    hub = Hub(tmp_path)
    hub.config.site.smoothing_tau_s = 0
    return hub


def add_env(hub, dev="env"):
    hub.register_device(Device(dev, dev, "test", status=Status.OK))
    for kind in (Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE):
        hub.register_entity(Entity(f"{dev}.{kind.value}", dev, kind.value, kind))


def air(hub, ts, t, rh=50.0, p=101325.0, dev="env"):
    for kind, v in ((Kind.TEMPERATURE, t), (Kind.HUMIDITY, rh), (Kind.PRESSURE, p)):
        hub.update_state(f"{dev}.{kind.value}", v, ts)
    hub.compute_site(ts)


def configure(hub, paths=(), positions=(), **kw):
    hub.config.advice = AdviceConfig(paths=list(paths), positions=list(positions), **kw)
    hub.bus.publish("config", None)


def path(pid="tower", distance=60, **kw):
    return AdvicePath(id=pid, name=kw.pop("name", pid), distance_m=distance, **kw)


def drifted_hub(tmp_path, paths=None, **kw):
    """A hub whose 60 m path has drifted -1.892 ms since the 'Aligned' marker. Returns (hub, now)."""
    hub = make_hub(tmp_path)
    add_env(hub)
    now = time.time()
    air(hub, now - 30, 20)
    hub.add_marker("Aligned", "test", now - 25)
    air(hub, now - 3, 26)
    kw.setdefault("hold_s", 10)
    configure(hub, paths or [path(drift_threshold_ms=1.0)], **kw)
    return hub, now


def advice_markers(hub):
    return [m for m in hub.recorder.markers() if m.source == "advice"]


# ------------------------------------------------------------------ wording
def test_the_notice_and_marker_wording():
    assert adv.drift_message("Delay tower L", 1.1, "Aligned") == \
        "Delay tower L: drifted +1.1 ms since Aligned, check delay (ADVISORY)"
    assert adv.drift_message("Fill", -0.96, "Aligned") == "Fill: drifted -1.0 ms since Aligned, check delay (ADVISORY)"
    assert adv.drift_marker_label("Delay tower L", 1.1) == "Drift: Delay tower L +1.1 ms"


# ------------------------------------------------------------------ the evaluator (pure)
def test_a_raise_needs_the_threshold_held_for_the_hold_time():
    ev = EV()
    assert ev.update("a", 0, 1.2, 1.0, 120) is None
    assert ev.update("a", 119.9, 1.2, 1.0, 120) is None
    assert ev.update("a", 120, 1.2, 1.0, 120) == "raise"
    assert ev.is_on("a") and ev.update("a", 121, 1.2, 1.0, 120) is None      # once per raise


def test_exactly_at_the_threshold_counts_and_just_under_does_not():
    ev = EV()
    assert ev.update("a", 0, 1.0, 1.0, 0) == "raise"                          # hold 0: at once
    ev = EV()
    assert ev.update("a", 0, 0.999, 1.0, 0) is None and not ev.is_on("a")


def test_a_dip_below_the_threshold_starts_the_hold_again():
    ev = EV()
    ev.update("a", 0, 1.2, 1.0, 60)
    assert ev.update("a", 50, 0.9, 1.0, 60) is None                           # dipped: pending dropped
    assert ev.update("a", 70, 1.2, 1.0, 60) is None                           # a fresh hold starts at 70
    assert ev.update("a", 129.9, 1.2, 1.0, 60) is None
    assert ev.update("a", 130, 1.2, 1.0, 60) == "raise"


def test_drift_either_way_raises():
    for sign in (1, -1):
        ev = EV()
        assert ev.update("a", 0, sign * 1.5, 1.0, 0) == "raise"


def test_it_clears_at_80_percent_of_the_threshold_not_before():
    ev = EV()
    ev.update("a", 0, 1.5, 1.0, 0)
    assert ev.update("a", 1, 0.801, 1.0, 0) is None                           # still above 0.8 x 1.0
    assert ev.update("a", 2, 0.8, 1.0, 0) == "clear"                          # at 0.8: cleared
    assert not ev.is_on("a")
    ev.update("b", 0, -1.5, 1.0, 0)
    assert ev.update("b", 1, -0.8, 1.0, 0) == "clear"                         # the same the other way


def test_noise_of_0_05_ms_around_the_threshold_does_not_flap_it():
    ev = EV()
    assert ev.update("a", 0, 1.05, 1.0, 0) == "raise"
    events = [ev.update("a", t, 1.0 + (0.05 if t % 2 else -0.05), 1.0, 0) for t in range(1, 41)]
    assert events == [None] * 40 and ev.is_on("a")                            # 0.95 is nowhere near 0.8
    # And a notice that has cleared is not raised again by noise just under the threshold.
    ev.update("a", 50, 0.7, 1.0, 0)
    assert not ev.is_on("a")
    events = [ev.update("a", 60 + t, 0.95 + (0.05 if t % 2 else -0.05), 1.0, 5) for t in range(40)]
    assert "raise" not in events and not ev.is_on("a")


def test_a_stale_reading_neither_raises_nor_clears():
    ev = EV()
    assert ev.update("a", 0, 5.0, 1.0, 0, stale=True) is None and not ev.is_on("a")   # never raises
    ev.update("a", 1, 1.5, 1.0, 0)
    assert ev.update("a", 2, 0.0, 1.0, 0, stale=True) is None and ev.is_on("a")       # never clears
    assert ev.update("a", 3, 0.0, 1.0, 0) == "clear"                                   # fresh again
    ev.update("b", 0, 1.5, 1.0, 100)
    ev.update("b", 90, 1.5, 1.0, 100, stale=True)                                      # a pending raise starts over
    assert ev.update("b", 101, 1.5, 1.0, 100) is None
    assert ev.update("b", 201, 1.5, 1.0, 100) == "raise"


def test_no_threshold_no_drift_or_a_dropped_path_clears():
    for kwargs in ({"drift_ms": 1.5, "threshold_ms": None}, {"drift_ms": None, "threshold_ms": 1.0},
                   {"drift_ms": float("nan"), "threshold_ms": 1.0}):
        ev = EV()
        ev.update("a", 0, 1.5, 1.0, 0)
        assert ev.update("a", 1, hold_s=0, **kwargs) == "clear" and not ev.is_on("a")
        assert ev.update("a", 2, hold_s=0, **kwargs) is None                          # and it stays quiet
    ev = EV()
    ev.update("a", 0, 1.5, 1.0, 0)
    ev.update("b", 0, 1.5, 1.0, 100)                                                   # b is only pending
    assert ev.tracked() == {"a", "b"}
    assert ev.drop("a") == "clear" and ev.drop("b") is None and ev.tracked() == set()


# ------------------------------------------------------------------ the notice through the hub
def test_a_drift_notice_raises_after_the_hold_silently_with_one_marker(tmp_path):
    hub, now = drifted_hub(tmp_path)
    hub.advice.tick(now)
    assert hub.alarms.active == {} and advice_markers(hub) == []              # held for 10 s first
    hub.advice.tick(now + 9)
    assert hub.alarms.active == {}
    hub.advice.tick(now + 10)
    alarm = hub.alarms.active["advice:drift:tower"]
    assert (alarm.level, alarm.silent) == (1, True)
    assert alarm.message == "tower: drifted -1.9 ms since Aligned, check delay (ADVISORY)"
    assert hub.alarms.sounding is False and hub.alarms.max_level == 0           # silent: never sounds
    markers = advice_markers(hub)
    assert [m.label for m in markers] == ["Drift: tower -1.9 ms"]
    for k in range(11, 20):
        hub.advice.tick(now + k)
    assert len(advice_markers(hub)) == 1                                        # one per raise
    log = hub.recorder.alarm_log()
    assert [(r["alarm_id"], r["event"], r["level"]) for r in log] == [("advice:drift:tower", "raise", 1)]


def test_a_drift_that_wobbles_changes_the_line_quietly(tmp_path):
    """The line follows the drift (like a threshold alarm) but nothing is logged or pushed for it."""
    hub, now = drifted_hub(tmp_path)
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    pushed = []
    hub.bus.subscribe("alarms", lambda _t, _p: pushed.append(1))
    lines = set()
    for k in range(11, 21):
        air(hub, now - 1 + k / 100, 26.3 if k % 2 else 26.0)           # -1.98 and -1.89 ms: the rounding flips
        hub.advice.tick(now + k)
        lines.add(hub.alarms.active["advice:drift:tower"].message)
    assert lines == {"tower: drifted -2.0 ms since Aligned, check delay (ADVISORY)",
                     "tower: drifted -1.9 ms since Aligned, check delay (ADVISORY)"}
    assert pushed == []
    assert len(hub.recorder.alarm_log()) == 1                          # just the raise
    hub.config.advice.alarm = "beep"                                   # a real change still goes out
    hub.advice.tick(now + 30)
    assert pushed and hub.alarms.sounding

def test_beep_sounds_at_level_one_and_never_higher(tmp_path):
    hub, now = drifted_hub(tmp_path, alarm="beep")
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    alarm = hub.alarms.active["advice:drift:tower"]
    assert (alarm.level, alarm.silent) == (1, False)
    assert hub.alarms.sounding is True and hub.alarms.max_level == 1
    assert all(a.level == 1 for a in hub.alarms.active.values())
    assert [m.label for m in hub.recorder.markers() if m.label.startswith("ALARM")] == []


def test_switching_between_silent_and_beep_changes_a_live_notice(tmp_path):
    hub, now = drifted_hub(tmp_path)
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    assert hub.alarms.active["advice:drift:tower"].silent is True
    hub.config.advice.alarm = "beep"
    hub.advice.tick(now + 11)
    assert hub.alarms.active["advice:drift:tower"].silent is False and hub.alarms.sounding
    hub.config.advice.alarm = "silent"
    hub.advice.tick(now + 12)
    assert hub.alarms.active["advice:drift:tower"].silent is True and not hub.alarms.sounding
    assert len(advice_markers(hub)) == 1


def test_alarm_off_raises_nothing_and_clears_what_is_up(tmp_path):
    hub, now = drifted_hub(tmp_path, alarm="off")
    hub.advice.tick(now)
    hub.advice.tick(now + 60)
    assert hub.alarms.active == {} and advice_markers(hub) == []
    hub.config.advice.alarm = "silent"
    hub.advice.tick(now + 61)
    hub.advice.tick(now + 71)
    assert "advice:drift:tower" in hub.alarms.active
    hub.config.advice.alarm = "off"
    hub.advice.tick(now + 72)
    assert hub.alarms.active == {}


def test_no_marker_when_marker_on_alert_is_off(tmp_path):
    hub, now = drifted_hub(tmp_path, marker_on_alert=False)
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    assert "advice:drift:tower" in hub.alarms.active and advice_markers(hub) == []


def test_it_clears_without_a_marker_when_the_crew_re_aligns(tmp_path):
    hub, now = drifted_hub(tmp_path)
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    assert len(advice_markers(hub)) == 1
    hub.add_marker("Aligned", "crew", now - 1)                                  # "I have re-aligned"
    hub.advice.tick(now + 11)
    assert hub.alarms.active == {}
    assert len(advice_markers(hub)) == 1                                        # none added on the clear


def test_each_raise_adds_its_own_marker(tmp_path):
    hub, now = drifted_hub(tmp_path)
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    hub.add_marker("Aligned", "crew", now - 1)
    hub.advice.tick(now + 11)                                                   # cleared
    air(hub, now - 0.2, 20)                                                     # back the other way: +1.9 ms
    hub.advice.tick(now + 12)
    hub.advice.tick(now + 22)
    assert "advice:drift:tower" in hub.alarms.active
    assert len(advice_markers(hub)) == 2


def test_deleting_the_marker_clears_it(tmp_path):
    hub, now = drifted_hub(tmp_path)
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    assert "advice:drift:tower" in hub.alarms.active
    ref = next(m for m in hub.recorder.markers() if m.label == "Aligned")
    hub.delete_marker(ref.id)                                                   # no reference any more
    hub.advice.tick(now + 11)
    assert hub.alarms.active == {}


def test_disabling_deleting_or_removing_the_threshold_clears_it(tmp_path):
    for change in ("disable", "delete", "no_threshold", "empty"):
        hub, now = drifted_hub(tmp_path / change)
        hub.advice.tick(now)
        hub.advice.tick(now + 10)
        assert "advice:drift:tower" in hub.alarms.active
        if change == "disable":
            configure(hub, [path(drift_threshold_ms=1.0, enabled=False)])
        elif change == "delete":
            configure(hub, [path("other", drift_threshold_ms=1.0)])
        elif change == "no_threshold":
            configure(hub, [path(drift_threshold_ms=None)])
        else:
            configure(hub)
        hub.advice.tick(now + 11)
        assert hub.alarms.active == {}, change


def test_a_stale_site_freezes_the_notice_where_it_is(tmp_path):
    hub, now = drifted_hub(tmp_path)
    hub.advice.tick(now)
    hub.advice.tick(now + 10)
    assert "advice:drift:tower" in hub.alarms.active
    for e in hub.entities.values():
        e.updated = time.time() - 1000                                          # every sensor has gone quiet
    hub.advice.tick(now + 20)
    assert hub.advice.snapshot()["paths"][0]["stale"] is True
    assert "advice:drift:tower" in hub.alarms.active                            # not cleared by silence
    assert len(advice_markers(hub)) == 1


def test_stale_readings_never_start_a_notice(tmp_path):
    hub, now = drifted_hub(tmp_path)
    for e in hub.entities.values():
        e.updated = time.time() - 1000
    hub.advice.tick(now)
    hub.advice.tick(now + 600)
    assert hub.alarms.active == {} and advice_markers(hub) == []


def test_a_threshold_that_is_not_reached_raises_nothing(tmp_path):
    hub, now = drifted_hub(tmp_path, [path(drift_threshold_ms=2.0)])             # drift is 1.89 ms
    hub.advice.tick(now)
    hub.advice.tick(now + 600)
    assert hub.alarms.active == {}


# ------------------------------------------------------------------ the admin route
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def admin(client, pin="1234"):
    assert client.post("/api/admin/setup", json={"pin": pin}).status_code == 200


GOOD = {"paths": [{"id": "tower_l", "name": "Delay tower L", "distance_m": 85, "source_area": "Tower",
                   "current_delay_ms": 240.5, "drift_threshold_ms": 0.5, "phase_hint_hz": 80}],
        "positions": [{"id": "foh", "name": "FOH", "distance_m": 40},
                      {"id": "rear", "name": "Rear", "distance_m": 90}],
        "drift_reference": "Aligned", "air_loss_reference": "Line check", "air_loss_resolution": "third",
        "alarm": "beep", "hold_s": 30, "marker_on_alert": False}


def test_the_advice_route_needs_an_admin_session(client):
    assert client.put("/api/admin/advice", json=GOOD).status_code == 401
    admin(client)
    client.cookies.clear()
    assert client.put("/api/admin/advice", json=GOOD).status_code == 401
    assert client.hub.config.advice.paths == []


def test_the_advice_route_refuses_a_foreign_origin(client):
    admin(client)
    r = client.put("/api/admin/advice", json=GOOD, headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and client.hub.config.advice.paths == []


@pytest.mark.parametrize("bad", [
    {"paths": [{"id": "a", "name": "A", "distance_m": 0}]},
    {"paths": [{"id": "a", "name": "A", "distance_m": -4}]},
    {"paths": [{"id": "a", "name": SECRETISH * 3, "distance_m": 10}]},
    {"paths": [{"id": SECRETISH, "name": "A", "distance_m": 10}]},
    {"paths": [{"id": "a", "name": "A", "distance_m": SECRETISH}]},
    {"paths": [{"id": "a", "name": "A", "distance_m": 10, "drift_threshold_ms": 0.1}]},
    {"paths": [{"id": "a", "name": "A", "distance_m": 10}, {"id": "a", "name": "B", "distance_m": 10}]},
    {"paths": [{"id": f"p{i}", "name": "P", "distance_m": 10} for i in range(13)]},
    {"positions": [{"id": "x", "name": "X", "distance_m": 2000}]},
    {"air_loss_resolution": SECRETISH}, {"alarm": SECRETISH}, {"hold_s": 901}, {"drift_reference": ""},
])
def test_a_bad_advice_body_gets_a_422_that_never_echoes_it(client, bad):
    admin(client)
    r = client.put("/api/admin/advice", json=bad)
    assert r.status_code == 422
    assert SECRETISH not in r.text and "input" not in r.json()["detail"][0]
    assert client.hub.config.advice == AdviceConfig()                           # nothing was saved


@pytest.mark.parametrize("bad", [
    {**GOOD, "surprise": SECRETISH},
    {**GOOD, "paths": [{**GOOD["paths"][0], "surprise": SECRETISH}]},
    {**GOOD, "positions": [{**GOOD["positions"][0], "surprise": SECRETISH}]},
])
def test_unknown_keys_in_an_advice_body_get_a_422_but_old_files_still_load(client, bad):
    admin(client)
    r = client.put("/api/admin/advice", json=bad)
    assert r.status_code == 422 and SECRETISH not in r.text
    assert client.hub.config.advice == AdviceConfig()
    from stagewatch.core.config import Config
    old = Config.model_validate({"advice": bad})                        # a file written by a newer build
    assert old.advice.paths[0].id == "tower_l"


def test_control_and_bidi_characters_are_stripped_from_names(client):
    admin(client)
    body = {**GOOD, "paths": [{"id": "a", "name": "Tower\u202e L\n\x07\u200b", "distance_m": 10}],
            "positions": [{"id": "b", "name": " \u2066Rear\u2029 ", "distance_m": 50}],
            "drift_reference": "Al\u202eigned"}
    r = client.put("/api/admin/advice", json=body)
    assert r.status_code == 200
    saved = client.hub.config.advice
    assert (saved.paths[0].name, saved.positions[0].name, saved.drift_reference) == ("Tower L", "Rear", "Aligned")
    assert client.put("/api/admin/advice", json={**GOOD, "paths": [
        {"id": "a", "name": "\u202e\u200b", "distance_m": 10}]}).status_code == 422     # nothing left


def test_an_oversize_advice_body_is_refused(client):
    admin(client)
    r = client.put("/api/admin/advice", content=json.dumps({**GOOD, "drift_reference": "x" * 70000}),
                   headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_the_advice_settings_round_trip_and_publish_config(client):
    admin(client)
    seen = []
    client.hub.bus.subscribe("config", lambda _t, _p: seen.append(1))
    r = client.put("/api/admin/advice", json=GOOD)
    assert r.status_code == 200 and r.json()["paths"][0]["id"] == "tower_l"
    assert seen, "saving must publish the config topic"
    saved = client.hub.config.advice
    assert saved == AdviceConfig.model_validate(GOOD)
    assert ConfigStore(client.hub.data_dir / "config.yaml").load().advice == saved     # on disk
    block = client.get("/api/snapshot").json()["advice"]                               # public, no login needed
    client.cookies.clear()
    block = client.get("/api/snapshot").json()["advice"]
    assert [p["id"] for p in block["paths"]] == ["tower_l"] and block["resolution"] == "third"
    assert [p["id"] for p in block["positions"]] == ["foh", "rear"]
    public = json.dumps(block)
    for private in ("source_area", "Tower", "hold_s", "alarm", "marker_on_alert", "Line check"):
        assert private not in public


def test_the_osc_route_takes_include_advice_and_keeps_it_when_not_sent(client):
    admin(client)
    r = client.put("/api/admin/osc", json={"destinations": [], "include_advice": True})
    assert r.status_code == 200 and client.hub.config.osc_out.include_advice is True
    assert client.put("/api/admin/osc", json={"destinations": []}).status_code == 200      # an older page
    assert client.hub.config.osc_out.include_advice is True
    client.put("/api/admin/osc", json={"destinations": [], "include_advice": False})
    assert client.hub.config.osc_out.include_advice is False


def test_include_advice_is_off_by_default_and_old_files_load():
    from stagewatch.core.config import Config, OscOutConfig
    assert OscOutConfig().include_advice is False
    assert Config.model_validate({"osc_out": {"rate_hz": 2}}).osc_out.include_advice is False


# ------------------------------------------------------------------ the live feed
IPV4 = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


async def test_the_live_feed_forwards_advice_with_no_device_ids_or_addresses(tmp_path):
    hub = make_hub(tmp_path)
    hub.register_device(Device("secret_node_7", "Node seven", "esphome", hw_id="mac:025e00000001",
                               area="Delay Tower 1", status=Status.OK, status_detail="192.0.2.77"))
    for kind in (Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE):
        hub.register_entity(Entity(f"secret_node_7.{kind.value}", "secret_node_7", kind.value, kind))
    feed = LiveFeed(hub)
    q: asyncio.Queue = asyncio.Queue()
    feed.clients.add(q)
    now = time.time()
    air(hub, now - 30, 20, dev="secret_node_7")
    hub.add_marker("Aligned", "test", now - 25)
    hub.add_marker("Soundcheck", "test", now - 24)
    air(hub, now - 3, 26, dev="secret_node_7")
    configure(hub, [path("tower", 60, name="Tower", source_area="Delay Tower 1", current_delay_ms=100)],
              [AdvicePosition(id="foh", name="FOH", distance_m=40)])
    hub.advice.tick(now)
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    sent = [m for m in msgs if m["type"] == "advice"]
    assert len(sent) == 1
    msg = sent[0]
    assert set(msg) == {"type", "drift_ref", "air_ref", "resolution", "paths", "positions"}
    assert msg["paths"][0]["drift_ms"] == pytest.approx(-1.892, abs=0.001)
    assert [b["band"] for b in msg["positions"][0]["bands"]] == ["4k", "8k", "16k"]
    assert msg["drift_ref"]["label"] == "Aligned" and msg["air_ref"]["label"] == "Soundcheck"
    text = json.dumps(msg)
    assert "secret_node_7" not in text and "mac:" not in text and "025e" not in text
    assert "192.0.2.77" not in text and not IPV4.search(text)
    assert "Delay Tower 1" not in text and "source_area" not in text
    assert msg == {**hub.advice.snapshot(), "type": "advice"}                   # the same block as the snapshot


async def test_a_bus_advice_payload_that_is_not_a_dict_is_ignored(tmp_path):
    hub = make_hub(tmp_path)
    feed = LiveFeed(hub)
    q: asyncio.Queue = asyncio.Queue()
    feed.clients.add(q)
    hub.bus.publish("advice", "junk")
    assert q.empty()


def test_a_new_screen_gets_the_advice_block_in_its_first_message(client):
    with client.websocket_connect("/ws?dashboard=foh") as ws:
        first = ws.receive_json()
    assert first["type"] == "snapshot" and set(first["advice"]) == {
        "drift_ref", "air_ref", "resolution", "paths", "positions"}


# ------------------------------------------------------------------ OSC
def osc_hub(tmp_path, **osc):
    hub, now = drifted_hub(tmp_path, [path("tower_l", 60, name="Delay tower L", current_delay_ms=100,
                                           drift_threshold_ms=1.0), path("no_ref", 10)])
    hub.add_marker("Soundcheck", "test", now - 24)
    configure(hub, hub.config.advice.paths,
              [AdvicePosition(id="foh", name="FOH", distance_m=40), AdvicePosition(id="rear", name="Rear", distance_m=90)])
    for k, v in osc.items():
        setattr(hub.config.osc_out, k, v)
    return hub, now


def test_osc_sends_no_advice_unless_it_is_switched_on(tmp_path):
    hub, _now = osc_hub(tmp_path)
    addresses = [a for a, _args in OscOutIntegration(hub).build_messages()]
    assert addresses == ["/stagewatch/avg/env", "/stagewatch/alarm"]


def test_osc_sends_the_advice_numbers_with_slug_addresses(tmp_path):
    hub, now = osc_hub(tmp_path, include_advice=True)
    hub.advice.tick(now)
    msgs = dict(OscOutIntegration(hub).build_messages())
    assert msgs["/stagewatch/path/tower_l/drift"] == [pytest.approx(-1.892, abs=0.001)]
    assert msgs["/stagewatch/path/tower_l/suggested_delay"] == [pytest.approx(100 - 1.892, abs=0.001)]
    foh = msgs["/stagewatch/position/foh/change_db"]
    assert len(foh) == 3 and all(math.isfinite(v) for v in foh)                  # 4k, 8k, 16k in order
    assert foh[0] < foh[1] < foh[2] or foh[0] > foh[1] > foh[2]                  # grows with the band
    assert len(msgs["/stagewatch/position/rear/change_db"]) == 3
    addresses = " ".join(msgs)
    assert "Delay" not in addresses and "Tower" not in addresses and "secret" not in addresses
    assert all(re.fullmatch(r"/stagewatch/[a-z/_0-9.]+", a) for a in msgs)


def test_osc_sends_nan_for_what_is_unknown(tmp_path):
    hub, now = osc_hub(tmp_path, include_advice=True)
    msgs = dict(OscOutIntegration(hub).build_messages())
    assert math.isnan(msgs["/stagewatch/path/no_ref/suggested_delay"][0])        # no current delay set
    assert not math.isnan(msgs["/stagewatch/path/no_ref/drift"][0])
    configure(hub, [path("lonely", 10, current_delay_ms=5)], [AdvicePosition(id="foh", name="FOH", distance_m=40)])
    hub.delete_marker(next(m.id for m in hub.recorder.markers() if m.label == "Aligned"))
    hub.delete_marker(next(m.id for m in hub.recorder.markers() if m.label == "Soundcheck"))
    msgs = dict(OscOutIntegration(hub).build_messages())
    assert math.isnan(msgs["/stagewatch/path/lonely/drift"][0])                  # no reference marker
    assert math.isnan(msgs["/stagewatch/path/lonely/suggested_delay"][0])
    assert all(math.isnan(v) for v in msgs["/stagewatch/position/foh/change_db"])
    assert len(msgs["/stagewatch/position/foh/change_db"]) == 3


def test_osc_sends_nan_for_stale_numbers_and_one_float_per_third_octave_band():
    advice = {"paths": [{"id": "a", "drift_ms": 0.5, "suggested_delay_ms": 10.5, "stale": True}],
              "positions": [{"id": "foh", "stale": False, "bands": [
                  {"band": b, "change_db": None if b == "10k" else 0.25}
                  for b in ("4k", "5k", "6.3k", "8k", "10k", "12.5k", "16k")]}]}
    msgs = dict(advice_messages(advice))
    assert math.isnan(msgs["/stagewatch/path/a/drift"][0]) and math.isnan(msgs["/stagewatch/path/a/suggested_delay"][0])
    foh = msgs["/stagewatch/position/foh/change_db"]
    assert len(foh) == 7 and math.isnan(foh[4]) and foh[3] == 0.25


def test_osc_skips_an_id_that_is_not_a_slug():
    advice = {"paths": [{"id": "../x", "drift_ms": 1.0, "suggested_delay_ms": None},
                        {"id": "Delay Tower", "drift_ms": 1.0, "suggested_delay_ms": None}],
              "positions": [{"id": "a b", "bands": []}]}
    assert advice_messages(advice) == []
