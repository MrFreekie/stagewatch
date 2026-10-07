// Node test for the Wall Clock card's own logic (SW.wc in wallclock.js): the one view function
// every style draws from, the 12-hour and 24-hour edges, the 7-segment table, the tick timing,
// and the three faces drawn into a tiny fake DOM (in-place updates, lit LEDs, ghosts, states).
// Run: node tests/js/wall_clock_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");

// ---- a minimal DOM: enough for SW.h, createElementNS, attributes, text and children
class FakeEl {
  constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this._text = ""; this.writes = 0; this.listeners = {}; }
  setAttribute(k, v) { this.attrs[k] = String(v); this.writes++; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get className() { return this.attrs.class || ""; }
  set className(v) { this.attrs.class = String(v); }
  addEventListener() {}
  append(...nodes) { for (const n of nodes) { if (n instanceof FakeEl) this.children.push(n); else this._text += String(n); } }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); this.writes++; }
}
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, window: {}, Node: FakeEl, Element: FakeEl };
ctx.document = { createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (s) => String(s) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "wallclock.js"), "utf8"), ctx, { filename: "wallclock.js" });
const SW = vm.runInContext("SW", ctx);
const wc = SW.wc;

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

// ---- styles
eq(["digits", "ring", "segments"].map(wc.style), ["digits", "ring", "segments"], "known styles");
eq([undefined, null, "", "Ring", "hologram", 5].map(wc.style), ["digits", "digits", "digits", "digits", "digits", "digits"], "unknown style is digits");

// ---- time of day, 24 h and 12 h
const at = (h, m, s) => h * 3600 + m * 60 + s;
const t24 = (sec) => { const p = wc.timeParts(sec, false); return `${p.hh}:${p.mm}:${p.ss}${p.suffix}`; };
const t12 = (sec) => { const p = wc.timeParts(sec, true); return `${p.hh}:${p.mm}:${p.ss} ${p.suffix}`; };
eq(t24(at(0, 0, 0)), "00:00:00", "24 h midnight");
eq(t24(at(23, 59, 59)), "23:59:59", "24 h last second");
eq(t24(at(9, 5, 7)), "09:05:07", "24 h pads");
eq(t12(at(0, 0, 0)), "12:00:00 am", "12 h midnight is 12 am");
eq(t12(at(0, 30, 0)), "12:30:00 am", "12 h after midnight");
eq(t12(at(12, 0, 0)), "12:00:00 pm", "12 h noon is 12 pm");
eq(t12(at(11, 59, 59)), "11:59:59 am", "12 h last second of the morning");
eq(t12(at(13, 0, 0)), "1:00:00 pm", "12 h no leading zero");
eq(t12(at(23, 59, 59)), "11:59:59 pm", "12 h last second of the day");
eq(t12(at(9, 5, 7)), "9:05:07 am", "12 h single-digit hour");
eq([wc.daySeconds(0), wc.daySeconds(86399999), wc.daySeconds(86400000), wc.daySeconds(-1000), wc.daySeconds(90000000)],
  [0, 86399, 0, 86399, 3600], "day seconds wrap into one day");

// ---- 7-segment table: the standard digits, a-g only
const lit = { 0: 6, 1: 2, 2: 5, 3: 5, 4: 4, 5: 5, 6: 6, 7: 3, 8: 7, 9: 6 };
for (const d of Object.keys(lit)) {
  eq(wc.SEGMENTS[d].length, lit[d], `digit ${d} lights ${lit[d]} segments`);
  eq(/^[a-g]+$/.test(wc.SEGMENTS[d]) && new Set(wc.SEGMENTS[d]).size === wc.SEGMENTS[d].length, true, `digit ${d} uses distinct a-g`);
}
eq(wc.SEGMENTS["1"], "bc", "one is the two right segments");
eq(wc.SEGMENTS["8"], "abcdefg", "eight is all seven");
eq(Object.keys(wc.SEGMENTS).length, 10, "ten digits");

