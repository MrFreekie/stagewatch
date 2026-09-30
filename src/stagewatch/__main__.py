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

from . import __version__, updater_common
from .core.hub import Hub
from .integrations.esphome import EsphomeIntegration
from .integrations.osc_out import OscOutIntegration
from .version import build_info, version_string
from .web.server import create_app

log = logging.getLogger("stagewatch")


def default_data_dir(emulate: bool = False) -> Path:
    """Where config, history, secret key and logs live.

    Deliberately outside the source checkout, so updating or re-cloning the
    code (or `git clean`) never touches a running installation's data, and a
    public clone always starts blank. Emulate mode gets its own directory so
    simulated data never mixes with a real show's history.
    Override with --data-dir or $STAGEWATCH_DATA.
    """
    env = os.environ.get("STAGEWATCH_DATA")
    if env:
        base = Path(env)
    elif sys.platform == "win32":
        # Not %LOCALAPPDATA%: Windows silently redirects AppData for apps run
        # from packaged (MSIX) hosts, so data could land somewhere the user
        # can't find. A plain folder in the profile is visible and stable.
        base = Path.home() / "StagewatchData"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "stagewatch"
    return base / "emulate" if emulate else base


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


async def run(args: argparse.Namespace) -> int:
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
    hub.request_shutdown = lambda: setattr(server, "should_exit", True)
    log.info("Stagewatch %s starting on http://%s:%d (data: %s)%s", version_string(),
             args.host, args.port, data_dir, " [EMULATE]" if args.emulate else "")
    handshake_task = asyncio.create_task(_write_handshake_when_started(server))
    try:
        await server.serve()
    finally:
        handshake_task.cancel()
        if zc is not None:
            if service is not None:
                await zc.async_unregister_service(service)
            await zc.async_close()
    return int(getattr(hub, "exit_code", 0) or 0)  # 75 = launcher should apply a pending update


async def _write_handshake_when_started(server: uvicorn.Server) -> None:
    """Health signal for the launcher: only active when it set STAGEWATCH_SUPERVISED=1 (plus a
    state dir and nonce).  Normal/dev runs never touch this."""
    if os.environ.get(updater_common.ENV_SUPERVISED) != "1":
        return
    while not server.started:
        if server.should_exit:
            return
        await asyncio.sleep(0.2)
    try:
        repo = Path(__file__).resolve().parents[2]
        commit = updater_common.read_head_file(repo) or build_info().get("commit") or ""
        updater_common.write_handshake_from_env(__version__, commit)
    except Exception:
        log.exception("could not write launcher handshake")


def reset_admin_pin_cli(argv: list[str]) -> None:
    """`stagewatch reset-admin-pin --data-dir DIR`: local-only recovery (filesystem access)."""
    from .core.config import reset_admin_pin
    parser = argparse.ArgumentParser(prog="stagewatch reset-admin-pin",
                                     description="Clear the admin PIN so /admin lets you set a new one. "
                                                 "Needs access to the data folder; not available over HTTP.")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--emulate", action="store_true", help="use the emulate data folder")
    args = parser.parse_args(argv)
    data_dir = (args.data_dir or default_data_dir(args.emulate)).resolve()
    try:
        print(reset_admin_pin(data_dir))
    except (OSError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "reset-admin-pin":
        reset_admin_pin_cli(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(prog="stagewatch", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default all)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="config, database, logs (default: per-user app data folder, "
                             "or $STAGEWATCH_DATA; emulate mode uses an 'emulate' subfolder)")
    parser.add_argument("--print-data-dir", action="store_true",
                        help="print the data directory that would be used, then exit")
    parser.add_argument("--emulate", action="store_true",
                        help="use emulated sensor nodes instead of real hardware")
    parser.add_argument("--no-mdns", action="store_true", help="disable mDNS/zeroconf")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=version_string())
    args = parser.parse_args()
    args.data_dir = (args.data_dir or default_data_dir(args.emulate)).resolve()
    if args.print_data_dir:
        print(args.data_dir)
        return
    setup_logging(args.data_dir, args.verbose)
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        code = 0
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
