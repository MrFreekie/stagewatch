// Node test for SW.sourceStatus / SW.sourceInput (common.js): each card's Online / Offline / Error.
// Run: node tests/js/source_status_test.js   (tests/test_source_status.py runs it when node is installed)
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
const S = (o) => SW.sourceStatus(Object.assign({ source: "ontime", reported: "ok", ageS: 1, staleS: 3, stateS: 125, stateExact: true }, o));

// Online only with fresh data from a source that says ok.
let s = S({});
eq([s.state, s.word, s.symbol, s.cls], ["online", "Online", "●", "ok"], "online");
eq(s.lines, ["Online for 2 min 05 s", "Last data 1 s ago"], "online lines");
eq(s.label, "Ontime: Online", "online label");
eq(S({ stateExact: false }).lines[0], "Online for at least 2 min 05 s", "page loaded after the change");
eq(S({ ageS: 3 }).state, "online", "exactly at the limit is online");

// Stale beats online; the pop-out says how long.
s = S({ ageS: 12 });
eq([s.state, s.kind, s.word, s.symbol], ["offline", "stale", "Offline", "✕"], "stale");
eq(s.lines[0], "Last data 12 s ago", "stale age");
eq(s.label, "Ontime: Offline. Last data 12 s ago", "stale label");
eq(S({ reported: "stale", ageS: 1 }).state, "offline", "source-reported stale");

// No Online without data. Waiting is Offline with the word Waiting.
for (const o of [{ ageS: null }, { reported: "waiting", ageS: null }, { reported: "waiting", ageS: 1 }]) {
  s = S(o);
  eq([s.state, s.kind, s.word], ["offline", "waiting", "Offline"], "waiting " + JSON.stringify(o));
  eq(/waiting/i.test(s.headline) && /Waiting/.test(s.lines.join(" ")) && /Waiting/.test(s.label), true, "says Waiting");
}

// Offline, and the precedence with errors: offline beats stale; an explicit error beats stale and offline data.
s = S({ reported: "offline", ageS: 40 });
eq([s.state, s.kind, s.advice], ["offline", "offline", "Check Ontime is running and its address in Admin → Ontime."], "offline");
eq(s.lines, ["Last data 40 s ago", "Offline for 2 min 05 s"], "offline lines");
eq(S({ reported: "offline", ageS: null }).lines[0], "No data received yet", "never seen");
s = S({ reported: "error", ageS: 60 });
eq([s.state, s.word, s.symbol, s.cls], ["error", "Error", "!", "err"], "explicit error beats stale");
eq(S({ reported: "locked", source: "globcon" }).state, "error", "locked is an error");
eq(S({ reported: "locked", source: "globcon" }).advice, "Ask the system admin to enter the password in Admin.", "locked advice");

// Wording per source.
eq(S({ source: "smaart", reported: "offline" }).advice, "Check Smaart is running with its API on.", "smaart advice");
eq(S({ source: "globcon", reported: "offline" }).advice, "Check GLOBCON is running and the Windows firewall allows it.", "globcon advice");
eq(S({ source: "globcon", reported: "offline" }).label.indexOf("GLOBCON: Offline"), 0, "globcon label");

// Input from each card's own data.
const T = 1000;
eq(SW.sourceInput.ontime({ status: "ok", received_at: T - 1 }, 3), { source: "ontime", reported: "ok", staleS: 3, dataAt: T - 1 }, "ontime ok");
eq(SW.sourceInput.ontime({ status: "offline", received_at: T }, 3).dataAt, null, "an offline reading is not data");
eq(SW.sourceInput.ontime({ status: "error", received_at: T }, 3).reported, "error", "ontime error");
eq(SW.sourceInput.ontime(null, 3).reported, "offline", "no message");
eq(SW.sourceInput.globcon({ status: "ok" }, { meters_at: T - 2 }, 3).reported, "ok", "globcon ok");
eq(SW.sourceInput.globcon({ status: "ok" }, { meters_at: null }, 3).reported, "waiting", "globcon waiting");
eq(SW.sourceInput.globcon({ status: "offline" }, { meters_at: T - 2 }, 3).reported, "offline", "globcon offline");
eq(SW.sourceInput.globcon({ status: "ok" }, { locked: true }, 3).reported, "locked", "globcon locked");
const ent = (o) => Object.assign({ updated: T - 1, stale: false, value: 70.1 }, o);
eq(SW.sourceInput.smaart({ status: "ok" }, [ent()]).reported, "ok", "smaart ok");
eq(SW.sourceInput.smaart({ status: "ok" }, [ent({ stale: true })]).reported, "stale", "smaart stale");
eq(SW.sourceInput.smaart({ status: "ok" }, [ent({ updated: 0 })]).reported, "waiting", "smaart no value yet");
eq(SW.sourceInput.smaart({ status: "missing" }, [ent()]).reported, "offline", "smaart missing");
eq(SW.sourceInput.smaart({ status: "fault" }, [ent()]).reported, "error", "smaart fault");
eq(SW.sourceInput.smaart({ status: "initializing" }, []).reported, "waiting", "smaart starting");
eq(SW.sourceInput.smaart(undefined, []).reported, "waiting", "no device yet");

// Plain words only: no addresses, ports, hosts or technical terms on a public screen.
for (const src of ["ontime", "smaart", "globcon"]) {
  for (const r of ["ok", "stale", "waiting", "offline", "error", "locked"]) {
    const x = S({ source: src, reported: r }), text = [x.headline, x.advice, x.label, x.word].concat(x.lines).join(" ");
    eq(/\d+\.\d+\.\d+|:\d{2,5}\b|localhost|websocket|http|exception|traceback|timeout|ECONN|errno/i.test(text), false, "no technical text: " + text);
  }
}

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log("source status: all passed");
