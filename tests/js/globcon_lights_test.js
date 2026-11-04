// Node test for the GLOBCON Lights mode logic (SW.gc.lights in globcon.js): on / off, the 2 dB hysteresis, the per-channel hold
// (instant or seconds), GLOBCON's "no signal" (null) and missing readings counting as below, the frozen / waiting / offline
// view showing "no data" and never going dark, bad saved entries being ignored, and the card's DOM updating in place with
// text set by textContent only. The messages are SYNTHETIC, shaped like the real capture. Run: node tests/js/globcon_lights_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");

class FakeEl {
  constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this._text = ""; this.style = {}; this.hidden = false; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get className() { return this.attrs.class || ""; }
  set className(v) { this.attrs.class = String(v); }
  addEventListener() {}
  append(...nodes) { for (const n of nodes) { if (n instanceof FakeEl) this.children.push(n); else this._text += String(n); } }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); }
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

// A view as gc.view would give it (state ok): strips numbered 1 to 8 with levels.
const view = (dbs, over) => Object.assign({ state: "ok", ago: "", note: "", strips: dbs.map((db, i) => ({ num: i + 1, label: `Ch ${i + 1}`, db })) }, over || {});
const cfg = (lights) => ({ mode: "lights", lights });
const L = (strip, over) => Object.assign({ strip, threshold_db: -40, colour: "green", icon: "microphone", hold_s: 0 }, over || {});
const status = (r, i) => r.squares[i || 0].status;

// on at exactly the threshold, off one dB under (instant, nothing to hold or hysteresis to bridge when starting off)
eq(status(gc.lights(view([-40]), cfg([L(1)]), null, 10)), "on", "exactly at the threshold is on");
eq(status(gc.lights(view([-40.5]), cfg([L(1)]), null, 10)), "off", "just under, starting off, is off");
eq(status(gc.lights(view([-30]), cfg([L(1)]), null, 10)), "on", "above is on");

// -250 arrives as null, a missing strip has no reading: both count as below, never as on
eq(status(gc.lights(view([null]), cfg([L(1)]), null, 10)), "off", "null (-250) is off");
eq(status(gc.lights(view([-30]), cfg([L(5)]), null, 10)), "off", "a channel with no reading is off");

// hysteresis: once on it stays on down to threshold - 2 dB (hand-calculated: -40 - 2 = -42), off below that (instant hold)
let r = gc.lights(view([-30]), cfg([L(1)]), null, 10);
r = gc.lights(view([-41]), cfg([L(1)]), r.state, 10.1);
eq(status(r), "on", "-41 is inside the 2 dB hysteresis: stays on");
r = gc.lights(view([-42]), cfg([L(1)]), r.state, 10.2);
eq(status(r), "on", "-42 is exactly the lower line: stays on");
r = gc.lights(view([-42.5]), cfg([L(1)]), r.state, 10.3);
eq(status(r), "off", "-42.5 is under the lower line and the hold is instant: off");
r = gc.lights(view([-41]), cfg([L(1)]), r.state, 10.4);
eq(status(r), "off", "back at -41 it is NOT on again (needs the threshold itself)");
r = gc.lights(view([-39]), cfg([L(1)]), r.state, 10.5);
eq(status(r), "on", "-39 is on again");

// hold: 2 s after the last time at or above the lower line
const hcfg = cfg([L(1, { hold_s: 2 })]);
r = gc.lights(view([-30]), hcfg, null, 100);
r = gc.lights(view([-60]), hcfg, r.state, 100.5);
eq(status(r), "on", "0.5 s after dropping, a 2 s hold keeps it on");
r = gc.lights(view([null]), hcfg, r.state, 101.9);
eq(status(r), "on", "1.9 s after, with a null level, still on");
r = gc.lights(view([null]), hcfg, r.state, 102);
eq(status(r), "off", "exactly 2 s after: off");
// a level back inside the hysteresis band restarts the hold
r = gc.lights(view([-30]), hcfg, null, 200);
r = gc.lights(view([-41]), hcfg, r.state, 205);
r = gc.lights(view([-60]), hcfg, r.state, 206.9);
eq(status(r), "on", "the hold counts from the last time it was inside the band (1.9 s ago)");
r = gc.lights(view([-60]), hcfg, r.state, 207);
eq(status(r), "off", "2 s after the band: off");

// prev is never changed (pure)
const p0 = gc.lights(view([-30]), cfg([L(1)]), null, 1).state;
const snapshot = JSON.stringify(p0);
gc.lights(view([-90]), cfg([L(1)]), p0, 2);
eq(JSON.stringify(p0), snapshot, "prev state is not mutated");

// words and marks: colour is never the only signal
r = gc.lights(view([-30, -90]), cfg([L(1), L(2)]), null, 1);
eq([r.squares[0].word, r.squares[0].mark, r.squares[1].word, r.squares[1].mark], ["", "", "", ""], "on and off carry no words or tick (owner's choice); only no data has a word");
eq(r.squares[0].name, "Ch 1", "the name is GLOBCON's label");
r = gc.lights(view([-30]), cfg([L(1)]), null, 1);
r = gc.lights({ state: "ok", ago: "", note: "", strips: [{ num: 1, label: "", db: -30 }] }, cfg([L(1)]), null, 1);
eq(r.squares[0].name, "Channel 1", "no label: Channel N");

