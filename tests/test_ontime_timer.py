"""The Ontime Timer card: parsing and merging Ontime's timer, the one shared connection, the
public message, the config option and the emulated timer.

Real Ontime 4.14.0 output (tests/fixtures/ontime/) has only been captured in playback "roll".
Everything else here (pause, armed, stop, overtime, null current, unknown values) uses SYNTHETIC
payloads built by hand in this file. They are not recordings of a real Ontime.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import cards, ontimetimer
from stagewatch.core.config import Config, OntimeTimerConfig, SiteConfig, WallClockConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.core.ontimetimer import TimerReading, TimerState, clean_title, timer_message
from stagewatch.core.wallclock import seed_emulate_demo
from stagewatch.integrations.ontime import OntimeIntegration, parse
from stagewatch.integrations.ontime.client import OntimeSource
from stagewatch.integrations.ontime.emulate import (
    TIMER_CYCLE_S, EmulatedClock, emulated_timer_state)
from stagewatch.web.server import create_app

from test_ontime import ALLOWED, FakeOntime, free_port, load, until, wait  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
UTC = SiteConfig(timezone="UTC")

PUBLIC_KEYS = {"thresholds", "status", "label", "received_at", "playback", "phase", "current_ms", "duration_ms", "elapsed_ms",
               "added_ms", "finish_in_ms", "has_event", "title", "timer_type", "warn_ms", "danger_ms"}


# ------------------------------------------------------------------ helpers
def ws_messages(name="ws_connect_1.json"):
    return [item["msg"]["payload"] for item in load(name)["messages"] if item["msg"]["tag"] == "runtime-data"]


def synthetic(timer=None, event="keep", clock=58_994_023):
    """A hand-made runtime-data payload (SYNTHETIC, not Ontime output)."""
    base = {"addedTime": 0, "current": 600_000, "duration": 1_800_000, "elapsed": 1_200_000,
            "expectedFinish": 59_400_000, "phase": "default", "playback": "play", "secondaryTimer": None,
            "startedAt": 57_600_000}
    payload = {"clock": clock, "timer": {**base, **(timer or {})}}
    if event != "keep":
        payload["eventNow"] = event
    return payload


def synthetic_event(**kw):
    return {"title": "Support act", "timerType": "count-down", "timeWarning": 120_000, "timeDanger": 60_000, **kw}


# ------------------------------------------------------------------ parsing the real fixtures
def test_first_message_is_full_and_gives_the_timer_and_the_event():
    first = ws_messages()[0]
    s = parse.parse_timer(first)
    assert s == TimerState(playback="roll", phase="default", current_ms=405_977, duration_ms=1_800_000,
                           elapsed_ms=1_394_023, added_ms=0, has_event=True, title="Event 1",
                           timer_type="count-down", warn_ms=120_000, danger_ms=60_000)


def test_partial_messages_merge_with_the_last_full_state():
    msgs = ws_messages()
    assert "eventNow" in msgs[0] and all("eventNow" not in m for m in msgs[1:])   # the real shape
    state = None
    seen = []
    for m in msgs:
        state = parse.parse_timer(m, state)
        assert state is not None
        seen.append(state.current_ms)
        # the title and the thresholds survive every partial message
        assert (state.title, state.warn_ms, state.danger_ms, state.timer_type) == ("Event 1", 120_000, 60_000, "count-down")
        assert state.playback == "roll" and state.phase == "default"
    assert seen == sorted(seen, reverse=True) and len(set(seen)) == len(seen)   # counting down, every second


@pytest.mark.parametrize("name", ["ws_connect_1.json", "ws_connect_2.json", "ws_connect_3.json"])
def test_every_ws_fixture_parses_through_the_raw_path_too(name):
    state = None
    for item in load(name)["messages"]:
        kind, ms, payload = parse.parse_ws_runtime(json.dumps(item["msg"]))
        if item["msg"]["tag"] != "runtime-data":
            assert (kind, payload) == ("ignored", None)
            continue
        assert kind == "clock" and ms == payload["clock"]
        state = parse.parse_timer(payload, state)
        assert state is not None
    assert state.title == "Event 1" and state.duration_ms == 1_800_000


def test_poll_and_runtime_data_fixtures_give_the_same_timer_as_the_websocket():
    poll = parse.poll_payload(load("api_poll.json")["body"])
    assert poll is not None
    from_poll = parse.parse_timer(poll)
    from_ws = parse.parse_timer(load("runtime_data.json")[0]["payload"])
    assert from_poll == from_ws and from_poll.title == "Event 1" and from_poll.playback == "roll"


def test_a_clock_only_message_keeps_the_last_timer():
    s = parse.parse_timer(ws_messages()[0])
    assert parse.parse_timer({"clock": 5}, s) is s
    assert parse.parse_timer({"clock": 5}, None) is None


def test_only_the_allowed_paths_exist():
    assert parse.HTTP_PATHS == ("/api/version", "/api/poll") and parse.WS_PATH == "/ws"
    assert ALLOWED == {"/api/version", "/api/poll", "/ws"}


# ------------------------------------------------------------------ synthetic states
@pytest.mark.parametrize("playback", ["play", "roll", "pause", "armed", "stop"])
def test_known_playback_values_are_kept(playback):
    assert parse.parse_timer(synthetic({"playback": playback}, synthetic_event())).playback == playback


@pytest.mark.parametrize("value", ["start", "", "PLAY", None, 3, True, ["play"]])
def test_unseen_playback_values_become_unknown(value):
    s = parse.parse_timer(synthetic({"playback": value}, synthetic_event()))
    assert s is not None and s.playback == "unknown"


@pytest.mark.parametrize("value", ["warning", "danger", "overtime"])
def test_documented_phases_are_kept_and_others_are_unknown(value):
    assert parse.parse_timer(synthetic({"phase": value})).phase == value
    assert parse.parse_timer(synthetic({"phase": "something-new"})).phase == "unknown"


def test_negative_current_is_overtime_and_null_current_is_allowed():
    assert parse.parse_timer(synthetic({"current": -30_000})).current_ms == -30_000
    s = parse.parse_timer(synthetic({"current": None, "elapsed": None, "duration": None, "playback": "stop"}))
    assert (s.current_ms, s.elapsed_ms, s.duration_ms, s.playback) == (None, None, None, "stop")
    assert parse.parse_timer(synthetic({"addedTime": None})).added_ms == 0
    assert parse.parse_timer(synthetic({"addedTime": -60_000})).added_ms == -60_000


@pytest.mark.parametrize("key,value", [
    ("current", 1.5), ("current", True), ("current", "600000"), ("current", 100 * 3_600_000 + 1), ("current", -100 * 3_600_000 - 1),
    ("duration", -1), ("duration", 24 * 3_600_000 + 1), ("duration", 1e9), ("elapsed", "x"), ("elapsed", [1]),
    ("addedTime", False), ("addedTime", 2.0), ("addedTime", 25 * 3_600_000)])
def test_bad_timer_numbers_make_the_whole_timer_unreadable(key, value):
    assert parse.parse_timer(synthetic({key: value}, synthetic_event())) is None


def test_the_edges_of_the_ranges_are_accepted():
    s = parse.parse_timer(synthetic({"current": -100 * 3_600_000 + 1, "duration": 24 * 3_600_000, "addedTime": 24 * 3_600_000}))
    assert s is not None and s.duration_ms == 24 * 3_600_000


@pytest.mark.parametrize("timer", [None, [], "x", 5, True])
def test_a_timer_that_is_not_an_object_is_unreadable(timer):
    assert parse.parse_timer({"clock": 1, "timer": timer}) is None


def test_no_event_loaded_and_event_changes():
    s = parse.parse_timer(synthetic(event=None))
    assert (s.has_event, s.title, s.timer_type, s.warn_ms, s.danger_ms) == (False, "", "none", None, None)
    # an eventNow of null clears the event that came before
    with_event = parse.parse_timer(synthetic(event=synthetic_event()))
    assert with_event.title == "Support act"
    cleared = parse.parse_timer(synthetic(event=None), with_event)
    assert cleared.has_event is False and cleared.title == ""
    # a new eventNow replaces it; absent keeps it
    other = parse.parse_timer(synthetic(event=synthetic_event(title="Headliner", timeWarning=30_000)), with_event)
    assert (other.title, other.warn_ms, other.danger_ms) == ("Headliner", 30_000, 60_000)
    assert parse.parse_timer(synthetic(), other).title == "Headliner"
    assert parse.parse_timer(synthetic(event="nonsense")).has_event is False


def test_event_fields_that_are_wrong_do_not_hide_the_timer():
    s = parse.parse_timer(synthetic(event=synthetic_event(timeWarning="2m", timeDanger=True, timerType="stopwatch", title=7)))
    assert s is not None
    assert (s.warn_ms, s.danger_ms, s.timer_type, s.title) == (None, None, "unknown", "")
    assert parse.parse_timer(synthetic(event=synthetic_event(timerType="count-up"))).timer_type == "count-up"
    assert parse.parse_timer(synthetic(event=synthetic_event(timeWarning=-5))).warn_ms is None


def test_a_timer_with_a_bad_clock_still_parses_and_the_clock_is_invalid():
    payload = synthetic(clock="noon")
    assert parse.parse_timer(payload) is not None
    raw = json.dumps({"tag": "runtime-data", "payload": payload})
    kind, ms, got = parse.parse_ws_runtime(raw)
    assert (kind, ms) == ("invalid", None) and got is not None and parse.parse_timer(got) is not None
    assert parse.parse_ws_message(raw) == ("invalid", None)   # the old function still answers as before


def test_titles_are_cleaned():
    assert clean_title("  Support\tact \n two ") == "Support act two"
    assert clean_title("A‮B​C\u0000D\x07E") == "ABCDE"        # bidi override, zero-width, NUL, bell
    assert clean_title("x" * 500) == "x" * ontimetimer.TITLE_MAX
    assert len(clean_title("é" * 500)) == ontimetimer.TITLE_MAX
    assert clean_title(None) == "" and clean_title(5) == "" and clean_title(["a"]) == ""
    assert clean_title("\ud800lone") == "lone"                              # a lone surrogate
    assert clean_title("<b>Act</b>") == "<b>Act</b>"                       # markup stays text; the card uses textContent
    s = parse.parse_timer(synthetic(event=synthetic_event(title="A‮B" + "z" * 200)))
    assert "‮" not in s.title and len(s.title) <= ontimetimer.TITLE_MAX


def test_parsing_does_not_keep_anything_else_from_the_rundown():
    payload = synthetic(event=synthetic_event(note="secret note", cue="9", custom={"x": "y"}, colour="#fff"))
    payload["eventNext"] = {"title": "Next act"}
    blob = repr(parse.parse_timer(payload))
    assert "secret" not in blob and "Next act" not in blob and "#fff" not in blob


# ------------------------------------------------------------------ the public message
def reading(**kw):
    state = TimerState(**{"playback": "play", "phase": "default", "current_ms": 600_000, "duration_ms": 1_800_000,
                          "elapsed_ms": 1_200_000, "added_ms": 0, "has_event": True, "title": "Support act",
                          "timer_type": "count-down", "warn_ms": 120_000, "danger_ms": 60_000, **kw})
    return TimerReading(state, 1_790_000_000.0, "ok", "WebSocket")


def test_message_shape_has_only_the_agreed_fields():
    m = timer_message("Ontime", reading())
    assert set(m) == PUBLIC_KEYS
    assert m["finish_in_ms"] == 600_000 and m["title"] == "Support act" and m["status"] == "ok"
    assert "WebSocket" not in json.dumps(m)                       # the detail text is not sent


def test_finish_only_while_running_a_count_down_with_time_left():
    assert timer_message("Ontime", reading(playback="roll"))["finish_in_ms"] == 600_000
    for kw in ({"playback": "pause"}, {"playback": "armed"}, {"playback": "stop"}, {"playback": "unknown"},
               {"current_ms": -5000}, {"current_ms": None}, {"timer_type": "count-up"}):
        assert timer_message("Ontime", reading(**kw))["finish_in_ms"] is None, kw


def test_hidden_title_is_empty_in_the_message():
    assert timer_message("Ontime", reading(), show_title=False)["title"] == ""
    assert timer_message("Ontime", reading(), show_title=False)["has_event"] is True


def test_offline_and_error_messages_carry_no_timer():
    for status in ("offline", "error"):
        m = timer_message("Ontime", TimerReading(None, 5.0, status, "Can't reach Ontime"))
        assert set(m) == PUBLIC_KEYS and m["status"] == status
        assert m["current_ms"] is None and m["playback"] is None and m["title"] == "" and m["added_ms"] == 0
        assert "reach" not in json.dumps(m)
    assert timer_message("Ontime", TimerReading(reading().state, 5.0, "offline"))["current_ms"] is None   # never a stale value


# ------------------------------------------------------------------ the source reads the timer
def fast_source(url):
    return OntimeSource(lambda: url, poll_every_s=0.05, backoff_min_s=0.05, backoff_max_s=0.2, no_data_s=1.0,
                        open_timeout_s=1.0)


async def test_source_merges_the_timer_over_the_websocket_and_sends_nothing():
    async with FakeOntime() as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: src.latest_timer().status == "ok")
            await until(lambda: src.latest().clock_ms is not None and src.transport == "websocket")
            await asyncio.sleep(0.3)   # the fake then sends clock-only messages
            t = src.latest_timer()
            assert t.status == "ok" and t.state.title == "Event 1" and t.state.playback == "roll"
            assert t.state.warn_ms == 120_000
        finally:
            await src.stop()
        assert fake.received == [] and set(fake.paths) <= ALLOWED


class FullPollOntime(FakeOntime):
    """Like FakeOntime but its /api/poll answers with the real captured body."""

    def process_request(self, connection, request):
        if request.path == "/api/poll" and self.poll == "ok":
            self.paths.append(request.path)
            return self._respond(connection, 202, json.dumps(load("api_poll.json")["body"]))
        return super().process_request(connection, request)


async def test_source_reads_the_timer_by_polling_when_the_websocket_fails():
    async with FullPollOntime(ws=False) as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: src.latest_timer().status == "ok" and src.transport == "polling")
            assert src.latest_timer().state.title == "Event 1"
        finally:
            await src.stop()
        assert set(fake.paths) <= ALLOWED and fake.received == []


async def test_a_bad_clock_does_not_hide_a_good_timer_and_the_device_stays_ok(tmp_path):
    hub = Hub(tmp_path)
    inner = fast_source("http://127.0.0.1:1")
    hub.add_integration(OntimeIntegration(hub, inner=inner))
    integ = hub.integrations["ontime"]
    await integ.clock_source.acquire("ontime_timer")
    try:
        inner._ingest(synthetic(clock="noon", event=synthetic_event()), "websocket")
        assert inner.latest().status == "error" and inner.latest_timer().status == "ok"
        assert hub.devices["ontime"].status == Status.OK
        inner._ingest({"clock": 5, "timer": {"current": 1.5}}, "websocket")    # an unreadable timer
        assert inner.latest_timer().status == "error" and inner.latest_timer().state is None
        assert inner.latest_timer().detail == "Ontime sent a timer we can't read"
        inner._lost("unreachable")
        assert inner.latest_timer().status == "offline" and hub.devices["ontime"].status == Status.MISSING
        # after the connection is lost the merge starts again: nothing from the old event survives
        inner._ingest({"clock": 5, "timer": synthetic()["timer"]}, "websocket")
        assert inner.latest_timer().state.has_event is False and inner.latest_timer().state.title == ""
    finally:
        await integ.clock_source.release("ontime_timer")
        await hub.stop()


# ------------------------------------------------------------------ one shared connection (refcount)
class Probe(EmulatedClock):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.starts = self.stops = 0

    async def start(self):
        self.starts += 1
        await super().start()

    async def stop(self):
        self.stops += 1
        await super().stop()


def set_cards(hub, *ids):
    for d in hub.config.dashboards:
        d.cards = [c for c in d.cards if c not in ("wall_clock", "ontime_timer")]
    hub.config.dashboards[0].cards += list(ids)
    hub.save_config()


async def probe_hub(tmp_path, source="pc"):
    hub = Hub(tmp_path, emulate=True)
    hub.config.wall_clock.source = source
    probe = Probe(lambda: hub.config.site, cycle=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True, inner=probe))
    await hub.start()
    return hub, probe


async def test_nothing_runs_without_a_card(tmp_path):
    hub, probe = await probe_hub(tmp_path, "ontime")
    try:
        assert probe.starts == 0 and "ontime" not in hub.devices
        assert hub.snapshot()["ontime_timer"] is None and hub.ontime_timer.active is False
        assert all("ontime_timer" not in d.cards for d in hub.config.dashboards)   # never added by default
    finally:
        await hub.stop()


async def test_the_timer_card_alone_runs_only_ontime_while_the_clock_uses_the_pc(tmp_path):
    hub, probe = await probe_hub(tmp_path, "pc")
    try:
        set_cards(hub, "wall_clock", "ontime_timer")
        await until(lambda: hub.ontime_timer.active and hub.wall_clock.active)
        assert probe.starts == 1 and hub.wall_clock.admin_status()["source"] == "pc"
        assert hub.snapshot()["wall_clock"]["source"] == "pc"
        timer = hub.snapshot()["ontime_timer"]
        assert timer["status"] == "ok" and timer["label"] == "Ontime" and set(timer) == PUBLIC_KEYS
        set_cards(hub, "wall_clock")
        await until(lambda: not hub.ontime_timer.active)
        assert probe.stops == 1 and "ontime" not in hub.devices and hub.snapshot()["ontime_timer"] is None
        assert hub.wall_clock.active   # the PC clock carries on
    finally:
        await hub.stop()


async def test_both_cards_share_one_source_and_it_stops_with_the_last(tmp_path):
    hub, probe = await probe_hub(tmp_path, "ontime")
    try:
        set_cards(hub, "wall_clock", "ontime_timer")
        await until(lambda: hub.ontime_timer.active and hub.wall_clock.active)
        assert probe.starts == 1 and probe.stops == 0
        assert hub.integrations["ontime"].clock_source.holders == {"wall_clock", "ontime_timer"}
        set_cards(hub, "ontime_timer")                      # the clock card goes: the timer keeps it
        await until(lambda: not hub.wall_clock.active)
        assert probe.starts == 1 and probe.stops == 0 and "ontime" in hub.devices
        assert hub.snapshot()["ontime_timer"]["status"] == "ok"
        set_cards(hub, "wall_clock")                        # and back the other way round
        await until(lambda: hub.wall_clock.active and not hub.ontime_timer.active)
        assert probe.starts == 1 and probe.stops == 0 and hub.snapshot()["wall_clock"]["status"] == "ok"
        set_cards(hub)
        await until(lambda: not hub.wall_clock.active)
        assert probe.starts == 1 and probe.stops == 1 and "ontime" not in hub.devices
    finally:
        await hub.stop()


async def test_changing_the_address_restarts_the_shared_source_once(tmp_path):
    hub, probe = await probe_hub(tmp_path, "ontime")
    try:
        set_cards(hub, "wall_clock", "ontime_timer")
        await until(lambda: hub.ontime_timer.active and hub.wall_clock.active)
        hub.config.wall_clock = WallClockConfig(source="ontime", ontime_url="http://10.1.2.3:4001")
        hub.save_config()
        await until(lambda: probe.starts == 2)
        await asyncio.sleep(0.3)
        assert probe.starts == 2 and hub.ontime_timer.active and hub.wall_clock.active
        # timer only, clock on the PC: the address change still reaches the timer's connection
        hub.config.wall_clock = WallClockConfig(source="pc", ontime_url="http://10.1.2.3:4001")
        set_cards(hub, "ontime_timer")
        await until(lambda: not hub.wall_clock.active)
        hub.config.wall_clock = WallClockConfig(source="pc", ontime_url="http://10.9.9.9:4001")
        hub.save_config()
        await until(lambda: probe.starts == 3)
    finally:
        await hub.stop()


async def test_the_timer_card_with_no_ontime_integration_is_harmless(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    await hub.start()
    try:
        set_cards(hub, "ontime_timer")
        await asyncio.sleep(0.2)
        assert hub.snapshot()["ontime_timer"] is None
    finally:
        await hub.stop()


async def test_real_source_with_both_cards_opens_one_websocket(tmp_path):
    async with FakeOntime() as fake:
        hub = Hub(tmp_path)
        hub.config.wall_clock = WallClockConfig(source="ontime", ontime_url=fake.url)
        hub.add_integration(OntimeIntegration(hub, inner=fast_source(fake.url)))
        await hub.start()
        try:
            set_cards(hub, "wall_clock", "ontime_timer")
            await until(lambda: hub.snapshot()["ontime_timer"] and hub.snapshot()["ontime_timer"]["status"] == "ok")
            await until(lambda: hub.snapshot()["wall_clock"] and hub.snapshot()["wall_clock"]["status"] == "ok")
            await asyncio.sleep(0.3)
            assert fake.paths.count("/ws") == 1 and set(fake.paths) <= ALLOWED
            snap = hub.snapshot()["ontime_timer"]
            assert snap["title"] == "Event 1" and snap["playback"] == "roll" and snap["warn_ms"] == 120_000
            assert fake.url not in json.dumps(hub.snapshot()["ontime_timer"])
        finally:
            await hub.stop()
        assert fake.received == []


async def test_unreachable_ontime_with_only_the_timer_card_is_a_silent_alarm(tmp_path):
    hub = Hub(tmp_path)
    url = f"http://127.0.0.1:{free_port()}"
    hub.config.wall_clock = WallClockConfig(source="pc", ontime_url=url)
    hub.add_integration(OntimeIntegration(hub, inner=OntimeSource(lambda: hub.config.wall_clock.ontime_url,
                                                                  poll_every_s=0.05, backoff_min_s=0.05, backoff_max_s=0.1)))
    await hub.start()
    try:
        set_cards(hub, "ontime_timer")
        await until(lambda: hub.devices.get("ontime") and hub.devices["ontime"].status == Status.MISSING, timeout=30)
        alarm = hub.alarms.to_list()[0]
        assert alarm["silent"] is True and hub.alarms.sounding is False and hub.alarms.max_level == 0
        snap = hub.snapshot()["ontime_timer"]
        assert snap["status"] == "offline" and snap["current_ms"] is None and url not in json.dumps(snap)
    finally:
        await hub.stop()


# ------------------------------------------------------------------ emulate
def test_the_emulated_timer_cycles_through_every_state():
    def at(t):
        return emulated_timer_state(t)

    s = at(10)
    assert (s.playback, s.current_ms, s.added_ms, s.phase, s.title) == ("play", 50_000, 0, "default", "Emulated: Support act")
    assert at(15).phase == "default" and at(15).current_ms == 45_000
    s = at(25)
    assert (s.playback, s.current_ms) == ("pause", 40_000)
    s = at(31)
    assert (s.playback, s.added_ms, s.current_ms) == ("play", 20_000, 59_000)       # +0:20 added when it resumes
    assert (at(70).phase, at(70).current_ms) == ("warning", 20_000) and (at(85).phase, at(85).current_ms) == ("danger", 5_000)
    s = at(100)
    assert (s.playback, s.current_ms, s.phase) == ("play", -10_000, "overtime")
    assert at(119.999).current_ms < -29_000 and at(119.999).phase == "overtime"   # to -0:30
    s = at(125)
    assert (s.playback, s.current_ms, s.elapsed_ms) == ("stop", None, None) and s.has_event
    s = at(140)
    assert (s.playback, s.current_ms) == ("armed", 60_000)
    assert at(10 + TIMER_CYCLE_S) == at(10)                                            # it repeats
    assert {at(t).playback for t in range(180)} == {"play", "pause", "stop", "armed"}
    assert any(at(t).current_ms is not None and at(t).current_ms < 0 for t in range(180))


def test_emulated_timer_goes_stale_then_offline_in_cycle_mode():
    now = [1_000_000.0]
    clock = EmulatedClock(lambda: UTC, clock=lambda: now[0], cycle=True)
    t0 = now[0]

    def at(t):
        now[0] = t0 + t
        return clock.latest_timer()

    r = at(10)
    assert r.status == "ok" and r.state.playback == "play" and r.received_at == now[0]
    r = at(66)                                       # nothing new since t=60: the reading ages
    assert r.status == "ok" and r.state.current_ms == emulated_timer_state(60).current_ms and now[0] - r.received_at == pytest.approx(6)
    r = at(155)
    assert r.status == "offline" and r.state is None
    assert at(170).status == "ok" and at(170).state.playback == "armed"


def test_emulated_timer_follows_the_clock_dropout_when_not_cycling():
    now = [1_000_000.0]
    clock = EmulatedClock(lambda: UTC, clock=lambda: now[0], dropout_every_s=900, dropout_s=20)
    t0 = now[0]
    now[0] = t0 + 10
    assert clock.latest_timer().status == "ok"
    now[0] = t0 + 890
    assert clock.latest_timer().status == "offline"


async def test_emulate_mode_publishes_the_timer_through_the_hub(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True))
    await hub.start()
    try:
        set_cards(hub, "ontime_timer")
        await until(lambda: hub.ontime_timer.active)
        snap = hub.snapshot()["ontime_timer"]
        assert snap["status"] == "ok" and snap["title"] == "Emulated: Support act" and snap["timer_type"] == "count-down"
        assert hub.devices["ontime"].status == Status.OK
        got = []
        hub.bus.subscribe("ontime_timer", lambda _t, p: got.append(p))
        await until(lambda: len(got) >= 1, timeout=3)
        assert set(got[0]) == PUBLIC_KEYS
    finally:
        await hub.stop()


def test_the_emulate_demo_puts_the_timer_on_the_wall_dashboard_only():
    cfg = Config()
    assert seed_emulate_demo(cfg) is True
    on = {d.slug: "ontime_timer" in d.cards for d in cfg.dashboards}
    assert on == {"foh": False, "phone": False, "wall": True}


# ------------------------------------------------------------------ config and cards
def test_card_id_is_known_and_never_a_default():
    assert "ontime_timer" in cards.KNOWN_CARDS
    assert "ontime_timer" not in sum(cards.LAYOUT_DEFAULTS.values(), ())
    assert cards.strict_cards_error(["env_tiles", "ontime_timer"]) is None
    assert "ontime_timer" not in cards.default_cards("wall")


def test_config_option_defaults_to_showing_the_title_and_loads_old_files():
    assert OntimeTimerConfig().show_title is True
    cfg = Config.model_validate({"wall_clock": {"source": "pc"}})          # a file from before this release
    assert cfg.ontime_timer.show_title is True
    assert Config.model_validate({"ontime_timer": {"show_title": False}}).ontime_timer.show_title is False
    assert Config.model_validate({"ontime_timer": {"show_title": False, "future": 1}}).ontime_timer.show_title is False


# ------------------------------------------------------------------ web
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True))
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        yield c


def admin(c):
    assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200


def put_cards(c, *ids):
    dashboards = c.get("/api/info").json()["dashboards"]
    dashboards[0]["cards"] = [x for x in dashboards[0]["cards"] if x != "ontime_timer"] + list(ids)
    r = c.put("/api/admin/dashboards", json=dashboards)
    assert r.status_code == 200, r.text


def test_the_card_round_trips_and_the_timer_reaches_dashboards(client):
    admin(client)
    assert client.get("/api/snapshot").json()["ontime_timer"] is None
    put_cards(client, "ontime_timer")
    assert "ontime_timer" in client.hub.config.dashboards[0].cards
    wait(lambda: client.hub.ontime_timer.active)
    snap = client.get("/api/snapshot").json()["ontime_timer"]
    assert set(snap) == PUBLIC_KEYS and snap["status"] == "ok" and snap["title"] == "Emulated: Support act"
    state = client.get("/api/admin/state").json()
    assert state["ontime_timer"]["active"] is True and state["ontime_timer"]["card_assigned"] is True
    assert "ontime_url" not in state["ontime_timer"] and state["config"]["ontime_timer"] == {"show_title": True}
    slug = client.hub.config.dashboards[0].slug
    with client.websocket_connect("/ws?dashboard=" + slug) as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["ontime_timer"]["status"] == "ok"
        for _ in range(80):
            msg = ws.receive_json()
            if msg["type"] == "ontime_timer":
                assert set(msg) == PUBLIC_KEYS | {"type"}
                break
        else:
            pytest.fail("no ontime_timer message on the live feed")
    put_cards(client)
    wait(lambda: not client.hub.ontime_timer.active)
    assert client.get("/api/snapshot").json()["ontime_timer"] is None


def test_hiding_the_title_removes_it_from_every_public_message(client):
    admin(client)
    put_cards(client, "ontime_timer")
    wait(lambda: client.hub.ontime_timer.active)
    assert client.get("/api/snapshot").json()["ontime_timer"]["title"] != ""
    r = client.put("/api/admin/ontime-timer", json={"show_title": False})
    assert r.status_code == 200 and r.json() == {"show_title": False}
    assert client.hub.config.ontime_timer.show_title is False
    assert client.get("/api/snapshot").json()["ontime_timer"]["title"] == ""
    assert "Emulated" not in json.dumps(client.get("/api/snapshot").json()["ontime_timer"])
    assert client.get("/api/admin/state").json()["config"]["ontime_timer"] == {"show_title": False}
    assert client.put("/api/admin/ontime-timer", json={"show_title": True}).json() == {"show_title": True}
    wait(lambda: client.get("/api/snapshot").json()["ontime_timer"]["title"] != "")


def test_the_option_endpoint_needs_admin_and_same_origin_and_validates(client):
    assert client.put("/api/admin/ontime-timer", json={"show_title": False}).status_code == 401
    admin(client)
    assert client.put("/api/admin/ontime-timer", json={"show_title": False},
                      headers={"origin": "http://evil.example"}).status_code == 403
    assert client.hub.config.ontime_timer.show_title is True
    for bad in ({"show_title": "perhaps"}, {"show_title": [1]}):
        assert client.put("/api/admin/ontime-timer", json=bad).status_code == 422
    assert client.hub.config.ontime_timer.show_title is True


def test_the_timer_snapshot_carries_nothing_private(client):
    admin(client)
    put_cards(client, "ontime_timer")
    wait(lambda: client.hub.ontime_timer.active)
    text = json.dumps(client.get("/api/snapshot").json()["ontime_timer"])
    assert "127.0.0.1" not in text and "4001" not in text and "http" not in text
    for forbidden in ("note", "cue", "custom", "colour", "url", "secondary", "expected", "startedAt"):
        assert forbidden not in text


# ------------------------------------------------------------------ static
def test_page_loads_the_timer_script_after_common_and_before_dashboard():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    order = [html.index(f"/static/{n}.js") for n in ("common", "ontimetimer", "dashboard")]
    assert order == sorted(order)
    assert 'data-card="ontime_timer"' in html and 'id="ontime-timer-card"' in html


def test_timer_script_uses_only_safe_dom_and_no_animation_loop():
    js = re.sub(r"//.*", "", (STATIC / "ontimetimer.js").read_text(encoding="utf-8"))
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "requestAnimationFrame", "<canvas", "fetch(", "XMLHttpRequest"):
        assert bad not in js
    assert "http://" not in js and "https://" not in js


def test_admin_names_the_card_and_has_the_title_option():
    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert "ontime_timer:" in js and "/api/admin/ontime-timer" in js and "Show the event title on dashboards" in js
    assert "also used by the Ontime Timer card" in js


def test_timer_logic_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "ontime_timer_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


# ------------------------------------------------------------------ review fixes
class SlowProbe(Probe):
    """An emulated source whose start waits for the test to let it go."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def start(self):
        self.starts += 1
        self.entered.set()
        await self.gate.wait()
        await EmulatedClock.start(self)


