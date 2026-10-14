# Ontime Rundown

Whether the show is on time. The card shows where Ontime is in the day's running order, and whether it is running ahead or behind.

The Ontime Timer answers "how long is left in this item?". This card answers "are we on time?". It shows Ontime's own numbers. Stagewatch does not keep a copy of Ontime's rundown, and it only listens: it never starts, pauses or changes anything in Ontime.

Part of the [dashboard cards](README.md). Half size is available. The Ontime address and the title switch are set in Admin: see [Ontime](../integrations/ontime.md).

## What you see

- The title and note of the running event.
- A large figure: how far ahead or behind Ontime is, with words and a triangle.
- A bar showing how far through the day Ontime is. If Ontime expects to finish later than planned, a mark on the bar shows the planned end.
- Short lines: **Planned 11:30 to 22:30**, **Expected end 22:34 (planned 22:30)** and **Started 11:30**. A line is left out when Ontime does not send it. Stagewatch never guesses an expected end.
- The event list, with the running event marked.
- A footnote: "Ontime time: the times are Ontime's own clock, not converted."

The times are Ontime's own, shown exactly as Ontime shows them. They are not changed to Stagewatch's time zone. Ahead and behind come only from Ontime's own figure, so a wrong clock or time zone cannot make the show look late. A show that runs past midnight is handled, and times after midnight say "+1 day".

## Ahead, behind and on time

| You see | What it means |
|---|---|
| **▼ 4:10 BEHIND** (amber) | Ontime is running 4 minutes 10 seconds late. Hours show from one hour. |
| **▼▼ 9:00 BEHIND** (orange) | Behind by more than your smallest warning step. The double triangle shows the step without colour. If no warning steps are set, the step is 5 minutes. |
| **▲ 1:30 AHEAD** | Ontime is running 1 minute 30 seconds early. It stays calm. |
| **ON TIME** | Within 30 seconds either way. Shown small and calm on purpose, because nothing needs attention. |

Behind is never red. A negative figure on Ontime's own screen is ahead, so "-8:49" in Ontime shows here as "▲ 8:49 AHEAD".

A small label says which figure Ontime reports: **vs plan** (measured against the planned times) or **since start**. Stagewatch shows whichever Ontime is set to. If Ontime does not send the figure, the card shows `--` and "Ahead or behind not sent".

## The event list

Each row has a mark, the start time (and the end time on a tablet or wall), the cue and the title.

| Mark | Meaning |
|---|---|
| **▶** (bold) | The running event. |
| **→** | The next event. |
| **✓** (muted) | Finished. |
| **⊘** and **SKIPPED** (struck through) | Skipped in Ontime. |

A tablet shows the running event with 2 before and 8 after. A phone shows the running event and 4 after. A wall shows the running event and 10 after. Groups and milestones are left out.

## Other messages

| You see | What it means |
|---|---|
| **NOT STARTED** | The show has not started. The planned times are shown. |
| **FINISHED** | The show has started and no event is running now. |
| **NO RUNDOWN** | Ontime has no rundown loaded. |
| "Ontime has not sent rundown information yet" | The card has connected but has no figures yet. |
| "Can't place the current event in the event list" | Stagewatch cannot find the running event in the list, for example in a rundown of more than 200 events. It shows no list rather than guess. |
| "The event list may be out of date" | The last read of the list failed. The last good list stays. |
| "Ontime marks this as a later day of the rundown" | The running event belongs to a later day. |
| **▲ CAN'T READ** | Ontime sent something Stagewatch could not read. The last good figures stay, struck through, until a readable update arrives. |
| **▲ STALE** | Nothing has arrived for a few seconds. The figures are struck through. Don't trust them. |
| **▲ OFFLINE** | Stagewatch cannot hear Ontime. The figures show `--` and the list is hidden. It is a gap, never a zero. A quiet notice appears in the alarm bar, and it never makes a sound. |

## Event titles and notes

Dashboards have no password. Anyone who can open a dashboard with this card can see the event titles and the running event's note. Your tech can hide them on every dashboard with one switch in Admin. The list then shows times and cues, with "Event" for each title. The same switch also hides the title on the [Ontime Timer](ontime-timer.md). See [Ontime](../integrations/ontime.md).

## Online, Offline or Error

The card header has a small mark beside the Ontime logo: **Online** (green ●), **Offline** (amber ✕) or **Error** (red !). Tap it to see how long since the last data and what to try. On a wall screen it only shows when Ontime is not online. The marks are explained in [the dashboard header](sensors.md#the-dashboard-header).

## Tested on real kit

This card is experimental. Only one situation has been recorded from a real Ontime (version 4.14.0): the running event 9 of 16, on time. Also checked on a real Ontime: a negative figure means **ahead** and a positive one means **behind**.

Not yet checked against a real Ontime: groups, milestones and delays in the event list, what counts as "Finished" and "Not started", and how a multi-day rundown behaves. If something looks wrong, please say which.

Related: [Wall Clock](wall-clock.md), [Ontime Timer](ontime-timer.md), [Troubleshooting](../troubleshooting.md).