// frozen: the last state is kept, hatched, says "no data" and how old; never dark (never "off" by guessing)
let live = gc.lights(view([-30, -90]), cfg([L(1), L(2)]), null, 1);
const frozenView = view([-90, -90], { state: "frozen", ago: "10 s", note: "▲ FROZEN: no new levels" });
let fr = gc.lights(frozenView, cfg([L(1), L(2)]), live.state, 20);
eq(fr.squares.map((s) => [s.status, s.word, s.ago, s.on]), [["nodata", "no data", "10 s", true], ["nodata", "no data", "10 s", false]], "frozen: no data with age, last on state kept");
eq(fr.note, "▲ FROZEN: no new levels", "frozen note passes through");
// frozen levels (low) do not turn a lit square off
let back = gc.lights(view([-30]), cfg([L(1)]), fr.state, 21);
eq(status(back), "on", "after the freeze ends the state continues from the last live one");
for (const state of ["waiting", "offline", "locked"]) {
  const w = gc.lights({ state, strips: [], ago: "", note: "n" }, cfg([L(1)]), null, 1);
  eq([w.squares[0].status, w.squares[0].on], ["nodata", false], `${state}: no data, not lit, never guessed dark-quiet`);
}

// a bad cfg
eq(gc.lights(view([-30]), cfg([]), null, 1).squares.length, 0, "no lights");
eq(gc.lights(view([-30]), cfg([]), null, 1).note.indexOf("No channels") === 0, true, "an empty list says so");
eq(gc.lights(view([-30]), null, null, 1).squares.length, 0, "null cfg");
eq(gc.lights(null, cfg([L(1)]), null, 1).squares[0].status, "nodata", "null view");
const cleaned = gc.cleanLights([L(1), L(1, { colour: "red" }), { strip: 0 }, { strip: 17 }, { strip: 2.5 }, null, "x",
  L(3, { colour: "chartreuse", icon: "<img>", threshold_db: -500, hold_s: 99 }), L(4, { threshold_db: "loud", hold_s: NaN })]);
eq(cleaned.map((c) => c.strip), [1, 3, 4], "duplicates, out-of-range and junk entries are dropped");
eq(cleaned[1], { strip: 3, threshold_db: -72, colour: "green", icon: "microphone", hold_s: 10 }, "unknown names fall back, numbers are clamped");
eq(cleaned[2], { strip: 4, threshold_db: -40, colour: "green", icon: "microphone", hold_s: 1 }, "non-numbers get the defaults");

// the palette and icon lists are the ones the server accepts (core/config.py): the Python test compares the same words
eq(gc.LIGHT_COLOURS, ["green", "amber", "red", "blue", "cyan", "magenta", "white", "orange"], "colour list");
eq(gc.LIGHT_ICONS, ["microphone", "speaker", "timecode", "play", "megaphone", "camera", "headset", "signal"], "icon list");
for (const name of gc.LIGHT_ICONS) {
  const svg = gc.iconSvg(name);
  eq([svg.tagName, svg.getAttribute("aria-hidden"), svg.children.length > 0], ["svg", "true", true], `icon ${name} is an svg built with DOM methods`);
}

// the DOM: built once, updated in place, hostile names stay text
const ui = gc.createUi();
const hostile = "<img src=x onerror=alert(1)>";
let res = gc.lights({ state: "ok", ago: "", note: "", strips: [{ num: 1, label: hostile, db: -30 }, { num: 2, label: "TC", db: -90 }] }, cfg([L(1, { colour: "red", icon: "megaphone" }), L(2, { icon: "timecode" })]), null, 1);
ui.update({ sub: "Desk 1", title: "Desk 1", badge: "● LIVE" }, res);
const wrap = ui.nodes[2];
eq(wrap.hidden, false, "lights container shown");
eq(ui.strips.hidden, true, "bars hidden in Lights mode");
eq(wrap.children.length, 2, "two squares");
const first = wrap.children[0];
eq(first.className, "gc-light gc-c-red gc-l-on", "class carries colour and state");
eq(first.textContent.indexOf(hostile) >= 0, true, "a hostile name is shown as plain text");
eq(first.children[0].children.length, 4, "icon, name, state, age");
const iconBefore = first.children[0].children[0].children[0];
res = gc.lights({ state: "ok", ago: "", note: "", strips: [{ num: 1, label: hostile, db: -90 }, { num: 2, label: "TC", db: -90 }] }, cfg([L(1, { colour: "red", icon: "megaphone" }), L(2, { icon: "timecode" })]), res.state, 2);
ui.update({ sub: "Desk 1", title: "Desk 1", badge: "● LIVE" }, res);
eq(wrap.children[0] === first, true, "squares are updated in place, not rebuilt");
eq(first.children[0].children[0].children[0] === iconBefore, true, "the icon is not rebuilt when it has not changed");
eq(first.className, "gc-light gc-c-red gc-l-off", "now off");
ui.update({ sub: "Desk 1", title: "Desk 1", badge: "● LIVE", strips: [] }, null);
eq([wrap.hidden, ui.strips.hidden], [true, true], "back in meters mode the squares are hidden (and the bars follow the strip list)");

console.log(fails ? `${fails} of ${count} FAILED` : `all ${count} checks passed`);
process.exit(fails ? 1 : 0);
