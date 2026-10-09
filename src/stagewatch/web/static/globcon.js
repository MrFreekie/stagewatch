// GLOBCON levels card: what to show (SW.gc.view, pure) and the card's DOM, built once and updated in
// place. The server sends a "globcon_meters" message up to four times a second (GLOBCON itself sends
// ten): per controller its name, the current layer's label, and the first strips that have a level,
// each with its dB level exactly as GLOBCON reported it. Nothing is averaged, smoothed, peak-held or
// converted here, and the card makes no claim about peak or RMS. GLOBCON's "no signal" (-250) arrives
// as null and is drawn as an empty bar with a dash, never as zero.
//
// Every state has words and a symbol as well as colour. If the levels stop arriving, the last ones stay
// on screen, dimmed and struck through, with how old they are. Nothing here sounds, raises an alarm or
// sends anything to GLOBCON. Text goes in with textContent only.
"use strict";

SW.gc = (function () {
  const gc = {};

  gc.STALE_S = 3;            // GLOBCON sends about ten a second: this long without one is frozen
  gc.FLOOR_DB = -72;         // the bar runs from here to 0 dB, as GLOBCON's own web app draws it
  gc.CEIL_DB = 0;
  gc.HIGH_DB = -9;           // display guide only: a bar above this gets one triangle
  gc.HOTTER_DB = -3;         // ... and above this two. Not a claim about clipping or headroom.

  const isNum = (x) => typeof x === "number" && isFinite(x);

  // Bar height as a percentage, or 0 for no level. Clamped to the scale.
  gc.pct = function (db) {
    if (!isNum(db)) return 0;
    return Math.max(0, Math.min(100, ((db - gc.FLOOR_DB) / (gc.CEIL_DB - gc.FLOOR_DB)) * 100));
  };

  // The level as GLOBCON gave it: whole numbers stay whole, anything else to one decimal. null -> a dash.
  gc.fmtDb = function (db) {
    if (!isNum(db)) return "—";
    return Number.isInteger(db) ? String(db) : db.toFixed(1);
  };

  // "" (normal), "high" (one triangle) or "hot" (two). Symbol and words go with the colour.
  gc.band = function (db) {
    if (!isNum(db)) return "";
    if (db >= gc.HOTTER_DB) return "hot";
    if (db >= gc.HIGH_DB) return "high";
    return "";
  };
  gc.BAND_MARK = { "": "", high: "▲", hot: "▲▲" };
  gc.BAND_WORD = { "": "", high: "high level", hot: "very high level" };

  const ageText = (a) => (a < 10 ? "a few seconds" : a < 60 ? `${Math.round(a / 10) * 10} s` : `${Math.round(a / 60)} min`);

  // The controller block for this dashboard (opts.controller is 1 to 16), or null.
  gc.pick = function (m, controller) {
    if (!m || !Array.isArray(m.controllers)) return null;
    for (const c of m.controllers) if (c && c.controller === controller) return c;
    return null;
  };

  // The one place that decides what the card says. m is the "globcon_meters" message, opts is the
  // dashboard's {controller, strips}, now the server-corrected time in seconds. Returns:
  //   state   "ok" | "frozen" | "waiting" | "offline" | "locked"
  //   title   the controller's name (or "Controller N");  layer  the layer label as GLOBCON reports it
  //   badge   words and symbol for the state;  note  a sentence for the odd states ("" when fine)
  //   strips  [{label, db, text, pct, band, mark, word}] (always opts.strips entries when GLOBCON has them)
  //   cls     the card's class
  gc.view = function (m, opts, now) {
    const want = Math.max(1, Math.min(8, (opts && opts.strips) || 8));
    const num = (opts && opts.controller) || 1;
    const v = { state: "waiting", title: `Controller ${num}`, layer: "", badge: "… WAITING", note: "", strips: [], cls: "gc-s-waiting", ago: "" };
    const name = (m && m.label) || "GLOBCON";
    const c = gc.pick(m, num);
    if (!m || !c) {
      if (m && m.status === "offline") { v.state = "offline"; v.cls = "gc-s-offline"; v.badge = "▲ OFFLINE"; v.note = `▲ ${name} is not connected. No levels to show.`; }
      else v.note = `Waiting for ${name}…`;
      return v;
    }
    if (c.name) v.title = c.name;
    v.layer = c.layer_label || (typeof c.layer === "number" ? `Layer ${c.layer + 1}` : "");
    if (c.locked) {
      v.state = "locked"; v.cls = "gc-s-locked"; v.badge = "▲ PASSWORD NEEDED";
      v.note = `▲ ${name} wants a password for this controller. Enter it in Admin.`;
      return v;
    }
    const strips = Array.isArray(c.strips) ? c.strips.slice(0, want) : [];
    const age = isNum(c.meters_at) ? Math.max(0, now - c.meters_at) : null;
    const frozen = age !== null && (age > gc.STALE_S || m.status === "offline");
    v.strips = strips.map((s) => {
      const db = isNum(s.db) ? s.db : null;
      const band = frozen ? "" : gc.band(db);
      return { label: typeof s.label === "string" ? s.label : "", db, text: gc.fmtDb(db), pct: gc.pct(db), band, mark: gc.BAND_MARK[band], word: gc.BAND_WORD[band] };
    });
    if (age === null) {
      v.note = m.status === "offline" ? `▲ ${name} is not connected. No levels to show.` : `Connected to ${name}, waiting for levels…`;
      if (m.status === "offline") { v.state = "offline"; v.cls = "gc-s-offline"; v.badge = "▲ OFFLINE"; }
      return v;
    }
    if (frozen) {
      v.state = "frozen"; v.cls = "gc-s-frozen"; v.badge = "▲ FROZEN"; v.ago = ageText(age);
      v.note = `▲ FROZEN: no new levels from ${name} for ${v.ago}. These are the last levels received.`;
      return v;
    }
    v.state = "ok"; v.cls = "gc-s-ok"; v.badge = "● LIVE";
    if (!strips.length) v.note = "GLOBCON reports no strips with a level on this controller.";
    return v;
  };

  // ---------------------------------------------------------------- the card
  const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
  const setClass = (el, c) => { if (el.className !== c) el.className = c; };
  const setHidden = (el, hide) => { if (el.hidden !== hide) el.hidden = hide; };

  // Builds the card's content once (everything after the card's own <h2>) and returns {nodes, head, update(view)}.
  // `head` is the <span> for the title in the <h2>. Strip columns are rebuilt only when their number changes.
  gc.createUi = function () {
    const h = SW.h;
    const ui = {
      head: h("span", { class: "gc-title" }),
      layer: h("span", { class: "gc-layer" }),
      badge: h("span", { class: "gc-badge", role: "status" }),
      strips: h("div", { class: "gc-strips" }),
      note: h("p", { class: "gc-note", hidden: true, role: "status" }),
      foot: h("p", { class: "gc-foot muted" }, "Levels in dB as reported by GLOBCON. Bars run from -72 to 0 dB. ▲ above -9 dB, ▲▲ above -3 dB: a display guide only. Read-only."),
    };
    let cols = [];
    const build = (n) => {
      cols = [];
      for (let i = 0; i < n; i++) {
        const fill = h("div", { class: "gc-fill" });
        const col = {
          label: h("div", { class: "gc-label" }),
          fill,
          bar: h("div", { class: "gc-bar", "aria-hidden": "true" }, fill),
          value: h("div", { class: "gc-value" }),
          mark: h("span", { class: "gc-mark" }),
        };
        col.el = h("div", { class: "gc-col" }, col.label, col.bar, h("div", { class: "gc-reading" }, col.value, col.mark));
        cols.push(col);
      }
      ui.strips.replaceChildren(...cols.map((c) => c.el));
      ui.strips.setAttribute("data-n", String(n));
    };
    ui.nodes = [h("div", { class: "gc-top" }, ui.layer, ui.badge), ui.strips, ui.note, ui.foot];
    ui.update = function (v) {
      setText(ui.head, v.title);
      setText(ui.layer, v.layer ? `Layer: ${v.layer}` : "");
      setHidden(ui.layer, !v.layer);
      setText(ui.badge, v.badge);
      if (cols.length !== v.strips.length) build(v.strips.length);
      v.strips.forEach((s, i) => {
        const c = cols[i];
        setText(c.label, s.label || `Strip ${i + 1}`);
        setText(c.value, s.text);
        setText(c.mark, s.mark);
        c.mark.setAttribute("title", s.word);
        setClass(c.el, `gc-col${s.band ? ` gc-${s.band}` : ""}${s.db === null ? " gc-none" : ""}`);
        const h2 = `${s.pct.toFixed(0)}%`;
        if (c.fill.style.height !== h2) c.fill.style.height = h2;
      });
      setHidden(ui.strips, v.strips.length === 0);
      setText(ui.note, v.note);
      setHidden(ui.note, !v.note);
    };
    return ui;
  };

  return gc;
})();
