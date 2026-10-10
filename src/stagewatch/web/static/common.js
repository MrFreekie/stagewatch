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

// SVG element builder, the same shape as SW.h: svg("circle", {cx: 5, r: 2}, child...). Built with
// createElementNS and setAttribute (no innerHTML).
SW.svg = function (tag, attrs, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    el.setAttribute(k, v === true ? "" : v);
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
    err.detail = data ? data.detail : undefined;   // the raw list, for forms that mark the field
    if (data && data.retry_after) err.retryAfter = data.retry_after;
    throw err;
  }
  return data;
};

// Small pieces the admin pages share (Admin and the Schedule editor).
SW.toast = function (msg, isError) {
  const el = SW.h("div", { class: "toast" + (isError ? " error" : "") }, msg);
  document.body.append(el);
  setTimeout(() => el.remove(), 3500);
};
// Alarm notice timers. The server sends, for each alarm, how many seconds are left until an
// acknowledged advisory leaves the list (hide_in) or an unacknowledged one moves into "older
// notices" (fold_in), counted from the moment the list was sent. The page adds the time since it
// arrived, so nothing needs pushing every second and a reload simply asks again.
// Returns { shown, older, next }: next is the seconds until the list should be worked out again
// (null = never).
SW.splitAlarms = function (alarms, elapsedS) {
  const shown = [], older = [];
  let next = null;
  const soon = (s) => { if (next === null || s < next) next = s; };
  for (const a of alarms) {
    if (a.hide_in !== null && a.hide_in !== undefined) {
      const left = a.hide_in - elapsedS;
      if (left <= 0) continue;
      soon(left);
    }
    let isOld = !!a.old;
    if (!isOld && a.fold_in !== null && a.fold_in !== undefined) {
      const left = a.fold_in - elapsedS;
      if (left <= 0) isOld = true; else soon(left);
    }
    (isOld ? older : shown).push(a);
  }
  return { shown, older, next };
};
// The header's messages icon: the same alarm list the alarm bar shows (shown plus folded older
// notices, so the two always agree), sorted worst first then newest. A loud alarm nobody has
// acknowledged is an "alarm" (red), an acknowledged one still active is a "warning" (amber), and a
// quiet notice is "info" (blue). While the alarms are sounding the worst is always "alarm".
// Nothing is made up here: no alarms in, no messages out.
SW.MSG_SEV = {
  alarm: { rank: 3, word: "Alarm", symbol: "!", name: "alarm" },
  warning: { rank: 2, word: "Warning", symbol: "▲", name: "warning" },
  info: { rank: 1, word: "Information", symbol: "i", name: "information" },
};
SW.messageSummary = function (alarms, elapsedS, sounding) {
  const split = SW.splitAlarms(alarms || [], elapsedS);
  const items = [];
  const add = (a, older) => {
    const sev = a.silent ? "info" : (a.acked ? "warning" : "alarm");
    items.push({ id: a.id, sev: sev, text: a.message, since: a.since, older: older });
  };
  split.shown.forEach((a) => add(a, false));
  split.older.forEach((a) => add(a, true));
  items.sort((a, b) => (SW.MSG_SEV[b.sev].rank - SW.MSG_SEV[a.sev].rank) || ((b.since || 0) - (a.since || 0)));
  let worst = items.length ? items[0].sev : null;
  if (items.length && sounding) worst = "alarm";
  const n = items.length;
  const label = !n ? "No messages" : `${n} ${n === 1 ? "message" : "messages"}, worst: ${SW.MSG_SEV[worst].name}`;
  return { items: items, count: n, worst: worst, label: label, next: split.next };
};
// The header's connection indicator: Online, Offline or Error, always a colour AND a symbol AND a
// word. Stale beats online: Online is shown only while the live feed is up AND something (a data
// message or the server's reply to our ping) arrived within SW.FEED_SILENT_S. Times are ms from
// one clock (Date.now()). o = { now, wsUp, since (when wsUp last changed), lastHeard (or null),
// fault (the server answered but with an error, or a message could not be read) }.
// Plain words only: no addresses, host names, ports or internal error text.
SW.FEED_SILENT_S = 40;   // the page pings every 15 s; two missed replies and a bit
SW.CONN_STATES = {
  online: { word: "Online", symbol: "●", cls: "ok" },
  offline: { word: "Offline", symbol: "✕", cls: "off" },
  error: { word: "Error", symbol: "!", cls: "err" },
};
SW.connStatus = function (o) {
  const secs = (ms) => Math.max(0, Math.floor(ms / 1000));
  const heardAge = o.lastHeard === null || o.lastHeard === undefined ? null : secs(o.now - o.lastHeard);
  const sinceAge = o.since === null || o.since === undefined ? null : secs(o.now - o.since);
  const silent = !!o.wsUp && (heardAge === null || heardAge > SW.FEED_SILENT_S);
  let state;
  if (!o.wsUp || silent) state = "offline";
  else if (o.fault) state = "error";
  else state = "online";
  const meta = SW.CONN_STATES[state];
  const dur = (s) => SW.fmtDuration(s, false);
  const lastData = heardAge === null ? "No data received yet" : `Last data ${dur(heardAge)} ago`;
  const lines = [];
  let headline, advice;
  if (state === "online") {
    headline = "Live data is arriving.";
    lines.push(sinceAge === null ? "Connected" : `Connected for ${dur(sinceAge)}`, lastData);
    advice = "Nothing to do.";
  } else if (state === "error") {
    headline = "Stagewatch answers, but something is wrong with the data.";
    lines.push(lastData, sinceAge === null ? "" : `Connected for ${dur(sinceAge)}`);
    advice = "Wait a minute, then reload the page. If it stays like this, tell the system admin.";
  } else {
    headline = o.wsUp ? "Connected, but no data is coming through. The figures on screen are frozen."
      : "This screen has lost its connection to Stagewatch. The figures on screen are frozen.";
    lines.push(lastData, !o.wsUp && sinceAge !== null ? `Disconnected for ${dur(sinceAge)}` : "");
    advice = "Check the Wi-Fi, then reload the page.";
  }
  const label = state === "online" ? "Online" : state === "error" ? "Error. Data may be wrong"
    : heardAge === null ? "Offline. No data yet" : `Offline. Last data ${dur(heardAge)} ago`;
  return { state: state, word: meta.word, symbol: meta.symbol, cls: meta.cls, headline: headline,
    lines: lines.filter((x) => x), advice: advice, label: label, heardAge: heardAge, sinceAge: sinceAge };
};
// A card's own status for an EXTERNAL source (Ontime, Smaart, DirectOut GLOBCON): Online, Offline or
// Error, always colour AND symbol AND word. One pure function decides; SW.sourceStatusUi draws it.
// o = { source: "ontime" | "smaart" | "globcon",
//       reported: "ok" | "stale" | "waiting" | "offline" | "error" | "locked"  (what the source's own status says),
//       ageS: seconds since the last data (null = none seen yet),  staleS: older than this is stale (optional),
//       stateS: seconds in this state (null = unknown),  stateExact: true when this page saw the change happen }
// Precedence: an explicit error (or locked) shows Error; otherwise stale beats online, and offline beats the rest.
// "Waiting" (connected, no data yet) is Offline, never Online. No Online without fresh data.
// Plain words only: no addresses, host names, ports or internal error text (this runs on public screens).
SW.SOURCES = {
  ontime: { name: "Ontime", offline: "Check Ontime is running and its address in Admin → Ontime.",
    error: "Check Ontime is up to date, then tell the system admin." },
  smaart: { name: "Smaart", offline: "Check Smaart is running with its API on.",
    error: "Check the input and the chosen values in Smaart, then tell the system admin." },
  globcon: { name: "GLOBCON", offline: "Check GLOBCON is running and the Windows firewall allows it.",
    error: "Tell the system admin." },
};
SW.sourceStatus = function (o) {
  const src = SW.SOURCES[o.source] || { name: "The source", offline: "Check it is running.", error: "Tell the system admin." };
  const dur = (s) => SW.fmtDuration(s, false);
  const num = (x) => typeof x === "number" && Number.isFinite(x);
  const age = num(o.ageS) ? Math.max(0, o.ageS) : null;
  const reported = o.reported || "offline";
  let state, kind;   // kind: why it is not online
  if (reported === "error" || reported === "locked") { state = "error"; kind = reported; }
  else if (reported === "offline") { state = "offline"; kind = "offline"; }
  else if (reported === "waiting" || (reported === "ok" && age === null)) { state = "offline"; kind = "waiting"; }
  else if (reported === "stale" || (reported === "ok" && num(o.staleS) && age > o.staleS)) { state = "offline"; kind = "stale"; }
  else { state = "online"; kind = ""; }
  const meta = SW.CONN_STATES[state];
  const lastData = age === null ? "No data received yet" : `Last data ${dur(age)} ago`;
  const st = num(o.stateS) ? Math.max(0, o.stateS) : null;
  const how = (word) => (st === null ? "" : `${word} for ${o.stateExact ? "" : "at least "}${dur(st)}`);
  let headline, advice, lines;
  if (state === "online") {
    headline = `${src.name} is sending live data.`;
    lines = [how("Online"), lastData];
    advice = "Nothing to do.";
  } else if (state === "error") {
    headline = kind === "locked" ? `${src.name} wants a password for this controller.`
      : `${src.name} is answering, but Stagewatch cannot use what it sends.`;
    lines = [lastData, how("Problem")];
    advice = kind === "locked" ? "Ask the system admin to enter the password in Admin." : src.error;
  } else if (kind === "waiting") {
    headline = `Connected to ${src.name}, but no data has arrived yet. Waiting.`;
    lines = ["Waiting for the first data", how("Waiting")];
    advice = `If this goes on: ${src.offline.charAt(0).toLowerCase()}${src.offline.slice(1)}`;
  } else if (kind === "stale") {
    headline = `${src.name} has stopped sending data. The figures on this card are frozen.`;
    lines = [lastData, how("Quiet")];
    advice = src.offline;
  } else {
    headline = `Stagewatch is not getting data from ${src.name}.`;
    lines = [lastData, how("Offline")];
    advice = src.offline;
  }
  const label = `${src.name}: ` + (state === "online" ? "Online"
    : state === "error" ? (kind === "locked" ? "Error. Password needed" : "Error. Data may be wrong")
    : kind === "waiting" ? "Offline. Waiting for data" : age === null ? "Offline. No data yet" : `Offline. Last data ${dur(age)} ago`);
  return { state: state, kind: kind, word: meta.word, symbol: meta.symbol, cls: meta.cls, headline: headline,
    lines: lines.filter((x) => x), advice: advice, label: label, ageS: age };
};
// What each card's own status data means, as input for SW.sourceStatus (and SW.sourceStatusUi.update).
// Pure. dataAt is the server time (seconds) of the newest data, or null.
SW.sourceInput = {
  // "ontime_timer" / "ontime_rundown" / "wall_clock" message: status ok | offline | error, received_at.
  ontime: function (m, staleS) {
    if (!m) return { source: "ontime", reported: "offline", dataAt: null };
    const ok = m.status === "ok";
    return { source: "ontime", reported: m.status === "error" ? "error" : ok ? "ok" : "offline", staleS: staleS,
      dataAt: ok && typeof m.received_at === "number" ? m.received_at : null };
  },
  // "globcon_meters" message and the dashboard's controller (with meters_at): status ok | stale | waiting | offline.
  globcon: function (m, ctrl, staleS) {
    if (!m) return { source: "globcon", reported: "waiting", dataAt: null };
    const at = ctrl && typeof ctrl.meters_at === "number" ? ctrl.meters_at : null;
    const reported = ctrl && ctrl.locked ? "locked" : m.status === "offline" ? "offline" : at === null ? "waiting" : "ok";
    return { source: "globcon", reported: reported, staleS: staleS, dataAt: at };
  },
  // Smaart: its device status (initializing | ok | missing | fault | compromised) and the shown values.
  smaart: function (dev, ents) {
    const list = ents || [];
    const stamps = list.filter((e) => typeof e.updated === "number" && e.updated > 0).map((e) => e.updated);
    const at = stamps.length ? Math.max.apply(null, stamps) : null;
    const fresh = list.some((e) => e.updated && !e.stale && typeof e.value === "number" && !Number.isNaN(e.value));
    const s = dev ? dev.status : "";
    let reported;
    if (s === "missing") reported = "offline";
    else if (s === "fault" || s === "compromised") reported = "error";
    else if (!dev || s === "initializing") reported = "waiting";
    else reported = fresh ? "ok" : at === null ? "waiting" : "stale";
    return { source: "smaart", reported: reported, dataAt: at };
  },
};
// The small indicator in a card's header: a button (symbol + word) and a pop-out. Build once, then call
// ui.update(input, nowServerSeconds) whenever the card redraws. It remembers when it last saw the state
// change and the newest data time. aria-expanded, a polite announcement on change, Escape or a click
// elsewhere closes. A 1 s timer runs only while the pop-out is open. Nothing flashes.
SW.sourceStatusUi = function (source) {
  const h = SW.h;
  const sym = h("span", { class: "ss-sym", "aria-hidden": "true" });
  const word = h("span", { class: "ss-word" });
  const btn = h("button", { class: "ss-btn ss-off", type: "button", "aria-expanded": "false" }, sym, word);
  const live = h("span", { class: "sr-only", role: "status", "aria-live": "polite" });
  const head = h("p", { class: "ss-head" }), list = h("ul", {}), adv = h("p", { class: "ss-advice" });
  const panel = h("div", { class: "ss-panel", role: "region", hidden: true }, head, list, adv);
  const el = h("span", { class: "ss" }, btn, live, panel);
  const ui = { el: el, btn: btn, open: false, input: null, key: "", since: Date.now(), exact: false, dataAt: null, timer: null, at: Date.now(), now: 0 };
  function status(stateS) {
    const now = ui.now + (Date.now() - ui.at) / 1000;
    const ageS = ui.dataAt === null ? null : Math.max(0, now - ui.dataAt);
    return SW.sourceStatus(Object.assign({}, ui.input, { ageS: ageS, stateS: stateS, stateExact: ui.exact }));
  }
  function draw() {
    if (!ui.input) return null;
    const s = status((Date.now() - ui.since) / 1000);
    const cls = `ss-btn ss-${s.cls}`;
    if (btn.className !== cls) btn.className = cls;
    if (btn.title !== s.label) { btn.title = s.label; btn.setAttribute("aria-label", s.label); }
    if (sym.textContent !== s.symbol) sym.textContent = s.symbol;
    if (word.textContent !== s.word) word.textContent = s.word;
    if (ui.open) {
      head.textContent = s.headline;
      list.replaceChildren(...s.lines.map((t) => h("li", {}, t)));
      adv.textContent = s.advice;
    }
    return s;
  }
  ui.update = function (input, now) {
    ui.input = input; ui.now = now; ui.at = Date.now();
    if (typeof input.dataAt === "number") ui.dataAt = input.dataAt;
    const probe = status(0);
    const key = `${probe.state}:${probe.kind}`;
    if (key !== ui.key) {
      const first = ui.key === "";
      ui.key = key; ui.since = Date.now(); ui.exact = !first;
      if (!first || probe.state !== "online") live.textContent = probe.label;   // a change is announced; so is a bad start
    }
    return draw();
  };
  ui.setOpen = function (open, refocus) {
    ui.open = open;
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    clearInterval(ui.timer);
    if (open) { draw(); ui.timer = setInterval(draw, 1000); }
    if (!open && refocus) btn.focus();
  };
  btn.addEventListener("click", () => { ui.setOpen(!ui.open); });
  document.addEventListener("click", (ev) => { if (ui.open && !el.contains(ev.target)) ui.setOpen(false); });
  document.addEventListener("keydown", (ev) => { if (ui.open && (ev.key === "Escape" || ev.key === "Esc")) ui.setOpen(false, true); });
  panel.setAttribute("aria-label", `${(SW.SOURCES[source] || { name: "Source" }).name} status`);
  return ui;
};
SW.card = (title, ...body) => SW.h("section", { class: "card" }, SW.h("h2", {}, title), ...body);
// A normal link that looks like a button, at least 44 px tall.
SW.linkButton = function (text, href, primary) {
  return SW.h("a", { href: href, class: "touch",
    style: "display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;min-height:44px;padding:6px 16px;border-radius:8px;text-decoration:none;font-weight:600;"
      + (primary ? "background:var(--accent);color:var(--on-accent);border:1px solid var(--accent);" : "background:var(--panel-2);color:inherit;border:1px solid var(--grid);") }, text);
};
// Where Admin sends you after logging in. An allow-list of one fixed path: the value of ?next=
// must equal "/schedule" exactly (after decoding), never any other path or URL, so the login page
// can't be used to bounce someone to another site.
SW.ADMIN_NEXT = ["/schedule"];
SW.adminNext = function (search) {
  const m = /^(?:\?)?(?:[^&]*&)*?next=([^&]*)/.exec(String(search || ""));
  if (!m) return "";
  let v;
  try { v = decodeURIComponent(m[1]); } catch (_) { return ""; }
  return SW.ADMIN_NEXT.indexOf(v) >= 0 ? v : "";
};

