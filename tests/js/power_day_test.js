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

// history chart lines: phases in order, one line for a single circuit, energy never charted
const E = (id, g, f, over) => Object.assign({ id, device_id: "m", kind: "current", unit: "A", decimals: 3, labels: { channel: "x", field: f, group: g } }, over || {});
const ents = { b: E("b", "b", "current"), a: E("a", "a", "current"), c: E("c", "c", "current"), v: E("v", "a", "voltage", { kind: "voltage" }),
  en: E("en", "", "energy", { kind: "energy" }), o: E("o", "", "current", { device_id: "other" }) };
eq(SW.pw.chartSeries(ents, "m", "current").map((l) => l.label), ["Line 1 (Brown)", "Line 2 (Black)", "Line 3 (Grey)"], "phase order");
eq(SW.pw.chartSeries(ents, "m", "voltage").map((l) => l.id), ["v"], "other fields stay out");
eq(SW.pw.chartSeries({ x: E("x", "", "power", { kind: "power" }) }, "m", "power")[0].label, "Power", "single circuit uses the field word");
eq(SW.pw.chartSeries(ents, "m", "energy"), [], "energy is not charted");
eq([SW.pw.gapSeconds(2), SW.pw.gapSeconds(120)], [60, 360], "gap: at least 60 s, or 3 buckets");

// lines are named by colour; a single-phase meter shows the line it was assigned; ratings per circuit
eq([SW.pw.GROUP_WORD.a, SW.pw.GROUP_WORD.b, SW.pw.GROUP_WORD.c, SW.pw.GROUP_WORD.n], ["Line 1 (Brown)", "Line 2 (Black)", "Line 3 (Grey)", "Neutral (Blue)"], "line colours");
eq(SW.pw.channelTitle({ id: "pm1_0", cfg: { label: "Amp rack 3", phase: "L2" } }, false), "Amp rack 3 · Line 2 (Black)", "label and line");
eq(SW.pw.channelTitle({ id: "pm1_0", cfg: null }, false), "", "one plain circuit: no heading");
eq(SW.pw.channelTitle({ id: "pm1_1", cfg: null }, true), "Channel 2", "several circuits: numbered from 1");
const dev = { id: "m", name: "M", category: "meter", status: "ok", rating_a: 16, channels: { pm1_1: { rating_a: 10, phase: "L3" } } };
const cEnts = [E("p0", "", "current", { labels: { channel: "pm1_0", field: "current" } }), E("p1", "", "current", { labels: { channel: "pm1_1", field: "current" } })];
const mv = SW.pw.meterView(dev, cEnts, 1000);
eq(mv.channels.map((c) => c.rating), [16, 10], "a circuit's own rating wins, else the meter's");

// line-to-line voltage (400 V, band 376 to 440), neutral share, the meter's own maximum rows, new words
const LL = (v) => SW.pw.swingView(v, SW.pw.SWING.voltage_ll).state;
eq([LL(400), LL(376), LL(440), LL(375.9), LL(440.1), LL(230), LL(null)], ["ok", "ok", "ok", "low", "high", "na", "none"], "line-to-line states");
eq(SW.pw.swingView(400, SW.pw.SWING.voltage_ll).word, "In range", "line-to-line is not called the UK range");
eq(SW.pw.GROUP_WORD.l12, "Line 1 to Line 2 (Brown-Black)", "line-to-line words");
eq(SW.pw.neutralShare(4.36, 14, 11, 9).high, false, "neutral 4.36 of 14 A is 31 %: not high");
eq(Math.round(SW.pw.neutralShare(4.36, 14, 11, 9).pct), 31, "neutral share");
eq(SW.pw.neutralShare(8, 14, 11, 9).high, true, "8 of 14 A is 57 %: high");
eq([SW.pw.neutralShare(null, 1, 1, 1), SW.pw.neutralShare(1, 0, 0, 0)], [null, null], "no reading or no load: no figure");
const mm = [E("x", "a", "meter_max_current", { labels: { channel: "em0", field: "meter_max_current", group: "a" } }), E("y", "", "current")];
eq(SW.pw.meterMaxRows(mm, "m"), [{ entity: "x", label: "Line 1 (Brown) Current" }], "meter max rows");
eq(SW.pw.chartSeries({ p: E("p", "l12", "voltage_ll", { kind: "voltage" }), q: E("q", "l23", "voltage_ll", { kind: "voltage" }) }, "m", "voltage_ll").map((l) => l.label),
  ["Line 1 to Line 2 (Brown-Black)", "Line 2 to Line 3 (Black-Grey)"], "line-to-line chart lines");

