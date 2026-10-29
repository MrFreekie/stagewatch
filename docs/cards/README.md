# Dashboard cards

A **dashboard** is a page of **cards**. Your tech chooses which cards each dashboard shows and in
what order (see [Admin: Dashboards](../admin/dashboards.md)). So your screen may show fewer cards than
are listed here.

This page is the one list of every card. Each card has its own page.

## Every card

| Card | What it shows | On by default | Half size | Needs |
|---|---|---|---|---|
| [Tiles](env-tiles.md) | The site numbers: temperature, humidity, pressure, speed of sound and dew point | Tablet, phone, wall | No | A sensor node |
| [History](chart.md) | A graph of the site average and single sensors, with markers | Tablet, phone, wall | No | A sensor node |
| [Markers](markers.md) | Add and read markers such as **Aligned**, and the drift since each | Tablet, phone | No | A sensor node |
| [Sensors](sensors.md) | Each node, whether it is working, and its latest readings | Tablet | No | A sensor node |
| [Schedule](schedule.md) | NOW, NEXT and CURFEW, and the running order. Hidden until the show has a schedule | Tablet, phone, wall | No | A schedule (entered in Admin) |
| [Wall Clock](wall-clock.md) | A big clock for a wall screen | No | Yes | Optional: [Ontime](../integrations/ontime.md) |
| [Ontime Timer](ontime-timer.md) | The countdown Ontime is running | No | Yes | [Ontime](../integrations/ontime.md) |
| [Ontime Rundown](ontime-rundown.md) | Event position and ahead or behind, from Ontime | No | Yes | [Ontime](../integrations/ontime.md) |
| Open on a tablet | This dashboard's address and a QR code to open it on a phone or tablet | Wall | No | Nothing |
| [Barometer](barometer.md) | Sea-level pressure dial, 3-hour tendency and a rough outlook | No | No | A pressure sensor |
| [Condensation risk](condensation.md) | Site dew point and how close the air is to it (Dry, Low risk, Near saturation, Condensing), plus an optional check of whether your kit is below the dew point. A guide, not a guarantee | No | No | Temperature and humidity sensors |
| [Equipment](equipment.md) | Readings from Equipment-role sensors, such as amp racks. Hidden while there are none | No | No | An Equipment sensor |
| [Sound level](sound-level.md) | Up to three sound level values from Smaart, and their timeline. Hidden until set up | No | No | [Smaart](../integrations/smaart.md) |
| [Sound level limits](sound-level-limits.md) | The licence limit now, the next change and Smaart's live value beside it. Public wording only | No | No | [Smaart](../integrations/smaart.md) and limits entered in Admin |
| [DirectOut GLOBCON](globcon.md) | Level meters for 4, 8 or 16 channels of one controller. Hidden until GLOBCON has answered | No | Yes | [DirectOut GLOBCON](../integrations/globcon.md) |
| [RME interfaces](rme-units.md) | One tile per RME unit, such as a 12Mic: status word, temperature, power supplies, clock, preset and AVB streams. Hidden until a unit is added | No | Yes | [RME interfaces](../integrations/rme.md) |
| [Power](power.md) | Watts, volts, amps and energy from power meters. Hidden until a meter is added | No | Yes | [Shelly](../integrations/shelly.md) |
| [Power tree](power-tree.md) | Supplies, units and equipment as a tree: power, load bars and the unmonitored load at each unit (calculated). Several supplies side by side. Needs Power distribution set up | No | No | [Shelly](../integrations/shelly.md) and Power distribution in Admin |
| [Raspberry Pi](raspberry-pi.md) | The health of the Pi Stagewatch runs on: temperature, power supply, load, memory, disk. Hidden unless it runs on a Pi | No | Yes | A Raspberry Pi |
| [Inclinometer (WitMotion)](witmotion-tilt.md) | Roll and/or pitch of each WitMotion tilt sensor, an optional target angle, battery and the sensor's settings. A guide, not a safety system. Hidden until a sensor is set up | No | No | [WitMotion](../integrations/witmotion.md) |
| [Alignment drift](foh-advice.md) | How far each delay path has drifted since the **Aligned** marker, with a suggested delay. Advice only. Hidden until a path is set up | No | No | A sensor node and paths entered in Admin |
| [HF air loss](foh-advice.md) | Estimated change in top end lost to the air at FOH and Rear since the **Soundcheck** marker. Advice only. Hidden until a position is set up | No | No | A sensor node with humidity and positions entered in Admin |
| [Onsite contacts](contacts.md) | Key people for the event: job title, name, radio and area. Phone numbers only where allowed | No | No | Contacts entered in Admin |
| [Wi-Fi](wifi.md) | The crew internet: network name, password and a QR code to join. Only on dashboards that have it | No | No | Networks entered in Admin |
| [About Stagewatch](about.md) | A small advert: the name, one line and a QR code to the Stagewatch project page | No | Yes | Nothing |
| [Plans on file](documents.md) | The event's safety and risk documents shared with this dashboard. Titles only on the wall | No | No | Documents uploaded in Admin |

"On by default" is what a new dashboard of that layout starts with. A dashboard from an earlier
version keeps the cards it already had.

## Admin-only cards

These are on the **Admin** page, not on a dashboard.

- [Device errors](device-errors.md): how many problems each device had in a show. Experimental. Imports from
  [Import a log file](../admin/import-log.md).

## Always there

These are on every dashboard and are not cards.

- **Alarm bar.** Appears only when something is wrong. See [Alarm bar and notices](alarm-bar.md).
- **Messages bell.** Counts the messages and shows the worst kind. Not shown on the wall screen.
- **Connection status.** **Online**, **Offline** or **Error** for the whole screen.
- **Disconnected banner.** A red bar across the top when the screen loses contact with Stagewatch.
  The numbers turn grey so nobody trusts old readings. It clears by itself.
- **Alarm sound button.** Tap it once at soundcheck so the tablet is allowed to beep.

Cards that read from other software (Ontime, Smaart, GLOBCON) also show their own small
**Online**, **Offline** or **Error** beside their logo.

The full explanation of the header, the messages bell, the status marks, the disconnected banner and
the alarm sound button is on [The dashboard header](sensors.md#the-dashboard-header).

## Choose the cards on a dashboard

Open **Admin**, find **User dashboards**, and click **Edit cards**. Steps are in
[Admin: Dashboards](../admin/dashboards.md).

Related: [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).