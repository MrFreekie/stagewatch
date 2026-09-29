// Admin console: onboarding/login, site settings, ESPHome adoption,
// calibration, thresholds, dashboards, OSC output, shows, PIN.
"use strict";

(() => {
  const { h, api, fmt } = SW;
  const app = document.getElementById("app");
  let admin = null;   // /api/admin/state
  let snap = null;    // /api/snapshot
  let entityIdsRendered = "";

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

  // ----------------------------------------------------------- sections
  function siteCard() {
    const s = admin.config.site;
    const name = h("input", { value: s.name });
    const alt = h("input", { class: "num", type: "number", step: "1", value: s.altitude_m });
    const dist = h("input", { class: "num", type: "number", step: "0.1", value: s.reference_distance_m });
    const stale = h("input", { class: "num", type: "number", step: "1", value: s.stale_after_s });
    const tau = h("input", { class: "num", type: "number", step: "1", value: s.smoothing_tau_s });
    const outl = h("input", { type: "checkbox", checked: s.outlier_reject });
    return card("Site",
      h("div", { class: "row" },
        field("Site / show name", name),
        field("Altitude (m): used only without a pressure sensor", alt),
        field("Reference distance (m) for Δ ms", dist),
        field("Stale after (s)", stale),
        field("Smoothing τ (s)", tau),
        field("Reject outliers (≥3 sensors)", outl)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/site", {
          name: val(name), altitude_m: Number(alt.value), reference_distance_m: Number(dist.value),
          stale_after_s: Number(stale.value), smoothing_tau_s: Number(tau.value), outlier_reject: outl.checked,
        }), "Site saved").then(refresh) }, "Save site")));
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
        h("td", {}, h("button", { class: "small", onclick: (ev) => {
          const row = ev.target.closest("tr");
          const formRow = h("tr", {}, h("td", { colspan: 4 }, adoptForm(d)));
          row.after(formRow);
        } }, "Adopt…"))))))
      : h("p", { class: "muted" }, snap && admin.integrations.some((i) => i.emulate)
        ? "Emulate mode: discovery is off; emulated nodes are shown below."
        : "No unadopted ESPHome nodes found on the network yet (mDNS). You can add one by host/IP below.");

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
      h("h3", { class: "muted", style: "font-size:13px;margin:14px 0 6px" }, "ADD BY HOST / IP"),
      adoptForm(),
      h("h3", { class: "muted", style: "font-size:13px;margin:14px 0 6px" }, "ADOPTED"),
      h("div", { class: "table-scroll" }, devices.length ? devTable : h("p", { class: "muted" }, "None yet.")));
  }

  function entitiesCard() {
    const ents = snap.entities.filter((e) => !e.derived);
    const devName = (id) => (snap.devices.find((d) => d.id === id) || {}).name || id;
    const rows = ents.map((e) => {
      const s = admin.config.entities[e.id] || { offset: 0, include_in_average: true };
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

  function dashboardsCard() {
    const tbody = h("tbody");
    const addRow = (d = {}) => {
      const slug = h("input", { value: d.slug || "", placeholder: "url-name" });
      const title = h("input", { value: d.title || "" });
      const layout = h("select", {}, ["tablet", "phone", "wall"].map((l) => h("option", { value: l }, l)));
      layout.value = d.layout || "tablet";
      const mk = h("input", { type: "checkbox", checked: d.allow_marker !== false });
      const ack = h("input", { type: "checkbox", checked: !!d.allow_ack });
      const link = d.slug ? h("a", { href: `/d/${d.slug}`, target: "_blank" }, "open") : "";
      const tr = h("tr", {}, h("td", {}, slug), h("td", {}, title), h("td", {}, layout), h("td", {}, mk), h("td", {}, ack), h("td", {}, link),
        h("td", {}, h("button", { class: "small danger", onclick: () => tr.remove() }, "✕")));
      tr._read = () => ({ slug: val(slug), title: val(title), layout: layout.value, allow_marker: mk.checked, allow_ack: ack.checked });
      tbody.append(tr);
    };
    admin.config.dashboards.forEach(addRow);
    return card("User dashboards",
      h("p", { class: "muted" }, "Each dashboard has its own URL (/d/<name>) for tablets, phones and kiosk displays. Users can view without logging in; tick what they may do."),
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["URL name", "Title", "Layout", "Add markers", "Ack alarms", "", ""].map((x) => h("th", {}, x)))), tbody)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { onclick: () => addRow() }, "Add dashboard"),
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/dashboards",
          [...tbody.children].map((tr) => tr._read())), "Dashboards saved").then(refresh) }, "Save dashboards")));
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

  function showsCard() {
    const name = h("input", { placeholder: "e.g. Festival day 2" });
    return card("Shows",
      h("p", {}, "Current: ", h("strong", {}, snap.show.name), h("span", { class: "muted" }, ` (since ${new Date(snap.show.started * 1000).toLocaleString()})`)),
      h("form", { class: "row", onsubmit: (ev) => {
        ev.preventDefault();
        if (!val(name)) return;
        if (!confirm(`Start new show "${val(name)}"? Dashboards will show only new history and markers.`)) return;
        run(() => api("POST", "/api/admin/shows", { name: val(name) }), "New show started").then(refresh);
      } }, name, h("button", { class: "primary", type: "submit" }, "Start new show")),
      h("details", {}, h("summary", { class: "muted" }, `Previous shows (${admin.shows.length})`),
        h("ul", {}, admin.shows.map((s) => h("li", {}, `${s.name}: ${new Date(s.started * 1000).toLocaleString()}`)))));
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
          h("td", { class: "muted" }, SW.timeSec(a.ts)), h("td", {}, a.event), h("td", {}, ["", "advisory", "alert", "stop"][a.level] || ""), h("td", {}, a.message))))))
        : h("p", { class: "muted" }, "No alarms yet."));
  }

  function render() {
    entityIdsRendered = snap.entities.map((e) => e.id).join(",");
    app.replaceChildren(
      h("div", { class: "grid-2" }, siteCard(), showsCard()),
      devicesCard(), entitiesCard(), thresholdsCard(),
      h("div", { class: "grid-2" }, dashboardsCard(), oscCard()),
      h("div", { class: "grid-2" }, alarmLogCard(), securityCard()),
      catalogCard());
  }

  async function refresh() {
    [admin, snap] = await Promise.all([api("GET", "/api/admin/state"), api("GET", "/api/snapshot")]);
    render();
  }

  // Light live refresh: update values in place; rebuild only when the set of
  // entities changes, so half-typed form fields are never wiped.
  async function poll() {
    if (!admin || document.activeElement && document.activeElement.matches("input,select")) return;
    try {
      const s = await api("GET", "/api/snapshot");
      if (s.entities.map((e) => e.id).join(",") !== entityIdsRendered) { await refresh(); return; }
      snap = s;
      for (const e of s.entities) {
        const td = document.querySelector(`[data-live="${CSS.escape(e.id)}"]`);
        if (td) td.textContent = fmt(e.kind, e.value);
      }
    } catch (err) { if (err.status === 401) start(); }
  }

  async function start() {
    const info = await api("GET", "/api/info");
    const b = info.build || {};
    const commit = b.commit && b.commit !== "unknown" ? ` (${b.commit}${b.dirty ? "*" : ""})` : "";
    document.getElementById("version").textContent = `v${info.version}${commit}${info.emulate ? " · EMULATE" : ""}`;
    document.getElementById("version").title = `config schema ${b.config_schema}, db schema ${b.db_schema}, Python ${b.python}`;
    const logout = document.getElementById("logout");
    logout.hidden = !info.is_admin;
    logout.onclick = async () => { await api("POST", "/api/admin/logout"); location.reload(); };
    if (info.admin_setup_required) return renderAuth(true);
    if (!info.is_admin) return renderAuth(false);
    await refresh();
  }

  start();
  setInterval(poll, 5000);
})();
