# Feather S3 environment node (BME280 or SHT45)

This builds a **node** from an Adafruit ESP32-S3 Feather and a plug-in sensor that measures
temperature, humidity and (with the BME280) pressure. **No soldering.**

This page covers only what is special to this build. The steps every node shares
(secrets, flashing, adopting in Admin, calibration, updating, fixed IP) are in the
[node hub](README.md). Follow the hub's steps, using the notes below at the right moment.

> **Test status.** Both files here check out as valid with ESPHome 2026.9.1. A full build
> and flash of them was not repeated while writing this guide, and the TFT screen layout is
> marked untested on hardware in the file itself. Check your node's readings against a
> reference thermometer before you trust it.

Building the TFT Feather with the MS8607 sensor instead? Use the
[Feather S3 TFT node](feather-s3-tft-ms8607.md). Building a barometer (pressure) node
instead? Use [Pressure nodes](pressure-nodes.md).

---

## What you need

| Item | Notes |
|---|---|
| **Adafruit ESP32-S3 Feather** with STEMMA QT | Either the plain one, or the **TFT** one, which has a small screen. |
| **A sensor with a STEMMA QT connector** | **BME280** (Adafruit 2652) gives temperature, humidity **and pressure**. **SHT45** (Adafruit 5665) is more accurate on temperature and humidity but has **no pressure**. Easiest choice: the BME280. |
| **A STEMMA QT cable** | Get one **at least 10 cm long**. Shorter ones keep the sensor too close to the board's heat. |

The USB cable, power, computer and 2.4 GHz Wi-Fi are on the [hub](README.md#what-you-need-every-node).

---

## Plug it together (hub Step 1)

1. Plug the STEMMA QT cable into the Feather's **STEMMA QT port** (the small white socket).
2. Plug the other end into the sensor.

The plugs only fit one way round. Do not force them.

**What you should see:** nothing yet. There is nothing else to wire.

Where to put the sensor is in [hub Step 2](README.md#step-2-decide-where-it-will-live).
The Feather itself makes heat, so keep the sensor on the cable, in open air, away from it.

---

## Which file, and the small settings (hub Steps 3 and 4)

**Plain Feather (no screen):** use `stagewatch-feather-s3.yaml`. Near the top, under
`substitutions:`, check that the board matches yours. Look at the sticker or the shop page:

| Your board | `board:` | `flash_size:` |
|---|---|---|
| Adafruit **5323** (STEMMA QT, 8 MB flash, no PSRAM). This is the default in the file. | `adafruit_feather_esp32s3_nopsram` | `8MB` |
| Adafruit **5477** (4 MB flash, 2 MB PSRAM) | `adafruit_feather_esp32s3` | `4MB` |

A wrong `flash_size` (bigger than fitted) means the board will not start.

**TFT Feather:** use `stagewatch-feather-s3-tft.yaml`. It needs no board choice.

Change `node_name` to something short and unique, as in hub Step 4.

**Using an SHT45 instead of the BME280?**

- **Plain Feather:** in the file, find the block that says `Primary: BME280`. Put a `#` at
  the start of each line of that block, so the file ignores it. Then remove the `#` from
  each line of the `Alternative: SHT45` block just below it. Keep the spaces at the start
  of each line exactly as they are. YAML cares about spaces.
- **TFT Feather:** it works but needs a second edit in the screen section (the comment in
  the file says what). The easiest way is to use the **BME280** on the TFT model.
- The SHT45 has no pressure sensor. Stagewatch then works out pressure from the **site
  altitude**, which you set in Admin (hub Step 8).

If you are not sure, keep the BME280.

You may notice **battery** lines in the file. If your board's battery chip differs from the
one enabled, the battery reading may not appear. That does no harm.

---

## Flashing notes (hub Step 5)

The plain Feather and the TFT Feather both use the BOOT and RESET trick in the hub. For the
**TFT** version, the first build also downloads a font, so it needs internet.

---

## Check the node is alive

- **Plain Feather:** the little on-board light is **amber** while it starts or has no
  Stagewatch connection, and turns **dim green** when Stagewatch is connected.
- **TFT Feather:** the screen shows, from top to bottom:
  - the **node name**,
  - the **temperature** in large digits with °C,
  - the **humidity** (%) on the left and **pressure** (hPa) on the right,
  - along the bottom, the **Wi-Fi signal** (in dBm, for example `-60 dBm`, or `no Wi-Fi`)
    and the node's **IP address**.
  - If it shows **NO SENSOR** in red with `check STEMMA QT cable`, the sensor is not being
    read. See "If it didn't work" below.
  - Press the **BOOT** button to turn the screen backlight off and on (useful in dark
    venues).

**What you should see:** the temperature matches the room, give or take a degree or two
while the sensor settles. Then adopt the node in Admin ([hub Step 7](README.md#step-7-add-the-node-in-stagewatch)).

These two files have no 15-minute self-restart and no back-up Wi-Fi network, unlike the
newer files. See the hub.

---

## If it didn't work

The shared problems (no COM port, node not discovered, key errors, hardware faults) are in
the [hub table](README.md#if-it-didnt-work). These are specific to this build.

| Problem | What to check |
|---|---|
| **The TFT says NO SENSOR** (or readings never arrive) | Check the STEMMA QT cable is pushed in **fully** at both ends. Try the other way round if you have two ports on the sensor. Then check the log (below). |
| **The sensor is not detected** | The node scans the sensor connection when it starts and writes each thing it finds into its log, for example `Found i2c device at address 0x77`. A BME280 should show `0x77` (or `0x76` if its ADR jumper has been cut). An SHT45 should show `0x44`. If **nothing** is found, re-seat the cable or try another cable. If a different number shows, tell the project via a GitHub issue. |
| **The build fails at the fonts (TFT)** | The build needs internet to download the font. Check your connection and try again. |
| **The screen is upside down or the colours look wrong (TFT)** | The comment at the top of the file says which two settings to change. It is marked untested on hardware. |

---

## Advanced

For installers. Crew can skip this.

| Part | Plain Feather (5323 or 5477) | TFT Feather (5483) |
|---|---|---|
| STEMMA QT data / clock (I²C) | GPIO3 (SDA) / GPIO4 (SCL) | GPIO42 (SDA) / GPIO41 (SCL) |
| STEMMA QT power switch | GPIO7 (`I2C_POWER`), driven on at boot | GPIO21 (`TFT_I2C_POWER`), also powers the screen, driven on at boot |
| NeoPixel (the status light) | Data GPIO33, power enable GPIO21 | Data GPIO33, power enable GPIO34 |
| BOOT button | GPIO0 | GPIO0 |

If the power switch is not driven on, the sensor has no power. The files do this for you.
Some Adafruit web text lists SDA and SCL as GPIO3 and GPIO4 for every Feather. That is
right for the plain Feather and not for the TFT one.

---

**Next:** [Dashboard cards](../cards/README.md).
