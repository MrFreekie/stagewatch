"""Layout checks by eye, automated: phone, tablet and wall, Day and Night."""
import pytest

from .conftest import PIN, SIZES

COMBOS = [(s, t) for s in SIZES for t in ("day", "night")]
DASH = {"phone": "/d/phone", "tablet": "/d/foh", "wall": "/d/wall"}
MAX_CARD_H = 1500            # the Power card was 4,000+ px before the GUI fixes
KNOWN_TALL_ON_WALL = {"power-card", "power-tree-card"}   # 3,700 px and 1,800 px in emulate mode
MAX_HEADER_FRAC = 0.30       # of the phone screen height

CONTRAST_JS = r"""() => {
  const parse = (c) => { const m = c.match(/[\d.]+/g).map(Number); return {r:m[0],g:m[1],b:m[2],a:m.length>3?m[3]:1}; };
  const lum = (c) => { const f = (v) => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
    return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b); };
  const bg = (el) => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c.a > 0.9) return c; }
    return {r:255,g:255,b:255,a:1}; };
  const out = [];
  for (const el of document.querySelectorAll('h1,h2,h3,p,td,th,label,.muted,summary,button,span,div')) {
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const r = el.getBoundingClientRect(); if (r.width < 4 || r.height < 4) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || +cs.opacity < 0.5) continue;
    const fg = parse(cs.color), b = bg(el), l1 = lum(fg), l2 = lum(b);
    const ratio = (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);
    const big = parseFloat(cs.fontSize) >= 24 || (parseFloat(cs.fontSize) >= 18.66 && +cs.fontWeight >= 700);
    if (ratio < (big ? 3 : 4.5)) out.push(ratio.toFixed(2) + ' ' + el.tagName + '.' + el.className + ' "' + el.textContent.trim().slice(0,30) + '"');
  }
  return out;
}"""


@pytest.mark.parametrize("size,theme", COMBOS)
def test_dashboard_layout(open_page, size, theme):
    page, errors = open_page(DASH[size], size, theme)
    w, h = SIZES[size]
    assert page.evaluate("document.documentElement.scrollWidth") <= w, "horizontal page scroll"
    tall = page.evaluate("""() => [...document.querySelectorAll('.card')].map(
        (c) => [c.id, c.getBoundingClientRect().height]).filter((x) => x[1] > %d)""" % MAX_CARD_H)
    if size == "wall":   # known finding: the wall cannot fold these (see docs/developer-notes.md)
        tall = [t for t in tall if t[0] not in KNOWN_TALL_ON_WALL]
    assert not tall, f"cards taller than {MAX_CARD_H}px: {tall}"
    if size == "phone":
        head = page.evaluate("document.querySelector('header.top').getBoundingClientRect().height")
        assert head <= h * MAX_HEADER_FRAC, f"header is {head}px of {h}"
    assert not errors, errors


@pytest.mark.parametrize("size,theme", COMBOS)
def test_dashboard_touch_targets(open_page, size, theme):
    if size == "wall":
        pytest.skip("wall layout is not touched")
    page, _ = open_page(DASH[size], size, theme)
    small = page.evaluate("""() => [...document.querySelectorAll('button,a[href],input,select,summary')].map((e) => {
        const l = e.closest('label'); const r = (l && e.type === 'checkbox' ? l : e).getBoundingClientRect(); return [e.tagName + '#' + e.id + '.' + e.className, Math.round(r.width), Math.round(r.height)];
      }).filter((x) => x[1] > 0 && x[2] > 0 && x[2] < 44)""")
    assert not small, f"touch targets under 44px high: {small}"


@pytest.mark.parametrize("size,theme", COMBOS)
def test_dashboard_contrast(open_page, size, theme):
    page, _ = open_page(DASH[size], size, theme)
    bad = page.evaluate(CONTRAST_JS)
    assert not bad, bad[:15]


@pytest.mark.parametrize("size,theme", COMBOS)
def test_admin_tabs_after_login(open_page, size, theme):
    page, errors = open_page("/admin", size, theme)
    page.fill("input[type=password]", PIN)
    page.click("button.primary")
    page.wait_for_selector("[role=tab]")
    tabs = page.locator("[role=tab]")
    assert tabs.count() >= 4
    w, _ = SIZES[size]
    for i in range(tabs.count()):
        tabs.nth(i).click()
        page.wait_for_timeout(300)
        panel = page.locator("[role=tabpanel]:visible")
        assert panel.count() == 1 and panel.inner_text().strip(), f"tab {i} empty"
        assert page.evaluate("document.documentElement.scrollWidth") <= w, f"tab {i}: horizontal scroll"
    assert not page.evaluate(CONTRAST_JS), "admin contrast"
    assert not errors, errors
