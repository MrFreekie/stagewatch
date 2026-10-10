// Ontime Timer card: what to show (SW.ot.view, pure) and the card's DOM, built once and then
// updated in place once a second. The server sends an "ontime_timer" message about once a second
// (Ontime's own timer, as received); between messages the countdown runs on from the
// server-corrected clock. The countdown never comes from a time of day, so a wrong Ontime clock,
// another time zone or a wrong tablet clock cannot change it.
//
// Every state has words and a symbol as well as colour (never colour alone). Overtime is orange
// (never red) with the text OVER and a triangle. Nothing here sounds, raises an alarm, sends
// anything to Ontime or uses animation beyond the schedule card's optional slow flash.
// This is Ontime's timer on a screen. It is not a Stagewatch timer and not a cue source.
"use strict";

SW.ot = (function () {
  const ot = {};

  ot.STALE_S = 3;            // a reading older than this is stale (Ontime sends about once a second)
  ot.RUNNING = ["play", "roll"];

  const pad2 = (n) => (n < 10 ? "0" : "") + n;

  // Signed whole seconds as m:ss, or h:mm:ss from one hour. 59:59 -> 1:00:00; -1 -> "-0:01".
  ot.fmtClock = function (secs) {
    const n = Math.trunc(Number(secs) || 0);
    const a = Math.abs(n), h = Math.floor(a / 3600), m = Math.floor((a % 3600) / 60), s = a % 60;
    return `${n < 0 ? "-" : ""}${h > 0 ? `${h}:${pad2(m)}` : String(m)}:${pad2(s)}`;
  };

  // The whole second to show for a remaining time in ms. Above zero it rounds up (so the count
  // reaches 0:00 at the moment time is up, like the schedule card). From -1 s it is overtime and
  // counts the whole seconds gone over: 1500 -> 2, 0 -> 0, -300 -> 0, -1000 -> -1, -1999 -> -1.
  ot.shownSeconds = function (ms) {
    if (ms > 0) return Math.ceil(ms / 1000 - 1e-9);
    if (ms > -1000) return 0;
    return -Math.floor(-ms / 1000);
  };

  const isNum = (x) => typeof x === "number" && isFinite(x);
  const age = (m, now) => Math.max(0, now - m.received_at);
  const running = (m) => ot.RUNNING.indexOf(m.playback) >= 0;
  const countsDown = (m) => m.timer_type === "count-down";

  // Time moves on from the last message only while Ontime says it is running a count-down and the
  // reading is fresh. Everything else shows the last value as received.
  ot.advancing = (m, now) => running(m) && countsDown(m) && age(m, now) <= ot.STALE_S;

  // Remaining ms now (interpolated from the last message), or null when Ontime sent none.
  ot.remainingMs = function (m, now) {
    if (!isNum(m.current_ms)) return null;
    return ot.advancing(m, now) ? m.current_ms - age(m, now) * 1000 : m.current_ms;
  };

  // "2 MIN" for whole minutes, else "90 S": the wording on the tag next to the countdown.
  const spanText = (ms) => (ms % 60000 === 0 ? `${ms / 60000} MIN` : `${Math.round(ms / 1000)} S`);

  // A step applies only if it is shorter than the whole timer, so a 3 minute item never sits
  // in the 5 and 15 minute steps from the start.
  const applies = (stepMs, totalMs) => totalMs === null || stepMs < totalMs;

  // {level: "" | "warn" | "alert", tag} for a remaining time. Thresholds are Ontime's own
  // warning and danger times for the loaded event when it sent them; otherwise the site's
  // warning minutes (SW.scheduleWarn, as the schedule card: amber then orange, "15 MIN" / "5 MIN").
  // Never red.
  ot.level = function (remainingMs, m) {
    if (!isNum(remainingMs) || remainingMs <= -1000) return { level: "", tag: "" };   // overtime has its own tag
    const total = isNum(m.duration_ms) ? m.duration_ms + (isNum(m.added_ms) ? m.added_ms : 0) : null;
    if (isNum(m.warn_ms) || isNum(m.danger_ms)) {
      if (isNum(m.danger_ms) && applies(m.danger_ms, total) && remainingMs <= m.danger_ms) return { level: "alert", tag: `UNDER ${spanText(m.danger_ms)}` };
      if (isNum(m.warn_ms) && applies(m.warn_ms, total) && remainingMs <= m.warn_ms) return { level: "warn", tag: `UNDER ${spanText(m.warn_ms)}` };
      return { level: "", tag: "" };
    }
    const steps = SW.scheduleWarn.minutes.filter((min) => applies(min * 60000, total));   // longest first
    let step = 0;
    for (let i = 0; i < steps.length; i++) if (remainingMs <= steps[i] * 60000) step = steps[i];
    if (!step) return { level: "", tag: "" };
    return { level: step === steps[steps.length - 1] ? "alert" : "warn", tag: `${step} MIN` };
  };

  const STATES = {
    play: ["running", "▶ RUNNING"], roll: ["rolling", "▶ ROLLING"], pause: ["paused", "⏸ PAUSED"],
    armed: ["ready", "● READY"], stop: ["stopped", "■ STOPPED"],
  };
  const TYPE_NOTE = { "count-up": "Counting up", clock: "Clock", none: "No timer", unknown: "Unknown timer type" };

  // The one place that decides what the card says. m is the "ontime_timer" message; now is the
  // server-corrected time in seconds. Returns:
  //   state    "running" | "rolling" | "paused" | "ready" | "stopped" | "unknown" | "stale" | "off" | "error"
  //   digits   "m:ss" / "h:mm:ss" / "-m:ss" / "--:--";  over: true from 1 s past zero
  //   badge    the words and symbol for the state;  level / tag  the warning step ("" when none)
  //   title, added, extra (array of short facts for tablets), barPct (null = hide), note, noteLevel, cls
  ot.view = function (m, now) {
    const name = (m && m.label) || "Ontime";
    const v = { state: "off", digits: "--:--", over: false, badge: "▲ OFFLINE", level: "", tag: "", title: "",
      added: "", thrNote: "", extra: [], barPct: null, note: "", noteLevel: "warn", typeNote: "", cls: "ot-s-off", flash: false };
    if (!m || m.status !== "ok" || typeof m.playback !== "string") {
      if (m && m.status === "error") {
        v.state = "error"; v.cls = "ot-s-error"; v.badge = "▲ CAN'T READ";
        v.note = `▲ ${name} sent a timer we can't read`;
      } else {
        v.note = `▲ ${name} offline: no timer to show`;
      }
      return v;
    }
    const a = age(m, now);
    const info = STATES[m.playback] || ["unknown", "? UNKNOWN STATE"];
    v.state = info[0]; v.badge = info[1];
    v.title = m.has_event ? (m.title || "") : "No event loaded";
    v.typeNote = countsDown(m) ? "" : (TYPE_NOTE[m.timer_type] || "");
    const stale = a > ot.STALE_S;

    // The number. Frozen at the last reading unless it is advancing.
    let r = ot.remainingMs(m, now);
    if (m.playback === "armed" && r === null && isNum(m.duration_ms)) r = m.duration_ms + (m.added_ms || 0);
    if (r !== null) {
      v.digits = ot.fmtClock(ot.shownSeconds(r));
      v.over = countsDown(m) && r <= -1000 && v.state !== "ready" && v.state !== "stopped";
    }
    if (m.playback === "stop" && !isNum(m.current_ms)) v.digits = "--:--";

    // Warning step and overtime: only for a count-down that is running, paused or in a state we
    // don't know. Never for ready or stopped.
    const live = countsDown(m) && v.state !== "ready" && v.state !== "stopped";
    if (live && r !== null) {
      if (v.over) { v.level = "alert"; v.tag = "▲ OVER"; }
      else { const lv = ot.level(r, m); v.level = lv.level; v.tag = lv.tag; }
    }
    if (live && m.thresholds === "site") v.thrNote = "Warning times from Stagewatch settings";
    // The site's per-step flash (15, 5, 1!): in a step marked to flash, and in overtime when the
    // smallest step is marked.
    const fm = SW.scheduleWarn.flash_minutes || [], mins = SW.scheduleWarn.minutes || [];
    v.flash = v.level === "alert" && r !== null &&
      (v.over ? fm.indexOf(mins[mins.length - 1]) >= 0 : SW.nowFlash(ot.shownSeconds(r)));

    // Progress: how much of the timer (with any added time) has gone.
    const total = isNum(m.duration_ms) ? m.duration_ms + (m.added_ms || 0) : 0;
    if (total > 0 && countsDown(m)) {
      let used = 0;
      if (v.state === "ready" || v.state === "stopped") used = 0;
      else if (v.over) used = total;
      else if (r !== null) used = total - r;
      else if (isNum(m.elapsed_ms)) used = m.elapsed_ms;
      v.barPct = Math.max(0, Math.min(100, (used / total) * 100));
    }

    // Tablet facts.
    if (isNum(m.added_ms) && m.added_ms !== 0) {
      v.added = `${m.added_ms > 0 ? "+" : "-"}${ot.fmtClock(Math.round(Math.abs(m.added_ms) / 1000))} ${m.added_ms > 0 ? "added" : "removed"}`;
    }
    if (v.over) v.extra.push(`Over by ${ot.fmtClock(-ot.shownSeconds(r))}`);
    if (running(m) && !stale && !v.over && isNum(m.finish_in_ms) && m.finish_in_ms > 0 && countsDown(m)) v.extra.push(`Finishes ${SW.fmtTime(m.received_at + m.finish_in_ms / 1000)}`);
    if (isNum(m.elapsed_ms) && m.elapsed_ms >= 0 && v.state !== "ready" && v.state !== "stopped") {
      const e = ot.advancing(m, now) ? m.elapsed_ms + a * 1000 : m.elapsed_ms;
      v.extra.push(`Elapsed ${ot.fmtClock(Math.floor(e / 1000))}`);
    }

    // Stale: nothing arrived for a while. Freeze and strike the digits through, whatever the state said.
    if (stale) {
      const ago = a < 10 ? "a few seconds" : a < 60 ? `${Math.round(a / 10) * 10} s` : `${Math.round(a / 60)} min`;
      v.state = "stale"; v.badge = "▲ STALE"; v.cls = "ot-s-stale";
      v.note = `▲ STALE: nothing from ${name} for ${ago}. Don't trust this time.`;
      v.extra = []; v.level = ""; v.tag = ""; v.flash = false; v.thrNote = "";
      return v;
    }
    v.cls = `ot-s-${v.state}`;
    if (v.state === "unknown") v.note = "? Ontime sent a state this version doesn't know. The time is frozen.";
    return v;
  };

  // Milliseconds until the shown second changes (or the reading would turn stale), so the card
  // changes just after each second rather than at an arbitrary moment. Clamped to 30 ms - 1.1 s.
  ot.nextDelayMs = function (m, now) {
    if (!m || m.status !== "ok" || !ot.advancing(m, now)) return 1000;
    const r = ot.remainingMs(m, now);
    const frac = ((r % 1000) + 1000) % 1000;
    let delay = (frac === 0 ? 1000 : frac) + 10;
    delay = Math.min(delay, (ot.STALE_S - age(m, now)) * 1000 + 30);
    return Math.min(1100, Math.max(30, Math.round(delay)));
  };

  // ---------------------------------------------------------------- the card
  const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
  const setClass = (el, c) => { if (el.className !== c) el.className = c; };
  const setHidden = (el, hide) => { if (el.hidden !== hide) el.hidden = hide; };

  // Builds the card's content once (everything after the card's own <h2>) and returns
  // {nodes, update(view)}; update touches only what changed. Text goes in with textContent only.
  ot.createUi = function () {
    const h = SW.h;
    const ui = {
      title: h("div", { class: "ot-title" }),
      count: h("span", { class: "ot-count" }),
      added: h("span", { class: "ot-added" }),
      bar: h("div", { class: "sched-bar", hidden: true, "aria-hidden": "true" }, h("div", { class: "sched-bar-fill" })),
      badge: h("span", { class: "ot-badge", role: "status" }),
      tag: h("span", { class: "sched-tag", hidden: true }),
      type: h("span", { class: "ot-type muted" }),
      extra: h("p", { class: "ot-extra" }),
      note: h("p", { class: "ot-note", hidden: true, role: "status" }),
      thr: h("p", { class: "ot-thr muted", hidden: true }),
    };
    ui.nodes = [ui.title, h("div", { class: "ot-main" }, ui.count, ui.added), ui.bar,
      h("div", { class: "ot-tags" }, ui.badge, ui.tag, ui.type), ui.extra, ui.note, ui.thr];
    ui.update = function (v) {
      setText(ui.title, v.title);
      setHidden(ui.title, !v.title);
      setText(ui.count, v.digits);
      setText(ui.added, v.added);
      setHidden(ui.added, !v.added);
      setText(ui.badge, v.badge);
      const fl = v.flash ? " flash" : "";
      setText(ui.tag, v.tag);
      setClass(ui.tag, `sched-tag${v.level ? ` lvl-${v.level}` : ""}${fl}`);
      setHidden(ui.tag, !v.tag);
      setText(ui.type, v.typeNote);
      setHidden(ui.type, !v.typeNote);
      setText(ui.thr, v.thrNote);
      setHidden(ui.thr, !v.thrNote);
      setText(ui.extra, v.extra.join(" · "));
      setHidden(ui.extra, v.extra.length === 0);
      setText(ui.note, v.note);
      setClass(ui.note, `ot-note${v.note && v.noteLevel ? ` ${v.noteLevel}` : ""}`);
      setHidden(ui.note, !v.note);
      setHidden(ui.bar, v.barPct === null);
      if (v.barPct !== null) {
        const w = `${v.barPct.toFixed(1)}%`;
        const fill = ui.bar.firstChild;
        if (fill.style.width !== w) fill.style.width = w;
        setClass(ui.bar, `sched-bar${v.level ? ` lvl-${v.level}` : ""}${fl}`);
      }
    };
    return ui;
  };

  return ot;
})();
