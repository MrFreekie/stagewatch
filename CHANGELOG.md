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

- **Messages bell in the dashboard header.** A bell with a count and the worst kind of message (red `!` alarm, amber `▲` warning, blue `i` information; grey with no number when there is nothing). Tap it for a panel listing the same items as the alarm bar, worst first then newest, each with its age. It closes on a second tap, Esc or a tap elsewhere, and stays open as live updates arrive. It never flashes. It uses the alarm list dashboards already receive, so there are no new public fields. The wall screen does not show it.

### Changed

- **All Ontime settings are now in one Admin card called Ontime.** It holds the Connection (address, Test connection and the live status of the Wall Clock, Ontime Timer and Ontime Rundown cards), the Wall Clock's warning limit, and the event title switch for the Ontime Timer and Ontime Rundown, with one Save. The Wall Clock card keeps only its time source, format and date, with a pointer to the Ontime card; the separate Ontime Timer card is gone. The Ontime card starts closed, and stays open while Ontime cannot be reached. Nothing is saved differently: same settings, no change to saved files.
- **The dashboard header now stays at the top while a tablet or phone scrolls**, and is more compact on phones. The wall screen is unchanged.

- **Most Admin cards can now be folded away** by tapping their heading, so the page is shorter. Sensors, Event & show, Schedule, ESPHome nodes, Threshold alarms, Alarm notices, User dashboards and the Alarm log start open; Site, Software, OSC output, Security, Wall Clock, Ontime Timer, Barometer, Sound level (Smaart), DirectOut GLOBCON, Help and Integrations start closed. Stagewatch remembers each choice in that browser. A card that has something to tell you stays open (Site while the time zone is not set, Software while an update is available or running, OSC output while it has an error). **Expand all cards** and **Collapse all cards** at the top of the page open or close the rest. The Schedule summary now refreshes inside its card instead of replacing it.
- The **Connect a tablet** card in Admin can now be folded away by tapping its heading (open by default, and Stagewatch remembers your choice in that browser).
- The plain-digits **Wall Clock** is centred in its card and larger (up to 88 px on tablets, 56 px on phones, 220 px on the wall), with the note under it centred too.

- The **Ontime Rundown** card can also be set to half width (**Card width** in Edit cards), with a single-column list and smaller type.
- **Alarm bar: old notices time out, and connection problems read in plain words.** On dashboards, an acknowledged advisory notice now disappears 2 minutes after it was acknowledged, and a quiet (never beeping) advisory notice that has not changed for 30 minutes moves into an "Older notices (n)" fold-out, so nothing is hidden silently. A notice that is still beeping stays on the list until someone acknowledges it; a change of reason alone (for example "Timed out" to "Can't reach the node") updates the line but does not restart the timers. Alert and Stop alarms never time out. If a problem changes or comes back, the notice is shown again straight away with a fresh timer. Only the dashboard list changes: a missing node still shows Missing in the sensor list and in Admin. Both times are set in **Admin -> Alarm notices** (0 means never); saved settings gain an optional `alarms` section (`hide_acked_min`, `fold_old_min`): no schema change, no conversion; an older version ignores it and forgets it at its next save. The server's clock decides, so a page reload shows the same list. The alarm list gained the per-alarm fields `old`, `hide_in` and `fold_in`.
- **A node that cannot be reached is described in a short phrase, never a technical error.** The alarm line and the node's status now read, for example, "Feather ESP8266 + BME280: missing (can't reach the node)" instead of a long error text that included the node's IP address. The phrases are "Can't reach the node", "Connection refused", "Timed out", "Name not found", "Wrong encryption key", "API password not supported" and, for anything else, "Connection problem". Every device status text is also checked before it can reach a dashboard: anything that looks like an address, a host name or an error dump becomes "Connection problem". The full technical text is written to the log once per change.
- **Sound level: live values no longer show an updated time.** A value that has gone old still turns grey and shows its age ("Old reading, 12 s ago"); "Not available" and "No signal from Smaart" are unchanged. The line is reserved so a box does not change height.

