# Ontime Timer

The countdown that Ontime is running, shown next to your alarms and readings. Use it when the stage manager runs the show from Ontime and you want the same "time left" on the FOH tablet, the monitor desk or the wall.

This is Ontime's timer on a screen. It is not a Stagewatch timer, and it is not a cue. Stagewatch only listens to Ontime. It never starts, pauses or changes anything in Ontime.

Part of the [dashboard cards](README.md). Half size is available. The Ontime address and the title switch are set in Admin: see [Ontime](../integrations/ontime.md).

## What you see

- The title of the event Ontime has loaded. It is often an artist's name. Your tech can hide it (see "Event titles" below).
- The time left in large digits: `m:ss`, or `h:mm:ss` from one hour.
- A progress bar showing how much of the item has gone.
- The state in words, with a symbol.
- On a tablet: time added or removed, when the item finishes, and how long it has run. A phone shows the countdown, the state and the title. A wall screen shows a very large countdown, the title, the state and the bar.

The countdown comes from the numbers Ontime sends, not from clocks. A wrong clock or time zone on the Ontime computer or on your tablet cannot change it.

## The states

Every state has words and a symbol as well as colour.

| You see | What it means |
|---|---|
| **▶ RUNNING** or **▶ ROLLING** | The time is counting down. Rolling means Ontime is following the clock. |
| **⏸ PAUSED** | The time is frozen and dimmed. |
| **● READY** | An item is loaded but has not started. It shows the length of the item. |
| **■ STOPPED** | Nothing is running. It shows `--:--`, or "No event loaded". |
| **UNDER 2 MIN** (amber), then **UNDER 1 MIN** (orange) | The time left has reached the event's own warning and danger times from Ontime. The numbers are examples. |
| **15 MIN** (amber), then **5 MIN** (orange) | Ontime sent no warning times for this event, so the card uses your warning steps from Admin. It says "Warning times from Stagewatch settings". |
| **▲ OVER** (orange) | The time is up. The digits keep counting as `-0:01`, `-0:02`, and a tablet says "Over by". |
| **▲ STALE** | Nothing has arrived from Ontime for a few seconds. The time is frozen with a line through it. Don't trust it. |
| **▲ OFFLINE** | Stagewatch cannot hear Ontime. The digits show `--:--` and a quiet notice appears in the alarm bar. It never makes a sound. |
| **▲ CAN'T READ** | Ontime answered, but Stagewatch cannot use what it sent. Tell your tech. |

A warning step only applies if it is shorter than the whole item, so a 3-minute item does not start in the 15-minute step.

Running over is orange, never red, because running over is not a stop. Where your tech has chosen a warning step to flash, the tag beside the countdown pulses slowly.

If a timer counts up, or Ontime shows a clock instead of a timer, the card shows the number it was given and says so ("Counting up", "Clock"). Warning steps and "OVER" are only for a count-down.

If the card says the state is unknown, Ontime sent a state this version of Stagewatch does not know. The time is frozen.

## Event titles

Dashboards have no password, so anyone who can open a dashboard with this card can see the event title. Your tech can hide titles on every dashboard in Admin. That one switch also hides titles and notes on the [Ontime Rundown](ontime-rundown.md) card. See [Ontime](../integrations/ontime.md).

## Online, Offline or Error

The card header has a small mark beside the Ontime logo: **Online** (green ●), **Offline** (amber ✕) or **Error** (red !). Tap it to see how long since the last data and what to try. On a wall screen it only shows when Ontime is not online. The marks are explained in [the dashboard header](sensors.md#the-dashboard-header).

## Timer or Schedule card?

The Ontime Timer follows what the stage manager is running in Ontime right now. The [Schedule](schedule.md) card shows Stagewatch's own times and the whole running order, and does not need Ontime. You can have both on one dashboard.

## Tested on real kit

This card is experimental. Only the **rolling** state has been checked against a real Ontime (version 4.14.0). Pause, ready, stopped, running over and added time are built and tested in software, but not yet checked against the real thing. If one looks wrong, please say which.

Related: [Wall Clock](wall-clock.md), [Ontime Rundown](ontime-rundown.md), [Troubleshooting](../troubleshooting.md).
