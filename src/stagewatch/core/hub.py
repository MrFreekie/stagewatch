"""The hub: registry of devices/entities, state flow, derived site values,
alarms, markers, and the event bus everything else listens to."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .. import acoustics
from .alarms import AlarmChange, AlarmEngine
from .bus import EventBus
from .config import ConfigStore
from .derived import OUTLIER_LIMITS, Ema, robust_mean
from .model import ENV_KINDS, UNITS, Device, Entity, Kind, Marker, Status
from .plugin import Integration
from .recorder import Recorder

log = logging.getLogger(__name__)

SITE_DEVICE_ID = "site"
SITE_ENTITIES = {
    "site.temperature": ("Temperature", Kind.TEMPERATURE, 1),
    "site.humidity": ("Humidity", Kind.HUMIDITY, 0),
    "site.pressure": ("Pressure", Kind.PRESSURE, 0),
    "site.speed_of_sound": ("Speed of sound", Kind.SPEED_OF_SOUND, 2),
    "site.dew_point": ("Dew point", Kind.DEW_POINT, 1),
}
DEVICE_OFFLINE_LEVEL = 1


class Hub:
    def __init__(self, data_dir: Path, emulate: bool = False) -> None:
        self.data_dir = data_dir
        self.emulate = emulate
        self.bus = EventBus()
        self.store = ConfigStore(data_dir / "config.yaml")
        self.config = self.store.load()
        self.recorder = Recorder(data_dir / "stagewatch.sqlite3")
        self.alarms = AlarmEngine()
        self.devices: dict[str, Device] = {}
        self.entities: dict[str, Entity] = {}
        self.integrations: dict[str, Integration] = {}
        self.site_meta: dict = {"sensors": {}, "pressure_source": "altitude"}
        self._emas: dict[str, Ema] = {}
        self._tasks: list[asyncio.Task] = []
        self._register_site_device()

    # ------------------------------------------------------------ lifecycle
    def add_integration(self, integration: Integration) -> None:
        self.integrations[integration.manifest.domain] = integration

    async def start(self) -> None:
        for integration in self.integrations.values():
            try:
                await integration.start()
            except Exception:
                log.exception("Integration %s failed to start", integration.manifest.domain)
        self._tasks = [
            asyncio.create_task(self._periodic(1.0, self.tick), name="hub-tick"),
            asyncio.create_task(self._periodic(2.0, self.recorder.flush), name="hub-flush"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for integration in self.integrations.values():
            try:
                await integration.stop()
            except Exception:
                log.exception("Integration %s failed to stop", integration.manifest.domain)
        self.recorder.close()

    @staticmethod
    async def _periodic(interval: float, fn) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                fn()
            except Exception:
                log.exception("Periodic task %s failed", getattr(fn, "__name__", fn))

    def save_config(self) -> None:
        self.store.config = self.config
        self.store.save()
        self.bus.publish("config", None)

    # ------------------------------------------------------------- registry
    def _register_site_device(self) -> None:
        self.devices[SITE_DEVICE_ID] = Device(
            SITE_DEVICE_ID, "Site average", "stagewatch", "Stagewatch", "Derived",
            status=Status.OK)
        for entity_id, (name, kind, decimals) in SITE_ENTITIES.items():
            self.entities[entity_id] = Entity(entity_id, SITE_DEVICE_ID, name, kind,
                                              UNITS.get(kind, ""), decimals, derived=True)

    def register_device(self, device: Device) -> Device:
        existing = self.devices.get(device.id)
        if existing:
            existing.name, existing.manufacturer, existing.model = (
                device.name or existing.name, device.manufacturer, device.model)
            if device.area:
                existing.area = device.area
            device = existing
        else:
            self.devices[device.id] = device
        self.bus.publish("device", device)
        return device

    def remove_device(self, device_id: str) -> None:
        self.devices.pop(device_id, None)
        for entity_id in [e.id for e in self.entities.values() if e.device_id == device_id]:
            del self.entities[entity_id]
        change = self.alarms.set_condition(f"device:{device_id}", False, 0, "", time.time())
        if change:
            self._alarm_changed([change])
        self.bus.publish("device_removed", device_id)

    def set_device_status(self, device_id: str, status: Status, detail: str = "") -> None:
        device = self.devices.get(device_id)
        if device is None or (device.status == status and device.status_detail == detail):
            return
        device.status, device.status_detail = status, detail
        self.bus.publish("device", device)
        offline = status in (Status.MISSING, Status.FAULT)
        change = self.alarms.set_condition(
            f"device:{device_id}", offline, DEVICE_OFFLINE_LEVEL,
            f"{device.name}: {status.value}{' - ' + detail if detail else ''}", time.time())
        if change:
            self._alarm_changed([change])

    def register_entity(self, entity: Entity) -> Entity:
        existing = self.entities.get(entity.id)
        if existing:
            existing.name, existing.kind, existing.unit, existing.decimals = (
                entity.name, entity.kind, entity.unit, entity.decimals)
            entity = existing
        else:
            self.entities[entity.id] = entity
        self.bus.publish("entity", entity)
        return entity

    # ---------------------------------------------------------------- state
    def update_state(self, entity_id: str, raw_value: float | None,
                     ts: float | None = None) -> None:
        entity = self.entities.get(entity_id)
        if entity is None:
            return
        ts = ts if ts is not None else time.time()
        offset = self.config.entity_settings(entity_id).offset
        entity.raw_value = raw_value
        entity.value = None if raw_value is None else raw_value + offset
        entity.updated = ts
        self.recorder.record_state(entity_id, entity.value, ts)
        self.bus.publish("state", entity)

    def lookup(self, entity_id: str) -> tuple[float | None, bool]:
        entity = self.entities.get(entity_id)
        if entity is None:
            return None, True
        return entity.value, entity.is_stale(time.time(), self.config.site.stale_after_s)

    # -------------------------------------------------------------- derived
    def _env_inputs(self, kind: Kind, now: float) -> list[Entity]:
        stale_after = self.config.site.stale_after_s
        return [e for e in self.entities.values()
                if e.kind == kind and not e.derived and e.value is not None
                and not e.is_stale(now, stale_after)
                and self.config.entity_settings(e.id).include_in_average]

    def compute_site(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        site = self.config.site
        averages: dict[Kind, float | None] = {}
        counts: dict[str, int] = {}
        for kind in ENV_KINDS:
            inputs = self._env_inputs(kind, now)
            limit = OUTLIER_LIMITS.get(kind.value) if site.outlier_reject else None
            mean, used = robust_mean([e.value for e in inputs], limit)
            counts[kind.value] = used
            if mean is not None:
                ema = self._emas.setdefault(kind.value, Ema(site.smoothing_tau_s))
                ema.tau_s = site.smoothing_tau_s
                mean = ema.update(mean, now)
            averages[kind] = mean

        temp = averages[Kind.TEMPERATURE]
        rh = averages[Kind.HUMIDITY]
        pressure = averages[Kind.PRESSURE]
        self.site_meta = {
            "sensors": counts,
            "pressure_source": "measured" if pressure is not None else "altitude",
            "humidity_source": "measured" if rh is not None else "assumed 50%",
        }
        values: dict[str, float | None] = {
            "site.temperature": temp,
            "site.humidity": rh,
            "site.pressure": pressure,
            "site.speed_of_sound": None,
            "site.dew_point": None,
        }
        if temp is not None:
            p = pressure if pressure is not None else acoustics.pressure_at_altitude_pa(site.altitude_m)
            values["site.speed_of_sound"] = acoustics.speed_of_sound(
                temp, rh if rh is not None else 50.0, p)
            if rh is not None:
                values["site.dew_point"] = acoustics.dew_point_c(temp, rh)
        for entity_id, value in values.items():
            entity = self.entities[entity_id]
            entity.value = entity.raw_value = value
            if value is not None:
                entity.updated = now
                self.recorder.record_state(entity_id, value, now)
            self.bus.publish("state", entity)

    def tick(self) -> None:
        now = time.time()
        self.compute_site(now)
        changes = self.alarms.evaluate(self.config.thresholds, self.lookup, now)
        if changes:
            self._alarm_changed(changes)

    # --------------------------------------------------------------- alarms
    def _alarm_changed(self, changes: list[AlarmChange]) -> None:
        for change in changes:
            a = change.alarm
            self.recorder.log_alarm(a.id, change.event, a.level, a.message)
            if change.event == "raise" and a.level >= 2:
                self.add_marker(f"ALARM: {a.message}", "alarm")
        self.bus.publish("alarms", self.alarms.to_list())

    def ack_alarms(self, source: str) -> int:
        acked = self.alarms.ack_all()
        for a in acked:
            self.recorder.log_alarm(a.id, f"ack ({source})", a.level, a.message)
        if acked:
            self.bus.publish("alarms", self.alarms.to_list())
        return len(acked)

    # -------------------------------------------------------------- markers
    def add_marker(self, label: str, source: str, ts: float | None = None) -> Marker:
        label = label.strip()[:120] or "Marker"
        marker = self.recorder.add_marker(label, source[:60], ts)
        self.bus.publish("marker", marker)
        return marker

    def delete_marker(self, marker_id: int) -> bool:
        ok = self.recorder.delete_marker(marker_id)
        if ok:
            self.bus.publish("marker_deleted", marker_id)
        return ok

    def marker_delta(self, marker_id: int) -> dict | None:
        """Site values at the marker vs now, plus the change in travel time
        over the reference distance -- the number that matters for
        sub/tops/delay alignment drift."""
        marker = self.recorder.marker(marker_id)
        if marker is None:
            return None
        rows = {}
        for entity_id in SITE_ENTITIES:
            then = self.recorder.value_at(entity_id, marker.ts)
            now_v = self.entities[entity_id].value
            rows[entity_id] = {
                "then": then, "now": now_v,
                "delta": None if then is None or now_v is None else now_v - then,
            }
        distance = self.config.site.reference_distance_m
        c_then = rows["site.speed_of_sound"]["then"]
        c_now = rows["site.speed_of_sound"]["now"]
        delta_ms = None
        if c_then and c_now:
            delta_ms = (acoustics.travel_time_ms(distance, c_now)
                        - acoustics.travel_time_ms(distance, c_then))
        return {"marker": marker.to_dict(), "values": rows,
                "reference_distance_m": distance, "delta_travel_ms": delta_ms}

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        now = time.time()
        stale_after = self.config.site.stale_after_s
        return {
            "now": now,
            "site": {"name": self.config.site.name,
                     "reference_distance_m": self.config.site.reference_distance_m,
                     "stale_after_s": stale_after, **self.site_meta},
            "show": self.recorder.current_show(),
            "devices": [d.to_dict() for d in self.devices.values()],
            "entities": [e.to_dict(now, stale_after) for e in self.entities.values()],
            "markers": [m.to_dict() for m in self.recorder.markers()],
            "alarms": self.alarms.to_list(),
            "sounding": self.alarms.sounding,
        }
