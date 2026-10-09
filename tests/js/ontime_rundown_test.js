// Node test for the Ontime Rundown card's own logic (SW.rd in ontimerundown.js): the on-time band,
// ahead/behind wording with both offset signs, the times of day (midnight wrap), the day progress
// bar, "Event N of M" with both index bases, every state, stale and offline, and the card's DOM
// updated in place.
//
// The messages below are SYNTHETIC: built by hand. Only one state (running event 9 of 16, offset 0,
// mode "absolute") has been captured from a real Ontime 4.14.0 (tests/fixtures/ontime/). The offset
// sign, the index base, "finished" and "nothing loaded" are assumptions pinned here, one constant each.
// Run: node tests/js/ontime_rundown_test.js
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
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); this.writes++; }
  append(...nodes) { for (const n of nodes) { if (n instanceof FakeEl) this.children.push(n); else this._text += String(n); } }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); this.writes++; }
}
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, isFinite, window: {}, Node: FakeEl, Element: FakeEl };
ctx.document = { createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (s) => String(s) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
vm.runInContext(fs.readFileSync(path.join(STATIC, "ontimerundown.js"), "utf8"), ctx, { filename: "ontimerundown.js" });
const SW = vm.runInContext("SW", ctx);
const rd = SW.rd;

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}

const NOW = 1790000000, S = 1000, MIN = 60 * S, H = 3600 * S;
const msg = (o) => Object.assign({ status: "ok", label: "Ontime", received_at: NOW, position: { index: 3, total: 12 },
  offset_ms: 0, offset_mode: "absolute", planned_start_ms: 11.5 * H, planned_end_ms: 22.5 * H, expected_end_ms: 22.5 * H,
  actual_start_ms: 11.5 * H, current_day: 0, ontime_clock_ms: 15 * H, event_title: "", event_note: "" }, o || {});

// ---- the on-time band (offset in ms, positive = ahead, negative = behind)
eq([0, 29 * S, -29 * S, 30 * S, -30 * S].map((x) => rd.band(x).kind), ["ontime", "ontime", "ontime", "ontime", "ontime"], "within 30 s is on time, edges included");
eq([30001, 60 * S].map((x) => rd.band(x)), [{ kind: "ahead", level: "" }, { kind: "ahead", level: "" }], "ahead is neutral however far");
eq([-30001, -60 * S, -5 * MIN].map((x) => rd.band(x)), Array(3).fill({ kind: "behind", level: "warn" }), "behind beyond 30 s is amber up to the orange step");
eq([-(5 * MIN + 1), -3 * H].map((x) => rd.band(x)), Array(2).fill({ kind: "behind", level: "alert" }), "beyond the orange step is orange, never red");
eq(rd.band(null).kind, "none", "no offset");
SW.setScheduleWarn({ minutes: [15, 5, 1], flash_minutes: [] });
eq([rd.band(-61 * S).level, rd.band(-59 * S).level], ["alert", "warn"], "the orange step follows the site's smallest warning minute");
SW.setScheduleWarn({ minutes: [15, 5], flash_minutes: [] });
eq(rd.band(-5 * MIN - 1).level, "alert", "default site steps");

// ---- the offset wording and the sign assumption
eq(rd.OFFSET_POSITIVE_IS_AHEAD, false, "CONFIRMED on a real Ontime 4.14.0 by the owner: positive offset = behind, negative = ahead");
eq(rd.offsetView(-250 * S), { big: "▲ 4:10", sym: "▲", word: "AHEAD", phrase: "Running 4:10 ahead", level: "" }, "negative is ahead");
eq(rd.offsetView(250 * S), { big: "▼ 4:10", sym: "▼", word: "BEHIND", phrase: "Running 4:10 behind", level: "warn" }, "positive is behind");
eq(rd.offsetView(-529221).big, "▲ 8:49", "the real capture: -529221 ms is 8:49 ahead");
eq([rd.offsetView(10 * S).big, rd.offsetView(10 * S).phrase], ["ON TIME", ""], "within the band the words say on time, once");
eq(rd.offsetView(400 * S).level, "alert", "far behind is orange");
rd.OFFSET_POSITIVE_IS_AHEAD = true;
eq([rd.offsetView(250 * S).word, rd.offsetView(-250 * S).word], ["AHEAD", "BEHIND"], "flipping the one constant flips the meaning");
rd.OFFSET_POSITIVE_IS_AHEAD = false;

