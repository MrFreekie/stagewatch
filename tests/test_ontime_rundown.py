"""The Ontime Rundown card: parsing and merging Ontime's rundown and offset blocks (both the
documented shape and the one real 4.14.0 sends), the shared connection, the public message, the
emulated story and the static page.

Real Ontime 4.14.0 output (tests/fixtures/ontime/) has only been captured in one state: running
the 9th of 16 events, offset 0, mode "absolute". Everything else here (finished, nothing loaded,
behind or ahead, relative mode, the documented layout, later days) uses SYNTHETIC payloads built by
hand in this file. They are not recordings of a real Ontime.
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

from stagewatch.core import cards
from stagewatch.core.config import Config, WallClockConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.core.ontimerundown import RundownReading, RundownState, rundown_message
from stagewatch.core.wallclock import seed_emulate_demo
from stagewatch.integrations.ontime import OntimeIntegration, parse
from stagewatch.integrations.ontime.client import OntimeSource
from stagewatch.integrations.ontime.emulate import (
    RUNDOWN_CYCLE_S, RUNDOWN_OFFLINE, RUNDOWN_STALE, EmulatedClock, emulated_rundown_clock, emulated_rundown_state)
from stagewatch.web.server import create_app

from test_ontime import ALLOWED, FakeOntime, free_port, load, until, wait  # noqa: E402
from test_ontime_timer import Probe, fast_source  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"

PUBLIC_KEYS = {"status", "label", "received_at", "position", "offset_ms", "offset_mode", "planned_start_ms",
               "planned_end_ms", "expected_end_ms", "actual_start_ms", "current_day", "ontime_clock_ms", "unreadable"}
# current_day (only to say "a later day") and ontime_clock_ms (Ontime's clock, for the day bar) are public on purpose.


def ws_messages(name="ws_connect_1.json"):
    return [item["msg"]["payload"] for item in load(name)["messages"] if item["msg"]["tag"] == "runtime-data"]


# SYNTHETIC blocks ---------------------------------------------------------
def rundown_block(**kw):
    base = {"selectedEventIndex": 3, "numEvents": 12, "plannedStart": 41_400_000, "plannedEnd": 81_000_000,
            "actualStart": 41_400_000, "actualGroupStart": None, "currentDay": 0}
    return {**base, **kw}


def offset_block(**kw):
    base = {"absolute": -250_000, "relative": -90_000, "mode": "absolute", "expectedFlagStart": None,
            "expectedGroupEnd": None, "expectedRundownEnd": 81_250_000}
    return {**base, **kw}


def docs_rundown(**kw):
    """The shape Ontime's documentation describes: offset and expectedEnd inside rundown."""
    return rundown_block(offset=-250_000, expectedEnd=81_250_000, **kw)


# ------------------------------------------------------------------ real fixtures
def test_the_real_first_message_gives_the_rundown_and_the_offset():
    s = parse.parse_rundown(ws_messages()[0])
    assert s == RundownState(
        selected_index=8, num_events=16, planned_start_ms=41_400_000, planned_end_ms=81_000_000,
        actual_start_ms=41_400_000, current_day=0, offset_absolute_ms=0, offset_relative_ms=0, offset_mode="absolute",
        offset_expected_end_ms=81_000_000)
    assert (s.offset_ms, s.offset_kind, s.expected_end_ms) == (0, "absolute", 81_000_000)


def test_later_real_messages_carry_no_rundown_and_the_last_one_is_kept():
    msgs = ws_messages()
    assert all("rundown" not in m and "offset" not in m for m in msgs[1:])   # the real shape
    state = None
    for m in msgs:
        state = parse.parse_rundown(m, state)
    assert state is not None and state.selected_index == 8 and state.num_events == 16


@pytest.mark.parametrize("name", ["ws_connect_1.json", "ws_connect_2.json", "ws_connect_3.json"])
def test_every_ws_fixture_gives_the_same_rundown_through_the_raw_path(name):
    state = None
    for item in load(name)["messages"]:
        kind, _ms, payload = parse.parse_ws_runtime(json.dumps(item["msg"]))
        if kind == "clock":
            state = parse.parse_rundown(payload, state)
    assert state is not None and (state.selected_index, state.num_events) == (8, 16)


