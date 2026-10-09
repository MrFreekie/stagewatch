// Sound level card: what to say about each value (SW.spl.view, pure) and how the timeline treats
// gaps. The numbers come from the measurement software exactly as it sent them: nothing here
// averages, smooths, rounds beyond the one decimal shown, or fills a gap. A missing value is
// "not available" (a dash), never zero. Text is only ever set with textContent.
"use strict";

SW.spl = (function () {
  const spl = {};

  spl.LINE_STYLES = [[], [7, 4], [2, 3]];   // solid, dashed, dotted: the lines differ by more than colour
  spl.LINE_NAMES = ["solid line", "dashed line", "dotted line"];
  spl.GAP_MIN_S = 10;                       // a break in the line is at least this long

  const WEIGHT = { A: "A-weighted", C: "C-weighted", Z: "Unweighted (Z)" };

  // The chosen values, in slot order (labels.slot is "1".."3").
  spl.entities = function (entities) {
    return Object.values(entities).filter((e) => e && e.kind === "sound_level")
      .sort((a, b) => (Number((a.labels || {}).slot) || 9) - (Number((b.labels || {}).slot) || 9));
  };

  // The label as Smaart writes it for a time-weighted level ("SPL C Slow"); anything else keeps
  // its own name (the LAeq wording is unconfirmed). Display only: ids and stored data are untouched.
  spl.label = function (e) {
    const l = e.labels || {};
    if (typeof l.smaart_name === "string" && l.smaart_name) return l.smaart_name;   // Smaart's own text
    if (l.metric === "SPL" && l.weighting && l.time_constant) return `SPL ${l.weighting} ${l.time_constant}`;
    return e.name || e.id;
  };

  // The input the meter is tied to, as the software names it, or "" (then nothing is shown).
  spl.inputLine = function (device) {
    const n = device && typeof device.input_name === "string" ? device.input_name.trim() : "";
    return n ? `Input: ${n}` : "";
  };

  // One value's location label ("FOH"), or "". Plain text; a label only, never a key.
  spl.location = function (e) {
    return e && typeof e.location === "string" ? e.location.trim() : "";
  };

  // The tile's own location line: none when the card title already shows the one every tile shares.
  spl.tileLocation = function (e, entities) {
    const shared = (entities || []).length > 0 && spl.title(entities) !== "Sound level";
    return shared ? "" : spl.location(e);
  };

  // Saved per-input locations as a Map (an input may be called "constructor" or "__proto__") and back
  // to a plain object for the save. Only non-empty text is kept.
  spl.locMap = function (obj) {
    const m = new Map();
    if (obj && typeof obj === "object") for (const [k, v] of Object.entries(obj)) if (typeof v === "string" && v.trim()) m.set(k, v);
    return m;
  };
  spl.locObject = function (map) {
    const out = {};
    for (const [k, v] of map) if (typeof v === "string" && v.trim()) Object.defineProperty(out, k, { value: v, enumerable: true, writable: true, configurable: true });
    return out;
  };

  // The card title: "Sound level · FOH" only when every shown value has the same non-empty location;
  // otherwise "Sound level" (each value then carries its own location). Plain text for textContent.
  spl.title = function (entities) {
    const locs = (entities || []).map(spl.location);
    return locs.length && locs[0] && locs.every((l) => l === locs[0]) ? `Sound level · ${locs[0]}` : "Sound level";
  };

  // "A-weighted, Slow response" / "A-weighted Leq over 15 min" / "A-weighted peak"
  spl.describe = function (e) {
    const l = e.labels || {};
    const w = WEIGHT[l.weighting] || "";
    if (l.metric === "Leq") return `${w} Leq${l.period ? ` over ${l.period}` : ""}`.trim();
    if (l.metric === "Peak") return `${w} peak`.trim();
    return `${w}${l.time_constant ? `, ${l.time_constant} response` : ""}`.replace(/^, /, "");
  };

  // One value's look. `device` is the Smaart device ({status}) or undefined; `now` server seconds.
  // state: "live" | "stale" (an old value, frozen and dimmed, with its age) | "na" (not available) |
  // "wait" (nothing yet). The text is the number to one decimal or a dash.
  spl.view = function (e, device, now) {
    // When the values read different inputs the card has no single "Input:" line, so each value says
    // which input it is (Smaart's own text).
    const l = e.labels || {};
    const own = !(device && device.input_name) && typeof l.source === "string" ? l.source : "";
    const sub = [spl.describe(e), own].filter((x) => x).join(" · ");
    const has = e.value !== null && e.value !== undefined && !Number.isNaN(e.value);
    if (!e.updated) return { state: "wait", text: "—", sub, note: "Waiting for a value" };
    if (!has) {
      const lost = device && (device.status === "missing" || device.status === "fault");
      return { state: "na", text: "—", sub, note: lost ? "No signal from Smaart" : "Not available" };
    }
    const text = SW.fmt("sound_level", e.value, false);
    if (e.stale) return { state: "stale", text, sub, note: `Old reading, ${SW.age(e.updated, now)}` };
    return { state: "live", text, sub, note: "" };   // no updated time on a live value; an old one shows its age above
  };

  // ---- the graph's vertical range (dB). The numbers are never changed; only where the axis sits.
  spl.AUTO_MIN_SPAN = 20;   // a quiet room does not look dramatic
  spl.CUSTOM_MIN_SPAN = 10;

  // The chosen range from the Smaart device's public fields: {mode: "auto"} or {mode: "custom", min, max}.
  // Anything that is not a sensible custom range (numbers, 0..200, at least 10 dB) means automatic.
  spl.chartRange = function (device) {
    const d = device || {}, lo = d.chart_min_db, hi = d.chart_max_db;
    const ok = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 200;
    if (d.chart_range === "custom" && ok(lo) && ok(hi) && hi - lo >= spl.CUSTOM_MIN_SPAN) return { mode: "custom", min: lo, max: hi };
    return { mode: "auto" };
  };

  // Tidy axis ends for data from lo to hi: 2 dB of room, then outward to multiples of 5, and at
  // least AUTO_MIN_SPAN wide (centred on the data, still on multiples of 5).
  spl.niceRange = function (lo, hi) {
    let a = Math.floor((lo - 2) / 5) * 5, b = Math.ceil((hi + 2) / 5) * 5;
    if (b - a < spl.AUTO_MIN_SPAN) { const mid = (a + b) / 2; a = Math.floor((mid - spl.AUTO_MIN_SPAN / 2) / 5) * 5; b = a + spl.AUTO_MIN_SPAN; }
    return [a, b];
  };

  // The automatic axis for the readings in [t0, t1]. `seriesPoints` is a list of [[ts, value|null], ...].
  // A missing value (null) or a gap never counts. `prev` is the axis in use: it is kept while the data
  // still fits it and it is not much taller than needed, so the scale does not jump on every reading.
  // Returns null when there is nothing to fit.
  spl.autoRange = function (seriesPoints, t0, t1, prev) {
    let lo = Infinity, hi = -Infinity;
    for (const pts of seriesPoints || []) for (const p of pts || []) {
      const v = p[1];
      if (typeof v !== "number" || !Number.isFinite(v) || p[0] < t0 || p[0] > t1) continue;
      lo = Math.min(lo, v); hi = Math.max(hi, v);
    }
    if (!Number.isFinite(lo)) return null;
    const want = spl.niceRange(lo, hi);
    if (Array.isArray(prev) && lo >= prev[0] && hi <= prev[1] && (prev[1] - prev[0]) - (want[1] - want[0]) <= 20) return prev;
    return want;
  };

  // Do any readings in [t0, t1] fall outside a fixed range? {above, below}
  spl.clipFlags = function (seriesPoints, t0, t1, range) {
    const f = { above: false, below: false };
    if (!range) return f;
    for (const pts of seriesPoints || []) for (const p of pts || []) {
      const v = p[1];
      if (typeof v !== "number" || !Number.isFinite(v) || p[0] < t0 || p[0] > t1) continue;
      if (v > range[1]) f.above = true;
      if (v < range[0]) f.below = true;
    }
    return f;
  };

  spl.rangeNote = function (f) {
    const parts = [f.above ? "above the range, drawn at the top edge" : "", f.below ? "below the range, drawn at the bottom edge" : ""].filter((x) => x);
    return parts.length ? `Some readings are ${parts.join(" and some are ")}.` : "";
  };

  // Seconds of silence in a chart line that count as a gap: a few intervals between points, and
  // never less than GAP_MIN_S, so an outage is a break and ordinary spacing is not.
  spl.gapSeconds = function (bucketS) {
    return Math.max(spl.GAP_MIN_S, 3 * (Number(bucketS) || 0));
  };

  // One honest sentence about the chart's points. Nothing is averaged; over a long time the points
  // are the last reading in each interval.
  spl.chartNote = function (spanS, points) {
    const bucket = spanS / Math.max(points, 1);
    if (bucket <= 1.01) return "Each point is a reading exactly as Smaart sent it.";
    return `Each point is the last reading in about ${SW.num(bucket, bucket < 10 ? 1 : 0)} s. Nothing is averaged.`;
  };

  return spl;
})();
