// Node test for the About Stagewatch card (common.js: SW.aboutNodes, SW.ABOUT_URL).
// Run: node tests/js/about_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
class Node {}
function el(tag) {
  return Object.assign(new Node(), { tag, attrs: {}, children: [], setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; },
    append(...c) { this.children.push(...c); }, set textContent(v) { this.children = [String(v)]; } });
}
const ctx = { Node, Intl, Date, Number, Math, Object, Array, String, JSON, console, window: {},
  document: { createElement: el, createElementNS: (ns, t) => el(t), createTextNode: (t) => String(t) } };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "vendor", "qrcode.js"), "utf8") + "\nthis.qrcode = qrcode;", ctx, { filename: "qrcode.js" });
const SW = vm.runInContext("SW", ctx);

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
eq(SW.ABOUT_URL, "https://github.com/MrFreekie/stagewatch", "the project address");
const nodes = SW.aboutNodes();
const flat = JSON.stringify(nodes);
eq(flat.includes("Free, open-source show monitoring."), true, "the one-line text");
eq(flat.includes("https://github.com/MrFreekie/stagewatch"), true, "link to the project");
eq(flat.includes("QR code linking to the Stagewatch project page"), true, "QR has a descriptive label");
eq(nodes.length, 3, "line, QR plate, address");
// Without the QR library the card still shows the line and the address as text.
vm.runInContext("qrcode = undefined", ctx);
eq(SW.aboutNodes().length, 2, "no QR library: line and address only");
if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("about_test: all passed");