// ---- the view
const NOW = 1790000000;
const msg = (o) => Object.assign({ source: "ontime", label: "Ontime", clock_ms: at(14, 5, 9) * 1000, received_at: NOW, status: "ok",
  offset_s: 0.5, warn: false, display: {} }, o || {});
let v = wc.view(msg(), NOW);
eq([v.state, v.digits, v.suffix, v.level, v.cls, v.note, v.date], ["live", "14:05:09", "", "", "", "Matches Stagewatch", ""], "live");
v = wc.view(msg(), NOW + 2.4);
eq([v.state, v.digits], ["live", "14:05:11"], "live time runs on from the reading (age 2.4 s)");
v = wc.view(msg({ source: "pc", label: "Stagewatch PC", offset_s: 0 }), NOW);
eq([v.state, v.note], ["live", "Stagewatch's own clock"], "PC source note");
v = wc.view(msg({ offset_s: 3.2, warn: true }), NOW);
eq([v.state, v.level, v.cls, v.note], ["differs", "warn", "wc-differs", "▲ Differs from Stagewatch by +3.2 s (Ontime is ahead)"], "differs ahead");
v = wc.view(msg({ offset_s: -3.2, warn: true }), NOW);
eq(v.note, "▲ Differs from Stagewatch by -3.2 s (Ontime is behind)", "differs behind");
v = wc.view(msg({ offset_s: 3600, warn: true }), NOW);
eq(v.note, "▲ Differs from Stagewatch by +1 h (Ontime is ahead). Check the time zones.", "whole hours hint");
v = wc.view(msg({ offset_s: 125, warn: true }), NOW);
eq(v.note.indexOf("+2 min 5 s") > 0, true, "minutes and seconds");
v = wc.view(msg(), NOW + 4);
eq([v.state, v.digits, v.cls, v.level], ["stale", "14:05:09", "wc-stale", "warn"], "stale: frozen at the last reading");
eq(v.note, "▲ Stale: nothing from Ontime for a few seconds. Don't trust this time.", "stale note");
eq(wc.view(msg(), NOW + 20).note, "▲ Stale: nothing from Ontime for 20 s. Don't trust this time.", "stale 20 s");
eq(wc.view(msg(), NOW + 120).note, "▲ Stale: nothing from Ontime for 2 min. Don't trust this time.", "stale 2 min");
eq(wc.view(msg(), NOW + 3).state, "live", "3 s old is still live");
v = wc.view(msg({ status: "offline", clock_ms: null, offset_s: null }), NOW);
eq([v.state, v.digits, v.sec, v.cls, v.note], ["off", "--:--:--", null, "wc-off", "▲ Ontime offline: no time to show"], "offline");
v = wc.view(msg({ status: "error", clock_ms: null }), NOW);
eq([v.state, v.note], ["off", "▲ Ontime sent a time we can't read"], "error");
v = wc.view(msg({ status: "ok", clock_ms: null }), NOW);
eq(v.state, "off", "ok without a time is offline");
v = wc.view(msg({ status: "offline", clock_ms: null, label: "Stagewatch PC", source: "pc" }), NOW);
eq(v.note, "▲ Stagewatch PC offline: no time to show", "offline names the source");
eq(wc.view(null, NOW).state, "off", "no message at all is offline, not a crash");
eq(wc.view(msg({ display: undefined }), NOW).opts, { hour12: false, showDate: false, ring: "sweep", colonBlink: false }, "no display options: defaults");
eq(wc.view(msg({ display: { ring: "pulse", hour12: "yes" } }), NOW).opts, { hour12: false, showDate: false, ring: "sweep", colonBlink: false }, "odd options: defaults");
eq(wc.view(msg({ display: { ring: "fill", hour12: true, show_date: true, colon_blink: true } }), NOW).opts,
  { hour12: true, showDate: true, ring: "fill", colonBlink: true }, "options read");
