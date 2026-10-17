"""WitMotion WT901BLE tilt sensor: the byte-stream parser, the Bluetooth reader (against a fake bleak, no
radio), and the integration through the hub (gaps, markers, Equipment, emulate).

REAL and SYNTHETIC. The 20-byte frames F1-F9 are bytes from one real capture of a WT901BLECL (device
address removed; F6 has its address bytes zeroed). Expected values are worked out by hand from the
documented scales (accel raw/32768*16 g, gyro raw/32768*2000 deg/s, angle raw/32768*180 deg), e.g. F1 ax:
bytes c5 fd = 0xfdc5 = -571 -> -571/32768*16 = -0.279 g. The fake bleak and the junk/split cases are made up.
"""

from __future__ import annotations

import asyncio
import math
import time

import pytest

from stagewatch.core.config import WitmotionSensorConfig
from stagewatch.core.hub import Hub
from stagewatch.core.model import Status
from stagewatch.integrations.witmotion import DEVICE_ID, WitmotionIntegration, parser, reader as reader_mod
from stagewatch.integrations.witmotion.emulate import EmulatedReader
from stagewatch.integrations.witmotion.smoothing import Smoother
from stagewatch.integrations.witmotion.parser import DataFrame, FrameSplitter, RegisterReply
from test_ontime import until


