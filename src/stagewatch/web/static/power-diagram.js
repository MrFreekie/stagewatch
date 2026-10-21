// Power block diagram: supplies on the left, then intake units, main and final distribution, equipment on the
// right, one box per unit, joined by elbow lines. Drawn as inline SVG from the same structure the Power tree
// card uses (names, roles, upstream links: nothing new is recorded). Used by the Admin "Power distribution"
// card (a live preview of what is typed, before saving) and by a "Diagram" view on the Power tree card.
// layout() is pure (tested in tests/js/power_diagram_test.js); mount() is the DOM. Every string is set with
// textContent or setAttribute only, never as HTML. Stagewatch only reads: nothing here is sent to a meter.
SW.ptDiagram = (function () {
  const d = {};
  const SVGNS = "http://www.w3.org/2000/svg";
  const RANK = { source: 0, isu: 1, cdu: 2, fdu: 3, equipment: 4, ups: 5, other: 6 };
  // Box and spacing sizes in diagram units (1 unit = 1 px at 100 %).
  d.W = 200; d.H = 52; d.H_LIVE = 88; d.COL_GAP = 100; d.ROW_GAP = 12; d.PAD = 12; d.HEAD = 26; d.GROUP_GAP = 24; d.ELBOW = 24;
  d.CAP = 200;
  d.clip = (s, n) => { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, Math.max(n - 1, 1)) + "…" : s; };
  const order = (a, b) => (a.role in RANK ? RANK[a.role] : 9) - (b.role in RANK ? RANK[b.role] : 9)
    || String(a.display || a.name || "").localeCompare(String(b.display || b.name || ""), "en", { numeric: true });

  // nodes: [{ref, name, display, role, role_label, upstream, feed, ups_type}]. Returns
  // { boxes: [{ref, node, x, y, w, h, col, loop}], links: [{from, to, d, label, lx, ly}], groups: [{title, y, unassigned}],
  //   width, height, total, drawn, hidden }.
  // Supplies are roots, one group each (stacked). Units with no supply above them (or only a loop above them) go in
  // one Unassigned group. A unit is placed once, so a loop in the data can never hang the page.
  d.layout = function (nodes, opts) {
    const o = Object.assign({ live: false, cap: d.CAP }, opts);
    const list = (nodes || []).filter((n) => n && n.ref);
    const by = {};
    list.forEach((n) => { if (!by[n.ref]) by[n.ref] = n; });
    const kids = {};
    Object.keys(by).forEach((k) => { const n = by[k]; if (n.role !== "source" && n.upstream && by[n.upstream]) (kids[n.upstream] || (kids[n.upstream] = [])).push(n); });
    Object.keys(kids).forEach((k) => kids[k].sort(order));
    const seen = new Set();
    const h = o.live ? d.H_LIVE : d.H;
    let drawn = 0;
    const total = Object.keys(by).length;
    const groups = [];
    const walk = (n, col, loop) => {                    // a tree record, or null when over the cap
      if (seen.has(n.ref) || drawn >= o.cap) return null;
      seen.add(n.ref); drawn++;
      const rec = { node: n, col, loop: !!loop, kids: [] };
      (kids[n.ref] || []).forEach((k) => { const r = walk(k, col + 1, loop); if (r) rec.kids.push(r); });
      return rec;
    };
    const roots = Object.keys(by).map((k) => by[k]).filter((n) => n.role === "source").sort(order);
    roots.forEach((n) => { const r = walk(n, 0, false); if (r) groups.push({ title: `Supply: ${n.name || n.display || "unnamed"}`, unassigned: false, recs: [r] }); });
    const loose = Object.keys(by).map((k) => by[k]).filter((n) => !seen.has(n.ref) && n.role !== "source" && (!n.upstream || !by[n.upstream])).sort(order);
    const uRecs = [];
    loose.forEach((n) => { const r = walk(n, 0, false); if (r) uRecs.push(r); });
    Object.keys(by).map((k) => by[k]).filter((n) => !seen.has(n.ref)).sort(order).forEach((n) => { const r = walk(n, 0, true); if (r) uRecs.push(r); });
    if (uRecs.length) groups.push({ title: "Unassigned", unassigned: true, recs: uRecs });

    const boxes = [], links = [], outGroups = [];
    let y = d.PAD, maxCol = 0;
    const showHead = groups.length > 1 || (groups.length === 1 && groups[0].unassigned);
    groups.forEach((g) => {
      let row = 0;
      const place = (rec) => {
        if (!rec.kids.length) rec.row = row++;
        else { rec.kids.forEach(place); rec.row = (rec.kids[0].row + rec.kids[rec.kids.length - 1].row) / 2; }
      };
      g.recs.forEach(place);
      if (showHead) { outGroups.push({ title: g.title, y: y + 16, unassigned: g.unassigned }); y += d.HEAD; }
      const top = y;
      const emit = (rec, parent) => {
        const b = { ref: rec.node.ref, node: rec.node, col: rec.col, x: d.PAD + rec.col * (d.W + d.COL_GAP), y: top + rec.row * (h + d.ROW_GAP), w: d.W, h, loop: rec.loop };
        boxes.push(b);
        if (rec.col > maxCol) maxCol = rec.col;
        if (parent) {
          const x1 = parent.x + d.W, y1 = parent.y + h / 2, y2 = b.y + h / 2, xm = x1 + d.ELBOW;
          links.push({ from: parent.ref, to: b.ref, d: `M${x1} ${y1}H${xm}V${y2}H${b.x}`, label: rec.node.feed || "", lx: xm + 4, ly: y2 - 4 });
        }
        rec.kids.forEach((k) => emit(k, b));
      };
      g.recs.forEach((r) => emit(r, null));
      y = top + row * (h + d.ROW_GAP) - d.ROW_GAP + d.GROUP_GAP;
    });
    return { boxes, links, groups: outGroups, width: boxes.length ? d.PAD * 2 + maxCol * (d.W + d.COL_GAP) + d.W : 0,
      height: boxes.length ? y - d.GROUP_GAP + d.PAD : 0, total, drawn, hidden: total - drawn };
  };

  // ---- drawing ----------------------------------------------------------------------------------------
  // One style block inside the svg, so a saved image looks the same with no page around it. Colours are the
  // theme tokens; a saved copy has them filled in. Role is always in the text, never only a colour.
  const CSS = [
    ".pd-box{fill:var(--panel-2);stroke:var(--muted);stroke-width:1.5}",
    ".pd-node{cursor:pointer}.pd-node:focus{outline:none}.pd-node:focus .pd-box{stroke:var(--accent);stroke-width:4}",
    ".pd-anc .pd-box{stroke:var(--accent);stroke-width:2.5}.pd-sel .pd-box{stroke:var(--accent);stroke-width:4.5}",
    ".pd-high .pd-box{stroke:var(--warn);stroke-width:3.5}.pd-over .pd-box{stroke:var(--stop);stroke-width:4;stroke-dasharray:7 3}",
    ".pd-loop .pd-box{stroke:var(--stop);stroke-width:3;stroke-dasharray:7 3}",
    ".pd-unmon .pd-box{stroke-dasharray:3 3}",
    ".pd-t1{fill:var(--text);font:700 14px system-ui,Segoe UI,sans-serif}.pd-t2{fill:var(--text);font:13px system-ui,Segoe UI,sans-serif}",
    ".pd-t3{fill:var(--text);font:600 13px system-ui,Segoe UI,sans-serif}.pd-t4{fill:var(--muted);font:12px system-ui,Segoe UI,sans-serif}",
    ".pd-link{fill:none;stroke:var(--muted);stroke-width:1.5}.pd-on{stroke:var(--accent);stroke-width:3}",
    ".pd-lab{fill:var(--muted);font:11px system-ui,Segoe UI,sans-serif}.pd-head{fill:var(--text);font:600 14px system-ui,Segoe UI,sans-serif}",
    ".pd-track{fill:var(--grid)}.pd-fill{fill:var(--accent)}.pd-tick{stroke:var(--text);stroke-width:1}",
    "@media print{.pd-bg{fill:#fff}.pd-box{fill:#fff;stroke:#000}.pd-t1,.pd-t2,.pd-t3,.pd-t4,.pd-head,.pd-lab{fill:#000}.pd-link{stroke:#000}.pd-on,.pd-anc .pd-box,.pd-sel .pd-box{stroke:#000}.pd-track{fill:#ddd}.pd-fill{fill:#000}.pd-tick{stroke:#000}}",
  ].join("\n");
  const el = function (name, attrs, text) {
    const e = document.createElementNS(SVGNS, name);
    for (const k of Object.keys(attrs || {})) e.setAttribute(k, String(attrs[k]));
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  };
  // What a box says about the live reading (live mode only): { text, bar, level, word, cls }. Reuses the Power tree
  // card's reading and load logic (the lower of breaker and device limit); nothing is invented for an unmetered unit.
  d.liveInfo = function (n, entities, devices, now, byDev) {
    const P = SW.ptree;
    if (!P || n.kind === "point") return { text: "not monitored", bar: null, cls: " pd-unmon" };
    const rd = P.reading(n, entities, devices, now, byDev);
    if (rd.state === "unmetered") return { text: "not monitored", bar: null, cls: " pd-unmon" };
    if (rd.state === "waiting") return { text: "waiting for a reading", bar: null, cls: "" };
    if (rd.state === "none") return { text: "▲ no reading", bar: null, cls: "" };
    const f = P.watts(rd.w), ld = P.load(n, rd, devices);
    const lvl = ld.level === "over" ? " pd-over" : ld.level === "high" ? " pd-high" : "";
    return { text: (rd.state === "old" ? "▲ OLD " : "") + `${f.text} ${f.unit}`, bar: ld.level === "none" ? null : ld.bar,
      word: ld.level === "none" ? "" : `${ld.word} ${ld.text}`, cls: lvl };
  };
  d.upsWord = (n, upsTypes) => { const t = (upsTypes || []).find((x) => x.key === n.ups_type); return t ? t.label : (n.ups_type && n.ups_type !== "unknown" ? String(n.ups_type).replace(/_/g, " ") : ""); };
  const nodeTitle = (n, upsTypes) => [n.display || n.role_label || "Unit", n.name, n.role === "ups" ? d.upsWord(n, upsTypes) : "", n.feed ? `fed from ${n.feed}` : ""].filter(Boolean).join(", ");

  // Builds the svg for a layout. `live` is a function(node) -> liveInfo, or null. `sel` is the picked ref or "".
  d.draw = function (lay, opts) {
    const o = opts || {}, live = o.live || null, sel = o.sel || "";
    const svg = el("svg", { class: "pd-svg", viewBox: `0 0 ${Math.max(lay.width, 1)} ${Math.max(lay.height, 1)}`, role: "group", "aria-label": "Power block diagram", focusable: "false" });
    svg.append(el("style", {}, CSS), el("rect", { class: "pd-bg", x: 0, y: 0, width: lay.width, height: lay.height, fill: "transparent" }));
    // the supply path of the picked box
    const parentOf = {};
    lay.links.forEach((l) => { parentOf[l.to] = l.from; });
    const path = new Set(), onLink = new Set();
    for (let r = sel, guard = 0; r && guard < 100; r = parentOf[r], guard++) { path.add(r); if (parentOf[r]) onLink.add(parentOf[r] + ">" + r); }
    lay.groups.forEach((g) => svg.append(el("text", { class: "pd-head", x: d.PAD, y: g.y }, d.clip(g.title, 80))));
    lay.links.forEach((l) => {
      const on = onLink.has(l.from + ">" + l.to);
      svg.append(el("path", { class: "pd-link" + (on ? " pd-on" : ""), d: l.d }));
      if (l.label) svg.append(el("text", { class: "pd-lab", x: l.lx, y: l.ly }, d.clip(l.label, 16)));
    });
    lay.boxes.forEach((b) => {
      const n = b.node, lv = live ? live(n) : null;
      const cls = "pd-node" + (b.ref === sel ? " pd-sel" : path.has(b.ref) ? " pd-anc" : "") + (b.loop ? " pd-loop" : "") + (lv ? lv.cls : "");
      const titleTxt = nodeTitle(n, o.upsTypes) + (b.loop ? ", check: this is part of a loop" : "") + (lv ? `, ${lv.text}${lv.word ? ", " + lv.word : ""}` : "");
      const g = el("g", { class: cls, tabindex: "0", role: "button", "aria-pressed": b.ref === sel ? "true" : "false", "aria-label": titleTxt, "data-ref": n.ref });
      g.append(el("title", {}, titleTxt), el("rect", { class: "pd-box", x: b.x, y: b.y, width: b.w, height: b.h, rx: 8 }));
      const l1 = (n.display || n.role_label || "Unit") + (n.role === "ups" && d.upsWord(n, o.upsTypes) ? ` · ${d.upsWord(n, o.upsTypes)}` : "");
      g.append(el("text", { class: "pd-t1", x: b.x + 10, y: b.y + 20 }, d.clip(l1, 24)), el("text", { class: "pd-t2", x: b.x + 10, y: b.y + 38 }, d.clip(n.name || "", 27)));
      if (b.loop) g.append(el("text", { class: "pd-t4", x: b.x + 10, y: b.y + b.h - 4 }, "▲ check: loop"));
      if (lv) {
        g.append(el("text", { class: "pd-t3", x: b.x + 10, y: b.y + 56 }, d.clip(lv.text, 27)));
        if (lv.bar !== null && lv.bar !== undefined) {
          g.append(el("text", { class: "pd-t4", x: b.x + 10, y: b.y + 72 }, d.clip(lv.word, 30)),
            el("rect", { class: "pd-track", x: b.x + 10, y: b.y + 77, width: b.w - 20, height: 5, rx: 2 }),
            el("rect", { class: "pd-fill", x: b.x + 10, y: b.y + 77, width: Math.max(0, Math.min(100, lv.bar)) / 100 * (b.w - 20), height: 5, rx: 2 }),
            el("line", { class: "pd-tick", x1: b.x + 10 + 0.8 * (b.w - 20), x2: b.x + 10 + 0.8 * (b.w - 20), y1: b.y + 75, y2: b.y + 84 }));
        }
      }
      svg.append(g);
    });
    return svg;
  };

  // A copy of the svg that stands alone: the theme colours are filled in and a background is added.
  d.exportSvg = function (svg) {
    const cs = getComputedStyle(document.documentElement), copy = svg.cloneNode(true);
    const style = copy.querySelector("style");
    style.textContent = style.textContent.replace(/var\((--[a-z0-9-]+)\)/g, (m, k) => cs.getPropertyValue(k).trim() || "#888");
    const bg = copy.querySelector(".pd-bg");
    bg.setAttribute("fill", cs.getPropertyValue("--bg").trim() || "#fff");
    const vb = svg.viewBox.baseVal;
    copy.setAttribute("xmlns", SVGNS);
    copy.setAttribute("width", String(vb.width)); copy.setAttribute("height", String(vb.height));
    copy.removeAttribute("style");
    return new XMLSerializer().serializeToString(copy);
  };

  // ---- the view -----------------------------------------------------------------------------------------
  // mount(host, {liveKey}) -> { update(nodes, ctx) }. ctx = { upsTypes, entities, devices, now, emptyText }.
  // The diagram is redrawn only when the structure changes, and (with live figures on) at most every 5 s.
  // `liveKey` (a localStorage key) turns the "Show live power" switch on; without it the diagram is names only.
  d.mount = function (host, mopts) {
    const m = Object.assign({ liveKey: "", fit: null }, mopts);
    const h = SW.h;
    let sel = "", zoom = "auto", lastSig = "", lastAt = 0, lastSvg = null, lastLay = null, lastArgs = null, liveOn = false;
    const store = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private window: the choice just is not remembered */ } } };
    if (m.liveKey) liveOn = store.get(m.liveKey) === "1";
    const msg = h("p", { class: "muted pd-msg", role: "status" });
    const scroll = h("div", { class: "pd-scroll" });
    const btn = (label, fn, aria) => h("button", { type: "button", class: "small touch", onclick: fn, "aria-label": aria || label }, label);
    const liveBox = m.liveKey ? h("input", { type: "checkbox", checked: liveOn, "aria-label": "Show live power on the diagram" }) : null;
    if (liveBox) liveBox.onchange = () => { liveOn = liveBox.checked; store.set(m.liveKey, liveOn ? "1" : "0"); lastSig = ""; if (lastArgs) update(...lastArgs); };
    const bar = h("div", { class: "pd-bar row" }, btn("Fit", () => setZoom("fit"), "Fit the diagram to the screen"), btn("−", () => setZoom((cur() || 1) / 1.25), "Zoom out"), btn("+", () => setZoom((cur() || 1) * 1.25), "Zoom in"),
      h("span", { class: "grow" }), liveBox ? h("label", { class: "row pd-live" }, liveBox, " Show live power") : null,
      btn("Save as image", () => save(), "Save the diagram as an image (SVG)"), btn("Print", () => print(), "Print the diagram on one page"));
    const wrap = h("div", { class: "pd-wrap" }, bar, msg, scroll);
    host.replaceChildren(wrap);
    const cur = () => (typeof zoom === "number" ? zoom : lastLay && scroll.clientWidth ? Math.min(scroll.clientWidth / lastLay.width, 2) : 1);
    function size() {
      const svg = lastSvg;
      if (!svg || !lastLay) return;
      if (zoom === "fit") {
        svg.style.width = "100%"; svg.style.maxWidth = Math.round(lastLay.width * 2) + "px"; svg.style.height = "auto";
        svg.style.maxHeight = "calc(100vh - 150px)";
      } else {
        svg.style.maxWidth = "none"; svg.style.maxHeight = "none";
        svg.style.width = Math.round(lastLay.width * zoom) + "px"; svg.style.height = Math.round(lastLay.height * zoom) + "px";
      }
    }
    function setZoom(z) { zoom = z === "fit" ? "fit" : Math.max(0.3, Math.min(3, z)); size(); }
    function save() {
      if (!lastSvg) return;
      try {
        const url = URL.createObjectURL(new Blob([d.exportSvg(lastSvg)], { type: "image/svg+xml" }));
        const a = h("a", { href: url, download: "power-distribution.svg" });
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
      } catch (_) { msg.textContent = "Could not save the image here. Use Print and choose Save as PDF."; }
    }
    function print() {
      if (!lastSvg) return;
      const b = document.body;
      wrap.classList.add("pd-print-area"); b.classList.add("pd-printing");
      const done = () => { b.classList.remove("pd-printing"); wrap.classList.remove("pd-print-area"); window.removeEventListener("afterprint", done); };
      window.addEventListener("afterprint", done);
      window.print();
    }
    // pinch to zoom (two fingers) on touch screens; one finger scrolls sideways as usual
    const ptrs = new Map();
    let pinch0 = null;
    scroll.addEventListener("pointerdown", (e) => { ptrs.set(e.pointerId, [e.clientX, e.clientY]); if (ptrs.size === 2) { const p = [...ptrs.values()]; pinch0 = { dist: Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]), z: cur() }; } });
    scroll.addEventListener("pointermove", (e) => {
      if (!ptrs.has(e.pointerId)) return;
      ptrs.set(e.pointerId, [e.clientX, e.clientY]);
      if (ptrs.size === 2 && pinch0 && pinch0.dist > 0) {
        const p = [...ptrs.values()], dist = Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]), before = cur();
        setZoom(pinch0.z * dist / pinch0.dist);
        const k = cur() / before;
        scroll.scrollLeft = (scroll.scrollLeft + scroll.clientWidth / 2) * k - scroll.clientWidth / 2;
        scroll.scrollTop = (scroll.scrollTop + scroll.clientHeight / 2) * k - scroll.clientHeight / 2;
      }
    });
    const up = (e) => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch0 = null; };
    scroll.addEventListener("pointerup", up); scroll.addEventListener("pointercancel", up);
    const pick = (e) => {
      const g = e.target.closest && e.target.closest("g.pd-node");
      if (!g) return;
      if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
      if (e.type === "keydown") e.preventDefault();
      const ref = g.getAttribute("data-ref");
      sel = sel === ref ? "" : ref;
      lastSig = ""; if (lastArgs) update(...lastArgs, true);
      const again = scroll.querySelector(`g.pd-node[data-ref="${CSS_ESC(ref)}"]`);
      if (again && e.type === "keydown") again.focus();
    };
    const CSS_ESC = (s) => String(s).replace(/["\\]/g, "\\$&");
    scroll.addEventListener("click", pick); scroll.addEventListener("keydown", pick);

    function update(nodes, ctx, force) {
      lastArgs = [nodes, ctx];
      const c = ctx || {}, list = nodes || [];
      const structSig = JSON.stringify([liveOn, sel, list.map((n) => [n.ref, n.name, n.display, n.role, n.upstream, n.feed, n.ups_type, n.rating_a, n.breaker_a])]);
      const changedShape = structSig !== lastSig;
      if (!changedShape && !(liveOn && Date.now() - lastAt >= 5000)) return;
      lastAt = Date.now();
      if (!list.length) { lastSig = structSig; lastSvg = lastLay = null; scroll.replaceChildren(); msg.textContent = c.emptyText || "Nothing to draw yet."; wrap.classList.add("pd-none"); return; }
      wrap.classList.remove("pd-none");
      lastSig = structSig;
      const lay = d.layout(list, { live: liveOn });
      let byDev = null;
      if (liveOn) {
        byDev = {};
        Object.keys(c.entities || {}).forEach((k) => { const en = c.entities[k]; if (en && en.device_id) (byDev[en.device_id] || (byDev[en.device_id] = [])).push(en); });
      }
      const svg = d.draw(lay, { sel, upsTypes: c.upsTypes, live: liveOn ? (n) => d.liveInfo(n, c.entities, c.devices, c.now, byDev) : null });
      const sx = scroll.scrollLeft, sy = scroll.scrollTop;
      lastSvg = svg; lastLay = lay;
      scroll.replaceChildren(svg);
      if (zoom === "auto") zoom = m.fit !== false && scroll.clientWidth >= (m.fit ? 700 : 900) ? "fit" : 1;   // wall: fit from tablet width up; phones scroll and pinch
      size();
      scroll.scrollLeft = sx; scroll.scrollTop = sy;
      msg.textContent = lay.hidden > 0 ? `Showing the first ${lay.drawn} of ${lay.total} units. The rest are not drawn here.` : "";
    }
    return { update, redraw() { lastSig = ""; if (lastArgs) update(...lastArgs); }, get live() { return liveOn; } };
  };

  return d;
})();
