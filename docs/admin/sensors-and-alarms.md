# Admin: Sensors and alarms

This page is for the person who looks after Stagewatch. It covers the Admin cards that decide what
counts in the site average and when Stagewatch raises an alarm:

- **Sensors: calibration & averaging**
- **Calibration & hardware**
- **Threshold alarms**
- **Alarm notices**
- **Alarm log (this show)**

Stagewatch is an **advisory tool**. Limits you set here raise messages on the dashboards. They do
not control anything. Back to [Admin](README.md).

**Changes only stick after you click that card's Save button** (the one exception is the **Weight the
average by accuracy** tick, which saves as soon as you tick it).

## Sensors: calibration & averaging

Open **Admin** and find **Sensors: calibration & averaging**. Sensors are grouped by node, under
**Environment** and **Equipment** headings. Click a node's name to open or close it. The closed line
shows its status and any offset. Stagewatch remembers which nodes you left open. **Expand all** and
**Collapse all** do the lot. A node opens by itself if it has an offset or is not working.

Each sensor has a row:

| Column | What it is |
|---|---|
| **Sensor** | The sensor's name, with its ID under it. |
| **Value** | The latest reading. |
| **Offset** | A correction added to every reading. |
| **Role** | Environment or Equipment. |
| **Average** | Tick to include the sensor in the site average. |
| **Share** | How much the sensor counts in the average right now. |
| **Accuracy**, **Preset** | The maker's "±" figure. |
| **Save** | Saves that row. |

### Stale time