async def slow_hub(tmp_path, source="pc"):
    hub = Hub(tmp_path, emulate=True)
    hub.config.wall_clock.source = source
    probe = SlowProbe(lambda: hub.config.site, cycle=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True, inner=probe))
    await hub.start()
    return hub, probe


async def test_a_second_save_during_a_slow_start_is_not_lost_for_the_timer(tmp_path):
    hub, probe = await slow_hub(tmp_path)
    try:
        set_cards(hub, "ontime_timer")
        await asyncio.wait_for(probe.entered.wait(), 3)       # the first apply is stuck starting the source
        set_cards(hub)                                          # the card is taken off meanwhile
        probe.gate.set()
        await until(lambda: not hub.ontime_timer.active and probe.stops == 1
        and "ontime" not in hub.devices and hub.integrations["ontime"].clock_source.holders == frozenset())
        assert "ontime" not in hub.devices and hub.integrations["ontime"].clock_source.holders == frozenset()
    finally:
        await hub.stop()


async def test_a_second_save_during_a_slow_start_is_not_lost_for_the_wall_clock(tmp_path):
    hub, probe = await slow_hub(tmp_path, "ontime")
    try:
        set_cards(hub, "wall_clock")
        await asyncio.wait_for(probe.entered.wait(), 3)
        set_cards(hub)
        probe.gate.set()
        await until(lambda: not hub.wall_clock.active and probe.stops == 1 and "ontime" not in hub.devices)
        assert "ontime" not in hub.devices
    finally:
        await hub.stop()


