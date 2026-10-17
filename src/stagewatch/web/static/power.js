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

  pw.GROUP_ORDER = ["", "a", "b", "c", "n", "total", "l12", "l23", "l31", "avg"];
  // Line-to-line voltage groups (from meters that report them): shown as swing meters and rows, never in the phase table.
  pw.LL = ["l12", "l23", "l31", "avg"];
  pw.GROUP_WORD = { "": "", a: "Line 1 (Brown)", b: "Line 2 (Black)", c: "Line 3 (Grey)", n: "Neutral (Blue)", total: "Total",
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
  pw.FIELD_WORD = { voltage_ll: "Line-to-line voltage", reactive: "Reactive power", thd_v: "Voltage THD", thd_i: "Current THD", voltage: "Voltage", current: "Current", power: "Power", apparent: "Apparent power", pf: "Power factor", freq: "Frequency", device_temp: "Meter temperature" };
  pw.UNIT = { voltage_ll: "V", reactive: "var", thd_v: "%", thd_i: "%", voltage: "V", current: "A", power: "W", apparent: "VA", pf: "", freq: "Hz", device_temp: "°C" };

  // Power and energy are shown with a comma for thousands and a full stop for decimals.
  pw.fmt = function (e) {
    if (!e || !isNum(e.value)) return "—";
    return SW.num(e.value, Math.max(0, Math.min(3, e.decimals || 0)));
  };

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
      okWord: "Within typical range", note: "Typical 400 V band 376 to 440 V (advisory)", na: "Not a 400 V line-to-line supply: not judged" },
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

  // Phase imbalance, estimated from the three phase currents: the biggest difference from the average,
  // as a percentage of the average. Only when all three are real numbers and the average is above 0.
  pw.imbalance = function (a, b, c) {
    if (![a, b, c].every(isNum)) return null;
    const avg = (a + b + c) / 3;
    if (!(avg > 0)) return null;
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
    return { state: "ok", word: cfg.okWord || "Within UK range", pos };
  };

  // ---- history chart: one measurement of one meter over time (one line per phase on a 3-phase meter).
  pw.METRICS = [["power", "Power"], ["current", "Current"], ["voltage", "Voltage"], ["voltage_ll", "Line-to-line"], ["freq", "Frequency"], ["thd_v", "Voltage THD"], ["thd_i", "Current THD"]];
  pw.SPANS = [[900, "15m"], [3600, "1h"], [0, "Show"]];   // 0 = since the show started
  pw.POINTS = 500;
  // The lines to draw: [{ id, label, unit, decimals }] for one meter's field, in phase order.
  pw.chartSeries = function (entities, deviceId, field) {
    const out = [];
    for (const e of Object.keys(entities || {}).map((k) => entities[k])) {
      if (e.device_id !== deviceId || !e.labels || e.labels.field !== field || e.kind === "energy") continue;
      const g = e.labels.group || "";
      out.push({ id: e.id, label: pw.GROUP_WORD[g] || pw.FIELD_WORD[field], unit: e.unit || "", decimals: e.decimals || 0, order: pw.GROUP_ORDER.indexOf(g) });
    }
    return out.sort((a, b) => a.order - b.order);
  };
  // A silence longer than this between two points breaks the line (the meter was not read): never joined up.
  // The history chart's vertical scale for a measurement: voltage fixed to the UK range, frequency
  // to 50 Hz ± 1 Hz (a reading outside is drawn at the edge with a mark), power and current from 0
  // with the top following the readings. Anything else scales to the readings.
  pw.chartScale = function (field) {
    if (field === "voltage") return { fixed: [pw.SWING.voltage.low, pw.SWING.voltage.high], def: null };
    if (field === "voltage_ll") return { fixed: [pw.SWING.voltage_ll.low, pw.SWING.voltage_ll.high], def: null };
    if (field === "freq") return { fixed: [49, 51], def: null };
    if (field === "power" || field === "current" || field === "apparent") return { fixed: null, def: [0, 1e-6] };
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

  const ageText = (updated, now) => (updated ? SW.age(updated, now) : "never");

  // One meter's readings, grouped. entities is the snapshot's entity map (id -> entity), device the
  // public device. Returns null when the device has no readings at all.
  pw.meterView = function (device, ents, now) {
    const mine = ents.filter((e) => e.device_id === device.id && e.labels && e.labels.channel);
    const status = device.status || "";
    const newest = Math.max(0, ...mine.map((e) => e.updated || 0));
    const anyStale = mine.length > 0 && mine.some((e) => e.stale);
    let state = "live", badge = "OK", note = "";
    if (status === "initializing" || (status === "ok" && !mine.length)) { state = "waiting"; badge = "… WAITING"; note = "Waiting for the first reading"; }
    else if (status === "missing") { state = "frozen"; badge = "▲ NO READING"; note = `Not answering. Showing the last readings, ${ageText(newest, now)}.`; }
    else if (status === "fault" || status === "compromised") { state = "frozen"; badge = "▲ PROBLEM"; note = (device.status_detail || "Cannot read this meter") + (newest ? `. Last readings ${ageText(newest, now)}.` : "."); }
    else if (anyStale) { state = "frozen"; badge = "▲ OLD"; note = `Readings are old (${ageText(newest, now)}).`; }
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
    return { id: device.id, entities: Object.fromEntries(mine.map((e) => [e.id, e])), rating: isNum(device.rating_a) ? device.rating_a : null, name: device.name || device.id, area: device.area || "", state, badge, note, channels: list, newest, count: mine.length, model: device.model || "" };
  };

  pw.view = function (entities, devices, now) {
    const ents = Object.keys(entities || {}).map((k) => entities[k]);
    const meters = Object.keys(devices || {}).map((k) => devices[k])
      .filter((d) => d && d.category === "meter")
      .sort((a, b) => (a.name || "").localeCompare(b.name || ""))
      .map((d) => pw.meterView(d, ents, now));
    // The key changes only when the set of meters or readings changes, so the DOM is rebuilt rarely.
    const key = meters.map((m) => m.id + "@" + (m.rating || 0) + ":" + m.channels.map((c) => c.id + "~" + (c.rating || 0) + (c.cfg ? (c.cfg.label || "") + (c.cfg.phase || "") : "") + "[" + Object.keys(c.groups).map((g) => g + "=" + Object.keys(c.groups[g]).join("+")).join(",") + "|" + c.energy.map((x) => x.entity.id).join("+") + "]").join("")).join(";");
    return { meters, key };
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
            const c = cell(pw.FIELD_WORD[f], pw.UNIT[f]);
            r.fields.push({ entity: g[f].id, val: c.val, el: c.el });
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
              if (e) r.fields.push({ entity: e.id, val: td, el: td, plain: true });
              return td;
            });
            rows.push(h("tr", {}, h("th", { scope: "row" }, pw.GROUP_WORD[k] || ""), ...tds));
          }
          body.append(h("div", { class: "table-scroll" }, h("table", { class: "pw-table" },
            h("thead", {}, h("tr", {}, h("th", {}, ""), ...colsAll.map((f) => h("th", { class: "num" }, `${pw.FIELD_WORD[f]}${pw.UNIT[f] ? " (" + pw.UNIT[f] + ")" : ""}`)))),
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
          body.append(h("div", { class: "pw-energy" }, h("span", { class: "pw-label" }, word[0].toUpperCase() + word.slice(1)), " ", val, unit,
            h("span", { class: "pw-sub muted" }, " (the meter's running total)")));
          r.energy.push({ entity: x.entity.id, val, unit });
        }
      }
      // Load bar (needs a rating) and swing meters for voltage and frequency, per circuit or phase.
      r.gauges = [];
      const gaugeBox = h("div", { class: "pw-gauges" });
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
      const dayBody = h("div", { class: "pw-day" }, r.dayHead);
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
        dayBody.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, "Phase imbalance"), " ", val, h("span", { class: "pw-sub muted" }, " (estimated from the three phase currents)")));
        r.imb = { ids: [ph.groups.a.current.id, ph.groups.b.current.id, ph.groups.c.current.id], val };
      }
      r.neu = null;
      const nph = m.channels.find((c) => c.groups.n && c.groups.n.current && c.groups.a && c.groups.b && c.groups.c && c.groups.a.current && c.groups.b.current && c.groups.c.current);
      if (nph) {
        const val = h("span", { class: "pw-num" }), word = h("span", { class: "pw-gword" });
        dayBody.append(h("div", { class: "pw-dayrow" }, h("span", { class: "pw-label" }, "Neutral current"), " ", val, word, h("span", { class: "pw-sub muted" }, " of the biggest line current (advisory)")));
        r.neu = { ids: [nph.groups.n.current.id, nph.groups.a.current.id, nph.groups.b.current.id, nph.groups.c.current.id], val, word };
      }
      r.maxes = pw.meterMaxRows(Object.values(m.entities || {}), m.id).map((x) => {
        const val = h("span", { class: "pw-num" });
        dayBody.append(h("div", { class: "pw-dayrow pw-metermax" }, h("span", { class: "pw-label" }, "Meter's own maximum: " + x.label), " ", val, h("span", { class: "pw-sub muted" }, " (kept by the meter since its last reset)")));
        return { entity: x.entity, val };
      });
      r.gaps = h("p", { class: "pw-note pw-gaps", role: "status" });
      dayBody.append(r.gaps);
      // The show-day figures (highest, lowest, energy used ...) fold away under one line, closed until
      // someone opens it; the choice holds while the page is open.
      const dayFold = h("details", { class: "pw-dayfold" }, h("summary", {}, "Show day figures (highest, lowest, energy used)"), dayBody);
      dayFold.open = !!pw.dayFoldOpen[m.id];
      dayFold.addEventListener("toggle", () => { pw.dayFoldOpen[m.id] = dayFold.open; });
      r.root = h("div", { class: "pw-meter" }, head, r.note, body, r.gauges.length ? gaugeBox : null, dayFold);
      return r;
    }

    return {
      update(view, entities) {
        if (view.key !== ui.key) {
          ui.key = view.key;
          ui.meters = {};
          host.replaceChildren(...view.meters.map((m) => { const r = buildMeter(m); ui.meters[m.id] = r; return r.root; }));
        }
        for (const m of view.meters) {
          const r = ui.meters[m.id];
          if (!r) continue;
          r.name.textContent = m.name;
          r.area.textContent = m.area ? ` · ${m.area}` : "";
          r.badge.textContent = m.badge;
          r.badge.className = `pw-badge pw-b-${m.state}`;
          r.note.textContent = m.note;
          r.note.hidden = !m.note;
          r.root.className = `pw-meter pw-s-${m.state}`;
          for (const f of r.fields) {
            const e = entities[f.entity];
            f.val.textContent = pw.fmt(e);
          }
          for (const gg of r.gauges) {
            const e = entities[gg.entity];
            if (gg.kind === "load") {
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
                gg.sub.textContent = `Today ${SW.num(d.min, cfg.dec)} to ${SW.num(d.max, cfg.dec)} ${cfg.unit}. ${lowOut}${highOut}${cfg.note}.`;
              } else { gg.band.style.display = "none"; gg.sub.textContent = cfg.note + "."; }
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
