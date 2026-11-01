"""Regenerate the 0.7.0 upgrade fixtures from a checkout of the v0.7.0 tag.

    git worktree add <tmp>/sw070 v0.7.0
    PYTHONPATH=<tmp>/sw070/src uv run python tests/fixtures/upgrade_0_7_0/generate.py

Writes config.yaml and stagewatch.sqlite3 next to this file, using 0.7.0's own code so they are
exactly what 0.7.0 saves.  All names, addresses (RFC 5737 / .invalid) and secrets are made up.
"""
import sys
from pathlib import Path

from stagewatch.core.config import Config, ConfigStore
from stagewatch.core.recorder import Recorder
from stagewatch.version import CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION

assert (CONFIG_SCHEMA_VERSION, DB_SCHEMA_VERSION) == (2, 4), "run against the v0.7.0 source"
OUT = Path(__file__).parent
for n in ("config.yaml", "config.yaml.bak", "stagewatch.sqlite3", "stagewatch.sqlite3-wal", "stagewatch.sqlite3-shm"):
    (OUT / n).unlink(missing_ok=True)

raw = {
    "site": {"name": "Fixture Arena", "altitude_m": 123.0, "reference_distance_m": 42.0, "stale_after_s": 90.0,
             "smoothing_tau_s": 20.0, "timezone": "Europe/London", "day_rollover": "05:30",
             "schedule_auto_markers": False},
    "admin": {"pin_hash": "pbkdf2_sha256$200000$G0X5/WUxy5qIKk6NAv9mcg==$8IPIz0/7X1ne4KYSIPdRUMkNbGtRbtNxCRwFErrG5so="},
    "mdns_name": "fixture-sw",
    "esphome_devices": [
        {"id": "fosr-a", "host": "192.0.2.11", "name": "FOH node", "area": "FOH", "noise_psk": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
         "mac": "aa:bb:cc:00:00:01", "role": "environment"},
        {"id": "stage-b", "host": "192.0.2.12", "name": "Stage left", "area": "SL", "role": "equipment", "sleep_minutes": 5},
    ],
    "esphome_ignored": ["192.0.2.99"],
    "calibrations": {"mac:aabbcc000001/temperature": {"offset": -0.4, "accuracy": 0.5, "role": "environment"}},
    "thresholds": [
        {"id": "wind-hi", "entity": "foh_wind", "label": "Wind high", "above": 12.0, "level": 3, "hold_s": 10},
        {"id": "temp-lo", "entity": "foh_temperature", "below": 2.0, "hysteresis": 0.5},
    ],
    "dashboards": [
        {"slug": "foh", "title": "FOH", "layout": "tablet", "allow_ack": True,
         "cards": ["site", "alarms", "markers", "wall_clock"], "stage": "", "clock_style": "digits",
         "card_sizes": {"wall_clock": "half"}, "show_dew_point": False},
        {"slug": "wall", "title": "Wall", "layout": "wall", "cards": ["site", "wall_clock"]},
        {"slug": "phone", "title": "Phone", "layout": "phone", "allow_marker": False},
    ],
    "osc_out": {"destinations": [{"host": "192.0.2.50", "port": 9001, "alarm_messages": True}], "rate_hz": 2.0},
    "actions": {"targets": [{"id": "qlab1", "name": "QLab", "host": "192.0.2.60", "port": 53000}]},
    "updater": {"channel": "stable"},
    "wall_clock": {"source": "ontime", "ontime_url": "http://192.0.2.70:4001", "warn_offset_s": 3.0},
    "ontime_timer": {"show_title": False},
    "barometer": {"hemisphere": "north", "rapid_fall_alarm": True, "rapid_fall_hpa_3h": 4.0},
    "alarms": {"hide_acked_min": 5, "fold_old_min": 45},
    "spl": {"enabled": True, "host": "192.0.2.80", "port": 26000,
            "chart_range": "auto", "chart_min_db": 30.0, "chart_max_db": 120.0},
    "globcon": {"host": "192.0.2.90", "port": 9091},
    "witmotion": {"enabled": True, "address": "AA:BB:CC:00:00:02", "axis": "both", "target_x": 0.0, "target_y": 1.5},
    "shelly_devices": [{"id": "shelly-1", "host": "192.0.2.100", "mac": "aabbcc000003", "name": "Dist 1", "rating_a": 32.0}],
    "power_site": {"demand_window_min": 5, "gen_size_kva": 60.0, "show_advanced": True},
    "power_points": [{"id": "pp1", "name": "Dist A", "rating_a": 63.0, "ups_type": "unknown"}],
    "contacts": {"phones_enabled": True, "reveal_pin_hash": "pbkdf2_sha256$200000$G0X5/WUxy5qIKk6NAv9mcg==$8IPIz0/7X1ne4KYSIPdRUMkNbGtRbtNxCRwFErrG5so="},
    "advice": {"drift_reference": "Aligned", "alarm": "silent", "hold_s": 90},
}
store = ConfigStore(OUT / "config.yaml")
store.config = Config.model_validate(raw)
store.save()

rec = Recorder(OUT / "stagewatch.sqlite3")
t0 = 1_700_000_000.0
rec.start_show("Fixture Show One", new_event_name="Fixture Tour", day="2023-11-14")
for i in range(40):
    rec.record_state("foh_temperature", 15.0 + i * 0.1, t0 + i * 60)
    rec.record_state("foh_wind", 3.0 + (i % 7), t0 + i * 60)
rec.flush()
rec.add_marker("Doors", "manual", t0 + 600, "fixture note")
rec.add_marker("Support on", "schedule", t0 + 1200)
rec.log_alarm("wind-hi", "raised", 3, "Wind high")
rec.log_alarm("wind-hi", "cleared", 3, "Wind high")
rec.start_show("Fixture Show Two", day="2023-11-15")
rec.add_marker("Curfew", "manual", t0 + 90000)
rec.flush()
rec.close()
print("written to", OUT, file=sys.stderr)
