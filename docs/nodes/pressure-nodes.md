# Pressure nodes (barometer)

A **pressure node** is a small Wi-Fi box that measures **air pressure** and sends it to
Stagewatch. Stagewatch uses pressure with temperature and humidity to work out the **speed of
sound**, and shows it on the dashboards.

You can build several and let Stagewatch **average them** (the site average), the same way it
does for temperature. Several nodes in different places also let you spot one that is wrong.

This page covers the DPS310, XIAO and HUZZAH builds, including the three XIAO ESP32C3 builds
(one of them measures the temperature of a rack, not the air). It covers the board choice,
wiring, antenna and the problems that are specific to these boards. The steps every node
shares (secrets, flashing, adopting in Admin, calibration, updating, fixed IP, several Wi-Fi
networks) are in the [node hub](README.md).

Time needed: about 30 to 45 minutes for the first node. Most of that is waiting for the first
build.

## Test status

> **Test status.** All nine node files in this guide have been checked by ESPHome (it reads
> them without errors). The **XIAO ESP32C6 with a DPS310** (pressure only) has run on a real
> board and been adopted in Stagewatch: the sensor answered at `0x76` (address jumper
> bridged) and the status light stayed off while everything was well.
> The **S3 Feather with a DPS310 on the STEMMA QT port** failed at first (the I²C bus was held
> low and nothing answered; see "If it didn't work" below) and later worked with nothing
> changed, so the cause is not known. The **HUZZAH with
> an MPL3115A2** and the **HUZZAH with a BME/BMP280** have both run on a real board and been
> adopted in Stagewatch. The all-in-one files with an SHT45 have not been tested yet. The
> three **XIAO ESP32C3** files (DPS310 with SHT45, BME280, and TMP117) have not been tested
> on hardware yet. Treat the steps for any board not listed as tested as unproven, and tell
> us what you find.
>
> **The all-in-one files (DPS310 plus SHT45) are tested on hardware: pending.** That means
> the SHT45 additions have not run on a real board yet. Check their readings against a
> thermometer and a barometer you trust before you rely on them.

