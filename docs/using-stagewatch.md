# Using Stagewatch on show day

Short guide for crew. No installing here. Someone has already set it up.

> Stagewatch is an **advisory tool**. It does not replace your riggers, electricians or
> wind and power plans, and it does not control anything. It only reads the air.
> See the [main README](../README.md#stagewatch).

---

## Open a dashboard

Your tech will give you an address, like `http://192.168.1.50:8080`.

1. Join the show Wi-Fi.
2. Open the address in your browser. You will see tiles for each dashboard.
3. Tap the one for you.

The default dashboards are:

| Address ending | For |
|---|---|
| `/d/foh` | A tablet at FOH. Can add markers and acknowledge alarms. |
| `/d/phone` | Your phone. Can add markers. |
| `/d/wall` | A wall screen. Read only. |

No login needed to look. Your tech may have renamed them.

**Keep it handy:** bookmark it, or add it to your home screen.
On an iPhone or iPad: tap **Share**, then **Add to Home Screen**.
On Android: tap the browser's **menu** (three dots), then **Add to Home screen**.

### Supported browsers

Dashboards work on:

- iPad or iPhone with **iOS 12 or later**. That includes old iPad Air 1 and iPad mini 2 and 3.
- Recent **Chrome**, **Edge** (the newer Chromium one, not the old Windows 10 one), **Firefox** and **Safari**.

The **admin page** needs a little newer: **iOS 13 or later** on an iPad or iPhone.

If an old browser cannot run Stagewatch, you get a plain message saying **This browser is too old for
Stagewatch** instead of a blank page. Use another device or update the browser.

---
## What is on the screen

- **Top bar:** the site name, then the dashboard, event and day (for example
  "FOH · Summer Festival · Day 2"), the time, and a small **dot**.
  - **Green dot** = live. The screen is up to date.
  - **Red dot** = the tablet has lost contact. After a few seconds a red bar
    **Disconnected from Stagewatch - reconnecting...** covers the top and the numbers
    turn grey, so nobody trusts old readings. It goes away by itself when the connection
    returns. See [The tablet says it's disconnected](#the-tablet-says-its-disconnected).
- **Alarm bar:** only appears when something is wrong (see [Alarms](#what-the-alarm-colours-mean)).
- **Tiles:** the five numbers below.
- **History:** a graph. Buttons pick **Temp**, **RH**, **Pressure** or **c** (speed of
  sound) and how far back to look: **15m**, **1h**, **4h**, **12h**. The thick line is
  the site average. Thin lines are single sensors. The vertical lines are **markers**.
- **Markers:** add and read markers.
- **Sensors:** each node, whether it is working, and its latest readings.
- **Open on a tablet** (at the bottom, usually on wall screens): this dashboard's address and a
  QR code. Scan it with your phone or tablet camera to open the same dashboard.

Your tech chooses which of these cards each dashboard shows, and in what order. So your screen
may show fewer cards, or show them in a different order. The alarm bar is always there. A
dashboard with no cards shows only alarms.

---

## What the numbers mean

| Tile | In one line |
|---|---|
| **Temperature** | The **site average** of all the sensors, in °C. Hot air makes sound faster. |
| **Humidity** | How much water is in the air, in % relative humidity (RH). It changes the speed of sound only a little. |
| **Pressure** | Air pressure in hPa. It barely changes the speed of sound, but it is shown for the record. |
| **Speed of sound** | How fast sound is travelling right now, in metres per second (m/s), worked out from the readings. Under it is the time sound takes to go one metre. |
| **Dew point** | The temperature at which water would start to condense out of the air. Close to the air temperature means damp air. |

The **site average** is the combined reading from all your sensors. A sensor that has
stopped reporting is left out. One that is wildly different from the rest is also left out (when
there are three or more sensors). The result is smoothed so it does not jump about.

Some notes you may see under a tile:

- `3 sensor(s) averaged`: how many sensors are in the average.
- `no sensor: 50% assumed`: no humidity sensor is reporting, so Stagewatch assumed 50 %.
- `no sensor: from site altitude`: no pressure sensor is reporting, so it used the
  venue's altitude.
- `Outside the formula's tested range (0–30 °C): figures are approximate`, under **Speed of
  sound**: the formula Stagewatch uses is tested from 0 to 30 °C and from 75 to 102 kPa (750 to
  1020 hPa). Outside that, the speed of sound and the drift in ms are still shown and still
  close, but treat them as a guide. It is not an alarm. You may see it on a hot afternoon, a
  frosty night, or at a venue high in the mountains.

---

## Set an "Aligned" marker at soundcheck

**Why it matters.** Sound travels faster in warm air and slower in cold air. Over a
long throw, the arrival time changes as the day warms up or cools down. On a 30 m
delay path, a 10 °C change is about **1.5 ms**. That can move your alignment.

A **marker** is a bookmark in time. When you drop one at soundcheck, Stagewatch remembers
the temperature, humidity, pressure and speed of sound at that moment. It then keeps
telling you how far things have moved since.

**To set one:**

1. Get your system aligned as you normally do.
2. On your dashboard, find **Markers**.
3. In the box, type **Aligned**.
4. Tap **Add marker**.

**What you should see:** a small message "Marker 'Aligned' added", and **Aligned**
appears in the list with its time.

If you cannot see the box, that dashboard is not allowed to add markers. Use another
dashboard (for example FOH), or ask your tech.

You can add other markers too, for example **Doors** or **Headliner**.

### Read how far it has drifted

1. Tap a marker in the list.
2. A panel opens. It says **Since "Aligned" at 14:02** and shows a big number such as
   **+0.412 ms**.

**How to read it:**

- The big number is the **change in sound travel time** since you set the marker,
  over the **reference distance** (30 m unless your tech set another; the panel says
  which one).
- **Plus (+)** means sound now arrives **later** than at the marker. It is colder or the
  air has changed to make it slower.
- **Minus (-)** means sound now arrives **earlier**. It is warmer.
- **0.000 ms** means nothing has moved.
- The little table under it shows **Temp**, **RH**, **Pressure** and **c** at the marker,
  now, and the change.
- A dash **—** means it cannot work it out (for example no sensor was reporting).

Use it as a guide to decide when a re-check of your delays or sub alignment is worth doing.
Stagewatch does **not** change any of your processors. It only tells you.

The **History** graph shows markers as vertical lines. Tap a line to select it.

### Add a note to a marker

A note says what happened at a marker, for example "Delays re-timed after the rain. Subs
unchanged." Anyone on a dashboard that can add markers can write or change a note on **any**
marker.

1. Tap the marker in the list. The drift panel opens.
2. Under it, find **Note**. Tap **Add note** (or **Edit note** if it already has one).
3. Type the note. New lines are fine. It can be up to 1,000 characters.
4. Tap **Save**. To leave it as it was, tap **Cancel**.

**What you should see:** "Note saved", and the note under the drift panel. In the list, the
marker shows **✎ note**. Every open screen shows the new note within a second or two.

### Hide a marker

Hiding takes a marker off the chart and out of the list, to keep a busy day readable. Nothing is
lost: it stays in the history, the drift numbers still work from it, and reports still include
it. Only a person with the admin PIN can delete a marker.

1. Tap the marker in the list.
2. Under **Note**, tap **Hide**.

**To find it again:** under the marker list, tick **Show hidden (2)** (the number is how many
are hidden). Hidden markers show dimmed, marked **Hidden**. Tap **Un-hide** to put one back on
the chart.

### Markers Stagewatch adds by itself

Some markers appear without anyone adding them. Each kind has its own colour and shape on the
chart and in the list:

- **ALARM: …** (orange ▲): an **Alert** or **Stop** alarm started. When someone taps
  **Acknowledge**, Stagewatch hides that alarm's marker, so the chart doesn't fill up with
  alarms already dealt with. Tick **Show hidden** to see them. An alarm that clears by itself,
  without anyone acknowledging it, keeps its marker on the chart.
- **Schedule markers** (blue ◆): if the day has a schedule, Stagewatch adds a marker at each
  soundcheck, at doors, and when each act goes on and comes off stage, for example
  "Headliner: Kestrel Road on stage". See [Markers from the schedule](#markers-from-the-schedule).
- **Stagewatch notes** (grey ■): for example "Stagewatch was off for about 25 min".

---

## What the alarm colours mean

If your tech has set alarm limits, the alarm bar appears at the top of the screen
when a limit is crossed. There are three levels:

| Level | Colour | Meaning |
|---|---|---|
| **Advisory** | Amber (yellow-orange) | Something to be aware of. |
| **Alert** | Orange | Needs attention. |
| **Stop** | Red | Serious. Follow your event's own plan. |

Stagewatch does not decide what you must do. Your event's safety, wind and power plans
do that. See the advisory notice above.

An alarm shows up for two reasons:

- A **limit** was crossed (for example, "Temperature: 36.2 above 35").
- A **node has gone offline**. This shows as an advisory alarm, such as
  "Stage L node: missing - connection lost".

**When an alarm is "sounding"**, the bar pulses and a beep repeats every two seconds.

- **Acknowledge** stops the beeping. The button only shows on dashboards allowed to
  acknowledge (FOH by default). The alarm stays on screen, marked **(acknowledged)**, until
  the problem clears. Acknowledging does **not** fix anything.
- **Alert** and **Stop** alarms also drop a marker on the timeline that starts with **ALARM:**.
  **Acknowledge** hides that marker (it stays in the history: tick **Show hidden** under the
  marker list to see it). If the alarm clears by itself, its marker stays on the chart.

### The sound button

The top bar always has a button that says **Alarm sound: On** or **Alarm sound: Off**.
Browsers block sound until you tap something, so **tap it once at soundcheck**. It turns
to **On** and plays a short test beep. Tap it again to mute this tablet. Stagewatch
remembers your choice on that tablet. If it says **Off - tap to turn on** (in orange), the
tablet has not been given permission yet: tap it.

On a wall display in full-screen kiosk mode, sound usually works without tapping, and the
button hides itself once sound works.
Do not rely on beeping being heard on a tablet. Turn the volume up, and keep watching
the colours.

---

## What "stale" and "offline" mean

Every sensor reports every few seconds. If a reading has not been updated for a while,
it is **stale**.

- By default, a reading older than **60 seconds** is stale. The **Sensors** card says
  this at the bottom.
- Stale readings are **greyed out** and are **left out of the site average**.
- The **Status** next to each node shows:
  - **ok** (green): working.
  - **initializing** (grey): just started.
  - **missing** (red): Stagewatch cannot reach it. It has lost power or Wi-Fi.
  - **fault** (red): it is reachable but something is wrong, such as a wrong key.
- The **Updated** column says how long ago each node reported, for example `12s ago`.
- A tile with a dash **—** and grey text has no current value.

If a node goes offline, Stagewatch keeps trying to reconnect. Once it comes back, it is
used again.

---

## The tablet says it's disconnected

That is the **red dot** in the top bar. It means this tablet has lost contact with the
Stagewatch computer. The screen retries by itself every few seconds.

Try in this order:

1. Check the tablet is on the **show Wi-Fi**.
2. Reload the page.
3. Check the Stagewatch computer is on and on the same network.
4. Still stuck? See [Troubleshooting](troubleshooting.md).

---

## Admin tasks, in brief

Admin is for the person who looks after Stagewatch. It needs the **admin PIN**.

Open the address plus `/admin` (for example `http://192.168.1.50:8080/admin`). Log in. Cards
on the page:

| Card | What you do there |
|---|---|
| **Site** | Set the show name, **altitude** (used only if there is no pressure sensor), the **reference distance** for the "Δ ms" number (default 30 m), how quickly readings go **stale** (default 60 s), and the smoothing. Set the **Time zone** of the show site (for example Europe/London): every dashboard then shows site time, 24-hour, even on a tablet set to another zone. **New show day starts at** (default 06:00) decides which day late-night times belong to: with 06:00, 01:30 still counts as the night before. Keep it at 03:00 or later, so it stays clear of the hour when the clocks change. **Warning times (minutes)** (default `15, 5`) are the minutes before an item ends when the schedule card changes colour: amber at the first time, orange at each later one, with a tag such as "5 MIN". The next item turns amber within the last time. Type up to 8 different whole numbers from 1 to 240, separated by commas. Add `!` after a time to make the card flash at that time, for example `15, 5, 1!`. The flash is a slow pulse, and stays still on devices set to reduce motion. This applies to every dashboard. **Save site**. |
| **Event & show** | Shows the current event and day. Start each new show day here: see [Start a new day or a new event](#start-a-new-day-or-a-new-event). |
| **Schedule** | Shows the day, how many items there are, and what is on NOW and NEXT. To change the running order, go to **Admin → Schedule → Open schedule editor**. See [Edit the schedule](#edit-the-schedule). |
| **Software** | Check for and install updates. See [Updating and backups](updating-and-backups.md). |
| **ESPHome nodes** | Adopt new sensor nodes. See [First sensor node](first-sensor-node.md). Rename a node, change its area, or remove it. On a shared network the **Discovered node** list can fill up with other people's devices. Click **Ignore** next to one to hide it. Stagewatch never connects to a node you have not adopted, so ignoring only tidies the list. To bring one back, click **Show ignored** under the list, then **Unignore**. |
| **Sensors: calibration & averaging** | Correct a sensor with an **offset**. Untick **Average** to leave one out of the site average. |
| **Threshold alarms** | Add limits. Pick the **Entity** (choose the "Site:" ones for the average), the **Above** or **Below** value, the **Level** (Advisory, Alert, Stop), **Hyst.** (how far back past the limit before it clears) and **Hold s** (how many seconds it must last before it alarms). Values are in the units shown on the dashboards, **except pressure, which is in pascals (Pa)**. Click **Save thresholds**. |
| **User dashboards** | Add or remove dashboards. Each has a URL name, a title, a **Layout** (tablet, phone or wall), and ticks for whether people can **Add markers** and **Ack alarms**. Click **Edit cards** to choose what it shows (see [Choose the cards on a dashboard](#choose-the-cards-on-a-dashboard)). Click **Save dashboards**. |
| **OSC output** | Send the site average and alarm state to other gear (the desk, for example). See the [main README](../README.md#osc-output). |
| **Security** | Change the admin PIN. |
| **Alarm log** | See what alarmed, and when, in this show. |

**Nothing alarms until you add limits.** Out of the box there are no threshold alarms.
There is only the "node offline" alarm.

Changes only stick after you click that card's **Save** button.

Only a person with the admin PIN can delete a marker (the **✕** appears next to markers
when logged in). Notes, **Hide** and **Un-hide** work on any dashboard that can add markers.

### Start a new day or a new event

An **event** is a festival, a tour leg or a run of shows. Each **day** of it is one show.
Markers, alarms and the history chart belong to the current day.

**Start the next day of the same event** (for example Day 2 of a festival):

1. In **Admin**, find the **Event & show** card.
2. Click **Next day (same event)**. A small form opens.
3. Check the **Day name** (Stagewatch suggests the next number, such as "Day 2") and the
   **Date**. The day is written out under the date box, for example "Sat 3 Oct 2026".
4. Click **Start next day**, then **OK** to confirm.

You'll see the new day name on the card. Every dashboard reloads by itself and shows the new
day in its top bar. Its chart, markers and alarm log start empty. The previous day is kept
under **Previous shows**.

You can do this late at night. After midnight, but before the **New show day starts at** time
in the **Site** card, Stagewatch still suggests tomorrow's date, not the night you are
finishing.

**Start a new event** (a new festival or a new tour):

1. Click **New event…** on the **Event & show** card.
2. Type the **New event name**. Check the **First day name** and the **Date**.
3. Click **Start new event**, then **OK** to confirm.

The current event ends, and its days stay listed under **Previous shows**, grouped by event.

**Fix a name or a date:** open **Rename or change the date** on the same card. Change the
**Event name**, the **Day name** or the **Day date**, and click the **Save** button next to it.
If you set the date by hand, **Use the start date** puts it back.

If two people click start at the same moment, only one new day is started. The other gets a
message that a new show was already started, and nothing more changes.

### Edit the schedule

The schedule has its own page, so Admin stays short. It uses the same admin PIN.

1. Open **Admin → Schedule → Open schedule editor**. (You can also go straight to the address
   plus `/schedule`. If you are not logged in, Stagewatch asks for the PIN and then brings you
   back.)
2. Click **Add item**. Set the start time (24-hour, for example 19:30), the end time if you
   want one, the kind, the title and the stage. Leave **Stage** empty for items that apply to
   every stage.
3. Click **Setlist** to type a setlist. A preview shows how it will look on dashboards.
4. Use **▲** and **▼** to put items that start at the same time in the order you want.
5. Click **Save schedule**.

To bring in a running order, open **Paste or import a running order**, paste it (or open a
CSV or text file) and click **Preview**. Lines Stagewatch can't read are listed by line number.
Then add the rest. **Load demo day** fills in an example around the current time.

- **Export CSV** downloads the list as a file, named like `schedule-summer-festival-2026-10-02.csv`.
  It opens in Excel and can be pasted or opened here again. It has no setlists.
- **Print** opens your browser's print window with a clean black-on-white running order, with
  each act's setlist underneath. Choose **Save as PDF** there if you want a file instead.
- Export and Print use the list as it is on the page, including changes you have not saved.

**If it didn't work:** if someone else changed the schedule while you were editing, Stagewatch
says so and keeps what you typed. Click **Reload** to see their version. See
[Troubleshooting](troubleshooting.md).

### Markers from the schedule

With **Add markers from the schedule** ticked (at the top of the schedule page, on by default),
Stagewatch puts a blue ◆ marker on the chart as each moment of the running order arrives:

| Line in the schedule | Marker | When |
|---|---|---|
| Soundcheck "Headliner" | **Soundcheck: Headliner** (just the title if it already says soundcheck) | its start |
| Doors | **Doors** | its start |
| Act "Kestrel Road" | **Kestrel Road on stage** | its start |
| Act "Kestrel Road" | **Kestrel Road off stage** | its end, only if the act has an end time or the curfew cuts it off |

If a line has a stage, the stage follows in brackets: "Doors (Main stage)". Markers are placed at
the **planned** times, not when someone presses a button.

**Choose which lines add markers:** each line has a **Marker** tick box. It starts ticked for
soundcheck, doors and act lines, and unticked for everything else. Untick it to leave a line
out, or tick it on any other line (a changeover, for example) to get a marker with that line's
title at its start. If you haven't touched the box, changing the line's kind changes it too.
Click **Save schedule** to keep your choices. To stop all schedule markers, untick **Add
markers from the schedule**. That saves straight away.

Good to know:

- Each moment gets one marker, once. Saving the schedule again, or restarting Stagewatch, never
  adds a second one.
- If you move a line's time **before** it happens, the marker comes at the new time. Markers
  already on the chart stay where they are.
- Markers are placed as time passes, nothing is assumed. If Stagewatch was off when a moment
  passed, that moment gets no marker, now or later. The gap in the chart and the "Stagewatch
  was off" marker show it.
- A moment that passes while its marker is switched off gets no marker, even if you switch it on
  afterwards.
- Schedule markers can be hidden, given notes or deleted like any other marker.

### Choose the cards on a dashboard

Each dashboard shows a set of **cards**, such as the readings tiles, the history chart or the
sensor list. You choose which ones, and their order, for each dashboard.

1. Open **Admin** and find the **User dashboards** card.
2. Click **Edit cards** on the dashboard's row. The number on the button is how many cards it
   shows now. A list of all cards opens under the row.
3. Tick the cards you want. Untick the ones you don't.
4. Use **▲** and **▼** to move a card up or down. The top of the list is the top of the screen.
5. Optional: type a **Stage**, for example **Main stage**. Pick from the suggestions to match
   the names you already use. Cards that follow one stage (such as the schedule) then show only
   that stage. Leave it empty for every stage.
6. Click **Save dashboards**.

**What you'll see:** "Dashboards saved". Open screens reload by themselves within a few seconds
and show the new cards.

Good to know:

- A **new** dashboard starts with a sensible set for its layout. If you change its **Layout**
  before saving, the ticks change to that layout's set.
- **Wall Clock** is never switched on by default. Tick it if you want it, then choose its time
  source and look (see below).
- **Barometer** is never switched on by default either. It needs a pressure sensor (see below).
- **Schedule** stays hidden until the show has a schedule, even when it is ticked. Add the
  schedule under **Admin → Schedule → Open schedule editor**.
- **Open on a tablet** is always at the bottom of the screen, wherever it is in the list. Tick
  it on any dashboard, not only wall screens, to show its address and QR code.
- With no cards ticked, the dashboard shows only alarms. That's useful for a screen that should
  only shout when something is wrong.
- Dashboards from before this version keep exactly the cards they showed before.

**If it didn't work:** if the screen still shows the old cards, reload the page. See
[Troubleshooting](troubleshooting.md).

### Show the time (Wall Clock card)

The **Wall Clock** card shows the time of day. One time source serves the whole installation. You
choose it in **Admin → Wall Clock → Time source**:

- **Stagewatch PC** (the standard for new installations): the clock of the computer running
  Stagewatch, in the site's time zone. It needs nothing else, and it can't be out of step with
  Stagewatch.
- **Ontime**: the time from [Ontime](https://github.com/cpvalente/ontime), the free rundown and
  show timer, so everyone sees the same clock as the stage manager. Stagewatch only listens to
  Ontime. It never sends anything to it, and it does not read your rundown, timers or messages.

If you already use Ontime, Stagewatch keeps using it after the update. Stagewatch never switches to
the other source by itself: if Ontime stops, the card says so.

1. Open **Admin** and find the **Wall Clock** card.
2. Choose the **Time source**.
3. If you chose Ontime, type the **Ontime address**, for example `http://192.168.1.50:4001` (your
   Ontime computer's address and port). If Ontime runs on this computer, leave
   `http://127.0.0.1:4001`. Click **Test connection**. **What you'll see:** "Ontime 4.14.0 answered."
   (your version number). Optional: change **Warn if more than this many seconds out**. The
   default is 2 seconds.
4. Optional: choose **24-hour** or **12-hour (am/pm)**, tick **Show the date** and tick **Colons blink**. These
   apply to every dashboard.
5. Click **Save**.
6. Under **User dashboards**, click **Edit cards** on a dashboard, tick **Wall Clock**, choose its
   **Wall Clock look** (it appears beside the Wall Clock tick box) and click **Save dashboards**.

The look is set for each dashboard, so the wall can show the ring while a phone shows plain digits:

- **Plain digits**: the time in large digits.
- **LED ring**: an outer ring of 60 lights that fill up through each minute (all dark at :00, all
  lit by :59), an inner ring of 12 brighter hour lights, and the time in the middle. On a card narrower than about 280 pixels it shows plain digits instead.
- **7-segment digits**: the time as on a studio clock, with the unlit segments faintly visible.

The ring and the 7-segment look are always red on black, by day and by night.

**What you'll see:** the time, and "Matches Stagewatch" (Ontime) or "Stagewatch's own clock" (PC)
under it. In the Stagewatch emulate mode the three stock dashboards each show a different look and
the clock runs through "differs", "stale" and "offline" every two minutes, so you can see them all.

What the card tells you. Each of these has words with a ▲, and a dashed outline or a line through
the time, as well as colour:

- **Differs from Stagewatch by +3.2 s**: Ontime is 3.2 seconds ahead of the Stagewatch computer
  (a minus sign means behind). Check the two computers' clocks.
- **Check the time zones**: the difference is whole hours, so one computer is set to a different
  time zone. Ontime shows its own computer's time, so set **Time zone** under **Site** in Stagewatch
  to match.
- **Ontime offline**: Stagewatch can't hear Ontime. A quiet notice also appears in the alarm bar.
  It never makes a sound. Check Ontime is running and the address is right.
- **Stale**: nothing has arrived for 3 seconds. The time is dimmed with a line through it and is
  not live. Don't trust it.

The Stagewatch PC source never shows "Differs", because it is Stagewatch's own time.
Stagewatch only contacts Ontime while a dashboard has the Wall Clock card and Ontime is the source.

**If it didn't work:** see [Troubleshooting](troubleshooting.md).

### Read the pressure (Barometer card)

The **Barometer** card shows what the air pressure at your site is doing. It is for the question "is the
weather turning, and how fast?", on a tablet, a phone or the production wall. It needs at least one pressure
sensor, such as a BME280 node. With none, the card says "No pressure sensor" (and the wall hides it).

**What the card shows**

- **Sea-level pressure**, in hPa, on a dial (tablet and phone) and as a large number. Pressure drops with
  height, so Stagewatch converts what the sensors read to sea level, using the altitude in **Admin → Site** and the
  site temperature averaged over the last hour. The old words (Stormy, Rain, Change, Fair, Very dry) are only
  printed on the dial, as on an old barometer. The thin blue hand is where the pressure was 3 hours ago. If the
  value is off the dial, it says "Off the scale".
- **The 3-hour change**, as an arrow, a word and a figure, for example "▼ Falling quickly −3.9 hPa in 3 h". The
  words are the Met Office's: steady, slowly (0.1 to 1.5 hPa in 3 hours), plain (1.6 to 3.5), quickly (3.6 to 6.0)
  and very rapidly (more than 6.0).
- **FALLING QUICKLY** at the top of the card, with ▼▼ and "Weather may turn soon: check wind, lightning and
  forecasts", when pressure has fallen by 3.6 hPa or more in 3 hours. It is amber, or orange for "very rapidly".
  It is a hint to go and look, never an alarm.
- **Pressure hint**: a short phrase and a picture for the next few hours, such as "Showers likely", worked out from
  the pressure, the 3-hour change and the month (the Zambretti method). It is a guide from pressure at this site
  only. It is not a forecast.

**Odd corners of the hint.** The Zambretti method is a century-old paper calculator and has odd corners. At very low
pressure (below about 962 hPa) and at a few other pressures a falling trend can give a better-sounding hint than a steady
one. In winter, a rising trend can give a worse-sounding hint than a steady one at about 962 to 968 hPa and about 1008 to
1031 hPa (in summer, in a few narrow bands). Pressure readings that are not plausible (outside 30 to 110 kPa) are ignored.
This is one more reason the hint is not a forecast: treat it as a nudge to go and look.

**What it is not.** Stagewatch makes no claim that the hint is right. It can miss fast-moving fronts that pass in
under 3 hours, summer thunderstorms under high pressure (use the lightning plan for those) and local wind. Always
check the Met Office forecast and warnings, and follow your event's weather plan.

**What you'll see while it waits**

- "Collecting pressure history, trend ready about 21:30": the card needs 3 hours of unbroken readings before it gives a change and a hint.
  After an hour it also shows "Last hour: −0.6 hPa" as a first idea, without a word.
- "Pressure gap 14:10 to 15:05, ready about 18:05": the sensors or Stagewatch were off for more than 3 minutes.
  Stagewatch does not guess what happened in between, so it starts counting 3 hours again. A restart of under 3 minutes, or one missed reading,
  two does not cause this: Stagewatch reads its own history back when it starts.
- "▲ Pressure last seen 4m ago": the readings have stopped. The figure is dimmed and the hint is hidden.
- "Not enough readings to give a trend yet": the sensor is reporting too rarely (less often than about every 2 minutes) to
  measure a 3-hour change reliably. Stagewatch will not guess; it shows no trend and no hint until there is enough.
- "Sea-level pressure here is approximate above about 500 m": above that height the conversion is less accurate.
  It also says so when there is no temperature reading.

**Set it up**

1. In **Admin → Site**, check the **Altitude** is right for your site (see the next steps for an easy way).
2. In **Admin → User dashboards → Edit cards**, tick **Barometer** and click **Save dashboards**.
3. Optional: in **Admin → Barometer**, choose the hemisphere (it decides which months count as summer for the
   hint) and, if you want it, tick the quiet notice. That adds a silent line to the alarm list and one marker when
   pressure falls quickly. It never sounds.

**Set the altitude from today's sea-level pressure**

1. Look up today's pressure at sea level: the Met Office, a weather app, or the nearest airport's METAR (the
   figure after "Q", for example Q1013). Use one from the last hour and within about 20 km.
2. In **Admin → Site**, click **Set altitude from today's sea-level pressure…**.
3. Type the figure in hPa, for example `1,013.2`, and click **Work out altitude**.
4. Read the answer. It looks like "That gives an altitude of 110 m (now set to 0 m). Save 110 m?" Click **Save 110 m**,
   or **Cancel** to change nothing.

**What you'll see:** "Site saved", and the dial reads the figure you typed (within about 0.1 hPa). The altitude it
finds can differ a little from the surveyed height: it also soaks up any small error in the pressure sensor. If you
know your true height and the readings are off, use the pressure offset in the sensor settings instead. The card's
sea-level figure uses the measured temperature, so it can differ from an airport's QNH by a hPa or so.

**If it didn't work:** "No pressure sensor reading" means no pressure sensor is reporting right now. "Sea-level
pressure must be between 940 and 1,060 hPa" usually means a slipped decimal point. See
[Troubleshooting](troubleshooting.md).

A cheap BME280 is good at spotting a change but can be about 1 hPa out in absolute terms, and strong sun on its
box can fake a small change. Keep the sensor in the shade.

---

**Something not right?** [Troubleshooting](troubleshooting.md).
