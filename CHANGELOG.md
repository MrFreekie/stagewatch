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

### Security
- The Windows install is now managed-only. The previous installer ran a SYSTEM task from a
  user-writable checkout, so any process running as that user could escalate to SYSTEM.
  The new install lives in an Administrators-owned, ACL-locked `C:\Stagewatch` with its own
  toolchain, and keeps data in a protected `C:\ProgramData\Stagewatch`. The Pi gets an
  equivalent `install.sh --managed` (dedicated no-login user, hardened unit; untested on hardware).
- In-app updates require re-entering the admin PIN (wrong PINs count towards the login rate limit),
  are admin + same-origin only, and return short error categories to the UI (git/uv output goes to the
  log only). The updater refuses non-managed installs, downgrades, targets outside `main`, moved tags
  and targets that add dependency sources. The channel choice lives in `config.yaml`; the
  installer-written managed marker is never modified by the server.
- All request bodies are capped (64 KiB by default; `413` beyond that, per-route override hook).

### Changed
- The default data folder is now per-user and outside the source checkout
  (`%USERPROFILE%\StagewatchData` on Windows, `~/.local/share/stagewatch` on Linux), so code
  updates and re-clones never touch an installation's data. Emulate mode uses a
  separate `emulate` subfolder.

### Added
- **In-app updater** (Admin -> Software): Stable and Nightly channels, check/update/roll back with a
  PIN confirmation, changelog and full commit id shown before updating, automatic data backup when the
  config/database format changes, update history, backup and displaced-data sizes, and an
  "update available" badge in the admin header only. Works on managed installs only (409 `not_managed`
  elsewhere). `GET /api/admin/software`, `POST .../check|update|rollback`, `PUT .../channel`.
- `build_info()` / `/api/info` report `describe` (`0.2.0+14.gabc1234`), `channel`, `managed` and
  `supervised`.
- `scripts/updater_sandbox.py`: offline fake remote + managed clone for trying updates end to end.
- `stagewatch.launcher`: stdlib-only supervisor that restarts a crashed server with backoff, runs it
  in a kill-on-close Job Object on Windows, and (exit code 75) applies a pending update or
  rollback, health-checks it via a handshake file and rolls back automatically on failure.
  Update state lives in the admin-only `.git/stagewatch/` folder, never the data folder.
  If a manual rollback fails its health check, the data the restore displaced is put back.
- Managed installs: `deploy/windows/install-service.ps1` (rewritten; `-Channel`, `-Ref`,
  `-InstallDir`) and `deploy/pi/install.sh --managed`, with a `.git/stagewatch-managed.json` marker.
- Data backup/restore for updates (`stagewatch.backup`): consistent SQLite snapshot, sha256
  manifest kept in the admin-only state folder, retention of 10, displaced data kept on restore.
- GitHub Actions: `ci.yml` (tests and secret scan on Windows) and `nightly.yml` (fast-forwards
  the `nightly` branch to a tested main commit).
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
