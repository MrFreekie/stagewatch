"""Trend lines on the Power tree tiles: the batched read of already-recorded watts (nothing new is recorded),
its caps, gaps staying gaps, and a budget that fails if the read ever scans the whole states table."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from stagewatch.core.hub import Hub
from stagewatch.core.recorder import Recorder
from stagewatch.integrations.shelly import seed_emulate_shelly
from stagewatch.web import server
from stagewatch.web.server import create_app


def test_trend_averages_into_capped_points_across_shows_and_leaves_a_gap(tmp_path):
    rec = Recorder(tmp_path / "t.db")
    now = time.time()
    since = now - 1800
    rec.record_state("m.w", 100.0, since + 10)
    rec.record_state("m.w", 300.0, since + 11)                  # same bucket: averaged to 200
    rec.record_state("m.w", 500.0, now - 5)                     # a long hole between: no point in the hole
    rec.record_state("m.w", None, now - 60)                     # "not available" is not a value
    rec.flush()
    out = rec.trend_threaded(["m.w", "m.none"], since, now, 60)
    assert [round(v) for _, v in out["m.w"]] == [200, 500] and out["m.none"] == []
    assert len(out["m.w"]) <= 60
    rec.close()


def test_the_endpoint_gives_only_metered_nodes_with_history_and_caps_the_window(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    seed_emulate_shelly(hub.config)
    with TestClient(create_app(hub, lan_addresses=lambda: ["192.0.2.10"])) as c:
        empty = c.get("/api/power-tree/trend?minutes=9999").json()
        assert empty["minutes"] == 60 and empty["series"] == {}          # nothing recorded yet: no lines, no zeros
        ref = next(n["ref"] for n in c.get("/api/power-tree").json()["nodes"] if n["ref"].startswith("m:"))
        dev = ref.split(":")[1]
        hub.recorder.record_state(f"{dev}.meter_power_load", 750.0, time.time() - 60)
        got = c.get("/api/power-tree/trend?minutes=1").json()
        assert got["minutes"] == 15
        assert set(got["series"]) <= {n["ref"] for n in c.get("/api/power-tree").json()["nodes"] if not n["ref"].startswith("p:")}
        assert not any(k.startswith("p:") for k in got["series"])         # an unmetered point never has a line
        assert "192.0.2" not in str(got)


def test_trend_read_stays_within_budget_with_a_big_states_table(tmp_path):
    rec = Recorder(tmp_path / "big.db")
    now = time.time()
    rows = [(rec.show_id, now - 3600 + i * 2.0, f"m{n}.meter_power_load", 1000.0 + i % 50) for n in range(server.TREND_SERIES) for i in range(1800)]
    rows += [(rec.show_id, now - 86_400 + i, f"other{i % 40}.x", 1.0) for i in range(300_000)]      # unrelated bulk: a scan would show
    rec._db.executemany("INSERT INTO states (show_id, ts, entity_id, value) VALUES (?,?,?,?)", rows)
    rec._db.commit()
    ids = [f"m{n}.meter_power_load" for n in range(server.TREND_SERIES)]
    t0 = time.perf_counter()
    out = rec.trend_threaded(ids, now - 1800, now, server.TREND_POINTS)
    took = time.perf_counter() - t0
    assert all(0 < len(v) <= server.TREND_POINTS for v in out.values())
    assert took < 1.5       # measured well under half a second; a whole-table read per series is many seconds
    rec.close()
