# Sensors card and the dashboard header

This page has two parts. The first is the **header**, the bar along the top of every dashboard.
The second is the **Sensors** card, the list of sensor nodes and their latest readings.

Stagewatch is an **advisory tool**. It reads sensors and shows what they say. It does not
control anything. Back to [Dashboard cards](README.md).

## The dashboard header

The header is on every dashboard. It is not a card, so you cannot switch it off.

From left to right it shows:

- **Site name.** Under or beside it, the dashboard, event and day, for example
  `FOH · Summer Festival · Day 2`.
- **Messages bell.** How many messages there are, and how serious the worst one is.
- **Status button.** **Online**, **Offline** or **Error** for this screen.
- **Day / Night button.** Switches between a light and a dark look. Until you tap it, the screen
  follows the tablet's own setting. Each screen remembers its choice.
- **Alarm sound button.** Says **Alarm sound: On** or **Alarm sound: Off**.
- **Clock.** The time at the show site, 24-hour, with seconds. It is the same on every screen,
  even if a tablet's own clock is wrong.

On a tablet or phone the header stays at the top while you scroll. A wall screen does not scroll,
so its header stays where it is.

### Messages bell

The bell lists the same items as the alarm bar, so the two always agree. It never makes up a
message. Every message has a symbol and a word as well as a colour.

| Bell | Symbol and word | What it means |
|---|---|---|
| Red | **!** Alarm | An alarm that nobody has acknowledged yet. |
| Amber | **▲** Warning | An alarm that has been acknowledged but is still active. |
| Blue | **i** Information | A quiet notice. It never beeps. |
| Grey, no number | None | Nothing is raised. |

The number beside the bell is how many messages there are. The bell takes the colour of the worst
one. While an alarm is sounding, the bell is always red.

1. Tap the bell. A panel opens.
2. Read the list. The worst messages come first, and the newest first among those. Each line shows
   how long ago it started. A line marked **(older)** has been quiet for a while.
3. Tap the bell again, press Esc, or tap anywhere else to close the panel.

**What you'll see:** **No messages** when there is nothing to report. The panel stays open and
keeps itself up to date while you read it.

The wall screen does not show the bell. It shows the alarm bar instead.

### Status button: Online, Offline or Error

This is about **this screen's link to Stagewatch**. It replaces the small green and red dot that
older versions had.

| Status | Colour and symbol | What it means |
|---|---|---|
| **Online** | Green ● | Live data is arriving. |
| **Offline** | Amber ✕ | The link is lost, or nothing has arrived for 40 seconds. The figures on screen are frozen. |
| **Error** | Red ! | Stagewatch answered, but something is wrong with the data. |

Stagewatch never shows **Online** while the figures are frozen. If the link is up but silent, it
says **Offline**.

1. Tap the status button (or press Enter). A panel opens.
2. Read it. It says what is happening, when data last arrived, how long the screen has been
   connected or disconnected, and what to try.
3. Tap again, press Esc, or tap elsewhere to close it.

On a phone only the symbol shows, to save room. Tap it to see the word and the details.

On a wall screen the status shows only when it is **not** online, as a small mark and word. There
is nothing to tap.

**Disconnected banner.** If the screen loses contact for more than 3 seconds, a red bar appears
across the top: **Disconnected from Stagewatch — reconnecting…** with a count of seconds. The
numbers, tables and graph turn grey and dim so nobody trusts old readings. The screen reconnects
by itself and the banner goes away.

Cards that read from other software (Ontime, Smaart, GLOBCON) have their own small status
beside their logo. It works the same way, but it is about that source, not the whole screen.

### Alarm sound button

Browsers will not play sound until you have tapped something. **Tap the button once at
soundcheck.** It turns to **Alarm sound: On** and plays a short test beep. Tap it again to mute
that tablet.

- If it says **Alarm sound: Off — tap to turn on** in orange, the tablet has not been given
  permission yet. Tap it.
- Stagewatch remembers your choice on that tablet, for that dashboard.
- On a wall display in full-screen kiosk mode, sound usually works without a tap, and the button
  hides itself once it does.
- While an alarm is sounding, it beeps every 2 seconds until someone acknowledges it.

Do not rely on beeping being heard on a tablet. Turn the volume up, and keep watching the colours.

### If it didn't work

- **The status says Offline.** Check the tablet is on the show Wi-Fi, then reload the page.
- **The red banner will not go away.** Check the Stagewatch computer is on and on the same
  network. See [Troubleshooting](../troubleshooting.md).
- **The bell is missing.** You are on a wall screen. That is normal.

## The Sensors card

The **Sensors** card lists each sensor node, whether it is working, and its latest readings. It
is on tablet dashboards by default.

