"""Calculated neutral current (Shelly): the pure calculation and the integration around it.

Every expected number is worked out by hand in the test that uses it. Phasor sum: line k's current is
I_k at angle (theta_k - phi_k), theta = 0, -120, +120 degrees for L1, L2, L3, phi = angle the current lags its voltage.
"""

from __future__ import annotations

import math
import time

import pytest

from stagewatch.core.config import ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.integrations.shelly import NEUTRAL_ENTITY, NEUTRAL_ID, ShellyIntegration
from stagewatch.integrations.shelly import neutral as nt
from test_shelly import Fetch, NullTransport


def c(line, amps, deg=None):
    """A circuit whose current lags its voltage by deg degrees (negative = leads), with watts, pf and var to match."""
    if deg is None:
        return nt.Circuit(line, amps)
    phi = math.radians(deg)
    return nt.Circuit(line, amps, power=100.0, pf=math.cos(phi), reactive=100.0 * math.tan(phi))


# ------------------------------------------------------------------ the calculation
def test_balanced_lines_give_zero_by_either_method():
    """10/10/10 A: 100+100+100-100-100-100 = 0."""
    assert nt.neutral_current([c("L1", 10), c("L2", 10), c("L3", 10)]) == nt.Neutral(0.0, nt.METHOD_ESTIMATE)
    r = nt.neutral_current([c("L1", 10, 25), c("L2", 10, 25), c("L3", 10, 25)])   # same angle on all three: still balanced
    assert r.method == nt.METHOD_PHASORS and r.amps == pytest.approx(0.0, abs=1e-9)


def test_estimate_with_unequal_currents():
    """10/6/8 A: 100+36+64-60-48-80 = 12, so sqrt(12) = 3.4641 A."""
    r = nt.neutral_current([c("L1", 10), c("L2", 6), c("L3", 8)])
    assert r.method == nt.METHOD_ESTIMATE and r.amps == pytest.approx(math.sqrt(12))


def test_one_loaded_line_gives_that_line_current():
    """10/0/0 A: sqrt(100) = 10. (A circuit reading 0 A is a reading; it is not the same as no circuit.)
    10/10/0 A: sqrt(100+100-100) = 10."""
    assert nt.neutral_current([c("L1", 10), c("L2", 0), c("L3", 0)]).amps == pytest.approx(10.0)
    assert nt.neutral_current([c("L1", 10), c("L2", 10), c("L3", 0)]).amps == pytest.approx(10.0)


def test_circuits_on_one_line_are_added_before_the_estimate():
    """L1 = 6 + 4 = 10 A, so 10/10/10 again: zero."""
    assert nt.neutral_current([c("L1", 6), c("L1", 4), c("L2", 10), c("L3", 10)]).amps == pytest.approx(0.0, abs=1e-9)


def test_phasors_with_unequal_power_factors():
    """10 A lagging 30, 6 A at 0, 8 A leading 20 degrees: 10 at -30, 6 at -120, 8 at +140 degrees.
    Real: 8.660 - 3.000 - 6.128 = -0.468. Imag: -5.000 - 5.196 + 5.142 = -5.054. |sum| = 5.075 A.
    The equal-power-factor estimate would say 3.46 A: wrong by a third."""
    circuits = [c("L1", 10, 30), c("L2", 6, 0), c("L3", 8, -20)]
    r = nt.neutral_current(circuits)
    assert r.method == nt.METHOD_PHASORS and r.amps == pytest.approx(5.075, abs=0.005)
    assert nt.neutral_current([c(x.line, x.amps) for x in circuits]).amps == pytest.approx(3.464, abs=0.001)


def test_phasor_circuits_on_one_line_add_as_phasors_not_as_amps():
    """L1: 5 A at +60 and 5 A at -60 degrees add to 5 A at 0 (not 10 A). With 5 A at 0 on L2 and L3 that is balanced: zero."""
    r = nt.neutral_current([c("L1", 5, 60), c("L1", 5, -60), c("L2", 5, 0), c("L3", 5, 0)])
    assert r.amps == pytest.approx(0.0, abs=1e-9)


def test_one_circuit_without_a_trustworthy_power_factor_makes_it_an_estimate():
    bare = nt.Circuit("L3", 8.0, power=100.0, pf=0.9)              # no reactive power: sign of the angle unknown
    assert nt.neutral_current([c("L1", 10, 30), c("L2", 6, 0), bare]).method == nt.METHOD_ESTIMATE
    back = nt.Circuit("L3", 8.0, power=-100.0, pf=-0.9, reactive=10.0)    # returning power
    assert nt.neutral_current([c("L1", 10, 30), c("L2", 6, 0), back]).method == nt.METHOD_ESTIMATE
    low = nt.Circuit("L3", 8.0, power=1.0, pf=0.05, reactive=10.0)         # pf too small to give an angle
    assert nt.neutral_current([c("L1", 10, 30), c("L2", 6, 0), low]).method == nt.METHOD_ESTIMATE


def test_a_line_with_no_circuit_is_unavailable_not_zero():
    r = nt.neutral_current([c("L1", 10), c("L2", 10)])
    assert r.amps is None and "Line 3" in r.reason and r.method == ""
    assert nt.neutral_current([]).amps is None


def test_a_circuit_not_reading_gives_no_figure_not_a_two_line_figure():
    r = nt.neutral_current([c("L1", 10), c("L2", 6), nt.Circuit("L3", None)])
    assert r.amps is None and "Line 3" in r.reason and "not reading" in r.reason
    assert nt.neutral_current([c("L1", 10), c("L1", None), c("L2", 6), c("L3", 8)]).amps is None


