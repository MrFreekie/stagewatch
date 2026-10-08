// Node test for the Barometer card's own logic (SW.baro in barometer.js): the one view function
// the card draws from (every state, level and wording), the dial angles and the tables, and the
// dial drawn into a tiny fake DOM (drawn once, updated in place).
// Run: node tests/js/barometer_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");

class FakeEl {
  constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this._text = ""; this.writes = 0; }
  setAttribute(k, v) { this.attrs[k] = String(v); this.writes++; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener() {}
  append(...nodes) { for (const n of nodes) { if (n instanceof FakeEl) this.children.push(n); else this._text += String(n); } }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); this.writes++; }
}
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, window: {}, Node: FakeEl, Element: FakeEl };
ctx.document = { createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (s) => String(s) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "barometer.js"), "utf8"), ctx, { filename: "barometer.js" });
const SW = vm.runInContext("SW", ctx);
const baro = SW.baro;
SW.setSiteTime({ timezone: "UTC", utc_offset_s: 0, day_rollover: "06:00" });

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const near = (got, exp, tol, what) => { count++; if (!(Math.abs(got - exp) <= tol)) { fails++; console.log(`FAIL ${what}: got ${got}, expected ${exp}`); } };

// ---- dial angles. 270 degrees over 100 hPa = 2.7 per hPa, from -135 (lower left) to +135 (lower right).
// 1000 hPa is the middle: -135 + 50 x 2.7 = 0 (straight up). 1013.2: -135 + 63.2 x 2.7 = 35.64.
eq(baro.angle(950), -135, "950 at lower left");
eq(baro.angle(1050), 135, "1050 at lower right");
near(baro.angle(1000), 0, 1e-9, "1000 straight up");
near(baro.angle(1013.2), 35.64, 1e-9, "1013.2");
eq([baro.angle(900), baro.angle(1100)], [-135, 135], "the needle stops at the ends");
eq([baro.offScale(949.9), baro.offScale(950), baro.offScale(1050), baro.offScale(1050.1)], [true, false, false, true], "off the scale");

// ---- the traditional words sit at the inHg points: 28.5, 29, 29.5, 30, 30.5 inHg x 33.8639 hPa/inHg.
// By hand: 28.5 x 33.8639 = 965.10; 29 x 33.8639 = 982.05; 29.5 x 33.8639 = 998.98; 30 x 33.8639 = 1015.92;
// 30.5 x 33.8639 = 1032.85. To 0.1 hPa that is 965.1, 982.1, 999.0, 1015.9, 1032.8 (the plan printed 982.0
// and 998.9, which are truncated); the dial prints 965, 982, 999, 1016, 1033 either way.
eq(baro.DIAL_WORDS.map((w) => w[1]), ["Stormy", "Rain", "Change", "Fair", "Very dry"], "dial words");
eq(baro.DIAL_WORDS.map((w) => Math.round(w[0] * 10) / 10), [965.1, 982.1, 999.0, 1015.9, 1032.8], "dial word points");
eq(baro.DIAL_WORDS.map((w) => Math.round(w[0])), [965, 982, 999, 1016, 1033], "rounded as printed on the dial");

// ---- text helpers
eq([baro.hpaText(101320), baro.hpaText(95000), baro.hpaText(null), baro.hpaText(undefined), baro.hpaText(NaN)], ["1,013.2", "950.0", "—", "—", "—"], "hPa text");
eq([baro.changeText(-380), baro.changeText(160), baro.changeText(0), baro.changeText(-4), baro.changeText(null)], ["−3.8", "+1.6", "0.0", "0.0", "—"], "change text, real minus sign");

// ---- tables
eq(Object.keys(baro.OUTLOOK).join(""), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "26 outlook letters");
const group = (icon) => Object.keys(baro.OUTLOOK).filter((k) => baro.OUTLOOK[k][0] === icon).sort().join("");
eq([group("sun"), group("sun_cloud"), group("showers"), group("cloud"), group("rain"), group("storm")],
  ["AB", "CDFHIJMO", "EGKN", "LPQRS", "TUVWX", "YZ"], "icons by letter, as in the spec");
