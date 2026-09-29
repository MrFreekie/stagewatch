# Changelog

All notable changes to Stagewatch are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html):

- **MAJOR**: breaking change to the OSC/API/config contract, or a config/database
  change that can't be migrated automatically.
- **MINOR**: new integrations, features, dashboard cards (backwards compatible).
- **PATCH**: fixes only.

Add entries under **[Unreleased]** as you work. `scripts/bump_version.py` turns
them into a dated release section and tags the commit.

## [Unreleased]

### Changed
- The default data folder is now per-user and outside the source checkout
  (`%LOCALAPPDATA%\Stagewatch` on Windows, `~/.local/share/stagewatch` on Linux), so code
  updates and re-clones never touch an installation's data. Emulate mode uses a
  separate `emulate` subfolder.

### Added
- `--print-data-dir` option.
- GPL-3.0 licence.
- `scripts/site_config.py`: keep an installation's `config.yaml` in a separate
  **private** git repository (refuses public remotes; never includes `secret.key`,
  the database or logs).

## [0.1.0] - 2026-09-29

### Added
- Core hub with Home Assistant-style devices, entities, areas, event bus and a
  standard device status (OK / Initializing / Compromised / Fault / Missing / Not present).
- **ESPHome integration**: mDNS discovery, admin-controlled adopt/remove, native API
  streaming with automatic reconnect, encryption-key support, and emulate mode
  (three simulated nodes, one of which drops out periodically).
- Site-average environment: robust mean with outlier rejection, EMA smoothing,
  stale-sensor exclusion, per-sensor calibration offset and include/exclude.
- Derived speed of sound (Cramer 1993) using measured pressure, falling back to
  ISA pressure from site altitude; dew point.
- **Markers** on the timeline with "Δ since marker", including the change in sound
  travel time over a configurable reference distance.
- Threshold alarms (advisory / alert / stop) with hysteresis, hold time,
  acknowledge, sounder, and automatic markers for alert/stop.
- Device-offline advisory alarms.
- SQLite recorder: per-show history, markers and alarm log; new-show action.
- Web UI served by the hub: user dashboards (tablet / phone / wall layouts, per-dashboard
  marker and ack permissions, live WebSocket updates with auto-reconnect) and a
  PIN-protected admin console with first-run onboarding.
- OSC output: `/stagewatch/avg/env`, `/stagewatch/alarm`, optional per-node messages.
- Autostart: a Windows scheduled task at boot, and a Raspberry Pi systemd service
  with an optional Chromium kiosk.
- Version tracking: build info (version, git commit, schema versions) in the API,
  admin UI and logs; config and database schema versions with migration hooks.

[Unreleased]: https://github.com/MrFreekie/stagewatch/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MrFreekie/stagewatch/releases/tag/v0.1.0
