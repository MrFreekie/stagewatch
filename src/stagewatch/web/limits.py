"""Global request-body size cap (plans/in-app-updater.md amendment G).

Pure ASGI middleware: refuses a request with ``413`` when its declared ``Content-Length``
exceeds the limit, and caps the streamed body for chunked / lying clients, so no endpoint can
be made to buffer an unbounded body.  Applies to every HTTP request that has a body (any
method).  WebSocket messages are capped separately by ``WS_MAX_MESSAGE``
(uvicorn ``ws_max_size`` plus a check in the /ws handler).  ``overrides`` maps an exact path to a different
limit in bytes (the per-route hook), or pass ``limit_for(scope) -> int | None`` for anything
fancier.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Mapping

from fastapi import HTTPException

DEFAULT_BODY_LIMIT = 64 * 1024
WS_MAX_MESSAGE = 64 * 1024  # largest message a browser may send on /ws (uvicorn's own default is 16 MiB)
DRAIN_MAX_BYTES = 1024 * 1024  # read (and discard) at most this much of a refused body ...
DRAIN_TIMEOUT_S = 2.0          # ... for at most this long, so the client sees the 413, not a reset


async def _drain(receive, max_bytes: int = DRAIN_MAX_BYTES, timeout: float = DRAIN_TIMEOUT_S,
                 first_more: bool = True) -> None:
    """Discard up to ``max_bytes`` of the remaining request body.  Beyond that (or on timeout or
    disconnect) give up: the caller closes the connection.  Never raises."""
    if not first_more:
        return
    got = 0

    async def loop():
        nonlocal got
        while got <= max_bytes:
            msg = await receive()
            if msg["type"] != "http.request":
                return
            got += len(msg.get("body", b""))
            if not msg.get("more_body", False):
                return

    try:
        await asyncio.wait_for(loop(), timeout)
    except (asyncio.TimeoutError, Exception):
        pass


class BodySizeLimitMiddleware:
    def __init__(self, app, default_limit: int = DEFAULT_BODY_LIMIT,
                 overrides: Mapping[str, int] | None = None,
                 limit_for: Callable[[dict], int | None] | None = None):
        self.app = app
        self.default_limit = default_limit
        self.overrides = dict(overrides or {})
        self.limit_for = limit_for

    def _limit(self, scope) -> int:
        if self.limit_for is not None:
            v = self.limit_for(scope)
            if v is not None:
                return v
        return self.overrides.get(scope.get("path", ""), self.default_limit)

    @staticmethod
    async def _reject(send, status: int, detail: str) -> None:
        body = ('{"detail":"%s"}' % detail).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope)
        declared = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await self._reject(send, 400, "Invalid Content-Length")
                    return
                if declared < 0:
                    await self._reject(send, 400, "Invalid Content-Length")
                    return
        if declared is not None and declared > limit:
            await _drain(receive)
            await self._reject(send, 413, "Request body too large")
            return

        received = 0

        async def capped_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    await _drain(receive, first_more=message.get("more_body", False))
                    # HTTPException so FastAPI's body parsing re-raises it as a proper 413
                    raise HTTPException(413, "Request body too large")
            return message

        await self.app(scope, capped_receive, send)
