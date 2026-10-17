"""Sound level limits (0.4.0): admin-entered limits shown beside the values Smaart reports.

Stagewatch does no sound-level maths here either. Each limit is tied to one of the recorded Smaart
values (input source + Smaart's own metric name, e.g. "LAeq 15") and compared with the last value
received, exactly as received: ``over = value > limit`` (strictly above, no tolerance, no rounding).
A value that is stale or not available is "no reading", never "OK" and never zero.

Limits belong to the Event (a default for every day) with an optional per-show override. Each save
is a new row in ``spl_limit_revisions`` (kept for audit). Times are site wall-clock "HH:MM" on the
show day (core/sitetime.py: a time before the day rollover is after midnight).

``SplLimitService`` (ticked by the Hub) adds markers only as things happen live: a limit change
when its boundary passes (skipped if more than 5 minutes late, so nothing is back-filled after
downtime), the first value over a limit, and edits. Its alarms are silent level 1 notices.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import sitetime, spl

log = logging.getLogger(__name__)

MAX_LIMITS = 40
VALUE_MIN_DB, VALUE_MAX_DB = 40.0, 150.0
LABEL_MAX = 60
SET_BY_MAX = 120
REFERENCE_MAX = 200
NOTES_MAX = 500
ENTERED_BY_MAX = 120
CHANGE_NOTE_MAX = 200

LATE_S = 300.0          # a boundary noticed more than 5 minutes late gets no marker (no back-fill)
REARM_S = 120.0         # an over-limit marker re-arms after 2 minutes continuously at or under
CLEAR_AFTER_S = 120.0   # a change warning clears 2 minutes after the boundary
MARKER_SOURCE = "spl_limits"

DISCLAIMER = ("Reference only. The licence is authoritative. Stagewatch shows Smaart's figures and is "
              "not a compliance record.")
PERIOD_NOTE = ("The Leq period is whatever Smaart is set to; Stagewatch cannot check it.")


def _text(v: object, cap: int) -> str:
    if not isinstance(v, str):
        raise ValueError("must be text")
    clean = spl._clean_text(v, cap + 1)
    if len(clean) > cap:
        raise ValueError("too long")
    return clean


def _hhmm(v: object) -> str:
    if not isinstance(v, str):
        raise ValueError("time must be HH:MM")
    v = v.strip()
    if v:
        sitetime._parse_hhmm(v)   # raises ValueError
    return v


class SplLimit(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str = Field(pattern=r"^[a-z0-9]{1,12}$")
    source: str = Field("", max_length=spl.INPUT_NAME_MAX)
    metric: str = Field(min_length=1, max_length=spl.METRIC_NAME_MAX)
    kind: Literal["limit", "guidance"] = "limit"
    value_db: float = Field(ge=VALUE_MIN_DB, le=VALUE_MAX_DB, allow_inf_nan=False)
    start: str = ""
    end: str = ""
    label: str = Field("", max_length=LABEL_MAX)

    @field_validator("value_db", mode="before")
    @classmethod
    def _real(cls, v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("must be a number")
        return v

    @field_validator("source", "metric", "label", mode="before")
    @classmethod
    def _clean(cls, v, info):
        caps = {"source": spl.INPUT_NAME_MAX, "metric": spl.METRIC_NAME_MAX, "label": LABEL_MAX}
        return _text(v, caps[info.field_name])

    @field_validator("start", "end", mode="before")
    @classmethod
    def _time(cls, v):
        return _hhmm(v)

    @property
    def group(self) -> tuple[str, str, str]:
        return (self.source, self.metric, self.kind)


class SplLimitSet(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    limits: list[SplLimit] = Field(default_factory=list, max_length=MAX_LIMITS)
    set_by: str = Field("", max_length=SET_BY_MAX)
    reference: str = Field("", max_length=REFERENCE_MAX)
    public_notes: str = Field("", max_length=NOTES_MAX)

    @field_validator("set_by", "reference", "public_notes", mode="before")
    @classmethod
    def _clean(cls, v, info):
        caps = {"set_by": SET_BY_MAX, "reference": REFERENCE_MAX, "public_notes": NOTES_MAX}
        return _text(v, caps[info.field_name])


# ------------------------------------------------------------------ show-day windows
def _minutes(hhmm: str, roll: int) -> int:
    t = sitetime._parse_hhmm(hhmm)
    m = t.hour * 60 + t.minute
    return m + 1440 if m < roll else m


def _roll_min(site) -> int:
    r = sitetime._rollover(site)
    return r.hour * 60 + r.minute


def span(limit: SplLimit, site) -> tuple[int, int]:
    """The window in minutes from midnight of the show day ("" = the day's start or end)."""
    roll = _roll_min(site)
    a = _minutes(limit.start, roll) if limit.start else roll
    b = _minutes(limit.end, roll) if limit.end else roll + 1440
    return a, b


def set_errors(s: SplLimitSet, site) -> str | None:
    """Fixed text naming rows (1-based) for a set that cannot be saved, or None."""
    ids = [lim.id for lim in s.limits]
    if len(set(ids)) != len(ids):
        return "Each limit needs its own id"
    spans = []
    for i, lim in enumerate(s.limits, start=1):
        a, b = span(lim, site)
        if a >= b:
            return f"Row {i}: the end must be after the start within the show day"
        spans.append((i, lim.group, a, b))
    for x in range(len(spans)):
        for y in range(x + 1, len(spans)):
            i, g, a, b = spans[x]
            j, g2, c, d = spans[y]
            if g == g2 and a < d and c < b:
                return f"Rows {i} and {j} overlap for the same value and kind"
    return None


def windows(s: SplLimitSet, day: date, site) -> list[tuple[float, float, SplLimit]]:
    """(start ts, end ts, limit) for show day ``day``."""
    out = []
    roll = sitetime._rollover(site).strftime("%H:%M")
    nxt = day + timedelta(days=1)
    for lim in s.limits:
        a = sitetime.resolve(day, lim.start or roll, site)
        b = sitetime.resolve(nxt, roll, site) if not lim.end else sitetime.resolve(day, lim.end, site)
        if b > a:
            out.append((a, b, lim))
    return out


def evaluate(s: SplLimitSet, now: float, site) -> dict:
    """What applies at ``now``: ``active`` {group: (limit, start ts, end ts)} and ``changes``,
    the boundaries after now (up to the end of the next show day) as dicts sorted by time."""
    day = sitetime.show_day(now, site)
    wins = windows(s, day, site) + windows(s, day + timedelta(days=1), site)
    active: dict[tuple, tuple] = {}
    for a, b, lim in wins:
        if a <= now < b:
            active[lim.group] = (lim, a, b)

    by_group: dict[tuple, list] = {}
    for w in wins:
        by_group.setdefault(w[2].group, []).append(w)

    def at(group, t):
        for a, b, lim in by_group.get(group, ()):
            if a <= t < b:
                return lim
        return None

    # The end of tomorrow's windows is only where this list stops, not a change: leave it out.
    horizon = sitetime.resolve(day + timedelta(days=2), sitetime._rollover(site).strftime("%H:%M"), site)
    stamps = sorted({t for a, b, _l in wins for t in (a, b) if now < t < horizon})
    changes = []
    for t in stamps:
        for g in sorted(by_group):
            before, after = at(g, t - 0.001), at(g, t)
            bv = before.value_db if before else None
            av = after.value_db if after else None
            if bv != av:
                changes.append({"ts": t, "group": g, "before": before, "after": after})
    end = sitetime.resolve(day + timedelta(days=1), sitetime._rollover(site).strftime("%H:%M"), site)
    valid_until = min([t for t in stamps] + [end])   # nothing in the result changes before this
    return {"day": day.isoformat(), "active": active, "changes": changes, "valid_until": valid_until}


def tightens(before: SplLimit | None, after: SplLimit | None) -> bool:
    if after is None:
        return False
    return before is None or after.value_db < before.value_db


def fmt_db(v: float) -> str:
    return f"{v:g}"


def fmt_level(v: float, limit: float) -> str:
    """A received value for text: one decimal as the card shows, with more decimals only when one
    decimal would hide that it is above the limit (95.04 over 95 reads "95.04", not "95.0").
    Display only: the comparison always uses the value exactly as received."""
    for d in (1, 2, 3):
        s = f"{v:.{d}f}"
        if (float(s) > limit) == (v > limit):
            return s
    return repr(v)


def label_of(lim: SplLimit) -> str:
    return lim.label or lim.metric


# ------------------------------------------------------------------ the live service
class SplLimitService:
    """Owned by the Hub, ticked once a second. Never raises out of ``tick``."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self.doc: dict | None = None          # the effective revision row (see Recorder)
        self.set = SplLimitSet()
        self._active: dict[tuple, str] | None = None   # group -> active limit id (None until first tick)
        self._active_val: dict[tuple, float] = {}
        self._active_end: dict[tuple, float] = {}
        self._over: dict[str, dict] = {}      # limit id -> {"on", "under_since", "seen"}
        self._warn: dict[str, float] = {}     # alarm id -> boundary ts
        self._sig = None
        self._ev = None                       # (set, site key, from ts, evaluate result)
        self.reload()

    # -- the stored set
    def reload(self) -> None:
        rec = self.hub.recorder
        try:
            row = rec.spl_limits_effective(rec.event_id, rec.show_id)
        except Exception:  # noqa: BLE001
            log.exception("Could not read the sound level limits")
            row = None
        self.doc = row
        self._ev = None
        try:
            self.set = SplLimitSet.model_validate(row["set"]) if row else SplLimitSet()
        except Exception:  # noqa: BLE001 - a damaged row shows no limits rather than wrong ones
            log.warning("Sound level limits: the saved set could not be read; no limits shown")
            self.set = SplLimitSet()

    def changed(self, note: str | None = None) -> None:
        """After a save (``note`` given: an edit, marked live) or a show switch (None): re-read and
        tell dashboards."""
        self.reload()
        if note is not None:
            rev = self.doc["rev"] if self.doc else 0
            self.hub.add_marker(f"Sound level limits updated (rev {rev})" + (f": {note}" if note else ""),
                                MARKER_SOURCE)
        self._sig = None
        self._active = None   # a save or show switch is not a boundary passing: no change marker
        self.tick(time.time())

    def evaluate(self, now: float) -> dict:
        """evaluate() for now, reused until the next boundary or day end (it runs every second)."""
        site = self.hub.config.site
        key = (id(self.set), site.timezone, site.day_rollover)
        c = self._ev
        if c and c[0] == key and c[1] <= now < c[2]["valid_until"]:
            return c[2]
        ev = evaluate(self.set, now, site)
        self._ev = (key, now, ev)
        return ev

    # -- matching limits to recorded values
    def slot_entity(self, lim: SplLimit) -> str | None:
        pairs = self.hub.config.spl.effective_slots()
        for eid, pair in zip(spl.slot_ids(pairs), pairs):
            if pair == (lim.source, lim.metric):
                return eid
        return None

    def reading(self, lim: SplLimit, now: float) -> dict:
        """The live value beside a limit: state is over / under / no_reading / not_recorded."""
        eid = self.slot_entity(lim)
        if eid is None or not self.hub.config.spl.enabled:
            return {"entity_id": None, "value": None, "updated": None, "state": "not_recorded"}
        e = self.hub.entities.get(eid)
        if e is None or e.value is None:
            return {"entity_id": eid, "value": None, "updated": None, "state": "no_reading"}
        stale = e.is_stale(now, self.hub.stale_after_for(e))
        if stale:
            return {"entity_id": eid, "value": e.value, "updated": e.updated, "state": "no_reading"}
        return {"entity_id": eid, "value": e.value, "updated": e.updated,
                "state": "over" if e.value > lim.value_db else "under"}

    # -- the tick
    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        try:
            self._tick(now)
        except Exception:  # noqa: BLE001 - never stop the hub tick over limits
            log.exception("Sound level limits: tick failed")

    def _tick(self, now: float) -> None:
        site = self.hub.config.site
        cfg = self.hub.config.spl.limits_alert
        ev = self.evaluate(now)
        active = {g: v[0].id for g, v in ev["active"].items()}

        # 1. a boundary crossed live: one marker per limit change (kind=limit), never back-filled
        if self._active is not None and active != self._active:
            for g in sorted(set(active) | set(self._active)):
                if g[2] != "limit" or active.get(g) == self._active.get(g):
                    continue
                cur = ev["active"].get(g)
                at_ts = cur[1] if cur else self._active_end.get(g)   # when the change happened
                if at_ts is None or now - at_ts > LATE_S:
                    continue
                was = self._active_val.get(g)
                if cur:
                    lim = cur[0]
                    text = f"Sound level limit now {fmt_db(lim.value_db)}" + \
                        (f" (was {fmt_db(was)})" if was is not None else "") + f": {label_of(lim)}"
                else:
                    text = f"Sound level limit ended (was {fmt_db(was)}): {g[1]}" if was is not None else ""
                if text:
                    self.hub.add_marker(text, MARKER_SOURCE)
        self._active = active
        self._active_val = {g: v[0].value_db for g, v in ev["active"].items()}
        self._active_end = {g: v[2] for g, v in ev["active"].items()}

        # 2. exceed: on a live received value, debounced for the marker/alarm only
        live_ids = set()
        for g, (lim, _a, _b) in ev["active"].items():
            live_ids.add(lim.id)
            st = self._over.setdefault(lim.id, {"on": False, "under_since": None, "seen": None})
            r = self.reading(lim, now)
            if r["state"] == "no_reading":
                st["under_since"] = None   # the 2 minutes at or under must be seen, not assumed
            fresh = r["updated"] is not None and r["updated"] != st["seen"] and r["state"] in ("over", "under")
            if not fresh:
                continue
            st["seen"] = r["updated"]
            if r["state"] == "over":
                st["under_since"] = None
                if not st["on"]:
                    st["on"] = True
                    if lim.kind == "limit":
                        text = f"Over limit: {label_of(lim)} {fmt_level(r['value'], lim.value_db)} vs {fmt_db(lim.value_db)} (limit)"
                        if cfg.exceed_marker:
                            self.hub.add_marker(text, MARKER_SOURCE)
                        if cfg.exceed_alarm:
                            self._condition(f"spl:over:{lim.id}", True, text, now)
            elif st["on"]:
                if st["under_since"] is None:
                    st["under_since"] = r["updated"]
                elif now - st["under_since"] >= REARM_S:
                    st.update(on=False, under_since=None)
                    self._condition(f"spl:over:{lim.id}", False, "", now)
        for lid in [k for k in self._over if k not in live_ids]:
            if self._over[lid]["on"]:
                self._condition(f"spl:over:{lid}", False, "", now)
            del self._over[lid]
        if not cfg.exceed_alarm:
            for lid in list(self._over):
                self._condition(f"spl:over:{lid}", False, "", now)

        # 3. warning before a limit tightens (silent), cleared 2 minutes after the boundary
        want: dict[str, tuple[float, str]] = {}
        if cfg.change_warning_min > 0:
            lead = cfg.change_warning_min * 60
            for c in ev["changes"]:
                g, after = c["group"], c["after"]
                if g[2] != "limit" or not tightens(c["before"], after) or c["ts"] - now > lead:
                    continue
                aid = f"spl:change:{after.id}"
                hhmm = sitetime.local_hhmm(c["ts"], site)
                want.setdefault(aid, (c["ts"], f"Sound level limit drops to {fmt_db(after.value_db)} "
                                               f"at {hhmm}: {label_of(after)}"))
        for aid, (ts, text) in want.items():
            self._warn[aid] = ts
            self._condition(aid, True, text, now)
        change_ts = {c["ts"] for c in ev["changes"]}
        for aid, ts in list(self._warn.items()):
            if aid in want:
                continue
            if now >= ts + CLEAR_AFTER_S or (now < ts and (ts not in change_ts or cfg.change_warning_min == 0)):
                self._condition(aid, False, "", now)
                del self._warn[aid]

        # 4. tell dashboards when what they show changes
        sig = (self.doc["rev"] if self.doc else 0, tuple(sorted(active.items())),
               ev["changes"][0]["ts"] if ev["changes"] else None)
        if sig != self._sig:
            self._sig = sig
            self.hub.bus.publish("spl_limits", None)

    def _condition(self, aid: str, on: bool, text: str, now: float) -> None:
        change = self.hub.alarms.set_condition(aid, on, 1, text, now, silent=True)
        if change:
            self.hub._alarm_changed([change])

    # -- the public view
    def public(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        ev = self.evaluate(now)
        current = []
        for g, (lim, a, b) in sorted(ev["active"].items(), key=lambda kv: (kv[0][2] != "limit", kv[0])):
            nxt = next((c for c in ev["changes"] if c["group"] == g), None)
            current.append({
                "id": lim.id, "source": lim.source, "metric": lim.metric, "kind": lim.kind,
                "label": lim.label, "value_db": lim.value_db, "since": a, "until": b,
                "next_ts": nxt["ts"] if nxt else None,
                "next_value_db": nxt["after"].value_db if nxt and nxt["after"] else None,
                "reading": self.reading(lim, now),
            })
        upcoming = [{"ts": c["ts"], "source": c["group"][0], "metric": c["group"][1], "kind": c["group"][2],
                     "from_db": c["before"].value_db if c["before"] else None,
                     "to_db": c["after"].value_db if c["after"] else None} for c in ev["changes"][:10]]
        d = self.doc or {}
        return {
            "now": now, "day": ev["day"], "has_limits": bool(self.set.limits),
            "rev": d.get("rev", 0), "updated": d.get("saved_at"), "scope": d.get("scope", "none"),
            "copied": bool(d.get("copied_from_event")),
            "set_by": self.set.set_by, "reference": self.set.reference, "public_notes": self.set.public_notes,
            "limits": [lim.model_dump() for lim in self.set.limits],
            "current": current, "upcoming": upcoming,
            "disclaimer": DISCLAIMER, "period_note": PERIOD_NOTE,
        }


def dump_set(s: SplLimitSet) -> str:
    return json.dumps(s.model_dump(), separators=(",", ":"))
