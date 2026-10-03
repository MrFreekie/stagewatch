"""Wi-Fi signal and battery from nodes: own kinds, shown per node, never averaged."""

import re
from pathlib import Path

import pytest

from stagewatch.core.hub import Hub
from stagewatch.core.model import ENV_KINDS, Device, Entity, Kind, Status
from stagewatch.integrations.esphome.mapping import canonical_unit, sensor_kind

STATIC = Path(__file__).resolve().parents[1] / "src" / "stagewatch" / "web" / "static"


@pytest.mark.parametrize("device_class,unit,kind", [
    ("signal_strength", "dBm", Kind.SIGNAL),
    ("", "dBm", Kind.SIGNAL),             # older YAML without a device class
    ("battery", "%", Kind.BATTERY),
    ("", "%", Kind.GENERIC),              # a bare % could be anything: not guessed as battery
    ("voltage", "V", Kind.GENERIC),
    ("humidity", "%", Kind.HUMIDITY),
])
def test_kinds(device_class, unit, kind):
    assert sensor_kind(device_class, unit) == kind


def test_units_and_never_averaged():
    assert canonical_unit(Kind.SIGNAL, "dBm") == "dBm" and canonical_unit(Kind.BATTERY, "%") == "%"
    assert Kind.SIGNAL not in ENV_KINDS and Kind.BATTERY not in ENV_KINDS


def test_battery_and_signal_do_not_touch_site_values(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    h.register_device(Device("n", "n", "test", status=Status.OK))
    h.register_entity(Entity("n.temperature", "n", "t", Kind.TEMPERATURE))
    h.register_entity(Entity("n.battery", "n", "b", Kind.BATTERY))
    h.register_entity(Entity("n.wifi", "n", "w", Kind.SIGNAL))
    h.update_state("n.temperature", 20.0)
    h.update_state("n.battery", 87.0)
    h.update_state("n.wifi", -58.0)
    h.compute_site()
    assert h.entities["site.temperature"].value == pytest.approx(20.0)
    assert h.site_meta["sensors"] == {"temperature": 1, "humidity": 0, "pressure": 0}
    h.recorder.close()


def test_sensors_table_has_signal_and_battery_columns():
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert re.search(r'showSignal \? h\("th", \{ class: "num" \}, "Signal"\)', js)
    assert re.search(r'showBattery \? h\("th", \{ class: "num" \}, "Battery"\)', js)
    assert "signal_strength: { unit: \"dBm\"" in common and "battery: { unit: \"%\"" in common
    assert 'SW.signalWord = (dbm) => (dbm >= -67 ? "good" : dbm >= -75 ? "fair" : "weak");' in js
