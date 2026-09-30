"""Global request-body size cap (plans/in-app-updater.md amendment G).

Pure ASGI middleware: refuses a request with ``413`` when its declared ``Content-Length``
exceeds the limit, and caps the streamed body for chunked / lying clients, so no endpoint can
be made to buffer an unbounded body.  Applies to every HTTP request that has a body (any
method); WebSocket frames are not covered.  ``overrides`` maps an exact path to a different
limit in bytes (the per-route hook), or pass ``limit_for(scope) -> int | None`` for anything
fancier.
"""

from __future__ import annotations

from typing import Callable, Mapping

from fastapi import HTTPException

DEFAULT_BODY_LIMIT = 64 * 1024


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
            await self._reject(send, 413, "Request body too large")
            return

        received = 0

        async def capped_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # HTTPException so FastAPI's body parsing re-raises it as a proper 413
                    raise HTTPException(413, "Request body too large")
            return message

        await self.app(scope, capped_receive, send)
