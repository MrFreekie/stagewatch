// Node test for the WitMotion Admin card's rows, checks and words (witmotion-admin.js).
// Run: node tests/js/witmotion_admin_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, Set, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "witmotion-admin.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);
const wa = SW.wmAdmin;

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const row = (over = {}) => Object.assign(wa.blankRow("AA:BB:CC:DD:EE:01"), over);

// addresses: a Bluetooth address or a UUID, put in capitals; anything else is refused
eq(wa.address(" aa:bb:cc:dd:ee:01 "), "AA:BB:CC:DD:EE:01", "address in capitals");
eq(wa.address("0f8fad5b-d9cb-469f-a165-70867728950e"), "0F8FAD5B-D9CB-469F-A165-70867728950E", "uuid");
eq(["", "AA:BB:CC:DD:EE", "AA-BB-CC-DD-EE-01", "AA:BB:CC:DD:EE:0G", null].map(wa.address), ["", "", "", "", ""], "bad addresses");

// rows from the saved settings: the list, or the older single-sensor fields, or nothing
eq(wa.rowsFromConfig({}), { rows: [], legacy: false }, "nothing set up");
let r = wa.rowsFromConfig({ sensors: [{ address: "AA:BB:CC:DD:EE:01", label: "Stage Right", axis: "x", smoothing_s: 5, target_x: 2, on_target_deg: 0.3, close_deg: 1.5 }, { address: "AA:BB:CC:DD:EE:02" }] });
eq(r.legacy, false, "not legacy");
eq(r.rows.map((x) => [x.address, x.label, x.axis, x.smoothing_s, x.target_x, x.target_y, x.on_target_deg, x.close_deg, x.savedIndex]),
  [["AA:BB:CC:DD:EE:01", "Stage Right", "x", 5, 2, null, 0.3, 1.5, 0], ["AA:BB:CC:DD:EE:02", "", "both", 2, null, null, 0.2, 1, 1]], "list rows and defaults");
r = wa.rowsFromConfig({ address: "AA:BB:CC:DD:EE:09", axis: "y", smoothing_s: 0 });
eq([r.legacy, r.rows.length, r.rows[0].axis, r.rows[0].smoothing_s, r.rows[0].savedIndex], [true, 1, "y", 0, 0], "older fields give one row");
eq(wa.rowsFromConfig({ enabled: true }).rows, [], "enabled with nothing listed shows no row");

// checks, in crew words
eq(wa.check([row()]), "", "one good row");
eq(wa.check([]), "", "no rows can be saved");
eq(wa.check([row({ address: "nope" })]).startsWith("A sensor address looks like"), true, "bad address");
eq(wa.check([row(), row({ address: "aa:bb:cc:dd:ee:01" })]), "Two sensors have the same address. Each sensor needs its own.", "same address in another case");
eq(wa.check([row({ on_target_deg: 0.5, close_deg: 0.2 })]), "\"Close\" must be at least as wide as \"On target\".", "close tighter than on target");
eq(wa.check([row({ on_target_deg: null })]).includes("On target"), true, "blank band");
eq(wa.check([row({ target_x: 200 })]).includes("-180"), true, "target out of range");
eq(wa.check([1, 2, 3, 4, 5].map((n) => row({ address: `AA:BB:CC:DD:EE:0${n}` }))), "Up to 4 sensors.", "five sensors");

// what is sent: only the list, addresses in one form, names trimmed
const p = wa.payload(true, [row({ address: " aa:bb:cc:dd:ee:01", label: "  Stage Right  ", target_x: 2, savedIndex: 3 })]);
eq(p, { enabled: true, markers: true, temp_on_equipment: false, sensors: [{ address: "AA:BB:CC:DD:EE:01", label: "Stage Right", axis: "both", smoothing_s: 2, target_x: 2, target_y: null, on_target_deg: 0.2, close_deg: 1 }] }, "payload");
eq("savedIndex" in p.sensors[0], false, "the row number is not sent");

// status words: connected, link lost, not running, not saved. A gap is never shown as a number.
eq(wa.statusText({ connected: true, text: "Connected", age_s: 0.4 }, true), "Connected · last data 0.4 s ago", "connected");
eq(wa.statusText({ connected: false, text: "Link lost", age_s: null }, true), "▲ Link lost. No readings until it is back.", "link lost");
eq(wa.statusText(null, true), "Not running. Save with Enabled ticked to start it.", "not running");
eq(wa.statusText(null, false), "Not saved yet.", "not saved");

// signal words
eq(wa.signalText(-58), "strong signal (-58 dBm)", "strong");
eq(wa.signalText(-70), "fair signal (-70 dBm)", "fair");
eq(wa.signalText(-90), "weak signal (-90 dBm)", "weak");
eq(wa.signalText(null), "signal unknown", "unknown signal");
eq(wa.NOTE, "Tilt is a guide, not a safety system.", "advisory wording");

console.log(fails ? `${fails} of ${count} checks failed` : `all ${count} checks passed`);
process.exit(fails ? 1 : 0);