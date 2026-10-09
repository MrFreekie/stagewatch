// Node test for SW.splitAlarms (common.js): the dashboard's alarm notice timers.
// Run: node tests/js/alarm_notices_test.js   (tests/test_alarm_notices.py runs it when node is installed)
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
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const A = (id, extra) => Object.assign({ id, hide_in: null, fold_in: null, old: false }, extra);
const ids = (l) => l.map((a) => a.id);

// Nothing timed: everything shown, no timer.
let r = SW.splitAlarms([A("a"), A("b")], 500);
eq([ids(r.shown), ids(r.older), r.next], [["a", "b"], [], null], "no timers");

// Acknowledged: 120 s left when sent. At 119 s still shown (1 s to go); at 120 s gone.
r = SW.splitAlarms([A("a", { hide_in: 120 })], 119);
eq([ids(r.shown), r.next], [["a"], 1], "hide not yet");
r = SW.splitAlarms([A("a", { hide_in: 120 })], 120);
eq([ids(r.shown), ids(r.older), r.next], [[], [], null], "hide due");

// Unacknowledged: 1,800 s to fold. Before: shown, next = remaining. After: in the fold-out.
r = SW.splitAlarms([A("a", { fold_in: 1800 })], 1000);
eq([ids(r.shown), r.next], [["a"], 800], "fold pending");
r = SW.splitAlarms([A("a", { fold_in: 1800 })], 1800);
eq([ids(r.shown), ids(r.older), r.next], [[], ["a"], null], "fold due");

// Already old when sent (server decided): fold-out straight away. Never dropped.
r = SW.splitAlarms([A("a", { old: true }), A("b")], 0);
eq([ids(r.shown), ids(r.older)], [["b"], ["a"]], "old from server");

// The soonest of several timers wins.
r = SW.splitAlarms([A("a", { fold_in: 600 }), A("b", { hide_in: 90 })], 10);
eq(r.next, 80, "soonest");

// Alerts (no timers) are never touched however long the page has been open.
r = SW.splitAlarms([A("alert")], 10 ** 7);
eq([ids(r.shown), r.next], [["alert"], null], "alert never");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("alarm notices: all passed");
