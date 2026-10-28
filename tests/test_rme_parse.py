"""RME unit parsing, unit conversion, soundness mapping, the public message and the marker rules.

The fixtures in tests/fixtures/rme/ are SYNTHETIC: hand-made, shaped like the first status frame and the
deltas in the owner's captures of a 12Mic (firmware 2.0.1), with placeholder names and serial numbers. No
real serial, address or label is in them. States never seen on real kit (Warning, a power supply loss) are
built by hand in the tests and labelled synthetic.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from stagewatch.integrations.rme import mapping, parse
from stagewatch.integrations.rme.parse import BadFrame, RmeState

FIX = Path(__file__).parent / "fixtures" / "rme"
SERIAL = "SN-PLACEHOLDER-0001"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def baseline() -> RmeState:
    st = RmeState()
    res = st.ingest(load("status_full.json"))
    assert res.kind == "baseline"
    return st


def status(data: dict) -> str:
    return json.dumps({"msg": "status", "data": data})


# ------------------------------------------------------------------ unit conversion
def test_kelvin_to_celsius_hand_calculated():
    """335.05 K - 273.15 = 61.90 C (the capture); 273.15 K = 0.0 C; 336.15 K = 63.0 C."""
    assert parse.kelvin_to_celsius(335.05) == 61.9
    assert parse.kelvin_to_celsius(273.15) == 0.0
    assert parse.kelvin_to_celsius(336.15) == 63.0
    assert parse.kelvin_to_celsius(300.05) == 26.9       # 300.05 - 273.15 = 26.90


def test_kelvin_range_edges_accepted_and_just_outside_refused():
    assert parse.kelvin_to_celsius(200.0) == pytest.approx(-73.15, abs=0.06)
    assert parse.kelvin_to_celsius(450.0) == pytest.approx(176.85, abs=0.06)
    assert parse.kelvin_to_celsius(199.9) is None and parse.kelvin_to_celsius(450.1) is None


@pytest.mark.parametrize("bad", [True, False, None, "335.05", float("nan"), float("inf"), [335.0], {"k": 1}])
def test_kelvin_refuses_anything_that_is_not_a_finite_number(bad):
    assert parse.kelvin_to_celsius(bad) is None


def test_baseline_view_values_from_the_synthetic_capture():
    v = parse.make_view(baseline().data)
    assert (v.name, v.model, v.firmware, v.serial) == ("Stage L", "12Mic", "2.0.1", SERIAL)
    assert v.temp_c == 61.9
    assert v.psu == (True, False) and v.redundancy is False
    assert (v.clock_source, v.clock_locked, v.clock_rate_hz, v.clock_state) == ("Word clock", True, 48000, "OK")
    assert (v.preset_active, v.preset_loaded, v.preset_changed) == (0, 2, True)
    assert (v.preset_active_name, v.preset_loaded_name) == ("", "Live 2")
    assert (v.avb_active, v.avb_total) == (2, 4)
    assert dict(v.avb_states) == {"OK": 2, "Not monitored": 2}


def test_baseline_keeps_only_whitelisted_subtrees():
    data = baseline().data
    text = json.dumps(data)
    for gone in ("osc", "group_name", "Group Placeholder", "gptp", "Placeholder Mic", "stream_id", "dest_addr", "analog", "levelmeters"):
        assert gone not in text
    assert set(data) == {"device", "clock", "input", "output"}
    assert set(data["input"]) == {"avb"}


def test_soundness_items_of_the_capture_with_causes():
    v = parse.make_view(baseline().data)
    got = {s.area: (s.word, s.level, s.cause) for s in v.sound}
    assert got["Preset"] == ("CHECK", 1, "Preset changed since it was loaded")
    assert got["Clock"] == ("OK", 0, "Clock master, not following another source")   # a cause can accompany Good
    assert got["Unit"] == ("OK", 0, "") and got["Power"][0] == "OK" and got["Health"][0] == "OK"
    assert mapping.overall_level(v) == 1                     # the Notice on the preset decides, not the cause on Good


# ------------------------------------------------------------------ deltas
def test_delta_files_merge_into_the_baseline():
    st = baseline()
    assert st.ingest(load("delta_temp.json")).changed is True
    assert parse.make_view(st.data).temp_c == 63.0
    assert st.ingest(load("delta_temp.json")).changed is False            # the same value again changes nothing
    assert st.ingest(load("delta_psu.json")).kind == "delta"
    assert parse.make_view(st.data).psu == (True, True)
    st.ingest(load("delta_clock.json"))
    v = parse.make_view(st.data)
    assert (v.clock_source, v.clock_locked, v.clock_state) == ("Internal", None, "CHECK")
    assert {s.area: s.cause for s in v.sound}["Clock"] == "Input not in sync"
    assert st.data["clock"]["sources"]["wordclock"]["sync"] is False and st.data["clock"]["sources"]["wordclock"]["rate"] == 48000
    st.ingest(load("delta_preset.json"))
    v = parse.make_view(st.data)
    assert (v.preset_active, v.preset_loaded, v.preset_changed, v.preset_loaded_name) == (3, 3, False, "Walk-in")


def test_a_delta_before_any_baseline_is_ignored():
    st = RmeState()
    res = st.ingest(load("delta_temp.json"))
    assert (res.kind, res.changed) == ("ignored", False) and st.data is None


def test_a_second_status_is_a_resync_and_other_messages_are_ignored():
    st = baseline()
    assert st.ingest(load("status_full.json")).kind == "resync"
    assert st.ingest('{"msg": "something_else", "data": {"device": {"psu": {"state": [false, false]}}}}').kind == "ignored"
    assert parse.make_view(st.data).psu == (True, False)
    with pytest.raises(BadFrame):
        st.ingest('{"msg": "status", "data": 5}')


def test_unknown_keys_are_ignored_and_wrong_shapes_leave_values_alone():
    st = baseline()
    st.ingest('{"device": {"psu": 5, "health": {"temperatures": "hot"}, "extra": {"x": 1}}, "weird": [1, 2]}')
    v = parse.make_view(st.data)
    assert v.temp_c == 61.9 and v.psu == (True, False)
    assert "weird" not in st.data and "extra" not in st.data["device"]


@pytest.mark.parametrize("k", [100.0, 199.9, 450.1, True, "hot", None, float(10 ** 6)])
def test_an_unusable_temperature_keeps_the_old_one(k):
    st = baseline()
    st.ingest(json.dumps({"device": {"health": {"temperatures": {"core": k}}}}))
    assert parse.make_view(st.data).temp_c == 61.9


@pytest.mark.parametrize("state", [[True], [True, False, True], [1, 0], ["on", "off"], [], "yes", [True, None]])
def test_psu_needs_exactly_two_booleans_else_unknown_and_no_entity(state):
    st = baseline()
    st.ingest(json.dumps({"device": {"psu": {"state": state}}}))
    v = parse.make_view(st.data)
    assert v.psu is None
    assert "psu_1" not in mapping.entity_values(v) and "psu_2" not in mapping.entity_values(v)
    assert mapping.unit_message("u", "U", v, phase="up", frame_age_s=0, text_age_s=0)["psu"] is None


def test_entity_values():
    v = parse.make_view(baseline().data)
    assert mapping.entity_values(v) == {"core_temp": 61.9, "psu_1": 1.0, "psu_2": 0.0}
    assert mapping.entity_values(None) == {}


def test_numbers_are_never_bools_and_bools_never_numbers():
    st = baseline()
    st.ingest(json.dumps({"device": {"psu": {"redundancy_alert": 1}, "preset": {"active": True, "loaded": False}},
                          "clock": {"current_sampling_rate": True, "sources": {"wordclock": {"sync": 1}}}}))
    v = parse.make_view(st.data)
    assert v.redundancy is None and v.preset_active is None and v.preset_loaded is None
    assert v.clock_rate_hz == 48000 or v.clock_rate_hz is None
    assert v.clock_locked is None


def test_clock_rate_outside_a_sample_rate_range_is_unknown():
    st = baseline()
    for rate in (1, 7999, 400001, 48.5e9):
        st.ingest(json.dumps({"clock": {"current_sampling_rate": rate, "sampling_rate": rate}}))
        assert parse.make_view(st.data).clock_rate_hz is None
    st.ingest(json.dumps({"clock": {"current_sampling_rate": 96000}}))
    assert parse.make_view(st.data).clock_rate_hz == 96000


# ------------------------------------------------------------------ hostile frames
def nested(containers: int) -> str:
    return '{"a":' * containers + "1" + "}" * containers


def test_depth_twelve_is_fine_thirteen_is_refused():
    parse.decode_text(nested(12))
    with pytest.raises(BadFrame) as exc:
        parse.decode_text(nested(13))
    assert exc.value.code == "depth"


def test_a_very_deep_frame_is_a_bad_frame_not_a_crash():
    with pytest.raises(BadFrame):
        parse.decode_text("[" * 100000 + "]" * 100000)


def test_node_cap_5000_and_5001():
    ok = json.dumps({f"k{i}": i for i in range(5000)})
    parse.decode_text(ok)
    with pytest.raises(BadFrame) as exc:
        parse.decode_text(json.dumps({f"k{i}": i for i in range(5001)}))
    assert exc.value.code == "nodes"


def test_the_osc_schema_tree_has_its_own_looser_budget_and_is_dropped():
    osc = {f"n{i}": None for i in range(6000)}
    st = RmeState()
    assert st.ingest(status({"osc": osc, "device": {"entity_name": "A"}})).kind == "baseline"
    assert "osc" not in st.data
    with pytest.raises(BadFrame):
        parse.decode_text(status({"osc": {f"n{i}": None for i in range(20001)}}))


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", "5", "null", '{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}',
                                  '{"a": 1e999}', '{"a": ', "{" * 10])
def test_malformed_and_non_finite_frames_are_bad(text):
    with pytest.raises(BadFrame):
        parse.decode_text(text)


def test_a_300_kib_frame_and_a_long_string_are_refused():
    big = json.dumps({"device": {"entity_name": "x" * (300 * 1024)}})
    with pytest.raises(BadFrame) as exc:
        parse.decode_text(big)
    assert exc.value.code == "too_large"
    parse.decode_text(json.dumps({"s": "x" * 200}))
    with pytest.raises(BadFrame) as exc:
        parse.decode_text(json.dumps({"s": "x" * 201}))
    assert exc.value.code == "string"
    with pytest.raises(BadFrame):
        parse.decode_text(json.dumps({"k" * 65: 1}))


def test_wildcard_levels_are_bounded_and_keys_checked():
    st = baseline()
    many = {f"s{i}": {"rate": 48000} for i in range(100)}
    st.ingest(json.dumps({"clock": {"sources": many}}))
    assert len(st.data["clock"]["sources"]) <= parse.MAX_WILDCARD_KEYS
    st.ingest(json.dumps({"clock": {"sources": {"bad key!": {"rate": 1}, "x" * 30: {"rate": 1}}}}))
    assert "bad key!" not in st.data["clock"]["sources"]


# ------------------------------------------------------------------ soundness table
@pytest.mark.parametrize("token,word,symbol,level,known", [
    ("Good", "OK", "tick", 0, True),
    ("Inactive", "Not monitored", "dash", 0, True),
    ("Notice", "CHECK", "triangle", 1, True),
    ("Notice in progress", "CHECK (in progress)", "triangle", 1, True),
    ("NoticeInProgress", "CHECK (in progress)", "triangle", 1, True),
    ("Notice (in progress)", "CHECK (in progress)", "triangle", 1, True),
    ("Warning", "WARNING", "cross", 2, True),
    ("No Routing", "Info", "circle", 0, True),
    ("NoRouting", "Info", "circle", 0, True),
    ("Issues with Input", "Info", "circle", 0, True),
    ("IssuesWithInput", "Info", "circle", 0, True),
])
def test_soundness_table_row_by_row(token, word, symbol, level, known):
    s = parse.classify(token)
    assert (s.word, s.symbol, s.level, s.known) == (word, symbol, level, known)


@pytest.mark.parametrize("token,shown", [
    ("Surprise", "Unrecognised: Surprise"),
    ("Banana!<b>", "Unrecognised: Bananab"),
    ("X" * 60, "Unrecognised: " + "X" * 24),
    ("", "Unrecognised: -"), ("!!!", "Unrecognised: -"), (None, "Unrecognised: -"), (7, "Unrecognised: -"), (True, "Unrecognised: -"),
])
def test_an_unknown_word_is_never_green_and_is_sanitised(token, shown):
    s = parse.classify(token)
    assert s.word == shown and s.symbol == "question" and s.level == 1 and s.known is False


def test_causes_known_unknown_and_hostile():
    assert parse.cause_text("ClockMasterNoSync") == "Clock master, not following another source"
    assert parse.cause_text("InputNoSync") == "Input not in sync"
    assert parse.cause_text("PresetChanged") == "Preset changed since it was loaded"
    assert parse.cause_text("FooBarBaz") == "Foo Bar Baz (RME's wording)"
    assert parse.cause_text("<b>Evil</b>") == "bEvilb (RME's wording)" or "<" not in parse.cause_text("<b>Evil</b>")
    assert parse.cause_text(None) == "" and parse.cause_text("") == "" and parse.cause_text(5) == ""
    assert len(parse.cause_text("A" * 500)) < 80


def test_a_warning_state_gives_an_alert_but_never_a_sound_level_above_two():
    """SYNTHETIC: Warning has not been seen in a capture."""
    st = baseline()
    st.ingest(json.dumps({"device": {"health": {"soundness": {"state": "Warning", "cause": "Hot"}}}}))
    v = parse.make_view(st.data)
    assert mapping.overall_level(v) == 2
    assert mapping.unit_status(v, "up", frozenset()) == "warning"
    assert max(s.level for s in v.sound) == 2


def test_info_and_not_monitored_never_change_the_level():
    """SYNTHETIC: No Routing and Issues with Input have not been seen in a capture."""
    st = RmeState()
    st.ingest(status({"device": {"soundness": {"state": "No Routing"}, "health": {"soundness": {"state": "Inactive"}}}}))
    v = parse.make_view(st.data)
    assert mapping.overall_level(v) == 0 and mapping.unit_status(v, "up", frozenset()) == "ok"


def test_an_unknown_word_makes_the_tile_check_never_ok():
    st = baseline()
    st.ingest(json.dumps({"device": {"preset": {"soundness": {"state": "Good"}}, "soundness": {"state": "Surprise"}}}))
    v = parse.make_view(st.data)
    assert mapping.unit_status(v, "up", frozenset()) == "check"
    assert [s for s in v.sound if s.area == "Unit"][0].word == "Unrecognised: Surprise"


def test_avb_stream_words_combine_both_paths():
    st = RmeState()
    st.ingest(status({"input": {"avb": {
        "1": {"pri": {"soundness": {"state": "Good"}}, "sec": {"soundness": {"state": "Notice"}}},
        "2": {"pri": {"soundness": {"state": "Inactive"}}, "sec": {"soundness": {"state": "Good"}}},
        "3": {"pri": {"soundness": {"state": "Inactive"}}, "sec": {"soundness": {"state": "Inactive"}}},
        "crf": {"pri": {"soundness": {"state": "Good"}}}}}}))
    v = parse.make_view(st.data)
    assert (v.avb_active, v.avb_total) == (1, 3)           # stream 1 has a Notice so it is not "active"
    assert [s.area for s in v.sound if s.area.startswith("AVB")] == ["AVB in 1"]


# ------------------------------------------------------------------ the public message
def test_public_message_exact_field_set_and_nothing_private():
    st = baseline()
    st.ingest(load("delta_clock.json"))
    v = parse.make_view(st.data)
    msg = mapping.unit_message("stage_l", "Stage L", v, phase="up", frame_age_s=0.2, text_age_s=1.0)
    assert set(msg) == {"id", "name", "model", "status", "status_word", "status_symbol", "level", "link_up",
                        "last_frame_age_s", "no_updates_min", "temp_c", "psu", "redundancy", "clock", "preset",
                        "avb", "soundness", "firmware"}
    assert set(msg) == set(mapping.UNIT_FIELDS)
    assert set(msg["clock"]) == {"source_label", "locked", "rate_hz", "state"}
    assert set(msg["preset"]) == {"active_no", "loaded_no", "active_name", "loaded_name", "changed"}
    assert set(msg["avb"]) == {"active", "total", "states"}
    assert all(set(s) == {"area", "word", "symbol", "level", "cause"} for s in msg["soundness"])
    assert msg["psu"] == [{"n": 1, "on": True}, {"n": 2, "on": False}]
    text = json.dumps(msg)
    for private in (SERIAL, "Group Placeholder", "00-00-00", "Placeholder Mic", "gptp", "stream", "serial", "192.", "dest_addr"):
        assert private not in text


def test_message_before_a_baseline_and_when_offline():
    start = mapping.unit_message("u", "U", None, phase="starting", frame_age_s=None, text_age_s=None)
    assert (start["status"], start["status_word"], start["temp_c"], start["psu"], start["link_up"]) == ("starting", "Starting", None, None, False)
    assert mapping.unit_message("u", "U", None, phase="waiting", frame_age_s=None, text_age_s=None)["status"] == "starting"
    assert mapping.unit_message("u", "U", None, phase="down", frame_age_s=None, text_age_s=None)["status"] == "offline"
    v = parse.make_view(baseline().data)
    off = mapping.unit_message("u", "U", v, phase="down", frame_age_s=200, text_age_s=None)
    assert (off["status"], off["status_word"], off["status_symbol"], off["link_up"]) == ("offline", "OFFLINE", "dash", False)
    assert off["temp_c"] == 61.9 and off["last_frame_age_s"] == 180      # frozen last values with their age


def test_status_words_and_symbols():
    assert mapping.STATUS_WORDS["ok"] == ("OK", "tick") and mapping.STATUS_WORDS["check"] == ("CHECK", "triangle")
    assert mapping.STATUS_WORDS["warning"] == ("WARNING", "cross") and mapping.STATUS_WORDS["offline"] == ("OFFLINE", "dash")


def test_age_buckets_and_the_no_updates_note():
    assert [mapping.bucket_age(a) for a in (None, 0, 9.9, 10, 59, 119, 120, 179, 400)] == [None, 0, 0, 10, 50, 110, 120, 120, 360]
    v = parse.make_view(baseline().data)
    for text_age, expected in ((10, None), (119.9, None), (120, 2), (300, 5)):
        assert mapping.unit_message("u", "U", v, phase="up", frame_age_s=0, text_age_s=text_age)["no_updates_min"] == expected
    assert mapping.unit_message("u", "U", v, phase="down", frame_age_s=0, text_age_s=500)["no_updates_min"] is None


# ------------------------------------------------------------------ psu levels
def view_with(psu, redundancy):
    st = baseline()
    st.ingest(json.dumps({"device": {"psu": {"state": psu, "redundancy_alert": redundancy}}}))
    return parse.make_view(st.data)


def test_psu_loss_levels_alert_with_redundancy_advisory_if_seen_live_otherwise_nothing():
    """SYNTHETIC. Off at baseline without redundancy (one supply fitted) is not a fault; seen going off
    live it is an Advisory; with the unit's redundancy alert on, any supply off is an Alert."""
    one_fitted = view_with([True, False], False)
    assert mapping.psu_level(one_fitted, frozenset()) == 0
    assert mapping.psu_level(one_fitted, frozenset({2})) == 1
    assert mapping.psu_level(view_with([True, False], True), frozenset()) == 2
    assert mapping.psu_level(view_with([True, True], True), frozenset()) == 0
    assert mapping.psu_level(view_with([False, True], False), frozenset({2})) == 0      # the supply seen lost is not the one that is off
    cond = mapping.conditions(view_with([True, False], True))
    assert cond["psu"] == (2, "power supply 2 off")


