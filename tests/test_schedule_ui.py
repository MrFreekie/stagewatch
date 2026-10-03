"""Schedule UI (WP8): the dashboard's own NOW / NEXT / CURFEW maths must agree with
core/schedule.py, and the card and admin editor follow the house rules (static checks)."""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from stagewatch.core import schedule as sched

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "stagewatch" / "web" / "static"
SCRIPT = ROOT / "tests" / "js" / "schedule_test.js"

T = 1_790_000_000.0
M = 60.0


def _item(i, kind, start, end=None, stage=""):
    return {"id": i, "sort": i, "stage": stage, "kind": kind, "title": f"Item {i}",
            "planned_start": start, "planned_end": end, "setlist": ""}


DAY_ITEMS = [
    _item(1, "doors", T),
    _item(2, "act", T + 60 * M, T + 105 * M, "Main"),
    _item(3, "act", T + 60 * M, T + 120 * M, "Second"),
    _item(4, "changeover", T + 105 * M, T + 135 * M, "Main"),
    _item(5, "act", T + 135 * M, T + 225 * M, "Main"),
    _item(6, "curfew", T + 270 * M),
    _item(7, "curfew", T + 300 * M, None, "Second"),
]


def _random_day(rng: random.Random, n: int) -> list[dict]:
    """Overlaps, open ends, equal starts, several curfews and stages: the awkward cases."""
    items = []
    for i in range(n):
        kind = rng.choice(["act", "act", "act", "changeover", "doors", "other", "curfew", "load_out", "load_out",
                           "soundcheck", "venue_access", "load_in", "crew_call"])
        start = T + rng.randrange(0, 24) * 15 * M
        end = None if kind == "curfew" or rng.random() < 0.35 else start + rng.randrange(1, 8) * 15 * M
        items.append(_item(i + 1, kind, start, end, rng.choice(["", "", "Main", "main", "Second"])))
    # the list order is the server's order (by start, then sort): the client breaks ties by it
    items.sort(key=sched.sort_key)
    for k, it in enumerate(items):
        it["sort"] = k
    return items


def _cases() -> list[dict]:
    cases = []

    def add(name, items, now, stage):
        nn = sched.now_next(items, now, stage)
        ids = {k: (nn[k]["id"] if nn[k] else None) for k in ("current", "next", "curfew")}
        public = [{k: v for k, v in it.items() if k != "sort"} for it in items]
        cases.append({"name": name, "items": public, "now": now, "stage": stage,
                      "expect": {"state": nn["state"], "current_id": ids["current"], "next_id": ids["next"],
                                 "curfew_id": ids["curfew"], "seconds_to_curfew": nn["seconds_to_curfew"]}})

    for minute in range(-40, 421, 5):
        for stage in ("", "Main", "second", "Other"):
            add(f"day {minute} {stage!r}", DAY_ITEMS, T + minute * M, stage)
    # exact boundaries (start, end, curfew minute)
    for it in DAY_ITEMS:
        for t in (it["planned_start"], it["planned_end"]):
            if t is not None:
                for d in (-1, 0, 1):
                    add(f"edge {it['id']} {d}", DAY_ITEMS, t + d, "Main")
    # a Load Out after the curfew (23:00 curfew, Load Out 23:15-01:00, and an open-ended one)
    for label, load_out_end in (("closed", T + 180 * M), ("open", None)):
        items = [_item(1, "act", T, T + 60 * M), _item(2, "curfew", T + 120 * M),
                 _item(3, "load_out", T + 135 * M, load_out_end)]
        for minute in range(-10, 260, 5):
            add(f"loadout {label} {minute}", items, T + minute * M, "")
        for d in (-1, 0, 1):
            add(f"loadout {label} edge {d}", items, T + 135 * M + d, "")
    rng = random.Random(20261002)
    for n in range(200):
        items = _random_day(rng, rng.randrange(1, 12))
        for _ in range(4):
            add(f"random {n}", items, T + rng.randrange(-30, 400) * M + rng.choice([0, 0.5, 59]),
                rng.choice(["", "Main", "SECOND", "Nowhere"]))
    return cases


