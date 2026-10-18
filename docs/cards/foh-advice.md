# FOH advice: Alignment drift and HF air loss

Two cards for FOH and systems engineers. They use the air readings Stagewatch already has.

- **Alignment drift** shows how far each delay path has drifted since you aligned it.
- **HF air loss** shows how much top end (4 kHz and above) the air is estimated to take out at
  FOH and Rear, and how that has changed since soundcheck.

Both cards are switched off until your tech sets them up and adds them to a dashboard. Back to
[Dashboard cards](README.md).

> **Advice only.** Stagewatch never changes a delay, a processor or the mix. It is not a
> measurement, not a hearing-safety tool and not a noise-limit tool. Check with your own
> measurement tools and your ears before you change anything.
>
> **Tested on hardware: pending.** Checked in emulate mode and against hand-calculated figures.
> Not yet tried through a real show.

## Use the cards on the day

### Set the Aligned marker when your delays are right

1. Align your system as you normally do.
2. On the [Markers](markers.md) card, type **Aligned**.
3. Tap **Add marker**.

**What you'll see:** the **Alignment drift** header says `Since Aligned at 14:05`, with the change
in temperature and humidity since then. Every path starts at about **0.00 ms**.

### Set the Soundcheck marker at soundcheck

1. When soundcheck sounds right, type **Soundcheck** on the **Markers** card.
2. Tap **Add marker**.

**What you'll see:** the **HF air loss** header says `Compared with Soundcheck at 15:10`, with the
temperature and humidity then and now.

### Start again after you re-align

Add a new marker with the same name, for example **Aligned** again after you re-time the delays.
The card uses the newest marker of that name and starts from zero. The old one stays in the history.

Things to know about markers:

- Only markers from **today's show** count. Yesterday's marker is not used.
- The name only has to **start** with the text, and capitals do not matter. **Aligned after rain**
  works.
- No marker yet? The card says so and shows no numbers.
- If nothing was recorded around the time of the marker, the card says `No readings recorded at`
  the marker's name and time.

## What the Alignment drift card shows

One row per delay path (a delay tower, a front fill, sub to tops).

| Column | What it means |
|---|---|
| **Path** | The name your tech gave it. |
| **Distance** | The distance entered for that path. |
| **Drift** | How much later (+) or earlier (-) sound now arrives than when you set **Aligned**, in milliseconds. |
| **Now set** | The delay your tech says is set now. A dash if not entered. |
| **Suggested** | **Now set** plus the drift. This is a number to read, nothing is sent anywhere. |
| **Status** | **OK**, or **▲ CHECK** when the drift has passed the warn limit. Never red. |

- **Plus** means the air is cooler and sound is slower. **Minus** means warmer and faster.
- On a phone you see one line per path. On the wall screen you see the three paths that have drifted
  most.
- Small notes appear under the table when needed: `No humidity reading: 50 % assumed`, `No pressure
  reading: estimated from the site height`, `Site average used`, and `Outside the model's range:
  treat as rough`.
- If the readings go old, the rows grey out and a note says how old the last one was.

### How big is a drift?

Sound takes about 1.9 ms longer over 60 m when the air cools from 26 °C to 20 °C. As a guide,
**0.5 ms is about 1.6 °C of change at 60 m**.

### The silent drift notice

When a path stays past its warn limit for the hold time (2 minutes unless your tech changed it),
Stagewatch adds a quiet line to the alarm list: `Delay tower L: drifted +1.1 ms since Aligned,
check delay (ADVISORY)`.

- It is **silent** by default: no beep. Your tech can choose **Beep** (the normal advisory sound) or
  **Off**.
- It is never an Alert or Stop.
- It clears by itself when the drift falls back to 80 % of the limit or less.
- If your tech ticked **Add a marker when a notice shows**, one marker is added each time it shows,
  for example `Drift: Delay tower L +1.1 ms`.
- The warn limit is set per path, from 0.2 to 10 ms (1 ms to start with). Empty means no notice.

## What the HF air loss card shows

Air takes out more high frequency over a long distance. How much depends on temperature, humidity
and pressure, so the top end can change through the day without anyone touching the system.

For each position (FOH, Rear) and each band you see:

- A signed figure in dB: the **change since the Soundcheck marker**. Plus means **more** top end
  is lost now. Minus means **less**.
- Words next to it: `about the same` (under 0.5 dB), `slightly more` or `slightly less top end
  lost` (under 2 dB), and `clearly` more or less above that.
- A small grey line such as `now 4.7 dB over 40 m`: the total loss over that distance in today's air.