async def test_cancelling_during_start_leaves_no_holder_and_stops_the_client(tmp_path):
    hub, probe = await slow_hub(tmp_path)
    src = hub.integrations["ontime"].clock_source
    set_cards(hub, "ontime_timer")
    await asyncio.wait_for(probe.entered.wait(), 3)            # inside acquire, before it has finished
    await hub.stop()                                            # cancels the apply, then releases by name
    assert src.holders == frozenset() and probe.stops >= 1 and "ontime" not in hub.devices


async def test_cancelling_the_wall_clock_during_start_leaves_no_holder(tmp_path):
    hub, probe = await slow_hub(tmp_path, "ontime")
    src = hub.integrations["ontime"].clock_source
    set_cards(hub, "wall_clock")
    await asyncio.wait_for(probe.entered.wait(), 3)
    await hub.stop()
    assert src.holders == frozenset() and probe.stops >= 1


def test_an_event_change_without_a_timer_merges_into_the_last_state():
    """SYNTHETIC payloads (hand-made, not Ontime output)."""
    prev = parse.parse_timer(synthetic(event=synthetic_event()))
    changed = parse.parse_timer({"clock": 1, "eventNow": synthetic_event(title="Headliner", timeWarning=30_000, timeDanger=10_000)}, prev)
    assert (changed.title, changed.warn_ms, changed.danger_ms) == ("Headliner", 30_000, 10_000)
    assert (changed.playback, changed.current_ms, changed.duration_ms) == (prev.playback, prev.current_ms, prev.duration_ms)
    cleared = parse.parse_timer({"eventNow": None}, prev)
    assert cleared.has_event is False and cleared.current_ms == prev.current_ms
    assert parse.parse_timer({"eventNow": synthetic_event()}, None) is None     # nothing to merge into yet


