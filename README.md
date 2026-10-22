# Stagewatch

**Stagewatch collects the data that helps run a production day to day, and puts it in
front of the stage techs who need it: at a glance, in the dark, on the kit they already
have.**

It's for system techs, FOH and monitor engineers, RF techs, stage managers, riggers,
backline and production crew at concerts, festivals, tours and venues.

You put a few small Wi-Fi sensor boxes (nodes) around the site. Stagewatch listens to them
and to other show software, then shows the results on dashboards you open on a tablet,
a phone, a laptop or a wall screen. There is no app to install and no account to make.

What it isn't: a control system (it only reads from rigging, power and safety systems), a
scheduling or tour-management app (where you already use one, Stagewatch reads from it
rather than copying it), or a cloud service (it runs on your show network, offline).

## What you get

- **Collects.** Temperature, humidity and pressure from DIY [ESPHome](https://esphome.io)
  sensor nodes, plus power meters, sound level and show timers from other software.
- **Shows.** Dashboards for each role, such as FOH, phone and wall, each at its own address.
  Alarms appear at the top of every screen.
- **Remembers.** Everything goes on one timeline for the event and day, with markers and notes.
  If a source goes quiet, the chart shows a gap. Stagewatch never fills it in.
- **Helps.** The speed of sound, how far your alignment has drifted since you set an
  **Aligned** marker, a barometer with a "weather may turn" hint, threshold alarms, and the
  day's schedule.
- **Reports.** At the end of the day, one printable report of the readings, alarms, markers and any
  time Stagewatch was not recording, or an excerpt to share. It can also be made after a crash. See
  [Show report](docs/admin/show-report.md).
- **Starts itself.** On Windows, a Raspberry Pi or a Debian PC, it starts at boot, restarts if it
  stops, and can update itself with a backup and a rollback.

Which other products Stagewatch can read from, and how well each has been tested, is in
one table: [Integrations at a glance](docs/integrations/README.md#at-a-glance).

## Important notes

> **Advisory tool.** Stagewatch is not a certified safety system. It does not replace
> the monitoring, riggers, electricians or duty holders required by an event's
> safety, wind-management or power plans. It only reads from other equipment and never
> controls rigging, power or safety systems.

> **100 % vibe coded.** I'm a FOH / systems engineer, not a programmer. Every line of
> this project was written by an AI coding assistant ([Claude Code](https://claude.com/claude-code))
> working from my ideas, requirements and show-site experience. That's not a knock on the
> AI. It has built the test suite, security reviews and release checks too. But it does mean
> **no human software developer has reviewed this code.** Read it before you trust it,
> test it on your own kit before a show, and expect rough edges. Issues and pull requests
> from people who *do* write code are very welcome. The software is provided as-is, with no
> warranty (see [LICENSE](LICENSE), GPL-3.0).

**Tested so far.** The managed install is tested on Windows 11 and Windows 10 and on Debian 13.7
(virtual machines), and on a Raspberry Pi 4 (4 GB), by the project owner. It is not tested on a
Raspberry Pi 5 or with the Pi kiosk. Several integrations and node boards are not yet tested on
real kit: each guide says so. Try it at home before you rely on it at a show.

Stagewatch is independent and unofficial. Product and company names belong to their owners, and
no affiliation or endorsement is implied. The full wording is in the
[notices](docs/integrations/README.md#notices).

## Examples

**A wall clock fed by Ontime.** The stage manager runs the show in Ontime. You add the
**Wall Clock** card to the wall dashboard and pick **Ontime** as its time source, so the screen
shows the same clock as the SM's desk. If the two computers' clocks drift apart, or Ontime goes
quiet, the card says so in words. Add the **Ontime Timer** and **Ontime Rundown** cards beside
it to show the countdown and whether the show is ahead or behind. Stagewatch only listens to
Ontime. See [Ontime](docs/integrations/ontime.md).

**Sound level from Smaart on the FOH tablet.** Smaart measures at the mix position. Stagewatch
shows up to three of its values on the FOH tablet as big numbers, with a chart of the evening.
The numbers are exactly as Smaart reports them, and a drop-out is a gap, never a zero. See
[Smaart](docs/integrations/smaart.md).

**A rack temperature node plus a Shelly power meter.** A small node with a TMP117 sensor reports
the temperature inside an amp rack. A Shelly PM Mini on the rack's supply reports watts and
amps. Both are **Equipment** readings, so they show on the **Equipment** and **Power** cards and
can never change the site's air temperature or the speed of sound. Set a threshold alarm on
the rack temperature and Stagewatch raises an alarm if it passes the limit. The rack node file and
other Shelly models are not yet tested on real kit, and the Shelly has been tried on one PM Mini
Gen3 only. See [Build a sensor node](docs/nodes/README.md) and [Shelly](docs/integrations/shelly.md).

## Get started

Never used GitHub, Python or a command line? The guides are written for live-sound crew,
with every step explained and what you should see after it.

- **[Install on Windows](docs/install/windows.md)**: a FOH laptop or mini-PC.
- **[Install on a Raspberry Pi](docs/install/raspberry-pi.md)**: a small always-on box, with an
  optional wall-screen kiosk.
- **[Install on Debian Linux](docs/install/debian-linux.md)**: the same installer on an ordinary PC.

Then [build a sensor node](docs/nodes/README.md): choose a board, flash it and add it in Admin.
Want to look around first, with fake sensors and no hardware? Install [uv](https://docs.astral.sh/uv/),
download the project and run this in its folder (details in
[CONTRIBUTING.md](CONTRIBUTING.md#quick-start-run-from-source)), then open <http://localhost:8080>:

```bash
uv run stagewatch --emulate
```

## Guides

- [All guides, in the order to follow](docs/README.md)
- [Dashboard cards](docs/cards/README.md): what each card shows and what the colours mean
- [Admin](docs/admin/README.md): setting up the site, sensors, alarms and dashboards
- [Integrations](docs/integrations/README.md): Ontime, Smaart, DirectOut GLOBCON, Shelly, WitMotion tilt sensors, OSC output
- [Updating and backups](docs/updating-and-backups.md), including a forgotten PIN
- [Troubleshooting](docs/troubleshooting.md) and the [glossary](docs/glossary.md)
- [Changelog](CHANGELOG.md)

## On the horizon

These are ideas, not promises: more sensors and site data (wind, lightning, rack monitoring),
more power and mains metering, links to more show software, end-of-day reports, and easier
setup (a one-click installer, flashing a node from the browser).

## Security and contributing

Run Stagewatch on a private show network, never on the public internet. The admin page is
protected by a PIN, and the dashboards have no login. To report a vulnerability, please read
[SECURITY.md](SECURITY.md) and use GitHub's private reporting, not a public issue.

Want to work on the code? See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

Stagewatch is free software under the [GNU General Public License v3.0](LICENSE).
You may use, modify and redistribute it. If you distribute a modified version, you
must release its source under the same licence.

Vendored third-party component: `src/stagewatch/web/static/vendor/qrcode.js` is
[qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4 by Kazuhiko Arase,
MIT licence (compatible with GPL-3.0; licence text in `vendor/qrcode.LICENSE.txt`, header kept in the
file). It draws the "Connect a tablet" QR codes locally, so nothing is loaded from the internet.
"QR Code" is a registered trademark of DENSO WAVE INCORPORATED.

Sensor nodes are built with [ESPHome](https://esphome.io), a separate open-source project.

### Trademarks

Product names, brand names and logos shown in Stagewatch or its documentation (for example
WitMotion) are trademarks or registered trademarks of their respective owners. They are used only
to identify the equipment Stagewatch works with. Stagewatch is not affiliated with, endorsed by or
sponsored by any of these companies.

## Support

Stagewatch is free. If it saves you time on a show day, you can support it at
[buymeacoffee.com/fohengineer](https://buymeacoffee.com/fohengineer). ☕
