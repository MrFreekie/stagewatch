// WitMotion tilt card: words and numbers for the dashboard card (pure; no DOM).
// Advisory only. A gap or a stale reading is "No data", never the last angle. Colour always comes with a word.
"use strict";
SW.wm = (function () {
  const wm = {};
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  wm.title = (n) => (n > 1 ? "Inclinometers" : "Inclinometer");
  wm.NOTE = "Tilt is a guide, not a rigging safety system.";
  // Plain words for each limit level (1 = advisory, 2 = alert, 3 = stop), the same ladder as the alarm bar.
  wm.LEVELS = { 0: "Within limit", 1: "▲ ADVISORY", 2: "▲ ALERT", 3: "■ STOP" };
  const AXES = [
    { key: "tilt_roll", name: "X (roll)", hint: "side to side", axis: "x" },
    { key: "tilt_pitch", name: "Y (pitch)", hint: "front to back", axis: "y" },
  ];
  // Target angle: the signed difference (angle minus target, across the +-180 wrap), both rounded to the 0.1 deg
  // the sensor reads to, and which band it is in. Display only; no alarm comes from it.
  const r1 = (x) => Math.round(x * 10) / 10;
  wm.wrap = (d) => ((((d + 180) % 360) + 360) % 360) - 180;
  wm.BANDS = { on: "✓ On target", close: "◆ Close", off: "✕ Off target" };
  wm.targetView = function (value, t) {
    if (!t || !isNum(t.target)) return null;
    const out = { targetText: `Target ${SW.num(t.target, 1)}°`, band: "", bandText: "", diffText: "" };
    if (!isNum(value)) return out;   // no data: no colour, no difference
    const d = r1(wm.wrap(r1(value) - r1(t.target)));
    const a = Math.abs(d), on = isNum(t.on) ? t.on : 0.2, close = Math.max(on, isNum(t.close) ? t.close : 1.0);
    out.band = a <= on + 1e-9 ? "on" : a <= close + 1e-9 ? "close" : "off";
    out.bandText = wm.BANDS[out.band];
    out.diffText = `${d > 0 ? "+" : ""}${SW.num(d, 1)}° from target`;
    return out;
  };
  // The sensors Stagewatch has set up, in order (the first is plain "witmotion", then witmotion_2 ...).
  wm.sensorsOf = function (devices) {
    return Object.values(devices || {}).filter((d) => d && d.integration === "witmotion").sort((a, b) => (a.id < b.id ? -1 : 1));
  };
  // What to draw for one sensor: { name, linkText, live, axes: [{ key, label, hint, text, unit, level, levelText }], setup, info, warning }.
  wm.view = function (dev, entities) {
    const pub = dev.witmotion || {};
    const ent = (k) => (entities || {})[`${dev.id}.${k}`];
    const val = (k) => {
      const e = ent(k);
      return e && !e.stale && isNum(e.value) ? e.value : null;
    };
    const limits = pub.limits || {};
    const axes = [];
    for (const a of AXES) {
      if (!ent(a.key)) continue;   // an axis that is not watched has no reading at all
      const v = val(a.key);
      const lv = v === null ? null : limits[a.key];
      axes.push({
        key: a.key, label: a.name, hint: a.hint, text: v === null ? "No data" : SW.num(v, 1), unit: v === null ? "" : "°",
        target: wm.targetView(v, (pub.targets || {})[a.key]),
        level: isNum(lv) ? lv : null, levelText: isNum(lv) ? wm.LEVELS[Math.min(3, Math.max(0, lv))] : "",
      });
    }
    const age = val("link_age");
    const pct = val("battery_pct");
    const live = axes.some((a) => a.unit === "°");
    return {
      name: dev.name || "WitMotion tilt sensor",
      linkText: age === null ? "No data from the sensor" : `Last data ${SW.num(age, 1)} s ago`,
      live, axes,
      // Battery: approximate, worked out from the voltage. Low below 20 %, with the word as well as the colour.
      battery: pct === null ? null : { pct: Math.max(0, Math.min(100, Math.round(pct))), low: pct < 20 },
      smoothing: isNum(pub.smoothing_s) && pub.smoothing_s > 0 ? `smoothed over ${pub.smoothing_s} s` : "not smoothed",
      setup: typeof pub.setup_line === "string" ? pub.setup_line : "",
      info: typeof pub.info_line === "string" ? pub.info_line : "",
      warning: typeof pub.warning === "string" ? pub.warning : "",
    };
  };
  return wm;
})();
