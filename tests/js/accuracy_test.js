// Node test for the sensor-accuracy helpers in common.js (units, presets, share text).
// Run: node tests/js/accuracy_test.js
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
const SW = vm.runInContext("SW", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const part = (id) => SW.ACCURACY_PRESETS.find((p) => p.id === id);

// units: pressure is typed in hPa and stored in Pa
eq(SW.accuracyCanon("pressure", "1.0"), 100, "1 hPa is 100 Pa");
eq(SW.accuracyCanon("pressure", "2,5"), 250, "comma decimal accepted");
eq(SW.accuracyCanon("temperature", "0.1"), 0.1, "temperature as typed");
eq(SW.accuracyCanon("humidity", "3"), 3, "humidity as typed");
eq(SW.accuracyShown("pressure", 100), 1, "100 Pa shows as 1 hPa");
eq(SW.accuracyShown("pressure", 150), 1.5, "150 Pa shows as 1.5 hPa");
for (const bad of ["", "abc", "0", "-1", "NaN", "Infinity", "21"]) eq(SW.accuracyCanon("temperature", bad), null, `temperature "${bad}" is refused`);
eq(SW.accuracyCanon("pressure", "51"), null, "above 50 hPa refused");
eq(SW.accuracyCanon("speed_of_sound", "1"), null, "other kinds have no accuracy");
eq(SW.accuracyShown("temperature", null), null, "nothing stored");

// text next to the value
eq(SW.fmtAccuracy("humidity", 2, "typical"), "Accuracy ±2 %RH (typical)", "humidity text");
eq(SW.fmtAccuracy("pressure", 100, "maximum"), "Accuracy ±1 hPa (maximum)", "pressure text");
eq(SW.fmtAccuracy("temperature", null, "typical"), "", "no figure, no text");

// presets: only the confirmed figures
eq(SW.presetFill(part("sht45"), "temperature"), { accuracy: 0.1, basis: "typical", hint: "typical, from the manufacturer's page" }, "SHT45 temperature");
eq(SW.presetFill(part("sht45"), "humidity").accuracy, 1.0, "SHT45 humidity");
eq(SW.presetFill(part("tmp117"), "temperature").basis, "maximum", "TMP117 is a maximum");
eq(SW.presetFill(part("dps310"), "temperature").accuracy, 0.5, "DPS310 temperature");
eq(SW.presetFill(part("dps310"), "pressure").accuracy, 1.0, "DPS310 pressure");
eq(SW.presetFill(part("bme280"), "humidity").accuracy, 3, "BME280 humidity");
eq(SW.presetFill(part("bme280"), "pressure").accuracy, 1.0, "BME280 pressure");
eq(SW.presetFill(part("bme280"), "temperature").accuracy, null, "BME280 temperature is not confirmed");
eq(SW.presetFill(part("bmp280"), "pressure").accuracy, 1.0, "BMP280 pressure");
eq(SW.presetFill(part("bmp280"), "temperature").accuracy, null, "BMP280 temperature is not confirmed");
eq(SW.presetFill(part("ms8607"), "temperature").accuracy, 1.0, "MS8607 temperature");
eq(SW.presetFill(part("ms8607"), "humidity").accuracy, 3, "MS8607 humidity");
eq(SW.presetFill(part("ms8607"), "pressure").accuracy, 2.0, "MS8607 pressure");
for (const id of ["sht41", "sht40"]) for (const k of ["temperature", "humidity"]) eq(SW.presetFill(part(id), k).accuracy, null, `${id} ${k} left blank`);
eq(SW.presetFill(part("mpl3115a2"), "pressure").accuracy, null, "MPL3115A2 pressure left blank");
eq(/about 4 hPa/.test(SW.presetFill(part("mpl3115a2"), "pressure").hint), true, "MPL3115A2 hint mentions the distributor figure");
eq(SW.presetFill(part("sht45"), "pressure"), { accuracy: null, basis: null, hint: "The SHT45 does not measure pressure." }, "a kind the part does not measure");
eq(SW.presetFill(part("sht45"), "battery").accuracy, null, "non-environment kinds fill nothing");

// shares
eq(SW.fmtShare(100 / 104), "96.2 %", "96.2 %");
eq(SW.fmtShare(1), "100 %", "whole share");
eq(SW.fmtShare(0.5), "50.0 %", "half");
eq(SW.fmtShare(null), "—", "no share");

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
