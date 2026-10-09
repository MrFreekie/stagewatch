// Node test for the half-size card helper (common.js SW.cardIsHalf). Run: node tests/js/card_size_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
const SW = vm.runInContext("SW", ctx);

let fails = 0;
function eq(got, exp, what) {
  if (got !== exp) { fails++; console.log(`FAIL ${what}: got ${got}, expected ${exp}`); }
}

const dash = { card_sizes: { wall_clock: "half", ontime_timer: "full", chart: "half" } };
eq(SW.cardIsHalf(dash, "wall_clock", "tablet"), true, "half on tablet");
eq(SW.cardIsHalf(dash, "wall_clock", "wall"), true, "half on wall");
eq(SW.cardIsHalf(dash, "wall_clock", "phone"), false, "phone ignores half");
eq(SW.cardIsHalf(dash, "ontime_timer", "tablet"), false, "full stays full");
eq(SW.cardIsHalf(dash, "chart", "tablet"), false, "a card that is not allowed stays full");
eq(SW.cardIsHalf(dash, "markers", "tablet"), false, "not listed is full");
eq(SW.cardIsHalf({}, "wall_clock", "tablet"), false, "no card_sizes (older server)");
eq(SW.cardIsHalf(null, "wall_clock", "tablet"), false, "no dashboard");
eq(SW.cardIsHalf({ card_sizes: "half" }, "wall_clock", "tablet"), false, "wrong type");
eq(SW.cardIsHalf({ card_sizes: { wall_clock: "HALF" } }, "wall_clock", "tablet"), false, "exact value only");
eq(SW.cardIsHalf({ card_sizes: { wall_clock: "half" } }, "wall_clock", undefined), true, "unknown layout is not a phone");
eq(SW.cardIsHalf({ card_sizes: { toString: "half" } }, "toString", "tablet"), false, "prototype names are not cards");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("card size tests passed");
