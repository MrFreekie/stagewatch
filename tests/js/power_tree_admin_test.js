// Node test for the Power distribution card's pure rules (power-tree-admin.js).
// Run: node tests/js/power_tree_admin_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Set, Number, String, Math, Object, Array, console, SW: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "power-tree-admin.js"), "utf8"), ctx, { filename: "power-tree-admin.js" });
const PA = ctx.SW.ptAdmin;

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const roles = [
  { key: "source", abbr: "SRC", rank: 0 }, { key: "isu", abbr: "ISU", rank: 1 }, { key: "cdu", abbr: "CDU", rank: 2 },
  { key: "fdu", abbr: "FDU", rank: 3 }, { key: "equipment", abbr: "EQP", rank: 4 },
  { key: "ups", abbr: "UPS", rank: null }, { key: "other", abbr: "OTH", rank: null }];

eq(PA.display(roles, "isu", 1), "ISU01", "two digits");
eq(PA.display(roles, "cdu", 12), "CDU12", "twelve");
eq(PA.display(roles, "ups", null), "UPS", "no number");
eq(PA.display(roles, "", 3), "", "no role");
eq(PA.display(roles, "fdu", 100), "FDU", "out of range");

const nodes = [
  { ref: "p:gen", role: "source", upstream: "" }, { ref: "p:isu", role: "isu", upstream: "p:gen" },
  { ref: "m:a", role: "cdu", upstream: "p:isu" }, { ref: "m:b", role: "fdu", upstream: "m:a" },
  { ref: "m:u", role: "ups", upstream: "m:a" }, { ref: "m:n", role: "", upstream: "" }];
eq([...PA.descendants(nodes, "m:a")].sort(), ["m:b", "m:u"], "descendants");
eq(PA.upstreamChoices(roles, nodes, "p:gen", "source"), [], "a supply has nothing above it");
// isu: the supply and the unset node; not the CDU, FDU or UPS that hang below it
eq(PA.upstreamChoices(roles, nodes, "p:isu", "isu"), ["p:gen", "m:n"], "isu choices");
// cdu: the supply, the intake unit or the unset node; not itself or what it feeds
eq(PA.upstreamChoices(roles, nodes, "m:a", "cdu"), ["p:gen", "p:isu", "m:n"], "cdu choices");
// equipment: every higher level, the UPS and nothing else
eq(PA.upstreamChoices(roles, nodes, "m:n", "equipment"), ["p:gen", "p:isu", "m:a", "m:b", "m:u"], "equipment choices");
// a UPS is free except for itself (nothing hangs below it)
eq(PA.upstreamChoices(roles, nodes, "m:u", "ups"), ["p:gen", "p:isu", "m:a", "m:b", "m:n"], "ups choices");
eq(PA.slug("FOH generator 1", new Set()), "foh_generator_1", "slug");
eq(PA.slug("FOH generator 1", new Set(["foh_generator_1"])), "foh_generator_1_2", "slug taken");
eq(PA.slug("", new Set()), "point", "slug empty");

// Upstream option labels: the prefix shows as soon as role and number are set, and says why when it cannot
eq(PA.optionLabel(roles, "fdu", 1, "Stage left racks"), "FDU01 · Stage left racks", "prefix and name");
eq(PA.optionLabel(roles, "fdu", null, "Stage left racks"), "FDU (no number) · Stage left racks", "role but no number");
eq(PA.optionLabel(roles, "", null, "Stage left racks"), "Stage left racks (no role)", "no role");
eq(PA.optionLabel(roles, "", null, ""), "Unnamed (no role)", "no role, no name");
eq(PA.optionLabel(roles, "isu", 2, ""), "ISU02", "prefix alone");
// live: the same node read again after the admin picks a role and types a number
const live = { role: "", no: null };
eq(PA.optionLabel(roles, live.role, live.no, "Racks"), "Racks (no role)", "before");
live.role = "fdu"; eq(PA.optionLabel(roles, live.role, live.no, "Racks"), "FDU (no number) · Racks", "role picked");
live.no = 1; eq(PA.optionLabel(roles, live.role, live.no, "Racks"), "FDU01 · Racks", "number typed");

