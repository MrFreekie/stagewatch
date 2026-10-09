"""The interface every GLOBCON source implements, so the integration does not care whether the levels
come from the simulated feed or from a real GLOBCON.

A source tells its owner three things through callbacks, on the event loop:

* ``on_values(list[Value])``: names, labels, layer and similar values GLOBCON reported.
* ``on_meters(controller, blob)``: the raw level block of a controller (0-based number).
* ``on_link(up, detail)``: the connection came up or went down. ``detail`` is short fixed text for
  crew (never an address, never raw error text).

A source never sends anything that changes GLOBCON. It never raises out of ``start``; ``stop``
always ends it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from .protocol import Value

ValuesCallback = Callable[[list[Value]], None]
MetersCallback = Callable[[int, bytes], None]
LinkCallback = Callable[[bool, str], None]


class GlobconSource(ABC):
    #: True only once checked against a real GLOBCON beyond a capture. Never claimed before.
    verified: bool = False
    label: str = ""

    def __init__(self, on_values: ValuesCallback, on_meters: MetersCallback, on_link: LinkCallback) -> None:
        self._on_values, self._on_meters, self._on_link = on_values, on_meters, on_link
        #: Why it cannot read right now, as a short code for the admin page ("" = nothing wrong).
        self.problem = ""

    def set_wanted(self, controllers: list[int]) -> None:
        """The controllers (0-based) some dashboard shows. Only these are subscribed to."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
