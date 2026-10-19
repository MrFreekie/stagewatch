"""Calculated Power card figures: reactive power, phase unbalance, demand with load factor, generator size.
Every expected number is worked out by hand in the comment beside it."""

from __future__ import annotations

import typing

import pytest

from stagewatch.core.config import PowerSiteConfig, ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.integrations.shelly import ShellyIntegration, calc
from stagewatch.integrations.shelly import readings as rd
from test_shelly import Fetch, NullTransport


# ------------------------------------------------------------------ 1. reactive power
def test_reactive_is_square_root_of_va_squared_minus_w_squared():
    assert calc.reactive_var(800, 1000) == pytest.approx(600)      # sqrt(1000^2 - 800^2) = sqrt(360000) = 600
    assert calc.reactive_var(-800, 1000) == pytest.approx(600)     # a size only: no sign
    assert calc.reactive_var(1000, 980) == 0.0                     # 980 is within 5 % of 1000: rounding, so 0
    assert calc.reactive_var(1000, 900) is None                    # 900 < 950: the two readings disagree, no figure
    assert calc.reactive_var(None, 1000) is None and calc.reactive_var(800, None) is None


def test_reactive_reading_is_added_beside_power_and_apparent_but_the_meters_own_is_kept():
    status = {"em1:0": {"voltage": 230.0, "current": 5.0, "act_power": 920.0}}   # S = 230 x 5 = 1150; P = 920
    got = {r.object_id: r for r in rd.readings_from_status(status)}
    assert got["em1_0_reactive"].value == pytest.approx(690)       # sqrt(1150^2 - 920^2) = sqrt(476100) = 690
    assert got["em1_0_reactive"].calculated
    own = {"emeters": [{"power": 100.0, "reactive": -42.0, "pf": 0.92, "voltage": 230.0, "is_valid": True, "total": 1, "total_returned": 0}]}
    got = {r.object_id: r for r in rd.readings_from_status(rd.gen1_status(own))}
    assert got["gen1em_0_reactive"].value == -42.0 and got["gen1em_0_reactive"].calculated == ""   # the meter's own, with its sign
    assert [r for r in rd.readings_from_status(rd.gen1_status(own)) if r.field == "reactive"].__len__() == 1


# ------------------------------------------------------------------ 2. phase unbalance
def test_balanced_lines_are_zero_percent():
    u = calc.unbalance(10, 10, 10)                                   # mean 10, biggest deviation 0
    assert u["pct"] == 0 and u["shares"] == pytest.approx([100 / 3] * 3)


def test_unbalance_is_the_biggest_deviation_over_the_mean():
    u = calc.unbalance(12, 10, 8)                                    # mean 10, deviations 2, 0, 2 -> 2/10 = 20 %
    assert u["pct"] == pytest.approx(20) and u["mean"] == pytest.approx(10)
    assert u["shares"] == pytest.approx([40, 100 / 3, 80 / 3])       # 12/30, 10/30, 8/30


def test_no_unbalance_when_a_line_is_missing_or_the_load_is_near_zero():
    assert calc.unbalance(12, None, 8) is None
    assert calc.unbalance(0.5, 0.4, 0.3) is None                     # mean 0.4 A is below the 1 A floor
    assert calc.unbalance(1.0, 1.0, 1.0)["pct"] == 0                 # exactly at the floor is shown


# ------------------------------------------------------------------ 3. demand
def _feed(d, pairs):
    for t, v in pairs:
        d.feed(v, t)


def test_demand_average_peak_and_load_factor():
    d = calc.Demand(window_s=60, gap_s=30)
    _feed(d, [(0, 1000), (10, 1000), (20, 1000), (30, 2000), (40, 2000), (50, 2000), (60, 2000)])
    assert d.avg == pytest.approx(1500)       # window 0-60: three 10 s at 1000, three at 2000 = 90000 / 60
    d.feed(2000, 70)
    assert d.avg == pytest.approx(100000 / 60)   # window 10-70: two at 1000, four at 2000 = (20000+80000)/60 = 1666.67
    assert d.peak == pytest.approx(100000 / 60) and d.peak_at == 70
    assert d.day_avg == pytest.approx(110000 / 70)   # three 10 s at 1000, four at 2000 = 110000 W.s over 70 s = 1571.43
    assert d.load_factor == pytest.approx(33 / 35)   # 1571.43 / 1666.67 = 0.942857
    assert not d.avg_incomplete and not d.had_gap


def test_no_average_before_a_full_window_has_passed():
    d = calc.Demand(60, 30)
    _feed(d, [(0, 1000), (10, 1000), (20, 1000)])
    assert d.avg is None and d.peak is None and d.load_factor is None


