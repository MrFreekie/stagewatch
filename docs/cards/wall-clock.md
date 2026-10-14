# Wall Clock

A big clock for a wall screen, a tablet or a phone. It shows the time of day in your show's time zone.

Stagewatch is a guide, not a cue source. Do not use this clock to call or time cues.

Part of the [dashboard cards](README.md). Half size is available.

## Where the time comes from

Your tech chooses one time source for the whole installation. The card shows which one in its title.

| Title | Time source | What it means |
|---|---|---|
| **Wall Clock** | The computer running Stagewatch | Always available. It can never disagree with Stagewatch, so it never shows a warning line. |
| **Wall Clock · Ontime** | The clock in Ontime, the show timer | Everyone sees the same clock as the stage manager. A small Ontime logo and an **Online / Offline / Error** mark sit in the corner. |

Stagewatch never switches from one source to the other by itself. If Ontime stops, the card says so.

Setting the source, the Ontime address and the 12-hour or 24-hour format is done in Admin. See [Ontime](../integrations/ontime.md) and [Admin: Dashboards](../admin/dashboards.md).

## What you see

- The time, as hours, minutes and seconds. With the 12-hour format it also shows am or pm.
- Optionally, today's date underneath, such as "Fri 2 Oct 2026". It is left out whenever the time cannot be trusted.
- Nothing else while everything agrees. A line of words only appears when something is wrong.

Your tech picks the look for each dashboard, so the wall can show the ring while a phone shows plain digits.

| Look | What it shows |
|---|---|
| **Plain digits** | The time in large digits. |
| **LED ring** | An outer ring of 60 lights for the seconds, 12 brighter lights for the hours, and the time in the middle. |
| **7-segment digits** | The time as on a studio clock. Unlit segments stay faintly visible. |

The ring and the 7-segment look are always red on black, by day and by night. On a card that is too narrow for the ring, the card shows plain digits instead.

### How to read the LED ring

- The ring fills up through each minute. The light for the current second is on, and so are all the earlier ones.
- At :00 only the top light is on. That makes the new minute easy to spot.
- The 5-second lights come on exactly at :05, :10, :15 and so on. At :59 the whole ring is lit.
- The 12 brighter hour lights are always on. They are marks, not the hour hand.

## What the warning lines mean

Every warning has words with a triangle (▲), and a dashed outline or a line through the time. You never have to rely on colour.

| You see | What it means | What to do |
|---|---|---|
| **▲ Differs from Stagewatch by +3.2 s (Ontime is ahead)** | Ontime's clock and the Stagewatch computer's clock disagree by more than the limit. Plus means Ontime is ahead, minus means it is behind. The outline is dashed. | Check the clocks on the two computers. The default limit is 2 seconds and your tech can change it. |
| **... Check the time zones.** | The difference is a whole number of hours, so one computer is set to a different time zone. | Ask your tech to check the time zone in Stagewatch and on the Ontime computer. |
| **▲ Stale: nothing from Ontime for a few seconds. Don't trust this time.** | Nothing has arrived for more than 3 seconds. The time is dimmed, has a line through it, and is frozen at the last reading. | Do not use it. Check Ontime is running. |
| **▲ Ontime offline: no time to show** | Stagewatch cannot hear Ontime. The digits show dashes. A quiet notice also appears in the alarm bar. It never makes a sound. | Check Ontime is running and its address in Admin. |
| **▲ Ontime sent a time we can't read** | Ontime answered, but with something Stagewatch cannot use. | Tell your tech. |

The Stagewatch computer source never shows "Differs", because it is Stagewatch's own time.

## Online, Offline or Error

When the time comes from Ontime, the card header has a small mark: **Online** (green ●), **Offline** (amber ✕) or **Error** (red !). Tap it to see how long since the last data and what to try. On a wall screen it only shows when Ontime is not online. The marks are explained in [the dashboard header](sensors.md#the-dashboard-header).

## Tested on real kit

The Ontime clock has been tried against Ontime 4.14.0. Other versions of Ontime have not been tried.

Related: [Ontime Timer](ontime-timer.md), [Ontime Rundown](ontime-rundown.md), [Troubleshooting](../troubleshooting.md).
