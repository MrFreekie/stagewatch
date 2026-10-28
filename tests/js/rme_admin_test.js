// Node test for the RME Admin words and checks (SW.rmeAdmin in rme-admin.js): the Test answer in plain
// words by category, nothing private echoed, the bodies sent to the server, the card header chip.
// The answers are SYNTHETIC, shaped like POST /api/admin/rme/test. Run: node tests/js/rme_admin_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Math, Object, Array, String, Number, JSON, console, isFinite, Set, Map };
ctx.SW = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "rme-admin.js"), "utf8"), ctx, { filename: "rme-admin.js" });
const ra = vm.runInContext("SW.rmeAdmin", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}\n  got ${g}\n  exp ${e}`); }
}

// a good Test answer: the server's own sentence, no serial or address
let v = ra.testView({ ok: true, model: "12Mic", firmware: "2.0.1", name: "Stage L", already_added: false, message: "RME 12Mic, firmware 2.0.1, named 'Stage L'. Reading works." });
eq([v.ok, v.warn, v.name, v.already], [true, false, "Stage L", false], "good answer");
eq(v.lines[0], "RME 12Mic, firmware 2.0.1, named 'Stage L'. Reading works.", "model, firmware and name only");
eq(v.lines[v.lines.length - 1], "Nothing has been changed on the unit.", "says nothing was changed");
v = ra.testView({ ok: true, already_added: true, message: "x" });
eq(v.already, true, "already added is flagged so Save stays off");
// every category the server can send has plain advice, and a failure always says nothing changed
for (const c of ["not_local", "unreachable", "not_rme", "silent", "quiet", "other_unit", "redirect", "too_large", "closed", "bad_frames", "flood", "waiting"]) {
  const x = ra.testView({ ok: false, category: c, message: "Something plain" });
  eq([x.ok, x.warn, x.lines.length, x.lines[x.lines.length - 1]], [false, true, 3, "Nothing has been changed."], `category ${c}`);
}
v = ra.testView({ ok: false, category: "other_unit", message: "A different RME unit answered here" });
eq(/Replace unit/.test(v.lines[1]), true, "a swapped unit points to Replace unit");
// an unknown category: no invented advice
eq(ra.testView({ ok: false, category: "whatever", message: "No." }).lines, ["No.", "Nothing has been changed."], "unknown category");
// the category is not used as a key into anything else (prototype names)
eq(ra.testView({ ok: false, category: "constructor", message: "No." }).lines.length, 2, "prototype names get no advice");
eq(ra.testView(null).ok, false, "null answer");
// a hostile message stays a plain string for textContent
eq(ra.testView({ ok: false, message: "<b>x</b>" }).lines[0], "<b>x</b>", "message is passed through as text");

// bodies
eq(ra.addBody("  192.168.1.50 ", "", undefined), { host: "192.168.1.50" }, "test body without a port");
eq(ra.addBody("192.168.1.50", "8080", "Stage L", "FOH"), { host: "192.168.1.50", port: 8080, name: "Stage L", area: "FOH" }, "add body");
eq(ra.addBody("1.2.3.4", "99999", undefined), { host: "1.2.3.4" }, "a bad port is not sent");
eq([ra.portOk(""), ra.portOk("80"), ra.portOk("0"), ra.portOk("65536"), ra.portOk("8o")], [true, true, false, false, false], "port check");
const saved = { name: "A", area: "", host: "192.168.1.5", port: 80 };
eq(ra.patchBody(saved, { name: "B", area: "", host: "192.168.1.5", port: "80" }, false), { name: "B" }, "only what changed");
eq(ra.patchBody(saved, { name: "A", area: "", host: "192.168.1.5", port: "80" }, false), {}, "nothing changed");
eq(ra.patchBody(saved, { name: "A", area: "", host: "192.168.1.6", port: "81" }, false), { host: "192.168.1.6", port: 81 }, "address and port");
eq(ra.patchBody(saved, { name: "A", area: "", host: "192.168.1.5", port: "80" }, true), { replace_unit: true, host: "192.168.1.5", port: 80 }, "replace sends the address too");

// facts: no serial, nothing but model, firmware and age
eq(ra.facts({ model: "12Mic", firmware: "2.0.1", phase: "up", last_seen_age_s: 0 }), "12Mic · firmware 2.0.1", "up unit");
eq(ra.facts({ model: "12Mic", phase: "down", last_seen_age_s: 125 }), "12Mic · last heard 2 min ago", "away unit");
eq(ra.facts(null), "", "no unit");

// header chip: worst unit decides
eq(ra.chipInput([], 5), null, "no units: no chip");
eq(ra.chipInput([{ status: "ok", phase: "up" }], 5), { source: "rme", reported: "ok", dataAt: 5 }, "all fine");
eq(ra.chipInput([{ status: "ok", phase: "up" }, { status: "missing", phase: "down" }], 5).reported, "offline", "one away");
eq(ra.chipInput([{ status: "initializing", phase: "starting" }], 5).reported, "waiting", "starting");
eq(ra.chipInput([{ status: "compromised", phase: "up" }], 5).reported, "ok", "a unit with a CHECK is still online");

eq(ra.SECURITY, "RME units have no password: anyone on the network can change them. Stagewatch only reads.", "security line");

console.log(fails ? `${fails} of ${count} checks FAILED` : `${count} checks passed`);
process.exit(fails ? 1 : 0);
