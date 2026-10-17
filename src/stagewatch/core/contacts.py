"""Onsite contacts per event (plans/onsite-contacts.md).

Stored as ``<data_dir>/contacts/<event_id>.yaml`` (one file per event, so clearing an event's
contacts deletes one file) and ``contacts/roles.yaml`` (job titles used before; not personal
data). Never in config.yaml, the snapshot, /api/info, admin state, diagnostics, logs or the bus.
Log lines name event ids and counts only, never a name or a number.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..web.branding import _write

log = logging.getLogger(__name__)

FORMAT = 1
MAX_CONTACTS = 50
MAX_ROLES = 200
ROLE_MAX, NAME_MAX, PHONE_MAX, RADIO_MAX, AREA_MAX = 60, 80, 24, 32, 40
STALE_DAYS = 14
_EVENT = re.compile(r"[0-9]{1,12}")
_ID = re.compile(r"[0-9a-f]{8}")
_PHONE = re.compile(r"[+0-9 ().\-]*")
_INVISIBLE = re.compile("[­؜᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]")

DEFAULT_ROLES = (
    "Production Manager", "Production Assistant", "Stage Manager", "Assistant Stage Manager",
    "Show Caller", "Site Manager", "Event Control", "Event Safety Officer", "Head of Security",
    "Medical / First Aid", "Audio Crew Chief", "System Tech", "FOH Engineer", "Monitor Engineer",
    "RF Tech", "Backline Tech", "Lighting Designer", "Head Rigger", "Site Electrician",
    "Power / Generator Tech", "Tour Manager", "Artist Liaison", "Venue Duty Manager",
)


class ContactError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clean_text(text, limit: int) -> str:
    """One line of display text: NFC, no control or invisible characters, spaces collapsed."""
    text = _INVISIBLE.sub("", unicodedata.normalize("NFC", str(text)))
    text = "".join(" " if unicodedata.category(c)[0] in ("C", "Z") else c for c in text)
    return " ".join(text.split())[:limit]


def tel_href(phone: str) -> str:
    """``tel:`` target: a leading + and digits only."""
    digits = re.sub(r"[^0-9]", "", phone)
    return ("+" if phone.strip().startswith("+") else "") + digits


class Contact(BaseModel):
    """One position. Error messages are fixed text: the value is never echoed."""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    id: str = ""
    role: str = Field(max_length=ROLE_MAX * 2)
    name: str = Field("", max_length=NAME_MAX * 2)
    phone: str = Field("", max_length=PHONE_MAX * 2)
    show_phone: bool = False
    radio: str = Field("", max_length=RADIO_MAX * 2)
    area: str = Field("", max_length=AREA_MAX * 2)
    on_duty: bool = True

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        v = clean_text(v, ROLE_MAX)
        if not v:
            raise ValueError("Every contact needs a job title")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return clean_text(v, NAME_MAX)

    @field_validator("radio")
    @classmethod
    def _radio(cls, v):
        return clean_text(v, RADIO_MAX)

    @field_validator("area")
    @classmethod
    def _area(cls, v):
        return clean_text(v, AREA_MAX)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        v = " ".join(str(v).split())
        if not v:
            return ""
        if len(v) > PHONE_MAX or not _PHONE.fullmatch(v) or not 3 <= len(re.sub(r"[^0-9]", "", v)) <= 15:
            raise ValueError("Phone numbers can use digits, spaces, + - ( ) and . only, with 3 to 15 digits")
        return v

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return v if isinstance(v, str) and _ID.fullmatch(v) else ""


def valid_event(event_id) -> int:
    s = str(event_id)
    if not _EVENT.fullmatch(s) or int(s) < 1:
        raise ContactError(404, "No such event")
    return int(s)


def phone_allowed(enabled: bool, dash, contact: dict) -> bool:
    """All three switches: installation, dashboard and contact."""
    return bool(enabled and getattr(dash, "show_contact_phones", False) and contact.get("show_phone") and contact.get("phone"))


def contacts_for(contacts: list[dict], dash, phones_enabled: bool, with_phones: bool) -> list[dict]:
    """What one dashboard may see. ``phone`` is absent (not blank) unless allowed and
    ``with_phones``; ``has_phone`` says a number can be revealed (PIN)."""
    if dash is None or "contacts" not in (dash.cards or []):
        return []
    area = getattr(dash, "contacts_area", "") or ""
    out = []
    for c in contacts:
        if not c.get("on_duty", True):
            continue
        if area and c.get("area") and c["area"] != area:
            continue
        row = {k: c.get(k, "") for k in ("id", "role", "name", "radio", "area")}
        if phone_allowed(phones_enabled, dash, c):
            if with_phones:
                row["phone"], row["tel"] = c["phone"], tel_href(c["phone"])
            else:
                row["has_phone"] = True
        out.append(row)
    return out


class ContactStore:
    """Contacts per event. ``owner_of(event_id)`` is unique to that event (its created time), so a
    reused id after a database rollback never shows an older event's people."""

    def __init__(self, data_dir: Path, owner_of=None):
        self.root = Path(data_dir) / "contacts"
        self.owner_of = owner_of or (lambda _id: "")
        self._lock = threading.RLock()

    def _path(self, event_id) -> Path:
        return self.root / f"{valid_event(event_id)}.yaml"

    def _read(self, event_id: int) -> dict:
        path = self._path(event_id)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"contacts": [], "edited": None, "reminder_dismissed": False}
        except (UnicodeDecodeError, yaml.YAMLError):
            with self._lock:
                self._set_aside(path)
            return {"contacts": [], "edited": None, "reminder_dismissed": False}
        except OSError:   # locked by another program for a moment: never set a good file aside
            return {"contacts": [], "edited": None, "reminder_dismissed": False}
        if not isinstance(raw, dict) or raw.get("format") != FORMAT or str(raw.get("owner", "")) != self.owner_of(event_id):
            return {"contacts": [], "edited": None, "reminder_dismissed": False}
        out = []
        for e in raw.get("contacts") or []:
            try:
                c = Contact.model_validate(e).model_dump()
            except ValidationError:
                continue   # a hand-edited entry that does not validate is skipped, never fatal
            if not c["id"] or any(x["id"] == c["id"] for x in out):
                c["id"] = secrets.token_hex(4)
            out.append(c)
            if len(out) >= MAX_CONTACTS:
                break
        edited = raw.get("edited")
        return {"contacts": out, "edited": float(edited) if isinstance(edited, (int, float)) else None,
                "reminder_dismissed": raw.get("reminder_dismissed") is True}

    @staticmethod
    def _set_aside(path: Path) -> None:
        if not path.exists():
            return
        try:
            path.replace(path.with_name(f"{path.stem}.invalid.yaml"))
            log.warning("contacts file for event %s could not be read; set aside", path.stem)
        except OSError:
            pass

    def _write(self, event_id: int, state: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        body = {"format": FORMAT, "owner": self.owner_of(event_id), "edited": state["edited"],
                "reminder_dismissed": state["reminder_dismissed"], "contacts": state["contacts"]}
        _write(self._path(event_id), yaml.safe_dump(body, allow_unicode=True, sort_keys=False).encode("utf-8"))

    # ------------------------------------------------------------ reads
    def state(self, event_id) -> dict:
        return self._read(valid_event(event_id))

    def contacts(self, event_id) -> list[dict]:
        return self.state(event_id)["contacts"]

    def has_files(self, event_id) -> bool:
        """Any file for this event, readable or not (so Clear can always reach it)."""
        stem = str(valid_event(event_id))
        return self.root.is_dir() and any(f.name == f"{stem}.yaml" or f.name.startswith(f"{stem}.invalid")
                                          for f in self.root.iterdir())

    def stored_events(self) -> list[int]:
        """Event ids with a contacts file holding at least one contact."""
        if not self.root.is_dir():
            return []
        ids = []
        for f in self.root.glob("*.yaml"):
            if _EVENT.fullmatch(f.stem) and self.contacts(int(f.stem)):
                ids.append(int(f.stem))
        return sorted(ids)

    # ------------------------------------------------------------ changes
    def replace(self, event_id, contacts: list[Contact]) -> list[dict]:
        event_id = valid_event(event_id)
        if len(contacts) > MAX_CONTACTS:
            raise ContactError(422, f"Up to {MAX_CONTACTS} contacts per event")
        with self._lock:
            state = self._read(event_id)
            known = {c["id"] for c in state["contacts"]}
            rows, seen = [], set()
            for c in contacts:
                row = c.model_dump()
                if row["id"] not in known or row["id"] in seen:
                    row["id"] = secrets.token_hex(4)   # ids are the server's, never the client's
                seen.add(row["id"])
                rows.append(row)
            if not rows:
                self._delete_files(event_id)
                return []
            state.update(contacts=rows, edited=time.time())
            self._write(event_id, state)
            self.add_roles([r["role"] for r in rows])
        log.info("contacts saved for event %s: %d", event_id, len(rows))
        return rows

    def clear(self, event_id) -> None:
        event_id = valid_event(event_id)
        with self._lock:
            self._delete_files(event_id)
        log.info("contacts cleared for event %s", event_id)

    def _delete_files(self, event_id: int) -> None:
        if not self.root.is_dir():
            return
        stem = str(event_id)
        for f in self.root.iterdir():
            n = f.name
            if f.is_file() and (n == f"{stem}.yaml" or n == f".{stem}.yaml.tmp"
                                or (n.startswith(f"{stem}.invalid") and n.endswith(".yaml"))):
                f.unlink(missing_ok=True)

    def dismiss_reminder(self, event_id) -> None:
        event_id = valid_event(event_id)
        with self._lock:
            state = self._read(event_id)
            if state["contacts"]:
                state["reminder_dismissed"] = True
                self._write(event_id, state)

    def copy(self, src, dst) -> list[dict]:
        rows = [Contact.model_validate({**c, "id": ""}) for c in self.contacts(src)]
        if not rows:
            raise ContactError(404, "That event has no contacts to copy")
        if self.contacts(dst):
            raise ContactError(409, "This event already has contacts. Clear them first, then copy.")
        return self.replace(dst, rows)

    # ------------------------------------------------------------ job titles
    def roles(self) -> list[str]:
        try:
            raw = yaml.safe_load((self.root / "roles.yaml").read_text(encoding="utf-8"))
            custom = [clean_text(r, ROLE_MAX) for r in (raw or {}).get("roles") or [] if isinstance(r, str)]
        except (OSError, ValueError, yaml.YAMLError, AttributeError):
            custom = []
        return list(dict.fromkeys([*DEFAULT_ROLES, *(r for r in custom if r)]))

    def add_roles(self, roles: list[str]) -> None:
        have = self.roles()
        new = [r for r in dict.fromkeys(roles) if r and r not in have]
        if not new:
            return
        custom = [r for r in have if r not in DEFAULT_ROLES] + new
        self.root.mkdir(parents=True, exist_ok=True)
        _write(self.root / "roles.yaml", yaml.safe_dump({"format": FORMAT, "roles": custom[-MAX_ROLES:]},
                                                        allow_unicode=True).encode("utf-8"))


DEMO = [
    {"role": "Production Manager (DEMO)", "name": "Alex Demo", "phone": "07700 900123", "show_phone": True,
     "radio": "Ch 1 / Production", "area": ""},
    {"role": "Stage Manager (DEMO)", "name": "Sam Example", "phone": "07700 900456", "show_phone": True,
     "radio": "Ch 2 / Stage", "area": ""},
    {"role": "Head Rigger (DEMO)", "name": "Jo Placeholder", "phone": "+1 202 555 0142", "show_phone": False,
     "radio": "Ch 4", "area": ""},
    {"role": "Audio Crew Chief (DEMO)", "name": "Chris Sample", "phone": "07700 900789", "show_phone": True,
     "radio": "Ch 3 / Audio", "area": ""},
    {"role": "Event Safety Officer (DEMO)", "name": "Pat Tryout", "phone": "07700 900321", "show_phone": True,
     "radio": "Ch 1", "area": "", "on_duty": False},
]
