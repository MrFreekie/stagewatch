// Node test for the power tree card's helpers (power-tree.js). Run: node tests/js/power_tree_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, Set, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
for (const f of ["common.js", "power.js", "power-tree.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);
const T = SW.ptree;

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

const node = (ref, role, upstream, extra) => ({ ref, kind: ref[0] === "p" ? "point" : ref[0] === "m" ? "meter" : "circuit", name: ref, role, display: role.toUpperCase(), upstream: upstream || "", ...extra });

// ---- the forest: two roots, an unassigned meter, children under their parents
const s = { nodes: [node("p:g2", "source"), node("p:g1", "source"), node("m:b", "cdu", "m:a"), node("m:a", "isu", "p:g1"), node("m:z", "equipment", ""), node("m:c", "isu", "p:g2")] };
const f = T.forest(s);
eq(f.roots.length, 2, "two independent roots");
eq(f.roots.find((n) => n.ref === "p:g1").children.map((n) => n.ref), ["m:a"], "g1 has the ISU");
eq(f.roots.find((n) => n.ref === "p:g1").children[0].children.map((n) => n.ref), ["m:b"], "the CDU is under the ISU");
eq(f.roots.find((n) => n.ref === "p:g2").children.map((n) => n.ref), ["m:c"], "g2 has its own ISU");
eq(f.loose.map((n) => n.ref), ["m:z"], "a meter with no supply is Unassigned");
eq(T.inUse({ nodes: [node("m:x", "", "")] }), false, "no role and no upstream: no tree");
eq(T.inUse(s), true, "tree in use");
const loop = { nodes: [node("p:g", "source"), node("m:a", "isu", "m:b"), node("m:b", "cdu", "m:a")] };
eq(T.forest(loop).roots.length, 1, "a loop in bad data does not hang");

// ---- a node's own reading
const dev = (status) => ({ id: "d", status, rating_a: 16 });
const ent = (id, v, stale, labels) => ({ id, device_id: "d", value: v, stale: !!stale, updated: 100, labels: labels || {} });
const ents = { "d.meter_power_load": ent("d.meter_power_load", 1234, false, { derived: "1" }) };
const m = node("m:d", "isu", "p:g", { rating_a: 32 });
eq(T.reading(m, ents, { d: dev("ok") }, 100).state, "ok", "live meter");
eq(T.reading(m, ents, { d: dev("ok") }, 100).w, 1234, "its watts");
eq(T.reading(m, { "d.meter_power_load": ent("x", 1234, true) }, { d: dev("ok") }, 100).state, "old", "stale reading is old, never live");
eq(T.reading(m, ents, { d: dev("missing") }, 100).state, "none", "meter not answering");
eq(T.reading(m, {}, { d: dev("ok") }, 100).state, "waiting", "nothing received yet");
eq(T.reading(node("p:x", "cdu"), ents, {}, 100).state, "unmetered", "a point has no meter: not monitored");
eq(T.reading(node("p:x", "cdu"), ents, {}, 100).w, null, "and no figure, never zero");
eq(T.reading(node("c:d:ch0", "equipment"), { "d.ch0_total_power": ent("d.ch0_total_power", 50) }, { d: dev("ok") }, 100).w, 50, "a circuit falls back to its total group");

// ---- amps and the bar: busiest of three lines, all or nothing
const cur = (g, v, stale) => ent(`d.em_${g}_current`, v, stale, { field: "current", channel: "em", group: g });
const three = { a: cur("a", 16, false), b: cur("b", 8, false), c: cur("c", 4, false), t: cur("total", 28, false), n: cur("n", 9, false) };
const am = T.amps(m, three);
eq([am.ok, am.max, am.lines.map((l) => l.label)], [true, 16, ["L1", "L2", "L3"]], "three lines; neutral and total are not lines");
eq(T.amps(m, { ...three, b: cur("b", 8, true) }).ok, false, "one old line: no bar (no partial figure)");
const ld = T.load(m, { state: "ok", amps: am }, { d: dev("ok") });
eq([ld.level, Math.round(ld.pct), ld.rating], ["ok", 50, 32], "16 A of 32 A");
eq(T.load({ ...m, rating_a: 20 }, { state: "ok", amps: am }, { d: dev("ok") }).level, "high", "80 % warns");
eq(T.load({ ...m, rating_a: 16 }, { state: "ok", amps: am }, { d: dev("ok") }).level, "over", "100 % is over");
eq(T.load(m, { state: "old", amps: am }, { d: dev("ok") }).level, "none", "no bar from an old reading");

// ---- the server's figures
const gapEnt = (v, day, stale) => ({ "power_site.treegap_m_d": { value: v, day, stale: !!stale } });
eq(T.gap("m:d", gapEnt(1000, { state: "ok" })).watts, 1000, "gap ok");
eq(T.gap("m:d", gapEnt(null, { state: "incomplete", reason: "x" })).watts, null, "incomplete has no number");
eq(T.gap("m:d", gapEnt(-5, { state: "mismatch" })).watts, null, "a mismatch never shows a number");
eq(T.gap("m:d", {}).state, "waiting", "no entity yet");
eq(T.gap("m:d", gapEnt(1000, { state: "ok" }, true)).state, "incomplete", "a stale gap is not shown as a figure");
eq(T.gapLine(T.gap("m:d", gapEnt(1000, { state: "ok" }))).head, "Unmonitored (calculated)", "worded as calculated");
eq(T.gapLine(T.gap("m:d", gapEnt(null, { state: "mismatch", ups: false }))).level, "check", "mismatch asks for a check");
eq(T.gapLine(T.gap("m:d", gapEnt(80, { state: "ok", ups: true }))).head, "Loss and charging (calculated)", "UPS loss wording");
eq(T.gapLine(T.gap("m:d", gapEnt(null, { state: "incomplete", ups: true }))).watts, null, "UPS loss needs both sides");
eq(T.total("tree_site", { "power_site.tree_site": { value: 5000, day: { complete: false }, stale: false } }), { watts: 5000, complete: false, known: true }, "incomplete total is flagged");
eq(T.total("tree_site", {}).known, false, "no total yet");
eq(T.sourceTotal("p:g1", { "power_site.treesrc_p_g1": { value: 10, day: { complete: true }, stale: false } }).watts, 10, "source total by ref");
eq(T.watts(1500), { text: "1.50", unit: "kW" }, "kW from 1,000 W");
eq(T.watts(null).text, "—", "a dash, never 0");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("power tree: all cases passed");
