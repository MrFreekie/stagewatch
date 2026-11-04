// Node test for the card id helpers (common.js SW.cardBase, SW.validCardId, SW.globconOpts, SW.cardIsHalf, SW.previewDraft)
// with several GLOBCON cards. Run: node tests/js/globcon_multi_test.js
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
const eq = (got, exp, what) => { if (got !== exp) { fails++; console.log(`FAIL ${what}: got ${got}, expected ${exp}`); } };
const G = "globcon_meters";

eq(SW.cardBase(G + "#2"), G, "base of an instance");
eq(SW.cardBase("chart"), "chart", "base of a plain id");
eq(SW.cardBase(5), "", "base of a non-string");
for (const ok of [G, G + "#2", G + "#9", G + "#10", G + "#16", "chart"]) eq(SW.validCardId(ok), true, "valid " + ok);
for (const bad of [G + "#1", G + "#0", G + "#17", G + "#02", G + "#", "chart#2", "#2", G + "#2#3", 5, null, undefined, ""]) eq(SW.validCardId(bad), false, "invalid " + bad);

const dash = { layout: "wall", card_sizes: { [G + "#2"]: "half" }, globcon: { controller: 1, strips: 8 }, globcon_more: { [G + "#2"]: { controller: 4, strips: 8, mode: "lights" } } };
eq(SW.cardIsHalf(dash, G + "#2", "wall"), true, "instance half");
eq(SW.cardIsHalf(dash, G, "wall"), false, "first card full");
eq(SW.cardIsHalf(dash, G + "#2", "phone"), false, "phone always full");
eq(SW.cardIsHalf({ card_sizes: { "chart#2": "half" } }, "chart#2", "wall"), false, "not a half-capable kind");
eq(SW.globconOpts(dash, G).controller, 1, "first card options");
eq(SW.globconOpts(dash, G + "#2").controller, 4, "instance options");
eq(SW.globconOpts(dash, G + "#3").controller, 1, "unset instance: defaults");
eq(SW.globconOpts(null, G + "#2").strips, 8, "no dashboard: defaults");
eq(SW.globconOpts({ globcon_more: {} }, "toString").controller, 1, "no prototype lookups");

const known = ["env_tiles", "chart", G];
const saved = { slug: "x", cards: [], card_sizes: {} };
const ok = (d) => SW.previewDraft(d, saved, known);
const more = { controller: 2, strips: 8, range: "1-8", mode: "lights", lights: [{ strip: 1, threshold_db: -40, colour: "green", icon: "signal", hold_s: 1 }] };
const out = ok({ cards: [G, G + "#2"], card_sizes: { [G + "#2"]: "half" }, globcon_more: { [G + "#2"]: more } });
eq(out && out.cards.join(), G + "," + G + "#2", "draft keeps both cards");
eq(out && out.card_sizes[G + "#2"], "half", "draft size");
eq(out && out.globcon_more[G + "#2"].lights.length, 1, "draft lights");
eq(ok({ cards: [G, G], globcon_more: {} }), null, "duplicate id");
eq(ok({ cards: ["chart#2"] }), null, "chart cannot repeat");
eq(ok({ cards: [G + "#17"] }), null, "suffix too high");
eq(ok({ cards: [G], globcon_more: { [G + "#2"]: more } }), null, "options for a card not listed");
eq(ok({ cards: [G, G + "#2"], globcon_more: { [G + "#2"]: { controller: 40, strips: 8 } } }), null, "bad controller");
eq(ok({ cards: [G, G + "#2"], globcon_more: { [G]: more } }), null, "first card options do not go in globcon_more");
eq(ok({ cards: [G, G + "#2"], card_sizes: { "chart#2": "half" } }), null, "size for a non-repeating card");

if (fails) { console.log(fails + " failed"); process.exit(1); }
console.log("globcon_multi_test ok");
