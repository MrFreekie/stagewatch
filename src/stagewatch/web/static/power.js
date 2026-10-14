// Power card: watts, volts, amps, frequency and energy from power meters (Shelly), as the meters
// reported them. SW.pw.view works out what to show (pure, no DOM); SW.pw.build makes the card's DOM
// once and updates it in place. Readings are Equipment readings: they are never averaged, smoothed or
// part of any site value. Energy is the meter's running total (Wh), shown as Wh or kWh and never as a
// level. Apparent power and power factor appear only when a meter sends them.
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
    return { id: device.id, name: device.name || device.id, area: device.area || "", state, badge, note, channels: list, newest, count: mine.length, model: device.model || "" };
  };

  pw.view = function (entities, devices, now) {
    const ents = Object.keys(entities || {}).map((k) => entities[k]);
    const meters = Object.keys(devices || {}).map((k) => devices[k])
      .filter((d) => d && d.category === "meter")
      .sort((a, b) => (a.name || "").localeCompare(b.name || ""))
      .map((d) => pw.meterView(d, ents, now));
    // The key changes only when the set of meters or readings changes, so the DOM is rebuilt rarely.
    const key = meters.map((m) => m.id + ":" + m.channels.map((c) => c.id + "[" + Object.keys(c.groups).map((g) => g + "=" + Object.keys(c.groups[g]).join("+")).join(",") + "|" + c.energy.map((x) => x.entity.id).join("+") + "]").join("")).join(";");
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
      const r = { fields: [], energy: [] };
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
      r.root = h("div", { class: "pw-meter" }, head, r.note, body);
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