Example: the evening turns damp and cool. At 8 kHz over 60 m the air can take out about
**0.95 dB less** than at soundcheck. A damp, cooler evening does not always mean a duller top end.
Warm, drying air and cold, dry air lose more. The card shows the sign the figures give, not what
you might expect.

### Octave or third-octave bands

- **Octave** shows 4 kHz, 8 kHz and 16 kHz.
- **Third-octave** shows 4, 5, 6.3, 8, 10, 12.5 and 16 kHz.
- Your tech picks the default. On the card, tap **Octave** or **Third-octave** to change just
  that screen.
- Bands start at 4 kHz. Below that the air loss is small and the card does not show it.
- Each figure is estimated at the **middle** of its band, not across the whole band.
- Phone: you see the 8 kHz line for each position. Tap the line to open the other bands.
- A change of about 1 dB is often given as the smallest a listener can notice. That is a rule of
  thumb, and a change in the top octave alone may be harder to hear.

### When it shows no numbers

- `Needs a humidity reading`: air loss cannot be worked out without humidity. Check the
  [Sensors](sensors.md) card.
- `Needs a temperature reading`: same, for temperature.
- `The reference marker has no usable temperature or humidity reading`: add a new **Soundcheck**
  marker while the sensors are reporting.

## Limits

Please read these before you act on a figure.

- Both cards use the **site average** temperature, humidity and pressure. The one exception: a
  delay path can use the sensors in a named area instead (set by your tech), and falls back to the
  site average for any reading it is missing.
- They assume the **air is the same all the way** along the path.
- **Not included:** wind, layers of warm and cold air, a hot stage or cold field, the ground, rig
  height and the crowd.
- Air loss is **estimated at the middle of each band**.
- Both are advice. They are not a hearing-safety tool and not a noise-limit tool. They do not
  replace your measurement tools or your ears.

## For your tech: set it up

Open **Admin**, **Show** tab, **FOH advice** card.

1. Click **Add path** for each delay path. Type a **Name** and a **Distance (m)**. For a delay
   tower, distance means the **extra path the main PA's sound travels to the alignment point
   compared with the fill's own sound**. For a tower on axis that is about the distance from the main
   PA to the tower. For sub to tops it is the difference between the two paths.
2. Optional: type the **Current delay (ms)** so the card can show a **Suggested** value.
3. Optional: change **Warn at (ms)**, or choose an **Area** to use only that area's air sensors.
4. Click **Add position** for FOH and Rear (up to 4). Type each **Distance from the main PA (m)**.
5. Check **Alignment marker name** (starts as **Aligned**) and **Soundcheck marker name** (starts as
   **Soundcheck**).
6. Choose **Air loss bands**, **Drift notice** (**Off**, **Silent notice** or **Beep**), **Hold time (s)**
   and whether to **Add a marker when a notice shows**.
7. Click **Save**.
8. Under **User dashboards**, click **Edit cards** and add **Alignment drift** and **HF air loss**
   to the dashboards that need them. See [Admin: Dashboards](../admin/dashboards.md).

Limits: up to 12 paths and 4 positions. Distances run from 0.5 to 1,000 m. A card stays hidden
while it has nothing set up.

### Send the numbers out as OSC (optional)

Off unless you tick **FOH advice numbers (values only)** in the **OSC output** card in **Admin**.
Then Stagewatch sends, for each path and position:

| Address | Value |
|---|---|
| `/stagewatch/path/<id>/drift` | Drift in ms (plus means sound arrives later). |
| `/stagewatch/path/<id>/suggested_delay` | Current delay plus drift, in ms. |
| `/stagewatch/position/<id>/change_db` | Change in dB since the marker, one number per band, in band order. |

`<id>` is the short name Stagewatch made from the path or position name. An unknown value is sent as
NaN, never as 0.

These are **values, not commands**. Do not map the suggested delay onto a processor, a desk or
anything that changes sound. The engineer decides. See [OSC output](../integrations/osc-output.md).

## If it didn't work

| What you see | What to do |
|---|---|
| The card is not on the dashboard | Your tech needs to add it under **User dashboards** and set up a path or position first. |
| `No reference marker today` | Add an **Aligned** or **Soundcheck** marker today. Yesterday's does not count. |
| `Needs a humidity reading` | Check that a humidity sensor is reporting on the [Sensors](sensors.md) card. |
| Grey rows and `Old readings` | The sensors have stopped reporting. Check the nodes. |

More help: [Troubleshooting](../troubleshooting.md).

Related: [Markers](markers.md), [Tiles](env-tiles.md), [Glossary](../glossary.md).
