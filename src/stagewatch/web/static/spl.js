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
    const sub = spl.describe(e);
    const has = e.value !== null && e.value !== undefined && !Number.isNaN(e.value);
    if (!e.updated) return { state: "wait", text: "—", sub, note: "Waiting for a value" };
    if (!has) {
      const lost = device && (device.status === "missing" || device.status === "fault");
      return { state: "na", text: "—", sub, note: lost ? "No signal from Smaart" : "Not available" };
    }
    const text = SW.fmt("sound_level", e.value, false);
    if (e.stale) return { state: "stale", text, sub, note: `Old reading, ${SW.age(e.updated, now)}` };
    return { state: "live", text, sub, note: "" };
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