def test_poll_and_runtime_data_fixtures_give_the_same_rundown_as_the_websocket():
    poll = parse.poll_payload(load("api_poll.json")["body"])
    from_poll = parse.parse_rundown(poll)
    assert from_poll == parse.parse_rundown(load("runtime_data.json")[0]["payload"]) == parse.parse_rundown(ws_messages()[0])


def test_the_real_fixtures_hold_no_titles_or_lists_in_the_rundown_block():
    block = ws_messages()[0]["rundown"]
    assert set(block) == {"selectedEventIndex", "numEvents", "plannedStart", "plannedEnd", "actualStart",
                          "actualGroupStart", "currentDay"}
    assert not any(isinstance(v, (str, list, dict)) for v in block.values())


# ------------------------------------------------------------------ both shapes, merging
def test_the_documented_shape_is_read_too():
    s = parse.parse_rundown({"rundown": docs_rundown()})
    assert (s.offset_ms, s.offset_kind, s.expected_end_ms) == (-250_000, "unknown", 81_250_000)


def test_the_top_level_offset_block_wins_over_the_documented_keys():
    s = parse.parse_rundown({"rundown": docs_rundown(), "offset": offset_block(absolute=-1000, expectedRundownEnd=82_000_000)})
    assert (s.offset_ms, s.offset_kind, s.expected_end_ms) == (-1000, "absolute", 82_000_000)


def test_the_offset_mode_chooses_absolute_or_relative():
    both = {"rundown": rundown_block(), "offset": offset_block(absolute=-250_000, relative=-90_000)}
    assert parse.parse_rundown(both).offset_ms == -250_000
    rel = {"rundown": rundown_block(), "offset": offset_block(absolute=-250_000, relative=-90_000, mode="relative")}
    s = parse.parse_rundown(rel)
    assert (s.offset_ms, s.offset_kind) == (-90_000, "relative")


@pytest.mark.parametrize("mode", ["", "weird", None, 5, ["absolute"]])
def test_an_unknown_mode_is_never_guessed(mode):
    s = parse.parse_rundown({"rundown": rundown_block(), "offset": offset_block(mode=mode)})
    assert s.offset_ms is None and s.offset_kind is None
    assert s.expected_end_ms == 81_250_000    # that one does not depend on the mode


def test_a_bare_number_offset_is_read_like_the_documented_one():
    s = parse.parse_rundown({"rundown": rundown_block(), "offset": 12_000})
    assert (s.offset_ms, s.offset_kind) == (12_000, "unknown")


def test_partial_blocks_merge_key_by_key_and_other_messages_keep_everything():
    first = parse.parse_rundown({"rundown": rundown_block(), "offset": offset_block()})
    # only the index moved
    s = parse.parse_rundown({"rundown": {"selectedEventIndex": 4}}, first)
    assert s.selected_index == 4 and s.num_events == 12 and s.planned_end_ms == 81_000_000 and s.offset_ms == -250_000
    # only the offset moved
    s = parse.parse_rundown({"offset": {"absolute": 30_000}}, s)
    assert s.offset_ms == 30_000 and s.offset_mode == "absolute" and s.expected_end_ms == 81_250_000 and s.selected_index == 4
    # a clock-and-timer message changes nothing, and is the same object
    assert parse.parse_rundown({"clock": 1, "timer": {}}, s) is s
    assert parse.parse_rundown({"clock": 1}, None) is None
    assert parse.parse_rundown("nonsense", s) is s


def test_null_values_clear_and_a_null_block_clears_the_block():
    first = parse.parse_rundown({"rundown": rundown_block(), "offset": offset_block()})
    s = parse.parse_rundown({"rundown": {"selectedEventIndex": None, "actualStart": None}}, first)
    assert s.selected_index is None and s.actual_start_ms is None and s.num_events == 12
    s = parse.parse_rundown({"rundown": None}, first)
    assert s.num_events is None and s.planned_end_ms is None and s.offset_ms == -250_000   # the offset block stays
    s = parse.parse_rundown({"offset": None}, first)
    assert s.offset_ms is None and s.expected_end_ms is None and s.num_events == 12


def test_the_offset_keeps_ontimes_own_sign():
    ahead = parse.parse_rundown({"rundown": rundown_block(), "offset": offset_block(absolute=250_000)})
    behind = parse.parse_rundown({"rundown": rundown_block(), "offset": offset_block(absolute=-250_000)})
    msg = lambda s: rundown_message("Ontime", RundownReading(s, 1.0, "ok", "", 5))   # noqa: E731
    assert msg(ahead)["offset_ms"] == 250_000 and msg(behind)["offset_ms"] == -250_000   # the card decides ahead/behind


