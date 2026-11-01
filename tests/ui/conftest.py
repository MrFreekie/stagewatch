"""Browser layout tests: optional. Run with `pytest -m ui` (see docs/developer-notes.md).

Skipped unless asked for with -m ui, and skipped when Playwright or its chromium is missing,
so the normal suite and every contributor's setup stay as they were.
"""
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

SIZES = {"phone": (375, 812), "tablet": (768, 1024), "wall": (1920, 1080)}
PIN = "2468"


def pytest_collection_modifyitems(config, items):
    wanted = "ui" in (config.getoption("-m") or "")
    for item in items:
        if "tests/ui/" in item.nodeid.replace("\\", "/") and not wanted:
            item.add_marker(pytest.mark.skip(reason="UI tests only run with -m ui"))
        elif "tests/ui/" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.ui)


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser not installed
            pytest.skip(f"chromium not available: {str(exc).splitlines()[0]}")
        yield b
        b.close()


@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = tmp_path_factory.mktemp("uidata")
    proc = subprocess.Popen(
        [sys.executable, "-m", "stagewatch", "--emulate", "--no-mdns", "--host", "127.0.0.1",
         "--port", str(port), "--data-dir", str(data)],
        stdout=subprocess.DEVNULL, stderr=open(data / "err.log", "wb"))
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/api/info", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.kill()
        pytest.fail("server did not start")
    # First-run PIN, created the way the Admin page does it.
    req = urllib.request.Request(url + "/api/admin/setup", data=f'{{"pin":"{PIN}"}}'.encode(),
                                 headers={"Content-Type": "application/json", "Origin": url})
    urllib.request.urlopen(req, timeout=5)
    yield url
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def open_page(browser, base_url):
    """open_page(path, size, theme) -> (page, console_errors)."""
    made = []

    def _open(path, size, theme):
        w, h = SIZES[size]
        touch = size != "wall"
        ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=touch, is_mobile=(size == "phone"))
        ctx.add_init_script(f"localStorage.setItem('stagewatch.theme', '{theme}')")
        made.append(ctx)
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base_url + path)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)   # first snapshot paints the cards
        return page, errors

    yield _open
    for c in made:
        c.close()
