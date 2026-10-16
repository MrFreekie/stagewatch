"""LA Network Manager log: reading the file, folding bursts, and importing it into a show (preview,
write, repeat, undo). Expected numbers are counted by hand from tests/fixtures/lanwm/sample_2026-08-08.xml:
78 lines, one of them (unit 030 "Unit Disconnected") written twice within 1 ms; 42 are Session lines
(left out), 35 are Power or Network entries; 21 faults of which 9 never clear; 6 timeline markers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core.config import SiteConfig
from stagewatch.core.hub import Hub
from stagewatch.core.recorder import Recorder
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.integrations.lanwm import importer, mapping, parse
from stagewatch.web.server import create_app

SAMPLE = Path(__file__).parent / "fixtures" / "lanwm" / "sample_2026-08-08.xml"
SITE = SiteConfig(timezone="Europe/London")     # 8 Aug 2026 is British Summer Time: UTC + 1
DAY = "2026-08-08"


def utc(h, m, s, ms=0) -> float:
    return datetime(2026, 8, 8, h, m, s, ms * 1000, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def text():
    return SAMPLE.read_text(encoding="utf-8")


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "t.sqlite3")
    r.set_show_day(DAY)
    yield r
    r.close()


def plan(rec, text, **kw):
    return importer.plan_import(rec, SITE, fmt="lanwm", text=text, file_name="Log_2026_08_08_09h14m13s.xml",
                                show_id=rec.show_id, **kw)


def count(rec, table):
    return rec._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ the reader
def test_sample_counts(text):
    r = parse.parse_text(text)
    assert len(r.events) == 77 and r.duplicates == 1 and r.skipped == 0
    assert r.closed is False                       # LA NWM writes no </DATA> while a session runs
    assert r.first == datetime(2026, 8, 8, 9, 14, 13, 830000)
    assert r.last == datetime(2026, 8, 8, 22, 38, 30, 472000)


def test_raise_and_clear_share_a_key(text):
    ev = parse.parse_text(text).events
    raised = next(e for e in ev if e.unit == "033" and e.description == "Waiting for SMPS")
    cleared = next(e for e in ev if e.unit == "033" and e.description == "Waiting for SMPS CLEARED")
    assert not raised.cleared and cleared.cleared and raised.key == cleared.key == ("033", "Waiting for SMPS")
    assert raised.level == "Major" and cleared.level == "Info"


def test_a_cut_short_last_line_and_a_closing_tag():
    good = '<ITEM Time="8 Aug 2026 10:00:00.000" Level="Minor" Unit="012" Category="Power" Description="X" Acknowledged="NO"/>'
    r = parse.parse_text(f'<DATA>\n{good}\n<ITEM Time="8 Aug 2026 10:00:01.0\n')
    assert len(r.events) == 1 and r.skipped == 1 and not r.closed
    assert parse.parse_text(f"<DATA>\n{good}\n</DATA>\n").closed


@pytest.mark.parametrize("stamp, ok", [
    ("8 Aug 2026 09:14:13.830", True), ("08 Aug 2026 09:14:13", True), ("31 Feb 2026 09:14:13.000", False),
    ("8 Août 2026 09:14:13.830", False), ("8 Aug 2026 25:14:13.830", False), ("", False)])
def test_time_formats(stamp, ok):
    assert (parse.parse_time(stamp) is not None) is ok


def test_milliseconds_are_milliseconds():
    assert parse.parse_time("8 Aug 2026 09:14:13.5").microsecond == 500000
    assert parse.parse_time("8 Aug 2026 09:14:13.083").microsecond == 83000


def test_hostile_lines_are_skipped_or_neutralised():
    head = '<ITEM Time="8 Aug 2026 10:00:00.000" Level="Minor" Unit="012" Category="Power" Description='
    r = parse.parse_text("\n".join([
        head + '"' + "A" * 5000 + '" Acknowledged="NO"/>',                      # longer than any entry
        head + '"a&lt;b &xxe; &#0; ok" Acknowledged="NO"/>',                     # entities are never expanded
        head.replace('Unit="012"', 'Unit="../../etc"') + '"x" Acknowledged="NO"/>',   # not a unit id
        '<!DOCTYPE DATA [<!ENTITY a "aaaa">]>', "\x00\x01 junk", "<ITEM/>"]))
    assert r.skipped == 5 and len(r.events) == 1
    assert r.events[0].description == "a<b &xxe; ok"


def test_bytes_are_decoded_whatever_the_encoding():
    assert parse.decode("﻿abc".encode("utf-8")) == "abc"
    assert parse.decode("abc".encode("utf-16")) == "abc"
    assert parse.decode(b"caf\xe9") == "café"


# ------------------------------------------------------------- what it means
def test_faults_pair_up_and_bursts_fold(text):
    ev = [e for e in parse.parse_text(text).events if mapping.keep(e)]
    eps = mapping.episodes(ev)
    assert len(eps) == 21 and sum(1 for e in eps if e.end is None) == 9
    smps = next(e for e in eps if e.unit == "033" and e.description == "Waiting for SMPS")
    assert (smps.end - smps.start).total_seconds() == pytest.approx(5.818)      # 21:30:29.853 -> 21:30:35.671
    labels = [b.label() for b in mapping.bursts([e for e in eps if e.severity >= 2])]
    assert labels == ["LA unit 033: Waiting for SMPS",
                      "LA unit 033: SMPS controller internal error - unit service required",
                      "LA unit 029: Waiting for SMPS", "Unit Disconnected on 7 units"]


def test_session_lines_are_not_kept_but_three_can_be(text):
    ev = parse.parse_text(text).events
    assert sum(mapping.keep(e) for e in ev) == 35
    assert [e.description for e in ev if mapping.keep(e, True) and e.category == "Session"] == [
        "Going to Online Mode", "All Messages Acknowledged"]


# ------------------------------------------------------------------ importing
def test_preview_counts_and_times(rec, text):
    p = plan(rec, text)
    s = p.summary()
    assert (s["new"], s["already_present"], s["outside_day"], s["session_left_out"]) == (35, 0, 0, 42)
    assert s["markers"] == 6 and s["open_episodes"] == 9 and s["episodes_total"] == 21
    assert s["file"]["first"] == utc(8, 14, 13, 830) and s["file"]["last"] == utc(21, 38, 30, 472)
    assert s["show_day"] == {"from": utc(5, 0, 0), "to": utc(5, 0, 0) + 86400}    # 06:00 BST both days
    assert [m[1] for m in p.markers] == [
        "Waking Up All Units", "LA unit 033: Waiting for SMPS",
        "LA unit 033: SMPS controller internal error - unit service required",
        "LA unit 029: Waiting for SMPS", "Unit Disconnected on 7 units", "Standby All Units"]
    assert p.markers[1][0] == utc(20, 30, 29, 853)             # 21:30:29.853 local is 20:30 UTC
    assert count(rec, "device_log") == 0 and count(rec, "markers") == 0       # a preview writes nothing


def test_nothing_from_the_session_lines_is_stored(rec, text):
    importer.apply_import(rec, plan(rec, text))
    rows = rec._db.execute("SELECT msg, doc FROM device_log").fetchall()
    assert len(rows) == 35
    blob = " ".join(m + d for m, d in rows)
    for private in ("Card", "Ethernet", "Wi-Fi", "Ip Range", "Windows", ".nwm", "redacted"):
        assert private not in blob


def test_rows_carry_the_original_and_corrected_time(rec, text):
    importer.apply_import(rec, plan(rec, text, offset_s=-90))
    ts, dts, sev, app, msg = rec._db.execute(
        "SELECT ts, device_ts, severity, app, msg FROM device_log WHERE msg LIKE '%internal error%'").fetchone()
    assert dts == utc(20, 30, 35, 497) and ts == dts - 90
    assert (sev, app) == (2, "LA Network Manager")             # Critical is syslog "critical"
    assert msg == "LA unit 033: SMPS controller internal error - unit service required"


def test_importing_the_same_file_again_adds_nothing(rec, text):
    first = importer.apply_import(rec, plan(rec, text))
    assert (first["rows"], first["markers"]) == (35, 6)
    again = plan(rec, text)
    assert again.summary()["new"] == 0 and again.summary()["already_present"] == 35
    with pytest.raises(importer.ImportRefused):
        importer.apply_import(rec, again)
    assert count(rec, "device_log") == 35 and count(rec, "markers") == 6


def test_a_renamed_copy_of_the_file_is_still_recognised(rec, text):
    importer.apply_import(rec, plan(rec, text))
    other = importer.plan_import(rec, SITE, fmt="lanwm", text=text, file_name="copy of log.xml", show_id=rec.show_id)
    assert other.summary()["already_present"] == 35


def test_an_overlapping_file_adds_only_the_new_entries(rec, text):
    lines = text.splitlines()
    cut = next(i for i, l in enumerate(lines) if "21:30:29.853" in l)
    importer.apply_import(rec, plan(rec, "\n".join(lines[:cut])))
    p = plan(rec, text)
    # 14 entries from 21:30:29.853 on (033 x4, 029 x2, 7 disconnects, standby), so 21 are already there
    assert p.summary()["already_present"] == 21 and p.summary()["new"] == 14
    importer.apply_import(rec, p)
    assert count(rec, "device_log") == 35
    assert count(rec, "markers") == 6                          # no marker twice


def test_entries_outside_the_show_day_are_listed_not_moved(rec, text):
    p = plan(rec, text, offset_s=86400)                        # a laptop clock a day out: all land on 9 Aug
    s = p.summary()
    assert s["new"] == 0 and s["outside_day"] == 35
    with pytest.raises(importer.ImportRefused):
        importer.apply_import(rec, p)


def test_time_zone_setting_moves_the_times(rec, text):
    p = plan(rec, text, tz="UTC")
    assert p.summary()["file"]["first"] == utc(9, 14, 13, 830)


def test_undo_removes_rows_and_markers_together(rec, text):
    done = importer.apply_import(rec, plan(rec, text))
    rec.add_marker("mine", "manual")
    assert rec.vendor_imports(rec.show_id)[0]["import_id"] == done["import_id"]
    assert rec.undo_vendor_import(rec.show_id, done["import_id"]) == {"rows": 35, "markers": 6}
    assert count(rec, "device_log") == 0 and [m.label for m in rec.markers()] == ["mine"]
    assert rec.undo_vendor_import(rec.show_id, done["import_id"]) is None


def test_a_failed_write_leaves_nothing(rec, text, monkeypatch):
    p = plan(rec, text)
    p.markers.append((1.0, None, ""))                          # NOT NULL label: the marker insert fails
    with pytest.raises(Exception):
        importer.apply_import(rec, p)
    assert count(rec, "device_log") == 0 and count(rec, "markers") == 0


def test_refusals_never_repeat_the_file(rec):
    secret = "TOPSECRET-VENUE-DETAILS"
    with pytest.raises(importer.ImportRefused) as e:
        plan(rec, secret)
    assert secret not in str(e.value)
    with pytest.raises(importer.ImportRefused):
        plan(rec, "<DATA>", tz="Not/AZone")
    with pytest.raises(importer.ImportRefused):
        plan(rec, "<DATA>", offset_s=90000)


# ------------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    hub.add_integration(EsphomeIntegration(hub, emulate=True))
    hub.config.site = hub.config.site.model_copy(update={"timezone": "Europe/London"})
    hub.recorder.set_show_day(DAY)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": "1234"}).status_code == 200
        yield c


def test_api_preview_import_list_and_undo(client, text):
    body = {"text": text, "file_name": "Log_2026_08_08_09h14m13s.xml"}
    r = client.post("/api/admin/logs/import", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["dry_run"] is True and j["new"] == 35 and j["markers"] == 6 and "import_id" not in j
    assert client.get("/api/admin/logs/imports").json()["imports"] == []

    done = client.post("/api/admin/logs/import", json={**body, "dry_run": False}).json()
    assert done["rows"] == 35 and done["import_id"].startswith("lanwm-")
    listed = client.get("/api/admin/logs/imports").json()["imports"]
    assert [(i["import_id"], i["rows"], i["markers"]) for i in listed] == [(done["import_id"], 35, 6)]
    assert any(m.source == f"import:{done['import_id']}" for m in client.hub.recorder.markers())

    again = client.post("/api/admin/logs/import", json={**body, "dry_run": False})
    assert again.status_code == 422                                    # nothing new

    assert client.delete(f"/api/admin/logs/imports/{done['import_id']}").json()["rows"] == 35
    assert client.delete(f"/api/admin/logs/imports/{done['import_id']}").status_code == 404


def test_api_bad_input_is_refused_without_echo(client):
    r = client.post("/api/admin/logs/import", json={"text": "SECRET-LINE-123"})
    assert r.status_code == 422 and "SECRET-LINE-123" not in r.text
    r = client.post("/api/admin/logs/import", json={"text": "x", "tz": "Mars/Base"})
    assert r.status_code == 422 and "Mars" not in r.text
    assert client.post("/api/admin/logs/import", json={"text": "x", "show_id": 999999}).status_code == 422
    assert client.post("/api/admin/logs/import", json={"text": "x", "extra": 1}).status_code == 422
    assert client.post("/api/admin/logs/import", json={"text": "x", "offset_s": 999999}).status_code == 422


def test_api_needs_admin_and_same_origin(client, text):
    body = {"text": text, "dry_run": False}
    assert client.post("/api/admin/logs/import", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    client.post("/api/admin/logout")
    assert client.post("/api/admin/logs/import", json=body).status_code in (401, 403)
    assert client.get("/api/admin/logs/imports").status_code in (401, 403)
    assert client.delete("/api/admin/logs/imports/x").status_code in (401, 403)
    assert count(client.hub.recorder, "device_log") == 0


# ------------------------------------------------------------------------ CLI
def test_cli_previews_then_writes(tmp_path, capsys):
    from stagewatch.__main__ import import_log_cli
    r = Recorder(tmp_path / "stagewatch.sqlite3")
    r.set_show_day(DAY)
    r.close()
    args = [str(SAMPLE), "--data-dir", str(tmp_path), "--tz", "Europe/London"]
    import_log_cli(args)
    out = capsys.readouterr().out
    assert "New: 35" in out and "markers: 6" in out and "Nothing has been imported" in out
    r = Recorder(tmp_path / "stagewatch.sqlite3")
    assert count(r, "device_log") == 0
    r.close()
    import_log_cli(args + ["--write"])
    assert "Imported 35 entries and 6 markers" in capsys.readouterr().out
    r = Recorder(tmp_path / "stagewatch.sqlite3")
    assert count(r, "device_log") == 35
    r.close()
    with pytest.raises(SystemExit):                  # again: nothing new
        import_log_cli(args + ["--write"])


def test_admin_card_is_wired_to_the_endpoints():
    js = (Path(__file__).parents[1] / "src" / "stagewatch" / "web" / "static" / "admin.js").read_text(encoding="utf-8")
    assert 'foldCard(logImportCard(), "logimport"' in js
    for path in ("/api/admin/logs/import", "/api/admin/logs/imports"):
        assert path in js
    assert "dry_run: dry" in js and "confirm(" in js        # preview first, and Undo asks first
