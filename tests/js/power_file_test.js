// Node test for the Power distribution "Read a file" preview helpers (power-tree-admin.js).
// Run: node tests/js/power_file_test.js
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

const r = {
  new: [{ kind: "point", name: "FOH generator" }],
  changed: [{ kind: "meter", name: "Distro A", fields: ["number", "what feeds it"] }, { kind: "point", name: "Intake", fields: ["name"] }],
  unchanged: [{ kind: "meter", name: "Desk meter" }],
  skipped: [{ name: "Rack meter", why: "This hub does not have that meter." }],
  site_changed: false,
};
eq(PA.previewHeadline(r), "Nothing has been changed yet. Applying this file would add 1 and change 2; 1 already matches. 1 item is skipped.", "headline");
eq(PA.previewSections(r).map((s) => s.title), ["New", "Changed", "Skipped (not on this hub)", "Already the same"], "section order");
eq(PA.previewSections(r)[1].lines, ["Meter Distro A: number; what feeds it", "Point Intake: name"], "changed lines");
eq(PA.previewSections(r)[2].lines, ["Rack meter. This hub does not have that meter."], "skipped lines");
eq(PA.canApply(r), true, "can apply");
const same = { new: [], changed: [], unchanged: [{ kind: "point", name: "A" }], skipped: [], site_changed: false };
eq(PA.canApply(same), false, "nothing to apply");
eq(PA.previewHeadline(same), "Nothing to apply: everything in this file already matches.", "headline when equal");
eq(PA.previewSections(same).map((s) => s.title), ["Already the same"], "empty sections are left out");
eq(PA.canApply({ new: [], changed: [], site_changed: true }), true, "site settings alone can be applied");
eq(PA.previewHeadline({ new: [{ kind: "point", name: "x" }], changed: [], unchanged: [], skipped: [], site_changed: true }),
  "Nothing has been changed yet. Applying this file would add 1 and change 0; 0 already match. The site settings would change too.", "site words");
// a name with markup stays plain text (the page sets it with textContent)
eq(PA.previewSections({ new: [{ kind: "point", name: "<img src=x onerror=1>" }] })[0].lines, ["Point: <img src=x onerror=1>"], "markup stays text");
// the page never builds HTML from file text
const page = fs.readFileSync(path.join(STATIC, "power-distribution.js"), "utf8");
eq(/innerHTML|insertAdjacentHTML|outerHTML/.test(page), false, "no HTML from text on the page");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("power file: all cases passed");
