# Markers

A **marker** is a bookmark in time. The **Markers** card lets you add one, read the list, and see how
far the air has moved since each one. It is on tablet and phone dashboards by default.

Stagewatch only reads and reports. It does not change any of your processors. Back to
[Dashboard cards](README.md).

## Why markers matter

Sound travels faster in warm air and slower in cold air. Over a long throw, the arrival time
changes as the day warms up or cools down. On a 30 m delay path, a 10 °C change is about
**1.5 ms**. That can move your alignment.

When you drop a marker at soundcheck, Stagewatch remembers the temperature, humidity, pressure and
speed of sound at that moment. It then tells you how far things have moved since.

## Set an "Aligned" marker at soundcheck

1. Get your system aligned as you normally do.
2. On your dashboard, find the **Markers** card.
3. Click in the box and type **Aligned**.
4. Tap **Add marker**.

**What you'll see:** a small message `Marker "Aligned" added`, and **Aligned** appears at the top of
the list with its time. It is also drawn on the [History chart](chart.md).

You can add other markers too, for example **Doors** or **Headliner**. A label can be up to 120
characters. Leave the box empty and the marker is called **Marker**.

**If you cannot see the box:** that dashboard is not allowed to add markers. Use another dashboard
(for example FOH), or ask your tech to tick **Add markers** for it. A wall screen never shows the
box, because it has no buttons.

## Read how far it has drifted

1. Tap a marker in the list (or its tab on the chart).
2. A panel opens. It says `Since "Aligned" at 14:02` and shows a big number such as
   **+0.412 ms**.

How to read it:

- The big number is the **change in sound travel time** since the marker, over the **reference
  distance**. That is 30 m unless your tech set another. The line under the number says which.
- **Plus (+)** means sound now arrives **later** than at the marker. The air is colder, or has
  changed in a way that makes sound slower.
- **Minus (-)** means sound now arrives **earlier**. The air is warmer.
- **0.000 ms** means nothing has moved.
- The small table under it shows **Temp**, **RH**, **Pressure** and **c** at the marker, now, and
  the change.
- A dash **—** means it cannot be worked out, for example because no sensor was reporting at the
  time of the marker.
- If the air is outside the range the formula is tested for, a quiet note says the figures are
  approximate. See [Tiles](env-tiles.md#outside-the-tested-range).

Use it as a guide to decide when a re-check of your delays or sub alignment is worth doing.
Stagewatch does **not** change any of your processors. It only tells you.

## Add a note to a marker

A note says what happened at a marker, for example "Delays re-timed after the rain. Subs
unchanged." Anyone on a dashboard that can add markers can write or change a note on **any**
marker.

1. Tap the marker in the list. The drift panel opens.
2. Under it, find **Note**. Tap **Add note** (or **Edit note** if it already has one).
3. Type the note. New lines are fine. It can be up to 1,000 characters.
4. Tap **Save**. To leave it as it was, tap **Cancel**.

**What you'll see:** `Note saved`, and the note under the drift panel. In the list, the marker
shows **✎ note**. Every open screen shows the new note within a second or two.

**Privacy.** Notes are plain text and are visible to anyone on the show network. The box says so.
Do not put names or phone numbers in them.

## Hide a marker

Hiding takes a marker off the chart and out of the list, to keep a busy day readable. Nothing is
lost. It stays in the history, the drift numbers still work from it, and reports still include it.

1. Tap the marker in the list.
2. Under **Note**, tap **Hide**.

**What you'll see:** `Marker hidden. Tick Show hidden to find it again.`

To find it again, tick **Show hidden (2)** under the list (the number is how many are hidden).
Hidden markers show dimmed and marked **Hidden**. Tap **Un-hide** to put one back on the chart.

**Deleting** a marker is different. Only someone logged in to Admin with the PIN sees the **✕**
button, and it asks you to confirm. Notes, **Hide** and **Un-hide** work on any dashboard that can
add markers.

A marker that belongs to an alarm that is still active cannot be changed until the alarm clears.

## Marker shapes and colours

Every marker has its own shape and colour on the chart and in the list. The shape is there as well
as the colour, so you can tell them apart in any light.

| Shape and colour | What it is |
|---|---|
| ▼ amber | A **crew mark**. Someone added it, for example **Aligned**. |
| ■ grey | A **Stagewatch note**. Stagewatch added it, for example "Stagewatch was off for about 25 min". |
| ▲ orange | An **alarm**. Starts with `ALARM:`. |
| ◆ blue | A **schedule marker**. See [Schedule](schedule.md#markers-from-the-schedule). |
| ● purple | Reserved for contacts and devices. Nothing uses it yet. |

A few automatic markers currently use the crew-mark shape. They say what they are in their words:
`Pressure falling quickly` (see [Barometer](barometer.md)), `meter back` (see [Power](power.md))
and `Sound level readings resumed after a gap` (see [Sound level](sound-level.md)).

### Alarm markers

When an **Alert** or **Stop** alarm starts, Stagewatch adds a marker that starts with `ALARM:`.
When someone taps **Acknowledge**, that alarm's marker is hidden, so the chart does not fill up
with alarms already dealt with. Tick **Show hidden** to see them. An alarm that clears by itself,
without anyone acknowledging it, keeps its marker on the chart. Quiet notices (the grey, never-beeping
ones) add no marker.

### Calibration change markers

When your tech saves a sensor in Admin with a different **offset**, or switches **Average** on or
off for it, Stagewatch adds one grey ■ marker. That way the crew can see why a site value stepped.
For example:

- `Calibration changed: Stage Left Temp offset +0.3 °C (was 0 °C)`
- `Stage Left Temp now left out of the site average (was included)`

If both change in one save, they share one marker. No marker is made when a save changes nothing,
when a sensor is first adopted, or for an Equipment sensor's average tick (Equipment is never
averaged). Like any marker, you can add a note to it or hide it.

### Other Stagewatch notes

These grey ■ markers are added when something happens:

- Stagewatch starts again after being off, for example `Stagewatch was off for about 25 min (the
  computer restarted or lost power)`, or `Stagewatch restarted after an unexpected stop`.
- Software is updated or rolled back, for example `Software updated to ...`.

## Markers are never back-dated

Stagewatch makes markers only as time passes **live**. It never goes back and fills one in.

- If Stagewatch was off, there is a **gap** in the chart. When it starts again it adds one marker
  at that moment saying it was off. That marker is at the restart, not in the gap.
- If a schedule moment passed while Stagewatch was off, that moment gets no marker, now or later.
- A marker you add is stamped with the time you press **Add marker**.

## What your tech can change in Admin

- Who can add markers and acknowledge alarms: **Admin → User dashboards** (**Add markers**,
  **Ack alarms**). See [Dashboards](../admin/dashboards.md).
- The reference distance for the drift number: **Admin → Site** (**Reference distance (m) for Δ ms**).
  See [Site and shows](../admin/site-and-shows.md).
- Delete a marker: log in to Admin, then use the **✕** beside it on any dashboard.

## If it didn't work

- **No box to add a marker.** See the note above about **Add markers**.
- **The drift shows —.** No sensor was reporting when the marker was set, or none is reporting now.
  Check the [Sensors](sensors.md) card.
- **A marker has disappeared.** It may be hidden. Tick **Show hidden**.
- More help: [Troubleshooting](../troubleshooting.md).

## Tested status

**Tested on hardware: pending.** Markers have been checked with simulated sensors (emulate mode).
They have not yet been used through a whole real show. Check any drift number against your own
measurements before you re-time anything.

Related: [History chart](chart.md), [Glossary](../glossary.md).
