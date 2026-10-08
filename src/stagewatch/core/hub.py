"""The hub: registry of devices/entities, state flow, derived site values,
alarms, markers, and the event bus everything else listens to."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from .. import __version__, acoustics
from ..boottime import system_boot_time
from . import sitetime
from .alarms import AlarmChange, AlarmEngine
from .barometer import BarometerService
from .bus import EventBus
from .calibration import calibration_for as _calibration_for
from .config import Calibration, ConfigStore, EntitySettings
from .derived import OUTLIER_LIMITS, Ema, robust_mean
from .model import ENV_KINDS, UNITS, Device, Entity, Kind, Marker, Status
from .plugin import Integration
from .recorder import REASON_POWER_OR_RESTART, Recorder
from .schedule import DemoDoesNotFit, ScheduleMarkers, ScheduleService
from .ontimetimer import OntimeTimerService
from .wallclock import WallClockService

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
EXIT_APPLY = 75  # updater_common.EXIT_APPLY: the launcher applies a pending update/rollback


def time_doc(site, now: float | None = None) -> dict:
    """The site's time settings at ``now``, stored with each hub run (reports for past days
    must show the zone that was in use on the day)."""
    now = time.time() if now is None else now
    try:
        utc_offset_s = sitetime.utc_offset_s(now, site)
    except Exception:  # noqa: BLE001 - never stop the hub over a time zone
        utc_offset_s = 0
    return {"tz": site.timezone, "utc_offset_s": utc_offset_s, "day_rollover": site.day_rollover}


def duration_text(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "less than a minute"
    if minutes < 120:
        return f"about {minutes} min"
    hours = round(seconds / 3600)
    return f"about {hours} h" if hours < 48 else f"about {round(seconds / 86400)} days"


def down_text(seconds: float) -> str:
    return f"down {duration_text(seconds)}"


class Hub:
    def __init__(self, data_dir: Path, emulate: bool = False,
                 boot_time_fn: Callable[[], float | None] = system_boot_time) -> None:
        self.data_dir = data_dir
        self.emulate = emulate
        self.bus = EventBus()
        self.store = ConfigStore(data_dir / "config.yaml")
        self.config = self.store.load()
        self.recorder = Recorder(data_dir / "stagewatch.sqlite3")
        unclean = self.recorder.begin_run(__version__, time_doc(self.config.site))
        self.alarms = AlarmEngine()
        self.schedule = ScheduleService(self)
        self.schedule_markers = ScheduleMarkers(self)
        self.baro = BarometerService(self)  # 3-hour tendency from one averaged sample a minute
        self.wall_clock = WallClockService(self)  # runs a clock source only while a dashboard has the card
        self.ontime_timer = OntimeTimerService(self)  # holds the shared Ontime source while a dashboard has its card
        # Active alarm id -> the marker it added, hidden again if the alarm is acknowledged.
        self._alarm_markers: dict[str, int] = {}
        self._site_before = None  # the site settings just replaced (set_site), for the rebase
        self.devices: dict[str, Device] = {}
        self.entities: dict[str, Entity] = {}
        self.integrations: dict[str, Integration] = {}
        self.site_meta: dict = {"sensors": {}, "pressure_source": "altitude",
                                "c_out_of_range": False, "c_out_of_range_bounds": []}
        self._baro_block: dict = {"state": "stale"}
        self._emas: dict[str, Ema] = {}
        self._ema_sigs: dict[str, tuple] = {}
        self._tasks: list[asyncio.Task] = []
        # Set by the updater: process exit code (75 = launcher applies a pending update) and the
        # callback __main__ installs to stop uvicorn gracefully.
        self.exit_code = 0
        self.stop_reason = "update"  # what exit code 75 means this time: "update" or "rollback"
        self.request_shutdown = None
        self.bus.subscribe("device", lambda _topic, device: self._device_meta(device))
        self._register_site_device()
        if unclean is not None:
            try:
                boot = boot_time_fn()
            except Exception:  # noqa: BLE001 - an unreadable boot time just means "unknown"
                boot = None
            if boot is not None and boot > unclean["last_seen"]:
                # The computer started after Stagewatch was last seen: a restart or power loss,
                # not a Stagewatch fault. A quiet marker only; no alarm-log entry.
                text = (f"Stagewatch was off for {duration_text(unclean['down_s'])} "
                        "(the computer restarted or lost power)")
                log.info("%s", text)
                self.recorder.set_run_stop_reason(unclean["id"], REASON_POWER_OR_RESTART)
                self.add_marker(text, "hub")
            else:
                text = f"Stagewatch restarted after an unexpected stop ({down_text(unclean['down_s'])})"
                log.warning("%s; the previous run did not stop cleanly", text)
                self.recorder.log_alarm("hub", "unclean_stop", 0, text)
                self.add_marker(text, "hub")
        elif getattr(self.recorder, "previous_clean_stop", None):
            # Stopped cleanly, then the computer started again after that: a planned restart or
            # shutdown (Linux stops the service properly on the way down; Windows usually doesn't,
            # which is the unclean path above). Same quiet marker, so history gaps are explained
            # the same way on both.
            prev = self.recorder.previous_clean_stop
            try:
                boot = boot_time_fn()
            except Exception:  # noqa: BLE001 - an unreadable boot time just means "unknown"
                boot = None
            if prev["reason"] == "stop" and boot is not None and boot > prev["stopped"]:
                text = (f"Stagewatch was off for {duration_text(prev['down_s'])} "
                        "(the computer was restarted or shut down)")
                log.info("%s", text)
                self.add_marker(text, "hub")

    # ------------------------------------------------------------ lifecycle
    def add_integration(self, integration: Integration) -> None:
        self.integrations[integration.manifest.domain] = integration

    async def start(self) -> None:
        if self.emulate:
            self._emulate_demo_day()
            self.baro.start_demo("front", time.time())
        else:
            try:
                self.baro.seed(time.time())
            except Exception:  # noqa: BLE001 - no history just means the barometer collects afresh
                log.exception("Could not read the pressure history for the barometer")
        self.check_schedule_markers()  # moments that passed while Stagewatch was off are recorded as missed (no marker)
        for integration in self.integrations.values():
            try:
                await integration.start()
            except Exception:
                log.exception("Integration %s failed to start", integration.manifest.domain)
        await self.wall_clock.start()
        await self.ontime_timer.start()
        self._tasks = [
            asyncio.create_task(self._periodic(1.0, self.tick), name="hub-tick"),
            asyncio.create_task(self._periodic(2.0, self.recorder.flush), name="hub-flush"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.ontime_timer.stop()
        await self.wall_clock.stop()
        for integration in self.integrations.values():
            try:
                await integration.stop()
            except Exception:
                log.exception("Integration %s failed to stop", integration.manifest.domain)
        self.recorder.close(self.stop_reason if self.exit_code == EXIT_APPLY else "stop")

    @staticmethod
    async def _periodic(interval: float, fn) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                fn()
            except Exception:
                log.exception("Periodic task %s failed", getattr(fn, "__name__", fn))

    # ------------------------------------------------------------ site time
    def site_time(self, now: float | None = None) -> dict:
        """The public ``time`` block: {timezone, utc_offset_s, day_rollover}."""
        now = time.time() if now is None else now
        try:
            return sitetime.time_block(self.config.site, now)
        except Exception:  # noqa: BLE001 - never break the snapshot over a time zone
            log.exception("Site time block failed")
            return {"timezone": "", "utc_offset_s": 0, "day_rollover": "06:00"}

    def schedule_warn(self) -> dict:
        """The public schedule warning steps: {minutes, flash}. Visual settings only."""
        site = self.config.site
        return {"minutes": list(site.schedule_warn_minutes), "flash_minutes": list(site.schedule_warn_flash_minutes)}

    def set_site(self, site) -> None:
        """Replace the site settings (the caller saves). A time-zone change re-bases the current
        show's schedule (``site_time_changed``); a day-rollover change that moves the current
        show to another day re-bases it too (``show_day_changed``)."""
        old = self.config.site
        old_day = self.show_info()["day"]
        self.config.site = site
        self._site_before = old
        try:
            if site.timezone != old.timezone:
                self.site_time_changed(old.timezone, site.timezone)
            else:
                new_day = self.show_info()["day"]
                if new_day != old_day:
                    self.show_day_changed(old_day, new_day)
        finally:
            self._site_before = None

    def site_time_changed(self, old_timezone: str, new_timezone: str) -> None:
        """Hook: the site's time zone changed (``config.site`` already holds the new one). The
        current show's schedule is re-based to keep its local HH:MM and its place relative to the
        show day (plan §2.1). Never raises: a failed re-base is logged and the items stay put."""
        new_site = self.config.site
        old_site = self._site_before
        if old_site is None or old_site.timezone != old_timezone:
            old_site = new_site.model_copy(update={"timezone": old_timezone})
        try:
            show = self.recorder.current_show()
            old_day = sitetime.show_day(show["started"], old_site, show.get("day")).isoformat()
            self.schedule.rebase(old_site, old_day, new_site, self.show_info()["day"])
        except Exception:  # noqa: BLE001 - never fail a settings save over the schedule
            log.exception("Could not re-base the schedule after a time zone change")
        return None

    def save_config(self) -> None:
        self.store.config = self.config
        self.store.save()
        self.bus.publish("config", None)

    # ------------------------------------------------------------- registry
    def _register_site_device(self) -> None:
        self.register_device(Device(
            SITE_DEVICE_ID, "Site average", "stagewatch", "Stagewatch", "Derived",
            status=Status.OK))
        for entity_id, (name, kind, decimals) in SITE_ENTITIES.items():
            self.register_entity(Entity(entity_id, SITE_DEVICE_ID, name, kind,
                                        UNITS.get(kind, ""), decimals, derived=True))

    def _entity_meta(self, entity: Entity) -> None:
        """Describe the entity in the history (written only when something changed)."""
        device = self.devices.get(entity.device_id)
        try:
            self.recorder.upsert_entity_meta(
                entity.id, entity.device_id, entity.kind.value, entity.unit, entity.name,
                device.name if device else "", device.area if device else "",
                {"decimals": entity.decimals, "category": getattr(device, "category", "sensor")})
        except Exception:  # noqa: BLE001 - descriptive data must never break registration
            log.exception("Could not record the description of %s", entity.id)

    def _device_meta(self, device: Device) -> None:
        for entity in list(self.entities.values()):
            if entity.device_id == device.id:
                self._entity_meta(entity)

    def register_device(self, device: Device) -> Device:
        existing = self.devices.get(device.id)
        if existing:
            existing.name, existing.manufacturer, existing.model = (
                device.name or existing.name, device.manufacturer, device.model)
            if device.area:
                existing.area = device.area
            existing.hw_id = device.hw_id  # as reported now; "" when the board gave no MAC
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

    def set_device_status(self, device_id: str, status: Status, detail: str = "",
                          silent_alarm: bool = False) -> None:
        device = self.devices.get(device_id)
        if device is None or (device.status == status and device.status_detail == detail):
            return
        device.status, device.status_detail = status, detail
        self.bus.publish("device", device)
        offline = status in (Status.MISSING, Status.FAULT)
        change = self.alarms.set_condition(
            f"device:{device_id}", offline, DEVICE_OFFLINE_LEVEL,
            f"{device.name}: {status.value}{' - ' + detail if detail else ''}", time.time(),
            silent=silent_alarm)
        if change:
            self._alarm_changed([change])

    def remove_entity(self, entity_id: str) -> None:
        """Forget an entity (emulate scenarios that take a sensor away). Its history stays."""
        self.entities.pop(entity_id, None)

    def register_entity(self, entity: Entity) -> Entity:
        existing = self.entities.get(entity.id)
        if existing:
            existing.name, existing.kind, existing.unit, existing.decimals = (
                entity.name, entity.kind, entity.unit, entity.decimals)
            existing.hw_key = entity.hw_key
            entity = existing
        else:
            self.entities[entity.id] = entity
        self._entity_meta(entity)
        self.bus.publish("entity", entity)
        return entity

    # ---------------------------------------------------------------- state
    def update_state(self, entity_id: str, raw_value: float | None,
                     ts: float | None = None) -> None:
        entity = self.entities.get(entity_id)
        if entity is None:
            return
        ts = ts if ts is not None else time.time()
        offset = self.calibration_for(entity).offset
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
                and self.calibration_for(e).include_in_average]

    def calibration_for(self, entity: Entity) -> Calibration | EntitySettings:
        """The hardware record (by ``entity.hw_key``), else the legacy entry by entity id, else
        the defaults. Read-only."""
        return _calibration_for(self.config, entity.id, entity.hw_key)

    def _ema_settings(self, kind: Kind) -> tuple:
        site = self.config.site
        sensors = []
        for e in self.entities.values():
            if e.kind == kind and not e.derived:
                cal = self.calibration_for(e)
                sensors.append((e.id, float(cal.offset), bool(cal.include_in_average)))
        return (float(site.smoothing_tau_s), bool(site.outlier_reject), tuple(sorted(sensors)))

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
                # Start the smoothing afresh when the settings behind it change (smoothing time,
                # outlier rejection, which sensors count, their offsets), so a deliberate change
                # shows at once instead of easing in over ~3x the smoothing time. Sensors going
                # stale or coming back are readings, not settings, and stay smoothed.
                sig = self._ema_settings(kind)
                if self._ema_sigs.get(kind.value) != sig:
                    self._emas.pop(kind.value, None)
                    self._ema_sigs[kind.value] = sig
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
        range_issues: list[str] = []
        if temp is not None:
            p = pressure if pressure is not None else acoustics.pressure_at_altitude_pa(site.altitude_m)
            values["site.speed_of_sound"] = acoustics.speed_of_sound(
                temp, rh if rh is not None else 50.0, p)
            # Outside the formula's tested range the value is still shown and used; dashboards
            # add a quiet "approximate" note. No alarm.
            range_issues = acoustics.speed_of_sound_range_issues(temp, p)
            if rh is not None:
                values["site.dew_point"] = acoustics.dew_point_c(temp, rh)
        try:
            self._baro_block = self.baro.update(now, pressure, temp)
        except Exception:  # noqa: BLE001 - never stop the tick over the barometer
            log.exception("Barometer update failed")
            if self._baro_block.get("state") == "ok":
                self._baro_block = {**self._baro_block, "state": "stale"}   # the block stays; it just is not live
        self.site_meta["baro"] = self._baro_block
        self.site_meta["c_out_of_range"] = bool(range_issues)
        self.site_meta["c_out_of_range_bounds"] = range_issues
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
        self.check_schedule_markers(now)

    def check_schedule_markers(self, now: float | None = None) -> list[Marker]:
        """Add any schedule markers that are due (never raises: a failure is logged)."""
        try:
            return self.schedule_markers.check(now)
        except Exception:  # noqa: BLE001 - never stop the tick over the schedule
            log.exception("Could not add markers from the schedule")
            return []

    # --------------------------------------------------------------- alarms
    def _alarm_changed(self, changes: list[AlarmChange]) -> None:
        for change in changes:
            a = change.alarm
            self.recorder.log_alarm(a.id, change.event, a.level,
                                     a.message + (" (silent)" if a.silent else ""))
            if change.event == "raise" and a.level >= 2 and not a.silent:
                marker = self.add_marker(f"ALARM: {a.message}", "alarm")
                self._alarm_markers[a.id] = marker.id
            elif change.event == "clear":
                # Cleared without an acknowledge: its marker stays visible.
                self._alarm_markers.pop(a.id, None)
        self.bus.publish("alarms", self.alarms.to_list())

    def ack_alarms(self, source: str) -> int:
        """Acknowledge every sounding alarm. The marker each one added when it raised is hidden
        (it stays in the history and reports); silent alarms are never acknowledged here and
        add no markers."""
        acked = self.alarms.ack_all()
        for a in acked:
            self.recorder.log_alarm(a.id, f"ack ({source})", a.level, a.message)
            marker_id = self._alarm_markers.pop(a.id, None)
            if marker_id is not None:
                try:
                    self.update_marker(marker_id, hidden=True, current_show_only=False)
                except Exception:  # noqa: BLE001 - an acknowledge must never fail over a marker
                    log.exception("Could not hide the marker of an acknowledged alarm")
        if acked:
            self.bus.publish("alarms", self.alarms.to_list())
        return len(acked)

    # -------------------------------------------------------------- markers
    def add_marker(self, label: str, source: str, ts: float | None = None, note: str = "") -> Marker:
        """``note`` must already be clean (recorder.clean_note)."""
        label = label.strip()[:120] or "Marker"
        marker = self.recorder.add_marker(label, source[:60], ts, note)
        self.bus.publish("marker", marker)
        return marker

    def update_marker(self, marker_id: int, *, note: str | None = None, hidden: bool | None = None,
                      current_show_only: bool = True, source: str = "") -> Marker | None:
        """Change a marker's note (already clean) and/or hide or un-hide it, and tell every open
        screen (bus ``marker_updated``). None if there is no such marker in the current show
        (any show with ``current_show_only=False``)."""
        marker = self.recorder.update_marker(marker_id, note=note, hidden=hidden,
                                             show_id=self.recorder.show_id if current_show_only else None)
        if marker is not None:
            if source:  # who changed what, never the text itself
                fields = [n for n, v in (("note", note), ("hidden", hidden)) if v is not None]
                log.info("Marker %d changed (%s) by %s", marker_id, ", ".join(fields), source)
            self.bus.publish("marker_updated", marker)
        return marker

    def marker_alarm_active(self, marker_id: int) -> bool:
        """True while the alarm that raised this marker is still active."""
        return marker_id in self._alarm_markers.values()

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

    # ------------------------------------------------------- events & shows
    def show_day(self, started: float, override: str | None) -> str:
        """A show's day ('YYYY-MM-DD', site time): the admin's pick, or derived from its start
        and the day rollover."""
        try:
            return sitetime.show_day(started, self.config.site, override).isoformat()
        except Exception:  # noqa: BLE001 - never break the snapshot over a time zone
            log.exception("Show day failed")
            return override or ""

    def show_info(self) -> dict:
        """The current show for dashboards and the admin page: the Recorder's fields, with `day`
        resolved in site time and `day_set` true when the admin picked the date."""
        show = self.recorder.current_show()
        override = show.get("day")
        return {**show, "day": self.show_day(show["started"], override), "day_set": override is not None}

    def show_days(self, now: float | None = None) -> dict:
        """Suggested dates for the admin's buttons: `today` (the show day starting a show now
        gets) and `next` (for Next day: never the current show's own day, so starting Day 2
        after midnight but before the rollover still suggests the next date)."""
        now = time.time() if now is None else now
        today = self.show_day(now, None)
        current = self.show_info()["day"]
        try:
            following = (date.fromisoformat(current) + timedelta(days=1)).isoformat()
            nxt = max(today, following)
        except ValueError:
            nxt = today
        return {"today": today, "next": nxt}

    def start_show(self, name: str, *, new_event_name: str | None = None, day: str | None = None) -> dict:
        """Next day of the current event, or (new_event_name) day one of a new event. Dashboards
        reload; markers, alarms and history follow the new show."""
        self.recorder.start_show(name, new_event_name=new_event_name, day=day)
        show = self.show_info()
        self.bus.publish("show", show)
        return show

    def update_show(self, *, name: str | None = None, day: str | None = None, set_day: bool = False) -> dict:
        """Rename the current show and/or change its day (None = derive from its start)."""
        old_day = self.show_info()["day"]
        if name is not None:
            self.recorder.rename_show(name)
        if set_day:
            self.recorder.set_show_day(day)
        show = self.show_info()
        if show["day"] != old_day:
            self.show_day_changed(old_day, show["day"])
        self.bus.publish("show", show)
        return show

    def show_day_changed(self, old_day: str, new_day: str) -> None:
        """Hook: the current show's day changed. Its schedule moves to the new day keeping local
        HH:MM (plan §2.1). Never raises: a failed re-base is logged and the items stay put."""
        site = self.config.site
        try:
            self.schedule.rebase(site, old_day, site, new_day)
        except Exception:  # noqa: BLE001 - never fail a show change over the schedule
            log.exception("Could not re-base the schedule after a show day change")
        return None

    def _emulate_demo_day(self) -> None:
        """Emulate mode: a fresh database becomes "Demo Festival", "Day 1", and a show with no
        schedule gets the demo day (relative to now), so the schedule card works offline."""
        try:
            rec = self.recorder
            if [e["name"] for e in rec.events()] == ["Event 1"] and                     [s["name"] for s in rec.shows()] == ["First show"]:
                rec.rename_event("Demo Festival")
                rec.rename_show("Day 1")
            if not self.schedule.items():
                self.schedule.load_demo()
        except DemoDoesNotFit:
            log.info("No demo schedule this time: it would cross the day rollover")
        except Exception:  # noqa: BLE001 - a demo must never stop the hub
            log.exception("Could not set up the emulate demo day")

    def rename_event(self, name: str) -> dict:
        event = self.recorder.rename_event(name)
        self.bus.publish("show", self.show_info())
        return event

    # ------------------------------------------------------------- snapshot
    def schedule_snapshot(self) -> dict:
        """{show_id, day, revision}: no items (dashboards fetch GET /api/schedule)."""
        try:
            return self.schedule.summary()
        except Exception:  # noqa: BLE001 - never break the snapshot over the schedule
            log.exception("Schedule snapshot failed")
            return {"show_id": self.recorder.show_id, "day": "", "revision": 0}

    def snapshot(self) -> dict:
        now = time.time()
        stale_after = self.config.site.stale_after_s
        return {
            "now": now,
            "site": {"name": self.config.site.name,
                     "reference_distance_m": self.config.site.reference_distance_m,
                     "stale_after_s": stale_after, **self.site_meta,
                     "time": self.site_time(now), "schedule_warn": self.schedule_warn()},
            "show": self.show_info(),
            "schedule": self.schedule_snapshot(),
            "wall_clock": self.wall_clock.snapshot(),  # None while no dashboard has the card
            "ontime_timer": self.ontime_timer.snapshot(),  # likewise
            "devices": [d.to_dict() for d in self.devices.values()],
            "entities": [e.to_dict(now, stale_after) for e in self.entities.values()],
            "markers": [m.to_dict() for m in self.recorder.markers()],
            "alarms": self.alarms.to_list(),
            "sounding": self.alarms.sounding,
        }