@pytest.mark.parametrize("block", [
    {"selectedEventIndex": 1.5}, {"selectedEventIndex": True}, {"selectedEventIndex": "3"}, {"selectedEventIndex": -1},
    {"numEvents": 10_001}, {"numEvents": -1}, {"numEvents": 3.0}, {"plannedStart": -1}, {"plannedEnd": 72 * 3_600_000 + 1},
    {"actualStart": "11:30"}, {"currentDay": 367}, {"currentDay": False}, {"offset": 72 * 3_600_000 + 1},
    {"expectedEnd": -5},
])
def test_a_bad_rundown_value_makes_the_whole_block_unreadable(block):
    assert parse.parse_rundown({"rundown": rundown_block(**block)}) is None


@pytest.mark.parametrize("block", [{"absolute": 1.5}, {"relative": "x"}, {"absolute": True},
                                   {"absolute": 72 * 3_600_000 + 1}, {"expectedRundownEnd": -1}])
def test_a_bad_offset_value_makes_the_block_unreadable(block):
    assert parse.parse_rundown({"rundown": rundown_block(), "offset": offset_block(**block)}) is None


@pytest.mark.parametrize("value", [[], "x", 5, True, 1.5])
def test_a_rundown_that_is_not_an_object_is_unreadable(value):
    assert parse.parse_rundown({"rundown": value}) is None


def test_the_edges_of_the_ranges_are_accepted():
    s = parse.parse_rundown({"rundown": rundown_block(selectedEventIndex=12, numEvents=12, plannedStart=0,
                                                     plannedEnd=72 * 3_600_000, currentDay=366),
                             "offset": offset_block(absolute=-72 * 3_600_000, relative=72 * 3_600_000)})
    assert s is not None and s.offset_ms == -72 * 3_600_000


def test_a_selected_index_past_the_count_is_unreadable_but_the_last_event_is_fine():
    assert parse.parse_rundown({"rundown": rundown_block(selectedEventIndex=13)}) is None
    assert parse.parse_rundown({"rundown": rundown_block(selectedEventIndex=12)}) is not None   # 1-based last, or 0-based "past the end"
    assert parse.parse_rundown({"rundown": rundown_block(selectedEventIndex=0, numEvents=0)}) is not None


def test_nothing_but_numbers_is_kept_from_the_blocks():
    block = rundown_block(title="Secret act", note="x", custom={"a": 1}, cue="1", id="abc", flag=True)
    s = parse.parse_rundown({"rundown": block, "offset": offset_block(extra="x")})
    text = json.dumps(rundown_message("Ontime", RundownReading(s, 1.0, "ok", "", 1)))
    for forbidden in ("Secret", "custom", "cue", "abc", "note", "title", "flag"):
        assert forbidden not in text


# ------------------------------------------------------------------ the public message
def test_message_shape_has_only_the_agreed_fields():
    s = parse.parse_rundown(ws_messages()[0])
    msg = rundown_message("Ontime", RundownReading(s, 12.5, "ok", "WebSocket", 58_994_023))
    assert set(msg) == PUBLIC_KEYS
    assert msg["position"] == {"index": 8, "total": 16} and msg["offset_mode"] == "absolute"
    assert msg["planned_start_ms"] == 41_400_000 and msg["expected_end_ms"] == 81_000_000 and msg["ontime_clock_ms"] == 58_994_023
    assert msg["current_day"] == 0 and msg["unreadable"] is False   # listed on purpose
    assert "WebSocket" not in json.dumps(msg)       # the detail text is not public


def test_offline_error_and_no_data_messages_carry_no_figures():
    for r in (RundownReading(None, 1.0, "offline", "Can't reach Ontime"), RundownReading(None, 1.0, "error", "x")):
        m = rundown_message("Ontime", r)
        assert set(m) == PUBLIC_KEYS and m["position"] is None and m["offset_ms"] is None and m["ontime_clock_ms"] is None
        assert "reach" not in json.dumps(m)
    nodata = rundown_message("Ontime", RundownReading(None, 1.0, "ok", "WebSocket", 5))
    assert nodata["status"] == "ok" and nodata["position"] is None


