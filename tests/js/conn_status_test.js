// Node test for SW.connStatus (common.js): the header connection indicator's state and wording.
// Run: node tests/js/conn_status_test.js   (tests/test_conn_status.py runs it when node is installed)
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
const SW = vm.runInContext("SW", ctx);

let fails = 0;
function eq(got, exp, what) {
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const T = 1000000000000;
const S = (o) => SW.connStatus(Object.assign({ now: T, wsUp: true, since: T - 300000, lastHeard: T - 2000, fault: false }, o));

// Online: feed up and data fresh.
let s = S({});
eq([s.state, s.word, s.symbol, s.cls], ["online", "Online", "●", "ok"], "online");
eq(s.lines, ["Connected for 5 min 00 s", "Last data 2 s ago"], "online lines");
eq(s.advice, "Nothing to do.", "online advice");

// Stale beats online: silent too long while the socket looks up.
eq(S({ lastHeard: T - 41000 }).state, "offline", "silent feed");
eq(S({ lastHeard: T - 40000 }).state, "online", "exactly at the limit is still online");
s = S({ lastHeard: T - 12000 - 40000 });
eq([s.word, s.symbol, s.lines[0], s.label], ["Offline", "✕", "Last data 52 s ago", "Offline. Last data 52 s ago"], "silent wording");
eq(S({ lastHeard: null }).state, "offline", "nothing heard yet is not online");
eq(S({ lastHeard: null }).label, "Offline. No data yet", "no data label");

// Offline: connection lost, with ages and the advice.
s = S({ wsUp: false, since: T - 90000, lastHeard: T - 12000 });
eq([s.state, s.lines, s.advice], ["offline", ["Last data 12 s ago", "Disconnected for 1 min 30 s"], "Check the Wi-Fi, then reload the page."], "lost");
eq(s.label, "Offline. Last data 12 s ago", "lost label");

// Error: server answers but the feed is faulty; never beats offline, never shown with stale data.
s = S({ fault: true });
eq([s.state, s.word, s.symbol, s.cls], ["error", "Error", "!", "err"], "error");
eq(S({ fault: true, wsUp: false }).state, "offline", "offline beats error");
eq(S({ fault: true, lastHeard: T - 60000 }).state, "offline", "stale beats error");

// Plain words only: no addresses, ports, hosts or technical terms on a public screen.
for (const o of [{}, { wsUp: false }, { fault: true }, { lastHeard: null }]) {
  const x = S(o), text = [x.headline, x.advice, x.label].concat(x.lines).join(" ");
  eq(/\d+\.\d+\.\d+|:\d{2,5}\b|localhost|websocket|http|exception|traceback/i.test(text), false, "no technical text: " + text);
}

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("connection status: all passed");
