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
const am = T.amps(m, Object.values(three), { d: dev("ok") });
eq([am.ok, am.max, am.lines.map((l) => l.label)], [true, 16, ["L1", "L2", "L3"]], "three lines; neutral and total are not lines");
eq(T.amps(m, Object.values({ ...three, b: cur("b", 8, true) }), { d: dev("ok") }).ok, false, "one old line: no bar (no partial figure)");
const ld = T.load(m, { state: "ok", amps: am }, { d: dev("ok") });
eq([ld.level, Math.round(ld.pct), ld.rating], ["ok", 50, 32], "16 A of 32 A");
eq(T.load({ ...m, rating_a: 20 }, { state: "ok", amps: am }, { d: dev("ok") }).level, "high", "80 % warns");
eq(T.load({ ...m, rating_a: 16 }, { state: "ok", amps: am }, { d: dev("ok") }).level, "over", "100 % is over");
// single-phase circuits on different lines are not added together: the busiest line, not the sum
const one = (ch, v) => ent(`d.${ch}_current`, v, false, { field: "current", channel: ch, group: "" });
const twoLines = T.amps(m, [one("c0", 10), one("c1", 7)], { d: { ...dev("ok"), channels: { c0: { phase: "L1" }, c1: { phase: "L2" } } } });
eq([twoLines.max, twoLines.lines.map((l) => l.label)], [10, ["L1", "L2"]], "two lines are not added");
const noLine = T.amps(m, [one("c0", 10), one("c1", 7)], { d: dev("ok") });
eq(noLine.max, 17, "circuits with no line set are one group");
eq(T.load(m, { state: "old", amps: am }, { d: dev("ok") }).level, "none", "no bar from an old reading");

// ---- the server's figures
const gapEnt = (v, day, stale) => ({ ["power_site.treegap_" + T.slug("m:d")]: { value: v, day, stale: !!stale } });
eq(T.gap("m:d", gapEnt(1000, { state: "ok" })).watts, 1000, "gap ok");
eq(T.gap("m:d", gapEnt(null, { state: "incomplete", reason: "x" })).watts, null, "incomplete has no number");
eq(T.gap("m:d", gapEnt(-5, { state: "mismatch" })).watts, null, "a mismatch never shows a number");
eq(T.gap("m:d", {}).state, "waiting", "no entity yet");
eq(T.gap("m:d", gapEnt(1000, { state: "ok" }, true)).state, "incomplete", "a stale gap is not shown as a figure");
eq(T.gapLine(T.gap("m:d", gapEnt(1000, { state: "ok" }))).head, "Unmonitored (calculated)", "worded as calculated");
eq(T.gapLine(T.gap("m:d", gapEnt(null, { state: "mismatch", ups: false }))).level, "check", "mismatch asks for a check");
eq(T.gapLine(T.gap("m:d", gapEnt(80, { state: "ok", ups: true }))).head, "Loss and charging (calculated)", "UPS loss wording");
eq(T.gapLine(T.gap("m:d", gapEnt(null, { state: "incomplete", ups: true }))).watts, null, "UPS loss needs both sides");
eq(T.total("tree_site", { "power_site.tree_site": { value: null, day: { complete: false, partial_w: 5000 }, stale: false } }), { watts: null, partial: 5000, complete: false, known: true }, "incomplete total: no value, the monitored part kept for display");
eq(T.total("tree_site", { "power_site.tree_site": { value: 5000, day: { complete: true }, stale: false } }).complete, true, "complete total");
eq(T.total("tree_site", {}).known, false, "no total yet");
eq(T.sourceTotal("p:g1", { ["power_site.treesrc_" + T.slug("p:g1")]: { value: 10, day: { complete: true }, stale: false } }).watts, 10, "source total by ref");
eq(T.gapLine(T.gap("m:d", gapEnt(null, { state: "battery", ups: true, reason: "r" }))).head, "▲ MAY BE ON BATTERY", "a UPS giving out more than it takes in is not a wiring check");
eq(T.gapLine(T.gap("m:d", gapEnt(null, { state: "battery", ups: true }))).text.indexOf("Upstream"), -1, "and does not say Upstream");
eq(T.gap("m:d", gapEnt(80, { state: "ok", ups: true, efficiency: 91 })).efficiency, 91, "UPS efficiency passes through");
eq(T.ownCircuitsOnly({ kind: "meter", ref: "m:d", children: [{ ref: "c:d:a" }, { ref: "c:d:b" }] }), true, "a meter against its own circuits has no gap");
eq(T.ownCircuitsOnly({ kind: "meter", ref: "m:d", children: [{ ref: "c:d:a" }, { ref: "m:e" }] }), false, "but one with another meter below it does");
eq(T.slug("m:abc"), "m_abc_f273c4f4", "same entity id piece as the server");
eq(T.slug("c:dev:ch_0"), "c_dev_ch_0_ad2dadf3", "including a circuit");
eq(T.watts(1500), { text: "1.50", unit: "kW" }, "kW from 1,000 W");
eq(T.watts(null).text, "—", "a dash, never 0");

// breaker and device limit: the lower one is used, and which one limits is named
const LIM = (b, d) => { const l = SW.limit(b, d); return [l.amps, l.by, l.weak]; };
eq(LIM(13, 16), [13, "breaker", false], "breaker below the device limit");
eq(LIM(63, 32), [32, "device", true], "breaker above the device limit: the device is the weak point");
eq(LIM(16, 16), [16, "breaker", false], "equal counts as the breaker");
eq(LIM(null, 16), [16, "device", false], "device limit only");
eq(LIM(20, null), [20, "breaker", false], "breaker only");
eq(LIM(null, null), [null, "", false], "neither: no bar");
eq(LIM(0, 16), [16, "device", false], "zero means unknown");
eq(SW.limitText(13, 16), "Breaker 13 A · device limit 16 A · limited by breaker", "both set");
eq(SW.limitText(null, 6.3), "Device limit 6.3 A", "one decimal only when needed");
eq(SW.limitText(32, null), "Breaker 32 A", "breaker only text");
eq(SW.pw.loadView(11.2, 16, "breaker").text, "11.2 A of 16 A (70%)", "one number format");
eq(SW.pw.loadView(14, 13, "breaker").word, "OVER BREAKER", "over the breaker");
eq(SW.pw.loadView(17, 16, "device").word, "OVER DEVICE LIMIT", "over the device limit");
// the tree: a point's rating is its breaker; a circuit falls back to its meter's numbers
const dv = { m1: { rating_a: 16, breaker_a: 13, channels: { c0: { breaker_a: 6 } } } };
eq(T.limits({ kind: "point", ref: "p:g", rating_a: 125 }, dv), { breaker: 125, device: null }, "point: rating is the breaker");
eq(T.limits({ kind: "meter", ref: "m:m1", rating_a: 16 }, dv), { breaker: 13, device: 16 }, "meter");
eq(T.limits({ kind: "circuit", ref: "c:m1:c0", rating_a: null }, dv), { breaker: 6, device: 16 }, "circuit: own breaker, meter's device limit");
eq(T.limits({ kind: "circuit", ref: "c:m1:c1", rating_a: null }, dv), { breaker: 13, device: 16 }, "circuit: meter's breaker");
const ldv = T.load({ kind: "meter", ref: "m:m1", rating_a: 16 }, { state: "ok", amps: { ok: true, max: 11.2, lines: [] } }, dv);
eq([ldv.rating, ldv.by, ldv.level], [13, "breaker", "high"], "11.2 A against a 13 A breaker is high load, not ok");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("power tree: all cases passed");