# ------------------------------------------------------------------ markers
def views():
    st = baseline()
    before = parse.make_view(st.data)
    return st, before


def events_after(st, before, delta):
    st.ingest(json.dumps(delta))
    return mapping.marker_events(before, parse.make_view(st.data))


def test_psu_clock_and_preset_markers_are_visible_with_the_wording_of_the_spec():
    st, v0 = views()
    ev = events_after(st, v0, {"device": {"psu": {"state": [True, True]}}})
    assert [(e.key, e.text, e.hidden) for e in ev] == [("psu2", "PSU 2 restored", False)]
    v1 = parse.make_view(st.data)
    ev = events_after(st, v1, {"device": {"psu": {"state": [True, False]}}})
    assert [(e.text, e.hidden) for e in ev] == [("PSU 2 lost", False)]
    ev = events_after(st, parse.make_view(st.data), json.loads(load("delta_clock.json")))
    by_key = {e.key: e for e in ev}
    assert by_key["clock_source"].text == "clock source Word clock to Internal" and by_key["clock_source"].hidden is False
    assert "clock_lock" not in by_key                              # the selected source is now Internal: no lock to lose
    assert by_key["sound:Clock"].hidden is True                       # a Notice flap is hidden
    ev = events_after(st, parse.make_view(st.data), json.loads(load("delta_preset.json")))
    assert [e for e in ev if e.key == "preset"][0].text == "preset loaded changed 2 to 3"