def test_no_event_selected_is_position_with_a_null_index():
    s = parse.parse_rundown({"rundown": rundown_block(selectedEventIndex=None, actualStart=None)})
    m = rundown_message("Ontime", RundownReading(s, 1.0, "ok", "", 5))
    assert m["position"] == {"index": None, "total": 12} and m["actual_start_ms"] is None


# ------------------------------------------------------------------ the source (fake Ontime)
async def test_source_reads_the_rundown_over_the_websocket_and_sends_nothing():
    async with FakeOntime() as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: src.latest_rundown().status == "ok" and src.latest_rundown().state is not None)
            first = src.latest_rundown()
            await until(lambda: src.latest_rundown().received_at > first.received_at)   # clock-only messages keep it alive
            r = src.latest_rundown()
            assert r.state.selected_index == 8 and r.state.num_events == 16 and r.state.offset_ms == 0
            assert r.clock_ms is not None
        finally:
            await src.stop()
        assert fake.received == []                       # not one data frame sent: read-only
        assert set(fake.paths) <= ALLOWED               # no new path


async def test_source_reads_the_rundown_by_polling_when_the_websocket_fails():
    from test_ontime_timer import FullPollOntime
    async with FullPollOntime(ws=False) as fake:
        src = fast_source(fake.url)
        await src.start()
        try:
            await until(lambda: src.latest_rundown().state is not None and src.transport == "polling")
            assert src.latest_rundown().state.num_events == 16
        finally:
            await src.stop()
        assert set(fake.paths) <= ALLOWED and fake.received == []


async def test_a_connection_that_is_lost_clears_the_rundown():
    async with FakeOntime() as fake:
        src = fast_source(fake.url)
        await src.start()
        await until(lambda: src.latest_rundown().state is not None)
    try:
        await until(lambda: src.latest_rundown().status == "offline")
        assert src.latest_rundown().state is None
    finally:
        await src.stop()


def test_an_unreadable_block_keeps_the_last_good_figures_and_marks_them():
    """SYNTHETIC messages. One bad block must not blank the card until Ontime resends."""
    src = fast_source("http://127.0.0.1:1")
    good = {"clock": 1000, "rundown": rundown_block(), "offset": offset_block()}
    assert src._ingest(good, "websocket")
    r = src.latest_rundown()
    assert r.status == "ok" and r.state.selected_index == 3 and r.unreadable is False
    assert src._ingest({"clock": 2000, "rundown": rundown_block(numEvents=1.5)}, "websocket")
    r = src.latest_rundown()
    assert r.status == "ok" and r.unreadable is True                     # marked, not blanked
    assert (r.state.selected_index, r.state.num_events, r.state.offset_ms) == (3, 12, -250_000)   # last good, nothing guessed
    assert rundown_message("Ontime", r)["unreadable"] is True
    assert src.latest().status == "ok" and src.latest().clock_ms == 2000   # the clock is untouched
    src._ingest({"clock": 3000}, "websocket")                            # a plain message keeps the mark, and stays alive
    r2 = src.latest_rundown()
    assert r2.unreadable is True and r2.state == r.state and r2.received_at >= r.received_at
    src._ingest({"clock": 4000, "rundown": {"selectedEventIndex": 5}}, "websocket")   # the next good block merges into the last good
    r3 = src.latest_rundown()
    assert r3.unreadable is False and (r3.state.selected_index, r3.state.num_events, r3.state.offset_ms) == (5, 12, -250_000)
    assert rundown_message("Ontime", r3)["unreadable"] is False


def test_a_first_block_that_is_unreadable_has_nothing_to_keep():
    src = fast_source("http://127.0.0.1:1")
    src._ingest({"clock": 1000, "rundown": rundown_block(numEvents=1.5)}, "websocket")
    r = src.latest_rundown()
    assert r.status == "error" and r.state is None
    src._ingest({"clock": 2000}, "websocket")                            # still an error after a plain message
    assert src.latest_rundown().status == "error"
    src._ingest({"clock": 3000, "rundown": rundown_block(selectedEventIndex=5)}, "websocket")
    assert src.latest_rundown().status == "ok" and src.latest_rundown().state.selected_index == 5


