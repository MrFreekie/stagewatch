# Admin: Site and shows

This page is for the person who looks after Stagewatch. It covers two Admin cards: **Site**, the
settings that apply to the whole system, and **Event & show**, where you start each new show day.

Admin only changes Stagewatch's own settings. It never controls rigging, power or safety kit. Back
to [Admin](README.md).

**Changes only stick after you click that card's Save button.**

## The Site card

Open **Admin** and find **Site**. It is closed at first. Click its heading to open it. It opens by
itself while the time zone is not set.

| Setting | What it does | Starts as |
|---|---|---|
| **Site / show name** | The name at the top of every dashboard. | `Stagewatch` |
| **Altitude (m): for the barometer, and the speed of sound without a pressure sensor** | How high above sea level the site is, from -500 to 6,000 m. | 0 |
| **Distance for time difference (m)** | The distance used for the drift number on a marker. | 30 |
| **Stale after (s)** | How old a reading can get before it counts as stale. 5 to 3,600. | 60 |
| **Smoothing (seconds)** | How much the site values are smoothed. 0 to 900. 0 means no smoothing. | 30 |
| **Ignore odd readings (3 or more sensors)** | Leaves out a sensor that is far from the others. | Ticked |
| **Time zone** | The time zone of the show site. | Not set |
| **New show day starts at (HH:MM, 24-hour)** | Which show day a late-night time belongs to. | `06:00` |
| **Warning times (minutes)** | When the Schedule card changes colour. | `15, 5` |

Click **Save site** after any change. You will see `Site saved`.

### Time zone

All times on dashboards use this time zone, whatever the tablet is set to. Every dashboard then
shows the same site time, in 24-hour form.

1. Click in **Time zone**.
2. Pick your zone from the list, or type a name such as `Europe/London`.
3. Click **Save site**.

While no zone is set, a message under the card says so, and offers a button **Use … from this
browser**. Click it to use the zone your browser is set to, and it saves at once.

If you type a name Stagewatch does not know, it says `That time zone isn't recognised` and changes
nothing.

### New show day starts at

A show day runs from this time until the same time the next morning. With `06:00`, 01:30 still
counts as the night before. This decides which day late-night times belong to, including times
in the schedule and the suggested date when you start a new day.

- Type it as `HH:MM`, between `00:00` and `11:59`.
- **Use 03:00 or later**, so it stays clear of the hour when the clocks change.

### Warning times (minutes)

These are the minutes before an item ends when the **Schedule** card changes colour. Amber at the
first time, orange at each later one. The next item turns amber within the last time.

- Type 1 to 8 different whole numbers, from 1 to 240, separated by commas. The default is `15, 5`.
- Add `!` after a time to make the card flash at that time, for example `15, 5, 1!`. The flash is a
  slow pulse.
- This applies to every dashboard.

What the card then shows is on [the Schedule card page](../cards/schedule.md#the-colour-steps).

### Altitude

Type your site's height in metres. There is an easier way on the day, using today's sea-level
pressure: click **Set altitude from today's sea-level pressure…**. The steps are on
[the Barometer page](../cards/barometer.md#set-the-altitude).

### Reference distance

The drift number on a marker is the change in sound travel time over this distance. Set it to the
distance that matters to you, for example FOH to the delay tower, in metres. See
[Markers](../cards/markers.md#read-how-far-it-has-drifted).

### Stale after, smoothing and outliers

These decide how the site average is worked out. What "stale" looks like on screen is on
[the Sensors page](../cards/sensors.md#stale-readings), and the site average is explained on
[the Tiles page](../cards/env-tiles.md#the-site-average).

- **Stale after (s):** a reading older than this is greyed out and left out of the site average.
- **Smoothing (seconds):** the site average is smoothed over about this many seconds, so the numbers do
  not jump about.
- **Ignore odd readings (3 or more sensors):** with three or more sensors, one that is more than about 3 °C,
  15 %RH or 5 hPa from the middle value is left out. With two sensors Stagewatch cannot tell which
  is wrong, so nothing is left out.

Offsets and which sensors count are on the [Sensors and alarms](sensors-and-alarms.md) page.

### If it didn't work

- `Type 1 to 8 different whole numbers of minutes between 1 and 240…` The warning times are not in the
  right form. Nothing has been changed.
- `Type the new-day start as HH:MM between 00:00 and 11:59…` Nothing has been changed.

## The Event & show card

An **event** is a festival, a tour leg or a run of shows. Each **day** of it is one show. Markers,
alarms and the history chart belong to the current day. Dashboards show the event and day under
their title.

### Start the next day of the same event

For example Day 2 of a festival.

1. In **Admin**, find the **Event & show** card.
2. Click **Next day (same event)**. A small form opens.
3. Check the **Day name**. Stagewatch suggests the next number, such as `Day 2`.
4. Check the **Date**, written day first (dd/mm/yyyy). The day is written out under the box, for
   example `Sat 3 Oct 2026`.
5. Click **Start next day**.
6. Click **OK** to confirm.

**What you'll see:** the new day name on the card. Every dashboard reloads by itself and shows the new
day. Its chart, markers and alarm log start empty. The previous day is kept under **Previous
shows**.

You can do this late at night. After midnight but before the **New show day starts at** time,
Stagewatch still suggests tomorrow's date, not the night you are finishing.

### Start a new event

For a new festival or a new tour.

1. Click **New event…** on the **Event & show** card.
2. Type the **New event name**.
3. Check the **First day name** and the **Date**.
4. Click **Start new event**.
5. Click **OK** to confirm.

The current event ends. Its days stay listed under **Previous shows**, grouped by event.

### Fix a name or a date

1. Open **Rename or change the date** on the same card.
2. Change the **Event name**, the **Day name** or the **Day date**.
3. Click the button next to it: **Save event name**, **Save day name** or **Save date**.

If you set the date by hand, **Use the start date** puts it back to the date the day started on.

### Previous shows

**Previous shows (n)** lists every earlier day, grouped by event, with the date it was started.

### Event logo

Open **Event logo** in the Event & show card to show your event's logo beside the title on every
dashboard, on the wall screen, and (if you tick them) on the start page and in Admin.

- Use a **still** PNG, JPEG or WebP image, up to **1 MB** and between 32 and 2048 pixels on each side.
  Animated images, GIF, HEIC and SVG are not accepted yet.
- **Logo for light screens** is the main one. **Logo for dark screens** is optional: without it,
  choose how the light logo looks on dark screens (as it is, colours inverted, or on a white plate).
- Each event has its own logo. Nothing shows until you upload one. When you start a new event,
  **Use previous event's logo** copies the last one across.
- Open screens reload by themselves when you change the logo.
- The file is shown exactly as uploaded to anyone on the show network. Photos can hold hidden
  details, such as where they were taken: export the logo from a graphics program, not a camera.
- Only the current event's logo is shown to the public pages. Logos are kept in the `branding`
  folder of the data folder and are included in update backups.

### If it didn't work

If two people click start at the same moment, only one new day is started. The other gets
`A new show was already started` and nothing more changes. Reload the page.

## The Schedule card in Admin

The **Schedule** card shows the day, how many items there are, and what is on **NOW** and **NEXT**.
Click **Open schedule editor** to change the running order. The editor has its own page and uses
the same PIN. The steps for adding items, importing a running order, and exporting or printing it
are on [the Schedule page](../cards/schedule.md#edit-the-schedule-for-the-person-who-looks-after-stagewatch).

## Tested status

**Tested on hardware: pending.** The Site settings, the Event & show card and the event logo have been checked with
simulated data (emulate mode), not yet through a full show run on real equipment.

Related: [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).
