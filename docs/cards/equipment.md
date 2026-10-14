# Equipment

The **Equipment** card shows readings from sensors that watch **gear**, not the air: an amp rack,
a power supply, a fan, a heatsink. It is not on any dashboard by default, and it stays **hidden
until at least one sensor has the Equipment role**.

Stagewatch is an **advisory tool**. It shows what the sensors say. It does not control any gear.
Back to [Dashboard cards](README.md).

## Environment and Equipment

Stagewatch treats a temperature as the air at the site and averages it with the others. That would
be wrong for the temperature of an amp rack. A **role** tells Stagewatch which is which.

| Role | What it is | What it feeds |
|---|---|---|
| **Environment** | The air at the site: temperature, humidity, pressure. Every node starts as Environment. | The site average, the speed of sound, the dew point and the [Barometer](barometer.md). |
| **Equipment** | Gear: a rack, a power supply, a fan. | Only its own readings. |

**An Equipment reading can never move a site value.** It is never averaged, and never used for the
speed of sound, the dew point, the barometer or the accuracy weighting. This happens by itself.
There is no tick box to forget. A hot rack cannot pull the site temperature up.

Power meters from Shelly are Equipment too, but they have their own [Power](power.md) card.

## What the card shows

Readings are grouped by node. Each node has:

- **The node's name**, with its status beside it (**ok**, **missing**, **sleeping** and so on, as in
  [Sensors](sensors.md#what-the-status-means)).
- **One small tile per reading**, such as **Temperature**, **Pressure** or **Humidity**. A node
  with several readings shows them side by side, and wraps onto more lines only when it needs to.
- **The value with its unit**, for example `38.4 °C`.
- **A line under the value** that says how fresh it is.

| Line under the value | What it means |
|---|---|
| `12s ago` | A current reading, and how long ago it arrived. |
| `Old reading, 4m ago` | The sensor has stopped reporting. The value is dimmed and shown with its age. It is the last real reading, not a guess. |
| `Sleeping, last reading 6m ago` | A node that sleeps between readings, within its normal sleep time. See [Sensors](sensors.md#nodes-that-sleep-between-readings). |

A missing value shows as **—**, never as zero.

A `*` after a value means a calibration offset has been applied to that sensor. See
[Sensors](sensors.md#the-asterisk-calibration-offset).

The card does not show Wi-Fi signal or battery. Those are on the [Sensors](sensors.md) card.

## Where else Equipment appears

- **Sensors card.** Once you have any Equipment sensor, the table splits under **Environment** and
  **Equipment** headings.
- **History chart.** It draws only Environment sensors, unless you tick **Show equipment sensors**
  under the graph. Equipment sensors then appear as dashed lines. See [History chart](chart.md#show-equipment-sensors).
- **Alarms.** Threshold alarms work for both roles. For a rack, your tech can set an absolute limit
  on its own sensor, for example above 45 °C. It does not depend on the site average.

## What your tech can change in Admin

- Set a node's role when you adopt it, or later: **Admin → ESPHome nodes** (**Role**). See
  [ESPHome](../integrations/esphome.md).
- Set the role of one sensor on a node: **Admin → Sensors: calibration & averaging** (**Role**).
  See [Sensors and alarms](../admin/sensors-and-alarms.md).
- Add the card to a dashboard: **Admin → User dashboards → Edit cards**. See
  [Dashboards](../admin/dashboards.md).
- A temperature limit for a rack: **Admin → Threshold alarms**.

If you go back to an older version of Stagewatch, it does not know about roles and averages every
sensor again. Set the roles again after you upgrade.

## How to check it worked

1. Set a node to **Equipment**.
2. Watch the **Temperature** tile on the [Tiles](env-tiles.md) card.

**What you'll see:** the count under it (for example `3 sensor(s) averaged`) goes down by one, and
the value stops following that node. The node's readings now appear on the Equipment card.

## If it didn't work

- **The card is missing.** No sensor has the Equipment role yet, or the dashboard does not have the
  card ticked. Ask your tech.
- **A reading says Old reading.** The node has stopped reporting. Check it on the
  [Sensors](sensors.md) card.

## Tested status

**Tested on hardware: pending.** The Equipment card has been checked with a simulated rack node
("Rack 1 (sim)" in emulate mode). A TMP117 rack-temperature node file exists but has not yet been
tested on hardware. See [Sensor nodes](../nodes/README.md).

Related: [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).
