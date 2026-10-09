// Node test for the Sound level card's pure helpers (spl.js: SW.spl.entities, describe, view,
// gapSeconds, chartNote). Run: node tests/js/spl_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "spl.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

const ent = (id, slot, labels, over) => Object.assign({ id, kind: "sound_level", name: id, labels: Object.assign({ slot: String(slot) }, labels), value: 94.34, updated: 1000, stale: false }, over || {});
const A = ent("spl.a_slow", 1, { weighting: "A", metric: "SPL", time_constant: "Slow" });
const C = ent("spl.c_slow", 2, { weighting: "C", metric: "SPL", time_constant: "Slow" });
const L = ent("spl.laeq_15m", 3, { weighting: "A", metric: "Leq", period: "15 min" });

// slot order, other kinds left out
const all = { x: { id: "x", kind: "temperature" }, l: L, a: A, c: C };
eq(SW.spl.entities(all).map((e) => e.id), ["spl.a_slow", "spl.c_slow", "spl.laeq_15m"], "slot order");
eq(SW.spl.entities({}), [], "none");

// words
eq(SW.spl.describe(A), "A-weighted, Slow response", "describe A Slow");
eq(SW.spl.describe(L), "A-weighted Leq over 15 min", "describe LAeq");
eq(SW.spl.describe(ent("p", 1, { weighting: "Z", metric: "Peak" })), "Unweighted (Z) peak", "describe Z peak");
eq(SW.spl.describe({ labels: {} }), "", "no labels, no words");

// view: live value to one decimal, the number itself not changed
eq(SW.spl.view(A, { status: "ok" }, 1001), { state: "live", text: "94.3", sub: "A-weighted, Slow response", note: "Updated 1s ago" }, "live");
eq(SW.spl.view(ent("v", 1, {}, { value: 94.35 }), null, 1001).text, "94.3", "display only: one decimal");
// not available is a dash, never zero
const na = SW.spl.view(ent("n", 1, { weighting: "A", metric: "SPL", time_constant: "Slow" }, { value: null }), { status: "ok" }, 1001);
eq([na.state, na.text, na.note], ["na", "\u2014", "Not available"], "not available");
const lost = SW.spl.view(ent("n", 1, {}, { value: null }), { status: "missing" }, 1001);
eq([lost.state, lost.text, lost.note], ["na", "\u2014", "No signal from Smaart"], "no signal");
eq(SW.spl.view(ent("z", 1, {}, { value: 0 }), null, 1001).text, "0.0", "a real zero is shown as zero");
// nothing yet
eq(SW.spl.view(ent("w", 1, {}, { value: null, updated: null }), null, 1001).state, "wait", "waiting");
// stale: the old value stays, frozen, with its age
const stale = SW.spl.view(ent("s", 1, {}, { stale: true, updated: 880 }), null, 1000);
eq([stale.state, stale.text, stale.note], ["stale", "94.3", "Old reading, 2m ago"], "stale keeps the value and says how old");

// Smaart's own wording for time-weighted levels; everything else keeps its name; ids untouched
eq(SW.spl.label(ent("spl.c_slow", 1, { weighting: "C", metric: "SPL", time_constant: "Slow" }, { name: "C Slow" })), "SPL C Slow", "label C Slow");
eq(SW.spl.label(ent("spl.a_fast", 1, { weighting: "A", metric: "SPL", time_constant: "Fast" }, { name: "A Fast" })), "SPL A Fast", "label A Fast");
eq(SW.spl.label(L), L.name, "LAeq label unchanged");
// the input line: text only, nothing when empty
eq(SW.spl.inputLine({ input_name: "ASIO MADIface USB : Channel 7 (1)" }), "Input: ASIO MADIface USB : Channel 7 (1)", "input line");
eq([SW.spl.inputLine({}), SW.spl.inputLine({ input_name: "  " }), SW.spl.inputLine(undefined), SW.spl.inputLine({ input_name: 5 })], ["", "", "", ""], "no name, no line");
eq(SW.spl.inputLine({ input_name: "<b>x</b>" }), "Input: <b>x</b>", "markup stays plain text");

// gaps and the chart note
eq(SW.spl.gapSeconds(1), 10, "gap is at least 10 s");
eq(SW.spl.gapSeconds(7.2), 21.6, "gap grows with the interval");
eq(SW.spl.gapSeconds(undefined), 10, "bad bucket");
eq(SW.spl.chartNote(450, 500), "Each point is a reading exactly as Smaart sent it.", "short span: every reading");
eq(SW.spl.chartNote(3600, 500), "Each point is the last reading in about 7.2 s. Nothing is averaged.", "long span: last reading, labelled");
eq(SW.spl.chartNote(43200, 500), "Each point is the last reading in about 86 s. Nothing is averaged.", "very long span");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("spl ok");
