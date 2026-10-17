// Node test for the sound level limits helpers (spl-limits.js). Run: node tests/js/spl_limits_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "spl-limits.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);
const L = SW.splLimits;

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

const cur = { id: "a", kind: "limit", metric: "LAeq 15", value_db: 102, next_ts: 1000, next_value_db: 98, reading: { entity_id: "spl.x" } };
const ent = (v, stale) => ({ id: "spl.x", value: v, stale: !!stale });
eq(L.status(cur, ent(102)), "under", "equal is not over");
eq(L.status(cur, ent(102.1)), "over", "strictly above is over");
eq(L.status(cur, ent(null)), "no_reading", "missing value");
eq(L.status(cur, ent(110, true)), "no_reading", "stale is never OK or over");
eq(L.status(cur, undefined), "no_reading", "no entity yet");
eq(L.status({ ...cur, reading: { entity_id: null } }, ent(90)), "not_recorded", "metric not recorded");
eq(L.value(ent(94.36), "under"), "94.4", "one decimal as the card shows");
eq(L.value(ent(null), "no_reading"), "—", "a dash, never zero");
eq(L.value(ent(95.04), "over", 95), "95.04", "more decimals when one would hide the excess");
eq(L.value(ent(95.0), "under", 95), "95.0", "equal stays one decimal");
eq(L.line(cur, () => "22:00"), "Limit now 102 dB until 22:00, then 98", "limit line");
eq(L.line({ ...cur, next_value_db: null }, () => "23:00"), "Limit now 102 dB until 23:00, then none", "limit ends");
eq(L.line({ kind: "guidance", value_db: 100 }, () => ""), "Guidance 100 dB", "guidance line");
eq(L.countdown(1000 + 42 * 60, 1000), "in 42 min", "minutes");
eq(L.countdown(1000 + 65 * 60, 1000), "in 1 h 05 min", "hours");
eq(L.countdown(1010, 1000), "now", "under a minute");
eq(L.headline({ current: [{ kind: "guidance" }, { kind: "limit", id: "l" }] }).id, "l", "licence limit first");
eq(L.footer({ set_by: "A", reference: "R", rev: 3, updated: 5, copied: true }, () => "14:05"),
  "Set by A · Ref R · Rev 3 · Updated 14:05 · Copied from another event, not yet confirmed", "footer");

eq(L.unit("LAeq 15"), "dB(A)", "A weighting from the name");
eq(L.unit("LCpeak"), "dB(C)", "C weighting from the name");
eq(L.unit("Leq"), "dB", "no weighting in the name");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("spl_limits_test: all passed");