async def test_the_source_applies_an_event_only_message(tmp_path):
    inner = fast_source("http://127.0.0.1:1")
    inner._ingest(synthetic(event=synthetic_event()), "websocket")
    inner._ingest({"clock": 5, "eventNow": synthetic_event(title="Next one")}, "websocket")   # SYNTHETIC
    assert inner.latest_timer().state.title == "Next one" and inner.latest_timer().state.current_ms == 600_000
    inner2 = fast_source("http://127.0.0.1:1")
    inner2._ingest({"clock": 5, "eventNow": synthetic_event()}, "websocket")
    assert inner2.latest_timer().status == "offline"        # not an error: no timer yet


def test_the_option_body_is_strict_and_required(client):
    admin(client)
    assert client.put("/api/admin/ontime-timer", json={"show_title": False}).status_code == 200
    for bad in ({}, {"showTitle": True}, {"showTitle": False}, {"show_title": "false"}, {"show_title": 0},
                {"show_title": None}, {"show_title": True, "extra": 1}):
        assert client.put("/api/admin/ontime-timer", json=bad).status_code == 422, bad
    assert client.hub.config.ontime_timer.show_title is False      # never silently reset to shown


def test_combining_mark_piles_are_capped():
    zalgo = "a" + "̀́̂̃̄̅" + "b"
    assert clean_title(zalgo) == "à́b"
    assert clean_title("é café") == "é café"                  # normal accents are kept
    assert clean_title("a" + "̀" * 500) == "à̀"
    assert clean_title("̀́̂x") == "̀́x"                     # marks with no base too
    assert clean_title("\U0001F468‍\U0001F469") == "\U0001F468\U0001F469"      # ZWJ is dropped (documented)


def test_the_message_says_where_the_warning_times_come_from():
    assert timer_message("Ontime", reading())["thresholds"] == "ontime"
    assert timer_message("Ontime", reading(warn_ms=None))["thresholds"] == "ontime"       # one of the two is enough
    assert timer_message("Ontime", reading(warn_ms=None, danger_ms=None))["thresholds"] == "site"
    assert timer_message("Ontime", TimerReading(None, 1.0, "offline"))["thresholds"] is None
    s = parse.parse_timer(synthetic(event=synthetic_event(timeWarning=None, timeDanger=None)))
    assert timer_message("Ontime", TimerReading(s, 1.0, "ok"))["thresholds"] == "site"


def test_the_emulate_demo_puts_the_timer_above_the_clock_on_the_wall():
    cfg = Config()
    assert seed_emulate_demo(cfg)
    wall = cfg.dashboard("wall").cards
    assert wall.index("ontime_timer") < wall.index("wall_clock") and wall.index("ontime_timer") == 2   # after the rundown card


def test_the_title_clips():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.ot-title \{[^}]*overflow: hidden", css)