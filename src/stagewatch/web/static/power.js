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

  pw.GROUP_ORDER = ["", "a", "b", "c", "n", "total"];
  pw.GROUP_WORD = { "": "", a: "Phase A", b: "Phase B", c: "Phase C", n: "Neutral", total: "Total" };
  pw.FIELD_WORD = { voltage: "Voltage", current: "Current", power: "Power", apparent: "Apparent power", pf: "Power factor", freq: "Frequency", device_temp: "Meter temperature" };
  pw.UNIT = { voltage: "V", current: "A", power: "W", apparent: "VA", pf: "", freq: "Hz", device_temp: "°C" };

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
  pw.DAY_WORD = { power: "Highest power", current: "Highest current" };

  // The text of one figure row for entity e ("Highest power"/"Highest current"/"Voltage"), or a dash.
  pw.dayText = function (e, field) {
    const d = e && e.day;
    if (!d) return "—";
    if (field === "voltage" || field === "freq") {
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
    return { state: "ok", word: "Within UK range", pos };
  };

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
    const list = Object.keys(channels).sort().map((k) => channels[k]);
    return { id: device.id, rating: isNum(device.rating_a) ? device.rating_a : null, name: device.name || device.id, area: device.area || "", state, badge, note, channels: list, newest, count: mine.length, model: device.model || "" };
  };

  pw.view = function (entities, devices, now) {
    const ents = Object.keys(entities || {}).map((k) => entities[k]);
    const meters = Object.keys(devices || {}).map((k) => devices[k])
      .filter((d) => d && d.category === "meter")
      .sort((a, b) => (a.name || "").localeCompare(b.name || ""))
      .map((d) => pw.meterView(d, ents, now));
    // The key changes only when the set of meters or readings changes, so the DOM is rebuilt rarely.
    const key = meters.map((m) => m.id + "@" + (m.rating || 0) + ":" + m.channels.map((c) => c.id + "[" + Object.keys(c.groups).map((g) => g + "=" + Object.keys(c.groups[g]).join("+")).join(",") + "|" + c.energy.map((x) => x.entity.id).join("+") + "]").join("")).join(";");
    return { meters, key };
  };

  // ------------------------------------------------------------------ DOM
  pw.build = function (host) {
    const h = SW.h;
    const ui = { key: null, meters: {} };

    const cell = (label, unit, big) => {
      const val = h("span", { class: "pw-num" });
      const el = h("div", { class: "pw-cell" + (big ? " pw-big" : "") }, h("div", { class: "pw-label" }, label),
        h("div", { class: "pw-value" }, val, h("span", { class: "unit" }, unit)));
      return { el, val };
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
        const groupKeys = pw.GROUP_ORDER.filter((g) => ch.groups[g]);
        if (groupKeys.length === 1 && groupKeys[0] === "") {
          // One circuit: a big power reading and the rest beside it.
          const g = ch.groups[""];
          const grid = h("div", { class: "pw-grid" });
          for (const f of ["power", "voltage", "current", "freq", "apparent", "pf", "device_temp"]) {
            if (!g[f]) continue;
            const c = cell(pw.FIELD_WORD[f], pw.UNIT[f], f === "power");
            r.fields.push({ entity: g[f].id, val: c.val, el: c.el });
            grid.append(c.el);
          }
          body.append(grid);
        } else {
          // Several phases (or a total): a small table, one row per phase.
          const rows = [];
          const cols = ["current", "voltage", "power"].filter((f) => groupKeys.some((k) => ch.groups[k][f]));
          const extra = ["apparent", "pf"].filter((f) => groupKeys.some((k) => ch.groups[k][f]));
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
        for (const g of pw.GROUP_ORDER.filter((k) => ch.groups[k] && k !== "n" && k !== "total")) {
          const pre = pw.GROUP_WORD[g] ? pw.GROUP_WORD[g] + " " : "";
          const cur = ch.groups[g].current;
          if (cur && m.rating) {
            const fill = h("div", { class: "pw-fill" });
            const mark = h("div", { class: "pw-peakmark" });
            const text = h("span", { class: "pw-gtext" }), word = h("span", { class: "pw-gword" });
            const track = h("div", { class: "pw-track" }, fill, mark, h("i", { class: "pw-tick", style: `left:${pw.LOAD_HIGH}%` }));
            gaugeBox.append(h("div", { class: "pw-gauge" }, h("div", { class: "pw-glabel" }, h("span", { class: "pw-label" }, pre + "Load"), " ", text, " ", word), track,
              h("div", { class: "pw-gscale muted" }, h("span", {}, "0"), h("span", { style: `left:${pw.LOAD_HIGH}%` }, "80%"), h("span", { class: "r" }, "100%"))));
            r.gauges.push({ kind: "load", entity: cur.id, fill, mark, text, word, el: track });
          }
          for (const f of ["voltage", "freq"]) {
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
        for (const g of pw.GROUP_ORDER.filter((k) => ch.groups[k])) {
          for (const f of ["power", "current", "voltage", "freq"]) {
            const e = ch.groups[g][f];
            if (!e) continue;
            const word = f === "voltage" ? "Voltage range" : f === "freq" ? "Frequency range" : pw.DAY_WORD[f];
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
      r.gaps = h("p", { class: "pw-note pw-gaps", role: "status" });
      dayBody.append(r.gaps);
      r.root = h("div", { class: "pw-meter" }, head, r.note, body, r.gauges.length ? gaugeBox : null, dayBody);
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
              const lv = pw.loadView(e ? e.value : null, m.rating);
              gg.fill.style.width = lv.bar + "%";
              gg.text.textContent = lv.text;
              gg.word.textContent = lv.word;
              gg.el.className = `pw-track pw-l-${lv.level}`;
              const dm = e && e.day && isNum(e.day.max) ? 100 * e.day.max / m.rating : null;
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