// ---------------------------------------------------------------- safe Markdown
// SW.renderMarkdown(text, {links: true}) -> DocumentFragment. A small, deliberately limited subset:
// headings (# to ###, shown as h3 to h5), paragraphs (a single newline is a line break), "-", "*"
// and "1." lists, **bold**, *italic*, `code`, and [label](url) links. Everything else, including
// any HTML, is shown as literal text. Built only with createElement/createTextNode (never
// innerHTML), so input can never become markup. Only http:, https: and mailto: links become <a>;
// any other scheme (javascript:, data:, vbscript:, relative) stays as plain text.
// Links are off unless {links: true} is passed (policy documents opt in; setlists never do), so by
// default [label](url) shows as plain text. Input, line length, nesting and element count are
// capped so a huge or crafted document cannot hang a low-end tablet.
SW.MD_LIMITS = { chars: 100000, lines: 2000, lineChars: 2000, depth: 3, nodes: 6000, label: 300, url: 2000 };

SW.mdSafeUrl = function (raw) {
  const s = String(raw).trim();
  if (!s || s.length > SW.MD_LIMITS.url || !/^(https?:\/\/|mailto:)/i.test(s)) return null;
  let u;
  try { u = new URL(s); } catch (_) { return null; }
  if (u.protocol !== "http:" && u.protocol !== "https:" && u.protocol !== "mailto:") return null;
  if (u.protocol !== "mailto:" && (u.username || u.password)) return null;
  return u.href;
};