# ------------------------------------------------------------------ the integration
MACS = {"a": "025e000000a1", "b": "025e000000a2", "c": "025e000000a3"}


def pm(amps, pf=None):
    comp = {"id": 0, "voltage": 230.0, "current": amps, "apower": round(230.0 * amps, 1), "freq": 50.0,
            "aenergy": {"total": 1000.0}}
    if pf is not None:
        comp["pf"] = pf
    return {"pm1:0": comp}


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    yield h
    h.recorder.close()


async def rig(hub, phases=("L1", "L2", "L3")):
    integ = ShellyIntegration(hub, fetch=Fetch(), transport=NullTransport())
    for (k, mac), phase in zip(MACS.items(), phases):
        hub.config.shelly_devices.append(ShellyDeviceConfig(
            id=f"m_{k}", host=f"192.0.2.7{len(hub.config.shelly_devices)}", mac=mac, name=f"Meter {k}",
            channels=[ShellyChannelConfig(key="pm1_0", phase=phase)] if phase else []))
    hub.add_integration(integ)
    await integ.start()
    return integ


def feed(integ, key, status, ts):
    integ.status(MACS[key], status, ts)


def neutral(hub):
    return hub.entities[NEUTRAL_ENTITY]


async def test_the_neutral_is_calculated_labelled_and_recorded(hub):
    """10/6/8 A on L1/L2/L3, no power factor from these meters: the estimate, sqrt(12) = 3.4641 A."""
    integ = await rig(hub)
    now = time.time()
    feed(integ, "a", pm(10.0), now)
    feed(integ, "b", pm(6.0), now)
    assert neutral(hub).value is None                       # two lines are not enough
    feed(integ, "c", pm(8.0), now)
    e = neutral(hub)
    assert e.value == pytest.approx(math.sqrt(12)) and e.name == "Neutral current (calculated)"
    assert e.labels == {"channel": "neutral_calc", "field": "current", "group": "n", "calculated": nt.METHOD_ESTIMATE}
    assert e.locked_equipment and hub.devices[NEUTRAL_ID].category == "meter"
    note = hub.devices[NEUTRAL_ID].public["calc_note"]
    assert "not measured" in note and "estimate" in note


async def test_a_stale_line_means_no_figure(hub):
    integ = await rig(hub)
    now = time.time()
    feed(integ, "a", pm(10.0), now)
    feed(integ, "b", pm(6.0), now)
    feed(integ, "c", pm(8.0), now - 3600)                   # an hour old
    assert neutral(hub).value is None
    assert "not reading" in hub.devices[NEUTRAL_ID].public["calc_note"]
    feed(integ, "c", pm(8.0), now)
    assert neutral(hub).value == pytest.approx(math.sqrt(12))


async def test_a_meter_going_quiet_removes_the_figure(hub):
    integ = await rig(hub)
    now = time.time()
    for k, a in (("a", 10.0), ("b", 6.0), ("c", 8.0)):
        feed(integ, k, pm(a), now)
    assert neutral(hub).value is not None
    integ._clock = lambda: now + hub.config.site.stale_after_s + 5    # time passes; nothing has arrived since
    integ._update_neutral(integ._clock())
    assert neutral(hub).value is None


async def test_nothing_is_offered_until_a_circuit_is_put_on_a_line(hub):
    integ = await rig(hub, phases=("", "", ""))
    feed(integ, "a", pm(10.0), time.time())
    assert NEUTRAL_ID not in hub.devices and NEUTRAL_ENTITY not in hub.entities


async def test_a_line_with_no_circuit_says_so(hub):
    integ = await rig(hub, phases=("L1", "L2", ""))
    now = time.time()
    for k in "abc":
        feed(integ, k, pm(10.0), now)
    assert neutral(hub).value is None and "Line 3" in hub.devices[NEUTRAL_ID].public["calc_note"]


async def test_removing_a_meter_leaves_no_stale_figure(hub):
    integ = await rig(hub)
    now = time.time()
    for k, a in (("a", 10.0), ("b", 6.0), ("c", 8.0)):
        feed(integ, k, pm(a), now)
    await integ.remove("m_c")
    assert neutral(hub).value is None and "Line 3" in hub.devices[NEUTRAL_ID].public["calc_note"]


async def test_a_three_phase_meter_is_left_out(hub):
    """A three-phase meter gives its own measured neutral; its channel is never a single-phase circuit here."""
    integ = await rig(hub, phases=("L1", "L2", ""))
    hub.config.shelly_devices[2].channels = [ShellyChannelConfig(key="em0", phase="L3")]
    em = {"em:0": {"id": 0, "a_current": 5.0, "b_current": 5.0, "c_current": 5.0, "n_current": 0.1}}
    now = time.time()
    feed(integ, "a", pm(10.0), now)
    feed(integ, "b", pm(10.0), now)
    feed(integ, "c", em, now)
    assert neutral(hub).value is None and "Line 3" in hub.devices[NEUTRAL_ID].public["calc_note"]


async def test_the_calculated_note_reaches_the_public_device_dict(hub):
    """The note is set on .public; the card reads it from the device dict the dashboards get."""
    integ = await rig(hub)
    now = time.time()
    for k, a in (("a", 10.0), ("b", 6.0), ("c", 8.0)):
        feed(integ, k, pm(a), now)
    assert "not measured" in hub.devices[NEUTRAL_ID].to_dict()["calc_note"]
    snap = [d for d in hub.snapshot()["devices"] if d["id"] == NEUTRAL_ID]
    assert snap and "not measured" in snap[0]["calc_note"]
