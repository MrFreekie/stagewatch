// Node test for the calibration-offset helpers in common.js (SW.fmtOffset, SW.offsetNotes,
// SW.offsetFootnote, SW.averageAdjusted). Run: node tests/js/offset_note_test.js
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
const SW = vm.runInContext("SW", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

eq(SW.fmtCalOffset("pressure", -100), "-1.0 hPa", "pressure in hPa, signed");
eq(SW.fmtCalOffset("pressure", 50), "+0.5 hPa", "positive has a plus");
eq(SW.fmtCalOffset("temperature", 0.5), "+0.5 °C", "temperature");
eq(SW.fmtCalOffset("humidity", -2), "-2.0 % points", "humidity in % points");
eq(SW.fmtCalOffset("pressure", NaN), "", "bad number gives nothing");
eq(SW.fmtCalOffset("battery", 1), "+1.0 %", "unknown kinds still format safely");

const devs = [{ id: "a", name: "Feather S3" }, { id: "b", name: "MPL3115A2" }, { id: "c", name: "Plain" }];
const ents = [
  { id: "a.p", device_id: "a", kind: "pressure", offset: -100 },
  { id: "b.p", device_id: "b", kind: "pressure", offset: 50 },
  { id: "c.p", device_id: "c", kind: "pressure" },
  { id: "c.t", device_id: "c", kind: "temperature", offset: 0 },
  { id: "site.pressure", device_id: "site", kind: "pressure", derived: true, offset: 99 },
];
const notes = SW.offsetNotes(devs, ents);
eq(notes.map((n) => n.text), ["Feather S3 -1.0 hPa", "MPL3115A2 +0.5 hPa"], "one note per adjusted sensor");
eq(SW.offsetFootnote(notes), "* Calibration offset applied: Feather S3 -1.0 hPa, MPL3115A2 +0.5 hPa", "footnote text");
eq(SW.offsetFootnote([]), "", "no footnote when nothing is adjusted");

const two = SW.offsetNotes([{ id: "a", name: "Node" }], [
  { device_id: "a", kind: "pressure", offset: -100 }, { device_id: "a", kind: "temperature", offset: 0.3 }]);
eq(two.map((n) => n.text), ["Node pressure -1.0 hPa", "Node temperature +0.3 °C"], "a node with two offsets names the kind");

eq(SW.hasOffset({ offset: 0 }), false, "zero is not adjusted");
eq(SW.hasOffset({ offset: 1, derived: true }), false, "derived is never adjusted");
eq(SW.hasOffset(undefined), false, "missing entity");
eq(SW.averageAdjusted(ents, "pressure"), true, "pressure average has an adjusted sensor");
eq(SW.averageAdjusted(ents, "temperature"), false, "temperature average does not");
eq(SW.averageAdjusted(ents, "speed_of_sound"), false, "speed of sound is not marked");

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
