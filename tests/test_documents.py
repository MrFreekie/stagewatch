"""Per-event policy documents (plans/policy-documents.md, slice 1): content checks, the store,
the admin and dashboard API, visibility, sandboxed serving, and that nothing leaks elsewhere."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from stagewatch import backup as bk
from stagewatch.core import documents as dm
from stagewatch.core.hub import Hub
from stagewatch.web.server import create_app

PIN = "4711"
EVIL = {"Origin": "http://evil.example"}
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
SECRET_TITLE = "Lightning plan for Zed Quux"
SECRET_TEXT = "Call Zed Quux on 07700 900123"


# ---------------------------------------------------------------- content
def test_sniff_accepts():
    assert dm.sniff(PDF, "txt") == ("pdf", PDF)
    assert dm.sniff(b"\xef\xbb\xbf# Hi\r\nthere\r\n", "md") == ("md", b"# Hi\nthere\n")
    assert dm.sniff("a‮b​c\n".encode(), "txt") == ("txt", b"abc\n")   # bidi and zero-width removed


@pytest.mark.parametrize("data,status", [
    (b"", 422), (b"%PDF-1.4 truncated", 422), (b"PK\x03\x04docx", 415), (b"\xd0\xcf\x11\xe0doc", 415),
    (b"{\\rtf1 x}", 415), (b"a\x00b", 415), (b"a\x1bb", 415), (b"\xff\xfe\x00", 415), (b"\xef\xbb\xbf%PDF-1.4", 415),
    (b"x" * (dm.TEXT_MAX_BYTES + 1), 413), (b"%PDF-" + b"x" * dm.PDF_MAX_BYTES + b"%%EOF", 413)],
    ids=lambda v: str(v)[:16])
def test_sniff_refuses(data, status):
    with pytest.raises(dm.DocError) as e:
        dm.sniff(data, "md")
    assert e.value.status == status


def test_size_limits_exact():
    assert dm.sniff((chr(0x20AC) * (dm.TEXT_MAX_BYTES // 3)).encode(), "txt")[0] == "txt"   # 262,143 bytes, 87,381 characters
    big = b"%PDF-" + b"x" * (dm.PDF_MAX_BYTES - 10) + b"%%EOF"
    assert len(big) == dm.PDF_MAX_BYTES and dm.sniff(big, "txt")[0] == "pdf"


@pytest.mark.parametrize("bad", ["..", "../1", "1/2", "C:", "-1", "0", "1a", ""])
def test_event_ids_validated(bad):
    with pytest.raises(dm.DocError):
        dm.valid_event(bad)


@pytest.mark.parametrize("bad", ["..", "../aaaaaaaaaa", "C:aaaaaaaaaa", "aaaaaaaaaaa/", "AAAAAAAAAAAA", ""])
def test_doc_ids_validated(bad):
    with pytest.raises(dm.DocError):
        dm.valid_doc(bad)


def test_title_cleaned():
    assert dm.clean_title("  A‮\tB\n  " + "x" * 200) == ("A B " + "x" * 200)[:100]


# ---------------------------------------------------------------- store
def test_store_crash_between_file_and_index_cleans_orphan(tmp_path):
    s = dm.DocumentStore(tmp_path, lambda e: "owner")
    d = s.create(1, "Plan", "wind", [])
    s.upload(1, d["id"], PDF, "txt")
    orphan = tmp_path / "documents" / "1" / "abcdefabcdef.pdf"
    orphan.write_bytes(PDF)   # as if the index write never happened
    s.clean(1)
    assert not orphan.exists()
    assert (tmp_path / "documents" / "1" / f"{d['id']}.pdf").is_file()


def test_store_replace_changes_type_and_drops_old_file(tmp_path):
    s = dm.DocumentStore(tmp_path, lambda e: "owner")
    d = s.create(1, "Plan", "wind", [])
    s.upload(1, d["id"], PDF, "txt")
    s.upload(1, d["id"], b"# text", "md")
    files = sorted(f.name for f in (tmp_path / "documents" / "1").iterdir())
    assert files == [f"{d['id']}.md", "index.json"]
    assert s.get(1, d["id"])["ext"] == "md"


def test_store_rolled_back_event_ignores_old_folder(tmp_path):
    dm.DocumentStore(tmp_path, lambda e: "old").create(1, "Old", "wind", [])
    s = dm.DocumentStore(tmp_path, lambda e: "new")
    assert s.docs(1) == []
    s.create(1, "New", "heat", [])
    assert [x["title"] for x in s.docs(1)] == ["New"]


def test_store_limits(tmp_path, monkeypatch):
    s = dm.DocumentStore(tmp_path)
    for i in range(dm.MAX_DOCS):
        s.create(1, f"D{i}", "other", [])
    with pytest.raises(dm.DocError) as e:
        s.create(1, "One too many", "other", [])
    assert e.value.status == 409
    monkeypatch.setattr(dm, "EVENT_MAX_BYTES", len(PDF) + 5)
    ids = [x["id"] for x in s.docs(1)]
    s.upload(1, ids[0], PDF, "txt")
    with pytest.raises(dm.DocError) as e:
        s.upload(1, ids[1], PDF, "txt")
    assert e.value.status == 413


def test_damaged_index_is_not_fatal(tmp_path):
    (tmp_path / "documents" / "1").mkdir(parents=True)
    (tmp_path / "documents" / "1" / "index.json").write_text("{not json")
    assert dm.DocumentStore(tmp_path).docs(1) == []


# ---------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    hub = Hub(tmp_path, emulate=True)
    with TestClient(create_app(hub)) as c:
        c.hub = hub
        assert c.post("/api/admin/setup", json={"pin": PIN}).status_code == 200
        yield c


def base(c) -> str:
    return f"/api/admin/events/{c.hub.recorder.event_id}/documents"


def slug(c) -> str:
    return c.hub.config.dashboards[0].slug


def add(c, data=PDF, kind="pdf", dashboards=(), title="Plan", category="lightning"):
    r = c.post(base(c), json={"title": title, "category": category, "dashboards": list(dashboards)})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    r = c.put(f"{base(c)}/{doc_id}/file?kind={kind}", content=data)
    return doc_id, r


def test_auth_and_origin(client):
    client.post("/api/admin/logout")
    assert client.get(base(client)).status_code == 401
    assert client.post(base(client), json={"title": "x", "category": "wind"}).status_code == 401
    assert client.put(f"{base(client)}/abcdefabcdef/file", content=PDF).status_code == 401
    assert client.post("/api/admin/login", json={"pin": PIN}).status_code == 200
    assert client.post(base(client), json={"title": "x", "category": "wind"}, headers=EVIL).status_code == 403
    doc_id, _ = add(client)
    assert client.put(f"{base(client)}/{doc_id}/file", content=PDF, headers=EVIL).status_code == 403
    assert client.patch(f"{base(client)}/{doc_id}", json={"title": "y"}, headers=EVIL).status_code == 403
    assert client.delete(f"{base(client)}/{doc_id}", headers=EVIL).status_code == 403


def test_validation(client):
    assert client.post(base(client), json={"title": "x", "category": "weather"}).status_code == 422
    assert client.post(base(client), json={"title": "x", "category": "wind", "dashboards": ["nope"]}).status_code == 422
    assert client.post("/api/admin/events/99999/documents", json={"title": "x", "category": "wind"}).status_code == 404
    assert client.get("/api/admin/events/..%2F1/documents").status_code == 404


def test_over_cap_refused_before_reading(client):
    doc_id, _ = add(client)
    r = client.put(f"{base(client)}/{doc_id}/file", content=b"x",
                   headers={"Content-Length": str(dm.PDF_MAX_BYTES + 1)})
    assert r.status_code == 413
    r = client.put(f"{base(client)}/{doc_id}/file?kind=md", content=b"PK\x03\x04 a docx")
    assert r.status_code == 415 and "Word" in r.json()["detail"]


def test_admin_only_until_shared(client):
    doc_id, r = add(client)
    assert r.status_code == 200, r.text
    s = slug(client)
    assert client.get(f"/api/documents?dashboard={s}").json() == {"documents": []}
    hidden = client.get(f"/api/documents/{doc_id}/file?dashboard={s}")
    missing = client.get(f"/api/documents/abcdefabcdef/file?dashboard={s}")
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()
    assert client.get(f"{base(client)}/{doc_id}/file").status_code == 200   # admin can read it
    assert client.patch(f"{base(client)}/{doc_id}", json={"dashboards": [s]}).status_code == 200
    listed = client.get(f"/api/documents?dashboard={s}").json()["documents"]
    assert [d["id"] for d in listed] == [doc_id]
    assert set(listed[0]) == {"id", "title", "category", "kind", "updated"}


def test_pdf_served_sandboxed(client):
    s = slug(client)
    doc_id, _ = add(client, dashboards=[s])
    r = client.get(f"/api/documents/{doc_id}/file?dashboard={s}")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["cache-control"] == "no-store"


def test_text_reader_and_other_dashboard(client):
    client.hub.config.dashboards  # at least two default dashboards exist
    s, other = slug(client), client.hub.config.dashboards[1].slug
    doc_id, r = add(client, data=b"# Plan\n<script>x</script>", kind="md", dashboards=[s])
    assert r.status_code == 200
    j = client.get(f"/api/documents/{doc_id}?dashboard={s}").json()
    assert j["kind"] == "md" and j["text"] == "# Plan\n<script>x</script>"
    assert client.get(f"/api/documents/{doc_id}?dashboard={other}").status_code == 404
    assert client.get(f"/api/documents?dashboard={other}").json() == {"documents": []}


def test_only_current_event_is_public(client):
    s = slug(client)
    doc_id, _ = add(client, dashboards=[s])
    old = client.hub.recorder.event_id
    assert client.post("/api/admin/shows", json={"name": "Day 1", "event": "new", "event_name": "Next"}).status_code in (200, 201)
    assert client.hub.recorder.event_id != old
    assert client.get(f"/api/documents?dashboard={s}").json() == {"documents": []}
    assert client.get(f"/api/documents/{doc_id}/file?dashboard={s}").status_code == 404
    assert client.get(f"/api/admin/events/{old}/documents").json()["documents"][0]["id"] == doc_id


def test_replace_and_delete(client):
    doc_id, _ = add(client, dashboards=[slug(client)])
    r = client.put(f"{base(client)}/{doc_id}/file?kind=txt", content=b"new text")
    assert r.json()["documents"][0]["kind"] == "txt"
    assert client.delete(f"{base(client)}/{doc_id}").json()["documents"] == []
    assert client.delete(f"{base(client)}/{doc_id}").status_code == 404


def test_no_leaks_and_quiet_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    s = slug(client)
    doc_id, _ = add(client, data=SECRET_TEXT.encode(), kind="txt", dashboards=[s], title=SECRET_TITLE)
    with client.websocket_connect(f"/ws?dashboard={s}") as ws:
        snap = ws.receive_json()
        assert "Zed" not in json.dumps(snap)
        client.patch(f"{base(client)}/{doc_id}", json={"title": "Renamed"})
        for _ in range(20):
            m = ws.receive_json()
            if m["type"] == "documents_changed":
                assert m == {"type": "documents_changed"}
                break
        else:
            pytest.fail("no documents_changed message")
    for url in ("/api/snapshot", "/api/info", "/api/admin/state"):
        assert "Zed" not in client.get(url).text and doc_id not in client.get(url).text
    assert "Zed" not in caplog.text and "07700" not in caplog.text


def test_emulate_demo(client):
    r = client.post("/api/admin/documents/demo")
    assert r.status_code == 200
    kinds = sorted(d["kind"] for d in r.json()["documents"])
    assert kinds == ["md", "pdf", "txt"]
    assert len(client.get(f"/api/documents?dashboard={slug(client)}").json()["documents"]) == 2


def test_backup_covers_documents(tmp_path):
    assert "documents" in bk.DIRS


def test_long_text_refused_and_c1_controls():
    with pytest.raises(dm.DocError):
        dm.sniff(b"x\n" * dm.TEXT_MAX_LINES, "txt")
    with pytest.raises(dm.DocError):
        dm.sniff("a\u0085b".encode(), "txt")


def test_wall_never_serves_content(client):
    wall = next(d.slug for d in client.hub.config.dashboards if d.layout == "wall")
    doc_id, _ = add(client, data=b"text", kind="txt", dashboards=[wall])
    assert [d["id"] for d in client.get(f"/api/documents?dashboard={wall}").json()["documents"]] == [doc_id]
    assert client.get(f"/api/documents/{doc_id}?dashboard={wall}").status_code == 404
    assert client.get(f"/api/documents/{doc_id}/file?dashboard={wall}").status_code == 404


def test_emulate_demo_loads_once(client):
    client.post("/api/admin/documents/demo")
    assert len(client.post("/api/admin/documents/demo").json()["documents"]) == 3
