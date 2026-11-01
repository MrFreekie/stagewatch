"""Host-header check against DNS rebinding.

A web page on the internet can point its own name at an address on the show network and then talk to
Stagewatch as if it were the same site. Such a request still carries the attacker's name in its Host header,
so Stagewatch only answers a Host that is a literal IP address, ``localhost``, a single-word machine name
(those are never public names), the machine's mDNS ``<name>.local``, or a name the admin listed in
``allowed_hosts``. Pure ASGI middleware, so it covers HTTP and WebSocket.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Callable, Iterable

_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
MAX_ALLOWED_HOSTS = 20


def split_host(value: str) -> str | None:
    """The host part of a Host header (no port, lower case, no brackets), or None if it is malformed."""
    v = value.strip().lower()
    if not v or len(v) > 255 or any(ord(c) < 33 or ord(c) > 126 for c in v):
        return None
    if v.startswith("["):
        end = v.find("]")
        if end < 0:
            return None
        rest = v[end + 1:]
        if rest and not re.fullmatch(r":[0-9]{1,5}", rest):
            return None
        return v[1:end]
    if v.count(":") > 1:
        return v                                  # a bare IPv6 address; judged as one below
    host, sep, port = v.partition(":")
    if sep and not re.fullmatch(r"[0-9]{1,5}", port):
        return None
    return host.rstrip(".") or None


def clean_allowed(names: Iterable[object]) -> list[str]:
    """Admin-listed names: plain host names only (lower case, no port), at most MAX_ALLOWED_HOSTS."""
    out: list[str] = []
    for n in names:
        if not isinstance(n, str):
            continue
        h = split_host(n)
        if h and len(h) <= 253 and all(_LABEL.match(p) for p in h.split(".")) and h not in out:
            out.append(h)
        if len(out) >= MAX_ALLOWED_HOSTS:
            break
    return out


def host_ok(value: str | None, mdns_name: str = "", allowed: Iterable[str] = ()) -> bool:
    if value is None:
        return True                               # HTTP/1.0 without a Host header: not a browser
    host = split_host(value)
    if host is None:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if not all(_LABEL.match(p) for p in host.split(".")):
        return False
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        return True
    if host.endswith(".local") and host.count(".") == 1:
        return True                               # mDNS answers only on the local link
    return host in {a.lower() for a in allowed} or (bool(mdns_name) and host == f"{mdns_name.lower()}.local")


class HostCheckMiddleware:
    def __init__(self, app, names: Callable[[], tuple[str, Iterable[str]]]):
        self.app = app
        self.names = names

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        value = None
        for name, raw in scope.get("headers", []):
            if name == b"host":
                value = raw.decode("latin-1")
                break
        mdns, allowed = self.names()
        if host_ok(value, mdns, allowed):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        body = (b'{"detail":"This address is not allowed. Open Stagewatch by its IP address, '
                b'its .local name or a name listed in allowed_hosts."}')
        await send({"type": "http.response.start", "status": 421,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})
