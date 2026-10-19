"""Calculated neutral current: pure functions, no network, no hub. Never a measurement.

In a three-phase four-wire system the current in the neutral is the phasor sum of the three line
currents (Kirchhoff's current law at the star point): I_N = -(I_L1 + I_L2 + I_L3), so its size is
|I_L1 + I_L2 + I_L3|. The size depends on the angles between the currents, so there are two ways to work it out:

* ``phasors`` (best): each line's current is a phasor at angle (theta - phi), where theta is 0, -120 and
  +120 degrees for L1, L2 and L3 (UK/IEC positive sequence, voltages 120 degrees apart) and phi is the
  angle by which the current lags its voltage (negative when it leads), from the power factor and the
  sign of the reactive power. Circuits on one line are added as phasors first, using their own angles.
* ``estimate``: when only the currents are known, assume the same power factor on all three lines
  (every current in phase with its own voltage, so 120 degrees apart):
  I_N = sqrt(Ia^2 + Ib^2 + Ic^2 - Ia*Ib - Ib*Ic - Ic*Ia). Zero when balanced; equals the line current when one
  line is loaded. With unequal power factors this can be badly wrong (lines 10, 6 and 8 A lagging 30, 0 and
  leading 20 degrees give 5.08 A by phasors but 3.46 A by this formula), so it is always labelled as an estimate.

Limits (shown to the crew): fundamental frequency only (triplen harmonics add in the neutral, so a
non-linear load can really carry more), meters read at slightly different moments, and loads not on a
metered circuit are not counted.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass

LINES = ("L1", "L2", "L3")
LINE_WORD = {"L1": "Line 1 (Brown)", "L2": "Line 2 (Black)", "L3": "Line 3 (Grey)"}
_THETA = {"L1": 0.0, "L2": -2 * math.pi / 3, "L3": 2 * math.pi / 3}
PF_MIN = 0.1                    # below this the angle is too uncertain to use
METHOD_PHASORS = "phasors, from each circuit's power factor"
METHOD_ESTIMATE = "estimate (assumes equal power factor)"


@dataclass(frozen=True)
class Circuit:
    """One metered single-phase circuit as of now. A value that is missing or old is None (never zero)."""
    line: str                       # "L1", "L2" or "L3"
    amps: float | None
    power: float | None = None      # watts (negative = returned to the supply)
    pf: float | None = None
    reactive: float | None = None   # var, signed: positive = current lags the voltage (inductive)


@dataclass(frozen=True)
class Neutral:
    amps: float | None              # None = unavailable
    method: str = ""                # METHOD_* when there is a figure
    reason: str = ""                # why there is no figure, in crew words


def _angle(c: Circuit) -> float | None:
    """The angle of the current behind its line's voltage, or None when it cannot be trusted."""
    if c.power is None or c.pf is None or c.reactive is None or c.power < 0 or abs(c.pf) < PF_MIN:
        return None
    phi = math.acos(min(1.0, abs(c.pf)))
    return -phi if c.reactive < 0 else phi


def neutral_current(circuits: list[Circuit]) -> Neutral:
    """The calculated neutral current of the circuits given. Unavailable (no figure, a reason) unless every
    line has at least one circuit and every circuit has a current; nothing is assumed or left out."""
    for line in LINES:
        mine = [c for c in circuits if c.line == line]
        if not mine:
            return Neutral(None, reason=f"{LINE_WORD[line]} has no metered circuit")
        if any(c.amps is None or not math.isfinite(c.amps) or c.amps < 0 for c in mine):
            return Neutral(None, reason=f"A circuit on {LINE_WORD[line]} is not reading")
    used = [c for c in circuits if c.line in LINES]
    angles = [_angle(c) for c in used]
    if all(a is not None for a in angles):
        total = sum(c.amps * cmath.exp(1j * (_THETA[c.line] - a)) for c, a in zip(used, angles))
        return Neutral(abs(total), METHOD_PHASORS)
    a, b, c3 = (sum(c.amps for c in used if c.line == line) for line in LINES)
    square = a * a + b * b + c3 * c3 - a * b - b * c3 - c3 * a
    return Neutral(math.sqrt(max(0.0, square)), METHOD_ESTIMATE)
