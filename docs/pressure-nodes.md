# Build a pressure node (barometer)

A **pressure node** is a small Wi-Fi box that measures **air pressure** and sends it to
Stagewatch. Stagewatch uses pressure with temperature and humidity to work out the **speed of
sound**, and shows it on the dashboards.

You can build several and let Stagewatch **average them** (the site average), the same way it
does for temperature. Several nodes in different places also let you spot one that is wrong.

Time needed: about 30 to 45 minutes for the first node. Most of that is waiting for the first
build.

> **Test status.** All six node files have been checked by ESPHome (it reads them without
> errors). The **XIAO ESP32C6 with a DPS310** (pressure only) has run on a real board and been
> adopted in Stagewatch: the sensor answered at `0x76` (address jumper bridged) and the status
> light stayed off while everything was well.
> The **S3 Feather with a DPS310 on the STEMMA QT port** did not work on its first test: the
> I²C bus was held low and nothing answered (see "If it didn't work" below). The **HUZZAH with
> an MPL3115A2** has run on a real board and been adopted in Stagewatch. The BME/BMP280 HUZZAH
> file has not been tested yet. Treat the steps for any board not listed as tested as unproven,
> and tell us what you find.
>
> **The two all-in-one files (DPS310 plus SHT45) are tested on hardware: pending.** That means
> the SHT45 additions have not run on a real board yet. Check their readings against a
> thermometer and a barometer you trust before you rely on them.

