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
eq(SW.spl.view(A, { status: "ok" }, 1001), { state: "live", text: "94.3", sub: "A-weighted, Slow response", note: "" }, "live: no updated time");
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
// a metric Smaart names itself shows Smaart's own text, as it is
eq(SW.spl.label(ent("spl.laeq_10", 1, { smaart_name: "LAeq 10" }, { name: "LAeq 10" })), "LAeq 10", "Smaart's own label");
eq(SW.spl.label(ent("spl.x", 1, { smaart_name: "<b>x</b>" })), "<b>x</b>", "markup stays plain text in a label");
eq(SW.spl.label(ent("spl.x", 1, { smaart_name: "" }, { name: "X" })), "X", "empty Smaart text falls back to the name");
// each value names its input only when the card has no single "Input:" line
const two = ent("spl.q", 1, { smaart_name: "LAeq 10", source: "ASIO MADIface USB : Channel 8 (2)" });
eq(SW.spl.view(two, { status: "ok", input_name: "" }, 1001).sub, "ASIO MADIface USB : Channel 8 (2)", "own input shown");
eq(SW.spl.view(two, { status: "ok", input_name: "ASIO MADIface USB : Channel 8 (2)" }, 1001).sub, "", "one shared input: not repeated");
eq(SW.spl.view(Object.assign({}, A, { labels: Object.assign({}, A.labels, { source: "In 1" }) }), null, 1001).sub, "A-weighted, Slow response · In 1", "words then input");
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

const atLoc = (location) => ({ location });
eq(SW.spl.title([atLoc("FOH"), atLoc("FOH")]), "Sound level · FOH", "all the same: in the header");
eq(SW.spl.title([atLoc("FOH"), atLoc("Stage left")]), "Sound level", "different: header stays plain");
eq(SW.spl.title([atLoc("FOH"), {}]), "Sound level", "one without a location: header stays plain");
eq([SW.spl.title([]), SW.spl.title(undefined), SW.spl.title([{}])], ["Sound level", "Sound level", "Sound level"], "none: plain");
eq(SW.spl.location({ location: "  Monitor desk " }), "Monitor desk", "tile location trimmed");
eq([SW.spl.location({}), SW.spl.location({ location: 5 }), SW.spl.location(undefined)], ["", "", ""], "bad location: nothing");
eq(SW.spl.title([atLoc("<b>x</b>")]), "Sound level · <b>x</b>", "markup stays plain text");

// ---- graph range
eq(SW.spl.niceRange(30, 34), [20, 40], "quiet room: 20 dB minimum, tidy");
eq(SW.spl.niceRange(100, 100), [90, 110], "one value");
eq(SW.spl.niceRange(95, 118), [90, 120], "loud show");
eq(SW.spl.niceRange(90, 95), [80, 100], "small span widened to 20");
eq(SW.spl.niceRange(88, 130), [85, 135], "spike: 2 dB room, multiples of 5");
eq(SW.spl.autoRange([], 0, 100, null), null, "no series: nothing to fit");
eq(SW.spl.autoRange([[[10, null], [20, null]]], 0, 100, [0, 50]), null, "only gaps: nothing to fit, never zero");
eq(SW.spl.autoRange([[[10, 95], [20, null], [30, 118]]], 0, 100, null), [90, 120], "gap ignored");
eq(SW.spl.autoRange([[[10, 95], [200, 40]]], 0, 100, null), [85, 105], "outside the window ignored");
eq(SW.spl.autoRange([[[10, 100], [20, 104]]], 0, 100, [90, 120]), [90, 120], "fits the current axis: no jump");
eq(SW.spl.autoRange([[[10, 100], [20, 125]]], 0, 100, [90, 120]), [95, 130], "leaves the axis: re-fit");
eq(SW.spl.autoRange([[[10, 90], [20, 95]]], 0, 100, [85, 135]), [80, 100], "far too tall: shrink");
eq(SW.spl.chartRange({ chart_range: "custom", chart_min_db: 22, chart_max_db: 145 }), { mode: "custom", min: 22, max: 145 }, "custom");
eq(SW.spl.chartRange({ chart_range: "auto", chart_min_db: 22, chart_max_db: 145 }), { mode: "auto" }, "auto");
for (const bad of [undefined, {}, { chart_range: "custom" }, { chart_range: "custom", chart_min_db: 90, chart_max_db: 80 }, { chart_range: "custom", chart_min_db: 50, chart_max_db: 55 },
  { chart_range: "custom", chart_min_db: "22", chart_max_db: 145 }, { chart_range: "custom", chart_min_db: -1, chart_max_db: 145 }, { chart_range: "custom", chart_min_db: 22, chart_max_db: 201 }]) {
  eq(SW.spl.chartRange(bad), { mode: "auto" }, "bad custom range falls back to auto");
}
const pts = [[[10, 20], [20, 150], [30, null], [40, 100]]];
eq(SW.spl.clipFlags(pts, 0, 100, [22, 145]), { above: true, below: true }, "both ends");
eq(SW.spl.clipFlags(pts, 15, 100, [22, 145]), { above: true, below: false }, "window respected");
eq(SW.spl.clipFlags(pts, 0, 100, [0, 200]), { above: false, below: false }, "all inside");
eq(SW.spl.clipFlags(pts, 0, 100, null), { above: false, below: false }, "no fixed range");
eq(SW.spl.rangeNote({ above: true, below: false }), "Some readings are above the range, drawn at the top edge.", "note above");
eq(SW.spl.rangeNote({ above: true, below: true }), "Some readings are above the range, drawn at the top edge and some are below the range, drawn at the bottom edge.", "note both");
eq(SW.spl.rangeNote({ above: false, below: false }), "", "no note");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("spl ok");
