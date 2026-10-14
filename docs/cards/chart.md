# History chart

The **History** card is a graph of how the readings have moved, with your markers marked on it. It
is on tablet, phone and wall dashboards by default. In **Admin** it is called **History chart**.

Back to [Dashboard cards](README.md).

## What it shows

- **A thick line: the site average.** The combined reading from your Environment sensors. See
  [Tiles](env-tiles.md#the-site-average).
- **Thin lines: single sensors.** One per Environment sensor, named after its node. A key under the
  graph says which colour is which.
- **Vertical lines with tabs: [markers](markers.md).** Tap a tab to select that marker.

The graph shows the **current show day** only. A new day starts with an empty graph. Earlier days
are kept under **Previous shows** in Admin.

## Choose what to look at

Two rows of buttons sit above the graph.

| Buttons | Choices |
|---|---|
| What to plot | **Temp** (°C), **RH** (%), **Pressure** (hPa), **c** (speed of sound, m/s) |
| How far back | **15m**, **1h**, **4h**, **12h** |

1. Tap a button in the first row to change what is plotted.
2. Tap a button in the second row to change how far back it looks.

**What you'll see:** the graph redraws. Stagewatch remembers your choice on that tablet.

**c** shows the site line only, because the speed of sound is worked out from the site average, not
measured by each sensor.

On a long view, each point is an average of a short slice of time, so the graph stays quick to draw.
Every minute the whole graph is refreshed from the saved history.

## How to read it

- **The scale starts at a normal range** (10 to 30 °C, 0 to 100 %, 980 to 1,040 hPa, 335 to
  350 m/s) and widens by itself if the readings go outside it.
- **Time labels** are in site time, 24-hour, and stay on whole hours even when the clocks change.
- **A break in a line** means no reading for more than 5 minutes: the sensor was offline or
  Stagewatch was off. Stagewatch never joins the gap with a guess and never draws a zero.
- **Markers.** Crew marks, alarms, schedule markers and Stagewatch's own notes each have their own
  shape and colour. See [Markers](markers.md#marker-shapes-and-colours). Hidden markers are not
  drawn. The marker you select is drawn solid and keeps its full label. The others are dashed and
  stack in up to three rows, shrinking to a small tab when there is no room.
- **Pressure** is the pressure at your site. For sea-level pressure see [Barometer](barometer.md).
- When the screen loses contact, the graph dims and goes grey.

### The asterisk

A `*` after a name in the key means a **calibration offset** is applied to that sensor. On the
**Site average** line it means a sensor in that average has an offset. See
[Sensors](sensors.md#the-asterisk-calibration-offset).

## Show equipment sensors

If any sensor is set to **Equipment** (an amp rack, a fan, a power supply), a tick box appears
under the graph: **Show equipment sensors**.

- It is off to start with. Each person's choice is remembered on that tablet only.
- Ticked, the graph also draws each Equipment sensor of the chosen kind, for example a rack
  temperature, as its own **dashed** line. The key names it `(equipment)`.
- These lines are **never** part of the site average, the speed of sound or any other site
  figure. They are only there to look at.
- The tick box is hidden when **c** is selected, and when you have no Equipment sensor.

To see an Equipment reading as a number, add the [Equipment](equipment.md) card.

## What your tech can change in Admin

- Whether a dashboard shows this card, and where: **Admin → User dashboards → Edit cards**. See
  [Dashboards](../admin/dashboards.md).
- Which sensors are Environment and which Equipment: **Admin → ESPHome nodes** and **Admin →
  Sensors: calibration & averaging**. See [Sensors and alarms](../admin/sensors-and-alarms.md).
- Time zone, so the labels are in site time: **Admin → Site**.

## If it didn't work

- **The graph is empty.** The show day has only just started, or no sensor is reporting. Check the
  [Sensors](sensors.md) card.
- **A line stops.** That sensor went stale or offline. See [Sensors](sensors.md#stale-readings).
- **No equipment tick box.** No sensor has the Equipment role yet. See [Equipment](equipment.md).

## Tested status

**Tested on hardware: pending.** The chart has been checked with simulated sensors (emulate mode),
not yet with real nodes over a whole show.

Related: [Markers](markers.md), [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).