### Reading the table

| Column | What it shows |
|---|---|
| **Node** | The node's name, with its area (for example "Stage left") underneath. |
| **Status** | The node's health. See the list below. |
| **Temp**, **RH**, **Pressure** | The latest readings, in °C, %, and hPa. A blank means that node does not measure it. |
| **Signal** | Wi-Fi strength in dBm, with a word: **good**, **fair** or **weak**. Shown in amber when weak. Only appears if a node reports it. |
| **Battery** | Battery level in %, with the battery voltage beside it when the node reports one, for example `87 % · 3.98 V`. Shown in amber with the words **getting low** under 20 %. Only appears if a node reports it. Read-only: Stagewatch works out nothing about charging. |
| **Updated** | How long ago the node last reported, for example `12s ago`. |

Signal and battery are for your information. They are never part of the site average.

### What the Status means

| Status | Colour | What it means |
|---|---|---|
| **ok** | Green | Working. |
| **initializing** | Grey | Just started. It has not sent a reading yet. |
| **sleeping** | Grey | A battery node that sleeps between readings, and is within its normal sleep time. Hover or long-press for the age of its last reading. |
| **missing** | Red | Stagewatch cannot reach it. It has lost power or Wi-Fi. Hover or long-press for a short reason, such as "can't reach the node". |
| **fault** | Red | It can be reached, but something is wrong. For example the wrong encryption key, or a different board is answering at its address. |

If a node goes offline, Stagewatch keeps trying to reconnect. When it comes back, it is used
again. A node that is still missing stays marked **missing** here even after its alarm notice
has left the alarm bar.

### Stale readings

Every sensor reports every few seconds. A reading that has not been updated for a while is
**stale**.

- By default a reading older than **60 seconds** is stale. A line under the table says how many
  seconds the limit is on your system.
- Stale readings are **greyed out** and **left out of the site average**.
- A grey dash **—** means there is no current value.
- Stagewatch shows the last real reading, with its age. It never fills in a guess.

Your tech can change the limit in **Admin → Site** (**Stale after (s)**). See
[Site and shows](../admin/site-and-shows.md).

### Nodes that sleep between readings

Some battery nodes switch themselves off between readings. If your tech has told Stagewatch how
long a node sleeps, the node and its sensors say **sleeping** instead of **missing**, no
"node offline" alarm is raised, and the reading shown is the last real one.

- The reading still counts in the site average while it is within 2.5 times the sleep time
  (rounded up to whole minutes, so 5 minutes gives 13).
- After that it goes stale and **missing** as usual, and the alarm is raised.
- A node that has never reported is never shown as sleeping.

**Tested on hardware: pending.** This has only been checked with simulated times, not on a real
deep-sleep node. The setting is **Sleeps between readings** in **Admin → ESPHome nodes**. See
[ESPHome](../integrations/esphome.md).

### The asterisk: calibration offset

A `*` straight after a value means **a calibration offset has been applied** to that sensor. Your
tech checked it against a reference and corrected it. The reading shown already includes the
correction.

- A note under the table says which sensor and by how much, for example
  `* Calibration offset applied: Feather S3 -1.0 hPa`.
- The same `*` appears in the key under the [History chart](chart.md), and after the
  **Site average** line when a sensor in that average has an offset.
- The same `*` appears on the [Equipment card](equipment.md).

When your tech changes an offset, or switches a sensor in or out of the average, Stagewatch adds
a marker to the timeline so everyone can see why a site value stepped. See
[Markers](markers.md#calibration-change-markers).

### Environment and Equipment groups

If any sensor is set to **Equipment** (an amp rack or a power supply, not the air), the table
splits into two headings, **Environment** and **Equipment**. Only Environment sensors feed the
site average. See [Equipment](equipment.md).

Power meters from Shelly are not in this table. They have their own [Power](power.md) card.

### What your tech can change in Admin

- Rename a node, change its area or role, or tell Stagewatch it sleeps: **Admin → ESPHome nodes**.
- Offsets, leaving a sensor out of the average, accuracy: **Admin → Sensors: calibration &
  averaging**. See [Sensors and alarms](../admin/sensors-and-alarms.md).
- How old is stale: **Admin → Site**.
- Alarm when a limit is crossed: **Admin → Threshold alarms**.

### If the table is empty

It says **No sensor nodes yet. An admin can adopt ESPHome nodes.** Your tech needs to build and
add a node. See [Sensor nodes](../nodes/README.md).

## Tested status

**Tested on hardware: pending.** The header and the Sensors card have been checked with
Stagewatch's simulated sensors (emulate mode), not yet with a full set of real nodes at a show.

Related: [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).