v = wc.view(msg({ display: { hour12: true }, clock_ms: at(0, 0, 0) * 1000 }), NOW);
eq([v.digits, v.suffix], ["12:00:00", "am"], "12 h midnight in the view");
v = wc.view(msg({ display: { hour12: true }, status: "offline", clock_ms: null }), NOW);
eq([v.digits, v.suffix], ["--:--:--", ""], "offline has no am/pm");
v = wc.view(msg({ display: { show_date: true } }), NOW);
eq(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2} \d{4}$/.test(v.date), true, "date is British, from Stagewatch's site date");
eq(wc.view(msg({ display: { show_date: true } }), NOW + 10).date, "", "no date when stale");
eq(wc.view(msg({ display: { show_date: true }, status: "offline", clock_ms: null }), NOW).date, "", "no date when offline");

// ---- stale, offline and differs are never colour alone: words with a triangle in every case
for (const m of [msg({ offset_s: 3.2, warn: true }), msg(), msg({ status: "offline", clock_ms: null })]) {
  const vv = wc.view(m, m.status === "ok" && m.warn === false ? NOW + 10 : NOW);
  eq(vv.note.indexOf("▲") === 0, vv.level === "warn", `${vv.state} has a triangle note when it warns`);
}

// ---- tick timing: just after the shown second changes
eq(wc.nextDelayMs(msg({ clock_ms: 10500 }), NOW), 510, "shown time 10.5 s: next second in 0.5 s");
eq(wc.nextDelayMs(msg({ clock_ms: 10000 }), NOW), 1010, "just on a second: a full second");
eq(wc.nextDelayMs(msg({ clock_ms: 10900 }), NOW + 0.05), 1000 - 950 + 10, "the shown time moves on with age");
eq(wc.nextDelayMs(msg({ clock_ms: 10500 }), NOW + 10), 1010 - (((NOW + 10) * 1000) % 1000), "stale: follows our own clock");
eq(wc.nextDelayMs(null, NOW) >= 30 && wc.nextDelayMs(null, NOW) <= 1100, true, "no message: still a sane delay");
eq(wc.nextDelayMs(msg({ clock_ms: 10999.9 }), NOW) >= 30, true, "never spins");

// ---- faces
const view = (m, now) => wc.view(m, now === undefined ? NOW : now);
const O = { ring: "sweep", colonBlink: false, reduced: false };
const ledClasses = (face) => face.el.children[0].children.filter((c) => c.tagName === "circle").map((c) => c.attrs.class);
const litIndexes = (face, cls) => ledClasses(face).map((c, i) => (c.split(" ").indexOf(cls) >= 0 ? i : -1)).filter((i) => i >= 0);

