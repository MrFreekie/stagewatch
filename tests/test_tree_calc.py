"""Power tree figures (integrations/shelly/treecalc.py), worked out by hand."""
from stagewatch.integrations.shelly.treecalc import (GAP_INCOMPLETE, GAP_MISMATCH, GAP_OK, TNode, gaps, rollup)


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
    assert gaps([N("m:u", "ups", w=900), N("m:l", up="m:u", w=1000)])["m:u"].state == GAP_MISMATCH   # cannot give out more than it takes


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


async def test_gap_and_totals_are_recorded_from_real_readings(hub):
    integ = await _tree_site(hub)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)         # watts 2,208 + 1,840 + 1,472 = 5,520
    e = hub.entities
    gap = e["power_site.treegap_m_distro"]
    assert gap.value == pytest.approx(1472) and gap.day["state"] == "ok"            # 5,520 - (2,208 + 1,840) = the third circuit
    assert gap.labels["calculated"] and "channel" not in gap.labels                   # labelled, and not a meter-card series
    assert e["power_site.treesrc_p_gen"].value == pytest.approx(5520) and e["power_site.treesrc_p_gen"].day["complete"] is True
    assert e["power_site.tree_site"].value == pytest.approx(5520)
    assert "power_site.tree_unassigned" not in e


async def test_a_circuit_not_reading_makes_the_gap_incomplete_and_the_total_has_no_value(hub):
    integ = await _tree_site(hub)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    bad = _em1([12.0, 10.0, 8.0])
    bad["em1:1"]["act_power"] = None
    integ.status(MAC, bad, T0 + 10)
    gap = hub.entities["power_site.treegap_m_distro"]
    assert gap.value is None and gap.day["state"] == "incomplete"
    assert hub.entities["power_site.tree_site"].value is None and hub.entities["power_site.tree_site"].day["complete"] is False


async def test_nothing_is_recorded_until_a_tree_is_set_up(hub):
    chans = [ShellyChannelConfig(key=f"em1_{i}") for i in range(3)]
    integ = await _site(hub, chans)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    assert not [k for k in hub.entities if k.startswith("power_site.tree")]