def test_clock_lock_markers_both_ways():
    st, v0 = views()
    ev = events_after(st, v0, {"clock": {"sources": {"wordclock": {"sync": False}}}})
    assert [e.text for e in ev] == ["clock lost lock"]
    ev = events_after(st, parse.make_view(st.data), {"clock": {"sources": {"wordclock": {"sync": True}}}})
    assert [e.text for e in ev] == ["clock locked"]


def test_a_warning_marker_is_visible_and_a_notice_one_is_hidden():
    """SYNTHETIC: Warning has not been seen in a capture."""
    st, v0 = views()
    ev = events_after(st, v0, {"device": {"health": {"soundness": {"state": "Warning"}}}})
    assert [(e.text, e.hidden) for e in ev] == [("Health: WARNING", False)]
    ev = events_after(st, parse.make_view(st.data), {"device": {"health": {"soundness": {"state": "Good"}}}})
    assert [(e.text, e.hidden) for e in ev] == [("Health: back to OK", False)]       # leaving a warning is visible too
    ev = events_after(st, parse.make_view(st.data), {"device": {"health": {"soundness": {"state": "Notice"}}}})
    assert ev[0].hidden is True


def test_an_unchanged_view_gives_no_markers_and_temperature_alone_none():
    st, v0 = views()
    assert mapping.marker_events(v0, v0) == []
    assert events_after(st, v0, {"device": {"health": {"temperatures": {"core": 340.0}}}}) == []


