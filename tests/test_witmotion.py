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


async def make(hub, enabled=True, sensors=()):
    hub.config.witmotion.enabled = enabled
    hub.config.witmotion.sensors = [WitmotionSensorConfig(address=a, label=lb) for a, lb in sensors]
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
        cb["on_frame"](one(F2), 150.2)
        integ.tick(151.0)
        assert val(hub, "tilt_roll") == pytest.approx(120.51, abs=0.01)
    finally:
        await integ.stop()


async def test_a_silent_connected_link_becomes_a_gap(hub):
    integ, holder = await make(hub)
    try:
        cb = holder["r"].cb
        cb["on_connected"](100.0)
        cb["on_frame"](one(F1), 100.0)
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
        integ.tick(time.time())
        assert val(hub, "tilt_roll") == pytest.approx(2.1, abs=0.3)
        assert 29 < val(hub, "body_temp") < 30
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
        a.cb["on_frame"](one(F1), 100.0)
        b.cb["on_frame"](one(F2), 100.0)
        integ.tick(100.5)
        assert hub.entities["witmotion.tilt_roll"].value == pytest.approx(60.99, abs=0.02)
        assert hub.entities["witmotion_2.tilt_roll"].value == pytest.approx(120.51, abs=0.01)
        b.cb["on_disconnected"](110.0)                      # only the second one goes
        assert hub.entities["witmotion_2.tilt_roll"].value is None and hub.entities["witmotion.tilt_roll"].value is not None
        assert markers(hub) == ["Stage Left hang link lost"]
    finally:
        await integ.stop()
