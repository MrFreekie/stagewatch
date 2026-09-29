"""Admin PIN hashing and signed session tokens (stdlib only).

The PIN is stored as a salted PBKDF2 hash in config.yaml. Sessions are an
HMAC-signed expiry timestamp in an HttpOnly cookie; the signing key lives in
data/secret.key (created on first run, never committed). Changing the PIN
rotates the key so every existing session is logged out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

ITERATIONS = 200_000
SESSION_S = 12 * 3600
COOKIE = "stagewatch_admin"


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_pin(pin: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), base64.b64decode(salt_b64),
                                     int(iterations))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


class SessionSigner:
    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        self._key = self._load_or_create()

    def _load_or_create(self) -> bytes:
        if self.key_path.exists():
            key = self.key_path.read_bytes()
            if len(key) >= 32:
                return key
        return self.rotate()

    def rotate(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._key = key
        return key

    def issue(self, now: float | None = None) -> str:
        expiry = str(int((now or time.time()) + SESSION_S))
        sig = hmac.new(self._key, expiry.encode(), hashlib.sha256).hexdigest()
        return f"{expiry}.{sig}"

    def valid(self, token: str | None, now: float | None = None) -> bool:
        if not token or "." not in token:
            return False
        expiry, sig = token.split(".", 1)
        expected = hmac.new(self._key, expiry.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        try:
            return int(expiry) > (now or time.time())
        except ValueError:
            return False
