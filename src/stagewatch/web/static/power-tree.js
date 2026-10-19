// Power tree card: supplies at the top, then intake units, main and final distribution, equipment.
// Pure helpers (tested in tests/js/power_tree_test.js) and the DOM builder. Needs common.js and power.js.
//
// Figures come from the meters' own entities (live power and current) and from the figures the server works
// out and records (treecalc.py: the gap at each node, the monitored load of each supply, the site total).
// An unmetered node says "not monitored", never zero. A meter that is not reading shows as such, with no
// figure. Every string from the server is shown with textContent only.
SW.ptree = (function () {
  const t = {};
  const isNum = (x) => typeof x === "number" && isFinite(x);
  const SITE = "power_site";
  const RANK = { source: 0, isu: 1, cdu: 2, fdu: 3, equipment: 4, ups: 5, other: 6 };
  const LINE_WORD = { a: "L1", b: "L2", c: "L3" };

  t.slug = (ref) => String(ref).replace(/:/g, "_");

  // ---- the forest -------------------------------------------------------------------------------------
  // structure = { nodes: [...] } from /api/power-tree. Returns { roots, loose }: each root is a supply with
  // its subtree; "loose" are the nodes with no supply above them (the Unassigned group). Loops cannot happen
  // (the server forbids them) but a node is never visited twice, so bad data cannot hang the page.
  t.forest = function (structure) {
    const nodes = ((structure && structure.nodes) || []).map((n) => ({ ...n, children: [] }));
    const by = Object.fromEntries(nodes.map((n) => [n.ref, n]));
    const order = (a, b) => (a.role in RANK ? RANK[a.role] : 9) - (b.role in RANK ? RANK[b.role] : 9)
      || String(a.display || a.name).localeCompare(String(b.display || b.name), "en", { numeric: true });
    const seen = new Set();
    const attach = (n) => {
      seen.add(n.ref);
      for (const k of nodes) if (k.upstream === n.ref && !seen.has(k.ref)) n.children.push(k);
      n.children.sort(order);
      n.children.forEach(attach);
    };
    const roots = nodes.filter((n) => n.role === "source").sort(order);
    roots.forEach((n) => { n.upstream = ""; attach(n); });
    const loose = nodes.filter((n) => !seen.has(n.ref) && (!n.upstream || !by[n.upstream]) && n.role !== "source").sort(order);
    loose.forEach((n) => { if (!seen.has(n.ref)) attach(n); });
    return { roots, loose };
  };

  // Is there a tree at all? (any node with a role or an upstream)
  t.inUse = (structure) => !!structure && (structure.nodes || []).some((n) => n.role || n.upstream);

  // ---- a node's own reading ---------------------------------------------------------------------------
  const parseRef = function (ref) {
    const p = String(ref).split(":");
    return { kind: p[0], dev: p[1] || "", key: p.slice(2).join(":") };
  };
  // Live current per line from the node's meter entities. { lines: [{label, a}], total, max, ok } where ok is
  // false when any needed current is missing or old (then there is no bar: never a partial figure).
  t.amps = function (node, entities) {
    const r = parseRef(node.ref);
    const list = Object.keys(entities || {}).map((k) => entities[k]).filter((e) => e && e.device_id === r.dev && e.labels
      && e.labels.field === "current" && e.labels.channel && !e.labels.derived && (r.kind === "m" || e.labels.channel === r.key));
    const per = {}, bad = { v: false };
    for (const e of list) {
      const g = e.labels.group || "";
      if (g !== "" && !LINE_WORD[g]) continue;     // neutral, total, average: not a line
      const live = isNum(e.value) && !e.stale;
      if (!live) bad.v = true;
      per[g] = (per[g] || 0) + (live ? e.value : 0);
    }
    const three = ["a", "b", "c"].filter((g) => g in per);
    const lines = three.length ? three.map((g) => ({ label: LINE_WORD[g], a: per[g] })) : ("" in per ? [{ label: "", a: per[""] }] : []);
    const ok = lines.length > 0 && !bad.v;
    return { lines, ok, max: ok ? Math.max(...lines.map((l) => l.a)) : null, sum: ok ? lines.reduce((s, l) => s + l.a, 0) : null,
      three: three.length > 0 };
  };

  // { state: "unmetered" | "waiting" | "none" | "old" | "ok", w, updated, amps }
  t.reading = function (node, entities, devices, now) {
    if (node.kind === "point") return { state: "unmetered", w: null, updated: null, amps: null };
    const r = parseRef(node.ref), dev = (devices || {})[r.dev];
    const ids = r.kind === "m" ? [`${r.dev}.meter_power_load`] : [`${r.dev}.${r.key}_power`, `${r.dev}.${r.key}_total_power`];
    const e = ids.map((id) => (entities || {})[id]).find(Boolean);
    const out = { state: "waiting", w: null, updated: e ? e.updated : null, amps: t.amps(node, entities) };
    const st = dev ? dev.status || "" : "";
    if (!dev || st === "missing" || st === "fault" || st === "compromised") return { ...out, state: "none" };
    if (!e || st === "initializing") return out;
    if (!isNum(e.value)) return { ...out, state: "none" };
    if (e.stale) return { ...out, state: "old", w: e.value };
    return { ...out, state: "ok", w: e.value };
  };

  // ---- the server's calculated figures ----------------------------------------------------------------
  // Gap at a node: { state: "ok" | "incomplete" | "mismatch" | "waiting", watts, reason, ups, nodeW, childrenW }.
  t.gap = function (ref, entities) {
    const e = (entities || {})[`${SITE}.treegap_${t.slug(ref)}`];
    const d = (e && e.day) || null;
    if (!e || !d) return { state: "waiting", watts: null, reason: "", ups: false };
    if (e.stale) return { state: "incomplete", watts: null, reason: "Not updating.", ups: !!d.ups };
    return { state: d.state, watts: d.state === "ok" && isNum(e.value) ? e.value : null, reason: d.reason || "", ups: !!d.ups,
      nodeW: isNum(d.node_w) ? d.node_w : null, childrenW: isNum(d.children_w) ? d.children_w : null };
  };
  // A recorded total: { watts, complete, known } (known false while there is no entity yet).
  t.total = function (eid, entities) {
    const e = (entities || {})[`${SITE}.${eid}`];
    if (!e || !e.day) return { watts: null, complete: false, known: false };
    const live = !e.stale && isNum(e.value);
    return { watts: live ? e.value : null, complete: live && !!e.day.complete, known: true };
  };
  t.sourceTotal = (ref, entities) => t.total(`treesrc_${t.slug(ref)}`, entities);

  // ---- words ------------------------------------------------------------------------------------------
  t.watts = function (v) {
    const f = SW.pw.fmtW(v);
    return { text: f.text, unit: f.unit };
  };
  const STATE_WORD = { unmetered: "NOT MONITORED", waiting: "… WAITING", none: "▲ NO READING", old: "▲ OLD" };
  t.stateWord = (s) => STATE_WORD[s] || "";
  // The unmonitored line for a node: { head, text, level } where level is "", "warn" or "check".
  t.gapLine = function (g) {
    if (g.ups) {
      if (g.state === "ok") return { head: "Loss and charging (calculated)", watts: g.watts, text: "", level: "" };
      if (g.state === "mismatch") return { head: "▲ CHECK", watts: null, text: "It gives out more than it takes in. Check the Upstream, the line and the channel.", level: "check" };
      return { head: "Loss and charging", watts: null, text: g.state === "waiting" ? "Waiting for readings." : "Incomplete: needs the input and what it feeds both reading.", level: "warn" };
    }
    if (g.state === "ok") return { head: "Unmonitored (calculated)", watts: g.watts, text: "", level: "" };
    if (g.state === "mismatch") return { head: "▲ CHECK", watts: null, text: g.reason || "What it feeds reads more than it does.", level: "check" };
    if (g.state === "waiting") return { head: "Unmonitored", watts: null, text: "Waiting for readings.", level: "" };
    return { head: "Unmonitored: incomplete", watts: null, text: g.reason || "", level: "warn" };
  };
  // The load bar for a node: reuses the Power card's bar and its 80 % and 100 % levels.
  t.load = function (node, rd, devices) {
    const dev = (devices || {})[parseRef(node.ref).dev] || {};
    const rating = isNum(node.rating_a) ? node.rating_a : (node.kind === "circuit" || node.kind === "meter") && isNum(dev.rating_a) ? dev.rating_a : null;
    if (!rd.amps || !rd.amps.ok || rd.state !== "ok") return { ...SW.pw.loadView(null, rating), rating };
    // Three lines: the busiest line against the rating. Single-phase circuits of one meter: added together.
    return { ...SW.pw.loadView(rd.amps.three ? rd.amps.max : rd.amps.sum, rating), rating };
  };

  // ---- DOM --------------------------------------------------------------------------------------------
  // build(host) -> { update(structure, entities, devices, now) }. The tree is rebuilt only when its shape,
  // names or open/closed state change; otherwise numbers and classes are set in place.
  t.build = function (host) {
    const h = SW.h, closed = {};
    let key = "", els = {}, last = null;
    const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
    const setCls = (el, c) => { if (el.className !== c) el.className = c; };

    function nodeEl(n, depth) {
      const kids = n.children;
      const e = {
        row: h("div", { class: "pt-row" }),
        id: h("span", { class: "pt-id" }, n.display || n.role_label || "Unit"),
        name: h("span", { class: "pt-name" }, n.name || ""),
        state: h("span", { class: "pt-state" }),
        power: h("span", { class: "pt-power" }, h("span", { class: "pt-num" }), h("span", { class: "unit" })),
        amps: h("div", { class: "pt-amps muted" }),
        figs: null,
        bar: h("div", { class: "pt-bar" }, h("div", { class: "pw-track" }, h("div", { class: "pw-fill" }), h("i", { class: "pw-tick", style: `left:${SW.pw.LOAD_HIGH}%` })),
          h("span", { class: "pw-gword" })),
        gap: h("div", { class: "pt-gap" }, h("span", { class: "pt-gaphead" }), " ", h("span", { class: "pt-gapnum" }), h("span", { class: "pt-gaptext muted" })),
        ups: h("div", { class: "pt-ups muted" }),
        rating: h("div", { class: "pt-rating muted" }),
      };
      const toggle = kids.length ? h("button", { class: "pt-toggle", type: "button", "aria-expanded": closed[n.ref] ? "false" : "true",
        "aria-label": `Show or hide what is fed from ${n.display || n.name || "this unit"}`,
        onclick: () => { closed[n.ref] = !closed[n.ref]; key = ""; if (last) update(...last); } }, closed[n.ref] ? "▸" : "▾") : h("span", { class: "pt-toggle pt-leaf" });
      e.row.append(toggle, h("div", { class: "pt-main" },
        h("div", { class: "pt-head" }, e.id, e.name, e.state, n.role === "ups" ? h("span", { class: "pt-state pt-unmon" }, "OWN SUPPLY BELOW") : null),
        (e.figs = h("div", { class: "pt-figs" }, e.power, e.bar)), e.amps, e.rating, e.ups, e.gap));
      const li = h("li", { class: `pt-node pt-role-${n.role || "none"}`, "data-depth": String(depth) }, e.row);
      if (kids.length && !closed[n.ref]) li.append(h("ul", { class: "pt-kids" }, kids.map((k) => nodeEl(k, depth + 1))));
      e.n = n;
      els[n.ref] = e;
      e.li = li;
      return li;
    }

    const wrap = h("div", { class: "pt" });
    const summary = h("div", { class: "pt-summary" });
    const empty = h("p", { class: "pt-empty" }, "No power distribution set up. Add supplies, units and meters in Admin, under Power distribution.");
    const list = h("div", { class: "pt-forest" });
    const note = h("p", { class: "muted pt-ups-note" }, "Below a UPS is its own supply: its voltage and frequency are not the supply above.");
    wrap.append(empty, summary, list, note);
    host.replaceChildren(wrap);
    let tiles = {};

    function tile(id, label, sub) {
      if (!tiles[id]) {
        tiles[id] = { el: h("div", { class: "pt-tile" }, h("div", { class: "pw-label" }, label),
          h("div", { class: "pt-tilev" }, h("span", { class: "pt-num" }), h("span", { class: "unit" })), h("div", { class: "pt-tilesub muted" })) };
      }
      return tiles[id];
    }
    function fillTile(tl, tot, sub) {
      const f = t.watts(tot.watts);
      setText(tl.el.querySelector(".pt-num"), f.text);
      setText(tl.el.querySelector(".unit"), tot.watts == null ? "" : " " + f.unit);
      setText(tl.el.querySelector(".pt-tilesub"), tot.watts == null ? (tot.known ? "No reading" : "Waiting") : sub + (tot.complete ? "" : " ▲ INCOMPLETE"));
      setCls(tl.el, "pt-tile" + (tot.watts != null && !tot.complete ? " pt-incomplete" : ""));
    }

    function update(structure, entities, devices, now) {
      last = [structure, entities, devices, now];
      const used = t.inUse(structure);
      empty.hidden = used;
      summary.hidden = list.hidden = note.hidden = !used;
      if (!used) return;
      const f = t.forest(structure);
      const sig = JSON.stringify([f.roots, f.loose].map((g) => g.map(function walk(n) { return [n.ref, n.display, n.name, n.role, n.rating_a, n.children.map(walk)]; }))) + Object.keys(closed).filter((k) => closed[k]).join(",");
      if (sig !== key) {
        key = sig; els = {}; tiles = {};
        const groups = f.roots.map((r) => h("section", { class: "pt-group", "data-ref": r.ref }, h("ul", { class: "pt-tree" }, nodeEl(r, 0))));
        if (f.loose.length) groups.push(h("section", { class: "pt-group pt-unassigned" },
          h("h3", {}, "Unassigned"), h("p", { class: "muted pt-ugroup" }, "Not fed from a supply yet. Still counted in the site total."),
          h("ul", { class: "pt-tree" }, f.loose.map((n) => nodeEl(n, 0)))));
        list.replaceChildren(...groups);
        const items = f.roots.map((r) => ({ id: r.ref, label: `${r.display} ${r.name}`.trim() }));
        if (f.loose.length) items.push({ id: "", label: "Unassigned" });
        items.push({ id: "site", label: "Site total" });
        summary.replaceChildren(...items.map((it) => tile(it.id, it.label).el));
        host.dataset.ready = "1";
      }
      // numbers, in place
      for (const ref of Object.keys(els)) {
        const e = els[ref], n = e.n, rd = t.reading(n, entities, devices, now);
        const f2 = t.watts(rd.w);
        const shown = rd.state === "ok" || rd.state === "old";
        e.figs.hidden = rd.state === "unmetered";
        setText(e.power.firstChild, shown ? f2.text : "—");
        setText(e.power.lastChild, shown ? " " + f2.unit : "");
        setText(e.state, t.stateWord(rd.state) + (rd.state === "old" && rd.updated ? ` ${SW.age(rd.updated, now)}` : ""));
        setCls(e.state, "pt-state" + (rd.state === "ok" ? " pt-hide" : rd.state === "unmetered" ? " pt-unmon" : " pt-warn"));
        setCls(e.row, "pt-row" + (rd.state === "old" || rd.state === "none" ? " pt-dim" : ""));
        const lines = rd.amps && rd.amps.ok ? rd.amps.lines.map((l) => `${l.label ? l.label + " " : ""}${SW.num(l.a, 1)}`).join(" · ") + " A" : "";
        setText(e.amps, shown && lines ? lines : "");
        e.amps.hidden = !(shown && lines);
        const ld = t.load(n, rd, devices);
        e.bar.hidden = ld.level === "none";
        e.bar.firstChild.firstChild.style.width = ld.bar + "%";
        setText(e.bar.lastChild, ld.level === "none" ? "" : `${ld.word} ${SW.num(ld.pct, 0)}% of ${SW.num(ld.rating, 0)} A`);
        setCls(e.bar, "pt-bar" + (ld.level === "over" ? " pw-l-over" : ld.level === "high" ? " pw-l-high" : ""));
        const rated = rd.state === "unmetered" && isNum(n.rating_a) ? `Rated ${SW.num(n.rating_a, 0)} A` : (rd.state === "unmetered" && isNum(n.rating_kva) ? `Rated ${SW.num(n.rating_kva, 0)} kVA` : "");
        setText(e.rating, rated);
        e.rating.hidden = !rated;
        // Unmonitored gap (or UPS loss): only where this node is metered and feeds something
        const feeds = n.children.length > 0 && n.kind !== "point";
        e.gap.hidden = !feeds;
        e.ups.hidden = true;
        if (feeds) {
          const g = t.gap(n.ref, entities), gl = t.gapLine(g), gw = t.watts(gl.watts);
          setText(e.gap.children[0], gl.head);
          setText(e.gap.children[1], gl.watts == null ? "" : `${gw.text} ${gw.unit}`);
          setText(e.gap.children[2], gl.text ? " " + gl.text : "");
          setCls(e.gap, "pt-gap" + (gl.level ? " pt-gap-" + gl.level : ""));
          if (g.ups) {
            const inW = t.watts(g.nodeW), outW = t.watts(g.childrenW);
            setText(e.ups, `In ${g.nodeW == null ? "—" : inW.text + " " + inW.unit} → Out ${g.childrenW == null ? "—" : outW.text + " " + outW.unit}`);
            e.ups.hidden = false;
          }
        }
        if (n.role === "ups" && rd.state !== "unmetered" && !n.children.length) { setText(e.ups, "Nothing is fed from it yet."); e.ups.hidden = false; }
      }
      for (const r of f.roots) fillTile(tile(r.ref), t.sourceTotal(r.ref, entities), "monitored only");
      if (f.loose.length) fillTile(tile(""), t.total("tree_unassigned", entities), "monitored only");
      fillTile(tile("site"), t.total("tree_site", entities), "power only, monitored");
    }
    return { update };
  };

  return t;
})();
