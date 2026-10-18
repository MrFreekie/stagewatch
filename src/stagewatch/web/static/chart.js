// Minimal time-series chart on <canvas>: multiple series, markers as
// labelled vertical lines, crisp on high-DPI tablets, no dependencies.
"use strict";

class TimeChart {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.series = [];     // [{label, color, points: [[ts, v], ...], width}]
    this.markers = [];    // [{ts, label, selected}]
    this.range = [Date.now() / 1000 - 3600, Date.now() / 1000];
    this.format = opts.format || ((v) => v.toFixed(1));
    this.fixedRange = null;     // [lo, hi] used exactly when set; readings outside are drawn at the edge with a mark
    this.defaultRange = null;   // [lo, hi] the axis starts from; it grows if the data goes outside it
    this.onMarkerClick = opts.onMarkerClick || null;
    this._markerHits = [];
    this._hover = null;
    // ResizeObserver needs Safari 13.1; older iPads fall back to window resize/rotation.
    if (typeof ResizeObserver === "function") new ResizeObserver(() => this.draw()).observe(canvas);
    else window.addEventListener("resize", () => this.draw());    canvas.addEventListener("click", (ev) => this._click(ev));
    canvas.addEventListener("mousemove", (ev) => { this._hover = this._pos(ev); this.draw(); });
    canvas.addEventListener("mouseleave", () => { this._hover = null; this.draw(); });
  }

  _pos(ev) {
    const r = this.canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  _click(ev) {
    if (!this.onMarkerClick) return;
    const p = this._pos(ev);
    // Flags never overlap; the nearest centre wins just in case.
    let hit = null, best = Infinity;
    for (const m of this._markerHits) {
      if (p.y < m.y || p.y > m.y + m.h || p.x < m.x || p.x > m.x + m.w) continue;
      const d = Math.abs(p.x - (m.x + m.w / 2));
      if (d < best) { best = d; hit = m; }
    }
    if (hit) this.onMarkerClick(hit.marker);
  }

  // Marker tab sizes. Larger on the wall layout (bigger text) and on touch screens (24 px taps).
  _markerMetrics() {
    const wall = !!(document.body && document.body.classList && document.body.classList.contains("layout-wall"));
    const touch = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
    if (wall) return { font: 20, h: 30, pitch: 34, touch: true };
    if (touch) return { font: 13, h: 24, pitch: 30, touch: true };
    return { font: 12, h: 18, pitch: 20, touch: false };
  }

  // Works out where the marker flags go for this width and time range; returns 1 when a flag
  // row is needed (0 when no marker is in view) so the plot can make room for it.
  _layoutVisible(ctx, w, t0, t1, padL, pw) {
    const m = this._markerMetrics();
    const vis = [];
    for (const mk of this.markers) {
      if (mk.ts < t0 || mk.ts > t1) continue;
      vis.push({ x: Math.round(padL + ((mk.ts - t0) / (t1 - t0)) * pw) + 0.5, label: mk.label, selected: !!mk.selected, marker: mk });
    }
    this._ml = { m, items: vis, groups: [], padL };
    if (!vis.length) return 0;
    ctx.font = `${m.font}px system-ui, sans-serif`;
    const lead = m.font + 14;   // glyph, its spacing and the end padding
    const widths = vis.map((it) => ctx.measureText(it.label).width + lead);
    this._ml.groups = SW.chart.groupFlags(vis.map((it) => it.x - padL), widths, pw,
      { groupWidth: (n) => ctx.measureText(`${n} markers`).width + lead });
    ctx.font = "12px system-ui, sans-serif";
    return 1;
  }

  _css(name, fallback) {
    return getComputedStyle(this.canvas).getPropertyValue(name).trim() || fallback;
  }

  // The y range to draw for data spanning [lo, hi]. With a default range [d0, d1] the axis stays
  // exactly on it while the data fits (so the lines don't jump about with every reading), and
  // grows only on the side the data goes past, with a little room. Without one: fit the data.
  static yRange(lo, hi, def) {
    const room = (a, b) => Math.max(b - a, 1e-9) * 0.08;
    if (Array.isArray(def) && def.length === 2 && def[0] < def[1]) {
      const dLo = lo < def[0] ? lo - room(lo, Math.max(hi, def[1])) : def[0];
      const dHi = hi > def[1] ? hi + room(Math.min(lo, def[0]), hi) : def[1];
      return [dLo, dHi];
    }
    if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
    const m = (hi - lo) * 0.08;
    return [lo - m, hi + m];
  }

  static niceStep(span, target) {
    const raw = span / Math.max(target, 1);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / mag;
    return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
  }

  static timeStep(span) {
    const steps = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200];
    return steps.find((s) => span / s <= 7) || 86400;
  }

  // Axis tick instants (UTC epoch s) in [t0, t1] that fall on site-local multiples of tstep
  // (whole hours/minutes on the site clock: matters for +05:30-style zones and 2 h+ steps).
  // offsetAt(ts) is the site's UTC offset in seconds. The offset is re-read at every tick, so
  // the ticks stay on the site grid across a DST change inside the window:
  // - local time L is tried with the current offset, then with the offset found there;
  // - if L doesn't exist (spring-forward gap), the next boundary after the gap is used;
  // - a repeated hour (fall-back) is labelled once.
  static siteTicks(t0, t1, tstep, offsetAt) {
    const toInstant = (L, o) => {
      const n1 = L - o, o1 = offsetAt(n1);
      if (o1 === o) return n1;
      const n2 = L - o1;
      if (offsetAt(n2) === o1) return n2;
      return Math.ceil((n1 + o1) / tstep) * tstep - o1;   // L is in the gap
    };
    const ticks = [];
    let o = offsetAt(t0);
    let t = toInstant(Math.ceil((t0 + o) / tstep) * tstep, o);
    let guard = 0;   // never hang a tablet on a bad offset
    while (t <= t1 && ticks.length < 100 && guard++ < 1000) {
      if (t >= t0 && (!ticks.length || t > ticks[ticks.length - 1])) ticks.push(t);
      o = offsetAt(t);
      t = toInstant((Math.floor((t + o) / tstep) + 1) * tstep, o);
    }
    return ticks;
  }

  draw() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
    }
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const fg = this._css("--muted", "#8a93a6");
    const grid = this._css("--grid", "#2a3040");
    const text = this._css("--text", "#e8ecf4");
    ctx.font = "12px system-ui, sans-serif";

    const [t0, t1] = this.range;
    const pad = { l: 56, r: 12, t: 26, b: 24 };
    // Marker rows sit above the plot; the plot only gives up room for the rows in use.
    const rowsUsed = this._layoutVisible(ctx, w, t0, t1, pad.l, w - pad.l - pad.r);
    if (rowsUsed) pad.t = 6 + this._ml.m.pitch;
    const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;

    let lo = Infinity, hi = -Infinity;
    for (const s of this.series) for (const [t, v] of s.points) {
      if (t < t0 || t > t1 || v === null) continue;
      lo = Math.min(lo, v); hi = Math.max(hi, v);
    }
    if (!Number.isFinite(lo)) {
      ctx.fillStyle = fg;
      ctx.fillText("No data in this time range yet", pad.l + 10, pad.t + 20);
      this._drawMarkers(ctx, pad, ph, text);
      return;
    }
    const fixed = Array.isArray(this.fixedRange) && this.fixedRange.length === 2 && this.fixedRange[0] < this.fixedRange[1] ? this.fixedRange : null;
    if (fixed) [lo, hi] = fixed; else [lo, hi] = TimeChart.yRange(lo, hi, this.defaultRange);
    const x = (t) => pad.l + ((t - t0) / (t1 - t0)) * pw;
    const y = (v) => pad.t + (1 - ((fixed ? Math.min(hi, Math.max(lo, v)) : v) - lo) / (hi - lo)) * ph;

    // grid + y labels
    ctx.strokeStyle = grid; ctx.lineWidth = 1; ctx.fillStyle = fg;
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const ystep = TimeChart.niceStep(hi - lo, Math.max(3, Math.floor(ph / 45)));
    for (let v = Math.ceil(lo / ystep) * ystep; v <= hi; v += ystep) {
      const yy = Math.round(y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(pad.l + pw, yy); ctx.stroke();
      ctx.fillText(this.format(v), pad.l - 6, yy);
    }
    // x labels
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const tstep = TimeChart.timeStep(t1 - t0);
    for (const t of TimeChart.siteTicks(t0, t1, tstep, SW.siteOffset)) {
      const xx = Math.round(x(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, pad.t + ph); ctx.stroke();
      ctx.fillText(SW.fmtTime(t), xx, pad.t + ph + 6);
    }

    // series (break the line across gaps > 5 minutes: sensor offline). A series may set its own
    // gap length in seconds (s.gap) and a dash pattern (s.dash) so lines differ by more than colour.
    ctx.save();
    ctx.beginPath(); ctx.rect(pad.l, pad.t, pw, ph); ctx.clip();
    for (const s of this.series) {
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 2; ctx.lineJoin = "round";
      ctx.setLineDash(s.dash || []);
      const gap = s.gap || 300;
      ctx.beginPath();
      let prev = null;
      for (const [t, v] of s.points) {
        if (v === null) { prev = null; continue; }
        if (prev === null || t - prev > gap) ctx.moveTo(x(t), y(v)); else ctx.lineTo(x(t), y(v));
        prev = t;
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.restore();
    // A fixed range never hides a reading outside it: the line runs to the edge and a small triangle
    // (up for above, down for below) sits there, at most one every 10 px.
    if (fixed) {
      ctx.fillStyle = text; ctx.strokeStyle = this._css("--panel", "#161b26"); ctx.lineWidth = 1;
      const last = { up: -99, down: -99 };
      for (const s of this.series) for (const [t, v] of s.points) {
        if (v === null || t < t0 || t > t1 || (v <= hi && v >= lo)) continue;
        const up = v > hi, k = up ? "up" : "down", xx = x(t);
        if (xx - last[k] < 10) continue;
        last[k] = xx;
        const yy = up ? pad.t + 2 : pad.t + ph - 2, d = up ? 1 : -1;
        ctx.beginPath(); ctx.moveTo(xx, yy); ctx.lineTo(xx - 5, yy + 8 * d); ctx.lineTo(xx + 5, yy + 8 * d); ctx.closePath();
        ctx.fill(); ctx.stroke();
      }
    }

    this._drawMarkers(ctx, pad, ph, text);

    // hover crosshair with values
    if (this._hover && this._hover.x > pad.l && this._hover.x < pad.l + pw) {
      const t = t0 + ((this._hover.x - pad.l) / pw) * (t1 - t0);
      ctx.strokeStyle = fg; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(this._hover.x, pad.t); ctx.lineTo(this._hover.x, pad.t + ph); ctx.stroke();
      ctx.setLineDash([]);
      const lines = [SW.fmtTime(t, { seconds: true })];
      for (const s of this.series) {
        let best = null;
        for (const p of s.points) if (best === null || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
        if (best && best[1] !== null && Math.abs(best[0] - t) < (t1 - t0) / 50) lines.push(`${s.label}: ${this.format(best[1])}`);
      }
      const bw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 12;
      const bx = Math.min(this._hover.x + 10, w - bw - 4);
      ctx.fillStyle = this._css("--panel", "#161b26"); ctx.strokeStyle = grid;
      ctx.fillRect(bx, pad.t + 4, bw, lines.length * 16 + 8); ctx.strokeRect(bx, pad.t + 4, bw, lines.length * 16 + 8);
      ctx.fillStyle = text; ctx.textAlign = "left"; ctx.textBaseline = "top";
      lines.forEach((l, i) => ctx.fillText(l, bx + 6, pad.t + 9 + i * 16));
    }
  }

  // Draws the lines and flags worked out by _layoutVisible. Colour and glyph come from SW.markerStyle.
  _drawMarkers(ctx, pad, ph, text) {
    this._markerHits = [];
    const ml = this._ml;
    if (!ml || !ml.items.length) { ctx.fillStyle = text; return; }
    const m = ml.m, y = 2;
    const looks = {};
    const look = (src) => {
      const st = SW.markerStyle(src);
      if (!looks[st.key]) {
        looks[st.key] = { st, color: this._css(`--marker-${st.key}`, st.fallback), ink: this._css(`--marker-${st.key}-ink`, st.ink) };
      }
      return looks[st.key];
    };
    // One dashed line per marker (the selected one solid, drawn last), then the flags on top.
    const order = ml.items.map((_, i) => i).sort((a, b) => ml.items[a].selected - ml.items[b].selected);
    for (const i of order) {
      const it = ml.items[i], lk = look(it.marker.source);
      ctx.strokeStyle = lk.color; ctx.lineWidth = it.selected ? 2.5 : 1.2;
      ctx.setLineDash(it.selected ? [] : [5, 4]);
      ctx.beginPath(); ctx.moveTo(it.x, y + m.h); ctx.lineTo(it.x, pad.t + ph); ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.font = `${m.font}px system-ui, sans-serif`;
    for (const g of ml.groups) {
      const members = g.indices.map((i) => ml.items[i]);
      // a group takes the alarm look if it holds an alarm, so an alarm is never hidden by a crew mark
      const alarm = members.find((it) => SW.markerStyle(it.marker.source).key === "alarm");
      const lk = look((alarm || members[0]).marker.source);
      const lx = ml.padL + g.lx;
      const label = SW.chart.fitText(g.indices.length > 1 ? `${g.indices.length} markers` : members[0].label,
        g.w - m.font - 14, (t) => ctx.measureText(t).width);
      ctx.fillStyle = lk.color; ctx.fillRect(lx, y, g.w, m.h);
      ctx.fillStyle = lk.ink;
      ctx.fillText(lk.st.glyph, lx + 4, y + m.h / 2 + 1);
      ctx.fillText(label, lx + m.font + 8, y + m.h / 2 + 1);
      if (members.some((it) => it.selected)) { ctx.strokeStyle = text; ctx.lineWidth = 1.5; ctx.strokeRect(lx + 0.5, y + 0.5, g.w - 1, m.h - 1); }
      // a grouped flag opens its latest marker
      const pick = members.reduce((a, b) => (b.marker.ts > a.marker.ts ? b : a));
      this._markerHits.push({ x: lx, w: g.w, y: y - 2, h: m.pitch, marker: pick.marker });
    }
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillStyle = text;
  }
}

// Pure helpers for the chart's marker flags, kept apart so they can be tested without a canvas.
SW.chart = SW.chart || {};

// One row of flags. xs are the marker lines in px from the plot's left edge, widths the full flag
// width for each, plotWidth the plot's width. A flag starts at its line, or ends at it when it
// would pass the right edge; it never leaves the plot (widths are capped to plotWidth). Flags that
// would overlap (with `gap` px) are merged into one group, drawn as one flag of
// opts.groupWidth(count) px at the first member's place. Returns [{indices, lx, w}] left to right.
SW.chart.groupFlags = function (xs, widths, plotWidth, opts) {
  const gap = opts && opts.gap !== undefined ? opts.gap : 4;
  const gw = (opts && opts.groupWidth) || (() => 80);
  const place = (x, w) => {
    w = Math.min(w, plotWidth);
    const lx = x + w > plotWidth ? x - w : x;
    return { lx: Math.max(0, Math.min(lx, plotWidth - w)), w };
  };
  const order = xs.map((_, i) => i).sort((a, b) => xs[a] - xs[b] || a - b);
  const out = [];
  let cur = null;
  for (const i of order) {
    const p = place(xs[i], widths[i]);
    if (cur && p.lx < cur.end + gap) {
      cur.indices.push(i);
      const q = place(xs[cur.indices[0]], gw(cur.indices.length));
      cur.lx = q.lx; cur.w = q.w;
      cur.end = Math.max(cur.end, p.lx + p.w, q.lx + q.w);
    } else {
      cur = { indices: [i], lx: p.lx, w: p.w, end: p.lx + p.w };
      out.push(cur);
    }
  }
  // a grown group flag may now reach the next one: merge those too
  for (let k = 1; k < out.length; k++) {
    const a = out[k - 1], b = out[k];
    if (b.lx < a.lx + a.w + gap) {
      a.indices = a.indices.concat(b.indices);
      const q = place(xs[a.indices[0]], gw(a.indices.length));
      a.lx = q.lx; a.w = q.w; a.end = Math.max(a.end, b.end);
      out.splice(k, 1); k = Math.max(0, k - 2);
    }
  }
  return out.map((g) => ({ indices: g.indices, lx: g.lx, w: g.w }));
};

// Cuts text to fit maxW px (measure(text) gives its width), ending with an ellipsis when cut.
SW.chart.fitText = function (text, maxW, measure) {
  if (measure(text) <= maxW) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (measure(text.slice(0, mid) + "…") <= maxW) lo = mid; else hi = mid - 1;
  }
  return lo ? text.slice(0, lo).trimEnd() + "…" : "…";
};
