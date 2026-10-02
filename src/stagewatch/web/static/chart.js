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
    // Per row (x and y). Compact tabs can overlap, so the one whose centre is nearest wins.
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

  // Lane assignment for marker tabs (pure, so it can be tested without a canvas).
  // items: [{x, label, selected}] with x the line position in px, measure(label) the full tab
  // width, width the right-hand limit, rows the number of label rows (3).
  // Returns one {row, lx, w, compact, flipped} per item. Each label takes the first row where it
  // clears the earlier ones by `gap` px. The selected marker is placed first, in row 0, and the
  // rest are laid out around it. If no row has room the marker gets a compact tab (width
  // compactW) in the extra row numbered `rows`. A label that would pass the right edge flips to
  // the left of its line. Cost is O(n * rows) after sorting by x.
  static layoutMarkers(items, measure, width, rows, opts) {
    const gap = opts && opts.gap !== undefined ? opts.gap : 4;
    const cw = opts && opts.compactW ? opts.compactW : 14;
    const out = new Array(items.length);
    const lastEnd = [];
    for (let r = 0; r < rows; r++) lastEnd.push(-Infinity);
    const place = (it) => {
      const w = measure(it.label);
      const flipped = it.x + w > width;
      return { w, flipped, lx: Math.max(0, flipped ? it.x - w : it.x) };
    };
    let selIdx = -1, sel = null;
    for (let i = 0; i < items.length; i++) if (items[i].selected) { selIdx = i; break; }
    if (selIdx >= 0 && rows > 0) {
      const p = place(items[selIdx]);
      sel = p;
      out[selIdx] = { row: 0, lx: p.lx, w: p.w, compact: false, flipped: p.flipped };
    }
    const order = [];
    for (let i = 0; i < items.length; i++) if (i !== selIdx || rows <= 0) order.push(i);
    order.sort((a, b) => items[a].x - items[b].x || a - b);
    for (const i of order) {
      const it = items[i], p = place(it);
      let row = -1;
      for (let k = 0; k < rows; k++) {
        if (p.lx < lastEnd[k] + gap) continue;
        if (k === 0 && sel && p.lx < sel.lx + sel.w + gap && p.lx + p.w + gap > sel.lx) continue;
        row = k; break;
      }
      if (row >= 0) {
        lastEnd[row] = Math.max(lastEnd[row], p.lx + p.w);
        out[i] = { row, lx: p.lx, w: p.w, compact: false, flipped: p.flipped };
      } else {
        out[i] = { row: rows, lx: Math.min(Math.max(0, it.x - cw / 2), Math.max(0, width - cw)), w: cw, compact: true, flipped: false };
      }
    }
    return out;
  }

  // Works out where the marker tabs go for this width and time range; returns the number of
  // rows in use (0 when no marker is in view) so the plot can make room for exactly those.
  _layoutVisible(ctx, w, t0, t1, padL, pw) {
    const m = this._markerMetrics();
    const vis = [];
    for (const mk of this.markers) {
      if (mk.ts < t0 || mk.ts > t1) continue;
      const label = mk.label.length > 22 ? mk.label.slice(0, 21) + "…" : mk.label;
      vis.push({ x: Math.round(padL + ((mk.ts - t0) / (t1 - t0)) * pw) + 0.5, label, selected: !!mk.selected, marker: mk });
    }
    this._ml = { m, items: vis, lanes: [], rowsUsed: 0 };
    if (!vis.length) return 0;
    ctx.font = `${m.font}px system-ui, sans-serif`;
    const lead = m.font + 6;   // glyph and its spacing
    const lanes = TimeChart.layoutMarkers(vis, (l) => ctx.measureText(l).width + lead + 6, w, 3, { compactW: m.touch ? 22 : 14 });
    let used = 0;
    for (const l of lanes) used = Math.max(used, l.row + 1);
    this._ml.lanes = lanes; this._ml.rowsUsed = used;
    ctx.font = "12px system-ui, sans-serif";
    return used;
  }

  _css(name, fallback) {
    return getComputedStyle(this.canvas).getPropertyValue(name).trim() || fallback;
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
    if (rowsUsed) pad.t = 6 + rowsUsed * this._ml.m.pitch;
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
    if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
    const margin = (hi - lo) * 0.08;
    lo -= margin; hi += margin;
    const x = (t) => pad.l + ((t - t0) / (t1 - t0)) * pw;
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * ph;

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

    // series (break the line across gaps > 5 minutes: sensor offline)
    ctx.save();
    ctx.beginPath(); ctx.rect(pad.l, pad.t, pw, ph); ctx.clip();
    for (const s of this.series) {
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 2; ctx.lineJoin = "round";
      ctx.beginPath();
      let prev = null;
      for (const [t, v] of s.points) {
        if (v === null) { prev = null; continue; }
        if (prev === null || t - prev > 300) ctx.moveTo(x(t), y(v)); else ctx.lineTo(x(t), y(v));
        prev = t;
      }
      ctx.stroke();
    }
    ctx.restore();

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

  // Draws the lines and tabs worked out by _layoutVisible. Colour and glyph come from SW.markerStyle.
  _drawMarkers(ctx, pad, ph, text) {
    this._markerHits = [];
    const ml = this._ml;
    if (!ml || !ml.items.length) { ctx.fillStyle = text; return; }
    const m = ml.m, panel = this._css("--panel", "#161b26");
    const looks = {};
    const look = (src) => {
      const st = SW.markerStyle(src);
      if (!looks[st.key]) {
        looks[st.key] = { st, color: this._css(`--marker-${st.key}`, st.fallback), ink: this._css(`--marker-${st.key}-ink`, st.ink) };
      }
      return looks[st.key];
    };
    const tabY = (row) => 2 + row * m.pitch;
    // Selected marker last, so its solid line and label sit on top.
    const order = [];
    let selAt = -1;
    for (let i = 0; i < ml.items.length; i++) { if (ml.items[i].selected && selAt < 0) selAt = i; else order.push(i); }
    if (selAt >= 0) order.push(selAt);
    // lines first, then tabs, so a line never crosses a label
    for (const i of order) {
      const it = ml.items[i], ln = ml.lanes[i], lk = look(it.marker.source);
      ctx.strokeStyle = lk.color; ctx.lineWidth = it.selected ? 2.5 : 1.2;
      ctx.setLineDash(it.selected ? [] : [5, 4]);
      ctx.beginPath(); ctx.moveTo(it.x, tabY(ln.row) + m.h); ctx.lineTo(it.x, pad.t + ph); ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    for (const i of order) {
      const it = ml.items[i], ln = ml.lanes[i], lk = look(it.marker.source), y = tabY(ln.row);
      if (ln.compact) {
        ctx.fillStyle = panel; ctx.fillRect(ln.lx, y, ln.w, m.h);
        ctx.fillStyle = lk.color; ctx.font = `${m.font + 3}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(lk.st.glyph, ln.lx + ln.w / 2, y + m.h / 2 + 1);
        ctx.textAlign = "left";
      } else {
        ctx.fillStyle = lk.color; ctx.fillRect(ln.lx, y, ln.w, m.h);
        ctx.fillStyle = lk.ink; ctx.font = `${m.font}px system-ui, sans-serif`;
        ctx.fillText(lk.st.glyph, ln.lx + 4, y + m.h / 2 + 1);
        ctx.fillText(it.label, ln.lx + m.font + 8, y + m.h / 2 + 1);
        if (it.selected) { ctx.strokeStyle = text; ctx.lineWidth = 1.5; ctx.strokeRect(ln.lx + 0.5, y + 0.5, ln.w - 1, m.h - 1); }
      }
      // tap target: the whole row height, and at least 24 px wide on touch screens
      const hw = ln.compact && m.touch ? Math.max(ln.w, 32) : ln.w;
      this._markerHits.push({ x: ln.lx - (hw - ln.w) / 2, w: hw, y: y - 2, h: m.pitch, marker: it.marker });
    }
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillStyle = text;
  }
}
