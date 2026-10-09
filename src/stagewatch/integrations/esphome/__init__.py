"""ESPHome integration: discover nodes over mDNS, adopt them, and stream
their sensor states over the ESPHome native API (read-only).

Discovery only lists nodes; nothing connects until an admin adopts a node,
so a neighbouring production's ESPHome gear on a shared network is never
pulled in by accident.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

from aioesphomeapi import (
    APIClient,
    BinarySensorInfo,
    BinarySensorState,
    InvalidAuthAPIError,
    InvalidEncryptionKeyAPIError,
    ReconnectLogic,
    RequiresEncryptionAPIError,
    SensorInfo,
    SensorState,
)
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from ... import __version__
from ...core.calibration import (
    DETAIL_KNOWN_BOARD,
    check_identity,
    copy_node,
    drop_legacy,
    drop_node,
    known_board_needs_check,
    move_legacy,
    node_key,
    normalise_mac,
    records_of,
    sensor_key,
)
from ...core.config import MAX_IGNORED, EsphomeDeviceConfig
from ...core.model import Device, Entity, Kind, Status, slugify
from ...core.plugin import Integration, Manifest
from .emulate import EmulatedNode
from ...core.statustext import describe_connect_error
from .mapping import canonical_unit, sensor_kind, to_canonical

log = logging.getLogger(__name__)

SERVICE_TYPE = "_esphomelib._tcp.local."

MANIFEST = Manifest(
    domain="esphome",
    name="ESPHome",
    version="0.1.0",
    description="DIY ESP32/ESP8266 nodes running ESPHome: temperature, humidity, "
                "pressure (BME280, SHT4x, ...) plus generic sensors and contacts, and each "
                "board's MAC address (so calibration follows the hardware). Read-only: "
                "nothing is ever written to a node.",
    tier="experimental",
    direction="in",
    protocols=("ESPHome native API", "mDNS"),
    entity_kinds=("temperature", "humidity", "pressure", "contact", "battery", "signal_strength", "generic"),
    vendors=("ESPHome",),
)


class _NodeConnection:
    """One adopted node: a reconnecting API client feeding the hub."""

    def __init__(self, hub, cfg: EsphomeDeviceConfig, zc: AsyncZeroconf | None) -> None:
        self.hub = hub
        self.cfg = cfg
        self.zc = zc
        self._keys: dict[int, tuple[str, Kind, str]] = {}
        # Why the board was refused at its last connect, for the admin page only (it holds MACs
        # and other device ids, which never go into public status or alarm text):
        # {"reason": "different"|"duplicate"|"unreadable"|"known_board", "expected", "found", "other"}
        self.conflict: dict | None = None
        self._last_error = ""   # last connection-problem phrase logged, so the log gets one line per change
        self.client = APIClient(
            cfg.host, cfg.port, None,
            client_info=f"Stagewatch {__version__}",
            zeroconf_instance=zc,
            noise_psk=cfg.noise_psk or None,
        )
        mdns_name = cfg.host[:-len(".local")] if cfg.host.endswith(".local") else None
        self.logic = ReconnectLogic(
            client=self.client,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_connect_error=self._on_connect_error,
            zeroconf_instance=zc,
            name=mdns_name,
        )

    async def start(self) -> None:
        self.hub.register_device(Device(
            self.cfg.id, self.cfg.name or self.cfg.host, MANIFEST.domain,
            "ESPHome", "", self.cfg.area, Status.INITIALIZING, role=self.cfg.role))
        await self.logic.start()

    async def stop(self) -> None:
        await self.logic.stop()
        await self.client.disconnect()

    async def _on_connect(self) -> None:
        info, entities, _services = await self.client.device_info_and_list_entities()
        self._keys.clear()  # nothing is accepted until the board's identity checks out
        identity = check_identity(
            self.cfg.id, self.cfg.mac, info.mac_address,
            [(c.id, c.mac) for c in self.hub.config.esphome_devices])
        reason, detail = identity.reason, identity.detail
        if identity.action == "first" and known_board_needs_check(
                self.hub.config, self.cfg.id, node_key(identity.mac)):
            # A board connecting for the first time under this name already has calibration
            # records (e.g. re-adopted under a new name). Don't apply them silently: hold it
            # until the admin chooses "use the records" or "start fresh".
            reason, detail = "known_board", DETAIL_KNOWN_BOARD
        if reason:
            # Wrong, duplicate or unchecked board: register nothing, subscribe to nothing, and
            # keep what it reported (admin only) so the admin can resolve it. Public text has
            # no MAC and no other device id.
            self.conflict = {"reason": reason, "expected": identity.expected,
                             "found": identity.mac, "other": identity.other_id}
            self.hub.set_device_status(self.cfg.id, Status.FAULT, detail)
            log.warning("ESPHome %s: %s; its readings are ignored until an admin resolves it",
                        self.cfg.id, detail)
            return
        self.conflict = None
        changed = False
        if identity.action == "first":
            self.cfg.mac = identity.mac
            changed = True
            log.info("ESPHome %s: recorded its hardware address", self.cfg.id)
        elif identity.action == "no_mac":
            log.warning("ESPHome %s did not report a MAC address; calibration stays keyed by "
                        "device id", self.cfg.id)
        node = node_key(identity.mac) if identity.mac else ""
        if node and move_legacy(self.hub.config, self.cfg.id, node):
            changed = True
        if changed:
            try:
                self.hub.save_config()  # once per connect, only when something changed
            except OSError:
                log.exception("ESPHome %s: could not save the hardware address", self.cfg.id)
        self.hub.register_device(Device(
            self.cfg.id, self.cfg.name or info.friendly_name or info.name, MANIFEST.domain,
            info.manufacturer or "ESPHome", info.model or info.project_name or "",
            self.cfg.area, hw_id=node, role=self.cfg.role))
        for e in entities:
            object_id = slugify(e.object_id or e.name or str(e.key))
            entity_id = f"{self.cfg.id}.{object_id}"
            hw_key = sensor_key(node, object_id) if node else ""
            if isinstance(e, SensorInfo):
                kind = sensor_kind(e.device_class, e.unit_of_measurement)
                unit = e.unit_of_measurement
                self._keys[e.key] = (entity_id, kind, unit)
                self.hub.register_entity(Entity(
                    entity_id, self.cfg.id, e.name or object_id, kind,
                    canonical_unit(kind, unit),
                    decimals=0 if kind == Kind.PRESSURE else max(0, min(e.accuracy_decimals, 3)),
                    hw_key=hw_key))
            elif isinstance(e, BinarySensorInfo):
                self._keys[e.key] = (entity_id, Kind.CONTACT, "")
                self.hub.register_entity(Entity(
                    entity_id, self.cfg.id, e.name or object_id, Kind.CONTACT, "", 0,
                    hw_key=hw_key))
        self.client.subscribe_states(self._on_state)
        self._last_error = ""
        self.hub.set_device_status(self.cfg.id, Status.OK)
        log.info("ESPHome %s connected (%d entities)", self.cfg.host, len(self._keys))

    async def _on_disconnect(self, expected_disconnect: bool) -> None:
        self.hub.set_device_status(self.cfg.id, Status.MISSING,
                                   "" if expected_disconnect else "Connection lost")

    async def _on_connect_error(self, err: Exception) -> None:
        # The public text is one short fixed phrase. The error's own text (it can hold an IP
        # address) goes to the log only, once per change of phrase, never into the device status.
        phrase = describe_connect_error(err)
        if isinstance(err, (InvalidEncryptionKeyAPIError, RequiresEncryptionAPIError)):
            self.hub.set_device_status(self.cfg.id, Status.FAULT, phrase)
        elif isinstance(err, InvalidAuthAPIError):
            self.hub.set_device_status(self.cfg.id, Status.FAULT, "API password not supported")
            phrase = "API password not supported"
        else:
            self.hub.set_device_status(self.cfg.id, Status.MISSING, phrase)
        if phrase != self._last_error:
            self._last_error = phrase
            log.warning("ESPHome %s: %s (%s)", self.cfg.id, phrase, type(err).__name__)
            log.debug("ESPHome %s: full error: %s", self.cfg.id, err)   # may hold an address: debug only

    def _on_state(self, state) -> None:
        mapping = self._keys.get(state.key)
        if mapping is None:
            return
        entity_id, kind, unit = mapping
        now = time.time()
        if isinstance(state, SensorState):
            value = None if state.missing_state else to_canonical(kind, state.state, unit)
            self.hub.update_state(entity_id, value, now)
        elif isinstance(state, BinarySensorState):
            self.hub.update_state(entity_id, None if state.missing_state else float(state.state), now)


def discovery_key(d: dict) -> str:
    """Stable key for hiding a discovered node: its MAC (lower-case hex, no separators) when the
    mDNS TXT record has one, otherwise its mDNS name.  Prefixed so the two can't clash."""
    mac = "".join(c for c in str(d.get("mac") or "").lower() if c in "0123456789abcdef")
    return f"mac:{mac}" if len(mac) == 12 else f"name:{str(d.get('name') or '').lower()}"