eq(Object.keys(baro.OUTLOOK).filter((k) => baro.OUTLOOK[k][2] === "up").join(""), "CFIJLMY", "improving letters");
eq(Object.keys(baro.OUTLOOK).filter((k) => baro.OUTLOOK[k][2] === "down").join(""), "DHORU", "worsening letters");
eq(Object.keys(baro.OUTLOOK).every((k) => baro.OUTLOOK[k][1].length > 2 && !/forecast/i.test(baro.OUTLOOK[k][1])), true, "outlook words never say forecast");
eq(Object.keys(baro.TENDENCY), ["steady", "rising_slowly", "rising", "rising_quickly", "rising_very_rapidly", "falling_slowly", "falling", "falling_quickly", "falling_very_rapidly"], "tendency words");
eq(Object.keys(baro.TENDENCY).filter((k) => baro.TENDENCY[k][2]).map((k) => [k, baro.TENDENCY[k][2]]), [["falling_quickly", "warn"], ["falling_very_rapidly", "alert"]], "only the two fast falls are coloured (never red)");
eq(/not a forecast/i.test(baro.DISCLAIMER) && /Met Office/.test(baro.DISCLAIMER) && /guide, not a forecast/.test(baro.FOOTER), true, "advisory wording");

// ---- the view
const NOW = 1688247000 - 3600;      // Sat 1 Jul 2023 20:30 UTC
const base = { state: "ok", msl_pa: 101300, set_pa: 101700, tendency_pa_3h: -400, tendency_word: "falling_quickly", rapid_fall: true,
  outlook: "O", ready_ts: null, approx: "ok", reduction: "temperature", last_ts: NOW };

let v = baro.view(base, NOW);
eq([v.state, v.msl, v.dim, v.offScale], ["ok", "1,013.0", false, false], "ok basics");
eq(v.tendency, { arrow: "▼", word: "Falling quickly", figure: "−4.0 hPa in 3 h", level: "warn" }, "falling quickly tendency");
eq(v.flag, { text: "▼▼ FALLING QUICKLY", level: "warn", line: "Weather may turn soon: check wind, lightning and forecasts." }, "flag");
eq(v.outlook, { icon: "sun_cloud", text: "Showers, turning unsettled", arrow: "↘", wayText: "getting worse", letter: "O" }, "outlook O");
eq(v.setLabel, "3 h ago 1,017.0", "set hand label");
near(v.setAngle, baro.angle(1017), 1e-9, "set hand angle");
eq(v.tempNote, baro.TEMP_NOTE, "temperature info line");
eq(v.approx, "", "no approximation note at low altitude");

v = baro.view({ ...base, tendency_word: "falling_very_rapidly", tendency_pa_3h: -700 }, NOW);
eq([v.flag.text, v.flag.level, v.tendency.level, v.tendency.figure], ["▼▼ FALLING VERY RAPIDLY", "alert", "alert", "−7.0 hPa in 3 h"], "very rapidly");

v = baro.view({ ...base, tendency_word: "falling", tendency_pa_3h: -200, rapid_fall: false }, NOW);
eq([v.flag, v.tendency.word, v.tendency.level, v.tendency.arrow], [null, "Falling", "", "↘"], "plain fall has no flag");

v = baro.view({ ...base, tendency_word: "steady", tendency_pa_3h: 0, rapid_fall: false, outlook: "A" }, NOW);
eq([v.tendency.arrow, v.tendency.word, v.tendency.figure, v.outlook.text, v.outlook.arrow], ["→", "Steady", "0.0 hPa in 3 h", "Settled and fine", "→"], "steady");

v = baro.view({ ...base, tendency_word: "rising_quickly", tendency_pa_3h: 240, rapid_fall: false, outlook: "F" }, NOW);
eq([v.tendency.arrow, v.tendency.word, v.tendency.figure, v.tendency.level, v.outlook.arrow, v.outlook.wayText], ["▲", "Rising quickly", "+2.4 hPa in 3 h", "", "↗", "improving"], "rising");

