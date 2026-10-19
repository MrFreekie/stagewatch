"""The server freeze of 19 October 2026: after a restart the calculated power figures were rebuilt from the day's
history on the event loop, each with a query that read the whole states table, and each replay re-summed the
whole demand window per reading. With a show day of history the loop stood still for tens of seconds, the
launcher's health checks failed and it killed the server, over and over.

Budgets here are generous (a slow CI runner must pass) but a return of the whole-table read, the per-reading
window sum, or an unbounded recording rate fails them by a wide margin.
"""

from __future__ import annotations

import asyncio
import logging
import random
import statistics
import threading
import time
from collections import deque

import pytest

from stagewatch.core import loopguard
from stagewatch.core.config import PowerPointConfig, ShellyChannelConfig, ShellyDeviceConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.integrations.shelly import ShellyIntegration, _Meter, calc, replay
from stagewatch.integrations.shelly.emulate import DEMO3_HOST, EmulatedFleet, emulated_mac
from test_ontime import until

T0 = 1_790_000_000.0
N_METERS = 16
POLL_S = 2.0
ROWS_PER_MINUTE_BUDGET = 9_000        # measured 7,500 for 16 meters at 2 s (the readings themselves are most of it)


class Clk:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _fleet_config(hub: Hub, n: int) -> list[ShellyDeviceConfig]:
    devs = []
    for i in range(n):
        host = DEMO3_HOST if i == n - 1 else f"192.0.2.{20 + i}"
        ch = [] if host == DEMO3_HOST else [ShellyChannelConfig(key="pm1_0", label=f"C{i}", phase=("L1", "L2", "L3")[i % 3], in_total=True)]
        devs.append(ShellyDeviceConfig(id=f"m{i}", host=host, mac=emulated_mac(host), name=f"Meter {i}", rating_a=16,
                                       role="isu" if i == 0 else "equipment", unit_no=i + 1,
                                       upstream="p:gen" if i == 0 else "m:m0", channels=ch, poll_interval_s=POLL_S))
    hub.config.shelly_devices = devs
    hub.config.power_points = [PowerPointConfig(id="gen", name="Gen", role="source", unit_no=1, rating_kva=60)]
    hub.config.power_site.demand_window_min = 5
    return devs


def _integration(hub: Hub, devs, clk: Clk) -> ShellyIntegration:
    integ = ShellyIntegration(hub, emulate=True, clock=clk)
    for cfg in devs:
        integ._meters[cfg.mac] = _Meter(cfg)
        hub.register_device(integ._device(cfg, None, Status.INITIALIZING))
    return integ


def _cycle(integ, fleet, devs, clk: Clk) -> float:
    t0 = time.perf_counter()
    for cfg in devs:
        integ.status(cfg.mac, {"sys": {"mac": cfg.mac.upper()}, **fleet.meter(cfg.host).components(clk.t)}, clk.t)
    return time.perf_counter() - t0


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path, emulate=True)
    yield h
    h.recorder.close("stop")


# ------------------------------------------------------------------ the query
def test_raw_states_uses_the_index_not_a_table_scan(hub):
    """The slow query read every row ('SCAN states'); it must now search by show, entity and time."""
    plan = " ".join(str(r) for r in hub.recorder._db.execute("EXPLAIN QUERY PLAN " + hub.recorder._RAW_SQL,
                                                              (0, "x", 0, 1)).fetchall())
    assert "SEARCH states" in plan and "SCAN states" not in plan


def test_raw_states_still_reads_across_shows_and_skips_empty_values(hub):
    rec = hub.recorder
    rec.record_state("a.b", 1.0, T0)
    rec.record_state("a.b", None, T0 + 1)          # "not available": left out
    rec.record_state("a.c", 9.0, T0 + 1)           # another entity: left out
    rec.flush()
    rec.start_show("Second show")                  # a later show: both are read
    rec.record_state("a.b", 3.0, T0 + 2)
    assert rec.raw_states("a.b", T0 - 10, T0 + 10) == [(T0, 1.0), (T0 + 2, 3.0)]
    assert rec.raw_states("a.b", T0 + 1, T0 + 10) == [(T0 + 2, 3.0)]
    assert rec.count_raw_states("a.b", T0 - 10, T0 + 10, 100) == 2 and rec.count_raw_states("a.b", T0 - 10, T0 + 10, 1) == 1