SW.renderMarkdown = function (text, opts) {
  const L = SW.MD_LIMITS;
  const links = !!(opts && opts.links === true);
  const frag = document.createDocumentFragment();
  let src = typeof text === "string" ? text : (text === null || text === undefined ? "" : String(text));
  let truncated = false;
  if (src.length > L.chars) { src = src.slice(0, L.chars); truncated = true; }
  const budget = { nodes: 0 };
  const el = (tag, cls) => { budget.nodes++; const e = document.createElement(tag); if (cls) e.className = cls; return e; };
  const txt = (parent, s) => { if (s) { budget.nodes++; parent.appendChild(document.createTextNode(s)); } };

  // Index of the bracket that closes the one at `open`, or -1. Nested brackets are allowed.
  function closeBracket(s, open) {
    let d = 0;
    const end = Math.min(s.length, open + L.label);
    for (let i = open; i < end; i++) {
      const c = s.charAt(i);
      if (c === "[") d++;
      else if (c === "]") { d--; if (d === 0) return i; }
    }
    return -1;
  }

  function inline(parent, s, depth, allowLinks) {
    if (depth > L.depth || budget.nodes > L.nodes || s.length > L.lineChars) { txt(parent, s); return; }
    let buf = "";
    const flush = () => { txt(parent, buf); buf = ""; };
    let i = 0;
    while (i < s.length) {
      const c = s.charAt(i);
      if (c === "`") {
        const j = s.indexOf("`", i + 1);
        if (j > i + 1) { flush(); const e = el("code"); e.textContent = s.slice(i + 1, j); parent.appendChild(e); i = j + 1; continue; }
      } else if (c === "*" && s.charAt(i + 1) === "*") {
        const j = s.indexOf("**", i + 2);
        if (j > i + 2) { flush(); const e = el("strong"); inline(e, s.slice(i + 2, j), depth + 1, allowLinks); parent.appendChild(e); i = j + 2; continue; }
      } else if (c === "*") {
        let j = s.indexOf("*", i + 1);
        while (j > 0 && s.charAt(j + 1) === "*" && s.charAt(j - 1) !== "*") j = s.indexOf("*", j + 2);
        if (j > i + 1 && !/\s/.test(s.charAt(i + 1)) && !/\s/.test(s.charAt(j - 1))) {
          flush(); const e = el("em"); inline(e, s.slice(i + 1, j), depth + 1, allowLinks); parent.appendChild(e); i = j + 1; continue;
        }
      } else if (c === "[") {
        const close = closeBracket(s, i);
        if (close > 0 && s.charAt(close + 1) === "(") {
          const end = s.indexOf(")", close + 2);
          if (end > 0) {
            const label = s.slice(i + 1, close);
            const href = allowLinks && links ? SW.mdSafeUrl(s.slice(close + 2, end)) : null;
            flush();
            if (href && label.trim()) {
              const a = el("a");
              a.href = href;
              a.rel = "noopener noreferrer";
              a.target = "_blank";
              inline(a, label, depth + 1, false);
              parent.appendChild(a);
            } else {
              txt(parent, s.slice(i, end + 1));   // unsafe or unsupported link: plain text
            }
            i = end + 1;
            continue;
          }
        }
      }
      buf += c;
      i++;
    }
    flush();
  }

  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  if (lines.length > L.lines) { lines.length = L.lines; truncated = true; }
  const reHead = /^\s{0,3}(#{1,3})\s+(.*)$/;
  const reBullet = /^\s{0,3}[-*+]\s+(.*)$/;
  const reNumber = /^\s{0,3}\d{1,9}[.)]\s+(.*)$/;
  let para = null, list = null, listTag = "";
  const endPara = () => { para = null; };
  const endList = () => { list = null; listTag = ""; };
  for (let n = 0; n < lines.length && budget.nodes <= L.nodes; n++) {
    const line = lines[n];
    let m;
    if (!line.trim()) { endPara(); endList(); continue; }
    if ((m = reHead.exec(line))) {
      endPara(); endList();
      const e = el("h" + (m[1].length + 2), "md-h" + m[1].length);
      inline(e, m[2].trim(), 0, true);
      frag.appendChild(e);
    } else if ((m = reBullet.exec(line)) || (m = reNumber.exec(line))) {
      endPara();
      const tag = reBullet.test(line) ? "ul" : "ol";
      if (!list || listTag !== tag) { list = el(tag); listTag = tag; frag.appendChild(list); }
      const li = el("li");
      inline(li, m[1].trim(), 0, true);
      list.appendChild(li);
    } else {
      endList();
      if (!para) { para = el("p"); frag.appendChild(para); }
      else { para.appendChild(el("br")); }
      inline(para, line, 0, true);
    }
  }
  if (truncated) { const p = el("p", "muted"); txt(p, "(Text shortened: it was too long to show in full.)"); frag.appendChild(p); }
  return frag;
};

