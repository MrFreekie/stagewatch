"""When did this computer last start?  Used to tell "the computer restarted or lost power" apart
from "Stagewatch itself crashed".  Stdlib only; returns None whenever it cannot tell."""

from __future__ import annotations

import sys
import time


def system_boot_time() -> float | None:
    """Boot time as a Unix timestamp, or None if unknown (never guessed)."""
    try:
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32")
            k32.GetTickCount64.restype = ctypes.c_uint64
            return time.time() - k32.GetTickCount64() / 1000.0
        if sys.platform.startswith("linux"):
            with open("/proc/stat", encoding="ascii", errors="replace") as f:
                return parse_proc_stat_btime(f.read())
    except (OSError, ValueError, AttributeError):
        return None
    return None


def parse_proc_stat_btime(text: str) -> float | None:
    """The ``btime <unix seconds>`` line of /proc/stat."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "btime":
            try:
                value = float(parts[1])
            except ValueError:
                return None
            return value if value > 0 else None
    return None
