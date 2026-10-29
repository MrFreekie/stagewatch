"""Does this computer have internet right now? A calm, read-only answer for the header icon.

Stagewatch is mostly offline; the internet is only wanted for updates. One small HEAD request goes to
github.com, the host the in-app updater already talks to, every five minutes. It sends no identifying
header (no hub id, version or site name) and nothing else. "No internet" needs two failures in a row, and
it is information only: no alarm, no notice, no marker, and it never feeds the Online/Offline button.

The clock and the fetcher are injected so tests never wait and never touch the network.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

CHECK_URL = "https://github.com/"
INTERVAL_S = 300.0
RETRY_S = 60.0        # one follow-up after a first failure; never a burst
TIMEOUT_S = 3.0
FAILS_FOR_DOWN = 2
USER_AGENT = "Mozilla/5.0"

Fetcher = Callable[[str, float], bool]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):  # a captive portal's redirect must not read as internet
        return None


def default_fetch(url: str, timeout: float = TIMEOUT_S) -> bool:
    """True only for a plain 2xx from the real host (a portal's 200 page lacks GitHub's own headers)."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            if not 200 <= resp.status < 300:
                return False
            host = (urlsplit(url).hostname or "").lower()
            if host.endswith("github.com"):
                return "github" in (resp.headers.get("Server") or "").lower() or bool(resp.headers.get("X-GitHub-Request-Id"))
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def age_bucket(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 600:
        return "just now"
    if seconds < 3600:
        return "within the hour"
    return "over an hour ago"


class NetCheck:
    def __init__(self, hub, clock: Callable[[], float] = time.time, fetcher: Fetcher = default_fetch,
                 default_on: Callable[[], bool] = lambda: False) -> None:
        self.hub = hub
        self.clock = clock
        self.fetcher = fetcher
        self.default_on = default_on   # used while the admin has not chosen (the server ties it to managed installs)
        self.state = "unknown"
        self.checked_at: float | None = None
        self.since: float | None = None
        self.fails = 0
        self._task: asyncio.Task | None = None

    def enabled(self) -> bool:
        choice = self.hub.config.updater.check_internet
        return self.default_on() if choice is None else bool(choice)

    async def run_once(self) -> None:
        if not self.enabled():
            return
        try:
            ok = bool(await asyncio.to_thread(self.fetcher, CHECK_URL, TIMEOUT_S))
        except Exception:  # noqa: BLE001 - a broken check just reads as no answer
            ok = False
        now = self.clock()
        self.checked_at = now
        self.fails = 0 if ok else self.fails + 1
        new = "up" if ok else ("down" if self.fails >= FAILS_FOR_DOWN or self.state == "down" else self.state)
        if new != self.state:
            self.state, self.since = new, now
            self.hub.bus.publish("internet", self.snapshot())

    def next_delay(self) -> float:
        return RETRY_S if self.fails and self.state != "down" else INTERVAL_S

    def snapshot(self) -> dict:
        if not self.enabled() or self.state == "unknown":
            return {"state": "unknown", "age": ""}
        return {"state": self.state, "age": age_bucket(self.clock() - (self.checked_at or 0)),
                "for": age_bucket(self.clock() - (self.since or 0))}

    async def _loop(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.next_delay())

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="net-check")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