def hx(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


F1 = hx("55 61 c5 fd ba 06 bc 03 00 00 03 00 fd ff 5e 2b 70 0b 8c 51")
F2 = hx("55 61 66 05 29 04 31 fe 54 f5 50 f6 aa fc b3 55 06 e0 1f 9e")
F3 = hx("55 61 2e 03 78 02 4c f9 ea 05 99 03 c3 fc a2 72 62 f1 bc 7e")
F4 = hx("55 71 51 00 96 42 b5 15 79 3e 07 57 00 00 00 00 00 00 00 00")
F5 = hx("55 71 40 00 7c 0b 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
F6 = hx("55 71 64 00 9b 01 00 00 00 00 00 00 00 00 00 00 08 0d c8 00")
F7 = hx("55 71 3a 00 a4 f5 bb 14 bd d7 2a 2c c5 0b b4 52 83 0b 00 00")
F8_DATA = hx("55 61 1a fc 16 09 b4 02 b3 fd dc 02 00 0b 45 2c 65 0a 3e 54")
F9_DATA = hx("55 61 14 03 1a 04 df f9 93 05 6f 02 04 ff 09 64 6a f1 d0 74")
F9_REG = hx("55 71 51 00 0c f0 5f 17 c4 74 20 2c 00 00 00 00 00 00 00 00")


def one(data: bytes):
    out = FrameSplitter().feed(data)
    assert len(out) == 1
    return out[0]


# ----------------------------------------------------------------------------- parser
def test_data_frames_decode_to_hand_calculated_values():
    f = one(F1)
    assert (f.ax, f.ay, f.az) == pytest.approx((-0.279, 0.841, 0.467), abs=0.002)
    assert (f.gx, f.gy, f.gz) == pytest.approx((0.0, 0.183, -0.183), abs=0.005)
    assert (f.roll, f.pitch, f.yaw) == pytest.approx((60.99, 16.08, 114.68), abs=0.02)
    f = one(F2)   # raw 1382 1065 -463 / -2732 -2480 -854 / 21939 -8186 -25057
    assert (f.ax, f.ay, f.az) == pytest.approx((0.675, 0.520, -0.226), abs=0.002)
    assert (f.gx, f.gy, f.gz) == pytest.approx((-166.75, -151.37, -52.12), abs=0.01)
    assert (f.roll, f.pitch, f.yaw) == pytest.approx((120.51, -44.97, -137.64), abs=0.01)
    f = one(F3)   # yaw near +-180
    assert (f.roll, f.pitch, f.yaw) == pytest.approx((161.20, -20.56, 178.22), abs=0.01)


def test_register_replies():
    q = one(F4)
    assert isinstance(q, RegisterReply) and q.reg == 0x51
    assert parser.quaternion(q) == pytest.approx((0.52020, 0.16959, 0.48807, 0.67990), abs=1e-5)
    assert parser.temperature_c(one(F5)) == pytest.approx(29.40)             # raw 2940
    b = one(F6)
    assert b.reg == 0x64 and parser.battery_volts(b) == pytest.approx(4.11)  # raw 411, read as 10 mV units
    m = one(F7)                                                              # mag, angles, temperature (raw 2947)
    assert m.reg == 0x3A and m.values[:3] == (-2652, 5307, -10307)
    assert parser.temperature_c(m) == pytest.approx(29.47)
    # F7's angles worked out from its own bytes (11306, 3013, 21172 /32768*180)
    assert [v / 32768 * 180 for v in m.values[3:6]] == pytest.approx([62.11, 16.55, 116.30], abs=0.01)


def test_two_frames_in_one_notification():
    a = FrameSplitter().feed(F7 + F8_DATA)
    assert [type(x) for x in a] == [RegisterReply, DataFrame]
    assert (a[1].ax, a[1].ay, a[1].az) == pytest.approx((-0.487, 1.136, 0.338), abs=0.002)
    assert (a[1].gx, a[1].gy, a[1].gz) == pytest.approx((-35.95, 44.68, 171.88), abs=0.01)
    assert (a[1].roll, a[1].pitch, a[1].yaw) == pytest.approx((62.25, 14.62, 118.47), abs=0.01)
    b = FrameSplitter().feed(F9_DATA + F9_REG)
    assert [type(x) for x in b] == [DataFrame, RegisterReply] and b[1].reg == 0x51
    assert (b[0].ax, b[0].ay, b[0].az) == pytest.approx((0.385, 0.513, -0.766), abs=0.002)


def test_split_across_notifications_holds_the_partial_frame_back():
    s = FrameSplitter()
    assert s.feed(F1[:7]) == []
    got = s.feed(F1[7:])
    assert len(got) == 1 and got[0].roll == pytest.approx(60.99, abs=0.02)
    s = FrameSplitter()
    first = s.feed(F1 + F2[:10])
    assert len(first) == 1 and len(s.feed(F2[10:])) == 1          # one whole, then the second completes
    assert FrameSplitter().feed(F1[:19]) == []                    # under 20 bytes is never parsed


def test_resynchronises_after_junk_and_ignores_bad_headers():
    assert len(FrameSplitter().feed(b"\x01\x02\x03" + F1)) == 1
    bad = b"\x55\x62" + F1[2:]                                    # second byte not 61/71
    assert FrameSplitter().feed(bad) == []
    got = FrameSplitter().feed(bad + F2)
    assert len(got) == 1 and got[0].pitch == pytest.approx(-44.97, abs=0.01)
    s = FrameSplitter()
    assert s.feed(b"\x00\x55") == []                              # a lone 0x55 may be a header: kept
    assert len(s.feed(F1[1:])) == 1                               # ...and completes with the rest of F1
    s = FrameSplitter()
    assert s.feed(b"\xAA" * 500) == [] and bytes(s._buf) == b""   # endless junk does not pile up


def test_battery_percentage_is_approximate_and_unknown_when_not_understood():
    assert parser.battery_percent_approx(4.20) == 100.0
    assert parser.battery_percent_approx(3.70) == 28.0
    assert parser.battery_percent_approx(4.11) == pytest.approx(91, abs=1)
    assert parser.battery_percent_approx(3.20) == 0.0
    assert parser.battery_percent_approx(None) is None
    assert parser.battery_volts(RegisterReply(0x64, (840,) + (0,) * 7)) is None   # the vendor example's scale: not guessed
    assert parser.battery_volts(RegisterReply(0x40, (411,) + (0,) * 7)) is None   # wrong register


def test_inclination_only_when_still():
    still = DataFrame(0.0, 0.5, 0.866, 0, 0, 0, 0, 0, 0)          # 30 degrees about X
    assert parser.inclination_deg(still)[0] == pytest.approx(30.0, abs=0.1)
    assert parser.inclination_deg(DataFrame(0.0, 0.5, 3.0, 0, 0, 0, 0, 0, 0)) is None   # shaken: not 1 g


def test_read_command_bytes():
    assert parser.read_command(0x64) == bytes([0xFF, 0xAA, 0x27, 0x64, 0x00])


# ----------------------------------------------------------------------------- reader against a fake bleak
class FakeDevice:
    def __init__(self, name, address="AA:BB:CC:DD:EE:FF"):
        self.name, self.address = name, address


class FakeChar:
    def __init__(self, uuid):
        self.uuid = uuid


class FakeServices:
    def __init__(self, write=True):
        self.write = write

    def get_characteristic(self, uuid):
        if uuid == reader_mod.WRITE_UUID and not self.write:
            return None
        return FakeChar(uuid)


class FakeBleak:
    """Same few calls as bleak. It has no pair or bond call at all, so using one would fail the test."""

    def __init__(self, devices, write=True, fail_connects=0):
        self.devices, self.write, self.fail_connects = devices, write, fail_connects
        self.clients: list = []
        self.scans = 0
        outer = self

        class BleakScanner:
            @staticmethod
            async def find_device_by_filter(fn, timeout=None):
                outer.scans += 1
                await asyncio.sleep(0)
                return next((d for d in outer.devices if fn(d, None)), None)

        class BleakClient:
            def __init__(self, device, disconnected_callback=None, **kw):
                self.device, self.cb, self.kw = device, disconnected_callback, kw
                self.services = FakeServices(outer.write)
                self.notify_uuid = None
                self.handler = None
                self.writes: list[bytes] = []
                self.connected = False
                outer.clients.append(self)

            async def connect(self):
                if outer.fail_connects:
                    outer.fail_connects -= 1
                    raise OSError("connect failed")
                self.connected = True

            async def start_notify(self, char, handler):
                self.notify_uuid, self.handler = getattr(char, "uuid", char), handler

            async def write_gatt_char(self, char, data, response=False):
                self.writes.append(bytes(data))

            async def disconnect(self):
                self.connected = False

            def drop(self):   # the sensor goes away
                self.connected = False
                self.cb(self)

        self.BleakScanner, self.BleakClient = BleakScanner, BleakClient


async def fast_sleep(_s):
    await asyncio.sleep(0.005)


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(reader_mod, "FIRST_POLL_S", 0.01)
    monkeypatch.setattr(reader_mod, "BACKOFF_MIN_S", 0.01)
    monkeypatch.setattr(reader_mod, "POLL_SPACING_S", 0.0)


def make_reader(fake, address=""):
    ev: list = []
    r = reader_mod.WitmotionReader(
        address, bleak_module=fake, sleep=fast_sleep, poll_every_s=0.02,
        on_frame=lambda f, t: ev.append(("frame", f)), on_connected=lambda t: ev.append(("up",)),
        on_disconnected=lambda t: ev.append(("down",)))
    return r, ev


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_reader_finds_by_name_prefix_subscribes_to_ffe4_and_polls_battery_and_temperature(quick):
    fake = FakeBleak([FakeDevice("Other"), FakeDevice("WT901BLE67")])
    r, ev = make_reader(fake)
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: fake.clients and fake.clients[0].handler and len(fake.clients[0].writes) >= 2)
        c = fake.clients[0]
        assert c.device.name == "WT901BLE67" and c.notify_uuid == reader_mod.NOTIFY_UUID
        assert c.writes[:2] == [bytes([0xFF, 0xAA, 0x27, 0x64, 0]), bytes([0xFF, 0xAA, 0x27, 0x3A, 0])]
        assert not hasattr(c, "pair") and not hasattr(c, "create_bond")   # never pairs
        c.handler(None, bytearray(F7 + F8_DATA))                           # one 40-byte notification
        assert [e[0] for e in ev] == ["up", "frame", "frame"]
    finally:
        await stop(task)


async def test_reader_matches_a_configured_address_only(quick):
    fake = FakeBleak([FakeDevice("WT901BLE67", "11:22:33:44:55:66"), FakeDevice("WT901BLE12", "AA:BB:CC:DD:EE:FF")])
    r, _ = make_reader(fake, address="aa:bb:cc:dd:ee:ff")
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: fake.clients)
        assert fake.clients[0].device.name == "WT901BLE12"
    finally:
        await stop(task)