def test_a_message_before_any_rundown_is_ok_with_no_data():
    src = fast_source("http://127.0.0.1:1")
    src._ingest({"clock": 1000}, "websocket")
    r = src.latest_rundown()
    assert r.status == "ok" and r.state is None and r.clock_ms == 1000


# ------------------------------------------------------------------ one shared connection (refcount)
def set_cards(hub, *ids):
    for d in hub.config.dashboards:
        d.cards = [c for c in d.cards if c not in ("wall_clock", "ontime_timer", "ontime_rundown")]
    hub.config.dashboards[0].cards += list(ids)
    hub.save_config()


async def probe_hub(tmp_path, source="pc"):
    hub = Hub(tmp_path, emulate=True)
    hub.config.wall_clock.source = source
    probe = Probe(lambda: hub.config.site, cycle=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True, inner=probe))
    await hub.start()
    return hub, probe


async def test_nothing_runs_without_the_card(tmp_path):
    hub, probe = await probe_hub(tmp_path, "ontime")
    try:
        assert probe.starts == 0 and hub.snapshot()["ontime_rundown"] is None and hub.ontime_rundown.active is False
        assert all("ontime_rundown" not in d.cards for d in hub.config.dashboards)   # never added by default
    finally:
        await hub.stop()


async def test_the_three_cards_share_one_source_and_it_stops_with_the_last(tmp_path):
    hub, probe = await probe_hub(tmp_path, "ontime")
    try:
        set_cards(hub, "wall_clock", "ontime_timer", "ontime_rundown")
        await until(lambda: hub.ontime_rundown.active and hub.ontime_timer.active and hub.wall_clock.active)
        assert probe.starts == 1 and probe.stops == 0
        assert hub.integrations["ontime"].clock_source.holders == {"wall_clock", "ontime_timer", "ontime_rundown"}
        set_cards(hub, "ontime_rundown")
        await until(lambda: not hub.ontime_timer.active and not hub.wall_clock.active)
        assert probe.starts == 1 and probe.stops == 0 and "ontime" in hub.devices
        snap = hub.snapshot()["ontime_rundown"]
        assert snap["status"] == "ok" and set(snap) == PUBLIC_KEYS and snap["label"] == "Ontime"
        set_cards(hub)
        await until(lambda: not hub.ontime_rundown.active)
        assert probe.starts == 1 and probe.stops == 1 and "ontime" not in hub.devices
        assert hub.snapshot()["ontime_rundown"] is None
    finally:
        await hub.stop()


async def test_the_rundown_card_alone_with_the_real_source_opens_one_websocket(tmp_path):
    async with FakeOntime() as fake:
        hub = Hub(tmp_path)
        hub.config.wall_clock.ontime_url = fake.url
        hub.add_integration(OntimeIntegration(hub, inner=fast_source(fake.url)))
        await hub.start()
        try:
            set_cards(hub, "ontime_timer", "ontime_rundown")
            await until(lambda: (hub.snapshot()["ontime_rundown"] or {}).get("position") is not None)
            await asyncio.sleep(0.3)
            assert fake.paths.count("/ws") == 1 and set(fake.paths) <= ALLOWED
            snap = hub.snapshot()["ontime_rundown"]
            assert snap["position"] == {"index": 8, "total": 16} and fake.url not in json.dumps(snap)
        finally:
            await hub.stop()
        assert fake.received == []


async def test_unreachable_ontime_with_only_the_rundown_card_is_a_silent_alarm(tmp_path):
    hub = Hub(tmp_path)
    url = f"http://127.0.0.1:{free_port()}"
    hub.config.wall_clock = WallClockConfig(source="pc", ontime_url=url)
    hub.add_integration(OntimeIntegration(hub, inner=OntimeSource(lambda: hub.config.wall_clock.ontime_url,
                                                                  poll_every_s=0.05, backoff_min_s=0.05, backoff_max_s=0.1)))
    await hub.start()
    try:
        set_cards(hub, "ontime_rundown")
        await until(lambda: hub.devices.get("ontime") and hub.devices["ontime"].status == Status.MISSING, timeout=30)
        alarm = hub.alarms.to_list()[0]
        assert alarm["silent"] is True and hub.alarms.sounding is False and hub.alarms.max_level == 0
        await until(lambda: (hub.snapshot()["ontime_rundown"] or {}).get("status") == "offline")
        snap = hub.snapshot()["ontime_rundown"]
        assert snap["position"] is None and url not in json.dumps(snap)
    finally:
        await hub.stop()


