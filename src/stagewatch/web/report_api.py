"""Routes for the end-of-day show report (admin only, read-only).

GET /api/admin/report/shows         the shows and days there are reports for
GET /api/admin/report/markers       the markers of one show (for the "between markers" range)
GET /api/admin/report               the report as one printable page
GET /api/admin/report/data.zip      the same report plus CSV files, as a download
POST /api/admin/report/demo         emulate only: a finished demo day to report on

Errors use fixed wording and never repeat what was sent.  One report is made at a time.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from ..core.report import ALL_SECTIONS, ReportError, ReportScope, build_report, make_zip, open_readonly, render_html
from ..core.report import demo as report_demo
from ..core.report import redact
from ..core.report.query import DEFAULT_DEADLINE_S, get_show, list_shows

log = logging.getLogger("stagewatch.report")

PRESETS = ("day", "show_time", "markers", "custom")
HHMM = r"^\d{2}:\d{2}$"

# code -> (status, fixed wording).  Never the caller's input.
ERRORS = {
    "no_show": (404, "No such show."),
    "no_database": (503, "The history is not available."),
    "damaged": (503, "The history file could not be read. Try a backup."),
    "deadline": (503, "The report took too long. Try a shorter range, or make it on the hub PC with `stagewatch report`."),
    "too_big": (422, "The data is too large to download. Try a shorter range."),
    "no_doors_curfew": (422, "No doors and curfew in the schedule for this show."),
    "no_marker": (422, "Pick two markers from this show."),
    "bad_time": (422, "Times must be written as HH:MM, for example 19:30."),
    "end_before_start": (422, "The end is before the start. Times before 06:00 count as the next morning."),
    "empty_range": (422, "That range is empty."),
    "too_long": (422, "A range can be at most 24 hours. Use the whole day for longer."),
    "outside_show": (422, "That range is outside this show."),
    "bad_preset": (422, "Unknown range."),
    "bad_sections": (422, "Unknown section."),
}
BUSY = "A report is already being made. Try again in a moment."


def error_for(exc: ReportError) -> HTTPException:
    status, text = ERRORS.get(exc.code, (422, "The report could not be made."))
    return HTTPException(status, text)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:30].strip("-")
    return s or "show"


def file_name(show: dict, win_start_label: str, ext: str) -> str:
    day = show.get("day") or "day"
    day = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)) else "day"
    return f"stagewatch-report-{slug(show.get('event_name') or show.get('name') or '')}-{day}{win_start_label}.{ext}"


def parse_sections(raw: str) -> set[str] | None:
    if not raw:
        return None
    items = {p for p in raw.split(",") if p}
    if not items or not items <= set(ALL_SECTIONS):
        raise HTTPException(422, ERRORS["bad_sections"][1])
    return items


def register(app: FastAPI, hub, admin_deps: list) -> None:
    lock = asyncio.Lock()
    app.state.report_deadline_s = DEFAULT_DEADLINE_S

    def scope_params(
        show_id: int = Query(ge=1, le=10_000_000),
        preset: str = Query("day", max_length=12),
        from_: str = Query("", alias="from", pattern=r"^(\d{2}:\d{2})?$"),
        to: str = Query("", pattern=r"^(\d{2}:\d{2})?$"),
        from_marker: int | None = Query(None, ge=1, le=2_000_000_000),
        to_marker: int | None = Query(None, ge=1, le=2_000_000_000),
        sections: str = Query("", max_length=120),
        share: bool = False,
    ) -> ReportScope:
        if preset not in PRESETS:
            raise HTTPException(422, ERRORS["bad_preset"][1])
        return ReportScope(show_id, preset, from_, to, from_marker, to_marker, parse_sections(sections), share)

    def _make(scope: ReportScope, kind: str):
        """Runs in a worker thread: its own read-only connection, built and rendered there."""
        live = hub.recorder.run_id is not None
        conn = open_readonly(hub.recorder.path, app.state.report_deadline_s)
        try:
            data = build_report(conn, hub.config, scope, time.time(), "this hub", live)
            show = data.window.show
            span = ""
            if data.window.preset != "day":
                span = "-" + data.window.preset.replace("_", "")
            if kind == "zip":
                return make_zip(data), file_name(show, span, "zip"), show["id"]
            return render_html(data), file_name(show, span, "html"), show["id"]
        finally:
            conn.close()

    async def _run(scope: ReportScope, kind: str):
        if lock.locked():
            raise HTTPException(429, BUSY, headers={"Retry-After": "5"})
        async with lock:
            hub.recorder.flush()      # the last couple of seconds are on disk before we read
            t0 = time.monotonic()
            try:
                out = await asyncio.to_thread(_make, scope, kind)
            except ReportError as e:
                log.info("report refused (%s) for show %s", e.code, scope.show_id)
                raise error_for(e) from None
            except Exception as e:  # noqa: BLE001
                from ..core.report.query import is_deadline
                code = "deadline" if is_deadline(e) else None
                log.warning("report failed for show %s (%s)", scope.show_id, type(e).__name__)
                if code:
                    raise error_for(ReportError(code, "")) from None
                raise HTTPException(500, "The report could not be made.") from None
            log.info("report made: show %s, %s, %.1f s", scope.show_id, kind, time.monotonic() - t0)
            return out

    @app.get("/api/admin/report/shows", dependencies=admin_deps)
    async def report_shows():
        def read():
            conn = open_readonly(hub.recorder.path, app.state.report_deadline_s)
            try:
                return list_shows(conn, hub.config.site)
            finally:
                conn.close()
        hub.recorder.flush()
        try:
            shows = await asyncio.to_thread(read)
        except ReportError as e:
            raise error_for(e) from None
        current = hub.recorder.show_id
        return {"shows": [{"id": s["id"], "name": redact.scrub(s["name"]), "event": redact.scrub(s["event_name"]),
                           "day": s["day"], "started": s["started"], "ended": s["ended"],
                           "current": s["id"] == current} for s in reversed(shows)]}

    @app.get("/api/admin/report/markers", dependencies=admin_deps)
    async def report_markers(show_id: int = Query(ge=1, le=10_000_000)):
        def read():
            conn = open_readonly(hub.recorder.path, app.state.report_deadline_s)
            try:
                get_show(conn, show_id, hub.config.site)
                return conn.execute("SELECT id, ts, label FROM markers WHERE show_id = ? ORDER BY ts LIMIT 500",
                                    (show_id,)).fetchall()
            finally:
                conn.close()
        hub.recorder.flush()
        try:
            rows = await asyncio.to_thread(read)
        except ReportError as e:
            raise error_for(e) from None
        return {"markers": [{"id": r[0], "ts": r[1], "label": redact.scrub(r[2])[:80]} for r in rows]}

    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}

    @app.get("/api/admin/report", dependencies=admin_deps)
    async def report_html(scope: ReportScope = Depends(scope_params), download: bool = False):
        text, name, _ = await _run(scope, "html")
        disp = "attachment" if download else "inline"
        return Response(text.encode("utf-8"), media_type="text/html; charset=utf-8", headers={
            **headers, "Content-Disposition": f'{disp}; filename="{name}"',
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'"})

    @app.get("/api/admin/report/data.zip", dependencies=admin_deps)
    async def report_zip(scope: ReportScope = Depends(scope_params)):
        blob, name, _ = await _run(scope, "zip")

        def chunks():
            for i in range(0, len(blob), 64 * 1024):
                yield blob[i:i + 64 * 1024]
        return StreamingResponse(chunks(), media_type="application/zip", headers={
            **headers, "Content-Disposition": f'attachment; filename="{name}"', "Content-Length": str(len(blob))})

    @app.post("/api/admin/report/demo", dependencies=admin_deps)
    async def report_demo_day():
        if not hub.emulate:
            raise HTTPException(409, "The demo day only works in emulate mode. Nothing has been changed.")
        hub.recorder.flush()
        show_id = await asyncio.to_thread(report_demo.seed_demo_day, hub.recorder, time.time(), hub.config.site)
        hub.bus.publish("config", None)
        return {"ok": True, "show_id": show_id}
