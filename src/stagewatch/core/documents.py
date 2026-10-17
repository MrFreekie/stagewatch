"""Per-event policy and risk documents (plans/policy-documents.md, slice 1).

Stored as ``<data_dir>/documents/<event_id>/index.json`` plus ``<doc_id>.<pdf|md|txt>`` beside it.
The index sits with the files (not in config.yaml), so titles stay out of the config and
diagnostics and a downgrade cannot orphan it. File names are generated; the client's name is
never used on disk. The type is decided by content: a PDF must look like one; text must be clean
UTF-8. PDFs are not parsed or rewritten: they are served sandboxed in a new tab only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path

from ..web.branding import _write

log = logging.getLogger(__name__)

PDF_MAX_BYTES = 10 * 1024 * 1024
TEXT_MAX_BYTES = 256 * 1024
MAX_DOCS = 20
EVENT_MAX_BYTES = 40 * 1024 * 1024
TOTAL_MAX_BYTES = 200 * 1024 * 1024
TITLE_MAX = 100
CATEGORIES = ("lightning", "wind", "heat", "evacuation", "electrical", "site_rules", "medical",
              "noise", "other")
EXTS = ("pdf", "md", "txt")
FORMAT = 1
INDEX = "index.json"
_EVENT = re.compile(r"[0-9]{1,12}")
_DOC = re.compile(r"[0-9a-f]{12}")
# Bidirectional overrides and zero-width characters: removed from text so a document cannot
# show something different from what it holds.
_INVISIBLE = re.compile("[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_C0 = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")

MSG_TYPE = "Use a PDF, a Markdown (.md) file or a plain text (.txt) file. Save Word files as PDF first."
MSG_PDF = "This file could not be read as a PDF. Save it again as PDF and retry."
MSG_TEXT = "This text file has characters that cannot be shown. Save it as plain UTF-8 text and retry."
MSG_EMPTY = "The file is empty."
MSG_BUSY = "That file is being read by a screen right now. Wait a moment and try again."
MSG_LONG = "This text is too long to show on a tablet (over 100,000 characters or 2,000 lines). Split it or save it as PDF."
TEXT_MAX_CHARS, TEXT_MAX_LINES = 100_000, 2_000   # what SW.renderMarkdown shows in full
MSG_PDF_LARGE = "PDF files can be up to 10 MB. Save a smaller copy and retry."
MSG_TEXT_LARGE = "Text files can be up to 256 KB. Split it into smaller files and retry."
MSG_FULL = f"This event already has {MAX_DOCS} documents. Delete one first."
MSG_EVENT_FULL = "This event's documents would be over 40 MB. Delete or shrink one first."
MSG_TOTAL_FULL = "All documents together would be over 200 MB. Delete old events' documents first."


class DocError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def valid_event(event_id) -> int:
    s = str(event_id)
    if not _EVENT.fullmatch(s) or int(s) < 1:
        raise DocError(404, "No such event")
    return int(s)


def valid_doc(doc_id) -> str:
    s = str(doc_id)
    if not _DOC.fullmatch(s):
        raise DocError(404, "No such document")
    return s


def clean_title(text: str) -> str:
    """One line of display text: no control or invisible characters, at most TITLE_MAX long."""
    text = unicodedata.normalize("NFC", str(text))
    text = _INVISIBLE.sub("", text)
    text = "".join(" " if unicodedata.category(c)[0] in ("C", "Z") else c for c in text)
    return " ".join(text.split())[:TITLE_MAX]


def sniff(data: bytes, declared: str) -> tuple[str, bytes]:
    """(extension, bytes to store). ``declared`` is "md" or "txt" (from the file name) and only
    chooses between the two text kinds; the content decides PDF or text."""
    if not data:
        raise DocError(422, MSG_EMPTY)
    if data.startswith(b"%PDF-"):
        if len(data) > PDF_MAX_BYTES:
            raise DocError(413, MSG_PDF_LARGE)
        if b"%%EOF" not in data[-1024:]:
            raise DocError(422, MSG_PDF)
        return "pdf", data
    if len(data) > TEXT_MAX_BYTES:
        raise DocError(413, MSG_TEXT_LARGE)
    if data.startswith(b"PK\x03\x04") or data.startswith(b"\xd0\xcf\x11\xe0") or data.lstrip().startswith(b"{\\rtf"):
        raise DocError(415, MSG_TYPE)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DocError(415, MSG_TYPE) from None
    text = text.lstrip(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n")
    if _C0.search(text):
        raise DocError(415, MSG_TEXT)
    text = _INVISIBLE.sub("", text)
    if text.lstrip().startswith("%PDF-"):
        raise DocError(415, MSG_TYPE)
    if not text.strip():
        raise DocError(422, MSG_EMPTY)
    if len(text) > TEXT_MAX_CHARS or text.count(chr(10)) >= TEXT_MAX_LINES:
        raise DocError(413, MSG_LONG)
    return ("md" if declared == "md" else "txt"), text.encode("utf-8")


class DocumentStore:
    """Documents per event. ``owner_of(event_id)`` gives a string unique to that event (its
    created time), so a reused id after a database rollback never shows an older event's files."""

    def __init__(self, data_dir: Path, owner_of=None):
        self.root = Path(data_dir) / "documents"
        self.owner_of = owner_of or (lambda _id: "")
        self._lock = threading.Lock()

    def _dir(self, event_id) -> Path:
        return self.root / str(valid_event(event_id))

    # ------------------------------------------------------------ index
    def _load(self, event_id: int) -> list[dict]:
        d = self._dir(event_id)
        try:
            raw = json.loads((d / INDEX).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, dict) or raw.get("format") != FORMAT or raw.get("owner") != self.owner_of(event_id):
            return []
        out = []
        for e in raw.get("docs") or []:
            try:
                doc = {"id": valid_doc(e["id"]), "title": clean_title(e["title"]) or "Untitled",
                       "category": e["category"] if e["category"] in CATEGORIES else "other",
                       "ext": e["ext"] if e.get("ext") in EXTS else None,
                       "bytes": int(e.get("bytes") or 0), "sha256": str(e.get("sha256") or "")[:64],
                       "updated": float(e["updated"]),
                       "dashboards": [s for s in e.get("dashboards") or [] if isinstance(s, str)][:64]}
            except (KeyError, TypeError, ValueError, DocError):
                continue   # a hand-edited or damaged entry is skipped, never fatal
            if doc["ext"] and not (d / f"{doc['id']}.{doc['ext']}").is_file():
                doc.update(ext=None, bytes=0, sha256="")   # file lost: the entry waits for a new upload
            out.append(doc)
        return out

    def _save(self, event_id: int, docs: list[dict]) -> None:
        d = self._dir(event_id)
        d.mkdir(parents=True, exist_ok=True)
        body = {"format": FORMAT, "owner": self.owner_of(event_id), "docs": docs}
        _write(d / INDEX, json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8"))
        self._clean_orphans(d, docs)

    @staticmethod
    def _clean_orphans(d: Path, docs: list[dict]) -> None:
        keep = {f"{x['id']}.{x['ext']}" for x in docs if x["ext"]} | {INDEX}
        for f in d.iterdir():
            if f.is_file() and f.name not in keep and (not f.name.startswith(".") or f.name.endswith(".tmp")):
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass   # open on a screen (Windows): removed on a later save or restart

    def docs(self, event_id) -> list[dict]:
        event_id = valid_event(event_id)
        docs = self._load(event_id)
        return sorted(docs, key=lambda x: (CATEGORIES.index(x["category"]), x["title"].casefold()))

    def get(self, event_id, doc_id) -> dict:
        doc_id = valid_doc(doc_id)
        for x in self.docs(event_id):
            if x["id"] == doc_id:
                return x
        raise DocError(404, "No such document")

    def path(self, event_id, doc: dict) -> Path | None:
        return self._dir(event_id) / f"{doc['id']}.{doc['ext']}" if doc.get("ext") else None

    def clean(self, event_id) -> None:
        """Drop files the index does not name (left by a crash between file and index)."""
        event_id = valid_event(event_id)
        d = self._dir(event_id)
        if not d.is_dir():
            return
        with self._lock:
            docs = self._load(event_id)
            try:
                owned = json.loads((d / INDEX).read_text(encoding="utf-8")).get("owner") == self.owner_of(event_id)
            except (OSError, ValueError, AttributeError):
                owned = False
            if owned:
                self._clean_orphans(d, docs)

    # ------------------------------------------------------------ changes
    def create(self, event_id, title: str, category: str, dashboards: list[str]) -> dict:
        event_id = valid_event(event_id)
        if category not in CATEGORIES:
            raise DocError(422, "Choose a category from the list")
        title = clean_title(title)
        if not title:
            raise DocError(422, "Give the document a title")
        with self._lock:
            docs = self._load(event_id)
            if len(docs) >= MAX_DOCS:
                raise DocError(409, MSG_FULL)
            if not docs:
                self._claim(event_id)
            doc = {"id": secrets.token_hex(6), "title": title, "category": category, "ext": None,
                   "bytes": 0, "sha256": "", "updated": time.time(), "dashboards": list(dashboards)}
            docs.append(doc)
            self._save(event_id, docs)
        log.info("document %s created for event %s", doc["id"], event_id)
        return doc

    def _claim(self, event_id: int) -> None:
        """A folder left by a rolled-back database belongs to another event: empty it first."""
        d = self._dir(event_id)
        if d.is_dir():
            try:
                owner = json.loads((d / INDEX).read_text(encoding="utf-8")).get("owner")
            except (OSError, ValueError, AttributeError):
                owner = None
            if owner != self.owner_of(event_id):
                for f in d.iterdir():
                    if f.is_file():
                        f.unlink(missing_ok=True)

    def _total_bytes(self, skip_event: int) -> int:
        n = 0
        if self.root.is_dir():
            for d in self.root.iterdir():
                if d.is_dir() and _EVENT.fullmatch(d.name) and int(d.name) != skip_event:
                    n += sum(f.stat().st_size for f in d.iterdir() if f.is_file())
        return n

    def upload(self, event_id, doc_id, data: bytes, declared: str) -> dict:
        event_id, doc_id = valid_event(event_id), valid_doc(doc_id)
        ext, body = sniff(data, declared)
        with self._lock:
            docs = self._load(event_id)
            doc = next((x for x in docs if x["id"] == doc_id), None)
            if doc is None:
                raise DocError(404, "No such document")
            others = sum(x["bytes"] for x in docs if x["id"] != doc_id)
            if others + len(body) > EVENT_MAX_BYTES:
                raise DocError(413, MSG_EVENT_FULL)
            if self._total_bytes(event_id) + others + len(body) > TOTAL_MAX_BYTES:
                raise DocError(413, MSG_TOTAL_FULL)
            d = self._dir(event_id)
            try:
                _write(d / f"{doc_id}.{ext}", body)   # file first, then the index; orphans cleaned
            except PermissionError:
                (d / f".{doc_id}.{ext}.tmp").unlink(missing_ok=True)
                raise DocError(409, MSG_BUSY) from None
            doc.update(ext=ext, bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), updated=time.time())
            self._save(event_id, docs)
        log.info("document %s for event %s: %s file, %d bytes", doc_id, event_id, ext, len(body))
        return doc

    def patch(self, event_id, doc_id, title=None, category=None, dashboards=None) -> dict:
        event_id, doc_id = valid_event(event_id), valid_doc(doc_id)
        with self._lock:
            docs = self._load(event_id)
            doc = next((x for x in docs if x["id"] == doc_id), None)
            if doc is None:
                raise DocError(404, "No such document")
            if title is not None:
                title = clean_title(title)
                if not title:
                    raise DocError(422, "Give the document a title")
                doc["title"] = title
            if category is not None:
                if category not in CATEGORIES:
                    raise DocError(422, "Choose a category from the list")
                doc["category"] = category
            if dashboards is not None:
                doc["dashboards"] = list(dashboards)
            self._save(event_id, docs)
        return doc

    def delete(self, event_id, doc_id) -> None:
        event_id, doc_id = valid_event(event_id), valid_doc(doc_id)
        with self._lock:
            docs = self._load(event_id)
            left = [x for x in docs if x["id"] != doc_id]
            if len(left) == len(docs):
                raise DocError(404, "No such document")
            self._save(event_id, left)   # the file goes with the orphan clean-up
        log.info("document %s deleted from event %s", doc_id, event_id)


