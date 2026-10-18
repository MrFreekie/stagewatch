// Node test for the Edit cards preview draft check (common.js SW.previewDraft). Run: node tests/js/preview_draft_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
const SW = vm.runInContext("SW", ctx);

let fails = 0;
function eq(got, exp, what) {
  if (got !== exp) { fails++; console.log(`FAIL ${what}: got ${got}, expected ${exp}`); }
}
const known = ["env_tiles", "chart", "markers", "wall_clock", "power", "globcon_meters"];
const saved = { slug: "foh", title: "FOH", layout: "tablet", allow_marker: false, allow_ack: false, cards: ["chart"], card_sizes: {}, stage: "" };
const ok = (d) => SW.previewDraft(d, saved, known);

let out = ok({ cards: ["wall_clock", "chart"], card_sizes: { wall_clock: "half" }, stage: "Main" });
eq(out && out.cards.join(","), "wall_clock,chart", "order kept");
eq(out && out.card_sizes.wall_clock, "half", "half kept");
eq(out && out.stage, "Main", "stage");
eq(out && out.slug, "foh", "slug from saved");
eq(saved.cards.join(","), "chart", "saved not changed");
out = ok({ cards: [], allow_marker: true, allow_ack: true, slug: "x", layout: "wall" });
eq(out && out.allow_marker, false, "draft cannot allow markers");
eq(out && out.allow_ack, false, "draft cannot allow ack");
eq(out && out.slug + out.layout, "fohtablet", "draft cannot change slug or layout");
eq(ok({ cards: ["nope"] }), null, "unknown card");
eq(ok({ cards: ["chart", "chart"] }), null, "duplicate card");
eq(ok({ cards: "chart" }), null, "cards not a list");
eq(ok({}), null, "no cards");
eq(ok({ cards: [1] }), null, "card not a string");
eq(ok({ cards: Array.from({ length: 17 }, (_, i) => `c${i}`) }), null, "too many cards");
const many = Array.from({ length: 16 }, (_, i) => `c${i}`);
eq(!!SW.previewDraft({ cards: many }, saved, many), true, "16 cards allowed");
eq(ok({ cards: ["chart"], card_sizes: { chart: "half" } }), null, "half on a card that cannot be half");
eq(ok({ cards: ["power"], card_sizes: { power: "huge" } }), null, "unknown size");
eq(ok({ cards: ["power"], card_sizes: ["half"] }), null, "sizes not an object");
eq(ok({ cards: ["power"], card_sizes: { power: "full" } }).card_sizes.power, undefined, "full is dropped");
eq(ok({ cards: [], stage: "x".repeat(41) }), null, "stage too long");
eq(ok({ cards: [], show_dew_point: "yes" }), null, "bool must be bool");
eq(ok({ cards: [], globcon: { controller: 17, strips: 8 } }), null, "globcon controller range");
eq(ok({ cards: [], globcon: { controller: 2, strips: 6 } }), null, "globcon strips");
eq(ok({ cards: [], globcon: { controller: 2, strips: 8, range: "1-99" } }), null, "globcon range");
eq(ok({ cards: [], globcon: { controller: 2, strips: 8, range: "9-16" } }).globcon.range, "9-16", "globcon ok");
eq(ok(null), null, "null draft");
eq(SW.previewDraft({ cards: [] }, null, known), null, "no saved dashboard");

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("preview_draft_test: all passed");
