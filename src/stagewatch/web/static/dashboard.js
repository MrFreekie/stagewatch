// User dashboard: the alarm banner plus the cards this dashboard lists (live tiles, history
// chart with markers, marker deltas, sensor table, ...). Cards, their order, the layout
// (tablet / phone / wall) and allowed actions come from the dashboard's config on the server.
"use strict";

(() => {
  const { h, fmt, fmtDelta } = SW;
  const slug = decodeURIComponent(location.pathname.split("/").pop());
  const $ = (id) => document.getElementById(id);

  const SITE_TILES = [
    ["site.temperature", "Temperature"],
    ["site.humidity", "Humidity"],
    ["site.pressure", "Pressure"],
    ["site.speed_of_sound", "Speed of sound"],
    ["site.dew_point", "Dew point"],
  ];
  const SERIES_MODES = {
    temperature: { label: "Temp", site: "site.temperature", kind: "temperature" },
    humidity: { label: "RH", site: "site.humidity", kind: "humidity" },
    pressure: { label: "Pressure", site: "site.pressure", kind: "pressure" },
    speed_of_sound: { label: "c", site: "site.speed_of_sound", kind: "speed_of_sound" },
  };
  const SPANS = [[900, "15m"], [3600, "1h"], [14400, "4h"], [43200, "12h"]];
  const COLORS = ["--s2", "--s3", "--s4", "--s5", "--s6"];

  const state = {
    entities: {}, devices: {}, markers: [], alarms: [], sounding: false,
    site: {}, show: {}, schedule: { items: [] }, scheduleMeta: null, dash: null, isAdmin: false, now: Date.now() / 1000,
    mode: localGet("sw.mode", "temperature"), span: Number(localGet("sw.span", 3600)),
    selectedMarker: null, history: {}, showHidden: false,
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

  function renderTiles() {
    const tiles = $("tiles");
    tiles.replaceChildren();
    for (const [id, label] of SITE_TILES) {
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
        h("div", { class: "foot" }, foot),
        note ? h("div", { class: "foot approx" }, note) : null));
    }
  }

  function renderAlarms() {
    const bar = $("alarms");
    const active = state.alarms;
    const loud = active.filter((a) => !a.silent);
    // Only silent alarms: a calm "notice" style, no level colour, no Ack.
    bar.className = "alarm-bar" + (loud.length ? ` show l${Math.max(...loud.map((a) => a.level))}` : (active.length ? " show notice" : ""))
      + (state.sounding ? " sounding" : "");
    $("alarm-list").replaceChildren(...active.map((a) =>
      h("li", { class: a.silent ? "silent" : "" }, h("span", { class: "lvl" }, a.silent ? "notice" : a.level_name), a.message, a.acked ? h("span", { class: "muted" }, " (acknowledged)") : null)));
    const canAck = state.isAdmin || (state.dash && state.dash.allow_ack);
    $("ack").hidden = !(canAck && state.sounding);
    sounder.set(state.sounding);
    renderSound();
  }

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
    return Object.values(state.devices).filter((d) => d.id !== "site" && d.category !== "service")
      .sort((a, b) => (a.area || a.name).localeCompare(b.area || b.name));
  }

  // Node health: Wi-Fi signal (dBm) in words, and battery level. Amber when it needs attention.
  SW.signalWord = (dbm) => (dbm >= -67 ? "good" : dbm >= -75 ? "fair" : "weak");
  function renderSensors() {
    const devs = sensorDevices();
    const ents = Object.values(state.entities);
    const any = (k) => devs.some((d) => ents.some((e) => e.device_id === d.id && e.kind === k));
    const showSignal = any("signal_strength"), showBattery = any("battery");
    const rows = devs.map((d) => {
      const mine = ents.filter((e) => e.device_id === d.id);
      const byKind = (k) => mine.find((e) => e.kind === k);
      const cell = (k) => { const e = byKind(k); return h("td", { class: "num" + (e && e.stale ? " muted" : "") }, e ? fmt(k, e.value) : ""); };
      const health = (k, warn, text) => {
        const e = byKind(k);
        if (!e || e.value === null || e.value === undefined) return h("td", { class: "num muted" }, "");
        const cls = "num" + (e.stale ? " muted" : warn(e.value) ? " warn-text" : "");
        return h("td", { class: cls }, text(e.value));
      };
      const last = Math.max(0, ...mine.filter((e) => e.kind !== "signal_strength" && e.kind !== "battery").map((e) => e.updated || 0));
      return h("tr", {},
        h("td", {}, d.name, d.area ? h("div", { class: "muted", style: "font-size:12px" }, d.area) : null),
        h("td", {}, h("span", { class: `status ${d.status}`, title: d.status_detail || "" }, d.status)),
        cell("temperature"), cell("humidity"), cell("pressure"),
        showSignal ? health("signal_strength", (v) => v < -75, (v) => `${fmt("signal_strength", v)} ${SW.signalWord(v)}`) : null,
        showBattery ? health("battery", (v) => v < 20, (v) => (v < 20 ? `${fmt("battery", v)} low` : fmt("battery", v))) : null,
        h("td", { class: "num muted" }, SW.age(last || null, state.now)));
    });
    const cols = 6 + (showSignal ? 1 : 0) + (showBattery ? 1 : 0);
    $("sensors").replaceChildren(
      h("thead", {}, h("tr", {}, h("th", {}, "Node"), h("th", {}, "Status"),
        h("th", { class: "num" }, "Temp"), h("th", { class: "num" }, "RH"),
        h("th", { class: "num" }, "Pressure"),
        showSignal ? h("th", { class: "num" }, "Signal") : null,
        showBattery ? h("th", { class: "num" }, "Battery") : null,
        h("th", { class: "num" }, "Updated"))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: cols, class: "muted" }, "No sensor nodes yet. An admin can adopt ESPHome nodes."))));
    $("site-note").textContent = `Readings older than ${Math.round(state.site.stale_after_s || 60)} s are treated as stale and left out of the average.`;
  }

  // Notes, Hide and Un-hide: anyone on a dashboard allowed to add markers (any marker), and admins.
  // Deleting stays admin-only.
  const canEditMarkers = () => !!(state.isAdmin || (state.dash && state.dash.allow_marker));
  const findMarker = (id) => state.markers.filter((m) => m.id === id)[0] || null;

  function renderMarkers() {
    if (has("chart")) {
      chart.markers = SW.visibleMarkers(state.markers, false).map((m) => ({ ...m, selected: m.id === state.selectedMarker }));
      chart.draw();
    }
    if (!has("markers")) return;
    const canDelete = state.isAdmin;
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

  function renderSegs() {
    $("series-seg").replaceChildren(...Object.entries(SERIES_MODES).map(([k, m]) =>
      h("button", { class: k === state.mode ? "on" : "", onclick: () => { state.mode = k; localSet("sw.mode", k); renderSegs(); loadHistory(); } }, m.label)));
    $("span-seg").replaceChildren(...SPANS.map(([s, label]) =>
      h("button", { class: s === state.span ? "on" : "", onclick: () => { state.span = s; localSet("sw.span", String(s)); renderSegs(); loadHistory(); } }, label)));
  }

  // --------------------------------------------------------------- chart
  function chartEntities() {
    const mode = SERIES_MODES[state.mode];
    const ids = [mode.site];
    if (mode.kind !== "speed_of_sound") {
      for (const e of Object.values(state.entities)) if (!e.derived && e.kind === mode.kind) ids.push(e.id);
    }
    return ids;
  }

  function updateChartSeries() {
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    const ids = chartEntities();
    chart.series = ids.map((id, i) => {
      const e = state.entities[id];
      const dev = e && state.devices[e.device_id];
      return {
        label: i === 0 ? "Site average" : (dev ? dev.name : id),
        color: i === 0 ? css("--s1") : css(COLORS[(i - 1) % COLORS.length]),
        width: i === 0 ? 3 : 1.3,
        points: state.history[id] || [],
      };
    });
    const now = serverNow();
    chart.range = [now - state.span, now];
    chart.draw();
    $("legend").replaceChildren(...chart.series.map((s) => h("span", {}, h("i", { style: `background:${s.color}` }), s.label)));
  }

  async function loadHistory() {
    const ids = chartEntities();
    const since = serverNow() - state.span;
    try {
      state.history = await SW.api("GET", `/api/history?entities=${encodeURIComponent(ids.join(","))}&since=${since}&points=500`);
    } catch (_) { state.history = {}; }
    updateChartSeries();
  }

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
      const pick = () => { sched.selected = sched.selected === it.id ? null : it.id; tickSchedule(); };
      const el = h("li", tap ? { role: "button", tabindex: "0", title: "Show the setlist", onclick: pick,
        onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } } } : {},
      h("span", { class: "sched-row-time" }, timeRange(it)),
      h("span", { class: "sched-row-title" }, it.title, it.stage && !stage ? h("span", { class: "muted" }, ` · ${it.stage}`) : null),
      nowTag,
      h("span", { class: "sched-row-kind" }, tap ? "Setlist ›" : kindNote(it)));
      return { el, item: it, end: ends[i], nowTag, tap };
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
    // Optional slow pulse in the last (orange) step only; NEXT never flashes.
    const fl = lvl === "alert" && SW.scheduleWarn.flash ? " flash" : "";
    setClass(ui.now.el, `sched-block now${lvl ? ` lvl-${lvl}` : ""}${fl}`);
    for (const tag of [ui.now.tag, ui.stripTag]) {
      const text = step ? `${step} MIN` : "";
      setText(tag, text);
      setClass(tag, `sched-tag${lvl ? ` lvl-${lvl}` : ""}${fl}`);
      tag.hidden = !text;
    }
    setText(ui.now.title, cur ? cur.title : (idle[nn.state] || "—"));
    setText(ui.now.count, cur ? (ownEnd ? `${SW.fmtDuration(left, true)} left` : "") : "");
    setText(ui.now.line, cur ? `Started ${SW.fmtTime(cur.planned_start)}, ${SW.fmtDuration(now - cur.planned_start)} ago`
      + (ownEnd ? ` · ends ${SW.fmtTime(nn.currentEnd)}` : "") : "");
    // Progress bar: how much of the item's own planned time has gone. Hidden without an end time.
    const total = ownEnd ? nn.currentEnd - cur.planned_start : 0;
    const showBar = ownEnd && total > 0;
    ui.now.bar.hidden = !showBar;
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
      tag.hidden = !text;
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
      setClass(r.el, "sched-row" + (past ? " past" : "") + (isNow ? " now" : "") + (r.tap ? " tap" : "")
        + (r.tap && r.item.id === showId ? " sel" : ""));
      r.nowTag.hidden = !isNow;
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
      wc.ui = { head: card.querySelector("h2"), face: null, host: h("div", { class: "wc-host" }),
        date: h("p", { class: "wc-date" }), note: h("p", { class: "wc-note", role: "status" }) };
      card.replaceChildren(wc.ui.head, wc.ui.host, wc.ui.date, wc.ui.note);
    }
    wc.ui.head.textContent = `Wall Clock · ${m.label || "Ontime"}`;
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
    ui.face.update(v, { colonBlink: v.opts.colonBlink, reduced: SW.wc.reducedMotion() });
    setText(ui.date, v.date);
    ui.date.hidden = !v.date;
    setText(ui.note, v.note);
    setClass(ui.note, `wc-note${v.level ? ` ${v.level}` : ""}`);
    setClass(card, `card${v.cls ? ` ${v.cls}` : ""} wc-style-${style}`);
    wc.timer = setTimeout(tickWallClock, SW.wc.nextDelayMs(m, now));
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
    // A footer below everything, wherever it is in the list; it shows itself once it has an address.
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
    $("no-cards").hidden = want.length > 0;
  }

  // ---------------------------------------------------------- live feed
  function applySnapshot(msg) {
    state.entities = Object.fromEntries(msg.entities.map((e) => [e.id, e]));
    state.devices = Object.fromEntries(msg.devices.map((d) => [d.id, d]));
    state.markers = msg.markers;
    state.alarms = msg.alarms;
    state.sounding = msg.sounding;
    state.site = msg.site;
    SW.setSiteTime(msg.site.time);
    SW.setScheduleWarn(msg.site.schedule_warn);
    state.show = msg.show;
    state.scheduleMeta = msg.schedule || null;   // {show_id, day, revision}: the items are fetched
    state.wallClock = msg.wall_clock || null;    // null until a dashboard has the card and the source is running
    state.isAdmin = msg.is_admin;
    state.dash = msg.dashboard;
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
    $("marker-form").hidden = !(state.isAdmin || (state.dash && state.dash.allow_marker));
    layoutCards();
    renderAlarms();
    for (const id of assignedCards()) CARDS[id].render();
    if (state.selectedMarker !== null) {   // after a reconnect: the marker may have changed or gone
      if (!findMarker(state.selectedMarker)) { state.selectedMarker = null; noteEdit = null; $("delta").hidden = true; }
      renderMarkerNote();
    }
    if (!has("schedule")) stopScheduleTimer();
    if (!has("wall_clock")) stopWallClockTimer();
    syncSchedule();
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
        if (has("chart")) updateChartSeries();
        break;
      }
      case "device":
        state.devices[msg.device.id] = msg.device;
        if (has("sensors")) renderSensors();
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
      case "alarms": state.alarms = msg.alarms; state.sounding = msg.sounding; renderAlarms(); break;
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
  SW.connect(`?dashboard=${encodeURIComponent(slug)}`, onMessage, (up) => {
    $("conn").classList.toggle("on", up);
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
    loadedBuild = buildKey(i);
  }).catch(() => {});
  if (soundWanted()) sounder.enable(); // works in kiosk mode (autoplay allowed); otherwise tap the sound button
  renderSound();
  setInterval(() => { $("clock").textContent = SW.fmtTime(serverNow(), { seconds: true }); }, 1000);
  // Re-bucket history so long views stay tidy; refresh the sensors' "Updated" ages. Each only
  // does work while its card is on this dashboard.
  setInterval(() => { if (has("chart")) loadHistory(); }, 60000);
  setInterval(() => { state.now = serverNow(); if (has("sensors")) renderSensors(); }, 5000);
})();
