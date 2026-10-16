// Device errors, pure functions (no DOM): how long a problem lasted in words, the CSV the Device errors card
// downloads and its file name. Needs SW.csvCell from schedule-export.js. tests/js/device_errors_test.js checks them.
"use strict";

// 0 -> "-", 0.4 -> "under a second", 12 -> "12 s", 65 -> "1 min 05 s", 4000 -> "1 h 06 min".
SW.faultTime = function (seconds) {
  const x = Number(seconds) || 0;
  if (x <= 0) return "-";
  if (x < 1) return "under a second";
  return SW.fmtDuration(x, false);
};

// rows: the server's rows. fmtTime turns a time in seconds into the text shown on screen (the card passes
// the site-time formatter, so the file reads the same as the page).
SW.deviceErrorsCsv = function (rows, fmtTime) {
  const cols = ["Device", "Kind", "Stops", "Alerts", "Advisories", "Drop-outs", "Problems", "Seconds in fault",
    "Never cleared", "Flapping", "Last problem time", "Last problem level", "Last problem"];
  const lines = [cols.join(",")];
  (rows || []).forEach((r) => {
    const last = r.last || {};
    lines.push([r.name, r.kind, r.stops, r.alerts, r.advisories, r.dropouts, r.problems, r.seconds_in_fault,
      r.never_cleared, r.flapping ? "yes" : "", last.ts ? fmtTime(last.ts) : "", last.level || "", last.text || ""]
      .map((v) => SW.csvCell(v)).join(","));
  });
  return "﻿" + lines.join("\r\n") + "\r\n";
};

// "device-errors-<show>-<YYYY-MM-DD>.csv": letters, digits and "-" only.
SW.deviceErrorsFilename = function (showName, day) {
  let n = String(showName || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");
  if (!n) n = "show";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(day || "") ? day : "";
  return `device-errors-${n}${d ? `-${d}` : ""}.csv`;
};