// Timeline marker look by origin, shared by the chart and the marker list. The origin is the
// text before any ":" ("dashboard:foh" is a crew mark); unknown origins count as crew marks.
// Colours are the --marker-<key> tokens in style.css (--marker-<key>-ink is the text on them).
SW.MARKER_STYLES = {
  crew: { key: "crew", glyph: "▼", name: "Crew mark", fallback: "#f5b83d", ink: "#111" },
  system: { key: "system", glyph: "■", name: "Stagewatch note", fallback: "#9aa3b5", ink: "#111" },
  alarm: { key: "alarm", glyph: "▲", name: "Alarm", fallback: "#ff8a3d", ink: "#111" },
  schedule: { key: "schedule", glyph: "◆", name: "Schedule", fallback: "#4da3ff", ink: "#111" },
  device: { key: "device", glyph: "●", name: "Contact or device", fallback: "#c38bff", ink: "#111" },
};
SW.MARKER_SOURCES = { hub: "system", updater: "system", alarm: "alarm", schedule: "schedule", contact: "device", device: "device" };
SW.markerStyle = function (src) {
  const base = String(src === null || src === undefined ? "" : src).split(":")[0].trim().toLowerCase();
  const key = Object.prototype.hasOwnProperty.call(SW.MARKER_SOURCES, base) ? SW.MARKER_SOURCES[base] : "crew";
  return SW.MARKER_STYLES[key];
};

