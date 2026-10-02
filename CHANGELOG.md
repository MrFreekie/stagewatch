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

### Added
- Private site-config backup (`scripts/site_config.py`) now tracks an explicit list of files:
  `config.yaml`, event logos, on-site contacts, and event documents and templates. Backups,
  `config.yaml.bak`, temp files, `emulate/`, the key, the history database and logs are never
  included, and `push` refreshes the repo's `.gitignore` each time (and stops tracking anything
  it now excludes). Contacts and documents may hold personal data, so keep that repo private.
- Quiet notices: an alarm can now be marked silent. It shows in the alarm bar as a calm grey
  notice, never beeps, never needs an Acknowledge, and does not change the alarm level sent over
  OSC. The alarm log marks these entries with "(silent)". Nothing uses it yet; the Wall Clock
  source will, so a venue without Ontime never beeps.
- Dashboards: you can now choose which cards each dashboard shows, and in what order. In
  **Admin → User dashboards**, click **Edit cards** on a dashboard, tick the cards you want and
  use ▲ and ▼ to order them, then click **Save dashboards**. Your existing dashboards keep
  exactly the cards they show today. A dashboard with no cards shows only alarms.
- Dashboards: each dashboard can follow a **Stage** (for example "Main stage"), set in the same
  panel. Cards that show one stage, such as the schedule, will use it.
- Dashboards: the **Open on a tablet** address and QR code is now a card. Wall screens keep it,
  and you can add it to any other dashboard too.
- Dashboards: when the temperature is outside 0–30 °C, or the air pressure outside 75–102 kPa,
  a quiet note under **Speed of sound** (and in the marker drift panel) says the figures are
  approximate. That is the range the speed-of-sound formula is tested for. The values are still
  shown and used, and it is not an alarm.
- Site time: set the show site's time zone under **Admin → Site → Time zone**. Every time on
  dashboards and the admin page (the clock, the chart, markers, the alarm log, show start times)
  then shows site time in 24-hour format, even on a tablet set to the wrong zone or country.
  Until you set it, Stagewatch uses the time zone of the computer it runs on, and the Site card
  says so in amber, with a button to use the zone of the browser you are on.
- Admin → Site: **New show day starts at** (06:00 unless you change it). A show day runs until
  that time next morning, so a reading or marker at 01:30 still counts as the night before.
  Keep it at 03:00 or later, so it stays clear of the hour when the clocks change.
- Dashboards: the chart's time labels stay on whole site hours when the clocks change during
  the time shown.
- Admin → Site: if a time zone or show-day start time isn't accepted, the message now says what
  to type, and that nothing has been changed. The new Site fields and the **Use ... from this
  browser** button are now at least 44 px tall for gloved fingers.
- ESPHome nodes: on a shared show network, other people's devices no longer clutter your
  **Discovered node** list. Click **Ignore** next to a node to hide it. **Show ignored** lists the
  hidden ones, and **Unignore** brings one back. You can ignore up to 200 nodes. Stagewatch only
  connects to nodes you adopt, so ignoring changes nothing else.
- Show history: Stagewatch now records when it starts and stops. After a crash, a power cut or
  the PC switching off without shutting Stagewatch down, it adds a marker such as "Stagewatch
  restarted after an unexpected stop (down about 4 min)", so the gap is easy to see on the chart.
- Show history is written safely to disk every minute, so a power cut loses at most about the
  last minute of readings.
- Supported browsers are listed in the using guide: dashboards on iOS 12 or later and recent
  Chrome, Edge, Firefox and Safari; the admin page on iOS 13 or later.
- A message if JavaScript is turned off.

### Changed
- Dashboards: a dashboard with no cards now says so, and where to choose cards, instead of
  showing a blank screen. On wall screens the "approximate" note and the small lines under each
  tile are larger. In **Edit cards**, the ▲ and ▼ buttons have a clear outline in the light theme.
- Admin page: the **User dashboards** card now spans the full width, to make room for
  **Edit cards**. **OSC output** sits next to **Security**, and the **Alarm log** below them.
- Windows install guide: a new section for PCs with more than one network card. It covers
  setting each network to Private and fixing "Unidentified network" show networks that Windows
  keeps as Public.
- Windows install guide: a new step suggests turning off Fast Startup on show PCs, so a normal
  shut down is recorded as "the computer restarted or lost power" rather than an unexpected stop.
- Saved data: this update converts your settings and show history to a new format, ready for
  events, show days, running orders and the Wall Clock. Stagewatch takes a backup automatically
  before it updates. Going back to the older version (**Admin → Software → Roll back…**) restores
  that backup, so your data is exactly as it was before the update. Anything recorded after the
  update is set aside in the backups folder, not deleted. The conversion takes a few seconds, even
  with a long history.