// ---- offset formatting
eq([0, 29 * S, 31 * S, 59 * 60 * S + 59 * S, 3600 * S, 3661 * S].map(rd.fmtOffset), ["0:00", "0:29", "0:31", "59:59", "1:00:00", "1:01:01"], "m:ss then h:mm:ss");
eq([-90 * S].map(rd.fmtOffset), ["1:30"], "sign is dropped (the words carry it)");

// ---- times of day, midnight wrap
eq([0, 41400000, 81000000, 86340000].map(rd.fmtOntimeTime), ["00:00", "11:30", "22:30", "23:59"], "Ontime time as HH:MM");
eq([86400000, 91800000, 172800000 + 3600000].map(rd.fmtOntimeTime), ["00:00 +1 day", "01:30 +1 day", "01:00 +2 days"], "past midnight wraps with +N day");
eq([-1, 72 * H + 1, null, "x", NaN].map(rd.fmtOntimeTime), Array(5).fill("--:--"), "out of range or not a number");

// ---- day progress, including a show that runs past midnight
eq(rd.dayProgress(10 * H, 20 * H, 20 * H, 15 * H), { pct: 50, tickPct: null }, "halfway");
eq(rd.dayProgress(10 * H, 20 * H, 22 * H, 16 * H), { pct: 50, tickPct: (10 / 12) * 100 }, "expected end later: tick at the planned end");
eq(rd.dayProgress(10 * H, 20 * H, 18 * H, 15 * H).tickPct, null, "ahead: no tick");
eq(rd.dayProgress(10 * H, 20 * H, 20 * H, 9 * H).pct, 0, "before the start is 0");
eq(rd.dayProgress(10 * H, 20 * H, 20 * H, 23 * H).pct, 100, "after the end is 100");
eq(Math.round(rd.dayProgress(20 * H, 26 * H, 26 * H, 1 * H).pct), 83, "midnight wrap: 01:00 on a 20:00 to 02:00 show is 5 hours of 6 in");
eq(rd.dayProgress(20 * H, 26 * H, 26 * H, 23 * H).pct, 50, "before midnight on the same show: 3 of 6 hours");
eq(rd.dayProgress(20 * H, 20 * H, 20 * H, 21 * H), null, "empty span");
eq(rd.dayProgress(null, 20 * H, 20 * H, 21 * H), null, "no start");
eq(rd.dayProgress(10 * H, null, null, 12 * H), null, "no end");

// ---- "Event N of M" and the index-base assumption
eq(rd.INDEX_BASE, 0, "assumption: selectedEventIndex 0 is the first event");
eq([rd.positionText(0, 12), rd.positionText(3, 12), rd.positionText(11, 12)], ["Event 1 of 12", "Event 4 of 12", "Event 12 of 12"], "0-based");
eq(rd.positionText(12, 12), "", "one past the end can't be placed");
eq([rd.positionText(null, 12), rd.positionText(3, null), rd.positionText(-1, 12)], ["", "", ""], "missing or negative");
rd.INDEX_BASE = 1;
eq([rd.positionText(1, 12), rd.positionText(12, 12), rd.positionText(0, 12)], ["Event 1 of 12", "Event 12 of 12", ""], "1-based, if a capture shows it");
rd.INDEX_BASE = 0;

