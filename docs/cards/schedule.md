# Schedule

The **Schedule** card shows what is on **NOW**, what is **NEXT**, and the running order for the
day. It is on tablet, phone and wall dashboards by default, but it stays **hidden until the show day
has a schedule**.

The schedule is the running order that your tech types in or imports in Admin. It is Stagewatch's own
list of planned times. If your stage manager runs the show from Ontime, see
[Ontime Timer](ontime-timer.md) and [Ontime Rundown](ontime-rundown.md), which follow what Ontime
is doing right now. You can have both on one dashboard.

The Schedule card never beeps and never raises an alarm. Back to [Dashboard cards](README.md).

## Read the card

### NOW

NOW shows the item that is on at the moment.

- **Title** of the item.
- **Time left**, for example `12 min left`. This only appears when the item has its own end time.
  An item with no end time simply runs until the next item starts, so there is no countdown, only
  the line `Started 19:30, 12 min ago`.
- **Started and ends**, for example `Started 19:30, 12 min ago · ends 20:15`.
- **A progress bar** for how much of the item's planned time has gone. It also needs an end time.

When there is nothing on, NOW says **Not started yet**, **Nothing on now** or **Finished**.

### NEXT

NEXT shows the next item that has not started.

- **Title**, **Starts 21:00**, and a countdown such as `in 4 min 05 s`.
- When the next item starts the moment this one ends, NEXT shows only its start time, so you do
  not see the same countdown twice.
- **Nothing more today** when there are no more items.

### The colour steps

As time runs out, NOW changes colour. Each step also has a tag with words, so you do not rely on
colour.

| Time left on NOW | Colour | Tag |
|---|---|---|
| More than 15 minutes | Normal | None |
| 15 minutes or less | Amber | **15 MIN** |
| 5 minutes or less | Orange | **5 MIN** |

NEXT turns amber, with the tag **STARTS IN 5 MIN**, for the last 5 minutes before it starts.

These are the usual times (15 and 5 minutes). Your tech can change them in **Admin → Site → Warning
times (minutes)**. The tag follows the numbers they choose. The card is never red, because running
over is not a stop.

If your tech put a `!` after a time (for example `5!`), the card pulses slowly at that step. The
pulse stays still on screens set to reduce motion.

### The running order

On a tablet, the whole running order is listed under NOW and NEXT.

- Each line has the time, the title, the stage if the dashboard shows all stages, and the kind
  (for example **Changeover**).
- The item on now is tagged **NOW**. Items that have finished are dimmed.
- A **Curfew** is listed here as a line. It is not a separate block. An act with no end time is cut
  off at the curfew, so NOW will not run past it. An item that starts after the curfew, such as
  **Load Out**, runs as normal.
- **Setlist ›** appears on acts that have a setlist. Tap the line to read it. **Back to what's on
  now** returns to the current act.

### On a phone and on a wall screen

- **Phone.** A short strip shows what is on now and what is next. Tap it to see the rest. Tap again
  to close (**More ▼**, **Less ▲**).
- **Wall.** A large NOW and NEXT strip you can read from across the room. The running order is not
  shown.

### After the show

When everything is over, the card says **Finished** instead of counting hours. Once the next show
day has begun and nobody has started it in Admin, tablets and phones show a small note:
`Yesterday's schedule (Fri 2 Oct). Start the next day in Admin.` The wall screen hides the card.

## Stage

Your tech can set a **Stage** on a dashboard, for example **Main stage**. The card heading then
reads **Schedule · Main stage** and shows only that stage's items, plus items with no stage.
With no stage set, a dashboard shows every item.

## Times

All times are 24-hour **site time**, whatever the tablet is set to. The countdowns are worked out
from Stagewatch's own clock, so a wrong clock on a tablet does not change them.

## Markers from the schedule

With **Add markers from the schedule** ticked (on the schedule page, on by default), Stagewatch puts
a blue ◆ marker on the chart as each moment of the running order arrives.

| Line in the schedule | Marker | When |
|---|---|---|
| Soundcheck "Headliner" | **Soundcheck: Headliner** (just the title if it already says soundcheck) | its start |
| Doors | **Doors** | its start |
| Act "Kestrel Road" | **Kestrel Road on stage** | its start |
| Act "Kestrel Road" | **Kestrel Road off stage** | its end, only if the act has an end time or the curfew cuts it off |

If a line has a stage, the stage follows in brackets, for example `Doors (Main stage)`. Markers
sit at the **planned** times, not when someone presses a button.

