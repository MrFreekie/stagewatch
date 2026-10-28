// RME interfaces card: what to show (SW.rme.unitView, pure) and the card's DOM, built once per unit and
// updated in place. The server sends an "rme_units" message whenever something changes: per unit its
// name, model, a status word (OK / CHECK / WARNING / OFFLINE / Starting), core temperature, power
// supplies, clock, preset, AVB stream counts and the unit's own soundness list. Everything is shown as
// the unit reported it; nothing here is smoothed, guessed or sent back to the unit.
//
// Every state has a word and a symbol as well as colour. A unit that is offline keeps its last values on
// screen, dimmed and struck through, labelled with how long ago they were true: an old value is never
// shown as live. Text goes in with textContent only (names and words come from the unit).
"use strict";

SW.rme = (function () {
  const rme = {};

  rme.SYMBOLS = { tick: "✓", dash: "—", triangle: "▲", cross: "✖", circle: "●", question: "?" };
  const STATES = { ok: "ok", check: "check", warning: "warning", offline: "offline", starting: "starting" };
  const isNum = (x) => typeof x === "number" && isFinite(x);
  const text = (x, max) => (typeof x === "string" ? x.slice(0, max || 80) : "");

  // "a few seconds", "40 s", "5 min", "2 h 10 min". null (not known) -> "a while".
  rme.ageText = function (s) {
    if (!isNum(s) || s < 0) return "a while";
    if (s < 10) return "a few seconds";
    if (s < 60) return `${Math.round(s / 10) * 10} s`;
    if (s < 3600) return `${Math.round(s / 60)} min`;
    const m = Math.round(s / 60);
    return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
  };

  rme.fmtTemp = (t) => (isNum(t) ? `${SW.num(t, 1)} °C` : "—");

  // PSU words. psu is null (the unit did not say) or [{n, on}].
  rme.psuWords = function (psu) {
    return [1, 2].map((n) => {
      const p = Array.isArray(psu) ? psu.find((x) => x && x.n === n) : null;
      if (!p || typeof p.on !== "boolean") return { n, label: `PSU ${n}`, word: "n.a.", cls: "unknown" };
      return { n, label: `PSU ${n}`, word: p.on ? "On" : "Off", cls: p.on ? "on" : "off" };
    });
  };

  rme.clockText = function (c) {
    if (!c || typeof c !== "object" || !c.source_label) return "—";
    const parts = [text(c.source_label, 40)];
    if (c.locked === true) parts.push("Locked");
    else if (c.locked === false) parts.push("Not locked");
    else if (String(c.source_label).toLowerCase() !== "internal") parts.push("lock unknown");
    if (isNum(c.rate_hz) && c.rate_hz > 0) {
      const k = c.rate_hz / 1000;
      parts.push(`${SW.num(k, Number.isInteger(k) ? 0 : 1)} kHz`);
    }
    return parts.join(", ");
  };

  const presetName = (name, no) => (name ? text(name, 40) : isNum(no) ? `Preset ${no}` : "—");
  rme.presetText = function (p) {
    if (!p || typeof p !== "object") return { text: "—", changed: false };
    const a = presetName(p.active_name, p.active_no), l = presetName(p.loaded_name, p.loaded_no);
    return { text: a === l ? a : `Active ${a}, loaded ${l}`, changed: p.changed === true };
  };

  rme.avbText = function (a) {
    if (!a || !isNum(a.active) || !isNum(a.total)) return "—";
    return a.total === 0 ? "No streams" : `${a.active} of ${a.total} streams active`;
  };

  // The soundness list: what is not fine (anything that is not a tick or a grey dash), and everything.
  const soundItem = (s) => ({
    area: text(s && s.area, 40), word: text(s && s.word, 60), cause: text(s && s.cause, 120),
    symbol: SW.rme.SYMBOLS[s && s.symbol] || "?", kind: (s && s.symbol) || "question", level: isNum(s && s.level) ? s.level : 0,
  });
  rme.soundness = function (list) {
    const all = (Array.isArray(list) ? list : []).filter((s) => s && typeof s === "object").slice(0, 40).map(soundItem);
    return { all, notes: all.filter((s) => s.kind !== "tick" && s.kind !== "dash") };
  };

  // One unit as the card shows it. `u` is a unit from the "rme_units" message; `since` is how many seconds
  // ago the message arrived (the unit's own ages are as at that moment); `layout` is tablet, phone or wall.
  rme.unitView = function (u, since, layout) {
    u = u && typeof u === "object" ? u : {};
    const state = Object.prototype.hasOwnProperty.call(STATES, u.status) ? u.status : "starting";
    const wall = layout === "wall";
    const offline = state === "offline" || u.link_up === false && state !== "starting";
    const waiting = state === "starting";
    const v = {
      id: text(u.id, 40), name: text(u.name, 40) || "RME unit", model: text(u.model, 40),
      state, word: waiting ? "Starting" : text(u.status_word, 20) || state.toUpperCase(),
      symbol: rme.SYMBOLS[u.status_symbol] || "—", dim: false, ageNote: "", quiet: "",
    };
    v.cls = `rme-${state}`;
    const elapsed = isNum(since) && since > 0 ? since : 0;
    if (offline) {
      v.dim = true;
      v.word = "OFFLINE";
      v.symbol = rme.SYMBOLS.dash;
      v.ageNote = isNum(u.last_frame_age_s) ? `OFFLINE, last seen ${rme.ageText(u.last_frame_age_s + elapsed)} ago` : "OFFLINE, no data received";
    } else if (waiting) {
      v.ageNote = "Waiting for the unit's first status";
    } else if (isNum(u.no_updates_min) && u.no_updates_min > 0) {
      v.quiet = `No updates for ${u.no_updates_min} min`;
    }
    v.temp = waiting ? "—" : rme.fmtTemp(u.temp_c);
    v.psu = waiting ? rme.psuWords(null) : rme.psuWords(u.psu);
    v.psuText = v.psu.map((p) => `${p.label} ${p.word}`).join(", ");
    if (!wall) {
      v.clock = waiting ? "—" : rme.clockText(u.clock);
      const pr = rme.presetText(u.preset);
      v.preset = waiting ? "—" : pr.text;
      v.presetChanged = !waiting && pr.changed;
      v.avb = waiting ? "—" : rme.avbText(u.avb);
      const s = rme.soundness(waiting ? [] : u.soundness);
      v.notes = s.notes;
      v.checks = s.all;
      v.firmware = text(u.firmware, 30);
    }
    return v;
  };

  // The whole message: units in order, or [] for none (the card then hides). Any other shape counts as none.
  rme.units = function (m) {
    if (!m || typeof m !== "object" || !Array.isArray(m.units)) return [];
    return m.units.filter((u) => u && typeof u === "object").slice(0, 8);
  };

  // The card's own class: the worst unit decides the wall alarm edge (WARNING only).
  rme.cardClass = function (views) {
    return views.some((v) => v.state === "warning" && !v.dim) ? "rme-has-warning" : "";
  };

  // ---------------------------------------------------------------- the card
  const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
  const setClass = (el, c) => { if (el.className !== c) el.className = c; };
  const setHidden = (el, hide) => { if (el.hidden !== hide) el.hidden = hide; };

  rme.createTile = function () {
    const h = SW.h;
    const t = {
      name: h("span", { class: "rme-name" }), model: h("span", { class: "rme-model" }),
      sym: h("span", { class: "rme-sym", "aria-hidden": "true" }), word: h("span", { class: "rme-word" }),
      temp: h("div", { class: "rme-temp" }),
      psu1: h("span", { class: "rme-psu" }), psu2: h("span", { class: "rme-psu" }),
      clock: h("span", {}), preset: h("span", {}), presetFlag: h("span", { class: "rme-flag", hidden: true }, "▲ Preset changed"),
      avb: h("span", {}), age: h("p", { class: "rme-age", role: "status" }), quiet: h("p", { class: "rme-quiet" }),
      notes: h("ul", { class: "rme-notes" }), checks: h("ul", { class: "rme-checks" }), fw: h("p", { class: "rme-fw muted" }),
    };
    const row = (label, ...kids) => h("div", { class: "rme-row" }, h("span", { class: "rme-label" }, label), h("span", { class: "rme-val" }, ...kids));
    t.rowClock = row("Clock", t.clock);
    t.rowPreset = row("Preset", t.preset, " ", t.presetFlag);
    t.rowAvb = row("AVB", t.avb);
    t.more = h("details", { class: "rme-more" }, h("summary", {}, "All checks"), t.checks, t.fw);
    t.el = h("article", { class: "rme-tile" },
      h("div", { class: "rme-head" }, h("div", { class: "rme-title" }, t.name, t.model), h("div", { class: "rme-status", role: "status" }, t.sym, t.word)),
      t.temp,
      h("div", { class: "rme-psus" }, t.psu1, t.psu2),
      t.rowClock, t.rowPreset, t.rowAvb, t.notes, t.age, t.quiet, t.more);
    t.update = function (v) {
      setClass(t.el, `rme-tile ${v.cls}${v.dim ? " rme-dim" : ""}`);
      setText(t.name, v.name);
      setText(t.model, v.model);
      setHidden(t.model, !v.model);
      setText(t.sym, v.symbol);
      setText(t.word, v.word);
      setText(t.temp, v.temp);
      [t.psu1, t.psu2].forEach((el, i) => {
        const p = v.psu[i];
        setClass(el, `rme-psu rme-psu-${p.cls}`);
        setText(el, `${p.label} ${p.word}`);
      });
      const detail = v.clock !== undefined;   // the wall shows less
      [t.rowClock, t.rowPreset, t.rowAvb, t.notes, t.more].forEach((el) => setHidden(el, !detail));
      if (detail) {
        setText(t.clock, v.clock);
        setText(t.preset, v.preset);
        setHidden(t.presetFlag, !v.presetChanged);
        setText(t.avb, v.avb);
        fillList(t.notes, v.notes, true);
        fillList(t.checks, v.checks, false);
        setText(t.fw, v.firmware ? `Firmware ${v.firmware}` : "");
        setHidden(t.more, !v.checks.length && !v.firmware);
      }
      setText(t.age, v.ageNote);
      SW.slot(t.age, !v.ageNote);
      setText(t.quiet, v.quiet);
      SW.slot(t.quiet, !v.quiet);
    };
    return t;
  };

  function fillList(ul, items, withCause) {
    const sig = items.map((s) => `${s.area}|${s.symbol}|${s.word}|${s.cause}`).join("\n");
    if (ul.dataset && ul.dataset.sig === sig) return;
    if (ul.dataset) ul.dataset.sig = sig;
    const h = SW.h;
    ul.replaceChildren(...items.map((s) => h("li", { class: `rme-item rme-k-${s.kind}` },
      h("span", { class: "rme-isym", "aria-hidden": "true" }, s.symbol), " ",
      h("strong", {}, s.word), ` · ${s.area}`, withCause && s.cause ? h("span", { class: "muted" }, ` · ${s.cause}`) : null)));
  }

  // Builds the card body once and returns {nodes, update(units, since, layout) -> views}. Tiles are kept
  // by unit id and only added or removed when the list of units changes.
  rme.createUi = function () {
    const h = SW.h;
    const grid = h("div", { class: "rme-grid" });
    const empty = h("p", { class: "muted rme-empty", hidden: true }, "No RME units set up. Add one in Admin, on the Integrations tab.");
    const foot = h("p", { class: "muted rme-foot" }, "Reads the unit; never changes it. Not a protection system.");
    const ui = { nodes: [grid, empty, foot], tiles: new Map() };
    ui.update = function (units, since, layout) {
      const list = rme.units({ units });
      const keep = new Set(list.map((u) => text(u.id, 40)));
      for (const id of [...ui.tiles.keys()]) if (!keep.has(id)) ui.tiles.delete(id);
      const views = [];
      const order = [];
      list.forEach((u) => {
        const v = rme.unitView(u, since, layout);
        let t = ui.tiles.get(v.id);
        if (!t) { t = rme.createTile(); ui.tiles.set(v.id, t); }
        t.update(v);
        order.push(t.el);
        views.push(v);
      });
      const same = grid.children.length === order.length && order.every((el, i) => grid.children[i] === el);
      if (!same) grid.replaceChildren(...order);
      grid.setAttribute("data-n", String(order.length));
      setHidden(empty, order.length > 0);
      setHidden(foot, order.length === 0);
      return views;
    };
    return ui;
  };

  return rme;
})();