async def test_reader_without_write_characteristic_still_reads(quick):
    fake = FakeBleak([FakeDevice("WT901BLE67")], write=False)
    r, ev = make_reader(fake)
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: fake.clients and fake.clients[0].handler)
        fake.clients[0].handler(None, bytearray(F1))
        await until(lambda: any(e[0] == "frame" for e in ev))
        await asyncio.sleep(0.1)   # time for a poll to have happened, were there a write characteristic
        assert fake.clients[0].writes == [] and ("up",) in ev
    finally:
        await stop(task)


async def test_reader_reports_a_lost_link_and_reconnects_from_scratch(quick):
    fake = FakeBleak([FakeDevice("WT901BLE67")], fail_connects=1)
    r, ev = make_reader(fake)
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: len(fake.clients) >= 2 and fake.clients[1].handler)   # first connect failed, second worked
        fake.clients[1].handler(None, bytearray(F1[:7]))                           # a half frame, then the link drops
        fake.clients[1].drop()
        await until(lambda: ("down",) in ev and len(fake.clients) >= 3 and fake.clients[2].handler)
        assert fake.scans >= 3                                                      # scanned again each time
        fake.clients[2].handler(None, bytearray(F1[7:] + F1))
        # the old half frame is forgotten: only the whole F1 is delivered, never a frame glued from two links
        assert [e[0] for e in ev].count("frame") == 1
    finally:
        await stop(task)


