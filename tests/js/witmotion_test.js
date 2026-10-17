// Node test for the WitMotion tilt card's words and numbers (witmotion.js).
// Run: node tests/js/witmotion_test.js
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
vm.runInContext(fs.readFileSync(path.join(STATIC, "witmotion.js"), "utf8"), ctx, { filename: "witmotion.js" });
const SW = vm.runInContext("SW", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const ent = (id, value, stale = false) => ({ id, value, stale });
const dev = (extra = {}) => ({ id: "witmotion", name: "Stage Right hang", integration: "witmotion",
  witmotion: { smoothing_s: 2, setup_line: "2 Hz · 20 Hz bandwidth · horizontal · 6-axis", info_line: "battery 4.11 V", ...extra } });

// live, both axes: one decimal, direction hints, link age
let v = SW.wm.view(dev(), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", 1.26), "witmotion.tilt_pitch": ent("witmotion.tilt_pitch", -0.34), "witmotion.link_age": ent("witmotion.link_age", 0.4) });
eq(v.axes.map((a) => a.text), ["1.3", "-0.3"], "one decimal");
eq(v.axes.map((a) => a.hint), ["side to side", "front to back"], "direction hints");
eq(v.linkText, "Last data 0.4 s ago", "link age");
eq(v.smoothing, "smoothed over 2 s", "smoothing note");

// a stale or missing reading is "No data", never the last angle
v = SW.wm.view(dev(), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", 5.0, true), "witmotion.tilt_pitch": ent("witmotion.tilt_pitch", null) });
eq(v.axes.map((a) => a.text), ["No data", "No data"], "gap and stale show No data");
eq(v.live, false, "not live");
eq(v.linkText, "No data from the sensor", "no link age without data");

// an axis that is not watched is not drawn
v = SW.wm.view(dev(), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", 2.0) });
eq(v.axes.map((a) => a.key), ["tilt_roll"], "only the watched axis");

// limit levels carry words, and only where a threshold exists; no limit colour on a gap
v = SW.wm.view(dev({ limits: { tilt_roll: 2, tilt_pitch: 0 } }), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", 4.0), "witmotion.tilt_pitch": ent("witmotion.tilt_pitch", 0.1) });
eq(v.axes.map((a) => a.levelText), ["▲ ALERT", "Within limit"], "limit words");
v = SW.wm.view(dev({ limits: { tilt_roll: 3 } }), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", null) });
eq(v.axes[0].level, null, "no limit colour while there is no data");
v = SW.wm.view(dev(), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", 1) });
eq(v.axes[0].levelText, "", "no threshold, no limit text");

// order and filtering of sensors
eq(SW.wm.sensorsOf({ a: { id: "witmotion_2", integration: "witmotion" }, b: { id: "x", integration: "esphome" }, c: { id: "witmotion", integration: "witmotion" } }).map((d) => d.id), ["witmotion", "witmotion_2"], "sensorsOf");
eq(SW.wm.NOTE, "Tilt is a guide, not a rigging safety system.", "advisory note");

// target angle (hand-calculated): difference = angle - target, both read to 0.1 deg
const T = (value, target, on = 0.2, close = 1.0) => SW.wm.targetView(value, { target, on, close });
let t = T(2.6, 2.0);
eq([t.diffText, t.band, t.bandText, t.targetText], ["+0.6° from target", "close", "◆ Close", "Target 2.0°"], "+0.6 is close");
eq(T(2.2, 2.0).band, "on", "edge: exactly +0.2 is on target");
eq(T(1.8, 2.0).band, "on", "edge: exactly -0.2 is on target");
eq(T(2.3, 2.0).band, "close", "+0.3 is close");
eq(T(3.0, 2.0).band, "close", "edge: exactly +1.0 is close");
eq(T(3.1, 2.0).band, "off", "+1.1 is off target");
eq(T(0.9, 2.0).diffText, "-1.1° from target", "negative sign");
eq(T(0.9, 2.0).band, "off", "-1.1 is off target");
eq(T(2.0, 2.0).diffText, "0.0° from target", "zero has no sign");
eq(T(2.0, 2.0).band, "on", "zero is on target");
eq(T(-179.9, 179.9).diffText, "+0.2° from target", "wrap: -179.9 vs 179.9 is +0.2");
eq(T(-179.9, 179.9).band, "on", "wrap: on target");
eq(T(179.5, -179.5).diffText, "-1.0° from target", "wrap: 179.5 vs -179.5 is -1.0");
eq(T(179.5, -179.5).band, "close", "wrap: close");
eq(T(2.4, 2.0, 0.5, 2.0).band, "on", "own on band 0.5");
eq(T(4.0, 2.0, 0.5, 2.0).band, "close", "own close band 2.0");
eq(T(2.54, 2.0).diffText, "+0.5° from target", "compared as read to 0.1: 2.54 shows 2.5");
eq(T(null, 2.0), { targetText: "Target 2.0°", band: "", bandText: "", diffText: "" }, "no data: target only, no colour");
eq(T(2.0, null), null, "no target: nothing");
eq(SW.wm.targetView(2.0, undefined), null, "no target config: nothing");
v = SW.wm.view(dev({ targets: { tilt_roll: { target: 2.0, on: 0.2, close: 1.0 } } }), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", 2.6), "witmotion.tilt_pitch": ent("witmotion.tilt_pitch", 5) });
eq([v.axes[0].target.band, v.axes[1].target], ["close", null], "target only on the axis that has one");
v = SW.wm.view(dev({ targets: { tilt_roll: { target: 2.0, on: 0.2, close: 1.0 } } }), { "witmotion.tilt_roll": ent("witmotion.tilt_roll", 2.6, true) });
eq(v.axes[0].target.band, "", "stale reading: no band");

console.log(fails ? `${fails} of ${count} FAILED` : `all ${count} passed`);
process.exit(fails ? 1 : 0);
