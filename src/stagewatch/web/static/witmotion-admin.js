// WitMotion tilt sensors, Admin card: the sensor rows, their checks and the words (pure; no DOM).
// The server checks everything again; these checks only save a round trip and say it in crew words.
// Read-only toward the sensors: nothing here is ever sent to a sensor. Addresses stay on the Admin page.
"use strict";
SW.wmAdmin = (function () {
  const wa = {};
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  wa.MAX = 4;
  wa.SMOOTHING = [0, 1, 2, 5, 10];
  wa.NOTE = "Tilt is a guide, not a safety system.";
  const MAC = /^[0-9A-F]{2}(:[0-9A-F]{2}){5}$/;
  const UUID = /^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$/;
  // AA:BB:CC:DD:EE:FF, or a UUID on hosts that hide the real address. "" when it is neither.
  wa.address = function (text) {
    const t = String(text || "").trim().toUpperCase();
    return MAC.test(t) || UUID.test(t) ? t : "";
  };
  const blank = () => ({ address: "", label: "", axis: "both", smoothing_s: 2, target_x: null, target_y: null, on_target_deg: 0.2, close_deg: 1, accuracy_deg: null, savedIndex: null });
  wa.blankRow = function (address) { return Object.assign(blank(), { address: address || "" }); };
  const fromSensor = (s, i) => Object.assign(blank(), {
    address: s.address || "", label: s.label || "", axis: s.axis || "both", smoothing_s: isNum(s.smoothing_s) ? s.smoothing_s : 2,
    target_x: isNum(s.target_x) ? s.target_x : null, target_y: isNum(s.target_y) ? s.target_y : null,
    on_target_deg: isNum(s.on_target_deg) ? s.on_target_deg : 0.2, close_deg: isNum(s.close_deg) ? s.close_deg : 1,
    accuracy_deg: isNum(s.accuracy_deg) ? s.accuracy_deg : null, savedIndex: i });
  // The rows to show for the saved settings. A settings file that still uses the older single-sensor fields
  // gives one row, so saving here moves it into the list. { rows, legacy } (legacy: that row came from the old fields).
  wa.rowsFromConfig = function (wm) {
    wm = wm || {};
    if ((wm.sensors || []).length) return { rows: wm.sensors.map(fromSensor), legacy: false };
    if (wm.address) return { rows: [fromSensor(wm, 0)], legacy: true };
    return { rows: [], legacy: false };
  };
  // "" when the rows can be saved, otherwise one short sentence for the first problem.
  wa.check = function (rows) {
    if (rows.length > wa.MAX) return `Up to ${wa.MAX} sensors.`;
    const seen = new Set();
    for (const r of rows) {
      const a = wa.address(r.address);
      if (!a) return "A sensor address looks like AA:BB:CC:DD:EE:FF. Check each sensor's address.";
      if (seen.has(a)) return "Two sensors have the same address. Each sensor needs its own.";
      seen.add(a);
      if (isNum(r.target_x) && Math.abs(r.target_x) > 180 || isNum(r.target_y) && Math.abs(r.target_y) > 180) return "A target angle is between -180 and 180 degrees.";
      if (!isNum(r.on_target_deg) || r.on_target_deg < 0 || r.on_target_deg > 10) return "\"On target\" is between 0 and 10 degrees.";
      if (!isNum(r.close_deg) || r.close_deg < 0 || r.close_deg > 45) return "\"Close\" is between 0 and 45 degrees.";
      if (isNum(r.accuracy_deg) && (r.accuracy_deg < 0.01 || r.accuracy_deg > 10)) return "Accuracy is between 0.01 and 10 degrees, or left empty.";
      if (r.close_deg < r.on_target_deg) return "\"Close\" must be at least as wide as \"On target\".";
    }
    return "";
  };
  wa.payload = function (enabled, rows, markers, tempOnEquipment) {
    return { enabled: !!enabled, markers: markers !== false, temp_on_equipment: !!tempOnEquipment, sensors: rows.map((r) => ({
      address: wa.address(r.address), label: String(r.label || "").trim(), axis: r.axis,
      smoothing_s: r.smoothing_s, target_x: r.target_x, target_y: r.target_y, on_target_deg: r.on_target_deg, close_deg: r.close_deg,
      accuracy_deg: isNum(r.accuracy_deg) ? r.accuracy_deg : null })) };
  };
  // One line of live status for a row. `st` is the server's entry for the saved sensor, or null.
  wa.statusText = function (st, saved) {
    if (!saved) return "Not saved yet.";
    if (!st) return "Not running. Save with Enabled ticked to start it.";
    const age = isNum(st.age_s) ? ` · last data ${SW.num(st.age_s, 1)} s ago` : "";
    return st.connected ? `Connected${age}` : `▲ ${st.text || "Not connected"}. No readings until it is back.`;
  };
  // A signal strength in words, with the number for anyone who wants it. A guide only.
  wa.signalText = function (rssi) {
    if (!isNum(rssi)) return "signal unknown";
    return `${rssi >= -60 ? "strong" : rssi >= -75 ? "fair" : "weak"} signal (${Math.round(rssi)} dBm)`;
  };
  return wa;
})();
