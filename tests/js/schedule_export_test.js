// Node test for the Schedule page's CSV export (schedule-export.js): quoting, the byte-order mark,
// CRLF line endings, formula neutralising and the file name.
// Run: node tests/js/schedule_export_test.js
// Also: node tests/js/schedule_export_test.js --csv rows.json out.csv  (writes the CSV for a file of
// rows as UTF-8 bytes; tests/test_schedule_export.py reads it back through core/schedule.py).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, console, decodeURIComponent, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "schedule-export.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);

if (process.argv[2] === "--csv") {
  const rows = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
  fs.writeFileSync(process.argv[4], Buffer.from(SW.scheduleCsv(rows), "utf8"));
  process.exit(0);
}

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

// quoting (RFC 4180)
eq(SW.csvCell("plain"), "plain", "plain");
eq(SW.csvCell(""), "", "empty");
eq(SW.csvCell(null), "", "null");
eq(SW.csvCell("a,b"), '"a,b"', "comma");
eq(SW.csvCell('say "hi"'), '"say ""hi"""', "quotes doubled");
eq(SW.csvCell("two\nlines"), '"two\nlines"', "newline");
eq(SW.csvCell("two\r\nlines"), '"two\r\nlines"', "crlf");
eq(SW.csvCell("Zoë – Ünïcode"), "Zoë – Ünïcode", "unicode untouched");

// formula safety: = + - @ tab CR at the start get a leading '
for (const c of ["=SUM(A1)", "+1", "-1", "@cmd", "\tx", "\rx"]) {
  const out = SW.csvCell(c);
  eq(out.replace(/^"/, "").charAt(0), "'", `prefixed ${JSON.stringify(c)}`);
}
eq(SW.csvCell("=1,2"), "\"'=1,2\"", "prefix then quote");
eq(SW.csvCell("a=b"), "a=b", "= inside is fine");
eq(SW.csvCell("19:00"), "19:00", "times are not touched");

// whole file
const csv = SW.scheduleCsv([
  { start: "19:00", end: "", kind: "doors", stage: "", title: "Doors" },
  { start: "19:30", end: "20:15", kind: "act", stage: "Main, left", title: 'The "Band"' },
  { start: "20:15", end: "", kind: "act", stage: "", title: "=HYPERLINK(\"x\")" },
]);
eq(csv.charCodeAt(0), 0xFEFF, "starts with a byte-order mark");
eq(Buffer.from(csv, "utf8").slice(0, 3).toString("hex"), "efbbbf", "BOM is EF BB BF in UTF-8");
eq(csv.slice(1).split("\r\n"), [
  "start,end,kind,stage,title",
  "19:00,,doors,,Doors",
  '19:30,20:15,act,"Main, left","The ""Band"""',
  "20:15,,act,,\"'=HYPERLINK(\"\"x\"\")\"",
  "",
], "header, rows and CRLF");
eq(/(^|[^\r])\n/.test(csv.replace(/"[^"]*"/g, "")), false, "no bare LF outside quoted cells");
eq(SW.scheduleCsv([]), "﻿start,end,kind,stage,title\r\n", "empty list is just the header");
eq(SW.scheduleCsv([{ start: " 19:00 ", end: "", kind: "act", stage: "", title: " Hi " }]).slice(1).split("\r\n")[1], "19:00,,act,,Hi", "cells trimmed");

// file name: letters, digits and "-" only
eq(SW.scheduleFilename("Summer Festival", "2026-10-02"), "schedule-summer-festival-2026-10-02.csv", "name");
eq(SW.scheduleFilename("../Rock & Roll: \"Night\"!", "2026-10-02"), "schedule-rock-roll-night-2026-10-02.csv", "unsafe characters");
eq(SW.scheduleFilename("", "2026-10-02"), "schedule-show-2026-10-02.csv", "no event");
eq(SW.scheduleFilename("Café Ünï", "2026-10-02"), "schedule-caf-n-2026-10-02.csv", "non-ASCII dropped");
eq(SW.scheduleFilename("X", "not a date"), "schedule-x.csv", "bad day left out");
for (const n of [SW.scheduleFilename("a/b\\c:d*e?f", "2026-10-02"), SW.scheduleFilename("x".repeat(200), "2026-10-02")]) {
  eq(/^schedule-[a-z0-9-]+\.csv$/.test(n), true, `safe ${n.slice(0, 30)}`);
}

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
