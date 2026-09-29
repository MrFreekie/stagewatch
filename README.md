# Stagewatch

**A Home Assistant-style monitoring hub for live concert and festival audio.**

Stagewatch gathers show-site data from DIY and commercial devices into one server.
It shows the data on dashboards that anyone on the show network can open from a
tablet, phone or wall display. It records history with markers, raises alarms, and
sends values on to other gear over OSC.

Version 0.1 covers the environment: temperature, humidity and pressure from
[ESPHome](https://esphome.io) sensor nodes, averaged across the site. From that it
derives the **speed of sound** and shows **how far alignment has drifted since you
set it**. See the [roadmap](#roadmap) for wind, tilt, load cells, power, consoles,
RF and SPL.

> **Advisory tool.** Stagewatch is not a certified safety system. It does not replace
> the monitoring, riggers, electricians or duty holders required by an event's
> safety, wind-management or power plans.

---

## Why

The speed of sound changes by about 0.6 m/s per °C. Over a 30 m sub-to-FOH or
delay path, a 10 °C swing between soundcheck and headliner moves arrival times by
about 1.5 ms. Stagewatch measures the air where the sound travels. When you set a
marker such as **"Aligned"** at soundcheck, it shows how much the travel time has
changed since then.

## Features (v0.1)

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
  them with no login. Each dashboard has its own permission for adding markers and
  acknowledging alarms. Updates are live and reconnect automatically.
- **Admin console**: PIN protected, with first-run setup.
- **OSC output**: `/stagewatch/avg/env`, `/stagewatch/alarm`, and optional
  per-node messages.
- **Autostart at boot** on Windows or Raspberry Pi, with an optional Pi kiosk display.
- **Emulate mode**: build and demo a show with no hardware.

## Quick start

Requires [uv](https://docs.astral.sh/uv/). It fetches Python 3.12 itself.

```bash
git clone https://github.com/OWNER/stagewatch.git
cd stagewatch
uv sync
uv run stagewatch --emulate
```

Open <http://localhost:8080>. The first visit asks you to set an admin PIN.
`--emulate` runs three simulated sensor nodes. Drop it once real nodes are on the network.

```
stagewatch [--port 8080] [--data-dir DIR] [--emulate] [--no-mdns] [-v] [--version]
```

Runtime data lives in `./data`, or in `--data-dir` / `$STAGEWATCH_DATA`. That covers
`config.yaml`, the SQLite history, `secret.key` and logs. All of it is gitignored.

## Sensor nodes (ESPHome)

Example configs are in [`esphome/`](esphome/):

| File | Hardware |
|---|---|
| `stagewatch-env.yaml` | ESP32 + BME280 (temperature, RH, pressure), Wi-Fi |
| `stagewatch-env-sht45.yaml` | ESP32 + SHT45 (±0.1 °C temperature, RH), Wi-Fi |
| `stagewatch-env-poe.yaml` | Olimex ESP32-POE(-ISO) + BME280, PoE Ethernet |

1. Copy `esphome/secrets.example.yaml` to `esphome/secrets.yaml` (gitignored) and fill it in.
2. Flash the node: `esphome run esphome/stagewatch-env.yaml`
3. In **Admin → ESPHome nodes**, adopt the discovered node. Paste its API encryption
   key and give it an area such as "Stage L" or "Delay tower 1".

**Placement matters.** Keep the sensor away from the ESP32's own heat, out of direct
sun, and away from lamp and amp heat. Put it somewhere representative of the air the
sound travels through. Check it against a reference thermometer and set the offset in
**Admin → Sensors**.

## Run at boot

**Windows** (FOH laptop or mini-PC). Run this in an elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1
```

This registers a startup task running as SYSTEM, with automatic restart, and opens
the port on the Private/Domain firewall profiles. Remove it with `uninstall-service.ps1`.

**Raspberry Pi** (Pi OS Bookworm, 64-bit):

```bash
bash deploy/pi/install.sh --kiosk
```

This installs a systemd service with restart. `--kiosk` opens the `wall` dashboard
full screen on the HDMI display after login.

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
- **Secrets never go into git.** `.gitignore` excludes `data/`, `config.yaml`,
  `secret.key`, `.env` and `esphome/secrets.yaml`. A pre-commit hook blocks
  keys, hashes and runtime files:

  ```bash
  git config core.hooksPath .githooks
  ```

## Development

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

## Versioning and releases

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

1. **v0.1:** ESPHome environment, averaging, markers, alarms, dashboards, OSC, autostart
2. **ESPHome expansion:** wind (mean and gust, with warnings), inclinometers, relay/buzzer/stack-light outputs, Pi GPIO, dashboard and automation editors
3. **Protocols and commercial kit:** serial/LoRa gateways, Modbus, NMEA wind (Gill), Broadweigh T24 wind and load shackles, Straightpoint, MQTT
4. **Power:** Shelly, distro meters, residual current (Bender), generator controllers (DSE/ComAp), UPS, contact inputs from power and comms
5. **Pro-audio ecosystem:** Companion, DiGiCo / Yamaha / Allen & Heath, Shure / Sennheiser RF, Smaart SPL, d&b / L-Acoustics amps

See [INTEGRATIONS_WISHLIST.md](INTEGRATIONS_WISHLIST.md).