def test_client_now_next_matches_core_schedule(tmp_path):
    """tests/js/schedule_test.js: its own cases (the same as test_schedule.py), then every case
    here computed by core/schedule.py now_next, compared one by one."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    cases = _cases()
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    r = subprocess.run([node, str(SCRIPT), str(path)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr
    assert f"{len(cases)}/" in r.stdout or "passed" in r.stdout


# ------------------------------------------------------------- static checks
def _js(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_dashboard_schedule_card_is_wired_and_gated():
    js = _js("dashboard.js")
    assert "render: renderSchedule, empty: () => schedItems().length === 0" in js
    # one ticking timer, started only by renderSchedule once the card has items; it stops itself
    assert js.count("setInterval(tickSchedule, 1000)") == 1
    assert 'if (!ui || !has("schedule")) { stopScheduleTimer(); return; }' in js
    assert 'if (!has("schedule")) stopScheduleTimer();' in js
    # items are fetched (never pushed over the feed), only for an assigned card, by revision
    assert 'if (!has("schedule") || !state.scheduleMeta) return;' in js
    assert "/api/schedule${stage ? `?stage=${encodeURIComponent(stage)}` : \"\"}" in js
    # setlists: the safe renderer, links left off
    assert "SW.renderMarkdown(item.setlist)" in js and "links: true" not in js
    # times from SW.fmtTime only
    assert "toLocale" not in js and "Intl." not in js


def test_dashboard_schedule_updates_in_place():
    js = _js("dashboard.js")
    tick = re.search(r"function tickSchedule\(\) \{(.*?)\n  \}\n", js, re.S).group(1)
    # the per-second tick changes text and classes only: no rebuilds
    for banned in ("renderSchedule(", "card.replaceChildren", "innerHTML", "h(\"li\""):
        assert banned not in tick, banned
    assert "setText(" in tick and "setClass(" in tick


def test_curfew_levels_are_colour_and_text():
    js = _js("dashboard.js")
    assert '"15 MIN WARNING"' in js and '"5 MIN WARNING"' in js and '"PAST CURFEW"' in js
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    sched_css = "\n".join(line for line in css.splitlines() if ".sched" in line)
    assert ".sched-block.lvl-warn { border-color: var(--warn); }" in sched_css
    assert ".sched-block.lvl-alert { border-color: var(--alert); }" in sched_css
    # red only for up to 30 minutes after the curfew (lvl-past); later it is calm (lvl-done)
    for line in sched_css.splitlines():
        if "var(--stop)" in line:
            assert "lvl-past" in line, line
    assert "lvl-done" not in "\n".join(ln for ln in sched_css.splitlines() if "var(--stop)" in ln)
    # NEXT: amber with text for the last 5 minutes
    assert '"STARTS IN 5 MIN"' in js
    assert 'lvl-${nlvl}' in js
    # wall: large enough for 5 m; phone: the strip expands
    assert "body.layout-wall .sched-count { font-size: 64px; }" in css
    assert "body.layout-wall .sched-more { display: none; }" in css
    assert "body.layout-phone #schedule-card.sched-open .sched-body { display: block; }" in css
    assert '"aria-expanded"' in js


def test_old_schedule_note_and_wall_hiding():
    js = _js("dashboard.js")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "Yesterday's schedule (${SW.fmtDay(scheduleDay())}). Start the next day in Admin." in js
    assert 'classList.toggle("sched-stale", old)' in js
    # wall: the whole card goes; tablet and phone: only the note stays
    assert "body.layout-wall #schedule-card.sched-stale { display: none !important; }" in css
    assert "#schedule-card.sched-stale .sched-body, #schedule-card.sched-stale .sched-strip { display: none !important; }" in css
    assert '"Curfew was ' in js.replace("`Curfew was", '"Curfew was') and '"Finished"' in js


def test_admin_schedule_editor_rules():
    js = _js("schedule-editor.js")
    assert "function scheduleCard()" in js and "app.replaceChildren(scheduleCard())" in js
    # save sends the show and revision it loaded, and existing items' ids and dates unchanged
    assert "show_id: sd.showId, revision: sd.revision" in js
    assert "if (r.id) o.id = r.id;" in js and "if (r.date) o.date = r.date;" in js
    # preview first, then the rows are merged and saved with a PUT
    assert "dry_run: true" in js and "dry_run: false" not in js
    assert "The schedule was changed elsewhere. Reload to see it, then make your change again." in js
    assert "Visible to anyone on the show network." in js
    # the poll never rebuilds over typed text
    assert 'matches("input,select,textarea")' in js
    assert "if (!sd.dirty && !isEditing()) { await loadSchedule(); rerenderSchedule(); }" in js


def test_schedule_messages_are_known_server_texts():
    """The admin page maps pydantic's own messages to these; keep them in step with the server."""
    js = _js("schedule-editor.js")
    assert sched.MSG_TIME in js and sched.MSG_END_TIME in js
    assert sched.MSG_CHANGED in js


