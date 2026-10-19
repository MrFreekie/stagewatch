// Power card: watts, volts, amps, frequency and energy from power meters (Shelly), as the meters
// reported them. SW.pw.view works out what to show (pure, no DOM); SW.pw.build makes the card's DOM
// once and updates it in place. Readings are Equipment readings: they are never averaged, smoothed or
// part of any site value. Energy is the meter's running total (Wh), shown as Wh or kWh and never as a
// level. Apparent power and power factor appear only when a meter sends them.
//
// This show day's figures (highest power and current with the time, lowest and highest voltage, energy
// used since the first reading) come from the server and are plain numbers; they dim with the rest
// of the meter when it stops answering.
//
// Every state has words as well as colour. If a meter stops answering, its last readings stay on screen,
// dimmed, marked "NO READING" and with how old they are; a missing value is a dash, never 0. The card
// reads and shows only: it never switches or changes a meter. Text goes in with textContent only.
"use strict";

SW.pw = (function () {
  const pw = {};
  const isNum = (x) => typeof x === "number" && isFinite(x);

  // "pe" (protective earth / earth leakage current) is reserved: no meter reads it yet, so nothing is drawn
  // for it, but the name and the "Neutral and earth" group below are ready for when one does.
  pw.GROUP_ORDER = ["", "a", "b", "c", "n", "pe", "total", "l12", "l23", "l31", "avg"];
  // Line-to-line voltage groups (from meters that report them): shown as swing meters and rows, never in the phase table.
  pw.LL = ["l12", "l23", "l31", "avg"];
  pw.GROUP_WORD = { "": "", a: "Line 1 (Brown)", b: "Line 2 (Black)", c: "Line 3 (Grey)", n: "Neutral (Blue)", pe: "Earth leakage (PE)", total: "Total",
    l12: "Line 1 to Line 2 (Brown-Black)", l23: "Line 2 to Line 3 (Black-Grey)", l31: "Line 3 to Line 1 (Grey-Brown)", avg: "Average" };
  // The line a single-phase meter was assigned in Admin.
  pw.PHASE_WORD = { L1: "Line 1 (Brown)", L2: "Line 2 (Black)", L3: "Line 3 (Grey)" };
  // What to call a circuit: its label, its line, or "Channel N" when the meter has several.
  pw.channelTitle = function (ch, many) {
    const cfg = ch.cfg || {};
    const m = /_(\d+)$/.exec(ch.id);
    const parts = [cfg.label || (many && m ? `Channel ${+m[1] + 1}` : ""), pw.PHASE_WORD[cfg.phase] || ""].filter(Boolean);
    return parts.join(" · ");
  };
  pw.FIELD_WORD = { voltage_ll: "Line-to-line voltage", reactive: "Reactive power", thd_v: "Voltage THD", thd_i: "Current THD", unbalance: "Phase unbalance", demand: "Average demand", demand_va: "Average demand (VA)", voltage: "Voltage", current: "Current", power: "Power", apparent: "Apparent power", pf: "Power factor", freq: "Frequency", device_temp: "Meter temperature" };
  pw.UNIT = { voltage_ll: "V", reactive: "var", thd_v: "%", thd_i: "%", unbalance: "%", demand: "W", demand_va: "VA", voltage: "V", current: "A", power: "W", apparent: "VA", pf: "", freq: "Hz", device_temp: "°C" };

  // Power and energy are shown with a comma for thousands and a full stop for decimals.
  pw.fmt = function (e) {
    if (!e || !isNum(e.value)) return "—";
    return SW.num(e.value, Math.max(0, Math.min(3, e.decimals || 0)));
  };

  // Apparent power: VA below 1,000, kVA from 1,000 VA. A value is shown as given; "—" with no value.
  pw.fmtVA = function (v) {
    if (!isNum(v)) return { text: "—", unit: "VA" };
    if (Math.abs(v) < 1000) return { text: SW.num(v, 1), unit: "VA" };
    return { text: SW.num(v / 1000, 2), unit: "kVA" };
  };
  // Reactive power: var below 1,000, kVAr from 1,000 var. "—" with no value.
  pw.fmtVar = function (v) {
    if (!isNum(v)) return { text: "—", unit: "var" };
    if (Math.abs(v) < 1000) return { text: SW.num(v, 1), unit: "var" };
    return { text: SW.num(v / 1000, 2), unit: "kVAr" };
  };
  // Real power for demand figures: W below 1,000, kW from 1,000 W.
  pw.fmtW = function (v) {
    if (!isNum(v)) return { text: "—", unit: "W" };
    if (Math.abs(v) < 1000) return { text: SW.num(v, 0), unit: "W" };
    return { text: SW.num(v / 1000, 2), unit: "kW" };
  };
  // Words for a reading that Stagewatch worked out itself (labels.calculated says how): never passed off as the meter's own.
  // The calculated neutral current says when it is only an estimate (it assumed the same power factor on every line).
  pw.calcWord = (e) => (e && e.labels && e.labels.calculated ? (/^estimate/.test(e.labels.calculated) ? " (calculated estimate, assumes equal power factor)" : " (calculated)") : "");

  // The meter's running total: Wh below 1,000, kWh above. "—" with no value.
  pw.fmtEnergy = function (wh) {
    if (!isNum(wh)) return { text: "—", unit: "" };
    if (Math.abs(wh) < 1000) return { text: SW.num(wh, 1), unit: "Wh" };
    return { text: SW.num(wh / 1000, 2), unit: "kWh" };
  };

  // ---- this show day's figures (the server works them out from the readings it received; see
  // integrations/shelly/dayfigures.py). A missing figure is a dash, never 0.
  const hhmm = (ts) => SW.fmtTime(ts);
  const withUnit = (v, e) => `${SW.num(v, Math.max(0, Math.min(3, e.decimals || 0)))} ${e.unit || ""}`.trim();
  pw.DAY_WORD = { power: "Highest power", current: "Highest current", thd_v: "Highest voltage THD", thd_i: "Highest current THD" };

  // The text of one figure row for entity e ("Highest power"/"Highest current"/"Voltage"), or a dash.
  pw.dayText = function (e, field) {
    const d = e && e.day;
    if (!d) return "—";
    if (field === "voltage" || field === "freq" || field === "voltage_ll") {
      if (!isNum(d.min) || !isNum(d.max)) return "—";
      return `low ${withUnit(d.min, e)} at ${hhmm(d.min_at)}, high ${withUnit(d.max, e)} at ${hhmm(d.max_at)}`;
    }
    return isNum(d.max) ? `${withUnit(d.max, e)} at ${hhmm(d.max_at)}` : "—";
  };

  // Energy used this show day: the meter's own counter now, minus its counter at the first reading.
  pw.dayEnergy = function (e) {
    const d = e && e.day;
    if (!d || !isNum(d.wh)) return { text: "—", unit: "", sub: "" };
    const t = pw.fmtEnergy(d.wh);
    let sub = ` (meter counter, since ${hhmm(d.since)})`;
    if (isNum(d.went_back_at)) sub += `. The counter went back at ${hhmm(d.went_back_at)} (meter reset): energy used around then is not counted.`;
    return { text: t.text, unit: t.unit, sub };
  };

  // Times Stagewatch was not reading this show day, from whichever reading of the meter has the most.
  pw.gapText = function (ents) {
    let gaps = [];
    for (const e of ents) if (e.day && e.day.gaps && e.day.gaps.length > gaps.length) gaps = e.day.gaps;
    if (!gaps.length) return "";
    return "Not reading: " + gaps.map((g) => `${hhmm(g[0])} to ${hhmm(g[1])}`).join(", ") + ". Highest and lowest figures miss these times.";
  };

  // The show day the figures belong to, or "" when none has arrived.
  pw.dayOf = function (ents) {
    for (const e of ents) if (e.day && e.day.day) return e.day.day;
    return "";
  };

  // ---- load bar and swing meters (UK voltage and frequency). Advisory: the rating is what the admin typed in.
  pw.LOAD_HIGH = 80; pw.LOAD_OVER = 100;
  // How a swing meter judges a reading: red outside [low, high], amber outside [nearLow, nearHigh],
  // and "not judged" outside [judged]. Voltage: UK 230 V +10 % / -6 % = 216.2 to 253.0 V (ESQCR 2002).
  // Frequency: 49.5 to 50.5 Hz statutory, 49.8 to 50.2 Hz the grid's normal band.
  pw.SWING = {
    voltage: { word: "Voltage", unit: "V", dec: 1, scale: [195, 265], low: 216.2, high: 253.0, nearLow: 216.2, nearHigh: 253.0, judged: [180, 1e9],
      note: "UK range 230 V +10% / -6% (advisory)", na: "Not a 230 V supply: not judged" },
    // Line-to-line, 400 V supply: 230 V -6%/+10% on 400 V = 376 to 440 V (our own arithmetic, a typical band).
    voltage_ll: { word: "voltage", unit: "V", dec: 1, scale: [340, 460], low: 376, high: 440, nearLow: 376, nearHigh: 440, judged: [300, 1e9],
      okWord: "In range", note: "Typical 400 V band 376 to 440 V (advisory)", na: "Not a 400 V line-to-line supply: not judged" },
    freq: { word: "Frequency", unit: "Hz", dec: 2, scale: [49.0, 51.0], low: 49.5, high: 50.5, nearLow: 49.8, nearHigh: 50.2, judged: [40, 55],
      note: "UK 50 Hz: red outside 49.5 to 50.5, amber outside 49.8 to 50.2 (advisory)", na: "Not a 50 Hz supply: not judged" },
  };

  // How loaded a circuit is: { pct, bar (0..100, capped), level: "ok"|"high"|"over"|"none", word, text }.
  pw.loadView = function (amps, rating) {
    if (!isNum(rating) || rating <= 0 || !isNum(amps)) return { pct: null, bar: 0, level: "none", word: "", text: "—" };
    const pct = 100 * amps / rating;
    const level = pct >= pw.LOAD_OVER ? "over" : pct >= pw.LOAD_HIGH ? "high" : "ok";
    const word = level === "over" ? "OVER RATING" : level === "high" ? "HIGH LOAD" : "OK";
    return { pct, bar: Math.max(0, Math.min(100, pct)), level, word, text: `${SW.num(amps, 2)} A of ${SW.num(rating, 0)} A (${SW.num(pct, 0)}%)` };
  };

  // Phase imbalance (NEMA MG 1 current unbalance), from the three phase currents: the biggest difference of
  // any line from the average of the three, as a percentage of the average. Only when all three are real
  // numbers and the average is at least UNBALANCE_FLOOR_A (so noise at near-zero load shows nothing).
  // Keep the floor in step with calc.UNBALANCE_FLOOR_A on the server.
  pw.UNBALANCE_FLOOR_A = 1.0;
  pw.imbalance = function (a, b, c) {
    if (![a, b, c].every(isNum)) return null;
    const avg = (a + b + c) / 3;
    if (!(avg >= pw.UNBALANCE_FLOOR_A)) return null;
    return 100 * Math.max(Math.abs(a - avg), Math.abs(b - avg), Math.abs(c - avg)) / avg;
  };

  // Neutral current as a share of the biggest line current. Advisory: a high share hints the lines
  // are unbalanced (or very uneven loads); it is not a fault finding.
  pw.NEUTRAL_HIGH = 50;
  pw.neutralShare = function (n, a, b, c) {
    if (![n, a, b, c].every(isNum)) return null;
    const mx = Math.max(a, b, c);
    if (!(mx > 0)) return null;
    const pct = 100 * n / mx;
    return { pct, high: pct >= pw.NEUTRAL_HIGH };
  };

  // The meter's own peak registers ("meter_max_<field>"): rows of { label, entity }. Not Stagewatch's figures.
  pw.meterMaxRows = function (ents, deviceId) {
    return ents.filter((e) => e.device_id === deviceId && e.labels && /^meter_max_/.test(e.labels.field || ""))
      .map((e) => {
        const base = e.labels.field.replace(/^meter_max_/, "");
        const g = e.labels.group || "";
        return { entity: e.id, label: ((pw.GROUP_WORD[g] ? pw.GROUP_WORD[g] + " " : "") + (pw.FIELD_WORD[base] || base)).replace(/^./, (c) => c.toUpperCase()) };
      }).sort((x, y) => x.label.localeCompare(y.label));
  };

  pw.swingPos = (v, cfg) => Math.max(0, Math.min(100, 100 * (v - cfg.scale[0]) / (cfg.scale[1] - cfg.scale[0])));
  // state: "ok" | "near" | "low" | "high" | "na" | "none"
  pw.swingView = function (v, cfg) {
    if (!isNum(v)) return { state: "none", word: "", pos: 0 };
    const pos = pw.swingPos(v, cfg);
    if (v < cfg.judged[0] || v > cfg.judged[1]) return { state: "na", word: cfg.na, pos };
    if (v < cfg.low) return { state: "low", word: "LOW", pos };
    if (v > cfg.high) return { state: "high", word: "HIGH", pos };
    if (v < cfg.nearLow) return { state: "near", word: "CLOSE TO LOW LIMIT", pos };
    if (v > cfg.nearHigh) return { state: "near", word: "CLOSE TO HIGH LIMIT", pos };
    return { state: "ok", word: cfg.okWord || "In range", pos };
  };

  // ---- history chart: one measurement of one meter over time (one line per phase on a 3-phase meter).
  pw.METRICS = [["power", "Power"], ["apparent", "Apparent power"], ["current", "Current"], ["voltage", "Voltage"], ["voltage_ll", "Line-to-line"], ["freq", "Frequency"], ["thd_v", "Voltage THD"], ["thd_i", "Current THD"], ["reactive", "Reactive power"], ["unbalance", "Phase unbalance"], ["demand", "Average demand (W)"], ["demand_va", "Average demand (VA)"]];
  pw.POINTS = 500;
  // ---- what the chart can show. A "scope" is a short string chosen in the chart's select box:
  //   "all"                          every circuit of every meter, as separate lines
  //   "totals"                       each line's total (a table, not a graph: see phaseTotals)
  //   "x:<group id>"                 an extra group across every meter, e.g. "x:neutral_earth"
  //   "d:<device>"                   one meter, all its circuits
  //   "c:<device>/<channel>"         one circuit of a meter (a switch of a 2PM, an em1 channel ...)
  //   "g:<device>/<channel>/<group>" one phase of a three-phase meter (a, b, c, n, total ...)
  // Extending: a future earth-leakage (PE) current is just a reading with group "pe" and field "current". It
  // joins the "Neutral and earth" group below, so it appears as extra lines (in its own unit: see chartPlan)
  // without changing the scopes, the select box or the totals logic.
  pw.SERIES_GROUPS = [
    { id: "neutral_earth", label: "Neutral and earth", groups: ["n", "pe"], fields: ["current"] },
  ];
  const seriesGroup = (id) => pw.SERIES_GROUPS.find((x) => x.id === id);
  pw.MAX_LINES = 9;   // 3 colours x 3 dash styles

  const meterEntities = (entities) => Object.keys(entities || {}).map((k) => entities[k])
    .filter((e) => e && e.device_id && e.labels && e.labels.channel && e.kind !== "energy" && e.labels.field);
  // Does entity e belong in scope?
  pw.inScope = function (scope, e) {
    const l = e.labels || {};
    if (scope === "all") return true;
    const kind = scope.slice(0, 2), rest = scope.slice(2);
    if (kind === "x:") { const g = seriesGroup(rest); return !!g && g.groups.includes(l.group || "") && g.fields.includes(l.field); }
    if (kind === "d:") return e.device_id === rest;
    if (kind === "c:") return `${e.device_id}/${l.channel}` === rest;
    if (kind === "g:") return `${e.device_id}/${l.channel}/${l.group || ""}` === rest;
    return false;
  };

  // The lines to draw for a scope and a field: [{ id, label, unit, decimals, order }], in meter, circuit and phase
  // order. A label names only what is needed: the meter when several are shown, the circuit when a meter has
  // several, and the phase when there is one.
  pw.seriesFor = function (entities, devices, scope, field) {
    const all = meterEntities(entities);
    const ents = all.filter((e) => e.labels.field === field && pw.inScope(scope, e));
    const perDevice = {};
    for (const e of all) (perDevice[e.device_id] || (perDevice[e.device_id] = new Set())).add(e.labels.channel);
    const devNames = new Set(ents.map((e) => e.device_id));
    const out = ents.map((e) => {
      const l = e.labels, g = l.group || "", d = (devices && devices[e.device_id]) || {};
      const many = perDevice[e.device_id].size > 1;
      const parts = [];
      if (devNames.size > 1) parts.push(d.name || e.device_id);
      const t = pw.channelTitle({ id: l.channel, cfg: (d.channels || {})[l.channel] || null }, many);
      if (t) parts.push(t);
      if (pw.GROUP_WORD[g]) parts.push(pw.GROUP_WORD[g]);
      return { id: e.id, label: (parts.join(" · ") || pw.FIELD_WORD[field]) + pw.calcWord(e), unit: e.unit || "", decimals: e.decimals || 0,
        device: e.device_id, channel: l.channel, group: g, order: pw.GROUP_ORDER.indexOf(g), name: d.name || e.device_id };
    });
    return out.sort((a, b) => a.name.localeCompare(b.name) || a.channel.localeCompare(b.channel) || a.order - b.order);
  };
  // One meter's lines for a field (the older call).
  pw.chartSeries = (entities, deviceId, field) => pw.seriesFor(entities, null, "d:" + deviceId, field);

  // What to draw: at most MAX_LINES lines of one unit (never two units on one axis), each with a colour
  // number and a dash number (so a line is told apart by more than colour), and a note of anything left out.
  pw.chartPlan = function (entities, devices, scope, field) {
    let lines = pw.seriesFor(entities, devices, scope, field);
    const notes = [];
    if (lines.length) {
      const unit = lines[0].unit, keep = lines.filter((l) => l.unit === unit), n = lines.length - keep.length;
      if (n) notes.push(`${n} line${n === 1 ? "" : "s"} in another unit not drawn (one unit per chart).`);
      lines = keep;
    }
    if (lines.length > pw.MAX_LINES) { notes.push(`Only the first ${pw.MAX_LINES} lines are drawn: pick a meter or circuit to see the rest.`); lines = lines.slice(0, pw.MAX_LINES); }
    lines.forEach((l, i) => { l.colour = i % 3; l.dash = Math.floor(i / 3) % 3; });
    return { lines, note: notes.join(" ") };
  };

  // The choices for the chart's select box: [{ value, label, group }]; group is the meter's name (an option
  // group in the select box) or "" for the top choices.
  pw.chartScopes = function (entities, devices) {
    const ents = meterEntities(entities);
    const out = [];
    if (!ents.length) return out;
    const names = (id) => (devices && devices[id] && devices[id].name) || id;
    const devIds = [...new Set(ents.map((e) => e.device_id))].sort((a, b) => names(a).localeCompare(names(b)));
    out.push({ value: "all", label: "All on one graph", group: "" }, { value: "totals", label: "Totals", group: "" });
    for (const g of pw.SERIES_GROUPS) if (ents.some((e) => pw.inScope("x:" + g.id, e))) out.push({ value: "x:" + g.id, label: g.label, group: "" });
    for (const id of devIds) {
      const mine = ents.filter((e) => e.device_id === id);
      const chans = [...new Set(mine.map((e) => e.labels.channel))].sort();
      const grp = names(id);
      out.push({ value: "d:" + id, label: chans.length > 1 ? "All circuits" : "Whole meter", group: grp });
      for (const c of chans) {
        const cfg = ((devices && devices[id] && devices[id].channels) || {})[c] || null;
        const groups = pw.GROUP_ORDER.filter((g) => g && mine.some((e) => e.labels.channel === c && (e.labels.group || "") === g));
        if (chans.length > 1) out.push({ value: `c:${id}/${c}`, label: pw.channelTitle({ id: c, cfg }, true) || c, group: grp });
        if (groups.length > 1) for (const g of groups) out.push({ value: `g:${id}/${c}/${g}`, label: (chans.length > 1 ? "  " : "") + pw.GROUP_WORD[g], group: grp });
      }
    }
    return out;
  };
  // A saved choice that is no longer on offer (a meter removed) falls back to the first one.
  pw.pickScope = (scopes, wanted) => (scopes.some((s) => s.value === wanted) ? wanted : (scopes[0] ? scopes[0].value : ""));

  // ---- Totals view: the watts of each line, never summed across anything that is not reading.
  // Three-phase meters: their own a, b, c and total readings (the total is the meter's own, not a sum).
  // Single-phase circuits: grouped by the line set in Admin (L1, L2, L3, or "Line not set"), each group
  // added up, then the overall total. If any circuit in a group is not reading (meter not answering, reading
  // old, or never received) that group is INCOMPLETE: no figure, the missing circuits listed. Nothing is
  // filled in. Neutral and earth currents (SERIES_GROUPS) are listed one row per reading and never added up.
  pw.phaseTotals = function (devices, meters, entities) {
    const live = (m, e) => !!m && m.state === "live" && !!e && isNum(e.value) && !e.stale;
    const three = [], single = { L1: [], L2: [], L3: [], "": [] }, extra = [];
    let anySingle = false;
    for (const m of meters) {
      const d = (devices || {})[m.id] || {};
      const many = m.channels.length > 1;
      for (const ch of m.channels) {
        const name = [m.name, pw.channelTitle(ch, many)].filter(Boolean).join(" · ");
        if (["a", "b", "c"].some((k) => ch.groups[k])) {
          const rows = ["a", "b", "c", "total"].filter((k) => ch.groups[k] && ch.groups[k].power)
            .map((k) => { const ok = live(m, ch.groups[k].power), ap = ch.groups[k].apparent, vok = live(m, ap);
              return { label: pw.GROUP_WORD[k], ok, watts: ok ? ch.groups[k].power.value : null, va: vok ? ap.value : null, vaCalc: pw.calcWord(ap) !== "" }; });
          three.push({ name, rows });
        } else if (ch.groups[""] && ch.groups[""].power) {
          const e = ch.groups[""].power, cfg = (d.channels || {})[ch.id] || {};
          const ok = live(m, e);
          const ap = ch.groups[""].apparent, vok = live(m, ap);
          single[single[cfg.phase] ? cfg.phase : ""].push({ name, ok, watts: ok ? e.value : null, vaOk: vok, va: vok ? ap.value : null, vaCalc: pw.calcWord(ap) !== "" });
          anySingle = true;
        }
        for (const sg of pw.SERIES_GROUPS) for (const g of sg.groups) for (const f of sg.fields) {
          const e = ch.groups[g] && ch.groups[g][f];
          if (e) { const ok = live(m, e); extra.push({ group: sg.label, label: [name, pw.GROUP_WORD[g]].filter(Boolean).join(" · "), ok, value: ok ? e.value : null, unit: e.unit || "", decimals: e.decimals || 0 }); }
        }
      }
    }
    const sum = (items) => {
      const missing = items.filter((x) => !x.ok).map((x) => x.name);
      // VA is added the same way (a plain sum of the VA figures: it can overstate the true total when the loads are not in step).
      const vaMissing = items.filter((x) => !x.vaOk).map((x) => x.name);
      return { count: items.length, missing, watts: missing.length || !items.length ? null : items.reduce((s, x) => s + x.watts, 0),
        vaMissing, va: vaMissing.length || !items.length ? null : items.reduce((s, x) => s + x.va, 0), vaCalc: items.some((x) => x.vaCalc) };
    };
    const rows = ["L1", "L2", "L3", ""].filter((k) => single[k].length).map((k) => ({ label: k ? pw.PHASE_WORD[k] : "Line not set", ...sum(single[k]) }));
    // Phase unbalance (calculated on the server): the site's circuits set to lines, and each three-phase meter.
    const unb = [];
    for (const e of Object.keys(entities || {}).map((k) => entities[k])) {
      if (!e.labels || e.labels.field !== "unbalance") continue;
      const dev = (devices || {})[e.device_id] || {};
      const u = pw.unbText(e);
      unb.push({ label: e.device_id === pw.SITE_ID ? "Circuits set to Line 1, 2 and 3" : (dev.name || e.device_id), value: u.text, shares: u.shares });
    }
    unb.sort((a, b) => a.label.localeCompare(b.label));
    return { three, single: anySingle ? { rows, overall: sum([].concat(single.L1, single.L2, single.L3, single[""])) } : null, extra, unb };
  };

  // Builds the Totals table into host (replaces its contents only when the text would change).
  pw.renderTotals = function (host, t) {
    const h = SW.h;
    const num0 = (w) => (w === null ? "—" : `${SW.num(w, 0)} W`);
    const vaText = (v) => { if (v === null) return "—"; const f = pw.fmtVA(v); return `${f.text} ${f.unit}`; };
    const why = (x) => [x.watts === null && x.missing && x.missing.length ? `watts: not reading ${x.missing.join(", ")}` : "",
      x.va === null && x.vaMissing && x.vaMissing.length ? `VA: not available for ${x.vaMissing.join(", ")}` : ""].filter(Boolean);
    const state = (x) => (why(x).length ? "▲ INCOMPLETE: " + why(x).join("; ") : (x.watts === null && x.va === null ? "▲ No reading" : ""));
    const head = (vaWord) => h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", { class: "num" }, "Power (W)"), h("th", { class: "num" }, vaWord), h("th", {}, "")));
    const sections = [];
    for (const m of t.three) {
      const calc = m.rows.some((r) => r.vaCalc);
      sections.push(h("table", { class: "pw-table pw-totals" }, h("caption", {}, m.name + " (the meter's own readings)"), head("Apparent power" + (calc ? " (calculated)" : "")),
        h("tbody", {}, m.rows.map((r) => h("tr", {}, h("th", { scope: "row" }, r.label), h("td", { class: "num" }, num0(r.watts)), h("td", { class: "num" }, vaText(r.va)), h("td", { class: "pw-incomplete" }, r.ok ? "" : "▲ NO READING"))))));
    }
    if (t.single) {
      const all = t.single.overall;
      const row = (label, x, cls) => h("tr", { class: cls || "" }, h("th", { scope: "row" }, label), h("td", { class: "num" }, num0(x.watts)), h("td", { class: "num" }, vaText(x.va)), h("td", { class: "pw-incomplete" }, state(x)));
      sections.push(h("table", { class: "pw-table pw-totals" }, h("caption", {}, "Single-phase circuits, by line"), head("Sum of VA" + (all.vaCalc ? " (some calculated)" : "")),
        h("tbody", {}, t.single.rows.map((r) => row(r.label, r)), row("Total, all lines", all, "pw-grandtotal"))));
    }
    if (t.extra.length) {
      sections.push(h("table", { class: "pw-table pw-totals" }, h("caption", {}, t.extra[0].group + " (current, not added up)"),
        h("tbody", {}, t.extra.map((r) => h("tr", {}, h("th", { scope: "row" }, r.label), h("td", { class: "num" }, r.value === null ? "—" : `${SW.num(r.value, Math.min(3, r.decimals))} ${r.unit}`), h("td", { class: "pw-incomplete" }, r.ok ? "" : "▲ NO READING"))))));
    }
    if (t.unb && t.unb.length) {
      sections.push(h("table", { class: "pw-table pw-totals" }, h("caption", {}, "Phase unbalance (calculated, advisory)"),
        h("tbody", {}, t.unb.map((r) => h("tr", {}, h("th", { scope: "row" }, r.label), h("td", { class: "num" }, r.value), h("td", { class: "pw-incomplete" }, r.shares))))));
    }
    if (!sections.length) sections.push(h("p", { class: "muted" }, "No power readings yet."));
    const sig = JSON.stringify(t);
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;
    host.replaceChildren(...sections);
  };

  // A silence longer than this between two points breaks the line (the meter was not read): never joined up.
  // The history chart's vertical scale for a measurement: voltage fixed to the UK range, frequency
  // to 50 Hz ± 1 Hz (a reading outside is drawn at the edge with a mark), power and current from 0
  // with the top following the readings. Anything else scales to the readings.
  pw.chartScale = function (field) {
    if (field === "voltage") return { fixed: [pw.SWING.voltage.low, pw.SWING.voltage.high], def: null };
    if (field === "voltage_ll") return { fixed: [pw.SWING.voltage_ll.low, pw.SWING.voltage_ll.high], def: null };
    if (field === "freq") return { fixed: [49, 51], def: null };
    if (field === "power" || field === "current" || field === "apparent" || field === "demand" || field === "demand_va") return { fixed: null, def: [0, 1e-6] };
    return { fixed: null, def: null };
  };
  pw.gapSeconds = (bucketS) => Math.max(60, 3 * bucketS);
  // Display-only smoothing: each point becomes the mean of the recorded points within windowS
  // around it. It never reaches across a gap (no reading for more than gapS), so a gap stays a
  // gap and nothing is invented; the stored history is unchanged.
  pw.smoothWindow = (spanS) => Math.max(30, spanS / 60);   // 1 h -> 1 min, 6 h -> 6 min
  pw.smooth = function (points, windowS, gapS) {
    const pts = (points || []).filter((p) => p && Number.isFinite(p[1]));
    const out = [];
    let start = 0;   // first point of the current unbroken run
    for (let i = 0; i < pts.length; i++) {
      if (i > 0 && pts[i][0] - pts[i - 1][0] > gapS) start = i;
      let end = i;
      while (end + 1 < pts.length && pts[end + 1][0] - pts[end][0] <= gapS && pts[end + 1][0] - pts[i][0] <= windowS / 2) end++;
      let sum = 0, n = 0;
      for (let j = i; j >= start && pts[i][0] - pts[j][0] <= windowS / 2; j--) { sum += pts[j][1]; n++; }
      for (let j = i + 1; j <= end; j++) { sum += pts[j][1]; n++; }
      out.push([pts[i][0], sum / n]);
    }
    return out;
  };
  pw.chartNote = (spanS, points) => `One point per ${SW.num(Math.max(1, spanS / points), 0)} s at most, as recorded, smoothed over ${pw.smoothWindow(spanS) < 60 ? SW.num(pw.smoothWindow(spanS), 0) + " s" : SW.num(pw.smoothWindow(spanS) / 60, 0) + " min"}; gaps are left as gaps. Short surges are flattened: check the meter's maximum above.`;

  // The combined load bar: the SUM of the currents of a meter's single-phase circuits against the
  // meter's own rating. All or nothing: if any circuit's current is missing or old, or the meter is not
  // live, there is no sum ("unavailable"), never a partial one. Three-phase phases are never added.
  pw.combinedView = function (ids, entities, rating, live) {
    const none = { pct: null, bar: 0, level: "none", word: "", text: "—", ok: false };
    if (!live || !ids.length) return none;
    let sum = 0;
    for (const id of ids) {
      const e = entities[id];
      if (!e || !isNum(e.value) || e.stale) return none;
      sum += e.value;
    }
    return { ...pw.loadView(sum, rating), ok: true };
  };

  const ageText = (updated, now) => (updated ? SW.age(updated, now) : "never");

  // One meter's readings, grouped. entities is the snapshot's entity map (id -> entity), device the
  // public device. Returns null when the device has no readings at all.
  pw.meterView = function (device, ents, now) {
    const mine = ents.filter((e) => e.device_id === device.id && e.labels && e.labels.channel && !e.labels.derived);
    const status = device.status || "";
    const newest = Math.max(0, ...mine.map((e) => e.updated || 0));
    const anyStale = mine.length > 0 && mine.some((e) => e.stale);
    let state = "live", badge = "OK", note = "";
    if (status === "initializing" || (status === "ok" && !mine.length)) { state = "waiting"; badge = "… WAITING"; note = "Waiting for the first reading"; }
    else if (status === "missing") { state = "frozen"; badge = "▲ NO READING"; note = `Not answering. Showing the last readings, ${ageText(newest, now)}.`; }
    else if (status === "fault" || status === "compromised") { state = "frozen"; badge = "▲ PROBLEM"; note = (device.status_detail || "Cannot read this meter") + (newest ? `. Last readings ${ageText(newest, now)}.` : "."); }
    else if (anyStale) { state = "frozen"; badge = "▲ OLD"; note = `Readings are old (${ageText(newest, now)}).`; }
    if (state === "live" && device.calc_note) note = device.calc_note;   // the calculated neutral: how it was worked out, or why there is no figure
    const channels = {};
    for (const e of mine) {
      const c = e.labels.channel;
      const ch = channels[c] || (channels[c] = { id: c, groups: {}, energy: [] });
      const g = e.labels.group || "";
      if (e.kind === "energy") { ch.energy.push({ group: g, field: e.labels.field, entity: e }); continue; }
      (ch.groups[g] || (ch.groups[g] = {}))[e.labels.field] = e;
    }
    const cfgs = device.channels || {};
    const list = Object.keys(channels).sort().map((k) => {
      const ch = channels[k];
      ch.cfg = cfgs[k] || null;
      ch.rating = isNum(ch.cfg && ch.cfg.rating_a) ? ch.cfg.rating_a : (isNum(device.rating_a) ? device.rating_a : null);
      return ch;
    });
    const combinedIds = [];
    for (const ch of list) if (ch.groups[""] && ch.groups[""].current) combinedIds.push(ch.groups[""].current.id);
    const combined = !!device.combined_bar && isNum(device.rating_a) && device.rating_a > 0 && combinedIds.length >= 2;
    return { combined, combinedIds, id: device.id, entities: Object.fromEntries(mine.map((e) => [e.id, e])), rating: isNum(device.rating_a) ? device.rating_a : null, name: device.name || device.id, area: device.area || "", state, badge, note, channels: list, newest, count: mine.length, model: device.model || "",
      demandIds: ["demand", "demand_va"].map((f) => { const e = ents.find((x) => x.device_id === device.id && x.labels && x.labels.derived && x.labels.field === f); return e ? e.id : ""; }) };
  };

  pw.view = function (entities, devices, now) {
    const ents = Object.keys(entities || {}).map((k) => entities[k]);
    const meters = Object.keys(devices || {}).map((k) => devices[k])
      .filter((d) => d && d.category === "meter" && !d.derived)
      .sort((a, b) => (a.name || "").localeCompare(b.name || ""))
      .map((d) => pw.meterView(d, ents, now));
    // The key changes only when the set of meters or readings changes, so the DOM is rebuilt rarely.
    const key = meters.map((m) => m.id + "@" + (m.rating || 0) + (m.combined ? "C" + m.combinedIds.length : "") + ":" + m.channels.map((c) => c.id + "~" + (c.rating || 0) + (c.cfg ? (c.cfg.label || "") + (c.cfg.phase || "") : "") + "[" + Object.keys(c.groups).map((g) => g + "=" + Object.keys(c.groups[g]).join("+")).join(",") + "|" + c.energy.map((x) => x.entity.id).join("+") + "]").join("")).join(";");
    const total = pw.totalView(devices, meters);
    const site = pw.siteView(entities);
    return { meters, total, site, key: (site ? "S" + site.key + ";" : "") + (total ? "T" + total.parts.length + ";" : "") + key + meters.map((m) => m.demandIds.join("+")).join(",") };
  };

  // ---- site total: the watts of every circuit ticked "Include in site total" in Admin, added together.
  // All or nothing: if any ticked circuit is not reading right now (meter not answering, reading old, or
  // never received) there is no sum, only a list of what is missing. Nothing is filled in, estimated or
  // left out. A three-phase meter counts once, by its own total, never by its three phases as well.
  // Returns null when nothing is ticked, else { parts, missing, watts } with watts null when incomplete.
  pw.totalView = function (devices, meters) {
    const parts = [];
    for (const d of Object.keys(devices || {}).map((k) => devices[k])) {
      if (!d || d.category !== "meter" || !d.channels) continue;
      const m = meters.find((x) => x.id === d.id);
      for (const key of Object.keys(d.channels).sort()) {
        const cfg = d.channels[key];
        if (!cfg || !cfg.in_total) continue;
        const ch = m && m.channels.find((c) => c.id === key);
        const g = ch && (ch.groups[""] || ch.groups.total);
        const e = g && g.power;
        const ok = !!m && m.state === "live" && !!e && isNum(e.value) && !e.stale;
        const many = !!m && m.channels.length > 1;
        const name = [d.name || d.id, pw.channelTitle({ id: key, cfg }, many)].filter(Boolean).join(" · ");
        parts.push({ name, ok, watts: ok ? e.value : null, entity: e ? e.id : "" });
      }
    }
    if (!parts.length) return null;
    const missing = parts.filter((p) => !p.ok).map((p) => p.name);
    return { parts, missing, watts: missing.length ? null : parts.reduce((s, p) => s + p.watts, 0) };
  };

  // ---- calculated figures: demand, load factor, phase unbalance and the generator suggestion. They are
  // worked out on the server (integrations/shelly/calc.py and derived.py), recorded with history, and shown
  // here as plain numbers. All are advisory and labelled "calculated". A missing figure is a dash, never 0.
  pw.SITE_ID = "power_site";
  // The site's calculated entities (null when no circuit is ticked or given a line): { w, va, gen, unb, key }.
  pw.siteView = function (entities) {
    const g = (k) => (entities || {})[pw.SITE_ID + "." + k] || null;
    const o = { w: g("site_power_demand"), va: g("site_apparent_demand"), gen: g("generator"), unb: g("unbalance") };
    if (!o.w && !o.va && !o.gen && !o.unb) return null;
    o.key = ["w", "va", "gen", "unb"].map((k) => (o[k] ? k : "")).join("");
    return o;
  };
  // One demand figure as words: the average now, the day's peak and the load factor, or a dash.
  pw.demandText = function (e, isVA) {
    const d = e && e.day;
    if (!d) return "—";
    const f = isVA ? pw.fmtVA : pw.fmtW, s = (v) => { const t = f(v); return `${t.text} ${t.unit}`; };
    const parts = [isNum(e.value) && !e.stale ? `${d.window_min} min average ${s(e.value)}` : `no ${d.window_min} min average yet`];
    if (isNum(d.peak)) parts.push(`peak ${s(d.peak)} at ${hhmm(d.peak_at)}`);
    if (isNum(d.load_factor)) parts.push(`load factor ${SW.num(100 * d.load_factor, 0)}%`);
    return parts.join(", ") + (d.incomplete || d.peak_incomplete ? ". ▲ INCOMPLETE: readings were missing inside the window, so this covers only the time read." : "");
  };
  // Phase unbalance as { text, shares }: "—" with the reason when it cannot be worked out.
  pw.unbText = function (e) {
    if (!e || !isNum(e.value) || e.stale) return { text: "—", shares: "Needs every line reading, and at least 1 A on average." };
    const sh = e.day && Array.isArray(e.day.shares) ? e.day.shares : null;
    return { text: `${SW.num(e.value, 1)}%`, shares: sh ? "Share of the load: " + sh.map((x, i) => `Line ${i + 1} ${SW.num(x, 0)}%`).join(", ") : "" };
  };
  pw.GEN_NOTE = "This is a suggestion from the demand Stagewatch measured, nothing more. It leaves out loads that are not metered, starting surges, harmonics and anything added later. Ask a qualified person to size the generator.";
  // The generator suggestion: { head (kVA text), lines, level: "none"|"ok"|"limit"|"over" }.
  pw.genView = function (e, windowMin) {
    const d = e && e.day;
    if (!d || !isNum(d.min_kva)) return { head: "—", lines: [`Needs a full ${windowMin || 15} minutes of site readings first.`], level: "none" };
    const lines = [`Peak demand ${SW.num(d.peak_kva, 1)} kVA, kept to ${SW.num(d.loading_pct, 0)}% loading: at least ${SW.num(d.min_kva, 1)} kVA.`,
      `About ${SW.num(d.min_kw, 1)} kW at a generator power factor of ${SW.num(d.pf, 2)}.`,
      d.next_kva ? `Next standard size up: ${d.next_kva} kVA.` : "Larger than the standard sizes listed: ask your supplier."];
    let level = "ok";
    if (isNum(d.loaded_pct)) {
      level = d.state || "ok";
      const word = level === "over" ? " OVER 100%" : level === "limit" ? ` AT YOUR ${SW.num(d.loading_pct, 0)}% LIMIT` : "";
      lines.push(`Loaded to ${SW.num(d.loaded_pct, 0)}% of ${SW.num(d.size_kva, 0)} kVA at the day's peak${isNum(d.now_pct) ? ` (${SW.num(d.now_pct, 0)}% now)` : ""}.${word}`);
    }
    if (d.peak_incomplete) lines.push("▲ Readings were missing inside the peak window, so the real peak may be higher.");
    if (d.basis === "line") lines.push("Sized from three times the busiest line: a generator can give only a third of its rating on each line, so an uneven load needs a bigger one.");
    return { head: SW.num(d.min_kva, 1), lines, level };
  };

  // ------------------------------------------------------------------ DOM
  pw.dayFoldOpen = {};   // meter id -> the show-day fold is open (this page visit)
  pw.build = function (host) {
    const h = SW.h;
    const ui = { key: null, meters: {} };

    // Every headline figure (power, voltage, current, frequency, energy ...) in the same format.
    const cell = (label, unit) => {
      const val = h("span", { class: "pw-num" }), unitEl = h("span", { class: "unit" }, unit);
      const el = h("div", { class: "pw-cell" }, h("div", { class: "pw-label" }, label), h("div", { class: "pw-value" }, val, unitEl));
      return { el, val, unit: unitEl };
    };

    function buildMeter(m) {
      const r = { fields: [], energy: [], dayEnergy: [] };
      const energyRows = [];   // several phases: the meter totals fold away with the show-day figures
      r.name = h("span", { class: "pw-name" });
      r.area = h("span", { class: "pw-area muted" });
      r.badge = h("span", { class: "pw-badge" });
      r.note = h("p", { class: "pw-note", role: "status" });
      const head = h("div", { class: "pw-head" }, r.name, r.area, h("span", { class: "grow" }), r.badge);
      const body = h("div", { class: "pw-body" });
      for (const ch of m.channels) {
        const title = pw.channelTitle(ch, m.channels.length > 1);
        if (title) body.append(h("div", { class: "pw-chhead" }, title));
        const groupKeys = pw.GROUP_ORDER.filter((g) => ch.groups[g] && !pw.LL.includes(g));
        let grid = null;   // one circuit: its figures, and its energy, share one evenly spread row
        if (groupKeys.length === 1 && groupKeys[0] === "") {
          // One circuit: every figure in the same format, spread evenly across the card.
          const g = ch.groups[""];
          grid = h("div", { class: "pw-grid" });
          for (const f of ["power", "voltage", "current", "freq", "reactive", "apparent", "pf", "device_temp"]) {
            if (!g[f]) continue;
            const c = cell(pw.FIELD_WORD[f] + pw.calcWord(g[f]), pw.UNIT[f]);
            r.fields.push({ entity: g[f].id, val: c.val, el: c.el, va: f === "apparent" ? c.unit : null, vr: f === "reactive" ? c.unit : null });
            grid.append(c.el);
          }
          body.append(grid);
        } else {
          // Several phases (or a total): a small table, one row per phase.
          const rows = [];
          const cols = ["current", "voltage", "power"].filter((f) => groupKeys.some((k) => ch.groups[k][f]));
          const extra = ["reactive", "apparent", "pf"].filter((f) => groupKeys.some((k) => ch.groups[k][f]));
          const colsAll = cols.concat(extra);
          for (const k of groupKeys) {
            const tds = colsAll.map((f) => {
              const e = ch.groups[k][f];
              const td = h("td", { class: "num" });
              if (e) r.fields.push({ entity: e.id, val: td, el: td, plain: true, vaTable: f === "apparent", vrTable: f === "reactive" });
              return td;
            });
            rows.push(h("tr", {}, h("th", { scope: "row" }, pw.GROUP_WORD[k] || ""), ...tds));
          }
          body.append(h("div", { class: "table-scroll" }, h("table", { class: "pw-table" },
            h("thead", {}, h("tr", {}, h("th", {}, ""), ...colsAll.map((f) => h("th", { class: "num" }, `${pw.FIELD_WORD[f]}${pw.UNIT[f] && f !== "apparent" && f !== "reactive" ? " (" + pw.UNIT[f] + ")" : ""}`)))),
            h("tbody", {}, rows))));
        }
        for (const x of ch.energy) {
          const word = (pw.GROUP_WORD[x.group] ? pw.GROUP_WORD[x.group] + " " : "") + (x.field === "energy_ret" ? "energy returned" : "energy");
          if (grid) {   // a cell like the others; the label says it is the meter's own running total
            const c = cell(word[0].toUpperCase() + word.slice(1) + " (meter total)", "");
            grid.append(c.el);
            r.energy.push({ entity: x.entity.id, val: c.val, unit: c.unit });
            continue;
          }
          const val = h("span", { class: "pw-num" }), unit = h("span", { class: "unit" });
          energyRows.push(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, word[0].toUpperCase() + word.slice(1) + " (meter total)"), " ", val, unit));
          r.energy.push({ entity: x.entity.id, val, unit });
        }
      }
      // Load bar (needs a rating) and swing meters for voltage and frequency, per circuit or phase.
      r.gauges = [];
      const gaugeBox = h("div", { class: "pw-gauges" });
      if (m.combined) {
        const fill = h("div", { class: "pw-fill" });
        const text = h("span", { class: "pw-gtext" }), word = h("span", { class: "pw-gword" });
        const track = h("div", { class: "pw-track" }, fill, h("i", { class: "pw-tick", style: `left:${pw.LOAD_HIGH}%` }));
        gaugeBox.append(h("div", { class: "pw-gauge" }, h("div", { class: "pw-glabel" }, h("span", { class: "pw-label" }, "Combined load"), " ", text, " ", word), track,
          h("div", { class: "pw-gscale muted" }, h("span", {}, "0"), h("span", { style: `left:${pw.LOAD_HIGH}%` }, "80%"), h("span", { class: "r" }, "100%"))));
        r.gauges.push({ kind: "combined", ids: m.combinedIds, rating: m.rating, fill, text, word, el: track, meter: m });
      }
      for (const ch of m.channels) {
        for (const g of pw.GROUP_ORDER.filter((k) => ch.groups[k] && k !== "n" && k !== "total" && k !== "avg")) {
          const pre = pw.GROUP_WORD[g] ? pw.GROUP_WORD[g] + " " : "";
          const cur = ch.groups[g].current;
          if (cur && ch.rating) {
            const fill = h("div", { class: "pw-fill" });
            const mark = h("div", { class: "pw-peakmark" });
            const text = h("span", { class: "pw-gtext" }), word = h("span", { class: "pw-gword" });
            const track = h("div", { class: "pw-track" }, fill, mark, h("i", { class: "pw-tick", style: `left:${pw.LOAD_HIGH}%` }));
            gaugeBox.append(h("div", { class: "pw-gauge" }, h("div", { class: "pw-glabel" }, h("span", { class: "pw-label" }, pre + "Load"), " ", text, " ", word), track,
              h("div", { class: "pw-gscale muted" }, h("span", {}, "0"), h("span", { style: `left:${pw.LOAD_HIGH}%` }, "80%"), h("span", { class: "r" }, "100%"))));
            r.gauges.push({ kind: "load", rating: ch.rating, entity: cur.id, fill, mark, text, word, el: track });
          }
          for (const f of ["voltage", "voltage_ll", "freq"]) {
            const e = ch.groups[g][f];
            if (!e) continue;
            const cfg = pw.SWING[f];
            const pos = (v) => pw.swingPos(v, cfg);
            const zone = (a, b, cls) => h("div", { class: cls, style: `left:${pos(a)}%;width:${pos(b) - pos(a)}%` });
            const band = h("div", { class: "pw-range" }), live = h("div", { class: "pw-live" });
            const text = h("span", { class: "pw-gtext" }), word = h("span", { class: "pw-gword" }), sub = h("div", { class: "pw-gsub muted" });
            const track = h("div", { class: "pw-track pw-vtrack" },
              zone(cfg.low, cfg.high, "pw-warnzone"), zone(cfg.nearLow, cfg.nearHigh, "pw-okzone"), band, live);
            const marks = [cfg.low, cfg.nearLow, cfg.nearHigh, cfg.high].filter((x, i, a) => a.indexOf(x) === i);
            gaugeBox.append(h("div", { class: "pw-gauge" }, h("div", { class: "pw-glabel" }, h("span", { class: "pw-label" }, pre + cfg.word), " ", text, " ", word), track,
              h("div", { class: "pw-gscale muted" }, h("span", {}, `${cfg.scale[0]}`),
                ...marks.map((x) => h("span", { style: `left:${pos(x)}%` }, `${x}`)), h("span", { class: "r" }, `${cfg.scale[1]} ${cfg.unit}`)), sub));
            r.gauges.push({ kind: "swing", cfg, entity: e.id, band, live, text, word, sub, el: track });
          }
        }
      }
      // This show day's figures: highest power and current, voltage range, energy used.
      r.day = [];
      r.dayHead = h("div", { class: "pw-dayhead" });
      const dayBody = h("div", { class: "pw-day" }, r.dayHead, ...energyRows);
      for (const ch of m.channels) {
        for (const g of pw.GROUP_ORDER.filter((k) => ch.groups[k] && k !== "avg")) {
          for (const f of ["power", "current", "voltage", "voltage_ll", "freq", "thd_v", "thd_i"]) {
            const e = ch.groups[g][f];
            if (!e) continue;
            const word = f === "voltage" || f === "voltage_ll" ? "Voltage range" : f === "freq" ? "Frequency range" : pw.DAY_WORD[f];
            const val = h("span", { class: "pw-num" });
            dayBody.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, (pw.GROUP_WORD[g] ? pw.GROUP_WORD[g] + " " : "") + word.toLowerCase().replace(/^./, (c) => c.toUpperCase())), " ", val));
            r.day.push({ entity: e.id, field: f, val });
          }
        }
        for (const x of ch.energy) {
          if (x.field === "energy_ret") continue;
          const val = h("span", { class: "pw-num" }), unit = h("span", { class: "unit" }), sub = h("span", { class: "pw-sub muted" });
          dayBody.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, (pw.GROUP_WORD[x.group] ? pw.GROUP_WORD[x.group] + " " : "") + "Energy used this show day"), " ", val, unit, sub));
          r.dayEnergy.push({ entity: x.entity.id, val, unit, sub });
        }
      }
      r.imb = null;
      const ph = m.channels.find((c) => c.groups.a && c.groups.b && c.groups.c && c.groups.a.current && c.groups.b.current && c.groups.c.current);
      if (ph) {
        const val = h("span", { class: "pw-num" });
        dayBody.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, "Phase unbalance (calculated)"), " ", val, h("span", { class: "pw-sub muted" }, " (biggest difference from the average line current; needs at least 1 A)")));
        r.imb = { ids: [ph.groups.a.current.id, ph.groups.b.current.id, ph.groups.c.current.id], val };
      }
      r.neu = null;
      const nph = m.channels.find((c) => c.groups.n && c.groups.n.current && c.groups.a && c.groups.b && c.groups.c && c.groups.a.current && c.groups.b.current && c.groups.c.current);
      if (nph) {
        const val = h("span", { class: "pw-num" }), word = h("span", { class: "pw-gword" });
        dayBody.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, "Neutral current"), " ", val, word, h("span", { class: "pw-sub muted" }, " of the biggest line current (advisory)")));
        r.neu = { ids: [nph.groups.n.current.id, nph.groups.a.current.id, nph.groups.b.current.id, nph.groups.c.current.id], val, word };
      }
      r.demand = [];
      [["Demand, watts (calculated)", false], ["Demand, volt-amps (calculated)", true]].forEach(([word, isVA], i) => {
        if (!m.demandIds[i]) return;
        const val = h("span", { class: "pw-num" });
        dayBody.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, word), " ", val));
        r.demand.push({ entity: m.demandIds[i], val, isVA });
      });
      r.maxes = pw.meterMaxRows(Object.values(m.entities || {}), m.id).map((x) => {
        const val = h("span", { class: "pw-num" });
        dayBody.append(h("div", { class: "pw-dayrow pw-metermax" }, h("span", { class: "pw-label" }, "Meter's own maximum: " + x.label), " ", val, h("span", { class: "pw-sub muted" }, " (kept by the meter since its last reset)")));
        return { entity: x.entity, val };
      });
      r.gaps = h("p", { class: "pw-note pw-gaps", role: "status" });
      dayBody.append(r.gaps);
      // The show-day figures (highest, lowest, energy used ...) fold away under one line, closed until
      // someone opens it; the choice holds while the page is open.
      const dayFold = h("details", { class: "pw-dayfold" }, h("summary", {}, "Show day figures"), dayBody);
      dayFold.open = !!pw.dayFoldOpen[m.id];
      dayFold.addEventListener("toggle", () => { pw.dayFoldOpen[m.id] = dayFold.open; });
      r.root = h("div", { class: "pw-meter" }, head, r.note, body, r.gauges.length ? gaugeBox : null, dayFold);
      return r;
    }

    function buildTotal() {
      const t = cell("Site total power", "W");
      const note = h("p", { class: "pw-note", role: "status" });
      const sub = h("div", { class: "pw-gsub muted" });
      return { val: t.val, unit: t.unit, note, sub, root: h("div", { class: "pw-meter pw-total" }, h("div", { class: "pw-grid" }, t.el), note, sub) };
    }

    // Calculated site figures: demand, load factor, generator suggestion and phase unbalance. One headline row
    // in the same cell format as the rest; the detail folds away.
    function buildSite(s) {
      const r = { s };
      const cells = [];
      const add = (key, label, unit) => { const c = cell(label, unit); r[key] = c; cells.push(c.el); };
      if (s.w) { add("avg", "Site average demand (calculated)", "W"); add("peak", "Peak demand today", "W"); add("lf", "Load factor today", "%"); }
      if (s.gen) add("gen", "Suggested generator, at least (calculated)", "kVA");
      if (s.unb) add("unb", "Site phase unbalance (calculated)", "%");
      const body = h("div", { class: "pw-day" });
      r.rows = {};
      const row = (key, label) => { const v = h("span", { class: "pw-num" }); r.rows[key] = v; body.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, label), " ", v)); };
      if (s.w) row("w", "Demand, watts");
      if (s.va) row("va", "Demand, volt-amps");
      if (s.unb) { row("unb", "Phase unbalance"); r.shares = h("div", { class: "pw-dayrow muted" }); body.append(r.shares); }
      r.genLines = s.gen ? h("div", { class: "pw-dayrow" }) : null;
      if (s.gen) body.append(r.genLines, h("p", { class: "pw-note muted" }, pw.GEN_NOTE));
      body.append(h("p", { class: "pw-note muted" }, "Demand is the average over a rolling window (set in Admin), counted only over the time Stagewatch was reading. Load factor is the day's average divided by the peak. Phase unbalance uses the NEMA MG 1 voltage-unbalance method applied to current: the biggest difference of any line from the average of the three, as a percentage of that average."));
      const fold = h("details", { class: "pw-dayfold" }, h("summary", {}, "Site figures in detail"), body);
      fold.open = !!pw.dayFoldOpen[pw.SITE_ID];
      fold.addEventListener("toggle", () => { pw.dayFoldOpen[pw.SITE_ID] = fold.open; });
      r.root = h("div", { class: "pw-meter pw-total" }, h("div", { class: "pw-grid" }, ...cells), fold);
      return r;
    }

    return {
      update(view, entities) {
        if (view.key !== ui.key) {
          ui.key = view.key;
          ui.meters = {};
          ui.total = view.total ? buildTotal() : null;
          ui.site = view.site ? buildSite(view.site) : null;
          host.replaceChildren(...(ui.total ? [ui.total.root] : []), ...(ui.site ? [ui.site.root] : []), ...view.meters.map((m) => { const r = buildMeter(m); ui.meters[m.id] = r; return r.root; }));
        }
        if (ui.total && view.total) {
          const t = view.total, n = t.parts.length;
          ui.total.val.textContent = t.watts === null ? "—" : SW.num(t.watts, 0);
          ui.total.root.className = `pw-meter pw-total pw-s-${t.watts === null ? "frozen" : "live"}`;
          ui.total.note.textContent = t.watts === null ? `INCOMPLETE: not reading ${t.missing.join(", ")}. No total is shown until every ticked circuit is reading.` : "";
          SW.slot(ui.total.note, t.watts !== null);
          ui.total.sub.textContent = `Adds the watts of ${n} ticked circuit${n === 1 ? "" : "s"}: ${t.parts.map((p) => p.name).join(", ")}.`;
        }
        if (ui.site) {
          const r = ui.site, s = view.site;
          const put = (c, t) => { c.val.textContent = t.text; c.unit.textContent = t.unit; };
          const dw = s.w && entities[s.w.id], dv = s.va && entities[s.va.id];
          if (r.avg) {
            const d = dw && dw.day;
            put(r.avg, dw && isNum(dw.value) && !dw.stale ? pw.fmtW(dw.value) : { text: "—", unit: "W" });
            put(r.peak, d && isNum(d.peak) ? pw.fmtW(d.peak) : { text: "—", unit: "W" });
            put(r.lf, { text: d && isNum(d.load_factor) ? SW.num(100 * d.load_factor, 0) : "—", unit: "%" });
          }
          if (r.rows.w) r.rows.w.textContent = pw.demandText(dw, false);
          if (r.rows.va) r.rows.va.textContent = pw.demandText(dv, true);
          const win = dw && dw.day ? dw.day.window_min : dv && dv.day ? dv.day.window_min : 15;
          if (s.gen) {
            const gv = pw.genView(entities[s.gen.id], win);
            put(r.gen, { text: gv.head, unit: "kVA" });
            r.genLines.replaceChildren(...gv.lines.map((t) => h("div", {}, t)));
            r.genLines.className = `pw-dayrow pw-l-${gv.level}`;
          }
          if (s.unb) {
            const u = pw.unbText(entities[s.unb.id]);
            put(r.unb, { text: u.text === "—" ? "—" : u.text.replace("%", ""), unit: "%" });
            r.rows.unb.textContent = u.text; r.shares.textContent = u.shares;
          }
        }
        for (const m of view.meters) {
          const r = ui.meters[m.id];
          if (!r) continue;
          r.name.textContent = m.name;
          r.area.textContent = m.area ? ` · ${m.area}` : "";
          r.badge.textContent = m.badge;
          r.badge.className = `pw-badge pw-b-${m.state}`;
          r.note.textContent = m.note;
          SW.slot(r.note, !m.note);
          r.root.className = `pw-meter pw-s-${m.state}`;
          for (const f of r.fields) {
            const e = entities[f.entity];
            if (f.va) { const t = pw.fmtVA(e && e.value); f.val.textContent = t.text; f.va.textContent = t.unit; }
            else if (f.vr) { const t = pw.fmtVar(e && e.value); f.val.textContent = t.text; f.vr.textContent = t.unit; }
            else if (f.vrTable) { const t = pw.fmtVar(e && e.value); f.val.textContent = isNum(e && e.value) ? `${t.text} ${t.unit}` : "—"; }
            else if (f.vaTable) { const t = pw.fmtVA(e && e.value); f.val.textContent = isNum(e && e.value) ? `${t.text} ${t.unit}` : "—"; }
            else f.val.textContent = pw.fmt(e);
          }
          for (const gg of r.gauges) {
            const e = entities[gg.entity];
            if (gg.kind === "combined") {
              const lv = pw.combinedView(gg.ids, entities, gg.rating, m.state === "live");
              gg.fill.style.width = lv.bar + "%";
              gg.text.textContent = lv.ok ? lv.text : "Unavailable: a circuit is not reading";
              gg.word.textContent = lv.word;
              gg.el.className = `pw-track pw-l-${lv.level}`;
            } else if (gg.kind === "load") {
              const lv = pw.loadView(e ? e.value : null, gg.rating);
              gg.fill.style.width = lv.bar + "%";
              gg.text.textContent = lv.text;
              gg.word.textContent = lv.word;
              gg.el.className = `pw-track pw-l-${lv.level}`;
              const dm = e && e.day && isNum(e.day.max) ? 100 * e.day.max / gg.rating : null;
              gg.mark.style.display = dm === null ? "none" : "";
              if (dm !== null) { gg.mark.style.left = Math.min(100, dm) + "%"; gg.mark.title = `Highest today ${SW.num(e.day.max, 2)} A`; }
            } else {
              const cfg = gg.cfg;
              const vv = pw.swingView(e ? e.value : null, cfg);
              gg.text.textContent = e && isNum(e.value) ? `${SW.num(e.value, cfg.dec)} ${cfg.unit}` : "—";
              gg.word.textContent = vv.word;
              gg.el.className = `pw-track pw-vtrack pw-v-${vv.state}`;
              gg.live.style.display = vv.state === "none" ? "none" : "";
              gg.live.style.left = vv.pos + "%";
              const d = e && e.day;
              if (d && isNum(d.min) && isNum(d.max)) {
                const a = pw.swingPos(d.min, cfg), b = pw.swingPos(d.max, cfg);
                gg.band.style.display = ""; gg.band.style.left = a + "%"; gg.band.style.width = Math.max(1, b - a) + "%";
                const lowOut = d.min < cfg.low ? `Went below ${cfg.low} ${cfg.unit} at ${hhmm(d.min_at)}. ` : "";
                const highOut = d.max > cfg.high ? `Went above ${cfg.high} ${cfg.unit} at ${hhmm(d.max_at)}. ` : "";
                gg.sub.textContent = `Today ${SW.num(d.min, cfg.dec)} to ${SW.num(d.max, cfg.dec)} ${cfg.unit}. ${lowOut}${highOut}`.trim();
              } else { gg.band.style.display = "none"; gg.sub.textContent = ""; }
            }
          }
          const mine = Object.values(entities).filter((e) => e.device_id === m.id && e.labels && e.labels.channel);
          const day = pw.dayOf(mine);
          r.dayHead.textContent = day ? `Show day ${SW.fmtDay(day)}, from the readings Stagewatch received` : "";
          r.dayHead.hidden = !day;
          for (const x of r.day) x.val.textContent = pw.dayText(entities[x.entity], x.field);
          for (const x of r.dayEnergy) {
            const t = pw.dayEnergy(entities[x.entity]);
            x.val.textContent = t.text;
            x.unit.textContent = t.unit ? ` ${t.unit}` : "";
            x.sub.textContent = t.sub;
          }
          if (r.neu) {
            const v = pw.neutralShare(...r.neu.ids.map((k) => (entities[k] ? entities[k].value : null)));
            r.neu.val.textContent = v === null ? "—" : `${SW.num(v.pct, 0)}%`;
            r.neu.word.textContent = v && v.high ? " HIGH" : "";
          }
          for (const x of r.demand) x.val.textContent = pw.demandText(entities[x.entity], x.isVA);
          for (const x of r.maxes) {
            const e = entities[x.entity];
            x.val.textContent = e && isNum(e.value) ? `${SW.num(e.value, Math.max(0, Math.min(3, e.decimals || 0)))} ${e.unit || ""}`.trim() : "—";
          }
          if (r.imb) {
            const v = pw.imbalance(...r.imb.ids.map((k) => (entities[k] ? entities[k].value : null)));
            r.imb.val.textContent = v === null ? "—" : `${SW.num(v, 1)}%`;
          }
          const gt = pw.gapText(mine);
          r.gaps.textContent = gt;
          r.gaps.hidden = !gt;
          for (const x of r.energy) {
            const e = entities[x.entity];
            const t = pw.fmtEnergy(e ? e.value : null);
            x.val.textContent = t.text;
            x.unit.textContent = t.unit ? ` ${t.unit}` : "";
          }
        }
      },
    };
  };

  return pw;
})();