// Hidden markers stay in the history but are left off the chart and the marker list, unless the
// list's "Show hidden" box is ticked. Returns a new array, oldest first as given.
SW.visibleMarkers = function (markers, showHidden) {
  return (markers || []).filter(function (m) { return !!showHidden || !m.hidden; });
};
SW.hiddenMarkerCount = function (markers) {
  return (markers || []).filter(function (m) { return !!m.hidden; }).length;
};
SW.NOTE_MAX = 1000;   // characters in a marker note (the server checks too)

// Schedule rows whose "Marker" box is ticked unless changed: soundcheck, doors and act. The list
// comes from the server when it sends one (admin state: schedule_limits.marker_kinds).
SW.SCHEDULE_MARKER_KINDS = ["soundcheck", "doors", "act"];
SW.scheduleMarkerDefault = function (kind, kinds) {
  return (Array.isArray(kinds) ? kinds : SW.SCHEDULE_MARKER_KINDS).indexOf(kind) >= 0;
};

// Display formatting. Values arrive in canonical units (degC, %, Pa, m/s).
SW.KIND_FMT = {
  temperature: { unit: "°C", dec: 1, conv: (v) => v },
  dew_point: { unit: "°C", dec: 1, conv: (v) => v },
  humidity: { unit: "%", dec: 0, conv: (v) => v },
  pressure: { unit: "hPa", dec: 1, conv: (v) => v / 100 },
  speed_of_sound: { unit: "m/s", dec: 2, conv: (v) => v },
  contact: { unit: "", dec: 0, conv: (v) => v },
  battery: { unit: "%", dec: 0, conv: (v) => v },
  signal_strength: { unit: "dBm", dec: 0, conv: (v) => v },
  sound_level: { unit: "dB", dec: 1, conv: (v) => v },   // shown to one decimal, never changed or averaged
};

// Fixed-point with a comma every three digits and a full stop for decimals ("1,013.2"), whatever
// the browser's locale.
SW.num = function (v, dec) {
  const s = Number(v).toFixed(dec);
  const m = /^(-?)(\d+)(.*)$/.exec(s);
  return m ? m[1] + m[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + m[3] : s;
};

SW.fmt = function (kind, value, withUnit = true) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const f = SW.KIND_FMT[kind] || { unit: "", dec: 1, conv: (v) => v };
  const s = SW.num(f.conv(value), f.dec);
  return withUnit && f.unit ? `${s} ${f.unit}` : s;
};

SW.fmtDelta = function (kind, delta) {
  if (delta === null || delta === undefined) return "—";
  const f = SW.KIND_FMT[kind] || { unit: "", dec: 1, conv: (v) => v };
  const dec = kind === "speed_of_sound" ? 2 : f.dec + 1;
  return `${SW.signed(f.conv(delta), dec)} ${f.unit}`.trim();
};

// Calibration offsets. An entity carries `offset` (canonical units) only when one is applied.
SW.OFFSET_NOTE = "Calibration offset applied";
SW.OFFSET_KINDS = ["temperature", "humidity", "pressure"];
// "+0.5 °C", "-1.0 hPa", "+2.0 % points": always signed, one decimal, in the display unit.
SW.fmtCalOffset = function (kind, offset) {
  const f = SW.KIND_FMT[kind];
  if (!f || typeof offset !== "number" || !isFinite(offset)) return "";
  const unit = kind === "humidity" ? "% points" : f.unit;
  return `${SW.signed(f.conv(offset), 1)} ${unit}`.trim();
};
// Sensor roles: an entity carries `role: "equipment"` only for gear readings (never averaged).
// Absent means environment (air at the site).
SW.isEquipment = (e) => !!(e && !e.derived && e.role === "equipment");
SW.hasOffset = (e) => !!(e && !e.derived && typeof e.offset === "number" && e.offset !== 0);
// One note per adjusted sensor: [{ name, kind, text }], by node name. `devices` is a list of
// {id, name}; `entities` a list of entity dicts.
SW.offsetNotes = function (devices, entities) {
  const notes = [];
  for (const d of devices) {
    const adj = entities.filter((e) => e.device_id === d.id && SW.OFFSET_KINDS.indexOf(e.kind) >= 0 && SW.hasOffset(e));
    for (const e of adj) {
      const amount = SW.fmtCalOffset(e.kind, e.offset);
      if (amount) notes.push({ name: d.name, kind: e.kind, text: adj.length > 1 ? `${d.name} ${e.kind} ${amount}` : `${d.name} ${amount}` });
    }
  }
  return notes;
};
// "* Calibration offset applied: Feather S3 -1.0 hPa, MPL3115A2 +0.5 hPa", or "" when none.
SW.offsetFootnote = function (notes) {
  return notes.length ? `* ${SW.OFFSET_NOTE}: ${notes.map((n) => n.text).join(", ")}` : "";
};
// True when any sensor feeding this kind's site average has an offset.
SW.averageAdjusted = (entities, kind) => SW.OFFSET_KINDS.indexOf(kind) >= 0 && entities.some((e) => e.kind === kind && !SW.isEquipment(e) && SW.hasOffset(e));

