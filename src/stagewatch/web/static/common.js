// Shared helpers for Stagewatch pages. No framework, no build step, no CDN:
// everything is served by the Stagewatch server so it works on an offline
// show network.
"use strict";

const SW = {};

// Older browsers (Chrome before 86, Safari before 14) lack replaceChildren; the pages use it.
if (!Element.prototype.replaceChildren) {
  Element.prototype.replaceChildren = function (...nodes) {
    while (this.firstChild) this.removeChild(this.firstChild);
    this.append(...nodes);
  };
}
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
    if (data && data.retry_after) err.retryAfter = data.retry_after;
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

// ---- Site time ------------------------------------------------------------
// Every time on screen is the show site's wall clock, 24-hour, whatever zone the tablet is set
// to. The server sends a `time` block ({timezone, utc_offset_s, day_rollover}) in the snapshot
// and /api/info; pages pass it to SW.setSiteTime(). Inputs are UTC epoch seconds.
// - timezone set: Intl.DateTimeFormat with that IANA zone (DST-correct for any instant).
// - timezone "" (not set): the server computer's zone, as ts + utc_offset_s read in UTC. Exact
//   except across a DST change while no zone is set.
// - nothing from the server yet: this browser's own zone.
// Hours come from formatToParts and are assembled here: `hourCycle` is too new for iOS 12, and
// `hour12: false` makes some browsers print midnight as "24".
SW.site = { timezone: "", utc_offset_s: null, day_rollover: "06:00" };
SW._tf = {};   // cached formatters, one per option set; cleared when the zone changes
SW.setSiteTime = function (t) {
  if (!t) return;
  if (t.timezone !== SW.site.timezone) SW._tf = {};
  SW.site = { timezone: t.timezone || "", utc_offset_s: typeof t.utc_offset_s === "number" ? t.utc_offset_s : null,
    day_rollover: t.day_rollover || "06:00" };
};
// The browser's locale with Latin digits forced ("ar-EG" -> "ar-EG-u-nu-latn"). The Unicode
// "-u-nu-" extension works in every Intl version (iOS 10+); the newer `numberingSystem`
// option is passed too and is simply ignored by browsers that don't know it.
SW._latnLocale = function () {
  try {
    const l = new Intl.DateTimeFormat().resolvedOptions().locale;
    return l && l.indexOf("-u-") < 0 ? l + "-u-nu-latn" : l;
  } catch (_) { return undefined; }
};
SW._formatter = function (key, opts) {
  if (!(key in SW._tf)) {
    SW._tf[key] = null;   // stays null for an unknown zone in this browser, or no Intl
    const locales = key.indexOf("parts") === 0 ? ["en-US"] : [SW._latnLocale(), undefined];
    for (const loc of locales) {
      try { SW._tf[key] = new Intl.DateTimeFormat(loc, opts); break; } catch (_) { /* try the next */ }
    }
  }
  return SW._tf[key];
};
// {y, mo, d, h, mi, s} of the site's wall clock at ts, plus how they were obtained.
SW._siteParts = function (ts) {
  const tz = SW.site.timezone;
  if (tz) {
    const f = SW._formatter("parts|" + tz, { timeZone: tz, hour12: false, year: "numeric", month: "numeric",
      day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" });
    if (f && f.formatToParts) {
      try {
        const p = {};
        for (const part of f.formatToParts(new Date(ts * 1000))) p[part.type] = Number(part.value);
        if (p.year >= 0 && p.hour >= 0) return { y: p.year, mo: p.month, d: p.day, h: p.hour % 24, mi: p.minute, s: p.second };
      } catch (_) { /* fall through to the offset */ }
    }
  }
  let off = SW.site.utc_offset_s;
  if (off === null) off = -new Date(ts * 1000).getTimezoneOffset() * 60;   // not known yet: this browser
  const d = new Date((Math.floor(ts) + off) * 1000);
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth() + 1, d: d.getUTCDate(), h: d.getUTCHours(), mi: d.getUTCMinutes(), s: d.getUTCSeconds() };
};
// Site UTC offset (s) at ts: used to align chart ticks to site-local hours.
SW.siteOffset = function (ts) {
  const p = SW._siteParts(ts);
  return Math.round((Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s) / 1000 - Math.floor(ts)) / 60) * 60;
};
SW.fmtOffset = function (sec) {
  const m = Math.round(Math.abs(sec) / 60);
  const pad = (n) => (n < 10 ? "0" : "") + n;
  return `UTC${sec < 0 ? "-" : "+"}${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
};
// "14:05", {seconds: true} "14:05:09", {date: true} "Fri 2 Oct 2026, 14:05" (date order per locale).
SW.fmtTime = function (ts, opts) {
  if (ts === null || ts === undefined || !Number.isFinite(Number(ts))) return "—";
  ts = Number(ts);
  const o = opts || {};
  const p = SW._siteParts(ts);
  const pad = (n) => (n < 10 ? "0" : "") + n;
  let s = `${pad(p.h)}:${pad(p.mi)}`;
  if (o.seconds) s += `:${pad(p.s)}`;
  if (!o.date) return s;
  // Date part: formatted in UTC from the site's calendar date, so no zone maths happens twice.
  const f = SW._formatter("date", { timeZone: "UTC", numberingSystem: "latn", weekday: "short", day: "numeric",
    month: "short", year: "numeric" });
  let ds = `${p.y}-${pad(p.mo)}-${pad(p.d)}`;
  if (f) { try { ds = f.format(new Date(Date.UTC(p.y, p.mo - 1, p.d, 12))); } catch (_) { /* keep ISO */ } }
  return `${ds}, ${s}`;
};

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
// dashboard has a permanent "Alarm sound: On/Off" button; tapping it arms audio.
// `muted` is the user's choice (button set to Off): the alarm stays visible but silent.
SW.Sounder = class {
  constructor() { this.ctx = null; this.timer = null; this.muted = false; this.onchange = () => {}; }
  enable() {
    try {
      if (!this.ctx) {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        this.ctx.onstatechange = () => this.onchange();
      }
      if (this.ctx.state === "suspended") this.ctx.resume();
    } catch (_) { return false; }
    return this.ctx.state !== "closed";
  }
  get enabled() { return !!this.ctx && this.ctx.state === "running"; }
  beep() {
    if (!this.enabled || this.muted) return;
    const t = this.ctx.currentTime;
    for (const [start, freq] of [[0, 880], [0.25, 660]]) {
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t + start);
      gain.gain.exponentialRampToValueAtTime(0.3, t + start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + start + 0.2);
      osc.connect(gain);   // not chained: old Safari's connect() returns nothing
      gain.connect(this.ctx.destination);
      osc.start(t + start);
      osc.stop(t + start + 0.22);
    }
  }
  set(sounding) {
    if (sounding && !this.timer) { this.beep(); this.timer = setInterval(() => this.beep(), 2000); }
    if (!sounding && this.timer) { clearInterval(this.timer); this.timer = null; }
  }
};

// ---- Connection banner -------------------------------------------------
// SW.connection.report(up) is called with the live connection state. A drop shorter than
// 3 s is ignored (no flashing); after that a full-width banner shows the elapsed seconds and
// body gets the "disconnected" class so on-screen values look stale. Hidden again on reconnect.
SW.CONNECTION_GRACE_S = 3;
SW.connection = (() => {
  let downSince = null;
  let timer = null;
  let banner = null;
  const ensure = () => {
    if (banner) return banner;
    banner = SW.h("div", { class: "disconnect-banner", role: "alert", id: "disconnect-banner", hidden: true });
    document.body.prepend(banner);
    return banner;
  };
  const tick = () => {
    if (downSince === null) return;
    const secs = Math.floor((Date.now() - downSince) / 1000);
    if (secs < SW.CONNECTION_GRACE_S) return;
    ensure().hidden = false;
    banner.textContent = `Disconnected from Stagewatch \u2014 reconnecting\u2026 (${secs} s)`;
    document.body.classList.add("disconnected");
  };
  return {
    get down() { return downSince !== null; },
    report(up) {
      if (up) {
        downSince = null;
        clearInterval(timer); timer = null;
        if (banner) banner.hidden = true;
        document.body.classList.remove("disconnected");
      } else if (downSince === null) {
        downSince = Date.now();
        timer = setInterval(tick, 1000);
      }
    },
  };
})();

// For pages without a WebSocket (admin): a light heartbeat against /api/info.
SW.heartbeat = function (everyMs = 2000) {
  const beat = async () => {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 4000);
      const res = await fetch("/api/info", { cache: "no-store", signal: ctrl.signal });
      clearTimeout(t);
      SW.connection.report(res.status < 500);
    } catch (_) { SW.connection.report(false); }
  };
  setInterval(beat, everyMs);
};

// ---- QR codes (local; uses the vendored qrcode-generator in /static/vendor/qrcode.js) ----
// Returns an <svg> element (built with DOM calls, no innerHTML) or null if it cannot be made.
SW.qrSvg = function (text, size = 176) {
  if (typeof qrcode !== "function") return null;
  let qr;
  try { qr = qrcode(0, "M"); qr.addData(text); qr.make(); } catch (_) { return null; }
  const n = qr.getModuleCount();
  const quiet = 2;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${n + quiet * 2} ${n + quiet * 2}`);
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `QR code for ${text}`);
  svg.setAttribute("shape-rendering", "crispEdges");
  const bg = document.createElementNS(NS, "rect");
  bg.setAttribute("width", "100%"); bg.setAttribute("height", "100%"); bg.setAttribute("fill", "#ffffff");
  svg.append(bg);
  let d = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + quiet} ${r + quiet}h1v1h-1z`;
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d); path.setAttribute("fill", "#000000");
  svg.append(path);
  return svg;
};
