// Node test for the power block diagram's pure layout (power-diagram.js). Coordinates are worked out by hand
// from the sizes: box 200 x 52, column gap 100, row gap 12, padding 12, heading 26, group gap 24.
// Run: node tests/js/power_diagram_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Set, Number, String, Math, Object, Array, JSON, console, SW: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "power-diagram.js"), "utf8"), ctx, { filename: "power-diagram.js" });
const D = ctx.SW.ptDiagram;

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const n = (ref, role, upstream, name, feed) => ({ ref, role, upstream: upstream || "", name: name || ref, display: ref.toUpperCase(), feed: feed || "" });
const at = (lay, ref) => { const b = lay.boxes.find((x) => x.ref === ref); return b ? [b.x, b.y, b.col] : null; };

// one root: supply -> CDU -> two final units. Leaves at rows 0 and 1 (y 12 and 76); the CDU and supply sit between (44).
const one = D.layout([n("s", "source"), n("c", "cdu", "s"), n("f1", "fdu", "c", "A", "CDU01-C1-L1"), n("f2", "fdu", "c", "B")]);
eq(at(one, "s"), [12, 44, 0], "root centred on its children");
eq(at(one, "c"), [312, 44, 1], "CDU in column 1");
eq(at(one, "f1"), [612, 12, 2], "first final unit");
eq(at(one, "f2"), [612, 76, 2], "second final unit");
eq([one.width, one.height], [824, 140], "size of one root");
eq(one.groups, [], "one supply: no heading");
eq(one.links[1], { from: "c", to: "f1", d: "M512 70H536V38H612", label: "CDU01-C1-L1", lx: 540, ly: 34 }, "elbow from the CDU to f1 with its way and line label");
eq(one.links.length, 3, "three links");

// two roots stacked, each with a heading. Supplies sort by display name (S1 before S2). The first group's boxes start at
// 12 + 26 = 38; its box ends at 90; plus the 24 gap, the second heading row starts at 114 (text baseline 130), boxes at 140.
const two = D.layout([n("s2", "source", "", "Grid"), n("s1", "source", "", "Gen"), n("a", "isu", "s1"), n("b", "isu", "s2")]);
eq(two.groups.map((g) => [g.title, g.y]), [["Supply: Gen", 28], ["Supply: Grid", 130]], "two headings");
eq([at(two, "s1"), at(two, "a"), at(two, "s2"), at(two, "b")], [[12, 38, 0], [312, 38, 1], [12, 140, 0], [312, 140, 1]], "two stacked trees");
eq(two.height, 140 + 52 + 12, "height of two groups");

// a deep chain: one box per column, all on the same row
const deep = D.layout([n("s", "source"), n("i", "isu", "s"), n("c", "cdu", "i"), n("f", "fdu", "c"), n("e", "equipment", "f")]);
eq(deep.boxes.map((b) => [b.x, b.y]), [[12, 12], [312, 12], [612, 12], [912, 12], [1212, 12]], "a chain runs straight across");
eq([deep.width, deep.height], [1424, 76], "size of a chain");

// unassigned: a unit with no supply, and one whose upstream is not in the list, go under an Unassigned heading
const un = D.layout([n("s", "source"), n("c", "cdu", "s"), n("z", "fdu"), n("y", "fdu", "gone")]);
eq(un.groups.map((g) => [g.title, g.unassigned]), [["Supply: s", false], ["Unassigned", true]], "supply and unassigned headings");
eq([at(un, "y"), at(un, "z")], [[12, 140, 0], [12, 204, 0]], "unassigned units stacked as roots");
const only = D.layout([n("z", "fdu")]);
eq(only.groups.map((g) => g.title), ["Unassigned"], "only unassigned still has its heading");
eq(at(only, "z"), [12, 38, 0], "box under the heading");

// loops cannot hang or lose a unit: each is drawn once, flagged, in Unassigned
const lp = D.layout([n("a", "cdu", "b"), n("b", "fdu", "a"), n("s", "source")]);
eq(lp.boxes.length, 3, "every unit drawn once");
eq(lp.boxes.filter((b) => b.loop).map((b) => b.ref), ["a", "b"], "the loop is flagged");
eq(D.layout([n("a", "source", "a")]).boxes.length, 1, "a unit fed from itself");

// the cap: a message count, not a freeze
const many = []; for (let i = 0; i < 250; i++) many.push(n("u" + i, "equipment", i ? "s" : "", "x"));
many[0] = n("s", "source");
const capped = D.layout(many);
eq([capped.drawn, capped.total, capped.hidden], [200, 250, 50], "capped at 200");

// live mode makes the boxes taller; names are clipped
eq(D.layout([n("s", "source")], { live: true }).boxes[0].h, 88, "live box height");
eq(D.clip("abcdefghij", 5), "abcd\u2026", "clip long");
eq(D.clip("abc", 5), "abc", "clip short");
eq(D.layout([]).boxes, [], "empty");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("ok");
