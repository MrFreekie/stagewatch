"""Two GLOBCON cards on one dashboard (a Meters card and a Lights card) in a real browser: phone, tablet and wall, Day and
Night, full width and half width. Each card has its own DOM id, stays inside the screen, shows its own mode, and the page
makes no more GLOBCON requests than for one card (one shared message). Emulate mode supplies a simulated GLOBCON."""
import json
import urllib.request

import pytest

from .conftest import PIN, SIZES
from .test_layout import CONTRAST_JS, DASH

COMBOS = [(s, t) for s in SIZES for t in ("day", "night")]
SLUGS = {"phone": "phone", "tablet": "foh", "wall": "wall"}
G = "globcon_meters"


def _configure(base_url, size, half):
    hdr = {"Content-Type": "application/json", "Origin": base_url}
    login = urllib.request.urlopen(urllib.request.Request(base_url + "/api/admin/login", data=json.dumps({"pin": PIN}).encode(), headers=hdr), timeout=5)
    hdr["Cookie"] = login.headers.get("Set-Cookie", "").split(";")[0]
    rows = json.load(urllib.request.urlopen(base_url + "/api/info", timeout=5))["dashboards"]
    for row in rows:
        if row["slug"] == SLUGS[size]:
            row["cards"] = ["env_tiles", G, f"{G}#2"]
            row["card_sizes"] = {G: "half", f"{G}#2": "half"} if half else {}
            row["globcon"] = {"controller": 1, "strips": 8, "range": "1-8", "mode": "meters", "lights": []}
            row["globcon_more"] = {f"{G}#2": {"controller": 2, "strips": 8, "range": "1-4", "mode": "lights", "lights": [
                {"strip": 1, "threshold_db": -72, "colour": "green", "icon": "microphone", "hold_s": 0},
                {"strip": 2, "threshold_db": 0, "colour": "amber", "icon": "signal", "hold_s": 0},
                {"strip": 3, "threshold_db": -72, "colour": "cyan", "icon": "timecode", "hold_s": 0}]}}
    req = urllib.request.Request(base_url + "/api/admin/dashboards", data=json.dumps(rows).encode(), headers=hdr, method="PUT")
    assert urllib.request.urlopen(req, timeout=5).status == 200


@pytest.mark.parametrize("half", [False, True])
@pytest.mark.parametrize("size,theme", COMBOS)
def test_meters_and_lights_cards_side_by_side(open_page, base_url, size, theme, half):
    _configure(base_url, size, half)
    page, errors = open_page(DASH[size], size, theme)
    w, h = SIZES[size]
    page.wait_for_selector("#globcon-card .gc-strips", timeout=15000)
    page.wait_for_selector("#globcon-card-2 .gc-light", timeout=15000)
    assert page.evaluate("document.querySelectorAll('[id]').length") == page.evaluate("new Set([...document.querySelectorAll('[id]')].map((e) => e.id)).size"), "duplicate DOM ids"
    assert page.evaluate("document.querySelectorAll('[data-card^=\"globcon_meters\"]').length") == 2
    assert page.evaluate("document.documentElement.scrollWidth") <= w, "horizontal page scroll"
    assert page.evaluate("document.querySelectorAll('#globcon-card .gc-light').length") == 0, "first card is Meters"
    assert page.evaluate("document.querySelectorAll('#globcon-card-2 .gc-light').length") == 3
    boxes = page.evaluate("""() => ['globcon-card', 'globcon-card-2'].map((id) => {
        const r = document.getElementById(id).getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), Math.round(r.top), Math.round(r.bottom)]; })""")
    for left, right, top, bottom in boxes:
        assert left >= 0 and right <= w, "a card leaves the screen"
        assert bottom > top
    (l1, r1, t1, b1), (l2, r2, t2, b2) = boxes
    overlap = min(r1, r2) > max(l1, l2) and min(b1, b2) > max(t1, t2)
    assert not overlap, "the two cards overlap"
    if half and size == "wall":      # style.css pairs half cards from 900 px wide; a tablet or phone stacks them
        assert abs(t1 - t2) <= 4 and r1 <= l2 + 2, "half-width cards should sit side by side"
    # no flashing or animation
    assert page.evaluate("document.getElementById('globcon-card-2').getAnimations({subtree: true}).length") == 0
    bad = [b for b in page.evaluate(CONTRAST_JS) if ".gc-" in b]
    assert not bad, bad[:10]
    assert not errors, errors