- Each moment gets one marker, once. Saving the schedule again, or restarting Stagewatch, never
  adds a second one.
- If you move a line's time **before** it happens, the marker comes at the new time. Markers
  already on the chart stay where they are.
- **Nothing is back-dated.** If Stagewatch was off when a moment passed, that moment gets no marker,
  now or later. The gap in the chart and the "Stagewatch was off" marker show it.
- A moment that passes while its marker is switched off gets no marker, even if you switch it on
  afterwards.
- Schedule markers can be hidden, given notes or deleted like any other. See [Markers](markers.md).

## Edit the schedule (for the person who looks after Stagewatch)

The schedule has its own page, so Admin stays short. It uses the same admin PIN. Anyone on the show
network can read the schedule. Only someone with the PIN can change it.

1. Open **Admin**, find **Schedule**, and click **Open schedule editor**. You can also go to the
   address plus `/schedule`. If you are not logged in, Stagewatch asks for the PIN and brings you
   back.
2. Click **Add item**.
3. Type the start time in 24-hour form (**Start (HH:MM)**, for example `19:30`). Add an end time if
   you want one.
4. Choose the **Kind**: Venue Access, Load In, Crew Call, Soundcheck, Doors, Act, Changeover,
   Curfew, Load Out or Other.
5. Type the **Title**. Leave **Stage** empty for items that apply to every stage.
6. Optional: click **Setlist** to type a setlist (plain text or simple Markdown). A preview shows
   how it will look on dashboards.
7. Use **▲** and **▼** to put items that start at the same time in the order you want.
8. Click **Save schedule**.

**What you'll see:** `Schedule saved`. Open dashboards update within a second or two.

Things to know:

- A time earlier than the **New show day starts at** time (06:00 unless your tech changed it) counts
  as the next morning, so `23:00` to `00:30` works.
- A schedule can have up to 300 items, with titles up to 120 characters and setlists up to 8 KB each.
- If someone else changed the schedule while you were editing, Stagewatch tells you and keeps what
  you typed. Click **Reload** to see their version.
- **Undo changes** puts the list back to the last save.
- Each line has a **Marker** tick box. It starts ticked for soundcheck, doors and act lines. Untick
  it to leave a line out, or tick it on any other line (a changeover, for example) to get a marker
  with that line's title at its start. To stop all schedule markers, untick **Add markers from the
  schedule**. That saves straight away.

### Bring in a running order

1. Open **Paste or import a running order**.
2. Paste the list, or open a CSV or text file.
3. Choose the **Format**, or leave it on **Work it out**. The choices are one item per line (such as
   `19:30-20:15 Support`), CSV (`start, end, title, kind, stage`), or copied from a spreadsheet.
4. Click **Preview**.

**What you'll see:** the items Stagewatch understood. Lines it cannot read are listed by line
number. Add the rest in one click, then click **Save schedule**.

**Load demo day** fills in an example around the current time. It only appears if the schedule is
empty, or in emulate mode.

### Export and print

- **Export CSV** downloads the list as a file named like `schedule-summer-festival-2026-10-02.csv`.
  It opens in Excel and can be pasted or opened here again. It has no setlists.
- **Print** opens your browser's print window with a clean black-on-white running order, with each
  act's setlist underneath. Choose **Save as PDF** there if you want a file instead.
- Both use the list as it is on the page, including changes you have not saved.

### If it didn't work

- **The card is missing.** Nothing is in the schedule for this show day or this dashboard's stage.
  Check **Admin → User dashboards → Edit cards** has **Schedule** ticked.
- **"The schedule was changed elsewhere".** Another person saved first. Click **Reload**.
- **A time is refused on the night the clocks go forward.** A time that does not exist (01:00 to
  01:59 in the UK) is refused, with a note saying which time to use instead.
- More help: [Troubleshooting](../troubleshooting.md).

## What your tech can change in Admin

- The running order: **Admin → Schedule → Open schedule editor** (above).
- Warning times and the new-day time: **Admin → Site**. See [Site and shows](../admin/site-and-shows.md).
- Which stage a screen follows: **Admin → User dashboards → Edit cards → Stage**. See
  [Dashboards](../admin/dashboards.md).

## Tested status

**Tested on hardware: pending.** The Schedule card and editor have been checked with simulated
data (emulate mode), not yet through a real show day.

Related: [Markers](markers.md), [Glossary](../glossary.md).
