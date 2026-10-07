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
- Dashboards: a **Wall Clock** card shows the time of day from your Ontime rundown (version 4.14 tested)
  in large HH:MM:SS, as Ontime sends it. If it differs from Stagewatch's own time by more than your
  limit (2 s unless you change it), the card says by how much, and tells you to check the time zones
  when the difference is whole hours. It says "Ontime offline" when it can't hear Ontime, and "Stale"
  if nothing has arrived for 3 s. Stagewatch only listens to Ontime: it never sends it anything and
  does not read your rundown. Switch it on under **Admin → User dashboards → Edit cards**, then set the
  Ontime address in the new **Wall Clock** card (**Test connection** checks it). It is not on any
  dashboard by default, and Stagewatch does not contact Ontime until a dashboard has the card.
  If Ontime can't be reached you get a quiet on-screen notice, never a sound. Experimental until
  tested with a real show. `--emulate` shows a clock with an occasional short dropout.
- Admin: services such as Ontime are listed under **Integrations**, not in the ESPHome nodes table
  or on the Sensors card.
- Markers: you can add a **note** to any marker, to say what happened or what was changed.
  Tap a marker, then **Add note** or **Edit note** under the drift panel. Notes are plain text,
  new lines are fine, up to 1,000 characters. Every open screen shows the change straight away.
- Markers: **Hide** takes a marker off the chart and out of the list. It stays in the history,
  the drift numbers and reports. Tick **Show hidden (n)** under the marker list to see hidden
  markers (dimmed), and tap **Un-hide** to put one back. Any dashboard allowed to add markers can
  write notes and hide or un-hide any marker. Deleting a marker still needs the admin PIN.
- Markers: notes are shown on every screen on the show network, so the note box now says so and
  asks you not to put names or phone numbers in. Changes to a marker's note or hidden state are
  written to the log (who and what, never the text). An alarm's marker can't be changed while
  that alarm is still active. Adding an item to the schedule after its time has passed never
  puts a marker in the past.
- Alarms: when someone taps **Acknowledge**, the alarm's **ALARM:** marker is hidden, so the
  chart doesn't fill up with alarms already dealt with. An alarm that clears by itself keeps its
  marker on the chart.
- Schedule card: NOW now has a progress bar showing how much of the item's planned time has gone
  (it turns amber and orange with the last 15 and 5 minutes). When the next item starts as this one
  ends, NEXT shows only its start time, not the same countdown twice.
- Schedule: Stagewatch now puts a blue ◆ marker on the chart at each soundcheck, at doors, and
  when each act goes on stage and comes off ("Kestrel Road on stage"). Off stage only appears if
  the act has an end time or the curfew cuts it off. Markers sit at the planned times. Each line
  in the schedule editor has a **Marker** tick box (ticked for soundcheck, doors and act lines)
  to leave a line out or add one, and **Add markers from the schedule** at the top of the page
  switches them all off. Saving again or restarting never adds the same marker twice. A
  marker is only placed if Stagewatch is running as the moment passes. If it was off, that
  moment gets no marker and is never added later; the gap in the data and the "Stagewatch was
  off" marker show why.
- Dashboards: a **Day / Night** button in the top bar switches between the light and dark
  look. Each screen remembers its choice; until you tap it, it follows the device's own setting.
- Sensors card: new **Signal** and **Battery** columns for nodes that report them (for example
  the Feather node). Signal shows in dBm with "good", "fair" or "weak", in amber when weak;
  battery shows in %, in amber with "low" under 20 %. The columns only appear when a node has
  them, and neither is ever counted in the site averages.
- ESPHome nodes: a new sensor node for the Adafruit ESP32-S3 TFT Feather with an MS8607 sensor. Its screen shows temperature, humidity and pressure, plus dots for Wi-Fi and Stagewatch. A new step-by-step guide covers building and adopting it. Not yet tested on real hardware. **Action needed:** add a `wifi_ap_password` line to your `secrets.yaml` (see `secrets.example.yaml`) before building this node.
- Dashboards: a new Schedule card shows what's on NOW (and how long it has left), what's NEXT
  (with a countdown). On a tablet it also shows the running
  order, with finished items greyed out. Tap an act to see its setlist. On a phone it's a short
  strip; tap it to see the rest. On a wall screen it's a large NOW / NEXT strip you can
  read from across the room. It never beeps.
  The card stays hidden until the show day has a schedule. If a dashboard has a Stage set, it
  shows only that stage's items, plus items with no stage.
