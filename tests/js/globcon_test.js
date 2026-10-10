// Node test for the GLOBCON levels card's own logic (SW.gc in globcon.js): the bar scale, the dB text,
// the display bands, every state (live, frozen, offline, waiting, locked), the strip count, and the DOM
// updated in place. The messages are SYNTHETIC, made to match the real capture's shape (names, labels,
// whole-dB levels, -250 arriving as null). Run: node tests/js/globcon_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");

class FakeEl {
  constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this._text = ""; this.writes = 0; this.style = {}; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get className() { return this.attrs.class || ""; }
  set className(v) { this.attrs.class = String(v); this.writes++; }
  addEventListener() {}
  append(...nodes) { for (const n of nodes) { if (n instanceof FakeEl) this.children.push(n); else this._text += String(n); } }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); this.writes++; }
}
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, isFinite, window: {}, Node: FakeEl, Element: FakeEl };
ctx.document = { createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (s) => String(s) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "globcon.js"), "utf8"), ctx, { filename: "globcon.js" });
const SW = vm.runInContext("SW", ctx);
const gc = SW.gc;

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}\n  got ${g}\n  exp ${e}`); }
}

const strip = (i, db) => ({ index: i, label: `Strip ${i}`, meter: true, db });
const msg = (over = {}, cOver = {}) => Object.assign({
  status: "ok", label: "GLOBCON",
  controllers: [Object.assign({ controller: 1, name: "Desk 1", layer: 0, layer_label: "Inputs", locked: false, meters_at: 100,
    strips: [strip(0, -20), strip(1, null), strip(2, -8), strip(3, -2), strip(4, 0), strip(5, -72), strip(6, -100), strip(7, -12.5)] }, cOver)],
}, over);

// scale: -72 is empty, 0 is full, hand-calculated: (-36 + 72) / 72 = 50 %
eq(gc.pct(-72), 0, "pct floor"); eq(gc.pct(0), 100, "pct ceiling"); eq(gc.pct(-36), 50, "pct middle");
eq(gc.pct(-100), 0, "pct below clamps"); eq(gc.pct(6), 100, "pct above clamps"); eq(gc.pct(null), 0, "pct null");
eq(gc.pct(NaN), 0, "pct NaN");
// text: as received
eq(gc.fmtDb(-20), "-20", "whole dB"); eq(gc.fmtDb(-12.5), "-12.5", "one decimal"); eq(gc.fmtDb(null), "—", "null is a dash");
eq(gc.fmtDb(0), "0", "zero is zero"); eq(gc.fmtDb(undefined), "—", "undefined is a dash");
// bands: one triangle from -9, two from -3 (display guide only); boundaries and either side
eq(gc.band(-9.1), "", "below high"); eq(gc.band(-9), "high", "at high"); eq(gc.band(-3.1), "high", "below hot");
eq(gc.band(-3), "hot", "at hot"); eq(gc.band(0), "hot", "zero"); eq(gc.band(null), "", "null has no band");

// live
let v = gc.view(msg(), { controller: 1, strips: 8 }, 100.5);
eq([v.state, v.badge, v.title, v.layer, v.note], ["ok", "● LIVE", "Desk 1", "Inputs", ""], "live header");
eq(v.strips.map((s) => s.text), ["-20", "—", "-8", "-2", "0", "-72", "-100", "-12.5"], "texts as received");
eq(v.strips.map((s) => s.band), ["", "", "high", "hot", "hot", "", "", ""], "bands");
eq(v.strips.map((s) => s.mark), ["", "", "▲", "▲▲", "▲▲", "", "", ""], "every band has a symbol");
eq(v.strips[1].pct, 0, "no signal draws empty");
eq(v.strips[1].db, null, "no signal stays null, not zero");
eq(v.cls, "gc-s-ok", "live class");
// the edge of live and frozen: 3.0 s is still live, past it is frozen
eq(gc.view(msg(), { controller: 1, strips: 8 }, 103).state, "ok", "exactly 3 s is live");
v = gc.view(msg(), { controller: 1, strips: 8 }, 103.1);
eq([v.state, v.badge, v.cls], ["frozen", "▲ FROZEN", "gc-s-frozen"], "just past 3 s is frozen");
eq(v.strips.map((s) => s.band).join(""), "", "a frozen bar has no live band");
eq(v.strips[0].text, "-20", "frozen keeps the last value");
eq(/FROZEN/.test(v.note) && /a few seconds/.test(v.note), true, "frozen says so and how old");
eq(gc.view(msg(), { controller: 1, strips: 8 }, 100 + 75).note.indexOf("1 min") > 0, true, "75 s reads as 1 min");
// the link down: frozen with the age even if the clock has not moved
v = gc.view(msg({ status: "offline" }), { controller: 1, strips: 8 }, 100.2);
eq([v.state, v.strips[0].text], ["frozen", "-20"], "offline with levels is frozen, not blank");
// 4 strips
eq(gc.view(msg(), { controller: 1, strips: 4 }, 100.1).strips.length, 4, "four strips");
eq(gc.view(msg(), { controller: 1, strips: 99 }, 100.1).strips.length, 8, "never more than eight");
// other controller / nothing yet
v = gc.view(msg(), { controller: 2, strips: 8 }, 100.1);
eq([v.state, v.strips.length], ["waiting", 0], "a controller not in the message waits");
v = gc.view(msg({ status: "offline", controllers: [] }), { controller: 1, strips: 8 }, 100.1);
eq([v.state, v.badge], ["offline", "▲ OFFLINE"], "offline with nothing");
v = gc.view(null, { controller: 1, strips: 8 }, 1);
eq(v.state, "waiting", "no message");
// connected, no levels yet
v = gc.view(msg({ status: "waiting" }, { meters_at: null, strips: [] }), { controller: 1, strips: 8 }, 100);
eq([v.state, /waiting for levels/.test(v.note)], ["waiting", true], "waiting for levels");
// locked
v = gc.view(msg({}, { locked: true }), { controller: 1, strips: 8 }, 100.1);
eq([v.state, v.badge, v.strips.length], ["locked", "▲ PASSWORD NEEDED", 0], "locked");
// layer label falls back to the layer number, name to the controller number
v = gc.view(msg({}, { layer_label: "", name: "" }), { controller: 1, strips: 8 }, 100.1);
eq([v.layer, v.title], ["Layer 1", "Controller 1"], "fallback header text");
// hostile text and numbers
v = gc.view(msg({}, { strips: [{ index: 0, label: 5, meter: true, db: "loud" }, { index: 1, label: "ok", meter: true, db: Infinity }] }), { controller: 1, strips: 8 }, 100.1);
eq(v.strips.map((s) => [s.label, s.db, s.text]), [["", null, "—"], ["ok", null, "—"]], "non-numbers are empty");

// ---- channel groups (opts.range): every strip of the group by index, in order, with its channel number.
// A synthetic controller like the real one: 1-2 input manager, 3-8 flex channel (meters), 9-16 USB (no meter).
const full = [];
for (let i = 0; i < 16; i++) full.push({ index: i, label: i < 2 ? `Input Manager #${i + 1}` : i < 8 ? `Flex Channel ${i + 1}` : `USB ${i - 7}`, meter: i < 8, db: i < 8 ? (i === 3 ? null : -20 - i) : null });
const rm = msg({}, { strips: full });
const groups = { "1-4": [1, 4], "5-8": [5, 8], "1-8": [1, 8], "9-12": [9, 12], "13-16": [13, 16], "9-16": [9, 16], "1-16": [1, 16] };
for (const [r, [a, b]] of Object.entries(groups)) {
  v = gc.view(rm, { controller: 1, strips: 8, range: r }, 100.1);
  const nums = []; for (let n = a; n <= b; n++) nums.push(n);
  eq(v.strips.map((s) => s.num), nums, `range ${r} channel numbers`);
  eq(v.strips.map((s) => s.label), nums.map((n) => full[n - 1].label), `range ${r} labels`);
  eq(v.state, "ok", `range ${r} is live`);
}
eq(Object.keys(gc.RANGES), Object.keys(groups), "the seven groups");
v = gc.view(rm, { controller: 1, strips: 8, range: "9-12" }, 100.1);
eq(v.strips.map((s) => [s.text, s.pct, s.db, s.isWord]), [["no meter", 0, null, true], ["no meter", 0, null, true], ["no meter", 0, null, true], ["no meter", 0, null, true]], "no meter: empty bar and a word, never zero");
v = gc.view(rm, { controller: 1, strips: 8, range: "1-4" }, 100.1);
eq(v.strips.map((s) => s.text), ["-20", "-21", "-22", "no reading"], "a metered strip with no reading says so");
eq(v.strips[3].pct, 0, "no reading is an empty bar");
// a strip GLOBCON has told us nothing about is blank, still empty and never zero
v = gc.view(msg({}, { strips: [strip(4, -10)] }), { controller: 1, strips: 8, range: "5-8" }, 100.1);
eq(v.strips.map((s) => [s.num, s.label, s.text, s.pct]), [[5, "Strip 4", "-10", 86.11111111111111], [6, "", "no reading", 0], [7, "", "no reading", 0], [8, "", "no reading", 0]], "missing strips are blank");
// frozen with a group keeps the last levels
eq(gc.view(rm, { controller: 1, strips: 8, range: "1-4" }, 110).state, "frozen", "a group freezes like the rest");
// unknown range -> the older behaviour (first N with a meter)
v = gc.view(rm, { controller: 1, strips: 4, range: "2-3" }, 100.1);
eq(v.strips.map((s) => s.num), [1, 2, 3, 4], "an unknown range falls back to the older behaviour");
// the older behaviour skips strips with no meter and numbers them by index
v = gc.view(msg({}, { strips: [strip(0, -1), { index: 1, label: "x", meter: false, db: null }, strip(2, -2)] }), { controller: 1, strips: 8 }, 100.1);
eq(v.strips.map((s) => [s.num, s.text]), [[1, "-1"], [3, "-2"]], "older setting: only strips with a meter");