// ring, sweep
let ring = wc.createFace("ring");
ring.update(view(msg({ clock_ms: at(14, 5, 30) * 1000 })), O);
eq(ledClasses(ring).length, 60, "ring has 60 LEDs");
eq(ledClasses(ring).filter((c) => c.indexOf("hr") >= 0).length, 12, "ring has 12 hour markers");
eq([litIndexes(ring, "on"), litIndexes(ring, "t1"), litIndexes(ring, "t2")], [[30], [29], [28]], "sweep: one light and a two-LED trail");
eq(ledClasses(ring)[30], "led hr on", "a lit hour marker keeps its marker class");
ring.update(view(msg({ clock_ms: at(14, 6, 0) * 1000 })), O);
eq([litIndexes(ring, "on"), litIndexes(ring, "t1"), litIndexes(ring, "t2")], [[0], [59], [58]], "sweep wraps from :59 to :00");
// in place: one second later only the changed LEDs are written
ring.update(view(msg({ clock_ms: at(14, 6, 1) * 1000 })), O);
const writes0 = ring.el.children[0].children.filter((c) => c.tagName === "circle").reduce((a, c) => a + c.writes, 0);
ring.update(view(msg({ clock_ms: at(14, 6, 2) * 1000 })), O);
const writes1 = ring.el.children[0].children.filter((c) => c.tagName === "circle").reduce((a, c) => a + c.writes, 0);
eq(writes1 - writes0 <= 4, true, "a ring tick writes at most four LEDs");
ring.update(view(msg({ clock_ms: at(14, 6, 2) * 1000 })), O);
eq(ring.el.children[0].children.filter((c) => c.tagName === "circle").reduce((a, c) => a + c.writes, 0), writes1 + 0, "an unchanged second writes nothing to the LEDs");
// fill
ring = wc.createFace("ring");
ring.update(view(msg({ clock_ms: at(14, 5, 3) * 1000 })), { ring: "fill", colonBlink: false, reduced: false });
eq(litIndexes(ring, "on"), [0, 1, 2, 3], "fill: LEDs 0 to the second are lit");
ring.update(view(msg({ clock_ms: at(14, 6, 0) * 1000 })), { ring: "fill", colonBlink: false, reduced: false });
eq(litIndexes(ring, "on"), [0], "fill: cleared at :00");
// states
ring.update(view(msg(), NOW + 20), O);
eq([litIndexes(ring, "on").length, litIndexes(ring, "t1").length, ring.el.attrs.class], [0, 0, "wc-face wc-ring wc-s-stale"], "stale: ring dark except hour markers and dots");
eq(ledClasses(ring).filter((c) => c.indexOf("hr") >= 0).length, 12, "stale: hour markers stay");
ring.update(view(msg({ status: "offline", clock_ms: null })), O);
eq([litIndexes(ring, "on").length, ring.el.attrs.class], [0, "wc-face wc-ring wc-s-off"], "offline: ring dark");
ring.update(view(msg({ offset_s: 3.2, warn: true })), O);
eq(ring.el.attrs.class, "wc-face wc-ring wc-s-differs", "differs: face carries the class for the dashed outline");
eq(ring.el.children[0].textContent.indexOf("14:05:09") >= 0, true, "ring shows the time");
ring.update(view(msg({ status: "offline", clock_ms: null })), O);
eq(ring.el.children[0].textContent.indexOf("--:--:--") >= 0, true, "ring offline shows dashes");
eq(ring.el.children[0].attrs["aria-label"], "Wall Clock --:--:--", "ring label follows the view");
// colons
ring.update(view(msg({ clock_ms: at(14, 5, 9) * 1000 })), { ring: "sweep", colonBlink: true, reduced: false });
const colonCls = () => ring.el.children[0].children.filter((c) => c.tagName === "text")[0].children.filter((c) => c.attrs.class && c.attrs.class.indexOf("colon") === 0).map((c) => c.attrs.class);
eq(colonCls(), ["colon blink-off", "colon blink-off"], "colons blink on odd seconds");
ring.update(view(msg({ clock_ms: at(14, 5, 10) * 1000 })), { ring: "sweep", colonBlink: true, reduced: false });
eq(colonCls(), ["colon", "colon"], "colons lit on even seconds");
ring.update(view(msg({ clock_ms: at(14, 5, 9) * 1000 })), { ring: "sweep", colonBlink: true, reduced: true });
eq(colonCls(), ["colon", "colon"], "reduced motion: colons never blink");
ring.update(view(msg({ clock_ms: at(14, 5, 9) * 1000 })), { ring: "sweep", colonBlink: false, reduced: false });
eq(colonCls(), ["colon", "colon"], "blink off by default");

