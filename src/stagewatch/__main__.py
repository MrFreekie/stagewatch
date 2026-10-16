"""Command-line entry point: `stagewatch` or `python -m stagewatch`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import socket
import sys
from pathlib import Path

import uvicorn
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from . import __version__, updater_common
from .core.hub import Hub
from .core.wallclock import seed_emulate_demo
from .integrations.esphome import EsphomeIntegration
from .integrations.globcon import GlobconIntegration, seed_emulate_globcon
from .integrations.ontime import OntimeIntegration
from .integrations.osc_out import OscOutIntegration
from .integrations.shelly import ShellyIntegration, seed_emulate_shelly
from .integrations.pihost import PiHostIntegration
from .integrations.smaart import SmaartIntegration, seed_emulate_spl
from .netinfo import local_ipv4  # noqa: F401  (also used by tests)
from .version import build_info, version_string
from .web.limits import WS_MAX_MESSAGE
from .web.server import create_app

log = logging.getLogger("stagewatch")

EMULATE_SUBDIR = "emulate"


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
    return base / EMULATE_SUBDIR if emulate else base


def resolve_data_dir(explicit: Path | None, emulate: bool = False) -> Path:
    """The one place the data folder is decided.

    Emulate mode ALWAYS uses ``<data folder>/emulate``, whether the data folder came from
    --data-dir (a managed install passes its real one) or from the default, so simulated
    devices and history can never mix into a real show's data.
    """
    if explicit is None:
        return default_data_dir(emulate).resolve()
    base = Path(explicit)
    return (base / EMULATE_SUBDIR if emulate else base).resolve()


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


async def run(args: argparse.Namespace) -> int:
    data_dir: Path = args.data_dir
    zc = None if args.no_mdns else AsyncZeroconf()
    hub = Hub(data_dir, emulate=args.emulate)
    hub.add_integration(EsphomeIntegration(hub, emulate=args.emulate, zeroconf=zc))
    hub.add_integration(OscOutIntegration(hub))
    hub.add_integration(OntimeIntegration(hub, emulate=args.emulate))  # runs only while a dashboard has the Wall Clock card
    hub.add_integration(SmaartIntegration(hub, emulate=args.emulate))  # runs only when switched on in the admin page
    hub.add_integration(GlobconIntegration(hub, emulate=args.emulate))  # runs only while a dashboard has the GLOBCON levels card
    hub.add_integration(ShellyIntegration(hub, emulate=args.emulate))  # reads the power meters the admin added
    hub.add_integration(PiHostIntegration(hub, emulate=args.emulate))  # runs only on a Raspberry Pi (a simulated one in emulate)
    if args.emulate and seed_emulate_shelly(hub.config):   # a fresh emulate folder gets one simulated PM Mini and the Power card
        hub.save_config()
    if args.emulate and seed_emulate_demo(hub.config):  # a fresh emulate folder shows every clock style
        hub.save_config()
    if args.emulate and seed_emulate_spl(hub.config):   # ...and the Sound level card with simulated values
        hub.save_config()
    if args.emulate and seed_emulate_globcon(hub.config):   # ...and the GLOBCON levels card on the wall
        hub.save_config()
    app = create_app(hub)
    app.state.port = args.port  # for the "Connect a tablet" addresses

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
                            proxy_headers=False, ws_ping_interval=20,
                            ws_max_size=WS_MAX_MESSAGE)
    server = uvicorn.Server(config)
    hub.request_shutdown = lambda: setattr(server, "should_exit", True)
    log.info("Stagewatch %s starting on http://%s:%d (data: %s)%s", version_string(),
             args.host, args.port, data_dir, " [EMULATE]" if args.emulate else "")
    handshake_task = asyncio.create_task(_write_handshake_when_started(server))
    updater_common.write_server_lock(data_dir, args.port)  # lets `reset-admin-pin` detect us
    try:
        await server.serve()
    finally:
        updater_common.remove_server_lock(data_dir)
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
    parser.add_argument("--force", action="store_true",
                        help="run even if a Stagewatch server seems to be running on this data folder")
    args = parser.parse_args(argv)
    data_dir = resolve_data_dir(args.data_dir, args.emulate)
    if not args.force and updater_common.server_running(data_dir):
        print("error: Stagewatch appears to be running on this data folder. Stop the Stagewatch "
              "service first (a running server would overwrite the reset), or use --force.",
              file=sys.stderr)
        sys.exit(1)
    try:
        print(reset_admin_pin(data_dir))
        print("Note: until you set the new PIN in /admin, anyone on the network can set it. "
              "Restart Stagewatch and do it promptly.")
    except (OSError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def import_log_cli(argv: list[str]) -> None:
    """`stagewatch import-log FILE`: put a vendor log (LA Network Manager) into a show. Shows what it
    would do; adds `--write` to do it. Local only: it needs the data folder."""
    from .core import sitetime
    from .core.config import ConfigStore
    from .core.recorder import Recorder
    from .integrations.lanwm import importer, parse
    parser = argparse.ArgumentParser(prog="stagewatch import-log",
                                     description="Import an L-Acoustics Network Manager log file into a show. "
                                                 "Without --write it only shows what would be imported.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--format", choices=importer.FORMATS, default="lanwm")
    parser.add_argument("--show", type=int, default=None, help="show id (default: the current show)")
    parser.add_argument("--tz", default="", help="time zone of the computer that wrote the file "
                                                  "(default: the site's zone), e.g. Europe/London")
    parser.add_argument("--offset", type=float, default=0.0, help="seconds to add if that computer's clock was wrong")
    parser.add_argument("--session-markers", action="store_true",
                        help="also add markers for online/offline and acknowledge-all")
    parser.add_argument("--load-checker-markers", action="store_true",
                        help="also add markers for entering and leaving Load Checker mode")
    parser.add_argument("--write", action="store_true", help="import it (otherwise only a preview)")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--emulate", action="store_true", help="use the emulate data folder")
    parser.add_argument("--force", action="store_true",
                        help="run even if a Stagewatch server seems to be running on this data folder")
    args = parser.parse_args(argv)
    data_dir = resolve_data_dir(args.data_dir, args.emulate)
    if args.write and not args.force and updater_common.server_running(data_dir):
        print("error: Stagewatch appears to be running on this data folder. Import from the Admin page "
              "instead, or stop it first, or use --force.", file=sys.stderr)
        sys.exit(1)
    try:
        if args.file.stat().st_size > 20 * 1024 * 1024:
            raise importer.ImportRefused("That file is bigger than 20 MB.")
        text = parse.decode(args.file.read_bytes())
        site = ConfigStore(data_dir / "config.yaml").load().site
        recorder = Recorder(data_dir / "stagewatch.sqlite3")
        try:
            plan = importer.plan_import(recorder, site, fmt=args.format, text=text, file_name=args.file.name,
                                        show_id=args.show if args.show is not None else recorder.show_id,
                                        tz=args.tz, offset_s=args.offset, session_markers=args.session_markers,
                                        load_checker_markers=args.load_checker_markers)
            s = plan.summary()
            def when(ts):
                return sitetime.local(ts, site).strftime("%d %b %Y %H:%M:%S") if ts else "-"
            print(f"File: {when(s['file']['first'])} to {when(s['file']['last'])} "
                  f"({s['file']['entries']} entries{'' if s['file']['closed'] else ', session not closed'})")
            print(f"Show day: {when(s['show_day']['from'])} to {when(s['show_day']['to'])}")
            print(f"New: {s['new']}   already there: {s['already_present']}   outside the show day: "
                  f"{s['outside_day']}   session lines left out: {s['session_left_out']}   markers: {s['markers']}")
            for ep in s["episodes"]:
                length = "never cleared" if ep["seconds"] is None else f"cleared after {ep['seconds']:g} s"
                print(f"  {when(ep['start'])}  {ep['level']:8} {ep['unit']}: {ep['what']} ({length})")
            if s["outside_day"] and not s["new"]:
                print("None of it falls in this show's day. Shows in this data folder (use --show ID):")
                for sh in recorder.shows()[:10]:
                    print(f"  {sh['id']}: {sh['name']}  {sh['day'] or sitetime.show_day(sh['started'], site)}")
            if not args.write:
                print("Nothing has been imported. Add --write to import it.")
                return
            done = importer.apply_import(recorder, plan)
            print(f"Imported {done['rows']} entries and {done['markers']} markers (import {done['import_id']}).")
        finally:
            recorder.close()
    except (OSError, importer.ImportRefused) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "import-log":
        import_log_cli(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "reset-admin-pin":
        reset_admin_pin_cli(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(prog="stagewatch", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default all)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="config, database, logs (default: per-user app data folder, "
                             "or $STAGEWATCH_DATA; emulate mode always uses an 'emulate' subfolder of it)")
    parser.add_argument("--print-data-dir", action="store_true",
                        help="print the data directory that would be used, then exit")
    parser.add_argument("--emulate", action="store_true",
                        help="use emulated sensor nodes instead of real hardware")
    parser.add_argument("--no-mdns", action="store_true", help="disable mDNS/zeroconf")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=version_string())
    args = parser.parse_args()
    args.data_dir = resolve_data_dir(args.data_dir, args.emulate)
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
