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

Admin has four tabs: **Show day**, **Sensors and nodes**, **Integrations** and **Setup and system**. Each card has a title bar you can click to fold it open or shut. A card with something to look at stays open.

### Show day

| Admin card | What you do there | Guide |
|---|---|---|
| **Event & show** | Start a new day or event, rename, previous shows, event logo | [Site and shows](site-and-shows.md) |
| **Schedule** | The running order for the day | [Schedule card](../cards/schedule.md) |
| **Show report** | Make a printable end-of-day report, or an excerpt to share, from what was recorded | [Show report](show-report.md) |
| **Alarm log (this show)** | What alarmed, and when | [Sensors and alarms](sensors-and-alarms.md) |
| **Alarm notices** | How long notices stay on screen | [Sensors and alarms](sensors-and-alarms.md) |
| **User dashboards** | Add dashboards, choose cards, order and size | [Dashboards](dashboards.md) |
| **FOH advice** | Delay paths for Alignment drift and positions for HF air loss | [FOH advice cards](../cards/foh-advice.md) |
| **Connect a tablet** | Address and QR code for each dashboard | [Dashboards](dashboards.md) |
| **Documents** | Upload the event's plans and choose who sees them | [Plans on file](../cards/documents.md) |
| **Onsite contacts** | Key people for the event | [Onsite contacts](../cards/contacts.md) |
| **Wi-Fi** | Crew internet networks for the Wi-Fi card | [Wi-Fi](../cards/wifi.md) |

### Sensors and nodes

| Admin card | What you do there | Guide |
|---|---|---|
| **ESPHome nodes** | Adopt, rename and remove sensor nodes | [ESPHome](../integrations/esphome.md) |
| **Sensors: calibration & averaging** | Offsets, what counts in the site average, accuracy, role | [Sensors and alarms](sensors-and-alarms.md) |
| **Threshold alarms** | Add limits | [Sensors and alarms](sensors-and-alarms.md) |
| **Calibration & hardware** | Each sensor's board and offset history; save offsets to a file and read them back | [Sensors and alarms](sensors-and-alarms.md#calibration--hardware) |
| **Device errors** | Problems each device had in a show (experimental) | [Device errors](../cards/device-errors.md) |
| **Import a log file** | Read an L-Acoustics Network Manager log into a show (experimental) | [Import a log file](import-log.md) |

### Integrations

| Admin card | What you do there | Guide |
|---|---|---|
| **Integrations** (the tiles) | Add, edit or remove Ontime, Smaart, GLOBCON, Shelly, WitMotion and ESPHome nodes | [Integrations](../integrations/README.md) |
| **Ontime** | Timer settings and connection state | [Ontime](../integrations/ontime.md) |
| **Wall Clock** | Time source and look | [Wall Clock](../cards/wall-clock.md) |
| **Sound level (Smaart)** | Which values to record | [Smaart](../integrations/smaart.md) |
| **Sound level limits** | Licence limits and curfew | [Sound level limits](../cards/sound-level-limits.md) |
| **DirectOut GLOBCON** | Connection state | [GLOBCON](../integrations/globcon.md) |
| **WitMotion tilt sensors** | Find and set up tilt sensors | [WitMotion](../integrations/witmotion.md) |
| **Shelly power meters** | Names, areas, addresses and how often each meter is asked | [Shelly](../integrations/shelly.md), [Power](../cards/power.md) |
| **Power demand and generator size** | Demand window, loading limit and generator size for the suggestion (calculated, advisory) | [Power](../cards/power.md) |
| **Power distribution** | Its own page, opened from a short card on the Integrations tab. What feeds what: roles, numbers, ways, lines, breaker ratings and device limits; basic and advanced options. Units are grouped under the supply that feeds them, with a filter, a "Show only" kind, Expand all / Collapse all and a Save bar that stays on screen. **Save to a file** and **Read a file** back up the layout or move it to another hub (preview first, nothing changes until you press Apply) | [Shelly](../integrations/shelly.md#power-distribution-who-feeds-whom), [Power tree](../cards/power-tree.md) |
| **Barometer** | Pressure settings | [Barometer](../cards/barometer.md) |
| **This Raspberry Pi** | Health of the Pi (only on a Pi) | [Raspberry Pi card](../cards/raspberry-pi.md) |
| **Action targets** | Where OSC actions send (QLab, Companion); actions on the schedule and on thresholds | [OSC actions](osc-actions.md) |
| **OSC output** | Send the site average and alarm state to other gear | [OSC output](../integrations/osc-output.md) |
| **Integrations** (the list) | A table of what Stagewatch can connect to | [Integrations](../integrations/README.md) |

### Setup and system

| Admin card | What you do there | Guide |
|---|---|---|
| **Site** | Time zone, show-day start, altitude, warning times, reference distance, stale time | [Site and shows](site-and-shows.md) |
| **Clock** | Check the Pi's time against this device and set it, for a Pi with no network time or clock battery | [Clock](clock.md) |
| **Security** | Change the PIN | [Security and data](security-and-data.md) |
| **Software** | Check for updates, install, roll back, backups | [Updating and backups](../updating-and-backups.md) |
| **Help** | Download diagnostics and links to these guides | [Troubleshooting](../troubleshooting.md) |

Admin only changes Stagewatch's own settings. It never controls rigging, power or safety kit.
