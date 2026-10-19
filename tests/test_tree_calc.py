"""Power tree figures (integrations/shelly/treecalc.py), worked out by hand."""
from stagewatch.integrations.shelly.treecalc import (GAP_BATTERY, GAP_INCOMPLETE, GAP_MISMATCH, GAP_OK, TNode, gaps, node_slug, rollup, supply_key)


def N(ref, role="", kind="meter", up="", w=None):
    return TNode(ref, role, kind, up, w)


def test_gap_is_parent_minus_children():
    # 10,000 W in, 4,000 + 5,000 W out: 1,000 W unmonitored (tolerance 2 % of 19,000 + 15 W = 395 W)
    g = gaps([N("m:a", "isu", w=10000), N("m:b", "cdu", up="m:a", w=4000), N("m:c", "cdu", up="m:a", w=5000)])["m:a"]
    assert g.state == GAP_OK and g.watts == 1000 and g.children == 2


def test_small_negative_gap_is_rounding_not_a_mismatch():
    # 5,000 in, 5,100 out: -100 W, inside 2 % of 10,100 (202) + 10 W
    g = gaps([N("m:a", w=5000), N("m:b", up="m:a", w=5100)])["m:a"]
    assert g.state == GAP_OK and g.watts == 0.0


def test_negative_gap_beyond_accuracy_is_a_mismatch_with_no_number():
    # 5,000 in, 6,000 out: -1,000 W, beyond 2 % of 11,000 (220) + 10 W
    g = gaps([N("m:a", w=5000), N("m:b", up="m:a", w=6000)])["m:a"]
    assert g.state == GAP_MISMATCH and g.watts is None and "Upstream" in g.reason


def test_unmetered_or_silent_child_makes_the_gap_incomplete():
    for kid in (N("p:x", kind="point", up="m:a"), N("m:b", up="m:a", w=None)):
        g = gaps([N("m:a", w=5000), N("m:c", up="m:a", w=1000), kid])["m:a"]
        assert g.state == GAP_INCOMPLETE and g.watts is None


def test_silent_parent_is_incomplete_and_leaves_have_no_gap():
    out = gaps([N("m:a", w=None), N("m:b", up="m:a", w=10)])
    assert out["m:a"].state == GAP_INCOMPLETE and "m:b" not in out
    assert "p:u" not in gaps([N("p:u", kind="point"), N("m:b", up="p:u", w=10)])   # an unmetered node has no gap


def test_two_roots_totals_and_site_total():
    nodes = [N("p:g1", "source", "point"), N("m:a", "isu", up="p:g1", w=3000), N("m:b", "isu", up="p:g1", w=2000),
             N("p:g2", "source", "point"), N("m:c", "isu", up="p:g2", w=700)]
    r = rollup(nodes)
    assert r.sources["p:g1"].watts == 5000 and r.sources["p:g1"].complete
    assert r.sources["p:g2"].watts == 700
    assert r.site.watts == 5700 and r.site.complete and r.unassigned is None


def test_one_incomplete_source_makes_the_site_incomplete():
    nodes = [N("p:g1", "source", "point"), N("m:a", up="p:g1", w=3000),
             N("p:g2", "source", "point"), N("m:c", up="p:g2", w=None)]
    r = rollup(nodes)
    assert not r.sources["p:g2"].complete and r.sources["p:g2"].watts is None
    assert r.site.watts == 3000 and not r.site.complete      # the read part, marked incomplete


def test_metered_node_counts_once_not_with_its_children():
    nodes = [N("p:g", "source", "point"), N("m:a", "isu", up="p:g", w=1000), N("m:b", "cdu", up="m:a", w=400)]
    assert rollup(nodes).sources["p:g"].watts == 1000


def test_unmetered_leaf_leaves_the_supply_incomplete():
    nodes = [N("p:g", "source", "point"), N("p:f", "fdu", "point", "p:g"), N("m:a", up="p:f", w=300), N("p:e", "equipment", "point", "p:f")]
    t = rollup(nodes).sources["p:g"]
    assert t.watts == 300 and not t.complete and t.unmonitored == 1


