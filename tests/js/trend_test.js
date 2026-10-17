// Node test for SW.trendPath (common.js): the tiles' trend line, gaps left as gaps.
// Run: node tests/js/trend_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, window: {}, Element: function () {}, Node: function () {} };
ctx.document = { createElement: () => ({}) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static", "common.js"), "utf8"), ctx);
const SW = vm.runInContext("SW", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) { count++; if (got !== exp) { fails++; console.log(`FAIL ${what}: got ${JSON.stringify(got)}, expected ${JSON.stringify(exp)}`); } }

eq(SW.trendPath([], 0, 100, 10, 100, 10), "", "no history draws nothing");
eq(SW.trendPath(null, 0, 100, 10, 100, 10), "", "null history draws nothing");
// 0..100 s across 100 px, values 0..10 in a 10 px box with 1 px padding: 10 -> y 1, 0 -> y 9.
eq(SW.trendPath([[0, 0], [50, 10], [100, 0]], 0, 100, 60, 100, 10), "M0.0 9.0L50.0 1.0L100.0 9.0", "joined line");
// A 40 s hole with a 30 s gap limit is left open.
eq(SW.trendPath([[0, 0], [10, 10], [50, 0], [60, 10]], 0, 100, 30, 100, 10), "M0.0 9.0L10.0 1.0M50.0 9.0L60.0 1.0", "gap stays a gap");
// A null value breaks the line too; a lone point becomes a dash.
eq(SW.trendPath([[0, 0], [10, null], [20, 10], [30, 0]], 0, 100, 30, 100, 10), "M0.0 9.0h1M20.0 1.0L30.0 9.0", "null breaks, lone point dash");
// Flat values sit in the middle; points outside the span are dropped.
eq(SW.trendPath([[-5, 3], [0, 3], [100, 3]], 0, 100, 200, 100, 10), "M0.0 5.0L100.0 5.0", "flat line centred, old point dropped");

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