def test_lost_live_tracking():
    st, v0 = views()
    st.ingest(json.dumps({"device": {"psu": {"state": [True, True]}}}))
    v1 = parse.make_view(st.data)
    lost = mapping.lost_live_after(v0, v1, frozenset())
    assert lost == frozenset()                                   # restored: nothing lost
    st.ingest(json.dumps({"device": {"psu": {"state": [False, True]}}}))
    v2 = parse.make_view(st.data)
    assert mapping.lost_live_after(v1, v2, frozenset()) == frozenset({1})
    assert mapping.lost_live_after(v2, v1, frozenset({1})) == frozenset()


class Collector:
    def __init__(self):
        self.out = []

    def add(self, text, hidden):
        self.out.append((text, hidden))


def test_marker_policy_dedupes_one_key_per_ten_seconds():
    t = [100.0]
    c = Collector()
    p = mapping.MarkerPolicy(c.add, lambda: t[0])
    assert p.emit("psu2", "a") is True
    t[0] += 9.9
    assert p.emit("psu2", "a") is False                          # same marker inside 10 s
    assert p.emit("psu2", "b") is True                           # a different text is a different marker
    assert p.emit("clock", "c") is True                          # another key is fine
    t[0] += 0.2
    assert p.emit("psu2", "a") is True                           # 10.1 s later
    assert [x[0] for x in c.out] == ["a", "b", "c", "a"]


