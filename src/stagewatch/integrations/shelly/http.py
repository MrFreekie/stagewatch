"""A tiny, strict HTTP client for reading a Shelly meter. Read-only by construction.

Only two requests can ever be made, both plain GETs:

    GET /rpc/Shelly.GetDeviceInfo
    GET /rpc/Shelly.GetStatus

``rpc_path`` refuses every other method name before any connection is opened, so nothing that
switches a relay, changes a setting, restarts, updates or resets a meter can be sent (a test pins
this). The client also:

* only talks to a private-network IP address (the project's local-address check; no host names, so
  nothing can change the destination under us);
* never follows a redirect;
* gives up after a total time limit, and reads at most 256 KiB;
* never returns text from the meter in an error: failures are a short fixed category.

Authentication: a meter with a password set is reported as ``needs_password``. Stagewatch does not
send passwords yet.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from typing import Awaitable, Callable

from ..smaart.client import is_local_address

log = logging.getLogger(__name__)

GET_DEVICE_INFO = "Shelly.GetDeviceInfo"
GET_STATUS = "Shelly.GetStatus"
ALLOWED_METHODS = frozenset({GET_DEVICE_INFO, GET_STATUS})

TIMEOUT_S = 2.0              # total time for one request, connect to last byte
MAX_BODY = 256 * 1024
MAX_HEADERS = 16 * 1024
DEFAULT_PORT = 80

# A failed request is always one of these (the key into TEXT in __init__.py).
CATEGORIES = ("not_local", "timeout", "refused", "unreachable", "redirect", "needs_password",
              "not_shelly", "too_large", "bad_reply")


class ShellyError(Exception):
    """A request failed. ``category`` is one of CATEGORIES; the message carries nothing else."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class ForbiddenMethod(Exception):
    """An RPC method that is not on the allow-list was asked for. Nothing was sent."""


def rpc_path(method: str) -> str:
    """The request path for an allowed method. Raises ForbiddenMethod for anything else."""
    if method not in ALLOWED_METHODS:
        raise ForbiddenMethod("method not allowed")
    return f"/rpc/{method}"


Fetch = Callable[[str, str], Awaitable[dict]]   # (host, method) -> the JSON object the meter returned


async def get_json(host: str, method: str, *, port: int = DEFAULT_PORT, timeout: float = TIMEOUT_S,
                   max_body: int = MAX_BODY, address_ok: Callable = is_local_address) -> dict:
    """One allowed GET. Returns the decoded JSON object or raises ShellyError / ForbiddenMethod."""
    path = rpc_path(method)       # first: a forbidden name never reaches the network
    try:
        if not isinstance(host, str):
            raise ValueError    # a whole number would otherwise be read as an address
        ip = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        raise ShellyError("not_local") from None
    if not address_ok(ip):
        raise ShellyError("not_local")
    try:
        return await asyncio.wait_for(_fetch(str(ip), port, path, max_body), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        raise ShellyError("timeout") from None
    except ShellyError:
        raise
    except ConnectionRefusedError:
        raise ShellyError("refused") from None
    except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        raise ShellyError("unreachable") from None


async def _fetch(ip: str, port: int, path: str, max_body: int) -> dict:
    reader, writer = await asyncio.open_connection(ip, port, limit=MAX_HEADERS)
    try:
        host_header = f"[{ip}]" if ":" in ip else ip
        request = (f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\nAccept: application/json\r\n"
                   "User-Agent: Stagewatch\r\nConnection: close\r\n\r\n")
        writer.write(request.encode("ascii"))
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        if len(head) > MAX_HEADERS:
            raise ShellyError("bad_reply")
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ", 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
            raise ShellyError("not_shelly")
        code = int(parts[1])
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if code in (301, 302, 303, 307, 308):
            raise ShellyError("redirect")        # never followed
        if code == 401:
            raise ShellyError("needs_password")
        if code != 200:
            raise ShellyError("not_shelly")
        body = await _read_body(reader, headers, max_body)
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            raise ShellyError("bad_reply") from None
        if not isinstance(data, dict):
            raise ShellyError("bad_reply")
        return data
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 0.5)
        except Exception:  # noqa: BLE001 - closing is best effort
            pass


async def _read_body(reader: asyncio.StreamReader, headers: dict, max_body: int) -> bytes:
    if headers.get("transfer-encoding", "").lower() == "chunked":
        out = bytearray()
        while True:
            size_line = await reader.readuntil(b"\r\n")
            try:
                size = int(size_line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                raise ShellyError("bad_reply") from None
            if size == 0:
                return bytes(out)
            if len(out) + size > max_body:
                raise ShellyError("too_large")
            out += await reader.readexactly(size)
            await reader.readexactly(2)          # the CRLF after each chunk
    length = headers.get("content-length")
    if length is not None:
        if not length.isdigit():
            raise ShellyError("bad_reply")
        if int(length) > max_body:
            raise ShellyError("too_large")
        return await reader.readexactly(int(length))
    out = bytearray()                              # no length: read until the meter closes
    while True:
        chunk = await reader.read(8192)
        if not chunk:
            return bytes(out)
        out += chunk
        if len(out) > max_body:
            raise ShellyError("too_large")
