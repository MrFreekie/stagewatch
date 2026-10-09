"""Smaart sound level: up to three values the owner chooses (normally A Slow, C Slow and the
software's own 15 minute LAeq), recorded exactly as Smaart reports them and shown on the Sound level
card.

Read-only. Stagewatch only listens to Smaart: it sends nothing to it, never starts or stops
measuring, never touches calibration, gain, phantom power, logging or alarms, and never changes a
mix. It does not work anything out: no averaging, smoothing, rounding or unit change. A value Smaart
did not give is "not available" (never zero). If Smaart goes quiet or away, that is a gap in the
record, not a value; a marker says when the readings came back.

STATUS: the real client is UNVERIFIED. It was written without the Smaart SDK, so no Smaart field
name is known and, until the SDK is read, it can connect but cannot read a value. Use ``--emulate``
to see how the card behaves. See client.py and mapping.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ...core.hub import duration_text
from ...core.model import Device, Entity, Kind, Status, UNITS
from ...core.plugin import Integration, Manifest
from ...core.spl import DEVICE_ID, METRIC_BY_KEY, SplReading
from .client import SmaartSource
from .emulate import EmulatedSplSource
from .source import SplSource

log = logging.getLogger(__name__)

STALE_AFTER_S = 10.0          # connected, but no reading for this long: a gap
TICK_S = 1.0
MARKER_MIN_GAP_S = 60.0       # a flapping link adds at most one "resumed" marker a minute
MARKER_SOURCE = "spl"

MANIFEST = Manifest(
    domain="smaart",
    name="Smaart sound level",
    version="0.1.0",
    description="Records up to three sound level values you choose from Smaart (for example A Slow, "
                "C Slow and the LAeq 15 minute figure) exactly as Smaart reports them, and shows them "
                "on the Sound level card. Read-only: it only listens to Smaart and sends nothing to it. "
                "It does no sound-level maths of its own and never averages, smooths or rounds. NOT "
                "TESTED against a real Smaart: it was written without the Smaart SDK, so the real "
                "client can connect but cannot read a value yet; the simulated source (--emulate) "
                "works. Never part of the site averages.",
    tier="experimental",
    direction="in",
    protocols=("WebSocket",),
    entity_kinds=("sound_level",),
    vendors=("Rational Acoustics (Smaart)",),
)

SourceFactory = Callable[["SmaartIntegration"], SplSource]


def seed_emulate_spl(config) -> bool:
    """Emulate mode only (called from __main__, never by the hub): a fresh emulate config whose
    Sound level settings were never saved gets them switched on, with the usual three values, and
    the Sound level card on the stock FOH and wall dashboards, so the card can be seen offline.
    Does nothing once the settings have been saved or any dashboard has the card.
    Returns True if it changed the config."""
    from ...core.config import SplConfig
    if "spl" in config.model_fields_set or any("spl_live" in d.cards for d in config.dashboards):
        return False
    config.spl = SplConfig(enabled=True)
    for d in config.dashboards:
        if d.slug in ("foh", "wall"):
            d.cards = [*d.cards[:1], "spl_live", *d.cards[1:]]
    return True


class SmaartIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, source_factory: SourceFactory | None = None,
                 clock: Callable[[], float] = time.time, stale_after_s: float = STALE_AFTER_S) -> None:
        super().__init__(hub, emulate)
        self._factory = source_factory or self._default_source
        self._clock = clock
        self._stale_after = stale_after_s
        self._source: SplSource | None = None
        self._key: tuple | None = None
        self._lock = asyncio.Lock()
        self._watchdog: asyncio.Task | None = None
        self._registered = False
        self._link_up = False
        self._link_up_at: float | None = None
        self._last_rx: float | None = None       # when the last reading arrived (clock())
        self._gap_since: float | None = None     # when the record last went quiet
        self._had_data = False                   # a reading has arrived since this source started
        self._last_marker = 0.0
        self._available: dict[str, bool] = {}    # from the latest reading: metric key -> given
        self._detail = ""

    # ------------------------------------------------------------- lifecycle
    def _default_source(self, owner: "SmaartIntegration") -> SplSource:
        if self.emulate:
            return EmulatedSplSource(self._reading, self._link)
        cfg = self.hub.config.spl
        return SmaartSource(lambda: (cfg.host, cfg.port) if cfg.host and cfg.port else None,
                            self._reading, self._link)

    async def start(self) -> None:
        await self.apply()
        self._watchdog = asyncio.create_task(self._watch(), name="smaart-watchdog")

    async def stop(self) -> None:
        task, self._watchdog = self._watchdog, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            await self._stop_source()

    async def apply(self) -> None:
        """Make the running state match the saved settings (call after they change): start, stop or
        restart the source, and keep exactly the chosen values as entities. Safe to call any time."""
        async with self._lock:
            cfg = self.hub.config.spl
            wanted = bool(cfg.enabled and (self.emulate or (cfg.host and cfg.port)))
            if not wanted:
                await self._stop_source()
                if self._registered:
                    self._registered = False
                    self.hub.remove_device(DEVICE_ID)
                return
            key = (self.emulate, cfg.host, cfg.port)
            if self._source is None or key != self._key:
                await self._stop_source()
                self._register_device()
                self._reset_link_state()
                self._source = self._factory(self)
                self._key = key
                await self._source.start()
            self._sync_entities()

    async def _stop_source(self) -> None:
        source, self._source, self._key = self._source, None, None
        if source is not None:
            try:
                await source.stop()
            except Exception:  # noqa: BLE001
                log.exception("Could not stop the sound level source")

    def _reset_link_state(self) -> None:
        self._link_up, self._link_up_at, self._last_rx = False, None, None
        self._gap_since, self._had_data, self._available, self._detail = None, False, {}, ""

    # -------------------------------------------------------------- registry
    def _register_device(self) -> None:
        self.hub.register_device(Device(
            DEVICE_ID, "Sound level (simulated)" if self.emulate else "Sound level (Smaart)", "smaart",
            "Rational Acoustics" if not self.emulate else "Stagewatch",
            "Smaart" if not self.emulate else "Simulated Smaart",
            status=Status.INITIALIZING, status_detail="Connecting", category="service"))
        self._registered = True

    def slot_entities(self) -> list[tuple[str, str]]:
        """[(entity id, metric key)] for the chosen values, in slot order."""
        return [(METRIC_BY_KEY[k].entity_id, k) for k in self.hub.config.spl.slots if k in METRIC_BY_KEY]

    def _sync_entities(self) -> None:
        """Exactly the chosen values exist as entities (their history stays when one is dropped)."""
        if not self._registered:
            return
        keep = set()
        for n, (eid, key) in enumerate(self.slot_entities(), start=1):
            m = METRIC_BY_KEY[key]
            keep.add(eid)
            self.hub.register_entity(Entity(eid, DEVICE_ID, m.name, Kind.SOUND_LEVEL, UNITS[Kind.SOUND_LEVEL], 1,
                                            labels=m.labels(n)))
        for e in [e for e in self.hub.entities.values() if e.device_id == DEVICE_ID and e.id not in keep]:
            self.hub.remove_entity(e.id)

    # -------------------------------------------------------------- callbacks
    def _blank(self, ts: float) -> None:
        """Every chosen value becomes "not available" now (a gap in the record). Writes one
        not-available record per value; the chart reads it as a break, never as zero."""
        for eid, _key in self.slot_entities():
            e = self.hub.entities.get(eid)
            if e is not None and e.value is not None:
                self.hub.update_state(eid, None, ts)

    def _link(self, up: bool, detail: str) -> None:
        if not self._registered:
            return
        now = self._clock()
        if up:
            if not self._link_up:
                self._link_up, self._link_up_at = True, now
            self._detail = detail
            if self._last_rx is None or self._gap_since is not None:
                self.hub.set_device_status(DEVICE_ID, Status.INITIALIZING, detail)
            return
        self._link_up = False
        if self._gap_since is None and self._had_data:
            self._gap_since = now
            self._blank(now)
        self.hub.set_device_status(DEVICE_ID, Status.MISSING, detail, silent_alarm=True)   # a notice, never a sound

    def _reading(self, reading: SplReading) -> None:
        if not self._registered:
            return
        now = self._clock()
        gap = self._gap_since
        self._last_rx, self._had_data = now, True
        self._available = {k: reading.values.get(k) is not None for k in METRIC_BY_KEY}
        given = 0
        slots = self.slot_entities()
        for eid, key in slots:
            v = reading.values.get(key)
            self.hub.update_state(eid, v, reading.ts)
            given += v is not None
        if gap is not None:
            self._gap_since = None
            self._marker_resumed(now - gap, reading.ts)
        if not slots or given == 0:
            self.hub.set_device_status(DEVICE_ID, Status.COMPROMISED, "Connected, but none of the chosen values are available")
        else:
            text = "Receiving values" if given == len(slots) else f"Receiving values ({given} of {len(slots)} available)"
            self.hub.set_device_status(DEVICE_ID, Status.OK, text)

    def _marker_resumed(self, gap_s: float, ts: float) -> None:
        """One marker when readings come back after a gap, added now that they are live (never
        back-filled). At most one a minute if the link is flapping."""
        if ts - self._last_marker < MARKER_MIN_GAP_S:
            return
        self._last_marker = ts
        try:
            self.hub.add_marker(f"Sound level readings resumed after a gap ({duration_text(gap_s)})", MARKER_SOURCE, ts)
        except Exception:  # noqa: BLE001 - a marker must never stop a reading
            log.exception("Could not add the sound level marker")

    # -------------------------------------------------------------- watchdog
    def tick(self, now: float | None = None) -> None:
        """Connected but silent for too long is a gap too (Smaart paused, wrong address, a field the
        program no longer sends). Called every second; public so tests can drive it."""
        if not self._registered or self._source is None or not self._link_up:
            return
        now = self._clock() if now is None else now
        last = self._last_rx if self._last_rx is not None else self._link_up_at
        if last is None or now - last <= self._stale_after:
            return
        if self._gap_since is None and self._had_data:
            self._gap_since = now
            self._blank(now)
        if self.hub.devices[DEVICE_ID].status != Status.COMPROMISED:
            self.hub.set_device_status(DEVICE_ID, Status.COMPROMISED, "Connected, but no values are arriving")

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - never stop the watchdog
                log.exception("Sound level watchdog failed")

    # ----------------------------------------------------------------- admin
    def admin_status(self) -> dict:
        """Admin page: what is running and what the latest reading carried (no addresses)."""
        device = self.hub.devices.get(DEVICE_ID) if self._registered else None
        return {"running": self._registered,
                "status": device.status.value if device else "off",
                "detail": device.status_detail if device else "",
                "version": self._source.version if self._source else "",
                "source": (self._source.label if self._source else ""),
                "verified": bool(self._source.verified) if self._source else False,
                "available": dict(self._available)}

    def info(self) -> dict:
        return {**super().info(), **self.admin_status()}
