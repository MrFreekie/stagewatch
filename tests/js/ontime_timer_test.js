// Node test for the Ontime Timer card's own logic (SW.ot in ontimetimer.js): the countdown maths,
// m:ss formatting, the warning steps (Ontime's own times, the site's minutes as a fallback and the
// "step shorter than the timer" rule), every playback state, stale and offline, tick timing, and
// the card's DOM updated in place.
//
// The messages below are SYNTHETIC: built by hand to cover states a real Ontime has not yet been
// seen to send (pause, armed, stop, overtime, null current). Only "roll" has been captured from a
// real Ontime 4.14.0 (tests/fixtures/ontime/).
// Run: node tests/js/ontime_timer_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");

class FakeEl {
  constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this._text = ""; this.writes = 0; this.style = {}; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get className() { return this.attrs.class || ""; }
  set className(v) { this.attrs.class = String(v); this.writes++; }
  addEventListener() {}
  append(...nodes) { for (const n of nodes) { if (n instanceof FakeEl) this.children.push(n); else this._text += String(n); } }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); this.writes++; }
}
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, isFinite, window: {}, Node: FakeEl, Element: FakeEl };
ctx.document = { createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (s) => String(s) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "ontimetimer.js"), "utf8"), ctx, { filename: "ontimetimer.js" });
const SW = vm.runInContext("SW", ctx);
const ot = SW.ot;

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

const NOW = 1790000000;
const S = 1000;
// A synthetic "ontime_timer" message: a 30 minute count-down, running, 10 minutes left, received at NOW.
const msg = (o) => Object.assign({ status: "ok", label: "Ontime", received_at: NOW, playback: "play", phase: "default",
  current_ms: 600 * S, duration_ms: 1800 * S, elapsed_ms: 1200 * S, added_ms: 0, finish_in_ms: 600 * S, has_event: true,
  title: "Support act", timer_type: "count-down", warn_ms: 120 * S, danger_ms: 60 * S }, o || {});

// ---- m:ss
eq([0, 1, 59, 60, 61, 599, 3599, 3600, 3661, 36000].map(ot.fmtClock),
  ["0:00", "0:01", "0:59", "1:00", "1:01", "9:59", "59:59", "1:00:00", "1:01:01", "10:00:00"], "m:ss and h:mm:ss");
eq([-1, -59, -60, -3600].map(ot.fmtClock), ["-0:01", "-0:59", "-1:00", "-1:00:00"], "overtime shows a minus");

// ---- which whole second is shown
eq([1500, 1000, 999, 1, 0, -1, -999, -1000, -1999, -2000].map(ot.shownSeconds), [2, 1, 1, 1, 0, 0, 0, -1, -1, -2], "rounds up above zero; -0:01 from one second over");

// ---- the countdown runs on from the message, re-anchored by the next one
eq(ot.remainingMs(msg(), NOW), 600 * S, "at receipt: Ontime's value");
eq(ot.remainingMs(msg(), NOW + 2.5), 597500, "running: 2.5 s later");
eq(ot.remainingMs(msg({ playback: "roll" }), NOW + 1), 599 * S, "rolling counts down too");
eq(ot.remainingMs(msg({ playback: "pause" }), NOW + 2), 600 * S, "paused: frozen");
eq(ot.remainingMs(msg({ playback: "armed" }), NOW + 2), 600 * S, "armed: frozen");
eq(ot.remainingMs(msg({ received_at: NOW + 10, current_ms: 590 * S }), NOW + 10), 590 * S, "a new message re-anchors, no smoothing jump");
eq(ot.remainingMs(msg(), NOW + 3.5), 600 * S, "stale: frozen at the last reading");
eq(ot.remainingMs(msg({ current_ms: null }), NOW), null, "null current");
eq(ot.remainingMs(msg({ timer_type: "count-up", current_ms: 30 * S }), NOW + 2), 30 * S, "count-up is shown as received");
eq(ot.remainingMs(msg({ current_ms: -5 * S }), NOW + 2), -7 * S, "overtime keeps running");

// ---- views: running
let v = ot.view(msg(), NOW);
eq([v.state, v.digits, v.over, v.level, v.tag, v.title, v.badge, Math.round(v.barPct * 10) / 10], ["running", "10:00", false, "", "", "Support act", "▶ RUNNING", 66.7], "running");
eq(v.extra[0].indexOf("Finishes ") === 0 && v.extra[1], "Elapsed 20:00", "finish and elapsed");
eq(ot.view(msg(), NOW + 1).digits, "9:59", "one second on");
eq(ot.view(msg({ playback: "roll" }), NOW).badge, "▶ ROLLING", "rolling is labelled");
eq(ot.view(msg({ current_ms: 3599 * S }), NOW).digits, "59:59", "just under an hour");
eq(ot.view(msg({ current_ms: 3600 * S, duration_ms: 7200 * S }), NOW).digits, "1:00:00", "an hour");

