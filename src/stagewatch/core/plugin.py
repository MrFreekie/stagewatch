"""Integration (plugin) contract.

An integration owns one protocol or product family. It creates devices and
entities on the hub, pushes states, and reports device status. It must not
touch other integrations or the web layer. Every integration ships a
manifest (shown in the admin catalog) and must support emulate mode so a
show can be built and demoed with no hardware attached.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .hub import Hub

Tier = Literal["verified", "community", "experimental"]
# How an integration gets its data: "local_push" (the device sends changes as they happen, on the
# local network), "local_poll" (Stagewatch asks every few seconds, on the local network) or
# "cloud" (needs the internet). Unset means unknown or not applicable: Admin then shows nothing.
IotClass = Literal["local_push", "local_poll", "cloud"]


@dataclass(frozen=True)
class Manifest:
    domain: str
    name: str
    version: str
    description: str
    tier: Tier = "experimental"
    direction: Literal["in", "out", "both"] = "in"
    protocols: tuple[str, ...] = ()
    entity_kinds: tuple[str, ...] = ()
    vendors: tuple[str, ...] = field(default_factory=tuple)
    iot_class: IotClass | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Integration(ABC):
    manifest: Manifest

    def __init__(self, hub: "Hub", emulate: bool = False) -> None:
        self.hub = hub
        self.emulate = emulate

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    def info(self) -> dict:
        """Admin-facing runtime summary; override to add detail."""
        return {"manifest": self.manifest.to_dict(), "emulate": self.emulate}