async def test_reader_drops_a_link_that_goes_silent(quick, monkeypatch):
    monkeypatch.setattr(reader_mod, "SILENCE_S", 0.05)
    fake = FakeBleak([FakeDevice("WT901BLE67")])
    r, ev = make_reader(fake)
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: ("down",) in ev)   # connected, never any data: dropped by the watchdog
    finally:
        await stop(task)


async def test_missing_bleak_is_a_clear_error(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_bleak(name, *a, **k):
        if name == "bleak":
            raise ImportError("no")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_bleak)
    with pytest.raises(RuntimeError, match="bleak"):
        await reader_mod.WitmotionReader().run()


# ----------------------------------------------------------------------------- integration through the hub
class FakeReader:
    def __init__(self, address="", **cb):
        self.address, self.cb = address, cb

    async def run(self):
        await asyncio.Event().wait()


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    h.config.site.smoothing_tau_s = 0
    yield h
    h.recorder.close()


async def make(hub, enabled=True, sensors=(), axis="both", smoothing_s=0.0):
    hub.config.witmotion.enabled = enabled
    hub.config.witmotion.axis, hub.config.witmotion.smoothing_s = axis, smoothing_s
    hub.config.witmotion.sensors = [WitmotionSensorConfig(address=a, label=lb, smoothing_s=smoothing_s) for a, lb in sensors]
    holder = {}

    def factory(**kw):
        holder["r"] = FakeReader(**kw)
        holder.setdefault("all", []).append(holder["r"])
        return holder["r"]

    integ = WitmotionIntegration(hub, reader_factory=factory)
    await integ.start()
    return integ, holder


def val(hub, key):
    return hub.entities[f"{DEVICE_ID}.{key}"].value


def markers(hub):
    return [m.label for m in hub.recorder.markers() if m.source == "witmotion"]


def put3(cb, frame, t):
    """Three frames at once: the spike filter wants three real readings before it says anything."""
    for _ in range(3):
        cb["on_frame"](frame, t)


ALL = ("tilt_roll", "tilt_pitch", "accel_peak", "link_age", "battery_volts", "battery_pct", "body_temp")


async def test_off_by_default_does_nothing(hub):
    integ, _ = await make(hub, enabled=False)
    assert DEVICE_ID not in hub.devices and integ.info()["active"] is False
    await integ.stop()