// Sensor accuracy ("Accuracy ±"), admin only (never on the public dashboards). The server keeps it
// in canonical units (°C, %RH, Pa); the admin shows and takes °C, %RH and hPa.
SW.ACCURACY_KINDS = {
  temperature: { unit: "°C", factor: 1, min: 0.01, max: 20 },
  humidity: { unit: "%RH", factor: 1, min: 0.1, max: 30 },
  pressure: { unit: "hPa", factor: 100, min: 0.01, max: 50 },
};
// canonical -> number in the display unit (or null)
SW.accuracyShown = function (kind, canon) {
  const k = SW.ACCURACY_KINDS[kind];
  if (!k || typeof canon !== "number" || !isFinite(canon) || canon <= 0) return null;
  return Number((canon / k.factor).toPrecision(6));
};
// text typed in the display unit -> canonical number, or null when empty, not a number, below the smallest or above the largest figure
SW.accuracyCanon = function (kind, shown) {
  const k = SW.ACCURACY_KINDS[kind];
  const t = String(shown === null || shown === undefined ? "" : shown).trim().replace(",", ".");
  if (!k || t === "") return null;
  const v = Number(t);
  if (!isFinite(v) || v < k.min || v > k.max) return null;
  return Number((v * k.factor).toPrecision(8));
};
// "Accuracy ±2 %RH (typical)", or "" when there is no figure
SW.fmtAccuracy = function (kind, canon, basis) {
  const v = SW.accuracyShown(kind, canon);
  if (v === null) return "";
  return `Accuracy ±${v} ${SW.ACCURACY_KINDS[kind].unit} (${basis === "maximum" ? "maximum" : "typical"})`;
};
// Preset parts. Figures are in display units (°C, %RH, hPa) and are the typical ones from the
// manufacturers' pages (TMP117's headline is a maximum). A kind listed under `unconfirmed` has no
// confirmed figure: the preset fills nothing and says so. A kind in neither list is not measured.
SW.ACCURACY_PRESETS = [
  { id: "sht45", name: "SHT45", figures: { temperature: 0.1, humidity: 1.0 }, unconfirmed: [] },
  { id: "sht41", name: "SHT41", figures: {}, unconfirmed: ["temperature", "humidity"] },
  { id: "sht40", name: "SHT40", figures: {}, unconfirmed: ["temperature", "humidity"] },
  { id: "tmp117", name: "TMP117", figures: { temperature: 0.1 }, basis: "maximum", unconfirmed: [] },
  { id: "dps310", name: "DPS310", figures: { temperature: 0.5, pressure: 1.0 }, unconfirmed: [] },
  { id: "bme280", name: "BME280", figures: { humidity: 3, pressure: 1.0 }, unconfirmed: ["temperature"] },
  { id: "bmp280", name: "BMP280", figures: { pressure: 1.0 }, unconfirmed: ["temperature"] },
  { id: "ms8607", name: "MS8607", figures: { temperature: 1.0, humidity: 3, pressure: 2.0 }, unconfirmed: [] },
  { id: "mpl3115a2", name: "MPL3115A2", figures: {}, unconfirmed: ["temperature", "pressure"],
    hints: { pressure: "One distributor lists about 4 hPa. That is not confirmed." } },
];
// What choosing `part` does for a sensor of `kind`: { accuracy (display unit or null), basis, hint }.
SW.presetFill = function (part, kind) {
  const label = { temperature: "temperature", humidity: "humidity", pressure: "pressure" }[kind] || kind;
  if (!part || !SW.ACCURACY_KINDS[kind]) return { accuracy: null, basis: "typical", hint: "" };
  const basis = part.basis === "maximum" ? "maximum" : "typical";
  const fig = part.figures[kind];
  if (typeof fig === "number") return { accuracy: fig, basis, hint: `${basis}, from the manufacturer's page` };
  if (part.unconfirmed.indexOf(kind) >= 0) {
    const extra = part.hints && part.hints[kind] ? ` ${part.hints[kind]}` : "";
    return { accuracy: null, basis: null, hint: `No confirmed ${label} figure for the ${part.name}.${extra} Type it from the datasheet.` };
  }
  return { accuracy: null, basis: null, hint: `The ${part.name} does not measure ${label}.` };
};
// A sensor's share of the site average: "96.2 %", "100 %", or "—".
SW.fmtShare = function (share) {
  if (typeof share !== "number" || !isFinite(share) || share < 0) return "—";
  const pct = share * 100;
  return pct >= 99.95 ? "100 %" : `${SW.num(pct, 1)} %`;
};

// "+1.23" / "-1.23" / "0.00" (never "-0.00" from rounding noise)
SW.signed = function (v, dec) {
  const s = SW.num(v, dec);
  if (Number(v.toFixed(dec)) === 0) return (0).toFixed(dec);
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
// "14:05", {seconds: true} "14:05:09", {date: true} "Fri 2 Oct 2026, 14:05".
// Dates are always British (day month year), built here rather than by the browser, so a tablet
// set to a US locale still shows "2 Oct", never "Oct 2".
SW.DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];   // weeks start on Monday
SW.MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// "Fri 2 Oct 2026" from a calendar date (month 1-12).
SW.fmtDate = function (y, mo, d) {
  const dow = (new Date(Date.UTC(y, mo - 1, d)).getUTCDay() + 6) % 7;   // 0 = Monday
  return `${SW.DAYS[dow]} ${d} ${SW.MONTHS[mo - 1]} ${y}`;
};
SW.fmtTime = function (ts, opts) {
  if (ts === null || ts === undefined || !Number.isFinite(Number(ts))) return "—";
  ts = Number(ts);
  const o = opts || {};
  const p = SW._siteParts(ts);
  const pad = (n) => (n < 10 ? "0" : "") + n;
  let s = `${pad(p.h)}:${pad(p.mi)}`;
  if (o.seconds) s += `:${pad(p.s)}`;
  if (!o.date) return s;
  return `${SW.fmtDate(p.y, p.mo, p.d)}, ${s}`;
};

