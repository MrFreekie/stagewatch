// GLOBCON levels card: what to show (SW.gc.view, pure) and the card's DOM, built once and updated in
// place. The server sends a "globcon_meters" message up to four times a second (GLOBCON itself sends
// ten): per controller its name, the current layer's label, and every strip GLOBCON has told us about
// (index, label, whether it has a level meter, and its dB level exactly as GLOBCON reported it). The
// dashboard's channel group (opts.range, e.g. "9-16") picks the strips to draw, by index and in order. Nothing is averaged, smoothed, peak-held or
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

  // The channel groups, strip numbers as on the controller (1-based). Same list as core/config.py GLOBCON_RANGES.
  gc.RANGES = { "1-4": [1, 4], "5-8": [5, 8], "1-8": [1, 8], "9-12": [9, 12], "13-16": [13, 16], "9-16": [9, 16], "1-16": [1, 16] };

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
    const range = opts && typeof opts.range === "string" && Object.prototype.hasOwnProperty.call(gc.RANGES, opts.range) ? gc.RANGES[opts.range] : null;
    const num = (opts && opts.controller) || 1;
    const v = { state: "waiting", title: `Controller ${num}`, layer: "", badge: "… WAITING", note: "", strips: [], cls: "gc-s-waiting", ago: "" };
    const chan = range ? `Channels ${range[0]}-${range[1]}` : "Channels with a level";
    // The subtitle: which controller, which layer (both as GLOBCON reports them now) and which channels this card shows.
    const sub = () => [v.title, v.layer, chan].filter(Boolean).join(" - ");
    v.sub = sub();
    const name = (m && m.label) || "GLOBCON";
    const c = gc.pick(m, num);
    if (!m || !c) {
      if (m && m.status === "offline") { v.state = "offline"; v.cls = "gc-s-offline"; v.badge = ""; }
      else v.note = `Waiting for ${name}…`;
      return v;
    }
    if (c.name) v.title = c.name;
    v.layer = c.layer_label || (typeof c.layer === "number" ? `Layer ${c.layer + 1}` : "");
    v.sub = sub();
    if (c.locked) {
      v.state = "locked"; v.cls = "gc-s-locked"; v.badge = "▲ PASSWORD NEEDED";
      v.note = `▲ ${name} wants a password for this controller. Enter it in Admin.`;
      return v;
    }
    const all = Array.isArray(c.strips) ? c.strips.filter((s) => s && typeof s === "object") : [];
    let strips;
    if (range) {
      // Every strip of the group, in order, whether or not GLOBCON gave it a meter; one it has not told us about is blank.
      strips = [];
      for (let n = range[0]; n <= range[1]; n++) {
        const f = all.find((s) => s.index === n - 1);
        strips.push(f ? Object.assign({}, f, { num: n }) : { index: n - 1, num: n, label: "", meter: null, db: null });
      }
    } else {
      // Older setting: the first strips GLOBCON says have a level.
      strips = all.filter((s) => s.meter === true).slice(0, want).map((s) => Object.assign({}, s, { num: isNum(s.index) ? s.index + 1 : 0 }));
    }
    const age = isNum(c.meters_at) ? Math.max(0, now - c.meters_at) : null;
    const frozen = age !== null && (age > gc.STALE_S || m.status === "offline");
    v.strips = strips.map((s) => {
      const db = isNum(s.db) ? s.db : null;
      const band = frozen ? "" : gc.band(db);
      // With a channel group chosen, a strip with no level shows a plain word, never a zero.
      const word = range && db === null ? (s.meter === false ? "no meter" : "no reading") : "";
      return { num: s.num, label: typeof s.label === "string" ? s.label : "", db, text: word || gc.fmtDb(db), isWord: !!word, mute: s.mute === true, pct: gc.pct(db), band, mark: gc.BAND_MARK[band], word: gc.BAND_WORD[band] };
    });
    if (age === null) {
      v.note = m.status === "offline" ? "" : `Connected to ${name}, waiting for levels…`;
      if (m.status === "offline") { v.state = "offline"; v.cls = "gc-s-offline"; v.badge = ""; }
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

  // ---------------------------------------------------------------- Lights mode
  // One big square per chosen channel. The same fixed lists as core/config.py GLOBCON_LIGHT_COLOURS / _ICONS; the
  // colours themselves are in style.css (.gc-c-<name>), each with text chosen for contrast (tests/test_globcon_lights.py).
  gc.LIGHT_COLOURS = ["green", "amber", "red", "blue", "cyan", "magenta", "white", "orange"];
  gc.LIGHT_ICONS = ["microphone", "speaker", "timecode", "play", "megaphone", "camera", "headset", "radio", "signal"];
  gc.HYST_DB = 2;            // a light that is on stays on until the level is this far under its threshold
  gc.LIGHT_DEFAULT = { threshold_db: -40, colour: "green", icon: "microphone", hold_s: 1 };

  // Icon drawings on a 24 x 24 grid: [path data, filled?]. Built with DOM methods in gc.iconSvg.
  const ICON_PATHS = {
    microphone: [["M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z", 0], ["M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6", 0]],
    speaker: [["M4 9v6h4l5 4V5L8 9H4z", 0], ["M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11", 0]],
    timecode: [["M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0", 0], ["M12 7v5l3 2", 0]],
    play: [["M8 5v14l11-7z", 1]],
    megaphone: [["M3 10h4l9-5v14l-9-5H3z", 0], ["M7 15l1.5 5h3L10 14.5M19 9v6", 0]],
    camera: [["M3 7h4l2-2h6l2 2h4v12H3z", 0], ["M8 13a4 4 0 1 0 8 0a4 4 0 1 0-8 0", 0]],
    headset: [["M4 14v-2a8 8 0 0 1 16 0v2", 0], ["M4 14h3v5H4zM17 14h3v5h-3z", 0], ["M20 19c0 2-2 2-5 2", 0]],
    radio: [["M6 8h9v13H6z", 0], ["M8.5 8V3M9 12h4M9 15h4M15 11h2.5v4H15", 0]],
    signal: [["M4 20v-4M9 20v-8M14 20V8M19 20V4", 0]],
  };
  gc.iconSvg = function (name) {
    const paths = ICON_PATHS[name] || ICON_PATHS.signal;
    return SW.svg("svg", { viewBox: "0 0 24 24", class: "gc-icon", "aria-hidden": "true", focusable: "false", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" },
      paths.map((p) => SW.svg("path", p[1] ? { d: p[0], fill: "currentColor" } : { d: p[0] })));
  };

  const inList = (list, x) => typeof x === "string" && list.indexOf(x) >= 0;
  // The lights as saved for this dashboard, cleaned: only channels 1 to 16 once each, known colour and icon, sane numbers.
  gc.cleanLights = function (list) {
    const out = [], seen = {};
    if (!Array.isArray(list)) return out;
    for (const e of list) {
      if (!e || typeof e !== "object" || !Number.isInteger(e.strip) || e.strip < 1 || e.strip > 16 || seen[e.strip]) continue;
      seen[e.strip] = true;
      const t = isNum(e.threshold_db) ? Math.max(-72, Math.min(0, e.threshold_db)) : gc.LIGHT_DEFAULT.threshold_db;
      const hs = isNum(e.hold_s) ? Math.max(0, Math.min(10, e.hold_s)) : gc.LIGHT_DEFAULT.hold_s;
      out.push({ strip: e.strip, threshold_db: t, colour: inList(gc.LIGHT_COLOURS, e.colour) ? e.colour : gc.LIGHT_DEFAULT.colour,
        icon: inList(gc.LIGHT_ICONS, e.icon) ? e.icon : gc.LIGHT_DEFAULT.icon, hold_s: hs });
    }
    return out;
  };

  // The one place that decides which squares are on. view is gc.view's result, cfg the dashboard's globcon options
  // ({mode, lights}), prev the state returned last time (null at the start) and now the server-corrected time in
  // seconds. Pure: prev is never changed. A square is
  //   on     the level is at or above its threshold; it stays on until the level is HYST_DB under it (so a level
  //          sitting on the line does not chatter), and then for its hold time (0 = instant) after the last moment
  //          it was at or above that lower line;
  //   off    anything else. GLOBCON's "no signal" (-250 arrives as null) and a missing reading count as below;
  //   nodata the levels have stopped (frozen, offline, waiting): the square keeps what it last showed but is hatched
  //          and says "no data" and how old. It is never turned dark, because dark would say "quiet", which nobody knows.
  // Returns {state, squares: [{num, name, tip, colour, icon, status, on, mark, word, ago}], note}. Nothing is averaged,
  // smoothed or converted: the thresholds are in dB exactly as GLOBCON reports them.
  gc.lights = function (view, cfg, prev, now) {
    const lights = gc.cleanLights(cfg && cfg.lights);
    const live = !!view && view.state === "ok";
    const p = (prev && prev.by) || {};
    const by = {};
    const squares = lights.map((L) => {
      const s = view && Array.isArray(view.strips) ? view.strips.find((x) => x.num === L.strip) : null;
      const before = p[L.strip] || { on: false, above: null, seen: false };
      const st = { on: before.on, above: before.above, seen: before.seen };
      const name = s && s.label ? s.label : `Channel ${L.strip}`;
      const sq = { num: L.strip, name, tip: `${name} (channel ${L.strip}): on at ${gc.fmtDb(L.threshold_db)} dB or louder, as GLOBCON reports it`, colour: L.colour, icon: L.icon, status: "off", on: false, mark: "", word: "", ago: "" };
      if (live) {
        const db = s && isNum(s.db) ? s.db : null;
        if (db !== null && db >= L.threshold_db) { st.on = true; st.above = now; }
        else if (st.on && db !== null && db >= L.threshold_db - gc.HYST_DB) { st.above = now; }
        else if (st.on && !(L.hold_s > 0 && st.above !== null && now - st.above < L.hold_s)) { st.on = false; st.above = null; }
        st.seen = true;
        sq.status = st.on ? "on" : "off";
      } else {
        sq.status = "nodata"; sq.word = "no data"; sq.ago = view && view.ago ? view.ago : "";
        if (!st.seen) st.on = false;
      }
      sq.on = st.on;
      by[L.strip] = st;
      return sq;
    });
    let note = "";
    if (!lights.length) note = "No channels are switched on for Lights. Choose them in Admin, under User dashboards, Edit cards.";
    else if (!live && view && view.note) note = view.note;
    return { state: { by }, squares, note };
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
      lights: h("div", { class: "gc-lights", hidden: true }),
      note: h("p", { class: "gc-note", hidden: true, role: "status" }),
    };
    let cols = [];
    const build = (n) => {
      cols = [];
      for (let i = 0; i < n; i++) {
        const fill = h("div", { class: "gc-fill" });
        const col = {
          num: h("span", { class: "gc-num" }),
          name: h("span", { class: "gc-name" }),
          label: null,
          fill,
          mutetag: h("span", { class: "gc-mutetag", hidden: true }, "MUTED"),
          bar: null,
          value: h("div", { class: "gc-value" }),
          mark: h("span", { class: "gc-mark" }),
        };
        col.bar = h("div", { class: "gc-bar", "aria-hidden": "true" }, fill, col.mutetag);
        col.label = h("div", { class: "gc-label" }, col.num, col.name);
        col.el = h("div", { class: "gc-col" }, col.label, col.bar, h("div", { class: "gc-reading" }, col.value));
        cols.push(col);
      }
      ui.strips.replaceChildren(...cols.map((c) => c.el));
      ui.strips.setAttribute("data-n", String(n));
    };
    // Lights mode: one square per light, rebuilt only when the number of lights changes, then updated in place.
    let boxes = [];
    const buildLights = (n) => {
      boxes = [];
      for (let i = 0; i < n; i++) {
        const b = { icon: h("span", { class: "gc-licon" }), name: h("span", { class: "gc-lname" }), state: h("span", { class: "gc-lstate" }), ago: h("span", { class: "gc-lago" }), iconName: "" };
        b.el = h("div", { class: "gc-light", role: "group" }, h("div", { class: "gc-lin" }, b.icon, b.name, b.state, b.ago));
        boxes.push(b);
      }
      ui.lights.replaceChildren(...boxes.map((b) => b.el));
      ui.lights.setAttribute("data-n", String(n));
    };
    const aboutLights = SW.about(h("p", { class: "muted" }, "Each square is on when its channel is at or above its threshold. It stays on until the level has dropped 2 dB below the threshold, and then for its hold time. Thresholds are in dB exactly as GLOBCON reports them; nothing is averaged or smoothed. If the levels stop arriving, the squares show their last state hatched, with \"no data\" and how old it is, and never go dark."));
    ui.nodes = [h("div", { class: "gc-top" }, ui.layer, ui.badge), ui.strips, ui.lights, ui.note, aboutLights];
    // L is gc.lights' result in Lights mode, or null for the meters.
    ui.update = function (v, L) {
      setHidden(ui.lights, !L);
      setHidden(aboutLights, !L);
      if (L) {
        setText(ui.head, "DirectOut GLOBCON");
        setText(ui.layer, v.sub || v.title);
        setHidden(ui.layer, false);
        setText(ui.badge, v.badge);
        setHidden(ui.strips, true);
        if (boxes.length !== L.squares.length) buildLights(L.squares.length);
        L.squares.forEach((s, i) => {
          const b = boxes[i];
          setClass(b.el, `gc-light gc-c-${s.colour} gc-l-${s.status}`);
          if (b.iconName !== s.icon) { b.iconName = s.icon; b.icon.replaceChildren(gc.iconSvg(s.icon)); }
          setText(b.name, s.name);
          b.el.setAttribute("title", s.tip);
          setText(b.state, s.word);
          setText(b.ago, s.ago ? `${s.ago} old` : "");
          const label = `${s.name}: ${s.word || s.status}${s.ago ? `, ${s.ago} old` : ""}`;
          if (b.el.getAttribute("aria-label") !== label) b.el.setAttribute("aria-label", label);
        });
        setText(ui.note, L.note);
        if (ui.note.dataset) ui.note.dataset.slot = "";
        setHidden(ui.note, !L.note);
        return;
      }
      setText(ui.head, "DirectOut GLOBCON");
      setText(ui.layer, v.sub || v.title);
      setHidden(ui.layer, false);
      setText(ui.badge, v.badge);
      if (cols.length !== v.strips.length) build(v.strips.length);
      v.strips.forEach((s, i) => {
        const c = cols[i];
        setText(c.num, s.num ? String(s.num) : "");
        setText(c.name, s.label);
        setText(c.value, s.text);
        setText(c.mark, s.mark);
        c.mark.setAttribute("title", s.word);
        setClass(c.el, `gc-col${s.band ? ` gc-${s.band}` : ""}${s.db === null ? " gc-none" : ""}${s.isWord ? " gc-word" : ""}${s.mute ? " gc-muted" : ""}`);
        setHidden(c.mutetag, !s.mute);
        const h2 = `${s.pct.toFixed(0)}%`;
        if (c.fill.style.height !== h2) c.fill.style.height = h2;
      });
      setHidden(ui.strips, v.strips.length === 0);
      setText(ui.note, v.note);
      if (ui.note.dataset) ui.note.dataset.slot = "";   // the note line keeps its space: the card keeps its size
      setHidden(ui.note, !v.note);
    };
    return ui;
  };

  return gc;
})();
