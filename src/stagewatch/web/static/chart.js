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
    const hit = this._markerHits.find((m) => p.y < 22 && p.x >= m.x && p.x <= m.x + m.w);
    if (hit) this.onMarkerClick(hit.marker);
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
    const markerColor = this._css("--marker", "#f5b83d");
    ctx.font = "12px system-ui, sans-serif";

    const [t0, t1] = this.range;
    const pad = { l: 56, r: 12, t: 26, b: 24 };
    const pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;

    let lo = Infinity, hi = -Infinity;
    for (const s of this.series) for (const [t, v] of s.points) {
      if (t < t0 || t > t1 || v === null) continue;
      lo = Math.min(lo, v); hi = Math.max(hi, v);
    }
    if (!Number.isFinite(lo)) {
      ctx.fillStyle = fg;
      ctx.fillText("No data in this time range yet", pad.l + 10, pad.t + 20);
      this._drawMarkers(ctx, t0, t1, pad, pw, ph, markerColor, text);
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

    this._drawMarkers(ctx, t0, t1, pad, pw, ph, markerColor, text);

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

  _drawMarkers(ctx, t0, t1, pad, pw, ph, color, text) {
    this._markerHits = [];
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    for (const m of this.markers) {
      if (m.ts < t0 || m.ts > t1) continue;
      const xx = Math.round(pad.l + ((m.ts - t0) / (t1 - t0)) * pw) + 0.5;
      ctx.strokeStyle = color; ctx.lineWidth = m.selected ? 2.5 : 1.2;
      ctx.setLineDash(m.selected ? [] : [5, 4]);
      ctx.beginPath(); ctx.moveTo(xx, pad.t - 4); ctx.lineTo(xx, pad.t + ph); ctx.stroke();
      ctx.setLineDash([]);
      const label = m.label.length > 22 ? m.label.slice(0, 21) + "…" : m.label;
      const tw = ctx.measureText(label).width + 8;
      const lx = xx + tw > pad.l + pw + pad.r ? xx - tw : xx;  // flip near the right edge
      ctx.fillStyle = color; ctx.fillRect(lx, 2, tw, 18);
      ctx.fillStyle = "#111"; ctx.fillText(label, lx + 4, 5);
      this._markerHits.push({ x: lx, w: tw, marker: m });
    }
    ctx.fillStyle = text;
  }
}
