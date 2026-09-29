// Shared helpers for Stagewatch pages. No framework, no build step, no CDN:
// everything is served by the Stagewatch server so it works on an offline
// show network.
"use strict";

const SW = {};

// Element builder: h("div", {class: "x", onclick: fn}, "text", child, ...)
SW.h = function (tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
};

SW.api = async function (method, url, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    let msg = res.statusText;
    if (data && data.detail) {
      msg = typeof data.detail === "string" ? data.detail
        : data.detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
};

// Display formatting. Values arrive in canonical units (degC, %, Pa, m/s).
SW.KIND_FMT = {
  temperature: { unit: "°C", dec: 1, conv: (v) => v },
  dew_point: { unit: "°C", dec: 1, conv: (v) => v },
  humidity: { unit: "%", dec: 0, conv: (v) => v },
  pressure: { unit: "hPa", dec: 1, conv: (v) => v / 100 },
  speed_of_sound: { unit: "m/s", dec: 2, conv: (v) => v },
  contact: { unit: "", dec: 0, conv: (v) => v },
};

SW.fmt = function (kind, value, withUnit = true) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const f = SW.KIND_FMT[kind] || { unit: "", dec: 1, conv: (v) => v };
  const s = f.conv(value).toFixed(f.dec);
  return withUnit && f.unit ? `${s} ${f.unit}` : s;
};

SW.fmtDelta = function (kind, delta) {
  if (delta === null || delta === undefined) return "—";
  const f = SW.KIND_FMT[kind] || { unit: "", dec: 1, conv: (v) => v };
  const dec = kind === "speed_of_sound" ? 2 : f.dec + 1;
  return `${SW.signed(f.conv(delta), dec)} ${f.unit}`.trim();
};

// "+1.23" / "-1.23" / "0.00" (never "-0.00" from rounding noise)
SW.signed = function (v, dec) {
  const s = v.toFixed(dec);
  if (Number(s) === 0) return (0).toFixed(dec);
  return v > 0 ? `+${s}` : s;
};

SW.time = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
SW.timeSec = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

SW.age = function (updated, now) {
  if (!updated) return "never";
  const s = Math.max(0, Math.round(now - updated));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  return `${(s / 3600).toFixed(1)}h ago`;
};

// Live feed with automatic reconnect (server restarts, Wi-Fi blips).
SW.connect = function (query, onMessage, onStatus) {
  let ws = null;
  let delay = 1000;
  let pingTimer = null;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws${query || ""}`);
    ws.onopen = () => {
      delay = 1000;
      onStatus(true);
      pingTimer = setInterval(() => ws.readyState === 1 && ws.send('{"type":"ping"}'), 15000);
    };
    ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
    ws.onclose = () => {
      onStatus(false);
      clearInterval(pingTimer);
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 10000);
    };
    ws.onerror = () => ws.close();
  };
  open();
  return { send: (obj) => ws && ws.readyState === 1 && ws.send(JSON.stringify(obj)) };
};

// Alarm sounder. Browsers only allow audio after a user gesture, so the
// dashboard shows an "Enable sound" button until the user taps it.
SW.Sounder = class {
  constructor() { this.ctx = null; this.timer = null; }
  enable() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state === "suspended") this.ctx.resume();
    return this.ctx.state !== "closed";
  }
  get enabled() { return !!this.ctx && this.ctx.state === "running"; }
  beep() {
    if (!this.enabled) return;
    const t = this.ctx.currentTime;
    for (const [start, freq] of [[0, 880], [0.25, 660]]) {
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t + start);
      gain.gain.exponentialRampToValueAtTime(0.3, t + start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + start + 0.2);
      osc.connect(gain).connect(this.ctx.destination);
      osc.start(t + start);
      osc.stop(t + start + 0.22);
    }
  }
  set(sounding) {
    if (sounding && !this.timer) { this.beep(); this.timer = setInterval(() => this.beep(), 2000); }
    if (!sounding && this.timer) { clearInterval(this.timer); this.timer = null; }
  }
};