- Admin page: a new Schedule card for the current show day. Add, remove and reorder items, and
  set each one's start and end time, kind (doors, act, changeover, curfew or other), title, stage
  and setlist. The setlist box shows a preview of how it will look on dashboards. To bring in a
  running order, paste it (or open a CSV or text file) and click **Preview**. Any line Stagewatch
  can't read is listed by its line number, and you then add the rest in one click. **Load demo
  day** fills in an example running order around the current time. If someone else changes the
  schedule while you're editing, Stagewatch tells you and keeps what you typed. Click **Reload**
  to see their version: your own list is then kept as text in the Paste box, so you can copy
  from it.
- Day schedule (used by the Schedule cards above): Stagewatch can now store the running order for each show day, with doors, acts, changeovers
  and curfew, an optional stage per item, and a plain-text or Markdown setlist per act. Times
  are 24-hour site time. A time earlier than the day rollover (06:00 by default) counts as the
  next morning, so "23:00–00:30" works. You can paste a running order as lines such as
  `19:00 Doors` or `19:30-20:15 Support`, as CSV (`start,end,title,kind,stage`, including Excel's
  "CSV UTF-8" files), or copied straight from a spreadsheet. You see a preview first, and any
  line Stagewatch can't read is listed by its line number. Limits: 300 items, 120 characters per
  title, 8 KB per setlist. Anyone on the show network can read the schedule; only the admin can
  change it. If you change the site's time zone or the show's day, every item keeps its local
  time (for example 19:00 stays 19:00, even across a clock change). On the night the clocks go
  forward, a time that doesn't exist (01:00–01:59 in the UK) is refused with a note saying
  which time to use instead. If two people edit the schedule at once, the second save is
  refused with "The schedule was changed elsewhere", so nobody's change is lost silently.
  A tab left open on an earlier day can't overwrite today's schedule either. Emulate mode starts
  with a "Demo Festival", Day 1, and a demo running order around the current time.
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
  panel. Cards that show one stage, such as the schedule, use it.
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
- Events and show days: the admin **Shows** card is now **Event & show**. An event (a festival,
  a tour leg) is a group of show days. Click **Next day (same event)** to start Day 2, or
  **New event…** to end the current event and start a new one. Both ask you to confirm first.
  Markers, alarms and history belong to the current day, as before. Your existing shows are kept
  in an event called "Event 1", which you can rename.
- Events and show days: each day has a date. Stagewatch works it out from the site time and the
  **New show day starts at** time, so a day started after midnight still gets the right date.
  You can change it under **Rename or change the date**. Previous shows are listed by event.
- Dashboards: the line under the site name now reads *Dashboard · Event · Day*, for example
  "FOH · Summer Festival · Day 2".
- ESPHome nodes: sensor calibration now follows the physical board, not the name you gave it.
  Stagewatch reads each node's hardware address (its MAC) when it connects. If you change a
  node's address in Admin, its offsets stay with it. Your existing offsets are copied across the
  first time each node connects after the update, and readings stay exactly the same. If you go
  back to 0.2.0, your offsets still apply there too.
