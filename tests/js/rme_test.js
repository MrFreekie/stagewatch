// Node test for the RME interfaces card's own logic (SW.rme in rme.js): status words and symbols, the
// stale and offline wording, the wall subset, null handling, the soundness list, and that hostile text
// stays text. The messages are SYNTHETIC, made to match the shape the server sends. Run: node tests/js/rme_test.js
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
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); }
}
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, isFinite, Set, Map, window: {}, Node: FakeEl, Element: FakeEl };
ctx.document = { createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (s) => String(s) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "rme.js"), "utf8"), ctx, { filename: "rme.js" });
const SW = vm.runInContext("SW", ctx);
const rme = SW.rme;

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}\n  got ${g}\n  exp ${e}`); }
}

const unit = (over = {}) => Object.assign({
  id: "stage_l", name: "Stage L", model: "12Mic", status: "ok", status_word: "OK", status_symbol: "tick", level: 0,
  link_up: true, last_frame_age_s: 0, no_updates_min: null, temp_c: 61.9, psu: [{ n: 1, on: true }, { n: 2, on: true }],
  redundancy: true, clock: { source_label: "Word clock", locked: true, rate_hz: 48000, state: "Good" },
  preset: { active_no: 2, loaded_no: 2, active_name: "Live-2", loaded_name: "Live-2", changed: false },
  avb: { active: 4, total: 8, states: [{ word: "Good", count: 4 }] },
  soundness: [{ area: "Device", word: "OK", symbol: "tick", level: 0, cause: "" }], firmware: "2.0.1",
}, over);

// ---- ok
let v = rme.unitView(unit(), 0, "tablet");
eq([v.word, v.symbol, v.cls, v.dim], ["OK", "✓", "rme-ok", false], "ok has a word and a symbol");
eq(v.temp, "61.9 °C", "temperature with unit");
eq(v.psuText, "PSU 1 On, PSU 2 On", "PSU words");
eq(v.clock, "Word clock, Locked, 48 kHz", "clock text");
eq(v.preset, "Live-2", "same preset shows once");
eq(v.presetChanged, false, "no change flag");
eq(v.avb, "4 of 8 streams active", "AVB text");
eq(v.notes, [], "ticks are not listed");
eq(v.checks.length, 1, "but the fold lists every check");
// ---- every state has a word AND a symbol
for (const [st, word, sym] of [["ok", "OK", "tick"], ["check", "CHECK", "triangle"], ["warning", "WARNING", "cross"], ["offline", "OFFLINE", "dash"], ["starting", "Starting", "dash"]]) {
  const x = rme.unitView(unit({ status: st, status_word: word, status_symbol: sym, link_up: st !== "offline" }), 0, "tablet");
  eq([x.word.length > 0, x.symbol.length > 0, x.cls], [true, true, `rme-${st}`], `state ${st}`);
}
eq(rme.unitView(unit({ status: "warning", status_word: "WARNING", status_symbol: "cross" }), 0, "tablet").symbol, "✖", "warning is a cross");
eq(rme.unitView(unit({ status: "check", status_word: "CHECK", status_symbol: "triangle" }), 0, "tablet").symbol, "▲", "check is a triangle");
// ---- PSUs
eq(rme.psuWords(null).map((p) => p.word), ["n.a.", "n.a."], "psu null is n.a.");
eq(rme.psuWords([{ n: 1, on: true }, { n: 2, on: false }]).map((p) => p.word), ["On", "Off"], "psu on and off");
eq(rme.psuWords([{ n: 1, on: "yes" }]).map((p) => p.word), ["n.a.", "n.a."], "a non-boolean is n.a., not On");
// ---- temperature: missing is a dash, never 0
eq(rme.fmtTemp(null), "—", "null temp"); eq(rme.fmtTemp(undefined), "—", "undefined temp"); eq(rme.fmtTemp(NaN), "—", "NaN temp");
eq(rme.fmtTemp(0), "0.0 °C", "zero is zero"); eq(rme.fmtTemp(1234.5), "1,234.5 °C", "comma thousands");
// ---- clock
eq(rme.clockText({ source_label: "Word clock", locked: false, rate_hz: 44100 }), "Word clock, Not locked, 44.1 kHz", "not locked");
eq(rme.clockText({ source_label: "AVB", locked: null, rate_hz: null }), "AVB, lock unknown", "lock unknown");
eq(rme.clockText({ source_label: "Internal", locked: null, rate_hz: 48000 }), "Internal, 48 kHz", "internal has no lock word");
eq(rme.clockText(null), "—", "no clock");
// ---- preset
eq(rme.presetText({ active_no: 2, loaded_no: 3, active_name: "Live-2", loaded_name: "Live-3", changed: true }), { text: "Active Live-2, loaded Live-3", changed: true }, "changed");
eq(rme.presetText({ active_no: 2, loaded_no: 2, active_name: "", loaded_name: "", changed: false }).text, "Preset 2", "number when no name");
eq(rme.presetText({}).text, "—", "unknown preset");
// ---- AVB
eq(rme.avbText({ active: 0, total: 0 }), "No streams", "no streams"); eq(rme.avbText({ active: null, total: 8 }), "—", "unknown AVB");
// ---- soundness list: only what is not fine
v = rme.unitView(unit({ soundness: [
  { area: "Device", word: "OK", symbol: "tick", level: 0, cause: "" },
  { area: "Clock", word: "CHECK", symbol: "triangle", level: 1, cause: "Input not in sync" },
  { area: "AVB in 3", word: "Not monitored", symbol: "dash", level: 0, cause: "" },
  { area: "Preset", word: "Unrecognised: Odd", symbol: "question", level: 1, cause: "" }] }), 0, "tablet");
eq(v.notes.map((s) => s.area), ["Clock", "Preset"], "ticks and grey dashes are not listed");
eq(v.checks.length, 4, "the fold lists them all");
eq(v.notes[1].symbol, "?", "unknown word has a question mark");
// ---- offline: last values kept, dimmed, with age
v = rme.unitView(unit({ status: "offline", status_word: "OFFLINE", status_symbol: "dash", link_up: false, last_frame_age_s: 120 }), 60, "tablet");
eq([v.word, v.dim, v.ageNote], ["OFFLINE", true, "OFFLINE, last seen 3 min ago"], "offline: dimmed and aged by the time since the message");
eq(v.temp, "61.9 °C", "offline keeps the last temperature on screen");
eq(rme.unitView(unit({ status: "offline", link_up: false, last_frame_age_s: null }), 0, "tablet").ageNote, "OFFLINE, no data received", "offline with no data ever");
eq(rme.unitView(unit({ link_up: false }), 0, "tablet").dim, true, "link down is dim even if status lags");
// ---- starting
v = rme.unitView(unit({ status: "starting", status_word: "Starting", status_symbol: "dash", link_up: false, temp_c: null, psu: null }), 0, "tablet");
eq([v.word, v.dim, v.temp, v.clock, v.ageNote], ["Starting", false, "—", "—", "Waiting for the unit's first status"], "starting shows dashes, not zeros");
// ---- quiet unit: information only
v = rme.unitView(unit({ no_updates_min: 7 }), 0, "tablet");
eq([v.quiet, v.dim, v.word], ["No updates for 7 min", false, "OK"], "no updates note is information, not an outage");
// ---- ageText
eq([5, 10, 44, 59, 60, 150, 3599, 3600, 7500, null, -1].map(rme.ageText), ["a few seconds", "10 s", "40 s", "60 s", "1 min", "3 min", "60 min", "1 h 00 min", "2 h 05 min", "a while", "a while"], "age words");
// ---- wall subset
v = rme.unitView(unit(), 0, "wall");
eq([v.name, v.word, v.temp, v.psuText], ["Stage L", "OK", "61.9 °C", "PSU 1 On, PSU 2 On"], "wall keeps name, status, temperature, PSUs");
eq(["clock", "preset", "avb", "notes", "checks"].filter((k) => k in v), [], "wall drops the detail");
// ---- null and garbage
eq(rme.units(null), [], "null units"); eq(rme.units({ units: null }), [], "units null"); eq(rme.units({ units: [] }), [], "empty");
eq(rme.units({ units: [null, 3, "x", unit()] }).length, 1, "junk entries dropped");
eq(rme.units({ units: Array.from({ length: 20 }, () => unit()) }).length, 8, "at most eight");
v = rme.unitView(null, 0, "tablet");
eq([v.word, v.temp], ["Starting", "—"], "null unit is Starting");
v = rme.unitView({ status: "bogus", temp_c: "hot", psu: "x", clock: 5, preset: 7, avb: [], soundness: "no" }, NaN, "tablet");
eq([v.state, v.temp, v.psuText, v.clock, v.preset, v.avb, v.notes], ["starting", "—", "PSU 1 n.a., PSU 2 n.a.", "—", "—", "—", []], "garbage becomes dashes");
// ---- card class: only a live warning gives the wall edge
eq(rme.cardClass([rme.unitView(unit({ status: "warning", status_word: "WARNING", status_symbol: "cross" }), 0, "wall")]), "rme-has-warning", "warning edge");
eq(rme.cardClass([rme.unitView(unit({ status: "check", status_word: "CHECK", status_symbol: "triangle" }), 0, "wall")]), "", "check has no edge");
// ---- hostile text stays text
const evil = "<img src=x onerror=alert(1)>";
const evilUnit = unit({ name: evil, model: evil, firmware: evil, soundness: [{ area: evil, word: evil, symbol: "triangle", level: 1, cause: evil }] });
v = rme.unitView(evilUnit, 0, "tablet");
const ui = rme.createUi();
ui.update([evilUnit], 0, "tablet");
const all = ui.nodes.map((n) => n.textContent).join("|");
eq(all.indexOf("<img src=x") >= 0, true, "hostile text is shown as text");
const tags = [];
(function walk(n) { tags.push(n.tagName); (n.children || []).forEach(walk); })(ui.nodes[0]);
eq(tags.filter((t) => /^(img|script|iframe)$/i.test(t)), [], "no element made from the text");
eq(v.name.length <= 40, true, "long names are cut");
// ---- the card in place: one tile per unit, tiles kept between updates
eq(ui.tiles.size, 1, "one tile");
ui.update([unit()], 0, "tablet");
const first = ui.tiles.get("stage_l");
ui.update([unit(), unit({ id: "mon", name: "Monitor world" })], 0, "tablet");
eq([ui.tiles.size, ui.tiles.get("stage_l") === first], [2, true], "tile kept when another is added");
ui.update([unit({ id: "mon", name: "Monitor world" })], 0, "tablet");
eq(ui.tiles.size, 1, "tile removed with the unit");
const views = ui.update([], 0, "tablet");
eq([views.length, ui.nodes[1].hidden, ui.nodes[0].children.length], [0, false, 0], "no units: the empty note, no tiles");
eq(ui.nodes[2].textContent, "Reads the unit; never changes it. Not a protection system.", "advisory footer wording");
// the offline tile text carries the age
ui.update([unit({ status: "offline", status_word: "OFFLINE", status_symbol: "dash", link_up: false, last_frame_age_s: 30 })], 0, "tablet");
eq(/OFFLINE, last seen 30 s ago/.test(ui.nodes[0].textContent), true, "offline tile says how old");
// the status source for the card chip: the worst unit decides
eq(SW.sourceInput.rme([unit(), unit({ status: "offline", link_up: false, last_frame_age_s: 20 })], 1000).reported, "offline", "one unit away: chip says offline");
eq(SW.sourceInput.rme([unit()], 1000), { source: "rme", reported: "ok", dataAt: 1000 }, "all up: online");
eq(SW.sourceInput.rme(null, 1000).reported, "waiting", "no units: waiting");
eq(SW.sourceInput.rme([unit(), unit({ status: "offline", link_up: false, last_frame_age_s: 20 })], 1000).dataAt, 980, "last data is the away unit's, not the live one's");

console.log(fails ? `${fails} of ${count} checks FAILED` : `${count} checks passed`);
process.exit(fails ? 1 : 0);