> Stagewatch is an advisory tool with no warranty. Check your node's readings against a
> reference before you trust it. See the [main README](../README.md#stagewatch).

New to nodes? Read the [first sensor node guide](first-sensor-node.md) first. This guide links
back to it for the steps that are the same.

---

## Pressure only, or all-in-one?

There are two build options for the DPS310 nodes.

- **Option 1: pressure only.** A DPS310 and nothing else. Smallest and simplest. Use it when the
  site already has good air temperature and humidity sensors.
- **Option 2: all-in-one environment node.** The same DPS310 plus a **Sensirion SHT45**. One
  box then reports **pressure, air temperature and humidity**, and all three feed the site
  average.

**Why add the SHT45?** The speed of sound depends mostly on the **air temperature**, and only a
little on pressure and humidity. The DPS310 does report a temperature, but that is the
temperature of the **chip**, not of the air. It reads warm, so it is switched off in the files.
The SHT45 measures the real air temperature (about 0.1 °C) and humidity (about 1 %RH). Choose
option 2 if you want the speed of sound to come from the same box.

## Which board do I build?

| Board | Sensor | Soldering | File |
|---|---|---|---|
| Adafruit ESP32-S3 Feather, 4 MB flash and 2 MB PSRAM (product 5477) | Adafruit DPS310 (product 4494), on a STEMMA QT cable | None | `stagewatch-feather-s3-dps310.yaml` |
| Seeed XIAO ESP32C6 | Adafruit DPS310 (4494) on four jumper wires | Header pins on the XIAO | `stagewatch-xiao-esp32c6-dps310.yaml` |
| Adafruit ESP32-S3 Feather (5477) | DPS310 (4494) **and** Adafruit SHT45 (product 5665), both on STEMMA QT cables | None | `stagewatch-feather-s3-dps310-sht45.yaml` |
| Seeed XIAO ESP32C6 | DPS310 (4494) **and** SHT45 (5665), sharing the same four jumper wires | Header pins on the XIAO | `stagewatch-xiao-esp32c6-dps310-sht45.yaml` |
| Adafruit Feather HUZZAH ESP8266 (product 2821) | Adafruit MPL3115A2 (product 1893) | Headers on both | `stagewatch-feather-esp8266-mpl3115a2.yaml` |
| Adafruit Feather HUZZAH ESP8266 (2821) | BME280 or BMP280 breakout | Headers on the HUZZAH | `stagewatch-feather-esp8266-bme280.yaml` |

How they differ:

- **DPS310** is the best of these for pressure: about 1 hPa absolute, and it notices changes of
  about 0.06 hPa. Build these first.
- **MPL3115A2** is much less accurate (about 4 hPa). Use it to **cross-check** trends and to catch
  a big error, not for the exact pressure. It is a second type of sensor, for comparison.
- **BME280** also measures **temperature and humidity**, so this one is a full environment node
  as well as a barometer. A **BMP280** looks identical but has **no humidity**. The file covers
  both (see "Which chip is it?" below).
- The two ESP8266 boards have less memory and weaker Wi-Fi than the ESP32 boards. Do not make one
  the **only** pressure source on a critical site.

**The DPS310 and MPL3115A2 also report a chip temperature.** That is the temperature of the
chip, not of the air, and it reads warm. It is **switched off** in these files on purpose,
because Stagewatch would average it into the site temperature and pull it up. Leave it off. If an
installer turns it on to look for heating, untick **Average** for it in Admin (see "Checking and
correcting the readings").

Words used here: **flashing** is copying a program onto the node. **Firmware** is that program.
**ESPHome** is the free tool that builds it. See the [glossary](glossary.md).

---

## What you need

For every board:

| Item | Notes |
|---|---|
| **A USB data cable** | USB-C for the S3 Feather and the XIAO, micro-B for the HUZZAH. It must carry **data**, not just power. Many phone-charging cables are charge-only. |
| **A stable 5 V USB supply** | Best for show use. |
| **A computer** to flash it | Windows is described here. |
| **Wi-Fi at 2.4 GHz** | None of these boards uses 5 GHz. |
| *Optional (S3 Feather and HUZZAH only):* **a protected LiPo battery with a JST-PH plug** | A short ride-through if the power drops. **Check the polarity** first: many packs are wired the other way round and will damage the board. The XIAO file has no battery support. |

Then the parts for your board, from the table above. For the S3 Feather, also a **STEMMA QT
cable, 100 mm or longer**. For the XIAO, **four female-to-female jumper wires, 10 to 20 cm
long**, in red, black, blue and yellow if you can get them.

**Extra parts for the all-in-one option (DPS310 plus SHT45):**

| Board | Extra parts |
|---|---|
| S3 Feather | The **Adafruit SHT45 breakout (product 5665)** and a **second STEMMA QT cable, 100 to 200 mm**, to go from the DPS310 to the SHT45. The first cable (Feather to DPS310) is 100 mm or longer. |
| XIAO ESP32C6 | The **Adafruit SHT45 breakout (5665)** and **four more jumper wires** (or one Qwiic / STEMMA QT cable) to go from the DPS310's pins to the SHT45's pins. |
| Outdoors, either board | A small **radiation shield** (a louvred, Stevenson-type screen) for the SHT45. |

> **LiPo warning.** Never leave a charging pack in a hot flight case or under lamps.

---

## Step 1. Plug it together

### S3 Feather and DPS310 (no soldering)

1. Plug one end of the STEMMA QT cable into the **STEMMA QT port** on the Feather (the small white
   socket).
2. Plug the other end into the DPS310.

The plugs only fit one way round. Do not force them.

### XIAO ESP32C6 and DPS310 (four jumper wires)

Solder the header pins onto the XIAO first if they are not fitted. Then join the two boards with
four wires:

| XIAO pin | DPS310 pin | Wire colour |
|---|---|---|
| **3V3** | **VIN** | Red |
| **GND** | **GND** | Black |
| **D4** | **SDA** | Blue |
| **D5** | **SCL** | Yellow |

Check your DPS310 breakout takes **3.3 V** on VIN before you connect it. The Adafruit 4494 does.
Jumper wires are for bench use and testing. For a permanent node, solder the leads or use a
Qwiic or STEMMA QT cable, and put the pair in a vented housing with the USB lead held so it
cannot be pulled out.

### Adding the SHT45 (all-in-one option)

Both sensors share one **I²C bus** (the two data wires, SDA and SCL). They do not clash,
because each has its own address: the DPS310 answers at `0x77` (or `0x76` if you bridged its
address jumper) and the SHT45 at `0x44`, which cannot be changed. Every wire is simply passed
on from one board to the next.

**S3 Feather (no soldering).**

1. Plug the first STEMMA QT cable into the Feather's **STEMMA QT port** and the DPS310, as above.
2. Plug the second STEMMA QT cable (100 to 200 mm) into the DPS310's **second STEMMA QT socket**.
3. Plug the other end of that cable into the SHT45.

**XIAO ESP32C6.** Wire the DPS310 to the XIAO with the four wires in the table above. Then join
the SHT45 to the **DPS310's pins**, one wire each, matching names:

| DPS310 pin | SHT45 pin | Wire colour |
|---|---|---|
| **VIN** | **VIN** (use 3V3) | Red |
| **GND** | **GND** | Black |
| **SDA** | **SDA** | Blue |
| **SCL** | **SCL** | Yellow |

The Adafruit 5665 takes 3.3 V or 5 V on VIN. Use the **3.3 V** from the XIAO's 3V3 pin. You can
use a Qwiic or STEMMA QT cable between the two breakouts instead of the four wires. Keep the
DPS310's leads to the XIAO short (10 to 20 cm). The SHT45 lead is longer on purpose: see "Where
to put the SHT45" below.

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

**What you should see:** nothing yet. These nodes have no screen. Only a small LED on the board
shows the state (see "The LED" below).

---

## Step 2. Get your secrets ready

A **secrets file** holds your Wi-Fi password and the keys that lock your node. You make one
copy, called `secrets.yaml`, and fill it in.

First, get the node files. Open a normal **PowerShell** window (Start, type `powershell`,
Enter), paste this and press Enter. It downloads all six node files, so you can build any of
them:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $d = "$HOME\Documents\stagewatch-node"; New-Item -ItemType Directory -Force $d | Out-Null; foreach ($f in "stagewatch-feather-s3-dps310.yaml","stagewatch-xiao-esp32c6-dps310.yaml","stagewatch-feather-s3-dps310-sht45.yaml","stagewatch-xiao-esp32c6-dps310-sht45.yaml","stagewatch-feather-esp8266-mpl3115a2.yaml","stagewatch-feather-esp8266-bme280.yaml","secrets.example.yaml") { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/MrFreekie/stagewatch/main/esphome/$f" -OutFile "$d\$f" }; if (-not (Test-Path "$d\secrets.yaml")) { Copy-Item "$d\secrets.example.yaml" "$d\secrets.yaml" }; explorer $d
```

**What you should see:** a folder window opens with the six node files, `secrets.example.yaml`
(an example, leave it alone) and `secrets.yaml` (**your** copy). If you already have a
`secrets.yaml` from another node, the command leaves it as it is.

Right-click `secrets.yaml`, choose **Open with** > **Notepad**. Change the part **between the
quote marks** on each line. Keep the quote marks.

| Line | What to put |
|---|---|
| `wifi_ssid` | The exact name of the Wi-Fi network the Stagewatch computer is on. Capital letters matter. It must be 2.4 GHz. |
| `wifi_password` | That network's password. |
| `ota_password` | Make one up. It protects future **wireless** updates of the node. |
| `api_encryption_key` | A secret key that locks the link between the node and Stagewatch. It must be a **valid key** made as described in [Make the key](feather-s3-tft-node.md#make-the-key). Make a **new one for every node**. |
| `wifi_ap_password` | A password for the node's own back-up Wi-Fi network, used only if it cannot join yours. At least 8 characters. If your old `secrets.yaml` does not have this line, add it. |

**Keep a copy of each key.** You paste it into Stagewatch when you adopt the node, and
Stagewatch never shows it again.

> **Never share `secrets.yaml` or put it online.** It holds your Wi-Fi password and your keys.
> Do not email it, post it or upload it to GitHub.

### Give every board its own name

Two nodes with the same name will fight. Open the node file in Notepad. Near the top, under
`substitutions:`, change these two lines. Keep the quote marks and the spaces at the start of
each line.

| Line | What it does |
|---|---|
| `node_name` | The node's network name, no spaces. The files start with `stagewatch-baro-s3-1`, `stagewatch-baro-c6-1`, `stagewatch-env-s3-1` (S3 Feather with SHT45), `stagewatch-env-c6-1` (XIAO with SHT45), `stagewatch-baro-esp8266-1` and `stagewatch-env-esp8266-1`. |
| `friendly_name` | The longer name shown in tools. |

**Building a second board of the same type?** Make a copy of the file, give it a new name such
as `stagewatch-xiao-esp32c6-dps310-2.yaml`, and change `node_name` to `stagewatch-baro-c6-2` and
`friendly_name` to "Stagewatch Baro C6 2". It can use the same `api_encryption_key` as your other
nodes: nothing else needs changing in the copy.

### The sensor's I²C address (DPS310 files)

The DPS310 files (all four) have a third line, `dps310_address`. Leave it at `"0x77"` for a normal Adafruit
4494. If you bridge the address jumper on the breakout (or tie its SDO pin low), the sensor
answers at `0x76` and you must change the line to `"0x76"`. Do this only if you changed the
breakout.

---

## Step 3. Flash it over USB

The **first flash must be over USB**. Later updates can go over Wi-Fi.

**The first build takes several minutes** (5 to 15 is normal) and needs **internet**, because
ESPHome downloads its tools. Do not unplug anything while it works.

### Install ESPHome and run it

The easy route is **ESPHome Device Builder** (no commands). Follow
[Option A in the first sensor node guide](first-sensor-node.md#option-a-esphome-device-builder-no-command-line),
then import your node file from `Documents\stagewatch-node`, click **Install** and choose the
option that installs **over USB from this computer**.

If you prefer to paste commands, paste this in PowerShell to install ESPHome (you already have
`uv` if Stagewatch is installed on this PC):

```powershell
uv tool install esphome
```

Close PowerShell and open a new one. Then paste the line for **your** board. It builds and flashes:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-feather-s3-dps310.yaml
```

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-xiao-esp32c6-dps310.yaml
```

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-feather-s3-dps310-sht45.yaml
```

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-xiao-esp32c6-dps310-sht45.yaml
```

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-feather-esp8266-mpl3115a2.yaml
```

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-feather-esp8266-bme280.yaml
```

When it asks how to upload, choose the **COM port** (a name like `COM3`), not "Over the air".

### Getting each board ready to accept the program

**S3 Feather.** If the computer does not find the board:

1. **Hold the BOOT button.**
2. **Tap the RESET button.**
3. **Let go of BOOT.**

Then run the command above and choose the COM port. When it finishes, **tap RESET** once.

> Flashing this way **replaces Adafruit's built-in "UF2" loader**. That is fine for Stagewatch. If
> you ever want it back, use Adafruit's web installer.

**XIAO ESP32C6.** Either:

- **Hold BOOT**, plug in the USB cable, then let go of BOOT; or
- **Hold BOOT**, tap **RESET**, then let go of BOOT.

Then run the command above.

**HUZZAH (both ESP8266 files).** Try **without touching any button** first. The board's USB chip
usually puts it into flashing mode by itself. If it is not found:

1. **Hold the GPIO0 button.**
2. **Press and let go of RESET.**
3. **Let go of GPIO0.**

Then run the command above.

> The ESPHome web installer at <https://web.esphome.io> can only flash a ready-made program, not
> the Stagewatch files. It is handy for checking that your cable and board talk to the computer.

**What you should see:** build messages scroll past, then an upload with a percentage. At the end
it says it succeeded and starts showing the node's log.

---

## Step 4. Check the boot log

Right after the upload, ESPHome shows the node's **log** (the messages it prints as it starts).
You can also see it later by pasting this in PowerShell with your board's file name:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome logs stagewatch-xiao-esp32c6-dps310.yaml
```

Near the start the node **scans the I²C bus** (the wires to the sensor) and lists each device it
finds. Look for the sensor's address:

| Node | You should see |
|---|---|
| DPS310 (S3 Feather and XIAO, with or without the SHT45) | `0x77` (or `0x76` if you changed the address jumper) |
| SHT45 (all-in-one files) | `0x44` as well as the DPS310 address. You should see **both**. |
| MPL3115A2 | `0x60` (it cannot be changed) |
| BME280 or BMP280 | `0x76` or `0x77` |
| S3 Feather also | `0x36` (battery gauge, boards from early 2023) or `0x0B` (older boards) |

Then you should see readings for **Pressure** and **WiFi signal** (and, on the BME280 file
and the two all-in-one files, **Temperature** and **Humidity**).

On an all-in-one node, check the **Temperature** is close to a thermometer you trust. If it
reads several degrees warm, the SHT45 is too close to the board (see "Where to put the SHT45").

**If no address is listed**, the sensor is not talking to the board. Check the wires or cable
(see "If it didn't work").

### Which chip is it? (BME280 file only)

A BME280 and a BMP280 look the same. Read the log:

- The scan shows the address, `0x76` or `0x77`. The file starts with `0x76`. Most Adafruit boards
  are `0x77`, and cheap blue or purple modules are usually `0x76`. If yours is `0x77`, change
  the `address:` line in the file and flash again.
- The sensor's setup lines show a **chip id**: `0x60` is a **BME280** (has humidity). `0x58` is
  a **BMP280** (no humidity). Some older BMP280s read `0x56` or `0x57`.
- If the id is `0x58`, or there is no **Humidity** reading, you have a BMP280. Ask your installer
  to do the swap marked in the file: delete the BME280 block and remove the `#` from the
  BMP280 block below it.

---

## Step 5. Add the node in Stagewatch

1. On any device on the network, open Stagewatch's admin page: the Stagewatch computer's
   address, then `/admin` (for example `http://192.168.1.50:8080/admin`). Log in with your PIN.
2. Find the card **ESPHome nodes**. Under **Discovered node** your new node is listed by its
   name. This can take a minute.
3. Click **Adopt…** next to it. A form opens with the host and name already filled in.
4. In **Encryption key**, paste the key you made for this node.
5. In **Area**, type where the node lives, for example `FOH`, `Stage L` or `Delay tower 1`.
6. Click **Adopt**.

**What you should see:** a message "Node adopted". The node appears under **Adopted** with the
status **ok**. Open a dashboard: the pressure appears on its card, and the speed of sound starts
to update. With more than one pressure node adopted, Stagewatch shows the average of them.

If the node is not in the list, use **Add by host / IP** on the same card. Type the node's
address (or `your-node-name.local`), leave the port at `6053`, and fill in the name, area and
key. You can find the node's address in your router's list of connected devices, or in the node's
log. Then click **Adopt**.

---

## Using it on a gig

### The LED

These nodes have no screen. The small LED tells you the state.

| Board | LED | Meaning |
|---|---|---|
| S3 Feather | **Amber** | Starting up, or lost Wi-Fi or Stagewatch. |
| S3 Feather | **Dim green** | Connected to Stagewatch. All well. |
| XIAO ESP32C6 | **Lit** | Starting up, or lost Wi-Fi or Stagewatch. |
| XIAO ESP32C6 | **Off** | Connected to Stagewatch. All well. |
| HUZZAH (blue LED) | **Lit** | Starting up, or lost Wi-Fi or Stagewatch. |
| HUZZAH (blue LED) | **Off** | Connected to Stagewatch. All well. |

The XIAO file assumes the LED lights when its pin is low. That has not been checked on a real
board. If it is the wrong way round (lit when all is well), ask your installer to remove the
`inverted: true` line under `user_led` in the file.

### The node restarts itself

If the node has had **no Stagewatch connection for 15 minutes**, it restarts by itself. This
clears a stuck Wi-Fi connection. You do not need to do anything. If it keeps restarting, check
the Wi-Fi and that Stagewatch is running.

### Where to put it

A pressure sensor reads the **still air pressure** around it, and it drifts if it gets warm.

| Do | Don't |
|---|---|
| Put the sensor **in free air**, a hand's length or more from the board, on the long cable or a short lead. | Don't let it **lie on or touch the board**. The board, its regulator and any battery make heat. |
| Keep it **out of wind, draughts and fan or blower airflow**. | Don't put it in a **sealed box**. The pressure inside must match the air outside. A vented housing is right. |
| Keep it **out of direct sun** and away from **lamps, amps and dimmers**. | Don't put it by a **haze outlet**. |
| Power it from a **stable 5 V USB supply**, with the USB lead supported so it cannot be pulled out. | Don't rely on a battery for a whole show. Don't leave a charging pack in a hot case. |
| Keep it dry. None of these sensors is waterproof. | Don't expose it to rain. |

### Where to put the SHT45 (all-in-one nodes)

The board, its regulator and any battery warm the air around them. Warm air also reads
**drier**, so heat spoils both temperature and humidity.

- Put the SHT45 on a **short lead, 5 to 10 cm away** from the board and its regulator.
- Let it hang in **free, shaded air**, below or beside the board, not above it.
- Keep it **out of direct sun** and away from lamps and amp heat.
- **Outdoors**, put it in a **small radiation shield**. Do not seal it in a box: a sealed box
  gives wrong readings.
- Keep the DPS310 out of fan and blower airflow, as in the table above.

The SHT45's heater is switched off in the files, because it would raise the temperature reading.

### Checking and correcting the readings (calibration)

Every pressure sensor has its own small error. The DPS310 is about 1 hPa out in absolute terms.
So check it against something you trust, for example the **local airport pressure (QNH)** or a
reference barometer. Pressure is not the same as the airport figure at a hilltop site, so use
a reference at your own height if you can.

1. Leave the node running for 15 minutes.
2. In Admin, open the **Sensors: calibration & averaging** card.
3. If the node reads high or low, type the correction in **Offset**. It is **added** to every
   reading. Pressure offsets are in **pascals**: 1 hPa is 100 Pa. If the node reads 2 hPa too
   high, type `-200`. Click **Save**.
4. To leave a sensor out of the site average, untick **Average**. Do this if you see one
   that disagrees with the others and you do not yet know why.

Use the offset in Admin. Do not also add an offset in the node file.

On an all-in-one node, the **Temperature** and **Humidity** readings have their own **Offset**
and **Average** boxes in the same card. Trim them against a reference thermometer and hygrometer
in the same way.

Stagewatch ties the calibration to the **board itself**, so it stays with the board if its IP
address changes.

The S3 Feather also reports **battery** readings. These are for information only. They may look
odd or be missing if no battery is fitted. That does no harm.

---

## Updating it later

After the first USB flash, you can update the node over Wi-Fi. The node and your computer must
be on the same network. Run the same `esphome run` command as in Step 3 and choose the node's
name (not a COM port) when it asks how to upload. The update takes a few minutes and the node
restarts. If you change `ota_password` in `secrets.yaml`, update over USB once so the node
learns the new one.

---

## How to check it worked

- The boot log lists the sensor's address, and shows **Pressure** readings of around 1,000 hPa
  (the exact figure depends on the weather and your height).
- The LED shows the "all well" state for your board.
- In Admin, **ESPHome nodes** shows the node under **Adopted** with status **ok**.
- A dashboard shows the pressure, and a speed of sound.
- With two or more pressure nodes close together, their readings agree to within a hPa or two.
  If they do not, calibrate against a reference.

---

## If it didn't work

| What you see | Likely cause | What to do |
|---|---|---|
| The computer does not find the board (no COM port) | A charge-only USB cable, or the board is not in flashing mode. | Try another USB **data** cable and another USB port. Then use the button trick for your board in Step 3. |
| **S3 Feather only:** the log says `Recovery failed: SCL is held LOW on the bus`, the bus scan finds no devices, and starting takes about 11 seconds longer | **Not solved yet.** It happened with every DPS310 plugged into the STEMMA QT port, while the Feather's own battery gauge read fine with nothing plugged in, and the same sensor works on the XIAO. A likely cause (not confirmed): the port's 3.3 V is switched on after the bus starts, and an unpowered sensor pulls the bus low. | Unplug the sensor and cable and reboot: if the bus then scans clean, the fault is in the sensor side, not the board. As a workaround, wire the sensor to the Feather's always-on **3V** pin and the **SDA** and **SCL** pads with jumpers, as on the XIAO. Tell us what you find. |
| **HUZZAH:** the log says the bus was recovered, then `Found no devices` and `Communication failed`, and the board is on Wi-Fi and connected to Stagewatch | The sensor is not answering although the bus is healthy. Most often the **SDA and SCL wires are on the wrong pins**, or the sensor has no power. | Check against the wiring table: SDA goes to the pin marked **SDA** (GPIO4) and SCL to **SCL** (GPIO5), and the sensor's VIN goes to **3V**. Swap SDA and SCL if unsure, then reboot. |
| The log has no I²C address for the sensor | A wire or cable is loose, a wire is on the wrong pin, or the sensor has no power. | Push the STEMMA QT cable in at both ends, or check the four wires against the table in Step 1. Try another cable. |
| The log says the address is `0x76` but the file uses `0x77` (or the other way round) | The DPS310 or BME280 answers at a different address from the one in the file. | Change the address in the file to match the scan and flash again. |
| BME280 file: the log shows a chip-id error, and there is no **Humidity** | You have a BMP280, not a BME280. | Do the BMP280 swap marked in the file. See "Which chip is it?". |
| MPL3115A2: the first reading is missing, or readings seem one step behind | A known quirk of the driver. | Harmless at a 2-second update. Wait a few seconds. |
| All-in-one node: the log lists the DPS310 address but not `0x44`, and there is no **Temperature** or **Humidity** | The SHT45 is not connected or has no power. | Check the second STEMMA QT cable or the four SHT45 wires. Check the DPS310 and SHT45 pins match by name. |
| All-in-one node: **Temperature** reads several degrees warm, or **Humidity** reads low | The SHT45 is heated by the board, the sun or a lamp. | Move it 5 to 10 cm or more from the board, into shade. See "Where to put the SHT45". |
| The node is not in Stagewatch's **Discovered** list | It is on a different network, or the network blocks node discovery (mDNS). | Check it is on the same 2.4 GHz network as the Stagewatch computer, not a guest network. Turn off "client isolation" on the router. Then use **Add by host / IP**. |
| Status shows **fault**: "encryption key missing or wrong" | The key in Stagewatch is not the key on the node. | Paste the exact key from `secrets.yaml` that was there when you flashed. If you lost it, put a new key in `secrets.yaml`, flash the node again, then remove and adopt it again. |
| Status shows **fault**: "different hardware at this address" | A different board now answers at that node's address (for example you swapped boards). Stagewatch holds its readings back so one board's calibration is not applied to another. | In Admin, under **Adopted**, click **Remove** next to the node (history is kept), then adopt the new board with **Adopt…**. Set its **Offset** again. |
| Status shows **fault**: "same board as another device" | The same board has been adopted twice. | Remove one of the two. Also check two files do not share the same `node_name`. |
| The site temperature jumped up after adding a node | A chip temperature was switched on in a node file and is being averaged. | In Admin, **Sensors: calibration & averaging**, untick **Average** for that reading. Or switch the chip temperature off in the file again. |

More help: [Troubleshooting](troubleshooting.md#sensor-node-problems) and the
[first sensor node guide](first-sensor-node.md#if-it-doesnt-work).

---

## Advanced

For installers. Crew can skip this.

### Pins

| Board | I²C | Other |
|---|---|---|
| ESP32-S3 Feather (5477), with or without SHT45 | SDA GPIO3, SCL GPIO4, 100 kHz | `I2C_POWER` GPIO7 switches the STEMMA QT port's 3.3 V and is driven on at boot. NeoPixel data GPIO33, its power enable GPIO21. Boot button GPIO0. |
| XIAO ESP32C6 (with or without SHT45) | SDA GPIO22 (D4), SCL GPIO23 (D5), 100 kHz | User LED GPIO15, active low. |
| HUZZAH ESP8266 (both files) | SDA GPIO4, SCL GPIO5, 100 kHz | Blue LED GPIO2, active low. |

### Settings worth knowing

- Pressure is reported in hPa. Stagewatch converts it to pascals.
- The SHT45 reads every 5 seconds at high precision, with its heater off (`heater_max_duty: 0.0`).
  Its address is `0x44`. The two all-in-one files share the bus with the DPS310 at 100 kHz.
- The DPS310 and MPL3115A2 files average the last 5 readings (10 seconds) before sending. The
  DPS310 and MPL3115A2 have no oversampling setting in ESPHome. The BME280 file reads every 5
  seconds with 16x oversampling.
- The DPS310 chip temperature is a commented-out block named "Pressure sensor die temperature".
  Leave it off unless you are diagnosing heating, and untick **Average** if you turn it on.
- The S3 Feather file has a commented-out block for the older LC709203F battery gauge
  (`0x0B`). The XIAO and HUZZAH files have no battery gauge.
- The XIAO uses its ceramic antenna. Switching to an external (U.FL) antenna needs GPIO3 driven
  low, then GPIO14 high. That is not set up in the file.
- The S3 Feather has PSRAM fitted, but the file does not enable it. It is not needed.

### A fixed IP address

In the node file, find the commented-out block under `wifi:` and remove the `#` from the start of
each line. Change the numbers to suit your network. Keep the spaces at the start of each line.

```yaml
  manual_ip:
    static_ip: 192.168.1.50
    gateway: 192.168.1.1
    subnet: 255.255.255.0
```

Pick an address outside your router's automatic range, different for each node. Update the node
(over USB the first time).

### Moving between sites (several Wi-Fi networks)

A node can know more than one network. The steps are the same as for the screen Feather. See
[Moving between sites](feather-s3-tft-node.md#moving-between-sites-several-wi-fi-networks). The
node files carry the same commented-out `networks:` example under `wifi:`.

---

**Next:** [Using Stagewatch on show day](using-stagewatch.md).
