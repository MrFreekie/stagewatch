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
  // "gen1" from "FOH generator 1": letters, digits and underscores, as the server keeps ids.
  pa.slug = function (name, taken) {
    const base = String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 36) || "point";
    let id = base, n = 2;
    while (taken.has(id)) id = `${base}_${n++}`;
    return id;
  };
  return pa;
})();
