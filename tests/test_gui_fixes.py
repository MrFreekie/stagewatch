"""Guards for the 29 Oct 2026 GUI fixes (static files only: no browser)."""
import re
from pathlib import Path

from stagewatch.core.cards import MAX_CARDS

STATIC = Path(__file__).resolve().parent.parent / "src" / "stagewatch" / "web" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_card_editor_limit_matches_server():
    m = re.search(r"const MAX = (\d+);", read("cards-editor.js"))
    assert m and int(m.group(1)) == MAX_CARDS


def test_phone_header_keeps_words_on_the_chips():
    css = read("style.css")
    assert "body:not(.layout-wall) .cs-word { position: absolute" not in css
    assert 'id="sound-short"' in read("dashboard.html")


def test_power_card_folds_phase_bars_behind_a_status_line():
    js = read("power.js")
    assert "pw-gaugefold" in js and "pw-status" in js and "All circuits OK" in js
    assert 'document.body.classList.contains("layout-wall")' in js   # the wall cannot open a fold


def test_barometer_text_is_at_least_11_units():
    css = read("style.css")
    for sel in ("text.baro-num", ".baro-word", ".baro-setlabel"):
        m = re.search(re.escape(sel) + r" \{[^}]*font-size: ([\d.]+)px", css)
        assert m and float(m.group(1)) >= 11, sel


def test_admin_alarm_log_groups_by_alarm_and_hides_silent_tail():
    js = read("admin.js")
    assert "function groupAlarmLog" in js and '"Raised", "Cleared"' in js
    assert r"/ \(silent\)$/" in js


def test_admin_long_paragraphs_sit_behind_more_detail_and_popout_closes_on_tab_change():
    js = read("admin.js")
    assert js.count('h("summary", {}, "More detail")') >= 3
    assert 'window.addEventListener("hashchange", () => dlg.remove()' in js
    assert "Stop means act now" in js


def test_admin_header_and_rme_psu_off_touch_and_link_look():
    css = read("style.css")
    assert "header.top a.title, header.top #logout { min-height: 44px" in css
    assert re.search(r"\.rme-psu-off \{[^}]*\}", css).group(0).count("underline") == 0
