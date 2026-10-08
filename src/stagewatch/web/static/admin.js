// Admin console: onboarding/login, site settings, ESPHome adoption,
// calibration, thresholds, dashboards, OSC output, shows, PIN.
"use strict";

(() => {
  const { h, api, fmt } = SW;
  const app = document.getElementById("app");
  let admin = null;   // /api/admin/state
  let snap = null;    // /api/snapshot
  let sw = null;      // /api/admin/software
  let entityIdsRendered = "";
  let showIgnored = false;  // "Show ignored" toggle on the ESPHome nodes card

  const toast = SW.toast;
  async function run(fn, okMsg) {
    try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
    catch (err) { toast(err.message, true); throw err; }
  }
  const card = SW.card;
  const field = (label, input) => h("label", { class: "field" }, label, input);
  const val = (el) => el.value.trim();
  const numOrNull = (el) => (el.value.trim() === "" ? null : Number(el.value));

  // ---------------------------------------------------------------- auth
  function renderAuth(setup) {
    const pin = h("input", { type: "password", inputmode: "numeric", autocomplete: setup ? "new-password" : "current-password", placeholder: "PIN", minlength: 4, maxlength: 64 });
    const pin2 = setup ? h("input", { type: "password", inputmode: "numeric", autocomplete: "new-password", placeholder: "Repeat PIN" }) : null;
    const err = h("p", { class: "error" });
    const form = h("form", { class: "row", onsubmit: async (ev) => {
      ev.preventDefault();
      err.textContent = "";
      if (setup && pin.value !== pin2.value) { err.textContent = "PINs don't match"; return; }
      try {
        await api("POST", setup ? "/api/admin/setup" : "/api/admin/login", { pin: pin.value });
        // Sent here by the Schedule page: go back to it (only the one allow-listed path, never a URL).
        const next = SW.adminNext(location.search);
        if (next) { location.assign(next); return; }
        start();
      } catch (e) { err.textContent = e.message; }
    } }, pin, pin2, h("button", { class: "primary", type: "submit" }, setup ? "Set admin PIN" : "Log in"));
    app.replaceChildren(card(setup ? "Welcome: set an admin PIN" : "Admin login",
      setup ? h("p", { class: "muted" }, "First run. Choose a PIN (at least 4 characters). Only admins can change devices, thresholds and dashboards; dashboards stay view-only for everyone else.") : null,
      form, err));
    pin.focus();
  }

  function renderRecovery() {
    app.replaceChildren(card("Recovery required",
      h("p", { role: "alert" }, "The saved settings could not be read and the admin PIN could not be recovered. For safety, setting a new PIN over the network is disabled."),
      h("p", {}, "To recover, do one of these on the Stagewatch computer:"),
      h("ul", {},
        h("li", {}, "Restore config.yaml from a backup in the data folder (the unreadable file was kept next to it as config.invalid*.yaml), then restart Stagewatch."),
        h("li", {}, "Or run the reset script, which clears the PIN and restarts Stagewatch; this page then lets you set a new PIN. It only works with access to the computer's files.",
          h("ul", {},
            h("li", {}, "Windows (PowerShell as Administrator): ", h("code", {}, "powershell -ExecutionPolicy Bypass -File C:\\Stagewatch\\deploy\\windows\\reset-admin-pin.ps1")),
            h("li", {}, "Raspberry Pi (Terminal): ", h("code", {}, "bash /opt/stagewatch/deploy/pi/reset-admin-pin.sh")))),
        h("li", {}, "Step-by-step guide: docs/updating-and-backups.md (Forgotten PIN, or \"Recovery required\")."))));
  }

  // ----------------------------------------------------------- sections
  function siteCard() {
    const s = admin.config.site;
    const name = h("input", { value: s.name });
    const alt = h("input", { class: "num", type: "number", step: "1", value: s.altitude_m });
    const dist = h("input", { class: "num", type: "number", step: "0.1", value: s.reference_distance_m });
    const stale = h("input", { class: "num", type: "number", step: "1", value: s.stale_after_s });
    const tau = h("input", { class: "num", type: "number", step: "1", value: s.smoothing_tau_s });
    const outl = h("input", { type: "checkbox", checked: s.outlier_reject });
    // Time zone: a searchable list where the browser can supply one, otherwise plain text.
    // The server checks the name either way.
    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    const tzList = zones.length ? h("datalist", { id: "tz-list" }, zones.map((z) => h("option", { value: z }))) : null;
    const tz = h("input", { class: "touch", value: s.timezone, list: tzList ? "tz-list" : null, placeholder: "e.g. Europe/London",
      maxlength: 64, autocomplete: "off", autocapitalize: "off", spellcheck: false });
    const rollover = h("input", { class: "num touch", value: s.day_rollover, placeholder: "06:00", maxlength: 5,
      inputmode: "numeric", pattern: "(0[0-9]|1[01]):[0-5][0-9]" });
    // Warning steps for the Schedule card: one list for every dashboard.
    const flashSet = s.schedule_warn_flash_minutes || [];
    const warn = h("input", { class: "touch", value: (s.schedule_warn_minutes || [15, 5]).map((n) => n + (flashSet.indexOf(n) >= 0 ? "!" : "")).join(", "),
      placeholder: "15, 5, 1!", maxlength: 60, autocomplete: "off", spellcheck: false });
    const WARN_ERROR = "Type 1 to 8 different whole numbers of minutes between 1 and 240, separated by commas, for example 15, 5, 1! (a ! after a time makes the card flash at that time). Nothing has been changed.";
    const parseWarn = () => {
      const parts = String(warn.value).split(",").map((x) => x.trim()).filter((x) => x !== "");
      const m = parts.map((x) => /^(\d{1,3})\s*(!?)$/.exec(x));
      if (!m.length || m.length > 8 || m.some((r) => !r)) return null;
      const nums = m.map((r) => Number(r[1]));
      if (nums.some((n) => !(n >= 1 && n <= 240)) || new Set(nums).size !== nums.length) return null;
      return { minutes: nums.slice().sort((x, y) => y - x), flash: nums.filter((n, i) => m[i][2] === "!").sort((x, y) => y - x) };
    };
    // The server's 422 text is for developers; say it in crew words. Nothing typed is echoed back.
    const siteError = (err) => {
      const m = String(err.message || "");
      if (err.status === 422 && m.indexOf("timezone:") === 0) return "That time zone isn't recognised. Pick one from the list, or type a name like Europe/London. Nothing has been changed.";
      if (err.status === 422 && m.indexOf("schedule_warn_minutes:") === 0) return WARN_ERROR;
      if (err.status === 422 && m.indexOf("day_rollover:") === 0) return "Type the new-day start as HH:MM between 00:00 and 11:59, for example 03:00. Nothing has been changed.";
      return m;
    };
    const save = (overrides) => { const wm = parseWarn(); if (!wm) { toast(WARN_ERROR, true); return Promise.resolve(); }
      return api("PUT", "/api/admin/site", Object.assign({
      name: val(name), altitude_m: Number(alt.value), reference_distance_m: Number(dist.value),
      stale_after_s: Number(stale.value), smoothing_tau_s: Number(tau.value), outlier_reject: outl.checked,
      timezone: val(tz), day_rollover: val(rollover), schedule_warn_minutes: wm.minutes, schedule_warn_flash_minutes: wm.flash,
    }, overrides || {})).then(() => { toast("Site saved"); return refresh(); }, (err) => toast(siteError(err), true)); };
    let tzNote = null;
    if (!s.timezone) {
      const t = (snap.site && snap.site.time) || {};
      let browserZone = "";
      try { browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { /* old browser */ }
      tzNote = h("p", { class: "warn-text", role: "status" },
        `Time zone not set. Dashboards use the clock of the computer running Stagewatch (${SW.fmtOffset(t.utc_offset_s || 0)} now). `,
        browserZone ? h("button", { type: "button", class: "touch", onclick: () => save({ timezone: browserZone }) }, `Use ${browserZone} from this browser`) : null);
    }
    const altHelper = altitudeHelper(alt, save);
    return card("Site",
      h("div", { class: "row" },
        field("Site / show name", name),
        field("Altitude (m): for the barometer, and the speed of sound without a pressure sensor", alt),
        field("Reference distance (m) for Δ ms", dist),
        field("Stale after (s)", stale),
        field("Smoothing τ (s)", tau),
        field("Reject outliers (≥3 sensors)", outl)),
      altHelper,
      h("div", { class: "row", style: "margin-top:10px" },
        field("Time zone", tz), tzList,
        field("New show day starts at (HH:MM, 24-hour)", rollover)),
      h("p", { class: "muted hint" }, "All times on dashboards use this time zone, whatever the tablet is set to. A show day runs until the start time next morning, so 01:30 still counts as the night before. Use 03:00 or later for the new day, to stay clear of the hour when the clocks change."),
      h("div", { class: "row", style: "margin-top:10px" },
        field("Warning times (minutes)", warn)),
      h("p", { class: "muted hint" }, "Add ! after a time to flash the card at that time. Applies to every dashboard. The schedule card goes amber at the first time before an item ends and orange at the later ones. The next item turns amber within the last time."),
      tzNote,
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { class: "primary", onclick: () => save() }, "Save site")));
  }

  // "Set altitude from today's sea-level pressure": type the sea-level pressure (QNH) from the Met
  // Office, a weather app or the nearest airport; the server works out the altitude that makes the
  // barometer read that figure and sends it back for a look. Nothing is saved until Save is pressed
  // (it then uses the ordinary Save site path).
  function altitudeHelper(altInput, save) {
    const qnh = h("input", { class: "num touch", inputmode: "decimal", placeholder: "1,013.2", maxlength: 9, autocomplete: "off", "aria-label": "Sea-level pressure in hPa" });
    const out = h("div", { role: "status" });
    const panel = h("div", { class: "card-inset", hidden: true },
      h("p", { class: "muted hint" }, "Use the pressure at sea level from the Met Office, a weather app or the nearest airport METAR (QNH), taken in the last hour and within about 20 km. This sets an altitude that makes the barometer read that figure; it may differ a little from the surveyed height. If you know your true height and the readings are off, use the pressure offset in the sensor settings instead."),
      h("div", { class: "row" }, field("Sea-level pressure (hPa)", qnh),
        h("button", { type: "button", class: "touch", style: "align-self:flex-end", onclick: work }, "Work out altitude"),
        h("button", { type: "button", class: "touch", style: "align-self:flex-end", onclick: () => { panel.hidden = true; out.replaceChildren(); } }, "Cancel")),
      out);
    async function work() {
      const n = Number(String(qnh.value).replace(/[,\s]/g, ""));
      if (!isFinite(n) || String(qnh.value).trim() === "") { out.replaceChildren(h("p", { class: "error" }, "Type the pressure as a number of hPa, for example 1,013.2. Nothing has been changed.")); return; }
      try {
        const r = await api("POST", "/api/admin/site/altitude-from-pressure", { qnh_hpa: n });
        out.replaceChildren(
          h("p", {}, `Pressure measured here: ${SW.num(r.station_hpa, 1)} hPa (${r.sensors_used} sensor${r.sensors_used === 1 ? "" : "s"}, ${r.station_age_s} s ago). Sea-level pressure you entered: ${SW.num(n, 1)} hPa. `,
            "That gives an altitude of ", h("strong", {}, `${SW.num(r.altitude_m, 0)} m`), ` (now set to ${SW.num(r.current_altitude_m, 0)} m). Save ${SW.num(r.altitude_m, 0)} m?`),
          ...r.warnings.map((w) => h("p", { class: "warn-text" }, `▲ ${w}`)),
          h("div", { class: "row" },
            h("button", { type: "button", class: "primary touch", onclick: () => { altInput.value = r.altitude_m; save({ altitude_m: r.altitude_m }); } }, `Save ${SW.num(r.altitude_m, 0)} m`),
            h("button", { type: "button", class: "touch", onclick: () => { out.replaceChildren(); } }, "Cancel")));
      } catch (err) { out.replaceChildren(h("p", { class: "error" }, err.message)); }
    }
    return h("div", { style: "margin-top:10px" },
      h("button", { type: "button", class: "touch", onclick: () => { panel.hidden = !panel.hidden; if (!panel.hidden) qnh.focus(); } }, "Set altitude from today's sea-level pressure…"),
      panel);
  }

  // ------------------------------------------------------------ barometer
  // Settings for the Barometer card. The card itself is added to a dashboard under User dashboards →
  // Edit cards. Advisory only.
  const DEMOS = [["steady", "Settled high"], ["slow_fall", "Slow fall"], ["front", "Front arriving"], ["storm", "Storm"],
    ["rising", "Clearing"], ["dropout", "Sensor dropout 20 min"], ["none", "No pressure sensor"]];
  function barometerCard() {
    const b = admin.config.barometer || {};
    const hemi = h("select", { class: "touch" }, h("option", { value: "north" }, "Northern (UK, Europe, North America)"), h("option", { value: "south" }, "Southern"));
    hemi.value = b.hemisphere === "south" ? "south" : "north";
    const alarm = h("input", { type: "checkbox", checked: !!b.rapid_fall_alarm });
    const thr = h("input", { class: "num touch", type: "number", step: "0.1", min: "1.5", max: "10", value: b.rapid_fall_hpa_3h === undefined ? 3.6 : b.rapid_fall_hpa_3h });
    const demos = admin.emulate ? h("div", { class: "row", style: "margin-top:10px" }, h("span", { class: "muted" }, "Try the card with demo weather (emulate mode only; the dropout starts after 1 minute):"),
      DEMOS.map((d) => h("button", { type: "button", class: "touch", onclick: () => run(() => api("POST", "/api/admin/barometer/demo", { scenario: d[0] }), `${d[1]} started`).then(refresh, () => {}) }, d[1]))) : null;
    return card("Barometer",
      h("p", { class: "muted" }, "Sea-level pressure, how it has changed over 3 hours and a rough outlook, on dashboards that have the Barometer card (User dashboards → Edit cards). It uses the pressure sensors, the site temperature and the altitude in the Site card. It is a guide from pressure at this site only, not a forecast: it does not replace the Met Office forecast and warnings or your event's weather plan."),
      h("div", { class: "row" }, field("Hemisphere (for the summer and winter months)", hemi),
        field("Silent alarm and marker when pressure falls quickly", alarm),
        field("Falls at least this much in 3 h (hPa)", thr),
        h("button", { class: "primary touch", style: "align-self:flex-end", onclick: () => run(() => api("PUT", "/api/admin/barometer", {
          hemisphere: hemi.value, rapid_fall_alarm: alarm.checked, rapid_fall_hpa_3h: Number(thr.value) || 3.6,
        }), "Barometer saved").then(refresh, () => {}) }, "Save")),
      h("p", { class: "muted hint" }, "The falling-quickly warning on the card is always on. The silent alarm adds a line to the alarm list and one marker, and never sounds. 3.6 hPa in 3 hours is the Met Office's \"falling quickly\". The sea-level figure uses the measured temperature; it can differ from airport QNH by a hPa or so."),
      demos);
  }

  function adoptForm(prefill = {}) {
    const host = h("input", { placeholder: "node-name.local or IP", value: prefill.host || "" });
    const port = h("input", { class: "num", type: "number", value: prefill.port || 6053 });
    const name = h("input", { placeholder: "e.g. Stage L node", value: prefill.friendly_name || "" });
    const area = h("input", { placeholder: "e.g. Stage L, FOH, Delay tower 1" });
    const psk = h("input", { type: "password", placeholder: prefill.encrypted ? "required: api encryption key" : "api encryption key (if set)", autocomplete: "off" });
    return h("form", { class: "row", onsubmit: (ev) => {
      ev.preventDefault();
      run(() => api("POST", "/api/admin/esphome/adopt", {
        host: val(host), port: Number(port.value), name: val(name), area: val(area), noise_psk: val(psk),
      }), "Node adopted").then(refresh);
    } }, field("Host", host), field("Port", port), field("Name", name), field("Area", area),
    field("Encryption key", psk), h("button", { class: "primary", type: "submit", style: "align-self:flex-end" }, "Adopt"));
  }

  function devicesCard() {
    const discovered = admin.discovered.filter((d) => !d.adopted);
    const discTable = discovered.length ? h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Discovered node"), h("th", {}, "Address"), h("th", {}, "Board"), h("th", {}, ""))),
      h("tbody", {}, discovered.map((d) => h("tr", {},
        h("td", {}, d.friendly_name || d.name, h("div", { class: "muted", style: "font-size:12px" }, d.host)),
        h("td", {}, `${d.address}:${d.port}`),
        h("td", {}, d.board, d.encrypted ? h("div", { class: "muted", style: "font-size:12px" }, "encrypted") : null),
        h("td", {}, h("div", { class: "row" },
          h("button", { class: "small", onclick: (ev) => {
            const row = ev.target.closest("tr");
            const formRow = h("tr", {}, h("td", { colspan: 4 }, adoptForm(d)));
            row.after(formRow);
          } }, "Adopt…"),
          h("button", { class: "small", title: "Hide this node from the list. It is not connected to either way.",
            onclick: () => run(() => api("POST", "/api/admin/esphome/ignore", { key: d.key }), "Node ignored").then(refresh) }, "Ignore")))))))
      : h("p", { class: "muted" }, snap && admin.integrations.some((i) => i.emulate)
        ? "Emulate mode: discovery is off; emulated nodes are shown below."
        : "No unadopted ESPHome nodes found on the network yet (mDNS). You can add one by host/IP below.");

    const ignored = admin.ignored || [];
    const ignoredBlock = ignored.length ? h("div", { style: "margin-top:8px" },
      h("button", { class: "small", onclick: () => { showIgnored = !showIgnored; render(); } },
        showIgnored ? `Hide ignored (${ignored.length})` : `Show ignored (${ignored.length})`),
      showIgnored ? h("div", { class: "table-scroll" }, h("table", {},
        h("tbody", {}, ignored.map((g) => h("tr", {},
          h("td", {}, g.friendly_name || g.name || "Not on the network now",
            h("div", { class: "muted", style: "font-size:12px" }, g.host || g.key)),
          h("td", {}, h("button", { class: "small",
            onclick: () => run(() => api("POST", "/api/admin/esphome/unignore", { key: g.key }), "Node unignored").then(refresh) }, "Unignore")))))))
        : null) : null;

    // Where a node is: the IP it is connected on now, and the name it was adopted with. Admin only.
    const nodeAddress = (w) => {
      if (!w || (!w.address && !w.host)) return h("span", { class: "muted" }, "-");
      return h("div", {}, h("div", { style: "font-variant-numeric:tabular-nums" }, w.address || "Not connected"),
        w.host && w.host !== w.address ? h("div", { class: "muted", style: "font-size:12px" }, w.host) : null);
    };
    const devices = snap.devices.filter((d) => d.id !== "site" && d.category !== "service");   // services (Ontime) are listed under Integrations
    const devTable = h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Area"), h("th", {}, "Status"), h("th", {}, "Address"), h("th", {}, "Model"), h("th", {}, ""))),
      h("tbody", {}, devices.map((d) => {
        const name = h("input", { value: d.name });
        const area = h("input", { value: d.area });
        return h("tr", {},
          h("td", {}, name, h("div", { class: "muted", style: "font-size:12px" }, d.id)),
          h("td", {}, area),
          h("td", {}, h("span", { class: `status ${d.status}` }, d.status), d.status_detail ? h("div", { class: "muted", style: "font-size:12px" }, d.status_detail) : null),
          h("td", {}, nodeAddress(admin.hardware && admin.hardware.devices && admin.hardware.devices[d.id])),
          h("td", {}, d.model),
          h("td", {}, h("div", { class: "row" },
            h("button", { class: "small", onclick: () => run(() => api("PATCH", `/api/admin/devices/${encodeURIComponent(d.id)}`, { name: val(name), area: val(area) }), "Saved").then(refresh) }, "Save"),
            h("button", { class: "small danger", onclick: () => confirm(`Remove ${d.name}? History is kept.`) && run(() => api("DELETE", `/api/admin/devices/${encodeURIComponent(d.id)}`), "Removed").then(refresh) }, "Remove"))));
      })));

    return card("ESPHome nodes",
      h("div", { class: "table-scroll" }, discTable),
      ignoredBlock,
      h("h3", { class: "muted", style: "font-size:13px;margin:14px 0 6px" }, "ADD BY HOST / IP"),
      adoptForm(),
      h("h3", { class: "muted", style: "font-size:13px;margin:14px 0 6px" }, "ADOPTED"),
      h("div", { class: "table-scroll" }, devices.length ? devTable : h("p", { class: "muted" }, "None yet.")));
  }

  // Which sensor groups are open, remembered per node in this browser. Kept in memory too, so a
  // re-render after Save keeps the state even when the browser has no storage.
  // The same store and buttons serve the Sensors, Connect a tablet and Software cards.
  function foldStore(storageKey) {
    let state = null;
    const load = () => {
      if (state) return state;
      state = {};
      try {
        const o = JSON.parse(localStorage.getItem(storageKey) || "{}");
        if (o && typeof o === "object") state = o;
      } catch (_) { /* no storage: groups use their default */ }
      return state;
    };
    const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) { /* ignore */ } };
    const store = {
      state: load,
      save,
      isOpen: (id, dflt) => (Object.prototype.hasOwnProperty.call(load(), id) ? !!state[id] : dflt),
      // Set up a <details>: open by the remembered choice (else dflt); `force` keeps it open and
      // remembers nothing. Only a person's own toggle is remembered, never a default.
      bind(d, id, dflt, force) {
        let skip = 0;
        if (force || store.isOpen(id, dflt)) { d.open = true; skip = 1; }   // this change fires one toggle event: ignore it
        d.addEventListener("toggle", () => {
          if (skip) { skip = 0; return; }
          if (force) { d.open = true; return; }
          load()[id] = d.open; save();
        });
        return d;
      },
      setAll(els, ids, open) { els.forEach((d, i) => { d.open = open; load()[ids[i]] = open; }); save(); },
    };
    return store;
  }
  // Expand all / Collapse all for a list of folds.
  const foldButtons = (onAll) => h("div", { class: "row sg-buttons" },
    h("button", { class: "touch", onclick: () => onAll(true) }, "Expand all"),
    h("button", { class: "touch", onclick: () => onAll(false) }, "Collapse all"));
  const sensorFolds = foldStore("sw.admin.sensors.open");
  const connectFolds = foldStore("sw.admin.connect.open2");
  const softwareFolds = foldStore("sw.admin.software.open");

  function entitiesCard() {
    const ents = snap.entities.filter((e) => !e.derived);
    const settingsOf = (e) => (admin.hardware && admin.hardware.settings[e.id]) || admin.config.entities[e.id]
      || { offset: 0, include_in_average: true };
    // Same order as the adopted-nodes list; anything else (site rows) goes last, in a "Site" group.
    const devs = snap.devices.filter((d) => d.id !== "site" && d.category !== "service");
    const groups = devs.map((d) => ({ id: d.id, name: d.name, status: d.status, ents: ents.filter((e) => e.device_id === d.id) }));
    const known = new Set(devs.map((d) => d.id));
    const rest = ents.filter((e) => !known.has(e.device_id));
    if (rest.length) groups.push({ id: "site", name: "Site", status: "", ents: rest });
    const shown = groups.filter((g) => g.ents.length);
    const state = sensorFolds.state();

    const offsetText = (e) => {
      const o = Number(settingsOf(e).offset) || 0;
      if (!o) return "";
      const amount = SW.OFFSET_KINDS.indexOf(e.kind) >= 0 ? SW.fmtCalOffset(e.kind, o) : `${SW.signed(o, 1)} ${e.unit}`.trim();
      return `${e.name} ${amount}`;
    };
    // Inside a node the entity label is enough: drop the node's name from the front of it.
    const shortName = (g, e) => {
      const n = e.name || e.kind;
      return n.indexOf(g.name + " ") === 0 && n.length > g.name.length + 1 ? n.slice(g.name.length + 1) : n;
    };

    const groupEl = (g) => {
      const offs = g.ents.map(offsetText).filter(Boolean);
      const bad = g.status && g.status !== "ok";
      const defaultOpen = offs.length > 0 || !!bad;
      const rows = g.ents.map((e) => {
        const s = settingsOf(e);
        const label = shortName(g, e);
        const off = h("input", { class: "num", type: "number", step: "0.01", value: s.offset, "aria-label": `${g.name} ${label} offset` });
        const inc = h("input", { type: "checkbox", checked: s.include_in_average, "aria-label": `${g.name} ${label} in site average` });
        return h("tr", { id: `sensor-row-${e.id}` },
          h("td", {}, label, h("div", { class: "muted", style: "font-size:12px" }, e.id)),
          h("td", { class: "num", dataset: { live: e.id } }, fmt(e.kind, e.value)),
          h("td", {}, off, h("span", { class: "muted" }, " ", e.unit)),
          h("td", {}, inc),
          h("td", {}, h("button", { class: "small", "aria-label": `Save ${g.name} ${label}`, onclick: () => {
            const y = window.scrollY;
            run(() => api("PUT", `/api/admin/entities/${encodeURIComponent(e.id)}`,
              { offset: Number(off.value) || 0, include_in_average: inc.checked }), "Saved").then(refresh).then(() => window.scrollTo(0, y));
          } }, "Save")));
      });
      const d = h("details", { class: "sensor-group", id: `sensor-group-${g.id}` },
        h("summary", {},
          h("span", { class: "sg-name" }, g.name),
          g.status ? h("span", { class: `status ${g.status}` }, g.status) : null,
          h("span", { class: "muted" }, `${g.ents.length} ${g.ents.length === 1 ? "sensor" : "sensors"}`),
          offs.length ? h("span", { class: "muted sg-offs" }, `offset: ${offs.join(", ")}`) : null),
        h("div", { class: "table-scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Sensor"), h("th", { class: "num" }, "Value"), h("th", {}, "Offset"), h("th", {}, "Average"), h("th", {}, ""))),
          h("tbody", {}, rows))));
      d.open = Object.prototype.hasOwnProperty.call(state, g.id) ? !!state[g.id] : defaultOpen;
      d.addEventListener("toggle", () => { state[g.id] = d.open; sensorFolds.save(); });
      return d;
    };
    const els = shown.map(groupEl);
    const setAll = (open) => sensorFolds.setAll(els, shown.map((g) => g.id), open);
    return card("Sensors: calibration & averaging",
      h("p", { class: "muted" }, "Offset is added to every reading (compare against a reference such as a Kestrel). Pressure offsets are in Pa (1 hPa = 100 Pa). Untick to leave a sensor out of the site average, e.g. one in direct sun."),
      shown.length ? foldButtons(setAll) : null,
      shown.length ? els : h("p", { class: "muted" }, "No sensors yet. Add a node under ESPHome nodes."));
  }

  function thresholdsCard() {
    const entityOpts = snap.entities.filter((e) => e.kind !== "contact")
      .map((e) => h("option", { value: e.id }, e.derived ? `Site: ${e.name}` : e.id));
    const tbody = h("tbody");
    const addRow = (t = {}) => {
      const sel = h("select", {}, entityOpts.map((o) => o.cloneNode(true)));
      sel.value = t.entity || "site.temperature";
      const label = h("input", { value: t.label || "", placeholder: "label" });
      const above = h("input", { class: "num", type: "number", step: "any", value: t.above ?? "" });
      const below = h("input", { class: "num", type: "number", step: "any", value: t.below ?? "" });
      const level = h("select", {}, [1, 2, 3].map((l) => h("option", { value: l }, ["", "Advisory", "Alert", "Stop"][l])));
      level.value = String(t.level || 2);
      const hyst = h("input", { class: "num", type: "number", step: "any", value: t.hysteresis ?? 0 });
      const hold = h("input", { class: "num", type: "number", step: "1", value: t.hold_s ?? 0 });
      const en = h("input", { type: "checkbox", checked: t.enabled !== false });
      const tr = h("tr", {}, h("td", {}, sel), h("td", {}, label), h("td", {}, above), h("td", {}, below),
        h("td", {}, level), h("td", {}, hyst), h("td", {}, hold), h("td", {}, en),
        h("td", {}, h("button", { class: "small danger", onclick: () => tr.remove() }, "✕")));
      tr._read = (i) => ({ id: t.id || `t${Date.now().toString(36)}${i}`, entity: sel.value, label: val(label),
        above: numOrNull(above), below: numOrNull(below), level: Number(level.value),
        hysteresis: Number(hyst.value) || 0, hold_s: Number(hold.value) || 0, enabled: en.checked });
      tbody.append(tr);
    };
    admin.config.thresholds.forEach(addRow);
    return card("Threshold alarms",
      h("p", { class: "muted" }, "Values in display units except pressure (Pa). Hysteresis: how far back past the limit before it clears. Hold: seconds the condition must last before alarming. Alert/Stop alarms also drop a marker on the timeline."),
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Entity", "Label", "Above", "Below", "Level", "Hyst.", "Hold s", "On", ""].map((x) => h("th", {}, x)))), tbody)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { onclick: () => addRow() }, "Add threshold"),
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/thresholds",
          [...tbody.children].map((tr, i) => tr._read(i))), "Thresholds saved").then(refresh) }, "Save thresholds")));
  }

  // Card names and one-line hints for the "Edit cards" panel (ids: core/cards.py).
  const CARD_INFO = {
    env_tiles: ["Site readings", "Tiles for temperature, humidity, pressure, speed of sound and dew point."],
    schedule: ["Schedule", "What is on now and next, and the running order. Stays hidden until the show has a schedule."],
    chart: ["History chart", "Readings over time, with markers."],
    markers: ["Markers", "The marker list, the Add marker box, and how far things have drifted since a marker."],
    sensors: ["Sensor nodes", "Each sensor node, whether it is working, and its latest readings."],
    wall_clock: ["Wall Clock", "The time of day, as plain digits, an LED ring or 7-segment digits. Choose the source in the Wall Clock settings."],
    ontime_timer: ["Ontime Timer", "The countdown Ontime is running, with the event title. Read from Ontime; set its address in the Wall Clock and Ontime Timer settings."],
    barometer: ["Barometer", "Sea-level pressure dial, 3-hour trend and a rough outlook. A guide only, not a forecast. Needs a pressure sensor (a BME280 node)."],
    connect_footer: ["Open on a tablet", "This dashboard's address and a QR code, below all the other cards."],
  };
  const openCardPanels = new Set();   // slugs whose "Edit cards" panel stays open across a refresh

  // The "Edit cards" panel for one dashboard: which cards it shows, in what order, and its stage.
  // Returns {el, read(), setDefaults(layout), count()}.
  function cardsEditor(d, onChange) {
    const known = (admin.cards && admin.cards.known) || Object.keys(CARD_INFO);
    let items = [];
    const ul = h("ul", { class: "card-picker" });
    const fill = (list) => {
      const on = list.filter((id, i) => known.indexOf(id) >= 0 && list.indexOf(id) === i);
      items = on.map((id) => ({ id, on: true })).concat(known.filter((id) => on.indexOf(id) < 0).map((id) => ({ id, on: false })));
      draw();
    };
    const move = (i, dir) => {
      const j = i + dir;
      if (j < 0 || j >= items.length) return;
      const t = items[i]; items[i] = items[j]; items[j] = t;
      draw();
      // keep the keyboard on the card that moved
      const btn = ul.children[j] && ul.children[j].querySelector(dir < 0 ? ".card-up" : ".card-down");
      if (btn && !btn.disabled) btn.focus();
    };
    const draw = () => {
      ul.replaceChildren(...items.map((it, i) => {
        const info = CARD_INFO[it.id] || [it.id, ""];
        const box = h("input", { type: "checkbox", checked: it.on, onchange: () => { it.on = box.checked; if (lookField) lookField.style.display = box.checked ? "" : "none"; onChange(); } });
        const lookField = it.id === "wall_clock" ? h("label", { class: "field card-look", style: it.on ? "" : "display:none" }, h("span", {}, "Wall Clock look"), clockStyle) : null;
        return h("li", {},
          h("label", { class: "card-pick" }, box, h("span", {}, h("strong", {}, info[0]), info[1] ? h("span", { class: "muted" }, info[1]) : null)),
          lookField,
          h("button", { type: "button", class: "card-up", "aria-label": `Move ${info[0]} up`, title: "Move up", disabled: i === 0, onclick: () => move(i, -1) }, "▲"),
          h("button", { type: "button", class: "card-down", "aria-label": `Move ${info[0]} down`, title: "Move down", disabled: i === items.length - 1, onclick: () => move(i, 1) }, "▼"));
      }));
      onChange();
    };
    const stage = h("input", { class: "touch", value: d.stage || "", maxlength: 40, list: "stage-list", placeholder: "e.g. Main stage", autocomplete: "off" });
    const clockStyle = h("select", { class: "touch" }, h("option", { value: "digits" }, "Plain digits"), h("option", { value: "ring" }, "LED ring"), h("option", { value: "segments" }, "7-segment digits"));
    clockStyle.value = ["ring", "segments"].indexOf(d.clock_style) >= 0 ? d.clock_style : "digits";
    // Ids from a newer Stagewatch (kept in the settings after a downgrade) can't be saved by this one.
    const newer = (d.cards || []).filter((id) => known.indexOf(id) < 0);
    fill(d.cards || (admin.cards && admin.cards.defaults.tablet) || []);
    const el = h("div", { class: "cards-panel" },
      h("p", { class: "muted hint" }, "Tick the cards this dashboard shows. Use ▲ and ▼ to change the order, top to bottom. With no cards ticked, the screen shows only alarms. Wall Clock is never switched on by default: tick it here if you want it."),
      newer.length ? h("p", { class: "warn-text hint" }, `This dashboard also lists cards from a newer version of Stagewatch (${newer.join(", ")}). This version can't show them, and saving here removes them.`) : null,
      ul,
      h("div", { class: "row", style: "margin-top:10px" },
        field("Stage", stage)),
      h("p", { class: "muted hint" }, "Stage: which stage this screen follows, for cards that show one stage (like the schedule). Leave it empty to show every stage. The Wall Clock look (beside the Wall Clock card) is how that card is drawn on this screen. The ring and 7-segment looks are always red on black."));
    return {
      el,
      read: () => ({ cards: items.filter((it) => it.on).map((it) => it.id), stage: val(stage), clock_style: clockStyle.value }),
      setDefaults: (layout) => fill((admin.cards && admin.cards.defaults[layout]) || []),
      count: () => items.filter((it) => it.on).length,
    };
  }

  function dashboardsCard() {
    const tbody = h("tbody");
    const addRow = (d = {}) => {
      const isNew = !d.slug;
      const slug = h("input", { value: d.slug || "", placeholder: "url-name" });
      const title = h("input", { value: d.title || "" });
      const layout = h("select", {}, ["tablet", "phone", "wall"].map((l) => h("option", { value: l }, l)));
      layout.value = d.layout || "tablet";
      const mk = h("input", { type: "checkbox", checked: d.allow_marker !== false });
      const ack = h("input", { type: "checkbox", checked: !!d.allow_ack });
      const link = d.slug ? h("a", { href: `/d/${d.slug}`, target: "_blank" }, "open") : "";
      const editBtn = h("button", { type: "button", class: "touch", "aria-expanded": "false" });
      const panelRow = h("tr", { class: "cards-panel-row", hidden: true });
      let editor = null;
      const label = () => { if (editor) editBtn.textContent = `Edit cards (${editor.count()})`; };
      editor = cardsEditor(isNew ? { cards: (admin.cards && admin.cards.defaults.tablet) || [] } : d, label);
      label();
      panelRow.append(h("td", { colspan: 8 }, editor.el));
      const setOpen = (open) => {
        panelRow.hidden = !open;
        editBtn.setAttribute("aria-expanded", open ? "true" : "false");
        if (d.slug) { if (open) openCardPanels.add(d.slug); else openCardPanels.delete(d.slug); }
      };
      editBtn.onclick = () => setOpen(panelRow.hidden);
      // A new dashboard starts with its layout's cards; changing its layout picks that layout's set.
      if (isNew) layout.onchange = () => editor.setDefaults(layout.value);
      const tr = h("tr", {}, h("td", {}, slug), h("td", {}, title), h("td", {}, layout), h("td", {}, mk), h("td", {}, ack),
        h("td", {}, editBtn), h("td", {}, link),
        h("td", {}, h("button", { class: "small danger", onclick: () => { tr.remove(); panelRow.remove(); } }, "✕")));
      tr._read = () => Object.assign({ slug: val(slug), title: val(title), layout: layout.value, allow_marker: mk.checked, allow_ack: ack.checked }, editor.read());
      tbody.append(tr, panelRow);
      setOpen(isNew || openCardPanels.has(d.slug));
    };
    admin.config.dashboards.forEach(addRow);
    const stages = admin.stages || [];
    return card("User dashboards",
      h("p", { class: "muted" }, "Each dashboard has its own URL (/d/<name>) for tablets, phones and kiosk displays. Users can view without logging in; tick what they may do. Use Edit cards to choose what each one shows."),
      h("datalist", { id: "stage-list" }, stages.map((s) => h("option", { value: s }))),
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["URL name", "Title", "Layout", "Add markers", "Ack alarms", "Cards", "", ""].map((x) => h("th", {}, x)))), tbody)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { onclick: () => addRow() }, "Add dashboard"),
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/dashboards",
          [...tbody.children].filter((tr) => tr._read).map((tr) => tr._read())), "Dashboards saved").then(refresh) }, "Save dashboards")));
  }

  function oscCard() {
    const o = admin.config.osc_out;
    const tbody = h("tbody");
    const addRow = (d = {}) => {
      const host = h("input", { value: d.host || "", placeholder: "IP or hostname" });
      const port = h("input", { class: "num", type: "number", value: d.port || 9000 });
      const en = h("input", { type: "checkbox", checked: d.enabled !== false });
      const tr = h("tr", {}, h("td", {}, host), h("td", {}, port), h("td", {}, en),
        h("td", {}, h("button", { class: "small danger", onclick: () => tr.remove() }, "✕")));
      tr._read = () => ({ host: val(host), port: Number(port.value), enabled: en.checked });
      tbody.append(tr);
    };
    o.destinations.forEach(addRow);
    const perNode = h("input", { type: "checkbox", checked: o.per_node });
    const rate = h("input", { class: "num", type: "number", step: "0.5", value: o.rate_hz });
    const info = admin.integrations.find((i) => i.manifest.domain === "osc_out") || {};
    return card("OSC output",
      h("p", { class: "muted" }, "Sends /stagewatch/avg/env [temp °C, RH %, pressure Pa, c m/s, sensors] and /stagewatch/alarm [level, sounding]."),
      h("table", {}, h("thead", {}, h("tr", {}, ["Host", "Port", "On", ""].map((x) => h("th", {}, x)))), tbody),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { onclick: () => addRow() }, "Add destination"),
        field("Per-node messages", perNode), field("Rate (Hz)", rate),
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/osc", {
          destinations: [...tbody.children].map((tr) => tr._read()).filter((d) => d.host),
          per_node: perNode.checked, rate_hz: Number(rate.value) || 1,
        }), "OSC saved").then(refresh) }, "Save OSC")),
      info.last_error ? h("p", { class: "error" }, info.last_error) : null);
  }

  // ------------------------------------------------------------ wall clock
  // Where the Wall Clock card gets its time: this computer, or Ontime. One source for the whole
  // installation; Stagewatch never switches by itself. Read-only: it only listens. Ontime is only
  // contacted while a dashboard has the Wall Clock card. The look (digits, ring, segments) is
  // chosen per dashboard under User dashboards → Edit cards.
  function wallClockCard() {
    const w = admin.config.wall_clock, st = admin.wall_clock || {};
    const d = w.display || {};
    const url = h("input", { value: w.ontime_url, placeholder: "http://127.0.0.1:4001", autocomplete: "off", spellcheck: "false", style: "min-width:260px" });
    const warn = h("input", { class: "num", type: "number", step: "0.5", min: "1", max: "60", value: w.warn_offset_s });
    const source = h("select", {}, h("option", { value: "pc" }, "Stagewatch PC"), h("option", { value: "ontime" }, "Ontime"));
    source.value = w.source === "ontime" ? "ontime" : "pc";
    const hour12 = h("select", {}, h("option", { value: "24" }, "24-hour"), h("option", { value: "12" }, "12-hour (am/pm)"));
    hour12.value = d.hour12 ? "12" : "24";
    const showDate = h("input", { type: "checkbox", checked: !!d.show_date });
    const blink = h("input", { type: "checkbox", checked: !!d.colon_blink });
    // The address and the test serve both Ontime cards: show them when the clock uses Ontime or
    // any dashboard has the Ontime Timer card. The warning limit is the clock's alone.
    const timerOn = (admin.config.dashboards || []).some((x) => (x.cards || []).indexOf("ontime_timer") >= 0);
    const addressField = field(timerOn ? "Ontime address (also used by the Ontime Timer card)" : "Ontime address", url);
    const warnField = field("Warn if more than this many seconds out", warn);
    const ontimeOnly = [addressField, warnField];
    const result = h("p", { class: "muted", role: "status" });
    const testBtn = h("button", { style: "align-self:flex-end", onclick: async (ev) => {
      const btn = ev.target; btn.disabled = true; result.textContent = "Testing…";
      try {
        const r = await api("POST", "/api/admin/wall-clock/test", { ontime_url: val(url) });
        result.textContent = r.ok ? `Ontime ${r.version} answered.` : r.message;
      } catch (err) { result.textContent = err.message; }
      finally { btn.disabled = false; }
    } }, "Test connection");
    // Only Ontime has an address to set and test (display:none, because label.field would override [hidden]).
    const showOntime = () => {
      const clockOnOntime = source.value === "ontime";
      addressField.style.display = testBtn.style.display = clockOnOntime || timerOn ? "" : "none";
      warnField.style.display = clockOnOntime ? "" : "none";
    };
    source.onchange = showOntime;
    showOntime();
    const lines = [];
    if (!st.active) {
      lines.push(st.card_assigned ? "Starting…"
        : "Not running. It starts when a dashboard has the Wall Clock card (User dashboards → Edit cards).");
    } else if (st.source === "pc") {
      lines.push("Using this computer's clock");
    } else if (st.status === "ok") {
      lines.push(`Connected${st.transport ? ` (${st.transport})` : ""}`);
      if (st.last_message) lines.push(`Last message ${SW.fmtTime(st.last_message, { seconds: true })}`);
      if (st.version) lines.push(`Ontime version ${st.version}`);
    } else {
      lines.push(`▲ Not connected${st.detail ? `: ${st.detail}` : ""}`);
      if (st.version) lines.push(`Ontime version ${st.version}`);
    }
    return card("Wall Clock",
      h("p", { class: "muted" }, "Shows the time on dashboards that have the Wall Clock card. Choose where the time comes from: this computer, or Ontime (then it warns if Ontime differs from Stagewatch). Stagewatch only listens: it never sends anything to Ontime. If the source stops, the clock says so. It never switches to another source by itself."),
      h("div", { class: "row" }, field("Time source", source), ...ontimeOnly, testBtn),
      h("div", { class: "row", style: "margin-top:10px" }, field("Time format", hour12), field("Show the date", showDate), field("Blink the colons", blink),
        h("button", { class: "primary", style: "align-self:flex-end", onclick: () => run(() => api("PUT", "/api/admin/wall-clock", {
          source: source.value, ontime_url: val(url), warn_offset_s: Number(warn.value) || 2,
          display: { hour12: hour12.value === "12", show_date: showDate.checked, ring: d.ring === "fill" ? "fill" : "sweep", colon_blink: blink.checked },
        }), "Wall Clock saved").then(refresh, () => {}) }, "Save")),
      h("p", { class: "muted hint" }, "These apply to every dashboard. The look (plain digits, LED ring or 7-segment) is set for each dashboard under User dashboards → Edit cards. Ring and 7-segment are always red on black."),
      result,
      h("p", { class: "muted" }, lines.join(" · ")));
  }
  // ------------------------------------------------------- ontime timer
  // The Ontime Timer card shows the countdown Ontime is running. It uses the Ontime address from
  // the Wall Clock settings (one connection serves both cards). Read-only: it only listens.
  function ontimeTimerCard() {
    const t = admin.config.ontime_timer || { show_title: true }, st = admin.ontime_timer || {};
    const title = h("input", { type: "checkbox", checked: t.show_title !== false });
    const lines = [];
    if (!st.active) {
      lines.push(st.card_assigned ? "Starting…"
        : "Not running. It starts when a dashboard has the Ontime Timer card (User dashboards → Edit cards).");
    } else if (st.status === "ok") {
      lines.push("Connected");
      if (st.last_message) lines.push(`Last message ${SW.fmtTime(st.last_message, { seconds: true })}`);
    } else {
      lines.push(`▲ Not connected${st.detail ? `: ${st.detail}` : ""}`);
    }
    return card("Ontime Timer",
      h("p", { class: "muted" }, "Shows the countdown Ontime is running on dashboards that have the Ontime Timer card. It reads the Ontime address under Wall Clock, so set and test that first. Stagewatch only listens: it never starts, pauses or changes anything in Ontime. It is Ontime's timer on a screen, not a Stagewatch timer, so don't use it as a cue."),
      h("div", { class: "row" }, field("Show the event title on dashboards", title),
        h("button", { class: "primary", style: "align-self:flex-end", onclick: () => run(() => api("PUT", "/api/admin/ontime-timer", {
          show_title: title.checked,
        }), "Ontime Timer saved").then(refresh, () => {}) }, "Save")),
      h("p", { class: "muted hint" }, "The title is the event's name in Ontime, often an artist. Dashboards are not password protected, so untick this if the name should stay off the screens."),
      h("p", { class: "muted" }, lines.join(" · ")));
  }
  // ------------------------------------------------------- event & show
  // An event (a festival, a tour leg) is a group of show days. Markers, alarms and history
  // belong to the current day. "Next day" keeps the event; "New event…" ends it.
  let showPanel = "";   // "", "next" or "event": which start form is open
  let showBusy = false; // one start at a time from this page (the server refuses a second one too)
  const nameInput = (value, placeholder) => h("input", { value: value || "", placeholder, maxlength: 80, class: "touch", autocomplete: "off" });
  // A date picker where the browser has one; otherwise (old iPads) a text box that takes UK
  // day-first typing. Read it with dayOf(), never .value.
  const dateInput = (value) => {
    const input = h("input", { type: "date", value: value || "", class: "touch", required: true });
    if (input.type !== "date") {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
      input.value = m ? `${m[3]}/${m[2]}/${m[1]}` : "";
      input.placeholder = "dd/mm/yyyy";
      input.inputMode = "numeric";
    }
    return input;
  };
  const dayOf = (input) => SW.parseDay(input.value);
  // The browser draws the date box in its own style, so the chosen day is also written out
  // underneath as "Fri 2 Oct 2026".
  const dayEcho = (input) => (dayOf(input) ? SW.fmtDay(dayOf(input)) : (input.value.trim() ? "Not a date: use dd/mm/yyyy" : ""));
  const dateField = (label, input) => {
    const echo = h("span", { class: "date-echo", "aria-live": "polite" }, dayEcho(input));
    const upd = () => { echo.textContent = dayEcho(input); };
    input.addEventListener("input", upd);
    input.addEventListener("change", upd);
    return h("label", { class: "field" }, `${label} (dd/mm/yyyy)`, input, echo);
  };
  // The server's 422 text is short and fixed; say it in crew words. Nothing typed is echoed back.
  const showError = (err) => {
    const m = String(err.message || "");
    if (err.status === 422) {
      const i = m.indexOf("Value error, ");
      return `${i >= 0 ? m.slice(i + 13).split(";")[0] : "Check the names and the date"}. Nothing has been changed.`;
    }
    return m;
  };
  const showRun = (fn, okMsg) => fn().then((r) => { toast(okMsg); return r; }, (err) => { toast(showError(err), true); throw err; });

  function eventShowCard() {
    const ev = admin.event, show = admin.show, days = admin.show_days;
    const thisEvent = admin.events.find((e) => e.id === ev.id) || { shows: [] };
    const dayNo = thisEvent.shows.length + 1;

    const startShow = (body, okMsg) => {
      if (showBusy) return;
      showBusy = true;
      for (const b of document.querySelectorAll("[data-show-start]")) b.disabled = true;
      showRun(() => api("POST", "/api/admin/shows", Object.assign({ from_show_id: show.id }, body)), okMsg)
        .then(() => { showPanel = ""; }, () => {})
        .then(() => { showBusy = false; return refresh(); });
    };
    // Send a date only when it differs from what the start time gives, so an untouched day
    // keeps following the start time.
    const dayOrNull = (input) => (dayOf(input) && dayOf(input) !== days.today ? dayOf(input) : null);

    // Start forms (each ends in a confirm).
    let panel = null;
    if (showPanel === "next") {
      const name = nameInput(`Day ${dayNo}`, "e.g. Day 2");
      const date = dateInput(days.next);
      panel = h("form", { class: "card-inset", onsubmit: (e) => {
        e.preventDefault();
        if (!val(name) || !dayOf(date)) { toast("Type a name and a date (dd/mm/yyyy).", true); return; }
        if (!confirm(`Start "${val(name)}" (${SW.fmtDay(dayOf(date))}) as the next day of "${ev.name}"?\n\nDashboards switch to the new day: its history, markers and alarm log start empty. "${show.name}" is kept under Previous shows.`)) return;
        startShow({ name: val(name), event: "current", day: dayOrNull(date) }, `Started ${val(name)}`);
      } },
      h("div", { class: "row" }, field("Day name", name), dateField("Date", date)),
      h("p", { class: "muted hint" }, "The date is the show day this new day belongs to. After midnight it still suggests tomorrow's date, not the night you are finishing."),
      h("div", { class: "row" },
        h("button", { class: "primary touch", type: "submit", "data-show-start": "" }, "Start next day"),
        h("button", { class: "touch", type: "button", onclick: () => { showPanel = ""; render(); } }, "Cancel")));
    } else if (showPanel === "event") {
      const evName = nameInput("", "e.g. Summer Festival 2027");
      const name = nameInput("Day 1", "e.g. Day 1");
      const date = dateInput(days.today);
      panel = h("form", { class: "card-inset", onsubmit: (e) => {
        e.preventDefault();
        if (!val(evName) || !val(name) || !dayOf(date)) { toast("Type a name for the new event and its first day, and a date (dd/mm/yyyy).", true); return; }
        if (!confirm(`Start the new event "${val(evName)}"?\n\n"${ev.name}" ends. Dashboards switch to "${val(name)}" (${SW.fmtDay(dayOf(date))}) of the new event: history, markers and alarm log start empty. Everything from "${ev.name}" is kept under Previous shows.`)) return;
        startShow({ name: val(name), event: "new", event_name: val(evName), day: dayOrNull(date) }, `Started ${val(evName)}`);
      } },
      h("div", { class: "row" }, field("New event name", evName), field("First day name", name), dateField("Date", date)),
      h("div", { class: "row" },
        h("button", { class: "primary touch", type: "submit", "data-show-start": "" }, "Start new event"),
        h("button", { class: "touch", type: "button", onclick: () => { showPanel = ""; render(); } }, "Cancel")));
    }

    // Rename the event / the day, and correct the day's date.
    const evName = nameInput(ev.name, "e.g. Summer Festival");
    const dayName = nameInput(show.name, "e.g. Day 2");
    const dayDate = dateInput(show.day);
    const saveEvent = () => {
      if (!val(evName) || val(evName) === ev.name) return;
      showRun(() => api("PATCH", "/api/admin/events/current", { name: val(evName), event_id: ev.id }), "Event renamed").then(refresh, () => {});
    };
    const saveDay = (body) => showRun(() => api("PATCH", "/api/admin/shows/current", Object.assign({ show_id: show.id }, body)), "Day saved").then(refresh, () => {});

    // Previous shows, grouped by event (newest first).
    const prevCount = admin.events.reduce((n, e) => n + e.shows.filter((s) => s.id !== show.id).length, 0);
    const previous = h("details", {}, h("summary", { class: "muted" }, `Previous shows (${prevCount})`),
      admin.events.filter((e) => e.shows.some((s) => s.id !== show.id)).map((e) => h("div", { class: "event-group" },
        h("strong", {}, e.name), e.id === ev.id ? h("span", { class: "muted" }, " (this event)") : null,
        h("ul", {}, e.shows.filter((s) => s.id !== show.id).map((s) => h("li", {},
          `${s.name} · ${SW.fmtDay(s.day)}`,
          h("span", { class: "muted" }, ` (started ${SW.fmtTime(s.started, { date: true })})`)))))));

    return card("Event & show",
      h("p", { class: "event-now" }, "Event: ", h("strong", {}, ev.name)),
      h("p", { class: "event-now" }, "Day: ", h("strong", {}, show.name), ` · ${SW.fmtDay(show.day)}`,
        h("span", { class: "muted" }, ` (started ${SW.fmtTime(show.started, { date: true })})`)),
      h("p", { class: "muted hint" }, "Markers, alarms and history belong to the current day. Dashboards show the event and day under their title."),
      h("div", { class: "row" },
        h("button", { class: "primary touch", type: "button", "data-show-start": "", disabled: showBusy, onclick: () => { showPanel = showPanel === "next" ? "" : "next"; render(); } }, "Next day (same event)"),
        h("button", { class: "touch", type: "button", "data-show-start": "", disabled: showBusy, onclick: () => { showPanel = showPanel === "event" ? "" : "event"; render(); } }, "New event…")),
      panel,
      h("details", {}, h("summary", { class: "muted" }, "Rename or change the date"),
        h("div", { class: "row" }, field("Event name", evName),
          h("button", { class: "touch", type: "button", style: "align-self:flex-end", onclick: saveEvent }, "Save event name")),
        h("div", { class: "row" }, field("Day name", dayName),
          h("button", { class: "touch", type: "button", style: "align-self:flex-end", onclick: () => {
            if (val(dayName) && val(dayName) !== show.name) saveDay({ name: val(dayName) });
          } }, "Save day name")),
        h("div", { class: "row" }, dateField("Day date", dayDate),
          h("button", { class: "touch", type: "button", style: "align-self:flex-end", onclick: () => {
            if (!dayOf(dayDate)) toast("Type a date as dd/mm/yyyy.", true); else if (dayOf(dayDate) !== show.day) saveDay({ day: dayOf(dayDate) });
          } }, "Save date"),
          show.day_set ? h("button", { class: "touch", type: "button", style: "align-self:flex-end", onclick: () => saveDay({ day: null }) }, "Use the start date") : null),
        h("p", { class: "muted hint" }, show.day_set
          ? "You set this date by hand. Use the start date to go back to the date the day started on."
          : "Set from when the day started (a new show day starts at the time in the Site card). Change it if the day was started early or late.")),
      previous);
  }

  function securityCard() {
    const cur = h("input", { type: "password", placeholder: "current PIN", autocomplete: "current-password" });
    const nw = h("input", { type: "password", placeholder: "new PIN", autocomplete: "new-password" });
    return card("Security",
      h("form", { class: "row", onsubmit: (ev) => {
        ev.preventDefault();
        run(() => api("PUT", "/api/admin/pin", { current: cur.value, new: nw.value }), "PIN changed; other admin sessions logged out")
          .then(() => { cur.value = ""; nw.value = ""; });
      } }, cur, nw, h("button", { type: "submit" }, "Change PIN")));
  }

  // ------------------------------------------------------------ schedule
  // A short summary only. The editor is its own page, /schedule (schedule-editor.js).
  let schedData = null;     // the last GET /api/schedule
  let schedSkew = 0;        // the server's clock minus this one, so NOW / NEXT follow the server's time
  async function loadSchedule() {
    schedData = await api("GET", "/api/schedule");
    if (typeof schedData.now === "number") schedSkew = schedData.now - Date.now() / 1000;
  }
  function scheduleCard() {
    const c = card("Schedule");
    c.id = "schedule-admin";
    const items = schedData && Array.isArray(schedData.items) ? schedData.items : [];
    const day = (schedData && schedData.day) || (admin.show && admin.show.day) || "";
    const lines = [];
    if (!schedData) lines.push(h("p", { class: "error" }, "Could not load the schedule. Reload this page to try again."));
    else if (!items.length) lines.push(h("p", { class: "muted" }, "No schedule for this day yet."));
    else {
      const nn = SW.scheduleNowNext(items, Date.now() / 1000 + schedSkew, "");
      const idle = { before: "Not started yet", between: "Nothing on now", over: "Show over" };
      lines.push(h("p", {}, `${day ? `${SW.fmtDay(day)}: ` : ""}${items.length === 1 ? "1 item" : `${SW.num(items.length, 0)} items`}`));
      lines.push(h("p", { class: "sched-sum-now" }, h("strong", {}, "NOW "), nn.current ? nn.current.title : (idle[nn.state] || "Nothing on now")));
      lines.push(h("p", { class: "sched-sum-next" }, h("strong", {}, "NEXT "),
        nn.next ? `${nn.next.title}, ${SW.fmtTime(nn.next.planned_start)}` : "Nothing more today"));
    }
    if (!schedData || !items.length) {
      if (day) lines.unshift(h("p", { class: "muted" }, SW.fmtDay(day)));
    }
    c.append(...lines, h("div", { class: "row", style: "margin-top:10px" }, SW.linkButton("Open schedule editor", "/schedule", true)));
    return c;
  }
  function rerenderScheduleSummary() {
    const old = document.getElementById("schedule-admin");
    if (old) old.replaceWith(scheduleCard());
  }

  function catalogCard() {
    return card("Integrations",
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Integration", "Tier", "Direction", "Protocols", "Runtime"].map((x) => h("th", {}, x)))),
        h("tbody", {}, admin.integrations.map((i) => h("tr", {},
          h("td", {}, h("strong", {}, i.manifest.name), h("div", { class: "muted", style: "font-size:12px" }, i.manifest.description)),
          h("td", {}, i.manifest.tier), h("td", {}, i.manifest.direction), h("td", {}, i.manifest.protocols.join(", ")),
          h("td", { class: "muted" }, Object.entries(i).filter(([k]) => k !== "manifest").map(([k, v]) => `${k}: ${v}`).join(" · "))))))));
  }

  function alarmLogCard() {
    return card("Alarm log (this show)",
      admin.alarm_log.length ? h("div", { class: "table-scroll", style: "max-height:260px;overflow-y:auto" }, h("table", {},
        h("tbody", {}, admin.alarm_log.map((a) => h("tr", {},
          h("td", { class: "muted" }, SW.fmtTime(a.ts, { seconds: true })), h("td", {}, a.event), h("td", {}, ["", "advisory", "alert", "stop"][a.level] || ""), h("td", {}, a.message))))))
        : h("p", { class: "muted" }, "No alarms yet."));
  }

  // ------------------------------------------------------------ software
  // Everything from the server is shown with textContent only (h() creates text nodes):
  // changelogs and commit subjects are untrusted text.
  const swBadge = () => document.getElementById("update-badge");
  const bytes = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MiB` : `${Math.max(1, Math.round((n || 0) / 1024))} KiB`);
  const when = (iso) => { const t = Date.parse(iso); return Number.isNaN(t) ? (iso || "") : SW.fmtTime(t / 1000, { date: true }); };

  // Changelog excerpt + commit subjects as one plain-text block (blank-line runs collapsed).
  const changesText = (last) => [last.changelog, last.commits && last.commits.length ? "Commits:\n" + last.commits.map((c) => `- ${c}`).join("\n") : ""]
    .filter(Boolean).join("\n\n").replace(/\n{3,}/g, "\n\n").trim();

  async function loadSoftware() {
    try { sw = await api("GET", "/api/admin/software"); } catch (_) { sw = null; }
    swBadge().hidden = !(sw && sw.update_available);   // header only, never on user dashboards
    if (sw && sw.restarting) watchRestart(sw.commit);   // page opened/reloaded mid-restart
  }
  function rerenderSoftware() {
    swBadge().hidden = !(sw && sw.update_available);
    const old = document.getElementById("software");
    if (!old) return;
    // Keep the scroll position and, if it can be found again, the focused control.
    const y = window.scrollY;
    const ae = document.activeElement;
    const focusId = ae && ae.id && old.contains(ae) ? ae.id : "";
    old.replaceWith(softwareCard());
    if (focusId) { const f = document.getElementById(focusId); if (f && !f.disabled) f.focus({ preventScroll: true }); }
    window.scrollTo(0, y);
  }
  async function swAction(fn, okMsg) {
    try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
    catch (err) {
      toast(err.status === 429 && err.retryAfter ? `${err.message} (${err.retryAfter} s)` : err.message, true);
      return null;
    } finally { await loadSoftware(); rerenderSoftware(); }
  }

  // Full-screen overlay (blocks stray taps while the server restarts); reloads the page once
  // the server is back.  Also covers a page loaded while the server already reports "restarting".
  let watching = false;
  function watchRestart(startCommit) {
    if (watching) return;
    watching = true;
    const secsEl = h("span", { "aria-hidden": "true" });
    const msg = h("p", { class: "restart-msg" }, "Stagewatch is restarting for an update…", secsEl);
    const sub = h("p", { class: "muted" }, "This takes about a minute. This page reloads by itself.");
    const overlay = h("div", { class: "restart-overlay", role: "alertdialog", "aria-modal": "true", "aria-label": "Restarting for an update" },
      h("div", { class: "restart-box", tabindex: "-1", "aria-live": "polite" }, h("div", { class: "spinner", "aria-hidden": "true" }), msg, sub));
    document.body.append(overlay);
    overlay.firstChild.focus();   // keep keyboard focus off the page underneath
    const t0 = Date.now();
    let wasDown = false;
    const tick = async () => {
      const secs = Math.round((Date.now() - t0) / 1000);
      try {
        const res = await fetch("/api/info", { cache: "no-store", credentials: "same-origin" });
        if (!res.ok) throw new Error("down");
        const info = await res.json();
        // Reload when it went down and came back, the build changed, or it has been up for a long
        // while (a fast restart or a failed update that rolled back to the same build).
        if (wasDown || (info.build && info.build.commit !== startCommit) || secs > 90) { location.reload(); return; }
      } catch (_) { wasDown = true; }
      if (secs > 10 * 60) {
        msg.replaceChildren("Stagewatch has not come back.");
        sub.textContent = "Check the launcher log on the Stagewatch computer (administrators only). Windows: C:\\ProgramData\\Stagewatch\\logs\\launcher.log. Raspberry Pi: /var/lib/stagewatch/logs/launcher.log. Once Stagewatch is back, use Download diagnostics on this page and send the file when asking for help. This page will reload when the server returns.";
      } else secsEl.textContent = ` ${secs} s`;
      setTimeout(tick, 2000);
    };
    setTimeout(tick, 1500);
  }

  // Confirm dialog with PIN step-up.  submit(pin) performs the API call.
  function confirmDialog({ title, lines, pre, warning, confirmLabel, submit }) {
    const pin = h("input", { type: "password", inputmode: "numeric", autocomplete: "current-password", placeholder: "Admin PIN", "aria-label": "Admin PIN", maxlength: 64 });
    const err = h("p", { class: "error", role: "alert" });
    const heading = h("h3", { id: "sw-dlg-title", tabindex: "-1" }, title);
    const dlg = h("dialog", { class: "sw-dialog", "aria-labelledby": "sw-dlg-title" });
    const close = () => { dlg.close(); dlg.remove(); };
    const go = h("button", { class: "primary", type: "submit" }, confirmLabel);
    dlg.append(h("form", { onsubmit: async (ev) => {
      ev.preventDefault();
      if (go.disabled) return;
      err.textContent = "";
      go.disabled = true;
      try { await submit(pin.value); close(); }
      catch (e) { err.textContent = e.message; go.disabled = false; pin.value = ""; pin.focus(); }
    } },
    heading,
    h("dl", { class: "sw-facts" }, lines.flatMap(([k, v, mono]) => [h("dt", {}, k), h("dd", { class: mono ? "mono" : "" }, v)])),
    pre ? h("div", { class: "pre-wrap", tabindex: "0", role: "region", "aria-label": "Changes in this update" }, pre) : null,
    warning ? h("p", { class: "warn-text" }, warning) : null,
    h("p", { class: "muted", style: "font-size:13px" }, "Stagewatch will restart for about a minute; dashboards reconnect by themselves. Re-enter the admin PIN to continue."),
    h("div", { class: "row sw-dlg-actions" }, pin, h("button", { type: "button", onclick: close }, "Cancel"), go),
    err));
    dlg.addEventListener("cancel", () => dlg.remove());   // Escape
    document.body.append(dlg);
    dlg.showModal();
    // On touch devices the on-screen keyboard would hide the changelog: read first, then tap the PIN.
    (matchMedia("(pointer: fine)").matches ? pin : heading).focus();
  }

  function startUpdate(last) {
    const from = last.from_version || sw.version, to = last.target_version || "unknown version";
    confirmDialog({
      title: "Update Stagewatch",
      lines: [["Channel", last.channel === "nightly" ? "Nightly" : "Stable"], ["Version", `${from} → ${to}`], ["Commit", last.target_sha, true]],
      pre: changesText(last) || "(no changelog)",
      warning: last.schema_changed ? "This update changes the data format. A backup is made first and restored automatically if the update fails." : "",
      confirmLabel: "Update now",
      submit: async (pin) => {
        await api("POST", "/api/admin/software/update", { channel: last.channel, target_sha: last.target_sha, pin });
        watchRestart(sw.commit);
      },
    });
  }

  function startRollback(entry) {
    confirmDialog({
      title: "Roll back Stagewatch",
      lines: [["Version", `${entry.to_version || "current"} → ${entry.from_version || "previous"}`]],
      warning: entry.schema_changed ? "The data from before that update is restored. Anything recorded since is set aside in a displaced-… backup folder (kept, not deleted)." : "",
      confirmLabel: "Roll back",
      submit: async (pin) => {
        await api("POST", "/api/admin/software/rollback", { history_id: entry.id, pin });
        watchRestart(sw.commit);
      },
    });
  }

  function softwareCard() {
    const el = (...body) => { const c = card("Software", ...body); c.id = "software"; return c; };
    if (!sw) return el(h("p", { class: "muted" }, "Software status unavailable."));
    const info = h("table", {}, h("tbody", {},
      [["Version", sw.describe], ["Commit", sw.commit_full || sw.commit + (sw.dirty ? " (modified)" : "")],
       ["Channel", sw.channel === "nightly" ? "Nightly" : "Stable"],
       ["Install", sw.managed ? (sw.supervised ? "Managed" : "Managed, not started by the launcher") : "Development / manual"]]
        .map(([k, v]) => h("tr", {}, h("td", { class: "muted" }, k), h("td", { class: k === "Commit" ? "mono" : "" }, v)))));
    if (!sw.mutable) {
      return el(info, h("p", { class: "muted" }, sw.message || "In-app updates are only available on a managed install."));
    }
    const chanName = (c) => (c === "nightly" ? "Nightly" : "Stable");
    const checkedAt = (last) => SW.fmtTime(last.ts, { date: true });
    const chan = h("select", { id: "sw-channel" }, [["stable", "Stable"], ["nightly", "Nightly"]].map(([v, l]) => h("option", { value: v }, l)));
    chan.value = sw.channel;
    chan.onchange = () => swAction(() => api("PUT", "/api/admin/software/channel", { channel: chan.value }), "Channel changed");
    const busy = sw.job.running || sw.restarting;
    const checkBtn = h("button", { id: "sw-check", disabled: busy, onclick: async (ev) => {
      ev.target.disabled = true;
      ev.target.textContent = "Checking…";
      const r = await swAction(() => api("POST", "/api/admin/software/check"));
      if (r && !r.ok) toast(r.message, true);
    } }, busy && sw.job.running ? "Working…" : "Check for updates");

    const last = sw.last_check;
    let result;
    if (!last) result = h("p", { class: "muted" }, "Not checked yet.");
    else if (!last.ok) result = h("p", { class: "error", role: "alert" }, "Check failed: ", last.message, h("span", { class: "muted" }, ` (${checkedAt(last)})`));
    else if (!last.available) result = h("p", {}, h("span", { class: "sw-ok" }, "✓ Up to date"), h("span", { class: "muted" }, ` on the ${chanName(last.channel)} channel (checked ${checkedAt(last)})`));
    else result = h("div", { class: "sw-available" },
      h("h3", {}, "Update available"),
      h("div", { class: "sw-versions" }, `${last.from_version || sw.version} → `, h("strong", {}, last.target_version || "unknown version")),
      h("div", { class: "muted", style: "font-size:12px" }, "Commit"), h("div", { class: "mono" }, last.target_sha),
      last.schema_changed ? h("p", { class: "warn-text" }, "⚠ This update changes the data format; a backup is made first.") : null,
      changesText(last) ? [h("div", { class: "muted", style: "font-size:12px;margin-top:8px" }, "What's changed"),
        h("div", { class: "pre-wrap", tabindex: "0", role: "region", "aria-label": "What's changed" }, changesText(last))] : null,
      h("button", { id: "sw-update", class: "primary sw-go", disabled: sw.restarting, onclick: () => startUpdate(last) }, "Update now…"));

    // Nightly builds keep the release's version number, so show the commit when the versions match.
    const histLabel = (e) => {
      const fv = e.from_version || "?", tv = e.to_version || "?";
      if (fv !== tv || !e.from_commit || !e.to_commit) return `${fv} → ${tv}`;
      return `${fv} (${e.from_commit}) → ${tv} (${e.to_commit})`;
    };
    // A list, not a table: on a phone a wide table hides the Roll back button off-screen.
    const hist = sw.history.length ? h("ul", { class: "sw-hist" }, sw.history.slice(0, 10).map((e) => h("li", {},
      h("div", { class: "sw-hist-main" },
        h("div", {}, h("strong", {}, histLabel(e)), `  ${e.action || ""}: `,
          h("span", { class: e.result === "ok" ? "sw-ok" : "warn-text" }, (e.result === "ok" ? "✓ " : "⚠ ") + e.result), e.reason ? ` (${e.reason})` : ""),
        h("div", { class: "muted", style: "font-size:12px" }, when(e.ts))),
      e.can_rollback ? h("button", { id: `sw-rb-${e.id}`, class: "small danger", disabled: sw.restarting, onclick: () => startRollback(e) }, "Roll back…") : null)))
      : h("p", { class: "muted" }, "No updates yet.");
    const sizeTable = (rows) => h("table", {}, h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, r.id || r.name), h("td", { class: "num" }, bytes(r.size))))));
    const total = (rows) => bytes(rows.reduce((n, r) => n + (r.size || 0), 0));

    // One fold per section, with a one-line status in the summary.
    const updating = sw.job.running || sw.restarting;
    const newest = sw.history[0];
    const available = !!(last && last.ok && last.available);
    let updStatus;
    if (sw.restarting) updStatus = "restarting";
    else if (sw.job.running) updStatus = "working…";
    else if (available) updStatus = `update available ${last.target_version || ""}`.trim();
    else if (!last) updStatus = "not checked yet";
    else if (!last.ok) updStatus = "last check failed";
    else updStatus = `up to date, checked ${SW.fmtTime(last.ts)}`;
    const histFailed = !!(newest && newest.result !== "ok");
    const sections = [
      { key: "installed", label: "Installed", status: sw.describe, dflt: true, body: [info] },
      { key: "updates", label: "Updates", status: updStatus, dflt: true, force: available || updating || !!sw.update_available,
        body: [h("div", { class: "row sw-controls" }, h("label", { class: "field", for: "sw-channel" }, "Channel", chan), checkBtn),
          sw.channel === "nightly" ? h("p", { class: "warn-text" }, "Nightly is bleeding edge, tested automatically only. Don't run it on show days.") : null,
          sw.restarting ? h("p", { class: "warn-text", role: "status" }, "Restarting for an update…") : null,
          result] },
      { key: "history", label: "History", status: sw.history.length ? (histFailed ? "last update failed" : `${sw.history.length} ${sw.history.length === 1 ? "entry" : "entries"}`) : "no updates yet",
        dflt: histFailed, body: [hist] },
      sw.backups.length ? { key: "backups", label: "Data backups", status: `${sw.backups.length}, ${total(sw.backups)}`, dflt: false, body: [sizeTable(sw.backups)] } : null,
      sw.displaced.length ? { key: "displaced", label: "Displaced data", status: `${sw.displaced.length}, ${total(sw.displaced)}`, dflt: false,
        body: [h("p", { class: "muted" }, "Set aside by a restore."), sizeTable(sw.displaced)] } : null,
    ].filter(Boolean);
    const folds = sections.map((s) => {
      const d = h("details", { class: "sensor-group sw-fold", id: `sw-fold-${s.key}` },
        h("summary", { id: `sw-sum-${s.key}` },
          h("span", { class: "sg-name" }, s.label + ":"),
          h("span", { class: `muted${s.key === "updates" && available ? " sw-flag" : ""}` }, s.status)),
        h("div", { class: "sw-fold-body" }, s.body));
      return softwareFolds.bind(d, s.key, s.dflt, s.force);
    });
    return el(foldButtons((open) => softwareFolds.setAll(folds, sections.map((s) => s.key), open)), folds);
  }
  // ------------------------------------------------- connect a tablet
  // Shows the address to type (and a QR code to scan) for each dashboard. Admin-only: the
  // list of the computer's network addresses is not shown on the no-login dashboards.
  function connectCard() {
    const body = h("div", {}, h("p", { class: "muted" }, "Looking up this computer's network address…"));
    const c = card("Connect a tablet",
      h("p", { class: "muted" }, "On the tablet, join the same Wi-Fi as this computer, then scan a QR code or type the address into the browser."),
      body);
    c.id = "connect";
    api("GET", "/api/admin/connect").then((r) => {
      if (!r.addresses.length) {
        body.replaceChildren(h("p", { class: "warn-text" }, "This computer does not seem to be on a network. Connect it to the show network (Wi-Fi or cable) and reload this page."));
        return;
      }
      // One fold per dashboard and one for the home page. Up to 3 start open, otherwise only the first.
      const items = r.dashboards.map((d) => ({ key: d.slug, title: `${d.title} (${d.layout})`, urls: d.ip, mdns: d.mdns }))
        .concat([{ key: "home", title: "Home page (all dashboards)", urls: r.home.ip, mdns: r.home.mdns }]);
      const folds = items.map((it, i) => {
        const d = h("details", { class: "connect-fold", id: `connect-fold-${it.key}` },
          h("summary", {},
            h("span", { class: "cf-name" }, it.title),
            h("span", { class: "muted cf-url" }, it.urls[0] || "")),
          h("div", { class: "connect-item" },
            SW.qrSvg(it.urls[0], 132) || "",
            h("div", {},
              it.urls.map((u) => h("div", { class: "connect-url" }, u)),
              it.mdns ? h("div", { class: "muted" }, "Or, on most devices: ", h("span", { class: "mono" }, it.mdns), " (if that does not work, use the numbers above)") : null)));
        return connectFolds.bind(d, it.key, items.length <= 3 || i === 0);
      });
      body.replaceChildren(
        foldButtons((open) => connectFolds.setAll(folds, items.map((it) => it.key), open)),
        h("div", { class: "connect-list" }, folds));
    }).catch((err) => body.replaceChildren(h("p", { class: "error" }, err.message)));
    return c;
  }

  // ------------------------------------------------------------ support
  function supportCard() {
    const status = h("p", { class: "muted", role: "status" });
    const btn = h("button", { class: "primary", style: "min-height:48px", onclick: async () => {
      btn.disabled = true; status.textContent = "Preparing…";
      try {
        const res = await fetch("/api/admin/diagnostics", { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) { let m = res.statusText; try { m = (await res.json()).detail || m; } catch (_) { /* not JSON */ } throw new Error(m); }
        const blob = await res.blob();
        const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
        const a = h("a", { href: URL.createObjectURL(blob), download: m ? m[1] : "stagewatch-diagnostics.zip" });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        status.textContent = "Downloaded. Send that file when you ask for help.";
      } catch (err) { status.textContent = ""; toast(err.message, true); }
      finally { btn.disabled = false; }
    } }, "Download diagnostics");
    return card("Help",
      h("p", { class: "muted" }, "If something is not working, download this file and send it to whoever is helping you. It contains recent logs, the device list and your settings. It contains no passwords or keys."),
      h("div", { class: "row" }, btn), status);
  }

  function render() {
    entityIdsRendered = snap.entities.map((e) => e.id).join(",");
    dirty = false;   // everything on screen now matches saved state
    app.replaceChildren(
      h("div", { class: "grid-2" }, siteCard(), eventShowCard()),
      scheduleCard(),     // summary and a link to /schedule
      connectCard(),
      softwareCard(),
      devicesCard(), entitiesCard(), thresholdsCard(),
      dashboardsCard(),   // full width: room for the "Edit cards" panel
      h("div", { class: "grid-2" }, oscCard(), securityCard()),
      wallClockCard(),
      ontimeTimerCard(),
      barometerCard(),
      alarmLogCard(),
      supportCard(),
      catalogCard());
  }

  async function refresh() {
    [admin, snap] = await Promise.all([api("GET", "/api/admin/state"), api("GET", "/api/snapshot"), loadSoftware(),
      loadSchedule().catch(() => { schedData = null; })]);   // the Schedule card says so if it couldn't load
    SW.setSiteTime(snap.site && snap.site.time);
    render();
  }

  // Light live refresh: update values in place; rebuild only when the set of
  // entities changes, so half-typed form fields are never wiped.
  // A field counts as "being edited" while it has focus (checked before and after the request) or
  // after anything in the page has been typed into since the last full render. A full rebuild would
  // wipe that text, so the poll then only updates live values in place.
  const isEditing = () => !!document.activeElement && document.activeElement.matches("input,select,textarea");
  let dirty = false;
  app.addEventListener("input", () => { dirty = true; });
  app.addEventListener("change", () => { dirty = true; });

  async function poll() {
    if (!admin || isEditing()) return;
    try {
      const s = await api("GET", "/api/snapshot");
      if (isEditing()) return;   // focus moved into a field while the request was in flight
      const canRebuild = !dirty;
      if (s.entities.map((e) => e.id).join(",") !== entityIdsRendered) { if (canRebuild) await refresh(); return; }
      // Another admin page started a day/event or renamed one: show it here too.
      const showKey = (x) => (x && x.show ? `${x.show.id}|${x.show.name}|${x.show.event_name}|${x.show.day}` : "");
      if (showKey(s) !== showKey(snap)) { if (canRebuild) await refresh(); return; }
      snap = s;
      SW.setSiteTime(s.site && s.site.time);
      for (const e of s.entities) {
        const td = document.querySelector(`[data-live="${CSS.escape(e.id)}"]`);
        if (td) td.textContent = fmt(e.kind, e.value);
      }
      // The Schedule summary: reload it when the schedule changed anywhere, and redraw it every
      // poll so NOW / NEXT follow the clock. It has no fields, so a redraw wipes nothing.
      const meta = s.schedule || {};
      if (!schedData || meta.show_id !== schedData.show_id || meta.revision !== schedData.revision) {
        try { await loadSchedule(); } catch (_) { /* keep the last summary */ }
      }
      rerenderScheduleSummary();
      // Software status changes on its own (history entry once a new build is confirmed healthy,
      // background check finds an update): re-render that card only when it actually changed.
      if (++pollN % 3 === 0 && canRebuild && !document.querySelector("dialog[open]") && !watching) {
        const before = JSON.stringify(sw);
        await loadSoftware();
        if (JSON.stringify(sw) !== before) rerenderSoftware();
      }
    } catch (err) { if (err.status === 401) start(); }
  }
  let pollN = 0;

  async function start() {
    const info = await api("GET", "/api/info");
    const b = info.build || {};
    const commit = b.commit && b.commit !== "unknown" ? ` (${b.commit}${b.dirty ? "*" : ""})` : "";
    document.getElementById("version").textContent = `v${info.version}${commit}${info.emulate ? " · EMULATE" : ""}`;
    document.getElementById("version").title = `config schema ${b.config_schema}, db schema ${b.db_schema}, Python ${b.python}`;
    const logout = document.getElementById("logout");
    logout.hidden = !info.is_admin;
    logout.onclick = async () => { await api("POST", "/api/admin/logout"); location.reload(); };
    if (info.recovery_required) return renderRecovery();
    if (info.admin_setup_required) return renderAuth(true);
    if (!info.is_admin) return renderAuth(false);
    await refresh();
  }

  start();
  setInterval(poll, 5000);
  SW.heartbeat(2000);   // "Disconnected from Stagewatch" banner if the server stops answering
})();
