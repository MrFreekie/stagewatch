// Node test for the dashboard's own NOW / NEXT maths (SW.scheduleNowNext and friends in
// common.js). It must agree with core/schedule.py now_next: the same edge cases as
// tests/test_schedule.py are checked here, and tests/test_schedule_ui.py also passes a file of
// Python results (node schedule_test.js <cases.json>) to compare every case one by one.
// Run: node tests/js/schedule_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, console, window: {}, document: {} };
ctx.Element = function () {};
ctx.Element.prototype = { replaceChildren() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
const SW = vm.runInContext("SW", ctx);

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
const ids = (nn) => [nn.state, nn.current ? nn.current.id : null, nn.next ? nn.next.id : null, nn.curfew ? nn.curfew.id : null];

// ---- the same day as tests/test_schedule.py (DAY_ITEMS)
const T = 1790000000, M = 60;
const item = (id, title, kind, start, end, stage) => ({ id, title, kind, stage: stage || "", planned_start: start,
  planned_end: end === undefined ? null : end, setlist: "" });
const DAY = [
  item(1, "Doors", "doors", T),
  item(2, "Support", "act", T + 60 * M, T + 105 * M, "Main"),
  item(3, "Second stage act", "act", T + 60 * M, T + 120 * M, "Second"),
  item(4, "Changeover", "changeover", T + 105 * M, T + 135 * M, "Main"),
  item(5, "Headliner", "act", T + 135 * M, T + 225 * M, "Main"),
  item(6, "Curfew", "curfew", T + 270 * M),
  item(7, "Second stage curfew", "curfew", T + 300 * M, null, "Second"),
];
const CASES = [
  [-30, "", "before", null, 1, 6],
  [0, "", "running", 1, 2, 6],
  [30, "Main", "running", 1, 2, 6],
  [70, "Main", "running", 2, 4, 6],
  [70, "second", "running", 3, null, 6],   // stage match ignores case
  [110, "Main", "running", 4, 5, 6],
  [110, "Second", "running", 3, null, 6],
  [125, "Second", "between", null, null, 6],
  [240, "Main", "between", null, null, 6],  // after the last act, before curfew
  [280, "Main", "over", null, null, 6],     // past curfew: show over, the passed curfew kept
  [280, "Second", "between", null, null, 7], // the Second stage's own curfew is still to come
];
for (const [minute, stage, state, cur, nxt, cf] of CASES) {
  eq(ids(SW.scheduleNowNext(DAY, T + minute * M, stage)), [state, cur, nxt, cf], `now_next at ${minute} min, stage "${stage}"`);
}
eq(SW.scheduleNowNext(DAY, T + 280 * M, "Second").secondsToCurfew, 20 * M, "Second curfew in 20 min");
eq(SW.scheduleNowNext(DAY, T + 255 * M, "").secondsToCurfew, 15 * M, "curfew countdown");
const after = SW.scheduleNowNext(DAY, T + 400 * M, "");
eq([after.state, after.curfew.id, after.secondsToCurfew], ["over", 7, -100 * M], "after every curfew");

// ---- edges
eq(SW.scheduleNowNext([], T, "").state, "empty", "no items");
eq(SW.scheduleNowNext(null, T, "").state, "empty", "no list at all");
let nn = SW.scheduleNowNext(DAY, T + 105 * M, "Main");
eq([nn.current.id, nn.next.id], [4, 5], "start <= now < end: the boundary belongs to the next item");
const openEnd = [item(1, "Late act", "act", T)];
eq(SW.scheduleNowNext(openEnd, T + 600 * M, "").current.id, 1, "an open-ended last item without a curfew stays current");
eq(SW.scheduleNowNext(openEnd, T + 600 * M, "").currentEnd, null, "...with no end to count down to");
const withCurfew = openEnd.concat([item(2, "Curfew", "curfew", T + 60 * M)]);
eq(SW.scheduleNowNext(withCurfew, T + 61 * M, "").current, null, "...but never past a curfew");
eq(SW.scheduleNowNext(withCurfew, T + 30 * M, "").currentEnd, T + 60 * M, "an open-ended item runs until the curfew");
eq(SW.scheduleNowNext(withCurfew, T + 60 * M, "").state, "over", "at the curfew minute itself the show is over");
eq(SW.scheduleNowNext([item(1, "Curfew", "curfew", T)], T - 60, "").current, null, "curfews are never current");
eq(SW.scheduleNowNext([item(1, "Curfew", "curfew", T)], T - 60, "").state, "before", "only a curfew, before it");
// overlapping items: the latest start wins, and the earlier one comes back once the later ends
const overlap = [item(1, "Long set", "act", T, T + 120 * M), item(2, "Guest spot", "act", T + 30 * M, T + 45 * M)];
eq(SW.scheduleNowNext(overlap, T + 35 * M, "").current.id, 2, "overlap: the latest start wins");
eq(SW.scheduleNowNext(overlap, T + 50 * M, "").current.id, 1, "overlap: back to the long set");
// equal starts keep the server's order (the later one in the list wins, as in Python's sort)
const same = [item(9, "First listed", "act", T, T + 60 * M), item(3, "Second listed", "act", T, T + 60 * M)];
eq(SW.scheduleNowNext(same, T + 1, "").current.id, 3, "equal starts: the later listed wins");
// an open-ended item runs until the next later start, not one at the same minute
const open2 = [item(1, "Doors", "doors", T), item(2, "Bar opens", "other", T), item(3, "Act", "act", T + 30 * M, T + 60 * M)];
nn = SW.scheduleNowNext(open2, T + 10 * M, "");
eq([nn.current.id, nn.currentEnd], [2, T + 30 * M], "open-ended items end at the next later start");
// items arrive in any order
eq(ids(SW.scheduleNowNext(DAY.slice().reverse(), T + 70 * M, "Main")), ["running", 2, 4, 6], "order of the list doesn't matter");

// ---- stage filter
eq(SW.stageMatches("", "Main"), true, "no stage: shows everywhere");
eq(SW.stageMatches("Main", ""), true, "dashboard with no stage: sees everything");
eq(SW.stageMatches(" main stage ", "MAIN STAGE"), true, "case and spaces ignored");
eq(SW.stageMatches("Main", "B"), false, "other stage hidden");
eq(SW.stageMatches(null, undefined), true, "missing stages");
eq(SW.scheduleOrder(DAY, "second").map((i) => i.id), [1, 3, 6, 7], "stage order: no-stage items plus that stage");

// ---- past items in the running order
const ends = SW.scheduleEnds(SW.scheduleOrder(DAY, "Main"));
eq(ends, [T + 60 * M, T + 105 * M, T + 135 * M, T + 225 * M, T + 270 * M], "item ends (doors until the next start; curfew at itself)");
eq(SW.scheduleEnds([item(1, "Late", "act", T), item(2, "Curfew", "curfew", T + 9 * M)]), [T + 9 * M, T + 9 * M], "open item ends at the curfew");
eq(SW.scheduleEnds([item(1, "Late", "act", T)]), [null], "open item without curfew never ends");

// ---- NOW colour levels (item with its own end): neutral above 15 min, amber at T-15, orange at T-5
eq(SW.nowLevel(null), "", "no countdown without an end time");
eq(SW.nowLevel(undefined), "", "undefined is no countdown");
eq(SW.nowLevel(15 * M + 1), "", "T-15:01 is neutral");
eq(SW.nowLevel(15 * M), "warn", "T-15:00 is amber");
eq(SW.nowLevel(5 * M + 1), "warn", "T-5:01 is still amber");
eq(SW.nowLevel(5 * M), "alert", "T-5:00 is orange");
eq(SW.nowLevel(1), "alert", "last second");
eq(SW.nowLevel(0), "", "at the end it is no longer current: no level");
eq(SW.nowLevel(-60), "", "never red after the end");
eq(SW.curfewLevel, undefined, "the curfew countdown levels are gone");
{
  // a curfew cutting off an open-ended act: NOW counts to the curfew only if the act has its own end
  const open = [item(1, "Act", "act", T), item(2, "Curfew", "curfew", T + 60 * M)];
  const nnOpen = SW.scheduleNowNext(open, T + 50 * M, "");
  eq([nnOpen.current.id, nnOpen.currentEnd], [1, T + 60 * M], "cut-off end is still reported");
  eq(nnOpen.current.planned_end, null, "...but the item has no end of its own, so the card shows no countdown");
  // at the cut-off moment the act is no longer current
  eq(SW.scheduleNowNext(open, T + 60 * M, "").current, null, "reaching the end moves on");
}
// ---- a Load Out after the curfew keeps running (curfew 23:00, Load Out 23:15-01:00)
{
  const c = T + 600 * M;
  const LO = [item(1, "Headliner", "act", c - 90 * M, c - 10 * M), item(2, "Curfew", "curfew", c),
    item(3, "Load Out", "load_out", c + 15 * M, c + 120 * M)];
  const st = (m) => { const r = SW.scheduleNowNext(LO, c + m * M, ""); return [r.state, r.current ? r.current.id : null, r.next ? r.next.id : null]; };
  eq(st(-5), ["between", null, 3], "before curfew, Load Out is next");
  eq(st(10), ["between", null, 3], "after curfew, before Load Out");
  eq(st(30), ["running", 3, null], "23:30 NOW Load Out");
  eq(st(90), ["running", 3, null], "00:30 NOW Load Out");
  eq(st(125), ["over", null, null], "01:05 over (the dashboard says Finished)");
    const open = [LO[0], LO[1], item(3, "Load Out", "load_out", c + 15 * M)];
  eq(SW.scheduleNowNext(open, c + 600 * M, "").state, "running", "open-ended Load Out runs on");
}

// ---- NEXT amber in its last 5 minutes
eq(SW.nextLevel(null), "", "no next");
eq(SW.nextLevel(5 * M + 1), "", "T-5:01 neutral");
eq(SW.nextLevel(5 * M), "warn", "T-5:00 amber");
eq(SW.nextLevel(1), "warn", "last second");

// ---- custom warning steps (site setting, shared by every dashboard)
SW.setScheduleWarn({ minutes: [5, 15, 1], flash_minutes: [1, 7] });
eq(SW.scheduleWarn, { minutes: [15, 5, 1], flash_minutes: [1] }, "steps sorted; flash only for listed steps");
eq([SW.nowStep(15 * M + 1), SW.nowStep(15 * M), SW.nowStep(5 * M + 1), SW.nowStep(5 * M), SW.nowStep(M + 1), SW.nowStep(M), SW.nowStep(1)],
  [0, 15, 15, 5, 5, 1, 1], "the smallest step passed is the current one");
eq([SW.nowLevel(15 * M + 1), SW.nowLevel(15 * M), SW.nowLevel(6 * M), SW.nowLevel(5 * M), SW.nowLevel(M), SW.nowLevel(0)],
  ["", "warn", "warn", "alert", "alert", ""], "first step amber, every later step orange");
eq([SW.nowFlash(15 * M), SW.nowFlash(5 * M), SW.nowFlash(M + 1), SW.nowFlash(M), SW.nowFlash(30), SW.nowFlash(0), SW.nowFlash(null)],
  [false, false, false, true, true, false, false], "flash only in a step marked with !");
eq([SW.nextLevel(M + 1), SW.nextLevel(M), SW.nextLevel(0), SW.nextStep()], ["", "warn", "", 1], "NEXT: within the smallest step only");
SW.setScheduleWarn({ minutes: [15, 5], flash_minutes: [15, 5] });
eq([SW.nowFlash(14 * M), SW.nowFlash(4 * M)], [true, true], "several steps can flash");
SW.setScheduleWarn({ minutes: [10] });
eq([SW.nowLevel(11 * M), SW.nowLevel(10 * M), SW.nowStep(9 * M), SW.nextLevel(10 * M), SW.nextLevel(10 * M + 1), SW.nowFlash(9 * M)],
  ["", "warn", 10, "warn", "", false], "a single step is the first one: amber; no flash by default");
SW.setScheduleWarn({ minutes: [] });
SW.setScheduleWarn({ minutes: [0, 5] });
SW.setScheduleWarn({ minutes: ["5"] });
SW.setScheduleWarn(null);
eq(SW.scheduleWarn.minutes, [10], "bad payloads are ignored");
SW.setScheduleWarn({ minutes: [15, 5], flash_minutes: [] });

// ---- show day and the old-schedule check, 06:00 rollover, curfew 23:00 (site UTC+1, no zone name)
SW.setSiteTime({ timezone: "", utc_offset_s: 3600, day_rollover: "06:00" });
const at = (y, mo, d, h, mi) => Date.UTC(y, mo - 1, d, h, mi) / 1000 - 3600;   // site wall clock -> epoch
const cf = at(2026, 10, 2, 23, 0);
eq(SW.siteShowDay(at(2026, 10, 3, 5, 59)), "2026-10-02", "05:59 is still the previous show day");
eq(SW.siteShowDay(at(2026, 10, 3, 6, 0)), "2026-10-03", "06:00 is the new show day");
eq(SW.siteShowDay(at(2026, 10, 2, 23, 30)), "2026-10-02", "evening is today");
eq(SW.siteShowDay(at(2026, 3, 1, 2, 0)), "2026-02-28", "month boundary");
eq(SW.scheduleIsOld("2026-10-02", at(2026, 10, 3, 5, 59)), false, "before the rollover: yesterday's day is current");
eq(SW.scheduleIsOld("2026-10-02", at(2026, 10, 3, 6, 0)), true, "at the rollover: old");
eq(SW.scheduleIsOld("2026-10-02", at(2026, 10, 3, 8, 45)), true, "08:45 the morning after");
eq(SW.scheduleIsOld("2026-10-03", at(2026, 10, 3, 8, 45)), false, "today's schedule");
eq(SW.scheduleIsOld("2026-10-04", at(2026, 10, 3, 8, 45)), false, "a future day is not old");
eq(SW.scheduleIsOld("", at(2026, 10, 3, 8, 45)), false, "no day");
SW.setSiteTime({ timezone: "", utc_offset_s: 3600, day_rollover: "04:30" });
eq(SW.siteShowDay(at(2026, 10, 3, 4, 29)), "2026-10-02", "04:30 rollover, just before");
eq(SW.siteShowDay(at(2026, 10, 3, 4, 30)), "2026-10-03", "04:30 rollover, at it");
SW.setSiteTime({ timezone: "", utc_offset_s: null, day_rollover: "06:00" });

// ---- countdown text: never "4:05" (reads like a time of day)
eq(SW.fmtDuration(0, true), "0 s", "zero");
eq(SW.fmtDuration(44.2, true), "45 s", "seconds round up when counting down");
eq(SW.fmtDuration(44.8, false), "44 s", "...and down when counting up");
eq(SW.fmtDuration(245, true), "4 min 05 s", "under 10 min shows seconds");
eq(SW.fmtDuration(599.5, true), "10 min", "10 min boundary");
eq(SW.fmtDuration(900, true), "15 min", "T-15 reads 15 min");
eq(SW.fmtDuration(900.5, true), "16 min", "just over 15 rounds up");
eq(SW.fmtDuration(3600, true), "1 h 00 min", "an hour");
eq(SW.fmtDuration(5700, true), "1 h 35 min", "1 h 35");
eq(SW.fmtDuration(-30, true), "0 s", "never negative");

// ---- the Python cross-check: [{items, now, stage, expect: {state, current_id, next_id, curfew_id, seconds_to_curfew}}]
if (process.argv[2]) {
  const cases = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  for (const c of cases) {
    const r = SW.scheduleNowNext(c.items, c.now, c.stage);
    eq([r.state, r.current ? r.current.id : null, r.next ? r.next.id : null, r.curfew ? r.curfew.id : null, r.secondsToCurfew],
      [c.expect.state, c.expect.current_id, c.expect.next_id, c.expect.curfew_id, c.expect.seconds_to_curfew],
      `python case ${c.name}`);
  }
}

console.log(`${count - fails}/${count} passed`);
process.exit(fails ? 1 : 0);
