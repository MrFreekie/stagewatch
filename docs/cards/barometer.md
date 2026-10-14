# Barometer

The **Barometer** card shows what the air pressure at your site is doing. It answers one question:
"is the weather turning, and how fast?". It is not on any dashboard by default.

> **This is a guide, not a forecast.** The card shows a hint worked out from pressure at this site
> only. It can miss fast fronts, thunderstorms and local wind. It does not replace the Met Office
> forecast and warnings, your lightning plan or your event's weather plan. Stagewatch is advisory
> and never controls anything.

Back to [Dashboard cards](README.md).

## What you need

- **A pressure sensor** that is an **Environment** sensor, such as a node with a BME280, DPS310 or
  MS8607. A pressure sensor set to Equipment does not count. See [Equipment](equipment.md).
- **The right altitude** in **Admin → Site**. Pressure drops with height, so Stagewatch converts what
  the sensor reads to sea level using the altitude. See [Set the altitude](#set-the-altitude).
- **About 3 hours of unbroken readings** before the card gives a trend and a hint. This is not a
  fault. See [While it waits](#while-it-waits).

With no pressure sensor, the card says **No pressure sensor. Add a BME280 node to see the
barometer.** On a wall screen the card is hidden instead.

## What the card shows

### Sea-level pressure

The big number is the **sea-level pressure** in hPa, for example `1,013.2`. It is also shown on an
old-style dial on tablets and phones. A wall screen shows big numbers instead of a dial, because a
needle cannot be read from 5 m.

- The traditional words **Stormy**, **Rain**, **Change**, **Fair** and **Very dry** are printed on
  the dial at their old places. They are decoration only, as on an old barometer. The hint does not
  come from them.
- A small marker and thin line on the dial show where the pressure was **3 hours ago**, with the
  label `3 h ago 1,016.4`.
- If the pressure is beyond the ends of the dial (950 to 1,050 hPa), the card says **▲ Off the
  scale**. The number is still right.
- The sea-level figure uses the site temperature averaged over the last hour, and the altitude.
  Without a recent temperature it uses a standard atmosphere and says so.
  It can differ from an airport's reported pressure by a hPa or so.

### The 3-hour change

An arrow, a word and a figure, for example **↘ Falling slowly −1.2 hPa in 3 h**. The words are the
Met Office's:

| Change in 3 hours | Words shown |
|---|---|
| No change | **Steady** |
| 0.1 to 1.5 hPa | **Rising slowly** / **Falling slowly** |
| 1.6 to 3.5 hPa | **Rising** / **Falling** |
| 3.6 to 6.0 hPa | **Rising quickly** / **Falling quickly** |
| More than 6.0 hPa | **Rising very rapidly** / **Falling very rapidly** |

The arrow and the words are always there, so you do not rely on colour. A fast fall also gets a
coloured edge: amber for quickly, orange for very rapidly.

### The falling-quickly flag

When the pressure has fallen by the limit or more in 3 hours, a flag appears at the top of the card:
**▼▼ FALLING QUICKLY** (amber) or **▼▼ FALLING VERY RAPIDLY** (orange), with the words **Weather may
turn soon: check wind, lightning and forecasts.**

- The limit starts at **3.6 hPa** in 3 hours, the Met Office's "falling quickly".
- It is a hint to go and look, never an alarm. It never beeps and never flashes.
- Your tech can change the limit, and can ask for a silent notice. See
  [What your tech can change](#what-your-tech-can-change-in-admin).

### The pressure hint

A short phrase and a small picture for the next few hours, such as **Showers likely** or **Settled
and fine**, with a word for the way it is heading (**improving**, **steady** or **getting worse**).
It is worked out from the pressure, the 3-hour change and the month, using the old Zambretti method.

The hint always carries the line **A guide from pressure at this site only. Not a forecast. Check
the Met Office forecast and warnings.**

The Zambretti method is a century-old paper calculator, and it has odd corners. At some pressures
a falling trend can read better than a steady one, and in winter a rising trend can read worse. Treat the hint as a nudge
to go and look. Pressure readings that cannot be real weather (outside 30 to 110 kPa) are ignored.

## While it waits

Stagewatch never guesses. If it does not have enough readings, it says why.

| What the card says | What it means |
|---|---|
| `Waiting for a pressure reading.` | Nothing has arrived yet. |
| `Collecting pressure history, trend ready about 21:30` | It needs 3 hours of unbroken readings before it gives a change and a hint. After the first hour it also shows `Last hour: −0.6 hPa` as a first idea, without a word. |
| `Pressure gap 14:10 to 15:05, trend ready about 18:05` | The sensor or Stagewatch was off for more than 3 minutes. Stagewatch does not guess what happened in between, so it starts counting 3 hours again. A gap of 3 minutes or less does not cause this. |
| `▲ Pressure last seen 4m ago` | The readings have stopped. The figure is dimmed and the change and hint are hidden. |
| `Not enough readings to give a trend yet.` | The sensor reports too rarely (less often than about every 2 minutes) to measure a 3-hour change reliably. |
| `Sea-level pressure here is approximate above about 500 m.` | At that height the conversion is less accurate. Above about 2,000 m it says `rough`. |
| `Sea-level pressure is approximate: there is no temperature reading.` | No recent site temperature, so it used a standard atmosphere. |

When Stagewatch starts again, it reads its own saved readings back, so a quick restart does not
reset the 3 hours.

## Set the altitude

The sea-level figure is only as good as the altitude. If you know your site's height, type it into
**Admin → Site** (**Altitude (m)**). An easy way to get it right on the day:

1. Look up today's pressure at sea level. Use the Met Office, a weather app, or the nearest
   airport's METAR (the figure after "Q", for example `Q1013`). Use one from the last hour and
   within about 20 km.
2. Open **Admin → Site** and click **Set altitude from today's sea-level pressure…**.
3. Type the figure in hPa, for example `1,013.2`, and click **Work out altitude**.
4. Read the answer. It looks like `That gives an altitude of 110 m (now set to 0 m). Save 110 m?`
   Click **Save 110 m**, or **Cancel** to change nothing.

**What you'll see:** `Site saved`, and the dial reads the figure you typed (within about 0.1 hPa).

The altitude it finds can differ a little from the surveyed height, because it also soaks up any
small error in the pressure sensor. If you know your true height and the readings are off, use a
pressure offset in the sensor settings instead.

**If it didn't work:** `No pressure sensor reading` means no pressure sensor is reporting right now.
`Sea-level pressure must be between 940 and 1,060 hPa` usually means a slipped decimal point.

## What your tech can change in Admin

**Admin → Barometer** has:

- **Hemisphere (for the summer and winter months)**. It decides which months count as summer for the
  hint. The southern setting is a mirrored guess, because the method was built for the UK.
- **Silent alarm and marker when pressure falls quickly.** Off to start with. When on, it adds a
  quiet blue notice to the alarm list and one marker when pressure falls quickly. It never sounds,
  and it cannot be acknowledged.
- **Falls at least this much in 3 h (hPa).** The limit for the flag, from 1.5 to 10. The default is
  3.6.

Add the card to a dashboard in **Admin → User dashboards → Edit cards**. See
[Dashboards](../admin/dashboards.md). Altitude is in **Admin → Site**; see
[Site and shows](../admin/site-and-shows.md).

## Keep the sensor sensible

A cheap BME280 is good at spotting a change but can be about 1 hPa out in absolute terms. Strong
sun on its box can fake a small change, so keep the sensor in the shade.

## Tested status

**Tested on hardware: pending.** The Barometer has been checked with simulated weather (emulate
mode has demo buttons for a settled high, a slow fall, a front, a storm and a sensor dropout). It has
not yet been checked with a real pressure sensor over a real afternoon. Because it needs about 3
hours of readings before it gives a trend, test it at home well before a show.

Related: [Tiles](env-tiles.md), [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).
