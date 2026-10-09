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

from ...core.spl import SplReading, clean_input_name

ReadingCallback = Callable[[SplReading], None]
LinkCallback = Callable[[bool, str], None]
CatalogCallback = Callable[[list, list], None]   # (inputs, metric names) as the software lists them


class SplSource(ABC):
    #: True only once the source has been checked against the real software's documentation and a
    #: real session. Shown to the admin; never claimed for a source that has not been.
    verified: bool = False
    #: Plain-text name for the admin page.
    label: str = ""

    def __init__(self, on_reading: ReadingCallback, on_link: LinkCallback,
                 on_catalog: CatalogCallback | None = None) -> None:
        self._on_reading = on_reading
        self._on_link = on_link
        self._on_catalog = on_catalog
        self.version = ""   # the software's version as it reported it (cleaned), "" if unknown
        self._input_name = ""
        #: The inputs ("deviceName : channelName") and metric names the software lists, in its order,
        #: once it has told us (empty until then). The admin drop-downs come from these.
        self.inputs: list[str] = []
        self.metrics: list[str] = []
        #: Why the source cannot read right now, as a short code for the admin page ("" = nothing
        #: wrong): "auth_needed", "wrong_password", "no_inputs", "api".
        self.problem = ""

    def set_wanted(self, sources: list[str]) -> None:
        """The input sources the chosen values need ("" = the first input listed). Sources that read
        from every input anyway (the simulated one) may ignore it."""

    async def refresh(self) -> bool:
        """Ask the software for its input and metric names again, now (the admin Refresh button).
        True once fresh lists have arrived; False if the link is down or the software did not answer.
        A source that cannot do this says False."""
        return False

    def _catalog(self, inputs: list[str], metrics: list[str]) -> None:
        self.inputs, self.metrics = list(inputs), list(metrics)
        if self._on_catalog is not None:
            self._on_catalog(self.inputs, self.metrics)

    @property
    def input_name(self) -> str:
        """The input the meter is tied to, as the software names it (cleaned), "" if unknown."""
        return self._input_name

    @input_name.setter
    def input_name(self, value: object) -> None:
        self._input_name = clean_input_name(value)

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
