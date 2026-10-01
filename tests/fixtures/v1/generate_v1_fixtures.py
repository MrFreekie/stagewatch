"""Regenerate the v1 (Stagewatch 0.2.0) data fixtures in this folder.

Run it with the *v0.2.0 code*, never with newer code, so the files are exactly what a 0.2.0
install leaves on disk:

    git worktree add --detach <tmp> v0.2.0
    uv run --no-sync python tests/fixtures/v1/generate_v1_fixtures.py --src <tmp>/src --out tests/fixtures/v1
    git worktree remove <tmp>

Writes config.yaml and stagewatch.sqlite3 (WAL folded in, journal_mode=delete). All values are
made up: documentation IP addresses, a throw-away random encryption key, and the PIN 1234.
After the 0.2.0 code has saved the config, a legacy ESPHome ``password:`` key is added to one
device, as configs written before passwords were removed still have it.
"""

from __future__ import annotations

import argparse
import base64
import sqlite3
import sys
from pathlib import Path

BASE_TS = 1790251200.0  # 2026-09-24 12:00:00 UTC; fixed so the fixture is reproducible
LEGACY_PASSWORD = "fixture-legacy-api-password"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True, help="src/ folder of a v0.2.0 checkout")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    sys.path.insert(0, str(args.src.resolve()))

    import time

    clock = [BASE_TS]
    time.time = lambda: clock[0]  # every timestamp the 0.2.0 code writes comes from this clock

    import yaml

    from stagewatch import version
    from stagewatch.core.config import (Dashboard, EntitySettings, EsphomeDeviceConfig, OscDestination,
                                        Threshold)
    from stagewatch.core.hub import Hub
    from stagewatch.core.model import Device, Entity, Kind, Status
    from stagewatch.web.auth import hash_pin

    assert version.CONFIG_SCHEMA_VERSION == 1 and version.DB_SCHEMA_VERSION == 1, "run with the v0.2.0 code"
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for name in ("config.yaml", "stagewatch.sqlite3", "stagewatch.sqlite3-wal", "stagewatch.sqlite3-shm",
                 "config.yaml.bak", "secret.key"):
        (out / name).unlink(missing_ok=True)

    hub = Hub(out)
    cfg = hub.config
    cfg.site.name = "Fixture Arena"
    cfg.site.altitude_m = 35
    cfg.site.reference_distance_m = 45
    cfg.admin.pin_hash = hash_pin("1234")
    psk = base64.b64encode(bytes(range(32))).decode()  # throw-away fixture key
    cfg.esphome_devices = [
        EsphomeDeviceConfig(id="foh", host="foh-node.local", name="FOH", area="FOH", noise_psk=psk),
        EsphomeDeviceConfig(id="stage_l", host="192.0.2.11", name="Stage left", area="Stage"),
        EsphomeDeviceConfig(id="stage_r", host="192.0.2.12", port=6053, name="Stage right", area="Stage"),
    ]
    cfg.entities = {
        "foh.temperature": EntitySettings(offset=-0.4),
        "stage_l.humidity": EntitySettings(offset=2.5, include_in_average=False),
    }
    cfg.thresholds = [
        Threshold(id="foh_hot", entity="foh.temperature", label="FOH hot", above=30.0, level=2,
                  hysteresis=0.5, hold_s=10),
        Threshold(id="site_damp", entity="site.humidity", label="Damp", above=85.0, level=1),
    ]
    cfg.dashboards = [
        Dashboard(slug="foh", title="FOH", layout="tablet", allow_marker=True, allow_ack=True),
        Dashboard(slug="monitors", title="Monitor world", layout="phone", allow_marker=True),
        Dashboard(slug="lobby", title="Lobby screen", layout="wall", allow_marker=False),
    ]
    cfg.osc_out.destinations = [OscDestination(host="192.0.2.50", port=9000)]
    cfg.updater.channel = "stable"
    hub.save_config()

    for dev_id, name, area in (("foh", "FOH", "FOH"), ("stage_l", "Stage left", "Stage"),
                               ("stage_r", "Stage right", "Stage")):
        hub.register_device(Device(dev_id, name, "esphome", "ESPHome", "BME280", area, Status.OK))
        for kind in (Kind.TEMPERATURE, Kind.HUMIDITY, Kind.PRESSURE):
            hub.register_entity(Entity(f"{dev_id}.{kind.value}", dev_id, kind.value.title(), kind,
                                       {"temperature": "°C", "humidity": "%", "pressure": "Pa"}[kind.value]))

    def record(minutes: int, temp0: float) -> None:
        for i in range(minutes * 6):  # one sample per device and kind every 10 s
            clock[0] += 10
            for n, dev_id in enumerate(("foh", "stage_l", "stage_r")):
                hub.update_state(f"{dev_id}.temperature", temp0 + n * 0.3 + i * 0.01, clock[0])
                hub.update_state(f"{dev_id}.humidity", 55.0 - i * 0.02 + n, clock[0])
                hub.update_state(f"{dev_id}.pressure", 101200.0 + n * 10 - i * 0.1, clock[0])
            hub.compute_site(clock[0])
            hub.recorder.flush()

    # Show 1 ("First show", created by the 0.2.0 recorder itself): 20 min, markers and alarms
    record(10, 21.0)
    hub.add_marker("Line check", "admin", clock[0])
    hub.set_device_status("stage_r", Status.MISSING, "connection lost")
    record(2, 21.5)
    hub.set_device_status("stage_r", Status.OK)
    hub.add_marker("Doors", "dashboard:foh", clock[0])
    record(8, 22.0)

    # Show 2: an alarm raised by a threshold, acked from a dashboard, then cleared
    clock[0] += 3600
    hub.recorder.start_show("Day 2")
    record(5, 24.0)
    hub.add_marker("Headliner on", "dashboard:foh", clock[0])
    for value in (31.0, 31.0):
        hub.update_state("foh.temperature", value, clock[0])
        changes = hub.alarms.evaluate(cfg.thresholds, hub.lookup, clock[0])
        if changes:
            hub._alarm_changed(changes)
        clock[0] += 11
    hub.ack_alarms("dashboard:foh")
    hub.update_state("foh.temperature", 25.0, clock[0])
    changes = hub.alarms.evaluate(cfg.thresholds, hub.lookup, clock[0])
    if changes:
        hub._alarm_changed(changes)
    record(5, 25.0)
    hub.recorder.close()

    # Fold the WAL into the main file so the fixture is a single, self-contained file.
    db = sqlite3.connect(str(out / "stagewatch.sqlite3"))
    db.execute("PRAGMA journal_mode=DELETE")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    db.execute("VACUUM")
    db.close()

    # Legacy ESPHome API password (removed before 0.2.0 shipped, still present in older configs).
    path = out / "config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for dev in raw["esphome_devices"]:
        if dev["id"] == "stage_l":
            dev["password"] = LEGACY_PASSWORD
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    for name in ("config.yaml.bak", "secret.key", "logs"):
        p = out / name
        if p.is_file():
            p.unlink()
    print(f"wrote {path} and {out / 'stagewatch.sqlite3'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
