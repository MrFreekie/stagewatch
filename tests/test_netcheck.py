"""Header internet check: two failures for "down", instant recovery, off by default when unmanaged, no network."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stagewatch.core.netcheck import NetCheck, age_bucket


class Bus:
    def __init__(self): self.sent = []
    def publish(self, topic, payload): self.sent.append((topic, payload))


def make(results, choice=None, default_on=True):
    t = {"now": 1000.0}
    hub = SimpleNamespace(config=SimpleNamespace(updater=SimpleNamespace(check_internet=choice)), bus=Bus())
    it = iter(results)
    nc = NetCheck(hub, clock=lambda: t["now"], fetcher=lambda url, timeout: next(it), default_on=lambda: default_on)
    return nc, hub, t


def run(nc): asyncio.run(nc.run_once())


def test_down_needs_two_failures_and_recovers_at_once():
    nc, hub, t = make([True, False, False, True])
    run(nc); assert nc.snapshot()["state"] == "up"
    run(nc); assert nc.snapshot()["state"] == "up" and nc.next_delay() == 60.0
    t["now"] += 60; run(nc); assert nc.snapshot()["state"] == "down" and nc.next_delay() == 300.0
    run(nc); assert nc.snapshot()["state"] == "up"
    assert [p["state"] for _, p in hub.bus.sent] == ["up", "down", "up"]


def test_off_means_not_checked_and_no_fetch():
    nc, _, _ = make([], choice=False)
    run(nc)
    assert nc.snapshot() == {"state": "unknown", "age": ""}
    nc, _, _ = make([], choice=None, default_on=False)
    run(nc); assert nc.state == "unknown"


def test_public_payload_has_no_host():
    nc, _, t = make([True]); run(nc); t["now"] += 700
    snap = nc.snapshot()
    assert set(snap) == {"state", "age", "for"} and snap["age"] == "within the hour"
    assert age_bucket(5) == "just now"


def test_fetcher_error_reads_as_failure():
    def boom(url, timeout): raise RuntimeError("x")
    nc, _, _ = make([]); nc.fetcher = boom
    run(nc); run(nc)
    assert nc.state == "down"
