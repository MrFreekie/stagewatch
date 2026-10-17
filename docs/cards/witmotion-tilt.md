# Tilt (WitMotion)

The **Tilt** card shows the tilt of each [WitMotion sensor](../integrations/witmotion.md) in large degrees. It is not on any dashboard by default and stays hidden until a sensor has been set up. Your tech adds it from the card picker in [Admin: Dashboards](../admin/dashboards.md).

> **A guide, not a safety system.** The card says so on screen. Use it to see whether something has moved and to help set a hang. It never replaces the rigger's own checks.

Back to [Dashboard cards](README.md).

## What it shows

For each sensor:

- the sensor's **label** (for example "Stage Right hang") and how old its last data is;
- **X (roll), side to side** and/or **Y (pitch), front to back**, in degrees to one decimal place, using the smoothed value. Your tech chooses which axes to watch;
- if your tech set a **target angle**: the target, the difference ("+0.6° from target") and a marker, with a shape and words as well as colour: **✓ On target**, **◆ Close** or **✕ Off target**;
- if your tech set an alarm **threshold** on the tilt: the number turns amber, orange or red with the words **ADVISORY**, **ALERT** or **STOP**;
- in small print: what the sensor says about its own setup (return rate, bandwidth, install direction, axis mode), its battery and its body temperature.

Which way X and Y point in the room depends on how the sensor is fixed. Tilt it and see which number moves.

## No data

If the link is lost or the sensor goes quiet, the card shows **No data** with no colour. It never shows the last angle as if it were live. When the sensor comes back the smoothing starts again, so the number appears a moment later.

## Small print

- **unknown** means the sensor did not answer that question. Nothing is guessed.
- The battery shows **not available for this sensor model** on the WT9011DCL.
- **Sensor body** is the temperature of the sensor's own chip, not the air.
- A warning line appears if the sensor sends data too slowly for Stagewatch to tell a quiet sensor from a lost link.

The wall view shows only the big numbers (and the target marker), not the small print.

## The readings

The tilt, acceleration, battery and temperature are recorded as **Equipment**, so they never change a site average, and they are not used for the speed of sound.