async def test_readings_gap_marker_and_return(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        assert hub.devices[DEVICE_ID].role == "equipment"
        assert all(hub.entities[f"{DEVICE_ID}.{k}"].locked_equipment for k in ALL)
        cb["on_connected"](100.0)
        f = one(F1)
        for _ in range(10):                                 # 10 frames in a second are stored once
            cb["on_frame"](f, 100.5)
        cb["on_frame"](one(F6), 100.5)
        cb["on_frame"](one(F7), 100.5)
        integ.tick(101.0)
        assert val(hub, "tilt_roll") == pytest.approx(60.99, abs=0.02) and val(hub, "tilt_pitch") == pytest.approx(16.08, abs=0.02)
        assert val(hub, "accel_peak") == pytest.approx(math.sqrt(0.279 ** 2 + 0.841 ** 2 + 0.467 ** 2), abs=0.005)
        assert val(hub, "link_age") == pytest.approx(0.5)
        assert val(hub, "battery_volts") == pytest.approx(4.11)
        assert 85 < val(hub, "battery_pct") < 95
        assert val(hub, "body_temp") == pytest.approx(29.47)
        assert hub.devices[DEVICE_ID].status == Status.OK
        assert markers(hub) == []                           # nothing happened yet: no marker
        cb["on_disconnected"](110.0)                        # the sensor goes away
        assert all(val(hub, k) is None for k in ALL)
        assert hub.devices[DEVICE_ID].status == Status.MISSING
        assert markers(hub) == ["Tilt sensor link lost"]
        cb["on_connected"](150.0)
        assert markers(hub)[1].startswith("Tilt sensor back")
        assert val(hub, "tilt_roll") is None                # not back-filled, not the old number
        put3(cb, one(F2), 150.2)
        integ.tick(151.0)
        assert val(hub, "tilt_roll") == pytest.approx(120.51, abs=0.01)
    finally:
        await integ.stop()


async def test_a_silent_connected_link_becomes_a_gap(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        put3(cb, one(F1), 100.0)
        integ.tick(101.0)
        assert val(hub, "tilt_roll") is not None
        integ.tick(106.5)                                   # more than the 5 s limit with no frame
        assert val(hub, "tilt_roll") is None and markers(hub) == ["Tilt sensor link lost"]
        integ.tick(107.5)
        assert markers(hub) == ["Tilt sensor link lost"]    # no repeat
    finally:
        await integ.stop()


async def test_old_battery_and_temperature_become_a_gap(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        cb["on_frame"](one(F6), 100.0)
        for t in range(100, 300):                           # data keeps flowing but the register replies stop
            cb["on_frame"](one(F1), float(t))
            integ.tick(float(t) + 0.5)
        assert val(hub, "battery_volts") is None and val(hub, "tilt_roll") is not None
    finally:
        await integ.stop()


async def test_emulate_runs_without_hardware(hub):
    integ = WitmotionIntegration(hub, emulate=True)
    await integ.start()
    try:
        await until(lambda: val(hub, "battery_volts") is not None, timeout=5)
        await asyncio.sleep(0.5)
        integ.tick(time.time())
        assert val(hub, "tilt_roll") == pytest.approx(2.1, abs=0.3)
        assert 29 < val(hub, "body_temp") < 30
        assert readout(hub)["setup_line"] == "10 Hz · 20 Hz bandwidth · horizontal · 6-axis"
    finally:
        await integ.stop()


def test_emulated_reader_has_an_outage_cycle():
    r = EmulatedReader()
    assert not r.is_out(10.0) and r.is_out(245.0) and not r.is_out(265.0)


# ----------------------------------------------------------------------------- renamed sensors, two sensors
async def test_reader_finds_a_renamed_sensor_by_its_address(quick):
    """Crew can rename the sensor (its Device Name is writable): the address still finds it, the name does not matter."""
    fake = FakeBleak([FakeDevice("WT901BLE67", "11:22:33:44:55:66"), FakeDevice("Stage Right", "AA:BB:CC:DD:EE:01")])
    r, _ = make_reader(fake, address="AA:BB:CC:DD:EE:01")
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: fake.clients)
        assert fake.clients[0].device.name == "Stage Right"
    finally:
        await stop(task)


async def test_renamed_sensor_is_not_found_by_prefix_alone(quick):
    fake = FakeBleak([FakeDevice("Stage Right", "AA:BB:CC:DD:EE:01")])
    r, _ = make_reader(fake)
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: fake.scans >= 2)
        assert fake.clients == []
    finally:
        await stop(task)


async def test_factory_name_with_a_suffix_is_found_by_prefix(quick):
    fake = FakeBleak([FakeDevice("WT901BLE68-SR", "AA:BB:CC:DD:EE:02")])
    r, _ = make_reader(fake)
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: fake.clients)
        assert fake.clients[0].device.name == "WT901BLE68-SR"
    finally:
        await stop(task)


async def test_two_sensors_are_both_read_and_scanned_one_at_a_time(quick):
    fake = FakeBleak([FakeDevice("WT901BLE67", "11:22:33:44:55:66"), FakeDevice("Stage Right", "AA:BB:CC:DD:EE:01")])
    lock = asyncio.Lock()
    evs, tasks = [], []
    for addr in ("11:22:33:44:55:66", "AA:BB:CC:DD:EE:01"):
        ev: list = []
        evs.append(ev)
        r = reader_mod.WitmotionReader(addr, bleak_module=fake, sleep=fast_sleep, poll_every_s=0.02, scan_lock=lock,
                                       on_frame=lambda f, t, ev=ev: ev.append(f))
        tasks.append(asyncio.create_task(r.run()))
    try:
        await until(lambda: len(fake.clients) == 2 and all(c.handler for c in fake.clients))
        by_name = {c.device.name: c for c in fake.clients}
        by_name["WT901BLE67"].handler(None, bytearray(F1))
        by_name["Stage Right"].handler(None, bytearray(F2))
        assert evs[0][0].roll == pytest.approx(60.99, abs=0.02) and evs[1][0].roll == pytest.approx(120.51, abs=0.01)
    finally:
        for t in tasks:
            await stop(t)


async def test_two_sensors_in_the_hub_have_their_own_devices_labels_and_gaps(hub):
    integ, holder = await make(hub, sensors=[("11:22:33:44:55:66", "Stage Right hang"), ("AA:BB:CC:DD:EE:01", "Stage Left hang")])
    try:
        a, b = holder["all"]
        assert (a.address, b.address) == ("11:22:33:44:55:66", "AA:BB:CC:DD:EE:01")
        assert hub.devices["witmotion"].name == "Stage Right hang" and hub.devices["witmotion_2"].name == "Stage Left hang"
        a.cb["on_connected"](100.0)
        b.cb["on_connected"](100.0)
        put3(a.cb, one(F1), 100.0)
        put3(b.cb, one(F2), 100.0)
        integ.tick(100.5)
        assert hub.entities["witmotion.tilt_roll"].value == pytest.approx(60.99, abs=0.02)
        assert hub.entities["witmotion_2.tilt_roll"].value == pytest.approx(120.51, abs=0.01)
        b.cb["on_disconnected"](110.0)                      # only the second one goes
        assert hub.entities["witmotion_2.tilt_roll"].value is None and hub.entities["witmotion.tilt_roll"].value is not None
        assert markers(hub) == ["Stage Left hang link lost"]
    finally:
        await integ.stop()