class EsphomeIntegration(Integration):
    manifest = MANIFEST

    def __init__(self, hub, emulate: bool = False, zeroconf: AsyncZeroconf | None = None) -> None:
        super().__init__(hub, emulate)
        self.zc = zeroconf
        self.discovered: dict[str, dict] = {}
        self._nodes: dict[str, _NodeConnection | EmulatedNode] = {}
        self._browser: AsyncServiceBrowser | None = None
        self._pending: set[asyncio.Task] = set()
        # One lock per device: address changes, resolves and removal never overlap, so a request
        # can't leave a stray connection running or bring a deleted device back.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, device_id: str) -> asyncio.Lock:
        return self._locks.setdefault(device_id, asyncio.Lock())

    async def start(self) -> None:
        if self.emulate:
            for node in EmulatedNode.defaults(self.hub):
                self._nodes[node.device_id] = node
                await node.start()
            return
        if self.zc is not None:
            self._browser = AsyncServiceBrowser(
                self.zc.zeroconf, SERVICE_TYPE, handlers=[self._on_service])
        for cfg in self.hub.config.esphome_devices:
            await self._start_node(cfg)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.async_cancel()
        for node in list(self._nodes.values()):
            await node.stop()
        self._nodes.clear()

    async def _start_node(self, cfg: EsphomeDeviceConfig) -> None:
        node = _NodeConnection(self.hub, cfg, self.zc)
        self._nodes[cfg.id] = node
        try:
            await node.start()
        except Exception:
            log.exception("Could not start ESPHome node %s", cfg.host)
            self.hub.set_device_status(cfg.id, Status.FAULT, "failed to start")

    # ------------------------------------------------------------ discovery
    def _on_service(self, zeroconf, service_type: str, name: str,
                    state_change: ServiceStateChange) -> None:
        node_name = name.removesuffix("." + service_type).removesuffix(".")
        if state_change is ServiceStateChange.Removed:
            self.discovered.pop(node_name, None)
            return
        task = asyncio.create_task(self._resolve(service_type, name, node_name))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _resolve(self, service_type: str, name: str, node_name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(self.zc.zeroconf, 3000):
            return
        props = {k.decode(errors="ignore"): (v or b"").decode(errors="ignore")
                 for k, v in (info.properties or {}).items()}
        addresses = info.parsed_addresses()
        self.discovered[node_name] = {
            "name": node_name,
            "host": f"{node_name}.local",
            "address": addresses[0] if addresses else "",
            "port": info.port or 6053,
            "mac": props.get("mac", ""),
            "friendly_name": props.get("friendly_name", ""),
            "board": props.get("board", ""),
            "version": props.get("version", ""),
            "encrypted": "api_encryption" in props,
        }

    # ---------------------------------------------------------------- admin
    def discovered_list(self) -> list[dict]:
        """Discovered nodes the admin hasn't ignored, each with its ``key`` and ``adopted`` flag."""
        adopted_hosts = {c.host.lower() for c in self.hub.config.esphome_devices}
        by_mac = {c.mac: c.id for c in self.hub.config.esphome_devices if c.mac}
        ignored = set(self.hub.config.esphome_ignored)
        return [{**d, "key": discovery_key(d), "adopted": d["host"].lower() in adopted_hosts
                 or d["address"] in adopted_hosts,
                 # Its mDNS record carries the MAC of an adopted board (a hint, not proof).
                 "maybe_adopted_as": by_mac.get(normalise_mac(d.get("mac")) or "", "")}
                for d in sorted(self.discovered.values(), key=lambda d: d["name"])
                if discovery_key(d) not in ignored]

    def ignored_list(self) -> list[dict]:
        """Ignored entries for the admin page, labelled when the node is on the network now."""
        seen = {discovery_key(d): d for d in self.discovered.values()}
        out = []
        for key in self.hub.config.esphome_ignored:
            d = seen.get(key)
            out.append({"key": key, "name": d["name"] if d else "", "host": d["host"] if d else "",
                        "friendly_name": d["friendly_name"] if d else "", "seen": d is not None})
        return out

    def ignore(self, key: str) -> bool:
        """Hide a node that is currently discovered.  False if there is no such node."""
        if not any(discovery_key(d) == key for d in self.discovered.values()):
            return False
        ignored = self.hub.config.esphome_ignored
        if key not in ignored:
            if len(ignored) >= MAX_IGNORED:
                raise ValueError("full")
            ignored.append(key)
            self.hub.save_config()
        return True

    def unignore(self, key: str) -> bool:
        ignored = self.hub.config.esphome_ignored
        if key not in ignored:
            return False
        ignored.remove(key)
        self.hub.save_config()
        return True

    def adopted_as(self, host: str) -> str:
        """The id of the adopted device that is the same board as the discovered node at ``host``
        (matched on the MAC in its mDNS record), or "" if there is none."""
        h = host.strip().lower()
        macs = {normalise_mac(d.get("mac")) for d in self.discovered.values()
                if h and (d["host"].lower() == h or d["address"] == h or d["name"].lower() == h)}
        macs.discard(None)
        for c in self.hub.config.esphome_devices:
            if c.mac and c.mac in macs:
                return c.id
        return ""

    async def adopt(self, cfg: EsphomeDeviceConfig) -> str:
        """Adopt a node. Returns a warning ("" if none) when its mDNS record says it is a board
        that is already adopted; the adopt still goes ahead, and the connect-time check faults a
        real duplicate (an mDNS record is only a hint)."""
        if any(c.id == cfg.id for c in self.hub.config.esphome_devices):
            raise ValueError(f"A device with id '{cfg.id}' already exists")
        existing = self.adopted_as(cfg.host)
        cfg.mac = ""  # only ever recorded by the hub, on the first connect
        self.hub.config.esphome_devices.append(cfg)
        self.hub.save_config()
        if not self.emulate:
            async with self._lock(cfg.id):
                if self.config_of(cfg.id) is cfg:
                    await self._start_node(cfg)
        return f"This board looks like '{existing}', which is already adopted." if existing else ""

    def config_of(self, device_id: str) -> EsphomeDeviceConfig | None:
        return next((c for c in self.hub.config.esphome_devices if c.id == device_id), None)

    async def update(self, device_id: str, name: str | None, area: str | None,
                     host: str | None = None, port: int | None = None,
                     role: str | None = None) -> None:
        """Rename or move a device, or change its role. A new host or port restarts its
        connection; its recorded MAC stays, so the same board at the new address carries on with
        its calibration."""
        async with self._lock(device_id):
            await self._update_locked(device_id, name, area, host, port, role)

    async def _update_locked(self, device_id: str, name: str | None, area: str | None,
                             host: str | None, port: int | None, role: str | None = None) -> None:
        reconnect = None
        for cfg in self.hub.config.esphome_devices:
            if cfg.id == device_id:
                if name is not None:
                    cfg.name = name
                if area is not None:
                    cfg.area = area
                if role is not None:
                    cfg.role = role
                if (host is not None and host != cfg.host) or (port is not None and port != cfg.port):
                    cfg.host = host if host is not None else cfg.host
                    cfg.port = port if port is not None else cfg.port
                    reconnect = cfg
        device = self.hub.devices.get(device_id)
        if device:
            if name:
                device.name = name
            if area is not None:
                device.area = area
            if role is not None:
                self.hub.set_node_role(device_id, role)   # tells every open screen at once
            self.hub.bus.publish("device", device)
        self.hub.save_config()
        if reconnect is not None:
            await self._restart_node(reconnect, "connecting at the new address")

    async def _restart_node(self, cfg: EsphomeDeviceConfig, detail: str) -> None:
        """Stop the node's connection and start a new one. Call with ``self._lock(cfg.id)`` held."""
        node = self._nodes.pop(cfg.id, None)
        if node is not None:
            try:
                await node.stop()
            except Exception:
                log.exception("Could not stop ESPHome node %s", cfg.id)
        if self.emulate or self.config_of(cfg.id) is not cfg:  # removed meanwhile: stay stopped
            return
        self.hub.set_device_status(cfg.id, Status.INITIALIZING, detail)
        await self._start_node(cfg)

    def node_addresses(self) -> dict[str, dict]:
        """Admin only (an address never goes in the public snapshot): where each adopted node is,
        {device_id: {"host": the name or address it was adopted with, "address": the IP it is
        connected on now, "" while it is not connected}}."""
        out = {}
        for device_id, node in self._nodes.items():
            if not isinstance(node, _NodeConnection):
                continue
            try:
                address = node.client.connected_address or ""
            except Exception:  # noqa: BLE001 - an address is a convenience, never worth an error
                address = ""
            out[device_id] = {"host": node.cfg.host, "address": str(address)[:64]}
        return out

    def hardware_conflicts(self) -> dict[str, dict]:
        """Admin only. Devices held in FAULT by an identity check:
        {device_id: {reason, expected, found, other, records}} (MACs as 12 hex, "" if none;
        ``records`` = how many calibration records the found board already has)."""
        out = {}
        for device_id, node in self._nodes.items():
            if isinstance(node, _NodeConnection) and node.conflict:
                found = node.conflict["found"]
                out[device_id] = {**node.conflict, "records": len(records_of(
                    self.hub.config, node_key(found))) if found else 0}
        return out

    async def resolve_hardware(self, device_id: str,
                               action: Literal["new_hardware", "move_calibration", "forget_mac"]) -> None:
        """Settle a device held by an identity check, then reconnect it.

        - "different" (another board answers at the address):
          ``new_hardware`` accepts it with no calibration (the device's old offsets, including
          the rollback mirror, don't carry over; the old board's records stay under its own key
          in case it returns). ``move_calibration`` also copies the old board's records to the
          new one (``method: moved``).
        - "known_board" (first connect of a board that already has records):
          ``move_calibration`` uses those records; ``new_hardware`` starts afresh (the board's
          old records are removed, and this device's own settings, if any, apply).
        - "unreadable" (a MAC was recorded but the board sent none): ``forget_mac`` clears the
          recorded MAC so the board is accepted again.
        Anything else (including "duplicate": remove one of the two devices instead) raises
        LookupError, and nothing is changed.
        """
        async with self._lock(device_id):
            node = self._nodes.get(device_id)
            cfg = self.config_of(device_id)
            conflict = node.conflict if isinstance(node, _NodeConnection) else None
            reason = (conflict or {}).get("reason")
            if cfg is None or reason is None:
                raise LookupError("nothing to resolve")
            if reason == "unreadable":
                if action != "forget_mac":
                    raise LookupError("not for this problem")
                cfg.mac = ""
            elif reason in ("different", "known_board") and action in ("new_hardware", "move_calibration"):
                new_mac = conflict["found"]
                if not new_mac or any(c.mac == new_mac and c.id != device_id
                                      for c in self.hub.config.esphome_devices):
                    raise LookupError("that board is in use as another device")
                new_node = node_key(new_mac)
                if reason == "different":
                    if action == "move_calibration" and cfg.mac:
                        copy_node(self.hub.config, node_key(cfg.mac), new_node)
                    else:
                        drop_legacy(self.hub.config, device_id)
                elif action == "new_hardware":
                    drop_node(self.hub.config, new_node)
                # known_board + move_calibration: nothing to copy; on reconnect the board's records
                # win and this device's legacy entries are brought in step as the rollback mirror.
                cfg.mac = new_mac
            else:
                raise LookupError("not for this problem")
            self.hub.save_config()
            log.info("ESPHome %s: hardware check resolved (%s)", device_id, action)
            await self._restart_node(cfg, "reconnecting")

    async def remove(self, device_id: str) -> None:
        async with self._lock(device_id):
            self.hub.config.esphome_devices = [
                c for c in self.hub.config.esphome_devices if c.id != device_id]
            node = self._nodes.pop(device_id, None)
            if node is not None:
                await node.stop()
            self.hub.save_config()
            self.hub.remove_device(device_id)

    def info(self) -> dict:
        return {**super().info(), "nodes": len(self._nodes),
                "discovered": len(self.discovered)}
