"""The report's data as CSV files in one ZIP, plus the HTML report.

Each file is UTF-8 with a byte order mark (so Excel reads it correctly), RFC 4180 quoting, UK time
formats (DD/MM/YYYY HH:MM:SS, site time) and the unit in the column name.  A text cell that
starts with = + - @ tab or CR gets a leading ' so a spreadsheet cannot run it as a formula (the
same rule as the schedule export).  Numbers are written as numbers.
"""

from __future__ import annotations

import csv
import io
import zipfile

from . import fmt
from .build import ReportData
from .html import render_html
from .query import ReportError

MAX_ZIP_BYTES = 50 * 1024 * 1024
_DANGER = ("=", "+", "-", "@", "\t", "\r")


def cell(v: object) -> str | int | float:
    """One CSV cell, made safe.  Text starting with a formula character is prefixed with '."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return v
    s = str(v)
    return "'" + s if s.startswith(_DANGER) else s


def _csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow([cell(h) for h in headers])
    for r in rows:
        w.writerow([cell(c) for c in r])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def _r(v: float | None, d: int = 2):
    return None if v is None else round(v, d)


def csv_files(data: ReportData) -> dict[str, bytes]:
    """File name -> bytes for each section that is in the report."""
    site, sec, h = data.site, data.sections, data.header
    z = bool(h.get("dst"))
    ts = lambda t: fmt.stamp(t, site, z)  # noqa: E731
    out: dict[str, bytes] = {}

    s = sec.get("summary")
    if isinstance(s, dict) and "error" not in s:
        rows = [["Time Stagewatch was recording (%)", _r(s["coverage"] * 100, 1) if s.get("coverage_known") and s["coverage"] is not None else ""],
                ["Times Stagewatch was not recording", s["down_count"]], ["Alarms", s["alarms"]],
                ["Alarms never acknowledged", s["never_acked"]], ["Device outages", s["outages"]],
                ["Markers", s["markers"]]]
        for k, r in (s.get("ranges") or {}).items():
            rows.append([f"Site {k} lowest ({r['unit']})", _r(r["min"])])
            rows.append([f"Site {k} highest ({r['unit']})", _r(r["max"])])
        out["summary.csv"] = _csv_bytes(["Measure", "Value"], rows)

    env = sec.get("env")
    if isinstance(env, dict) and "error" not in env:
        rows, series = [], []
        charts = {c["kind"]: c for c in data.charts.get("env", [])}
        for k in env["kinds"]:
            for r in ([k["site"]] if k["site"] else []) + k["sensors"]:
                rows.append([k["title"], r["label"], r.get("area", ""), k["unit"], _r(r["min"]), ts(r["min_ts"]),
                             _r(r["max"]), ts(r["max_ts"]), _r(r["mean"]), _r(r["coverage"] * 100, 1),
                             _r(r["interval_s"], 1)])
            c = charts.get(k["kind"], {})
            for p in c.get("band", []):
                series.append([k["title"], "Site average", ts(p[0]), _r(p[2]), _r(p[1]), _r(p[3])])
            for ln in c.get("lines", []):
                for p in ln["points"]:
                    series.append([k["title"], ln["label"], ts(p[0]), _r(p[2]), _r(p[1]), _r(p[3])])
        out["env_stats.csv"] = _csv_bytes(
            ["Kind", "Sensor", "Area", "Unit", "Lowest", "Lowest at", "Highest recorded sample", "Highest at", "Mean",
             "Coverage (%)", "Recording interval (s)"], rows)
        out["readings_1min.csv"] = _csv_bytes(
            ["Kind", "Sensor", "Minute starting", "Lowest of minute", "Mean of minute", "Highest of minute"], series)

    al = sec.get("alarms")
    if isinstance(al, dict) and "rows" in al:
        hide = al.get("hide_ack_from")
        hdr = ["Raised", "Already active at start", "Cleared", "Ended", "Duration (s)", "Level", "Message",
               "First acknowledged", "Acknowledged before range"] + ([] if hide else ["Acknowledged from"]) + ["Time to acknowledge (s)"]
        rows = []
        for r in al["rows"]:
            row = [ts(r["raised"]), r["already_active"], ts(r["cleared"]), r["end_text"], _r(r["duration_s"], 0),
                   r["level_text"], r["message"], ts(r["ack_ts"]), r["acked_before"]]
            if not hide:
                row.append(r["ack_from"])
            row.append(_r(r["time_to_ack_s"], 0))
            rows.append(row)
        out["alarms.csv"] = _csv_bytes(hdr, rows)

    dv = sec.get("devices")
    if isinstance(dv, dict) and "rows" in dv:
        hide = dv.get("hide_detail")
        rows = [[r["device"], r["area"], ts(r["start"]), r["already_active"], ts(r["end"]), _r(r["duration_s"], 0),
                 r["status"], "" if hide else r["detail"], r["end_text"]] for r in dv["rows"]]
        out["outages.csv"] = _csv_bytes(["Device", "Area", "From", "Already offline at start", "To", "Duration (s)",
                                         "Status", "Detail", "Ended"], rows)

    mk = sec.get("markers")
    if isinstance(mk, dict) and "rows" in mk:
        if mk.get("minimal"):
            out["markers.csv"] = _csv_bytes(["No.", "Time", "Label"], [[r["n"], ts(r["ts"]), r["label"]] for r in mk["rows"]])
        else:
            out["markers.csv"] = _csv_bytes(
                ["No.", "Time", "Label", "Source", "Site temperature (°C)", "Site humidity (%RH)",
                 "Travel change vs first marker (ms)", "Travel change vs previous marker (ms)"],
                [[r["n"], ts(r["ts"]), r["label"], r["source"], _r(r["temp_c"]), _r(r["rh"], 0),
                  _r(r["delta_first_ms"]), _r(r["delta_prev_ms"])] for r in mk["rows"]])

    g = sec.get("gaps")
    if isinstance(g, dict) and "down" in g:
        rows = [["Stagewatch not recording", "", ts(d["start"]), ts(d["end"]), _r(d["length_s"], 0), d["how_ended"], d["note"]]
                for d in g["down"]]
        rows += [["Sensor with no readings", x["sensor"], ts(x["start"]), ts(x["end"]), _r(x["length_s"], 0),
                  x["outage"], ""] for x in g["sensor"]]
        out["gaps.csv"] = _csv_bytes(["Kind", "Sensor", "From", "To", "Length (s)", "How it ended / linked to", "Note"], rows)

    spl = sec.get("spl")
    if isinstance(spl, dict) and "error" not in spl:
        rows = [["Measured", r["label"], "", _r(r["max"]), ts(r["max_ts"]), _r(r["min"]), r.get("labels", "")] for r in spl["measured"]]
        rows += [["Limits in force at start", x, "", None, "", None, ""] for x in spl["limits_at_start"]]
        for rv in spl["revisions"]:
            rows += [[f"Revision {rv['rev']}", x, ts(rv["ts"]), None, "", None, rv["note"]] for x in rv["limits"]] or \
                    [[f"Revision {rv['rev']}", "", ts(rv["ts"]), None, "", None, rv["note"]]]
        out["spl_limits.csv"] = _csv_bytes(["Kind", "Input or limit", "Changed at", "Highest recorded sample (dB)",
                                            "Highest at", "Lowest (dB)", "Note"], rows)

    pw = sec.get("power")
    if isinstance(pw, dict) and "error" not in pw:
        rows = []
        for grp in ("current", "leakage", "voltage_frequency", "other"):
            for r in pw[grp]:
                rows.append([grp.replace("_", " "), r["label"], r["unit"], _r(r["max"], 3), ts(r["max_ts"]),
                             _r(r["min"], 3), _r(r["mean"], 3), _r(r["coverage"] * 100, 1), _r(r["interval_s"], 1)])
        for x in pw["energy"]:
            rows.append(["energy (kWh)", x["label"], "kWh", _r(x["kwh"], 3), "", None, None, None, None])
        out["power.csv"] = _csv_bytes(["Group", "Reading", "Unit", "Highest recorded sample / kWh", "Highest at", "Lowest",
                                       "Mean", "Coverage (%)", "Recording interval (s)"], rows)

    sy = sec.get("system")
    if isinstance(sy, dict) and "rows" in sy:
        out["system_events.csv"] = _csv_bytes(["Time", "Source", "Event", "Detail"],
                                              [[ts(r["ts"]), r["source"], r["event"], r["message"]] for r in sy["rows"]])
    sc = sec.get("schedule")
    if isinstance(sc, dict) and "rows" in sc:
        out["schedule.csv"] = _csv_bytes(["Start", "End", "Stage", "Kind", "Title"],
                                         [[ts(r["start"]), ts(r["end"]), r["stage"], r["kind"], r["title"]] for r in sc["rows"]])
    return out


def make_zip(data: ReportData, max_bytes: int = MAX_ZIP_BYTES) -> bytes:
    """report.html plus one CSV per section, as ZIP bytes.  The size is checked as each file is
    added, so a huge result stops early; refuses a result over ``max_bytes`` (``too_big``)."""
    too_big = ReportError("too_big", "The data is too large to download. Try a shorter range.")
    buf = io.BytesIO()
    raw = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        files = {"report.html": render_html(data).encode("utf-8")}
        files.update(csv_files(data))
        for name, blob in files.items():
            raw += len(blob)
            if raw > max_bytes * 4:
                raise too_big
            z.writestr(name, blob)
            if buf.tell() > max_bytes:
                raise too_big
    return buf.getvalue()
