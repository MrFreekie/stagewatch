// Node test for SW.messageSummary (common.js): the header messages icon's count and worst kind.
// Run: node tests/js/header_messages_test.js   (tests/test_header_messages.py runs it when node is installed)
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
const A = (id, extra) => Object.assign({ id, message: id, since: 100, acked: false, silent: false, level: 1, hide_in: null, fold_in: null, old: false }, extra);
const ids = (s) => s.items.map((i) => i.id);

// Nothing in, nothing out: no badge, no worst, a plain label.
let s = SW.messageSummary([], 0, false);
eq([s.count, s.worst, s.label], [0, null, "No messages"], "empty");
eq(SW.messageSummary(null, 0, false).count, 0, "null list");

// Unacknowledged loud alarm = alarm; acknowledged = warning; quiet notice = info.
eq(SW.messageSummary([A("a")], 0, false).worst, "alarm", "unacked");
eq(SW.messageSummary([A("a", { acked: true })], 0, false).worst, "warning", "acked");
eq(SW.messageSummary([A("a", { silent: true })], 0, false).worst, "info", "silent");

// Worst wins, label reads plainly, singular and plural.
s = SW.messageSummary([A("i", { silent: true }), A("w", { acked: true }), A("n", { silent: true })], 0, false);
eq([s.count, s.worst, s.label], [3, "warning", "3 messages, worst: warning"], "mixed");
eq(SW.messageSummary([A("i", { silent: true })], 0, false).label, "1 message, worst: information", "singular");

// Order: worst first, then newest.
s = SW.messageSummary([A("old-info", { silent: true, since: 50 }), A("new-info", { silent: true, since: 90 }),
  A("warn", { acked: true, since: 10 }), A("alarm", { since: 5 })], 0, false);
eq(ids(s), ["alarm", "warn", "new-info", "old-info"], "order");

// Sounding forces the worst to alarm.
eq(SW.messageSummary([A("w", { acked: true })], 0, true).worst, "alarm", "sounding");
eq(SW.messageSummary([], 0, true).worst, null, "sounding with nothing");

// Same timers as the alarm bar: acknowledged ones leave, folded ones are still counted.
const list = [A("gone", { acked: true, hide_in: 120 }), A("fold", { silent: true, fold_in: 60 }), A("keep")];
s = SW.messageSummary(list, 120, false);
eq([ids(s), s.count], [["keep", "fold"], 2], "timers");
eq(s.items.filter((i) => i.older).map((i) => i.id), ["fold"], "older flag");
const bar = SW.splitAlarms(list, 120);
eq(s.count, bar.shown.length + bar.older.length, "agrees with the bar");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("header messages: all passed");