// ---- views
let v = rd.view(msg(), NOW);
eq([v.state, v.big, v.word, v.quiet, v.level, v.modeNote], ["running", "ON TIME", "", true, "", "vs plan"], "running, on time (quiet)");
eq("position" in v, false, "no position line any more");
eq([rd.view(msg({ offset_ms: 250 * S }), NOW).quiet, rd.view(msg({ offset_ms: -250 * S }), NOW).quiet], [false, false], "behind and ahead stay loud");
eq([v.planned, v.expected, v.started], ["Planned 11:30 to 22:30", "Expected end 22:30", "Started 11:30"], "tablet lines");
eq([Math.round(v.barPct), v.tickPct], [32, null], "day bar");
v = rd.view(msg({ offset_ms: 250 * S, expected_end_ms: 22.5 * H + 250 * S }), NOW);
eq([v.big, v.word, v.level, v.expected], ["▼ 4:10", "BEHIND", "warn", "Expected end 22:34 (planned 22:30)"], "behind");
eq(v.tickPct !== null, true, "tick shown when expected end is later");
eq(rd.view(msg({ offset_ms: 400 * S }), NOW).level, "alert", "orange");
eq(rd.view(msg({ offset_ms: -90 * S }), NOW).big, "▲ 1:30", "ahead");
eq(rd.view(msg({ offset_ms: 70 * S, offset_mode: "relative" }), NOW).modeNote, "since start", "relative mode is labelled");
eq(rd.view(msg({ offset_mode: "unknown" }), NOW).modeNote, "", "unknown mode: no label");
eq(rd.view(msg({ offset_ms: null, offset_mode: null }), NOW).phrase, "Ahead or behind not sent", "no offset: nothing guessed");
eq(rd.view(msg({ expected_end_ms: null }), NOW).expected, "", "no expected end: nothing shown, the planned end is not substituted");
eq(rd.view(msg({ actual_start_ms: null }), NOW).started, "", "no actual start");
eq(rd.view(msg({ position: { index: 12, total: 12 } }), NOW).state, "unplaced", "index outside the rundown");
eq(rd.view(msg({ position: { index: 12, total: 12 } }), NOW).note.indexOf("Can't place") === 0, true, "and says so");
eq(rd.view(msg({ current_day: 1 }), NOW).day.indexOf("later day") > 0, true, "a later day is marked, the number is not shown");
eq(rd.view(msg({ current_day: 0 }), NOW).day, "", "day 0: nothing");

// ---- not started, finished, nothing loaded, no data
v = rd.view(msg({ position: { index: null, total: 12 }, actual_start_ms: null, offset_ms: null }), NOW);
eq([v.state, v.big, v.planned, v.quiet, v.barPct], ["notstarted", "NOT STARTED", "Planned 11:30 to 22:30", false, null], "not started");
v = rd.view(msg({ position: { index: null, total: 12 } }), NOW);
eq([v.state, v.big, v.phrase, v.planned, v.started], ["finished", "FINISHED", "", "Planned end 22:30", "Started 11:30"], "finished (assumed: started, nothing selected)");
v = rd.view(msg({ position: { index: null, total: 0 }, planned_start_ms: null, planned_end_ms: null }), NOW);
eq([v.state, v.note], ["empty", "Ontime has no rundown loaded"], "no rundown");
v = rd.view(msg({ position: null, offset_ms: null }), NOW);
eq([v.state, v.big, v.note], ["nodata", "--", "Ontime has not sent rundown information yet"], "no data yet is not zero");

// ---- offline, error, stale
v = rd.view({ status: "offline", label: "Ontime" }, NOW);
eq([v.state, v.big, v.badge, v.note], ["off", "--", "▲ OFFLINE", "▲ Ontime offline: no rundown to show"], "offline: a gap, not a zero");
eq(rd.view(null, NOW).state, "off", "no message");
v = rd.view({ status: "error", label: "Ontime" }, NOW);
eq([v.state, v.badge], ["error", "▲ CAN'T READ"], "unreadable");
v = rd.view(msg({ offset_ms: 250 * S }), NOW + 3);
eq(v.state, "running", "3 s old is still live");
v = rd.view(msg({ offset_ms: 250 * S }), NOW + 12);
eq([v.state, v.stale, v.badge, v.level, v.big, v.note], ["stale", true, "", "", "▼ 4:10", "▲ STALE: nothing from Ontime for 10 s. Don't trust these figures."], "stale: struck figures, said once in words with a triangle, no colour step");
v = rd.view(msg({ offset_ms: 250 * S, unreadable: true }), NOW);
eq([v.state, v.stale, v.level, v.big, v.badge], ["unreadable", true, "", "▼ 4:10", ""], "unreadable block: last figures kept, struck through, no colour step");
eq(v.note.indexOf("▲ CAN'T READ") === 0, true, "and says so in words");
eq(rd.view(msg({ offset_ms: 250 * S, unreadable: false }), NOW).state, "running", "a readable block clears it");
eq([rd.view(msg({ offset_ms: 250 * S }), NOW).phraseDup, rd.view(msg({ offset_ms: null }), NOW).phraseDup], [true, false], "the phrase that repeats the figure is marked for the wall");

