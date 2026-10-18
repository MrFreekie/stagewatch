"""Find WitMotion sensors for the Admin card's "Find sensors" button, and check the addresses typed there.

The search only listens for Bluetooth advertisements for a few seconds. It never connects, never pairs and
never asks a sensor anything. It keeps the devices whose own name starts ``WT`` and reports the name, the
address and the signal strength. (Crew can rename a sensor in the WitMotion app, so a renamed one will not
show here: its address can be typed in by hand.) The addresses go to the Admin page only.
"""

from __future__ import annotations

import logging
import re

from .reader import import_bleak

log = logging.getLogger(__name__)

SCAN_S = 8.0
NAME_PREFIX = "WT"
MAX_RESULTS = 30

# AA:BB:CC:DD:EE:FF (Windows, Linux) or a UUID (macOS-style hosts hide the real address).
_MAC = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
_UUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")

# Fixed words for the Admin card (never text built from an exception).
UNAVAILABLE = ("Bluetooth is not available. Check this computer has Bluetooth, that it is switched on, "
               "and that Stagewatch is allowed to use it.")
BUSY = "Bluetooth is busy with the sensors. Try again in a moment."


class ScanBusy(Exception):
    """A search is already running."""


class ScanUnavailable(Exception):
    """Bluetooth cannot be used right now (no adaptor, switched off, no permission, or bleak is missing).
    ``message`` is the fixed sentence for the Admin card."""

    def __init__(self, message: str = UNAVAILABLE) -> None:
        super().__init__(message)
        self.message = message


def normalise_address(text) -> str | None:
    """The address in one standard form (capitals), or None when it is neither a Bluetooth address nor a UUID."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    return t.upper() if _MAC.match(t) or _UUID.match(t) else None


def pick_sensors(found) -> list[dict]:
    """From (name, address, rssi) triples, the WitMotion sensors: name starts WT, each address once, the
    strongest signal first. A signal that is not a whole number is left out (None), never guessed."""
    best: dict[str, dict] = {}
    for name, address, rssi in found:
        name = name if isinstance(name, str) else ""
        address = address if isinstance(address, str) else ""
        if not name.startswith(NAME_PREFIX) or not address:
            continue
        r = rssi if isinstance(rssi, int) and not isinstance(rssi, bool) else None
        old = best.get(address.upper())
        if old is None or (r is not None and (old["rssi"] is None or r > old["rssi"])):
            best[address.upper()] = {"name": name[:40], "address": address.upper(), "rssi": r}
    rows = sorted(best.values(), key=lambda x: (x["rssi"] is None, -(x["rssi"] or 0), x["address"]))
    return rows[:MAX_RESULTS]


async def listen(seconds: float = SCAN_S, bleak_module=None) -> list[dict]:
    """Listen for ``seconds`` and return the WitMotion sensors heard. Raises ScanUnavailable if Bluetooth
    cannot be used. Listening only: no connection is made."""
    try:
        bleak = bleak_module or import_bleak()
        heard = await bleak.BleakScanner.discover(timeout=seconds, return_adv=True)
    except Exception as exc:   # no adaptor, switched off, no permission, bleak missing: they all read the same here
        log.warning("WitMotion: the Bluetooth search could not run (%s)", type(exc).__name__)
        raise ScanUnavailable() from exc
    found = []
    for device, adv in (heard or {}).values():
        found.append((getattr(device, "name", None) or getattr(adv, "local_name", None),
                      getattr(device, "address", ""), getattr(adv, "rssi", None)))
    return pick_sensors(found)


def emulated() -> list[dict]:
    """Two made-up sensors for --emulate, so the button can be tried with no Bluetooth."""
    return pick_sensors([("WT901BLE67", "AA:BB:CC:DD:EE:01", -58), ("WT901BLE68", "AA:BB:CC:DD:EE:02", -71)])
