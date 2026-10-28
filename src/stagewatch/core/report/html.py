"""The report as one printable HTML page: no script, no external requests, every value escaped.

The page carries its own style and inline SVG charts, so it opens offline and prints to A4
(use the browser's Save as PDF).  Level colours are always paired with words.
"""

from __future__ import annotations

import html as _html

from . import fmt, svg
from .build import ALARM_ROW_CAP, ENV_ADVICE, SECTION_NAMES, ReportData
from .query import ReportError  # noqa: F401  (re-export for callers)


class Raw(str):
    """Markup that is already safe (built here from escaped parts)."""


def e(v: object) -> str:
    return _html.escape("" if v is None else str(v), quote=True)


def _cell(v: object) -> str:
    return str(v) if isinstance(v, Raw) else e(v)


CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
ROW_CAP = 200   # rows shown in one table; the rest are in the CSV


def table(headers: list[str], rows: list[list], cls: str = "", cap: int | None = None) -> str:
    more = 0
    if cap is not None and len(rows) > cap:
        more, rows = len(rows) - cap, rows[:cap]
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_cell(c)}</td>" for c in r) + "</tr>" for r in rows)
    note = f'<p class="mut">... {more} more, in the CSV</p>' if more else ""
    return (f'<div class="scroll"><table class="{e(cls)}"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>{note}")


def _section(key: str, body: str) -> str:
    return f'<section id="s-{e(key)}"><h2>{e(SECTION_NAMES.get(key, key))}</h2>{body}</section>'


def _failed(sec: dict) -> bool:
    return isinstance(sec, dict) and "error" in sec


def _failed_text(sec: dict) -> str:
    return f'<p class="warn">This section could not be made (code: {e(sec["error"])}). The rest of the report is unaffected.</p>'


CSS = """
:root{--fg:#111;--bg:#fff;--mut:#444;--line:#bbb;--head:#eee;--warn:#7a3b00}
*{box-sizing:border-box}
body{font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:var(--fg);background:var(--bg);margin:0;padding:16px}
main{max-width:210mm;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:22px 0 6px;border-bottom:2px solid var(--fg);padding-bottom:2px}
h3{font-size:14px;margin:12px 0 4px}
p{margin:4px 0}.mut{color:var(--mut)}.warn{color:var(--warn);font-weight:600}
.badge{display:inline-block;border:2px solid var(--fg);border-radius:4px;padding:1px 8px;font-weight:700;margin-right:6px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;margin:6px 0 10px;font-size:12.5px}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:left;vertical-align:top}
th{background:var(--head)}td.n{text-align:right}
.chart{width:100%;height:auto;margin:6px 0;break-inside:avoid}
.hint{background:#f4f4f4;border:1px solid var(--line);padding:6px 8px;margin:10px 0}
.lvl{font-weight:700;text-transform:uppercase;font-size:11px;border:1px solid var(--fg);padding:0 4px;border-radius:3px}
.lvl-advisory{background:#fbe7b5}.lvl-alert{background:#f6c3a3}.lvl-stop{background:#7a0019;color:#fff}.lvl-notice{background:#eee}
footer{margin-top:24px;border-top:1px solid var(--line);padding-top:8px;font-size:12px;color:var(--mut)}
@media print{
@page{size:A4;margin:12mm}
body{padding:0;font-size:11pt}.hint{display:none}
tr,svg,h2,h3{break-inside:avoid}h2,h3{break-after:avoid}
thead{display:table-header-group}
.scroll{overflow:visible}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
"""


def render_html(data: ReportData) -> str:
    h, site, sec = data.header, data.site, data.sections
    dst = bool(h.get("dst"))
    parts: list[str] = []
    title = f'Show report - {h["site_name"]} - {h["date_text"]}'
    head = [f'<h1>{e(h["site_name"])}: show report</h1>',
            f'<p><span class="badge">{e(h["badge"])}</span><strong>{e(h["completeness"])}</strong></p>']
    ev = f'{h["event_name"]}, ' if h.get("event_name") else ""
    head.append(f'<p>{e(ev)}{e(h["show_name"])}, {e(h["date_text"])}</p>')
    head.append(f'<p>Report for {e(h["range_text"])} ({e(h["range_label"])}), time zone {e(h["tz"])} ({e(h["utc"])}).</p>')
    head.append(f'<p class="mut">Made at {e(fmt.stamp(h["made_at"], site, dst))} by Stagewatch {e(h["version"])} '
                f'from: {e(h["source"])}.</p>')
    if h.get("omitted"):
        head.append(f'<p class="mut">This report leaves out: {e(", ".join(h["omitted"]).lower())}.</p>')
    for n in h.get("notes", []):
        head.append(f'<p class="warn">{e(n)}</p>')
    head.append('<p class="hint">To save as PDF: press Ctrl+P (Cmd+P on Mac), then choose Save as PDF.</p>')
    parts.append("<header>" + "".join(head) + "</header>")
    if "summary" in sec:
        parts.append(_section("summary", _summary(sec["summary"], data)))
    parts.append(_section("gaps", _gaps(sec.get("gaps", {}), data)))
    if "env" in sec:
        parts.append(_section("env", _env(sec["env"], data)))
    for key, fn in (("wind", _wind), ("power", _power), ("spl", _spl)):
        if key in sec and sec[key] is not None:
            parts.append(_section(key, _failed_text(sec[key]) if _failed(sec[key]) else fn(sec[key], data)))
    for key, fn in (("alarms", _alarms), ("devices", _devices), ("markers", _markers),
                    ("schedule", _schedule), ("system", _system)):
        if key in sec and sec[key] is not None:
            parts.append(_section(key, _failed_text(sec[key]) if _failed(sec[key]) else fn(sec[key], data)))
    parts.append('<footer><p>Stagewatch is a guide for the crew and does not replace certified monitoring or '
                 'the people responsible for safety. Times are site time.</p>'
                 f'<p>{e(ENV_ADVICE)}</p></footer>')
    return ("<!doctype html>\n<html lang=\"en-GB\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta name=\"referrer\" content=\"no-referrer\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP}\">"
            f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>" + "".join(parts) +
            "</main></body></html>")


def _t(ts, data: ReportData) -> str:
    return fmt.clock(ts, data.site, bool(data.header.get("dst")))


def _d(ts, data: ReportData) -> str:
    return fmt.stamp(ts, data.site, bool(data.header.get("dst")))


def _summary(s: dict, data: ReportData) -> str:
    if _failed(s):
        return _failed_text(s)
    rows = []
    if s.get("coverage") is not None:
        cov = fmt.pct(s["coverage"]) if s.get("coverage_known") else "not known"
        rows.append(["Time Stagewatch was recording", cov])
    rows.append(["Times Stagewatch was not recording", s["down_count"]])
    lv = s.get("alarms_by_level") or {}
    if lv:
        rows.append(["Alarms", f'{s["alarms"]} (' + ", ".join(f"{v} {k}" for k, v in lv.items() if v) + ")"
                     if s["alarms"] else "0"])
        if s.get("never_acked") is not None:
            rows.append(["Alarms never acknowledged", s["never_acked"]])
    rows.append(["Device outages", s["outages"]])
    if s.get("markers") is not None:
        rows.append(["Markers", s["markers"]])
    for k, r in (s.get("ranges") or {}).items():
        rows.append([f"Site {k}", f'{fmt.num(r["min"], r["decimals"])} to {fmt.num(r["max"], r["decimals"])} {r["unit"]}'])
    if s.get("max_gust") is not None:
        rows.append(["Highest wind gust (highest recorded sample)", fmt.num(s["max_gust"])])
    return table(["Measure", "Value"], rows)


def _gaps(g: dict, data: ReportData) -> str:
    if _failed(g):
        return _failed_text(g)
    out = ['<p>Gaps are shown as they are. Nothing is filled in.</p>']
    if g["down"]:
        rows = [[_d(d["start"], data), _d(d["end"], data), fmt.duration(d["length_s"]), d["how_ended"], d["note"]]
                for d in g["down"]]
        out.append("<h3>Stagewatch not recording</h3>" + table(["From", "To", "Length", "How it ended", "Note"], rows, cap=ROW_CAP))
    else:
        out.append("<p>Stagewatch was recording for the whole range.</p>")
    if g["sensor"]:
        rows = [[x["sensor"], _d(x["start"], data), _d(x["end"], data), fmt.duration(x["length_s"]), x["outage"]]
                for x in g["sensor"]]
        out.append("<h3>Sensors with no readings while Stagewatch was recording</h3>"
                   + table(["Sensor", "From", "To", "Length", "Linked to"], rows, cap=ROW_CAP))
    for j in g["clock_jumps"]:
        out.append(f'<p class="warn">The computer clock moved backwards when Stagewatch started at {e(_d(j["ts"], data))}.</p>')
    for n in g["notes"]:
        out.append(f"<p>{e(n)}</p>")
    return "".join(out)


def _env(env: dict, data: ReportData) -> str:
    if _failed(env):
        return _failed_text(env)
    out = [f'<p class="mut">{e(env["advice"])}</p>']
    if env.get("empty"):
        out.append(f'<p>{e(env["empty"])}</p>')
    h = data.header
    charts = {c["kind"]: c for c in data.charts.get("env", [])}
    for k in env["kinds"]:
        out.append(f'<h3>{e(k["title"])} ({e(k["unit"])})</h3>')
        c = charts.get(k["kind"])
        if c:
            out.append(svg.line_chart(c, data.charts.get("context", {}), h["range_start"], h["range_end"],
                                      data.site, bool(h.get("dst"))))
        rows = []
        for r in ([k["site"]] if k["site"] else []) + k["sensors"]:
            d = k["decimals"]
            name = r["label"] + (f' ({r["area"]})' if r.get("area") else "")
            rows.append([name, f'{fmt.num(r["min"], d)} at {_t(r["min_ts"], data)}',
                         f'{fmt.num(r["max"], d)} at {_t(r["max_ts"], data)}', fmt.num(r["mean"], d),
                         fmt.pct(r["coverage"])])
        out.append(table(["Sensor", "Lowest", "Highest", "Mean", "Coverage"], rows))
    ex = env.get("extra", {})
    if "dew_point" in ex:
        r = ex["dew_point"]
        out.append(f'<p>Site dew point: {fmt.num(r["min"])} to {fmt.num(r["max"])} °C.</p>')
    if "speed_of_sound" in ex:
        r = ex["speed_of_sound"]
        out.append(f'<p>Site speed of sound: {fmt.num(r["min"])} to {fmt.num(r["max"])} m/s, up to '
                   f'{fmt.num(r["travel_change_ms"])} ms change over {r["distance_m"]:g} m (the reference distance in Stagewatch).</p>')
    if env.get("mean_note"):
        out.append(f'<p class="mut">{e(env["mean_note"])}</p>')
    return "".join(out)


def _wind(w: dict, data: ReportData) -> str:
    rows = [[r["label"], "gust" if r.get("gust") else "speed", f'{fmt.num(r["max"])} {r["unit"]} at {_t(r["max_ts"], data)}',
             fmt.num(r["mean"]) if not r.get("gust") else "", fmt.pct(r["coverage"])] for r in w["rows"]]
    return (table(["Sensor", "Kind", "Highest recorded sample", "Mean", "Coverage"], rows)
            + f'<p class="mut">{e(w["peak_note"])}</p><p class="mut">{e(w["advice"])}</p>')


def _power(p: dict, data: ReportData) -> str:
    out = [f'<p class="mut">{e(p["peak_note"])}</p>']

    def block(title: str, rows: list[dict], dec: int = 1) -> None:
        if not rows:
            return
        body = [[r["label"], r["unit"], f'{fmt.num(r["max"], dec)} at {_t(r["max_ts"], data)}',
                 f'{fmt.num(r["min"], dec)} at {_t(r["min_ts"], data)}', fmt.num(r["mean"], dec), fmt.pct(r["coverage"])]
                for r in rows]
        out.append(f"<h3>{e(title)}</h3>" + table(["Reading", "Unit", "Highest recorded sample", "Lowest", "Mean", "Coverage"], body))

    block("Current (highest per phase)", p["current"], 2)
    block("Earth leakage", p["leakage"], 2)
    if p["energy"]:
        body = [[x["label"], fmt.num(x["kwh"], 2), x["method"] + (" - estimated" if x["estimated"] else ""),
                 (f'{x["resets"]} counter reset(s); the step across each is not counted' if x["resets"] else "")
                 + (f' No readings for {fmt.duration(x["gap_s"])}.' if x["gap_s"] else "")] for x in p["energy"]]
        out.append("<h3>Energy (kWh)</h3>" + table(["Meter", "kWh", "How worked out", "Note"], body))
        if p["total_kwh"] is not None:
            out.append(f'<p>Total: {fmt.num(p["total_kwh"], 2)} kWh.</p>')
        elif p["total_note"]:
            out.append(f'<p class="mut">{e(p["total_note"])}</p>')
    block("Voltage and frequency", p["voltage_frequency"], 1)
    block("Power and other readings", p["other"], 2)
    out.append(f'<p class="mut">{e(p["advice"])}</p>')
    return "".join(out)


def _spl(s: dict, data: ReportData) -> str:
    out = []
    if s["measured"]:
        rows = [[r["label"], r.get("labels", ""), f'{fmt.num(r["max"])} dB at {_t(r["max_ts"], data)}',
                 f'{fmt.num(r["min"])} dB', fmt.pct(r["coverage"])] for r in s["measured"]]
        out.append(table(["Input", "Metric", "Highest recorded sample", "Lowest", "Coverage"], rows))
        out.append(f'<p class="mut">{e(s["peak_note"])}</p>')
    if s["measured_note"]:
        out.append(f'<p>{e(s["measured_note"])}</p>')
    if s["limits_at_start"]:
        out.append("<h3>Limits in force when the range began</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in s["limits_at_start"]) + "</ul>")
    for r in s["revisions"]:
        out.append(f'<h3>Changed at {e(_t(r["ts"], data))} (revision {e(r["rev"])})</h3>'
                   + (f'<p>{e(r["note"])}</p>' if r["note"] else "")
                   + "<ul>" + "".join(f"<li>{e(x)}</li>" for x in r["limits"]) + "</ul>")
    out.append(f'<p class="mut">{e(s["advice"])}</p>')
    return "".join(out)


def _alarms(a: dict, data: ReportData) -> str:
    rows = []
    for r in a["rows"][:ALARM_ROW_CAP]:
        raised = "already active at start of range" if r["already_active"] else _t(r["raised"], data)
        cleared = _t(r["cleared"], data) if r["cleared"] is not None else r["end_text"]
        ack = _t(r["ack_ts"], data) if r["ack_ts"] is not None else \
            ("acknowledged before this range" if r["acked_before"] else "")
        rows.append([raised, cleared, fmt.duration(r["duration_s"]),
                     Raw(f'<span class="lvl lvl-{e(r["level_text"])}">{e(r["level_text"])}</span>'), r["message"], ack,
                     r["ack_from"], fmt.duration(r["time_to_ack_s"]) if r["time_to_ack_s"] is not None else ""])
    hdr = ["Raised", "Cleared", "Duration", "Level", "Message", "First acknowledged", "Acknowledged from", "Time to acknowledge"]
    if a.get("hide_ack_from"):
        hdr.pop(6)
        for r in rows:
            r.pop(6)
    out = [table(hdr, rows) if rows else "<p>No alarms in this range.</p>"]
    if len(a["rows"]) > ALARM_ROW_CAP:
        out.append(f'<p>{len(a["rows"]) - ALARM_ROW_CAP} more are in the CSV.</p>')
    if a.get("note"):
        out.append(f'<p class="mut">{e(a["note"])}</p>')
    return "".join(out)


def _devices(d: dict, data: ReportData) -> str:
    quiet = d.get("quieted") or []
    note = ("" if not quiet else '<p class="mut">Imported units quieted by the admin (their alerts show as '
            f'Advisory): {e(", ".join(quiet[:50]))} (quieted).</p>')
    return _devices_table(d, data) + note


def _devices_table(d: dict, data: ReportData) -> str:
    if not d["rows"]:
        return "<p>No device outages in this range.</p>"
    hide = d.get("hide_detail")
    rows = []
    for r in d["rows"]:
        row = [r["device"], r["area"], "already offline at start" if r["already_active"] else _t(r["start"], data),
               _t(r["end"], data), fmt.duration(r["duration_s"]), r["status"] + ("" if hide or not r["detail"] else f' ({r["detail"]})'),
               r["end_text"]]
        rows.append(row)
    return table(["Device", "Area", "From", "To", "Duration", "Status", "Ended"], rows, cap=ROW_CAP)


def _markers(m: dict, data: ReportData) -> str:
    if not m["rows"]:
        return "<p>No markers in this range.</p>"
    rows = []
    for r in m["rows"]:
        base = [r["n"], _t(r["ts"], data), r["label"]]
        if not m.get("minimal"):
            base += [r["source"], fmt.num(r["temp_c"]) if r["temp_c"] is not None else "",
                     fmt.num(r["rh"], 0) if r["rh"] is not None else "",
                     fmt.num(r["delta_first_ms"], 2) if r["delta_first_ms"] is not None else "",
                     fmt.num(r["delta_prev_ms"], 2) if r["delta_prev_ms"] is not None else ""]
        rows.append(base)
    hdr = ["No.", "Time", "Label"] if m.get("minimal") else \
        ["No.", "Time", "Label", "Source", "Site °C", "Site %RH", "Travel change vs first (ms)", "vs previous (ms)"]
    return table(hdr, rows, cap=ROW_CAP)


def _schedule(s: dict, data: ReportData) -> str:
    if not s["rows"]:
        return "<p>No planned items in this range.</p>"
    return table(["Time", "Stage", "Kind", "Title"], [[_t(r["start"], data), r["stage"], r["kind"], r["title"]] for r in s["rows"]], cap=ROW_CAP)


def _system(s: dict, data: ReportData) -> str:
    if not s["rows"]:
        return "<p>No updates, rollbacks or restarts in this range.</p>"
    return table(["Time", "Source", "Event", "Detail"], [[_t(r["ts"], data), r["source"], r["event"], r["message"]] for r in s["rows"]], cap=ROW_CAP)
