// Node test for SW.adminNext (common.js): the admin login goes back to another page only when
// ?next= is exactly "/schedule". Anything else must come out empty (no open redirect).
// Run: node tests/js/admin_next_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, console, decodeURIComponent, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
const SW = vm.runInContext("SW", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  if (got !== exp) { fails++; console.log(`FAIL ${what}: got ${JSON.stringify(got)}, expected ${JSON.stringify(exp)}`); }
}

for (const ok of ["?next=/schedule", "next=/schedule", "?next=%2Fschedule", "?a=1&next=/schedule", "?next=/schedule&a=1"]) {
  eq(SW.adminNext(ok), "/schedule", `accepts ${ok}`);
}
const bad = [
  "", "?", "?next=", "?next=/", "?next=/admin", "?next=/schedule/", "?next=/schedule/x", "?next=/Schedule",
  "?next=/schedule?x=1", "?next=/schedule%3Fx%3D1", "?next=/schedule%23x", "?next=//evil.example", "?next=//evil.example/schedule",
  "?next=https://evil.example/schedule", "?next=http://evil.example", "?next=%2F%2Fevil.example", "?next=/%2Fevil.example",
  "?next=/\\evil.example", "?next=javascript:alert(1)", "?next=%", "?next=%E0%A4%A", "?next=/schedule%00", "?next=/schedule ",
  "?next=%20/schedule", "?xnext=/schedule", "?next_=/schedule", "?next=/api/admin/state", "?next=/d/foh",
  "?next=/evil&next=/schedule",
];
for (const b of bad) eq(SW.adminNext(b), "", `refuses ${b}`);
eq(SW.adminNext(undefined), "", "refuses undefined");
eq(SW.adminNext(null), "", "refuses null");

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