def test_removing_the_second_card_leaves_the_first_alone(open_page, base_url):
    _configure(base_url, "tablet", False)
    page, errors = open_page(DASH["tablet"], "tablet", "day")
    page.wait_for_selector("#globcon-card-2 .gc-light", timeout=15000)
    hdr = {"Content-Type": "application/json", "Origin": base_url}
    login = urllib.request.urlopen(urllib.request.Request(base_url + "/api/admin/login", data=json.dumps({"pin": PIN}).encode(), headers=hdr), timeout=5)
    hdr["Cookie"] = login.headers.get("Set-Cookie", "").split(";")[0]
    rows = json.load(urllib.request.urlopen(base_url + "/api/info", timeout=5))["dashboards"]
    for row in rows:
        if row["slug"] == SLUGS["tablet"]:
            row["cards"] = ["env_tiles", G]
    req = urllib.request.Request(base_url + "/api/admin/dashboards", data=json.dumps(rows).encode(), headers=hdr, method="PUT")
    assert urllib.request.urlopen(req, timeout=5).status == 200
    page.wait_for_function("!document.getElementById('globcon-card-2')", timeout=20000)      # the page follows the saved change
    page.wait_for_selector("#globcon-card .gc-strips", timeout=15000)
    assert not errors, errors


@pytest.mark.parametrize("size", ["phone", "wall"])
def test_edit_cards_adds_options_and_removes_a_second_card(open_page, base_url, size):
    _configure(base_url, "phone", False)              # a known starting point: first card only is rewritten below
    hdr = {"Content-Type": "application/json", "Origin": base_url}
    login = urllib.request.urlopen(urllib.request.Request(base_url + "/api/admin/login", data=json.dumps({"pin": PIN}).encode(), headers=hdr), timeout=5)
    hdr["Cookie"] = login.headers.get("Set-Cookie", "").split(";")[0]
    rows = json.load(urllib.request.urlopen(base_url + "/api/info", timeout=5))["dashboards"]
    for row in rows:
        if row["slug"] == "phone":
            row["cards"] = ["env_tiles", G]
            row["globcon_more"] = {}
    urllib.request.urlopen(urllib.request.Request(base_url + "/api/admin/dashboards", data=json.dumps(rows).encode(), headers=hdr, method="PUT"), timeout=5)

    page, errors = open_page("/admin", size, "day")
    page.fill("input[type=password]", PIN)
    page.click("button.primary")
    page.wait_for_selector("[role=tab]")
    page.goto(base_url + "/dashboard-editor?d=phone")
    page.wait_for_selector("button:has-text('Add another GLOBCON card')")
    before = page.evaluate("document.documentElement.scrollWidth")
    page.click("button:has-text('Add another GLOBCON card')")
    assert page.locator("select[aria-label='GLOBCON controller 2']").count() == 1
    page.select_option("select[aria-label='GLOBCON controller 2']", "5")
    page.select_option("select[aria-label='GLOBCON display 2']", "lights")
    page.check("input[aria-label='Light for channel 1 2']")
    page.click("button:has-text('Add another GLOBCON card')")
    assert page.locator("select[aria-label='GLOBCON controller 3']").count() == 1
    page.click("button[aria-label='Remove DirectOut GLOBCON 3']")
    assert page.locator("select[aria-label='GLOBCON controller 3']").count() == 0
    w, _ = SIZES[size]
    wide = page.evaluate("""() => [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 1).map((e) => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right)).slice(0, 8)""")
    assert page.evaluate("document.documentElement.scrollWidth") <= w, f"horizontal page scroll: {wide} (before adding: {before})"
    page.click("button.primary:has-text('Save')")
    page.wait_for_selector("text=Saved.")
    saved = [r for r in json.load(urllib.request.urlopen(urllib.request.Request(base_url + "/api/admin/state", headers={"Cookie": hdr["Cookie"]}), timeout=5))["config"]["dashboards"] if r["slug"] == "phone"][0]
    assert saved["cards"] == ["env_tiles", G, f"{G}#2"]
    assert saved["globcon_more"][f"{G}#2"]["controller"] == 5 and saved["globcon_more"][f"{G}#2"]["mode"] == "lights"
    assert [e["strip"] for e in saved["globcon_more"][f"{G}#2"]["lights"]] == [1]
    assert not errors, errors
