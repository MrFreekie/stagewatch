# Stagewatch

**Stagewatch collects the data that helps run a production day to day, and puts it in
front of the stage techs who need it: at a glance, in the dark, on the kit they already
have.**

It's for system techs, FOH and monitor engineers, RF techs, stage managers, riggers,
backline and production crew at concerts, festivals, tours and venues.

- **Collects:** readings and events from DIY [ESPHome](https://esphome.io) sensor nodes,
  commercial devices and other show software, all on one timeline.
- **Shows:** dashboards for each role, on any tablet, phone, laptop or wall screen on the
  show network. No app to install and no account.
- **Remembers:** everything is recorded against the event and day, with markers and notes,
  so you can look back at what happened and when.
- **Helps:** advisory figures and warnings, such as the **speed of sound** and **how far
  alignment has drifted since you set it**, threshold alarms, and the day's schedule.

What it isn't: a control system (it only reads from rigging, power and safety systems), a
scheduling or tour-management app (where you already use one, Stagewatch reads from it
rather than copying it), or a cloud service (it runs on your show network, offline).

Today it covers the environment (temperature, humidity and pressure, averaged across the
site), node health, markers, alarms, the schedule and role dashboards. See the
[roadmap](#roadmap) for wind, lightning, power and racks, RF, SPL and more.

> **Advisory tool.** Stagewatch is not a certified safety system. It does not replace
> the monitoring, riggers, electricians or duty holders required by an event's
> safety, wind-management or power plans.

> **100 % vibe coded.** I'm a FOH / systems engineer, not a programmer. Every line of
> this project was written by an AI coding assistant ([Claude Code](https://claude.com/claude-code))
> working from my ideas, requirements and show-site experience. That's not a knock on the
> AI. It has built the test suite, security reviews and release checks too. But it does mean
> **no human software developer has reviewed this code.** Read it before you trust it,
> test it on your own kit before a show, and expect rough edges. Issues and pull requests
> from people who *do* write code are very welcome. The software is provided as-is, with no
> warranty (see [LICENSE](LICENSE), GPL-3.0).

> Product and company names mentioned are trademarks of their respective owners. No
> affiliation or endorsement is implied.

☕ If Stagewatch helps your shows, you can [buy me a coffee](https://buymeacoffee.com/fohengineer).

---

## Getting started (no coding needed)

Never used GitHub, Python or a command line? These guides are written for live-sound
crew, with every step explained and what you should see after it. Start here:

- **[Start here](docs/README.md)**: what Stagewatch is, what you need, and the guides in order.
- **[Install on Windows](docs/install-windows.md)**: a FOH laptop or mini-PC that starts Stagewatch at boot.
- **[Install on a Raspberry Pi](docs/install-raspberry-pi.md)**: a small always-on box, with an optional wall-screen kiosk.
- **[Build your first sensor node](docs/first-sensor-node.md)**: an ESP32-S3 Feather and a plug-in sensor, no soldering.
- **[Build a Feather sensor node with a screen](docs/feather-s3-tft-node.md)**: ESP32-S3 TFT Feather and an MS8607 sensor, no soldering.
- **[Using Stagewatch on show day](docs/using-stagewatch.md)**: dashboards, markers, alarms.
- More: [updating and backups](docs/updating-and-backups.md) (including a forgotten PIN),
  [troubleshooting](docs/troubleshooting.md) and a [glossary](docs/glossary.md).

The managed install is **tested on Windows 11 and Windows 10 (virtual machines), and on
Debian 13.7 (virtual machine). It is not yet tested on Raspberry Pi hardware.** Try it at
home before you rely on it at a show.

The rest of this page is reference material for installers and developers.

---

## Why

The speed of sound changes by about 0.6 m/s per °C. Over a 30 m sub-to-FOH or
delay path, a 10 °C swing between soundcheck and headliner moves arrival times by
about 1.5 ms. Stagewatch measures the air where the sound travels. When you set a
marker such as **"Aligned"** at soundcheck, it shows how much the travel time has
changed since then.

## Features

- **ESPHome nodes**: discovered automatically over mDNS and adopted by an admin.
  Each connects over the encrypted native API and reconnects on its own.
- **Site average**: stale sensors are dropped, outliers are rejected, and the result is
  smoothed. Each sensor has a calibration offset and can be excluded from the average.
- **Speed of sound**: Cramer (1993), using measured pressure, or the site altitude
  when no pressure sensor is reporting. Dew point is shown too.
- **Timeline with markers**: "Aligned", "Doors", "Headliner". Each marker shows the
  change since it was set: temperature, RH, pressure, *c*, and **Δ travel time in ms**
  over a reference distance.
- **Alarms**: advisory, alert and stop thresholds with hysteresis and hold time,
  acknowledge, a sounder, and automatic markers. There's also an alarm when a
  device goes offline.
- **Dashboards**: tablet, phone and wall layouts, each at its own URL. Users view
  them with no login. You choose which cards each dashboard shows, and in what order.
  Each dashboard has its own permission for adding markers and acknowledging alarms.
  Updates are live and reconnect automatically.
- **Admin console**: PIN protected, with first-run setup.
- **OSC output**: `/stagewatch/avg/env`, `/stagewatch/alarm`, and optional
  per-node messages.
- **Autostart at boot** on Windows or Raspberry Pi, with an optional Pi kiosk display.
- **Emulate mode**: build and demo a show with no hardware.

## Code vs. your installation

The repository is a **blank system**. It contains no configuration, devices, PINs or
history. Each installation keeps its own data in a folder **outside the code**:

| Platform | Default data folder |
|---|---|
| Windows | `%USERPROFILE%\StagewatchData` (the boot service uses `C:\ProgramData\Stagewatch`) |
| Linux / Pi | `~/.local/share/stagewatch` (the boot service uses `/var/lib/stagewatch`) |
| Emulate mode | an `emulate` subfolder of the above, so simulated data never mixes with real shows |

Override it with `--data-dir DIR` or `$STAGEWATCH_DATA`. Print the folder in use with
`stagewatch --print-data-dir`. The folder holds `config.yaml` (devices, thresholds,
dashboards, PIN hash, ESPHome keys), the SQLite history, `secret.key` and logs.

This means you can `git pull`, switch branches, or delete and re-clone the code, and
your installation stays exactly as it was. Old config and databases are migrated
automatically when a newer version starts. **Back up the data folder** to keep your
setup. It contains secrets, so keep backups private.

**Keep your site config in a private git repo (optional).** It tracks an
allow-list: `config.yaml`, event logos, on-site contacts, and event documents and
templates (contacts and documents can hold personal data). It never tracks `secret.key`,
the database, logs, `config.yaml.bak`, `backups/`, `emulate/` or temp files, `push`
rewrites the repo's `.gitignore` each time, and it refuses public remotes and any remote
it cannot verify as a private GitHub repo (unless you pass `--allow-unverified-remote`).
Only the paths listed in the script's header are tracked, and nested copies such as
`sub/config.yaml` stay ignored. Untracking a file does not remove it from history that
was already pushed.

```bash
gh repo create YOUR_NAME/stagewatch-site --private
uv run python scripts/site_config.py init --remote https://github.com/YOUR_NAME/stagewatch-site.git
uv run python scripts/site_config.py push -m "Added delay tower nodes"
```

## Sensor nodes (ESPHome)

Step by step for beginners: [Build your first sensor node](docs/first-sensor-node.md).
Example configs are in [`esphome/`](esphome/):

| File | Hardware |
|---|---|
| `stagewatch-env.yaml` | ESP32 + BME280 (temperature, RH, pressure), Wi-Fi |
| `stagewatch-env-sht45.yaml` | ESP32 + SHT45 (±0.1 °C temperature, RH), Wi-Fi |
| `stagewatch-env-poe.yaml` | Olimex ESP32-POE(-ISO) + BME280, PoE Ethernet |
| `stagewatch-feather-s3.yaml` | Adafruit ESP32-S3 Feather (STEMMA QT) + BME280 or SHT45, Wi-Fi |
| `stagewatch-feather-s3-tft.yaml` | Adafruit ESP32-S3 TFT Feather + BME280 or SHT45, with on-board status display |
| `stagewatch-s3-tft-ms8607.yaml` | Adafruit ESP32-S3 TFT Feather (5483) + MS8607, with screen. Guide: [Feather sensor node](docs/feather-s3-tft-node.md) |

1. Copy `esphome/secrets.example.yaml` to `esphome/secrets.yaml` (gitignored) and fill it in.
2. Flash the node: `esphome run esphome/stagewatch-env.yaml`
3. In **Admin → ESPHome nodes**, adopt the discovered node. Paste its API encryption
   key and give it an area such as "Stage L" or "Delay tower 1".

**Placement matters.** Keep the sensor away from the ESP32's own heat, out of direct
sun, and away from lamp and amp heat. Put it somewhere representative of the air the
sound travels through. Check it against a reference thermometer and set the offset in
**Admin → Sensors**.

## Run at boot

Step by step for beginners: [Windows](docs/install-windows.md) and
[Raspberry Pi](docs/install-raspberry-pi.md). The installer picks the latest release
(currently v0.2.0). To pin a version, pass `-Ref <tag-or-sha>` (Windows) or
`--ref <tag-or-sha>` (Pi); to follow Nightly, pass `-Channel nightly` / `--channel nightly`. The managed install is tested on Windows 11 and Windows 10
(virtual machines) and on Debian 13.7 (virtual machine), and **not yet on Raspberry Pi hardware**.

**Windows** (FOH laptop or mini-PC). Needs Git for Windows and `uv`. Run this in an
elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1
```

This is a *managed install*: it clones the latest release into `C:\Stagewatch` (locked
down so only Administrators/SYSTEM can write to it), keeps its own Python toolchain
there, stores data in `C:\ProgramData\Stagewatch` (SYSTEM and Administrators only) and
registers a startup task running as SYSTEM. The task runs the **launcher**
(`stagewatch.launcher`), a small supervisor that restarts the server after a crash
(Task Scheduler alone does not restart a process that has exited) and stops it cleanly
with the task. The launcher is also what applies updates and rolls back a failed one (see [Updating](#updating)).
The installer also opens the port on the Private/Domain firewall profiles. Options:
`-InstallDir`, `-DataDir`, `-Port`, `-Channel stable|nightly`, `-Ref <tag-or-sha>`.
Remove it with `uninstall-service.ps1` (data is kept). It has been tested in Windows 11 and
Windows 10 virtual machines, not yet on a real show PC. Development checkouts on your Desktop are not affected: run those with
`uv run stagewatch` as usual.

**Raspberry Pi** (Pi OS Bookworm, 64-bit):

```bash
bash deploy/pi/install.sh --kiosk
```

This installs the hardened launcher-based service (starts at boot, restarts on crash, in-app
updates) under a dedicated `stagewatch` user in `/opt/stagewatch`, with data in
`/var/lib/stagewatch` (readable only with `sudo`). Tested on a Debian 13.7 virtual machine;
untested on Raspberry Pi hardware. If you used the older Linux install (service ran from your
own checkout), run the installer again: it takes over and keeps your data. `--kiosk` opens the `wall` dashboard
full screen on the HDMI display after login (labwc or X11 desktops; set *Screen Blanking* to Off
in `raspi-config` on Wayland). A Pi has no battery-backed clock: without internet (NTP) it starts
with the last saved time, so add an RTC HAT or join a network with time service before the show,
or the history timestamps will be wrong.

Notes for both platforms: a development copy started with `uv run stagewatch` uses port 8080
and its own data folder (see above), not the boot service's, so stop the service before running
one, or pass `--port`. The boot service's data folder is not readable by ordinary users on
purpose: open an elevated PowerShell (Windows) or use `sudo` (Pi) to read logs or copy backups.

## Updating

Plain-words version for crew: [Updating and backups](docs/updating-and-backups.md).

**Admin → Software** can update Stagewatch from GitHub without a terminal. It only works on a
**managed install** (the Windows/Pi installers above), where the server runs under the launcher.
On a development checkout or a manual run the card shows the version and says in-app updates are
only available on a managed install; update those with `git pull`.

- **Channels.** *Stable* (default) is the newest `vX.Y.Z` release tag reachable from `main`.
  *Nightly* is the `nightly` branch, which only CI moves, to a `main` commit that passed the
  Windows test run. Nightly is bleeding edge, tested automatically only: don't run it on show days.
  The channel you pick is stored in `config.yaml`; the installer's choice is the default.
- **Check, then update.** *Check for updates* fetches from GitHub (at most once every 30 s) and
  shows the target version, its full commit id and the changelog. *Update now* asks you to
  **re-enter the admin PIN** (so a tablet left logged in can't start an update), then restarts
  Stagewatch. Dashboards reconnect by themselves; the admin page shows "Stagewatch is restarting
  for an update" and reloads. Expect about a minute of downtime. There is no check for a show in
  progress: don't update mid-show. Updates never install anything that changes where dependencies
  come from, or the Python version; those need a manual update.
- **Backups and rollback.** If the new version changes the config or database format, your data
  is backed up first (`<data>/backups/`, at most 10 kept; the checksums live in the admin-only
  `.git/stagewatch/` folder). If the new version fails to start, the launcher goes back to the
  old version and restores that backup automatically. *Roll back* in the update history does the
  same on demand. Anything a restore replaces is set aside in `<data>/backups/displaced-*/`
  (never deleted automatically; the card lists the sizes). Markers recorded since the update
  are lost from the live database on a data restore, but kept in the displaced folder.
- **The repository must be public.** There is no token support: while the repo is private, or the
  machine is offline, Check shows "Update source not reachable (repository is private or offline)."
- **What this protects against.** The updater refuses downgrades, targets that are not on `main`,
  moved release tags and dependency-source changes, and it only ever runs code from the pinned
  GitHub origin. It **cannot** protect you from a compromised GitHub account. Turn on 2FA and set up
  repository rulesets: no force-push or deletion on `main`; only GitHub Actions may update
  `nightly`; no update or deletion of `v*` tags.
- **A release tag was re-created ("update source rejected the fetch").** If a `vX.Y.Z` tag is
  deleted and re-created upstream (even on the same commit) the tag object changes, and Check
  refuses with "Changed tag: vX.Y.Z". That refusal is deliberate: a moved tag is what tampering
  looks like, so Stagewatch never accepts it by itself. After confirming with the maintainer that
  the change was intended, an administrator deletes the local copy in the managed clone and checks
  again: `git -C C:\Stagewatch tag -d vX.Y.Z` (Pi: `/opt/stagewatch`, or wherever the install lives).
- **Locked out of Admin ("Recovery required").** If `config.yaml` cannot be read (a typo, a partial
  write, a rollback onto a stricter version), Stagewatch keeps the bad file as
  `config.invalid*.yaml`, salvages every section that is still valid, including the admin PIN, and
  starts. Only if the PIN cannot be salvaged does Admin show "Recovery required" and refuse to set
  a new PIN over the network (otherwise anyone on the LAN could claim admin). Restore `config.yaml`
  from a backup, or on the Stagewatch computer run the ready-made script
  (`deploy\windows\reset-admin-pin.ps1` as Administrator, or `bash /opt/stagewatch/deploy/pi/reset-admin-pin.sh`
  on a Pi; they stop Stagewatch, run `stagewatch reset-admin-pin --data-dir <data folder>` and start it
  again), then set a new PIN. The underlying command
  needs file access to the data folder and is not available over HTTP. Stop the Stagewatch service
  first (the command refuses to run while it detects a running server; `--force` overrides). After
  a reset, **anyone on the network can set the new PIN until you do**, so restart and set it promptly.
  Stagewatch also keeps `config.yaml.bak` (the previous good config, same secrets, same folder
  protection) and falls back to it automatically if `config.yaml` is empty or unreadable.
- **Trying it offline.** `uv run python scripts/updater_sandbox.py` builds a local fake remote and
  a managed clone and runs the real launcher and server against them (no network).

## OSC output

Configure destinations in **Admin → OSC output**. NaN means "not measured".

| Address | Arguments |
|---|---|
| `/stagewatch/avg/env` | temp °C, RH %, pressure Pa, speed of sound m/s, number of temperature sensors |
| `/stagewatch/alarm` | highest active level (0–3), sounding (0/1) |
| `/stagewatch/node/<id>/env` | temp °C, RH %, pressure Pa *(when per-node is on)* |

## Security

- Admin actions need the PIN. It's stored as a salted PBKDF2 hash, and sessions are
  signed HttpOnly SameSite cookies. Changing the PIN logs out every other session.
- Cross-origin requests are refused. Repeated wrong PINs are rate-limited.
- ESPHome encryption keys are never returned by the API.
- Run Stagewatch on a private control network, not the public internet.

## For developers

Everything from here to the roadmap is for people who work on the code. To *use*
Stagewatch, see [Getting started](#getting-started-no-coding-needed) above.

### Quick start (run from source)

Requires [uv](https://docs.astral.sh/uv/). It fetches Python 3.12 itself. This is for trying
it out or developing. For a show computer use the [installers](#run-at-boot) instead.

```bash
git clone https://github.com/MrFreekie/stagewatch.git
cd stagewatch
uv sync
uv run stagewatch --emulate
```

Open <http://localhost:8080>. The first visit asks you to set an admin PIN.
`--emulate` runs three simulated sensor nodes. Drop it once real nodes are on the network.

```
stagewatch [--host HOST] [--port 8080] [--data-dir DIR] [--print-data-dir] [--emulate] [--no-mdns] [-v] [--version]
stagewatch reset-admin-pin [--data-dir DIR] [--emulate] [--force]
```

### Development

**Before your first commit:** run `git config core.hooksPath .githooks`. The hook
blocks secrets (keys, PIN hashes, `secrets.yaml`, config and data files) from being
committed.

```bash
uv sync                       # install, including dev tools
uv run pytest                 # tests
uv run stagewatch --emulate   # run with simulated nodes
uv run python scripts/check_secrets.py --all   # scan tracked files for secrets
```

Layout:

```
src/stagewatch/
  acoustics.py        speed of sound (Cramer 1993), dew point, unit conversion
  core/               hub, model, config, recorder, alarms, derived values, plugin API
  integrations/       esphome (+ emulator), osc_out
  web/                FastAPI server, auth, static dashboard/admin UI (no build step)
esphome/              example node configs
deploy/               Windows and Raspberry Pi autostart
scripts/              release and secret-check tools
tests/
```

New integrations subclass `stagewatch.core.plugin.Integration`. Each one ships a
`Manifest` (vendor, protocol, tier) and supports emulate mode.

### Versioning and releases

Stagewatch follows [Semantic Versioning](https://semver.org), and changes are recorded
in [CHANGELOG.md](CHANGELOG.md):

- The version lives in `pyproject.toml` and `src/stagewatch/__init__.py`. A test keeps
  them in step with the changelog.
- The running version, git commit and config/database schema versions are shown in
  the admin header, in `/api/info`, and at the top of the log.
- `config.yaml` carries `schema_version`, and the database uses `PRAGMA user_version`.
  Both have migration hooks, so older data upgrades on start.

To cut a release, add notes under **[Unreleased]** in the changelog, then run:

```bash
uv run python scripts/bump_version.py minor   # or patch / major / X.Y.Z
git push --follow-tags
```

The script updates the version files, dates the changelog, commits, and creates the
tag `vX.Y.Z`. It never pushes.

## Roadmap

Released:
1. **v0.1:** ESPHome environment, averaging, markers, alarms, dashboards, OSC, autostart
2. **v0.2:** in-app updater (Stable / Nightly channels, automatic backup and rollback), managed install, crash-restarting launcher

Planned:
3. **v0.3:** events and show days, one site time zone, per-dashboard card picker, day schedule / setlist card, Wall Clock card (Ontime), hardware identity with calibration records that follow each sensor
4. **v0.4:** SPL limit display, key onsite contacts
5. **v0.5:** per-event logo
6. **Manual backup and restore:** "Back up now" with a note, a list of automatic and manual backups, download a backup, restore a chosen backup (admin PIN; applied safely by the launcher with the current data kept aside), scheduled daily backups with retention. Builds on the updater's backup system.
7. **Easier setup:** a one-click Windows installer, browser-based flashing for sensor nodes (plug in over USB, click Install, no YAML), and a **Windows portable edition**. The portable edition is a zip you unzip and double-click, with no admin rights needed, for trying Stagewatch out, demos and USB sticks. To make it permanent later, install Stagewatch normally and copy your data across.

Later:
- **ESPHome expansion:** wind (mean and gust, with warnings), inclinometers, relay/buzzer/stack-light outputs, Pi GPIO, dashboard and automation editors, Bluetooth relay
- **Protocols and commercial kit:** serial/LoRa gateways, Modbus, NMEA wind (Gill), Broadweigh T24 wind and load shackles, Straightpoint, MQTT
- **Power:** Shelly, distro meters, residual current (Bender), generator controllers (DSE/ComAp), UPS, contact inputs from power and comms
- **Pro-audio ecosystem:** Companion, DiGiCo / Yamaha / Allen & Heath, Shure / Sennheiser RF, Smaart SPL, d&b / L-Acoustics amps
- **Advice for FOH:** alignment drift since the *Aligned* marker with suggested delays, high-frequency air loss, temperature inversion, wind relative to the PA, curfew SPL budget prediction
- **Site and kit:** lightning detection, crew heat stress, condensation risk, amp rack monitoring, battery and LoRa sensor nodes, health of the Stagewatch computer itself
- **Network:** syslog receiver for show switches and other gear (port errors, PoE, loops on the timeline), Dante / PTP clock health
- **Reports and views:** end-of-day show report (works after a crash or power cut), tour history and venue profiles, floor-plan view, themes including a night mode

See [INTEGRATIONS_WISHLIST.md](INTEGRATIONS_WISHLIST.md).

## Licence

Stagewatch is free software under the [GNU General Public License v3.0](LICENSE).
You may use, modify and redistribute it. If you distribute a modified version, you
must release its source under the same licence.

Vendored third-party component: `src/stagewatch/web/static/vendor/qrcode.js` is
[qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4 by Kazuhiko Arase,
MIT licence (compatible with GPL-3.0; licence text in `vendor/qrcode.LICENSE.txt`, header kept in the
file). It draws the "Connect a tablet" QR codes locally, so nothing is loaded from the internet.
"QR Code" is a registered trademark of DENSO WAVE INCORPORATED.

## Support

Stagewatch is free. If it saves you time on a show day, you can support it at
[buymeacoffee.com/fohengineer](https://buymeacoffee.com/fohengineer). ☕
