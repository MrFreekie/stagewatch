"""Power distribution tree, step 1: roles, validation, config and the admin/public endpoints. Hand-worked cases."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from stagewatch.core import power_tree as pt
from stagewatch.core.config import Config, ConfigStore, PowerPointConfig, ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

FIX = Path(__file__).resolve().parent / "fixtures" / "v2" / "config.yaml"


def N(ref, role="", no=None, up="", kind="point"):
    return pt.Node(ref, kind, role, no, up, ref)


def codes(nodes):
    return sorted((p.code, p.ref) for p in pt.validate(nodes))


# ------------------------------------------------------------------ names
def test_display_names():
    assert pt.display_name("isu", 1) == "ISU01"
    assert pt.display_name("cdu", 12) == "CDU12"
    assert pt.display_name("fdu", 99) == "FDU99"
    assert pt.display_name("ups", None) == "UPS"
    assert pt.display_name("", 3) == ""
    assert pt.display_name("isu", 100) == "ISU"
    assert pt.role_label("isu") == "Intake switch unit (ISU)"
    assert pt.role_label("") == ""
    assert pt.role_label("ups") == "UPS"


def test_roles_are_stable_keys():
    assert pt.ROLES == ("source", "isu", "cdu", "fdu", "equipment", "ups", "other")
    assert pt.UPS_TYPES == ("online", "line_interactive", "standby", "unknown")


# ------------------------------------------------------------------ validator
def test_valid_forest_with_two_sources():
    nodes = [N("p:gen1", "source", 1), N("p:gen2", "source", 2), N("p:isu1", "isu", 1, "p:gen1"),
             N("p:isu2", "isu", 2, "p:gen2"), N("m:a", "cdu", 1, "p:isu1", "meter"), N("m:b", "fdu", 1, "m:a", "meter")]
    assert pt.validate(nodes) == []


def test_self_parent():
    assert codes([N("p:a", "isu", 1, "p:a")]) == [("self_parent", "p:a")]


def test_unknown_upstream():
    assert codes([N("p:a", "isu", 1, "p:zzz")]) == [("unknown_upstream", "p:a")]


def test_cycle_flags_every_member_and_not_the_tail():
    nodes = [N("p:a", "other", None, "p:b"), N("p:b", "other", None, "p:c"), N("p:c", "other", None, "p:a"),
             N("p:t", "other", None, "p:a")]
    assert codes(nodes) == [("cycle", "p:a"), ("cycle", "p:b"), ("cycle", "p:c")]


def test_role_order():
    # FDU over a CDU, same level, equipment over anything ranked
    assert codes([N("p:f", "fdu", 1), N("p:c", "cdu", 1, "p:f")]) == [("role_order", "p:c")]
    assert codes([N("p:c1", "cdu", 1), N("p:c2", "cdu", 2, "p:c1")]) == [("role_order", "p:c2")]
    assert codes([N("p:e", "equipment", 1), N("p:i", "isu", 1, "p:e")]) == [("role_order", "p:i")]


def test_levels_may_be_skipped():
    assert pt.validate([N("p:s", "source", 1), N("p:f", "fdu", 1, "p:s")]) == []


def test_ups_sits_between_any_levels_and_other_is_free():
    nodes = [N("p:s", "source", 1), N("p:c", "cdu", 1, "p:s"), N("p:u", "ups", 1, "p:c"),
             N("p:e", "equipment", 1, "p:u"), N("p:o", "other", None, "p:e")]
    assert pt.validate(nodes) == []


def test_source_has_no_upstream():
    assert codes([N("p:s", "source", 1), N("p:t", "source", 2, "p:s")]) == [("source_has_upstream", "p:t")]


def test_orphans_are_valid_but_get_advice():
    nodes = [N("m:a", "cdu", 1, "", "meter"), N("m:b", "", None, "", "meter"), N("p:g", "source", 1)]
    assert pt.validate(nodes) == []
    assert sorted((p.code, p.ref) for p in pt.warnings(nodes)) == [("no_role", "m:b"), ("no_upstream", "m:a")]


def test_numbers_unique_per_role_in_range_and_need_a_role():
    nodes = [N("p:a", "isu", 1), N("p:b", "isu", 1), N("p:c", "cdu", 1)]
    assert codes(nodes) == [("duplicate_number", "p:b")]
    assert codes([N("p:a", "", 5)]) == [("number_without_role", "p:a")]
    assert codes([N("p:a", "isu", 0)]) == [("number_range", "p:a")]
    assert codes([N("p:a", "isu", 100)]) == [("number_range", "p:a")]


def test_messages_use_display_names_not_raw_refs():
    msg = pt.validate([N("p:f", "fdu", 3), N("p:c", "cdu", 2, "p:f")])[0].message
    assert "CDU02" in msg and "FDU03" in msg and "p:" not in msg


def test_tidy_on_load_cuts_missing_self_and_one_loop_edge():
    nodes = [N("p:a", "other", None, "p:b"), N("p:b", "other", None, "p:a"), N("p:c", "other", None, "p:c"),
             N("p:d", "other", None, "p:gone"), N("p:e", "other", None, "p:a")]
    cut = pt.tidy_on_load(nodes)
    assert set(cut) == {"p:a", "p:c", "p:d"}      # loop cut once, at the smallest ref


# ------------------------------------------------------------------ config
def tree_cfg():
    c = Config()
    c.power_points = [PowerPointConfig(id="gen1", name="FOH generator", role="source", unit_no=1, rating_kva=100),
                      PowerPointConfig(id="isu1", name="Intake", role="isu", unit_no=1, upstream="p:gen1", rating_a=125,
                                       monitored=False, allowance={"unit": "kw", "value": 4})]
    c.shelly_devices = [ShellyDeviceConfig(id="m1", host="192.168.1.50", mac="AABBCCDDEEFF", name="Distro A",
                                           role="cdu", unit_no=2, upstream="p:isu1",
                                           channels=[ShellyChannelConfig(key="pm1_0", role="fdu", unit_no=3, upstream="m:m1")])]
    return c


def test_round_trip(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.config = tree_cfg()
    store.save()
    back = ConfigStore(tmp_path / "config.yaml").load()
    assert [(p.id, p.role, p.unit_no, p.upstream, p.rating_kva) for p in back.power_points] == \
        [("gen1", "source", 1, "", 100.0), ("isu1", "isu", 1, "p:gen1", None)]
    assert back.power_points[1].allowance.unit == "kw" and back.power_points[1].allowance.value == 4 and not back.power_points[1].monitored
    d = back.shelly_devices[0]
    assert (d.role, d.unit_no, d.upstream) == ("cdu", 2, "p:isu1")
    assert (d.channels[0].role, d.channels[0].unit_no, d.channels[0].upstream) == ("fdu", 3, "m:m1")
    assert back.schema_version == tree_cfg().schema_version == 2      # additive: no bump


def test_old_release_file_loads_with_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(FIX.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert cfg.power_points == []
    d = cfg.shelly_devices[0]
    assert (d.role, d.unit_no, d.upstream) == ("", None, "") and d.channels[0].role == ""
    assert d.rating_a == 32 and d.channels[0].label == "Amp rack"      # nothing else changed


def test_older_build_would_ignore_the_new_keys():
    # an older build's models use extra="ignore": the new keys are simply skipped (and dropped on its next save)
    from stagewatch.core.config import _Model
    assert _Model.model_config["extra"] == "ignore"


def test_unknown_role_number_and_ups_type_are_softened_not_fatal():
    d = ShellyDeviceConfig(id="m1", host="192.168.1.50", mac="AABBCCDDEEFF", role="wind", unit_no=500, upstream=7)
    assert (d.role, d.unit_no, d.upstream) == ("", None, "")
    p = PowerPointConfig(id="u", name="UPS", role="ups", ups_type="quantum")
    assert p.ups_type == "unknown"


def test_load_clears_bad_upstreams_with_a_count_only(tmp_path, caplog):
    c = tree_cfg()
    c.power_points[1].upstream = "p:vanished"
    c.shelly_devices[0].upstream = "m:m1"      # itself
    p = tmp_path / "config.yaml"
    store = ConfigStore(p)
    store.config = c
    store.save()
    with caplog.at_level(logging.WARNING):
        back = ConfigStore(p).load()
    assert back.power_points[1].upstream == "" and back.shelly_devices[0].upstream == ""
    assert back.shelly_devices[0].channels[0].upstream == "m:m1"      # valid: still kept
    assert "vanished" not in caplog.text and "2 upstream links cleared" in caplog.text


def test_salvage_keeps_valid_points_and_drops_only_bad(tmp_path):
    raw = yaml.safe_load(yaml.safe_dump(tree_cfg().model_dump(mode="json")))
    raw["power_points"].append({"id": "", "name": "no id"})
    raw["power_points"].append("junk")
    raw["dashboards"] = "broken"
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert [x.id for x in cfg.power_points] == ["gen1", "isu1"]
    assert cfg.power_points[1].upstream == "p:gen1" and cfg.shelly_devices[0].upstream == "p:isu1"


def test_duplicate_and_surplus_points_are_dropped():
    c = Config(power_points=[{"id": "a", "name": "1"}, {"id": "a", "name": "2"}] + [{"id": f"x{i}", "name": "n"} for i in range(60)])
    assert len(c.power_points) == pt.MAX_POINTS and c.power_points[0].name == "1"


def test_delete_point_reparent_detach_and_ask():
    c = tree_cfg()
    with pytest.raises(pt.TreeError):
        pt.delete_point(c, "isu1", None)
    pt.delete_point(c, "isu1", "reparent")      # CDU02 now fed straight from the generator: allowed
    assert c.shelly_devices[0].upstream == "p:gen1" and [p.id for p in c.power_points] == ["gen1"]
    c = tree_cfg()
    pt.delete_point(c, "isu1", "detach")
    assert c.shelly_devices[0].upstream == ""
    with pytest.raises(LookupError):
        pt.delete_point(c, "nope", None)


def test_reparent_refused_when_it_would_break_the_order():
    c = tree_cfg()
    c.power_points[0].role = "equipment"      # contrived: new parent ranks below the children
    c.power_points[1].upstream = "p:gen1"
    with pytest.raises(pt.TreeError):
        pt.delete_point(c, "isu1", "reparent")
    assert [p.id for p in c.power_points] == ["gen1", "isu1"] and c.shelly_devices[0].upstream == "p:isu1"


def test_public_structure_has_no_addresses_or_allowances():
    s = pt.public_structure(tree_cfg())
    text = str(s)
    for secret in ("192.168.1.50", "AABBCCDDEEFF", "allowance", "monitored"):
        assert secret not in text
    by = {n["ref"]: n for n in s["nodes"]}
    assert by["m:m1"]["display"] == "CDU02" and by["m:m1"]["upstream"] == "p:isu1"
    assert by["p:isu1"]["rating_a"] == 125 and by["p:gen1"]["rating_kva"] == 100
    assert by["c:m1:pm1_0"]["display"] == "FDU03"


# ------------------------------------------------------------------ endpoints
@pytest.fixture
def client(tmp_path):
    h = Hub(tmp_path)
    h.config.shelly_devices = [ShellyDeviceConfig(id="m1", host="192.168.1.50", mac="AABBCCDDEEFF", name="Distro A"),
                               ShellyDeviceConfig(id="m2", host="192.168.1.51", mac="AABBCCDDEE00", name="Distro B")]
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


GOOD = {"points": [{"id": "gen1", "name": "FOH generator", "role": "source", "unit_no": 1, "rating_kva": 100},
                   {"id": "isu1", "name": "Intake", "role": "isu", "unit_no": 1, "upstream": "p:gen1",
                    "monitored": False, "allowance": {"unit": "kw", "value": 3}}],
        "meters": [{"id": "m1", "role": "cdu", "unit_no": 1, "upstream": "p:isu1",
                    "channels": [{"key": "pm1_0", "role": "fdu", "unit_no": 1, "upstream": "m:m1"}]}]}


def test_routes_need_admin_and_same_origin(client):
    anon = TestClient(client.app)
    for verb, url, kw in [("put", "/api/admin/power-tree", {"json": GOOD}), ("delete", "/api/admin/power-points/isu1", {})]:
        assert getattr(anon, verb)(url, **kw).status_code == 401
        assert getattr(client, verb)(url, headers={"Origin": "http://evil.example"}, **kw).status_code == 403


def test_put_saves_and_state_shows_display_names(client):
    r = client.put("/api/admin/power-tree", json=GOOD)
    assert r.status_code == 200 and r.json()["power_tree"]["problems"] == []
    st = client.get("/api/admin/state").json()
    assert st["config"]["shelly_devices"][0]["upstream"] == "p:isu1"
    disp = {n["ref"]: n["display"] for n in st["power_tree"]["nodes"]}
    assert disp["p:gen1"] == "SRC01" and disp["m:m1"] == "CDU01" and disp["c:m1:pm1_0"] == "FDU01"
    assert [w["ref"] for w in st["power_tree"]["warnings"]] == ["m:m2"]      # the meter with no role
    assert st["config"]["power_points"][1]["allowance"] == {"unit": "kw", "value": 3.0}


def test_put_refuses_a_bad_tree_and_changes_nothing(client):
    bad = {"points": [{"id": "a", "name": "A", "role": "cdu", "unit_no": 1, "upstream": "p:b"},
                      {"id": "b", "name": "B", "role": "fdu", "unit_no": 1}]}
    r = client.put("/api/admin/power-tree", json=bad)
    assert r.status_code == 422 and "CDU01" in r.json()["detail"] and "Nothing has been changed" in r.json()["detail"]
    assert client.hub.config.power_points == []
    loop = {"points": [{"id": "a", "name": "A", "role": "other", "upstream": "p:b"},
                       {"id": "b", "name": "B", "role": "other", "upstream": "p:a"}]}
    assert client.put("/api/admin/power-tree", json=loop).status_code == 422
    dup = {"points": [{"id": "a", "name": "A", "role": "isu", "unit_no": 1}, {"id": "b", "name": "B", "role": "isu", "unit_no": 1}]}
    assert client.put("/api/admin/power-tree", json=dup).status_code == 422
    unknown = {"meters": [{"id": "nope"}]}
    assert client.put("/api/admin/power-tree", json=unknown).status_code == 422
    missing_up = {"points": [{"id": "a", "name": "A", "role": "isu", "unit_no": 1, "upstream": "p:ghost"}]}
    assert client.put("/api/admin/power-tree", json=missing_up).status_code == 422
    assert client.hub.config.power_points == []


@pytest.mark.parametrize("bad", [
    {"points": [{"name": "A", "role": "wind"}]}, {"points": [{"name": "A", "unit_no": 100}]},
    {"points": [{"name": ""}]}, {"points": [{"name": "a\nb"}]}, {"points": [{"name": "A", "extra": 1}]},
    {"points": [{"name": "A", "ups_type": "x"}]}, {"points": [{"name": "A", "id": "Bad Id"}]},
    {"points": [{"name": "A", "rating_a": -1}]}, {"points": [{"name": "A", "allowance": {"unit": "w", "value": 1}}]},
    {"points": [{"name": "A"}] * 41}, {"meters": [{"id": "m1", "channels": [{"key": "A B"}]}]},
    {"meters": [{"id": "m1", "upstream": "x" * 81}]}])
def test_put_validates_the_body(client, bad):
    r = client.put("/api/admin/power-tree", json=bad)
    assert r.status_code == 422 and client.hub.config.power_points == []


def test_new_point_gets_an_unused_id(client):
    client.put("/api/admin/power-tree", json={"points": [{"name": "Tower generator", "role": "source", "unit_no": 1}]})
    client.put("/api/admin/power-tree", json={"points": [{"name": "Tower generator", "role": "source", "unit_no": 2}]})
    assert [p.id for p in client.hub.config.power_points] == ["tower_generator", "tower_generator_2"]


def test_ups_type_kept_only_for_ups(client):
    body = {"points": [{"id": "u", "name": "Rack UPS", "role": "ups", "unit_no": 1, "ups_type": "online"},
                       {"id": "g", "name": "Gen", "role": "source", "unit_no": 1, "ups_type": "online"}]}
    assert client.put("/api/admin/power-tree", json=body).status_code == 200
    got = {p.id: p.ups_type for p in client.hub.config.power_points}
    assert got == {"u": "online", "g": "unknown"}


def test_allowance_dropped_when_monitored(client):
    body = {"points": [{"id": "a", "name": "A", "allowance": {"unit": "kw", "value": 2}, "monitored": True}]}
    client.put("/api/admin/power-tree", json=body)
    assert client.hub.config.power_points[0].allowance is None


def test_delete_point_endpoint(client):
    client.put("/api/admin/power-tree", json=GOOD)
    r = client.delete("/api/admin/power-points/isu1")
    assert r.status_code == 409 and "Nothing has been changed" in r.json()["detail"]
    assert client.delete("/api/admin/power-points/isu1?children=sideways").status_code == 422
    assert client.delete("/api/admin/power-points/isu1?children=reparent").status_code == 200
    assert client.hub.config.shelly_devices[0].upstream == "p:gen1"
    assert client.delete("/api/admin/power-points/isu1").status_code == 404


def test_public_structure_endpoint_has_no_private_data(client):
    client.put("/api/admin/power-tree", json=GOOD)
    anon = TestClient(client.app)
    r = anon.get("/api/power-tree")
    assert r.status_code == 200
    for secret in ("192.168", "AABBCC", "allowance", "monitored"):
        assert secret not in r.text
    assert {n["display"] for n in r.json()["nodes"]} >= {"SRC01", "ISU01", "CDU01", "FDU01"}


def test_admin_card_rules_in_node():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(Path(__file__).resolve().parent / "js" / "power_tree_admin_test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_removing_a_meter_detaches_what_it_fed(client):
    from stagewatch.core.config import ShellyDeviceConfig as D
    c = client.hub.config
    c.shelly_devices[1].upstream = "m:m1"
    pt.detach_children(c, pt.meter_ref("m1"))
    assert c.shelly_devices[1].upstream == ""
    assert D  # silence unused import in editors


# ------------------------------------------------------------------ ways, links and single-circuit meters
MAC1, MAC2 = "aabbccddee01", "aabbccddee02"


def single_cfg(**dev):
    """A meter with exactly one circuit, plus a CDU point above it."""
    cfg = Config()
    cfg.power_points = [PowerPointConfig(id="cdu", name="Stage left", role="cdu", unit_no=2)]
    cfg.shelly_devices = [ShellyDeviceConfig(id="rack", host="192.0.2.5", mac=MAC1, name="Rack", circuit_keys=["pm1_0"], **dev)]
    return cfg


def test_two_units_may_not_use_the_same_way_of_the_same_upstream():
    a = pt.Node("m:a", "meter", "equipment", 1, "p:u", "a", 2, "L1")
    b = pt.Node("m:b", "meter", "equipment", 2, "p:u", "b", 2, "L2")
    c = pt.Node("m:c", "meter", "equipment", 3, "p:u", "c", 3, "L3")
    up = pt.Node("p:u", "point", "cdu", 2)
    bad = [p for p in pt.validate([up, a, b, c]) if p.code == "duplicate_way"]
    assert len(bad) == 1 and bad[0].ref == "m:b"
    assert "way 2 of CDU02" in bad[0].message and "EQP01" in bad[0].message
    # the same way on a different upstream is fine
    other = pt.Node("p:v", "point", "cdu", 3)
    assert not [p for p in pt.validate([up, other, a, pt.Node("m:d", "meter", "equipment", 4, "p:v", "d", 2)]) if p.code == "duplicate_way"]


@pytest.mark.parametrize("up,way,line,expected", [
    ("CDU02", 2, "L1", "CDU02-C2-L1"), ("CDU01", 1, "3P", "CDU01-C1-3P"), ("CDU02", 2, "", "CDU02-C2"),
    ("CDU02", None, "L1", "CDU02"), ("", 2, "L1", "")])
def test_link_label(up, way, line, expected):
    """The same cases are in tests/js/power_tree_admin_test.js (PA.linkLabel)."""
    assert pt.link_label(up, way, line) == expected


def test_a_single_circuit_meter_is_one_node_and_its_settings_live_on_the_circuit():
    cfg = single_cfg()
    holder = pt.holder_of_meter(cfg.shelly_devices[0])
    holder.role, holder.unit_no, holder.upstream, holder.way, holder.phase = "equipment", 1, "p:cdu", 2, "L1"
    holder.breaker_a, holder.rating_a = 13.0, 16.0
    dev = cfg.shelly_devices[0]
    assert (dev.role, dev.unit_no, dev.upstream, dev.way, dev.rating_a, dev.breaker_a) == ("", None, "", None, None, None)   # not held twice
    ch = dev.channels[0]
    assert (ch.role, ch.unit_no, ch.upstream, ch.way, ch.phase, ch.breaker_a, ch.rating_a) == ("equipment", 1, "p:cdu", 2, "L1", 13.0, 16.0)
    nodes = pt.build_nodes(cfg)
    assert [n.ref for n in nodes] == ["p:cdu", "m:rack"]          # the circuit is not a second node
    assert pt.validate(nodes) == []
    rack = next(n for n in nodes if n.ref == "m:rack")
    assert (rack.way, rack.line) == (2, "L1")
    pub = {n["ref"]: n for n in pt.public_structure(cfg)["nodes"]}
    assert list(pub) == ["p:cdu", "m:rack"] and pub["m:rack"]["feed"] == "CDU02-C2-L1"
    assert pub["m:rack"]["breaker_a"] == 13.0 and pub["m:rack"]["rating_a"] == 16.0
    assert "192.0.2" not in str(pub) and MAC1 not in str(pub)


def test_a_three_phase_single_circuit_is_labelled_3p_and_never_has_a_line_choice():
    cfg = single_cfg()
    cfg.shelly_devices[0].circuit_keys, cfg.shelly_devices[0].three_phase_keys = ["em0"], ["em0"]
    h = pt.holder_of_meter(cfg.shelly_devices[0])
    h.role, h.upstream, h.way = "equipment", "p:cdu", 1
    assert pt.public_structure(cfg)["nodes"][1]["feed"] == "CDU02-C1-3P"


def test_older_file_with_role_and_upstream_on_the_meter_keeps_working_and_is_moved_once():
    cfg = single_cfg(role="equipment", unit_no=4, upstream="p:cdu", way=3, rating_a=16)
    cfg.shelly_devices[0].channels = [ShellyChannelConfig(key="pm1_0", phase="L2", rating_a=10)]      # the circuit's own limit wins
    # read both: before anything is moved the node already shows the meter's older values
    assert [n.upstream for n in pt.build_nodes(cfg) if n.ref == "m:rack"] == ["p:cdu"]
    assert pt.merge_single_circuits(cfg) == 5                    # role, number, upstream, way, and the meter's older limit
    dev, ch = cfg.shelly_devices[0], cfg.shelly_devices[0].channels[0]
    assert (dev.role, dev.unit_no, dev.upstream, dev.way, dev.rating_a) == ("", None, "", None, None)
    assert (ch.role, ch.unit_no, ch.upstream, ch.way, ch.rating_a, ch.phase) == ("equipment", 4, "p:cdu", 3, 10.0, "L2")
    assert pt.merge_single_circuits(cfg) == 0                    # again: nothing to do
    assert pt.public_structure(cfg)["nodes"][1]["rating_a"] == 10.0


def test_things_fed_from_the_single_circuit_are_re_pointed_at_the_meter_and_nothing_is_counted_twice():
    cfg = single_cfg()
    cfg.shelly_devices[0].channels = [ShellyChannelConfig(key="pm1_0", role="ups", unit_no=1, upstream="p:cdu")]
    cfg.shelly_devices.append(ShellyDeviceConfig(id="lamp", host="192.0.2.6", mac=MAC2, role="equipment", unit_no=1, upstream="c:rack:pm1_0"))
    pt.tidy_config(cfg)
    assert cfg.shelly_devices[1].upstream == "m:rack"
    nodes = pt.build_nodes(cfg)
    assert sorted(n.ref for n in nodes) == ["m:lamp", "m:rack", "p:cdu"] and pt.validate(nodes) == []


def test_a_meter_with_two_circuits_keeps_a_header_row_and_its_circuits():
    cfg = single_cfg()
    dev = cfg.shelly_devices[0]
    dev.circuit_keys = ["pm1_0", "pm1_1"]
    dev.channels = [ShellyChannelConfig(key="pm1_0", role="equipment", unit_no=1, upstream="p:cdu", way=1, phase="L1"),
                    ShellyChannelConfig(key="pm1_1", role="equipment", unit_no=2, upstream="p:cdu", way=2, phase="L2")]
    nodes = pt.build_nodes(cfg)
    assert sorted(n.ref for n in nodes) == ["c:rack:pm1_0", "c:rack:pm1_1", "m:rack", "p:cdu"]
    assert {n.ref: n.line for n in nodes if n.kind == "circuit"} == {"c:rack:pm1_0": "L1", "c:rack:pm1_1": "L2"}


def test_way_and_extras_are_applied_and_a_clashing_way_is_refused(tmp_path):
    from stagewatch.web.server import PowerTreeBody, apply_power_tree
    hub = Hub(tmp_path)
    hub.config.shelly_devices = [ShellyDeviceConfig(id="rack", host="192.0.2.5", mac=MAC1, name="Rack", circuit_keys=["pm1_0"])]
    hub.config.power_points = [PowerPointConfig(id="cdu", name="Stage left", role="cdu", unit_no=2)]
    body = PowerTreeBody.model_validate({"meters": [{"id": "rack", "role": "equipment", "unit_no": 1, "upstream": "p:cdu", "way": 2,
                                                      "label": "Amp rack", "phase": "L1", "breaker_a": 13, "rating_a": 16, "in_total": True, "channels": []}],
                                        "show_advanced": True})
    apply_power_tree(hub.config, body)
    ch = hub.config.shelly_devices[0].channels[0]
    assert (ch.way, ch.label, ch.phase, ch.breaker_a, ch.rating_a, ch.in_total) == (2, "Amp rack", "L1", 13.0, 16.0, True)
    assert hub.config.power_site.show_advanced is True
    clash = PowerTreeBody.model_validate({"points": [{"id": "x", "name": "Spare", "role": "equipment", "unit_no": 9, "upstream": "p:cdu", "way": 2}]})
    with pytest.raises(pt.TreeError) as exc:
        apply_power_tree(hub.config, clash)
    assert "way 2" in exc.value.message
    with pytest.raises(Exception):
        PowerTreeBody.model_validate({"meters": [{"id": "rack", "way": 100}]})
    hub.recorder.close()


def test_old_config_without_way_or_circuit_keys_loads_and_a_newer_file_survives_a_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(FIX.read_text(encoding="utf-8"), encoding="utf-8")
    store = ConfigStore(path)
    cfg = store.load()
    assert all(d.circuit_keys == [] and d.way is None for d in cfg.shelly_devices) and cfg.power_site.show_advanced is False
    cfg.shelly_devices[0].circuit_keys = ["pm1_0"]
    cfg.shelly_devices[0].channels[0].way = 4
    cfg.power_site.show_advanced = True
    store.save()
    again = ConfigStore(path).load()
    assert again.shelly_devices[0].circuit_keys == ["pm1_0"] and again.shelly_devices[0].channels[0].way == 4 and again.power_site.show_advanced
    # out-of-range ways in a file are dropped, never costing the meter
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["shelly_devices"][0]["channels"][0]["way"] = 500
    assert Config.model_validate(raw).shelly_devices[0].channels[0].way is None
