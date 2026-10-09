// Wall Clock card: what to show (SW.wc.view, pure) and the three looks (digits, ring, segments).
// Every look is drawn from the same view object, so stale / offline / differs mean the same
// thing in all of them and never depend on colour alone: each also has words with a ▲, and a
// dashed outline (differs) or a strike-through (stale). Faces are built once with SW.h / SVG
// createElementNS and then updated in place: only what changed is touched, once a second.
// Nothing here sounds, flashes or raises an alarm. No canvas, no requestAnimationFrame.
"use strict";

SW.wc = (function () {
  const wc = {};
  const SVGNS = "http://www.w3.org/2000/svg";

  wc.STALE_S = 3;                       // a reading older than this is stale
  wc.STYLES = ["digits", "ring", "segments"];
  wc.RING_MIN_WIDTH = 280;              // narrower than this (px) the ring is replaced by plain digits

  // An unknown style (a newer release) is plain digits.
  wc.style = (s) => (wc.STYLES.indexOf(s) >= 0 ? s : "digits");

  // Digit -> lit segments (a top, b top right, c bottom right, d bottom, e bottom left, f top left, g middle).
  wc.SEGMENTS = { "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg", "5": "acdfg",
    "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg" };

  const pad2 = (n) => (n < 10 ? "0" : "") + n;

  // Whole seconds since midnight (0-86399) from milliseconds, wrapped into one day.
  wc.daySeconds = (ms) => Math.floor((((ms % 86400000) + 86400000) % 86400000) / 1000);

  // {hh, mm, ss, suffix} for a time of day in seconds. 24-hour: "00".."23". 12-hour: no leading
  // zero, 00:xx is 12 am, 12:xx is 12 pm. Never "24:00".
  wc.timeParts = function (sec, hour12) {
    const h = Math.floor(sec / 3600) % 24, m = Math.floor(sec / 60) % 60, s = sec % 60;
    if (!hour12) return { hh: pad2(h), mm: pad2(m), ss: pad2(s), suffix: "" };
    return { hh: String(h % 12 === 0 ? 12 : h % 12), mm: pad2(m), ss: pad2(s), suffix: h < 12 ? "am" : "pm" };
  };

  // Options with defaults; anything unexpected falls back to the default.
  wc.options = function (d) {
    d = d && typeof d === "object" ? d : {};
    return { hour12: d.hour12 === true, showDate: d.show_date === true, colonBlink: d.colon_blink === true };
  };

  wc.wholeHours = (off) => Math.abs(off) >= 1800 && Math.abs(Math.abs(off) - Math.round(Math.abs(off) / 3600) * 3600) < 5;
  // "+3.2 s", "+2 min 5 s", or (whole hours) "+1 h".
  wc.offsetText = function (off) {
    const a = Math.abs(off);
    if (a < 60) return `${SW.signed(off, 1)} s`;
    const sign = off < 0 ? "-" : "+";
    if (wc.wholeHours(off)) return `${sign}${Math.round(a / 3600)} h`;
    return `${sign}${Math.floor(a / 60)} min ${Math.round(a % 60)} s`;
  };

  // The one place that decides what the card says. m is the "wall_clock" message
  // ({source, label, clock_ms, received_at, status, offset_s, warn, display}); now is the
  // server-corrected time in seconds. Returns:
  //   state   "live" | "differs" | "stale" | "off"
  //   sec     time of day in whole seconds (running when live, frozen when stale), null when off
  //   digits  "HH:MM:SS" / "H:MM:SS" ("--:--:--" when off); suffix "am" / "pm" (12 h) or ""
  //   note    the sentence under the clock; level "warn" or ""; cls the card's state class
  //   date    "Fri 2 Oct 2026" when the option is on and the time can be trusted, else ""
  wc.view = function (m, now) {
    const o = wc.options(m && m.display);
    const name = (m && m.label) || "Ontime";
    const live = !!m && m.status === "ok" && typeof m.clock_ms === "number";
    const age = live ? Math.max(0, now - m.received_at) : 0;
    const v = { state: "off", sec: null, digits: "--:--:--", suffix: "", note: "", level: "warn", cls: "wc-off",
      date: "", name, opts: o, parts: null };
    if (!live) {
      v.note = m && m.status === "error" ? `▲ ${name} sent a time we can't read` : `▲ ${name} offline: no time to show`;
      return v;
    }
    const stale = age > wc.STALE_S;
    v.sec = wc.daySeconds(stale ? m.clock_ms : m.clock_ms + age * 1000);   // frozen at the last reading when stale
    v.parts = wc.timeParts(v.sec, o.hour12);
    v.digits = `${v.parts.hh}:${v.parts.mm}:${v.parts.ss}`;
    v.suffix = v.parts.suffix;
    if (stale) {
      // Rounded in steps so the live region isn't re-announced every second.
      const ago = age < 10 ? "a few seconds" : age < 60 ? `${Math.round(age / 10) * 10} s` : `${Math.round(age / 60)} min`;
      v.state = "stale"; v.cls = "wc-stale";
      v.note = `▲ Stale: nothing from ${name} for ${ago}. Don't trust this time.`;
      return v;
    }
    if (m.warn && typeof m.offset_s === "number") {
      const ahead = m.offset_s < 0 ? "behind" : "ahead";
      v.state = "differs"; v.cls = "wc-differs";
      v.note = `▲ Differs from Stagewatch by ${wc.offsetText(m.offset_s)} (${name} is ${ahead})${wc.wholeHours(m.offset_s) ? ". Check the time zones." : ""}`;
    } else {
      v.state = "live"; v.cls = ""; v.level = "";
      v.note = m.source === "pc" ? "Stagewatch's own clock" : "";   // Ontime agreeing with Stagewatch is the normal case: say nothing
    }
    if (o.showDate) {
      const p = SW._siteParts(now + (typeof m.offset_s === "number" ? m.offset_s : 0));
      v.date = SW.fmtDate(p.y, p.mo, p.d);
    }
    return v;
  };

  // Milliseconds until the next tick, so the display changes just after the shown second
  // changes (not at an arbitrary moment in it). Clamped so a bad value can't stall or spin.
  wc.nextDelayMs = function (m, now) {
    let shown = now * 1000;
    if (m && m.status === "ok" && typeof m.clock_ms === "number" && now - m.received_at <= wc.STALE_S) {
      shown = m.clock_ms + (now - m.received_at) * 1000;
    }
    const frac = ((shown % 1000) + 1000) % 1000;
    return Math.min(1100, Math.max(30, Math.round(1000 - frac + 10)));
  };

  wc.reducedMotion = function () {
    try { return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (_) { return false; }
  };

  // ---------------------------------------------------------------- drawing
  function S(tag, attrs, ...children) {
    const el = document.createElementNS(SVGNS, tag);
    for (const k of Object.keys(attrs || {})) el.setAttribute(k, attrs[k]);
    for (const c of children) if (c) el.append(c);
    return el;
  }
  const setAttr = (el, k, v) => { if (el.getAttribute(k) !== String(v)) el.setAttribute(k, v); };
  const setText = (el, s) => { if (el.textContent !== s) el.textContent = s; };
  // Face-wide state classes: wc-live / wc-differs / wc-stale / wc-off, plus the style.
  function faceClass(face, style, v) {
    setAttr(face, "class", `wc-face wc-${style} wc-s-${v.state}`);
  }

  // digits: the plain time, as before.
  function digitsFace() {
    const el = SW.h("div", { class: "wc-time", "data-live": "" });
    return {
      style: "digits", el,
      update(v) {
        setText(el, v.state === "off" ? v.digits : v.digits + (v.suffix ? ` ${v.suffix}` : ""));
      },
    };
  }

  // ring: an OUTER ring of 60 seconds LEDs and an INNER ring of 12 hour marks (always lit,
  // brighter), the time as SVG text in the middle (viewBox 220 x 220). The seconds fill up:
  // second s lights LEDs 0..s-1, so every earlier second of the minute stays lit and the whole
  // ring is dark at :00, which makes the minute rollover unmistakable.
  const RING_R = 102, MARK_R = 91.3;  // hour marks: same size as a seconds LED, one seconds-spacing (~10.7) inside it
  function ringFace() {
    const leds = [];
    const svg = S("svg", { viewBox: "0 0 220 220", width: "220", height: "220", role: "img", class: "wc-svg" });
    const at = (r, i, n) => { const a = (i * 2 * Math.PI) / n; return { cx: (110 + r * Math.sin(a)).toFixed(2), cy: (110 - r * Math.cos(a)).toFixed(2) }; };
    for (let i = 0; i < 60; i++) {
      const c = S("circle", Object.assign(at(RING_R, i, 60), { r: "2.6", class: "led" }));
      leds.push(c);
      svg.append(c);
    }
    for (let i = 0; i < 12; i++) svg.append(S("circle", Object.assign(at(MARK_R, i, 12), { r: "2.6", class: "led mark" })));    const parts = { hh: S("tspan"), c1: S("tspan", { class: "colon" }), mm: S("tspan"), c2: S("tspan", { class: "colon" }), ss: S("tspan") };
    setText(parts.c1, ":"); setText(parts.c2, ":");
    const text = S("text", { x: "110", y: "120", "text-anchor": "middle", class: "wc-digits" },
      parts.hh, parts.c1, parts.mm, parts.c2, parts.ss);
    const suffix = S("text", { x: "110", y: "146", "text-anchor": "middle", class: "wc-suffix" });
    const strike = S("line", { x1: "40", y1: "110", x2: "180", y2: "110", class: "wc-strike" });
    svg.append(text, suffix, strike);
    const el = SW.h("div", {}, svg);
    const seen = new Array(60).fill("");
    let label = "";
    return {
      style: "ring", el,
      update(v, opts) {
        faceClass(el, "ring", v);
        // Seconds LEDs lit: 0..s-1, frozen (and dimmed by CSS) when stale, none when offline. The hour marks stay lit.
        const want = new Array(60).fill("");
        if (v.sec !== null) {
          const s = v.sec % 60;
          for (let i = 0; i < s; i++) want[i] = "on";
        }
        for (let i = 0; i < 60; i++) {
          if (seen[i] !== want[i]) { seen[i] = want[i]; setAttr(leds[i], "class", "led" + (want[i] ? " on" : "")); }
        }
        const t = v.state === "off" ? { hh: "--", mm: "--", ss: "--" } : v.parts;
        setText(parts.hh, t.hh); setText(parts.mm, t.mm); setText(parts.ss, t.ss);
        setText(suffix, v.state === "off" ? "" : v.suffix);
        const dim = !!opts.colonBlink && !opts.reduced && v.sec !== null && v.state !== "stale" && v.sec % 2 === 1;
        setAttr(parts.c1, "class", dim ? "colon blink-off" : "colon");
        setAttr(parts.c2, "class", dim ? "colon blink-off" : "colon");
        const next = `${v.digits}${v.suffix ? ` ${v.suffix}` : ""}`;
        if (next !== label) { label = next; svg.setAttribute("aria-label", `Wall Clock ${next}`); }
      },
    };
  }

  // segments: six 7-segment digits and two colons, 304 x 66 units. All seven segments of every
  // digit are always drawn; unlit ones are faint "ghosts".
  const SEG_GEOM = (function () {
    // Horizontal segment from x0 to x1 centred on y; vertical from y0 to y1 centred on x. Half thickness 3.
    const hz = (x0, x1, y) => [[x0, y], [x0 + 3, y - 3], [x1 - 3, y - 3], [x1, y], [x1 - 3, y + 3], [x0 + 3, y + 3]];
    const vt = (y0, y1, x) => [[x, y0], [x + 3, y0 + 3], [x + 3, y1 - 3], [x, y1], [x - 3, y1 - 3], [x - 3, y0 + 3]];
    return { a: hz(4, 32, 3), g: hz(4, 32, 33), d: hz(4, 32, 63), f: vt(4, 32, 3), b: vt(4, 32, 33), e: vt(34, 62, 3), c: vt(34, 62, 33) };
  })();
  const SEG_X = [0, 44, 108, 152, 224, 268];     // left edge of each digit
  const SEG_COLONS = [88, 204];
  function segmentsFace() {
    const svg = S("svg", { viewBox: "-4 -4 312 74", width: "312", height: "74", role: "img", class: "wc-svg" });
    const digits = SEG_X.map((x) => {
      const g = S("g", { transform: `translate(${x} 0)` });
      const segs = {};
      for (const k of Object.keys(SEG_GEOM)) {
        segs[k] = S("polygon", { points: SEG_GEOM[k].map((p) => p.join(",")).join(" "), class: "seg" });
        g.append(segs[k]);
      }
      svg.append(g);
      return { segs, ch: null };
    });
    const colons = SEG_COLONS.map((x) => {
      const g = S("g", { class: "colon" }, S("rect", { x: x, y: 18, width: 6, height: 6, class: "dot" }), S("rect", { x: x, y: 42, width: 6, height: 6, class: "dot" }));
      svg.append(g);
      return g;
    });
    const strike = S("line", { x1: "-4", y1: "33", x2: "308", y2: "33", class: "wc-strike" });
    svg.append(strike);
    const suffix = SW.h("div", { class: "wc-suffix" });
    const el = SW.h("div", {}, svg, suffix);
    let label = "";
    return {
      style: "segments", el,
      update(v, opts) {
        faceClass(el, "segments", v);
        // Six characters: a 12-hour clock's missing leading zero is a blank first digit.
        const chars = v.state === "off" ? [" ", " ", " ", " ", " ", " "] : (v.parts.hh.length === 1 ? " " + v.parts.hh : v.parts.hh).split("")
          .concat(v.parts.mm.split(""), v.parts.ss.split(""));
        for (let i = 0; i < 6; i++) {
          const d = digits[i];
          if (d.ch === chars[i]) continue;      // usually only the last digit changes
          d.ch = chars[i];
          const lit = wc.SEGMENTS[chars[i]] || "";
          for (const k of Object.keys(d.segs)) setAttr(d.segs[k], "class", lit.indexOf(k) >= 0 ? "seg on" : "seg");
        }
        const dim = !!opts.colonBlink && !opts.reduced && v.sec !== null && v.state !== "stale" && v.sec % 2 === 1;
        const cc = v.state === "off" ? "colon" : dim ? "colon on blink-off" : "colon on";
        for (const c of colons) setAttr(c, "class", cc);
        setText(suffix, v.state === "off" ? "" : v.suffix);
        const next = `${v.digits}${v.suffix ? ` ${v.suffix}` : ""}`;
        if (next !== label) { label = next; svg.setAttribute("aria-label", `Wall Clock ${next}`); }
      },
    };
  }

  // A face for a style. The ring needs room: below RING_MIN_WIDTH the caller asks for digits.
  wc.createFace = function (style) {
    return style === "ring" ? ringFace() : style === "segments" ? segmentsFace() : digitsFace();
  };

  return wc;
})();