- If you run Stagewatch from a downloaded copy (not installed), it saves a safety copy of your show
  history as `stagewatch.sqlite3.pre-v2.bak` in your data folder before converting it. It keeps
  only the two newest of these copies and deletes older ones. You can delete them once you are
  happy with the new version.
- Show history: all your existing shows are kept and grouped into one event called "Event 1".
- Dashboards look the same as before after the update, and saving **User dashboards** in the admin
  page keeps what each dashboard shows.
- Install guides: the `-Ref main` step is no longer needed now that v0.2.0 is released.
- Raspberry Pi / Linux: there is now only one way to install. `install.sh` always sets up the
  protected, updatable install (own `stagewatch` user, program in `/opt/stagewatch`, data in
  `/var/lib/stagewatch`). `--managed` is still accepted but does nothing. Tested on a Debian 13.7
  virtual machine; still not tested on real Raspberry Pi hardware.

### Fixed
- Software: after an update, a roll back and another update, **Roll back...** showed against two
  entries in the update history. It now shows only on the newest one.
- Security: Stagewatch now refuses messages larger than 64 KB from a dashboard's live connection.
  Normal dashboards are not affected.
- Windows: stopping or reinstalling Stagewatch with the install, uninstall or reset-PIN scripts
  was recorded as "Stagewatch restarted after an unexpected stop". The scripts now ask Stagewatch
  to stop properly first, so it is recorded as a normal stop.
- If the computer restarts or loses power, Stagewatch now says "Stagewatch was off for about 25 min
  (the computer restarted or lost power)" on the chart, with no alarm. "Unexpected stop" is kept
  for when Stagewatch itself stopped while the computer stayed on. On Windows with Fast Startup, a
  normal shutdown can still show as "unexpected stop".
- Readings are no longer lost if the history file is busy for a moment while Stagewatch saves
  them: Stagewatch keeps them and tries again.
- Raspberry Pi / Linux: in-app updates were refused with "unsafe_permissions" on Debian 13, whose
  default settings make new folders group-writable. The installer now sets safe permissions itself
  and repairs an existing install. **Action needed** if you installed on Debian 13 before this fix:
  run the installer again (your data is kept), then check for updates.
- Update history showed "0.2.0 → 0.2.0" for Nightly updates. When the version number is the same, it now
  also shows which build, for example "0.2.0 (3d4f56e) → 0.2.0 (3f91b89)".
- Update checks failed with "Update source not reachable" (or were refused) when the project had no Nightly build published yet, even on the Stable channel. A missing Nightly build no longer blocks Stable updates or re-running the installers.
- Old browsers (such as the 2016 Edge on Windows 10) showed a blank page. Stagewatch now says
  **This browser is too old for Stagewatch** and what to use instead.
- Dashboards now work on iPads with iOS 12 (iPad Air 1, mini 2 and 3): the history chart, alarm
  colours, the alarm sound button and the spacing in the top bar no longer break on old Safari.

### Removed
- The older Linux install, where the service ran from your own downloaded folder as your own user
  and could not be updated from the admin page.
  **Action needed** if you used it: run `bash deploy/pi/install.sh --kiosk` again from your
  Stagewatch folder (leave off `--kiosk` if you did not use it). The installer stops the old
  service, switches you over and **keeps your data**; it also saves a safety copy of it as
  `/var/lib/stagewatch.pre-managed-<date>` that you can delete once you are happy.

### Security
- The "Open on a tablet" footer now gives the address on the network the screen is using, never an
  address from another network card (for example the venue internet or a VPN).
- Dashboard titles (up to 80 characters) and stage names can no longer contain hidden or control
  characters that could make them read misleadingly on screens.
- When the admin page sent something invalid, the error reply repeated back what was sent,
  which could include a PIN typed into the wrong place. Error replies now say only which field was
  wrong and why.

## [0.2.0] - 2026-10-01

### Highlights
- **Update from the admin page.** Admin → Software checks for new versions, updates with one tap
  (admin PIN required) and can roll back if anything goes wrong. Your data is backed up
  automatically when an update changes how it is stored. Choose **Stable** or **Nightly** (newest,
  less tested).
- **Starts by itself when the PC switches on**, before anyone logs in, and restarts itself if it
  crashes. Windows installs into a protected `C:\Stagewatch` folder, and your show data lives
  separately in `C:\ProgramData\Stagewatch`. Tested on Windows 11 and Windows 10. A Raspberry Pi /
  Linux version is included: tested on Debian 13.7 in a virtual machine, **not yet on Raspberry Pi
  hardware**.
- **Connect a tablet:** the admin page shows each dashboard's address and a QR code to scan. Wall
  screens show one in the footer.
