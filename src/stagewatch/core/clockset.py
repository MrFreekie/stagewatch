"""Set the computer's clock from the browser that is looking at Stagewatch (Raspberry Pi with no time source).

The app never runs a shell and never runs as root. On Linux it calls ONE root-owned helper through sudo:
``sudo -n /usr/local/sbin/stagewatch-set-time <whole seconds>`` (deploy/pi/stagewatch-set-time; the sudo rule
allows that file and nothing else). Everything here is plain functions and a small class with the clock, the
monotonic timer, the platform and the command runner injected, so tests never run sudo or touch a clock.

Only the time is ever sent: a whole number of seconds that the server works out itself from the browser's
estimate plus the time that passed since the request arrived. No history is rewritten.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

HELPER = "/usr/local/sbin/stagewatch-set-time"
EPOCH_MIN_MS = 1_735_689_600_000        # 2025-01-01 00:00:00 UTC
EPOCH_MAX_MS = 4_102_444_800_000        # 2100-01-01 00:00:00 UTC (not allowed)
RTT_MAX_MS = 5000
CONFIRM_OVER_S = 5.0                    # a bigger jump needs confirm_jump
NOTHING_UNDER_S = 1.0                   # a smaller difference is not worth a change
RATE_S = 10.0                           # one change per this many seconds
CAN_SET_CACHE_S = 30.0
RUN_TIMEOUT_S = 15.0
NOT_LINUX = "Windows and macOS set their own clock."
NOT_SET_UP = "Not set up on this Pi."
_OK_LINE = re.compile(r"^OK (\d{10}) rtc=(yes|no|failed)$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")

Runner = Callable[..., "subprocess.CompletedProcess"]


class ClockError(Exception):
    """A refusal with fixed, plain text (never the submitted values, a path or a program's own output)."""

    MESSAGES = {
        "not_supported": NOT_LINUX,
        "not_set_up": "The clock button is not set up on this Pi. Nothing has been changed.",
        "out_of_range": "That time is not one this computer will accept. Nothing has been changed.",
        "nothing_to_do": "The clock is already right to within a second. Nothing has been changed.",
        "needs_confirm": "That is a big change to the clock. Confirm it first. Nothing has been changed.",
        "rate_limited": "The clock was just changed. Wait 10 seconds before trying again.",
        "failed": "The Pi would not change its clock. Nothing has been changed.",
        "bad_reply": "The Pi gave an answer Stagewatch did not understand. Check the time shown before trying again.",
    }
    STATUS = {"not_supported": 409, "not_set_up": 409, "out_of_range": 422, "nothing_to_do": 409,
              "needs_confirm": 409, "rate_limited": 429, "failed": 500, "bad_reply": 500}

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = self.MESSAGES[code]
        self.status = self.STATUS[code]


def default_runner(argv: list[str], timeout: float = RUN_TIMEOUT_S) -> "subprocess.CompletedProcess":
    """Run a program from an argument list: no shell, no input, a timeout."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, check=False,
                          stdin=subprocess.DEVNULL)


def parse_timedatectl(text: str) -> dict:
    """``timedatectl show -p NTPSynchronized -p Timezone`` -> {"ntp_synchronized": bool|None, "timezone": str}."""
    out: dict = {"ntp_synchronized": None, "timezone": ""}
    for line in str(text or "").splitlines():
        key, _, value = line.partition("=")
        value = value.strip()
        if key == "NTPSynchronized":
            out["ntp_synchronized"] = True if value == "yes" else False if value == "no" else None
        elif key == "Timezone" and re.fullmatch(r"[A-Za-z0-9_+./-]{1,64}", value):
            out["timezone"] = value
    return out


def parse_helper_line(text: str) -> tuple[int, str] | None:
    """The helper's one line, ``OK <seconds> rtc=yes|no|failed`` -> (seconds, rtc); anything else is None."""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        return None
    m = _OK_LINE.match(lines[0].strip())
    return (int(m.group(1)), m.group(2)) if m else None


def jump_words(seconds: float) -> str:
    """'3 h 12 min' / '40 s' for a size of change (always positive)."""
    s = int(round(abs(seconds)))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    if s < 86400:
        return f"{s // 3600} h {(s % 3600) // 60:02d} min"
    return f"{s // 86400} d {(s % 86400) // 3600} h"


class ClockSetter:
    def __init__(self, hub, *, runner: Runner = default_runner, platform: str | None = None,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 helper: str = HELPER, exists: Callable[[str], bool] = os.path.isfile,
                 source_dir: Path | None = None) -> None:
        self.hub = hub
        self.runner = runner
        self.platform = platform if platform is not None else sys.platform
        self.wall = wall
        self.mono = mono
        self.helper = helper
        self.exists = exists
        self.source_dir = source_dir or Path(__file__).resolve().parents[3]
        self._can_set: tuple[float, bool, str] | None = None
        self._last_set = -1e9

    # ------------------------------------------------------------ what can be done here
    @property
    def linux(self) -> bool:
        return self.platform.startswith("linux")

    def can_set(self) -> tuple[bool, str]:
        """(possible, plain reason if not). Emulate mode is always possible: it is a dry run."""
        if self.hub.emulate:
            return True, ""
        if not self.linux:
            return False, NOT_LINUX
        now = self.mono()
        if self._can_set is not None and now - self._can_set[0] < CAN_SET_CACHE_S:
            return self._can_set[1], self._can_set[2]
        ok, reason = False, NOT_SET_UP
        if self.exists(self.helper):
            try:
                r = self.runner(["sudo", "-n", "-l", self.helper], timeout=5)
                ok = r.returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        self._can_set = (now, ok, "" if ok else reason)
        return self._can_set[1], self._can_set[2]

    def setup_command(self) -> str | None:
        """The one line to type on the Pi, from the folder this program runs from."""
        script = self.source_dir / "deploy" / "pi" / "install-clock-helper.sh"
        path = str(script).replace("\\", "/") if script.is_file() else "/opt/stagewatch/deploy/pi/install-clock-helper.sh"
        if not _SAFE_PATH.match(path):
            path = "/opt/stagewatch/deploy/pi/install-clock-helper.sh"
        return f"sudo bash {path}"

    def system_time_info(self) -> dict:
        """Whether the system clock is network-synchronised and its time zone (read only; None when unknown)."""
        if not self.linux or self.hub.emulate:
            return {"ntp_synchronized": None, "timezone": ""}
        try:
            r = self.runner(["timedatectl", "show", "-p", "NTPSynchronized", "-p", "Timezone"], timeout=3)
            if r.returncode == 0:
                return parse_timedatectl(r.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
        return {"ntp_synchronized": None, "timezone": ""}

    def status(self) -> dict:
        ok, reason = self.can_set()
        info = self.system_time_info()
        not_set_up = (not ok) and reason == NOT_SET_UP
        return {
            "hub_epoch": self.wall(),
            "site_timezone": self.hub.site_time().get("timezone", ""),
            "system_timezone": info["timezone"],
            "ntp_synchronized": info["ntp_synchronized"],
            "can_set": ok,
            "reason_if_not": reason,
            "is_emulate": bool(self.hub.emulate),
            "setup_command": self.setup_command() if not_set_up else None,
        }

    # ------------------------------------------------------------ the change
    def set(self, epoch_ms: int, rtt_ms: int, confirm_jump: bool, received: float) -> dict:
        """``epoch_ms``: the browser's estimate of the time at which its request reached us.  ``received``: our
        monotonic reading when the request arrived.  Returns a small result dict or raises ClockError."""
        ok, _ = self.can_set()
        if not ok:
            raise ClockError("not_supported" if not self.linux and not self.hub.emulate else "not_set_up")
        if not (EPOCH_MIN_MS <= epoch_ms < EPOCH_MAX_MS) or not (0 <= rtt_ms <= RTT_MAX_MS):
            raise ClockError("out_of_range")
        now_mono = self.mono()
        if now_mono - self._last_set < RATE_S:
            raise ClockError("rate_limited")
        before = self.wall()
        exact = epoch_ms / 1000.0 + (now_mono - received)
        target = int(round(exact))
        diff = exact - before
        if abs(diff) < NOTHING_UNDER_S:
            raise ClockError("nothing_to_do")
        if abs(diff) > CONFIRM_OVER_S and not confirm_jump:
            raise ClockError("needs_confirm")
        if not (EPOCH_MIN_MS // 1000 <= target < EPOCH_MAX_MS // 1000):
            raise ClockError("out_of_range")
        self._last_set = now_mono
        try:
            self.hub.recorder.flush()
        except Exception:  # noqa: BLE001 - a failed flush must not stop the clock being set
            log.exception("Could not save readings before setting the clock")
        if self.hub.emulate:
            line = self._fake_runner(["sudo", "-n", self.helper, str(target)])
            ack = parse_helper_line(line.stdout)
            log.info("Emulate: clock set from a browser skipped (would have changed it by %+d s)", target - round(before))
            if ack is None:
                raise ClockError("bad_reply")
            return {"ok": True, "emulated": True, "changed_by_s": target - int(round(before)), "rtc": "no",
                    "message": "Emulate: the clock was not changed."}
        argv = ["sudo", "-n", self.helper, str(target)]
        try:
            r = self.runner(argv, timeout=RUN_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            self._can_set = None
            raise ClockError("failed") from None
        if r.returncode != 0:
            self._can_set = None
            log.warning("The clock helper refused or failed (exit %s)", r.returncode)
            raise ClockError("failed")
        ack = parse_helper_line(r.stdout)
        if ack is None:
            log.warning("The clock helper gave an answer that was not understood")
            raise ClockError("bad_reply")
        after = self.wall()
        step = after - before          # how far the clock moved (the helper's work takes a moment: close enough)
        self.hub.clock_stepped(step)
        error = -step                  # the old clock's error: positive = it was ahead
        sign = "+" if error >= 0 else "-"
        self.hub.add_marker(f"Clock set from a browser (was {sign}{int(round(abs(error)))} s)", "hub")
        log.info("Clock set from a browser: changed by %+d s (hardware clock: %s)", round(step), ack[1])
        return {"ok": True, "emulated": False, "changed_by_s": int(round(step)), "rtc": ack[1],
                "message": "The clock has been set."}

    @staticmethod
    def _fake_runner(argv: list[str]) -> "subprocess.CompletedProcess":
        """Emulate mode runs the same code path against this instead of sudo."""
        return subprocess.CompletedProcess(argv, 0, stdout=f"OK {argv[-1]} rtc=no\n", stderr="")
