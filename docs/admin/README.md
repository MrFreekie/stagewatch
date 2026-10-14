# Admin

The **admin page** is where a tech sets Stagewatch up. Everyone else only needs the
[dashboards](../cards/README.md).

## Open it

1. On the Stagewatch computer, open `http://localhost:8080/admin`.
   On a tablet, use the computer's address instead of `localhost`, for example `http://192.168.1.50:8080/admin`.
2. The first time, choose a PIN of at least 4 characters. Until you do, anyone on the network could set one, so do it straight away.
3. After that, type your PIN to log in.

Forgotten the PIN? See [Updating and backups](../updating-and-backups.md).

**Changes only stick after you click that card's Save button.**

## What each Admin card is for

| Admin card | What you do there | Guide |
|---|---|---|
| **Site** | Time zone, show-day start, altitude, warning times, reference distance, stale time | [Site and shows](site-and-shows.md) |
| **Event & show** | Start a new day or event, rename, previous shows | [Site and shows](site-and-shows.md) |
| **Schedule** | The running order for the day | [Schedule card](../cards/schedule.md) |
| **Connect a tablet** | Address and QR code for each dashboard | [Dashboards](dashboards.md) |
| **Software** | Check for updates, install, roll back, backups | [Updating and backups](../updating-and-backups.md) |
| **ESPHome nodes** | Adopt, rename and remove sensor nodes | [ESPHome](../integrations/esphome.md) |
| **Sensors: calibration & averaging** | Offsets, what counts in the site average, accuracy, role | [Sensors and alarms](sensors-and-alarms.md) |
| **Threshold alarms** | Add limits | [Sensors and alarms](sensors-and-alarms.md) |
| **Alarm notices** | How long notices stay on screen | [Sensors and alarms](sensors-and-alarms.md) |
| **User dashboards** | Add dashboards, choose cards, order and size | [Dashboards](dashboards.md) |
| **OSC output** | Send the site average and alarm state to other gear | [OSC output](../integrations/osc-output.md) |
| **Security** | Change the PIN | [Security and data](security-and-data.md) |
| **Wall Clock** | Time source and look | [Wall Clock](../cards/wall-clock.md) |
| **Ontime** | Address and connection to Ontime | [Ontime](../integrations/ontime.md) |
| **Barometer** | Pressure settings | [Barometer](../cards/barometer.md) |
| **Sound level (Smaart)** | Connect to Smaart | [Smaart](../integrations/smaart.md) |
| **DirectOut GLOBCON** | Connect to a GLOBCON controller | [GLOBCON](../integrations/globcon.md) |
| **Shelly power meters** | Add power meters | [Shelly](../integrations/shelly.md) |
| **Alarm log (this show)** | What alarmed, and when | [Sensors and alarms](sensors-and-alarms.md) |
| **Help** | Download diagnostics and links to these guides | [Troubleshooting](../troubleshooting.md) |
| **Integrations** | A list of what Stagewatch can connect to | [Integrations](../integrations/README.md) |

Admin only changes Stagewatch's own settings. It never controls rigging, power or safety kit.

> **Being written.** Some linked pages are placeholders until the guides are finished.