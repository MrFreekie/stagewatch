"""Offline sandbox for trying the in-app updater end to end (no network, no install).

    uv run python scripts/updater_sandbox.py [--dir DIR] [--port 8090] [--reset]

Builds, under DIR (default: a folder in the system temp dir):
  origin.git   a local bare "remote" seeded from this working tree: v0.1.0, then v0.2.0 (bumps
               CONFIG_SCHEMA_VERSION so the update makes a data backup and has a changelog),
               and a `nightly` branch one commit after v0.2.0
  clone/       a managed clone checked out (detached) at v0.1.0, with a managed marker
  data/        an empty data dir
then runs the real launcher, which supervises the real server (emulate mode, port 8090).  Open
http://127.0.0.1:8090/admin, set a PIN, and use Software -> Check for updates -> Update now.
Stop with Ctrl-C.  The sandbox relaxes only what a local remote needs: file:// fetches, no
ACL check, no `uv sync`.  Production code paths are otherwise unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if "--serve" not in sys.argv:  # the supervised child must import the *clone's* code (PYTHONPATH)
    sys.path.insert(0, str(ROOT / "src"))

from stagewatch import updater_common as uc  # noqa: E402

GIT = shutil.which("git")


def git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="sandbox", GIT_AUTHOR_EMAIL="sandbox@example.invalid",
               GIT_COMMITTER_NAME="sandbox", GIT_COMMITTER_EMAIL="sandbox@example.invalid",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return subprocess.run([GIT, "-c", "protocol.file.allow=always", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


def build(root: Path) -> Path:
    bare, work, clone, data = root / "origin.git", root / "work", root / "clone", root / "data"
    git(root, "init", "-q", "--bare", "-b", "main", str(bare))
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "remote", "add", "origin", str(bare))
    for rel in git(ROOT, "ls-files", "-co", "--exclude-standard").splitlines():  # copy the current tree (uncommitted edits included)
        src = ROOT / rel
        if src.is_file():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, work / rel)

    def edit(rel: str, fn) -> None:
        p = work / rel
        p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8", newline="")

    import re
    edit("src/stagewatch/__init__.py", lambda t: re.sub(r'__version__ = "[^"]+"', '__version__ = "0.1.0"', t))
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "sandbox v0.1.0")
    git(work, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    edit("src/stagewatch/__init__.py", lambda t: re.sub(r'__version__ = "[^"]+"', '__version__ = "0.2.0"', t))
    edit("src/stagewatch/version.py", lambda t: t.replace("CONFIG_SCHEMA_VERSION = 1", "CONFIG_SCHEMA_VERSION = 2"))
    edit("CHANGELOG.md", lambda t: t.replace("## [Unreleased]", "## [Unreleased]\n\n## [0.2.0] - 2026-10-01\n\n"
                                             "### Added\n- Sandbox release: changes the config schema version.\n", 1))
    git(work, "commit", "-q", "-am", "sandbox v0.2.0")
    git(work, "tag", "-a", "v0.2.0", "-m", "v0.2.0")
    edit("CHANGELOG.md", lambda t: t + "\n<!-- nightly -->\n")
    git(work, "commit", "-q", "-am", "sandbox nightly work")
    git(work, "branch", "nightly")
    git(work, "push", "-q", "origin", "main", "nightly", "--tags")
    git(root, "clone", "-q", str(bare), str(clone))
    git(clone, "checkout", "-q", "--detach", "v0.1.0")
    data.mkdir()
    uc.ensure_state_dir(clone)
    uc.write_marker(clone / ".git" / uc.MARKER_NAME, {
        "repo_path": str(clone), "origin_url": str(bare), "git_path": GIT, "uv_path": sys.executable,
        "uv_env": {}, "data_dir": str(data), "channel": "stable", "port": 8090, "emulate": True,
        "created": "2026-01-01T00:00:00Z"})
    return clone / ".git" / uc.MARKER_NAME


async def serve(marker_path: Path, port: int) -> int:
    """The supervised child: the real app, with the updater allowed to fetch from a local remote."""
    import uvicorn
    from stagewatch.__main__ import _write_handshake_when_started
    from stagewatch.core.hub import Hub
    from stagewatch.core.updater import Updater
    from stagewatch.integrations.esphome import EsphomeIntegration
    from stagewatch.integrations.osc_out import OscOutIntegration
    from stagewatch.web.server import create_app

    marker = uc.load_marker(marker_path, require_https=False)
    hub = Hub(marker.data_dir, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    hub.add_integration(OscOutIntegration(hub))
    updater = Updater(hub, marker, supervised=os.environ.get(uc.ENV_SUPERVISED) == "1",
                      git_protocols=("file",), check_interval_s=2)
    server = uvicorn.Server(uvicorn.Config(create_app(hub, updater=updater), host="127.0.0.1", port=port,
                                           log_level="warning"))
    hub.request_shutdown = lambda: setattr(server, "should_exit", True)
    task = asyncio.create_task(_write_handshake_when_started(server))
    try:
        await server.serve()
    finally:
        task.cancel()
    return int(hub.exit_code or 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", type=Path, default=Path(tempfile.gettempdir()) / "stagewatch-updater-sandbox")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--reset", action="store_true", help="delete and rebuild the sandbox")
    ap.add_argument("--serve", type=Path, help=argparse.SUPPRESS)  # internal: the supervised child
    args = ap.parse_args()
    if args.serve:
        return asyncio.run(serve(args.serve, args.port))

    root = args.dir.resolve()
    if args.reset and root.exists():
        shutil.rmtree(root, onerror=lambda f, p, _e: (os.chmod(p, 0o700), f(p)))
    marker_path = root / "clone" / ".git" / uc.MARKER_NAME
    if not marker_path.exists():
        root.mkdir(parents=True, exist_ok=True)
        marker_path = build(root)
    from stagewatch import launcher as lc
    marker = uc.load_marker(marker_path, require_https=False)
    launcher = lc.Launcher(
        marker, child_cmd=[sys.executable, str(Path(__file__).resolve()), "--serve", str(marker_path),
                           "--port", str(args.port)],
        sync_fn=lambda m: None, acl_checker=lambda p: [], git_protocols=("file",),
        health_timeout=90, stable_seconds=3)
    lc._setup_logging(marker.data_dir)
    import signal
    signal.signal(signal.SIGINT, lambda *_: launcher.stop_event.set())
    print(f"Sandbox in {root}\nOpen http://127.0.0.1:{args.port}/admin  (Ctrl-C to stop)")
    return launcher.run()


if __name__ == "__main__":
    sys.exit(main())