def test_failed_schedule_fetch_does_not_retry_in_a_loop():
    # Security review: a failed fetch kept the old key and re-called syncSchedule() at once, so a
    # dropped network made every dashboard hammer /api/schedule. Retry only after a success.
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "if (ok && schedKey(state.scheduleMeta) !== sched.loadedKey) syncSchedule();" in js
    assert "if (sched.loadedKey && schedKey(state.scheduleMeta) !== sched.loadedKey)" not in js


# ------------------------------------------------- the Schedule page (moved out of Admin)
def test_admin_next_allow_list_runs_in_node():
    """tests/js/admin_next_test.js: ?next= is accepted only when it is exactly /schedule."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "js" / "admin_next_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr


def test_admin_login_uses_only_the_allow_listed_next():
    admin = _js("admin.js")
    assert "SW.adminNext(location.search)" in admin and "location.assign(next)" in admin
    # the login code never reads the query string itself, so nothing bypasses the allow-list
    assert "location.search" not in admin.replace("SW.adminNext(location.search)", "")
    assert 'SW.ADMIN_NEXT = ["/schedule"];' in _js("common.js")
    # the Schedule page sends a logged-out user to that exact URL
    assert 'location.replace("/admin?next=/schedule")' in _js("schedule-editor.js")


def test_admin_has_a_schedule_summary_and_no_editor():
    admin = _js("admin.js")
    body = admin[admin.index("function scheduleCard()"):admin.index("function catalogCard()")]
    assert "SW.scheduleNowNext(" in body
    assert "No schedule for this day yet." in body
    assert 'SW.linkButton("Open schedule editor", "/schedule", true)' in body
    for editor_only in ("saveSchedule", "scheduleRowEl", "dry_run", "/api/admin/schedule", "Save schedule", "sdRow"):
        assert editor_only not in admin, editor_only
    # the link is a real same-tab link at least 44 px tall
    common = _js("common.js")
    link = common[common.index("SW.linkButton = function"):common.index("// Where Admin sends you")]
    assert 'SW.h("a", { href: href' in link and "min-height:44px" in link
    assert "target" not in link


def test_schedule_page_is_static_html_with_a_way_back():
    html = (STATIC / "schedule.html").read_text(encoding="utf-8")
    assert 'href="/admin"' in html and 'id="day"' in html
    assert re.findall(r'<script[^>]*\ssrc="([^"]+)"', html) == [
        "/static/compat.js", "/static/common.js", "/static/schedule-export.js", "/static/schedule-editor.js"]
    # print view: built with SW.renderMarkdown (links off), shown only when printing
    js = _js("schedule-editor.js")
    assert "SW.renderMarkdown(r.setlist)" in js and "window.print()" in js and "SW.scheduleCsv(sd.rows)" in js
    assert "@media print" in html and "#print-view { display: none; }" in html
    assert "header.top, .site-footer, #app" in html
    assert "SW.fmtDay(show.day)" in _js("schedule-editor.js")


def test_editor_kinds_come_from_the_server_with_a_title_case_fallback():
    js = _js("schedule-editor.js")
    assert "admin.schedule_limits.kinds" in js and "schedKinds()" in js
    for k in ("venue_access", "load_in", "crew_call", "soundcheck", "doors", "act", "changeover", "curfew", "load_out", "other"):
        assert f"{k}:" in js, k
    assert 'split("_")' in js and "toUpperCase()" in js
    assert "10:00 Load In" in js and "23:15 Load Out" in js
