// RME interfaces, Admin card: the words and checks (pure; no DOM). The server checks everything again;
// these only say it in crew words. Read-only toward the units: nothing here is ever sent to a unit except
// by the server, and the server only listens. Serial numbers never reach this page (only "set / not set").
"use strict";
SW.rmeAdmin = (function () {
  const ra = {};
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  ra.MAX = 8;
  ra.SECURITY = "RME units have no password: anyone on the network can change them. Stagewatch only reads.";
  ra.NOTE = "Tried on one RME 12Mic only. Other RME units are untested.";

  // What to check, by the category the Test answer carries. The server's own sentence says what happened.
  ra.ADVICE = {
    not_local: "Use the unit's own address on your network, for example 192.168.1.50. Host names are not accepted.",
    unreachable: "Check the unit is switched on, the address is right, and this computer can reach the unit's network.",
    not_rme: "The address belongs to something else. Check it against the unit's own display or RME's page.",
    silent: "Try Test again. If it keeps happening, restart the unit.",
    quiet: "Try Test again. Check the network cable between this computer and the unit.",
    other_unit: "The address was reused, or the unit was swapped. After a swap, use Replace unit under Advanced.",
    redirect: "Check the address. The unit should answer at its own address.",
    too_large: "Tell the system admin: this unit sends more than Stagewatch accepts.",
    closed: "Try Test again. A unit that limits how many remote controls it accepts may refuse a second one.",
    bad_frames: "Tell the system admin: the unit sent data Stagewatch could not read.",
    flood: "Tell the system admin: the unit sent far more data than expected.",
    waiting: "Try Test again in a few seconds.",
  };

  // The Test answer as lines to show. { ok, lines: [text], warn: bool }. Only the model, firmware and name the
  // unit gave (the server already cleaned them). An unknown category gets no advice rather than a guess.
  ra.testView = function (r) {
    if (!r || typeof r !== "object") return { ok: false, lines: ["Could not read the answer. Nothing has been changed."], warn: true };
    if (r.ok === true) {
      const lines = [typeof r.message === "string" && r.message ? r.message : "Reading works."];
      if (r.already_added) lines.push("This unit has already been added.");
      lines.push("Nothing has been changed on the unit.");
      return { ok: true, lines, warn: false, already: !!r.already_added, name: typeof r.name === "string" ? r.name : "" };
    }
    const lines = [typeof r.message === "string" && r.message ? r.message : "That did not work."];
    const advice = Object.prototype.hasOwnProperty.call(ra.ADVICE, r.category) ? ra.ADVICE[r.category] : "";
    if (advice) lines.push(advice);
    lines.push("Nothing has been changed.");
    return { ok: false, lines, warn: true };
  };

  const ageWords = (s) => (!isNum(s) ? "" : s < 10 ? "a few seconds" : s < 60 ? `${Math.round(s / 10) * 10} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

  // One line of facts under a unit's name: model, firmware and how long ago Stagewatch last heard from it.
  ra.facts = function (u) {
    u = u || {};
    const parts = [];
    if (u.model) parts.push(String(u.model).slice(0, 40));
    if (u.firmware) parts.push(`firmware ${String(u.firmware).slice(0, 30)}`);
    if (u.phase !== "up" && isNum(u.last_seen_age_s)) parts.push(`last heard ${ageWords(u.last_seen_age_s)} ago`);
    return parts.join(" · ");
  };

  // The one status the card header chip shows: the worst unit decides.
  ra.chipInput = function (units, now) {
    const list = Array.isArray(units) ? units : [];
    if (!list.length) return null;
    const st = (u) => u.status;
    if (list.some((u) => st(u) === "missing" || u.phase === "down")) return { source: "rme", reported: "offline", dataAt: null };
    if (list.some((u) => st(u) === "initializing" || u.phase === "starting" || u.phase === "waiting" || u.phase === "off")) return { source: "rme", reported: "waiting", dataAt: null };
    return { source: "rme", reported: "ok", dataAt: now };
  };

  // Body for POST /api/admin/rme/test and /api/admin/rme. A port is sent only when typed (80 is the default).
  ra.addBody = function (host, port, name, area) {
    const b = { host: String(host || "").trim() };
    const p = Number(port);
    if (port !== "" && port !== null && port !== undefined && Number.isInteger(p) && p >= 1 && p <= 65535) b.port = p;
    if (name !== undefined) { b.name = String(name || "").trim(); b.area = String(area || "").trim(); }
    return b;
  };
  ra.portOk = (v) => v === "" || (/^\d{1,5}$/.test(String(v)) && Number(v) >= 1 && Number(v) <= 65535);

  // Body for PUT /api/admin/rme/{id}: only what changed. `replace` means new hardware at this name.
  ra.patchBody = function (saved, now, replace) {
    const b = {};
    if (now.name !== saved.name) b.name = now.name;
    if (now.area !== saved.area) b.area = now.area;
    if (now.host !== saved.host) b.host = now.host;
    if (Number(now.port) !== Number(saved.port)) b.port = Number(now.port);
    if (replace) { b.replace_unit = true; if (b.host === undefined) b.host = now.host; if (b.port === undefined) b.port = Number(now.port); }
    return b;
  };

  ra.removeText = (name) => `Remove the RME unit ${name}? Stagewatch stops listening to it. Its history and markers are kept, and it can be added again.`;
  ra.replaceText = (name) => `Use this only when you have swapped the hardware at ${name}. Stagewatch will take the unit that answers at this address as ${name}, and keep its history. Carry on?`;
  return ra;
})();
