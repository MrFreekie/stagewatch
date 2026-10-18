// Node test for the chart's marker flags (SW.chart.groupFlags, SW.chart.fitText), the source -> look
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

// ---- flags: one row, grouped when they would overlap (SW.chart.groupFlags)
const W = 1000, GAP = 4;
const gw = (n) => 100;   // "N markers" flag width
const gf = (xs, ws) => SW.chart.groupFlags(xs, ws, W, { groupWidth: gw });
function clashes(groups) {
  for (let i = 1; i < groups.length; i++) if (groups[i].lx < groups[i - 1].lx + groups[i - 1].w + GAP) return true;
  return false;
}
const inside = (groups) => groups.every((g) => g.lx >= 0 && g.lx + g.w <= W);

eq(gf([100], [90]), [{ indices: [0], lx: 100, w: 90 }], "lone flag starts at its line");
eq(gf([50, 300, 600], [90, 90, 90]).map((g) => g.indices), [[0], [1], [2]], "spread flags stay single");
eq(gf([100, 110, 120], [90, 90, 90]), [{ indices: [0, 1, 2], lx: 100, w: 100 }], "three close flags become one '3 markers' flag");
eq(gf([300, 100], [90, 90]).map((g) => g.indices), [[1], [0]], "groups come out left to right");
eq(gf([990], [60]), [{ indices: [0], lx: 930, w: 60 }], "flips left of its line at the right edge");
eq(gf([10], [2000]), [{ indices: [0], lx: 0, w: 1000 }], "a flag wider than the plot is capped to it");
eq(gf([960, 990], [60, 60]).length, 1, "two flags at the right edge group, not overlap");
// a single flag next to a group that grows into it gets merged as well
eq(gf([100, 105, 195], [90, 20, 20]).length, 1, "a grown group swallows the flag it now reaches");

let seed = 7;
const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
for (const n of [2, 10, 40, 500]) {
  const xs = [], ws = [];
  for (let i = 0; i < n; i++) { xs.push(Math.round(rnd() * W)); ws.push(30 + Math.round(rnd() * 150)); }
  const t0 = Date.now(), g = gf(xs, ws);
  ok(Date.now() - t0 < 100, `${n} flags lay out quickly`);
  ok(!clashes(g), `${n} random flags never overlap`);
  ok(inside(g), `${n} random flags stay within the plot`);
  eq(g.reduce((a, x) => a + x.indices.length, 0), n, `${n} random flags: every marker counted once`);
}

// ---- flag text fits with an ellipsis
const m10 = (t) => 10 * t.length;
eq(SW.chart.fitText("Doors", 100, m10), "Doors", "short text unchanged");
eq(SW.chart.fitText("Headline on stage", 60, m10), "Headl…", "long text cut with an ellipsis");
ok(m10(SW.chart.fitText("Headline on stage", 60, m10)) <= 60, "cut text fits");

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