// ---- Ontime's own warning and danger times
v = ot.view(msg({ current_ms: 120 * S }), NOW);
eq([v.level, v.tag], ["warn", "UNDER 2 MIN"], "at Ontime's warning time: amber with words");
v = ot.view(msg({ current_ms: 60 * S }), NOW);
eq([v.level, v.tag], ["alert", "UNDER 1 MIN"], "at Ontime's danger time: orange with words");
v = ot.view(msg({ current_ms: 121 * S }), NOW);
eq(v.level, "", "just above the warning time: no level");
v = ot.view(msg({ current_ms: 40 * S, warn_ms: 90 * S, danger_ms: 45 * S }), NOW);
eq([v.level, v.tag], ["alert", "UNDER 45 S"], "seconds wording for odd times");
v = ot.view(msg({ current_ms: 100 * S, duration_ms: 90 * S, warn_ms: 120 * S, danger_ms: 60 * S }), NOW);
eq(v.level, "", "a warning time longer than the whole timer never applies");
v = ot.view(msg({ current_ms: 30 * S, duration_ms: 90 * S, warn_ms: 120 * S, danger_ms: 60 * S }), NOW);
eq([v.level, v.tag], ["alert", "UNDER 1 MIN"], "but the shorter danger time does");
v = ot.view(msg({ current_ms: 100 * S, warn_ms: null, danger_ms: 60 * S }), NOW);
eq(v.level, "", "only a danger time sent: warning stays off");

// ---- the site's warning minutes when Ontime sent none, same steps as the schedule card
const fb = (r, extra) => ot.level(r, msg(Object.assign({ warn_ms: null, danger_ms: null }, extra)));
eq([fb(16 * 60 * S).level, fb(15 * 60 * S), fb(5 * 60 * S), fb(5 * 60 * S + 1).level], ["", { level: "warn", tag: "15 MIN" }, { level: "alert", tag: "5 MIN" }, "warn"], "fallback 15 and 5 minutes");
eq(fb(10 * 60 * S, { duration_ms: 180 * S }), { level: "", tag: "" }, "a 3 minute timer never shows the 5 and 15 minute steps");
eq(fb(10 * 60 * S, { duration_ms: 600 * S }), { level: "", tag: "" }, "a 10 minute timer drops the 15 minute step (it is longer than the timer)");
eq(fb(4 * 60 * S, { duration_ms: 600 * S }), { level: "alert", tag: "5 MIN" }, "the last step left is orange");
SW.setScheduleWarn({ minutes: [10, 2], flash: true });
eq(fb(2 * 60 * S), { level: "alert", tag: "2 MIN" }, "the site's own minutes are used");
SW.setScheduleWarn({ minutes: [15, 5], flash: false });
for (const secs of [0.5, 60, 299, 300, 301, 899, 900, 901, 5000]) {
  eq(fb(secs * S, { duration_ms: null }).level, SW.nowLevel(secs), `same level as SW.nowLevel at ${secs} s`);
}

// ---- overtime: orange, OVER, a triangle, never red
v = ot.view(msg({ current_ms: -30 * S, elapsed_ms: 1830 * S, finish_in_ms: null }), NOW);
eq([v.digits, v.over, v.level, v.tag, v.barPct], ["-0:30", true, "alert", "▲ OVER", 100], "30 s over");
eq(v.extra[0], "Over by 0:30", "over by line");
v = ot.view(msg({ current_ms: -1 * S }), NOW);
eq([v.digits, v.over, v.tag], ["-0:01", true, "▲ OVER"], "1 s over: -0:01");
v = ot.view(msg({ current_ms: -500 }), NOW);
eq([v.digits, v.over, v.level], ["0:00", false, "alert"], "less than 1 s over: 0:00, still orange");
v = ot.view(msg({ current_ms: 2 * S }), NOW + 3.5);
eq([v.state, v.digits], ["stale", "0:02"], "stale before zero stays frozen");
v = ot.view(msg({ current_ms: 2 * S, received_at: NOW + 1 }), NOW + 4);
eq([v.digits, v.over], ["-0:01", true], "the countdown runs through zero into overtime");

