// Schedule editor (its own page, /schedule): the current show day's running order. It needs the
// admin login; without one the page sends you to Admin and back (Admin accepts only ?next=/schedule).
"use strict";

(() => {
  const { h, api } = SW;
  const { toast, card } = SW;
  const app = document.getElementById("app");
  let admin = null;   // /api/admin/state (show, limits, stages, site settings)
  let snap = null;    // /api/snapshot (for the poll)

  // Not logged in: Admin's login, then straight back here. The fixed path is the only one Admin accepts.
  const toLogin = () => { location.replace("/admin?next=/schedule"); };

  let sd = null;            // {showId, revision, day, rows, dirty, errors: {rowKey: {field: msg}}, cardErrors, conflict, changedElsewhere}
  let sdServer = null;      // the last GET /api/schedule
  let sdKey = 0;
  let sdBusy = false;
  let sdStatus = null;      // the "Unsaved changes" line, updated without a re-render
  let isEmulate = false;
  const imp = { text: "", format: "auto", preview: null, error: "", open: false, busy: false };
  const openSetlists = new Set();   // row keys whose setlist box is open
  // The kinds come from the server (admin.schedule_limits.kinds), so the list follows the backend.
  // Unknown values get a title-cased label ("some_kind" -> "Some Kind"); the fixed list is only the
  // fallback for a server that doesn't send one.
  const KIND_LABEL = { venue_access: "Venue Access", load_in: "Load In", crew_call: "Crew Call", soundcheck: "Soundcheck",
    doors: "Doors", act: "Act", changeover: "Changeover", curfew: "Curfew", load_out: "Load Out", other: "Other" };
  const kindLabel = (k) => KIND_LABEL[k] || String(k).split("_").filter(Boolean).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  const schedKinds = () => {
    const ks = admin && admin.schedule_limits && Array.isArray(admin.schedule_limits.kinds) && admin.schedule_limits.kinds.length
      ? admin.schedule_limits.kinds : Object.keys(KIND_LABEL);
    return ks.map((k) => [k, kindLabel(k)]);
  };
  const kb = (n) => `${SW.num(n / 1024, n % 1024 ? 1 : 0)} KB`;
  const utf8 = (s) => (typeof TextEncoder === "function" ? new TextEncoder().encode(s).length : s.length);
  // Timeline markers: each row's "Marker" box starts ticked for the kinds the server names
  // (soundcheck, doors, act). `markerSet` is true once the box differs from its kind's default or
  // has been clicked; until then a kind change moves the box with it, and Save sends null ("the
  // kind's default") rather than a fixed choice.
  const markerKinds = () => (admin && admin.schedule_limits && Array.isArray(admin.schedule_limits.marker_kinds)
    ? admin.schedule_limits.marker_kinds : SW.SCHEDULE_MARKER_KINDS);
  const markerDefault = (kind) => SW.scheduleMarkerDefault(kind, markerKinds());
  // Existing items keep `id` and `date` (the calendar date of their start, which the server needs
  // back unchanged so after-midnight items stay put). New rows have neither.
  const sdRow = (it) => {
    const kind = it.kind || "act";
    const def = markerDefault(kind);
    const has = typeof it.marker === "boolean";
    return { key: ++sdKey, id: it.id || null, date: it.date || "", kind: kind,
      title: it.title || "", start: it.start || "", end: it.end || "", stage: it.stage || "", setlist: it.setlist || "",
      marker: has ? it.marker : def, markerSet: has && it.marker !== def };
  };
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
    const old = document.getElementById("schedule-editor");
    if (old) old.replaceWith(scheduleCard());
  }
  const csvCell = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const rowsAsCsv = (rows) => ["start,end,title,kind,stage"].concat(rows.map((r) =>
    [r.start, r.end, r.title, r.kind, r.stage].map((v) => csvCell(String(v || "").trim())).join(","))).join("\n");

  // The server's messages are fixed crew text; pydantic's own ones are not, so use ours for those.
  function schedMsg(msg, field) {
    const lim = admin.schedule_limits || {};
    const m = String(msg || "");
    const ve = m.indexOf("Value error, ");   // may follow a "text: " style field prefix
    if (ve >= 0) return m.slice(ve + 13);
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
      const o = { kind: r.kind, title: r.title.trim(), start: r.start.trim(), end: r.end.trim(), stage: r.stage.trim(), setlist: r.setlist,
        marker: r.markerSet ? !!r.marker : null };
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
    // Marker: a tick box, at least 44 px to tap. An untouched box follows the kind.
    const mark = h("input", { type: "checkbox", checked: !!r.marker, "aria-label": `Add a timeline marker for item ${i + 1}`,
      onchange: (ev) => { r.marker = !!ev.target.checked; r.markerSet = true; sdTouch(); } });
    const kind = h("select", { class: "touch", "aria-label": "Kind", onchange: (ev) => {
      r.kind = ev.target.value;
      if (!r.markerSet) { r.marker = markerDefault(r.kind); mark.checked = r.marker; }
      sdTouch();
    } }, schedKinds().map(([v, l]) => h("option", { value: v }, l)));
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
        h("label", { class: "field sched-edit-marker", title: "Put a marker on the chart when this happens" }, "Marker",
          h("span", { class: "sched-edit-marker-box" }, mark)),
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

  // ---- Export and print: both use the list as it is on the page.
  function exportCsv() {
    const text = SW.scheduleCsv(sd.rows);
    const name = SW.scheduleFilename((admin.show || {}).event_name, sd.day || (admin.show || {}).day);
    const a = h("a", { href: URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" })), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    toast(`Saved ${name}`);
  }

  // A plain running order for paper (or "Save as PDF"): shown only when printing (schedule.html).
  function buildPrintView() {
    const show = admin.show || {};
    const day = sd.day || show.day || "";
    const rows = sd.rows.map((r) => {
      const when = r.start ? (r.end ? `${r.start}–${r.end}` : r.start) : "";
      const date = r.date && sd.day && r.date !== sd.day ? ` (${SW.fmtDay(r.date)})` : "";
      const main = h("tr", { class: "pv-item" },
        h("td", { class: "pv-time" }, when, date), h("td", {}, kindLabel(r.kind)), h("td", {}, r.stage || "All stages"), h("td", { class: "pv-title" }, r.title));
      const set = r.setlist.trim() ? h("tr", { class: "pv-set" }, h("td", { colspan: 4 }, h("div", { class: "pv-setlist" }, SW.renderMarkdown(r.setlist)))) : null;
      return [main, set];
    });
    const el = document.getElementById("print-view") || h("section", { id: "print-view" });
    el.replaceChildren(
      h("h1", {}, show.event_name || "Running order"),
      h("p", { class: "pv-day" }, [day ? SW.fmtDay(day) : "", show.name].filter((x, i, a) => x && a.indexOf(x) === i).join(" · ")),
      h("table", {},
        h("thead", {}, h("tr", {}, ["Time", "Kind", "Stage", "Title"].map((x) => h("th", {}, x)))),
        h("tbody", {}, rows.reduce((all, pair) => all.concat(pair), []))),
      h("p", { class: "pv-foot" }, `Printed ${SW.fmtTime(Date.now() / 1000, { date: true })}`));
    if (!el.parentNode) document.body.append(el);
  }
  function printSchedule() { buildPrintView(); window.print(); }

  function scheduleCard() {
    const el = (...body) => { const c = card("Running order", ...body); c.id = "schedule-editor"; return c; };
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
      const first = document.querySelector("#schedule-editor .sched-edit-row:last-child input");
      if (first) first.focus();
    };

    // Paste / import
    const ta = h("textarea", { class: "sched-import-text", rows: 8, "aria-label": "Running order to import",
      placeholder: "10:00 Load In\n14:00 Crew Call\n16:00-17:00 Soundcheck: Band\n19:00 Doors\n19:30-20:15 Support: Band name\n20:15-20:45 Changeover\n20:45-22:15 Headliner\n23:00 Curfew\n23:15 Load Out",
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
            h("td", {}, kindLabel(r.kind)), h("td", {}, r.title), h("td", {}, r.stage || "All")))))) : null,
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

    // Schedule-wide switch for the timeline markers (saved at once, separately from the list).
    const site = (admin.config && admin.config.site) || {};
    const autoBox = h("input", { type: "checkbox", checked: site.schedule_auto_markers !== false, onchange: async (ev) => {
      const on = !!ev.target.checked;
      ev.target.disabled = true;
      try {
        const r = await api("PUT", "/api/admin/schedule/settings", { auto_markers: on });
        if (admin.config && admin.config.site) admin.config.site.schedule_auto_markers = r.auto_markers;
        toast(r.auto_markers ? "Markers from the schedule: on" : "Markers from the schedule: off. Nothing new is added to the chart.");
      } catch (err) {
        ev.target.checked = !on;
        toast(`${err.message} Nothing has been changed.`, true);
      } finally { ev.target.disabled = false; }
    } });
    const autoMarkers = h("div", { class: "sched-auto-markers" },
      h("label", { class: "sched-auto-label" }, autoBox, h("span", {}, "Add markers from the schedule")),
      h("p", { class: "muted hint" }, "Puts a marker on the chart at each soundcheck, at doors, and when each act goes on and comes off stage (off stage only if the act has an end time or a curfew cuts it off). Untick Marker on a line to leave it out, or tick it on any other line. Markers come at the planned times."));

    const stages = admin.stages || [];
    return el(
      h("p", { class: "muted hint" }, `The running order for ${show.name || "this show day"}${sd.day ? `, ${SW.fmtDay(sd.day)}` : ""}. Times are 24-hour, in site time. Times before ${rollover} count as the next morning, so 23:00 to 00:30 works. Leave Stage empty for items that apply to every stage.`),
      h("p", { class: "muted hint" }, "Visible to anyone on the show network."),
      h("p", { class: "muted hint" }, `${sd.rows.length} of ${lim.max_items || 300} items. Titles up to ${lim.title_max || 120} characters. Setlists up to ${kb(lim.setlist_max_bytes || 8192)} each, ${kb(lim.setlist_total_max_bytes || 262144)} in total. Dashboards list items by start time: ▲ and ▼ only matter for items that start at the same time.`),
      h("datalist", { id: "sched-stage-list" }, stages.map((s) => h("option", { value: s }))),
      autoMarkers,
      notices,
      list,
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { type: "button", class: "touch", onclick: addItem }, "Add item"),
        h("button", { type: "button", class: "primary touch", disabled: sdBusy, onclick: () => saveSchedule() }, "Save schedule"),
        sd.dirty ? h("button", { type: "button", class: "touch", onclick: () => {
          if (confirm("Undo every change since the last save?")) { sdFromServer(sdServer); rerenderSchedule(); }
        } }, "Undo changes") : null,
        lim.demo_allowed ? h("button", { type: "button", class: "touch", onclick: loadDemo }, "Load demo day") : null,
        h("button", { type: "button", class: "touch", disabled: !sd.rows.length, onclick: exportCsv }, "Export CSV"),
        h("button", { type: "button", class: "touch", disabled: !sd.rows.length, onclick: printSchedule }, "Print"),
        sdStatus),
      h("p", { class: "muted hint" }, "Export CSV and Print use the list as it is on this page, including changes you have not saved. The CSV has no setlists; Print has them."),
      importBox);
  }

  // ------------------------------------------------------------ page
  const showKey = (x) => (x && x.show ? `${x.show.id}|${x.show.name}|${x.show.event_name}|${x.show.day}` : "");
  function renderHeader() {
    const show = admin.show || {};
    const parts = [show.event_name, show.day ? SW.fmtDay(show.day) : "", show.name].filter((x, i, a) => x && a.indexOf(x) === i);
    document.getElementById("day").textContent = parts.join(" · ");
  }
  function render() {
    renderHeader();
    app.replaceChildren(scheduleCard());
  }

  // Same rule as the old Admin card: a full redraw would wipe what is being typed, so the poll
  // leaves a focused field alone (what was typed lives in `sd` anyway).
  const isEditing = () => !!document.activeElement && document.activeElement.matches("input,select,textarea");

  async function poll() {
    if (!admin || !sd || isEditing()) return;
    try {
      const s = await api("GET", "/api/snapshot");
      if (isEditing()) return;   // focus moved into a field while the request was in flight
      SW.setSiteTime(s.site && s.site.time);
      if (showKey(s) !== showKey(snap)) {   // a new show day or a rename: the stale notice and header follow
        admin = await api("GET", "/api/admin/state");
        snap = s;
        renderHeader();
        rerenderSchedule();
      }
      // The schedule changed on another page: reload it, unless this page has unsaved edits
      // (then say so, and keep them). Always redraw after replacing sd: inputs still tied to the
      // old rows would otherwise take edits that Save never sends.
      const meta = s.schedule || {};
      if (meta.show_id !== sd.showId || meta.revision !== sd.revision) {
        if (!sd.dirty && !isEditing()) { await loadSchedule(); rerenderSchedule(); }
        else if (!sd.changedElsewhere) { sd.changedElsewhere = true; rerenderSchedule(); }
      }
    } catch (err) { if (err.status === 401) toLogin(); }
  }

  async function start() {
    let info;
    try { info = await api("GET", "/api/info"); } catch (err) {
      app.replaceChildren(card("Running order", h("p", { class: "error" }, "Could not reach Stagewatch. Reload this page to try again.")));
      return;
    }
    isEmulate = !!info.emulate;
    if (info.recovery_required || info.admin_setup_required || !info.is_admin) { toLogin(); return; }
    const logout = document.getElementById("logout");
    logout.hidden = false;
    logout.onclick = async () => { await api("POST", "/api/admin/logout"); toLogin(); };
    try {
      [admin, snap] = await Promise.all([api("GET", "/api/admin/state"), api("GET", "/api/snapshot")]);
      SW.setSiteTime(snap.site && snap.site.time);
      await loadSchedule();
    } catch (err) {
      if (err.status === 401) { toLogin(); return; }
      if (!admin) {
        app.replaceChildren(card("Running order", h("p", { class: "error" }, `Could not load the schedule: ${err.message}. Reload this page to try again.`)));
        return;
      }
      // the lists loaded but the schedule did not: scheduleCard says so
    }
    render();
    setInterval(poll, 5000);
  }

  // Leaving with typed changes not saved: let the browser ask first.
  // Ctrl+P or the browser's own Print menu: build the printable list first.
  window.addEventListener("beforeprint", () => { if (admin && sd) buildPrintView(); });
  window.addEventListener("beforeunload", (ev) => {
    if (sd && sd.dirty) { ev.preventDefault(); ev.returnValue = ""; }
  });

  start();
  SW.heartbeat(2000);   // "Disconnected from Stagewatch" banner if the server stops answering
})();