def test_a_psu_that_drops_and_returns_within_ten_seconds_gives_both_markers():
    t = [0.0]
    c = Collector()
    p = mapping.MarkerPolicy(c.add, lambda: t[0])
    assert p.emit("psu2", "PSU 2 lost") is True
    t[0] += 2.0
    assert p.emit("psu2", "PSU 2 restored", restore=True) is True
    t[0] += 2.0
    assert p.emit("psu2", "PSU 2 lost") is False                 # the same loss again inside 10 s
    t[0] += 1.0
    assert p.emit("psu2", "PSU 2 restored", restore=True) is True    # a return is never held back
    assert [x[0] for x in c.out] == ["PSU 2 lost", "PSU 2 restored", "PSU 2 restored"]


def test_events_that_put_things_back_are_flagged_as_restores():
    old = parse.make_view(baseline().data)
    st = baseline()
    st.ingest(json.dumps({"device": {"psu": {"state": [False, True]}}}))
    down = parse.make_view(st.data)
    assert [(e.text, e.restored) for e in mapping.marker_events(old, down) if "PSU 1" in e.text] == [("PSU 1 lost", False)]
    assert [(e.text, e.restored) for e in mapping.marker_events(down, old) if "PSU 1" in e.text] == [("PSU 1 restored", True)]