def test_unassigned_group_keeps_loose_meters_in_the_site_total():
    nodes = [N("p:g", "source", "point"), N("m:a", up="p:g", w=1000), N("m:z", w=250),
             N("c:z:0", kind="circuit")]       # a bare circuit with no role is not part of the tree
    r = rollup(nodes)
    assert r.unassigned.watts == 250 and r.unassigned_refs == ["m:z"]
    assert r.site.watts == 1250


def test_ups_loss_is_input_minus_output_and_needs_both():
    ups = N("m:u", "ups", w=1100)
    assert gaps([ups, N("m:l", up="m:u", w=1000)])["m:u"].watts == 100
    assert gaps([ups, N("m:l", up="m:u", w=None)])["m:u"].state == GAP_INCOMPLETE
    # gives out more than it takes in: it may be on battery, which is not a wiring fault
    g = gaps([N("m:u", "ups", w=2), N("m:l", up="m:u", w=800)])["m:u"]
    assert g.state == GAP_BATTERY and g.watts is None and "Upstream" not in g.reason and "battery" in g.reason


def test_unmetered_ups_leaves_the_total_incomplete():
    nodes = [N("p:g", "source", "point"), N("p:u", "ups", "point", "p:g"), N("m:l", up="p:u", w=800)]
    t = rollup(nodes).sources["p:g"]
    assert t.watts == 800 and not t.complete


# ------------------------------------------------------------------ through the integration (recorded, with history)
import pytest                                              # noqa: E402
from stagewatch.core.config import PowerPointConfig, ShellyChannelConfig   # noqa: E402
from stagewatch.core.hub import Hub                        # noqa: E402
from test_shelly_calc import MAC, T0, _em1, _site          # noqa: E402


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    yield h
    h.recorder.close()


async def _tree_site(hub):
    chans = [ShellyChannelConfig(key=f"em1_{i}", role="equipment", unit_no=i + 1, upstream="m:distro") for i in range(2)]
    chans.append(ShellyChannelConfig(key="em1_2"))
    integ = await _site(hub, chans)
    dev = hub.config.shelly_devices[0]
    dev.role, dev.unit_no, dev.upstream = "isu", 1, "p:gen"
    hub.config.power_points = [PowerPointConfig(id="gen", name="Main generator", role="source", unit_no=1)]
    return integ


async def test_totals_are_recorded_from_real_readings_and_a_meter_has_no_gap_against_its_own_circuits(hub):
    integ = await _tree_site(hub)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)         # watts 2,208 + 1,840 + 1,472 = 5,520
    e = hub.entities
    src = e[f"power_site.treesrc_{node_slug('p:gen')}"]
    assert src.value == pytest.approx(5520) and src.day["complete"] is True and src.labels["calculated"]
    assert "channel" not in src.labels                                               # not a meter-card series
    assert e["power_site.tree_site"].value == pytest.approx(5520)
    assert "power_site.tree_unassigned" not in e
    assert not [k for k in e if "treegap" in k]          # 5,520 minus its own circuits is 0 by construction: no figure


async def test_an_incomplete_total_is_never_recorded_as_a_value(hub):
    integ = await _tree_site(hub)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    bad = _em1([12.0, 10.0, 8.0])
    bad["em1:1"]["act_power"] = None
    integ.status(MAC, bad, T0 + 10)
    site = hub.entities["power_site.tree_site"]
    assert site.value is None and site.day["complete"] is False
    assert [v for t_, v in hub.recorder.raw_states("power_site.tree_site", T0 - 5, T0 + 20) if v is not None] == [pytest.approx(5520)]