// Chart smoothing (display only): a mean within the window, never across a gap, nothing invented.
eq(SW.pw.smooth([[0, 10], [10, 20], [20, 30]], 30, 60), [[0, 15], [10, 20], [20, 25]], "smooths within the window");
eq(SW.pw.smooth([[0, 10], [10, 20], [500, 100], [510, 200]], 30, 60), [[0, 15], [10, 15], [500, 150], [510, 150]], "never across a gap");
eq(SW.pw.smooth([[0, 10], [10, null], [20, 30]], 30, 60).length, 2, "missing values are dropped, not filled");
eq(SW.pw.smooth(undefined, 30, 60), [], "no history: no points");
// Chart scales: voltage fixed to the UK range, frequency to 49-51 Hz, power and current from 0.
eq(SW.pw.chartScale("voltage").fixed, [216.2, 253], "voltage: UK range");
eq(SW.pw.chartScale("freq").fixed, [49, 51], "frequency: 50 Hz plus or minus 1");
eq([SW.pw.chartScale("power").fixed, SW.pw.chartScale("power").def[0], SW.pw.chartScale("current").def[0]], [null, 0, 0], "power and current start at 0");
// One status for every Shelly meter: the worst meter decides.
const live = { dev: { status: "ok" }, ents: [{ updated: 100, value: 1 }] };
eq(SW.sourceInput.shelly([live, { dev: { status: "missing" }, ents: [] }]).reported, "offline", "one meter offline: Offline");
eq(SW.sourceInput.shelly([live]).reported, "ok", "all live: ok");
eq(SW.sourceInput.shelly([]).reported, "waiting", "no meters: waiting");

// Site total: all or nothing, never a partial sum.
{
  const ent = (id, dev, ch, v, stale) => ({ id, device_id: dev, kind: "power", unit: "W", decimals: 0, value: v, stale: !!stale, updated: 100, labels: { channel: ch, field: "power", group: "" } });
  const devs = (ok2) => ({
    a: { id: "a", name: "Amp A", category: "meter", status: "ok", channels: { pm1_0: { in_total: true, phase: "L1" } } },
    b: { id: "b", name: "Amp B", category: "meter", status: ok2 ? "ok" : "missing", channels: { pm1_0: { in_total: true, phase: "L2" } } },
    c: { id: "c", name: "Other", category: "meter", status: "ok", channels: { pm1_0: { label: "not ticked" } } },
  });
  const ents = (stale) => ({ "a.pm1_0_power": ent("a.pm1_0_power", "a", "pm1_0", 1000), "b.pm1_0_power": ent("b.pm1_0_power", "b", "pm1_0", 250.5, stale), "c.pm1_0_power": ent("c.pm1_0_power", "c", "pm1_0", 99) });
  let v = SW.pw.view(ents(false), devs(true), 101);
  eq(v.total.watts, 1250.5, "total adds only the ticked circuits");
  eq(v.total.parts.length, 2, "two circuits counted");
  v = SW.pw.view(ents(true), devs(true), 101);
  eq([v.total.watts, v.total.missing], [null, ["Amp B · Line 2 (Black)"]], "a stale circuit: no sum, and it is named");
  v = SW.pw.view(ents(false), devs(false), 101);
  eq(v.total.watts, null, "a meter not answering: no sum");
  const noReading = ents(false); delete noReading["b.pm1_0_power"];
  eq(SW.pw.view(noReading, devs(true), 101).total.watts, null, "never received: no sum");
  const none = devs(true); delete none.a.channels.pm1_0.in_total; delete none.b.channels.pm1_0.in_total;
  eq(SW.pw.view(ents(false), none, 101).total, null, "nothing ticked: no total");
}

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("power_day_test: all passed");
