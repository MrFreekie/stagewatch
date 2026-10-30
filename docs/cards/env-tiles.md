# Tiles: temperature, humidity, pressure, speed of sound

The **tiles** show the five numbers that matter for the air at your site. In **Admin** this card is
called **Site readings**. It is on tablet, phone and wall dashboards by default.

Stagewatch is an **advisory tool**. It shows what the sensors say. It does not control
anything. Back to [Dashboard cards](README.md).

## What each tile shows

| Tile | Unit | In one line |
|---|---|---|
| **Temperature** | °C | The [site average](../glossary.md) of the air sensors. Hot air makes sound faster. |
| **Humidity** | % | How much water is in the air (relative humidity). It changes the speed of sound only a little. |
| **Pressure** | hPa | Air pressure at your site. It barely changes the speed of sound, but it is shown for the record. |
| **Speed of sound** | m/s | How fast sound is travelling right now, worked out from the readings. Under it is the time sound takes to travel one metre, in ms. |
| **Dew point** | °C | The temperature at which water would start to condense out of the air. Close to the air temperature means damp air. |

Why it matters: sound travels faster in warm air and slower in cold air. On a 30 m delay path, a
10 °C change is about 1.5 ms. To see how far your system has drifted since soundcheck, set a
marker. See [Markers](markers.md).

## Today's lows and highs

An extra tile, **Today's lows and highs**, shows the lowest and highest temperature, humidity and
pressure since midnight at your site, with the time of each. It is worked out from the readings
Stagewatch actually recorded. If nothing was recorded for a reading, it shows **n.a.**; Stagewatch
never fills a gap or guesses. The tile is off by default.

## Choosing the readings

**Admin → User dashboards → Edit cards**: tick **Site readings**, then under **Choose readings**
tick the tiles this dashboard shows and use ▲ and ▼ to put them in order. The tiles share the card's
width, so fewer tiles are bigger on a wall screen. Speed of sound is still worked out from
temperature, humidity and pressure even if you hide those tiles. The History chart and the marker
change panel keep every reading. Leaving the default set (the five readings above, in that order)
changes nothing for existing dashboards.

## The site average

The **site average** is the combined reading from all your **Environment** sensors. The small line
under a tile says how many sensors are in it, for example `3 sensor(s) averaged`.

A sensor is left out of the average when:

- **It is stale.** It has not reported for longer than the stale time (60 seconds unless your tech
  changed it). See [Sensors](sensors.md#stale-readings).
- **It is an outlier.** There are three or more sensors, and one is far from the middle value:
  more than about 3 °C for temperature, 15 % for humidity, or 5 hPa for pressure. Your tech can
  switch this off in **Admin → Site** (**Ignore odd readings (3 or more sensors)**).
- **Your tech ticked it out** of the average in Admin.
- **It is an Equipment sensor** (a rack, a fan, a power supply). Gear readings never move a site
  value. See [Equipment](equipment.md).

The result is smoothed, over about 30 seconds unless your tech changed it, so the numbers do
not jump about.

If your tech has applied a calibration offset to a sensor, the reading already includes it. The
`*` that marks an offset is explained in [Sensors](sensors.md#the-asterisk-calibration-offset).

## "Looks warmed by direct sun"

A temperature sensor in direct sun reads its own warm housing, not the air. Stagewatch watches for
this in daytime (08:00 to 19:00 site time). If one sensor reads **4 °C or more above the middle
value of the others for 10 minutes** (it needs at least two others), or **warms by 3 °C or more in
10 minutes while the others do not follow**, a calm note appears under the tiles naming it, and it
is left out of the site temperature while that lasts. If it is your only temperature sensor it is
still used, and the note says so.

It is advice only: no alarm, sound or marker, and nothing is changed. The note goes away on its
own once the sensor is back within 2 °C of the others (or stops warming quickly). To fix it, move
the sensor into shade. In Admin the sensor's share shows **Left out**.

## Notes you may see under a tile

| Note | What it means |
|---|---|
| `3 sensor(s) averaged` | How many sensors are in the temperature average. |
| `2 sensor(s)` | Under **Humidity** or **Pressure**: how many sensors are in that average. |
| `no sensor: 50% assumed` | Under **Humidity**. No humidity sensor is reporting, so Stagewatch assumed 50 %. |
| `no sensor: from site altitude` | Under **Pressure**. No pressure sensor is reporting, so it used the venue's altitude. |
| `2.915 ms per metre` | Under **Speed of sound**. The time sound takes to travel one metre. |
| `Outside the formula's tested range (0–30 °C): figures are approximate` | Under **Speed of sound**. See below. |

### Outside the tested range

The speed-of-sound formula is tested from 0 to 30 °C and from 75 to 102 kPa (750 to 1,020 hPa).
Outside that, the speed of sound and the drift in ms are still shown and still close, but treat
them as a guide. **It is not an alarm.** You may see it on a hot afternoon, a frosty night, or at a
venue high in the mountains.

## What the tile states mean

- **A number in normal text.** A current value.
- **A dash — in grey text.** There is no current value. Check the [Sensors](sensors.md) card.
  Stagewatch never shows a zero in place of a missing value.
- **All numbers dim and grey, with a red bar across the top.** The screen has lost contact with
  Stagewatch. The numbers are old. See [the header](sensors.md#the-dashboard-header).

## Layouts

- **Tablet:** the five tiles at normal size.
- **Phone:** the same tiles with smaller type.
- **Wall:** very large numbers, readable from across the room. The notes under each tile are
  larger too.

## What your tech can change in Admin

- Altitude, stale time, smoothing, outlier rejection: **Admin → Site**. See
  [Site and shows](../admin/site-and-shows.md).
- Offsets, which sensors are in the average, accuracy weighting: **Admin → Sensors: calibration &
  averaging**. See [Sensors and alarms](../admin/sensors-and-alarms.md).
- Whether a dashboard shows the tiles at all: **Admin → User dashboards → Edit cards**. See
  [Dashboards](../admin/dashboards.md).

## Tested status

**Tested on hardware: pending.** The tiles have been checked with simulated sensors (emulate
mode). They have not yet been checked with a full set of real nodes at a show. Treat the numbers
as a guide and check against your own measurements.

Related: [History chart](chart.md), [Markers](markers.md), [Glossary](../glossary.md).
