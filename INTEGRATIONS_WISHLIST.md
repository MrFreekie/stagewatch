# Integration wishlist: live concert audio

Candidate integrations, in the spirit of a Q-SYS plugin catalog. The status reflects
publicly available documentation at the time of writing. Check it again before
building.

**Legend**
- **Priority**: ★★★ high show value · ★★ useful · ★ niche
- **Docs**: ✅ public · 🔒 gated / on request · ❓ unverified · ❌ closed
- **Direction**: ⬅ into Stagewatch · ➡ out of Stagewatch · ⬌ both

Anything touching rigging, power or safety systems is **read-only and advisory**.
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

## Power
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| Distro meters (Eastron, Carlo Gavazzi, Schneider, Socomec, Janitza) | V / A / Hz / kW / THD per phase | Modbus RTU/TCP | ✅ | ★★★ | ⬅ |
| Bender RCMS / DOLD residual current monitors | Earth-leakage trend | Modbus RTU | ✅ | ★★★ | ⬅ |
| Deep Sea Electronics generator controllers | Genset state, load, fuel | Modbus (GenComm) | 🔒 | ★★★ | ⬅ |
| ComAp generator controllers | Genset | Modbus TCP | ✅ | ★★ | ⬅ |
| UPS (APC / Eaton / CyberPower) | On battery, runtime | NUT / SNMP | ✅ | ★★ | ⬅ |
| Whirlwind PL-PM1RJ | Distro metering | Ethernet | ❓ | ★ | ⬅ |

## PA system, amplifiers, processing
| Integration | Brings | Protocol | Docs | Pri | Dir |
|---|---|---|---|---|---|
| d&b amplifiers, DS100 | Mains voltage, temperature, protect/fault | OCA / AES70 | ✅ | ★★★ | ⬅ |
| L-Acoustics LA-series, P1, LC16D | Amp health, mains, temperature | Electronics HTTP API | 🔒 | ★★★ | ⬅ |
| DirectOut Prodigy / ACE | Input status, clock, redundancy state | Vendor remote protocol | ❓ | ★★★ | ⬅ |
| Powersoft | Amp health | Vendor | ❓ | ★★ | ⬅ |
| Lake / Lab.gruppen | Processor and amp health | Lake third-party API | ❓ | ★★ | ⬅ |
| Meyer Galaxy / self-powered cabinets | Cabinet health | Vendor | ❓ | ★ | ⬅ |
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
| Dante / AES67 | Device presence, PTP leader | mDNS / PTP | partly | ★★ | ⬅ |
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
| Seeed XIAO MG24 (Sense), nRF52840; Adafruit Feather nRF52840 | BLE only | Later | BLE beacons via the Bluetooth relay, or USB serial. |
| Seeed XIAO RP2040/RP2350/SAMD21; Adafruit Feather RP2040/M4 | none | Later | USB serial nodes, once the `serial_line` input exists. |

## Feature backlog (non-integration)
- **Setlist / day schedule sheet**: a built-in running order for the day (doors, support, changeover, headliner, curfew) and a per-show setlist. Items can be entered by hand or imported (CSV/paste). Schedule times auto-create timeline markers, and a dashboard card shows "now / next / time to curfew". It could run standalone or sync with Ontime (above) when that's in use.
- **Custom logo upload**: the admin uploads a logo (production, venue or company) shown in dashboard headers and kiosk/wall views. Size- and type-limited (PNG/SVG/JPEG), stored in the data folder (never the repo), SVG sanitised or served with a safe content type, and removable to revert to the default.

## Documented as not integrable (for now)
- **SSE / Solotech ProSight** inclinometers: no data output is documented.
- **LAPTEQ / Nexo GEO Sight** displays: standalone.
- **Eilon Ron StageMaster** load cells: proprietary software only.
- **Time.is**: its terms of use forbid use from scripts and apps (a separate API is available by contacting them). Use public NTP for internet time instead.
- **Hoist-integrated load cells** (Kinesys Apex, Movecat, ChainMaster): these belong to the automation operator's system.
