// This Raspberry Pi: words and rows for the Admin card and the dashboard card (pure; no DOM).
// Every flag is said in words; an unknown throttle state is "Unknown", never "OK".
"use strict";
SW.pi = (function () {
  const pi = {};
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);
  pi.TEMP_WARN_C = 80;
  // "Under-voltage now" / "Under-voltage since boot" / "OK" / "Unknown" for the power supply line.
  pi.powerText = function (th) {
    if (!th) return { text: "Unknown (the Pi did not say)", level: "unknown" };
    if (th.under_voltage) return { text: "▲ Under-voltage now: check the power supply and cable", level: "warn" };
    if (th.under_voltage_seen) return { text: "OK now (was short of power since it started)", level: "note" };
    return { text: "OK", level: "ok" };
  };
  pi.speedText = function (th) {
    if (!th) return { text: "Unknown (the Pi did not say)", level: "unknown" };
    if (th.throttled || th.freq_capped) return { text: "▲ Slowed down now (throttled)", level: "warn" };
    if (th.soft_temp_limit) return { text: "▲ At its temperature limit", level: "warn" };
    if (th.throttled_seen || th.freq_capped_seen || th.soft_temp_limit_seen) return { text: "Full speed now (slowed down earlier since it started)", level: "note" };
    return { text: "Full speed", level: "ok" };
  };
  // Rows [{ label, value, unit, level }] from the readings (entities by suffix) and the device's public "pi" block.
  pi.rows = function (ents, pub) {
    const v = (k) => (ents[k] && !ents[k].stale && isNum(ents[k].value) ? ents[k].value : null);
    const out = [];
    const t = v("cpu_temp");
    out.push({ key: "temp", label: "CPU temperature", value: t === null ? "—" : SW.num(t, 1), unit: t === null ? "" : "°C", level: t !== null && t >= pi.TEMP_WARN_C ? "warn" : "ok" });
    const th = pub ? pub.throttle : null;
    const pw = pi.powerText(th), sp = pi.speedText(th);
    out.push({ key: "power", label: "Power supply", value: pw.text, unit: "", level: pw.level });
    out.push({ key: "speed", label: "Speed", value: sp.text, unit: "", level: sp.level });
    const l = v("cpu_load");
    out.push({ key: "load", label: "CPU load (1 min)", value: l === null ? "—" : SW.num(l, 2), unit: "", level: "ok" });
    const m = v("memory_used");
    out.push({ key: "mem", label: "Memory used", value: m === null ? "—" : SW.num(m, 0), unit: m === null ? "" : "%", level: "ok" });
    const d = v("disk_free");
    const share = pub && isNum(pub.disk_share_free) ? pub.disk_share_free : null;
    out.push({ key: "disk", label: "Disk free", value: d === null ? "—" : SW.num(d, 1), unit: d === null ? "" : `GB${share === null ? "" : ` (${Math.round(share * 100)}%)`}`,
      level: d !== null && (d < 1 || (share !== null && share < 0.05)) ? "warn" : "ok" });
    const w = v("wifi_signal");
    out.push({ key: "wifi", label: "Wi-Fi signal", value: w === null ? "—" : SW.num(w, 0), unit: w === null ? "(not on Wi-Fi)" : "dBm", level: "ok" });
    const up = pub && isNum(pub.uptime_s) ? pub.uptime_s : null;
    out.push({ key: "uptime", label: "Running for", value: up === null ? "—" : SW.fmtDuration(up, false), unit: "", level: "ok" });
    return out;
  };
  // The Pi device's readings by suffix ("cpu_temp" ...), from the snapshot's entities.
  pi.entitiesOf = function (entities, deviceId) {
    const out = {};
    for (const e of Object.values(entities || {})) if (e.device_id === deviceId) out[e.id.slice(deviceId.length + 1)] = e;
    return out;
  };
  return pi;
})();