# ------------------------------------------------------------------ the demand maths
def _reference_avg(segs, lo):
    """The original way: add up every held segment in the window each time (independent of the running sums)."""
    ws = s = 0.0
    for t0, t1, v in segs:
        d = t1 - max(t0, lo)
        ws += v * d
        s += d
    return ws / s if s > 0 else None


def test_demand_running_sums_give_the_same_averages_as_summing_the_window_each_time():
    rng = random.Random(7)
    d = calc.Demand(window_s=300, gap_s=30)
    ref: deque = deque()
    t, last = 0.0, None
    for i in range(6000):
        t += rng.choice([1.0, 2.0, 2.0, 3.0, 45.0 if i % 400 == 0 else 2.0])     # now and then a silence (a gap)
        v = rng.uniform(0, 5000)
        if last is not None and t - last[0] <= 30:
            ref.append((last[0], t, last[1]))
        last = (t, v)
        d.feed(v, t)
        while ref and ref[0][1] <= t - 300:
            ref.popleft()
        if t - d.first_ts >= 300 and ref:
            assert d.avg == pytest.approx(_reference_avg(ref, t - 300), rel=1e-9)


def test_demand_feed_does_a_fixed_amount_of_work_whatever_the_window_holds():
    d = calc.Demand(window_s=900, gap_s=30)
    for i in range(20_000):
        d.feed(1000.0 + i % 7, 1.0 * i)
    t0 = time.perf_counter()
    for i in range(20_000, 40_000):
        d.feed(1000.0 + i % 7, 1.0 * i)
    per_feed = (time.perf_counter() - t0) / 20_000
    assert per_feed < 100e-6        # about 4 microseconds; summing the 900-segment window each time was over 100


# ------------------------------------------------------------------ the whole poll path
async def test_sixteen_meters_at_two_seconds_stay_within_the_work_and_recording_budget(hub):
    devs = _fleet_config(hub, N_METERS)
    clk = Clk(T0)
    integ = _integration(hub, devs, clk)
    fleet = EmulatedFleet(clk, outages=False)
    reads = []
    real = hub.recorder.raw_states
    hub.recorder.raw_states = lambda *a: reads.append(a) or real(*a)
    took = []
    minutes = 8                                      # longer than the 5 minute demand window
    for _ in range(int(minutes * 60 / POLL_S)):
        took.append(_cycle(integ, fleet, devs, clk))
        clk.t += POLL_S
    hub.recorder.flush()
    rows = hub.recorder._db.execute("SELECT COUNT(*) FROM states").fetchone()[0]
    assert rows / minutes < ROWS_PER_MINUTE_BUDGET, f"{rows / minutes:.0f} rows a minute"
    # one cycle is 16 polls: 15 ms measured; 250 ms leaves room for a slow runner but not for a scan or a window re-sum
    assert statistics.median(took) < 0.25 and max(took[5:]) < 1.0
    # the history is read back once per figure at the start, never again while polling goes on
    assert len(reads) == len({a[0] for a in reads})