def test_marker_policy_rate_cap_then_one_overflow_marker_then_recovers():
    t = [0.0]
    c = Collector()
    p = mapping.MarkerPolicy(c.add, lambda: t[0])
    for i in range(30):
        p.emit(f"k{i}", f"m{i}")
        t[0] += 1.0
    texts = [x[0] for x in c.out]
    assert texts[:20] == [f"m{i}" for i in range(20)]
    assert texts[20] == mapping.OVERFLOW_TEXT and len(texts) == 21           # 20 markers then ONE notice
    t[0] += 60
    assert p.emit("again", "back") is True
    assert c.out[-1] == ("back", False)
    assert [x[0] for x in c.out[21:-1]] == [f"m{i}" for i in range(20, 30)]       # the final states held back


def test_marker_policy_marks_the_final_state_when_the_cap_window_ends():
    t = [0.0]
    c = Collector()
    notice = lambda: "RME Stage L: further RME changes not marked"
    p = mapping.MarkerPolicy(c.add, lambda: t[0], notice)
    for i in range(20):
        p.emit(f"k{i}", f"m{i}")
    p.emit("psu1", "PSU 1 lost")                                  # held back by the cap
    p.emit("psu1", "PSU 1 restored", restore=True)                # the final state of that key
    assert c.out[-1] == ("RME Stage L: further RME changes not marked", False)
    assert len(c.out) == 21
    t[0] += 30
    p.poll()
    assert len(c.out) == 21                                       # still inside the minute
    t[0] += 31
    p.poll()
    assert c.out[21:] == [("PSU 1 restored", False)]
    p.poll()
    assert len(c.out) == 22                                       # marked once


