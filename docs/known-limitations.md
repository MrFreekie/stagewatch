# Known limitations

This page is the honest list of what Stagewatch does not do, what it only estimates, and what has
not yet been tried on real kit. Read it once before you rely on Stagewatch at a show.

Back to the [guides index](README.md).

## Safety and scope

- **Stagewatch is an advisory tool.** It is not a certified safety system, and nothing it shows is a
  calibrated or compliance record. It does not replace the riggers, electricians, wind and weather
  plans or duty holders an event needs. See the [notices](integrations/README.md#notices).
- **It only reads.** It never controls rigging, power, generators or safety systems, and it never
  mutes or changes a PA. The one thing that sends anything out is [OSC output](integrations/osc-output.md),
  which you switch on yourself, and which only sends Stagewatch's own numbers.
- **It is not** a scheduling or tour app, a cloud service, or a replacement for the software it reads from
  (Ontime, Smaart and the rest). It does not copy their data unless you start an import yourself.
- **There is no warranty.** The project was written by an AI assistant, with no human code review.
  Test it on your own kit before a show.

## Measurements

- **Sound level is shown as Smaart reports it.** Stagewatch does not average, smooth or convert it. It is
  not a calibrated measurement.
- **Site numbers come from your Environment sensors only.** Readings from Equipment sensors (racks, fans,
  power supplies) never change the site temperature, the speed of sound or the barometer.
- **Node sensors are hobby parts.** Check a node against a trusted instrument before you trust it, and
  allow for how it is mounted (a node in the sun or beside a warm amp reads high).
- **Power cards show what the meters report.** Some figures on the Power and Power tree cards are
  calculated from several meters and say so. Amounts for the load on a unit that has no meter are
  worked out, not measured.
- **Tilt is a guide.** The [WitMotion](integrations/witmotion.md) card is not a load or array-angle
  measurement.

## Estimates

These are advice, and the cards say so:

- **Alignment drift and HF air loss.** Worked out from the site readings since your marker. They suggest;
  they do not measure what the rig is doing.
- **Barometer outlook.** A rough hint from the 3-hour pressure change. It is not a forecast.
- **Condensation risk.** A guide from the site dew point. Surfaces, airflow and warm-up time change what
  really happens.
- **Show report.** The power part is not complete: it does not yet give percent of rating, a leakage
  trend or generator rows. The sound level part has no step chart yet.

## Time and data gaps

- **A gap stays a gap.** If Stagewatch, a node or a source goes quiet, or the computer restarts, the chart
  shows a gap and a restart marker. Stagewatch never fills it in, guesses a value or shows a zero. An old
  value is shown frozen, with its age.
- **Markers are made only as time passes live.** A marker is never added for a time Stagewatch was not
  running.
- **Clocks.** Every reading is stored by the time on the Stagewatch computer. A Raspberry Pi has no clock
  battery, so with no network time it needs the [Clock card](admin/clock.md) or an RTC board. The
  **Set the clock from this device** button is accurate to about a second, and network time can undo a
  step later. Readings already saved keep their old times, so set the clock before the show.
- **Imports are never repeated.** Importing the same log file twice adds nothing, but a log file cannot
  fill a time Stagewatch itself was not recording.

## Platforms and browsers

- **Tested install platforms.** Windows 11 and Windows 10 and Debian 13.7 (virtual machines), and a
  Raspberry Pi 4 (4 GB). **Not tested:** a Raspberry Pi 5, the Pi kiosk screen, macOS and other Linux
  versions.
- **Browsers.** The dashboards are built to open on an old iPad running iOS 12 Safari as well as on
  current phones, tablets and laptops. Anything older than that is not a target.
- **A tablet that goes to sleep stops showing.** Set it to stay awake. The alarm
  sound needs one tap on the **alarm sound** button after the page opens, because browsers do not
  allow sound until you do.
- **Dashboards have no login.** Anyone on the show network can open them.

## Hardware

- **Not every board and sensor has been built and run.** Each [node guide](nodes/README.md) says which
  ones have been tested. The rest are written but not yet tried on real parts.
- **Wi-Fi nodes need 2.4 GHz.** A node on a weak signal drops out, and the dashboards show it.
- **Tried on one device only:** the RME 12Mic (other RME units are untested), the Shelly PM Mini Gen3
  (other Shelly models are untested), and DirectOut GLOBCON (one controller; the level scale for each
  channel type is not confirmed, and the live meters should be compared with GLOBCON's own screen).
- **The RME card cannot spot a silent stall by itself.** It relies on Stagewatch's link to the unit
  going away. Compare it with the unit's own display if the numbers stop moving.
- **Bluetooth tilt sensors** have not yet been run on a Raspberry Pi, and Windows is untested.
- **Not yet tested on real kit:** the Wi-Fi card, the Condensation risk card, the header internet icon,
  OSC output to a real receiver, and the Clock card's helper on a real Pi. Each page for these says so.
  They have been checked with simulated data only.

## Security

- **Run Stagewatch on a private show network.** Never put it on the public internet. See
  [SECURITY.md](../SECURITY.md).
- **The Admin page is protected by a PIN only.** There are no user accounts.
- **Some kit has no login at all.** RME units accept changes from any device on their network, so keep
  their network separate. Stagewatch sends them nothing.
- **The Wi-Fi card shows its password** to every dashboard that has the card. Use it for guest or crew
  internet only, never the network your show equipment runs on.
- **Channel names on the RME card are shown on every dashboard that has it.** Put nothing private in one.
- **The updater trusts GitHub.** It cannot protect you if the project's GitHub account were taken over.
- **The Pi clock button** needs a small root-owned helper and relaxes two of the service's systemd
  protections. You can leave it out when installing, or remove it later. See [SECURITY.md](../SECURITY.md).

## Not yet built

These are ideas, not promises: wind and lightning readings, more power metering (voltage drop, UPS
types), links to more show software, more RME information (gain, 48 V and phase are not read), a Nebra
status report button in Admin, and a one-click installer or flashing a node from the browser.