async def test_a_restart_with_a_long_history_does_not_hold_the_loop_and_gives_the_same_figures(hub):
    devs = _fleet_config(hub, 4)
    clk = Clk(T0)
    integ = _integration(hub, devs, clk)
    fleet = EmulatedFleet(clk, outages=False)
    for _ in range(int(20 * 60 / POLL_S)):               # 20 minutes: far more than SYNC_ROWS readings for every figure
        _cycle(integ, fleet, devs, clk)
        clk.t += POLL_S
    hub.recorder.flush()
    site = "power_site.site_power_demand"
    before = hub.entities[site].value
    peak_before = hub.entities[site].day["peak"]
    # "restart": a new integration over the same history. Its first cycle must not replay everything on this thread.
    integ2 = _integration(hub, devs, clk)
    fed_here, me = [], threading.get_ident()      # only feeds on this (the loop's) thread count
    real_feed = calc.Demand.feed
    calc.Demand.feed = lambda self, v, t: (fed_here.append(1) if threading.get_ident() == me else None, real_feed(self, v, t))[1]
    try:
        _cycle(integ2, fleet, devs, clk)
        assert len(fed_here) < 100              # the live readings only, not the ~600 recorded ones for each figure
        # the figures come back once the day has been read back, and agree with what was running before
        for _ in range(100):
            clk.t += POLL_S
            _cycle(integ2, fleet, devs, clk)
            if hub.entities["m1.meter_power_demand"].value is not None and hub.entities[site].value is not None:
                break
            await asyncio.sleep(0.05)
    finally:
        calc.Demand.feed = real_feed
    after = hub.entities[site]
    assert after.value is not None and after.day["peak"] >= peak_before - 1e-6          # nothing lost
    assert after.value == pytest.approx(before, rel=0.05)


def test_readings_arriving_during_a_rebuild_are_held_and_fed_in_order():
    class Rec:
        def flush(self): ...
        def count_raw_states(self, *a): return replay.SYNC_ROWS + 1
        def raw_states_threaded(self, *a): return [(float(t), 1.0) for t in range(1, 6)]

    seen = []

    class Target:
        last_ts = None

        def feed(self, v, t):
            if self.last_ts is not None and t <= self.last_ts:
                return          # as Demand and DayFigures do
            seen.append(t)
            self.last_ts = t

    rb = replay.Rebuild(Rec(), "x", lambda: 0.0, 5.0, Target())
    rb._fut.result()
    rb.feed(2.0, 3.0)        # older than what was read back: ignored, not fed twice
    rb.feed(2.0, 6.0)
    assert seen == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0] and rb.ready


def test_a_failed_rebuild_falls_back_to_the_live_readings_and_the_held_ones_are_capped(caplog):
    class Rec:
        def flush(self): ...
        def count_raw_states(self, *a): return replay.SYNC_ROWS + 1
        def raw_states_threaded(self, *a): raise RuntimeError("database is locked")

    class Target:
        last_ts = None
        n = 0

        def feed(self, v, t):
            self.n += 1
            self.last_ts = t

    tg = Target()
    rb = replay.Rebuild(Rec(), "x", lambda: 0.0, 5.0, tg)
    with pytest.raises(RuntimeError):
        rb._fut.result()
    for i in range(replay.MAX_HELD + 50):                 # held while not noticed as done: bounded
        rb._held.append((1.0, 10.0 + i)) if len(rb._held) < replay.MAX_HELD else None
    assert len(rb._held) == replay.MAX_HELD
    with caplog.at_level(logging.ERROR):
        assert rb.ready                                    # not stuck: logs the failure and carries on
    assert "could not read the day so far" in caplog.text and tg.n == replay.MAX_HELD
    rb.feed(1.0, 1e9)
    assert tg.n == replay.MAX_HELD + 1                     # live readings go straight through afterwards


# ------------------------------------------------------------------ self-protection
def test_a_slow_section_is_named_in_a_warning(caplog):
    loopguard._last_logged.clear()
    with caplog.at_level(logging.WARNING, logger="stagewatch.core.loopguard"):
        with loopguard.timed("Test section", limit_s=0.02):
            time.sleep(0.06)
        with loopguard.timed("Quick section", limit_s=5.0):
            pass
    text = caplog.text
    assert "Slow section: Test section held the event loop" in text and "Quick section" not in text


async def test_the_watchdog_says_where_a_blocked_loop_is_stuck(caplog):
    wd = loopguard.LoopWatchdog(stall_s=0.3)
    with caplog.at_level(logging.WARNING, logger="stagewatch.core.loopguard"):
        wd.start()
        await asyncio.sleep(0.4)
        time.sleep(1.0)                                  # block the loop on purpose
        await until(lambda: wd.reports >= 1, timeout=5.0)
        await wd.stop()
    assert "The event loop has not run for" in caplog.text and "test_the_watchdog_says_where" in caplog.text
