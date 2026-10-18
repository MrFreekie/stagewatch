// FOH advice cards: Alignment drift and HF air loss. Words, rows and the on-screen tables.
// The view functions are pure (no DOM) and run under node (tests/js/advice_test.js); the mount
// functions build the DOM with SW.h and textContent only and then update the same elements in place.
// Advice only: nothing here changes a delay or any other setting. The numbers come from the server's
// "advice" block (core/advice.py); no physics is done in the browser.
"use strict";
SW.advice = (function () {
  const a = {};
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  const DASH = "—";

  // Fixed strings (plan 9.5). None of the words the plan bans appear anywhere in this file.
  a.DRIFT_FOOTER = "Advice only. Stagewatch never changes your delays.";
  a.AIR_SUBTITLE = "Estimated from air temperature and humidity. Advisory, not a measurement.";
  a.AIR_FOOTER = "Straight-line, uniform air only. Wind, temperature layers, ground and crowd are not included. Stagewatch never changes your system.";
  a.NEEDS_HUMIDITY = "Needs a humidity reading";
  a.NEEDS_TEMPERATURE = "Needs a temperature reading";
  a.OUT_OF_RANGE = "Outside the model's range: treat as rough";
  a.NO_DRIFT_MARKER = "No reference marker today. Add a marker once your delays are set.";
  a.NO_AIR_MARKER = "No reference marker today. Add a marker at soundcheck.";
  a.RULE_OF_THUMB = "A change of about 1 dB is often given as the smallest a listener can notice. That is a rule of thumb, and a change in the top octave alone may be harder to hear. Figures are estimated at the middle of each band.";
  // Wording cut-offs for the air-loss change, in dB (plan 9.4): under 0.5 "about the same", under 2 "slightly", then "clearly".
  a.SAME_DB = 0.5;
  a.CLEARLY_DB = 2;
  a.HEADLINE_BAND = "8k";
  a.WALL_ROWS = 3;

  // ---- small formatters
  a.distance = (m) => (isNum(m) ? `${SW.num(m, Number.isInteger(m) ? 0 : 1)} m` : DASH);
  a.signedMs = (v) => (isNum(v) ? `${SW.signed(v, 2)} ms` : DASH);
  a.plainMs = (v) => (isNum(v) ? `${SW.num(v, 2)} ms` : DASH);
  a.signedDb = (v) => (isNum(v) ? `${SW.signed(v, 1)} dB` : DASH);
  a.bandLabel = (band) => String(band).replace("k", " kHz");

  // ---- drift
  a.driftStatus = function (p) {
    if (!isNum(p.drift_ms)) return { text: DASH, cls: "none" };
    return p.level === "advisory" ? { text: "▲ CHECK", cls: "check" } : { text: "OK", cls: "ok" };
  };
  // Small notes under a drift row, in plain words. Codes come from core/advice.py FLAG_*.
  a.driftNotes = function (p) {
    const out = [];
    const flags = p.flags || [];
    if (flags.indexOf("out_of_range") >= 0) out.push(a.OUT_OF_RANGE);
    if (flags.indexOf("humidity_assumed") >= 0) out.push("No humidity reading: 50 % assumed");
    if (flags.indexOf("pressure_altitude") >= 0) out.push("No pressure reading: estimated from the site height");
    if ((p.source_fallback || []).length) out.push("Site average used");
    return out;
  };
  a.driftHeader = function (ref) {
    if (!ref) return a.NO_DRIFT_MARKER;
    const at = SW.fmtTime(ref.ts);
    if (!ref.has_readings) return `No readings recorded at ${ref.label} (${at})`;
    const d = ref.delta || {};
    const parts = [`Since ${ref.label} at ${at}`];
    if (isNum(d.t_c)) parts.push(`${SW.signed(d.t_c, 1)} °C`);
    if (isNum(d.rh)) parts.push(`${SW.signed(d.rh, 0)} % RH`);
    return parts.join(" · ");
  };
  // The paths with the largest drift first (nothing known goes last); the order of equal ones is kept.
  a.largestDrift = function (paths, n) {
    const size = (p) => (isNum(p.drift_ms) ? Math.abs(p.drift_ms) : -1);
    return (paths || []).map((p, i) => ({ p, i })).sort((x, y) => size(y.p) - size(x.p) || x.i - y.i).slice(0, n).map((x) => x.p);
  };
  // The view the card draws. layout: "tablet" | "phone" | "wall". `ageText` is "" or how old the readings are.
  a.driftView = function (adv, layout, ageText) {
    const paths = (adv && adv.paths) || [];
    const shown = layout === "wall" ? a.largestDrift(paths, a.WALL_ROWS) : paths;
    const notes = [];
    const rows = shown.map((p) => {
      const st = a.driftStatus(p);
      const pn = a.driftNotes(p);
      if (pn.length && layout !== "wall") notes.push(`${p.name}: ${pn.join(". ")}`);
      return { id: p.id, name: p.name, stale: !!p.stale, status: st,
        cells: { distance: a.distance(p.distance_m), drift: a.signedMs(p.drift_ms), nowset: a.plainMs(p.current_delay_ms),
          suggested: a.plainMs(p.suggested_delay_ms), status: st.text } };
    });
    if (ageText) notes.unshift(`Old readings: the last one was ${ageText}`);
    return { layout, sig: [layout].concat(rows.map((r) => r.id)).join("|"), header: a.driftHeader(adv && adv.drift_ref),
      footer: a.DRIFT_FOOTER, notes, rows };
  };

  // ---- air loss
  // {sym, text, same}: what a change in dB means, in words. Positive = more top end lost.
  a.changeWords = function (c) {
    if (!isNum(c)) return { sym: "", text: DASH, same: false, cls: "none" };
    if (Math.abs(c) < a.SAME_DB) return { sym: "≈", text: "about the same", same: true, cls: "same" };
    const adj = Math.abs(c) < a.CLEARLY_DB ? "slightly " : "clearly ";
    return c < 0 ? { sym: "▲", text: `${adj}less top end lost`, same: false, cls: "less" }
      : { sym: "▼", text: `${adj}more top end lost`, same: false, cls: "more" };
  };
  // Which bands to show: the system's own, or the other view when the card asks for it.
  a.pickBands = function (pos, wanted, system) {
    if (!wanted || wanted === system) return pos.bands || [];
    return pos.other_bands && pos.other_bands.length ? pos.other_bands : (pos.bands || []);
  };
  // What a position cannot show, said in words ("" when it can).
  a.positionMessage = function (pos) {
    const flags = pos.flags || [];
    if (flags.indexOf("no_temperature") >= 0) return a.NEEDS_TEMPERATURE;
    if (flags.indexOf("needs_humidity") >= 0) return a.NEEDS_HUMIDITY;
    return "";
  };
  a.airNotes = function (pos) {
    const flags = pos.flags || [];
    const out = [];
    if (flags.indexOf("out_of_range") >= 0) out.push(a.OUT_OF_RANGE);
    if (flags.indexOf("reference_incomplete") >= 0) out.push("The reference marker has no usable temperature or humidity reading");
    if (flags.indexOf("pressure_altitude") >= 0) out.push("No pressure reading: estimated from the site height");
    return out;
  };
  a.airHeader = function (ref) {
    if (!ref) return a.NO_AIR_MARKER;
    const at = SW.fmtTime(ref.ts);
    if (!ref.has_readings) return `No readings recorded at ${ref.label} (${at})`;
    const was = ref.was || {}, d = ref.delta || {};
    const bits = [];
    if (isNum(was.t_c) && isNum(d.t_c)) bits.push(`${SW.num(was.t_c, 1)} → ${SW.num(was.t_c + d.t_c, 1)} °C`);
    if (isNum(was.rh) && isNum(d.rh)) bits.push(`${SW.num(was.rh, 0)} → ${SW.num(was.rh + d.rh, 0)} % RH`);
    return bits.length ? `Compared with ${ref.label} at ${at}: ${bits.join(", ")}` : `Compared with ${ref.label} at ${at}`;
  };
  a.bandCell = function (b, distance) {
    const w = a.changeWords(b.change_db);
    return { band: b.band, label: a.bandLabel(b.band), fig: isNum(b.change_db) ? a.signedDb(b.change_db) : DASH,
      sym: w.sym, words: isNum(b.change_db) ? w.text : "", cls: w.cls,
      now: isNum(b.now_db) ? `now ${SW.num(b.now_db, 1)} dB over ${a.distance(distance)}` : "" };
  };
  // Positions with the largest 8 kHz change first (unknown last); equal ones keep their order.
  a.largestChange = function (positions, wanted, system, n) {
    const size = (pos) => {
      const b = a.pickBands(pos, wanted, system).find((x) => x.band === a.HEADLINE_BAND);
      return b && isNum(b.change_db) ? Math.abs(b.change_db) : -1;
    };
    return (positions || []).map((p, i) => ({ p, i })).sort((x, y) => size(y.p) - size(x.p) || x.i - y.i).slice(0, n).map((x) => x.p);
  };
  // The view the card draws. wanted: "" (follow the system), "octave" or "third".
  a.airView = function (adv, layout, wanted, ageText) {
    const system = (adv && adv.resolution) || "octave";
    const positions = (adv && adv.positions) || [];
    const shown = layout === "wall" ? a.largestChange(positions, wanted, system, a.WALL_ROWS) : positions;
    const notes = [];
    const rows = shown.map((pos) => {
      const message = a.positionMessage(pos);
      const bands = message ? [] : a.pickBands(pos, wanted, system).map((b) => a.bandCell(b, pos.distance_m));
      const bandsAll = a.pickBands(pos, wanted, system).map((b) => b.band);
      if (message) notes.push(`${pos.name}: ${message}`);
      for (const n of a.airNotes(pos)) if (notes.indexOf(`${pos.name}: ${n}`) < 0 && layout !== "wall") notes.push(`${pos.name}: ${n}`);
      return { id: pos.id, name: pos.name, distance: a.distance(pos.distance_m), stale: !!pos.stale, message,
        bandKeys: bandsAll, bands, head: bands.find((c) => c.band === a.HEADLINE_BAND) || null };
    });
    if (ageText) notes.unshift(`Old readings: the last one was ${ageText}`);
    const bandKeys = rows.length ? rows[0].bandKeys : [];
    return { layout, system, sig: [layout, bandKeys.join(",")].concat(rows.map((r) => r.id)).join("|"),
      subtitle: a.AIR_SUBTITLE, header: a.airHeader(adv && adv.air_ref), footer: a.AIR_FOOTER, notes, rows, bandKeys };
  };

  // ---- DOM (SW.h and textContent only). Each mount builds once per structure and then patches text.
  const setText = (el, text) => { if (el.textContent !== text) el.textContent = text; };
  const setClass = (el, cls) => { if (el.className !== cls) el.className = cls; };
  const lines = (box, list) => {   // a notes box: one paragraph per note; the box keeps one line of space
    SW.slot(box, list.length === 0);
    if (box.dataset.n === list.join("\n")) return;
    box.dataset.n = list.join("\n");
    box.replaceChildren(...list.map((t) => SW.h("span", { class: "adv-note-line" }, t)));
  };

  a.mountDrift = function (root) {
    const h = SW.h;
    const head = h("p", { class: "adv-head" }), body = h("div", { class: "adv-body" });
    const notes = h("p", { class: "adv-notes muted" });
    const foot = h("p", { class: "muted adv-foot" });
    root.replaceChildren(head, body, notes, foot);
    let sig = null, refs = {};
    return function update(view) {
      if (view.sig !== sig) { sig = view.sig; refs = buildDrift(body, view); }
      setText(head, view.header);
      setText(foot, view.footer);
      lines(notes, view.notes);
      for (const r of view.rows) {
        const x = refs[r.id];
        if (!x) continue;
        setClass(x.row, x.base + (r.stale ? " adv-stale" : ""));
        for (const k of Object.keys(r.cells)) if (x[k]) setText(x[k], r.cells[k]);
        setClass(x.status, `adv-status adv-${r.status.cls}`);
      }
    };
  };
  function buildDrift(body, view) {
    const h = SW.h, refs = {};
    const col = (r, key, cls) => { const el = h("td", { class: cls }); refs[r.id][key] = el; return el; };
    if (view.layout === "tablet") {
      const rowsEl = view.rows.map((r) => {
        refs[r.id] = {};
        const tr = h("tr", {}, h("th", { scope: "row", class: "adv-name" }, r.name), col(r, "distance", "adv-num"),
          col(r, "drift", "adv-num adv-drift"), col(r, "nowset", "adv-num"), col(r, "suggested", "adv-num"), col(r, "status", ""));
        refs[r.id].row = tr; refs[r.id].base = "adv-row";
        return tr;
      });
      body.replaceChildren(h("div", { class: "table-scroll" }, h("table", { class: "adv-table" },
        h("thead", {}, h("tr", {}, ["Path", "Distance", "Drift", "Now set", "Suggested", "Status"].map((t, i) => h("th", { class: i > 0 && i < 5 ? "adv-num" : "" }, t)))),
        h("tbody", {}, rowsEl))));
    } else {
      body.replaceChildren(h("ul", { class: "adv-list" }, view.rows.map((r) => {
        refs[r.id] = {};
        const li = h("li", { class: "adv-row adv-line" });
        const name = h("span", { class: "adv-name" }, r.name);
        const drift = h("span", { class: "adv-num adv-drift" }), status = h("span", { class: "adv-status" });
        li.append(name, drift, status);
        Object.assign(refs[r.id], { row: li, base: "adv-row adv-line", drift, status });
        return li;
      })));
    }
    return refs;
  }

  a.mountAir = function (root) {
    const h = SW.h;
    const sub = h("p", { class: "muted adv-sub" }), head = h("p", { class: "adv-head" });
    const body = h("div", { class: "adv-body" }), notes = h("p", { class: "adv-notes muted" });
    const foot = h("p", { class: "muted adv-foot" });
    root.replaceChildren(sub, head, body, notes, foot);
    let sig = null, refs = {};
    return function update(view) {
      if (view.sig !== sig) { sig = view.sig; refs = buildAir(body, view); }
      setText(sub, view.subtitle);
      setText(head, view.header);
      setText(foot, view.footer);
      lines(notes, view.notes);
      for (const r of view.rows) {
        const x = refs[r.id];
        if (!x) continue;
        const dim = r.stale ? " adv-stale" : "";
        if (x.row) setClass(x.row, x.base + dim);
        const cells = r.bands.length ? r.bands : r.bandKeys.map((b) => ({ band: b, fig: DASH, sym: "", words: "", cls: "none", now: "" }));
        for (const c of cells) {
          const y = x.bands[c.band];
          if (!y) continue;
          setText(y.fig, c.fig); setText(y.sym, c.sym); setText(y.words, c.words); setText(y.now, c.now);
          setClass(y.cell, `adv-cell adv-${c.cls}${dim}`);
        }
        if (x.head) {
          const c = r.head || { fig: DASH, sym: "", words: "", now: "", cls: "none" };
          setText(x.head.fig, c.fig); setText(x.head.sym, c.sym); setText(x.head.words, c.words || r.message);
          setText(x.head.now, c.now);
          setClass(x.head.cell, `adv-cell adv-${c.cls}${dim}`);
        }
      }
    };
    function buildAir(box, v) {
      const refs2 = {};
      const cell = (tag) => {
        const sym = h("span", { class: "adv-sym", "aria-hidden": "true" }), fig = h("span", { class: "adv-fig adv-num" });
        const words = h("span", { class: "adv-words" }), now = h("span", { class: "adv-now" });
        return { sym, fig, words, now, cell: h(tag, { class: "adv-cell" }, h("span", { class: "adv-main" }, sym, fig), words, now) };
      };
      v.rows.forEach((r) => { refs2[r.id] = { bands: {} }; });
      if (v.layout === "tablet") {
        const trs = v.bandKeys.map((band) => h("tr", {}, h("th", { scope: "row", class: "adv-name" }, a.bandLabel(band)),
          v.rows.map((r) => { const c = cell("td"); refs2[r.id].bands[band] = c; return c.cell; })));
        box.replaceChildren(h("div", { class: "table-scroll" }, h("table", { class: "adv-table adv-air" },
          h("thead", {}, h("tr", {}, h("th", {}, "Band"), v.rows.map((r) => h("th", { class: "adv-pos" }, r.name, h("span", { class: "adv-dist" }, ` ${r.distance}`))))),
          h("tbody", {}, trs))));
        return refs2;
      }
      if (v.layout === "wall") {
        box.replaceChildren(h("ul", { class: "adv-list" }, v.rows.map((r) => {
          const c = cell("span");
          refs2[r.id].head = c;
          const li = h("li", { class: "adv-row adv-line" }, h("span", { class: "adv-name" }, r.name, h("span", { class: "adv-dist" }, ` ${r.distance}`)), c.cell);
          refs2[r.id].row = li; refs2[r.id].base = "adv-row adv-line";
          return li;
        })));
        return refs2;
      }
      // phone: the 8 kHz line for each position; tap to see the other bands
      box.replaceChildren(h("ul", { class: "adv-list" }, v.rows.map((r) => {
        const c = cell("span");
        refs2[r.id].head = c;
        const more = h("div", { class: "adv-more", hidden: true }, v.bandKeys.filter((b) => b !== a.HEADLINE_BAND).map((band) => {
          const d = cell("div"); refs2[r.id].bands[band] = d; return h("div", { class: "adv-bandrow" }, h("span", { class: "adv-bandname" }, a.bandLabel(band)), d.cell);
        }));
        const btn = h("button", { type: "button", class: "adv-tap touch", "aria-expanded": "false", onclick: () => {
          const open = more.hidden; more.hidden = !open; btn.setAttribute("aria-expanded", open ? "true" : "false");
        } }, h("span", { class: "adv-name" }, r.name, h("span", { class: "adv-dist" }, ` ${r.distance} · ${a.bandLabel(a.HEADLINE_BAND)}`)), c.cell);
        const li = h("li", { class: "adv-row" }, btn, more);
        refs2[r.id].row = li; refs2[r.id].base = "adv-row";
        return li;
      })));
      return refs2;
    }
  };
  return a;
})();
