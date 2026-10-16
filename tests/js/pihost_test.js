// Node test for the Raspberry Pi card's words (pihost.js: SW.pi.rows, powerText, speedText).
// Run: node tests/js/pihost_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "pihost.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const E = (v, stale) => ({ value: v, stale: !!stale, updated: 1 });

eq(SW.pi.powerText(null).level, "unknown", "no throttle data: unknown, never OK");
eq(SW.pi.powerText({ under_voltage: true }).level, "warn", "under-voltage now");
eq(SW.pi.powerText({ under_voltage: false, under_voltage_seen: true }).level, "note", "under-voltage earlier");
eq(SW.pi.speedText({ throttled: false, freq_capped: true }).level, "warn", "frequency capped counts as slowed down");
const rows = SW.pi.rows({ cpu_temp: E(81.2), disk_free: E(0.8), wifi_signal: E(-60, true) }, { throttle: null, disk_share_free: 0.02, uptime_s: 7200 });
const by = Object.fromEntries(rows.map((r) => [r.key, r]));
eq([by.temp.value, by.temp.level], ["81.2", "warn"], "hot at 80 °C or more");
eq(by.disk.level, "warn", "low disk");
eq(by.wifi.value, "—", "a stale reading is not shown as live");
eq(by.load.value, "—", "missing reading: a dash, never 0");
eq(SW.pi.entitiesOf({ "pi_host.cpu_temp": { id: "pi_host.cpu_temp", device_id: "pi_host" }, x: { id: "x", device_id: "y" } }, "pi_host"),
  { cpu_temp: { id: "pi_host.cpu_temp", device_id: "pi_host" } }, "entities by suffix");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("pihost_test: all passed");
