"""Smaart sound level: up to three values the owner chooses (an input source and a metric each, from
Smaart's own lists), recorded exactly as Smaart reports them and shown on the Sound level card.

Read-only. Stagewatch listens to Smaart and sends it exactly four kinds of message (outbound.py: ask
whether a password is needed, ask for the input list, log in, ask for one update a second); it never
starts or stops measuring, never touches calibration, gain, phantom power, logging or alarms, and
never changes a mix. It does not work anything out: no averaging, smoothing, rounding or unit change.
A value Smaart did not give, or flagged overload, is "not available" (never zero). If Smaart goes
quiet or away, that is a gap in the record, not a value; a marker says when the readings came back.

STATUS: UNVERIFIED. The client was written from the script Smaart's own SPL web page uses, not from
Rational Acoustics' SDK, and has NOT been tested against a live Smaart (no real data message has been
seen). Use ``--emulate`` to see how the card behaves. See client.py and mapping.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ...core.hub import duration_text
from ...core import spl
from ...core.model import Device, Entity, Kind, Status, UNITS
from ...core.plugin import Integration, Manifest
from ...core.spl import DEVICE_ID, SplReading
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
    version="0.2.0",
    description="Records up to three sound level values you choose from Smaart (an input and a metric "
                "each, for example SPL A Slow, SPL C Slow and LAeq 10) exactly as Smaart reports them, "
                "and shows them on the Sound level card. Reads only Smaart's live meter figures; it does "
                "not read Smaart's history, logs or alarms. It sends Smaart four fixed messages and "
                "nothing else (is a password needed, the input list, the password, one update a second) "
                "and never changes Smaart's measurement, gain, calibration, logging, alarms or mix. It "
                "does no sound-level maths and never averages, smooths or rounds. NOT TESTED against a "
                "live Smaart: written from the script Smaart's own web page uses; the simulated source "
                "(--emulate) works. Never part of the site averages.",
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
    from ...core.config import SplConfig, SplSlot
    if "spl" in config.model_fields_set or any("spl_live" in d.cards for d in config.dashboards):
        return False
    # The simulated Smaart lists SPL A Slow, SPL C Slow, LAeq 1 and LAeq 10 (no LAeq 15), so the demo
    # uses the ones it has.
    pairs = [("", "SPL A Slow"), ("", "SPL C Slow"), ("", "LAeq 10")]
    config.spl = SplConfig(enabled=True, meters=[SplSlot(metric=m, source=s) for s, m in pairs],
                           slots=spl.legacy_keys_for(pairs))
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
        self._slot_ok: dict[str, bool] = {}      # entity id -> its latest reading was a number
        self._inputs: list[str] = []             # what Smaart lists (empty until it has told us)
        self._metrics: list[str] = []
        self._detail = ""

    # ------------------------------------------------------------- lifecycle
    def _default_source(self, owner: "SmaartIntegration") -> SplSource:
        if self.emulate:
            return EmulatedSplSource(self._reading, self._link, self._catalog)
        cfg = self.hub.config.spl
        return SmaartSource(lambda: (cfg.host, cfg.port) if cfg.host and cfg.port else None,
                            self._reading, self._link, self._catalog,
                            password_fn=lambda: self.hub.config.spl.password)

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
            key = (self.emulate, cfg.host, cfg.port, cfg.password)   # a new password logs in afresh
            if self._source is None or key != self._key:
                await self._stop_source()
                self._register_device()
                self._reset_link_state()
                self._source = self._factory(self)
                self._key = key
                self._source.set_wanted(self._wanted_sources())
                await self._source.start()
            else:
                self._source.set_wanted(self._wanted_sources())
            self._sync_entities()
            self._sync_input_name()

    async def _stop_source(self) -> None:
        source, self._source, self._key = self._source, None, None
        if source is not None:
            try:
                await source.stop()
            except Exception:  # noqa: BLE001
                log.exception("Could not stop the sound level source")

    def _reset_link_state(self) -> None:
        self._link_up, self._link_up_at, self._last_rx = False, None, None
        self._gap_since, self._had_data, self._slot_ok, self._detail = None, False, {}, ""
        self._inputs, self._metrics = [], []

    # -------------------------------------------------------------- registry
    def _register_device(self) -> None:
        self.hub.register_device(Device(
            DEVICE_ID, "Sound level (simulated)" if self.emulate else "Sound level (Smaart)", "smaart",
            "Rational Acoustics" if not self.emulate else "Stagewatch",
            "Smaart" if not self.emulate else "Simulated Smaart",
            status=Status.INITIALIZING, status_detail="Connecting", category="service"))
        self._registered = True

    def _first_input(self) -> str:
        """"The first input Smaart lists" as a name, or "" while Smaart has not told us."""
        return self._inputs[0] if self._inputs else ""

    def _resolved(self, source: str) -> str:
        return source or self._first_input()

    def _wanted_sources(self) -> list[str]:
        return [s for s, _m in self.hub.config.spl.effective_slots()]

    def _sync_input_name(self) -> None:
        """The device's input line: the one input every chosen value reads, else nothing (each value
        then carries its own input)."""
        if not self._registered:
            return
        names = {self._resolved(s) for s, _m in self.hub.config.spl.effective_slots()}
        if names == {""} or not names:
            name = self._source.input_name if self._source else ""
        else:
            name = names.pop() if len(names) == 1 else ""
        self.hub.set_device_input_name(DEVICE_ID, name)

    def slot_entities(self) -> list[tuple[str, tuple[str, str]]]:
        """[(entity id, (source, metric))] for the chosen values, in slot order."""
        pairs = self.hub.config.spl.effective_slots()
        return list(zip(spl.slot_ids(pairs), pairs))

    def _sync_entities(self) -> None:
        """Exactly the chosen values exist as entities (their history stays when one is dropped)."""
        if not self._registered:
            return
        keep = set()
        for n, (eid, (source, metric)) in enumerate(self.slot_entities(), start=1):
            keep.add(eid)
            self.hub.register_entity(Entity(eid, DEVICE_ID, metric, Kind.SOUND_LEVEL, UNITS[Kind.SOUND_LEVEL], 1,
                                            labels=spl.slot_labels(n, metric, self._resolved(source))))
        for e in [e for e in self.hub.entities.values() if e.device_id == DEVICE_ID and e.id not in keep]:
            self.hub.remove_entity(e.id)
        self._slot_ok = {k: v for k, v in self._slot_ok.items() if k in keep}

    def _catalog(self, inputs: list, metrics: list) -> None:
        """Smaart told us which inputs and metrics it has (from the source)."""
        self._inputs = [spl.clean_input_name(i) for i in inputs if isinstance(i, str)]
        self._metrics = [spl.clean_metric_name(m) for m in metrics if isinstance(m, str)]
        self._sync_entities()
        self._sync_input_name()

    # -------------------------------------------------------------- callbacks
    def _blank(self, ts: float) -> None:
        """Every chosen value becomes "not available" now (a gap in the record). Writes one
        not-available record per value; the chart reads it as a break, never as zero."""
        for eid, _pair in self.slot_entities():
            e = self.hub.entities.get(eid)
            if e is not None and e.value is not None:
                self.hub.update_state(eid, None, ts)
            self._slot_ok[eid] = False

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
        self._sync_input_name()
        slots = self.slot_entities()
        for eid, (source, metric) in slots:
            # A reading names the input it came from ("" = it does not say, so it applies to all).
            if reading.source and reading.source != self._resolved(source):
                continue
            v = reading.values.get(metric)
            self.hub.update_state(eid, v, reading.ts)
            self._slot_ok[eid] = v is not None
        if gap is not None:
            self._gap_since = None
            self._marker_resumed(now - gap, reading.ts)
        given = sum(1 for eid, _p in slots if self._slot_ok.get(eid))
        note = "" if self.emulate or (self._source is not None and self._source.verified) \
            else " (not yet tested against a live Smaart)"
        if not slots or given == 0:
            self.hub.set_device_status(DEVICE_ID, Status.COMPROMISED,
                                       "Connected, but none of the chosen values are available" + note)
        else:
            text = "Receiving values" if given == len(slots) else f"Receiving values ({given} of {len(slots)} available)"
            self.hub.set_device_status(DEVICE_ID, Status.OK, text + note)

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
        known = bool(self._source and self._source.inputs)
        slots = []
        for eid, (source, metric) in (self.slot_entities() if self._registered else []):
            slots.append({
                "entity": eid, "source": source, "metric": metric,
                "source_listed": (self._resolved(source) in self._inputs) if known else None,
                "metric_listed": (metric in self._metrics) if self._metrics else None,
                "available": bool(self._slot_ok.get(eid)),
            })
        return {"running": self._registered,
                "status": device.status.value if device else "off",
                "detail": device.status_detail if device else "",
                "version": self._source.version if self._source else "",
                "input_name": device.input_name if device else "",
                "source": (self._source.label if self._source else ""),
                "verified": bool(self._source.verified) if self._source else False,
                # What Smaart lists (its own text), for the drop-downs; empty until it has told us.
                "inputs": list(self._inputs), "metrics": list(self._metrics),
                "problem": self._source.problem if self._source else "",
                "slots": slots}

    def info(self) -> dict:
        return {**super().info(), **self.admin_status()}