# ----------------------------------------------------------------------------- smoothing (worked out by hand)
def feed(sm, values, t0, dt):
    return [sm.update(v, t0 + i * dt) for i, v in enumerate(values)]


def test_median_rejects_a_single_spike_and_needs_three_readings():
    out = feed(Smoother(0), [5, 5, 5, 50, 5, 5], 0, 0.1)
    assert out == [None, None, 5, 5, 5, 5]                  # median of the last 5: the 50 never gets through


def test_step_response_is_63_percent_after_one_time_constant_at_10_hz_and_at_2_hz():
    """Zeros, then a step to 10. The median takes the step on the 3rd new reading; the average then starts from 0
    and after exactly tau (2 s) it is 10 * (1 - e^-1) = 6.321, whatever the frame rate."""
    for dt in (0.1, 0.5):
        sm = Smoother(2.0)
        feed(sm, [0, 0, 0, 0, 0], 0, dt)                      # t = 0 .. 4 dt
        step_t = 5 * dt
        outs = [sm.update(10, step_t + i * dt) for i in range(40)]
        first = 2                                              # index where the median first says 10
        assert outs[first - 1] == pytest.approx(0.0)           # [0,0,10,10,10] not yet: median of 0,0,0,10,10 is 0
        k = round(2.0 / dt)                                    # k readings of dt = one time constant after the step
        assert outs[first + k - 1] == pytest.approx(10 * (1 - math.exp(-1)), abs=1e-6)


def test_an_angle_near_the_wrap_does_not_jump_to_zero():
    sm = Smoother(0, circular=True)
    out = feed(sm, [179, -179, 179, -179, 179], 0, 0.1)
    assert out[2] == pytest.approx(179.0, abs=1e-6)         # not 0: 179 and -179 are 2 degrees apart


def test_reset_forgets_everything():
    sm = Smoother(2.0)
    feed(sm, [10] * 10, 0, 0.1)
    sm.reset()
    assert feed(sm, [0, 0, 0], 100, 0.1) == [None, None, 0.0]   # a fresh start: nothing blended with the old 10


async def test_axis_choice_records_only_the_chosen_axis(hub):
    integ, holder = await make(hub, axis="x")
    try:
        assert f"{DEVICE_ID}.tilt_roll" in hub.entities and f"{DEVICE_ID}.tilt_pitch" not in hub.entities
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        put3(cb, one(F1), 100.0)
        integ.tick(100.5)
        assert val(hub, "tilt_roll") == pytest.approx(60.99, abs=0.02)
        pub = hub.devices[DEVICE_ID].public["witmotion"]
        assert pub["axis_text"] == "X (side to side)" and "depends on how the sensor is fixed" in pub["axis_help"]
    finally:
        await integ.stop()


async def test_smoothed_tilt_with_raw_beside_it_and_a_gap_resets_the_filter(hub):
    integ, holder = await make(hub, smoothing_s=2.0)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        f = one(F1)
        for i in range(2):
            cb["on_frame"](f, 100.0 + i * 0.1)
        integ.tick(100.2)
        assert val(hub, "tilt_roll") is None and val(hub, "tilt_roll_raw") == pytest.approx(60.99, abs=0.02)   # under 3 readings
        cb["on_frame"](f, 100.2)
        integ.tick(100.3)
        assert val(hub, "tilt_roll") == pytest.approx(60.99, abs=0.02)
        cb["on_disconnected"](110.0)
        cb["on_connected"](120.0)
        cb["on_frame"](one(F2), 120.0)
        cb["on_frame"](one(F2), 120.1)
        integ.tick(120.2)
        assert val(hub, "tilt_roll") is None                 # nothing carried across the gap
        cb["on_frame"](one(F2), 120.2)
        integ.tick(120.3)
        assert val(hub, "tilt_roll") == pytest.approx(120.51, abs=0.01)   # only new readings, not blended with 60.99
    finally:
        await integ.stop()