- **Alarm sound button** on every dashboard: tap it at soundcheck to hear a test beep and arm the
  alarm sound.
- **"Disconnected" banner** when a screen loses its connection, so old readings never look live.
- **Download diagnostics** (Admin → Help): one zip to send when asking for help, with passwords,
  keys and the PIN removed.
- **Forgotten admin PIN:** simple reset scripts for Windows and Raspberry Pi.
- **New step-by-step guides:** install on Windows or a Raspberry Pi, build your first sensor node,
  and use Stagewatch on show day.
- **Action needed if your ESPHome nodes use an API password:** that is no longer supported (ESPHome
  itself removed it in 2026.1). Re-flash those nodes with an API encryption key instead; the
  first-sensor-node guide shows how.

The sections below list every change in detail.

### Removed
- ESPHome API password support (removed upstream in ESPHome 2026.1; use the API encryption key). Old `config.yaml` files with a `password` on an ESPHome node still load; the value is dropped on the next save.

### Security
- Upgraded `cryptography` to 50.0.1 (fixes GHSA advisories for PKCS#7 decryption timing, certificate path building and wildcard name constraints). The lock file now covers Windows and Linux only, the supported platforms.
- Config validation/YAML errors no longer log input values (`hide_input_in_errors` on all config
  models; the load/salvage path logs section names, error types and line/column only, no tracebacks),
  so a bad `noise_psk` or `password` can't leak into `stagewatch.log`.
- `stagewatch reset-admin-pin` refuses while a Stagewatch server is running on that data folder
  (server writes `server.lock`; `--force` overrides), also clears the PIN in `config.yaml.bak`, and
  the README now warns that anyone on the network can set the new PIN until you do.
- The `nightly` promote job only runs from `main`, so a manual dispatch from another branch can't
  move the nightly branch.
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
- An invalid `config.yaml` can no longer reset the admin PIN. Previously the file was moved aside
  and the app started on defaults, so `/api/admin/setup` was open to anyone on the LAN. Now the PIN
  hash and every other section that still validates are salvaged (bad list entries are dropped
  individually), earlier `config.invalid*.yaml` files are never overwritten, and if the PIN cannot
  be recovered `/api/admin/setup` answers 409 "recovery required" and the admin page explains how to
  recover. New local-only command `stagewatch reset-admin-pin --data-dir <dir>` reopens onboarding
  (not reachable over HTTP).
- Update pre-apply permission check also verifies the owner (Administrators/SYSTEM) of the repo
  root, `.git`, `.venv`, `.uv` and `src`, and write access on `src/stagewatch`, the venv scripts
  dir, the uv dir and `.git/stagewatch`.
- Dependency-source check also flags `editable`/`virtual` lock sources (except the project itself)
  and wheel/sdist URLs outside pypi.org / files.pythonhosted.org.
- Build info (`/api/info`) runs git with the hardened flags/environment and a 3 s timeout.

### Fixed
- `-Emulate` on a managed install no longer mixes simulated data into the real data folder. Emulate
  mode now ALWAYS uses `<data folder>/emulate` (resolved in one place, `resolve_data_dir`), including
  when the launcher passes the marker's `--data-dir`. The marker's `data_dir` stays the real folder;
  in-app updates stay available in emulate mode, and backups/restores act on the real folder only (the
  `emulate` subfolder is never backed up or restored). `reset-admin-pin --emulate` targets it too.
- Power-cut safety: config saves, PIN reset and the updater's atomic writes flush and `fsync` the
  temp file before the rename (and the directory on POSIX). `config.yaml.bak` keeps the previous good
  config (same secrets, same folder protection, never part of the site-config repo), and an empty or
  unparseable `config.yaml` is restored from it before the salvage path runs.
- Stray `.config-*.yaml` temp files (data dir) and `*.tmp` files (updater state dir) left by
  interrupted saves are removed at startup when older than a minute.
- Launcher: `server-console.log` is rotated at launcher start (3 old copies, 5 MB cap) and now
  captures only stderr (the server already writes its own rotating `stagewatch.log`).
- Launcher watchdog: a supervised server that is alive but stops answering `/api/info` on localhost
  for about two minutes (after a startup grace period, and never while an update is pending) is
  restarted.
- Update check: a `nightly` branch that is not part of `main`'s history is reported as
  "not part of the main branch history" instead of "up to date".
- Request-body cap: drains a bounded amount (1 MiB / 2 s) of a refused body before the 413, so
  clients no longer see a connection reset.
- A refused or rejected update now leaves a timeline marker instead of a dangling "Updating
  software" marker (versions and reason category only).
- A successful manual rollback that restored a data backup now records `restored_backup: true`.
- The dirty flag in build info refreshes every 60 s instead of being frozen at startup.
- Re-created release tag: the check names the changed tag; recovery is documented in the README.
- Windows installer: the target folder is created and ACL-locked before cloning (previously a fresh
  `C:\Stagewatch` was user-writable until the clone finished); ownership is set to Administrators
  again after `uv sync` so the updater's owner check cannot fail on newly created files; the mDNS
  firewall rule now names the real interpreter (the venv `python.exe` is only a redirector, so the
  rule never matched); a failed clone (for example a private repository) leaves nothing behind and
  says why; re-running waits for the old server to release its files.
- Pi installer: `--managed` no longer exits silently on an unknown ref or when no release tag exists
  (`set -e` fired before the message); runs from `/` so `sudo -u stagewatch` works when your home is
  private; takes over a data folder left by the non-managed mode; missing option values are reported.
- Windows install guide: the download commands now switch on TLS 1.2 first. Some Windows 10 PCs
  otherwise fail with "Could not create SSL/TLS secure channel".
- Pi kiosk: disables screen blanking on X11, suppresses Chromium's "restore pages" bubble after a
  power cut, and reports a missing browser instead of failing cryptically.

### Changed
- The admin "software restarting" screen and the ESPHome key hint now say where the launcher log is
  (Windows `C:\ProgramData\Stagewatch\logs\launcher.log`, Pi `/var/lib/stagewatch/logs/launcher.log`) and
  suggest Download diagnostics; `esphome/secrets.example.yaml` shows the PowerShell and
  `openssl rand -base64 32` key commands.
- Admin Software card polish: Installed / Updates / History sections, an "Update available"
  panel (versions, full commit, scrollable plain-text changes), a labelled confirm dialog
  (PIN focus only on non-touch devices so the keyboard doesn't hide the changelog), history as
  a list with an always-visible "Roll back" button on phones, a full-screen "restarting"
  overlay with elapsed time that blocks stray taps and reloads the page, the card refreshing
  itself when a new build is confirmed, 44 px targets, and better text contrast on the
  accent-coloured buttons and the header badge.
- The default data folder is now per-user and outside the source checkout
  (`%USERPROFILE%\StagewatchData` on Windows, `~/.local/share/stagewatch` on Linux), so code
  updates and re-clones never touch an installation's data. Emulate mode uses a
  separate `emulate` subfolder.

### Added
- **Reset-PIN helper scripts** for managed installs: `deploy/windows/reset-admin-pin.ps1` (checks it
  is elevated, stops the "Stagewatch" task, resets, starts it again, prints the next step) and
  `deploy/pi/reset-admin-pin.sh` (untested on hardware). The admin "Recovery required" text and the
  docs point at them instead of the long one-liner.
- **Alarm sound button** on every dashboard: a permanent, labelled "Alarm sound: On/Off" button
  that arms audio at soundcheck (plays a test beep), remembered per device. The wall layout hides it
  only once sound already works (kiosk); otherwise it is shown.
- **"Disconnected from Stagewatch - reconnecting..."** banner (dashboards and admin) after 3 s
  without a live connection, with elapsed seconds; on-screen values look stale until it reconnects.
- **Connect a tablet** card in Admin: the `http://<LAN IPv4>:<port>/d/<slug>` address (plus the
  `<name>.local` one) and a QR code for each dashboard (`GET /api/admin/connect`, admin only). The wall
  dashboard footer shows one address and a QR code (`GET /api/dashboard/<slug>/address`, wall layout
  only). QR codes are drawn locally with the vendored MIT `qrcode-generator` 1.4.4 (no CDN).
- **Download diagnostics** (Admin -> Help, `GET /api/admin/diagnostics`, admin + same-origin): a zip
  (max 10 MB) with version/build, platform, uptime, the last 2000 lines of `stagewatch.log`,
  `launcher.log` and `server-console.log`, updater status/history, device list, recent alarms and the
  config with every secret redacted (by key name and by value pattern; PIN hash, `noise_psk`,
  passwords and `secret.key` are never included).
- Beginner-friendly user guides in `docs/`: start here, install on Windows, install on a Raspberry Pi,
  build your first sensor node, using Stagewatch on show day, updating and backups (including a
  forgotten PIN), troubleshooting and a glossary; the README now points to them first.
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
- ESPHome example configs for the Adafruit Feather ESP32-S3 and ESP32-S3 TFT (environment node with
  STEMMA QT sensors; **untested on hardware**, pins and calibration are examples only).
- `SECURITY.md`: how to report a security problem privately, and Dependabot checks for outdated
  dependencies and GitHub Actions.
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

[Unreleased]: https://github.com/MrFreekie/stagewatch/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MrFreekie/stagewatch/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MrFreekie/stagewatch/releases/tag/v0.1.0
