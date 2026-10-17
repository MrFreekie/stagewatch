"""OSC actions: schedule actions and threshold actions (0.4.0).

Outbound, notify-style messages to show audio and media software (QLab, Companion ...). Never
used to control rigging, power or safety systems, and nothing here ever mutes or changes a PA.

Safeguards (house rules 1 and 2):
* opt-in: only an admin builds targets and actions;
* one **armed** switch for the whole hub, in memory only: off after every start of Stagewatch,
  never switched off by itself; every arm and disarm is logged;
* **never late**: a schedule action fires only if Stagewatch is running at its moment (within
  ``JITTER_S`` of timer jitter). One already due when Stagewatch starts, or due while it was off,
  is logged ``missed`` and never sends. Each schedule run is written once ever (DB unique index);
* threshold actions send only after the threshold has been seen clear since start-up, so an alarm
  already active at start-up sends nothing; a per-threshold, per-trigger cool-down applies;
* rate limits: ``TARGET_MAX`` sends per target in ``TARGET_WINDOW_S``, ``HUB_MAX`` per hour per hub;
* UDP to a literal private / loopback / link-local address or a ``.local`` name; never broadcast.

Every outcome goes to the action log (``action_runs``) and, briefly, to the alarm log. Errors use
fixed text, never exception text or the values that were sent.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import re
import socket
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .config import ActionArg, ActionMessage

log = logging.getLogger(__name__)

JITTER_S = 2.0           # a schedule action fires only if now - due <= this
TARGET_MAX = 5           # sends per target ...
TARGET_WINDOW_S = 10.0   # ... in this many seconds
HUB_MAX = 60             # sends per hour per hub
TEST_GAP_S = 2.0         # one test per target per this many seconds
MAX_TARGETS = 20
MAX_ARGS = 8
ADDRESS_MAX = 200
STRING_MAX = 200
NAME_MAX = 40
MESSAGE_MAX = 120        # the message argument of /stagewatch/alarm/event
ICONS = ("generic", "music", "record")
PLACEHOLDERS = ("{value}", "{level}", "{label}")
STATUS_ADDRESS = "/stagewatch/alarm/event"
TEST_ADDRESS = "/stagewatch/test"
SAFETY_WARNING = ("Actions are for show audio and media. Never use them to control rigging, hoists, "
                  "power or safety systems.")

MSG_ADDRESS = (f"The OSC address must start with / and be up to {ADDRESS_MAX} characters, with no spaces "
               "or the characters # * , ? [ ] { }")
MSG_ARGS = f"An action can have up to {MAX_ARGS} values"
MSG_ARG = (f"Each value must be a whole number, a decimal number or text up to {STRING_MAX} characters "
           "(no hidden characters)")
MSG_TARGET = "Choose a target from the list"
MSG_HOST = ("The address must be a local network IP address (like 192.168.1.20) or a name ending in .local. "
            "Broadcast and multicast addresses are not allowed")
MSG_ID = "The id can use lower-case letters, digits and - only (up to 40 characters)"
MSG_NAME = f"Names must be 1 to {NAME_MAX} characters"

_ADDR_BAD = set(" #*,?[]{}")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_LOCAL_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+local$", re.IGNORECASE)


def _hidden(s: str) -> bool:
    return any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 or c in "​‌‍  ﻿" for c in s)


def check_address(v: Any) -> str:
    if not isinstance(v, str) or not v.startswith("/") or len(v) > ADDRESS_MAX or _hidden(v) \
            or any(c in _ADDR_BAD for c in v):
        raise ValueError(MSG_ADDRESS)
    return v


def check_args(args: Any, allow_placeholders: bool = False) -> list[dict]:
    if not isinstance(args, list) or len(args) > MAX_ARGS:
        raise ValueError(MSG_ARGS)
    out = []
    for a in args:
        a = a.model_dump() if isinstance(a, BaseModel) else a
        if not isinstance(a, dict) or a.get("t") not in ("i", "f", "s"):
            raise ValueError(MSG_ARG)
        t, v = a["t"], a.get("v")
        if allow_placeholders and v in PLACEHOLDERS:
            out.append({"t": t, "v": v})
            continue
        try:
            if t == "i":
                if isinstance(v, bool) or not isinstance(v, (int, str)):
                    raise ValueError
                v = int(v)
                if not -2**31 <= v < 2**31:
                    raise ValueError
            elif t == "f":
                if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                    raise ValueError
                v = float(v)
                if not math.isfinite(v):
                    raise ValueError
            else:
                if not isinstance(v, str) or len(v) > STRING_MAX or _hidden(v):
                    raise ValueError
        except (TypeError, ValueError):
            raise ValueError(MSG_ARG) from None
        out.append({"t": t, "v": v})
    return out


def check_host(v: Any) -> str:
    """A literal loopback / private / link-local IP, or a ``*.local`` name. Never broadcast or
    multicast (the name is checked again after it resolves, at send time)."""
    if not isinstance(v, str):
        raise ValueError(MSG_HOST)
    v = v.strip()
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        if _LOCAL_RE.match(v):
            return v.lower()
        raise ValueError(MSG_HOST) from None
    if not ip_allowed(ip):
        raise ValueError(MSG_HOST)
    return str(ip)


def ip_allowed(ip) -> bool:
    if ip.is_multicast or ip.is_unspecified:
        return False
    if ip.version == 4 and (ip == ipaddress.IPv4Address("255.255.255.255") or str(ip).endswith(".255")):
        return False  # broadcast (a .255 host is refused too: almost always a subnet broadcast)
    return ip.is_loopback or ip.is_private or ip.is_link_local


# ------------------------------------------------------------------ API models
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class MessageBody(_Strict):
    target_id: str = Field(max_length=40)
    address: str = Field(max_length=ADDRESS_MAX + 50)
    args: list[ActionArg] = Field(default_factory=list, max_length=MAX_ARGS + 1)

    @field_validator("target_id")
    @classmethod
    def _target(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(MSG_TARGET)
        return v

    @field_validator("address")
    @classmethod
    def _address(cls, v: str) -> str:
        return check_address(v)

    @field_validator("args")
    @classmethod
    def _args(cls, v: list) -> list:
        return [ActionArg(**a) for a in check_args(v)]


class ThresholdMessageBody(MessageBody):
    @field_validator("args")
    @classmethod
    def _args(cls, v: list) -> list:
        return [ActionArg(**a) for a in check_args(v, allow_placeholders=True)]


class ScheduleActionBody(_Strict):
    """The ``action`` of a schedule item: for an ``action`` row ``start`` is required (its time) and
    ``end`` optional (its end time); any other item may carry either, following its own times."""
    start: MessageBody | None = None
    end: MessageBody | None = None
    icon: str = "generic"
    enabled: StrictBool = True

    @field_validator("icon")
    @classmethod
    def _icon(cls, v: str) -> str:
        return v if v in ICONS else "generic"


class TargetBody(_Strict):
    id: str = Field(max_length=40)
    name: str = Field(max_length=200)
    host: str = Field(max_length=253)
    port: int = Field(ge=1, le=65535)
    enabled: StrictBool = True

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(MSG_ID)
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= NAME_MAX or _hidden(v):
            raise ValueError(MSG_NAME)
        return v

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        return check_host(v)


class TargetsPut(_Strict):
    targets: list[TargetBody] = Field(max_length=MAX_TARGETS)

    @model_validator(mode="after")
    def _unique(self):
        ids = [t.id for t in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("Each target id can only be used once")
        return self


class ThresholdActionsBody(_Strict):
    raised: ThresholdMessageBody | None = None
    cleared: ThresholdMessageBody | None = None
    acked: ThresholdMessageBody | None = None
    cooldown_s: int = Field(60, ge=0, le=3600)


# ------------------------------------------------------------------ sending
class SendError(Exception):
    """A send failed. ``str()`` is fixed crew text."""


class UdpSender:
    """python-osc over UDP, broadcast off. Names are resolved and checked before each send."""

    def send(self, host: str, port: int, address: str, args: list) -> None:
        from pythonosc.osc_message_builder import OscMessageBuilder
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
        except OSError:
            raise SendError("Could not find that target on the network") from None
        family, _, _, _, addr = infos[0]
        if not ip_allowed(ipaddress.ip_address(addr[0])):
            raise SendError("The target's address is not a local network address")
        b = OscMessageBuilder(address=address)
        for a in args:
            b.add_arg(a, {int: "i", float: "f", str: "s"}[type(a)])
        data = b.build().dgram
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.settimeout(1.0)
                s.sendto(data, addr[:2])
        except OSError:
            raise SendError("The message could not be sent (network error)") from None


class EmulatedSender:
    """Emulate mode: no sockets. Keeps what it would have sent (tests and the demo read it)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, str, list]] = []

    def send(self, host: str, port: int, address: str, args: list) -> None:
        self.sent.append((host, port, address, list(args)))
        log.info("Emulated OSC action: %s (%d values)", address, len(args))