// A show day ("YYYY-MM-DD", already in site time from the server) as "Fri 2 Oct 2026": always
// day-month-year in English, whatever the browser's language. Anything that isn't a date comes
// back unchanged.
// A typed or picked date as "YYYY-MM-DD", or "" if it isn't a real date. Takes the date
// picker's own "2026-10-02" or UK day-first typing: "2/10/2026", "02-10-2026", "2.10.2026"
// (old browsers without a date picker show a plain text box).
SW.parseDay = function (text) {
  const s = String(text || "").trim();
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
  let y, mo, d;
  if (m) { y = +m[1]; mo = +m[2]; d = +m[3]; } else {
    m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(s);
    if (!m) return "";
    d = +m[1]; mo = +m[2]; y = +m[3];
  }
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return "";
  const pad = (n) => (n < 10 ? "0" : "") + n;
  return `${y}-${pad(mo)}-${pad(d)}`;
};

SW.fmtDay = function (day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || "");
  if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return day || "—";
  return SW.fmtDate(Number(m[1]), Number(m[2]), Number(m[3]));
};

SW.age = function (updated, now) {
  if (!updated) return "never";
  const s = Math.max(0, Math.round(now - updated));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  return `${(s / 3600).toFixed(1)}h ago`;
};

// What a node's status pill says. A node set to sleep between readings that is out of contact but
// still within its limit shows "sleeping", with the age of its last reading in the hover text;
// anything else shows the node's own status unchanged.
SW.deviceStatus = function (d, now) {
  if (d && d.sleeping) {
    return { text: "sleeping", cls: "sleeping", title: `Sleeping between readings. Last reading ${SW.age(d.last_reading, now)}.` };
  }
  return { text: d ? d.status : "", cls: d ? d.status : "", title: (d && d.status_detail) || "" };
};

