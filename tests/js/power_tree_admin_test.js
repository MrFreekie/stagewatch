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

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("ok");
