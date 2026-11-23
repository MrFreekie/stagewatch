// Node test for SW.setFill (common.js): progress and load bars scale instead of changing width.
// Run: node tests/js/set_fill_test.js   (tests/test_set_fill.py runs it when node is installed)
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

let fails = 0;
function eq(got, exp, what) { if (got !== exp) { fails++; console.log(`FAIL ${what}: got ${got}, expected ${exp}`); } }
const el = () => ({ style: { transform: "" } });
let e = el(); SW.setFill(e, 50); eq(e.style.transform, "scaleX(0.5000)", "half");
e = el(); SW.setFill(e, 0); eq(e.style.transform, "scaleX(0.0000)", "empty");
e = el(); SW.setFill(e, 140); eq(e.style.transform, "scaleX(1.0000)", "over 100 is clamped");
e = el(); SW.setFill(e, -5); eq(e.style.transform, "scaleX(0.0000)", "below 0 is clamped");
e = el(); SW.setFill(e, NaN); eq(e.style.transform, "scaleX(0.0000)", "no number is empty");
e = el(); SW.setFill(e, 12.34567); eq(e.style.transform, "scaleX(0.1235)", "rounded to 4 places");
if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("set_fill ok");