// ---- Schedule: NOW / NEXT --------------------------------------------
// Pure functions, no DOM: the dashboard counts down itself, every second, from the items' planned
// epoch times and the server-corrected clock. These mirror core/schedule.py (stage_matches,
// now_next); tests/js/schedule_test.js checks both give the same answers.
// An item with no stage shows on every dashboard; a dashboard with no stage shows every item.
SW.stageMatches = function (itemStage, dashStage) {
  const a = String(itemStage || "").trim().toLowerCase();
  const b = String(dashStage || "").trim().toLowerCase();
  return !a || !b || a === b;
};
// This stage's items in running order: by planned start, then the order the server sent them in
// (the server's own order breaks ties the same way).
SW.scheduleOrder = function (items, stage) {
  const list = [];
  (items || []).forEach((it, i) => { if (SW.stageMatches(it.stage, stage)) list.push({ it: it, i: i }); });
  list.sort((a, b) => (a.it.planned_start - b.it.planned_start) || (a.i - b.i));
  return list.map((x) => x.it);
};
const _hasEnd = (it) => it.planned_end !== null && it.planned_end !== undefined;
// {state, current, next, curfew, currentEnd, secondsToCurfew} at `now` (epoch s).
// - current: the latest-starting non-curfew item with start <= now < its end. With no end it runs
//   until the next later start, else open-ended. Cut off at the first curfew after its start; an
//   item starting at or after a curfew (Load Out) runs normally. Curfews are never current.
//   currentEnd is that end (null if open-ended).
// - next: the first non-curfew item starting after now.
// - curfew: the next curfew; once all have passed, the last one (secondsToCurfew goes negative).
// - state: empty | before | running | between | over.
SW.scheduleNowNext = function (items, now, stage) {
  const mine = SW.scheduleOrder(items, stage);
  const curfews = mine.filter((i) => i.kind === "curfew");
  const acts = mine.filter((i) => i.kind !== "curfew");
  let upcoming = null;
  for (let k = 0; k < curfews.length; k++) if (curfews[k].planned_start > now) { upcoming = curfews[k]; break; }
  const curfew = upcoming || (curfews.length ? curfews[curfews.length - 1] : null);
  const pastCurfew = !upcoming && curfew !== null;
  let current = null, currentEnd = null;
  for (let idx = 0; idx < acts.length; idx++) {
    const it = acts[idx];
    const start = it.planned_start;
    if (start > now) break;
    // Cut off at the first curfew after its start; an item starting at or after a curfew
    // (typically Load Out) runs normally.
    let cut = Infinity;
    for (let k = 0; k < curfews.length; k++) if (curfews[k].planned_start > start) { cut = curfews[k].planned_start; break; }
    let end = _hasEnd(it) ? it.planned_end : null;
    if (end === null) {
      for (let k = idx + 1; k < acts.length; k++) if (acts[k].planned_start > start) { end = acts[k].planned_start; break; }
      if (end === null) end = Infinity;
    }
    end = Math.min(end, cut);
    if (now < end) { current = it; currentEnd = end; }   // keep looking: a later overlapping start wins
  }
  let next = null;
  for (let k = 0; k < acts.length; k++) if (acts[k].planned_start > now) { next = acts[k]; break; }
  let state;
  if (!mine.length) state = "empty";
  else if (current) state = "running";
  else if (now < mine[0].planned_start) state = "before";
  else if (!next && (pastCurfew || !upcoming)) state = "over";
  else state = "between";
  return { state: state, current: current, next: next, curfew: curfew,
    currentEnd: currentEnd === Infinity ? null : currentEnd,
    secondsToCurfew: curfew ? curfew.planned_start - now : null };
};
// When each item of an ordered list (SW.scheduleOrder) is over, for dimming past items: its end,
// else the next later non-curfew start, else the first curfew after it; null = open-ended.
// A curfew is "over" once it starts.
SW.scheduleEnds = function (ordered) {
  return ordered.map((it, idx) => {
    if (it.kind === "curfew") return it.planned_start;
    if (_hasEnd(it)) return it.planned_end;
    for (let k = idx + 1; k < ordered.length; k++) {
      const o = ordered[k];
      if (o.kind !== "curfew" && o.planned_start > it.planned_start) return o.planned_start;
    }
    for (let k = idx + 1; k < ordered.length; k++) {
      if (ordered[k].kind === "curfew" && ordered[k].planned_start > it.planned_start) return ordered[k].planned_start;
    }
    return null;
  });
};
// Schedule warning steps: minutes before NOW ends / NEXT starts, shared by every dashboard (the
// server sends them as site.schedule_warn = {minutes, flash_minutes}). Default 15 and 5.
// - NOW: the smallest step the item has passed is the current one. The first (largest) step is
//   "warn" (amber), every later step "alert" (orange). flash_minutes lists the steps that pulse. Above the first step, or with no
//   countdown, there is no level. At zero the item is no longer current. Never red. Always shown
//   with a text tag too.
// - NEXT: "warn" (amber) within the smallest step only.
SW.scheduleWarn = { minutes: [15, 5], flash_minutes: [] };
SW.setScheduleWarn = function (w) {
  if (!w || !Array.isArray(w.minutes) || !w.minutes.length || w.minutes.length > 8) return;
  const m = [];
  for (let i = 0; i < w.minutes.length; i++) {
    const n = w.minutes[i];
    if (typeof n !== "number" || !(n >= 1) || n > 240 || Math.floor(n) !== n) return;
    m.push(n);
  }
  m.sort(function (a, b) { return b - a; });
  const f = [];
  if (Array.isArray(w.flash_minutes)) for (let i = 0; i < w.flash_minutes.length; i++) if (m.indexOf(w.flash_minutes[i]) >= 0) f.push(w.flash_minutes[i]);
  SW.scheduleWarn = { minutes: m, flash_minutes: f };
};
// The step (in minutes) NOW is in, or 0 when it is above the first step / has no countdown.
SW.nowStep = function (seconds) {
  if (seconds === null || seconds === undefined || !(seconds > 0)) return 0;
  const m = SW.scheduleWarn.minutes;
  let step = 0;
  for (let i = 0; i < m.length; i++) if (seconds <= m[i] * 60) step = m[i];
  return step;
};
SW.nowLevel = function (seconds) {
  const step = SW.nowStep(seconds);
  if (!step) return "";
  return step === SW.scheduleWarn.minutes[0] ? "warn" : "alert";
};
// True when NOW is in a step that was marked to flash (and so has a level).
SW.nowFlash = function (seconds) {
  const step = SW.nowStep(seconds);
  return step > 0 && SW.scheduleWarn.flash_minutes.indexOf(step) >= 0;
};
// NEXT turns amber (and says so) within the smallest step before the next item starts: "" or "warn".
SW.nextStep = function () {
  const m = SW.scheduleWarn.minutes;
  return m[m.length - 1];
};
SW.nextLevel = function (seconds) {
  if (seconds === null || seconds === undefined) return "";
  return seconds > 0 && seconds <= SW.nextStep() * 60 ? "warn" : "";
};
// The show day ("YYYY-MM-DD") that is current in site time at nowTs: before the site's
// day_rollover you are still on the previous calendar day's show day.
SW.siteShowDay = function (nowTs) {
  const p = SW._siteParts(nowTs);
  const rm = /^(\d{1,2}):(\d{2})/.exec(SW.site.day_rollover || "06:00");
  const rollover = rm ? Number(rm[1]) * 60 + Number(rm[2]) : 360;
  const d = new Date(Date.UTC(p.y, p.mo - 1, p.d));
  if (p.h * 60 + p.mi < rollover) d.setUTCDate(d.getUTCDate() - 1);
  const pad = (n) => (n < 10 ? "0" : "") + n;
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
};
// True when the schedule's day (YYYY-MM-DD) is before today's show day: the next day hasn't been
// started. A missing or odd day is never old.
SW.scheduleIsOld = function (day, nowTs) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day || "")) return false;
  return day < SW.siteShowDay(nowTs);
};
// A length of time for countdowns: "45 s", "4 min 05 s", "45 min", "1 h 05 min". Never "4:05",
// which reads like a time of day. roundUp for time left (a countdown reaches "0 s" at the
// moment, not a second early); otherwise rounded down (time since).
SW.fmtDuration = function (seconds, roundUp) {
  const pad = (n) => (n < 10 ? "0" : "") + n;
  const x = Math.max(0, Number(seconds) || 0);
  const s = roundUp ? Math.ceil(x - 1e-6) : Math.floor(x);
  if (s < 60) return `${s} s`;
  if (s < 600) return `${Math.floor(s / 60)} min ${pad(s % 60)} s`;
  const m = roundUp ? Math.ceil(s / 60) : Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${pad(m % 60)} min`;
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

// Card size (Dashboard.card_sizes, core/cards.py HALF_CAPABLE). A card is half width only when
// the dashboard says "half" for it, the card is allowed to be half, and the layout is not the
// phone (a phone always shows full width). Anything else is full.
SW.HALF_CAPABLE = ["wall_clock", "ontime_timer", "ontime_rundown", "globcon_meters"];
SW.cardIsHalf = function (dash, id, layout) {
  if (layout === "phone" || SW.HALF_CAPABLE.indexOf(id) < 0) return false;
  const sizes = dash && dash.card_sizes;
  return !!sizes && typeof sizes === "object" && Object.prototype.hasOwnProperty.call(sizes, id) && sizes[id] === "half";
};
