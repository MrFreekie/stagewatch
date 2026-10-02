// Node test for SW.fmtTime (common.js) and TimeChart.siteTicks (chart.js).
// Run: node tests/js/site_time_test.js   (tests/test_sitetime.py runs it when node is installed)
// Expected instants are fixed UTC values (see the 2026 transition dates in test_sitetime.py).
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
vm.runInContext(fs.readFileSync(path.join(STATIC, "chart.js"), "utf8"), ctx, { filename: "chart.js" });
const SW = vm.runInContext("SW", ctx);
const TimeChart = vm.runInContext("TimeChart", ctx);

const utc = (y, mo, d, h = 0, mi = 0, s = 0) => Date.UTC(y, mo - 1, d, h, mi, s) / 1000;
let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const zone = (tz, off) => SW.setSiteTime({ timezone: tz, utc_offset_s: off, day_rollover: "06:00" });
const ticks = (t0, t1, step) => TimeChart.siteTicks(t0, t1, step, SW.siteOffset).map((t) => SW.fmtTime(t));

// ---- fmtTime across the 2026 changes
zone("Europe/London", 3600);
eq(SW.fmtTime(utc(2026, 3, 29, 0, 59, 59), { seconds: true }), "00:59:59", "London before spring forward");
eq(SW.fmtTime(utc(2026, 3, 29, 1, 0, 0), { seconds: true }), "02:00:00", "London after spring forward");
eq(SW.fmtTime(utc(2026, 10, 25, 0, 30)), "01:30", "London overlap, first");
eq(SW.fmtTime(utc(2026, 10, 25, 1, 30)), "01:30", "London overlap, second");
eq(SW.fmtTime(utc(2026, 7, 10, 23, 0)), "00:00", "midnight is 00, not 24");
zone("America/New_York", -14400);
eq(SW.fmtTime(utc(2026, 3, 8, 7, 30)), "03:30", "New York after the gap");
eq(SW.fmtTime(utc(2026, 11, 1, 5, 30)), "01:30", "New York overlap, first");
eq(SW.fmtTime(utc(2026, 11, 1, 6, 30)), "01:30", "New York overlap, second");
zone("Australia/Lord_Howe", 37800);
eq(SW.fmtTime(utc(2026, 10, 3, 15, 29)), "01:59", "Lord Howe before +30 min");
eq(SW.fmtTime(utc(2026, 10, 3, 15, 30)), "02:30", "Lord Howe after +30 min");
zone("Asia/Kathmandu", 20700);
eq(SW.fmtTime(utc(2026, 7, 1, 0, 0)), "05:45", "Kathmandu +05:45");
eq(SW.siteOffset(utc(2026, 7, 1)), 20700, "Kathmandu offset");
zone("", 7200);
eq(SW.fmtTime(utc(2026, 7, 1, 22, 15, 3), { seconds: true }), "00:15:03", "unset zone uses the server offset");
zone("Mars/Olympus", -3600);
eq(SW.fmtTime(utc(2026, 7, 1, 0, 30)), "23:30", "unknown zone falls back to the offset");
eq(SW.fmtTime(null), "—", "missing time");
eq(SW.fmtOffset(-16200), "UTC-04:30", "fmtOffset");

// ---- dates use Latin digits whatever the locale
zone("Europe/London", 3600);
SW._latnLocale = () => "ar-EG-u-nu-latn";
SW._tf = {};
const dated = SW.fmtTime(utc(2026, 10, 2, 13, 5), { date: true });
eq(/^[^٠-٩۰-۹]*$/.test(dated) && /2026/.test(dated) && /14:05$/.test(dated), true,
  `Arabic locale date has Latin digits (${dated})`);
SW._latnLocale = () => "not a locale tag!";   // bad tag: falls back to the default locale
SW._tf = {};
eq(/14:05$/.test(SW.fmtTime(utc(2026, 10, 2, 13, 5), { date: true })), true, "bad locale tag falls back");

// ---- chart ticks stay on the site grid across a DST change in the window
zone("America/New_York", -18000);
eq(ticks(utc(2026, 3, 7, 20), utc(2026, 3, 8, 20), 21600), ["18:00", "00:00", "06:00", "12:00"], "NY spring, 6 h");
eq(ticks(utc(2026, 3, 8, 6, 30), utc(2026, 3, 8, 7, 30), 900),
  ["01:30", "01:45", "03:00", "03:15", "03:30"], "NY spring gap, 15 min");
eq(ticks(utc(2026, 10, 31, 12), utc(2026, 11, 1, 12), 21600), ["12:00", "18:00", "00:00", "06:00"], "NY fall back, 6 h");
zone("Europe/London", 3600);
eq(ticks(utc(2026, 10, 24, 12), utc(2026, 10, 25, 12), 21600), ["18:00", "00:00", "06:00", "12:00"], "London fall back, 6 h");
eq(ticks(utc(2026, 10, 24, 22, 30), utc(2026, 10, 25, 3, 30), 3600),
  ["00:00", "01:00", "02:00", "03:00"], "London fall back, 1 h: repeated 01:00 labelled once");
eq(ticks(utc(2026, 3, 28, 12), utc(2026, 3, 29, 12), 21600), ["12:00", "18:00", "00:00", "06:00", "12:00"], "London spring, 6 h");
zone("Australia/Lord_Howe", 37800);
eq(ticks(utc(2026, 10, 3, 12, 7), utc(2026, 10, 3, 18, 7), 3600),
  ["23:00", "00:00", "01:00", "03:00", "04:00", "05:00"], "Lord Howe +30 min, 1 h");
eq(ticks(utc(2026, 4, 4, 12, 7), utc(2026, 4, 4, 18, 7), 3600),
  ["00:00", "01:00", "02:00", "03:00", "04:00"], "Lord Howe -30 min, 1 h: repeated half hour labelled once");
zone("Asia/Kolkata", 19800);
eq(ticks(utc(2026, 7, 1, 1, 7), utc(2026, 7, 1, 13, 0), 7200),
  ["08:00", "10:00", "12:00", "14:00", "16:00", "18:00"], "Kolkata 2 h ticks on even site hours");
zone("Europe/London", 3600);
eq(TimeChart.siteTicks(utc(2026, 7, 1, 12), utc(2026, 7, 1, 13), 300, () => NaN).length <= 100, true,
  "a broken offset function can't hang the chart");

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