### Added
- **The Admin Help card now links to the Stagewatch guides on GitHub**, grouped as Getting started, Day to day, Hardware and nodes and Reference. The links need an internet connection and open in a new tab; Stagewatch itself still works offline. A test checks that every linked file exists.
- **History chart: "Show equipment sensors".** A tick under the chart (off by default, remembered per browser) also draws Equipment sensors of the chosen kind, such as a rack temperature, as separate dashed lines named after the sensor. They never enter the site average or any site figure, and nothing new is sent to the dashboards.
- **"Sleeps between readings" for deep-sleep ESPHome nodes (not yet tested on a real deep-sleep node).** In **Admin -> ESPHome nodes**, type how many minutes a node sleeps between wake-ups. While it is out of contact but its last reading is no older than 2.5 times that (rounded up to whole minutes), the node and its sensors show **Sleeping** with the age of the last reading, instead of stale or missing, and no node-offline alarm is raised. After that limit it shows stale and missing as normal. The last real reading is kept as it was; Stagewatch never shows a zero or an invented value, and a node that has not reported yet is never shown as sleeping. While the reading is within the limit it still counts in the site average (Environment) as it did before. Leave the box empty and nothing changes. Saved settings gain an optional `sleep_minutes` on each ESPHome node: no schema change, no conversion; an older version ignores it and treats the node as always on. The public snapshot gains only `sleeps`, `sleeping` and `last_reading` on a node that has the setting, and `sleeping` on its sensors.
- **Integrations in Admin say how they get their data.** Each integration can now carry an optional `iot_class` of `local_push`, `local_poll` or `cloud`, shown as a small tag under its name: "Local, live push", "Local, checks every few seconds" or, as a warning, "Uses the internet". ESPHome, Ontime, Smaart and GLOBCON are marked local, live push; OSC output is left unmarked. An integration with no value shows no tag.
- **DirectOut GLOBCON card (experimental, not tested on a real GLOBCON).** A new card shows the live level meters DirectOut's GLOBCON reports for one controller: a chosen group of channels as large bars with the channel number, GLOBCON's name for it and the level in dB, the controller's name and the layer's name as GLOBCON reports them right now, and the words "as reported by GLOBCON". Pick the controller (1 to 16) and the channels per dashboard under **Edit cards** (Channels 1 to 4, 5 to 8, 1 to 8, 9 to 12, 13 to 16, 9 to 16 or 1 to 16; strip numbers on the controller's current layer; every channel of the group is shown, and one GLOBCON says has no level meter shows an empty bar and "no meter", never zero; a dashboard saved earlier keeps "first 4 or 8 with a level" until you choose a group). The card can be made **Half** width like Wall Clock, with a compact grid for 16 channels; set GLOBCON's address (127.0.0.1, port 9091 to begin with) and an optional password under **Admin -> DirectOut GLOBCON**. It is read-only: Stagewatch sends GLOBCON only Ping, GET, SUBSCRIBE and UNSUBSCRIBE (and a log-in, only if you saved a password and GLOBCON asks for one) and can never send SET, UPDATE or ACTION, so it cannot move a fader, mute, solo, change a layer or run a function. A test fails if any other message is ever sent. Levels are shown exactly as GLOBCON reports them (no peak or RMS claim, no averaging or smoothing), GLOBCON's "no signal" (-250) shows as an empty bar, and if GLOBCON goes quiet the last levels stay on screen, dimmed and struck through, with their age; nothing is shown as zero, recorded or back-filled. Browsers get about four updates a second (GLOBCON sends ten). The device is an Equipment service: never part of the site averages, and its loss raises a quiet notice only. The wire format is read by a small hand-written decoder (no new package). Written from GLOBCON's own web page and checked against one recording of it, never run against a live GLOBCON. Saved settings gain an optional `globcon` section and a per-dashboard `globcon` option (`controller`, `strips`, and an optional `range` such as `9-16`): no schema change, no conversion; an older version ignores them and forgets them at its next save. The dashboards' public fields gain `globcon` (controller, strips and range only); the snapshot gains `globcon_meters`, which carries all 16 strips (label, whether GLOBCON gives it a meter, and the level) for each wanted controller so every dashboard can pick its own group. Try it with `--emulate`: the wall dashboard gets the card, with a simulated dropout every 90 seconds. Seen in a headless browser on the wall, tablet and phone sizes, not on real screens.
- **Calibration changes leave a marker.** Saving a sensor in Admin with a different calibration offset, or with **Include in average** switched on or off, adds one system marker to the timeline with the sensor's name and the old and new value (for example "Calibration changed: Stage Left Temp offset +0.3 °C (was 0 °C)"), so the crew can see why a site value stepped. One marker per save; nothing is added when a save changes nothing, when a sensor is first adopted, for an Equipment sensor's average tick, or at start-up. No schema change; it uses the existing system marker, so notes and hiding work as usual.
- **Half-size cards.** Under **Admin -> User dashboards -> Edit cards**, **Wall Clock** and **Ontime Timer** now have a **Size** choice (Full or Half). Half-size cards sit side by side with another half-size card on tablet and wall screens, and a lone half-size card stays half width. A phone always shows every card full width, and below 900 px wide a tablet screen shows one column. Every card stays Full unless you choose Half, so nothing changes by itself. The Wall Clock face and the Ontime Timer count and title shrink to fit the narrower card. Saved dashboards gain an optional `card_sizes` list (card name and `full` or `half`); there is no data conversion, no backup is needed and the schema versions are unchanged. An older version ignores it and drops it the next time it saves, so choose Half again after you upgrade.
- **Sound level: graph range.** **Admin → Sound level (Smaart)** has a new **Graph range**: **Automatic** fits the timeline to the readings (tidy multiples of 5 dB, never narrower than 20 dB, and the scale moves only when the readings leave it, so it does not jump on every sample; gaps and not-available values never count as zero) or **Custom** with **Min** and **Max** dB (0 to 200, at least 10 dB apart, prefilled 22 and 145). A custom graph never hides a reading outside it: the line runs to the edge, a small triangle marks it and a note under the graph says so; the numbers in the boxes are unaffected. Saved settings gain optional `chart_range`, `chart_min_db` and `chart_max_db` keys in the existing `spl` section (not written while automatic with the defaults): no schema change; a damaged range falls back to automatic; an older version ignores them, so going back to an older build drops the graph range at its next save. Saving only the range does not restart or re-login the Smaart connection. The only new public fields are those three, on the Sound level device. Not seen on a tablet, phone or wall in a real browser by the developer.
- **Sound level: locations.** Give the Sound level card a **Default location** and, once Smaart is connected, a **Location for this input** for each input (up to eight), for example "FOH" or "Stage left". The card title reads "Sound level · FOH" when every value has the same location; otherwise each value shows its own under its name. Locations are labels only: they are never part of an entity id, a stored key or the history, saving them does not restart or re-login the Smaart connection, and text is cleaned (40 characters, no control characters) and shown as plain text. A location whose input Smaart no longer lists shows as "Not listed now" with a Remove button. Saved settings gain optional `location` and `locations` keys in the existing `spl` section (not written when empty): no schema change, no conversion; an older version ignores them, so going back to an older build drops the locations and the graph range at its next save. The only new public field is `location` on a sound level value.
- **Sound level: a Refresh button.** After you rename an input in Smaart (or start a new one), press **Refresh** on **Admin → Sound level (Smaart)** and the input and value drop-downs read Smaart's names again, keeping what you chose where Smaart still lists it. A chosen input or value that Smaart no longer lists stays chosen, is marked "Smaart does not list this", and shows as not available (never zero, never moved to another input). It asks Smaart with the same "which inputs are active?" message Stagewatch already uses, so the list of messages sent to Smaart is unchanged, and at most once every 2 seconds. It says "Smaart is not connected" if there is no link. The drop-downs also follow Smaart's lists by themselves while the page is open. No settings change. Not tested against a live Smaart.
- **Sound level: the real Smaart connection, and a choice of input for each value.** Each of the (up to three) sound level values is now an **input** and a **value**, both picked from drop-downs filled with Smaart's own lists once Stagewatch is connected (for example "ASIO MADIface USB : Channel 7 (1)" and "SPL A Slow", "SPL C Slow", "LAeq 1", "LAeq 10"; the "FS Peak" value is left out, as Smaart's own page does). Values on different inputs are never combined. A value saved before this version has no input and means "the first input Smaart lists"; it keeps its name and its history. The old normal choices map to Smaart's wording ("SPL A Slow", "SPL C Slow", "LAeq 15"); if Smaart does not list "LAeq 15" the admin page says so and that value shows as not available, instead of guessing which LAeq you meant. **Written from Smaart's own web page script; not yet tested against a live Smaart**, and no real reading has been seen, so expect to adjust it once it has met a real Smaart (the card and the device status say so). What Stagewatch sends to Smaart is a fixed list of exactly four messages, checked in one place and pinned by a test: ask whether a password is needed, ask for the list of inputs, send the API password (log-in only), and ask for one update a second. It never starts or stops measuring, never changes calibration, gain, logging, alarms or the mix, and never reads Smaart's history or log streams (so a gap stays a gap; nothing is back-filled). A reading Smaart marks as overload, or does not give, shows as a dash, never zero. Smaart's API password (if you set one in Smaart under Options → Preferences → API) is entered in **Admin → Sound level (Smaart)**; it is stored in the settings file like an ESPHome encryption key, is write-only (the page only says "a password is saved"; leaving the box empty keeps it, and a tick removes it), is never logged, and is left out of the support bundle. A wrong password is reported plainly and is **not** retried until you save a new one. If Smaart has no active inputs the page says to start logging in Smaart. Only Smaart's `/api/v4/` address is known; another Smaart version that answers differently is reported as "did not answer like Smaart's API". The port is the one Smaart's SPL web page uses (26000 is the usual example): please confirm it on your Smaart. Emulate mode now acts like this protocol: two inputs, the four values, no "LAeq 15", an occasional overload point and a dropout. Saved settings gain `meters` and `password` keys in the existing `spl` section (older `slots` are still read and still written for first-input values), so there is no data conversion, no backup is needed and the schema versions are unchanged; an older version ignores the new keys and drops them the next time it saves, so enter them again after you upgrade. Saving the Sound level settings with a password (even the same one) now logs in again at once, so "check it and save again" works after a refused password; the password is sent as plain text on the show network, so use one that is unique to Smaart; typed IPv4-in-IPv6 addresses are refused like public ones; a name that will not look up now times out instead of waiting; a numeric `password: 1234` in the settings file is read as text; an empty port means 26000; and the card's intro tucks the list of messages sent to Smaart into a fold-out.
- **Sound level shows which input a meter listens to.** When the software names the input (Smaart writes it like "ASIO MADIface USB : Channel 7 (1)"), the Sound level card and the Admin status show it as a small plain-text line, and time-weighted values read "SPL C Slow" as Smaart writes them (labels only; stored data and ids are unchanged). The real connection leaves the name empty until the developer kit has been read, so only emulate mode shows it, and nothing is shown when it is unknown.
- **Ontime Rundown card.** A new dashboard card shows where [Ontime](https://github.com/cpvalente/ontime) is in the day (the running event is highlighted in the event list), whether it is running **ahead** or **behind** (for example "▼ 4:10 BEHIND", with words and a triangle, never colour alone), the planned start and end, when Ontime expects to finish, when it started, and a bar for how far through the day it is. "On time" means within 30 seconds and is shown small and calm, because nothing needs attention (behind and ahead stay large); behind is amber, then orange, never red. It shows Ontime's own offset (whichever mode Ontime reports, labelled "vs plan" or "since start") and Ontime's own clock times, not converted to Stagewatch's time zone. It says so in words when the show has not started, has finished, or Ontime has no rundown, and shows "STALE" or "OFFLINE" (a gap, never a zero) when Ontime goes quiet. Tick **Ontime Rundown** under **Admin → User dashboards → Edit cards**; it is never added by default and uses the same Ontime address and the same single connection as the Wall Clock and Ontime Timer cards. Stagewatch still only listens and sends nothing to Ontime, with one deliberate addition: while a dashboard has this card it makes one extra read-only request, `GET /data/rundowns/current`, for the **event list** (cue, title, start and end time, skipped), when the running event changes and at least once a minute (never more often than every 5 seconds, no redirects followed, 1 MiB limit, a failed read keeps the last list marked "may be out of date"). The card also shows the **title and note of the running event** and the list of events around it (a tablet shows the running event with 2 before and 8 after, a phone 4 after, a wall 10 after). **Anyone who can open the dashboard can see these titles and notes**, so keep the card off dashboards that should not show them, or untick **Show the event title on dashboards** in the Ontime Timer card on the Admin page: that one switch now hides titles and notes on both the Timer and the Rundown card (the list keeps cues and times). If the running event cannot be found in the list, the card says so and shows no list; it never guesses a position. Colours, custom fields and triggers are never read. **Ahead and behind follow Ontime's own screen, confirmed on a real Ontime 4.14.0: a positive offset is behind and a negative one is ahead.** **Experimental:** only one situation has been recorded from a real Ontime 4.14.0 (running event 9 of 16, offset 0). What "finished" and "no rundown" look like, groups and milestones in the list, and a multi-day rundown are assumptions, covered by hand-made test data and listed in the user guide. If Ontime sends a rundown block Stagewatch can't read, the card keeps the last figures that made sense, strikes them through and says "CAN'T READ" until a readable block arrives; it never guesses. `--emulate` puts the card high on the wall dashboard with a story from not started through amber and orange, ahead, no rundown loaded and finished, with stale and offline gaps. There are no new settings, so there is no data conversion and no backup is needed; if you go back to an older version after ticking this card, the older version drops the card the next time you save dashboards there, so tick it again after you upgrade.
- **Sound level (Smaart), first part.** A new **Sound level** card shows up to three sound level values you choose, normally **A Slow**, **C Slow** and **LAeq 15 min**, with their timeline on one chart. Stagewatch records the numbers **exactly as the measurement software sends them**: it never averages, smooths, rounds or corrects them, and a value the software does not give shows as a dash, never as zero. If the software goes quiet or away, that is a gap in the chart (the line breaks), the card says so, and one marker is added when the readings come back. On a long view the chart shows the last reading in each interval and says so. Sound levels are never part of the site average and cannot be given a calibration offset. The address starts as this computer (`127.0.0.1`) on port `26000`; both can be changed. Set it up under **Admin → Sound level (Smaart)** (switch it on, choose the values) and add the card with **User dashboards → Edit cards**; it stays hidden until values are set up and is never added by default. **This is not tested against a real Smaart.** Stagewatch was written without the Smaart developer kit (Rational Acoustics supply it on request), so the real connection can reach Smaart but cannot read a value yet; the simulated source in **emulate mode** (`--emulate`) shows how the card behaves, including a dropout and a value that is not available. The Smaart address must be on your local network: public addresses and number-like names (such as 134744072 or 0x8.0x8.0x8.0x8) are refused, and a name is looked up and checked each time it connects. A saved address that is no longer acceptable is cleared with a warning instead of resetting your settings. Stagewatch only listens: it sends nothing to Smaart and never changes calibration, gain, logging, alarms or the mix. Saved settings only add a new `spl` section, so there is no data conversion, no backup is needed and the schema versions are unchanged; an older version ignores the section and drops it the next time it saves.
- **Environment and Equipment roles.** Each ESPHome node now has a **Role**: **Environment** (the air at the site, as before) or **Equipment** (gear, such as an amp rack or a power supply). Equipment readings are never part of the site average, the speed of sound, the dew point, the barometer or the accuracy weighting, so a hot rack cannot pull the site temperature up. Choose the role when you adopt a node (Environment is pre-selected) or later in the **ESPHome nodes** list. In **Sensors: calibration & averaging**, a sensor can follow its node or be set to Environment or Equipment on its own, the node folds sit under **Environment** and **Equipment** headings, and the Average tick and accuracy boxes grey out on Equipment rows ("Equipment readings are never averaged"). Every node you already have stays Environment, so nothing changes until you change a role. On dashboards, the **Sensors** table gets the same two headings when you have any Equipment sensor, the history chart plots only Environment sensors, and a new **Equipment** card (never switched on by default; add it with **Edit cards**) shows each Equipment reading by node. It stays hidden while you have none. Threshold alarms work on both roles, and an Equipment threshold looks only at its own sensor. Emulate mode has a new "Rack 1 (sim)" Equipment node. If you go back to an older version, it ignores roles and averages every sensor again, and it drops the roles the next time it saves, so set them again after you upgrade.
- Admin page: each sensor in **Sensors: calibration & averaging** can now have an **Accuracy** (the maker's ± figure, in °C, %RH or hPa) marked **typical** or **maximum**. A **Preset…** list fills in the typical figure for common parts (SHT45, TMP117, DPS310, BME280, BMP280, MS8607) from the manufacturers' pages, and leaves the box empty, with a note, where no clear figure was found (SHT40, SHT41, MPL3115A2, and the BME280 and BMP280 temperature). The figure stays with the board, like its offset, and shows only on the Admin page. A new switch, **Weight the average by accuracy** (off by default), makes sensors with a smaller figure count for more in the site average, but only for a kind where every sensor in the average has a figure and the figures are all typical or all maximum. No sensor counts for more than 80 % (the card says when that limit applies), so the average still covers every place you put a sensor. Otherwise that kind keeps equal weights and the card says why. A **Share** column shows how much each sensor counts right now. If you go back to an older version, it ignores these new settings and drops them the next time it saves, so you would need to type them in again.
- Admin page: **Connect a tablet** now has one fold-out section per dashboard, plus one for the home page, each with its QR code and addresses (up to 3 start open, otherwise only the first). **Software** is folded into **Installed**, **Updates**, **History**, **Data backups** and **Displaced data**, each with a one-line status; **Updates** stays open while an update is available or running. Both cards have **Expand all** and **Collapse all**, and Stagewatch remembers your choices in this browser.
- Admin page: **Sensors: calibration & averaging** now has one fold-out section per node, showing its status, how many sensors it has and any offset (for example "offset: Pressure -1.0 hPa"). Nodes with an offset, or that are not ok, start open. Stagewatch remembers which you open in this browser, and **Expand all** and **Collapse all** sit at the top of the card.
- Dashboards: a sensor with a calibration offset now shows a `*` after its value in the **Sensors** table, with a note under the table such as "* Calibration offset applied: Feather S3 -1.0 hPa". The chart key marks the sensor too, and the Site average line when a sensor in that average has an offset. The change appears on open dashboards straight away.
- ESPHome nodes: two new all-in-one environment node files (S3 Feather or XIAO ESP32C6, with a DPS310 and a Sensirion SHT45) report pressure, air temperature and humidity from one box; the pressure nodes guide covers parts, wiring and where to place the SHT45. Not yet tested on hardware.
- ESPHome nodes: four new pressure (barometer) node files and a build guide, [Pressure nodes](docs/pressure-nodes.md), for an ESP32-S3 Feather or a XIAO ESP32C6 with a DPS310, and a Feather HUZZAH with an MPL3115A2 or a BME280/BMP280. They are checked by ESPHome but not yet tested on real boards.
- Ontime Timer: a new dashboard card that shows the countdown **Ontime** is running, with the
  event's title, so the stage manager's timer sits next to your alarms and readings. It shows the
  time left (m:ss, or h:mm:ss from an hour), a progress bar, and the state in words with a symbol:
  ▶ RUNNING, ▶ ROLLING, ⏸ PAUSED, ● READY or ■ STOPPED. Tablets also show time added or removed,
  when the item finishes and how long it has run. The card turns amber and then orange using
  **Ontime's own warning and danger times** for that event, with words such as "UNDER 2 MIN". If
  Ontime doesn't send them, it uses the warning minutes you set under **Admin → Site**. When the time
  is up it counts on as -0:01, -0:02 in orange with "▲ OVER" (never red). If nothing arrives from
  Ontime the time is struck through and says "▲ STALE", and if Ontime is gone it says "▲ OFFLINE";
  an old countdown is never shown as live. Tick **Ontime Timer** under **Admin → User dashboards →
  Edit cards**. It uses the same **Ontime address** as the Wall Clock (the address box and
  **Test connection** now show whenever either card is switched on) and shares the one connection:
  Stagewatch still only listens and never sends anything to Ontime, and it reads only the main
  timer and the loaded event's title and warning times, not your rundown, notes or messages. The
  event title is shown on every dashboard by default; untick **Admin → Ontime Timer → Show the event
  title on dashboards** to hide it. This is Ontime's timer on a screen, not a Stagewatch timer and
  not a cue. It is **experimental**: only the "rolling" state has been checked against a real Ontime
  (4.14.0); pause, ready, stopped, running over and added time are built and tested but still to
  be checked against the real thing. If Ontime stops, a quiet notice appears in the alarm bar (never
  a sound), the same one the Wall Clock uses. `--emulate` puts the card on the wall dashboard and
  runs a three-minute story: running, paused, time added, running over, stopped, ready, stale and
  offline. Action needed: none. If you go back to an older version of Stagewatch after ticking this
  card, the older version doesn't know the card: the next time you save dashboards there it is
  taken off, and the title setting is forgotten (event titles go back to being shown), so you would
  tick the card and untick the title option again. When Ontime doesn't send its own warning times, the
  card says "Warning times from Stagewatch settings" so you know where the colours come from.
- Dashboards: a new **Barometer** card, for outdoor events and anywhere with a pressure sensor (a BME280
  node). It shows the **sea-level pressure** on an old-style dial (the words Stormy, Rain, Change, Fair and
  Very dry sit at their traditional places), how the pressure has **changed over the last 3 hours** in the Met
  Office's words (steady, rising or falling slowly, quickly or very rapidly), a small hand for where it was
  3 hours ago, and a rough **Pressure hint** with a weather picture. When pressure falls by 3.6 hPa or more in
  3 hours (the Met Office's "falling quickly") the card shows **FALLING QUICKLY** with ▼▼ and a reminder to check
  wind, lightning and the forecast. The card is a guide from pressure at this site only. It is not a forecast and
  never replaces the Met Office forecast and warnings or your event's weather plan. The wall layout shows big
  numbers instead of a dial. It says "Collecting pressure history, trend ready about 21:30" until 3 hours of unbroken readings exist, and
  shows a gap (never a guess) if the sensor or Stagewatch stopped for more than 3 minutes, and says "Not enough readings to give a trend yet" when readings are too thin.
  The Pressure hint uses the Zambretti method, which has odd corners at low pressure and in winter (see the user guide); it is never a forecast. Pressure readings that are not plausible are ignored. Tick it under
  **Admin → User dashboards → Edit cards**; it is never switched on by default. Pressure is shown in hPa only.
  The sea-level figure uses the measured temperature and the altitude in **Admin → Site**.
- Admin: **Set altitude from today's sea-level pressure** (in the Site card). Type the sea-level pressure
  from the Met Office, a weather app or the nearest airport (QNH); Stagewatch works out the altitude that makes
  the barometer read that figure and asks you to confirm before it saves anything. **Admin → Barometer** sets the
  hemisphere (for the summer and winter months of the hint) and an optional silent alarm and marker when pressure
  falls quickly (off by default, never sounds). `--emulate` has demo weather buttons (settled high, slow fall,
  front arriving, storm, clearing, sensor dropout, no sensor).
  If you go back to an older version of Stagewatch, it forgets the Barometer settings; nothing else changes.
- Wall Clock: the card now has **three looks**, chosen for each dashboard: plain digits (as
  before), an **LED ring** (an outer ring of 60 lights for the seconds, an inner ring of 12 brighter lights for the hours,
  and the time in the middle) and **7-segment digits** like a studio clock. The ring and 7-segment looks
  are always red on black, by day and by night. Choose the look under **Admin → User dashboards →
  Edit cards → Wall Clock look**. Under **Admin → Wall Clock** you can also choose 24-hour or
  12-hour (am/pm), show the date. A ring on a card narrower than about 280 pixels shows plain digits. "Differs",
  "stale" and "offline" are shown with words and a ▲ as well as a dashed outline or a line through
  the time, never colour alone. `--emulate` gives the three stock dashboards one look each, and the
  clock cycles through live, differs, stale and offline every two minutes.
- Wall Clock: a new time source, **Stagewatch PC**, the clock of the computer running Stagewatch.
  It is the standard for **new** installations. An installation already set to Ontime stays on
  Ontime. One source serves the whole installation, and Stagewatch never switches to another
  by itself: if Ontime stops, the card shows "Stale" and then "offline". Choose the source under
  **Admin → Wall Clock → Time source**. Action needed: none. If you go back to an older version
  of Stagewatch after choosing Stagewatch PC, the older version sets the Wall Clock settings back to
  Ontime and keeps a copy of your old settings file next to it (`config.invalid.yaml`); your PIN and
  other settings are kept. It also forgets the looks you chose, so you would choose them again.
- Schedule card: you can now choose its **warning times**. In **Admin → Site → Warning times
  (minutes)**, type the minutes before an item ends, for example `15, 5, 1!` (up to 8 times, 1 to
  240 minutes). The card goes amber at the first time and orange at every later one, with a tag
  such as "5 MIN". The next item turns amber within the last time. Put `!` after a time to make the
  card flash at that time: the left edge, the tag and the progress bar fade slowly about once a
  second, and stop when the item ends. With "reduce motion" switched on in the device, they stay
  steady and stronger instead. The standard is still `15, 5` with no flashing. One list applies to
  every dashboard. Visual only; no sound. Your saved settings are not changed (a Nightly
  "flash the last step" tick becomes a `!` on the smallest time). An older version of Stagewatch
  ignores these settings.
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

- The **Wall Clock** card also shows the small Ontime logo while its time comes from Ontime (not for the Stagewatch PC clock).

- The **Ontime Timer** and **Ontime Rundown** cards show a small Ontime logo in their header corner, the same size and place as the Stagewatch badge on the Sound level card.

- The **Wall Clock** card no longer says "Matches Stagewatch" under an Ontime time that agrees; a line appears only when the time differs, is stale or offline.

- The **Ontime Timer** card shows the event title larger and bolder (32 px on tablets, 24 px on phones, 64 px on the wall), in full text colour, so it stands out from the count.

- The **Sound level** card shows a small Stagewatch badge in its header corner (26 px on dashboards, 44 px on the wall).
- Admin → Site: the altitude label now says the altitude is also used for the barometer's sea-level figure
  (it used to say it was only used without a pressure sensor).
- Wall Clock LED ring: the 60 lights for the seconds are now an outer ring, and the 12 hour marks
  are a separate inner ring that is always lit. The seconds fill up: every second that has passed
  in the minute stays lit, and all the lights go out at the start of the next minute (none are lit
  at :00). The "Ring light" choice is gone, because the ring always fills up. Action needed: none;
  a saved "ring" setting is kept but ignored. When the time goes stale, the lit lights freeze and dim.
- Admin: the **Wall Clock look** choice now sits on the Wall Clock line under **User dashboards**
  **Edit cards**, and only shows while the Wall Clock card is ticked for that dashboard.
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

- **Ontime Rundown card: "can't read" when the show ran off plan.** A real Ontime sends a negative "expected rundown end" while the rundown is running early or late; the card refused it. It is now read. Checked against a real Ontime 4.14.0 capture.

- **Sound level (Smaart): an input with brackets in its name was dropped.** Smaart names its streams with percent-encoding and brackets (for example `Channel 7 (1)`), and the first live test showed the card stuck on "Connected, but no values are arriving" because the only input was refused. Brackets and a few other harmless characters are now accepted; paths that could change the host (a leading `//`, `@`, `?`, `#`, `\`, `..`) are still refused. Checked against a real capture from Smaart Suite 9.6.4.
- XIAO ESP32C6 nodes: an optional external-antenna block (off by default; the built-in antenna stays
  the default) and a guide section on when to use it. Not yet tested on hardware.
- Admin: the adopted ESPHome nodes list now has an **Address** column: the IP address each node is
  connected on now ("Not connected" while it is down) and the name it was adopted with. Only the admin
  page shows it, never the dashboards.
- Charts: each chart now starts on a sensible scale (temperature 10 to 30 °C, humidity 0 to 100 %,
  pressure 980 to 1040 hPa, speed of sound 335 to 350 m/s) and stays on it while the readings fit,
  so the lines no longer jump about. If a reading goes outside, only that side of the scale grows.
- Sensor nodes: a battery gauge that reads a little over 100 % (or under 0) is now shown as 100 % (or 0 %). A reading that is not a number is ignored, never shown as 0 %.
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

- The Wall Clock **Blink the colons** option is removed: it was unstable. The colons are always steady. A saved `colon_blink` setting is ignored.
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