class RateLimiter:
    def __init__(self) -> None:
        self._target: dict[str, deque] = {}
        self._hub: deque = deque()

    def allow(self, target_id: str, now: float) -> bool:
        q = self._target.setdefault(target_id, deque())
        while q and now - q[0] >= TARGET_WINDOW_S:
            q.popleft()
        while self._hub and now - self._hub[0] >= 3600:
            self._hub.popleft()
        if len(q) >= TARGET_MAX or len(self._hub) >= HUB_MAX:
            return False
        q.append(now)
        self._hub.append(now)
        return True


def _typed(args: list[dict], fill: dict | None = None) -> list:
    out = []
    for a in args:
        t, v = a.get("t"), a.get("v")
        if fill is not None and v in PLACEHOLDERS:
            v = fill.get(v)
        try:
            out.append(int(v) if t == "i" else float(v) if t == "f" else str(v)[:STRING_MAX])
        except (TypeError, ValueError):
            out.append(0 if t == "i" else math.nan if t == "f" else "")
    return out


@dataclass
class Due:
    item_id: int
    phase: str      # "start" | "end"
    ts: float
    label: str
    msg: dict       # {target_id, address, args}


def schedule_dues(items: list[dict]) -> list[Due]:
    """Every schedule send, in time order: an action row's start (and end, if it has an end time
    and an end message); any other row's attached start / end messages at its own times."""
    out = []
    for it in items:
        doc = it.get("action")
        if not doc or not isinstance(it.get("id"), int) or not doc.get("enabled", True):
            continue
        for phase, ts in (("start", it["planned_start"]), ("end", it.get("planned_end"))):
            msg = doc.get(phase)
            if msg and ts is not None:
                label = it["title"] if it["kind"] == "action" else f"{it['title']} ({phase})"
                out.append(Due(it["id"], phase, ts, label, msg))
    out.sort(key=lambda d: (d.ts, d.item_id, d.phase))
    return out


