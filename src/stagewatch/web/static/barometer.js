// Barometer card: what to say (SW.baro.view, pure) and the old-style dial (SW.baro.createDial).
// The server sends pressures in Pa, a state, a tendency word and an outlook letter (site.baro);
// everything people read is decided here, from fixed tables, so wording can change without a
// server change. An unknown word or letter shows "—", never a guess. Advisory only: the card
// always says it is a guide from pressure at this site, not a forecast.
// The dial is built once with SVG createElementNS (no innerHTML) and then only the needle
// rotation and a few texts are changed in place. No canvas, no requestAnimationFrame, no sound.
"use strict";

SW.baro = (function () {
  const baro = {};

  baro.MIN_HPA = 950;            // the dial scale
  baro.MAX_HPA = 1050;
  baro.START_DEG = -135;         // lower left, measured clockwise from 12 o'clock
  baro.SWEEP_DEG = 270;
  baro.DEG_PER_HPA = 2.7;

  // The traditional words of an old barometer, at the points an inHg dial prints them
  // (28.5, 29.0, 29.5, 30.0, 30.5 inHg at 33.8639 hPa per inHg). Words only: the outlook comes
  // from the Zambretti table below, never from these.
  baro.HPA_PER_INHG = 33.8639;
  baro.DIAL_WORDS = [[28.5, "Stormy"], [29.0, "Rain"], [29.5, "Change"], [30.0, "Fair"], [30.5, "Very dry"]]
    .map((w) => [w[0] * baro.HPA_PER_INHG, w[1]]);

  // Tendency words (Met Office "coast and sea" guide). arrow + words never rely on colour alone;
  // level colours only the two fast falls.
  baro.TENDENCY = {
    steady: ["→", "Steady", ""],
    rising_slowly: ["↗", "Rising slowly", ""],
    rising: ["↗", "Rising", ""],
    rising_quickly: ["▲", "Rising quickly", ""],
    rising_very_rapidly: ["▲", "Rising very rapidly", ""],
    falling_slowly: ["↘", "Falling slowly", ""],
    falling: ["↘", "Falling", ""],
    falling_quickly: ["▼", "Falling quickly", "warn"],
    falling_very_rapidly: ["▼", "Falling very rapidly", "alert"],
  };

  // The 26 Zambretti outcomes: [icon, words, way]. way: "up" improving, "same", "down" worsening.
  // Keep in step with OUTLOOK_LETTERS in core/barometer.py (a test checks the keys).
  baro.OUTLOOK = {
    A: ["sun", "Settled and fine", "same"],
    B: ["sun", "Fine", "same"],
    C: ["sun_cloud", "Turning fine", "up"],
    D: ["sun_cloud", "Fine, turning unsettled", "down"],
    E: ["showers", "Mostly fine, a shower possible", "same"],
    F: ["sun_cloud", "Fairly fine, improving", "up"],
    G: ["showers", "Fairly fine, showers early on", "same"],
    H: ["sun_cloud", "Fairly fine, showers later", "down"],
    I: ["sun_cloud", "Showers early, then improving", "up"],
    J: ["sun_cloud", "Changeable, getting better", "up"],
    K: ["showers", "Showers likely", "same"],
    L: ["cloud", "Unsettled, clearing later", "up"],
    M: ["sun_cloud", "Unsettled, probably improving", "up"],
    N: ["showers", "Showery, bright spells", "same"],
    O: ["sun_cloud", "Showers, turning unsettled", "down"],
    P: ["cloud", "Changeable, some rain", "same"],
    Q: ["cloud", "Unsettled, short fine spells", "same"],
    R: ["cloud", "Unsettled, rain later", "down"],
    S: ["cloud", "Unsettled, some rain", "same"],
    T: ["rain", "Mostly very unsettled", "same"],
    U: ["rain", "Occasional rain, getting worse", "down"],
    V: ["rain", "Rain at times, very unsettled", "same"],
    W: ["rain", "Rain at frequent intervals", "same"],
    X: ["rain", "Rain, very unsettled", "same"],
    Y: ["storm", "Stormy, may improve", "up"],
    Z: ["storm", "Stormy, much rain", "same"],
  };
  baro.WAY_ARROW = { up: "↗", same: "→", down: "↘" };
  baro.WAY_TEXT = { up: "improving", same: "steady", down: "getting worse" };

  baro.DISCLAIMER = "A guide from pressure at this site only. Not a forecast. Check the Met Office forecast and warnings.";
  baro.FOOTER = "Stagewatch is a guide, not a forecast.";
  baro.FALL_LINE = "Weather may turn soon: check wind, lightning and forecasts.";
  baro.TEMP_NOTE = "Sea-level figure uses the measured temperature; it can differ from airport QNH by a hPa or so.";
  baro.NO_SENSOR = "No pressure sensor. Add a BME280 node to see the barometer.";

  // "1,013.2" for Pa; "—" for nothing.
  baro.hpaText = (pa) => (typeof pa === "number" && isFinite(pa) ? SW.num(pa / 100, 1) : "—");
  // "+1.6", "−3.8" (a real minus sign), "0.0": a change in Pa as hPa to one decimal.
  baro.changeText = function (pa) {
    if (typeof pa !== "number" || !isFinite(pa)) return "—";
    const s = SW.signed(pa / 100, 1);
    return s.charAt(0) === "-" ? "−" + s.slice(1) : s;
  };

  // Needle angle in degrees (0 = straight up) for a pressure in hPa, held to the ends of the
  // scale. offScale says the real value is beyond them.
  baro.angle = function (hpa) {
    const clamped = Math.max(baro.MIN_HPA, Math.min(baro.MAX_HPA, hpa));
    return baro.START_DEG + (clamped - baro.MIN_HPA) * baro.DEG_PER_HPA;
  };
  baro.offScale = (hpa) => hpa < baro.MIN_HPA || hpa > baro.MAX_HPA;

  const isNum = (v) => typeof v === "number" && isFinite(v);

  // The one place that decides what the card says. b is site.baro (or undefined before the first
  // tick); now is server-corrected seconds. Returns:
  //   state     "ok" | "collecting" | "gap" | "stale" | "no_sensor" | "waiting"
  //   dim       true when the figures are old (stale)
  //   msl       "1,013.2" or "—";  angle / setAngle  needle degrees or null;  offScale
  //   setLabel  "3 h ago 1,016.4" or ""
  //   tendency  {arrow, word, figure, level} or null
  //   flag      {text, level, line} when pressure is falling quickly, else null
  //   outlook   {icon, text, arrow, wayText, letter} or null
  //   notes     short status sentences (collecting, gap, stale, last hour)
  //   approx    sentence about the sea-level figure, or ""
  //   tempNote  the "uses measured temperature" info line, or ""
  baro.view = function (b, now) {
    const v = { state: "waiting", dim: false, msl: "—", angle: null, setAngle: null, offScale: false, setLabel: "",
      tendency: null, flag: null, outlook: null, notes: [], approx: "", tempNote: "" };
    if (!b || typeof b !== "object") { v.notes.push("Waiting for a pressure reading."); return v; }
    v.state = ["ok", "collecting", "gap", "stale", "no_sensor"].indexOf(b.state) >= 0 ? b.state : "waiting";
    if (v.state === "no_sensor") { v.dim = true; v.notes.push(baro.NO_SENSOR); return v; }
    if (v.state === "waiting") { v.notes.push("Waiting for a pressure reading."); return v; }
    if (isNum(b.msl_pa)) {
      const hpa = b.msl_pa / 100;
      v.msl = baro.hpaText(b.msl_pa);
      v.angle = baro.angle(hpa);
      v.offScale = baro.offScale(hpa);
    }
    if (v.state === "stale") {
      v.dim = true;
      v.notes.push(isNum(b.last_ts) ? `▲ Pressure last seen ${SW.age(b.last_ts, now)}` : "▲ Waiting for a pressure reading.");
      return v;     // no tendency, no outlook, no flag while the reading is old
    }
    if (b.approx === "rough") v.approx = "Sea-level pressure here is rough at this height (above about 2,000 m).";
    else if (b.approx === "approx" && b.reduction === "isa") v.approx = "Sea-level pressure is approximate: there is no temperature reading.";
    else if (b.approx === "approx") v.approx = "Sea-level pressure here is approximate above about 500 m.";
    if (b.reduction === "temperature") v.tempNote = baro.TEMP_NOTE;
    if (v.state === "collecting" || v.state === "gap") {
      if (v.state === "gap" && Array.isArray(b.gap) && b.gap.length === 2) {
        v.notes.push(`Pressure gap ${SW.fmtTime(b.gap[0])} to ${SW.fmtTime(b.gap[1])}${isNum(b.ready_ts) ? `, ready about ${SW.fmtTime(b.ready_ts)}` : ""}`);
      } else {
        v.notes.push(`Collecting${isNum(b.ready_ts) ? `, ready about ${SW.fmtTime(b.ready_ts)}` : ""}`);
      }
      if (isNum(b.last_hour_pa)) v.notes.push(`Last hour: ${baro.changeText(b.last_hour_pa)} hPa`);   // provisional: no word
      return v;
    }
    // state ok
    if (isNum(b.set_pa)) {
      v.setAngle = baro.angle(b.set_pa / 100);
      v.setLabel = `3 h ago ${baro.hpaText(b.set_pa)}`;
    }
    const t = Object.prototype.hasOwnProperty.call(baro.TENDENCY, b.tendency_word) ? baro.TENDENCY[b.tendency_word] : null;
    if (t && isNum(b.tendency_pa_3h)) {
      v.tendency = { arrow: t[0], word: t[1], figure: `${baro.changeText(b.tendency_pa_3h)} hPa in 3 h`, level: t[2] };
    }
    if (b.rapid_fall === true) {
      v.flag = { text: `▼▼ ${b.tendency_word === "falling_very_rapidly" ? "FALLING VERY RAPIDLY" : "FALLING QUICKLY"}`,
        level: b.tendency_word === "falling_very_rapidly" ? "alert" : "warn", line: baro.FALL_LINE };
    }
    const o = typeof b.outlook === "string" && Object.prototype.hasOwnProperty.call(baro.OUTLOOK, b.outlook) ? baro.OUTLOOK[b.outlook] : null;
    if (o) v.outlook = { icon: o[0], text: o[1], arrow: baro.WAY_ARROW[o[2]], wayText: baro.WAY_TEXT[o[2]], letter: b.outlook };
    else if (b.outlook) v.outlook = { icon: "", text: "—", arrow: "", wayText: "", letter: "" };
    return v;
  };

  // ---- drawing -----------------------------------------------------------
  const setAttr = (el, k, val) => { if (el.getAttribute(k) !== String(val)) el.setAttribute(k, val); };
  const setText = (el, text) => { if (el.textContent !== text) el.textContent = text; };
  const polar = (r, deg) => [100 + r * Math.sin(deg * Math.PI / 180), 100 - r * Math.cos(deg * Math.PI / 180)];
  const f1 = (n) => (Math.round(n * 10) / 10).toString();

  // Little weather pictures in a 24 x 24 box, drawn in the text colour (currentColor).
  const CLOUD = "M7 18h10a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.2 9.1 4.5 4.5 0 0 0 7 18z";
  const ICONS = {
    sun: [["circle", { cx: 12, cy: 12, r: 4.5 }], ["path", { d: "M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" }]],
    sun_cloud: [["circle", { cx: 8, cy: 8, r: 3 }], ["path", { d: "M8 2v1.5M2 8h1.5M3.8 3.8l1 1M12.2 3.8l-1 1" }], ["path", { d: "M9 20h8.5a3.5 3.5 0 0 0 .5-6.95A5 5 0 0 0 9 12.4 3.8 3.8 0 0 0 9 20z" }]],
    cloud: [["path", { d: CLOUD }]],
    showers: [["path", { d: "M7 15h10a3.5 3.5 0 0 0 .5-6.95A5 5 0 0 0 7.2 7.2 3.9 3.9 0 0 0 7 15z" }], ["path", { d: "M9 18l-1 2.5M13 18l-1 2.5M17 18l-1 2.5" }]],
    rain: [["path", { d: "M7 14h10a3.5 3.5 0 0 0 .5-6.95A5 5 0 0 0 7.2 6.2 3.9 3.9 0 0 0 7 14z" }], ["path", { d: "M8 16.5l-1.5 5M12 16.5l-1.5 5M16 16.5l-1.5 5" }]],
    storm: [["path", { d: "M7 14h10a3.5 3.5 0 0 0 .5-6.95A5 5 0 0 0 7.2 6.2 3.9 3.9 0 0 0 7 14z" }], ["path", { d: "M12.5 14.5l-3 4h3l-1.5 4.5 4.5-6h-3l1.5-2.5z" }]],
  };
  baro.icon = function (name) {
    const svg = SW.svg("svg", { viewBox: "0 0 24 24", width: 24, height: 24, class: "baro-icon", "aria-hidden": "true", focusable: "false",
      fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round", "stroke-linejoin": "round" });
    for (const part of ICONS[name] || []) svg.append(SW.svg(part[0], part[1]));
    return svg;
  };

  // The dial: a 200 x 200 face. Static parts (ticks, numbers, words, hub) are drawn once; update()
  // moves the needle and the "set hand" and changes two texts, only when they changed.
  baro.createDial = function () {
    const svg = SW.svg("svg", { viewBox: "0 0 200 200", class: "baro-dial", role: "img", "aria-label": "Barometer dial" });
    svg.append(SW.svg("circle", { cx: 100, cy: 100, r: 96, class: "baro-bezel" }));
    svg.append(SW.svg("circle", { cx: 100, cy: 100, r: 90, class: "baro-face" }));
    for (let p = baro.MIN_HPA; p <= baro.MAX_HPA; p += 5) {
      const major = p % 10 === 0, a = baro.angle(p);
      const o = polar(88, a), i = polar(major ? 80 : 84, a);
      svg.append(SW.svg("line", { x1: f1(o[0]), y1: f1(o[1]), x2: f1(i[0]), y2: f1(i[1]), class: major ? "baro-tick major" : "baro-tick" }));
      if (major) {
        const t = polar(71, a);
        const label = SW.svg("text", { x: f1(t[0]), y: f1(t[1] + 2.4), class: "baro-num", "text-anchor": "middle" });
        label.textContent = SW.num(p, 0);
        svg.append(label);
      }
    }
    for (const w of baro.DIAL_WORDS) {          // each word at its own point, no coloured bands
      const a = baro.angle(w[0]), m = polar(80, a), t = polar(52, a);
      svg.append(SW.svg("circle", { cx: f1(m[0]), cy: f1(m[1]), r: 1.6, class: "baro-point" }));
      const label = SW.svg("text", { x: f1(t[0]), y: f1(t[1] + 2.4), class: "baro-word", "text-anchor": "middle" });
      label.textContent = w[1];
      svg.append(label);
    }
    const setHand = SW.svg("g", { class: "baro-sethand", transform: "rotate(-135 100 100)" });
    setHand.append(SW.svg("path", { d: "M100 17 L96 28 L104 28 Z", class: "baro-setmark" }));   // a small marker on the rim
    setHand.append(SW.svg("line", { x1: 100, y1: 28, x2: 100, y2: 56, class: "baro-setline" }));
    svg.append(setHand);
    const setLabel = SW.svg("text", { x: 100, y: 152, class: "baro-setlabel", "text-anchor": "middle" });
    svg.append(setLabel);
    const hand = SW.svg("g", { class: "baro-needle", transform: "rotate(-135 100 100)" });
    hand.append(SW.svg("path", { d: "M100 22 L95 100 L105 100 Z", class: "baro-hand" }));
    hand.append(SW.svg("line", { x1: 100, y1: 100, x2: 100, y2: 118, class: "baro-tail" }));
    svg.append(hand);
    svg.append(SW.svg("circle", { cx: 100, cy: 100, r: 6, class: "baro-hub" }));
    let last = {};
    const rotate = (el, key, deg) => {
      if (last[key] === deg) return;
      last[key] = deg;
      el.setAttribute("transform", `rotate(${f1(deg)} 100 100)`);   // old browsers
      if (el.style) el.style.transform = `rotate(${f1(deg)}deg)`;   // eased by CSS
    };
    return {
      el: svg,
      update(v) {
        setAttr(svg, "class", `baro-dial${v.dim ? " dim" : ""}`);
        if (v.angle !== null) rotate(hand, "hand", v.angle);
        setAttr(hand, "visibility", v.angle === null ? "hidden" : "visible");
        setAttr(setHand, "visibility", v.setAngle === null ? "hidden" : "visible");
        if (v.setAngle !== null) rotate(setHand, "set", v.setAngle);
        setText(setLabel, v.setLabel);
        const label = v.angle === null ? "Barometer dial, no reading" : `Barometer dial, ${v.msl} hPa${v.offScale ? ", off the scale" : ""}`;
        setAttr(svg, "aria-label", label);
      },
    };
  };

  return baro;
})();