async def test_stale_limit_follows_the_sensors_own_rate(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        for t in (100.0, 102.0, 104.0):                       # 0.5 Hz: period 2 s, so the limit is 6 s
            cb["on_frame"](one(F1), t)
        integ.tick(109.5)
        assert val(hub, "tilt_roll") is not None              # 5.5 s of quiet is normal at this rate
        integ.tick(111.0)
        assert val(hub, "tilt_roll") is None
    finally:
        await integ.stop()


async def test_battery_reading_that_is_not_understood_says_so(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        cb["on_frame"](one(hx("55 71 64 00 ed 01 00 00 00 00 00 00 00 00 00 00 a2 00 c8 00")), 100.0)   # raw 493
        assert val(hub, "battery_volts") is None and val(hub, "battery_pct") is None
        assert hub.devices[DEVICE_ID].public["witmotion"]["battery_note"] == "Not available for this sensor model"
        cb["on_frame"](one(F6), 101.0)
        assert hub.devices[DEVICE_ID].public["witmotion"]["battery_note"] == ""
    finally:
        await integ.stop()


def test_old_config_without_the_new_fields_loads_with_defaults():
    from stagewatch.core.config import WitmotionConfig
    c = WitmotionConfig.model_validate({"enabled": True, "sensors": [{"address": "AA:BB:CC:DD:EE:01"}]})
    assert c.sensors[0].axis == "both" and c.sensors[0].smoothing_s == 2.0 and c.axis == "both"


# ----------------------------------------------------------------------------- settings read-out
# SYNTHETIC: built from WitMotion's register table (no capture of these two replies yet). Rate reply: register 0x03,
# code at index 0. Bandwidth reply: register 0x1F, bandwidth at index 0, install direction at index 4, axis mode at index 5.
RATE_10HZ = hx("55 71 03 00 06 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
RATE_2HZ = hx("55 71 03 00 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
RATE_UNSURE = hx("55 71 03 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
BW_20_HORIZ_6AXIS = hx("55 71 1f 00 04 00 00 00 00 00 00 00 00 00 01 00 00 00 00 00")
BW_10_VERT_9AXIS = hx("55 71 1f 00 05 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00")
BATT_493 = hx("55 71 64 00 ed 01 00 00 00 00 00 00 00 00 00 00 a2 00 c8 00")     # real, address zeroed: raw 493


def test_setting_codes_decode_and_unknown_codes_stay_unknown():
    assert parser.rate_text(one(RATE_10HZ)) == "10 Hz" and parser.rate_period_s(one(RATE_2HZ)) == 0.5
    assert "not confirmed" in parser.rate_text(one(RATE_UNSURE))
    assert parser.setup_texts(one(BW_20_HORIZ_6AXIS)) == {"bandwidth": "20 Hz", "orient": "horizontal", "axis_mode": "6-axis"}
    assert parser.setup_texts(one(BW_10_VERT_9AXIS)) == {"bandwidth": "10 Hz", "orient": "vertical", "axis_mode": "9-axis"}
    assert parser.setup_texts(RegisterReply(0x1F, (99, 0, 0, 0, 7, 3, 0, 0))) == {}      # codes not in the table: nothing said
    assert parser.rate_text(RegisterReply(0x03, (0x55,) + (0,) * 7)) is None


async def test_reader_reads_the_settings_once_per_connection_and_never_writes_them(quick):
    fake = FakeBleak([FakeDevice("WT901BLE67")])
    r, _ = make_reader(fake)
    task = asyncio.create_task(r.run())
    try:
        await until(lambda: fake.clients and len(fake.clients[0].writes) >= 6)
        await asyncio.sleep(0.15)                            # more polls happen; the settings are not asked again
        w = fake.clients[0].writes
        assert w[:4] == [parser.read_command(0x64), parser.read_command(0x3A), parser.read_command(0x03), parser.read_command(0x1F)]
        assert w.count(parser.read_command(0x03)) == 1
        assert all(b[:3] == bytes([0xFF, 0xAA, 0x27]) for b in w)   # reads only: no unlock, no setting written
    finally:
        await stop(task)


def readout(hub):
    return hub.devices[DEVICE_ID].public["witmotion"]


async def test_readout_shows_the_settings_and_unknown_until_answered(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        integ.tick(100.5)
        assert readout(hub)["setup_line"] == "unknown · bandwidth unknown · install direction unknown · axis mode unknown"
        cb["on_frame"](one(RATE_2HZ), 101.0)
        cb["on_frame"](one(BW_20_HORIZ_6AXIS), 101.0)
        cb["on_frame"](one(F6), 101.0)
        cb["on_frame"](one(F7), 101.0)
        integ.tick(101.5)
        r = readout(hub)
        assert r["setup_line"] == "2 Hz · 20 Hz bandwidth · horizontal · 6-axis"
        assert "battery 4.11 V (about 91 %, approximate)" in r["info_line"] and "sensor body 29.5 °C" in r["info_line"]
        assert "firmware not available" in r["info_line"] and "warning" not in r
        assert not any("setup" in k for k in hub.entities)       # information, not a recorded reading
        cb["on_disconnected"](110.0)
        cb["on_connected"](120.0)
        integ.tick(120.5)
        assert readout(hub)["setup_line"].startswith("unknown")  # read again after reconnecting, nothing carried over
    finally:
        await integ.stop()


async def test_readout_says_so_for_an_unreadable_battery_and_warns_about_a_very_slow_rate(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        cb["on_frame"](one(BATT_493), 101.0)
        cb["on_frame"](one(RATE_UNSURE), 101.0)                   # 0.1 or 0.2 Hz: a frame only every 5 to 10 s
        integ.tick(101.5)
        r = readout(hub)
        assert "battery: not available for this sensor model" in r["info_line"]
        assert "too slowly" in r["warning"] and "return rate" in r["warning"]
    finally:
        await integ.stop()


# ----------------------------------------------------------------------------- the dashboard card
def test_the_tilt_card_is_known_and_can_be_added_to_a_dashboard(tmp_path):
    from fastapi.testclient import TestClient
    from stagewatch.core import cards
    from stagewatch.web.server import create_app
    assert "witmotion_tilt" in cards.KNOWN_CARDS and "witmotion_tilt" not in cards.default_cards("tablet")
    h = Hub(tmp_path, emulate=True)
    with TestClient(create_app(h)) as c:
        assert c.post("/api/admin/setup", json={"pin": "4711"}).status_code == 200
        r = c.put("/api/admin/dashboards", json=[{"slug": "foh", "cards": ["env_tiles", "witmotion_tilt"]}])
        assert r.status_code == 200, r.text
    h.recorder.close()


async def test_limit_levels_reach_the_public_block_without_private_details(hub):
    from stagewatch.core.config import Threshold
    hub.config.thresholds = [Threshold(id="t1", entity="witmotion.tilt_roll", label="Roll", above=3.0, level=2)]
    integ, holder = await make(hub, sensors=[("AA:BB:CC:DD:EE:01", "Stage Right hang")])
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        put3(cb, one(F1), 100.0)                          # roll 60.99: above the limit
        now = time.time()
        hub.alarms.set_condition("threshold:t1", True, 2, "x", now)
        integ.tick(100.5)
        pub = readout(hub)
        assert pub["limits"] == {"tilt_roll": 2}           # only the reading that has a threshold, at its alarm level
        assert "AA:BB" not in str(hub.devices[DEVICE_ID].to_dict(now)) and "AA:BB" not in str(pub)   # no address in public
        hub.alarms.set_condition("threshold:t1", False, 2, "", now)
        integ.tick(100.6)
        assert readout(hub)["limits"] == {"tilt_roll": 0}
    finally:
        await integ.stop()


# ----------------------------------------------------------------------------- target angle (config and public block)
def test_target_config_defaults_and_old_configs():
    from stagewatch.core.config import WitmotionConfig
    c = WitmotionConfig.model_validate({"enabled": True, "sensors": [{"address": "AA:BB:CC:DD:EE:01"}]})
    s = c.sensors[0]
    assert (s.target_x, s.target_y, s.on_target_deg, s.close_deg) == (None, None, 0.2, 1.0)
    s = WitmotionConfig.model_validate({"sensors": [{"address": "x", "target_x": 2.5, "on_target_deg": 0.5, "close_deg": 0.1}]}).sensors[0]
    assert s.close_deg == 0.5                                   # "close" is never tighter than "on target"


async def test_targets_reach_the_public_block_only_for_watched_axes_that_have_one(hub):
    hub.config.witmotion.sensors = []
    hub.config.witmotion.axis, hub.config.witmotion.enabled = "x", True
    hub.config.witmotion.target_x, hub.config.witmotion.target_y = 2.0, 5.0     # Y has a target but is not watched
    integ = WitmotionIntegration(hub, reader_factory=lambda **kw: FakeReader(**kw))
    await integ.start()
    try:
        assert readout(hub)["targets"] == {"tilt_roll": {"target": 2.0, "on": 0.2, "close": 1.0}}
    finally:
        await integ.stop()


async def test_no_target_means_an_empty_targets_block_and_no_alarm_is_made(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        put3(cb, one(F1), 100.0)
        integ.tick(100.5)
        assert readout(hub)["targets"] == {} and not [k for k in hub.alarms.active if "witmotion" in k]
    finally:
        await integ.stop()
