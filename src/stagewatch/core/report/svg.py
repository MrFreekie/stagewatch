"""Self-contained SVG charts for the report: no script, no external files, prints as vectors.

Colours are the Okabe-Ito set (safe for the common kinds of colour blindness).  Nothing depends on
colour alone: the site line is solid, the sensor lines differ in dash, a stretch with no recording
is hatched and labelled, and every marker has a number that matches the markers table.
"""

from __future__ import annotations

import html as _html

from .. import sitetime
from .query import BUCKET_S

W, H = 700, 250
PX0, PX1, PY0, PY1 = 52, 688, 14, 176
MAX_POINTS = 600
SENSOR_COLOURS = ["#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#7F6A00", "#444444", "#999999"]
SENSOR_DASH = ["", "6 3", "2 2", "8 3 2 3", "", "6 3", "2 2", "8 3 2 3"]
SITE_COLOUR = "#0072B2"
LEVEL_COLOURS = {1: "#E69F00", 2: "#D55E00", 3: "#7A0019"}
_seq = [0]


def _e(s: object) -> str:
    return _html.escape(str(s), quote=True)


def _downsample(points: list[list[float]], limit: int = MAX_POINTS) -> list[list[float]]:
    if len(points) <= limit:
        return points
    k = -(-len(points) // limit)
    out = []
    for i in range(0, len(points), k):
        g = points[i:i + k]
        out.append([g[0][0], sum(p[1] for p in g) / len(g), min(p[2] for p in g), max(p[3] for p in g)])
    return out


def _runs(points: list[list[float]], gap_s: float) -> list[list[list[float]]]:
    """Split a line wherever there is a gap, so a gap is never drawn across."""
    out, cur = [], []
    for p in points:
        if cur and p[0] - cur[-1][0] > gap_s:
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def _hour_ticks(start: float, end: float, site) -> list[float]:
    first = sitetime.local(start, site).replace(minute=0, second=0, microsecond=0)
    ticks, base = [], first.timestamp()
    span_h = (end - start) / 3600.0
    step = 1 if span_h <= 10 else 2 if span_h <= 20 else 3 if span_h <= 36 else 6
    i = 0
    while i < 400:
        ts = base + i * 3600.0   # absolute hours, so a clock change does not shift the ticks
        if ts > end:
            break
        if ts >= start and i % step == 0:
            ticks.append(ts)
        i += 1
    return ticks


def line_chart(spec: dict, context: dict, start: float, end: float, site, dst: bool = False,
               uid: str = "c") -> str:
    """One chart: the site min-max band and mean, thin sensor lines, hatched stretches with no
    recording, numbered marker ticks and an alarm strip.  ``spec`` is an entry of charts['env']."""
    _seq[0] += 1
    cid = f"{uid}{_seq[0]}"
    span = max(end - start, 1.0)

    def X(t: float) -> float:
        return PX0 + (min(max(t, start), end) - start) / span * (PX1 - PX0)

    band = _downsample(spec.get("band") or [])
    lines = [(ln["label"], _downsample(ln["points"])) for ln in spec.get("lines", [])]
    vals = [v for p in band for v in (p[2], p[3])] + [p[1] for _, pts in lines for p in pts]
    if not vals:
        return (f'<svg class="chart" viewBox="0 0 {W} 60" role="img" aria-label="{_e(spec["title"])}: no readings">'
                f'<text x="10" y="34" font-size="13" fill="#444">{_e(spec["title"])}: no readings in this range.</text></svg>')
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.1 or max(abs(hi) * 0.02, 0.5)
    lo, hi = lo - pad, hi + pad

    def Y(v: float) -> float:
        return PY1 - (v - lo) / (hi - lo) * (PY1 - PY0)

    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="{_e(spec["title"])} over the report range">',
           f'<title>{_e(spec["title"])}</title>',
           f'<defs><pattern id="{cid}h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           f'<rect width="6" height="6" fill="#eee"/><line x1="0" y1="0" x2="0" y2="6" stroke="#888" stroke-width="2"/>'
           f'</pattern></defs>',
           f'<rect x="{PX0}" y="{PY0}" width="{PX1 - PX0}" height="{PY1 - PY0}" fill="#fff" stroke="#999"/>']
    for a, b in context.get("down", []):
        if b > start and a < end:
            x0, x1 = X(a), X(b)
            out.append(f'<rect x="{x0:.1f}" y="{PY0}" width="{max(x1 - x0, 1.5):.1f}" height="{PY1 - PY0}" '
                       f'fill="url(#{cid}h)" opacity="0.9"><title>Stagewatch not recording</title></rect>')
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = Y(v)
        out.append(f'<line x1="{PX0}" y1="{y:.1f}" x2="{PX1}" y2="{y:.1f}" stroke="#ddd"/>'
                   f'<text x="{PX0 - 5}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#333">{v:.1f}</text>')
    for t in _hour_ticks(start, end, site):
        x = X(t)
        label = sitetime.local(t, site).strftime("%H:%M")
        if dst:
            label += " " + (sitetime.local(t, site).tzname() or "")
        out.append(f'<line x1="{x:.1f}" y1="{PY1}" x2="{x:.1f}" y2="{PY1 + 4}" stroke="#666"/>'
                   f'<text x="{x:.1f}" y="{PY1 + 30}" text-anchor="middle" font-size="10" fill="#333">{_e(label)}</text>')
    gap = BUCKET_S * 1.5
    if band:
        for run in _runs(band, gap):
            top = " ".join(f"{X(p[0]):.1f},{Y(p[3]):.1f}" for p in run)
            bot = " ".join(f"{X(p[0]):.1f},{Y(p[2]):.1f}" for p in reversed(run))
            out.append(f'<polygon points="{top} {bot}" fill="{SITE_COLOUR}" opacity="0.18"/>')
            out.append(f'<polyline points="{" ".join(f"{X(p[0]):.1f},{Y(p[1]):.1f}" for p in run)}" fill="none" '
                       f'stroke="{SITE_COLOUR}" stroke-width="2.2"/>')
    for i, (label, pts) in enumerate(lines):
        col, dash = SENSOR_COLOURS[i % 8], SENSOR_DASH[i % 8]
        for run in _runs(pts, gap):
            d = f' stroke-dasharray="{dash}"' if dash else ""
            out.append(f'<polyline points="{" ".join(f"{X(p[0]):.1f},{Y(p[1]):.1f}" for p in run)}" fill="none" '
                       f'stroke="{col}" stroke-width="1"{d}><title>{_e(label)}</title></polyline>')
    for a, b, level in context.get("alarms", []):
        if b > start and a < end:
            x0, x1 = X(a), X(b)
            out.append(f'<rect x="{x0:.1f}" y="{PY1 + 7}" width="{max(x1 - x0, 2):.1f}" height="7" '
                       f'fill="{LEVEL_COLOURS.get(level, "#666")}"><title>Alarm level {int(level)}</title></rect>')
    for n, ts in context.get("markers", [])[:40]:
        if start <= ts <= end:
            x = X(ts)
            out.append(f'<line x1="{x:.1f}" y1="{PY0}" x2="{x:.1f}" y2="{PY1}" stroke="#333" stroke-dasharray="1 3"/>'
                       f'<circle cx="{x:.1f}" cy="{PY0 + 2}" r="7" fill="#fff" stroke="#333"/>'
                       f'<text x="{x:.1f}" y="{PY0 + 6}" text-anchor="middle" font-size="9" fill="#000">{int(n)}</text>')
    # legend, one line
    lx = PX0
    legend = spec.get("legend") or "site mean, band = lowest to highest"
    if band or not spec.get("legend"):
        out.append(f'<line x1="{lx}" y1="{H - 8}" x2="{lx + 18}" y2="{H - 8}" stroke="{SITE_COLOUR}" stroke-width="2.2"/>'
                   f'<text x="{lx + 22}" y="{H - 4}" font-size="10" fill="#333">{_e(legend)}</text>')
    else:
        out.append(f'<text x="{lx}" y="{H - 4}" font-size="10" fill="#333">{_e(legend)}</text>')
    out.append(f'<rect x="{lx + 230}" y="{H - 14}" width="14" height="10" fill="url(#{cid}h)" stroke="#888"/>'
               f'<text x="{lx + 248}" y="{H - 4}" font-size="10" fill="#333">Stagewatch not recording</text>')
    if spec.get("more"):
        out.append(f'<text x="{PX1}" y="{H - 4}" text-anchor="end" font-size="10" fill="#333">'
                   f'{int(spec["more"])} more sensors are in the table</text>')
    out.append("</svg>")
    return "".join(out)