DEMO_LIGHTNING = """# Example lightning action plan (DEMO)

This is a made-up example for trying Stagewatch. It is not advice.

## Who decides
- Duty manager: Sam Example, 07700 900123
- Stage manager: Alex Sample, 07700 900456

## Levels
1. **Lightning within 30 km:** tell the stage manager. Check the radar every 10 minutes.
2. **Within 15 km:** stop work at height. Prepare to stop the show.
3. **Within 8 km:** stop the show. Clear the stage and the barrier.

Restart only when the duty manager says so, at least 30 minutes after the last strike within 15 km.
"""

DEMO_WIND = """Example wind plan (DEMO)

Made-up example. Check your own structure's wind ratings with the supplier.

Mean wind above 10 m/s: secure banners and lower soft goods.
Gusts above 15 m/s: stop the show and clear the stage.
"""


def _demo_pdf() -> bytes:
    """A tiny one-page PDF built by hand (no library), with correct cross-reference offsets."""
    text = b"BT /F1 18 Tf 72 720 Td (Example site rules \\(DEMO\\)) Tj 0 -28 Td /F1 12 Tf (Made-up example for trying Stagewatch.) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % x for x in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def demo_documents(shared: list[str]) -> list[tuple]:
    """(title, category, dashboards, kind, bytes) for emulate mode's "Load demo documents"."""
    return [("Example lightning action plan (DEMO)", "lightning", shared, "md", DEMO_LIGHTNING.encode()),
            ("Example wind plan (DEMO)", "wind", [], "txt", DEMO_WIND.encode()),
            ("Example site rules (DEMO)", "site_rules", shared, "pdf", _demo_pdf())]


def public(doc: dict) -> dict:
    """What a dashboard may see: no sizes, hashes, paths or who else can see it."""
    return {"id": doc["id"], "title": doc["title"], "category": doc["category"],
            "kind": doc["ext"], "updated": doc["updated"]}


def admin(doc: dict) -> dict:
    return {k: doc[k] for k in ("id", "title", "category", "bytes", "updated", "dashboards")} | {"kind": doc["ext"]}