async def test_tree_entities_are_renamed_and_removed_with_the_tree(hub):
    integ = await _tree_site(hub)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    eid = f"power_site.treesrc_{node_slug('p:gen')}"
    assert hub.entities[eid].name.startswith("SRC01")
    hub.config.power_points[0].unit_no = 2
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0 + 3)
    assert hub.entities[eid].name.startswith("SRC02")
    hub.config.power_points = []
    dev = hub.config.shelly_devices[0]
    dev.role, dev.unit_no, dev.upstream = "", None, ""
    for ch in dev.channels:
        ch.role, ch.unit_no, ch.upstream = "", None, ""
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0 + 6)
    assert not [k for k in hub.entities if k.startswith("power_site.tree")]


async def test_the_tree_is_recorded_at_most_every_heartbeat_when_nothing_moves(hub):
    integ = await _tree_site(hub)
    rows = []
    real = hub.update_state

    def counting(eid, value, ts, *a, **k):
        if eid.startswith("power_site.tree"):
            rows.append(eid)
        return real(eid, value, ts, *a, **k)
    hub.update_state = counting
    for i in range(30):                                     # a minute of 2 s polls, a hair of jitter (well under 5 W)
        integ.status(MAC, _em1([12.0 + (i % 2) * 0.001, 10.0, 8.0]), T0 + 2 * i)
    # two tree entities (the supply and the site), at most a row every 10 s each, plus the first
    assert 0 < len(rows) <= 2 * (60 // 10 + 2)


async def test_line_currents_are_not_added_across_supplies(hub):
    chans = [ShellyChannelConfig(key=f"em1_{i}", phase=p, in_total=True) for i, p in enumerate(("L1", "L2", "L3"))]
    integ = await _site(hub, chans)
    dev = hub.config.shelly_devices[0]
    dev.role, dev.unit_no, dev.upstream = "isu", 1, "p:gen"
    chans[2].role, chans[2].unit_no, chans[2].upstream = "equipment", 1, "p:gen2"      # the third line is on another supply
    hub.config.power_points = [PowerPointConfig(id="gen", name="A", role="source", unit_no=1),
                               PowerPointConfig(id="gen2", name="B", role="source", unit_no=2)]
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    e = hub.entities
    assert all(f"power_site.line_{i}_current" not in e or e[f"power_site.line_{i}_current"].value is None for i in (1, 2, 3))
    assert "power_site.unbalance" not in e or e["power_site.unbalance"].value is None
    assert e[f"power_site.supply_{node_slug('p:gen')}_line_1_current"].value == 12.0        # per supply instead
    assert e[f"power_site.supply_{node_slug('p:gen2')}_line_3_current"].value == 8.0
    assert e["shelly_neutral.neutral_current"].value is None                                 # never across supplies


async def test_generator_is_sized_from_three_times_the_busiest_line(hub):
    chans = [ShellyChannelConfig(key=f"em1_{i}", phase=p, in_total=True) for i, p in enumerate(("L1", "L2", "L3"))]
    integ = await _site(hub, chans)
    for i in range(0, 320, 10):
        integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0 + i)            # VA per line: 2,760, 2,300, 1,840; total 6,900
    d = hub.entities["power_site.generator"].day
    assert d["basis"] == "line" and d["peak_kva"] == pytest.approx(3 * 2.76)             # 8.28 kVA, not 6.9
    assert d["min_kva"] == pytest.approx(3 * 2.76 / 0.8)                                  # 10.35 kVA


def test_generator_suggestion_line_basis_by_hand():
    from stagewatch.integrations.shelly import calc
    s = calc.generator_suggestion(16000, 80, 0.8, line_peak_va=12000)      # 12/2/2 kVA: 3 x 12 / 0.8 = 45 kVA
    assert s["min_kva"] == pytest.approx(45) and s["basis"] == "line" and s["next_kva"] == 50
    even = calc.generator_suggestion(18000, 80, 0.8, line_peak_va=6000)    # 6/6/6: 3 x 6 = 18, not more than the total
    assert even["basis"] == "total" and even["min_kva"] == pytest.approx(22.5)
    assert calc.generator_suggestion(16000, 80, 0.8)["min_kva"] == pytest.approx(20)   # lines unknown: the total


