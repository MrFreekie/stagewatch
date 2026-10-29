// Node test for the Condensation risk card's words (condensation.js: SW.cond).
// Run: node tests/js/condensation_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "condensation.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const word = (air, dew) => SW.cond.view(air, dew).headline;

// Band edges (margin = air - dew point): 0.99 condensing, 1 near, 3.99 near, 4 low, 7.99 low, 8 dry.
eq(word(20, 20), "Condensing", "margin 0");
eq(word(20, 19.01), "Condensing", "margin 0.99");
eq(word(20, 19), "Near saturation", "margin 1");
eq(word(20, 16.01), "Near saturation", "margin 3.99");
eq(word(20, 16), "Low risk", "margin 4");
eq(word(20, 12.01), "Low risk", "margin 7.99");
eq(word(20, 12), "Dry", "margin 8");
eq(word(20, 21), "Condensing", "dew point above air temperature");
// Hand-calculated: 20 C at 50 % RH has a dew point of 9.26 C, margin 10.74: Dry.
eq(word(20, 9.26), "Dry", "20 C 50 % RH");
eq(SW.cond.view(20, 9.26).state, "dry", "state id");

// No data is "No reading", never a guess.
for (const [a, d] of [[null, 5], [5, null], [null, null], [undefined, 5], [NaN, 5], ["5", 5]]) {
  const v = SW.cond.view(a, d);
  eq([v.headline, v.state, v.margin, v.dew], ["No reading", "none", null, null], `no reading for ${a}/${d}`);
}

// Typed kit temperatures.
eq(SW.cond.parseKit("4"), 4, "4");
eq(SW.cond.parseKit(" -3.5 "), -3.5, "negative");
eq(SW.cond.parseKit("4,5"), 4.5, "comma decimal");
eq(SW.cond.parseKit("5."), 5, "trailing point while typing");
for (const bad of ["", "abc", "4 C", "1e3", "200", "-61", "--4", "4.123", null, undefined]) eq(SW.cond.parseKit(bad), null, `bad kit ${bad}`);

// Kit against a dew point of 9.3: below fogs, within 2 C may mist, otherwise fine. Never a guess without a dew point.
eq(SW.cond.kitView(4, 9.3).level, "below", "cold kit");
eq(SW.cond.kitView(9.29, 9.3).level, "below", "just below");
eq(SW.cond.kitView(9.3, 9.3).level, "close", "equal is close, not below");
eq(SW.cond.kitView(11.29, 9.3).level, "close", "just under 2 above");
eq(SW.cond.kitView(11.3, 9.3).level, "ok", "2 above");
eq(SW.cond.kitView(null, 9.3).level, "none", "no kit typed");
eq(SW.cond.kitView(4, null).level, "none", "no dew point");
eq(/guarantee/.test(SW.cond.FOOT), true, "advisory wording");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("condensation_test: ok");
