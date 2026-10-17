"""Bluetooth reader for a WitMotion WT901BLE (bleak). Thin on purpose: all decoding is in parser.py.

Rules that come from the real sensor (captured 2026-10, WT901BLE67, firmware 10080.1.8):
* NEVER pair or bond. The sensor rejects it and drops the link. Only connect, discover and subscribe.
* Notifications only on ffe4. The optional write characteristic ffe9 is used only to ask for registers
  (battery 0x64, temperature 0x3A): the commands sent are built by parser.read_command and nothing else.
* One scanner at a time; a fresh service discovery on each connect; reconnect with backoff.
* A lost link is reported as lost (``on_disconnected``); nothing is replayed or filled in afterwards.

``bleak`` is imported only when a real link is started, so the rest of Stagewatch (and every test) runs
without it. Tests pass a fake ``bleak`` module with the same few calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from . import parser

log = logging.getLogger(__name__)

NOTIFY_UUID = "0000ffe4-0000-1000-8000-00805f9a34fb"
WRITE_UUID = "0000ffe9-0000-1000-8000-00805f9a34fb"
NAME_PREFIX = "WT901BLE"

SCAN_S = 10.0
CONNECT_S = 20.0
BACKOFF_MIN_S = 2.0
BACKOFF_MAX_S = 60.0
HEALTHY_S = 30.0            # a link that stayed up this long starts the backoff again
SILENCE_S = 15.0            # connected but no frame this long: drop it and reconnect (a stuck link)
POLL_EVERY_S = 45.0         # battery and temperature: 30-60 s is plenty
POLL_SPACING_S = 0.2        # between register reads (the sensor needs a moment)
POLL_REGS = (parser.REG_BATTERY, parser.REG_MAG_ANGLE_TEMP)
SETTINGS_REGS = (parser.REG_RATE, parser.REG_BANDWIDTH)   # read once after each connect, never written
TICK_S = 1.0
FIRST_POLL_S = 2.0          # not at once: the link is still settling for about 5 s after connecting


def import_bleak():
    try:
        import bleak
    except ImportError as exc:
        raise RuntimeError("The Bluetooth library 'bleak' is not installed (pip install bleak)") from exc
    return bleak


class WitmotionReader:
    def __init__(self, address: str = "", *, bleak_module=None,
                 on_frame: Callable[[object, float], None] = lambda f, t: None,
                 on_connected: Callable[[float], None] = lambda t: None,
                 on_disconnected: Callable[[float], None] = lambda t: None,
                 on_status: Callable[[str], None] = lambda s: None,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 scan_lock: asyncio.Lock | None = None,
                 poll_every_s: float = POLL_EVERY_S, scan_s: float = SCAN_S) -> None:
        self.address = address.strip().lower()
        self._bleak = bleak_module
        self.on_frame, self.on_connected, self.on_disconnected, self.on_status = (
            on_frame, on_connected, on_disconnected, on_status)
        self._sleep = sleep
        self._scan_lock = scan_lock or asyncio.Lock()   # shared by all sensors: one scan or connect at a time
        self.poll_every_s = poll_every_s
        self.scan_s = scan_s
        self.splitter = parser.FrameSplitter()
        self.last_frame: float | None = None   # time.monotonic() of the latest frame
        self._lost = asyncio.Event()

    # ------------------------------------------------------------------ scanning
    def _matches(self, device, adv=None) -> bool:
        if self.address:
            return (getattr(device, "address", "") or "").lower() == self.address
        name = getattr(device, "name", None) or getattr(adv, "local_name", None) or ""
        return name.startswith(NAME_PREFIX)

    async def _find(self):
        self.on_status("searching")
        return await self._bleak.BleakScanner.find_device_by_filter(self._matches, timeout=self.scan_s)

    # ------------------------------------------------------------------ one connection
    def _on_notify(self, _sender, data: bytearray) -> None:
        now = time.time()
        self.last_frame = time.monotonic()
        for frame in self.splitter.feed(bytes(data)):
            try:
                self.on_frame(frame, now)
            except Exception:   # a bad frame must never break the link
                log.exception("WitMotion: could not handle a frame")

    def _client(self, device):
        def lost(_client) -> None:
            self._lost.set()
        try:   # ask for a fresh service discovery on each connect (BlueZ and Windows each take their own option)
            return self._bleak.BleakClient(device, disconnected_callback=lost,
                                           bluez={"use_cached_services": False},
                                           winrt={"use_cached_services": False})
        except TypeError:
            return self._bleak.BleakClient(device, disconnected_callback=lost)

    @staticmethod
    def _char(client, uuid: str):
        """The characteristic object when the service list has it (safer than the string if a UUID is
        repeated), the UUID string otherwise."""
        try:
            return client.services.get_characteristic(uuid) or uuid
        except Exception:
            return uuid

    async def _poll(self, client, write_char, regs=POLL_REGS) -> None:
        for reg in regs:
            await client.write_gatt_char(write_char, parser.read_command(reg), response=False)
            await self._sleep(POLL_SPACING_S)

    async def _session(self) -> bool:
        """One find, connect and read cycle. True if a connection was made."""
        connected = False
        client = None
        try:
            async with self._scan_lock:
                device = await self._find()
                if device is None:
                    self.on_status("not found")
                    return False
                if not self.address:   # first-time convenience: tell the admin which sensor this was, to put in the config
                    log.info("WitMotion: found %s, address %s", getattr(device, "name", ""), getattr(device, "address", ""))
                self.on_status("connecting")
                self._lost.clear()
                self.splitter.reset()
                self.last_frame = None
                client = self._client(device)
                await asyncio.wait_for(client.connect(), CONNECT_S)
                connected = True
                await client.start_notify(self._char(client, NOTIFY_UUID), self._on_notify)
            self.on_status("connected")
            self.on_connected(time.time())
            write_char = None
            try:
                write_char = client.services.get_characteristic(WRITE_UUID)
            except Exception:
                pass
            began = time.monotonic()
            next_poll = began + FIRST_POLL_S
            first = True   # the settings are read once per connection, with the first poll
            while not self._lost.is_set():
                await self._sleep(TICK_S)
                now = time.monotonic()
                if now - (self.last_frame or began) > SILENCE_S:
                    log.warning("WitMotion: no data for %.0f s, reconnecting", SILENCE_S)
                    break
                if write_char is not None and now >= next_poll:
                    next_poll = now + self.poll_every_s
                    try:
                        await self._poll(client, write_char, POLL_REGS + (SETTINGS_REGS if first else ()))
                        first = False
                    except Exception:   # a failed read request is not a lost link
                        log.warning("WitMotion: a register request failed", exc_info=True)
        finally:
            try:
                if client is not None:
                    await asyncio.wait_for(client.disconnect(), 5)
            except Exception:
                pass
            if connected:
                self.on_status("lost")
                self.on_disconnected(time.time())
        return connected

    # ------------------------------------------------------------------ the loop
    async def run(self) -> None:
        if self._bleak is None:
            self._bleak = import_bleak()
        backoff = BACKOFF_MIN_S
        while True:
            started = time.monotonic()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:   # Bluetooth failures vary a lot between systems
                log.warning("WitMotion: Bluetooth error (%s)", type(exc).__name__)
                self.on_status("error")
            backoff = BACKOFF_MIN_S if time.monotonic() - started >= HEALTHY_S else min(backoff * 2, BACKOFF_MAX_S)
            await self._sleep(backoff)
