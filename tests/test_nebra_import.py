"""Meyer Sound Nebra: the Events CSV reader, the import into a show (preview, write, repeat, undo, no double
count with the LA Network Manager log), the status report preview. Fixtures are anonymised by hand and the
expected numbers are counted by hand from tests/fixtures/nebra/events_2026-10-24.csv:

27 lines: 2 database-update lines, 4 lock losses (101 to 104) of which 101, 102, 103 lock again (104 never
does), 4 disconnects of one mixer (3 reconnect, the last stays open), 2 Hive client lines, 1 line of wording
nobody has seen, 4 disconnects of 4 DSPs within 2 s (all reconnect at 14:25:00). 12 faults, 2 never end,
5 markers (4 mixer disconnects, 1 burst of 4 DSPs). 25 rows without the database lines."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stagewatch.core import device_errors
from stagewatch.core.config import SiteConfig
from stagewatch.core.hub import Hub
from stagewatch.core.recorder import Recorder
from stagewatch.integrations.esphome import EsphomeIntegration
from stagewatch.integrations.lanwm import importer
from stagewatch.integrations.nebra import parse, status
from stagewatch.web.server import create_app
from zoneinfo import ZoneInfo

FIX = Path(__file__).parent / "fixtures" / "nebra"
EVENTS = FIX / "events_2026-10-24.csv"
STATUS = FIX / "status_2026-10-24.txt"
SITE = SiteConfig(timezone="Europe/London")      # 24 Oct 2026 is still British Summer Time: UTC + 1
DAY = "2026-10-24"
LONDON = ZoneInfo("Europe/London")


def utc(h, m, s=0, day=24) -> float:
    return datetime(2026, 10, day, h, m, s, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def text():
    return EVENTS.read_text(encoding="utf-8")


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path / "t.sqlite3")
    r.set_show_day(DAY)
    yield r
    r.close()


def plan(rec, text, name="2026-10-24_13-50_EventsReport.csv", **kw):
    return importer.plan_import(rec, SITE, fmt="nebra", text=text, file_name=name, show_id=rec.show_id, **kw)


def count(rec, table):
    return rec._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------ the reader
def test_sample_counts(text):
    r = parse.parse_text(text)
    assert len(r.events) == 27 and r.skipped == 0 and r.duplicates == 0 and r.unknown == 1
    assert r.first == datetime(2026, 10, 24, 13, 50, 0) and r.last == datetime(2026, 10, 24, 14, 25, 0)
    kinds = [e.kind for e in r.events]
    assert kinds.count("unlock") == 4 and kinds.count("lock") == 3
    assert kinds.count("disconnect") == 9 and kinds.count("reconnect") == 8
    assert kinds.count("db_start") == 1 and kinds.count("db_done") == 1 and kinds.count("other") == 1


def test_names_and_the_client_pc_name_is_never_kept(text):
    ev = parse.parse_text(text).events
    assert ev[2].name == "STAGE.AMP 101" and not ev[2].client
    hive = [e for e in ev if e.client]
    assert len(hive) == 2 and all("TESTPC" not in e.text and e.name == "Hive client" for e in hive)


def test_bad_lines_are_counted_not_fatal():
    r = parse.parse_text("Error;X has been disconnected from the Network.;2026-10-24 10:00:00\n"
                         "nonsense\nError;only two fields\nWarning;msg;2026-13-45 10:00:00\n"
                         "Warning;msg;2026-10-24 10:00:61\n\n" + "General;" + "x" * 3000 + ";2026-10-24 10:00:00\n")
    assert len(r.events) == 1 and r.skipped == 5


def test_exact_repeats_in_the_same_second_are_folded():
    line = "Warning;A.B is unlocked.;2026-10-24 10:00:00\n"
    r = parse.parse_text(line * 3)
    assert len(r.events) == 1 and r.duplicates == 2


def test_semicolons_inside_the_message_and_bom_and_crlf():
    raw = ("﻿Warning;A.B; is unlocked.;2026-10-24 10:00:00\r\nGeneral;hello;2026-10-24 10:00:01\r\n").encode("utf-8")
    r = parse.parse_text(parse.decode(raw))
    assert len(r.events) == 2 and r.events[1].kind == "other"


def test_hostile_text_is_only_text():
    hostile = ("Error;=HYPERLINK(\"http://x.invalid\")\x00\x07 <script>alert(1)</script> has been disconnected from the Network.;"
               "2026-10-24 10:00:00\n"
               "General;@SUM(1+1)\x1b[31m\x9b line two;2026-10-24 10:00:01\n")
    r = parse.parse_text(hostile)
    assert len(r.events) == 2
    for e in r.events:
        assert not any(ord(c) < 32 or ord(c) in (127, 0x2028) for c in e.text + e.name)
        assert len(e.text) <= parse.TEXT_MAX and len(e.name) <= parse.NAME_MAX
    assert r.events[0].kind == "disconnect" and "<script>" in r.events[0].name       # kept as plain text, never run
    assert r.events[1].kind == "other"


def test_a_huge_name_is_cut_short():
    r = parse.parse_text("Error;" + "N" * 150 + " has been disconnected from the Network.;2026-10-24 10:00:00\n")
    assert len(r.events[0].name) <= parse.NAME_MAX


def test_detect_format(text):
    assert importer.detect_format(text) == "nebra"
    assert importer.detect_format('<DATA>\n<ITEM Time="x"/>') == "lanwm"
    assert importer.detect_format(STATUS.read_text(encoding="utf-8")) == "nebra-status"
    assert importer.detect_format("hello") is None


# ----------------------------------------------------------------------- time
def test_clocks_back_hour_is_read_in_file_order():
    ev = parse.parse_text(
        "Warning;A.B is unlocked.;2026-10-25 00:50:00\n"        # BST: 23:50Z on the 24th
        "Error;X.Y has been disconnected from the Network.;2026-10-25 01:30:00\n"    # first pass: 00:30Z
        "Error;X.Y has been reconnected to the Network.;2026-10-25 01:45:00\n"       # first pass: 00:45Z
        "Warning;A.B is locked again.;2026-10-25 01:10:00\n"    # goes back: second pass, 01:10Z
        "Error;Z.Y has been disconnected from the Network.;2026-10-25 01:50:00\n"    # second pass: 01:50Z
        "Error;Z.Y has been reconnected to the Network.;2026-10-25 02:05:00\n").events   # GMT: 02:05Z
    from stagewatch.integrations.nebra import importer as ni
    epochs, ambiguous, missing = ni.resolve_times(ev, LONDON)
    assert epochs == [utc(23, 50), utc(0, 30, day=25), utc(0, 45, day=25), utc(1, 10, day=25),
                      utc(1, 50, day=25), utc(2, 5, day=25)]
    assert (ambiguous, missing) == (4, 0)


def test_the_hour_that_does_not_exist_is_read_before_the_change():
    from stagewatch.integrations.nebra import importer as ni
    ev = parse.parse_text("Warning;A.B is unlocked.;2026-03-29 01:30:00\n").events        # 01:00-02:00 skipped
    epochs, ambiguous, missing = ni.resolve_times(ev, LONDON)
    assert (ambiguous, missing) == (0, 1) and epochs == [datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc).timestamp()]


def test_preview_says_so_when_the_clock_change_is_in_the_file(tmp_path):
    rec = Recorder(tmp_path / "t.sqlite3")
    rec.set_show_day(DAY)
    p = plan(rec, "Error;X.Y has been disconnected from the Network.;2026-10-25 01:30:00\n"
                  "Error;X.Y has been reconnected to the Network.;2026-10-25 01:10:00\n")
    assert p.summary()["episodes"][0]["seconds"] == 2400.0          # 01:30 first pass to 01:10 second pass
    assert any("clocks went back" in n for n in p.summary()["notes"])
    rec.close()


def test_time_zone_and_offset(rec, text):
    p = plan(rec, text)
    assert p.first == utc(12, 50) and p.last == utc(13, 25)             # BST: 13:50 local is 12:50Z
    p = plan(rec, text, tz="UTC", offset_s=60)
    assert p.first == utc(13, 51)
    with pytest.raises(importer.ImportRefused):
        plan(rec, text, tz="Not/AZone")
    with pytest.raises(importer.ImportRefused):
        plan(rec, text, offset_s=90000)


# ------------------------------------------------------- pairing and open ends
def test_pairing_and_open_disconnects(rec, text):
    s = plan(rec, text).summary()
    assert s["episodes_total"] == 12 and s["open_episodes"] == 2
    mixer = [e for e in s["episodes"] if e["unit"].endswith("MIXER 1")]
    assert [e["seconds"] for e in mixer] == [20.0, 30.0, 10.0, None]
    assert mixer[-1]["note"] == "no reconnect in the file"
    amp101 = next(e for e in s["episodes"] if e["unit"].endswith("AMP 101"))
    assert amp101["seconds"] == 358.0 and amp101["level"] == "Advisory"
    amp104 = next(e for e in s["episodes"] if e["unit"].endswith("AMP 104"))
    assert amp104["seconds"] is None and amp104["note"] == "not locked again in the file"
    assert all(e["level"] == "Alert" for e in mixer)


def test_a_clear_with_no_raise_is_not_a_fault(rec):
    s = plan(rec, "Error;A.B has been reconnected to the Network.;2026-10-24 14:00:00\n").summary()
    assert s["new"] == 1 and s["episodes_total"] == 0


# ------------------------------------------------------------- the import
def test_preview_counts(rec, text):
    p = plan(rec, text)
    s = p.summary()
    assert (s["new"], s["session_left_out"], s["already_present"], s["outside_day"], s["covered_by_lanwm"]) == (25, 2, 0, 0, 0)
    assert s["markers"] == 5 and s["format"] == "nebra" and s["source"].startswith("nebra:")
    labels = [m[1] for m in p.markers]
    assert labels.count("Disconnected from the network on 4 units") == 1
    assert labels.count("Nebra unit STAGE.MIXER 1: disconnected from the network") == 4
    assert [m[0] for m in p.markers] == sorted(m[0] for m in p.markers)
    assert count(rec, "device_log") == 0                                  # a preview writes nothing
    assert any("wording Stagewatch does not know" in n for n in s["notes"])


def test_session_markers_option_adds_the_database_lines(rec, text):
    s = plan(rec, text, session_markers=True).summary()
    assert s["new"] == 27 and s["markers"] == 7 and s["session_left_out"] == 0


def test_rows_levels_and_nothing_private_stored(rec, text):
    done = importer.apply_import(rec, plan(rec, text))
    assert done["rows"] == 25 and done["markers"] == 5 and done["import_id"].startswith("nebra-")
    rows = rec._db.execute("SELECT msg, severity, doc, source, app FROM device_log").fetchall()
    assert len(rows) == 25 and {r[3] for r in rows} == {"nebra:2026-10-24_13-50_EventsReport.csv"}
    assert not any("TESTPC" in (r[0] + r[2]) for r in rows)
    disc = next(r for r in rows if r[0] == "Nebra unit STAGE.MIXER 1: disconnected from the network")
    assert disc[1] == 3 and json.loads(disc[2])["stagewatch_level"] == 2 and disc[4] == "Meyer Sound Nebra"
    unlock = next(r for r in rows if r[0] == "Nebra unit STAGE.AMP 101: stream unlocked")
    assert unlock[1] == 4 and json.loads(unlock[2])["stagewatch_level"] == 1
    other = next(r for r in rows if r[0] == "Nebra: Backup finished OK")
    assert other[1] == 6
    hive = [r[0] for r in rows if "Hive" in r[0]]
    assert hive == ["Nebra: Hive client has been disconnected from the Network.",
                    "Nebra: Hive client has been reconnected to the Network."]


def test_reimport_adds_nothing_and_overlap_adds_only_new(rec, text):
    importer.apply_import(rec, plan(rec, text))
    again = plan(rec, text, name="other-name.csv")
    assert again.summary()["new"] == 0 and again.summary()["already_present"] == 25 and again.markers == []
    with pytest.raises(importer.ImportRefused):
        importer.apply_import(rec, again)
    longer = text + "Error;LATE.UNIT has been disconnected from the Network.;2026-10-24 15:00:00\n"
    p = plan(rec, longer)
    assert p.summary()["new"] == 1 and p.summary()["already_present"] == 25 and len(p.markers) == 1


def test_outside_the_show_day_is_left_out_not_moved(rec):
    p = plan(rec, "Error;A.B has been disconnected from the Network.;2026-10-24 14:00:00\n"
                  "Error;A.B has been reconnected to the Network.;2026-10-26 14:00:00\n")
    assert p.summary()["new"] == 1 and p.summary()["outside_day"] == 1


def test_undo_removes_rows_and_markers_together(rec, text):
    done = importer.apply_import(rec, plan(rec, text))
    assert rec.undo_vendor_import(rec.show_id, done["import_id"]) == {"rows": 25, "markers": 5}
    assert count(rec, "device_log") == 0 and count(rec, "markers") == 0


def test_refusals_never_repeat_the_file(rec):
    secret = "TOPSECRET-VENUE-DETAILS"
    with pytest.raises(importer.ImportRefused) as e:
        plan(rec, secret)
    assert secret not in str(e.value) and "Nebra" in str(e.value)


def test_too_many_entries_is_refused(rec, monkeypatch):
    from stagewatch.integrations.nebra import importer as ni
    monkeypatch.setattr(ni, "MAX_ROWS", 3)
    with pytest.raises(importer.ImportRefused) as e:
        plan(rec, EVENTS.read_text(encoding="utf-8"))
    assert "too many" in str(e.value)


# ------------------------------------------------------- LA Network Manager
def la_xml(*units):
    items = "\n".join(
        f'<ITEM Time="24 Oct 2026 {t}" Level="{lv}" Unit="{u}" Category="Input" Description="AVB Audio Input Stream 1 Unlocked{c}" Acknowledged="NO"/>'
        for u, t, lv, c in units)
    return "<DATA>\n" + items + "\n</DATA>\n"


LA = la_xml(("101", "13:50:12.100", "Major", ""), ("102", "13:50:12.200", "Major", ""), ("103", "13:50:13.000", "Major", ""),
            ("101", "13:56:10.000", "Info", " CLEARED"), ("102", "13:56:10.100", "Info", " CLEARED"),
            ("103", "13:56:11.000", "Info", " CLEARED"))


def import_la(rec):
    importer.apply_import(rec, importer.plan_import(rec, SITE, fmt="lanwm", text=LA, file_name="Log.xml", show_id=rec.show_id))


def test_lock_changes_already_in_the_lanwm_log_are_skipped_and_counted(rec, text):
    import_la(rec)
    p = plan(rec, text)
    s = p.summary()
    assert s["covered_by_lanwm"] == 6 and s["new"] == 19 and s["episodes_total"] == 9      # 101-103 locks: 6 lines, 3 faults
    assert not any("AMP 101" in r[4] for r in p.rows) and any("AMP 104" in r[4] for r in p.rows)   # 104 is not in LA NM
    assert any("already covered by the LA Network Manager log" in n for n in s["notes"])
    assert s["markers"] == 5                                    # lock changes never made markers


def test_a_lock_change_more_than_10s_from_lanwm_is_kept(rec, text):
    far = la_xml(("101", "13:49:00.000", "Major", ""), ("101", "13:49:30.000", "Info", " CLEARED"))
    importer.apply_import(rec, importer.plan_import(rec, SITE, fmt="lanwm", text=far, file_name="L.xml", show_id=rec.show_id))
    assert plan(rec, text).summary()["covered_by_lanwm"] == 0


def test_device_errors_card_never_counts_the_same_unlock_twice(rec, text, tmp_path):
    # LA first, then Nebra
    import_la(rec)
    importer.apply_import(rec, plan(rec, text))
    a = device_errors.summarise(rec, [rec.show_id], {}, utc(20, 0), None)
    names_a = {r["name"] for r in a["rows"]}
    assert "LA unit 101" in names_a and "STAGE.AMP 101" not in names_a and "STAGE.AMP 104" in names_a
    # Nebra first, then LA: same answer, because the card also drops the duplicate
    rec2 = Recorder(tmp_path / "b.sqlite3")
    rec2.set_show_day(DAY)
    importer.apply_import(rec2, plan(rec2, text))
    import_la(rec2)
    b = device_errors.summarise(rec2, [rec2.show_id], {}, utc(20, 0), None)
    assert {r["name"] for r in b["rows"]} == names_a
    assert b["totals"] == a["totals"]
    rec2.close()


def test_device_errors_for_nebra_units(rec, text):
    importer.apply_import(rec, plan(rec, text))
    out = device_errors.summarise(rec, [rec.show_id], {}, utc(20, 0), None)
    rows = {r["name"]: r for r in out["rows"]}
    mixer = rows["STAGE.MIXER 1"]
    assert mixer["kind"] == "Meyer Sound (imported)" and mixer["alerts"] == 4 and mixer["flapping"] is True
    assert mixer["never_cleared"] == 1 and mixer["seconds_in_fault"] == 20 + 30 + 10
    amp = rows["STAGE.AMP 101"]
    assert amp["advisories"] == 1 and amp["seconds_in_fault"] == 358 and amp["flapping"] is False
    assert rows["STAGE.AMP 104"]["never_cleared"] == 1
    assert "Hive client" not in rows and not any("Hive" in n for n in rows)
    assert rows["RACK.DSP A"]["alerts"] == 1 and rows["RACK.DSP A"]["seconds_in_fault"] == 300


# ------------------------------------------------------------ status report
def test_status_inventory_is_plain_and_private():
    rep = status.parse_status(STATUS.read_text(encoding="utf-8"))
    ev = parse.parse_text(EVENTS.read_text(encoding="utf-8"))
    inv = status.inventory(rep, ev)
    assert (inv["version"], inv["as_of"], inv["clients"]) == ("2.6.9", "2026/10/24 21:37:00", 2)
    assert (inv["units"], inv["available"], inv["not_available"]) == (3, 2, ["STAGE.AMP 103"])
    assert inv["firmware"] == {"1.2.0": 1, "1.2.3": 2}
    assert (inv["streams"], inv["failing_total"], inv["alert_count"]) == (2, 1, 0)
    assert inv["failing_streams"][0]["code"] == "Bandwidth Exceeded"
    missing = {m["name"]: m["disconnected_at_end_of_events"] for m in inv["missing_from_report"]}
    assert missing == {"RACK.DSP A": False, "RACK.DSP B": False, "RACK.DSP C": False, "RACK.DSP D": False,
                       "STAGE.AMP 104": False, "STAGE.MIXER 1": True}
    shown = "\n".join(status.describe(inv)) + json.dumps(inv)
    for private in ("TESTSERIAL", "00:00:00:00:00", "0x000000000000", "TESTPC", "TESTSYSTEM"):
        assert private not in shown
    assert "Bandwidth Exceeded" in shown and "Available" in shown


def test_status_without_the_events_file_and_with_alerts():
    rep = status.parse_status("Nebra v.2.6.9\nSystem Status Report for system S, as of 2026/10/24 21:37:00\n"
                              "0 Connected clients\nActive alerts:\nUnit AA:BB:CC:DD:EE:FF is hot\nAVB Entities:\nAVB Connections:\n")
    inv = status.inventory(rep)
    assert inv["alert_count"] == 1 and "AA:BB" not in json.dumps(inv) and inv["missing_from_report"] == []
    assert inv["clients"] == 0 and "Every unit" not in "\n".join(status.describe(inv))


def test_status_hostile_lines_do_not_crash():
    rep = status.parse_status("AVB Entities:\n=cmd|'/C calc'\x00\nentity_name=" + "x" * 3000 + "\nstatus=\x1b[31mAvailable\n")
    assert status.inventory(rep)["lines_not_read"] >= 1


# ----------------------------------------------------------------- the API
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


def test_api_preview_write_repeat_undo(client, text):
    body = {"format": "nebra", "text": text, "file_name": "x_EventsReport.csv"}
    j = client.post("/api/admin/logs/import", json=body).json()
    assert j["dry_run"] is True and j["new"] == 25 and j["markers"] == 5 and "import_id" not in j
    done = client.post("/api/admin/logs/import", json={**body, "dry_run": False}).json()
    assert done["rows"] == 25 and done["import_id"].startswith("nebra-")
    assert client.post("/api/admin/logs/import", json={**body, "dry_run": False}).status_code == 422
    listed = client.get("/api/admin/logs/imports").json()["imports"]
    assert listed[0]["source"] == "nebra:x_EventsReport.csv"
    assert client.get("/api/admin/devices/errors").json()["rows"]
    assert client.delete(f"/api/admin/logs/imports/{done['import_id']}").json()["rows"] == 25


def test_api_size_cap_and_wrong_file(client):
    assert client.post("/api/admin/logs/import", json={"format": "nebra", "text": "x" * 5_000_001}).status_code == 422
    r = client.post("/api/admin/logs/import", json={"format": "nebra", "text": "SECRET-LINE-123"})
    assert r.status_code == 422 and "SECRET-LINE-123" not in r.text
    assert client.post("/api/admin/logs/import", json={"format": "nebra-status", "text": "x"}).status_code == 422


def test_api_needs_admin(client, text):
    client.post("/api/admin/logout")
    assert client.post("/api/admin/logs/import", json={"format": "nebra", "text": text, "dry_run": False}).status_code in (401, 403)


# --------------------------------------------------------------------- CLI
def test_cli_previews_then_writes_with_autodetect(tmp_path, capsys):
    from stagewatch.__main__ import import_log_cli
    r = Recorder(tmp_path / "stagewatch.sqlite3")
    r.set_show_day(DAY)
    r.close()
    args = [str(EVENTS), "--data-dir", str(tmp_path), "--tz", "Europe/London"]
    import_log_cli(args)                                          # no --format: found from the content
    out = capsys.readouterr().out
    assert "New: 25" in out and "markers: 5" in out and "Nothing has been imported" in out
    assert "no reconnect in the file" in out and "Note: 1 line had wording" in out
    import_log_cli(args + ["--write"])
    assert "Imported 25 entries and 5 markers (import nebra-" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        import_log_cli(args + ["--write"])


def test_cli_status_is_preview_only_and_private(tmp_path, capsys):
    from stagewatch.__main__ import import_log_cli
    import_log_cli([str(STATUS), "--format", "nebra-status", "--events", str(EVENTS)])
    out = capsys.readouterr().out
    assert "Units: 2 of 3 Available." in out and "Bandwidth Exceeded" in out and "STAGE.MIXER 1" in out
    assert "TESTSERIAL" not in out and "00:00:00" not in out and "TESTPC" not in out
    import_log_cli([str(STATUS)])                                 # found from the content too
    assert "Units: 2 of 3" in capsys.readouterr().out
    assert not (tmp_path / "stagewatch.sqlite3").exists()
    with pytest.raises(SystemExit):
        import_log_cli([str(STATUS), "--format", "nebra-status", "--write"])


def test_admin_card_offers_nebra():
    js = (Path(__file__).parents[1] / "src" / "stagewatch" / "web" / "static" / "admin.js").read_text(encoding="utf-8")
    assert 'value: "nebra"' in js and "format: formatSel.value" in js and "nebra-status" not in js


def test_status_report_values_in_square_brackets():
    """A real report writes every value as [value]; the names, firmware and 'No Error' must read cleanly."""
    from stagewatch.integrations.nebra import status
    text = "\n".join([
        "Nebra v.2.6.9",
        "System Status Report for system Test-System, as of 2026/10/24 21:37:59",
        "",
        "0 Connected clients:",
        "",
        "Active alerts:",
        "",
        "AVB Entities:",
        "entity_id=1/00-11-22-33-44-55 entity_model_id=2/[] entity_name=[AMP 101] group_name=[] serial#=[0000001] "
        "firmwareVersion=[1.2.3] status=Available #sources=0 #sinks=4 as_path_map=[0:2]",
        "entity_id=3/00-11-22-33-44-66 entity_model_id=4/[X] entity_name=[Mixer] group_name=[Net] serial#=[0000002] "
        "firmwareVersion=[2.0 (v1)] status=Available #sources=1 #sinks=1 as_path_map=[0:6]",
        "",
        "AVB Connections:",
        "Talker Location=[Net.Mixer.Output 1 (00-11-22-33-44-66,0)] Listener Location=[AMP 101.A 1 (00-11-22-33-44-55,0)] "
        "State=[Registered] Flags=[Class A] Failure Code=[No Error] VLAN=[2]",
        "Talker Location=[Net.Mixer.Output 2 (00-11-22-33-44-66,2)] Listener Location=[AMP 101.B 1 (00-11-22-33-44-55,1)] "
        "State=[Failed] Flags=[Class A] Failure Code=[Bandwidth] VLAN=[2]",
        "End of Report",
    ])
    inv = status.inventory(status.parse_status(text))
    assert inv["units"] == 2 and inv["available"] == 2
    assert inv["firmware"] == {"1.2.3": 1, "2.0 (v1)": 1}
    assert inv["failing_total"] == 1 and inv["failing_streams"][0]["code"] == "Bandwidth"
    printed = "\n".join(status.describe(inv))
    assert "0000001" not in printed and "00-11-22" not in printed
