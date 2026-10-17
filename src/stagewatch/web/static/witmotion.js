// WitMotion tilt card: words and numbers for the dashboard card (pure; no DOM).
// Advisory only. A gap or a stale reading is "No data", never the last angle. Colour always comes with a word.
"use strict";
SW.wm = (function () {
  const wm = {};
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  wm.NOTE = "Tilt is a guide, not a rigging safety system.";
  // Plain words for each limit level (1 = advisory, 2 = alert, 3 = stop), the same ladder as the alarm bar.
  wm.LEVELS = { 0: "Within limit", 1: "▲ ADVISORY", 2: "▲ ALERT", 3: "■ STOP" };
  const AXES = [
    { key: "tilt_roll", name: "X (roll)", hint: "side to side", axis: "x" },
    { key: "tilt_pitch", name: "Y (pitch)", hint: "front to back", axis: "y" },
  ];
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
        level: isNum(lv) ? lv : null, levelText: isNum(lv) ? wm.LEVELS[Math.min(3, Math.max(0, lv))] : "",
      });
    }
    const age = val("link_age");
    const live = axes.some((a) => a.unit === "°");
    return {
      name: dev.name || "WitMotion tilt sensor",
      linkText: age === null ? "No data from the sensor" : `Last data ${SW.num(age, 1)} s ago`,
      live, axes,
      smoothing: isNum(pub.smoothing_s) && pub.smoothing_s > 0 ? `smoothed over ${pub.smoothing_s} s` : "not smoothed",
      setup: typeof pub.setup_line === "string" ? pub.setup_line : "",
      info: typeof pub.info_line === "string" ? pub.info_line : "",
      warning: typeof pub.warning === "string" ? pub.warning : "",
    };
  };
  return wm;
})();