// the DOM: built once, updated in place, text only
const ui = gc.createUi();
ui.update(gc.view(msg(), { controller: 1, strips: 8 }, 100.1));
const cols = ui.strips.children.slice();
eq(cols.length, 8, "eight columns built");
const writes = cols[0].children[1].firstChild.writes;
ui.update(gc.view(msg(), { controller: 1, strips: 8 }, 100.2));
eq(ui.strips.children[0] === cols[0], true, "columns are reused, not rebuilt");
eq(cols[0].children[1].firstChild.writes, writes, "an unchanged level writes nothing");
eq(ui.head.textContent, "Desk 1", "title in the heading");
eq(ui.layer.textContent, "Layer: Inputs", "layer line");
eq(cols[3].children[2].children[0].textContent, "-2", "value text");
eq(cols[3].children[2].children[1].textContent, "▲▲", "symbol text");
eq(cols[3].className.indexOf("gc-hot") > 0, true, "hot class");
eq(cols[1].className.indexOf("gc-none") > 0, true, "no-signal class");
ui.update(gc.view(msg(), { controller: 1, strips: 4 }, 100.2));
eq(ui.strips.children.length, 4, "strip count change rebuilds");
ui.update(gc.view(rm, { controller: 1, strips: 8, range: "1-16" }, 100.2));
eq([ui.strips.children.length, ui.strips.getAttribute("data-n")], [16, "16"], "sixteen columns for 1-16");
eq(ui.strips.children[8].children[0].children[0].textContent, "9", "channel number shown");
eq(ui.strips.children[8].children[0].children[1].textContent, "USB 1", "label shown");
eq(ui.strips.children[8].children[2].children[0].textContent, "no meter", "word instead of a number");
eq(ui.strips.children[8].className.indexOf("gc-word") > 0, true, "word class");

if (fails) { console.log(`${fails} of ${count} checks FAILED`); process.exit(1); }
console.log(`ok: ${count} checks passed`);