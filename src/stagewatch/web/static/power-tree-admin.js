// Power distribution, Admin card: the pure rules for what the drop-downs offer (no DOM).
// The server checks everything again (core/power_tree.py); this only keeps the choices sensible.
// Stagewatch only reads from the meters: nothing here is ever sent to one.
"use strict";
SW.ptAdmin = (function () {
  const pa = {};
  // `roles` is the list the server sends: [{key, label, abbr, rank}] (rank is null for ups and other).
  pa.display = function (roles, role, no) {
    const r = roles.find((x) => x.key === role);
    if (!r) return "";
    return Number.isInteger(no) && no >= 1 && no <= 99 ? r.abbr + String(no).padStart(2, "0") : r.abbr;
  };
  // What an Upstream option reads: "FDU01 · Stage left racks". A unit with a role but no number yet reads
  // "FDU (no number) · name" and one with no role "name (no role)", so it is clear why the prefix is missing.
  // `no` is the number as typed (a whole number 1 to 99) or null.
  pa.optionLabel = function (roles, role, no, name) {
    const nm = String(name || "").trim();
    const r = roles.find((x) => x.key === role);
    if (!r) return (nm || "Unnamed") + " (no role)";
    const d = pa.display(roles, role, no);
    return [d === r.abbr ? `${r.abbr} (no number)` : d, nm].filter(Boolean).join(" · ");
  };
  // Everything below `ref` (what is fed from it, however far down), so it is never offered as its own upstream.
  pa.descendants = function (nodes, ref) {
    const out = new Set(), todo = [ref];
    while (todo.length) {
      const cur = todo.pop();
      nodes.forEach((n) => { if (n.upstream === cur && !out.has(n.ref)) { out.add(n.ref); todo.push(n.ref); } });
    }
    return out;
  };
  // The refs that may feed `self` if it has `role`. A supply has nothing above it. A ranked role needs a
  // higher level above it (supply, intake, main, final, equipment in that order); a UPS or "other" can sit
  // anywhere; a node with no role yet can pick anything.
  pa.upstreamChoices = function (roles, nodes, self, role) {
    if (role === "source") return [];
    const rank = (k) => { const r = roles.find((x) => x.key === k); return r && r.rank !== null && r.rank !== undefined ? r.rank : null; };
    const below = pa.descendants(nodes, self);
    const mine = rank(role);
    return nodes.filter((n) => {
      if (n.ref === self || below.has(n.ref)) return false;
      const theirs = rank(n.role);
      return mine === null || theirs === null || theirs < mine;
    }).map((n) => n.ref);
  };
  // The next number nobody else has for this role (1, 2, 3 ...). `rows` is [{ref, role, no}]; `self` is left out.
  pa.nextNumber = function (rows, self, role) {
    if (!role) return null;
    const used = new Set(rows.filter((r) => r.ref !== self && r.role === role && Number.isInteger(r.no)).map((r) => r.no));
    for (let n = 1; n <= 99; n++) if (!used.has(n)) return n;
    return null;
  };
  // The likely upstream: the only unit of the nearest higher role among the choices (for example the only CDU
  // for a final distribution unit). "" when there is no single obvious one (then the admin picks).
  pa.suggestUpstream = function (roles, nodes, self, role) {
    const rank = (k) => { const r = roles.find((x) => x.key === k); return r && r.rank !== null && r.rank !== undefined ? r.rank : null; };
    const mine = rank(role);
    if (mine === null || mine === 0) return "";
    const ok = new Set(pa.upstreamChoices(roles, nodes, self, role));
    const ranked = nodes.filter((n) => ok.has(n.ref) && rank(n.role) !== null);
    if (!ranked.length) return "";
    const top = Math.max(...ranked.map((n) => rank(n.role)));
    const best = ranked.filter((n) => rank(n.role) === top);
    return best.length === 1 ? best[0].ref : "";
  };
  // The link label: "CDU02-C2-L1" (way 2, line 1), "CDU01-C1-3P", "CDU02-C2" when no line is known, "CDU02" with no way.
  // Kept equal to core/power_tree.py link_label (a test checks the same cases).
  pa.linkLabel = function (upDisplay, way, line) {
    if (!upDisplay) return "";
    if (!Number.isInteger(way)) return upDisplay;
    return `${upDisplay}-C${way}` + (line ? `-${line}` : "");
  };
  // A message when another node already uses this way of this upstream; "" when fine. `nodes` is [{ref, upstream, way, label}].
  pa.wayClash = function (nodes, self, upstream, way) {
    if (!upstream || !Number.isInteger(way)) return "";
    const other = nodes.find((n) => n.ref !== self && n.upstream === upstream && n.way === way);
    return other ? `Way ${way} is already used by ${other.label || "another unit"}. Each way feeds one thing.` : "";
  };
  // The rows the card shows for a meter: ONE row for a meter with a single circuit (its own settings live on
  // that circuit), or the meter's header row plus one row per circuit. A lone three-phase circuit is the single case.
  pa.meterRows = function (m) {
    const chans = m.channels || [];
    if (m.single) return [{ kind: "meter", ref: `m:${m.id}`, single: true }];
    const shown = chans.filter((c) => !c.three_phase || chans.length > 1);
    return [{ kind: "meter", ref: `m:${m.id}`, single: false }].concat(shown.map((c) => ({ kind: "circuit", ref: `c:${m.id}:${c.key}`, key: c.key })));
  };
  // "gen1" from "FOH generator 1": letters, digits and underscores, as the server keeps ids.
  pa.slug = function (name, taken) {
    const base = String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 36) || "point";
    let id = base, n = 2;
    while (taken.has(id)) id = `${base}_${n++}`;
    return id;
  };
  return pa;
})();