class ActionService:
    """Owned by the hub. ``check(now)`` runs once a second; threshold hooks run from the hub."""

    def __init__(self, hub, sender=None) -> None:
        self.hub = hub
        self.sender = sender or (EmulatedSender() if hub.emulate else UdpSender())
        self.armed = False          # never saved: off after every start
        self.armed_by = ""
        self.armed_at = 0.0
        self.started = time.time()
        self.limiter = RateLimiter()
        self._tests: dict[str, float] = {}
        self._cooldown: dict[tuple[str, str], float] = {}
        self._seen_clear: set[str] = set()   # threshold ids seen live and not tripped since start
        self._live: set[str] = set()         # alarm ids raised live (after being seen clear)
        self._done: tuple[int, dict] | None = None
        self.rev = 0

    # ---- targets and arming
    def targets(self) -> dict:
        return {t.id: t for t in self.hub.config.actions.targets}

    def ensure_demo_target(self) -> bool:
        """Emulate mode only: a "Demo QLab" target (the emulated sender opens no socket)."""
        from .config import ActionTarget
        if not self.hub.emulate:
            return False
        cfg = self.hub.config.actions
        if not any(t.id == "demo-qlab" for t in cfg.targets):
            if len(cfg.targets) >= MAX_TARGETS:
                return False
            cfg.targets.append(ActionTarget(id="demo-qlab", name="Demo QLab", host="127.0.0.1", port=53000))
            self.hub.save_config()
        return True

    def set_armed(self, armed: bool, who: str = "admin") -> None:
        if armed == self.armed:
            return
        self.armed, self.armed_by, self.armed_at = armed, who, time.time()
        text = f"Actions {'armed' if armed else 'switched off'} ({who})"
        self.hub.recorder.log_alarm("actions", "armed" if armed else "disarmed", 0, text)
        log.info(text)
        self._changed()

    def _changed(self) -> None:
        self.rev += 1
        self.hub.bus.publish("actions", {"armed": self.armed, "rev": self.rev})

    def status(self) -> dict:
        return {"armed": self.armed, "armed_by": self.armed_by, "armed_at": self.armed_at or None,
                "rev": self.rev, "emulated": isinstance(self.sender, EmulatedSender)}

    # ---- sending
    def _send(self, msg: dict, now: float, fill: dict | None = None) -> tuple[str, str, str]:
        """-> (status, detail, target name). Never raises."""
        target = self.targets().get(msg.get("target_id", ""))
        if target is None:
            return "failed", "The target no longer exists", ""
        if not target.enabled:
            return "failed", "The target is switched off", target.name
        if not self.limiter.allow(target.id, now):
            return "failed", "Rate limit: too many sends", target.name
        try:
            self.sender.send(target.host, target.port, msg["address"], _typed(msg.get("args", []), fill))
        except SendError as e:
            return "failed", str(e), target.name
        except Exception:  # noqa: BLE001 - never stop the tick over one send
            log.exception("OSC action send failed")
            return "failed", "The message could not be sent", target.name
        return "sent", "emulated" if isinstance(self.sender, EmulatedSender) else "", target.name

    def _record(self, source: str, ref: str, phase: str, due: float, status: str, label: str,
                msg: dict, target: str, detail: str, who: str, fired: float | None) -> bool:
        ok = self.hub.recorder.add_action_run(source, ref, phase, due, status, fired=fired, label=label,
                                              target=target, address=msg.get("address", ""),
                                              detail=detail, who=who)
        if ok:
            words = f"{label}: {status}{f' ({detail})' if detail and detail != 'emulated' else ''}"
            self.hub.recorder.log_alarm(f"action:{source}:{ref}", "action", 0, words[:200])
            self._changed()
        return ok

    def test(self, msg: dict, who: str = "admin", now: float | None = None) -> dict:
        """Send now (works while disarmed: a deliberate admin click). One per target per 2 s."""
        now = time.time() if now is None else now
        tid = msg.get("target_id", "")
        if now - self._tests.get(tid, -1e9) < TEST_GAP_S:
            return {"status": "failed", "detail": "Wait a moment between tests"}
        self._tests[tid] = now
        status, detail, name = self._send(msg, now)
        self._record("test", tid, "test", now, status, "Test", msg, name, detail, who,
                     now if status == "sent" else None)
        return {"status": status, "detail": detail}

    # ---- schedule
    def invalidate(self) -> None:
        self._done = None

    def check(self, now: float | None = None) -> int:
        """Settle every due schedule send. Returns how many were sent."""
        now = time.time() if now is None else now
        rec = self.hub.recorder
        show_id = rec.show_id
        if self._done is None or self._done[0] != show_id:
            self._done = (show_id, rec.action_run_keys(show_id))
        done = self._done[1]
        sent = 0
        for d in schedule_dues(self.hub.schedule.items()):
            key = (str(d.item_id), d.phase)
            prev = done.get(key)
            if prev is not None:
                # Moved to a later time and never sent: it waits again for its new moment.
                if prev[1] != "sent" and d.ts != prev[0] and d.ts > now:
                    rec.reset_action_run(*key)
                    del done[key]
                    self._changed()
                continue
            if d.ts > now:
                break
            late = now - d.ts
            if d.ts <= self.started or late > JITTER_S:
                status, detail, name = "missed", ("Stagewatch was not running" if d.ts <= self.started
                                                  else "Too late to send"), ""
                name = getattr(self.targets().get(d.msg.get("target_id", "")), "name", "")
            elif not self.armed:
                status, detail = "skipped", "Actions were off"
                name = getattr(self.targets().get(d.msg.get("target_id", "")), "name", "")
            else:
                status, detail, name = self._send(d.msg, now)
            if self._record("schedule", key[0], d.phase, d.ts, status, d.label, d.msg, name, detail,
                            "schedule", now if status == "sent" else None):
                sent += status == "sent"
            done[key] = (d.ts, status)
        return sent

    def item_status(self) -> dict[tuple[str, str], tuple[float, str]]:
        rec = self.hub.recorder
        return rec.action_run_keys(rec.show_id)

    # ---- thresholds
    def threshold_seen(self, threshold_id: str, tripped: bool) -> None:
        """The hub reports each live (not stale) evaluation of a threshold with no active alarm."""
        if not tripped:
            self._seen_clear.add(threshold_id)

    def alarm_event(self, threshold, event: str, value: float | None, now: float | None = None) -> None:
        """``event``: raised | cleared | acked, for threshold ``threshold``'s alarm."""
        now = time.time() if now is None else now
        aid = threshold.id
        if event == "raised":
            if aid not in self._seen_clear:
                return  # already active at start-up: nothing assumed, nothing sent
            self._live.add(aid)
        elif aid not in self._live:
            return
        if event == "cleared":
            self._live.discard(aid)
        label = (threshold.label or threshold.entity)[:MESSAGE_MAX]
        fill = {"{value}": math.nan if value is None else round(float(value), 1),
                "{level}": threshold.level, "{label}": label}
        self._status_message(threshold, event, label, value)
        acts = getattr(threshold, "actions", None)
        msg = getattr(acts, event, None) if acts is not None else None
        if not isinstance(msg, ActionMessage) or not msg.target_id or not msg.address:
            return
        m = msg.model_dump()
        key = (aid, event)
        what = f"{label} {event}"
        if not self.armed:
            status, detail, name = "skipped", "Actions were off", ""
        elif now - self._cooldown.get(key, -1e9) < acts.cooldown_s:
            status, detail, name = "skipped", "Cool-down", ""
        else:
            self._cooldown[key] = now
            status, detail, name = self._send(m, now, fill)
        if not name:
            name = getattr(self.targets().get(m["target_id"]), "name", "")
        self._record("threshold", aid, event, now, status, what, m, name, detail, "threshold",
                     now if status == "sent" else None)

    def _status_message(self, threshold, event: str, label: str, value) -> None:
        """/stagewatch/alarm/event to OSC-out destinations that have "alarm messages" ticked."""
        dests = [d for d in self.hub.config.osc_out.destinations if d.enabled and d.alarm_messages]
        if not dests:
            return
        level = threshold.level if event != "cleared" else 0
        text = f"{label}: {value:.1f}" if isinstance(value, (int, float)) else label
        args = [f"threshold:{threshold.id}", int(level), event, text[:MESSAGE_MAX]]
        for d in dests:
            try:
                check_host(d.host)
                self.sender.send(d.host, d.port, STATUS_ADDRESS, args)
            except (ValueError, SendError):
                log.warning("Alarm message not sent to an OSC output destination")
            except Exception:  # noqa: BLE001
                log.exception("Alarm message send failed")

