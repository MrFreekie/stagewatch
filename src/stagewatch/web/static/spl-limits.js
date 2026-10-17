// Sound level limits: what to say beside each limit (pure helpers, tested in tests/js/spl_limits_test.js).
// The comparison is Smaart's value exactly as received against the entered number: over means
// strictly above. A value that is stale or missing is "no reading", never OK and never zero.
// Text is only ever set with textContent.
"use strict";

SW.splLimits = (function () {
  const L = {};

  // The state of one limit now: "over", "under", "no_reading" or "not_recorded" (no recorded
  // Smaart value matches the limit's metric). ``ent`` is the dashboard's live entity, if any.
  L.status = function (cur, ent) {
    const eid = cur && cur.reading ? cur.reading.entity_id : null;
    if (!eid) return "not_recorded";
    if (!ent || ent.value === null || ent.value === undefined || !Number.isFinite(Number(ent.value)) || ent.stale) return "no_reading";
    return Number(ent.value) > Number(cur.value_db) ? "over" : "under";
  };

  L.WORDS = { over: "OVER", under: "", no_reading: "No reading", not_recorded: "Metric not available" };

  // The frequency weighting from Smaart's metric name ("LAeq 15" → "A", "LCpeak" → "C"), or "".
  L.weight = (metric) => { const m = /^L([ACZ])/i.exec(String(metric || "").trim()); return m ? m[1].toUpperCase() : ""; };
  L.unit = (metric) => (L.weight(metric) ? `dB(${L.weight(metric)})` : "dB");

  L.db = (v) => (v === null || v === undefined ? "—" : String(Number(v)));

  // "Limit now 102 dB until 22:00, then 98" / "Guidance 100 dB" (fmt formats a time).
  L.line = function (cur, fmt) {
    const what = cur.kind === "guidance" ? "Guidance" : "Limit now";
    let s = `${what} ${L.db(cur.value_db)} dB`;
    if (cur.next_ts) {
      s += ` until ${fmt(cur.next_ts)}`;
      s += cur.next_value_db === null || cur.next_value_db === undefined ? ", then none" : `, then ${L.db(cur.next_value_db)}`;
    }
    return s;
  };

  // "in 42 min", "in 1 h 05 min", "now". Counts on the server's clock (serverNow()).
  L.countdown = function (ts, now) {
    const s = Math.max(0, Math.round(ts - now));
    if (s < 60) return "now";
    const m = Math.floor(s / 60), hh = Math.floor(m / 60), mm = m % 60;
    return hh ? `in ${hh} h ${mm < 10 ? "0" : ""}${mm} min` : `in ${m} min`;
  };

  // One decimal, with more only when one decimal would hide that it is above the limit
  // (95.04 over 95 shows "95.04", not "95.0"). Display only.
  L.value = function (ent, st, limit) {
    if (st === "no_reading" || st === "not_recorded" || !ent) return "—";
    const v = Number(ent.value);
    if (limit === undefined || limit === null) return v.toFixed(1);
    for (const d of [1, 2, 3]) {
      const s = v.toFixed(d);
      if ((Number(s) > limit) === (v > limit)) return s;
    }
    return String(v);
  };

  // The limits for one recorded value (matched by entity id), licence limits first.
  L.forEntity = function (data, eid) {
    return ((data && data.current) || []).filter((c) => c.reading && c.reading.entity_id === eid);
  };

  // The headline limit for the Limits card: the first licence limit, else the first guidance.
  L.headline = function (data) {
    const cur = (data && data.current) || [];
    return cur.find((c) => c.kind === "limit") || cur[0] || null;
  };

  // The footer: "Set by X · Ref Y · Rev 3 · Updated 14:05" (parts left out when empty).
  L.footer = function (data, fmt) {
    const parts = [];
    if (data.set_by) parts.push(`Set by ${data.set_by}`);
    if (data.reference) parts.push(`Ref ${data.reference}`);
    if (data.rev) parts.push(`Rev ${data.rev}`);
    if (data.updated) parts.push(`Updated ${fmt(data.updated)}`);
    if (data.copied) parts.push("Copied from another event, not yet confirmed");
    return parts.join(" · ");
  };

  return L;
})();
