# Integration wishlist: live concert audio

Candidate integrations, in the spirit of a Q-SYS plugin catalog. The status reflects
publicly available documentation at the time of writing. Check it again before
building.

**Legend**
- **Priority**: ★★★ high show value · ★★ useful · ★ niche
- **Docs**: ✅ public · 🔒 gated / on request · ❓ unverified · ❌ closed
- **Direction**: ⬅ into Stagewatch · ➡ out of Stagewatch · ⬌ both

Anything touching rigging, power or safety systems is **read-only and advisory**. For UPSs,
conditioners and PDUs this means Stagewatch never sends shutdown, self-test or outlet on/off
commands, and never asks for SNMP write access.
Anything going out to audio gear is **opt-in, admin-configured, and notify-style by
default**. Stagewatch never mutes or changes the PA or mix on its own.

## Environment and site
| Integration | Brings | Protocol | Docs | Pri | Dir | Status |
|---|---|---|---|---|---|---|
| ESPHome DIY nodes | T/RH/P, wind, tilt, contacts, outputs | ESPHome native API | ✅ | ★★★ | ⬌ | **v0.1 (sensors)** |
| Shelly | Power, contacts, relays | Gen2+ RPC / MQTT | ✅ | ★★★ | ⬌ | planned |
| Gill WindSonic / Vaisala WXT | Pro wind (+T/RH/P) | NMEA MWV / ASCII / Modbus | ✅ | ★★★ | ⬅ | planned |
| Broadweigh / Mantracourt T24 | Wind + load shackles | T24 binary / T24-GW1 Modbus | ✅ | ★★★ | ⬅ | planned |
| Crosby Straightpoint Radiolink | Load cells | Modbus RTU / ASCII | ✅ | ★★ | ⬅ | planned |
| Kinesys Libra | Load cells | Relay outputs (protocol ❓) | ❓ | ★★ | ⬅ | |
| d&b ArraySight | Array angle, T/RH | AES70 over PoE | 🔒 | ★★ | ⬅ | |
| L-Acoustics P1 Sensor | T/RH | Electronics HTTP API | 🔒 | ★★ | ⬅ | |
| RuuviTag / pvvx BLE tags | Cheap T/RH/P | BLE adverts | ✅ | ★ | ⬅ | |
| **ESP32 Bluetooth relay** (ESPHome `bluetooth_proxy`) | Picks up BLE sensor adverts (RuuviTag, pvvx, XIAO nRF52840/MG24 beacons) anywhere on site and forwards them over Wi-Fi/Ethernet. Fixes BLE's 10–30 m range and flaky Windows Bluetooth. | ESPHome native API BLE advertisement stream (reuses the `esphome` integration) | ✅ | ★★★ | ⬅ | **dev list** |
| Seeed XIAO MG24 Sense (EFR32MG24) | USB-connected tilt (rough, ±0.5–1°; not array-angle grade) + env node via add-on BME280/SHT45; BLE beacon later. The on-board mic is **not** SPL-grade. | Arduino sketch → `ENV,`/`TILT,` lines over USB serial (phase 3 `serial_line`); BLE via the relay above | ✅ | ★ | ⬅ | |
| METAR / met-office API | Forecast wind and pressure baseline | HTTPS | ✅ | ★ | ⬅ | |
| **WeatherFlow Tempest** weather station | Wind (mean, gust, direction), rain, **lightning distance and count**, temperature, humidity, pressure, sun / UV: one box covers the wind, lightning, rain and heat-stress features | Local UDP broadcast on the LAN (no cloud needed) | ✅ | ★★★ | ⬅ | |
| Davis WeatherLink Live | Wind, rain, temperature / humidity from Davis stations | Local HTTP API (+ UDP live broadcast) | ✅ | ★★ | ⬅ | |
| Ecowitt gateways | Wind, rain, temperature / humidity; lightning with the WH57 sensor | Gateway "custom server" HTTP push to Stagewatch | ❓ (community-documented) | ★★ | ⬅ | |
| Kestrel handheld weather meters (LiNK models) | Reference temperature / humidity / wind for calibration | Bluetooth LE (LiNK) | ❓ (check what is documented) | ★★ | ⬅ | |
| Air quality and CO2: Aranet4, ESPHome SCD41 / PM sensors | CO2 and particulates in indoor arenas, tents and dusty sites (crew welfare) | Bluetooth LE (Aranet4) / ESPHome native API | ✅ ESPHome, ❓ Aranet4 | ★★ | ⬅ | |
| Official weather warnings (Met Office and other national services) | Severe wind and thunderstorm warnings for the site on the timeline, when the hub is online | CAP / RSS feeds over HTTPS | ✅ (check each country's terms) | ★★ | ⬅ | |
| **Lightning detector node** (ESPHome + AS3935 breakout) | Strike distance (km) and strike count/trend, for the event's lightning / "30-30" procedure. Advisory only; the AS3935 estimates distance to the storm front and can false-trigger near switching power supplies | ESPHome `as3935_i2c` / `as3935_spi` (reuses the `esphome` integration) | ✅ | ★★★ | ⬅ | **planned** |
| Online lightning data (e.g. Blitzortung) | Strikes near the site when the hub has internet; cross-check for the local detector | HTTPS / WebSocket | ❓ (terms of use must be checked; Blitzortung restricts use of its data) | ★★ | ⬅ | |
| Amp rack monitor node (ESPHome) | Rack temperature, fan running (tach or airflow), rack door contact, UPS dry contact | ESPHome native API | ✅ | ★★ | ⬅ | planned |
| **Battery sensor nodes** (ESPHome deep sleep) | Env / wind readings from places with no mains power; wakes, reports, sleeps. Battery level and "last seen" shown per node | ESPHome native API | ✅ | ★★ | ⬅ | planned |

## Power
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| **Distro meters with built-in Ethernet**: Eastron SDM630-TCP / SDM630MCT-TCP, Janitza UMG 96-PA (+ -EL module) / UMG 96RM-E, Carlo Gavazzi EM24 E1, Schneider PM5560, ABB M4M 30 Ethernet, Phoenix Contact EMpro, Siemens PAC3200/3220 | Current per phase **and neutral**, the meter's own **max-demand / peak** values (so short peaks are not missed), V, Hz, kW, kWh, THD | Modbus TCP (read-only: function codes 03/04 only) | ✅ | ★★★ | ⬅ |
| Distro meters on RS485 (Modbus RTU), via an isolated RS485-to-Ethernet gateway (e.g. Moxa MGate MB3170I) | As above | Modbus RTU through a Modbus TCP gateway | ✅ | ★★★ | ⬅ |
| **Earth leakage with a continuous mA reading** (not just a trip relay): Janitza UMG 96-PA-RCM-EL (type A/B), Bender RCMS150-01 / RCMS410 (+ COM465IP gateway), Doepke e.Guard RCM B (PoE), Socomec Digiware R-60, Siemens 5SV8 COM | Residual current in mA per circuit, leakage creep trend, time above warning level | Modbus TCP / Modbus RTU / MQTT (Bender COM465IP) | ✅ | ★★★ | ⬅ |
| Shelly Pro 3EM / Pro 3EM-400 (120 A or 400 A clamps, optional neutral CT) | Current per phase + N, V, kW, kWh. Cheap and quick to clamp on; no peak-hold values | HTTP RPC / MQTT / Modbus TCP | ✅ | ★★★ | ⬅ |
| Schneider PowerTag (wireless, via Panel Server) | Current per phase (+ N on some models), kWh | Modbus TCP from the Panel Server | ✅ | ★★ | ⬅ |
| StageSmarts C24 distro | Supply voltages, current incl. neutral, per-channel load | Built-in web server (API unknown) | ❓ | ★ | ⬅ |
| Portable power loggers (Fluke 1736/1738, Chauvin Arnoux PEL 104/106) | Live power data during setup | Vendor apps only; no open live interface found | ❌ | ★ | ⬅ |
| Deep Sea Electronics generator controllers (DSE7320 MKII etc.; Ethernet via DSE855 / DSE890) | Genset state, load, fuel | Modbus (GenComm register map on request from DSE). Start/stop/mode keys are never sent | 🔒 | ★★★ | ⬅ |
| ComAp generator controllers (InteliLite 4) | Genset | Modbus TCP, port 502 (full register list on request) | 🔒 | ★★ | ⬅ |
| **Rackmount UPS with a network card**: APC Smart-UPS SRT/SRTL/SMT/SMTL (NMC3), Eaton 5P/5PX/9PX (Network-M2/M3), CyberPower OL/PR (RMCARD205/305), Vertiv GXT5 (RDU101), Riello (NetMan 204) | On battery, charge %, runtime left, load %, input/output V and Hz, battery temperature, alarms (overload, bypass, fault, replace battery), self-test result; mains failures and transfers as timeline markers | SNMP v1/v2c/v3: standard UPS-MIB (RFC 1628) + vendor MIBs, plus SNMP traps | ✅ | ★★★ | ⬅ |
| UPS over USB / serial, via NUT (Network UPS Tools) | Same as above, for UPSs without a network card. A NUT server on a Pi or Linux box owns the USB cable; Stagewatch reads it over the network | NUT network protocol, TCP 3493 (RFC 9271) | ✅ | ★★★ | ⬅ |
| Furman F1500-UPS / BlueBOLT CV2 | Battery %, charging / discharging, bank status | RS-232 ASCII / BlueBOLT CV2 local UDP (XML) | ✅ | ★★ | ⬅ |
| Smart PDUs / conditioners: Middle Atlantic RackLink Premium+, SurgeX Squid | Per-outlet voltage, current, power | Redfish / JSON HTTP / SNMP | ✅ | ★★ | ⬅ |
| Whirlwind PL-PM1RJ (US 120 V only) | Distro metering incl. neutral | Ethernet web page | ❓ | ★ | ⬅ |
| Rental power platforms (Aggreko Connect, Power Logistics, Atlas Copco FleetLink, Pramac Link) | Genset / distro telemetry | Vendor cloud portals; no public API found | 🔒 | ★ | ⬅ |

## PA system, amplifiers, processing
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| d&b amplifiers, DS100 | Mains voltage, temperature, protect/fault | OCA / AES70 | ✅ | ★★★ | ⬅ |
| L-Acoustics amplified controllers: LA2Xi, LA4X, LA12X, LA7.16(i) (LA8 to be confirmed), plus P1 and LC16D | Amp fault state, per-channel protect / temperature state / limit / impedance faults, fuse protect, power supply, input source and fallback, AES / AVB / clock status, standby, preset | HTTP JSON API, polled with GET only (never commands). The vendor API docs are on request; the open-source Companion module shows the structure. **Option:** watch the LA Network Manager event log (XML) on the system tech's laptop | 🔒 / ❓ | ★★★ | ⬅ |
| DirectOut Prodigy / ACE | Input status, clock, redundancy state | Vendor remote protocol | ❓ | ★★★ | ⬅ |
| Powersoft | Amp health | Vendor | ❓ | ★★ | ⬅ |
| Lake / Lab.gruppen processors and amps: LM 26 / LM 44, LMX 48 / LMX 88, PLM / PLM+ / D Series | PSU A/B, temperature, fan, NO INPUT, clock slipping, Dante faults, input source change (failover), mutes, frame offline | DLM (Direct Lake Messaging, binary UDP; read-only queries; spec on request). **Option:** watch the Lake Controller Event Log (XML) on the system tech's laptop; set the log save interval in Lake Controller v8 so it is written during the show | ❓ / ✅ (log) | ★★ | ⬅ |
| Meyer Galaxy / self-powered cabinets | Cabinet health | Vendor | ❓ | ★ | ⬅ |
| Martin Audio (Vu-Net / iKON amplifiers) | Amp and cabinet health, temperature, faults | Vendor (check for a third-party protocol) | ❓ | ★★ | ⬅ |
| Adamson (amplified systems / PLM-based racks) | Amp health, faults | Vendor (check) | ❓ | ★★ | ⬅ |
| Nexo (NXAMP / NeMo) | Amp health, temperature, protect | Vendor (check for a NeMo / NXAMP remote protocol) | ❓ | ★★ | ⬅ |
| JBL / Crown (HiQnet: Performance Manager, I-Tech, VTX amps) | Amp health, mains, temperature, faults | HiQnet (Harman third-party protocol, check availability) | ❓ | ★★ | ⬅ |
| Biamp Tesira | Read named control values (system status, faults, levels) | Tesira Text Protocol (TTP) over Telnet/SSH | ✅ | ★★ | ⬅ |
| Spatial / immersive processors: L-ISA Controller, KLANG | Snapshot / scene recall → marker; processor status | OSC | ❓ (check each vendor's OSC docs) | ★★ | ⬅ |
| Delay / alignment tools (any OSC receiver) | Averaged T/RH/P + speed of sound | OSC `/stagewatch/avg/env` | ✅ | ★★★ | ➡ **v0.1** |

## Consoles
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| DiGiCo SD / Quantum | Snapshot → marker; alarm → GPI macro | Macro OSC, GPI/GPO, MIDI | ✅ | ★★★ | ⬌ |
| Yamaha RIVAGE / CL / QL / DM3 / DM7 | Scene → marker; user-key indication | RCP (TCP 49280), RIVAGE OSC spec | ✅ | ★★★ | ⬌ |
| Allen & Heath dLive / Avantis / SQ | Scene → marker; GPIO module | TCP MIDI (51325/51327), A&H GPIO | ✅ | ★★★ | ⬌ |
| Midas / Behringer M32 / X32 / WING | Scene → marker | OSC | ✅ (community) | ★★ | ⬌ |
| SSL Live, Midas HD96, Avid VENUE | Scene / state | MIDI / GPIO | ❓ | ★ | ⬅ |

## RF, IEM, comms
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| Shure ULX-D / Axient Digital / QLX-D / SLX-D / PSM | Battery runtime, RF, audio, interference | TCP 2202 command strings | ✅ | ★★★ | ⬅ |
| Sennheiser EW-DX / Digital 6000 / EW-D | Battery, RF, warnings | SSCv1 / SSCv2 | ✅ | ★★★ | ⬅ |
| Wisycom, Lectrosonics, Sony DWX | Battery / RF | Vendor | ❓ | ★ | ⬅ |
| **RF Explorer** spectrum analysers | RF noise floor and interference on chosen bands over time, on the same timeline as dropouts | USB serial API | ✅ | ★★ | ⬅ |
| Riedel, Clear-Com, Green-GO intercom | Hold / show stop → marker; alarm → call light | GPIO | ✅ | ★★ | ⬌ |

## Measurement, SPL, noise
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| Smaart / Smaart SPL | LAeq / LCeq, SPL alarms | WebSocket JSON API | ✅ | ★★★ | ⬅ |
| NTi XL2 | SPL logging | USB remote commands | ✅ | ★★ | ⬅ |
| 10EaZy, Svantek, Cirrus off-site monitors | Off-site noise | Vendor / cloud | ❓ | ★★ | ⬅ |

## Network, clock, show control, indicators
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| Bitfocus Companion | Stream Deck values, Mark/Ack buttons, bridge to hundreds of modules | HTTP / OSC, custom variables | ✅ | ★★★ | ⬌ |
| Luminex GigaCore, Netgear AV M4250 | Link, PoE budget, port errors | SNMP / REST | ✅ / ❓ | ★★ | ⬅ |
| **Syslog receiver** (show switches, routers, Wi-Fi APs, other gear) | Switch and network events on the show timeline: port up/down, PoE overload, loop / spanning-tree changes, errors. Filter by device and severity; chosen messages become alarms or markers. Only accepts messages from devices the admin lists; size- and rate-limited; stored with the show's history | Syslog RFC 3164 / RFC 5424 over UDP (and TCP). Uses a port above 1024 (e.g. 5514) by default, because port 514 needs extra permissions on Linux | ✅ | ★★★ | ⬅ |
| SNMP traps (switches, UPS) | Same idea as syslog, for gear that only sends traps | SNMP v2c / v3 traps | ✅ | ★★ | ⬅ |
| UniFi controller API (UniFi Network application) | Wi-Fi client counts, AP and switch health, device offline: explains "the tablets keep dropping" | UniFi Network API (REST; check the official vs community API) | ❓ | ★★ | ⬅ |
| Clock servers (Meinberg and others) | NTP / PTP health of the show network: sync state, offset, holdover | SNMP (vendor MIBs) / web | ✅ (Meinberg MIBs) | ★★ | ⬅ |
| **Stagewatch computer health** (built in) | CPU temperature and load, free disk, memory, network link state, clock sync offset: so the monitor is monitored too | Local OS | ✅ | ★★★ | ⬅ |
| Dante / AES67 | Device presence, PTP clock leader and **leader changes** (clock health) | mDNS / PTP | partly | ★★ | ⬅ |
| QLab, grandMA3, TouchOSC, Chataigne | Markers in, alarms out | OSC | ✅ | ★★ | ⬌ |
| LTC / MTC timecode | Timecode on markers | Audio / MIDI | ✅ | ★★ | ⬅ |
| Pi GPIO, USB and Modbus relays, ESPHome outputs | Relays, sounders, stack lights, displays | native | ✅ | ★★★ | ➡ |
| Patlite / Werma network stack lights, Art-Net / sACN fixtures | Visual alarms | Socket / HTTP, Art-Net | ✅ | ★★ | ➡ |
| ntfy / Pushover / Telegram | Alerts for roaming crew | HTTPS | ✅ | ★★ | ➡ |
| [Ontime](https://github.com/cpvalente/ontime) (open-source rundown / show timer) | **Wall Clock source** (time of day, in planning); later: running order, current/next item, timers, and cue markers | WebSocket `runtime-data` (~1 Hz, `clock` = ms since local midnight) and HTTP `GET /api/poll`, **verified on v4.14.0** | ✅ | ★★★ | ⬅ |
| Local NTP server | **Wall Clock source** and clock-health check (offset, stratum) for the show network | SNTP (UDP 123) | ✅ | ★★ | ⬅ |
| Internet time (public NTP: pool.ntp.org, time.cloudflare.com, NIST) | **Wall Clock source** when the hub has internet; reference for checking the other clocks | SNTP (UDP 123); NTS optional later | ✅ | ★★ | ⬅ |
| USB serial GPS receiver | **Wall Clock source** (UTC from GPS; independent of the network) with fix status | NMEA 0183 `$GPRMC` / `$GPZDA` over serial (PPS later) | ✅ | ★★ | ⬅ |

## Node hardware (DIY boards)
Boards expected to work as Stagewatch sensor or output nodes. **Supported** = tested with Stagewatch; **Planned** = intended for support, with an example config to be written; **Candidate** = should work (runs ESPHome) but not planned yet. Sensors plug in over I2C (Qwiic/STEMMA QT where available): BME280, SHT45, SCL3300, etc.

| Board | Radio / network | Status | Notes |
|---|---|---|---|
| ESP32 DevKit + BME280 / SHT45 | Wi-Fi, BLE | Example configs in `esphome/` | Reference node for v0.1 (not yet tested on hardware). |
| Olimex ESP32-POE(-ISO) | Ethernet + PoE | Example config in `esphome/` | Isolated PoE version recommended on show power. |
| **Seeed XIAO ESP32C3** | Wi-Fi 2.4 GHz, BLE | **Planned** | Cheapest tiny env node; very mature ESPHome support. |
| **Seeed XIAO ESP32C5** | **Dual-band 2.4 / 5 GHz Wi-Fi 6**, BLE | **Planned** | For crowded 2.4 GHz festival sites; needs ESPHome 2026.7+. |
| Seeed XIAO W5500 Ethernet Adapter (ESP32-S3 + PoE) | Ethernet + 802.3af PoE | Candidate (strong) | One-cable fixed node, or a wired Bluetooth relay. |
| Seeed XIAO ESP32S3 / ESP32C6 | Wi-Fi, BLE | Candidate | S3 suits the Bluetooth relay, displays and outputs. |
| **Adafruit Feather ESP32-S3** (4 MB flash / 2 MB PSRAM, and 8 MB flash / no PSRAM) | Wi-Fi, BLE | **Planned** (owned; config in progress) | STEMMA QT plug-in sensors, LiPo charging + battery gauge. |
| **Adafruit Feather ESP32-S3 TFT** (4 MB / 2 MB PSRAM) | Wi-Fi, BLE | **Planned** (owned; config in progress) | Built-in 240×135 screen: the node shows its own readings, IP and status. The Reverse TFT should also work (candidate). |
| Waveshare ESP32-S3 1.47" LCD (172×320, USB-A plug) | Wi-Fi, BLE | Candidate (display) | A small USB-powered **display node**: plug it into any USB port for a mini Wall Clock or site readout. Needs the planned output-display feature (Stagewatch pushing values to a node). No STEMMA QT connector. |
| Adafruit Feather ESP32 V2 | Wi-Fi, BLE | Candidate | STEMMA QT; classic ESP32 (good Bluetooth relay). |
| Adafruit Feather ESP32-C6 | Wi-Fi 6, BLE | Candidate | STEMMA QT, battery. |
| Adafruit Feather ESP32-S2 | Wi-Fi only (**no BLE**) | Candidate (env only) | Fine as an env node; can't be a Bluetooth relay. |
| Adafruit Feather + Ethernet FeatherWing (W5500) | Ethernet (no PoE) | Candidate | Wired node; power separately or via a PoE splitter. |
| **Adafruit TMP119** (PID 6482, ±0.03 °C) reference probe | via host board: Wi-Fi (ESPHome) or USB serial | **Planned** (calibration reference) | Temperature-only reference for calibrating other nodes. ESPHome: use the `tmp117` platform (the TMP119 is register-compatible; ESPHome's tmp117 driver doesn't check the device ID; **untested**). USB-serial version later (Arduino + Adafruit_TMP117 library → `ENV,` lines) as a portable probe plugged into the Stagewatch PC. |
| Seeed XIAO MG24 (Sense), nRF52840; Adafruit Feather nRF52840 | BLE only | Later | BLE beacons via the Bluetooth relay, or USB serial. |
| Seeed XIAO RP2040/RP2350/SAMD21; Adafruit Feather RP2040/M4 | none | Later | USB serial nodes, once the `serial_line` input exists. |

## Feature backlog (non-integration)
- **Setlist / day schedule sheet**: a built-in running order for the day (doors, support, changeover, headliner, curfew) and a per-show setlist. Items can be entered by hand or imported (CSV/paste). Schedule times auto-create timeline markers, and a dashboard card shows "now / next / time to curfew". It could run standalone or sync with Ontime (above) when that's in use.
- **One-click installer** (roadmap): download one file, double-click, approve the Windows admin prompt, and Stagewatch installs itself. It installs Git and uv via winget, runs the managed install and opens the first-run page. A signed/packaged installer comes later; the Raspberry Pi gets an equivalent. Spec: in planning.
- **Windows portable edition** (roadmap): a zip with everything inside (its own Python included). Unzip, double-click `Start Stagewatch`, no admin rights and no Git needed. Good for trying Stagewatch out, demos, borrowed laptops and a USB stick in the gig bag. Unlike the installed version it only runs while someone is logged in with the window open, and updates by downloading the new zip. To make it permanent (starts at power-on, updates itself), install Stagewatch normally and copy your data folder across: the portable edition links to the steps. Windows on ARM isn't supported. Comes after the one-click installer. Spec: in planning.
- **Browser-based node flashing** (roadmap): plug a board into USB, open a page in Chrome or Edge, pick your board, click Install and enter the Wi-Fi. Prebuilt firmware per supported board comes from CI (ESP Web Tools + Improv Wi-Fi), and a per-node security key is set when the node is adopted. No compiling and no YAML. Spec: in planning.
- **Manual backup and restore**: an admin "Backups" card with:
  - **Back up now** with a note, and a list of automatic (pre-update) and manual backups, showing sizes;
  - **download** a backup as a zip; it contains the PIN hash and ESPHome keys, so it's admin-only with a warning;
  - **restore** a chosen backup: needs the admin PIN, is applied by the launcher with the server stopped, keeps the current data aside, and checks the sha256 manifest first;
  - **scheduled daily backups** with retention.
  Import/upload of a backup from another hub comes later, because it needs careful validation. It builds on the updater's backup system (`backup.py`).
- **Calibration assistant**: in admin, pick a reference sensor (e.g. TMP119) and the sensors under test, place them together, and let it collect for about 10–15 min. It shows the mean difference and how stable it was, then offers **Apply offset** with one click (reusing the per-sensor offset). Temperature from the reference; for humidity, guide the user through a saturated-salt check (≈75 %RH NaCl, ≈33 %RH MgCl₂). Shows 'not settled' if readings are still drifting.
- **Custom logo upload**: the admin uploads a logo (production, venue or company) shown in dashboard headers and kiosk/wall views. Size- and type-limited (PNG/SVG/JPEG), stored in the data folder (never the repo), SVG sanitised or served with a safe content type, and removable to revert to the default.

### Advice for FOH (uses data Stagewatch already has)
- **Alignment drift warning**: "Temperature is up 6 °C since the *Aligned* marker: the delay towers at 60 m have drifted about 1.1 ms." Shows a suggested delay per path (distances entered by the admin) and alerts when the drift passes a threshold you set. Advisory: Stagewatch never changes delays itself.
- **High-frequency air loss** (ISO 9613-1 air absorption): extra loss at 8 and 16 kHz over each throw distance, now compared with soundcheck. Explains why a mix goes dull as the evening turns damp.
- **Temperature inversion**: two sensors at different heights (deck and PA height) show whether the air gets warmer or cooler with height, which bends sound down towards or up away from the crowd and neighbours.
- **Wind relative to the PA**: wind direction shown against the PA's aim and the delay towers, and towards noise-sensitive neighbours (bearings set by the admin).
- **Curfew SPL budget**: predicts from the current level and trend whether the LAeq15 will go over the limit before the 15 minutes are up ("At this level you'll exceed the 15-min limit in about 4 min"), not only after.

### Site and crew welfare
- **Heat stress for crew**: an estimated WBGT heat-stress index for stage and FOH on hot days, with advisory levels. Estimated from temperature, humidity and (optionally) a black-globe or sun sensor; not a certified WBGT meter.
- **Condensation risk**: warns when gear or cases (measured with a surface or case sensor) are colder than the air's dew point. Useful for early load-ins.
- **Forecast overlay** (later): the forecast wind and rain drawn on the timeline ahead of "now".

### Reports and history
- **End-of-day show report**: an HTML page (printable to PDF) with the day's temperature / humidity / wind ranges, alarms and acknowledgements, markers, SPL against the limit, and device faults. Built from the recorded history, so it can be made at any time, including after a crash or power cut; a partial day is marked as such, and gaps where the hub was down are shown rather than hidden.
- **Tour history / venue profiles**: "Last time at this venue: 14 °C, 71 % RH, delays set to X." Venue conditions and notes kept across a tour.

### Dashboards
- **Floor-plan view**: upload a site plan and place sensors on it, coloured by their current value.
- **Themes / skins**: more display styles, including a red-on-black night mode for dark FOH positions.
- **Other languages** (eventually).

### Much later
- Import speaker positions and distances from Soundvision / ArrayCalc for the drift and air-loss figures.
- Rain sensor / gauge. Crew noise dose. Curfew countdown card with overrun warning. E-ink displays for delay towers and rigging points.

## Documented as not integrable (for now)
- **SSE / Solotech ProSight** inclinometers: no data output is documented.
- **LAPTEQ / Nexo GEO Sight** displays: standalone.
- **Eilon Ron StageMaster** load cells: proprietary software only.
- **Time.is**: its terms of use forbid use from scripts and apps (a separate API is available by contacting them). Use public NTP for internet time instead.
- **Hoist-integrated load cells** (Kinesys Apex, Movecat, ChainMaster): these belong to the automation operator's system.