# ------------------------------------------------------------------ the emulated story
def test_the_emulated_rundown_cycles_through_every_state():
    s = emulated_rundown_state(10)
    assert s.selected_index is None and s.actual_start_ms is None and s.num_events == 12            # not started
    on_time = emulated_rundown_state(35)
    assert abs(on_time.offset_ms) <= 30_000 and on_time.selected_index is not None
    amber = emulated_rundown_state(60)
    assert -300_000 < amber.offset_ms < -30_000
    peak = emulated_rundown_state(90)
    assert peak.offset_ms == -360_000 and peak.offset_ms < -300_000                                 # past the default 5 minute step
    ahead = emulated_rundown_state(154)
    assert ahead.offset_ms > 60_000
    none_loaded = emulated_rundown_state(180)
    assert none_loaded.num_events == 0 and none_loaded.selected_index is None                       # no rundown loaded
    done = emulated_rundown_state(210)
    assert done.selected_index is None and done.actual_start_ms is not None                         # finished
    assert emulated_rundown_state(10 + RUNDOWN_CYCLE_S) == s
    assert all(emulated_rundown_state(t / 2).planned_end_ms == 81_000_000
               for t in range(0, int(RUNDOWN_CYCLE_S * 2)) if emulated_rundown_state(t / 2).num_events)
    assert 41_400_000 <= emulated_rundown_clock(0) < emulated_rundown_clock(100) < 81_000_000


def test_every_emulated_state_parses_through_the_real_parser_limits():
    for t in range(0, int(RUNDOWN_CYCLE_S)):
        s = emulated_rundown_state(t)
        block = {"selectedEventIndex": s.selected_index, "numEvents": s.num_events, "plannedStart": s.planned_start_ms,
                 "plannedEnd": s.planned_end_ms, "actualStart": s.actual_start_ms, "currentDay": s.current_day}
        off = {"absolute": s.offset_absolute_ms, "relative": s.offset_relative_ms, "mode": s.offset_mode,
               "expectedRundownEnd": s.offset_expected_end_ms}
        assert parse.parse_rundown({"rundown": block, "offset": off}) == s


def test_emulated_rundown_goes_stale_then_offline_in_cycle_mode():
    now = [1_000_000.0]
    clock = EmulatedClock(lambda: Config().site, clock=lambda: now[0], cycle=True)
    t0 = now[0]
    now[0] = t0 + 20
    assert clock.latest_rundown().status == "ok" and clock.latest_rundown().received_at == now[0]
    now[0] = t0 + RUNDOWN_STALE[0] + 7
    r = clock.latest_rundown()
    assert r.status == "ok" and now[0] - r.received_at == pytest.approx(7)       # ageing
    now[0] = t0 + RUNDOWN_OFFLINE[0] + 1
    assert clock.latest_rundown().status == "offline" and clock.latest_rundown().state is None


