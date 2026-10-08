"""Dashboard card ids: which cards exist, and the default card list for each layout.

Pure data, no I/O. ``Dashboard.cards`` (core/config.py) holds an ordered list of these ids.
The alarm banner, the header and the "disconnected" banner are always shown and are not cards.
"""

from __future__ import annotations

import re

# Cards this build can render, in the order the admin card picker lists them.
KNOWN_CARDS_0_3: tuple[str, ...] = (
    "env_tiles",       # site value tiles
    "chart",           # history chart
    "markers",         # marker list, add form and delta
    "sensors",         # node table (sensor devices only)
    "schedule",        # NOW / NEXT / CURFEW and the running order; hidden while the show has no items
    "wall_clock",      # Wall Clock (Ontime); never added by default
    "ontime_timer",    # Ontime Timer: the countdown Ontime is running; never added by default
    "connect_footer",  # "Open on a tablet" QR footer
    "barometer",       # sea-level pressure dial, 3-hour tendency, rough outlook; never added by default
    "equipment",       # readings from Equipment-role sensors (amp racks, PSUs), by node; hidden while there are none; never added by default
)
KNOWN_CARDS = KNOWN_CARDS_0_3

# What a 0.2.0 dashboard showed, plus the schedule card (invisible until a schedule exists).
# Used only when migrating dashboards from config v1; wall dashboards also get connect_footer.
LEGACY_CARDS: tuple[str, ...] = ("env_tiles", "schedule", "chart", "markers", "sensors")

# Defaults for newly created dashboards (and a fresh install's default dashboards).
LAYOUT_DEFAULTS: dict[str, tuple[str, ...]] = {
    "tablet": ("env_tiles", "schedule", "chart", "markers", "sensors"),
    "phone": ("env_tiles", "schedule", "markers", "chart"),
    "wall": ("env_tiles", "schedule", "chart", "connect_footer"),
}

MAX_CARDS = 16
# Any well-formed id survives a load, so ids from a newer release (e.g. 0.4.0's spl_limits,
# contacts) are kept through a downgrade and re-upgrade; renderers skip ids they don't know.
CARD_ID_RE = re.compile(r"[a-z_]{1,32}")  # use fullmatch


def default_cards(layout: str) -> list[str]:
    return list(LAYOUT_DEFAULTS.get(layout, LAYOUT_DEFAULTS["tablet"]))


def legacy_cards(layout: str) -> list[str]:
    """The card list a dashboard from config v1 migrates to: today's look, unchanged."""
    return list(LEGACY_CARDS) + (["connect_footer"] if layout == "wall" else [])


def strict_cards_error(cards) -> str | None:
    """For the API (loading is lenient): a fixed-text problem with a submitted card list, or None."""
    if not isinstance(cards, list) or len(cards) > MAX_CARDS:
        return f"A dashboard can have at most {MAX_CARDS} cards"
    if any(not isinstance(c, str) or not CARD_ID_RE.fullmatch(c) for c in cards) or len(set(cards)) != len(cards):
        return "Card names must be listed once each"
    if unknown_cards(cards):
        return "Unknown card"
    return None


def unknown_cards(cards: list[str]) -> list[str]:
    """Ids this build can't render (for the API, which refuses them; loading keeps them)."""
    return [c for c in cards if c not in KNOWN_CARDS]