A reading older than **Stale after (s)** is stale. It is greyed out and left out of the site
average. It is set in the **Site** card (60 seconds to begin with). See
[Site and shows](site-and-shows.md) and [Sensors: stale readings](../cards/sensors.md#stale-readings).

### Correct a sensor with an offset

Use an offset when you have compared a sensor with a reference you trust, such as a Kestrel, and it
reads high or low.

1. Open the sensor's node.
2. Type the correction in **Offset**. Use a minus sign to take the reading down. An offset is **added**
   to every reading, so a sensor reading 0.4 °C too high needs `-0.4`.
3. Click **Save** on that row.

Units: temperature in °C, humidity in % points, and **pressure in pascals (Pa)**, where 1 hPa is 100
Pa. The unit is shown beside the box.

**What you'll see:** the reading changes by that amount. On the dashboards, a `*` appears after the
sensor's value, with a note under the **Sensors** table, so crew know the reading has been adjusted.
The History key and the Site average line show it too. See
[Sensors: the asterisk](../cards/sensors.md#the-asterisk-calibration-offset).

The offset stays with the board's hardware (MAC) address, so it follows the board if its IP address
changes.

**Changing an offset leaves a marker.** When you save a different offset, Stagewatch adds one system
marker to the timeline, for example `Calibration changed: Stage Left Temp offset +0.3 °C (was 0 °C)`,
so the crew can see why a site value stepped. Saving without a real change adds nothing, and a new
sensor's first settings are not a change. See [Markers](../cards/markers.md#calibration-change-markers).

### Leave a sensor out of the average

Untick **Average** on the sensor's row and click **Save**. Use it for a sensor in direct sun, or one
next to a heat source. Stagewatch adds a marker for this too ("… now left out of the site average").

On an **Equipment** row the **Average** tick and the accuracy boxes are greyed out, with the words
`Equipment readings are never averaged`. The offset still works.

### Role: Environment or Equipment

Every node has a role, and a single sensor can differ from its node.

1. Find the sensor's row.
2. In **Role**, choose **Environment** or **Equipment**. The first choice, `Node's role (…)`, follows
   the node.
3. Click **Save** on that row.

**Environment** feeds the site average, the speed of sound and the barometer. **Equipment** never
does. Set a whole node's role under **ESPHome nodes**: see [ESPHome](../integrations/esphome.md#roles-environment-and-equipment).
What the Equipment card shows is on [the Equipment page](../cards/equipment.md).

### Accuracy and weighting

Every sensor is a little out, and some are more out than others. The maker prints a "±" figure in
the datasheet. You can type it in, so that Stagewatch counts the better sensors for more in the site
average. This is optional and **off** until you switch it on. It only shows on the Admin page, never
on the dashboards.

1. Open a node.
2. In **Accuracy**, type the figure after the **±**. Use °C for temperature, %RH for humidity and
   **hPa** for pressure (not Pa, unlike the offset). For example `0.5` for ±0.5 °C. Stagewatch
   accepts 0.01 to 20 °C, 0.1 to 30 %RH and 0.01 to 50 hPa.
3. Pick **typical** or **maximum**. Most datasheets headline the typical figure. If you are not
   sure, leave it on **typical**.
4. Or choose your part from **Preset…**. It fills in the typical figure from the maker's page. Check
   it against your sensor's datasheet and change it if you know better. If the maker's page gave no
   clear figure, the preset leaves the box empty and says so: type it yourself.
5. Click **Save** on that row.

**What you'll see:** a line such as `Accuracy ±1 %RH (typical)` under the value.

To use the figures, tick **Weight the average by accuracy** at the top of the card. It saves at
once. A sensor with a smaller figure then counts for more: a sensor of ±0.1 °C counts 4 times as
much as one of ±0.2 °C. **No sensor ever counts for more than 80 %**, however good its figure, so the
site average still covers every place you put a sensor. The **Share** column shows how much each
sensor counts now, as a percentage, and `(capped)` when the 80 % limit is holding a sensor back. With
the switch off, every sensor in the average shares it equally.

Stagewatch weights temperature, humidity or pressure only when **every sensor in that average has
an accuracy figure and the figures are all typical or all maximum**. Otherwise that one uses equal
weights, and the card says why, for example `Equal weights: 2 sensors have no accuracy figure`.
A sensor that is stale, left out by its **Average** tick, or thrown out as an outlier does not count,
and the other shares adjust to add up to 100 %. Under **Share**, the words you may see are `Never`
(Equipment), `Stale`, `Left out` and `—` (no reading yet).

Where a sensor sits (sun, stage lights, heat from kit, its own warmth, airflow) usually matters more
than the datasheet figure. Sensors you calibrated against the same reference share that reference's
error, so averaging them does not remove it.

An offset and an accuracy figure do different jobs. The offset corrects a reading you have checked
against a reference. The accuracy figure says how far the sensor can still be off. After you
calibrate a sensor, type the uncertainty you are left with, not the datasheet figure.

**How to check it worked:** turn the switch on, give two sensors of one kind different figures, and
watch the **Share** column change to match. Turn the switch off and the shares go back to equal.

### Sleeping nodes

If a battery node sleeps between readings, set **Sleeps between readings** under **ESPHome nodes**.
See [ESPHome: sleeping nodes](../integrations/esphome.md#sleeping-nodes).

## Calibration & hardware

An offset belongs to the **board** it was set on, not to the name or address you gave the node. If you
remove a node and add it again, or change its address, the same board keeps its offsets. Open
**Calibration & hardware** to see, for every sensor on a known board:

- the board's hardware address (only ever shown here, never on dashboards),
- the offset in use,
- when it was last saved and how (set by hand, checked against a reference, imported, and so on),
- the history, newest first, with a note field. Type what you checked it against (for example a
  Kestrel) and a short note, then **Add note**. This adds a line to the history and does not change
  the offset. Change the offset itself under **Sensors: calibration & averaging**.

**A board that needs a decision.** If a different board answers at a node's address, or a board
already has saved calibration, Stagewatch ignores its readings until you choose. The card says why
and offers the choices (start fresh, keep the calibration, or use the saved calibration).

**Back up or move to another hub.** **Save calibration to a file** downloads every offset with the
hardware address it belongs to. **Read a file** shows what would change, in plain words (new,
changed, for a board this hub does not know yet, and skipped), and changes nothing until you press
**Apply to this hub**. Applying adds an "imported from a file" line to each sensor's history and puts
one marker on the timeline. Sound levels and power meter readings are recorded exactly as received,
so a file can never adjust them. The file contains hardware addresses: keep it private.

## Threshold alarms

A threshold alarm raises a message on the dashboards when a reading goes above or below a limit you
choose. **There are none out of the box.** The only alarm until you add one is "node offline".

Open **Admin** and find **Threshold alarms**.

1. Click **Add threshold**. A row appears.
2. In **Entity**, choose the reading. Choose the ones that start `Site:` to watch the site average.
   Or choose a single sensor's ID, for example a rack's temperature.
3. Type a **Label**. It leads the alarm line, for example `Rack 1: 46.2 above 45`. With no label the
   line starts with the reading's ID.
4. Type a limit in **Above**, **Below**, or both. Leave the other empty.
5. Choose the **Level**: **Advisory**, **Alert** or **Stop**. A new row starts on **Alert**.
6. Optional: set **Hyst.** and **Hold s** (below).
7. Leave **On** ticked.
8. Click **Save thresholds**.

**What you'll see:** `Thresholds saved`. When a reading crosses the limit, the
[alarm bar](../cards/alarm-bar.md) shows it.

To remove a limit, click **✕** on its row and click **Save thresholds**. To switch it off for a
while, untick **On**. Either clears its alarm.

### Units

Values are in the units shown on the dashboards (°C, %, m/s), **except pressure, which is in pascals
(Pa)**. 1,013 hPa is 101,300 Pa.

### Hysteresis and hold

- **Hyst.** (hysteresis) is how far back past the limit a reading must come before the alarm clears.
  With **Above** `35` and **Hyst.** `1`, the alarm clears when the reading is 34 or lower. It stops
  an alarm flicking on and off when a reading hovers around the limit. It starts at 0.
- **Hold s** is how many seconds the reading must stay past the limit before the alarm is raised. Up
  to 3,600. It starts at 0, which means at once.

### How the alarms behave

- An alarm stays on the bar until the reading has come back past the limit by the **Hyst.** amount.
- **Acknowledge** stops the beeping but never clears an alarm. See
  [Alarm bar and notices](../cards/alarm-bar.md#acknowledge).
- A reading that is **stale** neither raises nor clears an alarm. A lost node is reported by its own
  "node offline" alarm instead.
- **Alert** and **Stop** alarms also drop an `ALARM:` marker on the timeline. **Advisory** alarms do
  not.
- Threshold alarms work for both roles. For a rack, set an absolute limit on its own sensor. It does
  not depend on the site average.

## Alarm notices

This card keeps the alarm list on dashboards short. It changes **advisory notices only**. **Alert** and
**Stop** alarms always stay until they clear.

1. Open **Admin** and find **Alarm notices**.
2. Type a number in **Hide acknowledged notices after (minutes, 0 = never)**. It starts at 2. The
   most is 1,440.
3. Type a number in **Move old notices into "Older notices" after (minutes, 0 = never)**. It starts at
   30. The most is 10,080.
4. Click **Save**.

Type a number in both boxes. `0` means never. If a box is empty, the card asks you to fill in both.

What the timers do on a dashboard, with the full rules, is on
[Alarm bar and notices](../cards/alarm-bar.md#how-long-notices-stay-on-screen). The sensor list and
this page still show what is wrong, for example a node marked **missing**, after its notice has left
the bar. Open screens take the new times at once.

## Alarm log

**Alarm log (this show)** lists what alarmed, and when, for the current show day. Each line has the
time, what happened, the level and the message.

- What happened is `raise` (an alarm started), `clear` (it ended), `change` (its text or level
  changed) or `ack` (someone acknowledged it, and from which dashboard).
- A quiet notice from outside software is marked `(silent)`.
- When you start a new show day, the log starts empty. Earlier days are kept under **Previous shows**.

It says `No alarms yet.` when there is nothing to show.

## Tested status

**Tested on hardware: pending.** Offsets, accuracy weighting, threshold alarms and the notice timers
have been checked with simulated sensors and times (emulate mode and automatic tests), not yet at a
full show with real nodes.

Related: [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).
