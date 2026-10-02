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

  function toast(msg, isError) {
    const el = h("div", { class: "toast" + (isError ? " error" : "") }, msg);
    document.body.append(el);
    setTimeout(() => el.remove(), 3500);
  }
  async function run(fn, okMsg) {
    try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
    catch (err) { toast(err.message, true); throw err; }
  }
  const card = (title, ...body) => h("section", { class: "card" }, h("h2", {}, title), ...body);
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
    // The server's 422 text is for developers; say it in crew words. Nothing typed is echoed back.
    const siteError = (err) => {
      const m = String(err.message || "");
      if (err.status === 422 && m.indexOf("timezone:") === 0) return "That time zone isn't recognised. Pick one from the list, or type a name like Europe/London. Nothing has been changed.";
      if (err.status === 422 && m.indexOf("day_rollover:") === 0) return "Type the new-day start as HH:MM between 00:00 and 11:59, for example 03:00. Nothing has been changed.";
      return m;
    };
    const save = (overrides) => api("PUT", "/api/admin/site", Object.assign({
      name: val(name), altitude_m: Number(alt.value), reference_distance_m: Number(dist.value),
      stale_after_s: Number(stale.value), smoothing_tau_s: Number(tau.value), outlier_reject: outl.checked,
      timezone: val(tz), day_rollover: val(rollover),
    }, overrides || {})).then(() => { toast("Site saved"); return refresh(); }, (err) => toast(siteError(err), true));
    let tzNote = null;
    if (!s.timezone) {
      const t = (snap.site && snap.site.time) || {};
      let browserZone = "";
      try { browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { /* old browser */ }
      tzNote = h("p", { class: "warn-text", role: "status" },
        `Time zone not set. Dashboards use the clock of the computer running Stagewatch (${SW.fmtOffset(t.utc_offset_s || 0)} now). `,
        browserZone ? h("button", { type: "button", class: "touch", onclick: () => save({ timezone: browserZone }) }, `Use ${browserZone} from this browser`) : null);
    }
    return card("Site",
      h("div", { class: "row" },
        field("Site / show name", name),
        field("Altitude (m): used only without a pressure sensor", alt),
        field("Reference distance (m) for Δ ms", dist),
        field("Stale after (s)", stale),
        field("Smoothing τ (s)", tau),
        field("Reject outliers (≥3 sensors)", outl)),
      h("div", { class: "row", style: "margin-top:10px" },
        field("Time zone", tz), tzList,
        field("New show day starts at (HH:MM, 24-hour)", rollover)),
      h("p", { class: "muted hint" }, "All times on dashboards use this time zone, whatever the tablet is set to. A show day runs until the start time next morning, so 01:30 still counts as the night before. Use 03:00 or later for the new day, to stay clear of the hour when the clocks change."),
      tzNote,
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { class: "primary", onclick: () => save() }, "Save site")));
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

    const devices = snap.devices.filter((d) => d.id !== "site");
    const devTable = h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Area"), h("th", {}, "Status"), h("th", {}, "Model"), h("th", {}, ""))),
      h("tbody", {}, devices.map((d) => {
        const name = h("input", { value: d.name });
        const area = h("input", { value: d.area });
        return h("tr", {},
          h("td", {}, name, h("div", { class: "muted", style: "font-size:12px" }, d.id)),
          h("td", {}, area),
          h("td", {}, h("span", { class: `status ${d.status}` }, d.status), d.status_detail ? h("div", { class: "muted", style: "font-size:12px" }, d.status_detail) : null),
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

  function entitiesCard() {
    const ents = snap.entities.filter((e) => !e.derived);
    const devName = (id) => (snap.devices.find((d) => d.id === id) || {}).name || id;
    const rows = ents.map((e) => {
      // The settings that apply now (hardware record, else legacy entry), so an offset kept only
      // in a hardware record still shows.
      const s = (admin.hardware && admin.hardware.settings[e.id]) || admin.config.entities[e.id]
        || { offset: 0, include_in_average: true };
      const off = h("input", { class: "num", type: "number", step: "0.01", value: s.offset });
      const inc = h("input", { type: "checkbox", checked: s.include_in_average });
      return h("tr", {},
        h("td", {}, devName(e.device_id), h("div", { class: "muted", style: "font-size:12px" }, e.id)),
        h("td", {}, e.kind),
        h("td", { class: "num", dataset: { live: e.id } }, fmt(e.kind, e.value)),
        h("td", {}, off, h("span", { class: "muted" }, " ", e.unit === "Pa" ? "Pa" : e.unit)),
        h("td", {}, inc),
        h("td", {}, h("button", { class: "small", onclick: () => run(() => api("PUT", `/api/admin/entities/${encodeURIComponent(e.id)}`,
          { offset: Number(off.value) || 0, include_in_average: inc.checked }), "Saved").then(refresh) }, "Save")));
    });
    return card("Sensors: calibration & averaging",
      h("p", { class: "muted" }, "Offset is added to every reading (compare against a reference such as a Kestrel). Pressure offsets are in Pa (1 hPa = 100 Pa). Untick to leave a sensor out of the site average, e.g. one in direct sun."),
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Sensor"), h("th", {}, "Kind"), h("th", { class: "num" }, "Value"), h("th", {}, "Offset"), h("th", {}, "Average"), h("th", {}, ""))),
        h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: 6, class: "muted" }, "No sensors yet."))))));
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
    schedule: ["Schedule", "Now, next and curfew. Stays hidden until the show has a schedule."],
    chart: ["History chart", "Readings over time, with markers."],
    markers: ["Markers", "The marker list, the Add marker box, and how far things have drifted since a marker."],
    sensors: ["Sensor nodes", "Each sensor node, whether it is working, and its latest readings."],
    wall_clock: ["Wall Clock", "The show clock from Ontime. Stays hidden until Ontime is connected."],
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
        const box = h("input", { type: "checkbox", checked: it.on, onchange: () => { it.on = box.checked; onChange(); } });
        return h("li", {},
          h("label", { class: "card-pick" }, box, h("span", {}, h("strong", {}, info[0]), info[1] ? h("span", { class: "muted" }, info[1]) : null)),
          h("button", { type: "button", class: "card-up", "aria-label": `Move ${info[0]} up`, title: "Move up", disabled: i === 0, onclick: () => move(i, -1) }, "▲"),
          h("button", { type: "button", class: "card-down", "aria-label": `Move ${info[0]} down`, title: "Move down", disabled: i === items.length - 1, onclick: () => move(i, 1) }, "▼"));
      }));
      onChange();
    };
    const stage = h("input", { class: "touch", value: d.stage || "", maxlength: 40, list: "stage-list", placeholder: "e.g. Main stage", autocomplete: "off" });
    // Ids from a newer Stagewatch (kept in the settings after a downgrade) can't be saved by this one.
    const newer = (d.cards || []).filter((id) => known.indexOf(id) < 0);
    fill(d.cards || (admin.cards && admin.cards.defaults.tablet) || []);
    const el = h("div", { class: "cards-panel" },
      h("p", { class: "muted hint" }, "Tick the cards this dashboard shows. Use ▲ and ▼ to change the order, top to bottom. With no cards ticked, the screen shows only alarms. Wall Clock is never switched on by default: tick it here if you want it."),
      newer.length ? h("p", { class: "warn-text hint" }, `This dashboard also lists cards from a newer version of Stagewatch (${newer.join(", ")}). This version can't show them, and saving here removes them.`) : null,
      ul,
      h("div", { class: "row", style: "margin-top:10px" },
        field("Stage", stage)),
      h("p", { class: "muted hint" }, "Which stage this screen follows, for cards that show one stage (like the schedule). Leave it empty to show every stage."));
    return {
      el,
      read: () => ({ cards: items.filter((it) => it.on).map((it) => it.id), stage: val(stage) }),
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
  // The current show day's running order. The list being edited lives in `sd`, not in the page,
  // so a full re-render or the 5-second poll never wipes what was typed. Saving replaces the whole
  // list (PUT with the show and the revision this page loaded). If the schedule was changed
  // elsewhere in the meantime the server refuses, and what was typed stays on screen.
  let sd = null;            // {showId, revision, day, rows, dirty, errors: {rowKey: {field: msg}}, cardErrors, conflict, changedElsewhere}
  let sdServer = null;      // the last GET /api/schedule
  let sdKey = 0;
  let sdBusy = false;
  let sdStatus = null;      // the "Unsaved changes" line, updated without a re-render
  let isEmulate = false;
  const imp = { text: "", format: "auto", preview: null, error: "", open: false, busy: false };
  const openSetlists = new Set();   // row keys whose setlist box is open
  const SCHED_KINDS = [["act", "Act"], ["doors", "Doors"], ["changeover", "Changeover"], ["curfew", "Curfew"], ["other", "Other"]];
  const KIND_LABEL = { act: "Act", doors: "Doors", changeover: "Changeover", curfew: "Curfew", other: "Other" };
  const kb = (n) => `${SW.num(n / 1024, n % 1024 ? 1 : 0)} KB`;
  const utf8 = (s) => (typeof TextEncoder === "function" ? new TextEncoder().encode(s).length : s.length);
  // Existing items keep `id` and `date` (the calendar date of their start, which the server needs
  // back unchanged so after-midnight items stay put). New rows have neither.
  const sdRow = (it) => ({ key: ++sdKey, id: it.id || null, date: it.date || "", kind: it.kind || "act",
    title: it.title || "", start: it.start || "", end: it.end || "", stage: it.stage || "", setlist: it.setlist || "" });
  function sdFromServer(s) {
    sd = { showId: s.show_id, revision: s.revision, day: s.day, rows: (s.items || []).map(sdRow), dirty: false,
      errors: {}, cardErrors: [], conflict: "", changedElsewhere: false };
    openSetlists.clear();
  }
  async function loadSchedule(force) {
    sdServer = await api("GET", "/api/schedule");
    if (force || !sd || !sd.dirty) sdFromServer(sdServer);
  }
  function sdTouch() {
    sd.dirty = true;
    if (sdStatus) sdStatus.textContent = "Unsaved changes. Click Save schedule to keep them.";
  }
  function rerenderSchedule() {
    const old = document.getElementById("schedule-admin");
    if (old) old.replaceWith(scheduleCard());
  }
  const csvCell = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const rowsAsCsv = (rows) => ["start,end,title,kind,stage"].concat(rows.map((r) =>
    [r.start, r.end, r.title, r.kind, r.stage].map((v) => csvCell(String(v || "").trim())).join(","))).join("\n");

  // The server's messages are fixed crew text; pydantic's own ones are not, so use ours for those.
  function schedMsg(msg, field) {
    const lim = admin.schedule_limits || {};
    const m = String(msg || "");
    if (m.indexOf("Value error, ") === 0) return m.slice(13);
    const ours = {
      title: `Titles must be 1 to ${lim.title_max || 120} characters`,
      stage: `Stage names can be up to ${lim.stage_max || 40} characters`,
      start: "Times must be 24-hour HH:MM, for example 19:30",
      end: "End times must be 24-hour HH:MM, for example 21:15, or left empty",
      kind: "Choose a kind from the list",
      setlist: `Each setlist can be up to ${kb(lim.setlist_max_bytes || 8192)}`,
    };
    if (/^(String|Input|Field|Extra|Value should|List should)/.test(m)) return ours[field] || "Check this item";
    return m;
  }
  const SCHED_CONFLICT = "The schedule was changed elsewhere. Reload to see it, then make your change again.";

  async function saveSchedule(okMsg) {
    if (sdBusy) return false;
    const lim = admin.schedule_limits || {};
    sd.errors = {}; sd.cardErrors = []; sd.conflict = "";
    if (sd.rows.length > (lim.max_items || 300)) {
      sd.cardErrors.push(`A schedule can have up to ${lim.max_items || 300} items. Nothing has been saved.`);
      rerenderSchedule();
      return false;
    }
    const body = { show_id: sd.showId, revision: sd.revision, items: sd.rows.map((r) => {
      const o = { kind: r.kind, title: r.title.trim(), start: r.start.trim(), end: r.end.trim(), stage: r.stage.trim(), setlist: r.setlist };
      if (r.id) o.id = r.id;
      if (r.date) o.date = r.date;
      return o;
    }) };
    sdBusy = true;
    let ok = false;
    try {
      let res = await api("PUT", "/api/admin/schedule", body);
      if (!res || res.revision === undefined || !Array.isArray(res.items)) res = await api("GET", "/api/schedule");
      sdServer = res;
      sdFromServer(res);
      admin.schedule_limits = Object.assign({}, lim, { demo_allowed: isEmulate || !res.items.length });
      toast(okMsg || "Schedule saved");
      ok = true;
    } catch (err) {
      if (err.status === 409) {
        sd.conflict = /show day/i.test(err.message) ? err.message : SCHED_CONFLICT;
      } else if (err.status === 422 && Array.isArray(err.detail)) {
        for (const d of err.detail) {
          const loc = d.loc || [];
          const i = loc[1] === "items" && typeof loc[2] === "number" ? loc[2] : -1;
          const field = typeof loc[3] === "string" ? loc[3] : "";
          if (i >= 0 && sd.rows[i]) {
            const errs = sd.errors[sd.rows[i].key] || (sd.errors[sd.rows[i].key] = {});
            if (!errs[field]) errs[field] = schedMsg(d.msg, field);
          } else sd.cardErrors.push(schedMsg(d.msg, ""));
        }
        sd.cardErrors.unshift("Some items need fixing (marked in red below). Nothing has been saved.");
      } else {
        sd.cardErrors.push(`${err.status === 422 ? schedMsg(err.message, "") : err.message} Nothing has been saved.`);
      }
    } finally { sdBusy = false; }
    rerenderSchedule();
    return ok;
  }

  // After a conflict: load the saved schedule. Anything typed here goes into the Paste box as
  // text, so it isn't lost.
  async function reloadSchedule() {
    const mine = sd && sd.dirty ? rowsAsCsv(sd.rows) : "";
    try { await loadSchedule(true); } catch (err) { toast(err.message, true); return; }
    if (mine) {
      imp.text = mine; imp.preview = null; imp.error = ""; imp.open = true;
      toast("Loaded the saved schedule. Your version is in the Paste box as text (without setlists).");
    } else toast("Loaded the saved schedule.");
    rerenderSchedule();
  }

  async function previewImport() {
    imp.busy = true; imp.error = ""; imp.preview = null;
    rerenderSchedule();
    try {
      imp.preview = await api("POST", "/api/admin/schedule/import", { text: imp.text, format: imp.format, dry_run: true });
    } catch (err) {
      imp.error = `${err.status === 422 ? schedMsg(typeof err.detail === "string" ? err.detail : err.message, "") : err.message}`;
    } finally { imp.busy = false; }
    rerenderSchedule();
  }

  // The previewed rows are added to the end of the list, then the whole list is saved.
  async function addImported() {
    const p = imp.preview;
    if (!p || !p.rows.length) return;
    if (sd.dirty && !confirm("You have unsaved changes in the list. Adding these items saves those changes too. Carry on?")) return;
    sd.rows = sd.rows.concat(p.rows.map((r) => sdRow({ kind: r.kind, title: r.title, start: r.start, end: r.end, stage: r.stage, setlist: r.setlist || "" })));
    sd.dirty = true;
    const n = p.rows.length;
    if (await saveSchedule(`Added ${n} item${n === 1 ? "" : "s"} to the schedule`)) {
      imp.text = ""; imp.preview = null; imp.error = "";
      rerenderSchedule();
    }
  }

  async function loadDemo() {
    const has = sd && sd.rows.length;
    if (!confirm(has ? "Replace this day's whole schedule with the demo day (doors 40 minutes ago, curfew in about an hour and a half)?"
      : "Load a demo running order around the current time (doors 40 minutes ago, curfew in about an hour and a half)?")) return;
    try {
      await api("POST", "/api/admin/schedule/demo", { show_id: sd.showId });
      await loadSchedule(true);
      admin.schedule_limits = Object.assign({}, admin.schedule_limits, { demo_allowed: isEmulate });
      toast("Demo day loaded");
    } catch (err) { toast(err.message, true); }
    rerenderSchedule();
  }

  function scheduleRowEl(r, i, total, lim) {
    const errs = sd.errors[r.key] || {};
    const on = (field) => (ev) => { r[field] = ev.target.value; sdTouch(); };
    const timeInput = (field, ph) => h("input", { class: "num touch" + (errs[field] ? " invalid" : ""), value: r[field], placeholder: ph,
      maxlength: 5, inputmode: "numeric", autocomplete: "off", "aria-label": field === "start" ? "Start (HH:MM)" : "End (HH:MM, optional)", oninput: on(field) });
    const kind = h("select", { class: "touch", "aria-label": "Kind", onchange: on("kind") }, SCHED_KINDS.map(([v, l]) => h("option", { value: v }, l)));
    kind.value = r.kind;
    const title = h("input", { class: "touch" + (errs.title ? " invalid" : ""), value: r.title, maxlength: lim.title_max || 120, placeholder: "e.g. Support: Band name",
      "aria-label": "Title", oninput: on("title") });
    const stage = h("input", { class: "touch" + (errs.stage ? " invalid" : ""), value: r.stage, maxlength: lim.stage_max || 40, list: "sched-stage-list",
      placeholder: "All stages", "aria-label": "Stage (empty for all stages)", autocomplete: "off", oninput: on("stage") });
    // Setlist: a text box with a live preview (the same safe Markdown dashboards use, links off).
    const setOpen = openSetlists.has(r.key);
    const setBtn = h("button", { type: "button", class: "touch", "aria-expanded": setOpen ? "true" : "false",
      onclick: () => { if (openSetlists.has(r.key)) openSetlists.delete(r.key); else openSetlists.add(r.key); rerenderSchedule(); } },
    r.setlist.trim() ? "Setlist ✓" : "Setlist");
    let setPanel = null;
    if (setOpen) {
      const preview = h("div", { class: "sched-preview" }, SW.renderMarkdown(r.setlist));
      const size = h("span", { class: "muted" });
      const max = lim.setlist_max_bytes || 8192;
      const upd = () => {
        const n = utf8(r.setlist);
        size.textContent = `${kb(n)} of ${kb(max)}`;
        size.className = n > max ? "error" : "muted";
      };
      const ta = h("textarea", { class: "sched-setlist-input" + (errs.setlist ? " invalid" : ""), rows: 8, "aria-label": `Setlist for ${r.title || "this item"}`,
        placeholder: "1. First song\n2. Second song\n\n## Encore\n- Last song", oninput: (ev) => {
          r.setlist = ev.target.value; sdTouch(); upd(); preview.replaceChildren(SW.renderMarkdown(r.setlist));
        } });
      ta.value = r.setlist;
      upd();
      setPanel = h("div", { class: "sched-setlist-edit" },
        h("label", { class: "field" }, "Setlist", ta, size),
        h("div", {}, h("div", { class: "muted", style: "font-size:13px" }, "How it looks on dashboards"), preview),
        h("p", { class: "muted hint" }, "One song per line. Optional: 1. for numbered lines, - for bullets, ## for a heading, **bold**, *italic*. Links are not shown as links."));
    }
    const nextDay = r.date && sd.day && r.date !== sd.day ? h("span", { class: "muted sched-date" }, `(${SW.fmtDay(r.date)})`) : null;
    const errList = Object.keys(errs).map((f) => errs[f]).filter((m, k, a) => a.indexOf(m) === k);
    return h("li", { class: "sched-edit-row" },
      h("div", { class: "sched-edit-fields" },
        h("span", { class: "sched-edit-n muted" }, String(i + 1)),
        h("label", { class: "field" }, "Start", timeInput("start", "HH:MM"), nextDay),
        h("label", { class: "field" }, "End", timeInput("end", "optional")),
        h("label", { class: "field" }, "Kind", kind),
        h("label", { class: "field sched-edit-title" }, "Title", title),
        h("label", { class: "field" }, "Stage", stage),
        h("div", { class: "row sched-edit-btns" }, setBtn,
          h("button", { type: "button", class: "touch", "data-move": `${r.key}-up`, "aria-label": `Move item ${i + 1} up`, title: "Move up", disabled: i === 0, onclick: () => moveRow(i, -1) }, "▲"),
          h("button", { type: "button", class: "touch", "data-move": `${r.key}-down`, "aria-label": `Move item ${i + 1} down`, title: "Move down", disabled: i === total - 1, onclick: () => moveRow(i, 1) }, "▼"),
          h("button", { type: "button", class: "touch danger", "aria-label": `Remove item ${i + 1}`, title: "Remove", onclick: () => {
            sd.rows.splice(i, 1); openSetlists.delete(r.key); delete sd.errors[r.key]; sdTouch(); rerenderSchedule();
          } }, "✕"))),
      errList.length ? h("p", { class: "error sched-edit-err", role: "alert" }, errList.join(". ")) : null,
      setPanel);
  }

  function moveRow(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= sd.rows.length) return;
    const t = sd.rows[i]; sd.rows[i] = sd.rows[j]; sd.rows[j] = t;
    sdTouch();
    rerenderSchedule();
    const btn = document.querySelector(`[data-move="${t.key}-${dir < 0 ? "up" : "down"}"]`);
    if (btn && !btn.disabled) btn.focus();
  }

  function scheduleCard() {
    const el = (...body) => { const c = card("Schedule", ...body); c.id = "schedule-admin"; return c; };
    if (!sd) return el(h("p", { class: "error" }, "Could not load the schedule. Reload this page to try again."));
    const lim = admin.schedule_limits || {};
    const show = admin.show || {};
    const rollover = (admin.config.site && admin.config.site.day_rollover) || "06:00";
    const stale = sd.showId !== (show.id === undefined ? sd.showId : show.id);
    const notices = [];
    if (sd.conflict || sd.changedElsewhere || stale) {
      notices.push(h("div", { class: "sched-conflict", role: "alert" },
        h("p", {}, sd.conflict || (stale ? "A new show day has started since this list was loaded. Reload to see the current day's schedule."
          : "The schedule was changed on another page. Reload to see it. Saving here would be refused.")),
        h("button", { type: "button", class: "primary touch", onclick: reloadSchedule }, "Reload")));
    }
    for (const m of sd.cardErrors) notices.push(h("p", { class: "error", role: "alert" }, m));

    const list = sd.rows.length
      ? h("ol", { class: "sched-edit" }, sd.rows.map((r, i) => scheduleRowEl(r, i, sd.rows.length, lim)))
      : h("p", { class: "muted" }, "No items yet. Click Add item, paste a running order below, or load the demo day.");
    sdStatus = h("span", { class: "muted", role: "status" }, sd.dirty ? "Unsaved changes. Click Save schedule to keep them." : "");
    const addItem = () => {
      const r = sdRow({ kind: "act" });
      sd.rows.push(r); sdTouch(); rerenderSchedule();
      const first = document.querySelector("#schedule-admin .sched-edit-row:last-child input");
      if (first) first.focus();
    };

    // Paste / import
    const ta = h("textarea", { class: "sched-import-text", rows: 8, "aria-label": "Running order to import",
      placeholder: "19:00 Doors\n19:30-20:15 Support: Band name\n20:15-20:45 Changeover\n20:45-22:15 Headliner\n23:00 Curfew",
      oninput: (ev) => { imp.text = ev.target.value; } });
    ta.value = imp.text;
    const fmtSel = h("select", { class: "touch", "aria-label": "Format", onchange: (ev) => { imp.format = ev.target.value; } },
      [["auto", "Work it out"], ["lines", "One item per line"], ["csv", "CSV (start, end, title, kind, stage)"],
        ["tsv", "Copied from a spreadsheet (tab-separated)"]].map(([v, l]) => h("option", { value: v }, l)));
    fmtSel.value = imp.format;
    const file = h("input", { type: "file", accept: ".csv,.txt,.tsv,text/csv,text/plain", class: "touch", onchange: (ev) => {
      const f = ev.target.files && ev.target.files[0];
      if (!f) return;
      if (f.size > (lim.import_max_bytes || 65536)) { toast(`That file is too big. The limit is ${kb(lim.import_max_bytes || 65536)}.`, true); return; }
      const rd = new FileReader();
      rd.onload = () => { imp.text = String(rd.result || ""); imp.preview = null; imp.error = ""; rerenderSchedule(); };
      rd.onerror = () => toast("Could not read that file.", true);
      rd.readAsText(f);
    } });
    const p = imp.preview;
    let previewEl = null;
    if (p) {
      const errs = p.errors || [];
      const more = (p.error_count || errs.length) - errs.length;
      previewEl = h("div", { class: "card-inset" },
        h("p", {}, h("strong", {}, p.rows.length === 1 ? "1 item found." : `${p.rows.length} items found.`),
          errs.length ? ` ${p.error_count} line${p.error_count === 1 ? "" : "s"} could not be read:` : " Every line could be read."),
        errs.length ? h("ul", { class: "sched-import-errors" }, errs.map((e) => h("li", {}, h("strong", {}, `Line ${e.line}: `), schedMsg(e.error, ""))),
          more > 0 ? h("li", { class: "muted" }, `…and ${more} more.`) : null) : null,
        p.rows.length ? h("div", { class: "table-scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, ["Start", "End", "Kind", "Title", "Stage"].map((x) => h("th", {}, x)))),
          h("tbody", {}, p.rows.map((r) => h("tr", {}, h("td", { class: "num" }, r.start), h("td", { class: "num" }, r.end || ""),
            h("td", {}, KIND_LABEL[r.kind] || r.kind), h("td", {}, r.title), h("td", {}, r.stage || "All")))))) : null,
        h("div", { class: "row", style: "margin-top:8px" },
          p.rows.length ? h("button", { type: "button", class: "primary touch", disabled: sdBusy, onclick: addImported },
            errs.length ? `Add the ${p.rows.length} item${p.rows.length === 1 ? "" : "s"} that could be read` : `Add ${p.rows.length} item${p.rows.length === 1 ? "" : "s"} to the schedule`) : null,
          h("button", { type: "button", class: "touch", onclick: () => { imp.preview = null; rerenderSchedule(); } }, "Close preview")),
        errs.length && p.rows.length ? h("p", { class: "muted hint" }, "Lines that could not be read are left out. To include them, fix them in the box above and click Preview again.") : null);
    }
    const importBox = h("details", { open: imp.open, ontoggle: (ev) => { imp.open = ev.target.open; } },
      h("summary", {}, "Paste or import a running order"),
      h("p", { class: "muted hint" }, "One item per line, like 19:00 Doors or 19:30-20:15 Support: Band name. Or CSV with the columns start, end, title, kind, stage (end, kind and stage can be left empty). You can paste straight from a spreadsheet. New items go after the ones already in the list."),
      ta,
      h("div", { class: "row", style: "margin-top:8px" },
        h("label", { class: "field" }, "Or open a file", file),
        h("label", { class: "field" }, "Format", fmtSel),
        h("button", { type: "button", class: "touch", style: "align-self:flex-end", disabled: imp.busy || sdBusy,
          onclick: () => { if (!imp.text.trim()) { toast("Paste or open a running order first.", true); return; } previewImport(); } }, imp.busy ? "Reading…" : "Preview")),
      imp.error ? h("p", { class: "error", role: "alert" }, imp.error) : null,
      previewEl);

    const stages = admin.stages || [];
    return el(
      h("p", { class: "muted hint" }, `The running order for ${show.name || "this show day"}${sd.day ? `, ${SW.fmtDay(sd.day)}` : ""}. Times are 24-hour, in site time. Times before ${rollover} count as the next morning, so 23:00 to 00:30 works. Leave Stage empty for items that apply to every stage.`),
      h("p", { class: "muted hint" }, "Visible to anyone on the show network."),
      h("p", { class: "muted hint" }, `${sd.rows.length} of ${lim.max_items || 300} items. Titles up to ${lim.title_max || 120} characters. Setlists up to ${kb(lim.setlist_max_bytes || 8192)} each, ${kb(lim.setlist_total_max_bytes || 262144)} in total. Dashboards list items by start time: ▲ and ▼ only matter for items that start at the same time.`),
      h("datalist", { id: "sched-stage-list" }, stages.map((s) => h("option", { value: s }))),
      notices,
      list,
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { type: "button", class: "touch", onclick: addItem }, "Add item"),
        h("button", { type: "button", class: "primary touch", disabled: sdBusy, onclick: () => saveSchedule() }, "Save schedule"),
        sd.dirty ? h("button", { type: "button", class: "touch", onclick: () => {
          if (confirm("Undo every change since the last save?")) { sdFromServer(sdServer); rerenderSchedule(); }
        } }, "Undo changes") : null,
        lim.demo_allowed ? h("button", { type: "button", class: "touch", onclick: loadDemo }, "Load demo day") : null,
        sdStatus),
      importBox);
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
    if (old) old.replaceWith(softwareCard());
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
    const sub = (text) => h("h3", { class: "sw-sub" }, text);
    const chanName = (c) => (c === "nightly" ? "Nightly" : "Stable");
    const checkedAt = (last) => SW.fmtTime(last.ts, { date: true });
    const chan = h("select", { id: "sw-channel" }, [["stable", "Stable"], ["nightly", "Nightly"]].map(([v, l]) => h("option", { value: v }, l)));
    chan.value = sw.channel;
    chan.onchange = () => swAction(() => api("PUT", "/api/admin/software/channel", { channel: chan.value }), "Channel changed");
    const busy = sw.job.running || sw.restarting;
    const checkBtn = h("button", { disabled: busy, onclick: async (ev) => {
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
      h("button", { class: "primary sw-go", disabled: sw.restarting, onclick: () => startUpdate(last) }, "Update now…"));

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
      e.can_rollback ? h("button", { class: "small danger", disabled: sw.restarting, onclick: () => startRollback(e) }, "Roll back…") : null)))
      : h("p", { class: "muted" }, "No updates yet.");
    const sizes = (rows, label) => rows.length ? h("div", {}, sub(label),
      h("table", {}, h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "mono" }, r.id || r.name), h("td", { class: "num" }, bytes(r.size)))))))
      : null;

    return el(sub("Installed"), info,
      sub("Updates"),
      h("div", { class: "row sw-controls" }, h("label", { class: "field", for: "sw-channel" }, "Channel", chan), checkBtn),
      sw.channel === "nightly" ? h("p", { class: "warn-text" }, "Nightly is bleeding edge, tested automatically only. Don't run it on show days.") : null,
      sw.restarting ? h("p", { class: "warn-text", role: "status" }, "Restarting for an update…") : null,
      result,
      sub("Update history"), hist,
      sizes(sw.backups, "Data backups"), sizes(sw.displaced, "Displaced data (set aside by a restore)"));
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
      const item = (title, urls, mdns) => h("li", { class: "connect-item" },
        SW.qrSvg(urls[0], 132) || "",
        h("div", {},
          h("div", { class: "muted" }, title),
          urls.map((u) => h("div", { class: "connect-url" }, u)),
          mdns ? h("div", { class: "muted" }, "Or, on most devices: ", h("span", { class: "mono" }, mdns), " (if that does not work, use the numbers above)") : null));
      body.replaceChildren(h("ul", { class: "connect-list" },
        r.dashboards.map((d) => item(`${d.title} (${d.layout})`, d.ip, d.mdns)),
        item("Home page (all dashboards)", r.home.ip, r.home.mdns)));
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
      scheduleCard(),     // full width: one row per item
      connectCard(),
      softwareCard(),
      devicesCard(), entitiesCard(), thresholdsCard(),
      dashboardsCard(),   // full width: room for the "Edit cards" panel
      h("div", { class: "grid-2" }, oscCard(), securityCard()),
      alarmLogCard(),
      supportCard(),
      catalogCard());
  }

  async function refresh() {
    [admin, snap] = await Promise.all([api("GET", "/api/admin/state"), api("GET", "/api/snapshot"), loadSoftware(),
      loadSchedule().catch(() => {})]);   // the Schedule card says so if it couldn't load
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
      // The schedule changed on another page: reload the Schedule card, unless this page has
      // unsaved edits there (then say so, and keep them).
      const meta = s.schedule || {};
      if (sd && (meta.show_id !== sd.showId || meta.revision !== sd.revision)) {
        if (!sd.dirty) { await loadSchedule(); if (!isEditing()) rerenderSchedule(); }
        else if (!sd.changedElsewhere) { sd.changedElsewhere = true; rerenderSchedule(); }
      }
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
    isEmulate = !!info.emulate;
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
