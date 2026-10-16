// Node test for the Device errors card's pure functions (device-errors.js): time in words, the CSV and file name.
// Run: node tests/js/device_errors_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, console, decodeURIComponent, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "schedule-export.js", "device-errors.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

eq(SW.faultTime(0), "-", "zero");
eq(SW.faultTime(null), "-", "null");
eq(SW.faultTime(0.4), "under a second", "sub-second");
eq(SW.faultTime(12), "12 s", "seconds");
eq(SW.faultTime(65), "1 min 05 s", "minute");
eq(SW.faultTime(4000), "1 h 06 min", "hour");

const rows = [
  { name: "FOH node", kind: "Sensor", stops: 0, alerts: 0, advisories: 0, dropouts: 2, problems: 2, seconds_in_fault: 42.5,
    never_cleared: 0, flapping: true, last: { ts: 100, level: "Offline", text: "FOH node: missing" } },
  { name: "=HYPERLINK(\"x\")", kind: "L-Acoustics (imported)", stops: 1, alerts: 0, advisories: 0, dropouts: 0, problems: 1,
    seconds_in_fault: 0, never_cleared: 1, flapping: false, last: { ts: 200, level: "Stop", text: "SMPS, \"bad\"" } },
  { name: "Quiet", kind: "Sensor", stops: 0, alerts: 0, advisories: 0, dropouts: 0, problems: 0, seconds_in_fault: 0,
    never_cleared: 0, flapping: false, last: null },
];
const csv = SW.deviceErrorsCsv(rows, (t) => `T${t}`);
const lines = csv.split("\r\n");
eq(csv.charAt(0), "﻿", "byte-order mark");
eq(lines[0], "﻿Device,Kind,Stops,Alerts,Advisories,Drop-outs,Problems,Seconds in fault,Never cleared,Flapping,Last problem time,Last problem level,Last problem", "header");
eq(lines[1], "FOH node,Sensor,0,0,0,2,2,42.5,0,yes,T100,Offline,FOH node: missing", "device row");
eq(lines[2], "\"'=HYPERLINK(\"\"x\"\")\",L-Acoustics (imported),1,0,0,0,1,0,1,,T200,Stop,\"SMPS, \"\"bad\"\"\"", "formula neutralised and quoted");
eq(lines[3], "Quiet,Sensor,0,0,0,0,0,0,0,,,,", "quiet row has empty last-problem cells");
eq(lines.length, 5, "CRLF after every line");

eq(SW.deviceErrorsFilename("Day 1", "2026-10-15"), "device-errors-day-1-2026-10-15.csv", "file name");
eq(SW.deviceErrorsFilename("../../Evil name!", "bad"), "device-errors-evil-name.csv", "unsafe characters and bad day");
eq(SW.deviceErrorsFilename("", null), "device-errors-show.csv", "no name");

console.log(fails ? `${fails} of ${count} failed` : `all ${count} passed`);
process.exit(fails ? 1 : 0);