- ESPHome nodes: Stagewatch ignores a node's readings, and shows it as **fault**, when:
  - a different board answers at its address ("different hardware at this address");
  - the same board is added twice ("same board as another device");
  - a board you calibrated before is added under a new name ("board needs checking in
    Admin"), so old offsets are never used without you saying so.

  Readings never mix into the wrong board's history. The hardware address is shown on the
  admin page only, never on dashboards or in alarms. A MAC can be faked, so set an encryption
  key on every node to stop other devices pretending to be yours.

### Changed
- The schedule card no longer has a separate curfew timer. Instead, the act on now counts down to
  its end time (if it has one), turning amber at 15 minutes ("15 MIN") and orange at 5 ("5 MIN").
  NOW and NEXT now sit side by side, and the curfew is still listed in the running order.
- The schedule editor now has its own page, so Admin is shorter. On Admin, the Schedule card
  shows the day, how many items there are, and what is on NOW and NEXT. Click **Open schedule
  editor** to change the running order. It uses the same admin PIN: if you open the page without
  logging in, Stagewatch asks for the PIN and brings you back. The new page also has **Export
  CSV** (a spreadsheet file of the running order, without setlists) and **Print** (a clean
  black-on-white running order with the setlists, which you can also save as a PDF). The kind
  list in the editor follows the server, so new kinds appear without any change here.
- Schedule card after the show: the card goes calm ("Finished") instead of counting hours. Once the day changes over and
  nobody has pressed Next day, tablets and phones show a small note ("Yesterday's schedule ...
  Start the next day in Admin") and the wall screen hides the card. NEXT turns amber with
  "STARTS IN 5 MIN" for its last five minutes. An item with no end time no longer shows a countdown under NOW, just its
  start time.
- Schedule kinds: Venue Access, Load In, Crew Call, Soundcheck and Load Out join Doors, Act,
  Changeover and Curfew, and an import picks them up from titles ("Video Load In", "Matt
  Soundcheck"). An item that starts after the curfew, such as Load Out, now shows as NOW while
  it runs instead of the card saying the show is over.
- Wall screen: chart marker
  labels are larger. On touch screens marker tabs are taller and easier to tap. In the running
  order, finished items are easier to read.
- Markers on the chart are easier to read. Labels now stack in up to three rows instead of
  piling on top of each other, and when there is still no room a marker shrinks to a small
  tab you can tap to select it. Markers are coloured by where they came from, and each also
  has its own shape: amber ▼ for crew marks, grey ■ for Stagewatch's own notes, orange ▲ for
  alarms (blue ◆ and purple ● are kept for the schedule and contacts). The marker list shows the
  same shape and colour. The marker you select always keeps its full label.
- Dates and numbers now always use UK style, whatever language the tablet or browser is set
  to. Dates run day, month, year ("Fri 2 Oct 2026, 14:05"), and large numbers have a comma
  for thousands ("1,013.2 hPa").
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
  events, show days, running orders, the Wall Clock, marker notes, hidden markers and schedule
  markers. If you already run a test build from before marker notes, its show history is
  converted once more, the same safe way. Stagewatch takes a backup automatically
  before it updates. Going back to the older version (**Admin → Software → Roll back…**) restores
  that backup, so your data is exactly as it was before the update. Anything recorded after the
  update is set aside in the backups folder, not deleted. The conversion takes a few seconds, even
  with a long history.
- If you run Stagewatch from a downloaded copy (not installed), it saves a safety copy of your show
  history as `stagewatch.sqlite3.pre-v3.bak` in your data folder before converting it. It keeps
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
- Site settings now show at once. Changing the smoothing time, outlier rejection, which
  sensors count towards the average, or a sensor offset used to ease in over a minute or two;
  now the site values jump straight to the new figure. Normal readings are still smoothed.
- The admin page no longer wipes text you are typing. Its five-second refresh used to rebuild the
  page while you were in a text area, or after you had typed into a field and clicked away; it now
  leaves the page alone until you save, and only updates the live values.
- After an update, a tablet or browser could keep using some old page files and show a broken
  admin page or dashboard until it was refreshed by hand (for example a dashboard showing only
  its top bar). Pages now ask for their files by a fingerprint of each file's contents, and
  browsers check for new files each time a page loads, so nobody needs to press Ctrl+F5 after an
  update, including when updating from 0.2.0.
- Old iPads and other browsers without a date picker show a plain box for dates. You can now
  type the date there as dd/mm/yyyy, and the day is written out underneath so you can check it.
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
  normal shutdown can still show as "unexpected stop". On Linux and Raspberry Pi, where the
  computer stops Stagewatch properly before restarting, the chart says "(the computer was
  restarted or shut down)" instead.
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