// ---- the other playback states (synthetic)
v = ot.view(msg({ playback: "pause", current_ms: 195 * S, finish_in_ms: null }), NOW + 2);
eq([v.state, v.digits, v.badge, v.level, v.tag, v.extra.some((x) => x.indexOf("Finishes") === 0)], ["paused", "3:15", "⏸ PAUSED", "", "", false], "paused: frozen, no finish time");
v = ot.view(msg({ playback: "pause", current_ms: 50 * S, finish_in_ms: null }), NOW);
eq([v.level, v.tag], ["alert", "UNDER 1 MIN"], "paused inside the danger step keeps its words");
v = ot.view(msg({ playback: "armed", current_ms: 1800 * S, elapsed_ms: 0, finish_in_ms: null }), NOW);
eq([v.state, v.digits, v.badge, v.barPct, v.level], ["ready", "30:00", "● READY", 0, ""], "armed: the loaded duration, empty bar");
v = ot.view(msg({ playback: "armed", current_ms: null, elapsed_ms: null, finish_in_ms: null }), NOW);
eq([v.digits, v.barPct], ["30:00", 0], "armed with null current shows the loaded duration");
v = ot.view(msg({ playback: "stop", current_ms: null, elapsed_ms: null, finish_in_ms: null }), NOW);
eq([v.state, v.digits, v.badge, v.title, v.barPct, v.level], ["stopped", "--:--", "■ STOPPED", "Support act", 0, ""], "stopped, null current");
v = ot.view(msg({ playback: "stop", current_ms: null, has_event: false, title: "", duration_ms: null }), NOW);
eq([v.title, v.barPct], ["No event loaded", null], "stopped with nothing loaded");
v = ot.view(msg({ playback: "stop", current_ms: null, title: "" }), NOW);
eq(v.title, "", "title hidden by the admin: nothing shown (the server sends it empty)");
v = ot.view(msg({ playback: "unknown", current_ms: 100 * S }), NOW + 2);
eq([v.state, v.digits, v.badge, v.note.indexOf("?") === 0], ["unknown", "1:40", "? UNKNOWN STATE", true], "an unknown state is neutral and frozen");
v = ot.view(msg({ playback: "play", current_ms: null }), NOW);
eq([v.digits, v.level], ["--:--", ""], "running with null current");
v = ot.view(msg({ current_ms: -5 * 3600 * S, playback: "pause" }), NOW);
eq(v.digits, "-5:00:00", "a large negative current");

// ---- added time, other timer types
v = ot.view(msg({ added_ms: 120 * S }), NOW);
eq(v.added, "+2:00 added", "added time");
eq(ot.view(msg({ added_ms: -60 * S }), NOW).added, "-1:00 removed", "removed time");
eq(ot.view(msg({ added_ms: 0 }), NOW).added, "", "no added time: nothing shown");
v = ot.view(msg({ timer_type: "count-up", current_ms: 90 * S, finish_in_ms: null }), NOW + 1);
eq([v.digits, v.typeNote, v.level, v.over, v.barPct], ["1:30", "Counting up", "", false, null], "count-up: the number as received, honestly labelled");
eq(ot.view(msg({ timer_type: "clock" }), NOW).typeNote, "Clock", "clock label");
eq(ot.view(msg({ timer_type: "none" }), NOW).typeNote, "No timer", "no timer label");

// ---- stale and offline: never an old countdown shown as live
v = ot.view(msg(), NOW + 20);
eq([v.state, v.digits, v.badge, v.level, v.cls], ["stale", "10:00", "▲ STALE", "", "ot-s-stale"], "stale: frozen at the last reading");
eq(v.note, "▲ STALE: nothing from Ontime for 20 s. Don't trust this time.", "stale note");
eq(ot.view(msg({ playback: "pause" }), NOW + 20).state, "stale", "a paused timer that stops arriving is stale too");
v = ot.view(msg({ status: "offline", playback: null, current_ms: null }), NOW);
eq([v.state, v.digits, v.badge, v.note], ["off", "--:--", "▲ OFFLINE", "▲ Ontime offline: no timer to show"], "offline");
v = ot.view(msg({ status: "error", playback: null, current_ms: null }), NOW);
eq([v.state, v.digits, v.note], ["error", "--:--", "▲ Ontime sent a timer we can't read"], "unreadable timer");
eq(ot.view(null, NOW).state, "off", "no message at all is offline, not a crash");
// states are never colour alone: every state has a badge with words
for (const pb of ["play", "roll", "pause", "armed", "stop", "unknown"]) {
  eq(/[A-Z]{4,}/.test(ot.view(msg({ playback: pb }), NOW).badge), true, `${pb} has words`);
}

