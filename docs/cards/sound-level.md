# Sound level

Up to three sound level values from Smaart, and a graph of how they have moved. It answers "how loud is it at the mix position, and has it been climbing?".

The numbers are Smaart's own. Stagewatch copies them across exactly as Smaart sends them. It does not average, smooth, round or correct them, and it does no sound-level maths of its own. This is not a calibrated compliance record. The Smaart log or report is the record that counts.

Part of the [dashboard cards](README.md). The card stays hidden until values have been chosen in Admin. Choosing inputs and values, the Smaart address and password, locations and the graph range are set up in Admin: see [Smaart](../integrations/smaart.md).

## What you see

Each chosen value has its own box:

- The name exactly as Smaart writes it, such as "SPL A Slow" or "LAeq 10".
- A big number in dB.
- A short description under it, such as "A-weighted, Slow response".
- A location, such as "FOH", if your tech has set one. When every value has the same location, it moves into the card title ("Sound level · FOH").
- A note line, when there is something to say.

Above the graph, one line says "Input: ..." with the name of the Smaart input. If the values come from different inputs, each box names its own input instead. Values from different inputs are never combined.

| In the box | What it means |
|---|---|
| A dash and **Waiting for a value** | Nothing has arrived yet. |
| A dash and **Not available** | Smaart does not give that value. A reading Smaart marks as overload also shows as a dash. It is never shown as 0. |
| A dash and **No signal from Smaart** | Smaart has gone away. A notice above the graph says why. |
| The number greyed, with **Old reading, 12 s ago** | The value has gone old. It stays on screen dimmed, with its age, and is never shown as live. |

A live value shows no time, because live is the normal case.

## The graph

- The buttons **15m**, **1h** and **Show** choose how far back to look. **Show** means since the show started. Your choice is remembered on that screen.
- Each value has its own line style, solid, dashed or dotted, so you can tell them apart without colour. The legend names the style. In the light theme the lines use darker colours.
- **A gap in the line means no readings** for that time. Nothing is filled in. When readings come back after a gap, one marker is added, such as "Sound level readings resumed after a gap".
- **Schedule markers appear on the chart too.** Doors, soundchecks and acts going on and off stage (as set on the Schedule) show as markers, so you can see the level against the plan. They are the same markers as on the history chart, and appear only as the times pass live.
- On a long view the graph shows the last reading in each interval, and the note under the graph says so. Nothing is averaged.
- The up and down scale is set by your tech. Automatic fits the readings in steps of 5 dB, and is never narrower than 20 dB. Custom uses a fixed range. On a custom graph a reading outside the range is drawn at the edge with a small triangle and a note under the graph, never hidden. The numbers in the boxes are never affected.

Sound levels are never part of the site averages and cannot have a calibration offset.

## Online, Offline or Error

The card header has a small mark beside the Stagewatch badge: **Online** (green ●), **Offline** (amber ✕) or **Error** (red !). Tap it to see how long since the last data and what to try, such as checking that Smaart is running with its API on. The marks are explained in [the dashboard header](sensors.md#the-dashboard-header).

## Who can see it

Dashboards have no password. Anyone on the show network can see these numbers and the Smaart input names. Only add the card to screens where that is fine.

There are no limits or alarms for the level yet. For now the card is a display and a record only.

## Tested on real kit

Not yet tested against a live Smaart. This card was built from the script Smaart's own web page uses, not from Rational Acoustics' developer kit, so expect to adjust it once it has met a real Smaart. Please check the numbers against Smaart's own screen before you rely on them.

Related: [Troubleshooting](../troubleshooting.md).