// a rapid-fall flag from the server is trusted only when it is exactly true
eq(baro.view({ ...base, rapid_fall: "yes" }, NOW).flag, null, "flag needs a real true");

// nothing from the server is trusted as a table key
v = baro.view({ ...base, tendency_word: "__proto__", outlook: "<img src=x>" }, NOW);
eq([v.tendency, v.outlook.text, v.outlook.icon], [null, "—", ""], "unknown word and letter show a dash");
v = baro.view({ ...base, tendency_word: "constructor", outlook: "toString" }, NOW);
eq([v.tendency, v.outlook.text], [null, "—"], "inherited names are not table entries");

// off the scale
v = baro.view({ ...base, msl_pa: 94000 }, NOW);
eq([v.msl, v.offScale, v.angle], ["940.0", true, -135], "off the scale: needle at the stop, value still true");

// approximations
eq(baro.view({ ...base, approx: "approx" }, NOW).approx, "Sea-level pressure here is approximate above about 500 m.", "approx above 500 m");
eq(baro.view({ ...base, approx: "rough" }, NOW).approx, "Sea-level pressure here is rough at this height (above about 2,000 m).", "rough above 2,000 m");
v = baro.view({ ...base, approx: "approx", reduction: "isa" }, NOW);
eq([v.approx, v.tempNote], ["Sea-level pressure is approximate: there is no temperature reading.", ""], "no temperature");

// collecting: ready about 21:30; after an hour a provisional figure with no word
v = baro.view({ state: "collecting", msl_pa: 101300, ready_ts: 1688247000, first_ts: 1688247000 - 10800, last_ts: NOW, approx: "ok", reduction: "temperature" }, NOW);
eq([v.state, v.notes, v.tendency, v.outlook, v.flag, v.setLabel], ["collecting", ["Collecting, ready about 21:30"], null, null, null, ""], "collecting");
v = baro.view({ state: "collecting", msl_pa: 101300, ready_ts: 1688247000, last_hour_pa: -60, last_ts: NOW, approx: "ok", reduction: "temperature" }, NOW);
eq(v.notes, ["Collecting, ready about 21:30", "Last hour: −0.6 hPa"], "collecting with the last hour");
eq([v.tendency, v.outlook], [null, null], "no word and no outlook while collecting");

// gap
const t1410 = 1688169600 + 14 * 3600 + 10 * 60, t1505 = 1688169600 + 15 * 3600 + 5 * 60;
v = baro.view({ state: "gap", msl_pa: 101300, gap: [t1410, t1505], ready_ts: t1505 + 10800, last_ts: NOW, approx: "ok", reduction: "temperature" }, NOW);
eq(v.notes, ["Pressure gap 14:10 to 15:05, ready about 18:05"], "gap");
eq([v.tendency, v.outlook, v.flag], [null, null, null], "no word, outlook or flag in a gap");

// stale: dim, with its age, never a flag or an outlook, even if the payload still carries them
v = baro.view({ ...base, state: "stale", last_ts: NOW - 240 }, NOW);
eq([v.dim, v.notes, v.tendency, v.outlook, v.flag, v.msl], [true, ["▲ Pressure last seen 4m ago"], null, null, null, "1,013.0"], "stale");
eq(baro.view({ state: "stale", msl_pa: null, last_ts: null }, NOW).msl, "—", "a missing value is a dash, never 0");
eq(baro.view({ state: "stale", msl_pa: null, last_ts: null }, NOW).notes, ["▲ Waiting for a pressure reading."], "stale with nothing yet");

// no sensor, nothing yet, junk
v = baro.view({ state: "no_sensor" }, NOW);
eq([v.state, v.notes, v.msl, v.angle], ["no_sensor", ["No pressure sensor. Add a BME280 node to see the barometer."], "—", null], "no sensor");
eq(baro.view(undefined, NOW).state, "waiting", "before the first tick");
eq(baro.view({ state: "from_the_future" }, NOW).state, "waiting", "unknown state");
eq(baro.view("junk", NOW).state, "waiting", "junk");

