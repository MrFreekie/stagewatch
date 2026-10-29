// Node test for the Clock card's pure helpers (clock.js). Run: node tests/js/clock_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Math, Number, Object, Array, String, JSON, Infinity, console };
vm.createContext(ctx);
vm.runInContext("var SW = {};", ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "clock.js"), "utf8"), ctx, { filename: "clock.js" });
const C = vm.runInContext("SW.clock", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

// Hand sums. Client times in ms. Sample A: sent 1000, back 1400 (rtt 400), server said 61200.
// Middle of the trip is client 1200, so this device is (1200 - 61200)/1000 = -60 s behind the server.
// Sample B has the shortest trip: sent 5000, back 5040 (rtt 40), server said 65030; middle 5020 -> (5020 - 65030)/1000 = -60.01 s.
eq(C.estimate([{ sent: 1000, back: 1400, server: 61200 }, { sent: 5000, back: 5040, server: 65030 }]), { rttMs: 40, diffS: -60.01 }, "smallest round trip wins");
eq(C.estimate([{ sent: 0, back: 100, server: 10050 }]), { rttMs: 100, diffS: -10 }, "single sample: (50 - 10050) / 1000");
eq(C.estimate([{ sent: 0, back: 100, server: 3600050 }, { sent: 0, back: 100, server: 3600050 }]).diffS, -3600, "ties keep the first");
eq(C.estimate([]), null, "no samples");
eq(C.estimate(null), null, "null samples");
eq(C.estimate([{ sent: 10, back: 5, server: 1 }]), null, "negative round trip ignored");
eq(C.estimate([{ sent: 0, back: 6000, server: 1 }]), null, "round trip over 5 s ignored");
eq(C.estimate([{ sent: "a", back: 5, server: 1 }, { sent: 0, back: NaN, server: 1 }, null]), null, "bad samples ignored");
eq(C.estimate([{ sent: 0, back: 6000, server: 1 }, { sent: 0, back: 200, server: 100 }]).rttMs, 200, "a bad sample does not hide a good one");
// this device ahead of the Pi: server 1 s behind
eq(C.estimate([{ sent: 100000, back: 100000, server: 99000 }]).diffS, 1, "device ahead is positive");

// What the final request carries: send time + rtt/2.
eq(C.arrivalMs(1000000, 40), 1000020, "arrival = send + rtt/2");
eq(C.arrivalMs(1000000, 41), 1000021, "half a millisecond rounds up");
eq(C.body(1000000, 40, false), { epoch_ms: 1000020, rtt_ms: 40, confirm_jump: false }, "body");
eq(C.body(1000000, 40.4, true), { epoch_ms: 1000020, rtt_ms: 40, confirm_jump: true }, "body rounds the rtt");
eq(C.body(1000000, 9000, true).rtt_ms, 5000, "rtt capped at 5000");
eq(Object.keys(C.body(1, 1, true)).sort(), ["confirm_jump", "epoch_ms", "rtt_ms"], "only the three fields");

// Verdicts at the edges.
eq(C.verdict(0.99), "same", "just under 1 s");
eq(C.verdict(-0.99), "same", "just under 1 s, negative");
eq(C.verdict(1), "ok", "exactly 1 s");
eq(C.verdict(5), "ok", "exactly 5 s needs no confirmation");
eq(C.verdict(5.01), "confirm", "over 5 s");
eq(C.verdict(-7200), "confirm", "back two hours");
eq(C.verdict(NaN), "unknown", "NaN");

// Words.
eq(C.sizeWords(11520), "3 h 12 min", "3 h 12 min");
eq(C.sizeWords(-11520), "3 h 12 min", "size is positive");
eq(C.sizeWords(40), "40 s", "seconds");
eq(C.sizeWords(245), "4 min 05 s", "minutes");
eq(C.sizeWords(90000), "1 d 1 h", "days");
eq(C.confirmText(11520), "The Pi's clock will jump forward by 3 h 12 min. Charts and today's history are stored by time, so do this before the show, not during it.", "confirm forward");
eq(C.confirmText(-11520).slice(0, 38), "The Pi's clock will jump back by 3 h 1", "confirm back");
eq(C.differenceText(0.2), "Within a second", "same text");
eq(C.differenceText(-3), "3 s ahead of this device", "ahead text");
eq(C.differenceText(3), "3 s behind this device", "behind text");
eq(C.differenceText(NaN), "—", "unknown text");
eq(C.ntpText(true).slice(0, 3), "Yes", "ntp yes");
eq(C.ntpText(false).slice(0, 2), "No", "ntp no");
eq(C.ntpText(null), "Not known", "ntp unknown");

console.log(`${count} checks, ${fails} failed`);
process.exit(fails ? 1 : 0);
