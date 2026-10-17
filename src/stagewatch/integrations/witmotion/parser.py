"""WitMotion WT901BLE (BLE 5.0) byte-stream parser. Pure functions and one small buffer class: no
Bluetooth, no clock, no I/O, so it can be tested with hex and moved (for example into an ESP32 bridge).

The sensor sends fixed 20-byte frames, but one Bluetooth notification can hold two of them (40 bytes) and
a frame can arrive split, so the stream is cut into frames here and never assumed to be one per
notification. There is no checksum in these frames: a frame is accepted on its header and length only.

  55 61  + 9 x int16 LE   data frame: acceleration, angular rate, angles
  55 71  + reg(2) + 8 x int16 LE   register reply

Nothing here is guessed: a value that cannot be decoded is None, never a number.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

FRAME_LEN = 20
HEAD = 0x55
TYPE_DATA = 0x61
TYPE_REG = 0x71
MAX_BUFFER = 4 * FRAME_LEN      # a stream that never makes a frame is junk: do not let it grow

REG_MAG_ANGLE_TEMP = 0x3A       # mag x3, angles x3, temperature (index 6)
REG_TEMPERATURE = 0x40
REG_QUATERNION = 0x51
REG_BATTERY = 0x64

# Raw battery readings outside this range are not understood (the vendor page's own worked example
# uses a different scale), so they are "unknown", never a guessed percentage.
BATTERY_RAW_MIN = 250
BATTERY_RAW_MAX = 450


@dataclass(frozen=True)
class DataFrame:
    """One 55 61 frame, in physical units."""
    ax: float   # g
    ay: float
    az: float
    gx: float   # deg/s
    gy: float
    gz: float
    roll: float   # deg
    pitch: float
    yaw: float


@dataclass(frozen=True)
class RegisterReply:
    """One 55 71 frame: the start register and the 8 raw int16 values from it."""
    reg: int
    values: tuple[int, ...]


def read_command(reg: int) -> bytes:
    """The command that asks the sensor to send 8 registers from ``reg``: FF AA 27 <reg> 00."""
    return bytes((0xFF, 0xAA, 0x27, reg & 0xFF, 0x00))


def _decode(frame: bytes) -> DataFrame | RegisterReply:
    if frame[1] == TYPE_DATA:
        r = struct.unpack("<9h", frame[2:20])
        return DataFrame(
            r[0] / 32768 * 16, r[1] / 32768 * 16, r[2] / 32768 * 16,
            r[3] / 32768 * 2000, r[4] / 32768 * 2000, r[5] / 32768 * 2000,
            r[6] / 32768 * 180, r[7] / 32768 * 180, r[8] / 32768 * 180)
    reg = frame[2] | frame[3] << 8
    return RegisterReply(reg, struct.unpack("<8h", frame[4:20]))


class FrameSplitter:
    """Cuts a notification byte stream into frames. Feed every notification as it arrives."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        """Forget a half-received frame (call on every new connection)."""
        self._buf.clear()

    def feed(self, data: bytes) -> list[DataFrame | RegisterReply]:
        self._buf += data
        out: list[DataFrame | RegisterReply] = []
        buf = self._buf
        while buf:
            # Resynchronise: drop everything before the next 55 61 / 55 71.
            i = 0
            while i < len(buf):
                if buf[i] == HEAD and (i + 1 >= len(buf) or buf[i + 1] in (TYPE_DATA, TYPE_REG)):
                    break
                i += 1
            if i:
                del buf[:i]
            if len(buf) < FRAME_LEN:
                break   # empty, a lone 0x55, or a partial frame: hold it back
            out.append(_decode(bytes(buf[:FRAME_LEN])))
            del buf[:FRAME_LEN]
        if len(buf) > MAX_BUFFER:
            buf.clear()
        return out


def temperature_c(reply: RegisterReply) -> float | None:
    """Sensor body (chip) temperature in °C from a 0x3A or 0x40 reply; NOT the air temperature."""
    raw = None
    if reply.reg == REG_MAG_ANGLE_TEMP:
        raw = reply.values[6]
    elif reply.reg == REG_TEMPERATURE:
        raw = reply.values[0]
    if raw is None:
        return None
    t = raw / 100
    return t if -40 <= t <= 125 else None


def quaternion(reply: RegisterReply) -> tuple[float, float, float, float] | None:
    if reply.reg != REG_QUATERNION:
        return None
    return tuple(v / 32768 for v in reply.values[:4])   # type: ignore[return-value]


def battery_volts(reply: RegisterReply) -> float | None:
    """Cell voltage from a 0x64 reply. The raw value is read as units of 10 mV (411 = 4.11 V): one
    capture pair supports this, the vendor percentage table does not match the vendor app. None if the
    raw value is outside any plausible LiPo reading."""
    if reply.reg != REG_BATTERY:
        return None
    raw = reply.values[0]
    return raw / 100 if BATTERY_RAW_MIN <= raw <= BATTERY_RAW_MAX else None


# Rough single-cell LiPo, resting-ish voltage -> percent. Approximate by nature (load and temperature
# move it) and not checked against a discharge log of this sensor: always labelled approximate.
_LIPO_CURVE = ((3.30, 0.0), (3.50, 6.0), (3.60, 14.0), (3.70, 28.0), (3.80, 45.0),
               (3.90, 62.0), (4.00, 78.0), (4.10, 90.0), (4.20, 100.0))


def battery_percent_approx(volts: float | None) -> float | None:
    if volts is None:
        return None
    if volts <= _LIPO_CURVE[0][0]:
        return 0.0
    if volts >= _LIPO_CURVE[-1][0]:
        return 100.0
    for (v0, p0), (v1, p1) in zip(_LIPO_CURVE, _LIPO_CURVE[1:]):
        if v0 <= volts <= v1:
            return round(p0 + (p1 - p0) * (volts - v0) / (v1 - v0), 0)
    return None


def inclination_deg(f: DataFrame) -> tuple[float, float] | None:
    """Tilt from the gravity vector, (about X, about Y) in degrees, which does not wrap at ±180 like
    roll does. Only meaningful while the sensor is still (moving adds acceleration to gravity): None
    when the total acceleration is not close to 1 g."""
    import math
    g = math.sqrt(f.ax ** 2 + f.ay ** 2 + f.az ** 2)
    if not 0.85 <= g <= 1.15:
        return None
    return (math.degrees(math.atan2(f.ay, f.az)),
            math.degrees(math.atan2(-f.ax, math.sqrt(f.ay ** 2 + f.az ** 2))))
