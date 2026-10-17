"""Per-event logo files (plans/custom-logo-upload.md, phase 1).

Stored as ``<data_dir>/branding/events/<event_id>/logo-<light|dark>.<png|jpg|webp>``. The type is
decided by reading the file's own header bytes (no Pillow, no trust in ``Content-Type`` or the
client's file name). Still PNG, JPEG and WebP only; animated images, GIF, HEIC and SVG are refused.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import threading
from pathlib import Path

LOGO_MAX_BYTES = 1024 * 1024
MIN_PX, MAX_PX = 32, 2048
VARIANTS = ("light", "dark")
TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
_ID = re.compile(r"^[0-9]{1,12}$")

MSG_TYPE = "Use a PNG, JPEG or WebP image."
MSG_SVG = "SVG logos are not supported yet (coming in a later version). Use a PNG, JPEG or WebP image."
MSG_ANIMATED = "Use a still image: animated logos are not supported."
MSG_BROKEN = "This file could not be read as an image. Save it again as PNG, JPEG or WebP and retry."
MSG_EMPTY = "The file is empty."
MSG_LARGE = "The logo is larger than 1 MB. Save a smaller copy and retry."
MSG_PIXELS = f"The logo must be between {MIN_PX} and {MAX_PX} pixels wide and high."


class LogoError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def valid_id(event_id) -> int:
    """The event id as an int, or LogoError 404. Checked before any path is built."""
    s = str(event_id)
    if not _ID.match(s) or int(s) < 1:
        raise LogoError(404, "No such event")
    return int(s)


def _png(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise LogoError(422, MSG_BROKEN)
    w, h = struct.unpack(">II", data[16:24])
    pos = 8
    while pos + 8 <= len(data):   # look for an APNG animation chunk before the image data
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        if kind == b"acTL":
            raise LogoError(415, MSG_ANIMATED)
        if kind in (b"IDAT", b"IEND"):
            break
        pos += 12 + length
    return w, h


def _jpeg(data: bytes) -> tuple[int, int]:
    pos, n = 2, len(data)
    while pos + 4 <= n:   # bounded: every step moves forward through the file
        if data[pos] != 0xFF:
            raise LogoError(422, MSG_BROKEN)
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0x01, *range(0xD0, 0xD8)):
            pos += 2
            continue
        if marker in (0xD9, 0xDA):
            break
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if length < 2:
            raise LogoError(422, MSG_BROKEN)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 9 > n:
                break
            h, w = struct.unpack(">HH", data[pos + 5:pos + 9])
            return w, h
        pos += 2 + length
    raise LogoError(422, MSG_BROKEN)


def _webp(data: bytes) -> tuple[int, int]:
    if len(data) < 30:
        raise LogoError(422, MSG_BROKEN)
    kind, p = data[12:16], data[20:]
    if kind == b"VP8 ":
        if p[3:6] != b"\x9d\x01\x2a":
            raise LogoError(422, MSG_BROKEN)
        w, h = struct.unpack("<HH", p[6:10])
        return w & 0x3FFF, h & 0x3FFF
    if kind == b"VP8L":
        if p[0] != 0x2F:
            raise LogoError(422, MSG_BROKEN)
        bits = struct.unpack("<I", p[1:5])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        if p[0] & 0x02:
            raise LogoError(415, MSG_ANIMATED)
        w = int.from_bytes(p[4:7], "little") + 1
        h = int.from_bytes(p[7:10], "little") + 1
        return w, h
    raise LogoError(422, MSG_BROKEN)


def sniff(data: bytes) -> tuple[str, int, int]:
    """(extension, width, height) for an acceptable logo, else LogoError."""
    if not data:
        raise LogoError(422, MSG_EMPTY)
    if len(data) > LOGO_MAX_BYTES:
        raise LogoError(413, MSG_LARGE)
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            ext, (w, h) = "png", _png(data)
        elif data.startswith(b"\xff\xd8\xff"):
            ext, (w, h) = "jpg", _jpeg(data)
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ext, (w, h) = "webp", _webp(data)
        else:
            head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
            if head.startswith(b"<") and (b"<svg" in head or head.startswith(b"<?xml")):
                raise LogoError(415, MSG_SVG)
            raise LogoError(415, MSG_TYPE)
    except (struct.error, IndexError):
        raise LogoError(422, MSG_BROKEN) from None
    if not (MIN_PX <= w <= MAX_PX and MIN_PX <= h <= MAX_PX):
        raise LogoError(422, MSG_PIXELS)
    return ext, w, h


class BrandingStore:
    """Logo files per event, with a small cache of their details."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "branding" / "events"
        self._cache: dict[int, dict] = {}
        self._lock = threading.Lock()

    def _dir(self, event_id: int) -> Path:
        return self.root / str(valid_id(event_id))

    def _scan(self, event_id: int) -> dict:
        out: dict = {v: None for v in VARIANTS}
        d = self._dir(event_id)
        for v in VARIANTS:
            for ext, ctype in TYPES.items():
                f = d / f"logo-{v}.{ext}"
                if not f.is_file():
                    continue
                try:
                    data = f.read_bytes()
                    _, w, h = sniff(data)
                except (OSError, LogoError):
                    continue   # unreadable or hand-edited: treated as no logo
                out[v] = {"path": f, "ext": ext, "content_type": ctype,
                          "sha": hashlib.sha256(data).hexdigest()[:12],
                          "width": w, "height": h, "bytes": len(data)}
                break
        return out

    def info(self, event_id: int) -> dict:
        event_id = valid_id(event_id)
        with self._lock:
            if event_id not in self._cache:
                self._cache[event_id] = self._scan(event_id)
            return self._cache[event_id]

    def has_logo(self, event_id: int) -> bool:
        return any(self.info(event_id).values())

    def save(self, event_id: int, variant: str, data: bytes) -> dict:
        if variant not in VARIANTS:
            raise LogoError(404, "No such logo")
        ext, _, _ = sniff(data)
        d = self._dir(event_id)
        with self._lock:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".logo-{variant}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, d / f"logo-{variant}.{ext}")
            for other in TYPES:
                if other != ext:
                    (d / f"logo-{variant}.{other}").unlink(missing_ok=True)
            self._cache.pop(int(event_id), None)
        return self.info(event_id)

    def delete(self, event_id: int, variant: str) -> dict:
        if variant not in VARIANTS:
            raise LogoError(404, "No such logo")
        d = self._dir(event_id)
        with self._lock:
            for ext in TYPES:
                (d / f"logo-{variant}.{ext}").unlink(missing_ok=True)
            self._cache.pop(int(event_id), None)
        return self.info(event_id)

    def copy(self, from_id: int, to_id: int) -> dict:
        """Copy both variants of one event's logo to another (independent files). A variant the
        source does not have is removed from the target."""
        src = self.info(from_id)
        if not any(src.values()):
            raise LogoError(404, "That event has no logo")
        d = self._dir(to_id)
        with self._lock:
            d.mkdir(parents=True, exist_ok=True)
            for v in VARIANTS:
                for ext in TYPES:
                    (d / f"logo-{v}.{ext}").unlink(missing_ok=True)
                if src[v]:
                    tmp = d / f".logo-{v}.tmp"
                    shutil.copyfile(src[v]["path"], tmp)
                    os.replace(tmp, d / f"logo-{v}.{src[v]['ext']}")
            self._cache.pop(int(to_id), None)
        return self.info(to_id)


def public_meta(meta: dict | None) -> dict | None:
    """Details safe to send to a browser (no file path)."""
    if not meta:
        return None
    return {k: meta[k] for k in ("content_type", "sha", "width", "height", "bytes")}
