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
  // Day / Night, as on the dashboards: follows the device until tapped, then this browser remembers
  // the choice (the same setting the dashboards on this device use).
  (function themeToggle() {
    const KEY = "stagewatch.theme", btn = document.getElementById("theme-toggle");
    if (!btn) return;
    const isDay = () => {
      const t = document.documentElement.getAttribute("data-theme");
      return t ? t === "light" : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
    };
    const apply = (pick) => {
      if (pick === "day" || pick === "night") document.documentElement.setAttribute("data-theme", pick === "day" ? "light" : "dark");
      else document.documentElement.removeAttribute("data-theme");
      const day = isDay();
      btn.textContent = day ? "☾ Night" : "☀ Day";   // what a tap switches to
      btn.setAttribute("aria-label", day ? "Switch to night mode (dark)" : "Switch to day mode (light)");
    };
    btn.addEventListener("click", () => {
      const pick = isDay() ? "night" : "day";
      try { localStorage.setItem(KEY, pick); } catch (_) { /* not remembered; still switches */ }
      apply(pick);
    });
    let saved = "";
    try { saved = localStorage.getItem(KEY) || ""; } catch (_) { /* private mode: follow the device */ }
    apply(saved);
  })();
  const field = (label, input) => h("label", { class: "field" }, label, input);

  // Event logo (plans/custom-logo-upload.md): light and optional dark file for the current event,
  // the dark-screen option, where it shows, and "Use previous event's logo". Loads on its own so
  // the rest of the card never waits for it.
  const LOGO_MAX = 1024 * 1024;
  let brandOpen = false;
  function brandingPanel(ev) {
    const box = h("div", {}, h("p", { class: "muted" }, "Loading…"));
    const base = `/api/admin/events/${ev.id}`;
    const fill = (b) => {
      const reload = (p) => p.then(fill, () => {});
      const upload = (variant, input) => {
        const f = input.files && input.files[0];
        input.value = "";
        if (!f) return;
        if (!/^image\/(png|jpeg|webp)$/.test(f.type)) { toast("Use a PNG, JPEG or WebP image.", true); return; }
        if (f.size > LOGO_MAX) { toast("The logo is larger than 1 MB. Save a smaller copy and retry.", true); return; }
        reload(run(async () => {
          const res = await fetch(`${base}/logo/${variant}`, { method: "PUT", credentials: "same-origin",
            headers: { "Content-Type": f.type }, body: f });
          let data = null; try { data = await res.json(); } catch (_) { /* empty */ }
          if (!res.ok) throw new Error((data && typeof data.detail === "string") ? data.detail : "Upload failed");
          return data;
        }, "Logo saved"));
      };
      const slot = (variant, label) => {
        const meta = b[variant];
        const input = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", onchange: () => upload(variant, input) });
        return h("div", { class: "card-inset brand-slot" },
          h("strong", {}, label),
          meta ? h("div", { class: "row brand-previews" },
            h("span", { class: "brand-swatch dark" }, h("img", { src: meta.url, alt: `${label} on a dark screen`,
              class: variant === "light" && !b.dark && b.on_dark !== "as_is" ? b.on_dark : "" })),
            h("span", { class: "brand-swatch light" }, h("img", { src: meta.url, alt: `${label} on a light screen` })),
            h("span", { class: "muted" }, `${meta.width} × ${meta.height} px, ${Math.ceil(meta.bytes / 1024)} KB`))
            : h("p", { class: "muted" }, "No file."),
          h("div", { class: "row" },
            h("label", { class: "button touch" }, meta ? "Replace…" : "Choose file…", input),
            meta ? h("button", { class: "touch", type: "button", onclick: () => {
              if (confirm(`Remove the ${label.toLowerCase()}?`)) reload(run(() => api("DELETE", `${base}/logo/${variant}`), "Logo removed"));
            } }, "Remove") : null));
      };
      const opts = (patch) => reload(run(() => api("PUT", `${base}/branding`, Object.assign(
        { on_dark: b.on_dark, show_on_admin: b.show_on_admin, show_on_index: b.show_on_index }, patch)), "Saved"));
      const onDark = h("select", { onchange: () => opts({ on_dark: onDark.value }) },
        [["as_is", "Show as it is"], ["invert", "Invert colours"], ["plate", "On a white plate"]].map(([v, t]) =>
          h("option", { value: v, selected: b.on_dark === v }, t)));
      const tick = (key, text) => h("label", { class: "check" },
        h("input", { type: "checkbox", checked: b[key], onchange: (e) => opts({ [key]: e.target.checked }) }), ` ${text}`);
      const hasAny = b.light || b.dark;
      box.replaceChildren(...[
        h("p", { class: "muted hint" }, "Shown beside the title on every dashboard and the wall for this event only. PNG, JPEG or WebP, still image, up to 1 MB and 2048 pixels. Nothing shows until you upload one."),
        h("p", { class: "muted hint" }, "The file is served exactly as uploaded, to anyone on the show network. Photos can carry hidden details (such as GPS location): export the logo from a graphics program rather than using a camera photo."),
        slot("light", "Logo for light screens"),
        slot("dark", "Logo for dark screens (optional)"),
        b.light && !b.dark ? h("div", { class: "row" }, field("On dark screens, without a dark file", onDark)) : null,
        hasAny ? h("div", { class: "row" }, tick("show_on_index", "Also on the start page"), tick("show_on_admin", "Also on Admin")) : null,
        b.previous ? h("div", { class: "row" }, h("button", { class: "touch", type: "button", onclick: () => {
          if (!hasAny || confirm(`Replace this event's logo with the one from "${b.previous.name}"?`))
            reload(run(() => api("POST", `${base}/logo/copy`, { from_event_id: b.previous.id }), "Logo copied"));
        } }, `Use previous event's logo (${b.previous.name})`)) : null].filter(Boolean));
    };
    api("GET", `${base}/branding`).then(fill, (err) => box.replaceChildren(h("p", { class: "muted" }, err.message)));
    return h("details", { open: brandOpen, ontoggle: (e) => { brandOpen = e.target.open; } },
      h("summary", { class: "muted" }, "Event logo"), box);
  }
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

  // ------------------------------------------------------- sound level
  // Settings for the Sound level card: where Smaart is and which (up to three) values to record. The
  // card itself is added to a dashboard under User dashboards → Edit cards. Read-only toward Smaart.
  let splFill = null;   // fills the drop-downs of the Sound level card on screen from new lists
  // Read the current lists from the admin state and, if Smaart's names changed, update the drop-downs
  // in place (what is chosen and typed stays; nothing is rebuilt).
  async function pullSplLists() {
    if (!admin || !splFill) return;
    try {
      const st = (await api("GET", "/api/admin/state")).spl || {};
      const ins = st.inputs || [], mets = st.metrics || [], old = admin.spl || {};
      if (JSON.stringify([ins, mets]) === JSON.stringify([old.inputs || [], old.metrics || []])) return;
      admin.spl = { ...old, inputs: ins, metrics: mets };
      splFill(ins, mets);
    } catch (err) { if (err.status === 401) start(); }
  }

  function splCard() {
    const s = admin.config.spl || {}, st = admin.spl || {};
    const max = st.max_slots || 3, inputs = st.inputs || [], metrics = st.metrics || [];
    const defaults = st.default_metrics || {};
    const on = h("input", { type: "checkbox", checked: !!s.enabled });
    // Smaart's API password: write-only. The saved one is never shown, only whether there is one.
    // What is saved: the current form, or the older metric-only form (a slot with no input means
    // "the first input Smaart lists").
    const saved = (s.meters || (s.slots || []).map((k) => ({ source: "", metric: defaults[k] || k }))).slice(0, max);
    const slotRows = [];
    // (Re)build the options of every drop-down from Smaart's current lists, keeping what is chosen.
    // A chosen name Smaart no longer lists stays selected and says so; it is never swapped for another.
    const fillLists = (ins, mets) => {
      for (const r of slotRows) {
        const curSrc = r.src.value, curMet = r.met.value;
        const first = ins.length ? ` (${ins[0]})` : "";
        r.src.replaceChildren(h("option", { value: "" }, `First input in Smaart${first}`), ...ins.map((n) => h("option", { value: n }, n)));
        if (curSrc && !ins.includes(curSrc)) r.src.append(h("option", { value: curSrc }, `${curSrc} (Smaart does not list this now)`));
        r.src.value = curSrc;
        r.met.replaceChildren(h("option", { value: "" }, "(none)"), ...mets.map((n) => h("option", { value: n }, n)));
        if (curMet && !mets.includes(curMet)) r.met.append(h("option", { value: curMet }, mets.length ? `${curMet} (Smaart does not list this)` : curMet));
        r.met.value = curMet;
      }
    };
    // Locations are labels only. A default for the whole card, plus one per Smaart input (saved by the
    // input's name, so slots that read the same input share one field). They never change what is recorded.
    const locs = SW.spl.locMap(s.locations);   // a Map: input names are Smaart's text, never property names
    const defLoc = h("input", { class: "touch", maxlength: "40", autocomplete: "off", value: s.location || "", placeholder: "FOH" });
    const locBox = h("div", {});
    // Graph range: automatic fits the readings; custom is fixed. The numbers stay filled in (22 and 145 to begin with).
    const rangeMode = h("select", { class: "touch", "aria-label": "Graph range" },
      h("option", { value: "auto" }, "Automatic"), h("option", { value: "custom" }, "Custom"));
    rangeMode.value = s.chart_range === "custom" ? "custom" : "auto";
    const rangeMin = h("input", { class: "num touch", type: "number", min: "0", max: "200", step: "1", value: s.chart_min_db === undefined ? 22 : s.chart_min_db });
    const rangeMax = h("input", { class: "num touch", type: "number", min: "0", max: "200", step: "1", value: s.chart_max_db === undefined ? 145 : s.chart_max_db });
    const rangeNums = h("span", { class: "row spl-range-nums" }, field("Min dB", rangeMin), field("Max dB", rangeMax));
    const showRange = () => { rangeNums.hidden = rangeMode.value !== "custom"; };
    rangeMode.addEventListener("change", showRange);
    showRange();
    let curInputs = inputs;
    const renderLocs = (ins) => {
      curInputs = ins;
      if (!ins.length) {
        locBox.replaceChildren(h("p", { class: "muted hint" }, "A location for each input appears here once Smaart is connected. Any you have saved are kept."));
        return;
      }
      const kids = [], seen = new Set();
      for (const r of slotRows) {
        const name = r.src.value || ins[0];
        if (!r.met.value || !name || seen.has(name)) continue;
        seen.add(name);
        const inp = h("input", { class: "touch", maxlength: "40", autocomplete: "off", value: locs.get(name) || "", placeholder: "Uses the default location", "aria-label": `Location for ${name}`, oninput: () => { locs.set(name, inp.value); } });
        kids.push(h("div", { class: "row" }, field(`Location for this input (${name})`, inp)));
      }
      for (const name of [...locs.keys()].filter((n) => locs.get(n) && !ins.includes(n))) {
        const note = h("span", { class: "muted" }, `Not listed now: ${name} → ${locs.get(name)}. Kept in case the input comes back. `);
        kids.push(h("div", { class: "row" }, note, h("button", { type: "button", class: "touch", "aria-label": `Remove the location for ${name}`, onclick: () => { locs.delete(name); renderLocs(curInputs); } }, "Remove")));
      }
      locBox.replaceChildren(...kids);
    };
    for (let i = 0; i < max; i++) {
      const cur = saved[i] || { source: "", metric: "" };
      const src = h("select", { class: "touch spl-sel", "aria-label": `Value ${i + 1}: input`, onchange: () => renderLocs(curInputs) }, h("option", { value: cur.source }, cur.source));
      const met = h("select", { class: "touch spl-sel", "aria-label": `Value ${i + 1}: value`, onchange: () => renderLocs(curInputs) }, h("option", { value: cur.metric }, cur.metric));
      src.value = cur.source; met.value = cur.metric;
      slotRows.push({ src, met });
    }
    fillLists(inputs, metrics);
    renderLocs(inputs);
    splFill = (ins, mets) => { fillLists(ins, mets); renderLocs(ins); };
    const refreshStatus = h("span", { class: "muted", role: "status" });
    const refreshBtn = h("button", { class: "touch", type: "button", onclick: async () => {
      refreshBtn.disabled = true;
      refreshStatus.textContent = "";
      try {
        const r = await api("POST", "/api/admin/spl/refresh");
        refreshStatus.textContent = r.result || "Refreshed";
        toast(refreshStatus.textContent);
      } catch (err) {
        refreshStatus.textContent = err.message;
        toast(err.message, true);
      } finally { refreshBtn.disabled = false; }
      await pullSplLists();
    } }, "Refresh");
    const lines = [];
    if (!st.running) lines.push("Not running. Tick the box and Save to start.");
    else lines.push(st.status === "ok" ? (st.detail || "Receiving values") : `▲ ${st.status}${st.detail ? `: ${st.detail}` : ""}`);
    if (st.running && st.source) lines.push(st.input_name ? `${st.source} · ${st.input_name}` : st.source);
    if (st.version) lines.push(`Version ${st.version}`);
    const notes = [];
    if (st.running && !inputs.length && !metrics.length && !admin.emulate) notes.push("Once Stagewatch is connected to Smaart, the lists of inputs and values come from Smaart. Until then you see what is saved.");
    for (const slot of st.slots || []) {
      if (slot.metric_listed === false) notes.push(`Smaart does not list "${slot.metric}"${defaults.laeq_15m === slot.metric ? ": pick one of the LAeq figures it does list (Stagewatch cannot tell which period it is)" : ""}, so that value shows as not available.`);
      if (slot.source_listed === false) notes.push(`Smaart is not listing the input "${slot.source || "first input"}" now, so that value shows as not available.`);
    }
    return SW.brandCard("Sound level (Smaart)", "smaart-badge.png", "Smaart",
      h("p", { class: "muted" }, "Records up to three sound level values from Smaart, exactly as Smaart reports them, and shows them on dashboards that have the Sound level card (User dashboards → Edit cards). Stagewatch only listens: it never changes anything in Smaart, and a value Smaart does not give shows as a dash, never zero."),
      h("details", {}, h("summary", { class: "muted" }, "What Stagewatch sends to Smaart"),
        h("p", { class: "muted" }, "Four short fixed messages and nothing else: \"is a password needed?\", \"which inputs are active?\", the password (only to log in), and \"one update a second, please\". It never starts or stops measuring, never reads Smaart's history, and never changes calibration, gain, logging, alarms or the mix. It does no sound-level maths: nothing is averaged, smoothed or rounded."),
        h("p", { class: "muted" }, "The password goes to Smaart as plain text on the show network (Smaart's API is not encrypted as far as we know), so use a password that you use nowhere else.")),
      admin.emulate ? h("p", { class: "notice" }, "Emulate mode: a simulated Smaart with two inputs and the values SPL A Slow, SPL C Slow, LAeq 1 and LAeq 10, so no Smaart or address is needed. It has no LAeq 15, and now and then flags one reading as overload, to show how \"not available\" looks.")
        : h("p", { class: "notice" }, "Tested live on Smaart Suite 9.6.4; other Smaart versions have not been tested. Turn on Smaart's API under Options → Preferences → API first. The port is normally the one Smaart's SPL web page uses (26000 is the usual example); please confirm it on your Smaart."),
      h("div", { class: "row" }, h("label", { class: "field inline" }, on, " Record sound level"),
        admin.emulate ? null : h("span", { class: "muted" }, connText(s.host, s.port, st.password_set))),
      h("div", { class: "row" }, field("Default location (shown on the card)", defLoc),
        h("span", { class: "muted hint" }, "For example FOH or Stage left. Shown on the dashboard card, for any input without its own location below.")),
      h("div", { class: "row" }, field("Graph range", rangeMode), rangeNums,
        h("span", { class: "muted hint" }, "Automatic fits the graph to what Smaart reports. Custom keeps it fixed, for example 22 to 145 dB.")),
      ...slotRows.map((r, i) => h("div", { class: "row spl-slot" }, field(`Value ${i + 1}: input`, r.src), field(`Value ${i + 1}: value`, r.met))),
      locBox,
      h("div", { class: "row" }, refreshBtn, h("span", { class: "muted hint" }, "Re-reads the input and metric names from Smaart"), refreshStatus),
      h("div", { class: "row" }, h("button", { class: "primary touch", onclick: () => run(() => api("PUT", "/api/admin/spl", {
        enabled: on.checked, host: s.host || "", port: s.port || null,   // the address is set under Connections
        meters: slotRows.filter((r) => r.met.value).map((r) => ({ source: r.src.value, metric: r.met.value })),
        password: "", clear_password: false,
        location: defLoc.value,
        chart_range: rangeMode.value,
        ...(rangeMode.value === "custom" ? { chart_min_db: Number(rangeMin.value), chart_max_db: Number(rangeMax.value) } : {}),
        locations: SW.spl.locObject(locs),
      }), "Sound level saved").then(refresh, () => {}) }, "Save")),
      ...notes.map((n) => h("p", { class: "notice" }, n)),
      h("p", { class: "muted hint" }, "Normally SPL A Slow, SPL C Slow and an LAeq figure. The LAeq is Smaart's own number; Stagewatch cannot check which period it covers beyond the name Smaart gives it. Stagewatch is not a calibrated compliance record. The Smaart log or report is the record that counts."),
      h("p", { class: "muted" }, lines.join(" · ")));
  }

  // ------------------------------------------------------------ sound level limits
  // Limits per event (every day) with an optional override for this day. Each limit is tied to one
  // of the recorded Smaart values. Public wording only: anyone on the show network can read it.
  function splLimitsCard() {
    const body = h("div", {}, h("p", { class: "muted" }, "Loading…"));
    const c = card("Sound level limits", body);
    api("GET", "/api/admin/spl-limits").then((d) => body.replaceChildren(...splLimitsBody(d).filter((x) => x)),
      () => body.replaceChildren(h("p", { class: "notice" }, "Could not load the sound level limits.")));
    return c;
  }
  function splLimitsBody(d) {
    const slots = d.slots || [];
    const slotText = (s) => (s.source ? `${s.metric} (${s.source})` : s.metric);
    const newId = () => Math.random().toString(36).slice(2, 10).replace(/[^a-z0-9]/g, "") || "x1";
    const scope = h("select", { class: "touch", "aria-label": "Which limits" },
      h("option", { value: "event" }, "Every day of this event"), h("option", { value: "show" }, "This day only (override)"));
    scope.value = d.override_active ? "show" : "event";
    const rowsBox = h("div", { class: "spl-lim-rows" });
    const setBy = h("input", { class: "touch", maxlength: "120" });
    const ref = h("input", { class: "touch", maxlength: "200" });
    const notes = h("textarea", { class: "touch", maxlength: "500", rows: "2" });
    const enteredBy = h("input", { class: "touch", maxlength: "120" });
    const changeNote = h("input", { class: "touch", maxlength: "200" });
    let rows = [];
    function addRow(l) {
      l = l || { id: newId(), source: (slots[0] || {}).source || "", metric: (slots[0] || {}).metric || "", kind: "limit", value_db: "", start: "", end: "", label: "" };
      const met = h("select", { class: "touch spl-sel", "aria-label": "Smaart value" });
      const opts = slots.map((s, i) => h("option", { value: String(i) }, slotText(s)));
      let idx = slots.findIndex((s) => s.source === l.source && s.metric === l.metric);
      if (idx < 0) { opts.push(h("option", { value: "keep" }, `${slotText(l)} (not recorded now)`)); }
      met.replaceChildren(...opts);
      met.value = idx < 0 ? "keep" : String(idx);
      const kind = h("select", { class: "touch", "aria-label": "Kind" }, h("option", { value: "limit" }, "Licence limit"), h("option", { value: "guidance" }, "Guidance"));
      kind.value = l.kind;
      const val = h("input", { class: "num touch", type: "number", min: "40", max: "150", step: "0.1", value: l.value_db, "aria-label": "dB" });
      const start = h("input", { class: "touch", type: "time", value: l.start || "", "aria-label": "From" });
      const end = h("input", { class: "touch", type: "time", value: l.end || "", "aria-label": "Until" });
      const label = h("input", { class: "touch", maxlength: "60", value: l.label || "", "aria-label": "Label" });
      const r = { l, met, kind, val, start, end, label };
      // The weighting comes from Smaart's value name (LAeq → dB(A), LCeq → dB(C)); shown, not chosen.
      const unitOf = () => { const s = met.value === "keep" ? l : slots[Number(met.value)] || {}; const w = /^L([ACZ])/i.exec(String(s.metric || "").trim()); return w ? `dB(${w[1].toUpperCase()})` : "dB"; };
      const dbField = field(unitOf(), val);
      met.addEventListener("change", () => { const lab = dbField.querySelector("span, .label, legend") || dbField.firstChild; if (lab) lab.textContent = unitOf(); });
      r.el = h("div", { class: "row spl-lim-row" }, field("Smaart value", met), field("Kind", kind), dbField,
        field("From (empty = start of day)", start), field("Until (empty = end of day)", end), field("Label (optional)", label),
        h("button", { class: "touch", onclick: () => { rows = rows.filter((x) => x !== r); r.el.remove(); } }, "Remove"));
      rows.push(r);
      rowsBox.append(r.el);
    }
    function fill(set) {
      rows = []; rowsBox.replaceChildren();
      ((set && set.limits) || []).forEach(addRow);
      setBy.value = (set && set.set_by) || ""; ref.value = (set && set.reference) || ""; notes.value = (set && set.public_notes) || "";
    }
    fill(d.override_active ? d.show_set : d.event_set);
    scope.addEventListener("change", () => fill(scope.value === "show" ? (d.show_set || d.event_set) : d.event_set));
    const collect = () => ({
      scope: scope.value, entered_by: enteredBy.value, change_note: changeNote.value,
      set: {
        set_by: setBy.value, reference: ref.value, public_notes: notes.value,
        limits: rows.map((r) => {
          const s = r.met.value === "keep" ? r.l : slots[Number(r.met.value)];
          return { id: r.l.id, source: s.source || "", metric: s.metric, kind: r.kind.value, value_db: Number(r.val.value),
            start: r.start.value, end: r.end.value, label: r.label.value };
        }),
      },
    });
    const save = h("button", { class: "primary touch", onclick: () => run(() => api("PUT", "/api/admin/spl-limits", collect()), "Sound level limits saved").then(refresh, () => {}) }, "Save limits");
    const back = d.override_active ? h("button", { class: "touch", onclick: () => run(() => api("DELETE", "/api/admin/spl-limits/override"), "Back to the event's limits").then(refresh, () => {}) }, "Use the event's limits for this day") : null;
    const from = h("select", { class: "touch", "aria-label": "Copy from event" }, ...(d.events || []).map((e) => h("option", { value: String(e.id) }, e.name)));
    const copy = (d.events || []).length ? h("div", { class: "row" }, field("Copy limits from another event", from),
      h("button", { class: "touch", onclick: () => run(() => api("POST", "/api/admin/spl-limits/copy", { from_event_id: Number(from.value) }), "Limits copied: check and save them to confirm").then(refresh, () => {}) }, "Copy")) : null;
    const st = d.settings || {};
    const warn = h("input", { class: "num touch", type: "number", min: "0", max: "60", value: st.change_warning_min });
    const mk = h("input", { type: "checkbox" }); mk.checked = !!st.exceed_marker;
    const al = h("input", { type: "checkbox" }); al.checked = !!st.exceed_alarm;
    const saveSet = h("button", { class: "touch", onclick: () => run(() => api("PUT", "/api/admin/spl-limits/settings", { change_warning_min: Number(warn.value) || 0, exceed_marker: mk.checked, exceed_alarm: al.checked }), "Saved") }, "Save notices");
    const revs = (d.revisions || []).slice(0, 5).map((r) => `Rev ${r.rev}, ${SW.fmtTime(r.saved_at, { date: true })}${r.show_id ? " (this day)" : ""}${r.cleared ? ", back to event limits" : ""}${r.entered_by ? `, by ${r.entered_by}` : ""}${r.change_note ? `: ${r.change_note}` : ""}`);
    return [
      h("p", { class: "notice" }, "Visible to anyone on the show network. Don't paste confidential noise-plan wording."),
      d.copied ? h("p", { class: "notice" }, "▲ These limits were copied from another event and are not yet confirmed. Check them and save.") : null,
      !slots.length ? h("p", { class: "notice" }, "No Smaart values are recorded yet. Choose them in Sound level first; a limit is compared with one of them.") : null,
      h("div", { class: "row" }, field("Which limits", scope)),
      rowsBox,
      h("div", { class: "row" }, h("button", { class: "touch", onclick: () => addRow() }, "Add a limit")),
      h("div", { class: "row" }, field("Set by", setBy), field("Reference", ref)),
      h("div", { class: "row" }, field("Notes shown on dashboards", notes)),
      h("div", { class: "row" }, field("Entered by (admin only)", enteredBy), field("What changed (on the timeline)", changeNote)),
      h("div", { class: "row" }, save, back),
      h("p", { class: "muted hint" }, "Over means Smaart's value is above the entered number. A licence limit adds a marker and a silent notice the first time it is passed; guidance shows on the card only. Times are the site's clock on the show day (a time before the day rollover is after midnight). Stagewatch cannot check Smaart's Leq period: tie each limit to the Smaart value whose name matches the licence (for example LAeq 15)."),
      copy,
      h("div", { class: "row" }, field("Warn this many minutes before a limit drops (0 = off)", warn),
        h("label", { class: "field inline" }, mk, " Marker when a limit is first passed"), h("label", { class: "field inline" }, al, " Silent notice while over"), saveSet),
      revs.length ? h("p", { class: "muted" }, "Recent changes: " + revs.join(" · ")) : null,
    ];
  }

  // ------------------------------------------------------------ globcon
  // Where DirectOut GLOBCON is, for the DirectOut GLOBCON card. Read-only: Stagewatch only listens and
  // asks for names and levels. The controller and strips are chosen per dashboard (Edit cards).
  function globconCard() {
    const g = admin.config.globcon || {}, st = admin.globcon || {};
    const lines = [];
    if (!st.running) lines.push(st.card_assigned ? "Starting…" : "Not running. It starts when a dashboard has the DirectOut GLOBCON card (User dashboards → Edit cards).");
    else {
      lines.push(st.status === "ok" ? (st.detail || "Receiving levels") : `▲ ${st.status}${st.detail ? `: ${st.detail}` : ""}`);
      if (st.hint && st.status !== "ok") lines.push(st.hint);
      if (st.controllers && st.controllers.length) lines.push(`Controller${st.controllers.length === 1 ? "" : "s"} ${st.controllers.join(", ")} in use`);
    }
    return SW.brandCard("DirectOut GLOBCON", "directout-badge.png", "DirectOut",
      h("p", { class: "muted" }, "Shows live level meters from GLOBCON on dashboards that have the DirectOut GLOBCON card. Stagewatch only listens: it never moves a fader, mutes, solos, changes a layer or runs a function in GLOBCON. Levels are shown exactly as GLOBCON reports them, nothing is recorded, and if GLOBCON goes quiet the card freezes on the last levels and says how old they are."),
      h("details", {}, h("summary", { class: "muted" }, "What Stagewatch sends to GLOBCON"),
        h("p", { class: "muted" }, "Only \"are you there?\", \"tell me this\" and \"tell me when it changes\" (and \"stop telling me\"), for controller names, strip labels, the layer and the meter levels. If you save a password, it is sent to log in, and only when GLOBCON asks for one. It is sent as plain text on the show network, so use a password you use nowhere else.")),
      admin.emulate ? h("p", { class: "notice" }, "Emulate mode: a simulated GLOBCON with 16 controllers, so no GLOBCON or address is needed. It drops out for a few seconds every minute and a half, to show how a frozen card looks.")
        : h("p", { class: "notice" }, "Names, layers and labels checked on one real controller (2026-10). Live level meters work. The metering scale for different channel types and password login are not yet tested. Please check the levels against GLOBCON's own screen."),
      admin.emulate ? null : h("p", { class: "muted" }, connText(g.host, g.port || 9091, st.password_set)),
      h("p", { class: "muted" }, lines.join(" · ")));
  }

  // ------------------------------------------------------------ shelly power meters
  // Shelly power meters for the Power card. Read-only: Stagewatch only asks a meter for its readings and
  // never switches a relay, changes a setting, restarts or resets it. Addresses and MACs are shown here
  // and nowhere public. Phase 1 reads by HTTP polling only and has been tried on one Shelly PM Mini Gen3.
  // Add a Shelly meter (the Add integration pop-out). onDone runs after the meter is added.
  function shellyAddForm(onDone) {
    // Add a meter: type the address, press Test to see what is there, then Save.
    const host = h("input", { class: "touch", maxlength: "64", autocomplete: "off", spellcheck: "false", placeholder: "e.g. 192.168.1.50", value: shellyTest ? shellyTest.host : "", "aria-label": "Meter address" });
    const name = h("input", { maxlength: "60", value: "Power meter", "aria-label": "Name for this meter" });
    const area = h("input", { maxlength: "40", placeholder: "e.g. FOH", "aria-label": "Area" });
    const every = h("input", { class: "num", type: "number", min: "2", max: "5", step: "0.5", value: "3", "aria-label": "Reads every (seconds)" });
    const result = h("div", { class: "shelly-result", role: "status" });
    const save = h("button", { class: "primary touch", disabled: true }, "Save meter");
    const showResult = (r) => {
      if (!r) { result.replaceChildren(); save.disabled = true; return; }
      if (!r.ok) { result.replaceChildren(h("p", { class: "warn-text" }, `▲ ${r.message} Nothing has been changed.`)); save.disabled = true; return; }
      const lines = [h("p", {}, `Found ${r.model || "a Shelly"}${r.firmware ? `, firmware ${r.firmware}` : ""}. It measures: ${r.components.join(", ") || "power"}.`)];
      if (r.already_added) lines.push(h("p", { class: "warn-text" }, "▲ This meter has already been added."));
      lines.push(h("ul", { class: "shelly-readings" }, r.readings.map((x) => h("li", {}, `${x.name}: ${SW.num(x.value, Math.min(3, x.decimals))}${x.unit ? " " + x.unit : ""}`))));
      lines.push(h("p", { class: "muted" }, "These are live readings from the meter. Nothing has been changed on it."));
      result.replaceChildren(...lines);
      save.disabled = !!r.already_added;
    };
    const test = h("button", { class: "touch", onclick: async () => {
      shellyTest = null; showResult(null);
      result.replaceChildren(h("p", { class: "muted" }, "Asking the meter…"));
      try {
        const r = await api("POST", "/api/admin/shelly/test", { host: val(host) });
        shellyTest = Object.assign({ host: val(host) }, r);
        showResult(r);
      } catch (err) { result.replaceChildren(h("p", { class: "warn-text" }, `▲ ${err.message}`)); }
    } }, "Test");
    save.onclick = () => run(() => api("POST", "/api/admin/shelly/add", { host: val(host), name: val(name), area: val(area), interval_s: Number(every.value) }), "Meter added")
      .then(() => { shellyTest = null; if (onDone) onDone(); return refresh(); }, () => {});
    if (shellyTest && shellyTest.ok) showResult(shellyTest);
    return h("div", {},
      h("div", { class: "row" }, field("Meter address (an IP address on your local network)", host), test),
      result,
      field("Name", name), field("Area (optional)", area), field("Reads every (seconds, 2 to 5)", every),
      h("p", { class: "muted hint" }, "Tip: give the meter a fixed address in your router (a DHCP reservation) so it stays where Stagewatch expects it."),
      h("div", { class: "row sw-dlg-actions" }, h("span", { class: "grow" }), save));
  }
  let shellyTest = null;   // the last Test result for the Add form: {host, ...} (kept across a re-render)
  function shellyCard() {
    const st = admin.shelly || { meters: [] };
    const meters = st.meters || [];
    const statusPill = (m) => {
      const ss = SW.deviceStatus({ status: m.status, status_detail: m.detail }, snap.now);
      return h("span", { class: `status ${ss.cls}`, title: ss.title }, ss.text);
    };
    const rows = meters.map((m) => {
      const name = h("input", { value: m.name, maxlength: "60", "aria-label": `${m.name} name` });
      const area = h("input", { value: m.area, maxlength: "40", "aria-label": `${m.name} area` });
      const host = h("input", { class: "touch", value: m.host, maxlength: "64", autocomplete: "off", spellcheck: "false", "aria-label": `${m.name} address` });
      const every = h("input", { class: "num", type: "number", min: "2", max: "5", step: "0.5", value: m.interval_s, "aria-label": `${m.name} reads every (seconds)` });
      const rating = h("input", { class: "num", type: "number", min: "0", max: "200", step: "0.5", value: m.rating_a || "", placeholder: "none", "aria-label": `${m.name} rating (amps)` });
      const chRows = (m.channels || []).map((c) => {
        const label = h("input", { value: c.label, maxlength: "40", placeholder: "Label (e.g. Amp rack 3)", "aria-label": `${m.name} ${c.name} label` });
        const phase = h("select", { "aria-label": `${m.name} ${c.name} line` }, [["", "No line set"], ["L1", "Line 1 (Brown)"], ["L2", "Line 2 (Black)"], ["L3", "Line 3 (Grey)"]].map(([v, t]) => h("option", { value: v, selected: v === c.phase }, t)));
        const rating = h("input", { class: "num", type: "number", min: "0", max: "200", step: "0.5", value: c.rating_a || "", placeholder: "meter's", "aria-label": `${m.name} ${c.name} rating (amps)` });
        return { c, label, phase, rating, el: h("div", { class: "row", style: "margin:4px 0" }, h("span", { class: "muted", style: "min-width:90px" }, c.name), label,
          c.three_phase ? h("span", { class: "muted" }, "Lines 1 to 3 (Brown, Black, Grey)") : phase, rating, h("span", { class: "muted" }, "A")) };
      });
      const channelsPayload = () => chRows.map((r) => ({ key: r.c.key, label: val(r.label), phase: r.c.three_phase ? "" : r.phase.value, rating_a: Number(r.rating.value) || 0 }));
      const facts = [m.model, m.firmware ? `firmware ${m.firmware}` : "", `MAC ${m.mac}`].filter(Boolean).join(" · ");
      const first = h("tr", {},
        h("td", {}, name, h("div", { class: "muted", style: "font-size:12px" }, m.id)),
        h("td", {}, area),
        h("td", {}, statusPill(m), m.detail ? h("div", { class: "muted", style: "font-size:12px" }, m.detail) : null,
          m.problem ? h("div", { class: "warn-text", style: "font-size:12px" }, m.problem) : null,
          m.energy_went_back ? h("div", { class: "warn-text", style: "font-size:12px" }, `Energy counter went back at ${m.energy_went_back}`) : null),
        h("td", {}, host, h("div", { class: "muted", style: "font-size:12px" }, facts)),
        h("td", {}, h("div", { class: "row" }, every, h("span", { class: "muted" }, "s"))),
        h("td", {}, h("div", { class: "row" }, rating, h("span", { class: "muted" }, "A"))),
        h("td", {}, h("div", { class: "muted", style: "font-size:12px" }, `${m.ok_polls} ok, ${m.failed_polls} missed`)),
        h("td", {}, h("div", { class: "row" },
          h("button", { class: "small", onclick: () => run(() => api("PATCH", `/api/admin/shelly/${encodeURIComponent(m.id)}`, {
            name: val(name), area: val(area), host: val(host), interval_s: Number(every.value), rating_a: Number(rating.value) || 0, channels: channelsPayload() }), "Saved").then(refresh, () => {}) }, "Save"),
          h("button", { class: "small", title: "Makes two warnings from the rating: a notice at 80% and an alarm at 100%. Your own limits: Stagewatch does not protect or switch anything.",
            onclick: () => run(() => api("POST", `/api/admin/shelly/${encodeURIComponent(m.id)}/warnings`), "Warnings added at 80% and 100% (see Thresholds)").then(refresh, () => {}) }, "Add warnings"),
          h("button", { class: "small danger", onclick: () => confirm(`Remove ${m.name}? Its history is kept.`) && run(() => api("DELETE", `/api/admin/shelly/${encodeURIComponent(m.id)}`), "Removed").then(refresh, () => {}) }, "Remove"))));
      return chRows.length ? [first, h("tr", {}, h("td", { colspan: "8" }, h("div", { class: "muted", style: "font-size:12px" }, "Circuits on this meter: label, which mains line it is on (single-phase meters), and its own rating (blank uses the meter's rating)"), ...chRows.map((r) => r.el)))] : [first];
    });
    const table = h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Area"), h("th", {}, "Status"), h("th", {}, "Address"), h("th", {}, "Reads every"), h("th", {}, "Rating (amps)"), h("th", {}, "Polls"), h("th", {}, ""))),
      h("tbody", {}, [].concat(...rows)));


    return SW.brandCard("Shelly power meters", "shelly-badge.svg", "Shelly",
      h("p", { class: "muted" }, "Shows watts, volts, amps, frequency and energy from Shelly power meters on dashboards that have the Power card (User dashboards → Edit cards). Stagewatch only reads: every few seconds it asks each meter for its readings, and that is all it ever sends. It never switches a relay, changes a setting, restarts or resets a meter. Type a meter's rating in amps (for example 16 for a PM Mini, or the breaker it watches) to get a load bar with warnings at 80% and 100%. Voltage and frequency are checked against the UK ranges."),
      h("p", { class: "notice" }, "A Shelly Plug or any Shelly with a relay is read for its power only. Stagewatch never touches the relay, but it cannot stop the Shelly's own button, schedule or app from switching it. For a distro, prefer a meter with no relay, such as the PM Mini."),
      admin.emulate ? h("p", { class: "notice" }, "Emulate mode: meters are simulated, so any private address (such as 192.0.2.20) will \"find\" a simulated PM Mini. The demo meter stops answering for 40 seconds every 5 minutes, to show how a frozen card looks.")
        : h("p", { class: "notice" }, "Phase 1 reads by HTTP polling only and has been tried on one Shelly PM Mini Gen3. Other Shelly meters are read by what they report, but are not tested. A meter with a password is not supported yet."),
      meters.length ? h("div", { class: "table-scroll" }, table) : h("p", { class: "muted" }, "No power meters yet."),
      h("p", { class: "muted" }, "To add a meter, use Add integration in the Connections card."),
      h("p", { class: "muted hint" }, "Tip: give the meter a fixed address in your router (a DHCP reservation) so it stays where Stagewatch expects it. Stagewatch recognises a meter by its own hardware address, so if its address changes, type the new one in its row and press Save. Warnings at the breaker rating are planned for a later version. Figures are advisory and do not replace the electrician's inspection and testing."));
  }

  // ------------------------------------------------------------ alarm notices
  // How long advisory notices stay on dashboards. Only the dashboard list changes: the state behind
  // a notice (a node still Missing) stays visible in the sensor list and here.
  function alarmNoticesCard() {
    const a = admin.config.alarms || {};
    const mins = (v, dflt) => h("input", { class: "num touch", type: "number", step: "1", min: "0", value: v === undefined ? dflt : v });
    const hide = mins(a.hide_acked_min, 2), fold = mins(a.fold_old_min, 30);
    const msg = h("p", { class: "muted", role: "alert" });
    const save = () => {
      if (hide.value.trim() === "" || fold.value.trim() === "") {
        msg.textContent = "Type a number in both boxes. Type 0 for never.";
        return;
      }
      msg.textContent = "";
      run(() => api("PUT", "/api/admin/alarms", {
        hide_acked_min: Math.round(Number(hide.value)), fold_old_min: Math.round(Number(fold.value)),
      }), "Alarm notices saved").then(refresh, () => {});
    };
    return card("Alarm notices",
      h("p", { class: "muted" }, "Keeps the alarm list on dashboards short. Advisory notices only: alert and stop alarms stay until they clear. The sensor list and this page still show what is wrong. 0 means never."),
      h("div", { class: "row" },
        field("Hide acknowledged notices after (minutes, 0 = never)", hide),
        field("Move old notices into \"Older notices\" after (minutes, 0 = never)", fold),
        h("button", { class: "primary touch", style: "align-self:flex-end", onclick: save }, "Save")),
      msg,
      SW.help(h("p", { class: "muted hint" }, "An acknowledged notice leaves the list that long after it was acknowledged (up to 1,440 minutes); the sensor list still shows the condition, for example a node marked Missing. A quiet notice (one that never beeps) that has not changed moves into \"Older notices\" (up to 10,080 minutes); it is not removed. A notice that is still beeping stays on the list until someone acknowledges it. If the problem changes or comes back, the notice is shown again straight away.")));
  }

  // ------------------------------------------------------------ barometer
  // Settings for the Barometer card. The card itself is added to a dashboard under User dashboards →
  // Edit cards. Advisory only.
  const DEMOS = [["steady", "Settled high"], ["slow_fall", "Slow fall"], ["front", "Front arriving"], ["storm", "Storm"],
    ["rising", "Clearing"], ["dropout", "Sensor dropout 20 min"], ["none", "No pressure sensor"]];
  // This Raspberry Pi: shown only when Stagewatch runs on a Pi (or the simulated one in emulate mode).
  // Addresses come from the admin-only integration info; dashboards never get them.
  function piHostInfo() { return admin.integrations.find((i) => i.manifest.domain === "pihost" && i.active) || null; }
  function piHostCard() {
    const info = piHostInfo();
    if (!info) return null;
    const dev = snap.devices.find((d) => d.id === "pi_host") || {};
    const ents = SW.pi.entitiesOf(snap.entities, "pi_host");
    const rows = SW.pi.rows(ents, dev.pi);
    const p = info.pi || {};
    const addr = Object.keys(p.addresses || {}).map((k) => `${k}: ${p.addresses[k].join(", ")}`).join(" · ");
    return card("This Raspberry Pi",
      h("p", { class: "muted" }, `${dev.model || p.model || "Raspberry Pi"}. Read only: Stagewatch never changes the Pi's settings. Add the Raspberry Pi card to a dashboard to see this there (User dashboards → Edit cards).`),
      h("div", { class: "table-scroll" }, h("table", {}, h("tbody", {}, rows.map((r) => h("tr", { class: `pi-${r.level}` },
        h("th", { scope: "row" }, r.label), h("td", {}, r.value, r.unit ? " " + r.unit : "")))))),
      h("p", { class: "muted" }, addr ? `Network addresses (admin only): ${addr}` : "Network addresses: not known"),
      (dev.pi && dev.pi.warnings && dev.pi.warnings.length) ? h("p", { class: "pi-warnline" }, dev.pi.warnings.join(" ")) : null,
      h("p", { class: "muted hint" }, "Warnings show quietly in the dashboard header's messages (they never sound): short of power, slowed down, 80 °C or more, or under 1 GB / 5% disk free. Under-voltage and throttling also add a marker when they start and stop. Tested on a Raspberry Pi 4; other models are not tested."));
  }
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

  // Environment (air at the site: feeds the site average) or Equipment (gear: never averaged).
  function roleSelect(value, aria) {
    const sel = h("select", aria ? { "aria-label": aria } : {},
      h("option", { value: "environment" }, "Environment"), h("option", { value: "equipment" }, "Equipment"));
    sel.value = value === "equipment" ? "equipment" : "environment";
    return sel;
  }

  // Adopt a node (the Add integration pop-out). onDone runs after a successful adopt.
  function adoptForm(prefill = {}, onDone) {
    const host = h("input", { placeholder: "node-name.local or IP", value: prefill.host || "" });
    const port = h("input", { class: "num", type: "number", value: prefill.port || 6053 });
    const name = h("input", { placeholder: "e.g. Stage L node", value: prefill.friendly_name || "" });
    const area = h("input", { placeholder: "e.g. Stage L, FOH, Delay tower 1" });
    const psk = h("input", { type: "password", placeholder: prefill.encrypted ? "required: api encryption key" : "api encryption key (if set)", autocomplete: "off" });
    const role = roleSelect("environment");
    return h("form", { class: "row", onsubmit: (ev) => {
      ev.preventDefault();
      run(() => api("POST", "/api/admin/esphome/adopt", {
        host: val(host), port: Number(port.value), name: val(name), area: val(area), noise_psk: val(psk), role: role.value,
      }), "Node adopted").then(() => { if (onDone) onDone(); return refresh(); }, () => {});
    } }, field("Host", host), field("Port", port), field("Name", name), field("Area", area),
    field("Role", role), field("Encryption key", psk), h("button", { class: "primary", type: "submit", style: "align-self:flex-end" }, "Adopt"));
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
          h("button", { class: "small", onclick: () => connDialog(connProducts().find((p) => p.key === "esphome"), d) }, "Adopt…"),
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
    const devices = snap.devices.filter((d) => d.id !== "site" && d.category !== "service" && d.category !== "meter" && d.category !== "host");   // services (Ontime) are listed under Integrations; power meters under Shelly power meters
    const devTable = h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Area"), h("th", {}, "Role"), h("th", {}, "Sleeps between readings"), h("th", {}, "Status"), h("th", {}, "Address"), h("th", {}, "Model"), h("th", {}, ""))),
      h("tbody", {}, devices.map((d) => {
        const name = h("input", { value: d.name });
        const area = h("input", { value: d.area });
        const hwd = admin.hardware && admin.hardware.devices && admin.hardware.devices[d.id];
        const role = roleSelect(hwd && hwd.role, `${d.name} role`);
        // Empty = the node stays on. Otherwise the minutes between its wake-ups.
        const sleep = h("input", { class: "num", type: "number", min: "0", max: "1440", step: "1", value: hwd && hwd.sleep_minutes ? hwd.sleep_minutes : "",
          placeholder: "Off", "aria-label": `${d.name} wakes every (minutes)`, title: "Leave empty if the node stays on. Otherwise how many minutes it sleeps between readings." });
        const sleepMin = () => (sleep.value.trim() === "" ? 0 : Math.max(0, Math.min(1440, Math.round(Number(sleep.value)) || 0)));
        const st = SW.deviceStatus(d, snap.now);
        return h("tr", {},
          h("td", {}, name, h("div", { class: "muted", style: "font-size:12px" }, d.id)),
          h("td", {}, area),
          h("td", {}, role),
          h("td", {}, h("div", { class: "row" }, sleep, h("span", { class: "muted" }, "min")),
            h("div", { class: "muted", style: "font-size:12px" }, "Counted as stale after 2.5 times this")),
          h("td", {}, h("span", { class: `status ${st.cls}`, title: st.title }, st.text), d.status_detail ? h("div", { class: "muted", style: "font-size:12px" }, d.status_detail) : null, deviceErrorCounts["device:" + d.id] ? h("div", { style: "font-size:12px" }, h("a", { href: "#device-errors-card" }, `${problemsWord(deviceErrorCounts["device:" + d.id])} this show`)) : null, hwd && hwd.conflict ? h("div", { style: "font-size:12px" }, h("a", { href: "#calibration-card" }, "Held by the hardware check: choose what to do under Calibration & hardware")) : null),
          h("td", {}, nodeAddress(admin.hardware && admin.hardware.devices && admin.hardware.devices[d.id])),
          h("td", {}, d.model),
          h("td", {}, h("div", { class: "row" },
            h("button", { class: "small", onclick: () => run(() => api("PATCH", `/api/admin/devices/${encodeURIComponent(d.id)}`, { name: val(name), area: val(area), role: role.value, sleep_minutes: sleepMin() }), "Saved").then(refresh) }, "Save"),
            h("button", { class: "small danger", onclick: () => confirm(`Remove ${d.name}? History is kept.`) && run(() => api("DELETE", `/api/admin/devices/${encodeURIComponent(d.id)}`), "Removed").then(refresh) }, "Remove"))));
      })));

    return card("ESPHome nodes",
      h("div", { class: "table-scroll" }, discTable),
      ignoredBlock,
      h("p", { class: "muted" }, "To add a node by its address, use Add integration in the Connections card."),
      h("h3", { class: "muted", style: "font-size:13px;margin:14px 0 6px" }, "ADOPTED"),
      h("div", { class: "table-scroll" }, devices.length ? devTable : h("p", { class: "muted" }, "None yet.")));
  }

  // A node held by the hardware check says why, in plain words, and offers the ways out. Admin only.
  const resolveBlock = (d, conflict) => {
    if (!conflict) return null;
    const send = (action, ask) => (!ask || confirm(ask)) && run(() => api("POST", `/api/admin/esphome/${encodeURIComponent(d.id)}/resolve`, { action }), "Node settled").then(refresh, () => {});
    const btn = (text, action, ask) => h("button", { class: "small", style: "min-height:44px", onclick: () => send(action, ask) }, text);
    const why = {
      different: "A different board is answering at this address. Its readings are ignored until you choose.",
      known_board: "This board already has calibration saved from before. Its readings are ignored until you choose.",
      unreadable: "The board did not give its hardware address, so it cannot be checked. Its readings are ignored until you choose.",
      duplicate: `This is the same board as ${conflict.other || "another node"}. Remove one of the two nodes.`,
    }[conflict.reason] || "Held by the hardware check. Its readings are ignored.";
    const buttons = conflict.reason === "different" ? [
      btn("New board, start fresh", "new_hardware", `Treat this as a new board with no calibration? ${d.name} keeps no offsets.`),
      btn("New board, keep calibration", "move_calibration")]
      : conflict.reason === "known_board" ? [
        btn("Use the saved calibration", "move_calibration"),
        btn("Start fresh", "new_hardware", "Start fresh? The calibration saved for this board is deleted.")]
      : conflict.reason === "unreadable" ? [btn("Accept this board", "forget_mac")] : [];
    return h("div", { class: "notice", style: "margin-top:6px" }, h("div", {}, why), buttons.length ? h("div", { class: "row", style: "margin-top:6px" }, ...buttons) : null);
  };

  // ------------------------------------------------- calibration & hardware (plan 1.9)
  const macText = (key) => { const m = /^mac:([0-9a-f]{12})$/.exec(key || ""); return m ? m[1].match(/../g).join(":") : (key || ""); };
  const methodWord = { manual: "set by hand", reference: "checked against a reference", import: "imported from a file", migrated: "kept from before the upgrade", moved: "copied from another board" };
  const isoDate = (iso) => { const t = Date.parse(iso); return Number.isNaN(t) ? "" : SW.fmtTime(t / 1000, { date: true }); };   // site time, like every other time on the page
  const ageText = (iso) => { const d = new Date(iso); if (isNaN(d)) return ""; const s = Math.max(0, (Date.now() - d.getTime()) / 1000); return s < 60 ? "just now" : `${SW.fmtDuration(s, false)} ago`; };
  const fieldWord = { offset: "offset", include_in_average: "in site average", accuracy: "accuracy", accuracy_basis: "accuracy basis", role: "role" };
  const showVal = (v) => (v === null || v === "" ? "none" : v === true ? "yes" : v === false ? "no" : String(v));
  const hasHeldNode = () => !!(admin.hardware && Object.values(admin.hardware.devices || {}).some((d) => d.conflict));

  function calibrationCard() {
    const hw = admin.hardware || { entities: {}, settings: {}, devices: {}, calibrations: {} };
    const status = h("p", { class: "muted", role: "status" });
    const diffBox = h("div", { role: "status" });
    const offsetOf = (e, o) => (SW.OFFSET_KINDS.indexOf(e.kind) >= 0 ? SW.fmtCalOffset(e.kind, o) : `${SW.signed(o, 1)} ${e.unit}`.trim());

    const exportBtn = h("button", { class: "small", style: "min-height:44px", onclick: async () => {
      try {
        const res = await fetch("/api/admin/calibrations/export", { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) throw new Error("Could not make the file");
        const a = h("a", { href: URL.createObjectURL(await res.blob()), download: "stagewatch-calibration.json" });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        status.textContent = "Saved. Keep this file with your show files: it says which board each offset belongs to.";
      } catch (err) { toast(err.message, true); }
    } }, "Save calibration to a file");

    const list = (title, items) => (items.length ? h("div", {}, h("strong", {}, title), h("ul", {}, items)) : null);
    const sensorWord = (key) => {
      const id = Object.keys(hw.entities).find((i) => hw.entities[i] === key);
      const ent = id && snap.entities.find((x) => x.id === id);
      const dev = ent && snap.devices.find((x) => x.id === ent.device_id);
      return ent ? (dev ? `${dev.name}: ${ent.name}` : ent.name) :`${macText(key.split("/")[0])} ${key.split("/")[1]}`;
    };
    const showDiff = (data, r) => {
      const n = r.added.length + r.changed.length;
      const changed = r.changed.map((c) => h("li", {}, `${sensorWord(c.key)}: ` + Object.entries(c.fields).map(([f, v]) => `${fieldWord[f] || f} ${showVal(v.from)} → ${showVal(v.to)}`).join(", ")));
      diffBox.replaceChildren(h("div", { class: "notice" },
        h("p", {}, n ? `Nothing has been changed yet. Applying this file would add ${r.added.length} and change ${r.changed.length}; ${r.unchanged.length} already match.` : "Nothing to apply: every sensor in this file already matches."),
        list("New", r.added.map((k) => { const o = data.calibrations && data.calibrations[k] && data.calibrations[k].offset; return h("li", {}, sensorWord(k) + (typeof o === "number" ? `: offset ${showVal(o)}` : "")); })),
        list("Changed", changed),
        list("For a board this hub does not know (it applies if that board is added later)", r.unknown_hardware.map((k) => h("li", {}, sensorWord(k)))),
        list("Skipped (recorded exactly as received, never adjusted)", r.refused.map((k) => h("li", {}, sensorWord(k)))),
        h("div", { class: "row" },
          n ? h("button", { class: "primary small", style: "min-height:44px", onclick: () => run(() => api("POST", "/api/admin/calibrations/import", { data, dry_run: false }), "Calibration imported")
            .then(() => { diffBox.replaceChildren(); return refresh(); }, () => {}) }, "Apply to this hub") : null,
          h("button", { class: "small", style: "min-height:44px", onclick: () => diffBox.replaceChildren() }, n ? "Cancel" : "Close"))));
    };
    const fileInput = h("input", { type: "file", accept: "application/json,.json", "aria-label": "Calibration file to read", onchange: () => {
      const f = fileInput.files[0]; fileInput.value = "";
      if (!f) return;
      if (f.size > 1024 * 1024) { toast("That file is too big to be a calibration file. Nothing has been changed.", true); return; }
      const rd = new FileReader();
      rd.onload = () => {
        let data;
        try { data = JSON.parse(String(rd.result)); if (data && typeof data.calibrations === "object" && data.calibrations) Object.values(data.calibrations).forEach((c) => { if (c && typeof c === "object") delete c.history; }); } catch (_) { toast("That is not a Stagewatch calibration file. Nothing has been changed.", true); return; }
        run(() => api("POST", "/api/admin/calibrations/import", { data, dry_run: true })).then((r) => showDiff(data, r), () => {});
      };
      rd.onerror = () => toast("Could not read that file. Nothing has been changed.", true);
      rd.readAsText(f);
    } });

    // Boards the hardware check is holding: say why, offer the ways out.
    const held = Object.keys(hw.devices).filter((id) => hw.devices[id].conflict).map((id) => {
      const d = snap.devices.find((x) => x.id === id) || { id, name: id };
      return h("div", {}, h("strong", {}, d.name), resolveBlock(d, hw.devices[id].conflict));
    });

    const meterIds = new Set(snap.devices.filter((d) => d.category === "meter" || d.category === "host").map((d) => d.id));
    const ids = Object.keys(hw.entities).sort((a, b) => hw.entities[a].localeCompare(hw.entities[b]) || a.localeCompare(b));
    const rows = ids.map((id) => {
      const e = snap.entities.find((x) => x.id === id);
      if (!e || meterIds.has(e.device_id)) return null;   // power meter readings are never adjusted
      const dev = snap.devices.find((x) => x.id === e.device_id);
      const hist = (hw.calibrations && hw.calibrations[id]) || [];
      const last = hist[0];
      const s = hw.settings[id] || { offset: 0 };
      const ref = h("input", { maxlength: "120", style: "min-height:44px", placeholder: "Checked against (optional)", "aria-label": `${e.name} checked against` });
      const note = h("input", { maxlength: "200", style: "min-height:44px", placeholder: "Note", "aria-label": `${e.name} note` });
      const add = h("button", { class: "small", style: "min-height:44px", "aria-label": `Add note to ${e.name}`, disabled: !last, title: last ? "" : "Save an offset first",
        onclick: () => run(() => api("POST", `/api/admin/calibrations/${encodeURIComponent(id)}/note`, { reference: ref.value.trim(), note: note.value.trim() }), "Note saved").then(refresh, () => {}) }, "Add note");
      const histEl = hist.length ? h("details", {}, h("summary", {}, `History (${hist.length})`),
        h("ul", {}, hist.map((x) => h("li", {}, `${isoDate(x.date)}: offset ${offsetOf(e, x.offset)}, ${methodWord[x.method] || x.method}`
          + (x.reference ? ` (${x.reference})` : "") + (x.note ? ` – ${x.note}` : ""))))) : null;
      return h("tr", {},
        h("td", {}, e.name, h("div", { class: "muted", style: "font-size:12px" }, (dev ? `${dev.name} · ` : "") + macText(hw.entities[id].split("/")[0]))),
        h("td", {}, offsetOf(e, Number(s.offset) || 0)),
        h("td", {}, last ? h("span", { title: isoDate(last.date) }, ageText(last.date)) : h("span", { class: "muted" }, "Never saved"),
          last ? h("div", { class: "muted", style: "font-size:12px" }, methodWord[last.method] || last.method) : null),
        h("td", {}, h("div", { class: "row" }, ref, note, add), histEl));
    }).filter(Boolean);

    const c = card("Calibration & hardware",
      h("p", { class: "muted" }, "Each offset belongs to the board it was set on, so it follows that board if you re-adopt it or change its address. Offsets are set under Sensors: calibration & averaging. Hardware addresses are only shown here, never on dashboards."),
      held.length ? h("div", {}, h("h3", { class: "muted", style: "font-size:13px;margin:10px 0 6px" }, "NEEDS A DECISION"), held) : null,
      h("h3", { class: "muted", style: "font-size:13px;margin:10px 0 6px" }, "SENSORS"),
      rows.length ? h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Sensor and board"), h("th", {}, "Offset"), h("th", {}, "Last saved"), h("th", {}, "Note"))),
        h("tbody", {}, rows))) : h("p", { class: "muted" }, "No sensors with a known board yet. A node shows up here once it has connected."),
      h("h3", { class: "muted", style: "font-size:13px;margin:14px 0 6px" }, "BACK UP OR MOVE TO ANOTHER HUB"),
      h("p", { class: "muted hint" }, "Reading a file shows what would change first. Nothing is applied until you press Apply."),
      h("div", { class: "row" }, exportBtn, h("label", { class: "field inline" }, "Read a file: ", fileInput)),
      status, diffBox);
    c.id = "calibration-card";
    return c;
  }

  // ------------------------------------------------- device errors (plans/device-error-list.md)
  // Problems per device for a show. The count on each Devices row comes from the same reply.
  let deviceErrorCounts = {};   // device key -> problems in the current show
  async function loadDeviceErrorCounts() {
    try {
      const r = await api("GET", "/api/admin/devices/errors");
      deviceErrorCounts = Object.fromEntries(r.rows.filter((x) => x.problems > 0).map((x) => [x.key, x.problems]));
    } catch (_) { deviceErrorCounts = {}; }
  }
  const problemsWord = (n) => `${n} ${n === 1 ? "problem" : "problems"}`;

  function deviceErrorsCard() {
    const shows = (admin.events || []).flatMap((e) => e.shows.map((sh) => ({ id: sh.id, label: `${sh.name} · ${SW.fmtDay(sh.day)} (${e.name})` })));
    const currentId = snap.show && snap.show.id;
    let data = null, showQuiet = false;
    const openRows = new Set();
    const showSel = h("select", { class: "touch", "aria-label": "Show to look at", onchange: () => load() },
      shows.map((o) => h("option", { value: String(o.id), selected: o.id === currentId }, o.label + (o.id === currentId ? " – current" : ""))));
    const scopeSel = h("select", { class: "touch", "aria-label": "How much of the event", onchange: () => load() },
      h("option", { value: "show" }, "Just this day"), h("option", { value: "event" }, "All days of this event"));
    const box = h("div", { role: "status" }, h("p", { class: "muted" }, "Reading…"));
    const span = (a, b) => `${SW.fmtTime(a, { date: true })} to ${SW.fmtTime(b, { date: true })}`;
    const num = (n, cls) => h("td", { class: n ? (cls || "") : "muted", style: "text-align:center" }, n ? String(n) : "-");

    async function load() {
      try {
        data = await api("GET", `/api/admin/devices/errors?show_id=${encodeURIComponent(showSel.value)}&scope=${scopeSel.value}`);
        draw();
      } catch (err) { data = null; box.replaceChildren(h("p", { class: "error" }, err.message)); }
    }

    const ENDED = { clear: "", restart: " (ended when Stagewatch restarted)", open: " (still going)", show_end: " (still going when the day ended)", file: " (never cleared in the log)" };
    function timeline(r) {
      const items = r.episodes.slice().reverse().map((e) => h("li", {},
        `${SW.fmtTime(e.start, { seconds: true })} · ${e.level}: ${e.text} · ${e.seconds === null ? "no end known" : SW.faultTime(e.seconds)}${ENDED[e.ended_by] || ""}`));
      return h("tr", {}, h("td", { colspan: 8 }, h("ul", { class: "muted", style: "margin:4px 0 8px 18px" }, items),
        r.episodes_total > r.episodes.length ? h("p", { class: "muted hint" }, `Showing the latest ${r.episodes.length} of ${r.episodes_total}.`) : null,
        r.imported_from.length ? h("p", { class: "muted hint" }, `Imported from ${r.imported_from.join(", ")}.`) : null));
    }

    function rowEls(r) {
      const isOpen = openRows.has(r.key);
      const toggle = r.problems ? h("button", { class: "small", style: "min-height:44px", type: "button", "aria-expanded": String(isOpen),
        "aria-label": `${isOpen ? "Hide" : "Show"} the problems of ${r.name}`,
        onclick: () => { if (isOpen) openRows.delete(r.key); else openRows.add(r.key); draw(); } }, isOpen ? "Hide" : "Show") : null;
      const main = h("tr", {},
        h("td", {}, r.name, r.flapping ? h("span", { class: "pill", style: "margin-left:6px", title: "3 or more problems within 10 minutes" }, "flapping") : null,
          h("div", { class: "muted", style: "font-size:12px" }, r.kind)),
        h("td", { class: "muted" }, r.now || "-"),
        num(r.stops, "error"), num(r.alerts), num(r.advisories), num(r.dropouts),
        h("td", { class: r.seconds_in_fault ? "" : "muted" }, SW.faultTime(r.seconds_in_fault),
          r.never_cleared ? h("div", { class: "muted", style: "font-size:12px" }, `${r.never_cleared} never cleared`) : null),
        h("td", {}, r.last ? h("div", {}, h("span", { class: "muted" }, SW.fmtTime(r.last.ts, { seconds: true }) + " "), `${r.last.level}: ${r.last.text}`) : h("span", { class: "muted" }, "None"),
          toggle));
      return isOpen ? [main, timeline(r)] : [main];
    }

    function draw() {
      const t = data.totals, busy = data.rows.filter((r) => r.problems > 0), quiet = data.rows.filter((r) => r.problems === 0);
      const lines = [
        h("p", {}, t.problems ? `${problemsWord(t.problems)} in ${data.scope === "event" ? `${data.days} ${data.days === 1 ? "day" : "days"}` : "this day"}: ` +
          `${t.stops} Stop, ${t.alerts} Alert, ${t.advisories} Advisory, ${t.dropouts} ${t.dropouts === 1 ? "drop-out" : "drop-outs"}.` : "No problems recorded."),
        ...data.gaps.slice(0, 5).map((g) => h("p", { class: "muted hint" }, `Stagewatch was not running ${span(g.from, g.to)}, so problems in that time can't be counted.`)),
        data.gaps.length > 5 ? h("p", { class: "muted hint" }, `and ${data.gaps.length - 5} more breaks.`) : null,
      ];
      const head = h("thead", {}, h("tr", {}, ["Device", "Now", "Stop", "Alert", "Advisory", "Drop-outs", "Time in fault", "Last problem"].map((x) => h("th", {}, x))));
      const shown = showQuiet ? busy.concat(quiet) : busy;
      const table = shown.length ? h("div", { class: "table-scroll" }, h("table", {}, head, h("tbody", {}, shown.flatMap(rowEls)))) : null;
      const quietBtn = quiet.length ? h("button", { class: "small", style: "min-height:44px", type: "button", onclick: () => { showQuiet = !showQuiet; draw(); } },
        showQuiet ? "Hide quiet devices" : `Show ${quiet.length} quiet ${quiet.length === 1 ? "device" : "devices"}`) : null;
      const csvBtn = h("button", { class: "small", style: "min-height:44px", type: "button", disabled: !data.rows.length, onclick: () => {
        const text = SW.deviceErrorsCsv(data.rows, (ts) => SW.fmtTime(ts, { date: true, seconds: true }));
        const a = h("a", { href: URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" })), download: SW.deviceErrorsFilename(data.show.name, data.show.day) });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      } }, "Save as CSV");
      box.replaceChildren(...[...lines, table].filter(Boolean), h("div", { class: "row" }, quietBtn, csvBtn, h("button", { class: "small", style: "min-height:44px", type: "button", onclick: () => load() }, "Refresh")));
    }
    load();

    const c = card("Device errors",
      h("p", { class: "muted" }, "How many problems each device had, and when, so you can see which box keeps dropping out. A problem is counted once however long it lasts or however many units report it at the same moment. Imported LA Network Manager logs add their units here. Limit alarms (a stage that is too hot) are not device problems and stay in the alarm log."),
      h("div", { class: "row" }, field("Show", showSel), field("How much", scopeSel)),
      box);
    c.id = "device-errors-card";
    return c;
  }

  // ------------------------------------------------- import a log file (LA Network Manager)
  const LOG_MAX_CHARS = 5000000;   // the server's limit on the file's text
  const LEVEL_CLASS = { Stop: "error", Alert: "", Advisory: "muted" };

  function logImportCard() {
    let text = "", fileName = "", ticket = 0;
    const shows = (admin.events || []).flatMap((e) => e.shows.map((sh) => ({ id: sh.id, label: `${sh.name} · ${SW.fmtDay(sh.day)} (${e.name})` })));
    const currentId = snap.show && snap.show.id;
    const showSel = h("select", { class: "touch", "aria-label": "Show to import into", onchange: () => { loadImports(); preview(); } },
      shows.map((o) => h("option", { value: String(o.id), selected: o.id === currentId }, o.label + (o.id === currentId ? " – current" : ""))));
    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    const tz = h("input", { class: "touch", list: zones.length ? "tz-list" : null, placeholder: `Blank = this site's zone${admin.config.site.timezone ? " (" + admin.config.site.timezone + ")" : ""}`,
      maxlength: 64, autocomplete: "off", autocapitalize: "off", spellcheck: false, "aria-label": "Time zone of the computer that wrote the file", onchange: () => preview() });
    const offset = h("input", { class: "num touch", type: "number", step: "1", value: "0", "aria-label": "Clock offset in seconds", onchange: () => preview() });
    const sessionMarkers = h("input", { type: "checkbox", onchange: () => preview() });
    const loadCheckerMarkers = h("input", { type: "checkbox", onchange: () => preview() });
    const status = h("p", { class: "muted", role: "status" });
    const previewBox = h("div", { role: "status" });
    const listBox = h("div");

    const fileInput = h("input", { class: "touch", type: "file", accept: ".xml,text/xml,application/xml", "aria-label": "LA Network Manager log file to read", onchange: () => {
      const f = fileInput.files[0]; fileInput.value = "";
      if (!f) return;
      if (f.size > LOG_MAX_CHARS) { toast("That file is too big to be an LA Network Manager log. Nothing has been changed.", true); return; }
      const rd = new FileReader();
      rd.onload = () => { text = String(rd.result); fileName = f.name; preview(); };
      rd.onerror = () => toast("Could not read that file. Nothing has been changed.", true);
      rd.readAsText(f);
    } });

    const body = (dry) => ({ text, file_name: fileName, show_id: Number(showSel.value), tz: tz.value.trim(), offset_s: Number(offset.value) || 0,
      session_markers: sessionMarkers.checked, load_checker_markers: loadCheckerMarkers.checked, dry_run: dry });
    const span = (a, b) => `${SW.fmtTime(a, { date: true })} to ${SW.fmtTime(b, { date: true })}`;
    const clear = () => { text = ""; ticket++; previewBox.replaceChildren(); };

    async function preview() {
      if (!text) return;
      const mine = ++ticket;
      status.textContent = "Reading…";
      try {
        const r = await api("POST", "/api/admin/logs/import", body(true));
        if (mine !== ticket) return;
        status.textContent = ""; showPlan(r);
      } catch (err) {
        if (mine !== ticket) return;
        previewBox.replaceChildren(); status.textContent = err.message;
      }
    }

    function showPlan(r) {
      const none = r.new === 0;
      const lines = [
        h("p", {}, h("strong", {}, fileName), ` covers ${span(r.file.first, r.file.last)}. This show day is ${span(r.show_day.from, r.show_day.to)}.`),
        r.file.closed ? null : h("p", { class: "muted hint" }, "LA Network Manager had not closed this log (it was still open, or stopped suddenly). Everything up to the last whole line is read."),
        h("p", {}, `${r.new} new ${r.new === 1 ? "entry" : "entries"} to add, ${r.markers} ${r.markers === 1 ? "marker" : "markers"}. `
          + `${r.already_present} already imported, ${r.outside_day} outside this show day, ${r.session_left_out} session ${r.session_left_out === 1 ? "line" : "lines"} left out (they hold the laptop's addresses).`),
        none && r.outside_day > 0 && r.already_present === 0 ? h("p", { class: "error" }, "Nothing falls on this show day. Check the show, the time zone and the clock offset above.") : null,
        none && r.already_present > 0 ? h("p", { class: "muted" }, "Everything in this file is already imported.") : null,
      ];
      const eps = r.episodes.length ? h("details", { open: r.episodes.length <= 12 }, h("summary", {}, `Faults in the file (${r.episodes_total})`),
        h("div", { class: "table-scroll", style: "max-height:260px;overflow-y:auto" }, h("table", {}, h("tbody", {}, r.episodes.map((e) => h("tr", {},
          h("td", { class: "muted" }, SW.fmtTime(e.start, { seconds: true })),
          h("td", { class: LEVEL_CLASS[e.level] || "" }, e.level),
          h("td", {}, `${e.unit}: ${e.what}`),
          h("td", { class: "muted" }, e.seconds === null ? "never cleared" : e.seconds < 1 ? "cleared in under a second" : `cleared after ${SW.fmtDuration(e.seconds, false)}`))))))) : null;
      previewBox.replaceChildren(h("div", {}, ...lines, eps,
        h("div", { class: "row" },
          h("button", { class: "primary touch", type: "button", disabled: none, onclick: doImport }, "Import into this show"),
          h("button", { class: "touch", type: "button", onclick: () => { clear(); status.textContent = ""; } }, "Cancel"))));
    }

    async function doImport() {
      try {
        const r = await run(() => api("POST", "/api/admin/logs/import", body(false)), "Log imported");
        clear();
        status.textContent = `Imported ${r.rows} entries and ${r.markers} markers. Use Undo below to take it back out.`;
        await loadImports();
      } catch (_) { /* the toast says why */ }
    }

    async function loadImports() {
      try {
        const r = await api("GET", `/api/admin/logs/imports?show_id=${encodeURIComponent(showSel.value)}`);
        const name = (i) => i.source.replace(/^lanwm:/, "");
        listBox.replaceChildren(r.imports.length ? h("div", { class: "table-scroll" }, h("table", {}, h("tbody", {}, r.imports.map((i) => h("tr", {},
          h("td", {}, name(i)), h("td", { class: "muted" }, span(i.first, i.last)),
          h("td", {}, `${i.rows} entries, ${i.markers} markers`),
          h("td", {}, h("button", { class: "small danger", style: "min-height:44px", type: "button", "aria-label": `Undo import of ${name(i)}`,
            onclick: () => confirm(`Take this import back out? Its ${i.rows} entries and ${i.markers} markers are removed from the show.`)
              && run(() => api("DELETE", `/api/admin/logs/imports/${encodeURIComponent(i.import_id)}?show_id=${encodeURIComponent(showSel.value)}`), "Import removed").then(loadImports, () => {}) }, "Undo")))))))
          : h("p", { class: "muted" }, "Nothing imported into this show yet."));
      } catch (err) { listBox.replaceChildren(h("p", { class: "error" }, err.message)); }
    }
    loadImports();

    return card("Import a log file",
      h("p", { class: "muted" }, "Read an L-Acoustics Network Manager log after a show, so amplifier faults and power events appear as markers in that show's timeline and report. Stagewatch only reads a copy of the file; it never talks to Network Manager or the amplifiers. Nothing is added until you press Import, and every import can be undone."),
      h("p", { class: "muted hint" }, "The file is in the L-Acoustics folder inside AppData on the computer that runs Network Manager: LA Network Manager → Log, named Log_ with the date and time."),
      h("div", { class: "row" }, h("label", { class: "field inline" }, "Log file: ", fileInput)),
      h("div", { class: "row" },
        field("Show", showSel),
        field("Time zone of that computer", tz),
        field("Clock offset (seconds)", offset)),
      h("p", { class: "muted hint" }, "The log has no time zone, so it is read as the time zone above. If the laptop's clock was wrong, put the number of seconds to add (a laptop 5 minutes slow: 300)."),
      h("label", { class: "field inline" }, sessionMarkers, " Also add markers for going online or offline and for Acknowledge all"),
      h("label", { class: "field inline" }, loadCheckerMarkers, " Also add markers for entering and leaving Load Checker (it can make inputs unlock)"),
      status, previewBox,
      h("h3", { class: "muted", style: "font-size:13px;margin:14px 0 6px" }, "IMPORTED INTO THE SELECTED SHOW"),
      listBox);
  }

  // Which sensor groups are open, remembered per node in this browser. Kept in memory too, so a
  // re-render after Save keeps the state even when the browser has no storage.
  // The same store and buttons serve the Sensors, Connect a tablet and Software cards.
  const softClosed = new Set();   // soft-held cards the person closed during this notice
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
      // `force` "soft" opens it for a notice but lets the person close it: closed stays closed
      // (this page visit) until the notice clears and comes back.
      bind(d, id, dflt, force) {
        let skip = 0;
        if (force !== "soft") softClosed.delete(id);
        const open = force === "soft" ? !softClosed.has(id) : force || store.isOpen(id, dflt);
        if (open) { d.open = true; skip = 1; }   // this change fires one toggle event: ignore it
        d.addEventListener("toggle", () => {
          if (skip) { skip = 0; return; }
          if (force === "soft") { if (d.open) softClosed.delete(id); else softClosed.add(id); return; }
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
  const foldButtons = (onAll, what = "") => h("div", { class: "row sg-buttons" },
    h("button", { class: "touch", onclick: () => onAll(true) }, `Expand all${what}`),
    h("button", { class: "touch", onclick: () => onAll(false) }, `Collapse all${what}`));
  const sensorFolds = foldStore("sw.admin.sensors.open");
  const connectFolds = foldStore("sw.admin.connect.open2");
  const softwareFolds = foldStore("sw.admin.software.open");
  // Whole cards that fold under their heading, one remembered choice each. The key is a fixed
  // card name, kept in its own store so it cannot clash with a dashboard slug or a sensor group.
  const cardFolds = foldStore("sw.admin.cards.open");
  let pageFolds = [];   // every card fold on the page, for the Expand all / Collapse all cards buttons
  // Fold a card away under its <h2> (as Connect a tablet does). `dflt` is the first-visit state;
  // `force` keeps it open and un-foldable while it carries a notice. A re-render builds a new
  // <details> from the remembered state, so a card never closes itself under someone.
  function foldCard(c, key, dflt, force) {
    const head = c.firstChild, rest = Array.from(c.childNodes).slice(1);
    const whole = h("details", { class: "card-fold", id: `card-fold-${key}` }, h("summary", { id: `card-sum-${key}` }, head));
    whole.append(...rest);
    c.append(whole);
    cardFolds.bind(whole, key, dflt, force);
    pageFolds.push({ d: whole, store: cardFolds, key, force });
    return c;
  }
  // The one pair of buttons at the top of the page: every card, except those held open by a notice.
  function setAllCards(open) {
    pageFolds = pageFolds.filter((f) => f.d.isConnected);
    const stores = new Set();
    pageFolds.forEach((f) => {
      if (f.force === "soft") { f.d.open = open; return; }   // its toggle handler notes the choice
      if (f.force) return;
      f.d.open = open; f.store.state()[f.key] = open; stores.add(f.store);
    });
    stores.forEach((st) => st.save());
  }
  // The server recomputes the averages once a second: wait for that before redrawing the shares.
  const afterTick = (v) => new Promise((resolve) => setTimeout(() => resolve(v), 1200));
  const KIND_WORD = { temperature: "Temperature", humidity: "Humidity", pressure: "Pressure" };
  // One line per kind with a reason (or "Weighted by accuracy") while the switch is on.
  function weightNoteItems(averages, on) {
    if (!on) return [];
    return ["temperature", "humidity", "pressure"].filter((k) => averages[k] && averages[k].note)
      .map((k) => h("li", {}, `${KIND_WORD[k]}: ${averages[k].note}`));
  }

  function entitiesCard() {
    const meterIds = new Set(snap.devices.filter((d) => d.category === "meter" || d.category === "host").map((d) => d.id));
    const ents = snap.entities.filter((e) => !e.derived && !meterIds.has(e.device_id));   // power meter readings are never adjusted
    const settingsOf = (e) => (admin.hardware && admin.hardware.settings[e.id]) || admin.config.entities[e.id]
      || { offset: 0, include_in_average: true, accuracy: null, accuracy_basis: "typical", role: "" };
    const nodeRoleOf = (id) => {
      const hd = admin.hardware && admin.hardware.devices && admin.hardware.devices[id];
      return hd && hd.role === "equipment" ? "equipment" : "environment";
    };
    const averages = (admin.hardware && admin.hardware.averages) || {};
    const shareOf = (e) => ((averages[e.kind] || {}).sensors || {})[e.id];
    // Same order as the adopted-nodes list; anything else (site rows) goes last, in a "Site" group.
    const devs = snap.devices.filter((d) => d.id !== "site" && d.category !== "service" && d.category !== "meter" && d.category !== "host");   // meter readings are never adjusted
    const groups = devs.map((d) => ({ id: d.id, name: d.name, status: d.status, sleeping: !!d.sleeping, role: nodeRoleOf(d.id), ents: ents.filter((e) => e.device_id === d.id) }));
    const known = new Set(devs.map((d) => d.id));
    const rest = ents.filter((e) => !known.has(e.device_id));
    if (rest.length) groups.push({ id: "site", name: "Site", status: "", role: "environment", ents: rest });
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

    const shareText = (sh) => (sh && sh.state === "equipment" ? "Never" : sh && sh.state === "in" ? SW.fmtShare(sh.share) + (sh.capped ? " (capped)" : "") : sh && sh.state === "stale" ? "Stale" : sh && sh.state === "outlier" ? "Left out" : "—");
    const shareTitle = (sh) => (!sh ? "" : sh.state === "equipment" ? "Equipment readings are never averaged" : sh.state === "off" ? "Not in the site average (ticked off)"
      : sh.state === "stale" ? "No recent reading, so left out of the average" : sh.state === "outlier" ? "Reading far from the others, so left out of the average"
      : sh.state === "none" ? "No reading yet" : sh.capped ? "Capped at 80 %: this sensor would otherwise have counted for more" : "Share of the site average now");
    const groupEl = (g) => {
      const offs = g.ents.map(offsetText).filter(Boolean);
      const bad = g.status && g.status !== "ok" && !g.sleeping;
      const defaultOpen = offs.length > 0 || !!bad;
      const rows = g.ents.map((e) => {
        const s = settingsOf(e);
        const label = shortName(g, e);
        const off = h("input", { class: "num", type: "number", step: "0.01", value: s.offset, "aria-label": `${g.name} ${label} offset` });
        const inc = h("input", { type: "checkbox", checked: s.include_in_average, "aria-label": `${g.name} ${label} in site average` });
        const accEls = [];   // greyed out while the sensor is Equipment
        const roleSel = h("select", { "aria-label": `${g.name} ${label} role` },
          h("option", { value: "" }, `Node's role (${g.role === "equipment" ? "Equipment" : "Environment"})`),
          h("option", { value: "environment" }, "Environment"), h("option", { value: "equipment" }, "Equipment"));
        roleSel.value = s.role === "environment" || s.role === "equipment" ? s.role : "";
        const eqHint = h("div", { class: "muted", style: "font-size:12px", role: "status" });
        const accKind = SW.ACCURACY_KINDS[e.kind];
        let accCells;
        let readAccuracy = () => ({});
        if (accKind) {
          const shown = SW.accuracyShown(e.kind, s.accuracy);
          const acc = h("input", { class: "num", type: "number", step: "any", min: "0", value: shown === null ? "" : shown, inputmode: "decimal",
            "aria-label": `${g.name} ${label} accuracy` });
          const basis = h("select", { "aria-label": `${g.name} ${label} accuracy basis` },
            h("option", { value: "typical" }, "typical"), h("option", { value: "maximum" }, "maximum"));
          basis.value = s.accuracy_basis === "maximum" ? "maximum" : "typical";
          accEls.push(acc, basis);
          const presetNote = h("div", { class: "muted preset-note", role: "status", style: "font-size:12px" });
          const preset = h("select", { "aria-label": `${g.name} ${label} preset` },
            h("option", { value: "" }, "Preset…"),
            SW.ACCURACY_PRESETS.map((part) => h("option", { value: part.id }, part.name)));
          accEls.push(preset);
          preset.addEventListener("change", () => {
            const part = SW.ACCURACY_PRESETS.find((x) => x.id === preset.value);
            if (!part) { presetNote.textContent = ""; return; }
            const fill = SW.presetFill(part, e.kind);
            if (fill.accuracy !== null) { acc.value = String(fill.accuracy); basis.value = fill.basis; }
            presetNote.textContent = fill.hint;
            dirty = true;
          });
          accCells = [
            h("td", {}, h("div", { class: "acc-field" }, h("span", { class: "muted" }, "±"), acc, h("span", { class: "muted" }, ` ${accKind.unit}`), basis)),
            h("td", {}, preset, presetNote)];
          readAccuracy = () => {
            const typed = acc.value.trim();
            const canon = SW.accuracyCanon(e.kind, typed);
            if (typed !== "" && canon === null) return null;   // typed something we can't use
            return { accuracy: canon, accuracy_basis: basis.value };
          };
        } else {
          accCells = [h("td", { class: "muted", colspan: "2" }, "—")];
        }
        const sh = shareOf(e);
        const applyRole = () => {
          const eq = (roleSel.value || g.role) === "equipment";
          inc.disabled = eq;
          accEls.forEach((x) => { x.disabled = eq; });
          eqHint.textContent = eq ? "Equipment readings are never averaged" : "";
          tr.classList.toggle("role-eq", eq);
        };
        const accText = accKind ? SW.fmtAccuracy(e.kind, s.accuracy, s.accuracy_basis) : "";
        const tr = h("tr", { id: `sensor-row-${e.id}` },
          h("td", {}, label, h("div", { class: "muted", style: "font-size:12px" }, e.id)),
          h("td", { class: "num" }, h("span", { dataset: { live: e.id } }, fmt(e.kind, e.value)),
            accText ? h("div", { class: "muted", style: "font-size:12px" }, accText) : null),
          h("td", {}, off, h("span", { class: "muted" }, " ", e.unit)),
          h("td", {}, roleSel),
          h("td", {}, inc, eqHint),
          h("td", { class: "num" }, accKind ? h("span", { dataset: { share: e.id }, title: shareTitle(sh) }, shareText(sh)) : "—"),
          accCells,
          h("td", {}, h("button", { class: "small", "aria-label": `Save ${g.name} ${label}`, onclick: () => {
            const y = window.scrollY;
            const accBody = readAccuracy();
            if (accBody === null) { toast(`Type the accuracy as a number from ${accKind.min} to ${accKind.max} ${accKind.unit}, for example 0.5. Nothing has been changed.`, true); return; }
            api("PUT", `/api/admin/entities/${encodeURIComponent(e.id)}`,
              Object.assign({ offset: Number(off.value) || 0, include_in_average: inc.checked, role: roleSel.value }, accBody))
              .then(() => { toast("Saved"); return afterTick(); }).then(refresh,
                (err) => toast(err.status === 422 ? "That accuracy figure was not accepted. Nothing has been changed." : err.message, true))
              .then(() => window.scrollTo(0, y));
          } }, "Save")));
        roleSel.addEventListener("change", applyRole);
        applyRole();
        return tr;
      });
      const d = h("details", { class: "sensor-group", id: `sensor-group-${g.id}` },
        h("summary", {},
          h("span", { class: "sg-name" }, g.name),
          g.status ? h("span", { class: `status ${g.sleeping ? "sleeping" : g.status}` }, g.sleeping ? "sleeping" : g.status) : null,
          h("span", { class: "muted" }, `${g.ents.length} ${g.ents.length === 1 ? "sensor" : "sensors"}`),
          offs.length ? h("span", { class: "muted sg-offs" }, `offset: ${offs.join(", ")}`) : null),
        h("div", { class: "table-scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, "Sensor"), h("th", { class: "num" }, "Value"), h("th", {}, "Offset"), h("th", {}, "Role"), h("th", {}, "Average"), h("th", { class: "num" }, "Share"), h("th", {}, "Accuracy"), h("th", {}, "Preset"), h("th", {}, ""))),
          h("tbody", {}, rows))));
      d.open = Object.prototype.hasOwnProperty.call(state, g.id) ? !!state[g.id] : defaultOpen;
      d.addEventListener("toggle", () => { state[g.id] = d.open; sensorFolds.save(); });
      return d;
    };
    const els = shown.map(groupEl);
    const weigh = h("input", { type: "checkbox", checked: !!admin.config.site.weight_by_accuracy, "aria-label": "Weight the average by accuracy" });
    weigh.addEventListener("change", () => {
      const y = window.scrollY;
      run(() => api("PUT", "/api/admin/site", Object.assign({}, admin.config.site, { weight_by_accuracy: weigh.checked })), weigh.checked ? "Weighting on" : "Weighting off")
        .then(afterTick).then(refresh, () => { weigh.checked = !weigh.checked; }).then(() => window.scrollTo(0, y));
    });
    const notes = h("ul", { class: "weight-notes muted", role: "status", dataset: { weightNotes: "1" } });
    notes.append(...weightNoteItems(averages, admin.config.site.weight_by_accuracy));
    const setAll = (open) => sensorFolds.setAll(els, shown.map((g) => g.id), open);
    // Environment and Equipment headings, with the node folds under the one their role names.
    const section = (title, role, empty) => {
      const mine = shown.map((g, i) => ({ g, el: els[i] })).filter((x) => x.g.role === role);
      return [h("h3", { class: "sg-heading", id: `sensor-heading-${role}` }, title)]
        .concat(mine.length ? mine.map((x) => x.el) : [h("p", { class: "muted hint" }, empty)]);
    };
    return card("Sensors: calibration & averaging",
      h("p", { class: "muted" }, "Offset is added to every reading (compare against a reference such as a Kestrel). Pressure offsets are in Pa (1 hPa = 100 Pa). Untick to leave a sensor out of the site average, e.g. one in direct sun."),
      h("label", { class: "field inline" }, weigh, " Weight the average by accuracy"),
      h("p", { class: "muted hint" }, "Off: every sensor in the average counts the same. On: a sensor with a smaller accuracy figure counts for more, but no sensor counts for more than 80 %, so the average still covers the places you put sensors. This only applies to temperature, humidity or pressure when every sensor in that average has an accuracy figure and the figures are all typical or all maximum. Otherwise that one uses equal weights, and the reason is shown here. Share is how much each sensor counts in the average now."),
      notes,
      SW.help(h("p", { class: "muted hint" }, "Accuracy is the maker's ± figure from the datasheet, in °C, %RH or hPa. Typical is the usual figure; maximum is the worst case. Presets fill the typical figure from the manufacturer's page: check it against your sensor's datasheet, and change it if you know better."),
        h("p", { class: "muted hint" }, "After you calibrate a sensor, type the uncertainty you are left with, not the datasheet figure. Sensors calibrated against the same reference share that reference's error, so averaging them does not remove it. Where a sensor sits (sun, lights, heat, its own warmth, airflow) usually matters more than the datasheet. Accuracy is only shown here, not on the dashboards.")),
      shown.length ? foldButtons(setAll) : null,
      shown.length ? [...section("Environment", "environment", "None."),
        ...section("Equipment", "equipment", "None. Set a node's Role to Equipment under ESPHome nodes (an amp rack, a power supply). Equipment readings are never averaged.")]
        : h("p", { class: "muted" }, "No sensors yet. Add a node under ESPHome nodes."));
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
      // OSC actions on raised / cleared / acknowledged (sent only while actions are armed).
      const ta = t.actions || {};
      const targets = (admin.config.actions && admin.config.actions.targets) || [];
      const eds = ["raised", "cleared", "acked"].map((k) => [k, SW.actionMessageEditor(ta[k], targets,
        { label: { raised: "When raised, send", cleared: "When cleared, send", acked: "When acknowledged, send" }[k], placeholders: true, test: true })]);
      const cool = h("input", { class: "num", type: "number", min: 0, max: 3600, step: 1, value: ta.cooldown_s ?? 60 });
      const has = ["raised", "cleared", "acked"].some((k) => ta[k]);
      const actRow = h("tr", { class: "thr-actions" }, h("td", { colspan: 9 }, h("details", { open: false },
        h("summary", {}, has ? "OSC actions ✓" : "OSC actions"),
        targets.length ? null : h("p", { class: "muted" }, "No action targets yet. Add one in Action targets below."),
        eds.map(([, e]) => e.el),
        field("Cool-down (seconds)", cool),
        h("p", { class: "muted hint" }, "Values can be {value} (the reading), {level} (1 to 3) or {label}. Each trigger sends at most once per cool-down. An alarm already active when Stagewatch starts sends nothing."))));
      const tr = h("tr", {}, h("td", {}, sel), h("td", {}, label), h("td", {}, above), h("td", {}, below),
        h("td", {}, level), h("td", {}, hyst), h("td", {}, hold), h("td", {}, en),
        h("td", {}, h("button", { class: "small danger", onclick: () => { tr.remove(); actRow.remove(); } }, "✕")));
      tr._read = (i) => ({ id: t.id || `t${Date.now().toString(36)}${i}`, entity: sel.value, label: val(label),
        above: numOrNull(above), below: numOrNull(below), level: Number(level.value),
        hysteresis: Number(hyst.value) || 0, hold_s: Number(hold.value) || 0, enabled: en.checked,
        actions: Object.assign({ cooldown_s: Math.max(0, Math.min(3600, Math.round(Number(cool.value) || 0))) },
          Object.fromEntries(eds.map(([k, e]) => [k, e.read()]))) });
      tbody.append(tr, actRow);
    };
    admin.config.thresholds.forEach(addRow);
    return card("Threshold alarms",
      h("p", { class: "muted" }, "Values in display units except pressure (Pa). Hysteresis: how far back past the limit before it clears. Hold: seconds the condition must last before alarming. Alert/Stop alarms also drop a marker on the timeline."),
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Entity", "Label", "Above", "Below", "Level", "Hyst.", "Hold s", "On", ""].map((x) => h("th", {}, x)))), tbody)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { onclick: () => addRow() }, "Add threshold"),
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/thresholds",
          [...tbody.children].filter((tr) => tr._read).map((tr, i) => tr._read(i))), "Thresholds saved").then(refresh) }, "Save thresholds")));
  }

  // The dashboards table. Which cards each one shows is chosen on its own page (Edit cards,
  // /dashboard-editor), so this card keeps one size. A save here sends no card fields, and the
  // server keeps each dashboard's cards; a renamed dashboard sends its saved ones, so it keeps
  // them under the new name. A new dashboard gets its layout's default cards from the server.
  const CARD_FIELDS = ["cards", "stage", "clock_style", "card_sizes", "globcon", "show_dew_point", "show_contact_phones", "contacts_area"];
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
      const edit = isNew ? h("span", { class: "muted" }, "Save first")
        : SW.linkButton(`Edit cards (${(d.cards || []).length})`, `/dashboard-editor?d=${encodeURIComponent(d.slug)}`);
      const tr = h("tr", {}, h("td", {}, slug), h("td", {}, title), h("td", {}, layout), h("td", {}, mk), h("td", {}, ack),
        h("td", {}, edit), h("td", {}, link),
        h("td", {}, h("button", { class: "small danger", onclick: () => tr.remove() }, "✕")));
      tr._read = () => {
        const out = { slug: val(slug), title: val(title), layout: layout.value, allow_marker: mk.checked, allow_ack: ack.checked };
        if (!isNew && out.slug !== d.slug) CARD_FIELDS.forEach((k) => { if (d[k] !== undefined) out[k] = d[k]; });
        return out;
      };
      tbody.append(tr);
    };
    admin.config.dashboards.forEach(addRow);
    return card("User dashboards",
      h("p", { class: "muted" }, "Each dashboard has its own URL (/d/<name>) for tablets, phones and kiosk displays. Users can view without logging in; tick what they may do. Edit cards opens a page where you choose what each one shows, with a preview."),
      h("datalist", { id: "stage-list" }, (admin.stages || []).map((s) => h("option", { value: s }))),   // also the Onsite contacts areas
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["URL name", "Title", "Layout", "Add markers", "Ack alarms", "Cards", "", ""].map((x) => h("th", {}, x)))), tbody)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { onclick: () => addRow() }, "Add dashboard"),
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/dashboards",
          [...tbody.children].filter((tr) => tr._read).map((tr) => tr._read())), "Dashboards saved").then(refresh) }, "Save dashboards")));
  }

  function actionTargetsCard() {
    const tbody = h("tbody");
    const addRow = (t = {}) => {
      const name = h("input", { value: t.name || "", maxlength: 40, placeholder: "e.g. QLab", "aria-label": "Target name" });
      const host = h("input", { value: t.host || "", placeholder: "192.168.1.20 or qlab.local", "aria-label": "Address" });
      const port = h("input", { class: "num", type: "number", min: 1, max: 65535, value: t.port || 53000, "aria-label": "Port" });
      const en = h("input", { type: "checkbox", checked: t.enabled !== false, "aria-label": "On" });
      const id = t.id || `t-${Date.now().toString(36)}-${tbody.children.length}`;
      const tr = h("tr", {}, h("td", {}, name), h("td", {}, host), h("td", {}, port), h("td", {}, en),
        h("td", {}, h("button", { class: "small", onclick: () => SW.actionTest({ target_id: id, address: "/stagewatch/test", args: [] },
          [{ id, name: val(name), host: val(host), port: Number(port.value) }]) }, "Send test message")),
        h("td", {}, h("button", { class: "small danger", onclick: () => tr.remove() }, "✕")));
      tr._read = () => ({ id, name: val(name), host: val(host), port: Number(port.value), enabled: en.checked });
      tbody.append(tr);
    };
    ((admin.config.actions && admin.config.actions.targets) || []).forEach(addRow);
    const st = admin.actions || {};
    return card("Action targets",
      h("p", { class: "warn-text" }, h("strong", {}, SW.ACTION_WARNING)),
      h("p", { class: "muted" }, "Where schedule and threshold actions send their OSC messages (UDP). Use a local network address or a name ending in .local. Save the list before sending a test to a new target."),
      h("p", {}, st.armed ? "● Actions ARMED (switch them off on the Schedule page)" : "○ Actions off. Arm them on the Schedule page when you are ready."),
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Name", "Address", "Port", "On", "", ""].map((x) => h("th", {}, x)))), tbody)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { onclick: () => addRow() }, "Add target"),
        h("button", { class: "primary", onclick: () => run(() => api("PUT", "/api/admin/actions/targets",
          { targets: [...tbody.children].map((tr) => tr._read()).filter((t) => t.host || t.name) }), "Action targets saved").then(refresh) }, "Save targets")),
      h("p", { class: "muted hint" }, SW.ACTION_EXAMPLES));
  }

  function oscCard() {
    const o = admin.config.osc_out;
    const tbody = h("tbody");
    const addRow = (d = {}) => {
      const host = h("input", { value: d.host || "", placeholder: "IP or hostname" });
      const port = h("input", { class: "num", type: "number", value: d.port || 9000 });
      const en = h("input", { type: "checkbox", checked: d.enabled !== false });
      const am = h("input", { type: "checkbox", checked: !!d.alarm_messages, "aria-label": "Send alarm messages" });
      const tr = h("tr", {}, h("td", {}, host), h("td", {}, port), h("td", {}, en), h("td", {}, am),
        h("td", {}, h("button", { class: "small danger", onclick: () => tr.remove() }, "✕")));
      tr._read = () => ({ host: val(host), port: Number(port.value), enabled: en.checked, alarm_messages: am.checked });
      tbody.append(tr);
    };
    o.destinations.forEach(addRow);
    const perNode = h("input", { type: "checkbox", checked: o.per_node });
    const rate = h("input", { class: "num", type: "number", step: "0.5", value: o.rate_hz });
    const info = admin.integrations.find((i) => i.manifest.domain === "osc_out") || {};
    return card("OSC output",
      SW.help(h("p", { class: "muted" }, "Sends /stagewatch/avg/env [temp °C, RH %, pressure Pa, c m/s, sensors] and /stagewatch/alarm [level, sounding]."),
        h("p", { class: "muted" }, "Alarm messages: each threshold alarm change also sends /stagewatch/alarm/event [id, level 0 to 3, raised / acked / cleared, message].")),
      h("table", {}, h("thead", {}, h("tr", {}, ["Host", "Port", "On", "Alarm messages", ""].map((x) => h("th", {}, x)))), tbody),
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
  // chosen per dashboard under User dashboards → Edit cards. Everything about Ontime itself
  // (address, test, warning limit) is in the Ontime card.
  function wallClockCard() {
    const w = admin.config.wall_clock, st = admin.wall_clock || {};
    const d = w.display || {};
    const source = h("select", {}, h("option", { value: "pc" }, "Stagewatch PC"), h("option", { value: "ontime" }, "Ontime"));
    source.value = w.source === "ontime" ? "ontime" : "pc";
    const hour12 = h("select", {}, h("option", { value: "24" }, "24-hour"), h("option", { value: "12" }, "12-hour (am/pm)"));
    hour12.value = d.hour12 ? "12" : "24";
    const showDate = h("input", { type: "checkbox", checked: !!d.show_date });
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
      h("p", { class: "muted" }, "Shows the time on dashboards that have the Wall Clock card. Choose where the time comes from: this computer, or Ontime. If the source stops, the clock says so. It never switches to another source by itself."),
      h("p", { class: "muted", id: "wallclock-ontime-pointer" }, "Ontime settings are in the Ontime card."),
      h("div", { class: "row" }, field("Time source", source),
        field("Time format", hour12), field("Show the date", showDate),
        h("button", { class: "primary", style: "align-self:flex-end", onclick: () => run(() => api("PUT", "/api/admin/wall-clock", {
          source: source.value, ontime_url: w.ontime_url, warn_offset_s: w.warn_offset_s,
          display: { hour12: hour12.value === "12", show_date: showDate.checked, ring: d.ring === "fill" ? "fill" : "sweep" },
        }), "Wall Clock saved").then(refresh, () => {}) }, "Save")),
      h("p", { class: "muted hint" }, "These apply to every dashboard. The look (plain digits, LED ring or 7-segment) is set for each dashboard under User dashboards → Edit cards. Ring and 7-segment are always red on black."),
      h("p", { class: "muted" }, lines.join(" · ")));
  }
  // ------------------------------------------------------------ ontime
  // Every Ontime setting in one card: the connection shared by the Wall Clock, Ontime Timer and
  // Ontime Rundown cards, the Wall Clock's warning limit, and the Timer/Rundown title switch.
  // Read-only: Stagewatch only listens to Ontime. The old API endpoints and config fields are kept.
  function ontimeStatusLines(adm) {
    const wc = adm.wall_clock || {}, tm = adm.ontime_timer || {}, rd = adm.ontime_rundown || {};
    const clockOnOntime = adm.config.wall_clock.source === "ontime";
    const one = (name, st, offText) => {
      if (!st.active) return `${name}: ${st.card_assigned ? "starting…" : offText}`;
      if (st.status === "ok") {
        return `${name}: connected${st.last_message ? `, last message ${SW.fmtTime(st.last_message, { seconds: true })}` : ""}${st.version ? `, Ontime version ${st.version}` : ""}`;
      }
      return `${name}: ▲ not connected${st.detail ? ` (${st.detail})` : ""}`;
    };
    const lines = [];
    if (clockOnOntime) lines.push(one("Wall Clock", wc, "not running (no dashboard has the Wall Clock card)"));
    lines.push(one("Ontime Timer", tm, "not running (no dashboard has the Ontime Timer card)"));
    lines.push(one("Ontime Rundown", rd, "not running (no dashboard has the Ontime Rundown card)"));
    return lines;
  }
  // An error is a running source that cannot reach Ontime. It holds the card open, as Software does
  // for an update. A source that is not running (no card on any dashboard) is not an error.
  function ontimeHasError(adm) {
    const list = [adm.ontime_timer || {}, adm.ontime_rundown || {}];
    if (adm.config.wall_clock.source === "ontime") list.push(adm.wall_clock || {});
    return list.some((st) => st.active && st.status && st.status !== "ok");
  }
  // ------------------------------------------------------------ connections
  // Where each outside product is. The card lists the products that are set up, each with an Edit
  // button; "Add integration" picks one that is not. Both open the same pop-out, the same shape for
  // every product: address (IP or host name), port, password where the product has one, Test where
  // Stagewatch can try it, Save. Each Save uses that product's own settings call and keeps its other
  // settings as saved (they stay on its own card).
  const connText = (host, port, pwSet) => `Address: ${host || "not set"}${port ? `, port ${port}` : ""}${pwSet ? ", password saved" : ""}. Change it under Connections.`;
  // Ontime's address is one URL (http://host:port); the pop-out shows it as two boxes and rebuilds it.
  const splitUrl = (u) => {
    const m = /^(https?:\/\/)(\[[^\]]+\]|[^/:]+)(?::(\d+))?(\/.*)?$/i.exec(String(u || "").trim());
    return m ? { scheme: m[1], host: m[2], port: m[3] || "", path: m[4] || "" } : { scheme: "http://", host: "", port: "", path: "" };
  };
  // One entry per product: what is saved now, and how to test and save an address.
  function connProducts() {
    const w = admin.config.wall_clock, ot = splitUrl(w.ontime_url);
    const otUrl = (v) => `${ot.scheme}${v.host}${v.port ? `:${v.port}` : ""}${ot.path}`;
    const s = admin.config.spl || {}, sst = admin.spl || {};
    const g = admin.config.globcon || {}, gst = admin.globcon || {};
    return [
      // Ontime counts as added once its address is changed from the default or any card uses it.
      { key: "ontime", name: "Ontime", badge: "ontime-badge.png", host: ot.host, port: ot.port, dflt: 4001, password: null,
        added: w.ontime_url !== "http://127.0.0.1:4001" || w.source === "ontime" || [admin.ontime_timer, admin.ontime_rundown].some((x) => x && (x.active || x.card_assigned)),
        test: async (v) => { const r = await api("POST", "/api/admin/wall-clock/test", { ontime_url: otUrl(v) }); return r.ok ? `Ontime ${r.version} answered.` : r.message; },
        save: async (v) => { await api("PUT", "/api/admin/wall-clock", { source: w.source, ontime_url: otUrl(v), warn_offset_s: w.warn_offset_s, display: w.display }); return "Ontime address saved."; } },
      { key: "smaart", name: "Smaart", badge: "smaart-badge.png", host: s.host, port: s.port, dflt: 26000, added: !!(s.host || s.enabled), password: { set: !!sst.password_set },
        save: async (v) => {
          // The values chosen on the Sound level card are sent back as saved; the server keeps the rest.
          const keep = s.meters ? { meters: s.meters.map((m) => ({ source: m.source || "", metric: m.metric })) } : { slots: s.slots || [] };
          await api("PUT", "/api/admin/spl", Object.assign({ enabled: !!s.enabled || v.adding, host: v.host, port: v.port, password: v.password, clear_password: v.clear }, keep));
          return "Smaart address saved.";
        } },
      { key: "globcon", name: "DirectOut GLOBCON", badge: "directout-badge.png", host: g.host, port: g.port || 9091, dflt: 9091, added: !!(g.host || gst.running), password: { set: !!gst.password_set },
        save: async (v) => { await api("PUT", "/api/admin/globcon", { host: v.host, port: v.port || 9091, password: v.password, clear_password: v.clear }); return "GLOBCON address saved."; } },
    ].map((p) => Object.assign(p, { emulated: admin.emulate && p.key !== "ontime", added: p.added || admin.emulate })).concat([
      // One entry per device: always addable; each has its own add form, and is edited on its own card.
      { key: "shelly", name: "Shelly power meter", badge: "shelly-badge.svg", device: true, form: (prefill, done) => shellyAddForm(done) },
      { key: "esphome", name: "ESPHome node", badge: "stagewatch-badge.png", device: true, form: (prefill, done) => adoptForm(prefill || {}, done) },
    ]);
  }
  const connLogo = (p) => h("img", { class: "card-logo", src: `/static/${p.badge}`, alt: "", width: "26", height: "26", onerror: (e) => { e.target.hidden = true; } });

  // The pop-out. `pick` is the product to edit, or null for Add (then a product list comes first).
  function connDialog(pick, prefill) {
    const all = connProducts();
    const choices = pick ? [pick] : all.filter((p) => p.device || (!p.added && !p.emulated));
    const dlg = h("dialog", { class: "sw-dialog conn-dialog", "aria-labelledby": "conn-dlg-title" });
    const close = () => { dlg.close(); dlg.remove(); };
    const status = h("p", { class: "muted conn-status", role: "status" });
    const body = h("div", {});
    const product = h("select", { class: "touch", "aria-label": "Product", onchange: () => fill() }, choices.map((p) => h("option", { value: p.key }, p.name)));
    let read = null, current = null;
    function fill() {
      current = choices.find((p) => p.key === product.value) || choices[0];
      status.textContent = "";
      if (current.form) {   // a device: its own form, with its own Test and Save
        body.replaceChildren(current.form(prefill, close));
        testBtn.hidden = true; saveBtn.hidden = true;
        return;
      }
      saveBtn.hidden = false;
      const host = h("input", { class: "touch", id: current.key === "ontime" ? "ontime-address" : null, maxlength: "253", autocomplete: "off", spellcheck: "false", value: current.host || "", placeholder: "192.168.1.20 or name.local" });
      const port = h("input", { class: "num touch", type: "number", min: "1", max: "65535", value: current.port || "", placeholder: String(current.dflt) });
      const pw = current.password ? h("input", { class: "touch", type: "password", maxlength: "128", autocomplete: "new-password", placeholder: current.password.set ? "Saved" : "None" }) : null;
      const clearPw = current.password && current.password.set ? h("input", { type: "checkbox" }) : null;
      read = () => ({ host: val(host), port: port.value ? Number(port.value) : null, password: pw ? pw.value : "", clear: clearPw ? clearPw.checked : false, adding: !pick });
      body.replaceChildren(
        field("Address (IP or host name)", host),
        field(`Port (usually ${current.dflt})`, port),
        pw ? field("Password (empty keeps the saved one)", pw) : h("p", { class: "muted" }, `${current.name} has no password.`),
        clearPw ? h("label", { class: "check" }, clearPw, " Remove the saved password") : "");
      testBtn.hidden = !current.test;
      (pick ? host : product).focus();
    }
    const act = (btn, fn, busy) => async () => {
      btn.disabled = true; status.textContent = busy;
      try { status.textContent = await fn(read()); return true; } catch (err) { status.textContent = err.message; return false; }
      finally { btn.disabled = false; }
    };
    const testBtn = h("button", { type: "button", class: "touch", id: "ontime-test" }, "Test");
    testBtn.onclick = () => act(testBtn, (v) => current.test(v), "Testing…")();
    const saveBtn = h("button", { type: "button", class: "primary touch" }, "Save");
    saveBtn.onclick = async () => { if (await act(saveBtn, (v) => current.save(v), "Saving…")()) { toast(status.textContent); close(); refresh(); } };
    dlg.append(...[
      h("h3", { id: "conn-dlg-title" }, pick ? h("span", {}, connLogo(pick), pick.device ? ` Add ${pick.name}` : ` Edit ${pick.name}`) : "Add integration"),
      choices.length ? null : h("p", {}, "Every product Stagewatch can connect to is already added. Use Edit on its row to change it."),
      !pick && choices.length ? field("Product", product) : null,
      body,
      status,
      h("div", { class: "row sw-dlg-actions" }, h("span", { class: "grow" }), h("button", { type: "button", class: "touch", onclick: close }, choices.length ? "Cancel" : "Close"),
        choices.length ? testBtn : null, choices.length ? saveBtn : null)].filter(Boolean));   // append() would print a null
    dlg.addEventListener("cancel", () => dlg.remove());   // Escape
    document.body.append(dlg);
    dlg.showModal();
    if (choices.length) fill();
  }

  function connectionsCard() {
    const list = connProducts();
    const added = list.filter((p) => !p.device && (p.added || p.emulated));
    const rows = added.map((p) => h("tr", {},
      h("td", {}, h("span", { class: "conn-name" }, connLogo(p), h("strong", {}, p.name))),
      h("td", {}, p.emulated ? h("span", { class: "muted" }, "Simulated (emulate mode)") : `${p.host || "not set"}${p.port ? `:${p.port}` : ""}`),
      h("td", { class: "muted" }, p.password ? (p.password.set ? "Password saved" : "No password") : "No password needed"),
      h("td", { class: "conn-edit" }, p.emulated ? null : h("button", { class: "touch", "aria-label": `Edit ${p.name}`, onclick: () => connDialog(p) }, "Edit"))));
    const canAdd = true;   // a Shelly meter or an ESPHome node can always be added
    return card("Connections",
      h("p", { class: "muted" }, "Where each outside product is on the show network: its address, port and password. Add integration also adds a Shelly power meter or an ESPHome node; those are listed and changed on their own cards. Each product's other settings are on its own card."),
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Product", "Address and port", "Password", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, rows))),
      h("div", { class: "row conn-actions" }, h("span", { class: "grow" }),
        h("button", { class: "primary touch", disabled: !canAdd, title: canAdd ? "" : "Every product is already added", onclick: () => connDialog(null) }, "Add integration")));
  }

  function ontimeCard() {
    const w = admin.config.wall_clock, t = admin.config.ontime_timer || { show_title: true };
    const warn = h("input", { id: "ontime-warn", class: "num", type: "number", step: "0.5", min: "1", max: "60", value: w.warn_offset_s });
    const title = h("input", { id: "ontime-show-title", type: "checkbox", checked: t.show_title !== false });
    // One Save: the clock warning limit goes to the Wall Clock settings, the title switch to the Ontime
    // Timer settings. The address (Connections), the clock's own source and its look are kept as saved.
    const save = () => run(async () => {
      await api("PUT", "/api/admin/wall-clock", {
        source: w.source, ontime_url: w.ontime_url, warn_offset_s: Number(warn.value) || 2, display: w.display,
      });
      return api("PUT", "/api/admin/ontime-timer", { show_title: title.checked });
    }, "Ontime saved").then(refresh, () => {});
    const heading = (text) => h("h3", { class: "ontime-sub" }, text);
    return SW.brandCard("Ontime", "ontime-badge.png", "Ontime",
      h("p", { class: "muted" }, "Settings for the three Ontime cards: Wall Clock (when it uses Ontime), Ontime Timer and Ontime Rundown. Stagewatch only listens to Ontime: it never starts, pauses or changes anything in it. Ontime is only contacted while a dashboard has one of these cards."),
      heading("Connection"),
      h("p", { class: "muted" }, `One address serves all three cards: ${w.ontime_url}. Change it under Connections.`),
      h("div", { id: "ontime-status", class: "muted" }, ontimeStatusLines(admin).map((l) => h("p", {}, l))),
      heading("Wall Clock"),
      h("div", { class: "row" }, field("Warn if more than this many seconds out", warn)),
      h("p", { class: "muted hint" }, "Only used when the Wall Clock takes its time from Ontime (choose that in the Wall Clock card). The clock then warns if Ontime differs from the Stagewatch computer by more than this."),
      heading("Ontime Timer"),
      h("div", { class: "row" }, field("Show the event title on dashboards (also the Ontime Rundown card's titles and notes)", title)),
      h("p", { class: "muted hint" }, "The title is the event's name in Ontime, often an artist. Dashboards are not password protected, so untick this if the name should stay off the screens. The Ontime Timer is Ontime's timer on a screen, not a Stagewatch timer, so don't use it as a cue."),
      heading("Ontime Rundown"),
      h("p", { class: "muted hint" }, "No settings of its own. It uses the address above and the event title switch under Ontime Timer, so one tick hides every title and note."),
      h("div", { class: "row", style: "margin-top:10px" }, h("button", { id: "ontime-save", class: "primary", onclick: save }, "Save")));
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
    // "09:00" when the day started on its own date, else the full date too ("Thu 15 Oct 2026, 23:40").
    const startedText = (s) => {
      const full = SW.fmtTime(s.started, { date: true });
      return full.startsWith(`${SW.fmtDay(s.day)}, `) ? SW.fmtTime(s.started) : full;
    };
    const prevCount = admin.events.reduce((n, e) => n + e.shows.filter((s) => s.id !== show.id).length, 0);
    const previous = h("details", {}, h("summary", { class: "muted" }, `Previous shows (${prevCount})`),
      admin.events.filter((e) => e.shows.some((s) => s.id !== show.id)).map((e) => h("div", { class: "event-group" },
        h("strong", {}, e.name), e.id === ev.id ? h("span", { class: "muted" }, " (this event)") : null,
        h("ul", {}, e.shows.filter((s) => s.id !== show.id).map((s) => h("li", {},
          `${s.name} · ${SW.fmtDay(s.day)}`,
          h("span", { class: "muted" }, ` (started ${startedText(s)})`)))))));

    return card("Event & show",
      h("p", { class: "event-now" }, "Event: ", h("strong", {}, ev.name)),
      h("p", { class: "event-now" }, "Day: ", h("strong", {}, show.name), ` · ${SW.fmtDay(show.day)}`,
        h("span", { class: "muted" }, ` (started ${startedText(show)})`)),
      h("p", { class: "muted hint" }, "Markers, alarms and history belong to the current day. Dashboards show the event and day under their title."),
      h("div", { class: "row" },
        h("button", { class: "primary touch", type: "button", "data-show-start": "", disabled: showBusy, onclick: () => { showPanel = showPanel === "next" ? "" : "next"; render(); } }, "Next day (same event)"),
        h("button", { class: "touch", type: "button", "data-show-start": "", disabled: showBusy, onclick: () => { showPanel = showPanel === "event" ? "" : "event"; render(); } }, "New event…")),
      panel,
      brandingPanel(ev),
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
  // Event documents (plans/policy-documents.md): safety and risk plans per event, admin-only until
  // ticked for a dashboard. Loads on its own; the add form keeps its fields because typing in it
  // marks the page as being edited, so the poll never rebuilds it.
  const DOC_CATS = [["lightning", "Lightning"], ["wind", "Wind"], ["heat", "Heat"], ["evacuation", "Evacuation"],
    ["electrical", "Electrical"], ["site_rules", "Site rules"], ["medical", "Medical"], ["noise", "Noise"], ["other", "Other"]];
  const DOC_PDF_MAX = 10 * 1024 * 1024, DOC_TEXT_MAX = 256 * 1024;
  let docEvent = null;
  function documentsCard() {
    const events = admin.events || [];
    if (docEvent === null || !events.some((e) => e.id === docEvent)) docEvent = admin.show ? admin.show.event_id : (events[0] && events[0].id);
    const box = h("div", {}, h("p", { class: "muted" }, "Loading…"));
    const dashes = admin.config.dashboards || [];
    const catName = (c) => (DOC_CATS.find((x) => x[0] === c) || [c, "Other"])[1];
    const kindOf = (f) => (/\.pdf$/i.test(f.name) ? "pdf" : /\.(md|markdown)$/i.test(f.name) ? "md" : /\.txt$/i.test(f.name) ? "txt" : "");
    const base = () => `/api/admin/events/${docEvent}/documents`;
    const sendFile = async (id, f) => {
      const kind = kindOf(f);
      if (!kind) throw new Error("Use a PDF, a Markdown (.md) file or a plain text (.txt) file. Save Word files as PDF first.");
      if (f.size > (kind === "pdf" ? DOC_PDF_MAX : DOC_TEXT_MAX)) throw new Error(kind === "pdf" ? "PDF files can be up to 10 MB. Save a smaller copy and retry." : "Text files can be up to 256 KB. Split it into smaller files and retry.");
      const res = await fetch(`${base()}/${id}/file?kind=${kind}`, { method: "PUT", credentials: "same-origin",
        headers: { "Content-Type": "application/octet-stream" }, body: f });
      let data = null; try { data = await res.json(); } catch (_) { /* empty */ }
      if (!res.ok) throw new Error((data && typeof data.detail === "string") ? data.detail : "Upload failed");
      return data;
    };
    const warning = () => h("p", { class: "warn-text doc-warn" }, "▲ Dashboards have no login: anyone on the show network can read this file once it is shared. Remove names and phone numbers, or keep it admin-only.");
    const ticks = (chosen, onChange) => h("div", { class: "row" }, dashes.length ? dashes.map((d) => h("label", { class: "check" },
      h("input", { type: "checkbox", checked: chosen.indexOf(d.slug) >= 0, onchange: (e) => {
        const next = chosen.filter((s) => s !== d.slug).concat(e.target.checked ? [d.slug] : []);
        onChange(next);
      } }), ` ${d.title || d.slug}`)) : h("span", { class: "muted" }, "No dashboards yet."));
    const fill = (r) => {
      const reload = (p) => p.then(fill, () => {});
      const rows = r.documents.map((d) => {
        const shared = d.dashboards.map((s) => (dashes.find((x) => x.slug === s) || {}).title || s);
        const input = h("input", { type: "file", accept: ".pdf,.md,.txt,application/pdf,text/plain,text/markdown", onchange: () => {
          const f = input.files && input.files[0]; input.value = "";
          if (f) reload(run(() => sendFile(d.id, f), "Document replaced"));
        } });
        let panel = null;
        const edit = h("button", { class: "touch", type: "button", onclick: () => {
          if (panel) { panel.remove(); panel = null; return; }
          let chosen = d.dashboards.slice();
          const warnBox = h("div", {}, chosen.length ? warning() : null);
          panel = h("div", { class: "card-inset" }, h("strong", {}, "Shown on"),
            ticks(chosen, (next) => { chosen = next; warnBox.replaceChildren(chosen.length ? warning() : ""); }), warnBox,
            h("div", { class: "row" }, h("button", { class: "primary touch", type: "button", onclick: () =>
              reload(run(() => api("PATCH", `${base()}/${d.id}`, { dashboards: chosen }), "Saved")) }, "Save")));
          tr.after(h("tr", {}, h("td", { colspan: 5 }, panel)));
        } }, "Share…");
        const open = d.kind ? h("a", { href: `${base()}/${d.id}/file`, target: "_blank", rel: "noopener noreferrer" }, d.title) : h("span", {}, d.title);
        const tr = h("tr", {},
          h("td", {}, open, d.kind ? null : h("span", { class: "warn-text" }, " (no file yet)")),
          h("td", {}, catName(d.category)),
          h("td", {}, d.kind ? `${d.kind.toUpperCase()}, ${Math.max(1, Math.ceil(d.bytes / 1024))} KB` : "—"),
          h("td", {}, shared.length ? shared.join(", ") : h("span", { class: "muted" }, "Admin only")),
          h("td", {}, h("div", { class: "row" }, edit,
            h("label", { class: "button touch" }, d.kind ? "Replace…" : "Choose file…", input),
            h("button", { class: "touch danger", type: "button", onclick: () => {
              if (confirm(`Delete "${d.title}"? This cannot be undone.`)) reload(run(() => api("DELETE", `${base()}/${d.id}`), "Document deleted"));
            } }, "Delete"))));
        return tr;
      });
      // Add form
      const title = h("input", { maxlength: 100, placeholder: "For example: Lightning action plan" });
      const cat = h("select", {}, DOC_CATS.map(([v, t]) => h("option", { value: v }, t)));
      const file = h("input", { type: "file", accept: ".pdf,.md,.txt,application/pdf,text/plain,text/markdown" });
      let chosen = [];
      const warnBox = h("div");
      const add = async () => {
        const f = file.files && file.files[0];
        if (!val(title)) { toast("Give the document a title.", true); return; }
        if (!f) { toast("Choose a file.", true); return; }
        if (!kindOf(f)) { toast("Use a PDF, a Markdown (.md) file or a plain text (.txt) file. Save Word files as PDF first.", true); return; }
        const made = await run(() => api("POST", base(), { title: val(title), category: cat.value, dashboards: chosen }));
        try { fill(await run(() => sendFile(made.id, f), "Document added")); }
        catch (_) { fill(await api("GET", base())); }   // the entry stays, marked "no file yet"
        dirty = false;
      };
      const full = r.documents.length >= r.max_docs;
      // replaceChildren prints a null as "null": drop the parts that are not shown.
      box.replaceChildren(...[
        h("p", { class: "muted hint" }, "Safety and risk plans for this event: PDF, Markdown (.md) or plain text (.txt). PDF up to 10 MB, text up to 256 KB, 20 files per event. Each one is admin-only until you tick a dashboard. Dashboards have no login. Upload only files you trust."),
        events.length > 1 ? h("div", { class: "row" }, field("Event", h("select", { onchange: (e) => { docEvent = Number(e.target.value); load(); } },
          events.map((e) => h("option", { value: e.id, selected: e.id === docEvent }, e.name))))) : null,
        rows.length ? h("div", { class: "table-scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, ["Title", "Category", "File", "Shown on", ""].map((x) => h("th", {}, x)))), h("tbody", {}, rows)))
          : h("p", { class: "muted" }, "No documents for this event yet."),
        full ? h("p", { class: "muted" }, `This event has ${r.max_docs} documents, the most it can hold. Delete one to add another.`)
          : h("details", {}, h("summary", { class: "muted" }, "Add a document"),
            h("div", { class: "row" }, field("Title", title), field("Category", cat), field("File", file)),
            h("strong", {}, "Shown on (none ticked = admin only)"),
            ticks(chosen, (next) => { chosen = next; warnBox.replaceChildren(chosen.length ? warning() : ""); }), warnBox,
            h("div", { class: "row" }, h("button", { class: "primary touch", type: "button", onclick: add }, "Add document"))),
        admin.emulate || (snap && snap.emulate) ? h("div", { class: "row" }, h("button", { class: "touch", type: "button", onclick: () =>
          reload(run(() => api("POST", "/api/admin/documents/demo"), "Demo documents loaded")) }, "Load demo documents")) : null].filter(Boolean));
    };
    const load = () => api("GET", base()).then(fill, (err) => box.replaceChildren(h("p", { class: "muted" }, err.message)));
    if (docEvent) load(); else box.replaceChildren(h("p", { class: "muted" }, "No event yet."));
    return card("Documents", box);
  }

  // Onsite contacts (plans/onsite-contacts.md): key people per event. Personal data: admin-only
  // except job title, name, radio and area on dashboards with the Contacts card, and phone numbers
  // only where all three switches allow them. Nothing is kept in localStorage.
  let contactEvent = null;
  function contactsCard() {
    const events = admin.events || [];
    if (contactEvent === null || !events.some((e) => e.id === contactEvent)) contactEvent = admin.show ? admin.show.event_id : (events[0] && events[0].id);
    const box = h("div", {}, h("p", { class: "muted" }, "Loading…"));
    const base = (id) => `/api/admin/events/${id || contactEvent}/contacts`;
    const fill = (r) => {
      contactEvent = r.event_id;   // the list shown is always the one Save writes to
      const reload = (p) => p.then(fill, () => {});
      let rows = r.contacts.map((c) => Object.assign({}, c));
      const list = h("datalist", { id: "contact-roles" }, r.roles.map((t) => h("option", { value: t })));
      const tbody = h("tbody");
      const draw = () => {
        tbody.replaceChildren(...rows.map((c, i) => {
          const inp = (key, attrs) => h("input", Object.assign({ class: "touch", value: c[key] || "", oninput: (e) => { c[key] = e.target.value; dirty = true; } }, attrs));
          const tick = (key, label) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: !!c[key], onchange: (e) => { c[key] = e.target.checked; dirty = true; } }), ` ${label}`);
          const move = (dir) => { const j = i + dir; if (j < 0 || j >= rows.length) return; const t = rows[i]; rows[i] = rows[j]; rows[j] = t; dirty = true; draw(); };
          return h("tr", {},
            h("td", {}, inp("role", { list: "contact-roles", maxlength: 60, placeholder: "Job title", "aria-label": "Job title" })),
            h("td", {}, inp("name", { maxlength: 80, placeholder: "Name (or TBC)", "aria-label": "Name" })),
            h("td", {}, inp("phone", { maxlength: 24, type: "tel", placeholder: "Work phone", "aria-label": "Phone", autocomplete: "off" }), tick("show_phone", "Show")),
            h("td", {}, inp("radio", { maxlength: 32, placeholder: "e.g. Ch 2", "aria-label": "Radio" })),
            h("td", {}, inp("area", { maxlength: 40, list: "stage-list", placeholder: "All areas", "aria-label": "Area" })),
            h("td", {}, tick("on_duty", "On duty")),
            h("td", {}, h("div", { class: "row" },
              h("button", { type: "button", class: "touch", "aria-label": "Move up", disabled: i === 0, onclick: () => move(-1) }, "▲"),
              h("button", { type: "button", class: "touch", "aria-label": "Move down", disabled: i === rows.length - 1, onclick: () => move(1) }, "▼"),
              h("button", { type: "button", class: "touch danger", onclick: () => { rows.splice(i, 1); dirty = true; draw(); } }, "Remove"))));
        }));
      };
      draw();
      const save = () => reload(run(() => api("PUT", base(), { contacts: rows.map((c) => ({ id: c.id || "", role: c.role || "", name: c.name || "", phone: c.phone || "", show_phone: !!c.show_phone, radio: c.radio || "", area: c.area || "", on_duty: c.on_duty !== false })) }), "Contacts saved").then((x) => { dirty = false; return x; }));
      const clearAll = (id, name) => {
        if (confirm(`Delete all contacts for ${name}? This cannot be undone.`)) reload(run(() => api("DELETE", base(id)), "Contacts cleared"));
      };
      // Old events still holding personal data (storage limitation): ask until cleared or kept.
      const reminders = r.reminders.map((x) => h("div", { class: "card-inset warn-text" },
        h("p", {}, `▲ Contacts from ${x.name} (${x.count}) are still stored. That event has ended. Clear them?`),
        h("div", { class: "row" },
          h("button", { type: "button", class: "touch danger", onclick: () => clearAll(x.event_id, x.name) }, "Clear them"),
          h("button", { type: "button", class: "touch", onclick: () => reload(run(() => api("POST", `${base(x.event_id)}/dismiss-reminder`), "Kept. You will not be asked again for this event.")) }, "Keep them"))));
      const days = r.edited ? Math.floor((Date.now() / 1000 - r.edited) / 86400) : 0;
      // Settings: the installation-wide switch and the optional PIN that dashboards ask for.
      const phonesOn = h("input", { type: "checkbox", checked: r.phones_enabled });
      const pin = h("input", { class: "touch", type: "password", inputmode: "numeric", maxlength: 12, placeholder: r.reveal_pin_set ? "PIN set (type to change)" : "6 to 12 digits", autocomplete: "new-password" });
      const saveSettings = (clear) => {
        const body = { phones_enabled: phonesOn.checked };
        if (clear) body.clear_reveal_pin = true; else if (val(pin)) body.reveal_pin = val(pin);
        reload(run(() => api("PUT", "/api/admin/contacts/settings", body), "Saved").then(() => api("GET", base())));
      };
      const others = events.filter((e) => e.id !== contactEvent);
      const copyFrom = h("select", { class: "touch" }, others.map((e) => h("option", { value: e.id }, e.name)));
      const ename = (events.find((e) => e.id === contactEvent) || {}).name || "this event";
      box.replaceChildren(...[list, ...reminders,
        h("p", { class: "muted hint" }, "Key people for this event: job title, name, radio and area show on dashboards with the Onsite contacts card. Phone numbers show only when allowed here, on the dashboard (Edit cards) and for the contact. Dashboards have no login. Tell crew their details are on the screens, prefer work or show phones, and clear the list when the event is over."),
        events.length > 1 ? h("div", { class: "row" }, field("Event", h("select", { onchange: (e) => { contactEvent = Number(e.target.value); load(); } },
          events.map((e) => h("option", { value: e.id, selected: e.id === contactEvent }, e.name))))) : null,
        r.stale ? h("p", { class: "warn-text" }, `▲ These contacts were last edited ${days} days ago. Clear them if the event is over.`) : null,
        h("div", { class: "table-scroll" }, h("table", {},
          h("thead", {}, h("tr", {}, ["Job title", "Name", "Phone", "Radio", "Area", "", ""].map((x) => h("th", {}, x)))), tbody)),
        rows.length ? null : h("p", { class: "muted" }, "No contacts for this event yet."),
        h("div", { class: "row" },
          h("button", { type: "button", class: "touch", disabled: rows.length >= r.max_contacts, onclick: () => { if (rows.length < r.max_contacts) { rows.push({ role: "", name: "", phone: "", show_phone: false, radio: "", area: "", on_duty: true }); dirty = true; draw(); } } }, "Add contact"),
          h("button", { type: "button", class: "primary touch", onclick: save }, "Save contacts"),
          r.contacts.length || r.has_files ? h("button", { type: "button", class: "touch danger", onclick: () => clearAll(contactEvent, ename) }, "Clear all…") : null),
        !r.contacts.length && others.length ? h("div", { class: "row" }, field("Copy contacts from", copyFrom),
          h("button", { type: "button", class: "touch", onclick: () => reload(run(() => api("POST", `${base()}/copy`, { from_event_id: Number(copyFrom.value) }), "Contacts copied")) }, "Copy")) : null,
        h("details", {}, h("summary", { class: "muted" }, "Phone numbers on dashboards"),
          h("label", { class: "check" }, phonesOn, " Allow phone numbers on dashboards"),
          h("div", { class: "row" }, field("Dashboard PIN for phone numbers (optional)", pin)),
          h("p", { class: "muted hint" }, "With a PIN, dashboards show a Show numbers button and ask for it; the numbers hide again after 2 minutes. Not the admin PIN. Without a PIN, allowed numbers show straight away."),
          h("div", { class: "row" }, h("button", { type: "button", class: "primary touch", onclick: () => saveSettings(false) }, "Save"),
            r.reveal_pin_set ? h("button", { type: "button", class: "touch", onclick: () => saveSettings(true) }, "Remove PIN") : null)),
        admin.emulate || (snap && snap.emulate) ? h("div", { class: "row" }, h("button", { class: "touch", type: "button", onclick: () =>
          reload(run(() => api("POST", "/api/admin/contacts/demo"), "Demo contacts loaded")) }, "Load demo contacts")) : null].filter(Boolean));
    };
    const load = () => api("GET", base()).then(fill, (err) => box.replaceChildren(h("p", { class: "muted" }, err.message)));
    if (contactEvent) load(); else box.replaceChildren(h("p", { class: "muted" }, "No event yet."));
    return card("Onsite contacts", box);
  }

  function scheduleCard() {
    const c = card("Schedule", scheduleBody());
    c.id = "schedule-admin";
    return foldCard(c, "schedule", true, !schedData);
  }
  // The summary under the heading; redrawn on its own so the fold keeps its focus and state.
  function scheduleBody() {
    const c = h("div", { id: "schedule-admin-body" });
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
    if (admin.actions) lines.push(h("p", {}, h("strong", {}, admin.actions.armed ? "● Actions ARMED" : "○ Actions off")));
    c.append(...lines, h("div", { class: "row", style: "margin-top:10px" }, SW.linkButton("Open schedule editor", "/schedule", true)));
    return c;
  }
  function rerenderScheduleSummary() {
    const old = document.getElementById("schedule-admin-body");
    if (old) old.replaceWith(scheduleBody());
  }

  // How an integration gets its data, in plain words. Unset or unknown shows nothing. "Uses the
  // internet" is a warning, with words as well as the amber colour.
  const IOT_WORDS = { local_push: "Local, live push", local_poll: "Local, checks every few seconds", cloud: "Uses the internet" };
  function iotTag(iotClass) {
    if (!Object.prototype.hasOwnProperty.call(IOT_WORDS, iotClass)) return null;
    return h("div", { class: "iot-tag " + (iotClass === "cloud" ? "warn-text" : "muted") }, (iotClass === "cloud" ? "▲ " : "") + IOT_WORDS[iotClass]);
  }

  function catalogCard() {
    return card("Integrations",
      h("div", { class: "table-scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Integration", "Tier", "Direction", "Protocols", "Runtime"].map((x) => h("th", {}, x)))),
        h("tbody", {}, admin.integrations.map((i) => h("tr", {},
          h("td", { class: "catalog-name" }, h("strong", {}, i.manifest.name), h("div", { class: "muted", style: "font-size:12px" }, i.manifest.description), iotTag(i.manifest.iot_class)),
          h("td", {}, i.manifest.tier), h("td", {}, i.manifest.direction), h("td", {}, i.manifest.protocols.join(", ")),
          h("td", { class: "muted" }, Object.entries(i).filter(([k]) => k !== "manifest").map(([k, v]) => `${k}: ${v !== null && typeof v === "object" ? JSON.stringify(v) : v}`).join(" · "))))))));
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
    // Closed once set up; held open while an update is available or running.
    const last = sw && sw.last_check;
    const hold = !!(sw && (sw.update_available || sw.restarting || (sw.job && sw.job.running) || (last && last.ok && last.available)));
    return foldCard(softwareCardBody(), "software", false, hold);
  }
  function softwareCardBody() {
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
          sw.channel === "nightly" ? h("p", { class: "warn-text" }, "Nightly is the newest code, checked only by machines. Try it away from a live show first.") : null,
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
    // The whole card folds away under its heading. Open unless the person has closed it; the
    // remembered choice uses the same store as the folds inside (key "_card" cannot be a slug).
    const head = c.firstChild, rest = Array.from(c.childNodes).slice(1);
    const whole = h("details", { class: "card-fold", id: "connect-card-fold" }, h("summary", {}, head));
    whole.append(...rest);
    c.append(whole);
    connectFolds.bind(whole, "_card", true);
    pageFolds.push({ d: whole, store: connectFolds, key: "_card", force: false });
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
  // Project guides on GitHub. Each path is checked against the repo by tests/test_static_safety.py.
  const HELP_REPO = "https://github.com/MrFreekie/stagewatch/blob/main/";
  const HELP_LINKS = [
    { group: "Getting started", path: "README.md", title: "Stagewatch overview", about: "What Stagewatch is and how to start it" },
    { group: "Getting started", path: "docs/install/windows.md", title: "Install on Windows", about: "Set up Stagewatch on a Windows laptop or PC" },
    { group: "Getting started", path: "docs/install/raspberry-pi.md", title: "Install on a Raspberry Pi", about: "Set up Stagewatch on a Pi for a rack or wall display" },
    { group: "Getting started", path: "docs/install/debian-linux.md", title: "Install on Debian Linux", about: "Set up Stagewatch on a Debian PC" },
    { group: "Getting started", path: "docs/networking.md", title: "Networking", about: "One network, or the sensor nodes on their own Wi-Fi" },
    { group: "Day to day", path: "docs/cards/README.md", title: "Dashboard cards", about: "What each card does" },
    { group: "Day to day", path: "docs/admin/README.md", title: "Admin guide", about: "What each Admin card does" },
    { group: "Day to day", path: "docs/troubleshooting.md", title: "Troubleshooting", about: "What to try when something is wrong" },
    { group: "Day to day", path: "docs/updating-and-backups.md", title: "Updating and backups", about: "Update Stagewatch safely and keep your show data" },
    { group: "Hardware and nodes", path: "docs/nodes/README.md", title: "Build a sensor node", about: "Choose a board, build it and connect it" },
    { group: "Hardware and nodes", path: "docs/nodes/pressure-nodes.md", title: "Pressure nodes", about: "Barometer and environment node builds" },
    { group: "Hardware and nodes", path: "docs/nodes/feather-s3-tft-ms8607.md", title: "Feather S3 display node", about: "A node with its own small screen" },
    { group: "Hardware and nodes", path: "docs/integrations/README.md", title: "Integrations", about: "Ontime, Smaart, GLOBCON, Shelly and OSC output" },
    { group: "Reference", path: "docs/README.md", title: "All guides", about: "The full list of guides" },
    { group: "Reference", path: "docs/glossary.md", title: "Glossary", about: "Plain-English meanings of the terms used" },
    { group: "Reference", path: "CHANGELOG.md", title: "What has changed", about: "Every release and what it changed" },
  ];

  function helpLinks() {
    const groups = [];
    HELP_LINKS.forEach((l) => { if (groups.indexOf(l.group) < 0) groups.push(l.group); });
    return h("div", { class: "help-links" },
      h("h3", {}, "Guides"),
      h("p", { class: "muted" }, "These links need an internet connection and open GitHub in a new tab. Stagewatch itself works offline."),
      groups.map((g) => h("div", { class: "help-group" },
        h("h4", {}, g),
        h("ul", {}, HELP_LINKS.filter((l) => l.group === g).map((l) => h("li", {},
          h("a", { class: "help-link", href: HELP_REPO + l.path, target: "_blank", rel: "noopener noreferrer" },
            h("strong", {}, l.title), " ", h("span", { class: "muted" }, l.about))))))));
  }

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
      h("div", { class: "row" }, btn), status, helpLinks());
  }

  // ------------------------------------------------------------ card status chips
  // The Online / Offline / Error chip (SW.sourceStatusUi) in the header of each integration card
  // that is in use, just before the maker's badge. Built from data this page already has (admin
  // state and the snapshot). An input is null when the integration is off or not set up, and the
  // card then shows no chip. Where the admin state has no data time, a source the server reports
  // as working counts as fresh at the snapshot time.
  const devOf = (id) => snap.devices.find((d) => d.id === id);
  const entsOf = (id) => snap.entities.filter((e) => e.device_id === id);
  const CHIP_INPUT = {
    smaart() {
      if (!(admin.config.spl || {}).enabled) return null;
      const ents = SW.spl.entities(snap.entities);
      return SW.sourceInput.smaart(ents.length ? devOf(ents[0].device_id) : null, ents);
    },
    shelly() {
      const meters = (admin.shelly || {}).meters || [];
      return meters.length ? SW.sourceInput.shelly(meters.map((m) => ({ dev: devOf(m.id), ents: entsOf(m.id) }))) : null;
    },
    pihost() {
      return piHostInfo() ? Object.assign(SW.sourceInput.smaart(devOf("pi_host"), entsOf("pi_host")), { source: "pihost" }) : null;
    },
    globcon() {
      const st = admin.globcon || {};
      if (!st.running && !st.card_assigned) return null;
      const reported = !st.running ? "waiting" : st.status === "ok" ? "ok"
        : ["error", "locked", "waiting"].indexOf(st.status) >= 0 ? st.status : "offline";
      return { source: "globcon", reported, dataAt: reported === "ok" ? snap.now : null };
    },
    ontime() {
      const used = [admin.ontime_timer, admin.ontime_rundown, admin.config.wall_clock.source === "ontime" ? admin.wall_clock : null]
        .filter((x) => x && (x.active || x.card_assigned));
      if (!used.length) return null;
      const reported = used.some((x) => x.active && x.status === "error") ? "error"
        : used.every((x) => x.active && x.status === "ok") ? "ok" : used.some((x) => !x.active) ? "waiting" : "offline";
      const stamps = used.map((x) => x.last_message).filter((t) => typeof t === "number");
      return { source: "ontime", reported, dataAt: reported === "ok" ? (stamps.length ? Math.max(...stamps) : snap.now) : null };
    },
    esphome() {
      // The adopted nodes, as the ESPHome nodes card lists them.
      const nodes = snap.devices.filter((d) => d.id !== "site" && d.category !== "service" && d.category !== "meter" && d.category !== "host");
      if (!nodes.length) return null;
      const has = (...st) => nodes.some((d) => st.indexOf(d.status) >= 0);
      const reported = has("fault", "compromised") ? "error" : has("missing") ? "offline" : has("initializing") ? "waiting" : "ok";
      return { source: "esphome", reported, dataAt: reported === "ok" ? snap.now : null };
    },
  };
  let chips = [];
  function chip(c, key) {
    const input = CHIP_INPUT[key]();
    if (!input) return c;
    const ui = SW.sourceStatusUi(input.source);
    // In a folded card the header is a <summary>: a click on the chip opens its pop-out only.
    ui.el.addEventListener("click", (ev) => ev.preventDefault());
    const h2 = c.querySelector("h2");
    const logo = h2.querySelector(".card-logo");
    if (!logo && !h2.querySelector(".grow")) h2.append(h("span", { class: "grow" }));
    h2.insertBefore(ui.el, logo || null);
    ui.update(input, snap.now);
    chips.push({ key, ui });
    return c;
  }
  function updateChips() {
    chips.forEach((x) => { const input = CHIP_INPUT[x.key](); if (input) x.ui.update(input, snap.now); });
  }

  // The page in four groups, most used first. Each has a heading that the menu at the top jumps to.
  const GROUPS = [["show", "Show day"], ["sensors", "Sensors and nodes"], ["integrations", "Integrations"], ["system", "Setup and system"]];
  const groupHead = (id, title) => h("h2", { class: "admin-group", id: `group-${id}` }, title);
  const groupMenu = () => h("nav", { class: "row admin-groups", "aria-label": "Admin sections" },
    GROUPS.map(([id, title]) => h("a", { class: "touch", href: `#group-${id}` }, title)));

  function render() {
    entityIdsRendered = snap.entities.map((e) => e.id).join(",");
    dirty = false;   // everything on screen now matches saved state
    pageFolds = [];
    chips = [];
    // Core daily cards start open, set-up-once cards closed. A card with a notice is held open.
    // A maker's card shows once it is added under Connections (Add integration).
    const added = Object.fromEntries(connProducts().map((p) => [p.key, p.added]));
    const oscErr = ((admin.integrations.find((i) => i.manifest.domain === "osc_out") || {}).last_error);
    app.replaceChildren(...[
      groupMenu(),
      foldButtons(setAllCards, " cards"),
      groupHead(...GROUPS[0]),
      foldCard(eventShowCard(), "event", true),
      scheduleCard(),     // summary and a link to /schedule
      foldCard(alarmLogCard(), "alarmlog", true),
      foldCard(alarmNoticesCard(), "notices", true),
      foldCard(dashboardsCard(), "dashboards", true),   // full width: room for the "Edit cards" panel
      connectCard(),
      foldCard(documentsCard(), "documents", false),
      foldCard(contactsCard(), "contacts", false, admin.contact_reminders > 0),
      groupHead(...GROUPS[1]),
      chip(foldCard(devicesCard(), "nodes", true), "esphome"),
      foldCard(entitiesCard(), "sensors", true),
      foldCard(thresholdsCard(), "thresholds", true),
      foldCard(calibrationCard(), "calibration", false, hasHeldNode()),
      foldCard(deviceErrorsCard(), "deviceerrors", true),
      foldCard(logImportCard(), "logimport", false),
      groupHead(...GROUPS[2]),
      foldCard(connectionsCard(), "connections", true),
      added.ontime ? chip(foldCard(ontimeCard(), "ontime", false, ontimeHasError(admin) && "soft"), "ontime") : null,
      foldCard(wallClockCard(), "wallclock", false),
      added.smaart ? chip(foldCard(splCard(), "smaart", false), "smaart") : null,
      added.smaart ? foldCard(splLimitsCard(), "spllimits", false) : null,
      added.globcon ? chip(foldCard(globconCard(), "globcon", false), "globcon") : null,
      ((admin.shelly || {}).meters || []).length ? chip(foldCard(shellyCard(), "shelly", false), "shelly") : null,
      foldCard(barometerCard(), "barometer", false),
      piHostInfo() ? chip(foldCard(piHostCard(), "pihost", false), "pihost") : null,
      foldCard(actionTargetsCard(), "actiontargets", false, !!(admin.actions && admin.actions.armed)),
      foldCard(oscCard(), "osc", false, !!oscErr),
      foldCard(catalogCard(), "integrations", false),
      groupHead(...GROUPS[3]),
      foldCard(siteCard(), "site", false, !admin.config.site.timezone),
      foldCard(securityCard(), "security", false),
      softwareCard(),
      foldCard(supportCard(), "help", false)].filter(Boolean));
  }

  async function refresh() {
    [admin, snap] = await Promise.all([api("GET", "/api/admin/state"), api("GET", "/api/snapshot"), loadSoftware(),
      loadDeviceErrorCounts(),
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
      // Shares of the site average, live (the weighting note too).
      try {
        const live = await api("GET", "/api/admin/averages");
        if (admin.hardware) admin.hardware.averages = live.averages;
        for (const k of Object.keys(live.averages)) {
          for (const [id, sh] of Object.entries(live.averages[k].sensors)) {
            const el = document.querySelector(`[data-share="${CSS.escape(id)}"]`);
            if (el) el.textContent = sh.state === "in" ? SW.fmtShare(sh.share) + (sh.capped ? " (capped)" : "") : sh.state === "stale" ? "Stale" : sh.state === "outlier" ? "Left out" : "—";
          }
        }
        const nl = document.querySelector("[data-weight-notes]");
        if (nl) nl.replaceChildren(...weightNoteItems(live.averages, live.weight_by_accuracy));
      } catch (err) { if (err.status === 401) throw err; }
      // The Schedule summary: reload it when the schedule changed anywhere, and redraw it every
      // poll so NOW / NEXT follow the clock. It has no fields, so a redraw wipes nothing.
      const meta = s.schedule || {};
      if (!schedData || meta.show_id !== schedData.show_id || meta.revision !== schedData.revision) {
        try { await loadSchedule(); } catch (_) { /* keep the last summary */ }
      }
      rerenderScheduleSummary();
      // Ontime connection status, refreshed in place (text only, so nothing typed is lost).
      // The header chips follow the same live state.
      const ot = document.getElementById("ontime-status");
      if (ot || chips.length) {
        try {
          const live = await api("GET", "/api/admin/state");
          admin.wall_clock = live.wall_clock; admin.ontime_timer = live.ontime_timer; admin.ontime_rundown = live.ontime_rundown;
          admin.globcon = live.globcon;
          if (ot) ot.replaceChildren(...ontimeStatusLines(admin).map((l) => h("p", {}, l)));
        } catch (err) { if (err.status === 401) throw err; }
      }
      updateChips();
      // The Sound level drop-downs follow Smaart's lists (a renamed or new input) without a rebuild.
      if (pollN % 3 === 1 && admin.spl && admin.spl.running) await pullSplLists();
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
    SW.applyBranding(info, "show_on_admin");
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
