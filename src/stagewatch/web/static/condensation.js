// Condensation risk: the words for the card (pure; no DOM). The margin is the air temperature
// minus the dew point, both from the site readings (never equipment). Advisory: a guide, not a
// guarantee. A missing reading is "No reading", never a guess.
"use strict";
SW.cond = (function () {
  const c = {};
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  // Margin bands in degrees C, lowest margin first. Keep in step with CONDENSATION_BANDS_C in
  // tests/test_condensation.py (a test checks the numbers).
  c.BANDS = [
    { id: "condensing", below: 1, word: "Condensing", line: "Air at saturation: cold surfaces will be wet" },
    { id: "near", below: 4, word: "Near saturation", line: "Air near saturation: cold kit may mist" },
    { id: "low", below: 8, word: "Low risk", line: "Cold kit may mist if it is much colder than the air" },
    { id: "dry", below: Infinity, word: "Dry", line: "Little risk of condensation" },
  ];
  c.KIT_CLOSE_C = 2;   // kit this close above the dew point may still mist
  c.FOOT = "Guidance, not a guarantee.";
  c.ABOUT = "Dew point is the temperature at which the air gives up water. Kit colder than the dew point, brought in from a cold truck for example, will fog up. The margin is the site air temperature minus the dew point, from the site readings only (equipment sensors are left out). Dry: 8 °C or more. Low risk: 4 to 8 °C. Near saturation: 1 to 4 °C. Condensing: under 1 °C. Surfaces, airflow and warm-up time change what really happens.";

  c.band = function (margin) {
    if (!isNum(margin)) return null;
    for (let i = 0; i < c.BANDS.length; i++) if (margin < c.BANDS[i].below) return c.BANDS[i];
    return c.BANDS[c.BANDS.length - 1];
  };

  // air, dew: site readings in degrees C (null when missing or stale).
  c.view = function (air, dew) {
    if (!isNum(air) || !isNum(dew)) return { state: "none", band: null, air: null, dew: null, margin: null, headline: "No reading", line: "" };
    const margin = air - dew;
    const b = c.band(margin);
    return { state: b.id, band: b, air: air, dew: dew, margin: margin, headline: b.word, line: b.line };
  };

  // Parse what the crew typed ("4", "-3.5", "4,5"); null when it is not a sensible temperature.
  c.parseKit = function (text) {
    const s = String(text === undefined || text === null ? "" : text).trim().replace(",", ".");
    if (!/^-?\d{1,3}(\.\d{0,2})?$/.test(s)) return null;
    const v = Number(s);
    return v >= -60 && v <= 150 ? v : null;
  };

  // Is that kit below the dew point? level: "none", "ok", "close" or "below".
  c.kitView = function (kit, dew) {
    if (!isNum(kit)) return { level: "none", text: "" };
    if (!isNum(dew)) return { level: "none", text: "No dew point to compare with" };
    const gap = kit - dew;
    if (gap < 0) return { level: "below", text: "▲ Below the dew point: this kit will fog up", gap: gap };
    if (gap < c.KIT_CLOSE_C) return { level: "close", text: "Close to the dew point: this kit may mist", gap: gap };
    return { level: "ok", text: "Above the dew point: unlikely to fog up", gap: gap };
  };
  return c;
})();