// segments
const seg = wc.createFace("segments");
const svg = seg.el.children[0];
const digitGroups = () => svg.children.filter((c) => c.tagName === "g" && c.attrs.transform);
const digitLit = (i) => digitGroups()[i].children.map((p) => (p.attrs.class === "seg on" ? "x" : ".")).join("");
const order = ["a", "g", "d", "f", "b", "e", "c"];                      // the order the polygons are drawn in
const expectLit = (ch) => order.map((k) => ((wc.SEGMENTS[ch] || "").indexOf(k) >= 0 ? "x" : ".")).join("");
seg.update(view(msg({ clock_ms: at(12, 34, 56) * 1000 })), O);
eq(digitGroups().length, 6, "six digits");
eq(svg.children.filter((c) => c.tagName === "g" && c.attrs.class && c.attrs.class.indexOf("colon") === 0).length, 2, "two colons");
eq(digitGroups().every((g) => g.children.length === 7), true, "seven segments each, always drawn (unlit ones are ghosts)");
eq(["1", "2", "3", "4", "5", "6"].map((ch, i) => digitLit(i) === expectLit(ch)), [true, true, true, true, true, true], "12:34:56 lights the right segments");
const w0 = digitGroups().map((g) => g.children.reduce((a, p) => a + p.writes, 0));
seg.update(view(msg({ clock_ms: at(12, 34, 57) * 1000 })), O);
const w1 = digitGroups().map((g) => g.children.reduce((a, p) => a + p.writes, 0));
eq(w1.map((x, i) => x - w0[i]).map((d, i) => (i < 5 ? d : d > 0 ? 1 : 0)), [0, 0, 0, 0, 0, 1], "a tick touches only the digit that changed");
seg.update(view(msg({ display: { hour12: true }, clock_ms: at(9, 5, 7) * 1000 })), O);
eq(digitLit(0), ".......", "12 h: the missing leading zero is a blank digit");
eq(digitLit(1) === expectLit("9"), true, "12 h: the hour digit");
eq(seg.el.children[1].textContent, "am", "12 h: am text");
seg.update(view(msg({ display: { hour12: true }, clock_ms: at(21, 5, 7) * 1000 })), O);
eq([digitLit(0) === expectLit("0") || digitLit(0) === ".......", digitLit(1) === expectLit("9"), seg.el.children[1].textContent], [true, true, "pm"], "12 h: 21:05 is 9 pm");
seg.update(view(msg({ status: "offline", clock_ms: null })), O);
eq(digitGroups().map((g, i) => digitLit(i)), new Array(6).fill("......."), "offline: every segment is a ghost");
eq(seg.el.attrs.class, "wc-face wc-segments wc-s-off", "offline class");
seg.update(view(msg({ offset_s: 3.2, warn: true })), O);
eq(seg.el.attrs.class, "wc-face wc-segments wc-s-differs", "differs class (dashed outline)");
seg.update(view(msg(), NOW + 30), O);
eq([seg.el.attrs.class, digitLit(5) === expectLit("9")], ["wc-face wc-segments wc-s-stale", true], "stale: frozen digits stay, struck through by the stale class");
eq(svg.children.some((c) => c.tagName === "line" && c.attrs.class === "wc-strike"), true, "there is a strike-through line");
const colonG = () => svg.children.filter((c) => c.tagName === "g" && c.attrs.class && c.attrs.class.indexOf("colon") === 0).map((c) => c.attrs.class);
seg.update(view(msg({ clock_ms: at(14, 5, 9) * 1000 })), { ring: "sweep", colonBlink: true, reduced: false });
eq(colonG(), ["colon on blink-off", "colon on blink-off"], "segments: colons blink");
seg.update(view(msg({ clock_ms: at(14, 5, 9) * 1000 })), { ring: "sweep", colonBlink: true, reduced: true });
eq(colonG(), ["colon on", "colon on"], "segments: reduced motion never blinks");

// digits
const dg = wc.createFace("digits");
dg.update(view(msg()), O);
eq([dg.el.textContent, dg.el.attrs.class], ["14:05:09", "wc-time"], "digits as before");
dg.update(view(msg({ display: { hour12: true }, clock_ms: at(13, 0, 0) * 1000 })), O);
eq(dg.el.textContent, "1:00:00 pm", "digits 12 h");
dg.update(view(msg({ status: "offline", clock_ms: null })), O);
eq(dg.el.textContent, "--:--:--", "digits offline");
eq(wc.createFace("hologram").style, "digits", "an unknown face is digits");

console.log(`${count} checks, ${fails} failed`);
process.exit(fails ? 1 : 0);
