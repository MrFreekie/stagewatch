// Node test for hidden markers and the schedule "Marker" box defaults (common.js):
// SW.visibleMarkers, SW.hiddenMarkerCount, SW.scheduleMarkerDefault.
// Run: node tests/js/marker_notes_test.js   (tests/test_markers.py runs it when node is installed)
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

const ms = [
  { id: 1, ts: 10, label: "Aligned", hidden: false, note: "" },
  { id: 2, ts: 20, label: "ALARM: FOH temp", hidden: true, note: "" },
  { id: 3, ts: 30, label: "Doors", note: "Late" },            // from an older server: no hidden field
  { id: 4, ts: 40, label: "Rain", hidden: true, note: "Covers on" },
];
const ids = (list) => list.map((m) => m.id);

eq(ids(SW.visibleMarkers(ms, false)), [1, 3], "hidden markers are left out");
eq(ids(SW.visibleMarkers(ms, true)), [1, 2, 3, 4], "Show hidden lists them all, in the same order");
eq(SW.hiddenMarkerCount(ms), 2, "hidden count");
eq(SW.hiddenMarkerCount([]), 0, "no markers");
eq(ids(SW.visibleMarkers(null, false)), [], "no list at all");
eq(SW.visibleMarkers(ms, false) !== ms, true, "a new array (the state is not changed)");
eq(SW.NOTE_MAX, 1000, "note limit matches the server");

for (const [kind, want] of [["soundcheck", true], ["doors", true], ["act", true], ["changeover", false],
  ["curfew", false], ["load_in", false], ["other", false], ["", false]]) {
  eq(SW.scheduleMarkerDefault(kind), want, `default for ${kind || "(none)"}`);
}
eq(SW.scheduleMarkerDefault("curfew", ["curfew"]), true, "the server's list wins when sent");
eq(SW.scheduleMarkerDefault("act", "not a list"), true, "a bad list falls back to the built-in one");

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
