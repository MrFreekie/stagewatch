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
from ...core.config import MAX_IGNORED, EsphomeDeviceConfig
from ...core.model import Device, Entity, Kind, Status, slugify
from ...core.plugin import Integration, Manifest
from .emulate import EmulatedNode
from .mapping import canonical_unit, sensor_kind, to_canonical

log = logging.getLogger(__name__)

SERVICE_TYPE = "_esphomelib._tcp.local."

MANIFEST = Manifest(
    domain="esphome",
    name="ESPHome",
    version="0.1.0",
    description="DIY ESP32/ESP8266 nodes running ESPHome: temperature, humidity, "
                "pressure (BME280, SHT4x, ...) plus generic sensors and contacts.",
    tier="experimental",
    direction="in",
    protocols=("ESPHome native API", "mDNS"),
    entity_kinds=("temperature", "humidity", "pressure", "contact", "generic"),
    vendors=("ESPHome",),
)


class _NodeConnection:
    """One adopted node: a reconnecting API client feeding the hub."""

    def __init__(self, hub, cfg: EsphomeDeviceConfig, zc: AsyncZeroconf | None) -> None:
        self.hub = hub
        self.cfg = cfg
        self.zc = zc
        self._keys: dict[int, tuple[str, Kind, str]] = {}
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
            "ESPHome", "", self.cfg.area, Status.INITIALIZING))
        await self.logic.start()

    async def stop(self) -> None:
        await self.logic.stop()
        await self.client.disconnect()

    async def _on_connect(self) -> None:
        info, entities, _services = await self.client.device_info_and_list_entities()
        self.hub.register_device(Device(
            self.cfg.id, self.cfg.name or info.friendly_name or info.name, MANIFEST.domain,
            info.manufacturer or "ESPHome", info.model or info.project_name or "",
            self.cfg.area))
        self._keys.clear()
        for e in entities:
            object_id = slugify(e.object_id or e.name or str(e.key))
            entity_id = f"{self.cfg.id}.{object_id}"
            if isinstance(e, SensorInfo):
                kind = sensor_kind(e.device_class, e.unit_of_measurement)
                unit = e.unit_of_measurement
                self._keys[e.key] = (entity_id, kind, unit)
                self.hub.register_entity(Entity(
                    entity_id, self.cfg.id, e.name or object_id, kind,
                    canonical_unit(kind, unit),
                    decimals=0 if kind == Kind.PRESSURE else max(0, min(e.accuracy_decimals, 3))))
            elif isinstance(e, BinarySensorInfo):
                self._keys[e.key] = (entity_id, Kind.CONTACT, "")
                self.hub.register_entity(Entity(
                    entity_id, self.cfg.id, e.name or object_id, Kind.CONTACT, "", 0))
        self.client.subscribe_states(self._on_state)
        self.hub.set_device_status(self.cfg.id, Status.OK)
        log.info("ESPHome %s connected (%d entities)", self.cfg.host, len(self._keys))

    async def _on_disconnect(self, expected_disconnect: bool) -> None:
        self.hub.set_device_status(self.cfg.id, Status.MISSING,
                                   "" if expected_disconnect else "connection lost")

    async def _on_connect_error(self, err: Exception) -> None:
        if isinstance(err, (InvalidEncryptionKeyAPIError, RequiresEncryptionAPIError)):
            self.hub.set_device_status(self.cfg.id, Status.FAULT, "encryption key missing or wrong")
        elif isinstance(err, InvalidAuthAPIError):
            self.hub.set_device_status(self.cfg.id, Status.FAULT, "node needs an API password (unsupported; use an encryption key)")
        else:
            self.hub.set_device_status(self.cfg.id, Status.MISSING, str(err)[:120])

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
        ignored = set(self.hub.config.esphome_ignored)
        return [{**d, "key": discovery_key(d), "adopted": d["host"].lower() in adopted_hosts
                 or d["address"] in adopted_hosts}
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

    async def adopt(self, cfg: EsphomeDeviceConfig) -> EsphomeDeviceConfig:
        if any(c.id == cfg.id for c in self.hub.config.esphome_devices):
            raise ValueError(f"A device with id '{cfg.id}' already exists")
        self.hub.config.esphome_devices.append(cfg)
        self.hub.save_config()
        if not self.emulate:
            await self._start_node(cfg)
        return cfg

    async def update(self, device_id: str, name: str | None, area: str | None) -> None:
        for cfg in self.hub.config.esphome_devices:
            if cfg.id == device_id:
                if name is not None:
                    cfg.name = name
                if area is not None:
                    cfg.area = area
        device = self.hub.devices.get(device_id)
        if device:
            if name:
                device.name = name
            if area is not None:
                device.area = area
            self.hub.bus.publish("device", device)
        self.hub.save_config()

    async def remove(self, device_id: str) -> None:
        node = self._nodes.pop(device_id, None)
        if node is not None:
            await node.stop()
        self.hub.config.esphome_devices = [
            c for c in self.hub.config.esphome_devices if c.id != device_id]
        self.hub.save_config()
        self.hub.remove_device(device_id)

    def info(self) -> dict:
        return {**super().info(), "nodes": len(self._nodes),
                "discovered": len(self.discovered)}
