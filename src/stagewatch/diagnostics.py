"""Support bundle ("Download diagnostics"): redaction and zip assembly (pure functions, stdlib only).

The bundle is meant to be sent to someone who is helping, so it must never carry a PIN hash,
an ESPHome encryption key, a password or the session signing key (secret.key is never read).
Secrets are removed three ways: by key NAME (config values), by VALUE pattern (44-character
base64 keys, pbkdf2 hashes) and by the literal secret values currently in the config.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
LOG_LINES = 2000
LOG_READ_BYTES = 2 * 1024 * 1024
REDACTED = "[redacted]"

# Key names (split into words on _ - . and camelCase) that mark a secret.
SECRET_WORDS = {"pin", "psk", "pass", "passwd", "password", "passphrase", "secret", "token", "key", "apikey",
                "hash", "credential", "credentials", "auth", "cookie", "session", "salt", "digest"}
B64_KEY_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{43}=(?![A-Za-z0-9+/=])")
PBKDF2_RE = re.compile(r"pbkdf2[_a-z0-9]*\$\d+\$[A-Za-z0-9+/=_-]+\$[A-Za-z0-9+/=_-]+", re.I)
# "password: hunter2", "noise_psk=abc", "PIN 1234" style pairs in free text (key kept, value removed).
KV_RE = re.compile(
    r"(?i)\b((?:noise_)?psk|pin(?:_hash)?|passwords?|passwd|passphrase|secret|token|api[_-]?key|encryption[_ -]?key)"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)")
_WORD_SPLIT = re.compile(r"[_\-. ]+|(?<=[a-z])(?=[A-Z])")


def is_secret_key(name: str) -> bool:
    words = {w.lower() for w in _WORD_SPLIT.split(str(name)) if w}
    return bool(words & SECRET_WORDS)


def redact_text(text: str, known: tuple[str, ...] = ()) -> str:
    """Remove secrets from free text (log lines)."""
    for s in sorted({k for k in known if isinstance(k, str) and len(k) >= 4}, key=len, reverse=True):
        text = text.replace(s, REDACTED)
    text = PBKDF2_RE.sub(REDACTED, text)
    text = B64_KEY_RE.sub(REDACTED, text)
    return KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)


def redact_config(obj, known: tuple[str, ...] = (), _key: str = ""):
    """Deep copy of a config dict with every secret replaced.  Empty values stay empty so the
    file still shows whether something was set."""
    if isinstance(obj, dict):
        return {k: redact_config(v, known, str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_config(v, known, _key) for v in obj]
    if isinstance(obj, str):
        if obj and _key and is_secret_key(_key):
            return REDACTED
        return redact_text(obj, known)
    if obj and _key and is_secret_key(_key) and not isinstance(obj, bool):
        return REDACTED
    return obj


def known_secrets(config: dict) -> tuple[str, ...]:
    """Literal secret values in a (raw, un-redacted) config dict."""
    found: list[str] = []

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, str(k))
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str) and o and key and is_secret_key(key):
            found.append(o)
    walk(config)
    return tuple(found)


def tail_lines(path: Path, n: int = LOG_LINES, max_bytes: int = LOG_READ_BYTES) -> list[str] | None:
    """Last ``n`` lines of a text file (None if it cannot be read).  Reads only the tail."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return None
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # first line is probably cut in half
    return lines[-n:]


def assemble_zip(files: dict[str, str], logs: dict[str, list[str]], known: tuple[str, ...] = (),
                 cap: int = MAX_BYTES) -> bytes:
    """Zip ``files`` (already redacted text) plus ``logs`` (redacted here).  If the result would
    exceed ``cap`` bytes, the log tails are halved until it fits (then dropped)."""
    keep = {name: [redact_text(line, known) for line in lines] for name, lines in logs.items()}
    while True:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for name, text in files.items():
                z.writestr(name, text)
            for name, lines in keep.items():
                z.writestr(f"logs/{name}", "\n".join(lines) + ("\n" if lines else ""))
        if buf.tell() <= cap or not any(keep.values()):
            return buf.getvalue()
        keep = {name: lines[len(lines) // 2:] for name, lines in keep.items()}


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n"


README = """Stagewatch diagnostics
======================
Send this file when you ask for help. It is made on the Stagewatch computer and contains:
  info.json     version, build, platform, uptime
  devices.json  device list with their statuses and details
  config.json   your settings, with every PIN hash, password and encryption key removed
  updater.json  software update status and history
  alarm_log.json  recent alarms
  logs/         the last part of Stagewatch's own log files (secrets removed)

It contains NO passwords or keys (they show as [redacted]). It does include your show and
device names and network addresses, so look through it if that matters to you.
"""
