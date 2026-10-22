"""Small formatting helpers shared by the HTML and CSV writers (UK style, site time)."""

from __future__ import annotations

from .. import sitetime


def clock(ts: float | None, site, with_zone: bool = False) -> str:
    """HH:MM:SS on the site's wall clock (the zone abbreviation too when clocks changed)."""
    if ts is None:
        return ""
    lt = sitetime.local(ts, site)
    s = lt.strftime("%H:%M:%S")
    if with_zone:
        s += " " + (lt.tzname() or "")
    return s.strip()


def stamp(ts: float | None, site, with_zone: bool = False) -> str:
    """DD/MM/YYYY HH:MM:SS on the site's wall clock."""
    if ts is None:
        return ""
    lt = sitetime.local(ts, site)
    s = lt.strftime("%d/%m/%Y %H:%M:%S")
    if with_zone:
        s += " " + (lt.tzname() or "")
    return s.strip()


def long_date(ts: float, site) -> str:
    return sitetime.local(ts, site).strftime("%a %d %b %Y")


def duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(round(max(0.0, seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {sec:02d} s"
    return f"{sec} s"


def num(v: float | None, decimals: int = 1) -> str:
    if v is None or v != v:
        return ""
    return f"{v:.{decimals}f}"


def pct(v: float | None) -> str:
    return "" if v is None else f"{v * 100:.0f}%"
