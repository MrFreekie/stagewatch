// User dashboard: the alarm banner plus the cards this dashboard lists (live tiles, history
// chart with markers, marker deltas, sensor table, ...). Cards, their order, the layout
// (tablet / phone / wall) and allowed actions come from the dashboard's config on the server.
"use strict";

(() => {
  const { h, fmt, fmtDelta } = SW;
  const slug = decodeURIComponent(location.pathname.split("/").pop());
  const $ = (id) => document.getElementById(id);
  // Edit cards preview (/d/<slug>?preview=1 inside the editor's frame): never marks, acknowledges,
  // edits or sounds, and draws an unsaved draft the editor posts (checked in onDraft below).
  const PREVIEW = /[?&]preview=1(&|$)/.test(location.search) && window.parent !== window;
  let draft = null, savedDash = null;

  const SITE_TILES = [
    ["site.temperature", "Temperature"],
    ["site.humidity", "Humidity"],
    ["site.pressure", "Pressure"],
    ["site.speed_of_sound", "Speed of sound"],
    ["site.dew_point", "Dew point"],
  ];
  const SERIES_MODES = {
    // `range` is the chart's starting scale in canonical units (°C, %, Pa, m/s): the axis stays on
    // it while the readings fit and grows when they don't.
    temperature: { label: "Temp", site: "site.temperature", kind: "temperature", range: [10, 30] },
    humidity: { label: "RH", site: "site.humidity", kind: "humidity", range: [0, 100] },
    pressure: { label: "Pressure", site: "site.pressure", kind: "pressure", range: [98000, 104000] },
    speed_of_sound: { label: "c", site: "site.speed_of_sound", kind: "speed_of_sound", range: [335, 350] },
  };
  const SPANS = [[900, "15m"], [3600, "1h"], [14400, "4h"], [43200, "12h"], [0, "Show"]];   // 0 = since the show started, one list for every chart
  const COLORS = ["--s2", "--s3", "--s4", "--s5", "--s6"];

  const state = {
    entities: {}, devices: {}, markers: [], alarms: [], sounding: false,
    site: {}, show: {}, schedule: { items: [] }, scheduleMeta: null, dash: null, isAdmin: false, now: Date.now() / 1000,
    mode: localGet("sw.mode", "temperature"), span: Number(localGet("sw.span", 3600)),
    selectedMarker: null, history: {}, showHidden: false,
    showEquipment: localGet("sw.chartEquipment", "off") === "on",
    splSpan: Number(localGet("sw.splspan", 3600)), splHistory: {},
    pwMetric: localGet("sw.pwmetric", "power"), pwSpan: Number(localGet("sw.pwspan", 3600)), pwScope: localGet("sw.pwscope", ""), pwHistory: {},
  };
  // The marker whose note is being edited ({id, text}), or null.
  let noteEdit = null;
  const sounder = new SW.Sounder();
  // Tablets' clocks drift; all times come from the server, so track the offset.
  let clockOffset = 0;
  const serverNow = () => Date.now() / 1000 + clockOffset;
  const syncClock = (now) => { clockOffset = now - Date.now() / 1000; };

  function localGet(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
  function localSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } }
  function toast(msg) {
    const el = h("div", { class: "toast" }, msg);
    document.body.append(el);
    setTimeout(() => el.remove(), 3000);
  }

  const chart = new TimeChart($("chart"), {
    format: (v) => fmt(SERIES_MODES[state.mode].kind, v, false),
    onMarkerClick: (m) => selectMarker(m.id),
  });

  const splChart = new TimeChart($("spl-chart"), { format: (v) => SW.num(v, 0) });
  const pwChart = new TimeChart($("power-chart"), { format: (v) => SW.num(v, 1) });

  // ------------------------------------------------------------ rendering
  // The speed-of-sound formula (Cramer 1993) is tested for 0–30 °C and 75–102 kPa. Outside that
  // the value is still shown and used, with this quiet note beside it. Not an alarm.
  function rangeNote() {
    if (!state.site.c_out_of_range) return null;
    const bounds = state.site.c_out_of_range_bounds || [];
    const parts = [];
    if (bounds.some((b) => b.indexOf("temperature") === 0)) parts.push("0–30 °C");
    if (bounds.some((b) => b.indexOf("pressure") === 0)) parts.push("75–102 kPa");
    return `Outside the formula's tested range (${parts.length ? parts.join(", ") : "0–30 °C"}): figures are approximate`;
  }

  // Trend lines under the tile figures: one shared fetch for every tile, over the History card's
  // span (1 hour without that card), at most once a minute or when the span changes. The paths are
  // worked out once per fetch; renders in between reuse them.
  const TREND_POINTS = 60, TREND_W = 1000, TREND_H = 100;
  const tileHist = { at: 0, span: null, busy: false, d: {} };
  const tileSpan = () => (has("chart") ? state.span : 3600);
  async function loadTileHistory(force) {
    if (!has("env_tiles") || tileHist.busy) return;
    const span = tileSpan();
    if (!force && span === tileHist.span && Date.now() - tileHist.at < 60000) return;
    tileHist.busy = true; tileHist.at = Date.now(); tileHist.span = span;
    const ids = SITE_TILES.map(([id]) => id);
    const t1 = serverNow(), t0 = t1 - spanSeconds(span);
    let data = {};
    try {
      data = await SW.api("GET", `/api/history?entities=${encodeURIComponent(ids.join(","))}&since=${t0}&points=${TREND_POINTS}`) || {};
    } catch (_) { data = {}; }
    tileHist.busy = false;
    const gap = Math.max(60, 3 * (t1 - t0) / TREND_POINTS);
    tileHist.d = {};
    for (const id of ids) tileHist.d[id] = SW.trendPath(data[id], t0, t1, gap, TREND_W, TREND_H);
    renderTiles();
  }
  function trendBox(id) {
    const d = tileHist.d[id];
    return h("div", { class: "trend", "aria-hidden": "true" }, d ? SW.svg("svg", { viewBox: `0 0 ${TREND_W} ${TREND_H}`, preserveAspectRatio: "none" },
      SW.svg("path", { d, "vector-effect": "non-scaling-stroke" })) : null);
  }

  function renderTiles() {
    loadTileHistory(false);
    const tiles = $("tiles");
    tiles.replaceChildren();
    for (const [id, label] of SITE_TILES) {
      if (id === "site.dew_point" && state.dash && state.dash.show_dew_point === false) continue;   // unticked for this dashboard in Admin
      const e = state.entities[id];
      if (!e) continue;
      const [num, ...unit] = fmt(e.kind, e.value).split(" ");
      let foot = "";
      let note = null;
      const counts = state.site.sensors || {};
      if (id === "site.temperature") foot = `${counts.temperature || 0} sensor(s) averaged`;
      if (id === "site.humidity") foot = counts.humidity ? `${counts.humidity} sensor(s)` : "no sensor: 50% assumed";
      if (id === "site.pressure") foot = counts.pressure ? `${counts.pressure} sensor(s)` : "no sensor: from site altitude";
      if (id === "site.speed_of_sound" && e.value) {
        foot = `${(1000 / e.value).toFixed(3)} ms per metre`;
        note = rangeNote();
      }
      tiles.append(h("div", { class: "tile" + (e.value === null ? " stale" : "") },
        h("div", { class: "label" }, label),
        h("div", { class: "value" }, num, unit.length ? h("span", { class: "unit" }, unit.join(" ")) : null),
        trendBox(id),
        h("div", { class: "foot" }, foot),
        note ? h("div", { class: "foot approx" }, note) : null));
    }
  }

  // Alarm notice timers: the server says how long is left on each (see SW.splitAlarms); this page
  // adds the time since the list arrived and works the list out again when the next one is due.
  // performance.now() is a steady clock: a change of the device clock cannot hide or fold early.
  const steadyNow = () => (window.performance && performance.now ? performance.now() : Date.now());
  let alarmsAt = steadyNow(), alarmTimer = null, olderOpen = false;
  function setAlarms(list) {
    state.alarms = list;
    alarmsAt = steadyNow();
  }
  function renderAlarms() {
    clearTimeout(alarmTimer);
    const bar = $("alarms");
    const split = SW.splitAlarms(state.alarms, Math.max(0, (steadyNow() - alarmsAt) / 1000));
    const active = split.shown, older = split.older;
    if (split.next !== null) alarmTimer = setTimeout(renderAlarms, Math.min(2147000000, Math.max(1000, split.next * 1000 + 250)));
    // Styled from everything still listed, folded lines too: a sounding notice is never left unstyled.
    const loud = active.concat(older).filter((a) => !a.silent);
    // Only silent alarms: a calm "notice" style, no level colour, no Ack.
    bar.className = "alarm-bar" + (loud.length ? ` show l${Math.max(...loud.map((a) => a.level))}` : (active.length || older.length ? " show notice" : ""))
      + (state.sounding ? " sounding" : "");
    const line = (a) => h("li", { class: a.silent ? "silent" : "" }, h("span", { class: "lvl" }, a.silent ? "notice" : a.level_name), h("span", { class: "sr-only" }, ": "), a.message, a.acked ? h("span", { class: "muted" }, " (acknowledged)") : null);
    const fold = older.length ? h("li", { class: "older" },
      h("details", { open: olderOpen, ontoggle: (ev) => { olderOpen = ev.target.open; } },
        h("summary", {}, `Older notices (${older.length})`),
        h("ul", {}, older.map(line)))) : null;
    // The wall shows at most three lines (alerts sort first); the rest become one "+N more" line.
    const cap = (state.dash && state.dash.layout) === "wall" ? 3 : Infinity;
    const lines = active.slice(0, cap).map(line);
    if (active.length > cap) lines.push(h("li", { class: "more" }, `+${active.length - cap} more`));
    $("alarm-list").replaceChildren(...lines, ...(fold ? [fold] : []));
    const canAck = !PREVIEW && (state.isAdmin || (state.dash && state.dash.allow_ack));
    $("ack").hidden = !(canAck && state.sounding);
    sounder.set(state.sounding && !PREVIEW);
    renderSound();
    renderMessages();
  }

  // ---- Messages icon (header): the alarm bar's list as a count, the worst kind and a panel. ----
  // The panel element stays put; only its rows are rebuilt, so a live update never closes it.
  let msgOpen = false, msgTimer = null, msgLast = "";
  function renderMessages() {
    const sum = SW.messageSummary(state.alarms, Math.max(0, (steadyNow() - alarmsAt) / 1000), state.sounding);
    const btn = $("msg-btn"), badge = $("msg-badge");
    btn.className = "msg-btn" + (sum.worst ? ` sev-${sum.worst}` : "");
    btn.setAttribute("aria-label", sum.label);
    btn.title = sum.label;
    badge.hidden = !sum.count;
    badge.textContent = sum.count ? `${SW.MSG_SEV[sum.worst].symbol}${sum.count > 99 ? "99+" : sum.count}` : "";
    if (sum.label !== msgLast) { $("msg-live").textContent = sum.label; msgLast = sum.label; }
    const now = serverNow();
    $("msg-list").replaceChildren(...(sum.items.length ? sum.items.map((m) => {
      const sev = SW.MSG_SEV[m.sev];
      return h("li", { class: `msg-row sev-${m.sev}` },
        h("span", { class: "msg-sym", "aria-hidden": "true" }, sev.symbol),
        h("span", { class: "msg-word" }, sev.word),
        h("span", { class: "sr-only" }, ": "),
        h("span", { class: "msg-text" }, m.text, m.older ? h("span", { class: "muted" }, " (older)") : null),
        h("span", { class: "msg-age muted" }, m.since ? SW.age(m.since, now) : ""));
    }) : [h("li", { class: "msg-empty muted" }, "No messages")]));
  }
  function setMessagesOpen(open, refocus) {
    msgOpen = open;
    $("msg-panel").hidden = !open;
    $("msg-btn").setAttribute("aria-expanded", open ? "true" : "false");
    clearInterval(msgTimer);
    if (open) msgTimer = setInterval(renderMessages, 15000);   // keeps the ages current
    if (!open && refocus) $("msg-btn").focus();
  }
  $("msg-btn").addEventListener("click", (ev) => { ev.stopPropagation(); if (!msgOpen && cs.open) setConnOpen(false); setMessagesOpen(!msgOpen); });
  document.addEventListener("click", (ev) => {
    if (msgOpen && !$("msg-panel").contains(ev.target)) setMessagesOpen(false);
  });
  document.addEventListener("keydown", (ev) => {
    if (msgOpen && (ev.key === "Escape" || ev.key === "Esc")) setMessagesOpen(false, true);
  });

  // ---- Internet status (header, beside the connection button): information only. ----
  let netOpen = false;
  function renderNet(net) {
    const st = SW.netStatus(net);
    const btn = $("net-btn");
    btn.className = `cs-btn net-btn net-${st.cls}`;
    btn.setAttribute("aria-label", st.label);
    btn.title = st.label;
    $("net-sym").textContent = st.symbol;
    $("net-word").textContent = st.word;
    $("net-head").textContent = st.head;
  }
  function setNetOpen(open) {
    netOpen = open;
    $("net-panel").hidden = !open;
    $("net-btn").setAttribute("aria-expanded", open ? "true" : "false");
  }
  $("net-btn").addEventListener("click", (ev) => { ev.stopPropagation(); setNetOpen(!netOpen); });
  document.addEventListener("click", (ev) => { if (netOpen && !$("net-panel").contains(ev.target)) setNetOpen(false); });

  // ---- Connection status (header, beside the messages icon): Online / Offline / Error. ----
  // Fed by the existing live feed (SW.connect reconnects with back-off on its own). "Heard" is any
  // message from the server, including its reply to our 15 s ping, so a quiet but healthy feed stays
  // Online while a silent one turns Offline: stale beats online. Plain words only on screen.
  const cs = { wsUp: false, since: Date.now(), lastHeard: null, fault: false, open: false, last: "", timer: null };
  function renderConn() {
    const st = SW.connStatus({ now: Date.now(), wsUp: cs.wsUp, since: cs.since, lastHeard: cs.lastHeard, fault: cs.fault });
    const btn = $("cs-btn");
    btn.className = `cs-btn cs-${st.cls}`;
    btn.setAttribute("aria-label", st.label);
    btn.title = st.label;
    $("cs-sym").textContent = st.symbol;
    $("cs-word").textContent = st.word;
    $("cs-head").textContent = st.headline;
    $("cs-list").replaceChildren(...st.lines.map((t) => h("li", {}, t)));
    $("cs-advice").textContent = st.advice;
    if (st.state !== cs.last) { $("cs-live").textContent = st.label; cs.last = st.state; }
  }
  function setConnOpen(open, refocus) {
    cs.open = open;
    $("cs-panel").hidden = !open;
    $("cs-btn").setAttribute("aria-expanded", open ? "true" : "false");
    clearInterval(cs.timer);
    if (open) { renderConn(); cs.timer = setInterval(renderConn, 1000); }
    if (!open && refocus) $("cs-btn").focus();
  }
  $("cs-btn").addEventListener("click", (ev) => { ev.stopPropagation(); if (!cs.open && msgOpen) setMessagesOpen(false); setConnOpen(!cs.open); });
  document.addEventListener("click", (ev) => {
    if (cs.open && !$("cs-panel").contains(ev.target)) setConnOpen(false);
  });
  document.addEventListener("keydown", (ev) => {
    if (cs.open && (ev.key === "Escape" || ev.key === "Esc")) setConnOpen(false, true);
  });
  // The server answering with a fault (HTTP 5xx) is "Error"; no answer at all is the feed's business.
  function checkServer() {
    if (!cs.wsUp) return;
    SW.api("GET", "/api/info").then(() => { cs.fault = false; renderConn(); })
      .catch((err) => { if (err && err.status >= 500) { cs.fault = true; renderConn(); } });
  }
  setInterval(checkServer, 30000);
  setInterval(renderConn, 5000);

  // ---- Alarm sound On/Off (always visible). The choice is remembered per device (browser). ----
  // "On" needs a tap the first time (browser rule), so tapping the button arms audio; any first
  // tap anywhere on the page also arms it when the saved choice is On.
  const SOUND_KEY = `sw.sound.${slug}`;
  const soundWanted = () => localGet(SOUND_KEY, "on") !== "off";
  function renderSound() {
    const wanted = soundWanted();
    sounder.muted = !wanted;
    const armed = sounder.enabled;
    const on = wanted && armed;
    const btn = $("sound-toggle");
    const needsTap = wanted && !armed;
    $("sound-label").textContent = on ? "Alarm sound: On" : (needsTap ? "Alarm sound: Off \u2014 tap to turn on" : "Alarm sound: Off");
    btn.classList.toggle("on", on);
    btn.classList.toggle("needs-tap", needsTap);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "Alarms will beep on this device. Tap to mute." : "Tap to turn the alarm beep on for this device.";
    // The wall (kiosk) layout hides the button only when audio already works there.
    const layout = (state.dash && state.dash.layout) || "tablet";
    btn.hidden = layout === "wall" && on;
  }
  // Day / Night: follows the device until tapped, then this screen remembers the choice.
  const THEME_KEY = "stagewatch.theme";
  const deviceIsLight = () => !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
  const isDay = () => {
    const t = document.documentElement.getAttribute("data-theme");
    return t ? t === "light" : deviceIsLight();
  };
  function applyTheme(pick) {
    if (pick === "day" || pick === "night") document.documentElement.setAttribute("data-theme", pick === "day" ? "light" : "dark");
    else document.documentElement.removeAttribute("data-theme");
    const day = isDay(), btn = $("theme-toggle");
    btn.textContent = day ? "☾ Night" : "☀ Day";   // what a tap switches to
    btn.setAttribute("aria-label", day ? "Switch to night mode (dark)" : "Switch to day mode (light)");
    chart.draw();   // the chart reads its colours from the theme
  }
  $("theme-toggle").addEventListener("click", () => {
    const pick = isDay() ? "night" : "day";
    try { localStorage.setItem(THEME_KEY, pick); } catch (_) { /* not remembered; still switches */ }
    applyTheme(pick);
  });
  let savedTheme = "";
  try { savedTheme = localStorage.getItem(THEME_KEY) || ""; } catch (_) { /* private mode: follow the device */ }
  applyTheme(savedTheme);

  $("sound-toggle").addEventListener("click", () => {
    if (PREVIEW) return;
    if (soundWanted() && sounder.enabled) { localSet(SOUND_KEY, "off"); renderSound(); return; }
    localSet(SOUND_KEY, "on");
    sounder.enable();   // this tap is the user gesture the browser needs
    renderSound();
    // A short test beep so you can hear that it is on.
    Promise.resolve(sounder.ctx && sounder.ctx.resume()).then(() => { renderSound(); sounder.beep(); }).catch(() => {});
  });
  sounder.onchange = renderSound;
  // pointerdown needs Safari 13; touchend/mousedown cover iOS 12.
  for (const evt of ["pointerdown", "touchend", "mousedown", "keydown"]) {
    document.addEventListener(evt, () => { if (soundWanted() && !sounder.enabled) { sounder.enable(); renderSound(); } });
  }

  function sensorDevices() {
    return Object.values(state.devices).filter((d) => d.id !== "site" && d.category !== "service" && d.category !== "meter" && d.category !== "host")   // power meters have their own Power card
      .sort((a, b) => (a.area || a.name).localeCompare(b.area || b.name));
  }

  // Node health: Wi-Fi signal (dBm) in words, and battery level. Amber when it needs attention.
  SW.signalWord = (dbm) => (dbm >= -67 ? "good" : dbm >= -75 ? "fair" : "weak");
  // A "*" straight after a value or legend label: text, not colour, with a name for screen readers.
  const offsetStar = (title) => h("span", { class: "cal-star", role: "img", title: title || SW.OFFSET_NOTE, "aria-label": title || SW.OFFSET_NOTE }, "*");
  function renderSensors() {
    const devs = sensorDevices();
    const ents = Object.values(state.entities);
    const any = (k) => devs.some((d) => ents.some((e) => e.device_id === d.id && e.kind === k));
    const showSignal = any("signal_strength"), showBattery = any("battery");
    const isHealth = (e) => e.kind === "signal_strength" || e.kind === "battery";
    // One row for what a node measures: `mine` is the entities this row shows, `health` whether the
    // node's own signal and battery go on it.
    const rowFor = (d, mine, health) => {
      const byKind = (k) => mine.find((e) => e.kind === k);
      const cell = (k, extra) => { const e = byKind(k); return h("td", { class: "num" + (extra || "") + (e && e.stale ? " muted" : "") }, e ? fmt(k, e.value) : "", SW.hasOffset(e) ? offsetStar() : null); };
      const healthCell = (k, warn, text) => {   // health columns are left off the wall (class wall-skip)
        const e = health ? byKind(k) : null;
        if (!e || e.value === null || e.value === undefined) return h("td", { class: "num wall-skip muted" }, "");
        const cls = "num wall-skip" + (e.stale ? " muted" : warn(e.value) ? " warn-text" : "");
        return h("td", { class: cls }, text(e.value));
      };
      const last = Math.max(0, ...mine.filter((e) => !isHealth(e)).map((e) => e.updated || 0));
      return h("tr", {},
        h("td", {}, d.name, d.area ? h("div", { class: "muted", style: "font-size:12px" }, d.area) : null),
        h("td", {}, (() => { const st = SW.deviceStatus(d, state.now); return h("span", { class: `status ${st.cls}`, title: st.title }, st.text); })()),
        cell("temperature"), cell("humidity"), cell("pressure", " wall-skip"),
        showSignal ? healthCell("signal_strength", (v) => v < -75, (v) => `${fmt("signal_strength", v)} ${SW.signalWord(v)}`) : null,
        showBattery ? healthCell("battery", (v) => v < 20, (v) => (v < 20 ? `${fmt("battery", v)} low` : fmt("battery", v))) : null,
        h("td", { class: "num muted" }, SW.age(last || null, state.now)));
    };
    // Environment rows first, then Equipment. A node whose sensors are all equipment is an
    // equipment row; a node with a mix appears in both groups, each with its own sensors.
    const envRows = [], eqRows = [];
    for (const d of devs) {
      const mine = ents.filter((e) => e.device_id === d.id);
      const eq = mine.filter((e) => !isHealth(e) && SW.isEquipment(e));
      const env = mine.filter((e) => !isHealth(e) && !SW.isEquipment(e));
      if (!eq.length) { envRows.push(rowFor(d, mine, true)); continue; }
      if (!env.length) { eqRows.push(rowFor(d, mine, true)); continue; }
      envRows.push(rowFor(d, mine.filter((e) => !SW.isEquipment(e)), true));
      eqRows.push(rowFor(d, eq, false));
    }
    const cols = 6 + (showSignal ? 1 : 0) + (showBattery ? 1 : 0);
    const heading = (text) => h("tr", { class: "group-row" }, h("th", { colspan: cols, scope: "colgroup" }, text));
    const body = eqRows.length ? [heading("Environment"), ...envRows, heading("Equipment"), ...eqRows] : envRows;
    $("sensors").replaceChildren(
      h("thead", {}, h("tr", {}, h("th", {}, "Node"), h("th", {}, "Status"),
        h("th", { class: "num" }, "Temp"), h("th", { class: "num" }, "RH"),
        h("th", { class: "num wall-skip" }, "Pressure"),
        showSignal ? h("th", { class: "num wall-skip" }, "Signal") : null,
        showBattery ? h("th", { class: "num wall-skip" }, "Battery") : null,
        h("th", { class: "num" }, "Updated"))),
      h("tbody", {}, body.length ? body : h("tr", {}, h("td", { colspan: cols, class: "muted" }, "No sensor nodes yet. An admin can adopt ESPHome nodes."))));
    const foot = SW.offsetFootnote(SW.offsetNotes(devs, ents));
    $("offset-note").textContent = foot;
    $("offset-note").hidden = !foot;
    $("site-note").textContent = `Readings older than ${Math.round(state.site.stale_after_s || 60)} s are treated as stale and left out of the average.`;
  }

  // Notes, Hide and Un-hide: anyone on a dashboard allowed to add markers (any marker), and admins.
  // Deleting stays admin-only.
  const canEditMarkers = () => !PREVIEW && !!(state.isAdmin || (state.dash && state.dash.allow_marker));
  const findMarker = (id) => state.markers.filter((m) => m.id === id)[0] || null;

  function renderMarkers() {
    if (has("chart")) {
      chart.markers = SW.visibleMarkers(state.markers, false).map((m) => ({ ...m, selected: m.id === state.selectedMarker }));
      chart.draw();
    }
    if (has("spl_live")) updateSplChart();
    if (!has("markers")) return;
    const canDelete = !PREVIEW && state.isAdmin;
    const canEdit = canEditMarkers();
    const hiddenN = SW.hiddenMarkerCount(state.markers);
    if (!hiddenN) state.showHidden = false;
    const shown = SW.visibleMarkers(state.markers, state.showHidden);
    const items = shown.slice().sort((a, b) => b.ts - a.ts).map((m) => {
      const ms = SW.markerStyle(m.source);
      return h("li", { class: (m.id === state.selectedMarker ? "sel" : "") + (m.hidden ? " hidden-marker" : ""), onclick: () => selectMarker(m.id) },
        h("span", { class: "mk", title: ms.name, style: `background:var(--marker-${ms.key},${ms.fallback});color:var(--marker-${ms.key}-ink,${ms.ink})` }, ms.glyph),
        h("span", { class: "t" }, SW.fmtTime(m.ts)),
        h("span", { class: "lbl" }, m.label,
          m.note ? h("span", { class: "note-flag", title: "Has a note" }, "✎ note") : null,
          m.hidden ? h("span", { class: "hid-tag" }, "Hidden") : null),
        m.hidden && canEdit ? h("button", { type: "button", class: "mk-btn only-interactive", title: "Show this marker on the chart again",
          onclick: (ev) => { ev.stopPropagation(); setHidden(m.id, false); } }, "Un-hide") : null,
        canDelete ? h("button", { type: "button", class: "mk-btn danger", title: "Delete marker", "aria-label": "Delete marker", onclick: (ev) => { ev.stopPropagation(); deleteMarker(m.id); } }, "✕") : null);
    });
    const empty = hiddenN && !state.showHidden ? `No markers on show. ${hiddenN} hidden.` : "No markers yet. Add one at soundcheck, e.g. \"Aligned\".";
    $("marker-list").replaceChildren(...(items.length ? items : [h("li", { class: "muted" }, empty)]));
    $("marker-hidden-toggle").hidden = !hiddenN;
    $("marker-show-hidden").checked = state.showHidden;
    $("marker-hidden-label").textContent = `Show hidden (${hiddenN})`;
  }

  $("marker-show-hidden").addEventListener("change", (ev) => {
    state.showHidden = !!ev.target.checked;
    renderMarkers();
  });

  // ---- The selected marker's note, under the drift panel. Plain text (line breaks kept), built
  // with textContent only. Updated in place when the marker changes on another screen.
  const noteEl = h("div", { class: "marker-note" });
  // A 422 from the server reads "note: Value error, <crew text>": show only the crew text.
  const crewText = (err) => {
    const m = String((err && err.message) || "");
    const i = m.indexOf("Value error, ");
    return i >= 0 ? m.slice(i + 13) : m;
  };

  function renderMarkerNote() {
    const m = state.selectedMarker === null ? null : findMarker(state.selectedMarker);
    if (!m) { noteEl.replaceChildren(); return; }
    const can = canEditMarkers();
    if (noteEdit && noteEdit.id === m.id && can) {
      const count = h("span", { class: "muted", style: "font-size:12px" });
      const upd = () => { count.textContent = `${SW.num(noteEdit.text.length, 0)} of ${SW.num(SW.NOTE_MAX, 0)} characters`; };
      const ta = h("textarea", { rows: 4, maxlength: SW.NOTE_MAX, "aria-label": `Note for ${m.label}`,
        placeholder: "What happened, what was changed", oninput: (ev) => { noteEdit.text = ev.target.value; upd(); } });
      ta.value = noteEdit.text;
      upd();
      noteEl.replaceChildren(h("h3", {}, "Note"), ta, count,
        h("p", { class: "hint" }, "Visible to anyone on the show network. No names or phone numbers."),
        h("div", { class: "row" },
          h("button", { type: "button", class: "primary", onclick: () => saveNote(m.id, ta) }, "Save"),
          h("button", { type: "button", onclick: () => { noteEdit = null; renderMarkerNote(); } }, "Cancel")));
      return;
    }
    const kids = [h("h3", {}, "Note")];
    kids.push(m.note ? h("div", { class: "marker-note-text" }, m.note) : h("div", { class: "muted", style: "margin-bottom:6px" }, "No note."));
    if (m.hidden) kids.push(h("div", { class: "muted", style: "font-size:13px;margin-bottom:6px" }, "Hidden: not on the chart. Its readings still count for the drift above."));
    if (can) {
      kids.push(h("div", { class: "row only-interactive" },
        h("button", { type: "button", onclick: () => { noteEdit = { id: m.id, text: m.note || "" }; renderMarkerNote(); } }, m.note ? "Edit note" : "Add note"),
        h("button", { type: "button", onclick: () => setHidden(m.id, !m.hidden) }, m.hidden ? "Un-hide" : "Hide")));
    }
    noteEl.replaceChildren(...kids);
  }

  function applyMarker(updated) {
    state.markers = state.markers.map((x) => (x.id === updated.id ? updated : x));
    renderMarkers();
    // An open note editor here is left alone (what is being typed is kept); Save sends it.
    if (state.selectedMarker === updated.id && !(noteEdit && noteEdit.id === updated.id)) renderMarkerNote();
  }

  async function saveNote(id, ta) {
    const text = ta.value;
    if (text.length > SW.NOTE_MAX) { toast(`Notes can be up to ${SW.num(SW.NOTE_MAX, 0)} characters.`); return; }
    try {
      const m = await SW.api("PATCH", `/api/markers/${id}`, { note: text, dashboard: slug });
      noteEdit = null;
      applyMarker(m);
      toast("Note saved");
    } catch (err) { toast(`${crewText(err)} The note was not saved.`); }
  }

  async function setHidden(id, hidden) {
    try {
      const m = await SW.api("PATCH", `/api/markers/${id}`, { hidden, dashboard: slug });
      applyMarker(m);
      toast(hidden ? "Marker hidden. Tick Show hidden to find it again." : "Marker back on the chart");
    } catch (err) { toast(crewText(err)); }
  }

  // Seconds a span button covers; 0 ("Show") runs from the show start, or the last hour before one starts.
  function spanSeconds(span) {
    if (span > 0) return span;
    const since = (state.show && state.show.started) || serverNow() - 3600;
    return Math.max(60, serverNow() - since);
  }

  function renderSegs() {
    $("series-seg").replaceChildren(...Object.entries(SERIES_MODES).map(([k, m]) =>
      h("button", { class: k === state.mode ? "on" : "", onclick: () => { state.mode = k; localSet("sw.mode", k); renderSegs(); loadHistory(); } }, m.label)));
    $("span-seg").replaceChildren(...SPANS.map(([s, label]) =>
      h("button", { class: s === state.span ? "on" : "", onclick: () => { state.span = s; localSet("sw.span", String(s)); renderSegs(); loadHistory(); loadTileHistory(true); } }, label)));
  }

  // --------------------------------------------------------------- chart
  function chartEntities() {
    const mode = SERIES_MODES[state.mode];
    const ids = [mode.site];
    if (mode.kind !== "speed_of_sound") {
      for (const e of Object.values(state.entities)) if (!e.derived && e.kind === mode.kind && !SW.isEquipment(e)) ids.push(e.id);
    }
    return ids;
  }

  // Equipment readings of the same kind, only when the viewer has ticked "Show equipment sensors".
  // They are separate dashed lines; the site line and every average come from the server and never
  // include them.
  function equipmentChartIds() {
    const mode = SERIES_MODES[state.mode];
    if (!state.showEquipment || mode.kind === "speed_of_sound") return [];
    return Object.values(state.entities).filter((e) => SW.isEquipment(e) && e.kind === mode.kind).map((e) => e.id);
  }

  function updateChartSeries() {
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    renderEquipToggle();
    const ids = chartEntities();
    const eqIds = equipmentChartIds();
    chart.series = ids.map((id, i) => {
      const e = state.entities[id];
      const dev = e && state.devices[e.device_id];
      return {
        adjusted: i === 0 ? SW.averageAdjusted(Object.values(state.entities), SERIES_MODES[state.mode].kind) : SW.hasOffset(e),
        label: i === 0 ? "Site average" : (dev ? dev.name : id),
        color: i === 0 ? css("--s1") : css(COLORS[(i - 1) % COLORS.length]),
        width: i === 0 ? 3 : 1.3,
        points: state.history[id] || [],
      };
    }).concat(eqIds.map((id, j) => {
      const e = state.entities[id];
      return {
        adjusted: SW.hasOffset(e),
        label: `${e.name} (equipment)`,
        color: css(COLORS[(ids.length - 1 + j) % COLORS.length]),
        width: 1.6,
        dash: [7, 4],
        dashed: true,
        points: state.history[id] || [],
      };
    }));
    const now = serverNow();
    chart.range = [now - spanSeconds(state.span), now];
    chart.defaultRange = (SERIES_MODES[state.mode] || {}).range || null;
    chart.draw();
    $("legend").replaceChildren(...chart.series.map((s) => h("span", {}, h("i", { class: s.dashed ? "dashed" : "", style: s.dashed ? `color:${s.color}` : `background:${s.color}` }), s.label,
      s.adjusted ? offsetStar(s === chart.series[0] ? "Calibration offset applied to a sensor in this average" : SW.OFFSET_NOTE) : null)));
  }

  async function loadHistory() {
    const ids = chartEntities().concat(equipmentChartIds());
    const since = serverNow() - spanSeconds(state.span);
    try {
      state.history = await SW.api("GET", `/api/history?entities=${encodeURIComponent(ids.join(","))}&since=${since}&points=500`);
    } catch (_) { state.history = {}; }
    updateChartSeries();
  }

  // Per-viewer choice, kept in this browser only (and the page works without it).
  function renderEquipToggle() {
    const hasEq = Object.values(state.entities).some((e) => SW.isEquipment(e));
    $("chart-equip-toggle").hidden = !hasEq || SERIES_MODES[state.mode].kind === "speed_of_sound";
    $("chart-equip").checked = state.showEquipment;
  }
  $("chart-equip").addEventListener("change", (ev) => {
    state.showEquipment = !!ev.target.checked;
    localSet("sw.chartEquipment", state.showEquipment ? "on" : "off");
    loadHistory();
  });

  // ------------------------------------------------------------ markers
  async function selectMarker(id) {
    if (state.selectedMarker !== id) noteEdit = null;
    state.selectedMarker = id;
    renderMarkers();
    if (!has("markers")) return;   // the drift panel lives in the Markers card
    const box = $("delta");
    box.hidden = false;
    renderMarkerNote();
    box.replaceChildren(h("span", { class: "muted" }, "Loading…"), noteEl);
    try {
      const d = await SW.api("GET", `/api/markers/${id}/delta`);
      const v = d.values;
      const row = (label, id, kind) => h("tr", {}, h("td", { class: "muted" }, label),
        h("td", { class: "num" }, fmt(kind, v[id].then)), h("td", { class: "muted" }, "→"),
        h("td", { class: "num" }, fmt(kind, v[id].now)), h("td", { class: "num" }, fmtDelta(kind, v[id].delta)));
      const ms = d.delta_travel_ms;
      const note = rangeNote();
      // (replaceChildren would print a null child as the text "null", so only real nodes go in.)
      box.replaceChildren(...[
        h("div", { class: "muted" }, `Since "${d.marker.label}" at ${SW.fmtTime(d.marker.ts)}`),
        h("div", { class: "big" }, ms === null ? "—" : `${SW.signed(ms, 3)} ms`),
        h("div", { class: "muted", style: "font-size:12px;margin-bottom:6px" },
          `change in sound travel time over ${d.reference_distance_m} m (positive = sound now arrives later)`),
        note ? h("div", { class: "notice approx", style: "margin-bottom:6px" }, note) : null,
        h("table", {}, h("tbody", {},
          row("Temp", "site.temperature", "temperature"),
          row("RH", "site.humidity", "humidity"),
          row("Pressure", "site.pressure", "pressure"),
          row("c", "site.speed_of_sound", "speed_of_sound"))),
        noteEl].filter(Boolean));
    } catch (err) {
      box.replaceChildren(h("span", { class: "error" }, err.message), noteEl);
    }
  }

  async function deleteMarker(id) {
    if (!confirm("Delete this marker?")) return;
    try { await SW.api("DELETE", `/api/markers/${id}`); } catch (err) { toast(err.message); }
  }

  $("marker-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const label = $("marker-label").value.trim() || "Marker";
    try {
      const m = await SW.api("POST", "/api/markers", { label, dashboard: slug });
      $("marker-label").value = "";
      toast(`Marker "${m.label}" added`);
    } catch (err) { toast(err.message); }
  });

  $("ack").addEventListener("click", async () => {
    try { await SW.api("POST", "/api/alarms/ack", { dashboard: slug }); } catch (err) { toast(err.message); }
  });

  // ------------------------------------------------------------ schedule
  // NOW / NEXT and the running order for this dashboard's stage (dash.stage; empty = all
  // stages). The DOM is built once per schedule change (snapshot or "schedule" message); a
  // one-second tick then only updates text and classes in place. The tick runs only while the
  // card is on this dashboard and has items, and stops itself otherwise. Countdowns come from the
  // server-corrected clock (serverNow); nothing here sounds or raises an alarm.
  // Layouts (style.css): tablet shows everything; phone shows a strip that expands on tap; wall
  // shows only the large NOW / NEXT strip.
  const sched = { timer: null, ui: null, rows: [], items: [], selected: null, open: false, setlistKey: "",
    loadedKey: "", loading: false };
  const schedH2 = $("schedule-card").querySelector("h2");
  const schedStage = () => (state.dash && state.dash.stage ? state.dash.stage : "");
  const schedItems = () => SW.scheduleOrder((state.schedule && state.schedule.items) || [], schedStage());
  const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
  const setClass = (el, c) => { if (el.className !== c) el.className = c; };
  const hasSetlist = (it) => !!(it && it.setlist && it.setlist.trim());
  const timeRange = (it) => (it.planned_end !== null && it.planned_end !== undefined
    ? `${SW.fmtTime(it.planned_start)}–${SW.fmtTime(it.planned_end)}` : SW.fmtTime(it.planned_start));
  const KIND_NAMES = { venue_access: "Venue Access", load_in: "Load In", crew_call: "Crew Call", soundcheck: "Soundcheck",
    doors: "Doors", act: "", changeover: "Changeover", curfew: "Curfew", load_out: "Load Out", other: "" };
  // "Changeover" beside an item, unless its title already says so (anywhere in it; "Load-In" counts).
  const kindNote = (it) => {
    const k = KIND_NAMES[it.kind] || "";
    return k && it.title.toLowerCase().replace(/-/g, " ").indexOf(k.toLowerCase()) < 0 ? k : "";
  };
  // Tag text follows the site's warning steps: "10 MIN" for NOW, "STARTS IN 5 MIN" for NEXT.
  const until = (s) => `in ${SW.fmtDuration(s, true)}`;
  const scheduleDay = () => (state.schedule && state.schedule.day) || (state.scheduleMeta && state.scheduleMeta.day) || "";

  function schedBlock(kind, label) {
    const b = { kind, title: h("div", { class: "sched-title" }), count: h("div", { class: "sched-count" }),
      line: h("div", { class: "sched-line" }), tag: h("span", { class: "sched-tag", hidden: true }) };
    // NOW also gets a progress bar (time used of the item's own planned length).
    if (kind === "now") b.bar = h("div", { class: "sched-bar", hidden: true, "aria-hidden": "true" }, h("div", { class: "sched-bar-fill" }));
    b.el = h("div", { class: `sched-block ${kind}` },
      h("div", { class: "sched-label" }, h("span", {}, label), b.tag), b.title, b.count, b.bar || null, b.line);
    return b;
  }

  // The snapshot and the "schedule" message carry only {show_id, revision}. The items (with
  // setlists) come from GET /api/schedule?stage=..., fetched only while the card is on this
  // dashboard and only when the show, the revision or the dashboard's stage has changed.
  const schedKey = (m) => (m ? `${m.show_id}|${m.revision}|${schedStage().toLowerCase()}` : "");
  async function syncSchedule() {
    if (!has("schedule") || !state.scheduleMeta) return;
    if (schedKey(state.scheduleMeta) === sched.loadedKey || sched.loading) return;
    sched.loading = true;
    let ok = false;
    try {
      const stage = schedStage();
      const r = await SW.api("GET", `/api/schedule${stage ? `?stage=${encodeURIComponent(stage)}` : ""}`);
      state.schedule = { show_id: r.show_id, day: r.day, revision: r.revision, items: r.items || [] };
      sched.loadedKey = schedKey(r);
      ok = true;
    } catch (_) {
      // Keep what is on screen. No retry here (a dropped network would loop): the next snapshot
      // (reconnect) or schedule message tries again.
    } finally { sched.loading = false; }
    if (has("schedule")) {
      CARDS.schedule.el.hidden = CARDS.schedule.empty();
      renderSchedule();
    }
    // Changed again while a successful request was in flight: fetch once more.
    if (ok && schedKey(state.scheduleMeta) !== sched.loadedKey) syncSchedule();
  }

  function stopScheduleTimer() { if (sched.timer) { clearInterval(sched.timer); sched.timer = null; } }

  function renderSchedule() {
    const card = $("schedule-card");
    sched.items = schedItems();
    const stage = schedStage();
    schedH2.textContent = stage ? `Schedule · ${stage}` : "Schedule";
    if (!has("schedule") || !sched.items.length) {
      stopScheduleTimer();
      sched.ui = null;
      card.classList.remove("sched-stale");
      card.replaceChildren(schedH2);
      return;
    }
    if (sched.selected !== null && !sched.items.some((it) => it.id === sched.selected)) sched.selected = null;
    const ui = {
      now: schedBlock("now", "Now"), next: schedBlock("next", "Next"),
      stripNow: h("span", { class: "sched-strip-now" }), stripRest: h("span", { class: "sched-strip-rest" }),
      stripTag: h("span", { class: "sched-tag", hidden: true }), stripNextTag: h("span", { class: "sched-tag", hidden: true }),
      stale: h("p", { class: "sched-stale-note muted", hidden: true }), stripMore: h("span", { class: "sched-strip-more", "aria-hidden": "true" }),
      setHead: h("h3", { class: "sched-set-head" }), setBody: h("div", { class: "sched-set-body" }),
      setBack: h("button", { type: "button", class: "touch", hidden: true, onclick: () => { sched.selected = null; tickSchedule(); } }, "Back to what's on now"),
    };
    ui.strip = h("button", { type: "button", class: "sched-strip", "aria-expanded": sched.open ? "true" : "false",
      onclick: () => {
        sched.open = !sched.open;
        ui.strip.setAttribute("aria-expanded", sched.open ? "true" : "false");
        card.classList.toggle("sched-open", sched.open);
        setText(ui.stripMore, sched.open ? "Less ▲" : "More ▼");
      } },
    h("span", { class: "sched-strip-l1" }, h("span", { class: "sched-label" }, "Now"), ui.stripNow, ui.stripTag),
    h("span", { class: "sched-strip-l2" }, ui.stripRest, ui.stripNextTag), ui.stripMore);
    ui.stripMore.textContent = sched.open ? "Less ▲" : "More ▼";
    const ends = SW.scheduleEnds(sched.items);
    sched.rows = sched.items.map((it, i) => {
      const tap = hasSetlist(it);
      const nowTag = h("span", { class: "sched-nowtag", hidden: true }, "NOW");
      const mark = h("span", { class: "cue-mark", "aria-hidden": "true" }, "·");
      const pick = () => { sched.selected = sched.selected === it.id ? null : it.id; tickSchedule(); };
      const el = h("li", tap ? { role: "button", tabindex: "0", title: "Show the setlist", onclick: pick,
        onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } } } : {},
      mark,
      h("span", { class: "sched-row-time cue-time" }, timeRange(it)),
      h("span", { class: "sched-row-title cue-title" }, it.title, it.stage && !stage ? h("span", { class: "muted" }, ` · ${it.stage}`) : null),
      nowTag,
      h("span", { class: "sched-row-kind cue-tag" }, tap ? "Setlist ›" : kindNote(it)));
      return { el, item: it, end: ends[i], nowTag, mark, tap };
    });
    sched.ui = ui;
    sched.setlistKey = "";
    card.classList.toggle("sched-open", sched.open);
    card.replaceChildren(schedH2, ui.stale, ui.strip,
      h("div", { class: "sched-body" },
        h("div", { class: "sched-now" }, ui.now.el, ui.next.el),
        h("div", { class: "sched-more" },
          h("div", { class: "sched-order" }, h("h3", {}, "Running order"), h("ol", {}, sched.rows.map((r) => r.el))),
          h("div", { class: "sched-setlist" }, ui.setHead, ui.setBody, ui.setBack))));
    tickSchedule();
    if (!sched.timer) sched.timer = setInterval(tickSchedule, 1000);
  }

  // Once a second: text and classes only (no rebuild).
  function tickSchedule() {
    const ui = sched.ui;
    if (!ui || !has("schedule")) { stopScheduleTimer(); return; }
    const now = serverNow();
    const nn = SW.scheduleNowNext(sched.items, now, "");
    const cur = nn.current, nxt = nn.next;
    const idle = { before: "Not started yet", between: "Nothing on now", over: "Show over", empty: "—" };
    if (nn.state === "over") idle.over = "Finished";

    // NOW: title and time left, but only when the item has its own end time. Without one (it
    // simply runs until the next item) there is no countdown at all: the "Started" line is enough.
    const ownEnd = !!cur && cur.planned_end !== null && cur.planned_end !== undefined && nn.currentEnd !== null;
    // Time left: neutral above 15 min, amber (with a "15 MIN" tag) at 15, orange ("5 MIN") at 5.
    const left = ownEnd ? nn.currentEnd - now : null;
    const lvl = SW.nowLevel(left);
    const step = SW.nowStep(left);
    // Optional slow pulse in the steps marked with ! in the Site card; NEXT never flashes.
    const fl = SW.nowFlash(left) ? " flash" : "";
    setClass(ui.now.el, `sched-block now${lvl ? ` lvl-${lvl}` : ""}${fl}`);
    for (const tag of [ui.now.tag, ui.stripTag]) {
      const text = step ? `${step} MIN` : "";
      setText(tag, text);
      setClass(tag, `sched-tag${lvl ? ` lvl-${lvl}` : ""}${fl}`);
      SW.slot(tag, !text);
    }
    setText(ui.now.title, cur ? cur.title : (idle[nn.state] || "—"));
    setText(ui.now.count, cur ? (ownEnd ? `${SW.fmtDuration(left, true)} left` : "") : "");
    setText(ui.now.line, cur ? `Started ${SW.fmtTime(cur.planned_start)}, ${SW.fmtDuration(now - cur.planned_start)} ago`
      + (ownEnd ? ` · ends ${SW.fmtTime(nn.currentEnd)}` : "") : "");
    // Progress bar: how much of the item's own planned time has gone. Hidden without an end time.
    const total = ownEnd ? nn.currentEnd - cur.planned_start : 0;
    const showBar = ownEnd && total > 0;
    SW.slot(ui.now.bar, !showBar);
    if (showBar) {
      const pct = Math.max(0, Math.min(100, ((now - cur.planned_start) / total) * 100));
      const fill = ui.now.bar.firstChild;
      const w = `${pct.toFixed(1)}%`;
      if (fill.style.width !== w) fill.style.width = w;
      setClass(ui.now.bar, `sched-bar${lvl ? ` lvl-${lvl}` : ""}${fl}`);
    }
    // NEXT: planned time and countdown; amber with a tag for the last 5 minutes. When it starts
    // as the current item ends, NOW's "left" is the same number, so NEXT shows only its start time.
    const sameMoment = ownEnd && !!nxt && Math.abs(nxt.planned_start - nn.currentEnd) <= 60;
    const nlvl = nxt && !sameMoment ? SW.nextLevel(nxt.planned_start - now) : "";
    setClass(ui.next.el, `sched-block next${nlvl ? ` lvl-${nlvl}` : ""}`);
    setText(ui.next.title, nxt ? nxt.title : "Nothing more today");
    setText(ui.next.count, nxt && !sameMoment ? until(nxt.planned_start - now) : "");
    setText(ui.next.line, nxt ? `Starts ${SW.fmtTime(nxt.planned_start)}` : "");
    for (const tag of [ui.next.tag, ui.stripNextTag]) {
      const text = nlvl ? `STARTS IN ${SW.nextStep()} MIN` : "";
      setText(tag, text);
      setClass(tag, `sched-tag${nlvl ? ` lvl-${nlvl}` : ""}`);
      SW.slot(tag, !text);
    }
    // Phone strip: one glance line, tap for the rest.
    setText(ui.stripNow, cur ? cur.title : (idle[nn.state] || "—"));
    const rest = [];
    if (nxt) rest.push(sameMoment ? `Next ${SW.fmtTime(nxt.planned_start)}` : `Next ${SW.fmtTime(nxt.planned_start)}, ${until(nxt.planned_start - now)}`);

    // The schedule belongs to a show day that has finished and the next day hasn't been started:
    // tablets and phones show one muted note, the wall hides the card (style.css).
    const old = nn.state === "over" && SW.scheduleIsOld(scheduleDay(), now);
    $("schedule-card").classList.toggle("sched-stale", old);
    ui.stale.hidden = !old;
    if (old) setText(ui.stale, `Yesterday's schedule (${SW.fmtDay(scheduleDay())}). Start the next day in Admin.`);
    setText(ui.stripRest, rest.join(" · "));

    // Running order: past items dimmed, the current one marked NOW (text as well as colour).
    let showId = sched.selected;
    if (showId === null) {
      const pick = [cur, nxt].filter(hasSetlist)[0] || cur || nxt;
      showId = pick ? pick.id : null;
    }
    for (const r of sched.rows) {
      const isNow = !!cur && r.item.id === cur.id;
      const past = !isNow && r.end !== null && r.end <= now;
      const isNext = !isNow && !!nxt && r.item.id === nxt.id;
      setClass(r.el, "sched-row cue-row" + (past ? " past cue-past" : "") + (isNow ? " now cue-now" : "") + (isNext ? " cue-next" : "")
        + (r.tap ? " tap" : "") + (r.tap && r.item.id === showId ? " sel" : ""));
      r.nowTag.hidden = !isNow;
      const m = isNow ? "▶" : isNext ? "→" : past ? "✓" : "·";
      if (r.mark.textContent !== m) r.mark.textContent = m;
    }
    // Setlist panel: the tapped act, else what's on now (or next) with a setlist.
    const item = showId === null ? null : sched.items.filter((it) => it.id === showId)[0] || null;
    const key = item ? `${item.id}|${sched.selected === null ? "auto" : "pick"}|${item === cur}|${item.title}|${item.setlist}` : "none";
    if (key !== sched.setlistKey) {
      sched.setlistKey = key;
      if (!item) {
        ui.setHead.textContent = "Setlist";
        ui.setBody.replaceChildren(h("p", { class: "muted" }, "Nothing on now. Tap an act in the running order to see its setlist."));
      } else {
        const when = sched.selected !== null ? "" : (item === cur ? "Now: " : "Next: ");
        ui.setHead.textContent = `${when}${item.title}`;
        ui.setBody.replaceChildren(hasSetlist(item) ? SW.renderMarkdown(item.setlist)
          : h("p", { class: "muted" }, "No setlist for this one."));
      }
      ui.setBack.hidden = sched.selected === null;
    }
  }

  // A card's own status (Online / Offline / Error) for an external source, in its header just before the logo.
  // SW.sourceStatusUi / SW.sourceStatus decide and draw it. On the wall it shows only when not online (CSS).
  function addCardStatus(card, source) {
    const ui = SW.sourceStatusUi(source);
    const h2 = card.querySelector("h2");
    const logo = h2 && h2.querySelector(".card-logo");
    if (h2) h2.insertBefore(ui.el, logo || null);
    return ui;
  }

  // --------------------------------------------------------- wall clock
  // The time of day from one source (this computer, or Ontime), shown as received (no zone
  // conversion), with a note when it differs from Stagewatch's own time. The server sends a
  // "wall_clock" message about once a second; the time is advanced locally from the
  // server-corrected clock between messages. A reading older than 3 s is shown dimmed and says
  // so, never as if it were live. What to show is decided in SW.wc.view (wallclock.js); the look
  // is this dashboard's clock_style (digits, ring or segments). The timer runs only while the
  // card is on this dashboard, ticking just after each shown second. Nothing here sounds or
  // raises an alarm.
  const wc = { timer: null, ui: null };
  function stopWallClockTimer() { if (wc.timer) { clearTimeout(wc.timer); wc.timer = null; } }

  // The look to draw: this dashboard's style, but a ring needs room, so a narrow card shows digits.
  function wallClockStyle(card) {
    const style = SW.wc.style(state.dash && state.dash.clock_style);
    return style === "ring" && card.clientWidth > 0 && card.clientWidth < SW.wc.RING_MIN_WIDTH ? "digits" : style;
  }

  function renderWallClock() {
    const card = $("wall-clock-card");
    const m = state.wallClock;
    if (!has("wall_clock") || !m) {
      stopWallClockTimer();
      card.hidden = true;
      return;
    }
    card.hidden = false;
    if (!wc.ui) {
      wc.ui = { head: card.querySelector("h2"), headText: h("span"), face: null, host: h("div", { class: "wc-host" }),
        logo: h("img", { class: "card-logo", src: "/static/ontime-badge.png", alt: "Ontime", width: "26", height: "26" }),
        date: h("p", { class: "wc-date" }), note: h("p", { class: "wc-note", role: "status" }) };
      wc.ui.head.replaceChildren(wc.ui.headText, h("span", { class: "grow" }), wc.ui.logo);
      card.replaceChildren(wc.ui.head, wc.ui.host, wc.ui.date, wc.ui.note);
      wc.ui.status = addCardStatus(card, "ontime");
    }
    wc.ui.headText.textContent = m.source === "pc" ? "Wall Clock" : `Wall Clock · ${m.label || "Ontime"}`;
    wc.ui.logo.hidden = m.source !== "ontime";      // the Ontime logo only while the time comes from Ontime
    wc.ui.status.el.hidden = m.source !== "ontime";   // so is the status: the PC's own clock is not an external source
    tickWallClock();
  }

  function tickWallClock() {
    stopWallClockTimer();
    const m = state.wallClock, ui = wc.ui;
    if (!m || !ui || !has("wall_clock")) return;
    const card = $("wall-clock-card");
    const now = serverNow();
    const v = SW.wc.view(m, now);
    const style = wallClockStyle(card);
    if (!ui.face || ui.face.style !== style) {      // first draw, or the style / width changed
      ui.face = SW.wc.createFace(style);
      ui.host.replaceChildren(ui.face.el);
    }
    ui.face.update(v, { reduced: SW.wc.reducedMotion() });
    if (m.source === "ontime") ui.status.update(SW.sourceInput.ontime(m, SW.wc.STALE_S), now);
    setText(ui.date, v.date);
    SW.slot(ui.date, !v.date);
    setText(ui.note, v.note);
    SW.slot(ui.note, !v.note);
    setClass(ui.note, `wc-note${v.level ? ` ${v.level}` : ""}`);
    setClass(card, `card${v.cls ? ` ${v.cls}` : ""} wc-style-${style}`);
    wc.timer = setTimeout(tickWallClock, SW.wc.nextDelayMs(m, now));
  }

  // ------------------------------------------------------- ontime timer
  // Ontime's own countdown, as the server received it (an "ontime_timer" message about once a
  // second). Between messages the countdown runs on from the server-corrected clock, re-anchored
  // to Ontime's value on every message. What to show is decided in SW.ot.view (ontimetimer.js).
  // The timer runs only while the card is on this dashboard and ticks just after each shown
  // second. Nothing here sounds, raises an alarm or sends anything to Ontime.
  const ot = { timer: null, ui: null };
  function stopOntimeTimer() { if (ot.timer) { clearTimeout(ot.timer); ot.timer = null; } }

  function renderOntimeTimer() {
    const card = $("ontime-timer-card");
    if (!has("ontime_timer") || !state.ontimeTimer) {
      stopOntimeTimer();
      card.hidden = true;
      return;
    }
    card.hidden = false;
    if (!ot.ui) {
      ot.ui = SW.ot.createUi();
      card.replaceChildren(card.querySelector("h2") || h("h2", {}, "Ontime Timer"), ...ot.ui.nodes);
      ot.status = addCardStatus(card, "ontime");
    }
    tickOntimeTimer();
  }

  function tickOntimeTimer() {
    stopOntimeTimer();
    const m = state.ontimeTimer, ui = ot.ui;
    if (!m || !ui || !has("ontime_timer")) return;
    const now = serverNow();
    const v = SW.ot.view(m, now);
    ui.update(v);
    ot.status.update(SW.sourceInput.ontime(m, SW.ot.STALE_S), now);
    setClass($("ontime-timer-card"), `card ${v.cls}${v.level ? ` lvl-${v.level}` : ""}${v.over ? " ot-over" : ""}`);
    ot.timer = setTimeout(tickOntimeTimer, SW.ot.nextDelayMs(m, now));
  }

  // ------------------------------------------------------ ontime rundown
  // Ontime's running-order position and ahead/behind, as the server received it (an
  // "ontime_rundown" message about once a second). What to show is decided in SW.rd.view
  // (ontimerundown.js). A one-second tick keeps the stale check honest between messages. Nothing
  // here sounds, raises an alarm or sends anything to Ontime.
  const rdc = { timer: null, ui: null };
  function stopOntimeRundown() { if (rdc.timer) { clearTimeout(rdc.timer); rdc.timer = null; } }

  function renderOntimeRundown() {
    const card = $("ontime-rundown-card");
    if (!has("ontime_rundown") || !state.ontimeRundown) {
      stopOntimeRundown();
      card.hidden = true;
      return;
    }
    card.hidden = false;
    if (!rdc.ui) {
      rdc.ui = SW.rd.createUi();
      card.replaceChildren(card.querySelector("h2") || h("h2", {}, "Ontime Rundown"), ...rdc.ui.nodes);
      rdc.status = addCardStatus(card, "ontime");
    }
    tickOntimeRundown();
  }

  function tickOntimeRundown() {
    stopOntimeRundown();
    const m = state.ontimeRundown, ui = rdc.ui;
    if (!m || !ui || !has("ontime_rundown")) return;
    const now = serverNow();
    const v = SW.rd.view(m, now, (state.dash && state.dash.layout) || "tablet");
    ui.update(v);
    rdc.status.update(SW.sourceInput.ontime(m, SW.rd.STALE_S), now);
    setClass($("ontime-rundown-card"), `card ${v.cls}${v.level ? ` rd-lvl-${v.level}` : ""}`);
    rdc.timer = setTimeout(tickOntimeRundown, 1000);
  }
  // ------------------------------------------------------ globcon levels
  // Live level meters from DirectOut GLOBCON for this dashboard's controller and 4 or 8 strips (a
  // "globcon_meters" message up to four times a second). What to show is decided in SW.gc.view
  // (globcon.js). A one-second tick keeps the frozen check and its age honest between messages. The
  // levels are shown as GLOBCON reports them; nothing here sounds, raises an alarm or sends anything
  // to GLOBCON.
  const gcc = { timer: null, ui: null };
  function stopGlobcon() { if (gcc.timer) { clearTimeout(gcc.timer); gcc.timer = null; } }
  const gcOpts = () => (state.dash && state.dash.globcon) || { controller: 1, strips: 8 };

  function renderGlobcon() {
    const card = $("globcon-card");
    if (!has("globcon_meters") || !state.globcon) {
      stopGlobcon();
      card.hidden = true;
      return;
    }
    card.hidden = false;
    if (!gcc.ui) {
      gcc.ui = SW.gc.createUi();
      card.replaceChildren(h("h2", {}, gcc.ui.head, h("span", { class: "grow" }), h("img", { class: "card-logo", src: "/static/directout-badge.png", alt: "DirectOut", width: "26", height: "26", onerror: (e) => { e.target.hidden = true; } })), ...gcc.ui.nodes);
      gcc.status = addCardStatus(card, "globcon");
    }
    tickGlobcon();
  }

  function tickGlobcon() {
    stopGlobcon();
    const ui = gcc.ui;
    if (!ui || !state.globcon || !has("globcon_meters")) return;
    const now = serverNow();
    const v = SW.gc.view(state.globcon, gcOpts(), now);
    ui.update(v);
    gcc.status.update(SW.sourceInput.globcon(state.globcon, SW.gc.pick(state.globcon, gcOpts().controller || 1), SW.gc.STALE_S), now);
    setClass($("globcon-card"), `card ${v.cls}`);
    gcc.timer = setTimeout(tickGlobcon, 1000);
  }

  // ------------------------------------------------------ RME interfaces
  // One tile per RME unit from the "rme_units" message (SW.rme, rme.js). The server sends a message when
  // something changes, so a one-minute-or-less tick keeps "last seen N ago" honest while a unit is away.
  // The tick runs only while the card is on this dashboard and a unit is offline or starting. Nothing here
  // sounds or sends anything to a unit.
  const rmeC = { timer: null, ui: null, at: 0, status: null };
  function stopRme() { if (rmeC.timer) { clearTimeout(rmeC.timer); rmeC.timer = null; } }
  function renderRme() {
    stopRme();
    const card = $("rme-card");
    const units = SW.rme.units(state.rme);
    if (!has("rme_units") || !units.length) { card.hidden = true; return; }
    card.hidden = false;
    if (!rmeC.ui) {
      rmeC.ui = SW.rme.createUi();
      card.append(...rmeC.ui.nodes);
      rmeC.status = addCardStatus(card, "rme");
    }
    const layout = (state.dash && state.dash.layout) || "tablet";
    const views = rmeC.ui.update(units, Math.max(0, Date.now() / 1000 - rmeC.at), layout);
    const now = serverNow();
    rmeC.status.update(SW.sourceInput.rme(units, now), now);
    setClass(card, `card ${SW.rme.cardClass(views)}`.trim());
    if (views.some((v) => v.dim || v.state === "starting")) rmeC.timer = setTimeout(renderRme, 10000);
  }

  // --------------------------------------------------------- equipment  // Readings from sensors whose role is Equipment (an amp rack, a power supply): never part of
  // any site average. Grouped by node; each reading shows its value with the unit, goes dim with
  // its age when stale, and carries the calibration asterisk. Text is set with textContent only.
  // The card hides itself while there is no equipment sensor, and updates in place.
  function equipmentGroups() {
    const byDev = {};
    // Sensors with their own card on this dashboard are not repeated here.
    const own = new Set(has("witmotion_tilt") ? SW.wm.sensorsOf(state.devices).map((d) => d.id) : []);
    for (const e of Object.values(state.entities)) {
      // RME units: only the core temperature here; the power supplies are On / Off words on the RME card.
      if (e.kind === "contact" && (state.devices[e.device_id] || {}).integration === "rme") continue;
      if (!SW.isEquipment(e) || e.kind === "signal_strength" || e.kind === "battery") continue;
      // Inclinometers: only their body temperature, and only when Admin asks for it.
      const wmPub = (state.devices[e.device_id] || {}).witmotion;
      if (wmPub && wmPub.temp_on_equipment) { if (e.kind !== "temperature") continue; }
      else if (own.has(e.device_id)) continue;
      const cat = state.devices[e.device_id] && state.devices[e.device_id].category;
      if (cat === "meter") continue;   // shown on the Power card
      if (cat === "host" && (e.kind !== "temperature" || has("pi_host"))) continue;   // the Raspberry Pi: only its temperature here; the rest is on its own card
      (byDev[e.device_id] = byDev[e.device_id] || []).push(e);
    }
    return Object.keys(byDev).map((id) => ({ dev: state.devices[id] || { id, name: id, status: "" }, ents: byDev[id] }))
      .sort((a, b) => (a.dev.name || "").localeCompare(b.dev.name || ""));
  }
  const eqValue = (e) => {
    if (SW.KIND_FMT[e.kind]) return fmt(e.kind, e.value);
    if (e.value === null || e.value === undefined || Number.isNaN(e.value)) return "—";
    return `${SW.num(e.value, Math.max(0, Math.min(3, e.decimals || 0)))}${e.unit ? " " + e.unit : ""}`;
  };
  function renderEquipment() {
    const card = $("equipment-card");
    if (!has("equipment")) { card.hidden = true; return; }
    const groups = equipmentGroups();
    card.hidden = groups.length === 0;
    $("equipment-body").replaceChildren(...groups.map(({ dev, ents }) =>
      h("div", { class: "eq-node" },
        h("h3", {}, dev.name || dev.id, dev.status ? (() => { const st = SW.deviceStatus(dev, state.now); return h("span", { class: `status ${st.cls}`, title: st.title }, st.text); })() : null),
        h("div", { class: "eq-grid" }, ...ents.map((e) =>
          h("div", { class: "tile eq-tile" + (e.stale ? " stale" : "") },
            h("div", { class: "label" }, e.name || e.id),
            h("div", { class: "value" }, eqValue(e), SW.hasOffset(e) ? offsetStar() : null),
            h("div", { class: "foot" }, e.stale ? `Old reading, ${SW.age(e.updated, state.now)}`
              : e.sleeping ? `Sleeping, last reading ${SW.age(e.updated, state.now)}` : SW.age(e.updated, state.now))))))));
  }

  // ------------------------------------------------------------ power
  // Power meters (Shelly): readings exactly as the meters reported them, in place (SW.pw, power.js).
  // The card hides itself while there is no meter. A meter that stops answering keeps its last readings
  // on screen, dimmed, with the word NO READING and their age. Nothing here changes a meter.
  const pwUi = { ui: null };
  function renderPower() {
    const card = $("power-card");
    if (!has("power")) { card.hidden = true; return; }
    const view = SW.pw.view(state.entities, state.devices, state.now);
    card.hidden = view.meters.length === 0;
    if (!pwUi.ui) {
      pwUi.ui = SW.pw.build($("power-body"));
      // The range notes live once under "About these figures", not under every meter.
      setText($("power-ranges"), Object.values(SW.pw.SWING).map((c) => c.note).join(". ") + ".");
    }
    pwUi.ui.update(view, state.entities);
    if (!pwUi.status) pwUi.status = addCardStatus(card, "shelly");
    pwUi.status.update(SW.sourceInput.shelly(view.meters.map((m) => ({ dev: (state.devices || {})[m.id],
      ents: Object.values(m.entities) }))), state.now);
    renderPowerHist(view);
  }

  // Power tree (SW.ptree, power-tree.js). The shape comes from /api/power-tree (names, roles, upstream links,
  // ratings: nothing private) and is fetched on load and every 30 s; the numbers are the dashboard's own
  // entities, including the ones the server calculates. The card stays visible with an empty-state note
  // pointing to Admin until a tree is set up.
  const ptUi = { ui: null, data: null, loading: false, at: 0, trend: null, trendAt: 0, trendLoading: false, minutes: SW.ptree.trendMinutes(localGet("sw.ptspan", 30)), view: localGet("sw.ptview", "list") === "diagram" ? "diagram" : "list", diagram: null };
  // Trend lines on the tiles: ONE request for every tile (the server's recorded watts, nothing new), every 45 s,
  // and not while the tab is hidden. The tick that already runs every 5 s drives it, so there is no timer to stop.
  async function loadPowerTrend() {
    if (!has("power_tree") || ptUi.trendLoading || document.hidden || !ptUi.data || !SW.ptree.inUse(ptUi.data)) return;
    ptUi.trendLoading = true;
    ptUi.trendAt = Date.now();
    const minutes = ptUi.minutes;
    try {
      const tr = await SW.api("GET", `/api/power-tree/trend?minutes=${minutes}`);
      if (tr && tr.series && minutes === ptUi.minutes) ptUi.trend = tr;
    } catch (_) { /* keep the lines on screen */ } finally { ptUi.trendLoading = false; }
    renderPowerTree();
  }
  async function loadPowerTree() {
    if (!has("power_tree") || ptUi.loading) return;
    ptUi.loading = true;
    ptUi.at = Date.now();
    try { ptUi.data = await SW.api("GET", "/api/power-tree"); } catch (_) { /* keep what is on screen */ } finally { ptUi.loading = false; }
    renderPowerTree();
  }
  function wirePowerTrendOption() {
    const sel = $("pt-span");
    if (!sel || sel.dataset.wired) return;
    sel.dataset.wired = "1";
    sel.value = String(ptUi.minutes);
    sel.onchange = () => {
      ptUi.minutes = SW.ptree.trendMinutes(sel.value);
      localSet("sw.ptspan", String(ptUi.minutes));
      ptUi.trend = null; ptUi.trendAt = 0;
      loadPowerTrend();
    };
  }
  // List or Diagram, remembered per viewer. The diagram redraws on a change of shape and (with live power on) every 5 s.
  function wirePowerView() {
    const bl = $("pt-view-list"), bd = $("pt-view-diagram");
    const apply = () => {
      const dg = ptUi.view === "diagram";
      $("power-tree-body").hidden = dg; $("power-tree-diagram").hidden = !dg;
      bl.setAttribute("aria-pressed", String(!dg)); bd.setAttribute("aria-pressed", String(dg));
    };
    if (!bl.dataset.wired) {
      bl.dataset.wired = "1";
      const pick = (v) => { ptUi.view = v; localSet("sw.ptview", v); apply(); renderPowerTree(); };
      bl.onclick = () => pick("list"); bd.onclick = () => pick("diagram");
    }
    apply();
  }
  function renderPowerTree() {
    const card = $("power-tree-card");
    if (!has("power_tree")) { card.hidden = true; return; }
    card.hidden = false;
    if (!ptUi.data) { if (Date.now() - ptUi.at > 5000) loadPowerTree(); return; }
    if (!ptUi.ui) ptUi.ui = SW.ptree.build($("power-tree-body"));
    ptUi.ui.update(ptUi.data, state.entities, state.devices, state.now, ptUi.trend);
    wirePowerView();
    if (ptUi.view === "diagram") {
      if (!ptUi.diagram) ptUi.diagram = SW.ptDiagram.mount($("power-tree-diagram"), { liveKey: "sw.ptdiagLive", fit: (state.dash && state.dash.layout) === "wall" ? true : null });
      ptUi.diagram.update(SW.ptree.inUse(ptUi.data) ? ptUi.data.nodes : [], { entities: state.entities, devices: state.devices, now: state.now,
        upsTypes: [], emptyText: "No power distribution set up. Add supplies, units and meters in Admin, under Power distribution." });
    }
    wirePowerTrendOption();
    if (!ptUi.trendAt) loadPowerTrend();
    if (!ptUi.status && SW.ptree.inUse(ptUi.data)) ptUi.status = addCardStatus(card, "shelly");
    if (ptUi.status) ptUi.status.update(SW.sourceInput.shelly(Object.keys(state.devices || {}).map((k) => state.devices[k])
      .filter((d) => d && d.category === "meter" && !d.derived).map((d) => ({ dev: d, ents: Object.values(state.entities).filter((e) => e.device_id === d.id) }))), state.now);
  }

  // This Raspberry Pi: the health of the computer Stagewatch runs on (SW.pi, pihost.js). Read only;
  // no addresses here (those are admin only). Warnings also go to the header's messages.
  const piUi = { status: null };
  function renderPiHost() {
    const card = $("pi_host-card");
    const dev = state.devices.pi_host;
    if (!has("pi_host") || !dev) { card.hidden = true; return; }
    card.hidden = false;
    const ents = SW.pi.entitiesOf(state.entities, "pi_host");
    $("pi-body").replaceChildren(h("p", { class: "muted pi-model" }, dev.model || "Raspberry Pi"),
      h("div", { class: "pi-grid" }, SW.pi.rows(ents, dev.pi).map((r) => h("div", { class: `pi-cell pi-${r.level}` + (r.unit === "" && r.value.length > 12 ? " pi-wide" : "") },
        h("div", { class: "pw-label" }, r.label), h("div", { class: "pi-value" }, r.value, r.unit ? h("span", { class: "unit" }, " " + r.unit) : null)))));
    if (!piUi.status) piUi.status = addCardStatus(card, "pihost");
    piUi.status.update(SW.sourceInput.smaart(dev, Object.values(ents)), state.now);
  }

  // FOH advice (SW.advice, advice.js): alignment drift and HF air loss, from the server's "advice" block
  // (snapshot and live feed). Advice only. Both cards update their elements in place and have no timers
  // of their own; they hide while nothing enabled uses them.
  const advUi = { drift: null, air: null, airSig: "", seg: null };
  const advLayout = () => (state.dash && state.dash.layout) || "tablet";
  // How old the readings are, in words, when the advice says they are stale ("" otherwise). The site
  // temperature's own age is used; the advice block has no timestamp of its own.
  function adviceAge(list) {
    if (!list.some((x) => x.stale)) return "";
    const e = state.entities["site.temperature"];
    return e && e.updated ? SW.age(e.updated, state.now) : "a while ago";
  }
  function renderDrift() {
    const card = $("drift-card");
    const adv = state.advice;
    if (!has("alignment_drift") || !adv || !adv.paths.length) { card.hidden = true; return; }
    card.hidden = false;
    if (!advUi.drift) advUi.drift = SW.advice.mountDrift($("drift-body"));
    advUi.drift(SW.advice.driftView(adv, advLayout(), adviceAge(adv.paths)));
  }
  function airChoice() { return localGet("sw.airloss", ""); }   // "" follows the system setting
  function renderAir() {
    const card = $("airloss-card");
    const adv = state.advice;
    if (!has("hf_air_loss") || !adv || !adv.positions.length) { card.hidden = true; return; }
    card.hidden = false;
    if (!advUi.air) {
      advUi.air = SW.advice.mountAir($("airloss-body"));
      setText($("airloss-hint"), SW.advice.RULE_OF_THUMB);
    }
    const wanted = airChoice() || adv.resolution;
    if (advUi.seg !== wanted) {   // the Octave / Third-octave pick, right-aligned in the card header
      advUi.seg = wanted;
      $("airloss-seg").replaceChildren(...[["octave", "Octave"], ["third", "Third-octave"]].map(([k, label]) =>
        h("button", { type: "button", class: "touch" + (wanted === k ? " on" : ""), "aria-pressed": wanted === k ? "true" : "false",
          onclick: () => { localSet("sw.airloss", k); renderAir(); } }, label)));
    }
    advUi.air(SW.advice.airView(adv, advLayout(), wanted, adviceAge(adv.positions)));
  }

  // WitMotion tilt sensors (SW.wm, witmotion.js). Advisory; a gap or stale reading is "No data", never the last angle.
  const wmUi = {};   // device id -> its status indicator
  // Tilt trend under each angle: the recorded smoothed tilt over the last 30 minutes, fetched at most every
  // 30 s. A gap breaks the line (nothing is joined across it). At least 1° of range is drawn full height,
  // so sensor noise stays a flat line. A guide to "has it moved?", not a measurement.
  const WM_TREND_S = 1800, WM_TREND_POINTS = 90, WM_TREND_MIN_DEG = 1;
  const wmHist = { at: 0, busy: false, d: {} };
  async function loadWmHistory(ids) {
    if (wmHist.busy || !ids.length || Date.now() - wmHist.at < 30000) return;
    wmHist.busy = true; wmHist.at = Date.now();
    const t1 = serverNow(), t0 = t1 - WM_TREND_S;
    let data = {};
    try {
      data = await SW.api("GET", `/api/history?entities=${encodeURIComponent(ids.join(","))}&since=${t0}&points=${WM_TREND_POINTS}`) || {};
    } catch (_) { data = {}; }
    wmHist.busy = false;
    const gap = Math.max(60, 3 * WM_TREND_S / WM_TREND_POINTS);
    wmHist.d = {};
    for (const id of ids) wmHist.d[id] = SW.trendPath(data[id], t0, t1, gap, TREND_W, TREND_H, WM_TREND_MIN_DEG);
    renderWitmotion();
  }
  function renderWitmotion() {
    const card = $("witmotion_tilt-card");
    const sensors = SW.wm.sensorsOf(state.devices);
    if (!has("witmotion_tilt") || !sensors.length) { card.hidden = true; return; }
    card.hidden = false;
    $("wm-title").textContent = SW.wm.title(sensors.length);
    // One compact tile per sensor; the setup small print for all of them sits once, under the tiles.
    // Each tile has its own Online / Offline / Error indicator (kept per sensor, so an open pop-out survives a redraw),
    // and goes grey while it has no data. It is never hidden, and never shows an old angle.
    const views = sensors.map((dev) => SW.wm.view(dev, state.entities));
    loadWmHistory(sensors.flatMap((d) => views[sensors.indexOf(d)].axes.map((a) => `${d.id}.${a.key}`)));
    const tiles = views.map((v, i) => {
      const dev = sensors[i];
      const ui = wmUi[dev.id] || (wmUi[dev.id] = SW.sourceStatusUi("witmotion"));
      const ents = ["tilt_roll", "tilt_pitch"].map((k) => state.entities[`${dev.id}.${k}`]).filter(Boolean);
      ui.update(Object.assign(SW.sourceInput.smaart(dev, ents), { source: "witmotion" }), state.now);
      return h("div", { class: "wm-tile" + (v.live ? "" : " wm-off") },
      h("div", { class: "wm-head" }, h("span", { class: "wm-name", title: v.name }, v.name),
        v.battery ? h("span", { class: "wm-batt" + (v.battery.low ? " wm-batt-low" : ""), title: "Sensor battery (approximate)" },
          h("span", { class: "wm-batt-icon", "aria-hidden": "true" }, h("span", { class: "wm-batt-fill", style: `width:${v.battery.pct}%` })),
          `${v.battery.low ? "Low " : ""}${v.battery.pct} %`) : null, ui.el),
      v.battery && v.battery.trend ? h("div", { class: "muted wm-trend", title: "A rough estimate from the battery voltage over the last hour" }, v.battery.trend) : null,
      h("div", { class: "wm-axes" }, v.axes.map((a) => h("div", { class: "wm-axis" + (a.level ? ` wm-l${a.level}` : "") },
        h("div", { class: "pw-label", title: a.hint }, a.label, v.accuracy ? h("span", { class: "wm-acc", title: "Datasheet accuracy" }, " " + v.accuracy) : null),
        h("div", { class: "wm-value" + (a.unit ? "" : " wm-nodata") }, a.text, a.unit ? h("span", { class: "unit" }, a.unit) : null),
        h("div", { class: "trend wm-spark", "aria-hidden": "true", title: "Last 30 minutes" }, wmHist.d[`${dev.id}.${a.key}`]
          ? SW.svg("svg", { viewBox: `0 0 ${TREND_W} ${TREND_H}`, preserveAspectRatio: "none" },
            SW.svg("path", { d: wmHist.d[`${dev.id}.${a.key}`], "vector-effect": "non-scaling-stroke" })) : null),
        a.levelText ? h("div", { class: "wm-limit" }, a.levelText) : null,
        a.target ? h("div", { class: "wm-target muted" }, a.target.band ? `${a.target.targetText} · ${a.target.diffText}` : a.target.targetText) : null,
        // With no data the band keeps its space (hidden), so the tile does not change size.
        a.target ? (a.target.band ? h("div", { class: `wm-band wm-t-${a.target.band}` }, a.target.bandText)
          : h("div", { class: "wm-band wm-hold", "aria-hidden": "true" }, "✓ On target")) : null))),
      h("div", { class: "muted wm-foot" }, v.live ? `${v.linkText} · ${v.smoothing}` : v.linkText),
      v.warning ? h("p", { class: "wm-warn" }, "▲ " + v.warning) : null);
    });
    for (const id of Object.keys(wmUi)) if (!sensors.some((d) => d.id === id)) delete wmUi[id];
    const about = views.filter((v) => v.setup || v.info).map((v) => h("p", { class: "wm-small" },
      h("strong", {}, v.name + ": "), [v.setup, v.info].filter(Boolean).join(" · ")));
    $("wm-body").replaceChildren(h("div", { class: "wm-grid" }, tiles),
      views.some((v) => v.axes.some((a) => a.target)) ? h("p", { class: "muted wm-small" }, "Target: a guide for setting the hang, not a safety check.") : null,
      about.length ? SW.about(...about) : null,
      h("p", { class: "muted wm-note" }, SW.wm.NOTE));
  }

  // History chart under the meters: pick what to show (one meter, one circuit or phase, everything on one
  // graph, or the totals), a measurement and a span. Lines are what was recorded (averaged only into time
  // buckets); a silence breaks the line; nothing is filled in. The choice is kept in this browser only.
  const pwSpanS = () => spanSeconds(state.pwSpan);
  const pwFields = () => SW.pw.METRICS.filter(([f]) => SW.pw.seriesFor(state.entities, state.devices, state.pwScope, f).length);
  function renderPowerHist(view) {
    const box = $("power-hist");
    const meters = view.meters;
    box.hidden = !meters.length;
    if (!meters.length) return;
    const scopes = SW.pw.chartScopes(state.entities, state.devices);
    const scope = SW.pw.pickScope(scopes, state.pwScope);
    if (scope !== state.pwScope) { state.pwScope = scope; state.pwHistory = {}; }
    const sel = $("power-meter");
    const key = scopes.map((x) => x.value + x.label + x.group).join("|");
    if (sel.dataset.key !== key) {
      sel.dataset.key = key;
      const groups = {};
      const opts = [];
      for (const x of scopes) {
        const o = h("option", { value: x.value }, x.label);
        if (!x.group) { opts.push(o); continue; }
        if (!groups[x.group]) { groups[x.group] = h("optgroup", { label: x.group }); opts.push(groups[x.group]); }
        groups[x.group].append(o);
      }
      sel.replaceChildren(...opts);
      sel.onchange = () => { state.pwScope = sel.value; localSet("sw.pwscope", sel.value); state.pwHistory = {}; loadPowerHistory(); renderPowerHist(view); };
    }
    sel.value = state.pwScope;
    sel.hidden = scopes.length < 2;
    const totals = state.pwScope === "totals";
    $("power-totals").hidden = !totals;
    $("power-chart-box").hidden = totals;
    $("power-metric-seg").hidden = totals;
    $("power-span-seg").hidden = totals;
    if (totals) {
      SW.pw.renderTotals($("power-totals"), SW.pw.phaseTotals(state.devices, view.meters, state.entities));
      setText($("power-chart-note"), "Each line's watts as the meters report them now. A line with a circuit that is not reading shows no figure, never a partial one.");
      return;
    }
    const metrics = pwFields();
    if (metrics.length && !metrics.some(([f]) => f === state.pwMetric)) state.pwMetric = metrics[0][0];
    $("power-metric-seg").replaceChildren(...metrics.map(([f, label]) =>
      h("button", { class: f === state.pwMetric ? "on" : "", onclick: () => { state.pwMetric = f; localSet("sw.pwmetric", f); loadPowerHistory(); renderPowerHist(view); } }, label)));
    $("power-span-seg").replaceChildren(...SPANS.map(([s, label]) =>
      h("button", { class: s === state.pwSpan ? "on" : "", onclick: () => { state.pwSpan = s; localSet("sw.pwspan", String(s)); state.pwHistory = {}; loadPowerHistory(); renderPowerHist(view); } }, label)));
    if (!pwHist.loaded) { pwHist.loaded = true; loadPowerHistory(); }
    updatePowerChart();
  }
  const pwHist = { loaded: false, at: 0 };
  async function loadPowerHistory() {
    if (!has("power") || !state.pwScope || state.pwScope === "totals") return;
    const lines = SW.pw.chartPlan(state.entities, state.devices, state.pwScope, state.pwMetric).lines;
    if (!lines.length) return;
    const span = pwSpanS(), since = serverNow() - span;
    pwHist.at = Date.now();
    try {
      state.pwHistory = await SW.api("GET", `/api/history?entities=${encodeURIComponent(lines.map((l) => l.id).join(","))}&since=${since}&points=${SW.pw.POINTS}`);
    } catch (_) { state.pwHistory = {}; }
    updatePowerChart();
  }
  function updatePowerChart() {
    if (!has("power") || $("power-hist").hidden || state.pwScope === "totals") return;
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    const plan = SW.pw.chartPlan(state.entities, state.devices, state.pwScope, state.pwMetric), lines = plan.lines, span = pwSpanS(), now = serverNow();
    const gap = SW.pw.gapSeconds(span / SW.pw.POINTS);
    const dec = lines.length ? lines[0].decimals : 1;
    pwChart.format = (v) => SW.num(v, Math.min(2, dec));
    // Colour (3) and dash (3) together tell up to nine lines apart; the legend names every one.
    pwChart.series = lines.map((l) => ({ label: l.label, color: css(`--spl-${l.colour + 1}`), width: 2, gap, dash: SW.spl.LINE_STYLES[l.dash], points: SW.pw.smooth(state.pwHistory[l.id], SW.pw.smoothWindow(span, state.pwMetric), gap) }));
    pwChart.markers = SW.visibleMarkers(state.markers, false).filter((m) => m.source === "shelly" || m.source === "schedule").map((m) => ({ ...m, selected: false }));
    const sc = SW.pw.chartScale(state.pwMetric);
    pwChart.fixedRange = sc.fixed; pwChart.defaultRange = sc.def;
    pwChart.range = [now - span, now];
    pwChart.draw();
    $("power-legend").replaceChildren(...lines.map((l) => h("span", {}, h("i", { class: `spl-key spl-line-${l.dash}`, style: `border-top-color:${css(`--spl-${l.colour + 1}`)}` }), `${l.label}${l.unit ? " (" + l.unit + ")" : ""}`)));
    setText($("power-chart-note"), [plan.note, SW.pw.chartNote(span, SW.pw.POINTS, state.pwMetric)].filter(Boolean).join(" "));
  }

  // ------------------------------------------------------- sound level
  // Up to three values from Smaart (A Slow, C Slow, LAeq 15 min ...), exactly as Smaart reported
  // them, and their timeline. Nothing is averaged or smoothed here: the chart draws the points the
  // server gives (the last reading in each interval on long views) and breaks the line wherever
  // there was no reading. A missing value is a dash. The rows are built once and then only their
  // text changes. The card hides itself while no values are set up.
  const SPL_POINTS = 500;
  const splUi = { rows: {}, key: "" };
  const splSpanS = () => spanSeconds(state.splSpan);
  function splBuildRows(ents) {
    splUi.rows = {};
    $("spl-rows").replaceChildren(...ents.map((e, i) => {
      const r = {
        chip: h("i", { class: `spl-chip spl-line-${i}`, "aria-hidden": "true" }),
        name: h("span", {}), value: h("span", { class: "spl-num" }), unit: h("span", { class: "unit" }, "dB"),
        loc: h("div", { class: "foot spl-loc" }),
        sub: h("div", { class: "foot spl-sub" }), note: h("div", { class: "foot spl-note-line" }),
        limit: h("div", { class: "foot spl-limit-line" }),
      };
      r.chip.style.borderTopColor = `var(--spl-${(i % 3) + 1})`;
      r.el = h("div", { class: "tile spl-tile" }, h("div", { class: "label" }, r.chip, r.name),
        r.loc, h("div", { class: "value" }, r.value, r.unit), r.limit, r.sub, r.note);
      splUi.rows[e.id] = r;
      return r.el;
    }));
  }
  function renderSpl() {
    const card = $("spl-card");
    if (!has("spl_live")) { card.hidden = true; return; }
    const ents = SW.spl.entities(state.entities);
    card.hidden = ents.length === 0;
    if (!ents.length) return;
    const key = ents.map((e) => e.id).join(",");
    if (key !== splUi.key) { splUi.key = key; splBuildRows(ents); loadSplHistory(); renderSplSpan(); }
    const dev = state.devices[ents[0].device_id];
    for (const e of ents) {
      const v = SW.spl.view(e, dev, state.now), r = splUi.rows[e.id];
      setText(r.name, SW.spl.label(e));
      const loc = SW.spl.tileLocation(e, ents);
      setText(r.loc, loc);
      SW.slot(r.loc, !loc);
      setText(r.value, v.text);
      setText(r.sub, v.sub);
      setText(r.note, v.note);
      setClass(r.el, `tile spl-tile spl-${v.state}`);
      splLimitLine(r.limit, e);
    }
    setText($("spl-title"), SW.spl.title(ents));
    if (!splUi.status) { splUi.status = SW.sourceStatusUi("smaart"); const badge = card.querySelector(".spl-badge"); badge.parentNode.insertBefore(splUi.status.el, badge); }
    splUi.status.update(SW.sourceInput.smaart(dev, ents), state.now);
    const inp = $("spl-input"), line = SW.spl.inputLine(dev);
    SW.slot(inp, !line);
    setText(inp, line);
    // The state of the link to Smaart, in words (never colour alone).
    const down = dev && (dev.status === "missing" || dev.status === "fault" || dev.status === "compromised");
    const status = $("spl-status");
    SW.slot(status, !down);
    setText(status, down ? `Smaart: ${dev.status_detail || dev.status}. Readings are missing until it comes back.` : "");
    updateSplChart();
  }
  // Sound level limits: fetched from /api/spl-limits (on load, on "spl_limits_changed" and once a
  // minute); the live value beside a limit is the dashboard's own entity, compared as received.
  const splLim = { data: null, loading: false, at: 0 };
  const wantLimits = () => has("spl_limits") || has("spl_live");
  async function loadSplLimits() {
    if (!wantLimits() || splLim.loading) return;
    splLim.loading = true;
    splLim.at = Date.now();
    try {
      const stage = schedStage();
      splLim.data = await SW.api("GET", `/api/spl-limits${stage ? `?stage=${encodeURIComponent(stage)}` : ""}`);
    } catch (_) { /* keep what is on screen; the next message or minute tries again */ } finally { splLim.loading = false; }
    if (has("spl_limits")) renderSplLimits();
    if (has("spl_live")) { renderSpl(); updateSplChart(); }
  }
  function splLimitLine(el, e) {
    const L = SW.splLimits, rows = L.forEntity(splLim.data, e.id);
    el.hidden = !rows.length;
    if (!rows.length) { setText(el, ""); return; }
    let cls = "";
    const parts = rows.map((c) => {
      const st = L.status(c, e);
      if (st === "over") cls = c.kind === "limit" ? " lim-over" : (cls || " lim-guide-over");
      return L.line(c, SW.fmtTime) + (st === "over" ? (c.kind === "limit" ? " · ▲ OVER" : " · ▲ over guidance") : st === "no_reading" ? " · No reading" : "");
    });
    setText(el, parts.join(" | "));
    setClass(el, `foot spl-limit-line${cls}`);
  }
  function renderSplLimits() {
    const card = $("spl-limits-card"), d = splLim.data, L = SW.splLimits;
    if (!has("spl_limits")) { card.hidden = true; return; }
    const layout = (state.dash && state.dash.layout) || "tablet";
    const none = !d || !d.has_limits;
    card.hidden = none && layout !== "tablet";      // phone and wall: hidden while no limits are entered
    $("spl-limits-empty").hidden = !none;
    $("spl-limits-main").hidden = none;
    if (none) return;
    // One tile per limit in force (licence limits first). The live value is on the Sound level card;
    // here only the word OVER / No reading says how it compares.
    const now = serverNow(), cur = d.current || [];
    const tiles = cur.map((c) => {
      const ent = c.reading && c.reading.entity_id ? state.entities[c.reading.entity_id] : null;
      const st = L.status(c, ent);
      const word = st === "over" ? (c.kind === "limit" ? "▲ OVER the limit" : "▲ over guidance") : (L.WORDS[st] || "At or under");
      return h("div", { class: `tile spl-lim-tile lim-${st}${c.kind === "guidance" ? " lim-guidance" : ""}` },
        h("div", { class: "label" }, `${c.kind === "guidance" ? "Guidance" : "Limit"}: ${c.label || c.metric}`),
        h("div", { class: "value" }, h("span", { class: "spl-num" }, L.db(c.value_db)), h("span", { class: "unit" }, L.unit(c.metric))),
        h("div", { class: "foot" }, [c.label ? c.metric : "", c.next_ts ? `until ${SW.fmtTime(c.next_ts)}` : "", word].filter(Boolean).join(" · ")));
    });
    $("spl-limits-head").replaceChildren(...(tiles.length ? tiles : [h("div", { class: "tile spl-lim-tile" }, h("div", { class: "label" }, "No limit now"), h("div", { class: "value" }, "—"))]));
    const over = cur.some((c) => L.status(c, c.reading && state.entities[c.reading.entity_id]) === "over" && c.kind === "limit");
    setClass(card, `card spl-limits-card${over ? " lim-over" : ""}`);
    const nxt = (d.upcoming || []).slice(0, 3);
    $("spl-limits-next").replaceChildren(...(nxt.length ? nxt.map((u) => h("li", {},
      `Next: ${SW.fmtTime(u.ts)} → ${u.to_db === null ? "no limit" : `${L.db(u.to_db)} ${L.unit(u.metric)}`} (${u.metric}), ${L.countdown(u.ts, now)}`))
      : [h("li", {}, "No more changes today")]), ...[1, 2, 3].slice(Math.max(nxt.length, 1)).map(() => h("li", { "aria-hidden": "true" }, " ")));   // always 3 lines: the card keeps its size
    const cf = $("spl-limits-curfew");
    SW.slot(cf, !d.curfew_ts);
    setText(cf, d.curfew_ts ? `Curfew ${SW.fmtTime(d.curfew_ts)} (${L.countdown(d.curfew_ts, now)})` : "");
    const notes = $("spl-limits-notes");
    notes.hidden = !d.public_notes;
    setText(notes, d.public_notes || "");
    setText($("spl-limits-foot"), L.footer(d, SW.fmtTime));
    setText($("spl-limits-disclaimer"), `${d.disclaimer} ${d.period_note}`);
  }

  function renderSplSpan() {
    $("spl-span-seg").replaceChildren(...SPANS.map(([s, label]) =>
      h("button", { class: s === state.splSpan ? "on" : "", onclick: () => { state.splSpan = s; localSet("sw.splspan", String(s)); renderSplSpan(); loadSplHistory(); } }, label)));
  }
  function updateSplChart() {
    if (!has("spl_live") || $("spl-card").hidden) return;
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    const ents = SW.spl.entities(state.entities), span = splSpanS(), now = serverNow();
    const gap = SW.spl.gapSeconds(span / SPL_POINTS);
    splChart.series = ents.map((e, i) => ({
      label: SW.spl.label(e), color: css(`--spl-${(i % 3) + 1}`), width: 2, gap, dash: SW.spl.LINE_STYLES[i % 3],
      points: state.splHistory[e.id] || [],
    }));
    // Limits in force now, as flat lines from when each began (or the window start) to now, in the
    // colour of the value they apply to: licence limits in warning colour, guidance dotted.
    const lims = ((splLim.data && splLim.data.current) || []).filter((c) => c.value_db !== null && c.reading);
    for (const c of lims) {
      const i = ents.findIndex((e) => e.id === c.reading.entity_id);
      if (i < 0) continue;
      const from = Math.max(now - span, c.since || now - span);
      splChart.series.push({
        label: `${c.kind === "guidance" ? "Guidance" : "Limit"} ${c.label || c.metric}`,
        color: c.kind === "guidance" ? css(`--spl-${(i % 3) + 1}`) : css("--warn"), width: c.kind === "guidance" ? 1 : 2,
        dash: c.kind === "guidance" ? [2, 4] : [10, 5], points: [[from, c.value_db], [now, c.value_db]],
      });
    }
    splChart.markers = SW.visibleMarkers(state.markers, false).filter((m) => m.source === "spl" || m.source === "schedule").map((m) => ({ ...m, selected: false }));
    splChart.range = [now - span, now];
    // The vertical range comes from the Smaart device's public settings: automatic (fits the readings,
    // tidy values, steady) or custom (exactly min..max; readings outside are drawn at the edge, marked).
    const cr = SW.spl.chartRange(state.devices[ents[0].device_id]), pts = splChart.series.map((x) => x.points);
    let fr = null;
    if (cr.mode === "custom") fr = [cr.min, cr.max];
    else { fr = SW.spl.autoRange(pts, now - span, now, state.splAxis); state.splAxis = fr; }
    splChart.fixedRange = fr;
    splChart.defaultRange = null;
    const rangeNote = cr.mode === "custom" ? SW.spl.rangeNote(SW.spl.clipFlags(pts, now - span, now, fr)) : "";
    splChart.draw();
    $("spl-legend").replaceChildren(...ents.map((e, i) => h("span", {}, h("i", { class: `spl-key spl-line-${i % 3}`, style: `border-top-color:${css(`--spl-${(i % 3) + 1}`)}` }),
      `${SW.spl.label(e)} (${SW.spl.LINE_NAMES[i % 3]})`)));
    if (lims.some((c) => c.kind !== "guidance" && ents.some((e) => e.id === c.reading.entity_id)))
      $("spl-legend").append(h("span", {}, h("i", { class: "spl-key", style: `border-top:2px dashed ${css("--warn")}` }), "Limit now"));
    setText($("spl-note"), `${SW.spl.chartNote(span, SPL_POINTS)} Values are as Smaart reports them; this is not a compliance record.${rangeNote ? ` ${rangeNote}` : ""}`);
  }
  async function loadSplHistory() {
    if (!has("spl_live")) return;
    const ents = SW.spl.entities(state.entities);
    if (!ents.length) return;
    const span = splSpanS(), since = serverNow() - span;
    try {
      state.splHistory = await SW.api("GET", `/api/history?entities=${encodeURIComponent(ents.map((e) => e.id).join(","))}&since=${since}&points=${SPL_POINTS}`);
    } catch (_) { state.splHistory = {}; }
    updateSplChart();
  }
  // A live update: add the point (or a break when the value went missing), keep the window tidy.
  function splOnStates(list) {
    let any = false;
    for (const e of list) {
      if (e.kind !== "sound_level" || !e.updated) continue;
      any = true;
      const pts = state.splHistory[e.id] || (state.splHistory[e.id] = []);
      if (e.value === null) { if (pts.length && pts[pts.length - 1][1] !== null) pts.push([e.updated, null]); }
      else if (!pts.length || e.updated > pts[pts.length - 1][0]) pts.push([e.updated, e.value]);
      const oldest = serverNow() - splSpanS() - 60;
      while (pts.length > 1 && pts[1][0] < oldest) pts.shift();
    }
    return any;
  }

  // --------------------------------------------------------- barometer
  // Sea-level pressure, the 3-hour change and a rough outlook, from site.baro (Pa, a state, a
  // tendency word and an outlook letter; SW.baro.view decides the words). Advisory only. The dial
  // (tablet, phone) is drawn once and only its needle and two texts change; the wall shows big
  // numbers instead (a needle cannot be read at 5 m), and hides the card when there is no sensor.
  // Old readings are dimmed and say how old they are. Nothing here sounds or flashes.
  const baroUi = { ui: null, letter: null };
  const isWall = () => !!state.dash && state.dash.layout === "wall";
  function renderBarometer() {
    const card = $("barometer-card");
    if (!has("barometer")) { card.hidden = true; return; }
    const v = SW.baro.view(state.site.baro, state.now);
    card.hidden = isWall() && v.state === "no_sensor";
    if (!baroUi.ui) {
      const u = baroUi.ui = {
        dial: SW.baro.createDial(),
        flag: h("div", { class: "baro-flag", role: "status", hidden: true }, h("strong", { class: "baro-flag-text" }), h("span", { class: "baro-flag-line" })),
        msl: h("span", { class: "baro-num" }), off: h("span", { class: "baro-off" }),
        arrow: h("span", { class: "baro-arrow", "aria-hidden": "true" }), word: h("span", { class: "baro-tword" }), figure: h("span", { class: "baro-figure" }),
        iconHost: h("span", { class: "baro-icon-host" }), outText: h("span", { class: "baro-outlook-text" }), outWay: h("span", { class: "baro-way" }),
        notes: h("p", { class: "baro-notes", role: "status" }), info: h("p", { class: "baro-info muted" }), temp: h("p", { class: "baro-temp muted" }),
      };
      u.tend = h("div", { class: "baro-tend" }, u.arrow, u.word, u.figure);
      u.outlook = h("div", { class: "baro-outlook" }, u.iconHost, u.outText, u.outWay);
      u.hint = h("div", { class: "baro-hint" }, h("div", { class: "baro-label" }, "Pressure hint"), u.outlook);
      u.figures = h("div", { class: "baro-figures" },
        h("div", { class: "baro-label" }, "Sea-level pressure"),
        h("div", { class: "baro-value" }, u.msl, h("span", { class: "baro-unit" }, "hPa")), u.off, u.tend, u.hint);
      u.body = h("div", { class: "baro-body" }, h("div", { class: "baro-dial-host" }, u.dial.el), u.figures);
      card.append(u.flag, u.notes, u.body, u.info, h("p", { class: "baro-foot muted" }, SW.baro.FOOTER),
        SW.about(h("p", { class: "baro-disclaimer muted" }, SW.baro.DISCLAIMER), u.temp));
    }
    const u = baroUi.ui;
    setClass(card, `card baro baro-${v.state}${v.dim ? " dim" : ""}`);
    u.dial.update(v);
    setText(u.msl, v.msl);
    setText(u.off, v.offScale ? "▲ Off the scale" : "");
    SW.slot(u.off, !v.offScale);
    SW.slot(u.flag, !v.flag);
    if (v.flag) {
      setClass(u.flag, `baro-flag lvl-${v.flag.level}`);
      setText(u.flag.firstChild, v.flag.text);
      setText(u.flag.lastChild, v.flag.line);
    }
    SW.slot(u.tend, !v.tendency);
    if (v.tendency) {
      setClass(u.tend, `baro-tend${v.tendency.level ? ` lvl-${v.tendency.level}` : ""}`);
      setText(u.arrow, v.tendency.arrow);
      setText(u.word, v.tendency.word);
      setText(u.figure, v.tendency.figure);
    }
    SW.slot(u.hint, !v.outlook);
    if (v.outlook) {
      if (baroUi.letter !== v.outlook.letter) {
        baroUi.letter = v.outlook.letter;
        u.iconHost.replaceChildren(v.outlook.icon ? SW.baro.icon(v.outlook.icon) : "");
      }
      setText(u.outText, v.outlook.text);
      setText(u.outWay, v.outlook.arrow ? `${v.outlook.arrow} ${v.outlook.wayText}` : "");
    }
    setText(u.notes, v.notes.join(" · "));
    u.notes.hidden = !v.notes.length;
    setText(u.info, v.approx);
    SW.slot(u.info, !v.approx);
    setText(u.temp, v.tempNote);      // tablets only (style.css)
    u.temp.hidden = !v.tempNote;
  }

  // --------------------------------------------------------- condensation risk
  // Site dew point and the margin between air temperature and dew point (SW.cond picks the band and
  // the words). Site readings only; equipment never reaches them. Advisory: no alarm, sound or marker.
  // The optional kit temperature stays in this browser (localStorage) and is never sent anywhere.
  // The DOM is built once and updated in place so the typing box keeps focus; the wall hides the box.
  const KIT_KEY = "stagewatch.condensation.kit";
  const condUi = { ui: null };
  function renderCondensation() {
    const card = $("condensation-card");
    if (!has("condensation")) { card.hidden = true; return; }
    card.hidden = false;
    if (!condUi.ui) {
      const u = condUi.ui = {
        word: h("div", { class: "cond-word", role: "status" }),
        air: h("span", { class: "cond-num" }), dew: h("span", { class: "cond-num" }), margin: h("span", { class: "cond-num" }),
        line: h("p", { class: "cond-line" }),
        input: h("input", { type: "text", inputmode: "decimal", class: "cond-kit-input", id: "cond-kit", maxlength: "7", autocomplete: "off", "aria-describedby": "cond-kit-result" }),
        result: h("p", { class: "cond-kit-result", id: "cond-kit-result", role: "status" }),
      };
      const fig = (label, num) => h("div", { class: "cond-fig" }, h("div", { class: "cond-label" }, label), h("div", { class: "cond-value" }, num, h("span", { class: "unit" }, "°C")));
      u.input.value = localGet(KIT_KEY, "");
      u.input.addEventListener("input", () => { localSet(KIT_KEY, u.input.value.trim()); renderCondensation(); });
      card.append(u.word, h("div", { class: "cond-figs" }, fig("Air", u.air), fig("Dew point", u.dew), fig("Margin", u.margin)), u.line,
        h("div", { class: "cond-kit only-interactive" }, h("label", { for: "cond-kit" }, "Kit temperature (°C)"), u.input, u.result),
        h("p", { class: "cond-foot muted" }, SW.cond.FOOT), SW.about(h("p", { class: "muted" }, SW.cond.ABOUT)));
    }
    const u = condUi.ui;
    const air = state.entities["site.temperature"], dew = state.entities["site.dew_point"];
    const v = SW.cond.view(air ? air.value : null, dew ? dew.value : null);
    setClass(card, `card cond cond-${v.state}`);
    setText(u.word, v.headline);
    setText(u.air, v.air === null ? "—" : SW.num(v.air, 1));
    setText(u.dew, v.dew === null ? "—" : SW.num(v.dew, 1));
    setText(u.margin, v.margin === null ? "—" : SW.num(v.margin, 1));
    setText(u.line, v.line);
    const kit = SW.cond.parseKit(u.input.value);
    const kv = SW.cond.kitView(kit, v.dew);
    setClass(u.result, `cond-kit-result cond-kit-${kv.level}`);
    setText(u.result, u.input.value.trim() && kit === null ? "Type a temperature, for example 4" : kv.text);
  }

  // ----------------------------------------------------------- documents
  // Event plans shared with this dashboard (plans/policy-documents.md). The list is fetched, never
  // in the snapshot; a "documents_changed" message (no data) makes it fetch again. Text documents
  // open in a reader on this page; PDFs open in a new tab with the browser's own viewer. The wall
  // shows titles only. Document text is never kept in localStorage.
  const DOC_CATS = { lightning: "Lightning", wind: "Wind", heat: "Heat", evacuation: "Evacuation", electrical: "Electrical",
    site_rules: "Site rules", medical: "Medical", noise: "Noise", other: "Other" };
  const DOC_SIZE_KEY = "stagewatch.docs.size";
  let docList = null, docTicket = 0, docFailed = false;
  async function loadDocuments() {
    if (!has("documents")) return;
    const t = ++docTicket;
    try {
      const r = await SW.api("GET", `/api/documents?dashboard=${encodeURIComponent(slug)}`);
      if (t !== docTicket) return;
      docList = Array.isArray(r.documents) ? r.documents : [];
      docFailed = false;
    } catch (_) { docFailed = true; if (docList === null) docList = []; }
    CARDS.documents.el.hidden = CARDS.documents.empty();
    renderDocuments();
  }
  const docAge = (t) => { const s = serverNow() - t; return s >= 172800 ? `${Math.floor(s / 86400)} days ago` : SW.age(t, serverNow()); };
  const docUrl = (d, file) => `/api/documents/${encodeURIComponent(d.id)}${file ? "/file" : ""}?dashboard=${encodeURIComponent(slug)}`;
  function renderDocuments() {
    const body = $("documents-body");
    if (!body || docList === null) return;
    if (docFailed && !docList.length) {
      body.replaceChildren(h("p", { class: "muted" }, "Could not load the list. It tries again when the connection is back."));
      return;
    }
    if (!docList.length) {
      body.replaceChildren(h("p", { class: "muted" }, "No plans shared with this screen. Admin → Documents to share one."));
      return;
    }
    if (isWall()) {
      body.replaceChildren(h("p", { class: "doc-wall" }, docList.map((d) => d.title).join(" · ")),
        h("p", { class: "muted" }, "Read on a tablet or phone."));
      return;
    }
    body.replaceChildren(h("ul", { class: "doc-list" }, docList.map((d) => h("li", {},
      d.kind === "pdf"
        ? h("a", { class: "doc-open touch", href: docUrl(d, true), target: "_blank", rel: "noopener noreferrer" },
          h("strong", {}, d.title), h("span", { class: "muted" }, ` ${DOC_CATS[d.category] || "Other"} · PDF · updated ${docAge(d.updated)} ↗`))
        : h("button", { class: "doc-open touch", type: "button", onclick: () => openDocument(d) },
          h("strong", {}, d.title), h("span", { class: "muted" }, ` ${DOC_CATS[d.category] || "Other"} · updated ${docAge(d.updated)}`))))));
  }
  async function openDocument(d) {
    let r;
    try { r = await SW.api("GET", docUrl(d, false)); } catch (err) { SW.toast(err.message || "Could not open the document", true); return; }
    let size = 18;
    try { size = Math.min(32, Math.max(14, Number(localStorage.getItem(DOC_SIZE_KEY)) || 18)); } catch (_) { /* default size */ }
    const text = h("div", { class: "doc-text" }, r.kind === "md" ? SW.renderMarkdown(r.text) : h("pre", { class: "doc-plain" }, r.text));
    const setSize = (n) => {
      size = Math.min(32, Math.max(14, n));
      text.style.fontSize = `${size}px`;
      try { localStorage.setItem(DOC_SIZE_KEY, String(size)); } catch (_) { /* not remembered */ }
    };
    setSize(size);
    const close = () => { box.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const box = h("div", { class: "doc-reader", role: "dialog", "aria-modal": "true", "aria-label": r.title },
      h("div", { class: "doc-head" },
        h("div", { class: "doc-title" }, h("strong", {}, r.title),
          h("span", { class: "muted" }, ` Updated ${docAge(r.updated)}. Check you have the latest version.`)),
        h("button", { class: "touch", type: "button", "aria-label": "Smaller text", onclick: () => setSize(size - 2) }, "A−"),
        h("button", { class: "touch", type: "button", "aria-label": "Larger text", onclick: () => setSize(size + 2) }, "A+"),
        h("button", { class: "touch primary", type: "button", onclick: close }, "Close")),
      text);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  }

  // ----------------------------------------------------------- contacts
  // Onsite contacts for the current event (plans/onsite-contacts.md). Fetched, never in the
  // snapshot; "contacts_changed" (no data) makes it fetch again. Phone numbers come only when the
  // server allows them for this screen; with a reveal PIN they are fetched on request, held in
  // memory for 2 minutes and never stored on the device.
  const CONTACT_REVEAL_MS = 120000;
  let contactList = null, contactTicket = 0, contactFailed = false, contactPinNeeded = false, contactHideTimer = null;
  async function loadContacts() {
    if (!has("contacts")) return;
    const t = ++contactTicket;
    try {
      const r = await SW.api("GET", `/api/contacts?dashboard=${encodeURIComponent(slug)}`);
      if (t !== contactTicket) return;
      contactList = Array.isArray(r.contacts) ? r.contacts : [];
      contactPinNeeded = !!r.pin_needed;
      contactFailed = false;
    } catch (_) { contactFailed = true; if (contactList === null) contactList = []; }
    clearTimeout(contactHideTimer);
    CARDS.contacts.el.hidden = CARDS.contacts.empty();
    renderContacts();
  }
  function askContactsPin() {
    if (PREVIEW) return;
    const box = $("contacts-body");
    if (!box || box.querySelector(".contact-pin")) return;
    const input = h("input", { type: "password", inputmode: "numeric", autocomplete: "off", maxlength: 12, class: "touch", "aria-label": "Contacts PIN" });
    const form = h("form", { class: "contact-pin row", onsubmit: (e) => { e.preventDefault(); const pin = input.value; input.value = ""; form.remove(); if (pin) revealContacts(pin); } },
      h("label", {}, "Contacts PIN ", input),
      h("button", { class: "touch primary", type: "submit" }, "Show"),
      h("button", { class: "touch", type: "button", onclick: () => form.remove() }, "Cancel"));
    box.appendChild(form);
    input.focus();
  }
  async function revealContacts(pin) {
    let r;
    try { r = await SW.api("POST", "/api/contacts/reveal", { dashboard: slug, pin }); } catch (err) { SW.toast(err.message || "Could not show the numbers", true); return; }
    ++contactTicket;
    contactList = Array.isArray(r.contacts) ? r.contacts : [];
    contactPinNeeded = false;
    renderContacts();
    clearTimeout(contactHideTimer);
    contactHideTimer = setTimeout(loadContacts, CONTACT_REVEAL_MS);   // the list without numbers again
  }
  function renderContacts() {
    const body = $("contacts-body");
    if (!body || contactList === null) return;
    if (contactFailed && !contactList.length) {
      body.replaceChildren(h("p", { class: "muted" }, "Could not load the contacts. It tries again when the connection is back."));
      return;
    }
    if (!contactList.length) {
      body.replaceChildren(h("p", { class: "muted" }, "No contacts on duty. Admin → Onsite contacts to add them."));
      return;
    }
    const phoneOf = (c) => (c.tel ? h("a", { class: "contact-phone touch", href: `tel:${c.tel}` }, `☎ ${c.phone}`)
      : c.phone ? h("span", { class: "contact-phone" }, `☎ ${c.phone}`) : null);
    const extra = (c) => [c.radio ? `Radio ${c.radio}` : "", c.area].filter(Boolean).join(" · ");
    // replaceChildren would print a null as the text "null", so leave the button out instead.
    body.replaceChildren(...[
      h("ul", { class: "contact-list" }, contactList.map((c) => h("li", {},
        h("div", { class: "contact-who" }, h("strong", {}, c.role), h("span", {}, ` ${c.name || "TBC"}`)),
        extra(c) ? h("div", { class: "muted" }, extra(c)) : null,
        phoneOf(c)))),
      contactPinNeeded && !isWall() ? h("button", { class: "touch", type: "button", onclick: askContactsPin }, "Show numbers") : null].filter(Boolean));
  }

  // ------------------------------------------------------------------ wifi
  // Crew internet networks. Fetched on its own for this dashboard only (never in the snapshot); the
  // join code is drawn here, offline. "wifi_changed" (no data) makes it fetch again.
  let wifiList = null, wifiTicket = 0, wifiFailed = false;
  async function loadWifi() {
    if (!has("wifi")) return;
    const t = ++wifiTicket;
    try {
      const r = await SW.api("GET", `/api/wifi?dashboard=${encodeURIComponent(slug)}`);
      if (t !== wifiTicket) return;
      wifiList = Array.isArray(r.networks) ? r.networks : [];
      wifiFailed = false;
    } catch (_) { wifiFailed = true; if (wifiList === null) wifiList = []; }
    CARDS.wifi.el.hidden = CARDS.wifi.empty();
    renderWifi();
  }
  function renderWifi() {
    const body = $("wifi-body");
    if (!body || wifiList === null) return;
    if (!wifiList.length) {
      body.replaceChildren(h("p", { class: "muted" }, wifiFailed ? "Could not load the Wi-Fi details. It tries again when the connection is back." : "No Wi-Fi networks yet. Admin → Wi-Fi to add them."));
      return;
    }
    body.replaceChildren(h("div", { class: "wifi-list" }, wifiList.map((n) => {
      const qr = SW.qrSvg(SW.wifiPayload(n), 200, "L");
      let plate = null;
      if (qr) {
        qr.setAttribute("aria-label", `QR code to join ${n.ssid}`);
        qr.removeAttribute("width"); qr.removeAttribute("height");
        plate = h("div", { class: "wifi-qr" }, qr);
      }
      const open = n.security === "nopass" || !n.password;
      return h("div", { class: "wifi-net" }, plate,
        h("div", { class: "wifi-info" },
          n.label ? h("div", { class: "wifi-label" }, n.label) : null,
          h("div", { class: "wifi-field" }, h("span", { class: "wifi-key" }, "Network"), h("span", { class: "wifi-val" }, n.ssid)),
          open ? h("div", { class: "wifi-field" }, h("span", { class: "wifi-key" }, "Password"), h("span", { class: "wifi-val muted" }, "None needed"))
            : n.hide_password ? h("div", { class: "wifi-field" }, h("span", { class: "wifi-key" }, "Password"), h("span", { class: "wifi-val muted" }, "Scan the code to join"))
            : h("div", { class: "wifi-field" }, h("span", { class: "wifi-key" }, "Password"), h("span", { class: "wifi-val wifi-pass" }, n.password)),
          n.note ? h("div", { class: "wifi-note muted" }, n.note) : null));
    })));
  }

  // --------------------------------------------------------------- cards
  // One entry per card this build can show (core/cards.py KNOWN_CARDS). The dashboard lists
  // which cards it shows and in what order (dash.cards); ids this build doesn't know (from a
  // newer release) are skipped. `wide` cards span the full width; the others flow two to a row
  // on a tablet, as in 0.2.0. The alarm banner and the header are not cards: always shown.
  // `empty()` true keeps an assigned card hidden (nothing to show yet).
  const cardEl = (id) => document.querySelector(`[data-card="${id}"]`);
  const CARDS = {
    env_tiles: { el: cardEl("env_tiles"), wide: true, render: renderTiles },
    // Hidden while the show has no schedule for this dashboard's stage.
    schedule: { el: cardEl("schedule"), wide: true, render: renderSchedule, empty: () => schedItems().length === 0 },
    // renderMarkers also puts the marker lines on the chart (with or without the Markers card).
    chart: { el: cardEl("chart"), wide: true, render: () => { renderSegs(); renderMarkers(); loadHistory(); } },
    markers: { el: cardEl("markers"), render: renderMarkers },
    sensors: { el: cardEl("sensors"), render: renderSensors },
    // The time from Ontime. Hidden until the first message arrives (the server sends one with the snapshot).
    wall_clock: { el: cardEl("wall_clock"), wide: true, render: renderWallClock, empty: () => !state.wallClock },
    // Ontime's countdown. Hidden until the first message arrives (the server sends one with the snapshot).
    ontime_timer: { el: cardEl("ontime_timer"), wide: true, render: renderOntimeTimer, empty: () => !state.ontimeTimer },
    // Ontime's position in the running order and ahead/behind. Hidden until the first message arrives.
    ontime_rundown: { el: cardEl("ontime_rundown"), wide: true, render: renderOntimeRundown, empty: () => !state.ontimeRundown },
    // Hidden on the wall while there is no pressure sensor (renderBarometer keeps it in step).
    barometer: { el: cardEl("barometer"), render: renderBarometer, empty: () => isWall() && !!state.site.baro && state.site.baro.state === "no_sensor" },
    // Site dew point and margin; shows "No reading" rather than hiding when there is no data.
    condensation: { el: cardEl("condensation"), render: renderCondensation },
    // Hidden while no sensor has the Equipment role (renderEquipment keeps it in step).
    equipment: { el: cardEl("equipment"), render: renderEquipment, empty: () => equipmentGroups().length === 0 },
    // Live GLOBCON levels. Hidden until the first message arrives (the server sends one with the snapshot).
    globcon_meters: { el: cardEl("globcon_meters"), wide: true, render: renderGlobcon, empty: () => !state.globcon },
    // RME units. Hidden while none is set up (the server sends null).
    rme_units: { el: cardEl("rme_units"), wide: true, render: renderRme, empty: () => !SW.rme.units(state.rme).length },
    // Power meters (Shelly). Hidden while no meter has been added (renderPower keeps it in step).
    power: { el: cardEl("power"), wide: true, render: renderPower, empty: () => !Object.keys(state.devices).some((k) => state.devices[k].category === "meter") },
    // Power tree. Shows its own empty note until a tree is set up (renderPowerTree keeps it in step).
    power_tree: { el: cardEl("power_tree"), wide: true, render: () => { if (!ptUi.data) loadPowerTree(); renderPowerTree(); } },
    // This Raspberry Pi. Hidden unless the server runs on a Pi (renderPiHost keeps it in step).
    pi_host: { el: cardEl("pi_host"), render: renderPiHost, empty: () => !state.devices.pi_host },
    witmotion_tilt: { el: cardEl("witmotion_tilt"), render: renderWitmotion, empty: () => !SW.wm.sensorsOf(state.devices).length },
    // FOH advice. Each hides itself while no enabled path (drift) or position (air loss) uses it; never added by default.
    alignment_drift: { el: cardEl("alignment_drift"), wide: true, render: renderDrift, empty: () => !(state.advice && state.advice.paths.length) },
    hf_air_loss: { el: cardEl("hf_air_loss"), wide: true, render: renderAir, empty: () => !(state.advice && state.advice.positions.length) },
    // Event plans shared with this dashboard. Fetched on its own; the wall hides it while there are none.
    documents: { el: cardEl("documents"), render: () => { renderDocuments(); loadDocuments(); },   // every snapshot (and so every reconnect) refetches
      empty: () => docList === null || (isWall() && !docList.length) },
    // Onsite contacts for the current event. Fetched on its own; the wall hides it while there are none.
    contacts: { el: cardEl("contacts"), render: () => { renderContacts(); loadContacts(); },
      empty: () => contactList === null || (isWall() && !contactList.length) },
    // Hidden while no sound level values are set up (renderSpl keeps it in step).
    // Shows a hint on tablets while no limits are entered; hidden on phones and walls until then.
    spl_limits: { el: cardEl("spl_limits"), render: () => { if (!splLim.data) loadSplLimits(); renderSplLimits(); } },
    spl_live: { el: cardEl("spl_live"), render: renderSpl, empty: () => SW.spl.entities(state.entities).length === 0 },
    // A footer below everything, wherever it is in the list; it shows itself once it has an address.
    // About Stagewatch: static, no data; drawn once and works offline.
    // Crew Wi-Fi: name, password and a join code. Fetched for this dashboard only; hidden while there are no networks.
    wifi: { el: cardEl("wifi"), render: () => { renderWifi(); loadWifi(); }, empty: () => wifiList === null || !wifiList.length },
    about: { el: cardEl("about"), render: renderAbout },
    connect_footer: { el: cardEl("connect_footer"), footer: true, render: renderConnectFooter },
  };
  // Without a dashboard (an admin on an unknown slug) show the 0.2.0 set.
  const FALLBACK_CARDS = ["env_tiles", "chart", "markers", "sensors"];

  function assignedCards() {
    const list = state.dash && Array.isArray(state.dash.cards) ? state.dash.cards : FALLBACK_CARDS;
    return list.filter((id, i) => Object.prototype.hasOwnProperty.call(CARDS, id) && list.indexOf(id) === i);
  }
  function has(id) { return assignedCards().indexOf(id) >= 0; }

  // Put the assigned cards into <main> in the dashboard's order (after the alarm banner) and
  // show them; hide the rest. Consecutive half-width cards share a two-column row.
  function layoutCards() {
    const main = $("main");
    const want = assignedCards();
    const oldRows = Array.prototype.slice.call(main.querySelectorAll(".card-flow"));
    let row = null;
    for (const id of want) {
      const c = CARDS[id];
      if (c.footer) continue;
      if (c.wide) { main.appendChild(c.el); row = null; continue; }
      if (!row) { row = h("div", { class: "grid-2 card-flow" }); main.appendChild(row); }
      row.appendChild(c.el);
    }
    for (const id of Object.keys(CARDS)) {
      const c = CARDS[id];
      const on = want.indexOf(id) >= 0;
      if (!on && !c.footer) main.appendChild(c.el);   // parked at the end, hidden
      if (!c.footer) c.el.hidden = !on || (c.empty ? c.empty() : false);
      else if (!on) c.el.hidden = true;
    }
    for (const r of oldRows) r.remove();   // emptied by the moves above
    // Half-size cards (style.css puts two side by side on a tablet or wall screen, a lone one stays half).
    const lay = (state.dash && state.dash.layout) || "tablet";
    for (const id of Object.keys(CARDS)) {
      const c = CARDS[id];
      if (!c.wide) continue;
      if (want.indexOf(id) >= 0 && SW.cardIsHalf(state.dash, id, lay)) c.el.setAttribute("data-size", "half"); else c.el.removeAttribute("data-size");
    }
    $("no-cards").hidden = want.length > 0;
    fillGaps();
    resetHeights();
  }

  // Wall alarm edge: one shared hook, data-level="alert" | "stop" on the card, from the level
  // classes each card already sets (style.css draws the edge on the wall layout only). Kept in
  // step by a class observer, so cards that tick on their own timers need no extra call.
  const LEVEL_STOP = ".pw-l-over, .wm-l3";
  const LEVEL_ALERT = ".lvl-alert:not(.baro-tend):not(.next):not(.next *), .rd-lvl-alert, .ot-over, .lim-over, .wm-l2, .pt-gap-check, .rme-has-warning";
  function markLevels() {
    for (const card of $("main").querySelectorAll("[data-card]")) {
      const hit = (sel) => card.matches(sel) || !!card.querySelector(sel);
      const lvl = hit(LEVEL_STOP) ? "stop" : hit(LEVEL_ALERT) ? "alert" : "";
      if ((card.getAttribute("data-level") || "") === lvl) continue;
      if (lvl) card.setAttribute("data-level", lvl); else card.removeAttribute("data-level");
    }
  }
  // Observer callbacks come batched; data-level is outside the filter, so this cannot loop.
  new MutationObserver(markLevels).observe($("main"), { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });

  // No holes in the grid: a half-width card with no half-width partner beside it, or the odd card
  // left over in a two-up row, takes the full width (data-lone, style.css). Runs again whenever a
  // card shows or hides itself, as cards with nothing to show do.
  function fillGaps() {
    const vis = (el) => !el.hidden;
    const lone = [];
    let half = [];
    const flush = () => { if (half.length % 2) lone.push(half[half.length - 1]); half = []; };
    for (const el of $("main").children) {
      if (el.classList.contains("card-flow")) {
        flush();
        const kids = Array.prototype.filter.call(el.children, vis);
        if (kids.length % 2) lone.push(kids[kids.length - 1]);
      } else if (el.hasAttribute("data-card") && vis(el)) {
        if (el.getAttribute("data-size") === "half") half.push(el); else flush();
      }
    }
    flush();
    for (const el of document.querySelectorAll("main [data-lone]")) if (lone.indexOf(el) < 0) el.removeAttribute("data-lone");
    for (const el of lone) if (!el.hasAttribute("data-lone")) el.setAttribute("data-lone", "");
  }
  new MutationObserver((recs) => { if (recs.some((r) => r.target.hasAttribute("data-card"))) { fillGaps(); resetHeights(); } })
    .observe($("main"), { subtree: true, attributes: true, attributeFilter: ["hidden"] });

  // Cards keep their size while they run: a card may grow to fit (a longer list, a new tag) but does
  // not shrink back when that line goes, so nothing below it jumps. The hold is let go when the
  // screen size or the card list changes, or someone opens or closes a fold in a card.
  // Older Safari (13.1) has no ResizeObserver: cards there simply aren't held.
  const sizeHold = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((entries) => {
    for (const en of entries) {
      const el = en.target;
      if (el.hidden) continue;
      const hgt = el.offsetHeight;
      if (hgt > (parseFloat(el.style.minHeight) || 0)) el.style.minHeight = `${hgt}px`;
    }
  });
  function resetHeights() {
    const els = Object.keys(CARDS).filter((id) => !CARDS[id].footer).map((id) => CARDS[id].el);
    for (const el of els) el.style.minHeight = "";
    requestAnimationFrame(() => { for (const el of els) if (!el.hidden) el.style.minHeight = `${el.offsetHeight}px`; });   // hold from the new size
  }
  if (sizeHold) for (const id of Object.keys(CARDS)) if (!CARDS[id].footer) sizeHold.observe(CARDS[id].el);
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(resetHeights, 200); });
  $("main").addEventListener("toggle", resetHeights, true);   // <details> toggle does not bubble: capture it

  // ---------------------------------------------------------- live feed
  function applySnapshot(msg) {
    state.entities = Object.fromEntries(msg.entities.map((e) => [e.id, e]));
    state.devices = Object.fromEntries(msg.devices.map((d) => [d.id, d]));
    state.markers = msg.markers;
    setAlarms(msg.alarms);
    state.sounding = msg.sounding;
    state.site = msg.site;
    SW.setSiteTime(msg.site.time);
    SW.setScheduleWarn(msg.site.schedule_warn);
    state.show = msg.show;
    state.scheduleMeta = msg.schedule || null;   // {show_id, day, revision}: the items are fetched
    renderNet(msg.internet);
    state.wallClock = msg.wall_clock || null;    // null until a dashboard has the card and the source is running
    state.ontimeTimer = msg.ontime_timer || null; // likewise
    state.ontimeRundown = msg.ontime_rundown || null; // likewise
    state.globcon = msg.globcon_meters || null;       // likewise
    state.rme = msg.rme_units || null; rmeC.at = Date.now() / 1000;   // likewise: null while no RME unit is set up
    state.advice = msg.advice || null;                // FOH advice block (drift paths, air-loss positions)
    state.isAdmin = msg.is_admin;
    savedDash = msg.dashboard;
    state.dash = previewDash();
    state.now = msg.now;
    syncClock(msg.now);
    if (!state.dash && !state.isAdmin) { location.href = "/"; return; }
    const layout = (state.dash && state.dash.layout) || "tablet";
    document.body.className = `layout-${layout}`;
    document.title = `${state.dash ? state.dash.title : "Dashboard"} · ${msg.site.name}`;
    $("title").textContent = msg.site.name;
    // Subtitle: Dashboard · Event · Day (the event and day the markers and history belong to).
    const sub = [state.dash ? state.dash.title : "", msg.show.event_name, msg.show.name].filter((x) => x);
    $("show").textContent = sub.join(" · ");
    $("marker-form").hidden = PREVIEW || !(state.isAdmin || (state.dash && state.dash.allow_marker));
    layoutCards();
    renderAlarms();
    for (const id of assignedCards()) CARDS[id].render();
    if (state.selectedMarker !== null) {   // after a reconnect: the marker may have changed or gone
      if (!findMarker(state.selectedMarker)) { state.selectedMarker = null; noteEdit = null; $("delta").hidden = true; }
      renderMarkerNote();
    }
    if (!has("schedule")) stopScheduleTimer();
    if (!has("wall_clock")) stopWallClockTimer();
    if (!has("ontime_timer")) stopOntimeTimer();
    if (!has("ontime_rundown")) stopOntimeRundown();
    if (!has("globcon_meters")) stopGlobcon();
    if (!has("rme_units")) stopRme();
    syncSchedule();
    if (PREVIEW && state.isAdmin) {
      previewStrip();
      try { window.parent.postMessage({ type: "sw-preview-ready" }, location.origin); } catch (_) { /* ignore */ }
    }
  }

  // About Stagewatch: fixed text and a locally drawn QR code; no server data, so it works offline.
  function renderAbout() {
    const card = $("about-card");
    if (!has("about")) { card.hidden = true; return; }
    card.hidden = false;
    if ($("about-body").firstChild) return;
    $("about-body").replaceChildren(...SW.aboutNodes());
  }

  // "Open on a tablet" footer card: where tablets can reach this dashboard. Only the address and
  // path; the server sends a single LAN address (nothing else), and only to dashboards that
  // have this card.
  let wallAddressFor = null;
  async function renderConnectFooter() {
    const box = $("wall-address");
    if (!has("connect_footer")) { box.hidden = true; return; }
    if (wallAddressFor === slug && !box.hidden) return;
    try {
      const r = await SW.api("GET", `/api/dashboard/${encodeURIComponent(slug)}/address`);
      if (!r.url) { box.hidden = true; return; }
      wallAddressFor = slug;
      const qr = SW.qrSvg(r.url, 96);
      box.replaceChildren(qr || "", h("span", {}, "Open on a tablet: ", h("strong", {}, r.url)));
      box.hidden = false;
    } catch (_) { box.hidden = true; }
  }

  // The draft is used only by an admin's preview frame, only from the parent page on this origin,
  // and only when it passes the same checks as a saved dashboard. It lives in memory: a reload drops it.
  function previewDash() {
    if (!PREVIEW || !draft || !state.isAdmin || !savedDash) return savedDash;
    return SW.previewDraft(draft, savedDash, Object.keys(CARDS)) || savedDash;
  }
  function previewStrip() {
    let strip = $("preview-strip");
    if (!strip) {
      strip = h("div", { id: "preview-strip", class: "preview-strip", role: "status" });
      document.body.insertBefore(strip, document.body.firstChild);
    }
    strip.textContent = state.dash !== savedDash ? "Preview: not saved" : "Preview: saved version";
  }
  function onDraft(ev) {
    if (ev.origin !== location.origin || ev.source !== window.parent) return;
    const d = ev.data;
    if (!d || typeof d !== "object" || d.type !== "sw-preview-draft" || !state.isAdmin || !savedDash) return;
    draft = d.draft === null ? null : (SW.previewDraft(d.draft, savedDash, Object.keys(CARDS)) ? d.draft : null);
    state.dash = previewDash();
    previewStrip();
    layoutCards();
    renderAlarms();
    for (const id of assignedCards()) CARDS[id].render();
    if (!has("schedule")) stopScheduleTimer();
    if (!has("wall_clock")) stopWallClockTimer();
    if (!has("ontime_timer")) stopOntimeTimer();
    if (!has("ontime_rundown")) stopOntimeRundown();
    if (!has("globcon_meters")) stopGlobcon();
    if (!has("rme_units")) stopRme();
    syncSchedule();
  }
  if (PREVIEW) window.addEventListener("message", onDraft);

  function onMessage(msg) {
    switch (msg.type) {
      case "snapshot": applySnapshot(msg); break;
      case "states": {
        state.now = msg.now;
        syncClock(msg.now);
        state.site = { ...state.site, ...msg.site };
        SW.setSiteTime(msg.site && msg.site.time);   // keeps the offset fresh across a DST change
        SW.setScheduleWarn(msg.site && msg.site.schedule_warn);
        const shown = new Set(has("chart") ? chartEntities() : []);
        for (const e of msg.entities) {
          state.entities[e.id] = e;
          if (shown.has(e.id) && e.value !== null && e.updated) {
            const pts = state.history[e.id] || (state.history[e.id] = []);
            if (!pts.length || e.updated > pts[pts.length - 1][0]) pts.push([e.updated, e.value]);
          }
        }
        if (has("env_tiles")) renderTiles();
        if (has("sensors")) renderSensors();
        if (has("barometer")) renderBarometer();
        if (has("condensation")) renderCondensation();
        if (has("equipment")) renderEquipment();
        if (has("power")) renderPower();
        if (has("power_tree")) renderPowerTree();
        if (has("witmotion_tilt")) renderWitmotion();
        if (has("spl_live")) { splOnStates(msg.entities); renderSpl(); }
        if (has("chart")) updateChartSeries();
        break;
      }
      case "device":
        state.devices[msg.device.id] = msg.device;
        if (has("sensors")) renderSensors();
        if (has("equipment")) renderEquipment();
        if (has("power")) renderPower();
        if (has("power_tree")) renderPowerTree();
        if (has("pi_host")) renderPiHost();
        if (has("witmotion_tilt")) renderWitmotion();
        if (has("spl_live")) renderSpl();
        break;
      case "marker":
        if (!findMarker(msg.marker.id)) state.markers.push(msg.marker);
        renderMarkers(); break;
      case "marker_updated": if (findMarker(msg.marker.id)) applyMarker(msg.marker); break;
      case "marker_deleted":
        state.markers = state.markers.filter((m) => m.id !== msg.id);
        if (state.selectedMarker === msg.id) { state.selectedMarker = null; noteEdit = null; $("delta").hidden = true; }
        renderMarkers(); break;
      case "schedule":   // only {show_id, revision}: fetch the items if they changed
        state.scheduleMeta = Object.assign({}, state.scheduleMeta || {}, { show_id: msg.show_id, revision: msg.revision });
        syncSchedule();
        break;
      case "wall_clock": state.wallClock = msg; if (has("wall_clock")) renderWallClock(); break;
      case "ontime_timer": state.ontimeTimer = msg; if (has("ontime_timer")) renderOntimeTimer(); break;
      case "globcon_meters": state.globcon = msg; if (has("globcon_meters")) renderGlobcon(); break;
      case "rme_units": state.rme = msg; rmeC.at = Date.now() / 1000; if (has("rme_units")) renderRme(); break;
      case "advice":   // the same block as the snapshot's; the cards patch their cells in place
        state.advice = { drift_ref: msg.drift_ref, air_ref: msg.air_ref, resolution: msg.resolution, paths: msg.paths || [], positions: msg.positions || [] };
        if (has("alignment_drift")) renderDrift();
        if (has("hf_air_loss")) renderAir();
        break;
      case "ontime_rundown": state.ontimeRundown = msg; if (has("ontime_rundown")) renderOntimeRundown(); break;
      case "internet": renderNet(msg.internet); break;
      case "alarms": setAlarms(msg.alarms); state.sounding = msg.sounding; renderAlarms(); break;
      case "documents_changed": loadDocuments(); break;
      case "contacts_changed": loadContacts(); break;
      case "wifi_changed": loadWifi(); break;
      case "spl_limits_changed": loadSplLimits(); break;   // no payload: fetch the limits
      case "reload": location.reload(); break;
    }
  }

  // After a software update the server restarts with new code; the live feed
  // reconnects on its own, but this page would keep running the old JS. On
  // each reconnect compare the running build with the one this page loaded
  // with and reload if it changed.
  let loadedBuild = null;
  const buildKey = (i) => (i && i.build ? `${i.version}+${i.build.commit}` : null);
  let wasDown = false;
  const heardMessage = (msg) => {
    cs.lastHeard = Date.now();
    try { onMessage(msg); cs.fault = false; } catch (_) { cs.fault = true; }   // a message that cannot be used is a feed fault
    renderConn();
  };
  SW.connect(`?dashboard=${encodeURIComponent(slug)}`, heardMessage, (up) => {
    cs.wsUp = up; cs.since = Date.now();
    if (!up) cs.fault = false;
    renderConn();
    SW.connection.report(up);
    if (!up) { wasDown = true; return; }
    if (!wasDown) return;
    SW.api("GET", "/api/info").then((i) => {
      const key = buildKey(i);
      if (loadedBuild && key && key !== loadedBuild) location.reload();
    }).catch(() => {});
  });
  SW.api("GET", "/api/info").then((i) => {
    $("emulate").hidden = !i.emulate;
    SW.applyBranding(i);
    loadedBuild = buildKey(i);
  }).catch(() => {});
  if (soundWanted()) sounder.enable(); // works in kiosk mode (autoplay allowed); otherwise tap the sound button
  renderSound();
  renderConn();
  setInterval(() => { $("clock").textContent = SW.fmtTime(serverNow(), { seconds: true }); }, 1000);
  // Re-bucket history so long views stay tidy; refresh the sensors' "Updated" ages. Each only
  // does work while its card is on this dashboard.
  setInterval(() => { if (has("chart")) loadHistory(); if (has("env_tiles")) loadTileHistory(false); }, 60000);
  setInterval(() => { if (has("spl_live")) loadSplHistory(); }, 60000);
  setInterval(() => { state.now = serverNow(); if (has("sensors")) renderSensors(); if (has("barometer")) renderBarometer(); if (has("equipment")) renderEquipment(); if (has("power")) renderPower(); if (has("power_tree")) { renderPowerTree(); if (Date.now() - ptUi.at > 30000) loadPowerTree(); if (Date.now() - ptUi.trendAt > 45000) loadPowerTrend(); } if (has("pi_host")) renderPiHost(); if (has("witmotion_tilt")) renderWitmotion(); if (has("alignment_drift")) renderDrift(); if (has("hf_air_loss")) renderAir(); if (has("power") && Date.now() - pwHist.at > 15000) loadPowerHistory(); if (has("spl_live")) renderSpl(); if (has("spl_limits")) renderSplLimits(); if (wantLimits() && Date.now() - splLim.at > 60000) loadSplLimits(); }, 5000);
})();