def step_chart(spec: dict, context: dict, start: float, end: float, site, dst: bool = False,
               uid: str = "s") -> str:
    """The limit in force over time as a step line per limit (a break where no limit was set), with a
    dashed line and "r3" label where a new set of limits was saved, the stretches with no recording
    hatched, and numbered marker ticks.  ``spec`` is charts['spl'].  Every step also carries its value
    as text, so nothing depends on colour."""
    _seq[0] += 1
    cid = f"{uid}{_seq[0]}"
    span = max(end - start, 1.0)
    series = [s for s in spec.get("series", []) if s.get("steps")]
    title = spec.get("title", "Sound level limit (dB)")

    def X(t: float) -> float:
        return PX0 + (min(max(t, start), end) - start) / span * (PX1 - PX0)

    vals = [st[2] for s in series for st in s["steps"]]
    if not vals:
        return (f'<svg class="chart" viewBox="0 0 {W} 60" role="img" aria-label="{_e(title)}: no limits">'
                f'<text x="10" y="34" font-size="13" fill="#444">{_e(title)}: no limit was set in this range.</text></svg>')
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.25, 3.0)
    lo, hi = lo - pad, hi + pad

    def Y(v: float) -> float:
        return PY1 - (v - lo) / (hi - lo) * (PY1 - PY0)

    rows = -(-len(series) // 3)
    height = H + 14 * rows
    out = [f'<svg class="chart" viewBox="0 0 {W} {height}" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="{_e(title)} over the report range">',
           f'<title>{_e(title)}</title>',
           f'<defs><pattern id="{cid}h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           f'<rect width="6" height="6" fill="#eee"/><line x1="0" y1="0" x2="0" y2="6" stroke="#888" stroke-width="2"/>'
           f'</pattern></defs>',
           f'<rect x="{PX0}" y="{PY0}" width="{PX1 - PX0}" height="{PY1 - PY0}" fill="#fff" stroke="#999"/>']
    for a, b in context.get("down", []):
        if b > start and a < end:
            x0, x1 = X(a), X(b)
            out.append(f'<rect x="{x0:.1f}" y="{PY0}" width="{max(x1 - x0, 1.5):.1f}" height="{PY1 - PY0}" '
                       f'fill="url(#{cid}h)" opacity="0.9"><title>Stagewatch not recording</title></rect>')
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = Y(v)
        out.append(f'<line x1="{PX0}" y1="{y:.1f}" x2="{PX1}" y2="{y:.1f}" stroke="#ddd"/>'
                   f'<text x="{PX0 - 5}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#333">{v:.1f}</text>')
    for t in _hour_ticks(start, end, site):
        x = X(t)
        label = sitetime.local(t, site).strftime("%H:%M")
        if dst:
            label += " " + (sitetime.local(t, site).tzname() or "")
        out.append(f'<line x1="{x:.1f}" y1="{PY1}" x2="{x:.1f}" y2="{PY1 + 4}" stroke="#666"/>'
                   f'<text x="{x:.1f}" y="{PY1 + 30}" text-anchor="middle" font-size="10" fill="#333">{_e(label)}</text>')
    for rv in spec.get("revisions", [])[:40]:
        if start < rv["ts"] < end:
            x = X(rv["ts"])
            out.append(f'<line x1="{x:.1f}" y1="{PY0}" x2="{x:.1f}" y2="{PY1}" stroke="#555" stroke-dasharray="4 3"/>'
                       f'<text x="{x + 3:.1f}" y="{PY1 - 4}" font-size="9" fill="#222">r{int(rv["rev"])}</text>')
    labels_left = 14
    for i, s in enumerate(series):
        col, dash = SENSOR_COLOURS[i % 8], SENSOR_DASH[i % 8]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        runs: list[list[list[float]]] = []
        for a, b, v in s["steps"]:
            if runs and abs(runs[-1][-1][0] - a) < 1e-6:
                runs[-1].append([a, v])
            else:
                runs.append([[a, v]])
            runs[-1].append([b, v])
        for run in runs:
            pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in run)
            out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.2"{d}>'
                       f'<title>{_e(s["label"])}</title></polyline>')
        for a, b, v in s["steps"]:
            if labels_left <= 0:
                break
            labels_left -= 1
            x = X(a) + 3
            anchor = "start" if x < PX1 - 40 else "end"
            out.append(f'<text x="{x if anchor == "start" else X(a) - 3:.1f}" y="{Y(v) - 5:.1f}" text-anchor="{anchor}" '
                       f'font-size="10" fill="#000">{v:g} dB</text>')
    for n, ts in context.get("markers", [])[:40]:
        if start <= ts <= end:
            x = X(ts)
            out.append(f'<line x1="{x:.1f}" y1="{PY0}" x2="{x:.1f}" y2="{PY1}" stroke="#333" stroke-dasharray="1 3"/>'
                       f'<circle cx="{x:.1f}" cy="{PY0 + 2}" r="7" fill="#fff" stroke="#333"/>'
                       f'<text x="{x:.1f}" y="{PY0 + 6}" text-anchor="middle" font-size="9" fill="#000">{int(n)}</text>')
    ly = H - 4
    out.append(f'<rect x="{PX0}" y="{ly - 10}" width="14" height="10" fill="url(#{cid}h)" stroke="#888"/>'
               f'<text x="{PX0 + 18}" y="{ly}" font-size="10" fill="#333">Stagewatch not recording</text>'
               f'<line x1="{PX0 + 190}" y1="{ly - 5}" x2="{PX0 + 208}" y2="{ly - 5}" stroke="#555" stroke-dasharray="4 3"/>'
               f'<text x="{PX0 + 212}" y="{ly}" font-size="10" fill="#333">new limits saved (r = revision number)</text>')
    for i, s in enumerate(series):
        row, col_i = divmod(i, 3)
        x0, y = PX0 + col_i * 212, ly + 14 * (row + 1)
        col, dash = SENSOR_COLOURS[i % 8], SENSOR_DASH[i % 8]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        name = s["label"] if len(s["label"]) <= 26 else s["label"][:25] + "…"
        out.append(f'<line x1="{x0}" y1="{y - 4}" x2="{x0 + 18}" y2="{y - 4}" stroke="{SENSOR_COLOURS[i % 8]}" stroke-width="2.2"{d}/>'
                   f'<text x="{x0 + 22}" y="{y}" font-size="10" fill="#333">{_e(name)}</text>')
    if spec.get("more"):
        out.append(f'<text x="{PX1}" y="{ly}" text-anchor="end" font-size="10" fill="#333">'
                   f'{int(spec["more"])} more limits are in the table</text>')
    out.append("</svg>")
    return "".join(out)
