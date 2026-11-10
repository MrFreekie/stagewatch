"""GLOBCON Lights mode in a real browser: phone, tablet and wall, Day and Night. Squares stay square, inside the screen,
readable, with words as well as colour, and nothing animates. Emulate mode supplies a simulated GLOBCON."""
import json
import urllib.request

import pytest

from .conftest import PIN, SIZES
from .test_layout import CONTRAST_JS, DASH

COMBOS = [(s, t) for s in SIZES for t in ("day", "night")]
SLUGS = {"phone": "phone", "tablet": "foh", "wall": "wall"}
COLOURS = ["green", "amber", "red", "blue", "cyan", "magenta", "white", "orange"]
ICONS = ["microphone", "speaker", "timecode", "play", "megaphone", "camera", "headset", "radio", "signal"]


def _lights(n):
    """n lights: alternating threshold -72 (always on while levels arrive) and 0 (almost always off), every colour and icon in turn."""
    return [{"strip": i + 1, "threshold_db": -72 if i % 2 == 0 else 0, "colour": COLOURS[i % 8], "icon": ICONS[i % 8], "hold_s": 0} for i in range(n)]


def _configure(base_url, size, n):
    """Put the card in Lights mode, n channels, on the dashboard used for this size (through the Admin API)."""
    hdr = {"Content-Type": "application/json", "Origin": base_url}
    login = urllib.request.urlopen(urllib.request.Request(base_url + "/api/admin/login", data=json.dumps({"pin": PIN}).encode(), headers=hdr), timeout=5)
    cookie = login.headers.get("Set-Cookie", "").split(";")[0]
    hdr["Cookie"] = cookie
    rows = json.load(urllib.request.urlopen(base_url + "/api/info", timeout=5))["dashboards"]
    for row in rows:
        if row["slug"] == SLUGS[size]:
            if "globcon_meters" not in row["cards"]:
                row["cards"] = [*row["cards"], "globcon_meters"]
            row["globcon"] = {"controller": 1, "strips": 8, "range": "1-16", "mode": "lights", "lights": _lights(n)}
    req = urllib.request.Request(base_url + "/api/admin/dashboards", data=json.dumps(rows).encode(), headers=hdr, method="PUT")
    assert urllib.request.urlopen(req, timeout=5).status == 200


@pytest.mark.parametrize("n", [2, 8, 16])
@pytest.mark.parametrize("size,theme", COMBOS)
def test_lights_layout(open_page, base_url, size, theme, n):
    _configure(base_url, size, n)
    page, errors = open_page(DASH[size], size, theme)
    w, h = SIZES[size]
    page.wait_for_selector("#globcon-card .gc-light", timeout=15000)
    assert page.evaluate("document.documentElement.scrollWidth") <= w, "horizontal page scroll"
    boxes = page.evaluate("""() => [...document.querySelectorAll('#globcon-card .gc-light')].map((e) => {
        const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), Math.round(r.width), Math.round(r.height)]; })""")
    assert len(boxes) == n
    for left, right, bw, bh in boxes:
        assert abs(bw - bh) <= 2, f"square is {bw} x {bh}"
        assert left >= 0 and right <= w, "a square leaves the screen"
        assert bw >= 60, f"square only {bw}px wide"
    card_h = page.evaluate("document.getElementById('globcon-card').getBoundingClientRect().height")
    assert card_h <= (h if size == "wall" else 1500), f"card is {card_h}px high"
    # on and off carry no words (owner's choice); every square has a name
    for text in page.evaluate("""() => [...document.querySelectorAll('#globcon-card .gc-light')].map((e) => e.innerText)"""):
        assert text.strip().splitlines()[0].strip(), "square has no name"
    # no flashing or animation anywhere in the card
    assert page.evaluate("document.getElementById('globcon-card').getAnimations({subtree: true}).length") == 0
    bad = [b for b in page.evaluate(CONTRAST_JS) if ".gc-" in b]
    assert not bad, bad[:10]
    assert not errors, errors


@pytest.mark.parametrize("size,theme", [("tablet", "day"), ("tablet", "night")])
def test_lit_squares_use_their_colour_and_a_tick(open_page, base_url, size, theme):
    _configure(base_url, size, 8)
    page, _ = open_page(DASH[size], size, theme)
    page.wait_for_selector("#globcon-card .gc-light", timeout=15000)
    page.wait_for_timeout(2500)           # a few meter messages
    lit = page.evaluate("""() => [...document.querySelectorAll('#globcon-card .gc-l-on')].map((e) => [e.className, e.innerText, getComputedStyle(e).backgroundColor])""")
    frozen = page.evaluate("document.querySelectorAll('#globcon-card .gc-l-nodata').length")
    if frozen:
        pytest.skip("the simulated GLOBCON was in its dropout")
    assert lit, "no square lit although half have a threshold of -72 dB"
    for cls, text, bg in lit:
        assert "✓" in text and "ON" in text, text
        assert bg != "rgba(0, 0, 0, 0)"
    dull = page.evaluate("""() => [...document.querySelectorAll('#globcon-card .gc-l-off')].map((e) => e.innerText)""")
    assert dull and all("off" in t for t in dull)
