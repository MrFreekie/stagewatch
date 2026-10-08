// Node test for the chart's marker tab layout (TimeChart.layoutMarkers), the source -> look
// mapping (SW.markerStyle) and the contrast of the marker colour tokens in style.css.
// Run: node tests/js/marker_layout_test.js   (tests/test_marker_layout.py runs it when node is installed)
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "chart.js"), "utf8"), ctx, { filename: "chart.js" });
const SW = vm.runInContext("SW", ctx);
const TimeChart = vm.runInContext("TimeChart", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
function ok(cond, what) { eq(!!cond, true, what); }

const measure = (l) => 10 * l.length + 20;   // fixed-width "font": 10 px a letter plus 20 px of glyph and padding
const W = 1000, GAP = 4;
const lay = (items, rows = 3) => TimeChart.layoutMarkers(items, measure, W, rows);
const mk = (x, label, selected) => ({ x, label, selected: !!selected });

// no two full tabs in the same row overlap, and each keeps the 4 px gap
function overlaps(items, lanes) {
  const byRow = {};
  lanes.forEach((l) => { if (!l.compact) (byRow[l.row] = byRow[l.row] || []).push(l); });
  for (const r of Object.keys(byRow)) {
    const a = byRow[r].slice().sort((p, q) => p.lx - q.lx);
    for (let i = 1; i < a.length; i++) if (a[i].lx < a[i - 1].lx + a[i - 1].w + GAP) return true;
  }
  return false;
}

// ---- a lone marker, and a spread-out set, stay in row 0
eq(lay([mk(100, "Aligned")]), [{ row: 0, lx: 100, w: 90, compact: false, flipped: false }], "single marker");
let items = [mk(50, "Doors"), mk(300, "Support"), mk(600, "Headline")];
eq(lay(items).map((l) => l.row), [0, 0, 0], "spread markers share row 0");

// ---- crowded markers go to later rows, with no overlaps
items = [mk(100, "Aligned"), mk(110, "Gain up"), mk(120, "Mics on"), mk(130, "Check")];
let lanes = lay(items);
eq(lanes.map((l) => l.row), [0, 1, 2, 3], "four stacked markers: three rows, then compact");
eq(lanes.map((l) => l.compact), [false, false, false, true], "fourth is compact");
ok(!overlaps(items, lanes), "no overlaps (crowded)");

items = [];
for (let i = 0; i < 40; i++) items.push(mk(20 + i * 23, "Mark " + i));
lanes = lay(items);
ok(!overlaps(items, lanes), "no overlaps (40 markers)");
ok(lanes.some((l) => l.compact), "40 markers on 1000 px need compact tabs");
ok(lanes.every((l) => l.row <= 3), "never more than 3 rows plus the compact row");

// a randomised pass, 500 markers: no overlaps, every marker placed, quick
let seed = 7;
const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
items = [];
for (let i = 0; i < 500; i++) items.push(mk(Math.round(rnd() * W), "M" + Math.floor(rnd() * 1000), i === 250));
const t0 = Date.now();
lanes = lay(items);
ok(Date.now() - t0 < 100, "500 markers lay out quickly");
ok(!overlaps(items, lanes), "no overlaps (500 markers)");
ok(lanes.every((l) => l && typeof l.row === "number"), "every marker gets a place");
eq(lanes[250].row, 0, "selected one of 500 is in row 0");
eq(lanes[250].compact, false, "selected one of 500 is a full label");

// ---- compact tabs
lanes = lay([mk(100, "A"), mk(100, "B"), mk(100, "C"), mk(100, "D")]);
eq(lanes[3], { row: 3, lx: 93, w: 14, compact: true, flipped: false }, "compact tab is 14 px wide, centred on its line");
eq(lay([mk(2, "A"), mk(2, "B"), mk(2, "C"), mk(2, "D")])[3].lx, 0, "compact tab stays inside the left edge");
eq(lay([mk(999, "A"), mk(999, "B"), mk(999, "C"), mk(999, "D")])[3].lx, 986, "compact tab stays inside the right edge");
eq(lay([mk(100, "A"), mk(100, "B")], 1)[1].compact, true, "one row: the second goes compact");

// ---- the selected marker is always a full label in row 1 (index 0)
items = [mk(100, "Aligned"), mk(105, "Gain up"), mk(110, "Mics on"), mk(115, "Check"), mk(120, "Selected one", true)];
lanes = lay(items);
eq(lanes[4].row, 0, "selected marker in row 1");
eq(lanes[4].compact, false, "selected marker is a full label");
ok(!overlaps(items, lanes), "no overlaps around the selected marker");
ok(lanes.slice(0, 4).every((l) => l.row !== 0 || l.lx + l.w + GAP <= lanes[4].lx || l.lx >= lanes[4].lx + lanes[4].w + GAP),
  "others keep clear of the selected label in row 1");
items = [mk(100, "A"), mk(100, "B"), mk(100, "C"), mk(100, "D"), mk(100, "Picked", true)];
lanes = lay(items);
eq(lanes[4].compact, false, "selected stays full even when everything else is crowded");
eq(lanes.filter((l) => l.compact).length, 2, "two of the others are displaced to compact tabs");

// ---- right-edge flip
eq(lay([mk(990, "Late")])[0], { row: 0, lx: 930, w: 60, compact: false, flipped: true }, "flips left of its line near the right edge");
eq(lay([mk(940, "Late")])[0].flipped, false, "just fits: no flip");
eq(lay([mk(941, "Late")])[0].flipped, true, "one pixel over: flip");
lanes = lay([mk(960, "Nearly"), mk(990, "Edge")]);
ok(!overlaps([], lanes), "flipped label does not overlap its neighbour");

// ---- source mapping
const key = (s) => SW.markerStyle(s).key;
eq(key("dashboard"), "crew", "dashboard");
eq(key("dashboard:foh"), "crew", "dashboard:slug");
eq(key("admin"), "crew", "admin");
eq(key("hub"), "system", "hub");
eq(key("updater"), "system", "updater");
eq(key("alarm"), "alarm", "alarm");
eq(key("schedule"), "schedule", "schedule");
eq(key("contact"), "device", "contact");
eq(key("device"), "device", "device");
eq(key("Alarm"), "alarm", "case-insensitive");
eq(key("something-new"), "crew", "unknown source is a crew mark");
eq(key(""), "crew", "empty source");
eq(key(null), "crew", "null source");
eq(key(undefined), "crew", "missing source");
eq(key("constructor"), "crew", "prototype names are not sources");
eq(Object.keys(SW.MARKER_STYLES).map((k) => SW.MARKER_STYLES[k].glyph).filter((g, i, a) => a.indexOf(g) === i).length, 5,
  "five different glyphs: colour is never the only signal");
eq(SW.markerStyle("dashboard").glyph, "▼", "crew is a down triangle");
eq(SW.markerStyle("alarm").glyph, "▲", "alarm is an up triangle");

// ---- the CSS tokens exist for both themes and the text on each reads at 4.5:1 or better
const css = fs.readFileSync(path.join(STATIC, "style.css"), "utf8");
const lum = (hex) => {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const c = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const darkBlock = css.slice(css.indexOf(":root {"), css.indexOf("@media (prefers-color-scheme: light)"));
const lightStart = css.indexOf("@media (prefers-color-scheme: light)");
const lightBlock = css.slice(lightStart, css.indexOf("}", css.indexOf("}", lightStart) + 1));
for (const [theme, block] of [["dark", darkBlock], ["light", lightBlock]]) {
  for (const k of Object.keys(SW.MARKER_STYLES)) {
    const bg = new RegExp(`--marker-${k}:\\s*(#[0-9a-fA-F]{3,6})`).exec(block);
    const ink = new RegExp(`--marker-${k}-ink:\\s*(#[0-9a-fA-F]{3,6})`).exec(block);
    ok(bg && ink, `${theme} theme defines --marker-${k} and its ink`);
    if (bg && ink) {
      const c = contrast(bg[1], ink[1]);
      ok(c >= 4.5, `${theme} ${k}: ${ink[1]} on ${bg[1]} is ${c.toFixed(2)}:1`);
    }
  }
}

// ---- the chart's y range: stays on the default while the data fits, grows only where it must
const near = (a, b) => Math.abs(a - b) < 1e-6;
const yr = (lo, hi, def) => TimeChart.yRange(lo, hi, def);
eq(yr(18, 24, [10, 30]), [10, 30], "data inside the default: the axis stays exactly on it");
eq(yr(10, 30, [10, 30]), [10, 30], "data touching the default edges: unchanged");
{ const [lo, hi] = yr(18, 34, [10, 30]); ok(lo === 10 && hi > 34 && hi < 36, "hotter than the default: only the top grows, with a little room"); }
{ const [lo, hi] = yr(2, 24, [10, 30]); ok(hi === 30 && lo < 2 && lo > -3, "colder than the default: only the bottom grows"); }
{ const [lo, hi] = yr(2, 40, [10, 30]); ok(lo < 2 && hi > 40, "both sides out: both grow"); }
{ const [lo, hi] = yr(20, 20, null); ok(near(lo, 19.42) && near(hi, 20.58), "no default, one value: a little either side as before"); }
{ const [lo, hi] = yr(10, 20, undefined); ok(near(lo, 9.2) && near(hi, 20.8), "no default: fit the data with 8 % room as before"); }
eq(yr(5, 6, [30, 10]), yr(5, 6, null), "a backwards default is ignored");

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
