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
    { id: "condensing", below: 1, word: "Condensing", line: "Air at saturation. Kit more than {margin} °C colder than the air will mist" },
    { id: "near", below: 4, word: "Near saturation", line: "Kit more than {margin} °C colder than the air will mist" },
    { id: "moderate", below: 10, word: "Moderate", line: "Kit more than {margin} °C colder than the air will mist" },
    { id: "low", below: Infinity, word: "Low risk", line: "Low risk for kit at room temperature. Kit more than {margin} °C colder than the air will mist" },
  ];
  c.KIT_CLOSE_C = 3;   // kit this close above the dew point may still mist (ISO 8502-4)
  c.FOOT = "Guidance, not a guarantee.";
  c.ABOUT = "Dew point is the temperature at which the air gives up water. Kit colder than the dew point, brought in from a cold truck for example, will fog up. The margin is the site air temperature minus the dew point, from the site readings only (equipment sensors are left out). Condensing: under 1 °C. Near saturation: 1 to 4 °C. Moderate: 4 to 10 °C. Low risk: 10 °C or more, for kit at room temperature. Below 0 °C the card compares kit with the frost point when that is higher than the dew point. Kit within 3 °C of the dew point may still mist. An infrared thermometer reads low on shiny metal, so the kit may be warmer or colder than it shows. Surfaces, airflow and warm-up time change what really happens.";

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
    return { state: b.id, band: b, air: air, dew: dew, margin: margin, headline: b.word, line: b.line.replace("{margin}", c.num1(Math.max(margin, 0))) };
  };

  c.num1 = (x) => (Math.round(x * 10) / 10).toFixed(1);

  // Frost point from the dew point: Magnus over ice (b 22.46, c 272.62; WMO No. 8 Annex 4.B), using
  // the same vapour pressure as the dew point (Magnus over water, b 17.625, c 243.04).
  c.frostFromDew = function (dew) {
    const g = 17.625 * dew / (243.04 + dew);
    return 272.62 * g / (22.46 - g);
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
    // Under 0 C the frost point is the higher of the two, so kit is compared with that.
    const frost = dew < 0 ? c.frostFromDew(dew) : null;
    const ref = frost !== null && frost > dew ? frost : dew;
    const gap = kit - ref;
    if (gap < 0) return { level: "below", text: frost !== null ? "▲ Below the frost point: this kit will frost or fog up" : "▲ Below the dew point: this kit will fog up", gap: gap };
    if (gap < c.KIT_CLOSE_C) return { level: "close", text: "Close to the dew point: this kit may mist", gap: gap };
    return { level: "ok", text: "Above the dew point: unlikely to fog up", gap: gap };
  };
  return c;
})();
