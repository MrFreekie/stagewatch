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
    site: {}, show: {}, dash: null, isAdmin: false, now: Date.now() / 1000,
    mode: localGet("sw.mode", "temperature"), span: Number(localGet("sw.span", 3600)),
    selectedMarker: null, history: {},
  };
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
    return Object.values(state.devices).filter((d) => d.id !== "site")
      .sort((a, b) => (a.area || a.name).localeCompare(b.area || b.name));
  }

  function renderSensors() {
    const rows = sensorDevices().map((d) => {
      const ents = Object.values(state.entities).filter((e) => e.device_id === d.id);
      const byKind = (k) => ents.find((e) => e.kind === k);
      const cell = (k) => { const e = byKind(k); return h("td", { class: "num" + (e && e.stale ? " muted" : "") }, e ? fmt(k, e.value) : ""); };
      const last = Math.max(0, ...ents.map((e) => e.updated || 0));
      return h("tr", {},
        h("td", {}, d.name, d.area ? h("div", { class: "muted", style: "font-size:12px" }, d.area) : null),
        h("td", {}, h("span", { class: `status ${d.status}`, title: d.status_detail || "" }, d.status)),
        cell("temperature"), cell("humidity"), cell("pressure"),
        h("td", { class: "num muted" }, SW.age(last || null, state.now)));
    });
    $("sensors").replaceChildren(
      h("thead", {}, h("tr", {}, h("th", {}, "Node"), h("th", {}, "Status"),
        h("th", { class: "num" }, "Temp"), h("th", { class: "num" }, "RH"),
        h("th", { class: "num" }, "Pressure"), h("th", { class: "num" }, "Updated"))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: 6, class: "muted" }, "No sensor nodes yet. An admin can adopt ESPHome nodes."))));
    $("site-note").textContent = `Readings older than ${Math.round(state.site.stale_after_s || 60)} s are treated as stale and left out of the average.`;
  }

  function renderMarkers() {
    if (has("chart")) {
      chart.markers = state.markers.map((m) => ({ ...m, selected: m.id === state.selectedMarker }));
      chart.draw();
    }
    if (!has("markers")) return;
    const canDelete = state.isAdmin;
    const items = [...state.markers].sort((a, b) => b.ts - a.ts).map((m) => {
      const ms = SW.markerStyle(m.source);
      return h("li", { class: m.id === state.selectedMarker ? "sel" : "", onclick: () => selectMarker(m.id) },
        h("span", { class: "mk", title: ms.name, style: `background:var(--marker-${ms.key},${ms.fallback});color:var(--marker-${ms.key}-ink,${ms.ink})` }, ms.glyph),
        h("span", { class: "t" }, SW.fmtTime(m.ts)),
        h("span", { style: "flex:1" }, m.label),
        canDelete ? h("button", { class: "small danger", title: "Delete marker", onclick: (ev) => { ev.stopPropagation(); deleteMarker(m.id); } }, "✕") : null);
    });
    $("marker-list").replaceChildren(...(items.length ? items : [h("li", { class: "muted" }, "No markers yet. Add one at soundcheck, e.g. \"Aligned\".")]));
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
    state.selectedMarker = id;
    renderMarkers();
    if (!has("markers")) return;   // the drift panel lives in the Markers card
    const box = $("delta");
    box.hidden = false;
    box.replaceChildren(h("span", { class: "muted" }, "Loading…"));
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
          row("c", "site.speed_of_sound", "speed_of_sound")))].filter(Boolean));
    } catch (err) {
      box.replaceChildren(h("span", { class: "error" }, err.message));
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

  // --------------------------------------------------------------- cards
  // One entry per card this build can show (core/cards.py KNOWN_CARDS). The dashboard lists
  // which cards it shows and in what order (dash.cards); ids this build doesn't know (from a
  // newer release) are skipped. `wide` cards span the full width; the others flow two to a row
  // on a tablet, as in 0.2.0. The alarm banner and the header are not cards: always shown.
  // `empty()` true keeps an assigned card hidden (nothing to show yet).
  const cardEl = (id) => document.querySelector(`[data-card="${id}"]`);
  const nothing = () => {};
  const CARDS = {
    env_tiles: { el: cardEl("env_tiles"), wide: true, render: renderTiles },
    // Filled in by the schedule feature; hidden while the show has no schedule.
    schedule: { el: cardEl("schedule"), wide: true, render: nothing, empty: () => true },
    // renderMarkers also puts the marker lines on the chart (with or without the Markers card).
    chart: { el: cardEl("chart"), wide: true, render: () => { renderSegs(); renderMarkers(); loadHistory(); } },
    markers: { el: cardEl("markers"), render: renderMarkers },
    sensors: { el: cardEl("sensors"), render: renderSensors },
    // Filled in by the Wall Clock feature; hidden until then.
    wall_clock: { el: cardEl("wall_clock"), wide: true, render: nothing, empty: () => true },
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
    state.show = msg.show;
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
      case "marker": state.markers.push(msg.marker); renderMarkers(); break;
      case "marker_deleted":
        state.markers = state.markers.filter((m) => m.id !== msg.id);
        if (state.selectedMarker === msg.id) { state.selectedMarker = null; $("delta").hidden = true; }
        renderMarkers(); break;
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