def test_neutral_does_not_use_phasors_with_unsigned_calculated_reactive_power():
    import math
    from stagewatch.integrations.shelly import neutral as nt
    C = nt.Circuit
    deg = math.radians
    signed = [C("L1", 10, 2000, math.cos(deg(30)), 1000), C("L2", 6, 1300, 1.0, 0.0), C("L3", 8, 1700, math.cos(deg(20)), -600)]
    r = nt.neutral_current(signed)
    assert r.method == nt.METHOD_PHASORS and r.amps == pytest.approx(5.08, abs=0.01)    # 10 A lagging 30, 6 A at 0, 8 A leading 20
    calc_only = [C(c.line, c.amps, c.power, c.pf, abs(c.reactive), reactive_calc=True) for c in signed]
    r2 = nt.neutral_current(calc_only)
    assert r2.method == nt.METHOD_ESTIMATE and r2.amps == pytest.approx(math.sqrt(100 + 36 + 64 - 60 - 48 - 80), abs=1e-3)   # 3.46 A
    assert nt.neutral_current([C("L1", 5, supply="a"), C("L2", 5, supply="b"), C("L3", 5, supply="a")]).amps is None


def test_nothing_is_counted_twice_through_the_unassigned_group():
    # A: the meter has no role, but one of its circuits sits under the supply. 1,000 W under S, the other 500 W unassigned.
    nodes = [N("p:S", "source", "point"), N("m:M2", w=1500), N("c:M2:0", "equipment", "circuit", "p:S", 1000), N("c:M2:1", "", "circuit", "", 500)]
    r = rollup(nodes)
    assert (r.sources["p:S"].watts, r.unassigned.watts, r.site.watts) == (1000, 500, 1500)
    # B: a circuit with a role but no upstream, on a meter that is already under the supply: counted once
    nodes = [N("p:S", "source", "point"), N("m:M", "isu", "meter", "p:S", 3000), N("c:M:0", "fdu", "circuit", "", 1000)]
    assert rollup(nodes).site.watts == 3000
    # the same circuit and its loose meter: counted once, through the meter
    nodes = [N("m:M", "", "meter", "", 1500), N("c:M:0", "fdu", "circuit", "", 1000)]
    assert rollup(nodes).site.watts == 1500


def test_a_meter_against_its_own_circuits_has_no_gap_and_a_ups_shows_its_efficiency():
    assert "m:M" not in gaps([N("m:M", "isu", w=1500), N("c:M:0", "cdu", "circuit", "m:M", 1000), N("c:M:1", "cdu", "circuit", "m:M", 500)])
    g = gaps([N("m:u", "ups", w=1000), N("m:l", up="m:u", w=900)])["m:u"]
    assert g.watts == 100 and g.efficiency == pytest.approx(90)
    assert gaps([N("m:u", "ups", w=50), N("m:l", up="m:u", w=45)])["m:u"].efficiency is None      # too little load for a figure


def test_entity_id_pieces_never_collide_and_match_the_browser():
    import zlib
    assert node_slug("c:a:b") != node_slug("c:a_b") and node_slug("m:abc") == "m_abc_f273c4f4"
    assert node_slug("c:dev:ch_0") == f"c_dev_ch_0_{zlib.crc32(b'c:dev:ch_0'):08x}"


def test_the_supply_of_a_node_stops_at_a_ups():
    info = {"p:S": ("source", ""), "m:a": ("isu", "p:S"), "m:u": ("ups", "m:a"), "m:l": ("equipment", "m:u"), "m:x": ("equipment", "")}
    assert supply_key("m:a", info) == "p:S" and supply_key("m:l", info) == "m:u" and supply_key("m:x", info) == ""


async def test_nothing_is_recorded_until_a_tree_is_set_up(hub):
    chans = [ShellyChannelConfig(key=f"em1_{i}") for i in range(3)]
    integ = await _site(hub, chans)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    assert not [k for k in hub.entities if k.startswith("power_site.tree")]
