// Schedule export, pure functions (no DOM): the CSV the Schedule page downloads and its file name.
// tests/js/schedule_export_test.js checks them, and tests/test_schedule_export.py checks the file
// reads back through core/schedule.py parse_import.
"use strict";

// One CSV cell (RFC 4180): wrapped in quotes when it holds a comma, quote, CR or LF, with quotes
// doubled. A cell that starts with = + - @, a tab or a CR is prefixed with ' first, so a
// spreadsheet never runs it as a formula (see the "CSV export" note in core/schedule.py).
SW.csvCell = function (value) {
  let s = value === null || value === undefined ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

// rows: [{start, end, kind, stage, title}] -> the whole file as text: a byte-order mark (so Excel
// reads it as UTF-8), a header row, one row per item, CRLF after every line. The kind is the
// stored value (act, load_in, ...), so the file imports again as it is. Setlists are not included.
SW.scheduleCsv = function (rows) {
  const cols = ["start", "end", "kind", "stage", "title"];
  const lines = [cols.join(",")];
  (rows || []).forEach((r) => {
    lines.push(cols.map((c) => SW.csvCell(String(r[c] === null || r[c] === undefined ? "" : r[c]).trim())).join(","));
  });
  return "﻿" + lines.join("\r\n") + "\r\n";
};

// "schedule-<event>-<YYYY-MM-DD>.csv", with letters, digits and "-" only (anything else in the
// event name becomes "-"; no event name gives "show").
SW.scheduleFilename = function (eventName, day) {
  let ev = String(eventName || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");
  if (!ev) ev = "show";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(day || "") ? day : "";
  return `schedule-${ev}${d ? `-${d}` : ""}.csv`;
};