async def test_emulate_mode_publishes_the_rundown_through_the_hub(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(OntimeIntegration(hub, emulate=True))
    await hub.start()
    try:
        set_cards(hub, "ontime_rundown")
        await until(lambda: hub.ontime_rundown.active)
        snap = hub.snapshot()["ontime_rundown"]
        assert snap["status"] == "ok" and snap["position"]["total"] == 12
        assert hub.devices["ontime"].status == Status.OK
        got = []
        hub.bus.subscribe("ontime_rundown", lambda _t, p: got.append(p))
        await until(lambda: len(got) >= 1, timeout=3)
        assert set(got[0]) == PUBLIC_KEYS
    finally:
        await hub.stop()


def test_the_emulate_demo_puts_the_rundown_on_the_wall_dashboard_only():
    cfg = Config()
    assert seed_emulate_demo(cfg) is True
    assert {d.slug: "ontime_rundown" in d.cards for d in cfg.dashboards} == {"foh": False, "phone": False, "wall": True}
    wall = cfg.dashboard("wall").cards
    assert wall.index("ontime_rundown") == 1 and wall.index("ontime_rundown") < wall.index("ontime_timer") < wall.index("wall_clock")   # high on the wall


# ------------------------------------------------------------------ config and web
def test_card_id_is_known_and_never_a_default():
    assert "ontime_rundown" in cards.KNOWN_CARDS and cards.strict_cards_error(["ontime_rundown"]) is None
    for layout in ("tablet", "phone", "wall"):
        assert "ontime_rundown" not in cards.default_cards(layout) and "ontime_rundown" not in cards.legacy_cards(layout)


def test_the_card_id_survives_a_reload_and_a_file_from_before_still_loads(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    set_cards(hub, "ontime_rundown")
    again = Hub(tmp_path, emulate=True)
    assert "ontime_rundown" in again.config.dashboards[0].cards
    assert Config.model_validate({"wall_clock": {"source": "pc"}}).dashboards is not None   # no rundown settings needed


def test_no_config_field_was_added():
    assert not hasattr(Config().site, "ontime_rundown") and not hasattr(Config(), "ontime_rundown")


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
    dashboards[0]["cards"] = [x for x in dashboards[0]["cards"] if x != "ontime_rundown"] + list(ids)
    r = c.put("/api/admin/dashboards", json=dashboards)
    assert r.status_code == 200, r.text


def test_the_card_round_trips_and_the_rundown_reaches_dashboards(client):
    admin(client)
    assert client.get("/api/snapshot").json()["ontime_rundown"] is None
    put_cards(client, "ontime_rundown")
    assert "ontime_rundown" in client.hub.config.dashboards[0].cards
    wait(lambda: client.hub.ontime_rundown.active)
    snap = client.get("/api/snapshot").json()["ontime_rundown"]
    assert set(snap) == PUBLIC_KEYS and snap["status"] == "ok"
    state = client.get("/api/admin/state").json()
    assert state["ontime_rundown"]["active"] is True and state["ontime_rundown"]["card_assigned"] is True
    assert "ontime_url" not in state["ontime_rundown"]
    slug = client.hub.config.dashboards[0].slug
    with client.websocket_connect("/ws?dashboard=" + slug) as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["ontime_rundown"]["status"] == "ok"
        for _ in range(80):
            msg = ws.receive_json()
            if msg["type"] == "ontime_rundown":
                assert set(msg) == PUBLIC_KEYS | {"type"}
                break
        else:
            pytest.fail("no ontime_rundown message on the live feed")
    put_cards(client)
    wait(lambda: not client.hub.ontime_rundown.active)
    assert client.get("/api/snapshot").json()["ontime_rundown"] is None


def test_the_snapshot_carries_nothing_private(client):
    admin(client)
    put_cards(client, "ontime_rundown")
    wait(lambda: client.hub.ontime_rundown.active)
    text = json.dumps(client.get("/api/snapshot").json()["ontime_rundown"])
    assert "127.0.0.1" not in text and "http" not in text
    for forbidden in ("title", "note", "cue", "custom", "colour", "url", "id\""):
        assert forbidden not in text


# ------------------------------------------------------------------ static
def test_page_loads_the_rundown_script_after_common_and_before_dashboard():
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    order = [html.index(f"/static/{n}.js") for n in ("common", "ontimerundown", "dashboard")]
    assert order == sorted(order)
    assert 'data-card="ontime_rundown"' in html and 'id="ontime-rundown-card"' in html


def test_rundown_script_uses_only_safe_dom_and_no_animation_loop():
    js = re.sub(r"//.*", "", (STATIC / "ontimerundown.js").read_text(encoding="utf-8"))
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "requestAnimationFrame", "<canvas",
                "fetch(", "XMLHttpRequest", "WebSocket", "eval(", "new Function"):
        assert bad not in js
    assert "http://" not in js and "https://" not in js
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    block = css[css.index("Ontime Rundown card"):]
    assert "animation" not in block.split("/* ---- dashboard: Barometer")[0]


def test_admin_names_the_card_and_the_address_hint():
    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    assert "ontime_rundown:" in js and "also used by the Ontime Rundown card" in js


def test_nothing_in_the_ontime_code_writes_or_adds_a_path():
    assert parse.HTTP_PATHS == ("/api/version", "/api/poll") and parse.WS_PATH == "/ws"
    client_src = (ROOT / "src" / "stagewatch" / "integrations" / "ontime" / "client.py").read_text(encoding="utf-8")
    assert ".send(" not in client_src and "\"POST\"" not in client_src and "\"PUT\"" not in client_src


def test_rundown_logic_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "ontime_rundown_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
