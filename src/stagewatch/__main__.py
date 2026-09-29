"""Command-line entry point: `stagewatch` or `python -m stagewatch`."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import logging.handlers
import os
import socket
import sys
from pathlib import Path

import ifaddr
import uvicorn
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from . import __version__
from .core.hub import Hub
from .integrations.esphome import EsphomeIntegration
from .integrations.osc_out import OscOutIntegration
from .version import version_string
from .web.server import create_app

log = logging.getLogger("stagewatch")


def default_data_dir() -> Path:
    env = os.environ.get("STAGEWATCH_DATA")
    return Path(env) if env else Path.cwd() / "data"


def setup_logging(data_dir: Path, verbose: bool) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        data_dir / "logs" / "stagewatch.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers[:] = [file_handler, console]


def local_ipv4() -> list[str]:
    out = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if isinstance(ip.ip, str):
                addr = ipaddress.ip_address(ip.ip)
                if not (addr.is_loopback or addr.is_link_local):
                    out.append(ip.ip)
    return out


async def run(args: argparse.Namespace) -> None:
    data_dir: Path = args.data_dir
    zc = None if args.no_mdns else AsyncZeroconf()
    hub = Hub(data_dir, emulate=args.emulate)
    hub.add_integration(EsphomeIntegration(hub, emulate=args.emulate, zeroconf=zc))
    hub.add_integration(OscOutIntegration(hub))
    app = create_app(hub)

    service = None
    if zc is not None:
        addrs = local_ipv4()
        name = hub.config.mdns_name or "stagewatch"
        service = ServiceInfo(
            "_http._tcp.local.",
            f"Stagewatch {socket.gethostname()}._http._tcp.local.",
            addresses=[socket.inet_aton(a) for a in addrs],
            port=args.port,
            server=f"{name}.local.",
            properties={"path": "/", "version": __version__},
        )
        try:
            await zc.async_register_service(service, allow_name_change=True)
            log.info("mDNS: http://%s.local:%d  (%s)", name, args.port, ", ".join(addrs))
        except Exception:
            log.exception("mDNS advertisement failed; use the IP address instead")
            service = None

    config = uvicorn.Config(app, host=args.host, port=args.port, log_config=None,
                            proxy_headers=False, ws_ping_interval=20)
    server = uvicorn.Server(config)
    log.info("Stagewatch %s starting on http://%s:%d (data: %s)%s", version_string(),
             args.host, args.port, data_dir, " [EMULATE]" if args.emulate else "")
    try:
        await server.serve()
    finally:
        if zc is not None:
            if service is not None:
                await zc.async_unregister_service(service)
            await zc.async_close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="stagewatch", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default all)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(),
                        help="config, database, logs (default ./data or $STAGEWATCH_DATA)")
    parser.add_argument("--emulate", action="store_true",
                        help="use emulated sensor nodes instead of real hardware")
    parser.add_argument("--no-mdns", action="store_true", help="disable mDNS/zeroconf")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=version_string())
    args = parser.parse_args()
    args.data_dir = args.data_dir.resolve()
    setup_logging(args.data_dir, args.verbose)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
