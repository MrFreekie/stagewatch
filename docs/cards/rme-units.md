# RME interfaces

The health of the RME units on your network, such as the 12Mic: is each one OK, how hot is it, are both power supplies on, which clock is it following and which preset is loaded. It answers "is my pre alive and on the right clock?".

This is a convenience view. It is not a protection system. Stagewatch only listens to each unit. It never changes gain, 48 V, a preset, routing or the clock.

Part of the [dashboard cards](README.md). The card stays hidden until at least one unit is set up. Add units in Admin: see [RME interfaces](../integrations/rme.md). Then tick **RME interfaces** under **User dashboards → Edit cards**. It is never added by default, and it is full width only.

## What you see

There is one tile per unit, with the unit's name and model. Each tile shows:

- **A status word and a symbol**, never colour alone: **✓ OK**, **▲ CHECK**, **✖ WARNING**, **— OFFLINE** or **— Starting**.
- **The core temperature** in °C, as the unit reports it.
- **PSU 1 and PSU 2**: **On** or **Off**. If the unit does not say, the word is **n.a.**.
- **Clock**: the source the unit follows, whether it is **Locked** or **Not locked**, and the sample rate, for example `Word clock, Locked, 48 kHz`.
- **Preset**: the preset in use and the one loaded. When they differ, a **▲ Preset changed** flag appears.
- **AVB**: how many of the unit's AVB streams are active, for example `4 of 8 streams active`. A stream with no channels set up on the unit reads **Not in use**, not CHECK.
- **Channel names**: the names set on the unit's analogue inputs, for example `RF Mic 1`, each with its channel number. Only channels that have a name are listed. On a tablet the list is open; on a phone it is folded under **Channel names**. **These names are shown on every dashboard that has this card, so put nothing private in a channel name.** Gain, 48 V and phase are not read.
- **Anything the unit itself is unhappy about**, listed under the tile with its area (for example Clock or Preset) and, where the unit gives one, the reason. Things that are fine are not listed. A closed **All checks** line lists every check, and the unit's firmware.

If a unit has not given a value yet, the card shows **—**. A missing value is never shown as zero.

## What the words mean

| Word | Meaning |
|---|---|
| **✓ OK** | The unit says everything it watches is good. |
| **▲ CHECK** | The unit has a Notice: something worth a look, such as a clock input not in sync. Advisory. |
| **✖ WARNING** | The unit has a Warning, or a power supply has been lost on a unit with a redundancy alert. Alert level. |
| **— OFFLINE** | Stagewatch cannot reach the unit. |
| **— Starting** | Stagewatch is connected but the unit's full status has not arrived yet. |

A word the unit uses that Stagewatch has not seen before is shown as **Unrecognised** with a question mark, and is never shown as OK. Nothing on this card makes a sound.

## When a unit is away or quiet

- **OFFLINE** units keep their last values on screen, **dimmed and struck through**, with a line such as `OFFLINE, last seen 3 min ago`. Those figures are not live. Nothing is filled in for the time the unit was away.
- **No updates for 5 min** is information, not a fault: the unit is connected but has had nothing new to say.
- The card's **Online / Offline** label in the top-right corner shows the worst unit. Click it for what to check.

## On the wall and the phone

- **Wall:** only the name, the status word, the temperature (large), the two PSU words and a compact grid of channel names (the first 12, then `+N more`), so they read from across the room. A tile in WARNING gets the wall alarm edge. CHECK does not.
- **Phone:** tiles stack one above the other.
- **Tablet:** two tiles side by side, with the clock, preset, AVB and the list of problems.

## Equipment card

The unit's core temperature also appears on the [Equipment](equipment.md) card. The power supplies do **not**: they are On or Off words here, and would only show as 1 and 0 there.

## About these figures

Everything is what the unit reports, as it reports it. The wording of the unit's own health states is RME's. Tried on one 12Mic only: other RME units are untested. Compare the card with RME's own web page before you rely on it.

RME units have no password: anyone on the network can change them. Stagewatch only reads. See [RME interfaces](../integrations/rme.md).