> Stagewatch is an advisory tool with no warranty. Check your node's readings against a
> reference before you trust it. See the [notices](../integrations/README.md#notices).

New to nodes? Read the [node hub](README.md) first.

---

## Pressure only, or all-in-one?

There are two build options for the DPS310 nodes.

- **Option 1: pressure only.** A DPS310 and nothing else. Smallest and simplest. Use it when
  the site already has good air temperature and humidity sensors.
- **Option 2: all-in-one environment node.** The same DPS310 plus a **Sensirion SHT45**. One
  box then reports **pressure, air temperature and humidity**, and all three feed the site
  average.

**Why add the SHT45?** The speed of sound depends mostly on the **air temperature**, and only
a little on pressure and humidity. The DPS310 does report a temperature, but that is the
temperature of the **chip**, not of the air. It reads warm, so it is switched off in the
files. The SHT45 measures the real air temperature (about 0.1 °C) and humidity (about 1 %RH).
Choose option 2 if you want the speed of sound to come from the same box.

## Which board do I build?

| Board | Sensor | Soldering | File |
|---|---|---|---|
| Adafruit ESP32-S3 Feather, 4 MB flash and 2 MB PSRAM (product 5477) | Adafruit DPS310 (product 4494), on a STEMMA QT cable | None | `stagewatch-feather-s3-dps310.yaml` |
| Seeed XIAO ESP32C6 | Adafruit DPS310 (4494) on four jumper wires | Header pins on the XIAO | `stagewatch-xiao-esp32c6-dps310.yaml` |
| Adafruit ESP32-S3 Feather (5477) | DPS310 (4494) **and** Adafruit SHT45 (product 5665), both on STEMMA QT cables | None | `stagewatch-feather-s3-dps310-sht45.yaml` |
| Seeed XIAO ESP32C6 | DPS310 (4494) **and** SHT45 (5665), sharing the same four jumper wires | Header pins on the XIAO | `stagewatch-xiao-esp32c6-dps310-sht45.yaml` |
| Adafruit Feather HUZZAH ESP8266 (product 2821) | Adafruit MPL3115A2 (product 1893) | Headers on both | `stagewatch-feather-esp8266-mpl3115a2.yaml` |
| Adafruit Feather HUZZAH ESP8266 (2821) | BME280 or BMP280 breakout | Headers on the HUZZAH | `stagewatch-feather-esp8266-bme280.yaml` |
| Seeed XIAO ESP32C3 (with its 2.4 GHz antenna fitted) | DPS310 (4494) **and** SHT45 (5665) on STEMMA QT cables | A STEMMA QT (JST SH) connector on D4 and D5 | `stagewatch-xiao-esp32c3-dps310-sht45.yaml` (**not yet tested on hardware**) |
| Seeed XIAO ESP32C3 | A BME280 STEMMA QT breakout (cheaper, reads slightly warm, less accurate) | The same connector | `stagewatch-xiao-esp32c3-bme280.yaml` (**not yet tested on hardware**) |
| Seeed XIAO ESP32C3 | A TMP117 STEMMA QT breakout, for a **rack or amp temperature** (Equipment) | The same connector | `stagewatch-xiao-esp32c3-tmp117.yaml` (**not yet tested on hardware**; set the node to **Equipment** in Admin first, or its temperature would count in the site average) |

How they differ:

- **DPS310** is the best of these for pressure: about 1 hPa absolute, and it notices changes
  of about 0.06 hPa. Build these first.
- **MPL3115A2** is much less accurate (about 4 hPa). Use it to **cross-check** trends and to
  catch a big error, not for the exact pressure. It is a second type of sensor, for
  comparison.
- **BME280** also measures **temperature and humidity**, so this one is a full environment
  node as well as a barometer. A **BMP280** looks identical but has **no humidity**. The
  HUZZAH file covers both (see "Which chip is it?" below).
- The two ESP8266 boards have less memory and weaker Wi-Fi than the ESP32 boards. Do not
  make one the **only** pressure source on a critical site.
- **TMP117** measures the temperature of **equipment**, not the air. It is the only file
  here for an amp rack. Read the Equipment rule in the [hub](README.md#step-7-add-the-node-in-stagewatch).

**The DPS310 and MPL3115A2 also report a chip temperature.** That is the temperature of the
chip, not of the air, and it reads warm. It is **switched off** in these files on purpose,
because Stagewatch would average it into the site temperature and pull it up. Leave it off.
If an installer turns it on to look for heating, untick **Average** for it in Admin.

---

## What you need

Everything every node needs (USB data cable, 5 V supply, computer, 2.4 GHz Wi-Fi, optional
battery and its warning) is in the [hub](README.md#what-you-need-every-node). On top of that,
for your board, the parts from the table above, and:

- **S3 Feather:** a **STEMMA QT cable, 100 mm or longer**.
- **XIAO ESP32C6:** **four female-to-female jumper wires, 10 to 20 cm long**, in red, black,
  blue and yellow if you can get them. The XIAO file has no battery support.
- **XIAO ESP32C3:** a STEMMA QT (JST SH 4-pin) connector soldered to the D4 and D5 pads, a
  STEMMA QT cable, and the **2.4 GHz antenna fitted** (see "Built-in or external antenna?").

**Extra parts for the all-in-one option (DPS310 plus SHT45):**

| Board | Extra parts |
|---|---|
| S3 Feather | The **Adafruit SHT45 breakout (product 5665)** and a **second STEMMA QT cable, 100 to 200 mm**, to go from the DPS310 to the SHT45. The first cable (Feather to DPS310) is 100 mm or longer. |
| XIAO ESP32C6 | The **Adafruit SHT45 breakout (5665)** and **four more jumper wires** (or one Qwiic / STEMMA QT cable) to go from the DPS310's pins to the SHT45's pins. |
| XIAO ESP32C3 | The **Adafruit SHT45 breakout (5665)** and a second STEMMA QT cable to chain it from the DPS310. |
| Outdoors, any board | A small **radiation shield** (a louvred, Stevenson-type screen) for the SHT45. |

---

## Plug it together (hub Step 1)

### S3 Feather and DPS310 (no soldering)

1. Plug one end of the STEMMA QT cable into the **STEMMA QT port** on the Feather (the small
   white socket).
2. Plug the other end into the DPS310.

The plugs only fit one way round. Do not force them.

### XIAO ESP32C6 and DPS310 (four jumper wires)

Solder the header pins onto the XIAO first if they are not fitted. Then join the two boards
with four wires:

| XIAO pin | DPS310 pin | Wire colour |
|---|---|---|
| **3V3** | **VIN** | Red |
| **GND** | **GND** | Black |
| **D4** | **SDA** | Blue |
| **D5** | **SCL** | Yellow |

Check your DPS310 breakout takes **3.3 V** on VIN before you connect it. The Adafruit 4494
does. Jumper wires are for bench use and testing. For a permanent node, solder the leads or
use a Qwiic or STEMMA QT cable, and put the pair in a vented housing with the USB lead held
so it cannot be pulled out.

### Adding the SHT45 (all-in-one option)

Both sensors share one **I²C bus** (the two data wires, SDA and SCL). They do not clash,
because each has its own address: the DPS310 answers at `0x77` (or `0x76` if you bridged its
address jumper) and the SHT45 at `0x44`, which cannot be changed. Every wire is simply passed
on from one board to the next.

**S3 Feather (no soldering).**

1. Plug the first STEMMA QT cable into the Feather's **STEMMA QT port** and the DPS310, as
   above.
2. Plug the second STEMMA QT cable (100 to 200 mm) into the DPS310's **second STEMMA QT
   socket**.
3. Plug the other end of that cable into the SHT45.

**XIAO ESP32C6.** Wire the DPS310 to the XIAO with the four wires in the table above. Then
join the SHT45 to the **DPS310's pins**, one wire each, matching names:

| DPS310 pin | SHT45 pin | Wire colour |
|---|---|---|
| **VIN** | **VIN** (use 3V3) | Red |
| **GND** | **GND** | Black |
| **SDA** | **SDA** | Blue |
| **SCL** | **SCL** | Yellow |

The Adafruit 5665 takes 3.3 V or 5 V on VIN. Use the **3.3 V** from the XIAO's 3V3 pin. You
can use a Qwiic or STEMMA QT cable between the two breakouts instead of the four wires. Keep
the DPS310's leads to the XIAO short (10 to 20 cm). The SHT45 lead is longer on purpose: see
"Where to put the SHT45" below.

### XIAO ESP32C3 (all three files)

The C3 files expect a **STEMMA QT (JST SH 4-pin) connector soldered to the XIAO's D4 and D5
pads**. The cable colours are the standard ones:

| XIAO pad | GPIO | STEMMA QT pin | Wire colour |
|---|---|---|---|
| **3V3** | n/a | 3V3 (V+) | Red |
| **GND** | n/a | GND | Black |
| **D4** (SDA) | GPIO6 | SDA | Blue |
| **D5** (SCL) | GPIO7 | SCL | Yellow |

Check the 3V3 pad of your soldered connector with a meter before you connect a sensor. For
the DPS310 with SHT45, chain the second breakout from the first with a second STEMMA QT
cable. Both sit on the same bus. For the TMP117, keep the cable short (well under 1 m):
I²C is meant for short runs.

### HUZZAH and MPL3115A2 or BME280 / BMP280

Solder the headers on, then join the boards with four wires. The wiring is the same for both
sensors:

| HUZZAH pin | Sensor pin | Wire colour |
|---|---|---|
| **3V** | **VIN** (MPL3115A2) or **VIN / VCC** (BME280 / BMP280) | Red |
| **GND** | **GND** | Black |
| **SDA** | **SDA** (MPL3115A2) or **SDA / SDI** (BME/BMP280) | Blue |
| **SCL** | **SCL** (MPL3115A2) or **SCL / SCK** (BME/BMP280) | Yellow |

- Power the sensor from the HUZZAH's **3V** pin.
- Leave the MPL3115A2's **INT1** and **INT2** pins empty. They are not used.
- On a bare BME/BMP280 module, **CS / CSB goes to 3V** to select I²C mode.

**What you should see:** nothing yet. These nodes have no screen. Only a small LED on the
board shows the state (see the [hub](README.md#the-status-light)). The XIAO ESP32C3 has no
status light at all.

---

## Name and address settings (hub Steps 3 and 4)

Use the file for your board from the table above. In the hub's download step you already
have all of them.

The files start with these names. Change `node_name` and `friendly_name` so every node is
different:

| File | Starting `node_name` |
|---|---|
| `stagewatch-feather-s3-dps310.yaml` | `stagewatch-baro-s3-1` |
| `stagewatch-xiao-esp32c6-dps310.yaml` | `stagewatch-baro-c6-1` |
| `stagewatch-feather-s3-dps310-sht45.yaml` | `stagewatch-env-s3-1` |
| `stagewatch-xiao-esp32c6-dps310-sht45.yaml` | `stagewatch-env-c6-1` |
| `stagewatch-feather-esp8266-mpl3115a2.yaml` | `stagewatch-baro-esp8266-1` |
| `stagewatch-feather-esp8266-bme280.yaml` | `stagewatch-env-esp8266-1` |
| `stagewatch-xiao-esp32c3-dps310-sht45.yaml` | `stagewatch-env-c3-1` |
| `stagewatch-xiao-esp32c3-bme280.yaml` | `stagewatch-env-c3-2` |
| `stagewatch-xiao-esp32c3-tmp117.yaml` | `stagewatch-rack-c3-1` |

### The sensor's I²C address

The five DPS310 files (every file with a DPS310 in its name) have a line `dps310_address`.
Leave it at `"0x77"` for a normal Adafruit 4494. If you bridge the address jumper on the
breakout (or tie its SDO pin low), the sensor answers at `0x76` and you must change the line
to `"0x76"`. Do this only if you changed the breakout.

The C3 BME280 file has `bme280_address` (`"0x77"` by default, `"0x76"` on the alternate
address). The C3 TMP117 file has `tmp117_address` (`"0x48"` by default). The log scan
(hub Step 6) tells you which one your breakout uses.

---

## Flashing notes (hub Step 5)

Use the hub's flashing mode table for your board:

- **S3 Feather:** the BOOT, RESET, BOOT trick.
- **XIAO (C3 or C6):** hold BOOT while plugging in, or hold BOOT and tap RESET.
- **HUZZAH:** usually nothing; the GPIO0 and RESET trick if it is not found.

On the command line, use your node's file name in `esphome run`. For example, for the XIAO
ESP32C3 with a TMP117:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-xiao-esp32c3-tmp117.yaml
```

Then **tap RESET** once when the upload finishes, if the board does not restart by itself.

---

## Check the boot log (hub Step 6)

Use the address table in the hub. For these builds, the extra things to know are:

- On an all-in-one node, check the **Temperature** is close to a thermometer you trust. If it
  reads several degrees warm, the SHT45 is too close to the board (see "Where to put the
  SHT45").
- On the S3 Feather, you also see `0x36` (battery gauge, boards from early 2023) or `0x0B`
  (older boards).

### Which chip is it? (HUZZAH BME280 file only)

A BME280 and a BMP280 look the same. Read the log:

- The scan shows the address, `0x76` or `0x77`. The file starts with `0x76`. Most Adafruit
  boards are `0x77`, and cheap blue or purple modules are usually `0x76`. If yours is
  `0x77`, change the `address:` line in the file and flash again.
- The sensor's setup lines show a **chip id**: `0x60` is a **BME280** (has humidity). `0x58`
  is a **BMP280** (no humidity). Some older BMP280s read `0x56` or `0x57`.
- If the id is `0x58`, or there is no **Humidity** reading, you have a BMP280. Ask your
  installer to do the swap marked in the file: delete the BME280 block and remove the `#`
  from the BMP280 block below it.

---

## Adding it in Stagewatch (hub Step 7)

Follow the hub. Two things to remember for these builds:

- For the **TMP117 rack node**, set **Role** to **Equipment** on the adopt form, before you
  click **Adopt**. If you forget, change it under **Adopted** and click **Save**. Until you
  do, its temperature counts in the site average as if it were air.
- With more than one pressure node adopted, Stagewatch shows the average of them.

---

## Using it on a gig

### Where to put the SHT45 (all-in-one nodes)

The board, its regulator and any battery warm the air around them. Warm air also reads
**drier**, so heat spoils both temperature and humidity.

- Put the SHT45 on a **short lead, 5 to 10 cm away** from the board and its regulator.
- Let it hang in **free, shaded air**, below or beside the board, not above it.
- Keep it **out of direct sun** and away from lamps and amp heat.
- **Outdoors**, put it in a **small radiation shield**. Do not seal it in a box: a sealed box
  gives wrong readings.
- Keep the DPS310 out of fan and blower airflow, as in the [hub](README.md#step-2-decide-where-it-will-live).

The SHT45's heater is switched off in the files, because it would raise the temperature
reading.

### Where to put the TMP117 (rack node)

Put the TMP117 where the question is: on the rack exhaust, a heatsink or the warm side of the
rack, on its own short lead. Keep the XIAO itself in cooler air, away from the heat it is
measuring, because the board's own warmth is not the rack's. Tape or clip the sensor so it
touches or faces what you want to measure. Do not let it lie on live terminals or mains
parts. This is a low-voltage board: nothing mains-connected belongs on it, and any work
inside a powered rack is for a qualified person. A metal rack blocks Wi-Fi (see the antenna
section). Alarm and warning levels for this node are set in Stagewatch, not in the file. They
are advisory only. This node never controls anything.

### Built-in or external antenna? (XIAO boards only)

**XIAO ESP32C6.** The C6 has a small **built-in antenna** and a tiny socket for an
**external antenna** (a U.FL, also called IPEX, socket). **Every C6 file uses the built-in
antenna. That is the default and it is fine for most shows.** Only think about the external
antenna if:

- Stagewatch shows the node's **Signal** as **weak** (about -80 dBm or worse) and moving the
  node closer to an access point is not an option;
- the node has to sit **inside a metal case or rack**, which blocks the built-in antenna;
- you want a **bigger or more directional antenna**.

**What you need:** a 2.4 GHz Wi-Fi antenna with a **U.FL (IPEX) plug**. Fit it to the XIAO's
socket **before** you switch the option on. The plug is tiny and clicks on with a firm press
straight down. Do not pull the antenna by its lead.

**How to switch it on:**
1. Open your node file (`stagewatch-xiao-esp32c6-dps310.yaml` or the `-sht45` one) and go to
   the very end. The last block is headed **External antenna**.
2. Take the `# ` off the start of every line of that block, so the lines start with `switch:`
   and the spaces after it stay as they are.
3. Flash the node over **USB** the first time you do this (hub Step 5).
4. Watch the **Signal** column in Stagewatch for a few minutes. It should improve.

**To go back to the built-in antenna:** put the `# ` back on every line of the block and
flash again over USB.

**Be careful:**
- If you switch it on **without an antenna fitted**, the node loses its Wi-Fi. It cannot be
  updated over Wi-Fi either, so you will need a USB cable to flash it back.
- **This option has not been tested on a real board yet.** It follows Seeed's own
  instructions for the board (the antenna is chosen by pin `GPIO14`, after `GPIO3` is held
  low). Tell us what you find.
- Only fit antennas made for 2.4 GHz Wi-Fi.

**XIAO ESP32C3.** The C3 is different: **its Wi-Fi is weak or absent unless the small
2.4 GHz antenna is fitted** to its U.FL (IPEX) socket. Seeed says the board comes with one.
If yours did not, you will need to buy a 2.4 GHz Wi-Fi antenna with a **U.FL (IPEX / MHF1)**
plug. Fit it **before** you power the board. Press the plug straight down. Never pull on the
lead. The C3 has no antenna-select switch, so there is nothing to enable in the file.

If the **Signal** reads about -80 dBm or worse, or the node keeps dropping, move it closer to
an access point and clear of metal racks and cases, or fit a larger antenna. A metal amp rack
blocks Wi-Fi: for the TMP117 node, route an external antenna outside the rack, or use a wired
node. **The C3 files have not been tested on hardware**, so tell us what you find.

---

## If it didn't work

The shared problems (no COM port, node not discovered, key errors, hardware faults) are in
the [hub table](README.md#if-it-didnt-work). These are specific to the pressure builds.

| What you see | Likely cause | What to do |
|---|---|---|
| **S3 Feather only:** the log says `Recovery failed: SCL is held LOW on the bus`, the bus scan finds no devices, and starting takes about 11 seconds longer | **Cause not known.** It happened once with a DPS310 plugged into the STEMMA QT port, then went away with nothing changed. The Feather's own battery gauge read fine with nothing plugged in, and the same sensor works on the XIAO. A possible cause (not confirmed): a poor contact in the cable or socket, or the port's 3.3 V coming on after the bus starts. | Unplug the USB lead for ten seconds and start again. Push the STEMMA QT cable in firmly at both ends, or try another cable. If it keeps happening, wire the sensor to the Feather's always-on **3V** pin and the **SDA** and **SCL** pads with jumpers, as on the XIAO, and tell us what you find. |
| **HUZZAH:** the log says the bus was recovered, then `Found no devices` and `Communication failed`, and the board is on Wi-Fi and connected to Stagewatch | The sensor is not answering although the bus is healthy. Most often the **SDA and SCL wires are on the wrong pins**, or the sensor has no power. | Check against the wiring table: SDA goes to the pin marked **SDA** (GPIO4) and SCL to **SCL** (GPIO5), and the sensor's VIN goes to **3V**. Swap SDA and SCL if unsure, then reboot. |
| **XIAO:** the **Signal** is weak (about -80 dBm or worse) and readings keep dropping | The antenna is blocked, missing (C3), or the node is far from an access point. | Move the node closer to an access point and clear of metal. On a C3, check the antenna is fitted. If you still cannot, see "Built-in or external antenna?" above. |
| **XIAO ESP32C3:** no Wi-Fi at all | The 2.4 GHz antenna is not fitted. | Fit it, with the board unpowered, then try again. |
| The log has no I²C address for the sensor | A wire or cable is loose, a wire is on the wrong pin, or the sensor has no power. | Push the STEMMA QT cable in at both ends, or check the four wires against the table in "Plug it together". Try another cable. |
| BME280 file: the log shows a chip-id error, and there is no **Humidity** | You have a BMP280, not a BME280. | Do the BMP280 swap marked in the file. See "Which chip is it?". |
| MPL3115A2: the first reading is missing, or readings seem one step behind | A known quirk of the driver. | Harmless at a 2-second update. Wait a few seconds. |
| All-in-one node: the log lists the DPS310 address but not `0x44`, and there is no **Temperature** or **Humidity** | The SHT45 is not connected or has no power. | Check the second STEMMA QT cable or the four SHT45 wires. Check the DPS310 and SHT45 pins match by name. |
| All-in-one node: **Temperature** reads several degrees warm, or **Humidity** reads low | The SHT45 is heated by the board, the sun or a lamp. | Move it 5 to 10 cm or more from the board, into shade. See "Where to put the SHT45". |
| TMP117 node: the site temperature moved when it was adopted | The node was adopted as **Environment**. | In Admin, under **Adopted**, set its **Role** to **Equipment** and click **Save**. |

More help: [Troubleshooting](../troubleshooting.md#sensor-node-problems).

---

## Advanced

For installers. Crew can skip this.

### Pins

| Board | I²C | Other |
|---|---|---|
| ESP32-S3 Feather (5477), with or without SHT45 | SDA GPIO3, SCL GPIO4, 100 kHz | `I2C_POWER` GPIO7 switches the STEMMA QT port's 3.3 V and is driven on at boot. NeoPixel data GPIO33, its power enable GPIO21. Boot button GPIO0. |
| XIAO ESP32C6 (with or without SHT45) | SDA GPIO22 (D4), SCL GPIO23 (D5), 100 kHz | User LED GPIO15, active low. |
| XIAO ESP32C3 (all three files) | SDA GPIO6 (D4), SCL GPIO7 (D5) | No user LED. ESPHome's own default I²C pins for the C3 (GPIO8 and GPIO9) are not the XIAO's D4 and D5, so the files set the pins explicitly. |
| HUZZAH ESP8266 (both files) | SDA GPIO4, SCL GPIO5, 100 kHz | Blue LED GPIO2, active low. |

### Settings worth knowing

- Pressure is reported in hPa. Stagewatch converts it to pascals.
- The SHT45 reads every 5 seconds at high precision, with its heater off
  (`heater_max_duty: 0.0`). Its address is `0x44`. The all-in-one files share the bus with
  the DPS310.
- The DPS310 and MPL3115A2 files average the last 5 readings (10 seconds) before sending. The
  DPS310 and MPL3115A2 have no oversampling setting in ESPHome. The BME280 file reads every 5
  seconds with 16x oversampling.
- The TMP117 file reads every 5 seconds and shows the reading as **Rack temperature**.
- The DPS310 chip temperature is a commented-out block named "Pressure sensor die
  temperature". Leave it off unless you are diagnosing heating, and untick **Average** if you
  turn it on.
- The S3 Feather file has a commented-out block for the older LC709203F battery gauge
  (`0x0B`). The XIAO and HUZZAH files have no battery gauge.
- The S3 Feather has PSRAM fitted, but the file does not enable it. It is not needed.
- The C3 files each report **Uptime** (a diagnostic number) as well as the sensor readings.
- The C3 files have no controllable outputs: they contain only sensors.

---

**Next:** [Dashboard cards](../cards/README.md).