// auto-number: the next free number for the role, ignoring the row itself
const used = [{ ref: "a", role: "fdu", no: 1 }, { ref: "b", role: "fdu", no: 2 }, { ref: "c", role: "fdu", no: 4 }, { ref: "d", role: "cdu", no: 1 }, { ref: "self", role: "fdu", no: 3 }];
eq(PA.nextNumber(used, "self", "fdu"), 3, "fills the gap, not the row's own number");
eq(PA.nextNumber(used, "x", "fdu"), 5, "next free after 1, 2, 3 (self), 4");
eq(PA.nextNumber(used, "x", "cdu"), 2, "per role");
eq(PA.nextNumber(used, "x", "isu"), 1, "first of a role");
eq(PA.nextNumber(used, "x", ""), null, "no role, no number");
// the likely upstream: the only unit of the nearest higher role
const sn = [{ ref: "g", role: "source" }, { ref: "i", role: "isu" }, { ref: "c1", role: "cdu" }, { ref: "me", role: "fdu" }];
eq(PA.suggestUpstream(roles, sn, "me", "fdu"), "c1", "the only CDU");
eq(PA.suggestUpstream(roles, sn.concat([{ ref: "c2", role: "cdu" }]), "me", "fdu"), "", "two CDUs: no single obvious one");
eq(PA.suggestUpstream(roles, sn, "me", "cdu"), "i", "nearest higher role");
eq(PA.suggestUpstream(roles, sn, "me", "source"), "", "a supply has nothing above");
eq(PA.suggestUpstream(roles, sn, "me", "ups"), "", "a UPS: the admin chooses");
// labels
eq(PA.linkLabel("CDU02", 2, "L1"), "CDU02-C2-L1", "single phase");
eq(PA.linkLabel("CDU01", 1, "3P"), "CDU01-C1-3P", "three phase");
eq(PA.linkLabel("CDU02", 2, ""), "CDU02-C2", "no line known: no line part");
eq(PA.linkLabel("CDU02", null, "L1"), "CDU02", "no way: just the unit above");
eq(PA.linkLabel("", 2, "L1"), "", "nothing above");
// way validation: one way of an upstream feeds one thing
const wn = [{ ref: "a", upstream: "u", way: 2, label: "EQP01" }, { ref: "b", upstream: "u", way: 3, label: "EQP02" }, { ref: "c", upstream: "v", way: 2, label: "EQP03" }];
eq(PA.wayClash(wn, "x", "u", 2), "Way 2 is already used by EQP01. Each way feeds one thing.", "clash");
eq(PA.wayClash(wn, "a", "u", 2), "", "its own way is not a clash");
eq(PA.wayClash(wn, "x", "w", 2), "", "another upstream is fine");
eq(PA.wayClash(wn, "x", "u", null), "", "no way, no clash");
eq(PA.wayClash(wn, "x", "", 2), "", "no upstream, no clash");
// one row for a single-circuit meter; a header and a row per circuit otherwise; a lone three-phase circuit is the single case
eq(PA.meterRows({ id: "m", single: true, channels: [{ key: "pm1_0" }] }).map((r) => r.ref), ["m:m"], "one row for one circuit");
eq(PA.meterRows({ id: "m", single: false, channels: [{ key: "a" }, { key: "b" }] }).map((r) => r.ref), ["m:m", "c:m:a", "c:m:b"], "header and two circuits");
eq(PA.meterRows({ id: "m", single: false, channels: [{ key: "em0", three_phase: true }] }).map((r) => r.ref), ["m:m"], "a meter not yet known as single shows its header only");
eq(PA.meterRows({ id: "m", single: false, channels: [] }).map((r) => r.ref), ["m:m"], "no circuits seen yet: the header only");

// filter: every word must be found, any case, any order
eq(PA.matches("FDU02 Stage left racks", "stage FDU"), true, "words in any order");
eq(PA.matches("FDU02 Stage left racks", "fdu03"), false, "a word that is missing");
eq(PA.matches("anything", "   "), true, "blank filter shows all");
eq(PA.rowVisible("FDU01", "fdu", "", "fdu"), true, "kind filter keeps its kind");
eq(PA.rowVisible("CDU01", "cdu", "", "fdu"), false, "kind filter hides others");
eq(PA.rowVisible("x", "", "", "__none"), true, "no-role filter keeps roleless");
eq(PA.rowVisible("x", "cdu", "", "__none"), false, "no-role filter hides roles");
eq(PA.rowVisible("FDU01 foh", "fdu", "bar", "fdu"), false, "kind and text both apply");
// grouping under the supply
const ent = [
  { ref: "p:g1", role: "source", upstream: "" }, { ref: "p:g2", role: "source", upstream: "" },
  { ref: "m:a", role: "cdu", upstream: "p:g1" }, { ref: "m:b", role: "fdu", upstream: "m:a" },
  { ref: "m:c", role: "fdu", upstream: "p:g2" }, { ref: "m:lone", role: "", upstream: "" },
  { ref: "m:d", role: "equipment", upstream: "m:b" }];
eq(PA.groupRows(ent), [
  { key: "p:g1", unassigned: false, refs: ["p:g1", "m:a", "m:b", "m:d"] },
  { key: "p:g2", unassigned: false, refs: ["p:g2", "m:c"] },
  { key: "", unassigned: true, refs: ["m:lone"] }], "grouped by top supply, unassigned last, children under parents");
eq(PA.groupRows([{ ref: "a", role: "cdu", upstream: "ghost" }]), [{ key: "a", unassigned: true, refs: ["a"] }].map((g) => ({ key: "", unassigned: true, refs: ["a"] })), "an upstream that is not there leaves it unassigned");
eq(PA.groupRows([{ ref: "x", role: "cdu", upstream: "y" }, { ref: "y", role: "cdu", upstream: "x" }]).map((g) => g.key), ["x"], "a loop makes one group, not a crash");
eq(PA.groupRows([{ ref: "isu", role: "isu", upstream: "" }, { ref: "k", role: "cdu", upstream: "isu" }]), [{ key: "isu", unassigned: false, refs: ["isu", "k"] }], "a top unit that is not a supply still heads its group");
eq(PA.groupRows([]), [], "nothing");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("ok");
