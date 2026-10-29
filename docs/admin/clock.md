# Admin: Clock

This page is for the person who looks after Stagewatch. It covers the **Clock** card on the **System**
tab of Admin: checking the Pi's time against your tablet or phone, and setting it.

Back to [Admin](README.md). Install and RTC notes: [Raspberry Pi guide](../install/raspberry-pi.md#the-pi-has-no-clock-battery).

## Why it is there

A Raspberry Pi has no clock battery. With no internet it starts with the last time it saved, and
every reading is stored by time. If you have no network time and no RTC (a clock add-on board with a
battery), use this card to set the Pi's clock from a device that has the right time.

Windows and macOS set their own clock, so on those the card says so and the button is off.

## What the card shows

| Row | What it means |
|---|---|
| **Computer time (the one running Stagewatch)** | The time on the computer running Stagewatch, ticking, in the site time zone. |
| **This device's time** | The time on the tablet or phone you are using, in the same time zone. |
| **Difference** | How far apart they are, measured through the network delay. "Within a second" means they match. |
| **Clock matches the network** | Whether the Pi is getting the time from the network. **Not known** if the Pi would not say. |
| **Time zone** | The Pi's time zone, or the site time zone if the Pi would not say. |

## Set the clock

1. Check that this device's own clock is right (look at your phone's lock screen).
2. Press **Set the clock from this device**.
3. If the difference is more than 5 seconds, Stagewatch says how far the clock will jump, for
   example `The Pi's clock will jump forward by 3 h 12 min.` Press **Yes, set the clock**, or **Cancel**.

**What you should see:** `The clock has been set.`, the difference row reads **Within a second**,
and the show has a marker `Clock set from a browser (was +N s)` (the Pi was ahead) or
`(was -N s)` (the Pi was behind). It is added at the new time.

If the clocks already match within a second, nothing is changed. You can only change the clock once
every 10 seconds.

The page asks the Pi for its time several times, keeps the quickest answer and allows for half the
delay, so the result is normally right to within a second or so. It is not a precision time source.

> **Not yet tested on a real Pi.** The button, the helper and the hardware clock write have been checked with
> simulated data and tests only. Try it at home before the show.

## Do this before the show, not during it

Charts and history are stored by time. Stagewatch does not rewrite or back-fill anything:

- Readings saved before the change keep their old times.
- A jump **forward** leaves a gap on the charts. A jump **back** makes the charts overlap for that stretch.
- A show that ran across the change shows both. The marker tells you where.

Stagewatch saves what it has just before it changes the clock, and does not treat the change as a
crash, a restart or a frozen server.

## Not set up on this Pi

A Pi that was installed before this button existed says **Not set up on this Pi**. Type the one line
the card shows in a Terminal window on the Pi (it asks for your Pi password), then reload Admin. The
line installs a small program that can set the clock and nothing else, and a rule that lets
Stagewatch run only that program. It restarts Stagewatch for about 10 seconds. See
[SECURITY.md](../../SECURITY.md) for what it allows. To remove it, add `--uninstall` to the end of
the same line.

## Emulate mode

In Emulate mode the card works through the same steps but says `Emulate: the clock was not changed.`
Nothing is changed and no marker is added.

## If it didn't work

See [Troubleshooting](../troubleshooting.md). The Pi would not change its clock: check the line was
typed on the Pi, and that Stagewatch restarted after it.
