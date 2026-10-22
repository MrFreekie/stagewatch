// Power distribution (its own page, /power-distribution): who feeds whom. Each point (generator, grid supply,
// intake unit: things with no meter) and each power meter or circuit gets a role, a number and what feeds it.
// It needs the admin login; without one the page sends you to Admin and back (Admin accepts only the fixed
// path ?next=/power-distribution). Saving uses PUT /api/admin/power-tree, as the old Admin card did.
// Big systems: rows sit under a heading for the supply that feeds them, with a filter box, a "what is it" filter,
// Expand / Collapse all and a Save bar that stays on screen. The rules for filtering and grouping are in
// power-tree-admin.js (tested in node). Stagewatch only reads from the meters: nothing here is sent to one.
// Every string is set as text, never as HTML.
"use strict";

(() => {
  const { h, api, toast } = SW;
  const PA = SW.ptAdmin;
  const app = document.getElementById("app");
  let admin = null, dirty = false;
  let filterText = "", roleFilter = "";
  const groupOpen = new Map();   // group key -> open (remembered across re-draws)

  // The Day / Night choice this browser made on Admin or a dashboard.
  try {
    const t = localStorage.getItem("stagewatch.theme");
    if (t === "day" || t === "night") document.documentElement.setAttribute("data-theme", t === "day" ? "light" : "dark");
  } catch (_) { /* follow the device */ }

  const toLogin = () => { location.replace("/admin?next=/power-distribution"); };
  window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ""; } });

  const field = (label, input) => h("label", { class: "field" }, label, input);
  const val = (el) => el.value.trim();
  async function run(fn, okMsg) {
    try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
    catch (err) { if (err.status === 401) { toLogin(); } else toast(err.message, true); throw err; }
  }
  async function load() {
    admin = await api("GET", "/api/admin/state");
  }
  async function refresh() {
    try { await load(); } catch (err) { if (err.status === 401) { toLogin(); return; } toast(err.message, true); return; }
    dirty = false;
    render();
  }

  // "Use the device's name": asks the meter (one read-only request) for the names typed into it and puts one in the
  // box. It only fills the box: nothing is saved until Save, and nothing is ever sent to the meter.
  function useNameButton(m, input, pick, what) {
    const note = h("span", { class: "muted", role: "status" });
    const btn = h("button", { type: "button", class: "small touch", title: "Read the name typed into the meter itself and put it here. Nothing is changed on the meter.",
      onclick: async () => {
        note.textContent = "Asking the meter…";
        try {
          const r = await api("POST", `/api/admin/shelly/${encodeURIComponent(m.id)}/names`);
          const n = pick(r);
          if (n) { input.value = n; dirty = true; markDirty(); note.textContent = "Filled in. Press Save to keep it."; } else note.textContent = `The meter has no ${what} name.`;
        } catch (err) { note.textContent = "Could not read the meter."; }
      } }, "Use the device's name");
    return [btn, note];
  }
  let markDirty = () => {};

  function render() {
    const pt = admin.power_tree || { roles: [], ups_types: [], nodes: [], problems: [], warnings: [] };
    const roles = pt.roles;
    const meters = (admin.shelly || {}).meters || [];
    const points = (admin.config && admin.config.power_points) || [];
    let advanced = !!pt.show_advanced;
    const rows = [];      // one per node: { ref, kind, el, role, no, up, way, more, name(), line(), body(), search() }
    const listBox = h("div", { class: "pdp-list" });
    const roleLabel = (k) => (roles.find((r) => r.key === k) || {}).label || "";
    const roleSel = (cur, label) => h("select", { class: "touch", "aria-label": `${label} role` },
      [h("option", { value: "" }, "Choose…")].concat(roles.map((r) => h("option", { value: r.key, selected: r.key === cur }, r.label))));
    const noIn = (cur, label) => h("input", { class: "num touch", type: "number", min: "1", max: "99", step: "1", value: cur || "", placeholder: "No.", "aria-label": `${label} number` });
    const numIn = (v, max, ph, lab) => h("input", { class: "num touch", type: "number", min: "0", max, step: "any", value: v || "", placeholder: ph, "aria-label": lab });
    const noOf = (e) => (e.no.value.trim() === "" ? null : Math.round(Number(e.no.value)));
    const wayOf = (e) => (e.way.value.trim() === "" ? null : Math.round(Number(e.way.value)));
    const shown = (e) => PA.display(roles, e.role.value, noOf(e));
    const live = () => rows.map((e) => ({ ref: e.ref, role: e.role.value, no: noOf(e), upstream: e.up.value, way: wayOf(e), name: e.name(), label: shown(e) || e.name() }));

    // ---- save bar
    const status = h("span", { class: "pdp-status", role: "status" }, "No changes");
    const msg = h("span", { class: "warn-text", role: "alert" });
    markDirty = () => { dirty = true; status.textContent = "Unsaved changes"; status.classList.add("pdp-unsaved"); };
    listBox.addEventListener("input", () => { markDirty(); updateHeads(); queuePreview(); });
    listBox.addEventListener("change", () => { markDirty(); layout(); });

    // ---- live preview of the diagram from what is typed here (before saving)
    const previewHost = h("div", { class: "pd-host" });
    const preview = h("details", { class: "pd-preview", open: true, hidden: true }, h("summary", {}, "Diagram preview"),
      h("p", { class: "muted" }, "What feeds what, as it stands now (not saved until you press Save). Supplies are on the left, equipment on the right."), previewHost);
    let previewer = null, previewTimer = 0;
    const drawPreview = () => {
      previewTimer = 0;
      const byRef = {};
      rows.forEach((e) => { byRef[e.ref] = e; });
      const fedFrom = new Set(rows.map((e) => e.up.value).filter(Boolean));
      const nodes = rows.filter((e) => !(e.ref.startsWith("c:") && !e.role.value && !fedFrom.has(e.ref))).map((e) => {
        const up = byRef[e.up.value], upShown = up ? shown(up) || up.name() : "";
        return { ref: e.ref, kind: e.kind, name: e.name(), role: e.role.value, role_label: roleLabel(e.role.value), display: shown(e),
          upstream: up ? e.up.value : "", feed: up && upShown ? PA.linkLabel(upShown, wayOf(e), e.line()) : "", ups_type: e.upsType ? e.upsType() : null };
      });
      const used = nodes.some((n) => n.role || n.upstream);
      preview.hidden = !used;
      if (!used) return;
      if (!previewer) previewer = SW.ptDiagram.mount(previewHost, { fit: false });
      previewer.update(nodes, { upsTypes: pt.ups_types, emptyText: "Nothing to draw yet." });
    };
    const queuePreview = () => { if (!previewTimer) previewTimer = setTimeout(drawPreview, 150); };

    const refreshUps = () => {
      queuePreview();
      const nodes = live();
      rows.forEach((e) => {
        const keep = e.wantUp !== undefined ? e.wantUp : e.up.value, ok = PA.upstreamChoices(roles, nodes, e.ref, e.role.value);
        const sug = e.role.value ? PA.suggestUpstream(roles, nodes, e.ref, e.role.value) : "";
        e.wantUp = undefined;
        const opts = [h("option", { value: "" }, e.role.value === "source" ? "Nothing above a supply" : "Not set")];
        ok.forEach((ref) => { const o = rows.find((x) => x.ref === ref); opts.push(h("option", { value: ref }, PA.optionLabel(roles, o.role.value, noOf(o), o.name()) + (ref === sug ? " (suggested)" : ""))); });
        e.up.replaceChildren(...opts);
        e.up.value = ok.includes(keep) ? keep : "";
        e.shownEl.textContent = shown(e) || "";
        e.up.disabled = e.role.value === "source";
        const upRow = rows.find((x) => x.ref === e.up.value);
        const label = upRow ? PA.linkLabel(shown(upRow) || upRow.name(), wayOf(e), e.line()) : "";
        e.feedEl.textContent = label ? `Fed from ${label}` : "";
        e.wayMsg.textContent = PA.wayClash(nodes, e.ref, e.up.value, wayOf(e));
      });
    };
    // choosing a role fills in the next free number and, if nothing feeds it yet, the likely unit above
    const onRole = (e) => {
      const nodes = live(), role = e.role.value;
      if (role) {
        const taken = nodes.some((x) => x.ref !== e.ref && x.role === role && x.no === noOf(e));
        if (noOf(e) === null || taken) { const n = PA.nextNumber(nodes, e.ref, role); e.no.value = n === null ? "" : String(n); }
        if (!e.up.value) e.wantUp = PA.suggestUpstream(roles, live(), e.ref, role);
      }
      refreshUps();
      e.sync();
    };
    // kind: "point" | "meter" | "circuit". `titleText` is the small line above the row (the meter it belongs to).
    const mk = (ref, kind, label, role, unit, upstream, way, nameFn, titleText) => {
      const e = { ref, kind, name: nameFn, sync: () => {}, extra: () => "" };
      e.role = roleSel(role, label); e.no = noIn(unit, label);
      e.up = h("select", { class: "touch", "aria-label": `${label} upstream` });
      e.up.append(h("option", { value: upstream || "" }, ""));   // so the saved value survives until the list is built
      e.up.value = upstream || "";
      e.way = h("input", { class: "num touch", type: "number", min: "1", max: "99", step: "1", value: way || "", placeholder: "Way", "aria-label": `${label} way on the unit above` });
      e.shownEl = h("strong", { style: "min-width:64px" }, "");
      e.feedEl = h("span", { class: "muted" }, "");
      e.wayMsg = h("span", { class: "warn-text", role: "status" }, "");
      e.role.onchange = () => onRole(e); e.no.oninput = refreshUps; e.up.onchange = refreshUps; e.way.oninput = refreshUps;
      e.line = () => "";
      e.basic = h("div", { class: "row pdp-basic" }, e.shownEl, field("What is it?", e.role), field("No.", e.no), field("What feeds this?", e.up), e.feedEl);
      e.more = h("details", { class: "pd-more", open: advanced }, h("summary", {}, "More options"));
      e.el = h("div", { class: "pd-row" }, titleText ? h("div", { class: "muted pdp-sub" }, titleText) : null, e.basic, e.more);
      e.addMore = (...items) => e.more.append(h("div", { class: "row pdp-basic" }, ...items));
      e.wayField = () => [field("Way (circuit number on the unit above)", e.way), e.wayMsg];
      e.search = () => [shown(e), e.name(), roleLabel(e.role.value), titleText || "", e.extra()].join(" ");
      return e;
    };
    const lineSel = (cur, threePhase, label) => {
      if (threePhase) return null;
      return h("select", { class: "touch", "aria-label": `${label} line` }, [["", "Not set"], ["L1", "Line 1 (Brown)"], ["L2", "Line 2 (Black)"], ["L3", "Line 3 (Grey)"]].map(([v, t]) => h("option", { value: v, selected: v === (cur || "") }, t)));
    };
    const limitFields = (rating, breaker) => {
      const weak = h("span", { class: "warn-text", role: "status" });
      const chk = () => { weak.textContent = SW.limit(Number(breaker.value) || null, Number(rating.value) || null).weak ? SW.WEAK_NOTE : ""; };
      rating.addEventListener("input", chk); breaker.addEventListener("input", chk); chk();
      return [field("Breaker or fuse rating (A)", breaker), field("Device limit (A, what the meter can carry)", rating), weak];
    };

    // ---- distribution points
    const addPoint = (p, isNew) => {
      const name = h("input", { class: "touch", maxlength: "60", value: p.name || "", placeholder: "Name (e.g. FOH generator)", "aria-label": "Point name" });
      const e = mk(`p:${p.id}`, "point", p.name || "Point", p.role, p.unit_no, p.upstream, p.way, () => name.value.trim(), "");
      const ratingA = numIn(p.rating_a, 5000, "A", "Breaker or fuse rating (amps)"), ratingK = numIn(p.rating_kva, 10000, "kVA", "Rating (kVA)");
      const ups = h("select", { class: "touch", "aria-label": "UPS type" }, pt.ups_types.map((u) => h("option", { value: u.key, selected: u.key === (p.ups_type || "unknown") }, u.label)));
      e.upsType = () => ups.value;
      ups.addEventListener("change", queuePreview); name.addEventListener("input", queuePreview);
      e.sync = () => { ups.hidden = e.role.value !== "ups"; };
      const gone = h("button", { class: "small danger touch", type: "button", onclick: () => {
        if (isNew) { rows.splice(rows.indexOf(e), 1); e.el.remove(); markDirty(); refreshUps(); layout(); return; }
        const kids = rows.filter((x) => x.up.value === e.ref).length;
        const lose = dirty ? " Your other unsaved changes on this page are dropped when it reloads." : "";
        if (!confirm(`Remove ${shown(e) || e.name() || "this point"}?${kids ? ` ${kids} unit${kids === 1 ? " is" : "s are"} fed from it.` : ""}${lose}`)) return;
        const how = kids ? (confirm("Move the units fed from it up a level? OK moves them up. Cancel leaves them with no Upstream.") ? "reparent" : "detach") : "";
        run(() => api("DELETE", `/api/admin/power-points/${encodeURIComponent(p.id)}${how ? `?children=${how}` : ""}`), "Removed").then(refresh, () => {});
      } }, "Remove");
      // The old "Nothing measures it" tick and typed allowance had no effect on any figure; the saved fields stay, unchanged by this page.
      e.body = () => ({ id: p.id, name: name.value.trim(), role: e.role.value, unit_no: noOf(e), upstream: e.up.value, way: wayOf(e),
        rating_a: Number(ratingA.value) || 0, rating_kva: Number(ratingK.value) || 0, ups_type: e.role.value === "ups" ? ups.value : "unknown",
        monitored: true });
      e.basic.prepend(name);
      e.basic.append(ups, gone);
      e.addMore(...e.wayField(), field("Breaker or fuse rating (A)", ratingA), field("Rating (kVA)", ratingK));
      e.sync();
      rows.push(e);
      return e;
    };
    points.forEach((p) => addPoint(p, false));

    // ---- meters: one row for a one-circuit meter, else a header row and a row per circuit
    meters.forEach((m) => {
      const kinds = PA.meterRows(m);
      const ratingIn = numIn(m.rating_a, 200, "none", `${m.name} device limit (amps)`), breakerIn = numIn(m.breaker_a, 1000, "unknown", `${m.name} breaker or fuse rating (amps)`);
      const head = [m.name, m.area || "", kinds[0].single && m.three_phase ? "three-phase (3P)" : ""].filter(Boolean).join(" · ");
      const em = mk(`m:${m.id}`, "meter", m.name, m.role, m.unit_no, m.upstream, m.way, () => m.name, `Meter: ${head}`);
      em.id = m.id; em.extra = () => m.area || "";
      if (kinds[0].single) {
        const label = h("input", { class: "touch", maxlength: "40", value: m.label || "", placeholder: "Name on the Power card", "aria-label": `${m.name} label` });
        const inTotal = h("input", { type: "checkbox", checked: !!m.in_total, "aria-label": `${m.name} include in site total` });
        const line = lineSel(m.phase, m.three_phase, m.name);
        em.line = () => (m.three_phase ? "3P" : line.value);
        em.extra = () => `${m.area || ""} ${label.value}`;
        if (line) line.onchange = refreshUps;
        em.body = () => ({ id: m.id, role: em.role.value, unit_no: noOf(em), upstream: em.up.value, way: wayOf(em), label: val(label),
          ...(m.three_phase ? {} : { phase: line.value }), rating_a: Number(ratingIn.value) || 0, breaker_a: Number(breakerIn.value) || 0, in_total: inTotal.checked, channels: [] });
        em.basic.append(h("label", { class: "row pdp-tick", title: "Adds this meter's watts into one site total on the Power card" }, inTotal, " Include in site total"));
        em.addMore(...em.wayField(), m.three_phase ? h("span", { class: "muted" }, "Line: 3P (all three lines)") : field("Line", line), ...limitFields(ratingIn, breakerIn), field("Name on the Power card", label),
          ...useNameButton(m, label, (r) => (r.channels || {})[m.circuit_key], "circuit"), m.circuit_device_name ? h("span", { class: "muted" }, `Name on the meter: ${m.circuit_device_name}`) : null);
      } else {
        const chans = (m.channels || []);
        const singles = chans.filter((c) => !c.three_phase).length;
        const combined = h("input", { type: "checkbox", checked: !!m.combined_bar, "aria-label": `${m.name} combined load bar` });
        em.body = () => ({ id: m.id, role: em.role.value, unit_no: noOf(em), upstream: em.up.value, way: wayOf(em),
          rating_a: Number(ratingIn.value) || 0, breaker_a: Number(breakerIn.value) || 0, ...(singles >= 2 ? { combined_bar: combined.checked } : {}),
          channels: rows.filter((r) => r.kind === "circuit" && r.meterId === m.id).map((r) => r.body()) });
        em.addMore(...em.wayField(), ...limitFields(ratingIn, breakerIn));
        if (singles >= 2) em.addMore(h("label", { class: "row pdp-tick", title: "Adds one summary line to the Power card, 'All circuits, total', and its demand figures. With a limit set above, also one bar: the amps of all this meter's circuits added together" },
          combined, " All circuits, total (summary line; with a limit above, also a combined load bar)"));
      }
      rows.push(em);
      kinds.slice(1).forEach((k) => {
        const c = (m.channels || []).find((x) => x.key === k.key);
        const nm = `${m.name} ${c.label || c.name}`;
        const ec = mk(k.ref, "circuit", nm, c.role, c.unit_no, c.upstream, c.way, () => nm, `Circuit of ${m.name}${m.area ? ` · ${m.area}` : ""}`);
        ec.meterId = m.id;
        const label = h("input", { class: "touch", maxlength: "40", value: c.label || "", placeholder: "Label (e.g. Amp rack 3)", "aria-label": `${nm} label` });
        const inTotal = h("input", { type: "checkbox", checked: !!c.in_total, "aria-label": `${nm} include in site total` });
        const line = lineSel(c.phase, c.three_phase, nm);
        const cr = numIn(c.rating_a, 200, "meter's", `${nm} device limit (amps)`), cb = numIn(c.breaker_a, 1000, "meter's", `${nm} breaker or fuse rating (amps)`);
        ec.line = () => (c.three_phase ? "3P" : line.value);
        ec.extra = () => `${m.area || ""} ${label.value}`;
        if (line) line.onchange = refreshUps;
        ec.body = () => ({ key: c.key, role: ec.role.value, unit_no: noOf(ec), upstream: ec.up.value, way: wayOf(ec), label: val(label),
          ...(c.three_phase ? {} : { phase: line.value }), rating_a: Number(cr.value) || 0, breaker_a: Number(cb.value) || 0, in_total: inTotal.checked });
        ec.basic.prepend(label);
        ec.basic.append(h("label", { class: "row pdp-tick" }, inTotal, " Include in site total"));
        ec.addMore(...ec.wayField(), c.three_phase ? h("span", { class: "muted" }, "Line: 3P (all three lines)") : field("Line", line), ...limitFields(cr, cb),
          ...useNameButton(m, label, (r) => (r.channels || {})[c.key], "circuit"), c.device_name ? h("span", { class: "muted" }, `Name on the meter: ${c.device_name}`) : null);
        rows.push(ec);
      });
    });

    // ---- filter, grouping and the row count
    const filterIn = h("input", { type: "search", class: "touch", value: filterText, placeholder: "Filter by name, number or area", "aria-label": "Filter the units" });
    const kindSel = h("select", { class: "touch", "aria-label": "Show only this kind of unit" },
      [h("option", { value: "" }, "All kinds"), h("option", { value: "__none" }, "No role chosen yet")].concat(roles.map((r) => h("option", { value: r.key }, r.label))));
    kindSel.value = roleFilter;
    const countEl = h("span", { class: "muted", role: "status" });
    let heads = [];
    const filtering = () => filterText.trim() !== "" || roleFilter !== "";
    const headText = (g, byRef) => {
      if (g.unassigned) return "Not fed from anything yet";
      const r = byRef.get(g.key);
      return `${r.role.value === "source" ? "Supply" : "Starts at"} ${shown(r) || r.name() || "unnamed"}${shown(r) && r.name() ? ` · ${r.name()}` : ""}`;
    };
    function updateHeads() {
      const byRef = new Map(rows.map((e) => [e.ref, e]));
      heads.forEach((x) => { x.title.textContent = headText(x.g, byRef); });
    }
    function layout() {
      const active = document.activeElement;
      const byRef = new Map(rows.map((e) => [e.ref, e]));
      const groups = PA.groupRows(rows.map((e) => ({ ref: e.ref, role: e.role.value, upstream: e.up.value })));
      let visible = 0;
      heads = [];
      const els = [];
      groups.forEach((g) => {
        const show = g.refs.filter((r) => PA.rowVisible(byRef.get(r).search(), byRef.get(r).role.value, filterText, roleFilter));
        g.refs.forEach((r) => { byRef.get(r).el.hidden = show.indexOf(r) < 0; });
        visible += show.length;
        if (!show.length) return;
        const title = h("span", { class: "pdp-title" }, "");
        const total = g.refs.length;
        const open = filtering() ? true : groupOpen.get(g.key) !== false;
        const d = h("details", { class: "pdp-group", open }, h("summary", { class: "touch" }, title,
          h("span", { class: "muted pdp-n" }, show.length === total ? `${total} unit${total === 1 ? "" : "s"}` : `${show.length} of ${total}`)),
        ...g.refs.map((r) => byRef.get(r).el));
        d.addEventListener("toggle", () => { if (!filtering()) groupOpen.set(g.key, d.open); });
        heads.push({ g, title, d });
        els.push(d);
      });
      listBox.replaceChildren(...els);
      if (!rows.length) listBox.append(h("p", { class: "muted" }, "No points or meters yet. Add a point for each generator or supply, and add your Shelly meters under Admin → Integrations."));
      else if (!visible) listBox.append(h("p", { class: "muted" }, "Nothing matches the filter."));
      updateHeads();
      countEl.textContent = rows.length ? `Showing ${visible} of ${rows.length} unit${rows.length === 1 ? "" : "s"}` : "";
      if (active && active !== document.body && active.isConnected && document.activeElement !== active) { try { active.focus({ preventScroll: true }); } catch (_) { /* ignore */ } }
    }
    filterIn.addEventListener("input", () => { filterText = filterIn.value; layout(); });
    kindSel.addEventListener("change", () => { roleFilter = kindSel.value; layout(); });
    const setAll = (open) => { heads.forEach((x) => { x.d.open = open; if (!filtering()) groupOpen.set(x.g.key, open); }); };

    refreshUps();
    const advSwitch = h("input", { type: "checkbox", checked: advanced, "aria-label": "Show advanced power options" });
    advSwitch.onchange = () => {
      advanced = advSwitch.checked;
      rows.forEach((e) => { e.more.open = advanced; });
      api("PUT", "/api/admin/power-tree", { show_advanced: advanced }).catch(() => {});      // remembered; nothing else changes
    };
    const unusedId = () => PA.slug("point", new Set(rows.filter((r) => r.kind === "point").map((r) => r.ref.slice(2))));
    const add = h("button", { class: "touch", type: "button", onclick: () => {
      const e = addPoint({ id: unusedId(), name: "" }, true);
      markDirty(); refreshUps(); filterText = ""; roleFilter = ""; filterIn.value = ""; kindSel.value = "";
      groupOpen.set("", true); layout();
      const first = e.el.querySelector("input"); if (first) first.focus();
    } }, "Add a point");
    const save = h("button", { class: "primary touch", type: "button", onclick: () => {
      const body = { points: rows.filter((r) => r.kind === "point").map((r) => r.body()),
        meters: rows.filter((r) => r.kind === "meter").map((r) => r.body()) };
      if (body.points.some((p) => !p.name)) { msg.textContent = "Give every point a name."; return; }
      msg.textContent = "";
      save.disabled = true;
      run(() => api("PUT", "/api/admin/power-tree", body), "Power distribution saved").then(() => { dirty = false; return refresh(); }, (err) => { msg.textContent = `Not saved: ${err.message}`; })
        .finally(() => { save.disabled = false; });
    } }, "Save");
    const undo = h("button", { class: "touch", type: "button", onclick: () => {
      if (dirty && !confirm("Throw away your unsaved changes?")) return;
      refresh();
    } }, "Undo changes");
    // ---- Save to a file / Read a file. Reading shows a preview first; nothing changes until Apply.
    const fileNote = h("p", { class: "muted", role: "status" });
    const fileBox = h("div", { role: "status" });
    const saveFileBtn = h("button", { class: "touch", type: "button", title: "Download the saved layout as a file: a backup, or to move it to another hub", onclick: async () => {
      try {
        const res = await fetch("/api/admin/power-tree/export", { credentials: "same-origin", cache: "no-store" });
        if (res.status === 401) { toLogin(); return; }
        if (!res.ok) throw new Error("Could not make the file");
        const a = h("a", { href: URL.createObjectURL(await res.blob()), download: `stagewatch-power-distribution-${new Date().toISOString().slice(0, 10)}.json` });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        fileNote.textContent = dirty ? "Saved the layout as it is stored on the hub. Changes still on this page are not in the file until you press Save." : "Saved. The file says which meter each setting belongs to, by its MAC, and holds no addresses or passwords.";
      } catch (err) { toast(err.message, true); }
    } }, "Save to a file");
    const showFile = (data, r) => {
      const list = (s) => h("div", {}, h("strong", {}, s.title), h("ul", {}, s.lines.map((t) => h("li", {}, t))));
      fileNote.textContent = "";
      fileBox.replaceChildren(h("div", { class: "notice" },
        h("p", {}, PA.previewHeadline(r)),
        ...PA.previewSections(r).map(list),
        h("p", { class: "muted" }, "Reading a file never removes anything that is already here."),
        h("div", { class: "row" },
          PA.canApply(r) ? h("button", { class: "primary small touch", type: "button", onclick: () =>
            run(() => api("POST", "/api/admin/power-tree/import", { data, dry_run: false }), "Power distribution read from the file")
              .then(() => { fileBox.replaceChildren(); return refresh(); }, () => {}) }, "Apply to this hub") : null,
          h("button", { class: "small touch", type: "button", onclick: () => fileBox.replaceChildren() }, PA.canApply(r) ? "Cancel" : "Close"))));
    };
    const fileInput = h("input", { type: "file", accept: "application/json,.json", hidden: true, "aria-label": "Power distribution file to read", onchange: () => {
      const f = fileInput.files[0]; fileInput.value = "";
      if (!f) return;
      if (f.size > PA.FILE_MAX_BYTES) { toast("That file is too big to be a power distribution file. Nothing has been changed.", true); return; }
      const rd = new FileReader();
      rd.onload = () => {
        let data;
        try { data = JSON.parse(String(rd.result)); } catch (_) { toast("That is not a Stagewatch power distribution file. Nothing has been changed.", true); return; }
        if (!data || typeof data !== "object" || Array.isArray(data)) { toast("That is not a Stagewatch power distribution file. Nothing has been changed.", true); return; }
        run(() => api("POST", "/api/admin/power-tree/import", { data, dry_run: true })).then((r) => showFile(data, r), () => {});
      };
      rd.onerror = () => toast("Could not read that file. Nothing has been changed.", true);
      rd.readAsText(f);
    } });
    const readFileBtn = h("button", { class: "touch", type: "button", title: "Choose a saved file. You see what would change first; nothing changes until you press Apply", onclick: () => {
      if (dirty && !confirm("You have changes on this page that are not saved. Applying a file reloads the page and they would be lost. Choose a file anyway?")) return;
      fileInput.click();
    } }, "Read a file");
    const note = (list, cls) => list.map((x) => h("p", { class: cls }, x.message));
    const nUnits = rows.length;

    app.replaceChildren(
      h("section", { class: "card" },
        h("h2", {}, h("span", {}, "Power distribution"), h("span", { class: "grow" }), h("span", { class: "muted" }, `${nUnits} unit${nUnits === 1 ? "" : "s"}`)),
        h("p", { class: "muted" }, "Say what feeds what, so Stagewatch can show the power from each generator or supply down to the equipment. Add a point for anything with no meter (the generator, the grid supply, an intake unit). Then, for each meter: choose what it is, check its number, and say what feeds it. Stagewatch only reads from the meters."),
        h("label", { class: "row pdp-tick" }, advSwitch, " Show advanced power options (way, line, breaker rating, device limit)"),
        pt.problems.length ? h("div", { class: "notice" }, "Needs fixing: ", ...note(pt.problems, "warn-text")) : null,
        pt.warnings.length ? h("details", { class: "muted" }, h("summary", {}, `${pt.warnings.length} meter${pt.warnings.length === 1 ? "" : "s"} still to set up`), ...note(pt.warnings, "muted")) : null,
        preview),
      h("section", { class: "card" },
        h("div", { class: "row pdp-tools" }, field("Find", filterIn), field("Show only", kindSel),
          h("button", { class: "touch", type: "button", onclick: () => setAll(true) }, "Expand all"),
          h("button", { class: "touch", type: "button", onclick: () => setAll(false) }, "Collapse all"),
          h("span", { class: "grow" }), countEl, saveFileBtn, readFileBtn, fileInput, add),
        fileNote, fileBox,
        listBox,
        SW.help(h("p", { class: "muted hint" }, "The order is supply, intake unit (ISU), main distribution unit (CDU), final distribution unit (FDU), then equipment. A unit can skip a level but cannot be fed from the same level or from below. Units are grouped under the supply that feeds them; change what feeds a unit and it moves to its new group. When you choose what a unit is, Stagewatch fills in the next free number and suggests what feeds it; change either if it is wrong. A meter with one circuit is one row. Under More options: the way is the circuit number on the unit above, so a final unit on way 2 of CDU02 reads CDU02-C2, and with its line CDU02-C2-L1 (CDU01-C1-3P for a three-phase feed). Each way feeds one thing. The breaker or fuse rating is the protective device in front of it; the device limit is what the meter itself can carry; the lower of the two is used for the load bars. A UPS can sit anywhere in the chain; its type says how it behaves on mains. Numbers run from 1 to 99, each used once per role. Several generators or supplies are fine: each is the top of its own tree. Check the names ISU, CDU and FDU against your own paperwork. Nothing here changes any meter."))),
      h("div", { class: "pdp-bar" }, status, msg, h("span", { class: "grow" }), undo, save));
    layout();
    drawPreview();
  }

  async function start() {
    try { await load(); } catch (err) {
      if (err.status === 401) { toLogin(); return; }
      app.replaceChildren(SW.card("Power distribution", h("p", {}, `Could not load the power setup: ${err.message}`)));
      return;
    }
    render();
  }

  start();
  SW.heartbeat(2000);   // "Disconnected from Stagewatch" banner if the server stops answering
})();