// ---- the DOM is updated in place
const ui = rd.createUi();
ui.update(rd.view(msg({ offset_ms: 250 * S, expected_end_ms: 22.5 * H + 250 * S }), NOW));
eq([ui.big.textContent, ui.word.textContent, ui.big.className, ui.bar.hidden, ui.tick.hidden], ["▼ 4:10", "BEHIND", "rd-big", false, false], "card content");
const uiq = rd.createUi();
uiq.update(rd.view(msg(), NOW));
eq([uiq.big.textContent, uiq.big.className], ["ON TIME", "rd-big quiet"], "on time is the quiet class");
const before = ui.big.writes;
ui.update(rd.view(msg({ offset_ms: 250 * S, expected_end_ms: 22.5 * H + 250 * S }), NOW));
eq(ui.big.writes, before, "nothing rewritten when nothing changed");
ui.update(rd.view({ status: "offline", label: "Ontime" }, NOW));
eq([ui.big.textContent, ui.bar.hidden, ui.note.hidden, ui.word.hidden], ["--", true, false, true], "offline hides the rest");
eq("position" in ui, false, "the card has no position element");
const ui2 = rd.createUi();
ui2.update(rd.view(msg({ label: "<img src=x onerror=alert(1)>", status: "offline" }), NOW));
eq([ui2.note.textContent.indexOf("<img") > 0, ui2.note.children.length], [true, 0], "a label is text, never markup");

// ---- event title and note (SYNTHETIC)
v = rd.view(msg({ event_title: "Support act", event_note: "Check IEMs before the changeover." }), NOW);
eq([v.title, v.evnote], ["Support act", "Check IEMs before the changeover."], "title and note");
eq([rd.view(msg(), NOW).title, rd.view(msg(), NOW).evnote], ["", ""], "empty: nothing");
eq(rd.view(msg({ event_title: 5, event_note: null }), NOW).title, "", "wrong types show nothing");
v = rd.view(msg({ event_title: "Support act", event_note: "n", unreadable: true }), NOW);
eq([v.title, v.stale], ["Support act", true], "last good title stays, marked with the rest of the card");
const ui3 = rd.createUi();
ui3.update(rd.view(msg({ event_title: "<b>x</b>", event_note: "<img src=x onerror=alert(1)>" }), NOW));
eq([ui3.title.textContent, ui3.title.children.length, ui3.evnote.textContent, ui3.evnote.children.length, ui3.title.hidden, ui3.evnote.hidden],
  ["<b>x</b>", 0, "<img src=x onerror=alert(1)>", 0, false, false], "title and note are text, never markup");
ui3.update(rd.view(msg(), NOW));
eq([ui3.title.hidden, ui3.evnote.hidden], [true, true], "hidden again when empty");
eq(rd.view(msg({ expected_end_ms: -4729221 }), NOW).expected, "", "a negative expected end is not shown as a time");

// ---- the event list (SYNTHETIC rows; titles are made up)
const row = (i, state, o) => Object.assign({ cue: String(i + 1), title: `Act ${i + 1}`, start: 41400000 + i * 3000000, end: 44400000 + i * 3000000, state }, o || {});
const mk = (n, cur) => Array.from({ length: n }, (_, i) => row(i, cur === null ? (i === 0 ? "next" : "later") : i < cur ? "past" : i === cur ? "current" : i === cur + 1 ? "next" : "later"));
const ev = mk(20, 6);
eq(rd.listRows(ev, "tablet").map((r) => r.title), ["Act 5", "Act 6", "Act 7", "Act 8", "Act 9", "Act 10", "Act 11", "Act 12", "Act 13", "Act 14", "Act 15"], "tablet: 2 past, current, 8 ahead");
eq(rd.listRows(ev, "phone").map((r) => r.title), ["Act 7", "Act 8", "Act 9", "Act 10", "Act 11"], "phone: current and 4 ahead");
eq(rd.listRows(ev, "wall").length, 11, "wall: current and 10 ahead");
eq(rd.listRows(ev, "elsewhere").length, 11, "an unknown layout is treated as a tablet");
eq(rd.listRows(mk(3, 0), "phone").map((r) => r.state), ["current", "next", "later"], "short list: only what there is");
let r0 = rd.listRows(ev, "tablet").find((r) => r.state === "current");
eq([r0.mark, r0.time, r0.cue], ["▶", "16:30-17:20", "7"], "current row: marker, Ontime time with end on a tablet");
eq(rd.listRows(ev, "phone").find((r) => r.state === "current").time, "16:30", "phone: start only");
eq(rd.listRows(ev, "wall").find((r) => r.state === "current").time, "16:30-17:20", "wall: with the end");
eq(rd.listRows(mk(5, null), "phone").map((r) => r.title), ["Act 1", "Act 2", "Act 3", "Act 4", "Act 5"], "before the start: the first rows");
eq(rd.listRows(ev.map((e) => Object.assign({}, e, { state: "past" })), "tablet").map((r) => r.title), ["Act 18", "Act 19", "Act 20"], "finished: the last few rows");
eq(rd.listRows([row(0, "skipped")], "tablet")[0], { state: "skipped", mark: "⊘", time: "11:30-12:20", cue: "1", title: "Act 1", tag: "SKIPPED" }, "skipped rows are marked and labelled");
eq(rd.listRows([row(0, "next", { start: null, end: null })], "tablet")[0].time, "--:--", "unavailable time");
eq(rd.listRows([row(0, "next", { start: 86400000 + 3600000 })], "phone")[0].time, "01:00", "past midnight wraps");
eq(rd.listRows([row(0, "weird"), null, 5, { state: "next", title: 5, cue: null }], "tablet").map((r) => [r.state, r.title, r.cue]), [["later", "Act 1", "1"], ["next", "Event", ""]], "wrong types are tolerated");
eq([rd.listRows(null, "tablet"), rd.listRows([], "tablet"), rd.listRows("x", "tablet")], [[], [], []], "no list: no rows");
eq(rd.MARKS.current !== rd.MARKS.later && rd.MARKS.skipped !== rd.MARKS.past, true, "every state has its own marker, not colour alone");