def test_a_gap_is_left_out_not_read_as_zero_and_marks_the_window_incomplete():
    d = calc.Demand(60, 30)
    _feed(d, [(0, 1000), (10, 1000), (20, 1000), (100, 2000), (110, 2000)])   # silent from 20 to 100 (80 s > 30 s)
    assert d.avg == pytest.approx(2000)       # only 100-110 was read in the window 50-110: 2000, not diluted by zeros
    assert d.avg_incomplete and d.peak_incomplete and d.had_gap
    assert d.day_avg == pytest.approx(40000 / 30)    # (1000 x 20 s + 2000 x 10 s) / 30 s read = 1333.33


def test_gap_limit_is_three_poll_intervals_but_at_least_thirty_seconds():
    assert calc.gap_seconds(3) == 30 and calc.gap_seconds(5) == 30 and calc.gap_seconds(20) == 60


# ------------------------------------------------------------------ 4. generator
def test_generator_size_worked_by_hand():
    s = calc.generator_suggestion(40000, 80, 0.8)       # 40 kVA / 0.8 = 50 kVA; x 0.8 = 40 kW; 50 is a standard size
    assert s["min_kva"] == pytest.approx(50) and s["min_kw"] == pytest.approx(40) and s["next_kva"] == 50
    s = calc.generator_suggestion(41000, 80, 0.8)       # 41 / 0.8 = 51.25 kVA; 41 kW; next size up is 60
    assert s["min_kva"] == pytest.approx(51.25) and s["min_kw"] == pytest.approx(41) and s["next_kva"] == 60
    s = calc.generator_suggestion(41000, 100, 1.0)      # no margin, unity power factor: 41 kVA, 41 kW, next 50
    assert s["min_kva"] == pytest.approx(41) and s["min_kw"] == pytest.approx(41) and s["next_kva"] == 50
    assert calc.generator_suggestion(900000, 80)["next_kva"] is None    # 1125 kVA is above the list
    assert calc.generator_suggestion(None) is None and calc.generator_suggestion(0) is None


def test_headroom_against_a_stated_size():
    ok = calc.generator_suggestion(41000, 80, 0.8, size_kva=60, now_va=30000)
    assert ok["loaded_pct"] == pytest.approx(100 * 41 / 60) and ok["state"] == "ok"      # 68.33 %
    assert ok["now_pct"] == pytest.approx(50)                                              # 30 / 60
    assert calc.generator_suggestion(41000, 80, 0.8, size_kva=50)["state"] == "limit"     # 82 %, at or above 80 %
    assert calc.generator_suggestion(41000, 80, 0.8, size_kva=40)["state"] == "over"      # 102.5 %
    assert calc.generator_suggestion(40000, 80, 0.8, size_kva=50)["state"] == "limit"     # exactly 80 % counts as at the limit


# ------------------------------------------------------------------ settings
def test_settings_defaults_ranges_and_the_window_list_match_calc():
    d = PowerSiteConfig()
    assert (d.demand_window_min, d.gen_loading_pct, d.gen_power_factor, d.gen_size_kva) == (calc.DEFAULT_WINDOW_MIN, calc.GEN_LOADING_DEFAULT, calc.GEN_PF_DEFAULT, None)
    assert typing.get_args(PowerSiteConfig.model_fields["demand_window_min"].annotation) == calc.DEMAND_WINDOWS_MIN
    for bad in ({"gen_loading_pct": 49}, {"gen_loading_pct": 101}, {"gen_power_factor": 0.4}, {"gen_power_factor": 1.1},
                {"demand_window_min": 10}, {"gen_size_kva": 0}):
        with pytest.raises(ValueError):
            PowerSiteConfig(**bad)
    assert PowerSiteConfig(gen_loading_pct=50, gen_power_factor=0.5).gen_loading_pct == 50


# ------------------------------------------------------------------ through the integration
MAC = "025e00000077"
T0 = 1_790_000_000 - (1_790_000_000 % 86400) + 12 * 3600    # midday UTC: a real date, never near the 06:00 show-day rollover


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    yield h
    h.recorder.close()


async def _site(hub, channels):
    hub.config.power_site.demand_window_min = 5
    cfg = ShellyDeviceConfig(id="distro", host="192.0.2.70", mac=MAC, name="Distro", channels=channels)
    hub.config.shelly_devices.append(cfg)
    integ = ShellyIntegration(hub, fetch=Fetch(), transport=NullTransport())
    hub.add_integration(integ)
    await integ.start()
    return integ


def _em1(amps, volts=230.0, pf=None):
    """Three single-circuit channels. Watts = volts x amps x 0.8 so the VA (volts x amps) and W differ."""
    return {f"em1:{i}": {"voltage": volts, "current": a, "act_power": volts * a * 0.8, "pf": pf} for i, a in enumerate(amps)}