// ---- tick timing
eq(ot.nextDelayMs(msg({ current_ms: 10500 }), NOW), 510, "10.5 s left: next change in 0.5 s");
eq(ot.nextDelayMs(msg({ current_ms: 10000 }), NOW), 1010, "on a whole second: a full second");
eq(ot.nextDelayMs(msg({ current_ms: -500 }), NOW), 510, "overtime: next change when -1 s is reached");
eq(ot.nextDelayMs(msg({ playback: "pause" }), NOW), 1000, "paused: a slow check for staleness");
eq(ot.nextDelayMs(msg({ status: "offline" }), NOW), 1000, "offline: a slow check");
eq(ot.nextDelayMs(msg({ current_ms: 10500 }), NOW + 2.9), 130, "never sleeps past the stale moment");
eq(ot.nextDelayMs(null, NOW), 1000, "no message");
for (let i = 0; i < 40; i++) {
  const d = ot.nextDelayMs(msg({ current_ms: 600 * S + i * 37 }), NOW + i * 0.07);
  if (!(d >= 30 && d <= 1100)) eq(d, "30..1100", "delay in range");
}

// ---- the card's DOM
const ui = ot.createUi();
const byClass = (cls) => { const out = []; (function walk(e) { if (e instanceof FakeEl) { if ((e.attrs.class || "").split(" ").indexOf(cls) >= 0) out.push(e); e.children.forEach(walk); } })({ children: ui.nodes, attrs: {} }); return out[0]; };
ui.update(ot.view(msg(), NOW));
eq([ui.count.textContent, ui.title.textContent, ui.badge.textContent, ui.note.hidden, ui.tag.hidden], ["10:00", "Support act", "▶ RUNNING", true, true], "first draw");
const w0 = [ui.count, ui.title, ui.badge, ui.tag, ui.note, ui.extra, ui.bar.firstChild].reduce((a, e) => a + e.writes, 0);
ui.update(ot.view(msg(), NOW));
eq([ui.count, ui.title, ui.badge, ui.tag, ui.note, ui.extra].reduce((a, e) => a + e.writes, 0), w0 - ui.bar.firstChild.writes, "an unchanged second writes nothing");
const c0 = ui.count.writes, t0 = ui.title.writes;
ui.update(ot.view(msg(), NOW + 1));
eq([ui.count.textContent, ui.count.writes - c0, ui.title.writes - t0], ["9:59", 1, 0], "one second on: only the number is written");
ui.update(ot.view(msg({ current_ms: -2 * S }), NOW));
eq([ui.count.textContent, ui.tag.textContent, ui.tag.hidden, ui.tag.className, ui.bar.className], ["-0:02", "▲ OVER", false, "sched-tag lvl-alert", "sched-bar lvl-alert"], "overtime: words, triangle, orange classes");
SW.setScheduleWarn({ minutes: [15, 5], flash: true });
ui.update(ot.view(msg({ current_ms: -2 * S }), NOW));
eq(ui.tag.className, "sched-tag lvl-alert flash", "the optional flash follows the site setting");
SW.setScheduleWarn({ minutes: [15, 5], flash: false });
ui.update(ot.view(msg({ playback: "pause", added_ms: 60 * S, current_ms: 50 * S }), NOW));
eq([ui.added.textContent, ui.added.hidden, ui.badge.textContent], ["+1:00 added", false, "⏸ PAUSED"], "added time and paused");
ui.update(ot.view(msg({ status: "offline", playback: null, current_ms: null }), NOW));
eq([ui.count.textContent, ui.note.hidden, ui.bar.hidden, ui.note.className], ["--:--", false, true, "ot-note warn"], "offline: dashes, a warning note, no bar");
ui.update(ot.view(msg({ playback: "stop", current_ms: null, has_event: false, title: "" }), NOW));
eq([ui.title.textContent, ui.title.hidden], ["No event loaded", false], "nothing loaded");
ui.update(ot.view(msg({ title: "" }), NOW));
eq(ui.title.hidden, true, "no title: the line is hidden");
// text is set with textContent only: markup in a title stays text
ui.update(ot.view(msg({ title: "<img src=x onerror=alert(1)>" }), NOW));
eq([ui.title.textContent, ui.title.children.length], ["<img src=x onerror=alert(1)>", 0], "a title is text, never markup");

console.log(`${count} checks, ${fails} failed`);
process.exit(fails ? 1 : 0);