def test_a_held_back_state_equal_to_the_last_marker_is_not_marked_again():
    t = [0.0]
    c = Collector()
    p = mapping.MarkerPolicy(c.add, lambda: t[0])
    p.emit("psu1", "PSU 1 lost")
    for i in range(19):
        p.emit(f"k{i}", f"m{i}")
    p.emit("psu1", "PSU 1 restored", restore=True)                # held back
    p.emit("psu1", "PSU 1 lost")                                  # held back; final state = the last marker
    t[0] += 61
    p.poll()
    assert [x[0] for x in c.out].count("PSU 1 lost") == 1


def test_back_text():
    assert mapping.back_text(240) == "back after 4 min (readings missing meanwhile)"
    assert mapping.back_text(59.4) == "back after 59 s (readings missing meanwhile)"
    assert mapping.back_text(0.2) == "back after 1 s (readings missing meanwhile)"
    assert mapping.back_text(90) == "back after 2 min (readings missing meanwhile)"


def test_nothing_in_the_view_depends_on_a_nan():
    st = baseline()
    with pytest.raises(BadFrame):
        st.ingest('{"device": {"health": {"temperatures": {"core": NaN}}}}')
    assert math.isfinite(parse.make_view(st.data).temp_c)


def test_public_name_drops_serial_looking_words_and_the_serial_itself():
    assert parse.public_name("Stage L") == "Stage L"
    assert parse.public_name("Monitor World 2") == "Monitor World 2"
    assert parse.public_name("12Mic Stage L") == "12Mic Stage L"
    assert parse.public_name("12Mic 23051234") == "12Mic"                       # a long digit run
    assert parse.public_name("Stage SN12345AB") == "Stage"                      # looks like a serial
    assert parse.public_name("Rack ab-77c", "AB-77C") == "Rack"                 # holds the unit's serial
    assert parse.public_name("23051234") == ""
