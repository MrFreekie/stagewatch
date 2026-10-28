// "Set the clock from this device": the pure helpers (no DOM, no network). The Admin Clock card does the requests.
//
// How the time is worked out. The page asks the server for its time several times. Each sample records when the
// page sent the request, when the answer came back, and the server's time in the answer. The sample with the
// shortest round trip is the most trustworthy (least time for the network to add delay). The server's clock at the
// middle of that round trip is its answer; this device's clock at the same instant is (sent + back) / 2. The
// difference is how far the Pi's clock is from this device. The final request carries the time at which it will
// reach the server: this device's clock when it is sent, plus half the best round trip.
"use strict";
SW.clock = (function () {
  const c = {};
  c.CONFIRM_OVER_S = 5;      // a bigger jump needs the person to confirm
  c.NOTHING_UNDER_S = 1;     // a smaller difference is not worth changing
  c.RTT_MAX_MS = 5000;
  c.PROBES = 6;

  // samples: [{ sent, back, server }] (all in ms; server is the server's epoch ms). Returns the best one worked out, or null.
  c.estimate = function (samples) {
    let best = null;
    for (const s of samples || []) {
      if (!s || ![s.sent, s.back, s.server].every((n) => typeof n === "number" && Number.isFinite(n))) continue;
      const rtt = s.back - s.sent;
      if (rtt < 0 || rtt > c.RTT_MAX_MS) continue;
      if (best === null || rtt < best.rtt) best = { rtt, mid: (s.sent + s.back) / 2, server: s.server };
    }
    if (best === null) return null;
    // positive: this device is ahead of the Pi, so the Pi's clock would move forward
    return { rttMs: best.rtt, diffS: (best.mid - best.server) / 1000 };
  };

  // The time (epoch ms, whole) at which a request sent right now will reach the server.
  c.arrivalMs = function (nowMs, rttMs) { return Math.round(nowMs + rttMs / 2); };

  c.body = function (nowMs, rttMs, confirm) {
    return { epoch_ms: c.arrivalMs(nowMs, rttMs), rtt_ms: Math.max(0, Math.min(c.RTT_MAX_MS, Math.round(rttMs))), confirm_jump: !!confirm };
  };

  c.verdict = function (diffS) {
    const a = Math.abs(diffS);
    if (!Number.isFinite(a)) return "unknown";
    if (a < c.NOTHING_UNDER_S) return "same";
    return a > c.CONFIRM_OVER_S ? "confirm" : "ok";
  };

  // '3 h 12 min', '4 min 05 s', '40 s' (a size, always positive)
  c.sizeWords = function (diffS) {
    const pad = (n) => (n < 10 ? "0" : "") + n;
    const s = Math.round(Math.abs(diffS));
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${pad(s % 60)} s`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ${pad(Math.floor((s % 3600) / 60))} min`;
    return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
  };

  c.confirmText = function (diffS) {
    return `The Pi's clock will jump ${diffS >= 0 ? "forward" : "back"} by ${c.sizeWords(diffS)}. Charts and today's history are stored by time, so do this before the show, not during it.`;
  };

  // How far apart, in words, for the row on the card.
  c.differenceText = function (diffS) {
    if (c.verdict(diffS) === "unknown") return "—";
    if (c.verdict(diffS) === "same") return "Within a second";
    return `${c.sizeWords(diffS)} ${diffS >= 0 ? "behind this device" : "ahead of this device"}`;
  };

  c.ntpText = function (v) {
    if (v === true) return "Yes: the Pi is getting the time from the network";
    if (v === false) return "No: the Pi has no network time right now";
    return "Unknown";
  };
  return c;
})();
