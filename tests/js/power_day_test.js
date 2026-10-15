// Node test for the Power card's show-day figures (power.js: SW.pw.dayText, dayEnergy, gapText, dayOf).
// Run: node tests/js/power_day_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "power.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);
SW.setSiteTime({ timezone: "UTC", utc_offset_s: 0, day_rollover: "06:00" });

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

const T = Date.UTC(2026, 9, 14, 21, 14) / 1000;      // 21:14 UTC
const W = { unit: "W", decimals: 1, day: { day: "2026-10-14", max: 1234.5, max_at: T } };
const V = { unit: "V", decimals: 1, day: { day: "2026-10-14", min: 228, min_at: T - 720, max: 236.5, max_at: T + 1560 } };

eq(SW.pw.dayText(W, "power"), "1,234.5 W at 21:14", "highest power with its time");
eq(SW.pw.dayText(V, "voltage"), "low 228.0 V at 21:02, high 236.5 V at 21:40", "voltage range");
eq(SW.pw.dayText({ unit: "W", decimals: 1 }, "power"), "—", "no figures: a dash, never 0");
eq(SW.pw.dayText({ unit: "V", decimals: 1, day: { day: "d", max: 230, max_at: T } }, "voltage"), "—", "half a range is a dash");
eq(SW.pw.dayText(undefined, "power"), "—", "no entity: a dash");

// energy: 12,345 Wh is 12.35 kWh (to 2 decimals); under 1,000 stays Wh
eq(SW.pw.dayEnergy({ day: { wh: 12345, since: T - 900 } }), { text: "12.35", unit: "kWh", sub: " (meter counter, since 20:59)" }, "kWh");
eq(SW.pw.dayEnergy({ day: { wh: 10.5, since: T } }).text, "10.5", "Wh");
eq(SW.pw.dayEnergy({ day: { wh: 0, since: T } }).text, "0.0", "a real zero is shown as zero");
eq(SW.pw.dayEnergy({}).text, "—", "no figures: a dash");
eq(SW.pw.dayEnergy({ day: { wh: 37, since: T, went_back_at: T + 60 } }).sub.includes("went back at 21:15"), true, "counter reset is said");

// gaps and the show day
eq(SW.pw.gapText([{ day: { gaps: [[T, T + 300]] } }]).startsWith("Not reading: 21:14 to 21:19."), true, "gap text");
eq(SW.pw.gapText([{ day: {} }, {}]), "", "no gap, no words");
eq(SW.pw.dayOf([{}, W]), "2026-10-14", "show day");

// load bar: 1.8 A of 16 A is 11.25 %, 12.8 A is exactly 80 %, 16 A is 100 %, 20 A is 125 % (bar capped at 100)
const L = (a) => SW.pw.loadView(a, 16);
eq([L(1.8).level, Math.round(L(1.8).pct * 100) / 100], ["ok", 11.25], "11 percent is ok");
eq(L(1.8).text, "1.80 A of 16 A (11%)", "load text");
eq([L(12.8).level, L(12.8).word], ["high", "HIGH LOAD"], "80 percent is high");
eq([L(16).level, L(16).word], ["over", "OVER RATING"], "100 percent is over");
eq([L(20).level, L(20).bar], ["over", 100], "bar is capped");
eq(SW.pw.loadView(1, null).level, "none", "no rating: no bar");
eq(SW.pw.loadView(null, 16).text, "—", "no reading: a dash");

// swing meters: UK voltage 216.2 to 253.0; frequency red outside 49.5 to 50.5, amber outside 49.8 to 50.2
const VS = (v) => SW.pw.swingView(v, SW.pw.SWING.voltage).state, F = (v) => SW.pw.swingView(v, SW.pw.SWING.freq).state;
eq([VS(230), VS(216.2), VS(253), VS(216.1), VS(253.1), VS(110), VS(null)], ["ok", "ok", "ok", "low", "high", "na", "none"], "voltage states");
eq([F(50), F(49.8), F(50.2), F(49.79), F(50.21), F(49.5), F(49.49), F(50.51), F(60), F(null)],
  ["ok", "ok", "ok", "near", "near", "near", "low", "high", "na", "none"], "frequency states");
eq(SW.pw.swingPos(230, SW.pw.SWING.voltage), 50, "230 V is half way on 195 to 265");
eq(SW.pw.swingPos(300, SW.pw.SWING.voltage), 100, "position is capped");
eq(SW.pw.dayText({ unit: "Hz", decimals: 2, day: { day: "d", min: 49.91, min_at: T, max: 50.08, max_at: T + 60 } }, "freq"), "low 49.91 Hz at 21:14, high 50.08 Hz at 21:15", "frequency range");

// phase imbalance: currents 10, 12, 14 A -> average 12, biggest difference 2 -> 16.7 %
eq(Math.round(SW.pw.imbalance(10, 12, 14) * 10) / 10, 16.7, "imbalance");
eq(SW.pw.imbalance(5, 5, 5), 0, "balanced is 0");
eq([SW.pw.imbalance(null, 5, 5), SW.pw.imbalance(0, 0, 0)], [null, null], "no reading or no load: no figure");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("power_day_test: all passed");
