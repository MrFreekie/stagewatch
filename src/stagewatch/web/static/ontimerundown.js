// Ontime Rundown card: what to show (SW.rd.view, pure) and the card's DOM, built once and then
// updated in place once a second. The server sends an "ontime_rundown" message about once a
// second (Ontime's own counters, times and offset, as received). Ahead/behind comes only from
// Ontime's own offset, never from Ontime's time minus ours, so a wrong clock or another time zone
// cannot make the show look late. Ontime's times of day are shown as Ontime shows them
// (labelled "Ontime time"), never converted.
//
// Every state has words and a symbol as well as colour (never colour alone). Behind is amber,
// then orange, never red. Nothing here sounds, raises an alarm, sends anything to Ontime or
// animates. This is Ontime's running order on a screen; Stagewatch copies none of it.
"use strict";

SW.rd = (function () {
  const rd = {};

  // ---- Facts about Ontime that a real capture has not yet confirmed. Each is one line, so a
  // capture session can change it without touching the logic. Tests pin both readings.
  rd.OFFSET_POSITIVE_IS_AHEAD = true;   // Ontime's delay docs: positive = running early, negative = running late
  rd.INDEX_BASE = 0;                    // selectedEventIndex 0 = the first event (captured: 8 of 16, not yet proven)

  // ---- Thresholds (milliseconds)
  rd.ON_TIME_MS = 30 * 1000;            // within +/-30 seconds is "on time" (neutral)
  rd.ORANGE_DEFAULT_MS = 5 * 60 * 1000; // orange step when the site has no warning minutes
  // Behind by more than ON_TIME_MS is amber; by more than the orange step is orange. The orange
  // step is the site's smallest schedule warning step (the one the schedule card marks as the
  // last), so crew see the same steps everywhere; never below ON_TIME_MS. Never red.
  rd.orangeMs = function () {
    const w = typeof SW.scheduleWarn === "object" && SW.scheduleWarn ? SW.scheduleWarn.minutes : null;
    const last = Array.isArray(w) && w.length ? w[w.length - 1] * 60000 : NaN;
    return isNum(last) && last > rd.ON_TIME_MS ? last : rd.ORANGE_DEFAULT_MS;
  };
  rd.STALE_S = 3;                       // a reading older than this is stale (Ontime sends about once a second)
  const DAY_MS = 86400000;
  const MAX_TIME_MS = 72 * 3600000;

  const isNum = (x) => typeof x === "number" && isFinite(x);
  const pad2 = (n) => (n < 10 ? "0" : "") + n;

  // Positive = ahead, negative = behind, whatever sign Ontime uses.
  rd.aheadMs = (offsetMs) => (rd.OFFSET_POSITIVE_IS_AHEAD ? offsetMs : -offsetMs);

  // The band for an ahead (+) / behind (-) time in ms. Pure.
  //   kind  "ontime" (within +/-ON_TIME_MS, inclusive) | "ahead" | "behind"
  //   level "" (neutral) | "warn" (amber: behind by more than ON_TIME_MS) | "alert" (orange: more than ORANGE_MS)
  rd.band = function (aheadMs) {
    if (!isNum(aheadMs)) return { kind: "none", level: "" };
    if (Math.abs(aheadMs) <= rd.ON_TIME_MS) return { kind: "ontime", level: "" };
    if (aheadMs > 0) return { kind: "ahead", level: "" };
    return { kind: "behind", level: -aheadMs > rd.orangeMs() ? "alert" : "warn" };
  };

  // A length of time as m:ss, or h:mm:ss from one hour (whole seconds, rounded). 59:59 -> 1:00:00.
  rd.fmtOffset = function (ms) {
    const secs = Math.round(Math.abs(Number(ms) || 0) / 1000);
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    return `${h > 0 ? `${h}:${pad2(m)}` : String(m)}:${pad2(s)}`;
  };

  // Ontime's time of day (ms since its midnight) as HH:MM. Past midnight wraps with "+1 day"
  // (a show that runs to 01:30 has a planned end of 91,800,000). Out of range -> "--:--".
  rd.fmtOntimeTime = function (ms) {
    if (!isNum(ms) || ms < 0 || ms > MAX_TIME_MS) return "--:--";
    const days = Math.floor(ms / DAY_MS), t = Math.floor((ms % DAY_MS) / 60000);
    return `${pad2(Math.floor(t / 60))}:${pad2(t % 60)}${days > 0 ? ` +${days} day${days > 1 ? "s" : ""}` : ""}`;
  };

  // How far through the day: where Ontime's clock sits between the start and the (expected) end.
  // All Ontime values, so zones cannot matter. A clock before the start of a show that runs past
  // midnight is taken to be after midnight (+24 h). Returns {pct, tickPct} (0 to 100; tickPct is
  // the planned end, only when the expected end is later) or null when it can't be worked out.
  rd.dayProgress = function (startMs, plannedEndMs, expectedEndMs, clockMs) {
    if (!isNum(startMs) || !isNum(clockMs)) return null;
    const planned = isNum(plannedEndMs) ? plannedEndMs : null;
    const expected = isNum(expectedEndMs) ? expectedEndMs : null;
    const end = Math.max(planned === null ? -1 : planned, expected === null ? -1 : expected);
    if (end <= startMs) return null;
    let clock = clockMs;
    if (clock < startMs && end >= DAY_MS) clock += DAY_MS;
    const span = end - startMs;
    const pct = Math.max(0, Math.min(100, ((clock - startMs) / span) * 100));
    const tickPct = planned !== null && planned > startMs && planned < end ? ((planned - startMs) / span) * 100 : null;
    return { pct, tickPct };
  };

  // "Event 4 of 12" for a selected index and a count, or "" when it can't be placed.
  rd.positionText = function (index, total) {
    if (!isNum(index) || !isNum(total)) return "";
    const n = index - rd.INDEX_BASE + 1;
    return n >= 1 && n <= total ? `Event ${n} of ${total}` : "";
  };

  const MODE_NOTE = { absolute: "vs plan", relative: "since start", unknown: "" };

  // The words, symbol and level for an offset in Ontime's own sign. Never colour alone.
  rd.offsetView = function (offsetMs) {
    const ahead = rd.aheadMs(offsetMs), b = rd.band(ahead), t = rd.fmtOffset(ahead);
    if (b.kind === "ontime") return { big: "ON TIME", sym: "●", word: "", phrase: "", level: "" };
    if (b.kind === "ahead") return { big: `▲ ${t}`, sym: "▲", word: "AHEAD", phrase: `Running ${t} ahead`, level: "" };
    // Orange (far behind) gets a double triangle, so the step shows without colour too.
    const sym = b.level === "alert" ? "▼▼" : "▼";
    return { big: `${sym} ${t}`, sym, word: "BEHIND", phrase: `Running ${t} behind`, level: b.level };
  };

  const ago = (a) => (a < 10 ? "a few seconds" : a < 60 ? `${Math.round(a / 10) * 10} s` : `${Math.round(a / 60)} min`);

  // The one place that decides what the card says. m is the "ontime_rundown" message; now is the
  // server-corrected time in seconds. Returns:
  //   state  "running" | "notstarted" | "finished" | "empty" | "nodata" | "unplaced" | "stale" | "off" | "error"
  //   big (the large line), word, phrase, level ("" | "warn" | "alert"), position, modeNote,
  //   planned, expected, started, day (short facts), barPct / tickPct (null = hide), note, badge, cls
  rd.view = function (m, now) {
    const name = (m && m.label) || "Ontime";
    const v = { phraseDup: false, unreadable: false, state: "off", cls: "rd-s-off", badge: "▲ OFFLINE", big: "--", word: "", phrase: "", level: "", position: "",
      modeNote: "", planned: "", expected: "", started: "", day: "", barPct: null, tickPct: null, note: "", stale: false };
    if (!m || m.status !== "ok") {
      if (m && m.status === "error") {
        v.state = "error"; v.cls = "rd-s-error"; v.badge = "▲ CAN'T READ";
        v.note = `▲ ${name} sent a rundown we can't read`;
      } else {
        v.note = `▲ ${name} offline: no rundown to show`;
      }
      return v;
    }
    v.badge = "";
    const a = Math.max(0, now - m.received_at);
    const pos = m.position;
    const ps = m.planned_start_ms, pe = m.planned_end_ms, ee = m.expected_end_ms, as = m.actual_start_ms;
    const bothPlanned = isNum(ps) && isNum(pe);
    const plannedText = bothPlanned ? `Planned ${rd.fmtOntimeTime(ps)} to ${rd.fmtOntimeTime(pe)}`
      : isNum(ps) ? `Planned start ${rd.fmtOntimeTime(ps)}` : isNum(pe) ? `Planned end ${rd.fmtOntimeTime(pe)}` : "";

    if (!pos) {
      v.state = "nodata"; v.cls = "rd-s-nodata"; v.big = "--"; v.note = `${name} has not sent rundown information yet`;
    } else if (pos.total === 0) {
      v.state = "empty"; v.cls = "rd-s-empty"; v.big = "NO RUNDOWN"; v.note = `${name} has no rundown loaded`;
    } else if (pos.index === null || pos.index === undefined) {
      if (isNum(as)) {   // started, and no event is selected now
        v.state = "finished"; v.cls = "rd-s-finished"; v.big = "FINISHED";
        v.planned = isNum(pe) ? `Planned end ${rd.fmtOntimeTime(pe)}` : "";
        v.started = `Started ${rd.fmtOntimeTime(as)}`;
      } else {
        v.state = "notstarted"; v.cls = "rd-s-notstarted"; v.big = "NOT STARTED";
        v.planned = plannedText;
      }
    } else {
      v.position = rd.positionText(pos.index, pos.total);
      if (!v.position) {
        v.state = "unplaced"; v.note = `Can't place the current event in ${name}'s rundown`;
      } else {
        v.state = "running";
      }
      v.cls = `rd-s-${v.state}`;
      if (isNum(m.offset_ms)) {
        const o = rd.offsetView(m.offset_ms);
        v.big = o.big; v.word = o.word; v.phrase = o.phrase; v.level = o.level;
        v.phraseDup = !!o.phrase;   // "Running 4:10 behind" repeats the big figure and word (the wall hides it)
        v.modeNote = MODE_NOTE[m.offset_mode] || "";
      } else {
        v.big = "--"; v.phrase = "Ahead or behind not sent";
      }
      v.planned = plannedText;
      if (isNum(ee)) v.expected = `Expected end ${rd.fmtOntimeTime(ee)}${isNum(pe) && ee !== pe ? ` (planned ${rd.fmtOntimeTime(pe)})` : ""}`;
      if (isNum(as)) v.started = `Started ${rd.fmtOntimeTime(as)}`;
      const dp = rd.dayProgress(isNum(as) ? as : ps, pe, ee, m.ontime_clock_ms);
      if (dp) { v.barPct = dp.pct; v.tickPct = dp.tickPct; }
    }
    if (isNum(m.current_day) && m.current_day > 0) v.day = `${name} marks this as a later day of the rundown`;

    // Unreadable: Ontime's latest rundown block could not be read, so these are the last good
    // figures. Stale: nothing arrived for a while. Either way keep the figures but strike them
    // through, say so once in words, and never show a colour step for them.
    if (m.unreadable === true) {
      v.unreadable = true; v.stale = true; v.state = "unreadable"; v.cls = "rd-s-stale"; v.level = "";
      v.note = `▲ CAN'T READ: ${name} sent a rundown block we can't read. These are the last figures that made sense.`;
    } else if (a > rd.STALE_S) {
      v.stale = true; v.state = "stale"; v.cls = "rd-s-stale"; v.level = "";
      v.note = `▲ STALE: nothing from ${name} for ${ago(a)}. Don't trust these figures.`;
    }
    return v;
  };

  // ---------------------------------------------------------------- the card
  const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
  const setClass = (el, c) => { if (el.className !== c) el.className = c; };
  const setHidden = (el, hide) => { if (el.hidden !== hide) el.hidden = hide; };

  // Builds the card's content once (everything after the card's own <h2>) and returns
  // {nodes, update(view)}; update touches only what changed. Text goes in with textContent only.
  rd.createUi = function () {
    const h = SW.h;
    const ui = {
      big: h("span", { class: "rd-big" }),
      word: h("span", { class: "rd-word" }),
      mode: h("span", { class: "rd-mode muted" }),
      badge: h("span", { class: "rd-badge", role: "status" }),
      phrase: h("p", { class: "rd-phrase" }),
      position: h("div", { class: "rd-pos" }),
      tick: h("div", { class: "rd-tick", hidden: true }),
      bar: h("div", { class: "sched-bar rd-bar", hidden: true, "aria-hidden": "true" }, h("div", { class: "sched-bar-fill" })),
      planned: h("p", { class: "rd-line rd-planned" }),
      expected: h("p", { class: "rd-line rd-expected" }),
      started: h("p", { class: "rd-line rd-started" }),
      day: h("p", { class: "rd-line rd-day muted", hidden: true }),
      note: h("p", { class: "rd-note", hidden: true, role: "status" }),
      foot: h("p", { class: "rd-foot muted" }, "Ontime time: the times are Ontime's own clock, not converted."),
    };
    ui.bar.append(ui.tick);
    ui.nodes = [h("div", { class: "rd-main" }, ui.big, ui.word, ui.mode), ui.badge, ui.phrase, ui.position, ui.bar,
      ui.planned, ui.expected, ui.started, ui.day, ui.note, ui.foot];
    ui.update = function (v) {
      setText(ui.big, v.big);
      setText(ui.word, v.word);
      setHidden(ui.word, !v.word);
      setText(ui.mode, v.modeNote);
      setHidden(ui.mode, !v.modeNote);
      setText(ui.badge, v.badge);
      setHidden(ui.badge, !v.badge);
      setText(ui.phrase, v.phrase);
      setHidden(ui.phrase, !v.phrase);
      setClass(ui.phrase, v.phraseDup ? "rd-phrase dup" : "rd-phrase");
      setText(ui.position, v.position);
      setHidden(ui.position, !v.position);
      for (const k of ["planned", "expected", "started", "day"]) {
        setText(ui[k], v[k]);
        setHidden(ui[k], !v[k]);
      }
      setText(ui.note, v.note);
      setHidden(ui.note, !v.note);
      setHidden(ui.bar, v.barPct === null);
      if (v.barPct !== null) {
        const w = `${v.barPct.toFixed(1)}%`;
        const fill = ui.bar.firstChild;
        if (fill.style.width !== w) fill.style.width = w;
        setClass(ui.bar, `sched-bar rd-bar${v.level ? ` lvl-${v.level}` : ""}`);
        setHidden(ui.tick, v.tickPct === null);
        if (v.tickPct !== null) {
          const l = `${v.tickPct.toFixed(1)}%`;
          if (ui.tick.style.left !== l) ui.tick.style.left = l;
        }
      }
    };
    return ui;
  };

  return rd;
})();
