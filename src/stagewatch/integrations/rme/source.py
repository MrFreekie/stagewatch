"""The interface every RME source implements, so the integration does not care whether the frames
come from the simulated unit or from a real one.

A source tells its owner two things through callbacks, on the event loop:

* ``on_state(state, result)``: a text frame was folded into ``state`` (an ``RmeState``). ``result.kind`` is
  baseline (first full status of a connection), resync (a later full status on the same connection),
  delta, or ignored.
* ``on_link(up, detail, waiting=False)``: the link came up (only once a full status has arrived) or went
  down. ``waiting`` is True while the socket is open but no status has arrived yet. ``detail`` is
  short fixed text for crew: never an address, never raw error text.

A source never sends anything to the unit. It never raises out of ``start``; ``stop`` always ends it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from .parse import FrameResult, RmeState

StateCallback = Callable[[RmeState, FrameResult], None]
LinkCallback = Callable[..., None]


class RmeSource(ABC):
    #: True only once checked against real hardware beyond a capture. Never claimed before.
    verified: bool = False
    label: str = ""

    def __init__(self, on_state: StateCallback, on_link: LinkCallback) -> None:
        self._on_state, self._on_link = on_state, on_link
        #: Why it cannot read right now, as a short code for the admin page ("" = nothing wrong).
        self.problem = ""
        #: Source clock times (seconds, the same clock the integration passes in) of the last frame of any
        #: kind and of the last text frame. None until one has arrived on the current connection.
        self.last_frame_at: float | None = None
        self.last_text_at: float | None = None
        self.binary_frames = 0        # dropped before any decoding
        self.binary_dropped = 0       # binary frames over the rate limit
        self.dropped_frames = 0       # malformed, too many, or before a baseline
        self.bad_frames = 0

    def hint(self) -> str:
        """Admin page only: the address tried and why it cannot connect, in plain words ("" = nothing to
        say). Never sent to dashboards or the public snapshot."""
        return ""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