async def test_unbalance_and_line_currents_across_circuits_set_to_lines(hub):
    chans = [ShellyChannelConfig(key=f"em1_{i}", phase=p, in_total=True) for i, p in enumerate(("L1", "L2", "L3"))]
    integ = await _site(hub, chans)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    e = hub.entities
    assert [e[f"power_site.line_{i}_current"].value for i in (1, 2, 3)] == [12.0, 10.0, 8.0]
    assert e["power_site.unbalance"].value == pytest.approx(20)                 # mean 10, biggest deviation 2
    assert e["power_site.unbalance"].day["shares"] == pytest.approx([40, 100 / 3, 80 / 3])
    assert e["power_site.site_power_load"].value == pytest.approx(230 * 30 * 0.8)   # 5520 W (all three ticked)
    assert e["power_site.site_apparent_load"].value == pytest.approx(230 * 30)      # 6900 VA
    assert e["distro.em1_0_reactive"].value == pytest.approx(230 * 12 * 0.6)        # sqrt(S^2 - (0.8 S)^2) = 0.6 S = 1656 var
    assert hub.devices["power_site"].category == "meter" and hub.devices["power_site"].to_dict()["derived"] is True
    assert e["power_site.unbalance"].labels["channel"] == "calc"               # reaches the graph, not the meter cards


async def test_site_total_and_unbalance_have_no_value_when_a_circuit_has_none(hub):
    chans = [ShellyChannelConfig(key=f"em1_{i}", phase=p, in_total=True) for i, p in enumerate(("L1", "L2", "L3"))]
    integ = await _site(hub, chans)
    integ.status(MAC, _em1([12.0, 10.0, 8.0]), T0)
    bad = _em1([12.0, 10.0, 8.0])
    bad["em1:1"]["current"] = None
    integ.status(MAC, bad, T0 + 10)
    assert hub.entities["power_site.unbalance"].value is None
    assert hub.entities["power_site.site_apparent_load"].value is None
    assert hub.entities["power_site.line_2_current"].value is None


async def test_demand_peak_and_generator_suggestion_through_the_integration(hub):
    chans = [ShellyChannelConfig(key="em1_0", in_total=True)]
    integ = await _site(hub, chans)
    hub.config.power_site.gen_size_kva = 20.0
    # window 5 min = 300 s; 100 A x 230 V = 23,000 VA held for 300 s, polled every 10 s
    for i in range(0, 31):
        integ.status(MAC, _em1([100.0]), T0 + 10 * i)
    d = hub.entities["power_site.site_apparent_demand"]
    assert d.value == pytest.approx(23000) and d.day["peak"] == pytest.approx(23000)
    assert d.day["load_factor"] == pytest.approx(1.0) and d.day["window_min"] == 5
    g = hub.entities["power_site.generator"]
    assert g.value == pytest.approx(28.75)                  # 23 kVA / 0.8
    assert g.day["next_kva"] == 30 and g.day["min_kw"] == pytest.approx(23.0)    # 28.75 x 0.8
    assert g.day["state"] == "over" and g.day["loaded_pct"] == pytest.approx(115)   # 23 / 20 kVA
    # a meter's own demand sits on the meter, and the load series are recorded for history
    assert hub.entities["distro.meter_apparent_demand"].value == pytest.approx(23000)
    assert hub.recorder.raw_states("power_site.site_apparent_load", T0 - 3600, T0 + 5000)


# ------------------------------------------------------------------ the admin endpoint
@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from stagewatch.web.server import create_app
    h = Hub(tmp_path)
    with TestClient(create_app(h)) as c:
        c.hub = h
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        yield c


def test_power_site_settings_need_admin_and_the_same_origin_and_are_checked(client):
    from fastapi.testclient import TestClient
    body = {"demand_window_min": 30, "gen_loading_pct": 75, "gen_power_factor": 0.85, "gen_size_kva": 60}
    assert TestClient(client.app).put("/api/admin/power-site", json=body).status_code == 401
    assert client.put("/api/admin/power-site", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    for bad in ({**body, "gen_loading_pct": 40}, {**body, "gen_power_factor": 0.2}, {**body, "demand_window_min": 10},
                {**body, "gen_size_kva": -1}, {**body, "extra": 1}):
        assert client.put("/api/admin/power-site", json=bad).status_code == 422
    assert client.put("/api/admin/power-site", json=body).json() == {"ok": True}
    ps = client.hub.config.power_site
    assert (ps.demand_window_min, ps.gen_loading_pct, ps.gen_power_factor, ps.gen_size_kva) == (30, 75, 0.85, 60)
    client.put("/api/admin/power-site", json={**body, "gen_size_kva": 0})
    assert client.hub.config.power_site.gen_size_kva is None            # 0 clears the size
    assert client.get("/api/admin/state").json()["config"]["power_site"]["gen_loading_pct"] == 75