v = rd.view(msg({ events: ev, events_stale: false }), NOW, "wall");
eq([v.rows.length, v.listNote], [11, ""], "view carries the rows for its layout");
v = rd.view(msg({ events: ev, events_stale: true }), NOW, "tablet");
eq(v.listNote, "▲ The event list may be out of date.", "a stale list says so");
eq(rd.view(msg({ events: ev }), NOW + 12, "tablet").stale, true, "the card goes stale with its list");
eq(rd.view(msg({ events: ev, position: { index: null, total: 0 } }), NOW, "tablet").rows.length, 0, "no rundown loaded: no list");
eq(rd.view(msg(), NOW).rows, [], "no list sent: nothing");

const ui4 = rd.createUi();
ui4.update(rd.view(msg({ events: ev }), NOW, "tablet"));
eq([ui4.list.hidden, ui4.list.children.length], [false, 11], "rows are built");
eq(ui4.list.children[2].className, "rd-ev rd-ev-current", "the current row has its own class");
const w0 = ui4.list.writes;
ui4.update(rd.view(msg({ events: ev }), NOW + 1, "tablet"));
eq(ui4.list.writes, w0, "list not rebuilt when nothing changed");
ui4.update(rd.view(msg({ events: [row(0, "current", { title: "<img src=x onerror=alert(1)>" })] }), NOW, "tablet"));
eq([ui4.list.children.length, ui4.list.textContent.indexOf("<img src=x") >= 0, ui4.list.children[0].children.length], [1, true, 4], "titles are text, never markup");
ui4.update(rd.view(msg(), NOW, "tablet"));
eq(ui4.list.hidden, true, "hidden again with no rows");

// ---- review fixes
eq(rd.listRows([row(0, "next", { title: "" })], "tablet")[0].title, "Event", "a blank title (the admin switch) shows a neutral label");
eq(rd.listRows([row(0, "toString"), row(1, "constructor"), row(2, "__proto__")], "tablet").map((r) => r.state), ["later", "later", "later"], "a server string never indexes a table");
eq(rd.listRows(ev, "constructor").length, 11, "an inherited layout name is a tablet");
eq(rd.view(msg({ offset_mode: "constructor" }), NOW).modeNote, "", "an inherited mode name gives no label");
eq(rd.view(msg({ offset_mode: "toString" }), NOW).modeNote, "", "likewise");
v = rd.view(msg({ events: [], events_unplaced: true }), NOW, "tablet");
eq([v.rows.length, v.listNote], [0, "▲ Can't place the current event in the event list."], "unplaceable list is said, not guessed");
v = rd.view(msg({ event_title: "Act", event_text_unreadable: true }), NOW);
eq([v.textStale, v.stale, v.state], [true, false, "running"], "only the text is marked");
const ui5 = rd.createUi();
ui5.update(rd.view(msg({ event_title: "Act", event_text_unreadable: true }), NOW));
eq(ui5.title.className, "rd-title stale", "the title is marked");
ui5.update(rd.view(msg({ event_title: "Act" }), NOW));
eq(ui5.title.className, "rd-title", "and unmarked");

console.log(`${count} checks, ${fails} failed`);
process.exit(fails ? 1 : 0);
