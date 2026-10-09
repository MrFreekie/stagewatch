"""The interface every sound-level source implements, so the integration does not care whether the
numbers come from the emulated source or from a real Smaart.

A source tells its owner two things through callbacks, on the event loop:

* ``on_reading(SplReading)``: a new set of values. A value the software did not give is missing from
  ``values`` or None ("not available"), never zero.
* ``on_link(up, detail)``: the connection came up or went down. ``detail`` is short fixed text for
  crew (never an address, never raw error text).

A source never sends anything that changes the measurement software. It never raises out of
``start``; ``stop`` always ends it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ...core.spl import SplReading

ReadingCallback = Callable[[SplReading], None]
LinkCallback = Callable[[bool, str], None]


class SplSource(ABC):
    #: True only once the source has been checked against the real software's documentation and a
    #: real session. Shown to the admin; never claimed for a source that has not been.
    verified: bool = False
    #: Plain-text name for the admin page.
    label: str = ""

    def __init__(self, on_reading: ReadingCallback, on_link: LinkCallback) -> None:
        self._on_reading = on_reading
        self._on_link = on_link
        self.version = ""   # the software's version as it reported it (cleaned), "" if unknown

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