// ---- the dial: drawn once, updated in place
const walk = (el, f) => { f(el); el.children.forEach((c) => walk(c, f)); };
const count_ = (root, tag, cls) => { let n = 0; walk(root, (e) => { if (e.tagName === tag && (!cls || (e.attrs.class || "").split(" ").indexOf(cls) >= 0)) n++; }); return n; };
const dial = baro.createDial();
eq(count_(dial.el, "line", "baro-tick"), 21, "a tick every 5 hPa from 950 to 1050");
eq(count_(dial.el, "line", "major"), 11, "a long tick every 10 hPa");
const labels = []; walk(dial.el, (e) => { if (e.tagName === "text" && /baro-num/.test(e.attrs.class || "")) labels.push(e.textContent); });
eq(labels, ["950", "960", "970", "980", "990", "1,000", "1,010", "1,020", "1,030", "1,040", "1,050"], "a number every 10 hPa");
const words = []; walk(dial.el, (e) => { if (e.tagName === "text" && /baro-word/.test(e.attrs.class || "")) words.push(e.textContent); });
eq(words, ["Stormy", "Rain", "Change", "Fair", "Very dry"], "the five words");

let needle = null, setHand = null, setLabel = null;
walk(dial.el, (e) => { const c = e.attrs.class || ""; if (/baro-needle/.test(c)) needle = e; if (/baro-sethand/.test(c)) setHand = e; if (/baro-setlabel/.test(c)) setLabel = e; });
dial.update(baro.view({ ...base, msl_pa: 100000, set_pa: 101000 }, NOW));
eq(needle.attrs.transform, "rotate(0 100 100)", "needle at 1000 hPa points up");
eq(setHand.attrs.transform, "rotate(27 100 100)", "set hand at 1010 hPa: -135 + 60 x 2.7 = 27");
eq([setLabel.textContent, needle.attrs.visibility, setHand.attrs.visibility], ["3 h ago 1,010.0", "visible", "visible"], "set hand label and visibility");
const w0 = needle.writes + setHand.writes + dial.el.writes;
dial.update(baro.view({ ...base, msl_pa: 100000, set_pa: 101000 }, NOW));
eq(needle.writes + setHand.writes + dial.el.writes, w0, "an identical update touches nothing");
dial.update(baro.view({ ...base, msl_pa: 101300, set_pa: null }, NOW));
eq([needle.attrs.transform, setHand.attrs.visibility, setLabel.textContent], ["rotate(35.1 100 100)", "hidden", ""], "set hand hidden while collecting (1013 hPa: -135 + 63 x 2.7 = 35.1)");
dial.update(baro.view({ state: "stale", msl_pa: 101300, last_ts: NOW - 300 }, NOW));
eq(dial.el.attrs.class, "baro-dial dim", "a stale dial is dimmed");
dial.update(baro.view({ state: "no_sensor" }, NOW));
eq([needle.attrs.visibility, dial.el.attrs["aria-label"]], ["hidden", "Barometer dial, no reading"], "no reading: no needle");
dial.update(baro.view({ ...base, msl_pa: 93000 }, NOW));
eq(dial.el.attrs["aria-label"], "Barometer dial, 930.0 hPa, off the scale", "off the scale is in the label too");
const before = count_(dial.el, "line") + count_(dial.el, "text") + count_(dial.el, "circle") + count_(dial.el, "path");
dial.update(baro.view(base, NOW)); dial.update(baro.view({ ...base, msl_pa: 99000 }, NOW));
eq(count_(dial.el, "line") + count_(dial.el, "text") + count_(dial.el, "circle") + count_(dial.el, "path"), before, "updates never add elements");

// ---- the six pictures
for (const name of ["sun", "sun_cloud", "cloud", "showers", "rain", "storm"]) {
  const icon = baro.icon(name);
  eq([icon.tagName, icon.children.length > 0, icon.attrs["aria-hidden"], icon.attrs.stroke], ["svg", true, "true", "currentColor"], `icon ${name}`);
}
eq(baro.icon("nothing").children.length, 0, "unknown icon is empty");

console.log(`${count - fails}/${count} barometer checks passed`);
process.exit(fails ? 1 : 0);
