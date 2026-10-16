"""This Raspberry Pi: reading a fake /proc and /sys tree, decoding vcgencmd, gaps not zeros, quiet
warnings, live-only markers, Equipment only, and no addresses on public screens."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.integrations import pihost
from stagewatch.integrations.pihost import DEVICE_ID, PiHostIntegration, Reader, Stats, decode_throttled, warnings_of

ROOT = Path(__file__).resolve().parent.parent


def fake_tree(tmp: Path, model="Raspberry Pi 4 Model B Rev 1.5\x00", temp="56250\n") -> Path:
    files = {
        "proc/device-tree/model": model,
        "sys/class/thermal/thermal_zone0/temp": temp,
        "proc/loadavg": "0.52 0.40 0.31 1/123 4567\n",
        "proc/meminfo": "MemTotal:        3884000 kB\nMemFree:  100 kB\nMemAvailable:    2913000 kB\n",
        "proc/uptime": "3600.25 7000.00\n",
        "proc/net/wireless": ("Inter-| sta-|   Quality        |   Discarded packets\n"
                              " face | tus | link level noise |  nwid  crypt\n"
                              " wlan0: 0000   50.  -61.  -256        0      0\n"),
    }
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp


class FakeReader(Reader):
    def __init__(self, stats: Stats) -> None:
        super().__init__(Path("/nonexistent"), None, "")
        self.next = stats

    def is_pi(self) -> bool:
        return True

    def model(self) -> str:
        return "Raspberry Pi 4 Model B"

    async def read(self) -> Stats:
        return self.next


def flags(**on) -> dict:
    out = {n: False for n in pihost.THROTTLE_BITS.values()}
    out.update(on)
    return out


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    yield h
    h.recorder.close()


def test_decode_throttled_bits():
    f = decode_throttled("throttled=0x50005\n")
    assert f["under_voltage"] and f["throttled"] and f["under_voltage_seen"] and f["throttled_seen"]
    assert not f["freq_capped"] and not f["soft_temp_limit"]
    assert decode_throttled("throttled=0x0") == flags()
    assert decode_throttled("") is None and decode_throttled("error") is None and decode_throttled("throttled=zz") is None


async def test_reader_reads_a_fake_pi(tmp_path):
    r = Reader(fake_tree(tmp_path), tmp_path, "")
    assert r.is_pi() and r.model() == "Raspberry Pi 4 Model B Rev 1.5"
    s = await r.read()
    assert s.cpu_temp == 56.25 and s.cpu_load == 0.52 and s.memory_used == 25.0 and s.uptime_s == 3600.25
    assert s.wifi_signal == -61.0 and s.wifi_name == "wlan0"
    assert s.disk_free is not None and s.throttle is None   # no vcgencmd: unknown, never "OK"


async def test_not_a_pi_does_nothing(tmp_path, hub):
    r = Reader(fake_tree(tmp_path, model="Generic x86 PC"), tmp_path, "")
    assert not r.is_pi()
    integ = PiHostIntegration(hub, reader=r)
    await integ.start()
    assert DEVICE_ID not in hub.devices and not integ.info()["active"]


async def test_unreadable_values_are_gaps_not_zeros(tmp_path):
    r = Reader(fake_tree(tmp_path, temp="garbage"), None, "")
    (tmp_path / "proc/meminfo").unlink()
    s = await r.read()
    assert s.cpu_temp is None and s.memory_used is None and s.disk_free is None


async def test_poll_records_equipment_warns_quietly_and_marks_live(hub):
    s = Stats(model="Raspberry Pi 4", cpu_temp=58.0, cpu_load=0.5, memory_used=40.0, disk_free=20.0,
              disk_share_free=0.4, uptime_s=100.0, wifi_signal=-60.0, wifi_name="wlan0",
              addresses={"eth0": ["192.0.2.20"]}, throttle=flags())
    fr = FakeReader(s)
    integ = PiHostIntegration(hub, reader=fr)
    await integ.start()
    await integ.stop()   # drive polls by hand
    await integ.poll_once()
    assert hub.devices[DEVICE_ID].status == Status.OK and hub.devices[DEVICE_ID].role == "equipment"
    ent = hub.entities[f"{DEVICE_ID}.cpu_temp"]
    assert ent.value == 58.0 and ent.locked_equipment
    pi_markers = lambda: [m.label for m in hub.recorder.markers() if m.source == "pihost"]  # noqa: E731
    assert pi_markers() == []
    assert not any(a.startswith("pihost:") for a in hub.alarms.active)

    # Under-voltage starts: a quiet warning (never sounds) and one marker.
    fr.next = Stats(**{**s.__dict__, "throttle": flags(under_voltage=True, under_voltage_seen=True)})
    await integ.poll_once()
    a = hub.alarms.active.get("pihost:under_voltage")
    assert a is not None and a.silent and a.level == 1 and "power supply" in a.message
    # It stops: the warning clears and a second marker says so.
    fr.next = Stats(**{**s.__dict__, "throttle": flags(under_voltage_seen=True)})
    await integ.poll_once()
    assert "pihost:under_voltage" not in hub.alarms.active
    assert pi_markers() == ["Raspberry Pi under-voltage started", "Raspberry Pi under-voltage stopped"]

    # Public device data: numbers and flags, never addresses or the interface name.
    pub = json.dumps(hub.devices[DEVICE_ID].to_dict())
    assert "192.0.2.20" not in pub and "wlan0" not in pub and '"pi"' in pub
    assert integ.info()["pi"]["addresses"] == {"eth0": ["192.0.2.20"]}   # admin only

    # Unknown throttle state stays unknown: no under-voltage reading is recorded as 0.
    before = hub.entities[f"{DEVICE_ID}.under_voltage"].updated
    fr.next = Stats(**{**s.__dict__, "throttle": None})
    await integ.poll_once()
    assert hub.entities[f"{DEVICE_ID}.under_voltage"].updated == before


def test_warnings_hot_disk_and_hysteresis():
    hot = Stats(cpu_temp=81.0, disk_free=0.5, disk_share_free=0.02)
    keys = {w["key"] for w in warnings_of(hot)}
    assert keys == {"hot", "disk"}
    assert {w["key"] for w in warnings_of(Stats(cpu_temp=78.0), prev_hot=True)} == {"hot"}   # clears below 77 °C only
    assert warnings_of(Stats(cpu_temp=78.0)) == []


def test_js_pi_rows_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "pihost_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
