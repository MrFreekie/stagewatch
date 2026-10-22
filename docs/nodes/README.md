# Build a sensor node

A **node** is a small Wi-Fi box that measures the air (or the heat of a rack) and sends the
readings to Stagewatch. This page is the hub. It tells you which node to build, then walks
through the steps that are the same for every node. Each build has its own short page for
the parts, wiring and anything special about that board.

> Stagewatch is an advisory tool with no warranty. Check every node against a reference
> thermometer or barometer you trust before you rely on it. See the
> [notices](../integrations/README.md#notices).

Time needed: about 30 to 45 minutes for the first node. Most of that is waiting for the
first build. Later nodes take about 15 minutes.

Words used here: **flashing** is copying a program onto the node. **Firmware** is that
program. **ESPHome** is the free tool that builds it. See the [glossary](../glossary.md).

---

## Which node do I build?

Pick the row that matches the board you have, or the job you need done.

| I want | Build | Guide |
|---|---|---|
| Temperature, humidity and pressure, no soldering, no screen | Feather S3 with a BME280 (or SHT45) | [Feather S3 environment node](feather-s3-env.md) |
| The same, with a screen you can read from arm's length | Feather S3 TFT with an MS8607 | [Feather S3 TFT node](feather-s3-tft-ms8607.md) |
| A barometer (and optionally air temperature and humidity too) | DPS310 on a Feather S3, XIAO or HUZZAH | [Pressure nodes](pressure-nodes.md) |
| The temperature of an amp rack, kept out of the air readings | XIAO ESP32C3 with a TMP117 | [Pressure nodes](pressure-nodes.md#which-board-do-i-build) |

### All the node files

The project ships **15 node files** in the `esphome` folder. Each file is one complete
node: board, sensor and settings. Use the file that matches your board and sensor.

"Tested on hardware" means it has run on a real board and been adopted in Stagewatch.
"Checked only" means ESPHome reads the file without errors, and nothing more. Treat
anything not marked as tested as unproven, and tell us what you find.

| File | Board | Sensors | Status | Guide |
|---|---|---|---|---|
| `stagewatch-feather-s3.yaml` | Adafruit ESP32-S3 Feather, plain (5323 or 5477) | BME280 or SHT45 | Checked only. A full build and flash was not repeated. | [Feather S3 environment](feather-s3-env.md) |
| `stagewatch-feather-s3-tft.yaml` | Adafruit ESP32-S3 TFT Feather (5483) | BME280 or SHT45 | Checked only. The screen layout is untested on hardware. | [Feather S3 environment](feather-s3-env.md) |
| `stagewatch-s3-tft-ms8607.yaml` | Adafruit ESP32-S3 TFT Feather (5483) | MS8607 (temperature, humidity, pressure) | **Tested on hardware**, and the screen works. The battery readings have not been seen on a real board yet. | [Feather S3 TFT](feather-s3-tft-ms8607.md) |
| `stagewatch-s3-featherwing35-ms8607.yaml` | Adafruit ESP32-S3 Feather (5477, 4MB flash, 2MB PSRAM) with the 3.5" 480x320 TFT FeatherWing (3651) | MS8607 (temperature, humidity, pressure) | **Not tested on hardware yet.** Checked with `esphome config` and compiled. The screen start-up commands are typed in by hand (see the file header). Shows three live values with one-hour graphs. | The file header |
| `stagewatch-feather-s3-dps310.yaml` | Adafruit ESP32-S3 Feather (5477) | DPS310 (pressure) | Ran on a real board after a first try that failed. See [the notes](pressure-nodes.md#test-status). | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-feather-s3-dps310-sht45.yaml` | Adafruit ESP32-S3 Feather (5477) | DPS310 and SHT45 | Checked only. **Tested on hardware: pending.** | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-xiao-esp32c6-dps310.yaml` | Seeed XIAO ESP32C6 | DPS310 | **Tested on hardware** and adopted in Stagewatch. | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-xiao-esp32c6-dps310-sht45.yaml` | Seeed XIAO ESP32C6 | DPS310 and SHT45 | Checked only. **Tested on hardware: pending** for the SHT45 part. | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-feather-esp8266-mpl3115a2.yaml` | Adafruit Feather HUZZAH ESP8266 | MPL3115A2 (pressure) | **Tested on hardware** and adopted in Stagewatch. | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-feather-esp8266-bme280.yaml` | Adafruit Feather HUZZAH ESP8266 | BME280 or BMP280 | **Tested on hardware** and adopted in Stagewatch. | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-xiao-esp32c3-dps310-sht45.yaml` | Seeed XIAO ESP32C3 | DPS310 and SHT45 | **Not yet tested on hardware.** Checked only. | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-xiao-esp32c3-bme280.yaml` | Seeed XIAO ESP32C3 | BME280 | **Not yet tested on hardware.** Checked only. | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-xiao-esp32c3-tmp117.yaml` | Seeed XIAO ESP32C3 | TMP117 (rack temperature, Equipment) | **Not yet tested on hardware.** Checked only. | [Pressure nodes](pressure-nodes.md) |
| `stagewatch-env.yaml` | ESP32 DevKit | BME280 | No hardware test recorded. No step-by-step guide; read the notes at the top of the file. | None |
| `stagewatch-env-sht45.yaml` | ESP32 DevKit | SHT45 (no pressure) | No hardware test recorded. No step-by-step guide. | None |
| `stagewatch-env-poe.yaml` | Olimex ESP32-POE or ESP32-POE-ISO (wired Ethernet, no Wi-Fi) | BME280 | No hardware test recorded. No step-by-step guide. | None |

Also in the folder: `secrets.example.yaml`, the example for your private `secrets.yaml`
(Step 3).

The three XIAO ESP32C3 files each need the small 2.4 GHz antenna fitted before you power
the board. See [Built-in or external antenna?](pressure-nodes.md#built-in-or-external-antenna-xiao-boards-only).

---

## What you need (every node)

| Item | Notes |
|---|---|
| **A USB cable that carries data** | Not a charge-only cable. If the computer never sees the board, try another cable first. USB-C for the Feather S3 and XIAO, micro-B for the HUZZAH. |
| **A stable 5 V USB supply** | Best for show use. |
| **A computer** to flash it | Windows is described here. |
| **Wi-Fi at 2.4 GHz** | None of these boards uses 5 GHz. |
| *Optional:* **a protected LiPo battery with a JST-PH plug** | Only on boards that have a battery socket (the Feather S3 and HUZZAH, not the XIAO). A short ride-through if the power drops. |

> **LiPo warning.** The battery socket has a **+** and a **-** marked on the board. Many
> packs are wired the other way round and will damage the board. Check the pack's plug
> against the markings. Use a protected pack only. Never leave a charging pack in a hot
> flight case or under lamps.

The parts for your board, and the cable length, are on its own guide page.

---

## Step 1. Plug it together

Follow the wiring on your build's page. The STEMMA QT plugs only fit one way round. Do not
force them. There is nothing to see yet: the boards do nothing until they have a program.

---

## Step 2. Decide where it will live

A board makes heat. A sensor close to it, or lying on it, reads too warm (and warm air
reads drier, so humidity reads low too). Pressure sensors drift when they warm up.

| Do | Don't |
|---|---|
| Hang the sensor **on its cable, in free air**, a hand's length or more from the board. | Don't let it **lie on or touch the board**. The board, its regulator, a screen backlight and any battery all make heat. |
| Put it where the air matches the air the sound travels through, for example near the delay tower or the side of the stage. | Don't put it near **amps, lamps or dimmers**, or by a **haze outlet** (it pushes humidity up). |
| Keep it **out of direct sun**. Outdoors, use a radiation shield (a louvred cover). | Don't seal it in a box. A pressure sensor must see the same pressure as the outside air, so use a vented housing. |
| Keep it **out of wind, draughts and fan or blower airflow**. | Don't expose it to rain. None of these sensors is waterproof. |
| Power it from a **stable 5 V USB supply**, with the USB lead held so it cannot be pulled out. | Don't rely on a battery for a whole show, and don't leave a charging pack in a hot case. |

A node that measures **equipment** (a rack or amp) goes where the question is: on the
exhaust, the heatsink or the warm side. Keep the board itself in cooler air.

---

## Step 3. Get the node files and fill in your secrets

A **secrets file** holds your Wi-Fi password and the keys that lock your node. You make one
copy, called `secrets.yaml`, and fill it in. Every node on the same network can share the
Wi-Fi lines. Each node should have its own key.

1. Open a normal **PowerShell** window: click Start, type `powershell`, press Enter.
2. Paste this and press Enter. It downloads all 15 node files and the example secrets file
   into a folder called `stagewatch-node` in your Documents:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $d = "$HOME\Documents\stagewatch-node"; New-Item -ItemType Directory -Force $d | Out-Null; foreach ($f in "stagewatch-feather-s3.yaml","stagewatch-feather-s3-tft.yaml","stagewatch-s3-tft-ms8607.yaml","stagewatch-s3-featherwing35-ms8607.yaml","stagewatch-feather-s3-dps310.yaml","stagewatch-feather-s3-dps310-sht45.yaml","stagewatch-xiao-esp32c6-dps310.yaml","stagewatch-xiao-esp32c6-dps310-sht45.yaml","stagewatch-feather-esp8266-mpl3115a2.yaml","stagewatch-feather-esp8266-bme280.yaml","stagewatch-xiao-esp32c3-dps310-sht45.yaml","stagewatch-xiao-esp32c3-bme280.yaml","stagewatch-xiao-esp32c3-tmp117.yaml","stagewatch-env.yaml","stagewatch-env-sht45.yaml","stagewatch-env-poe.yaml","secrets.example.yaml") { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/MrFreekie/stagewatch/main/esphome/$f" -OutFile "$d\$f" }; if (-not (Test-Path "$d\secrets.yaml")) { Copy-Item "$d\secrets.example.yaml" "$d\secrets.yaml" }; explorer $d
```

**What you should see:** a folder window opens with the 15 node files,
`secrets.example.yaml` (an example, leave it alone) and `secrets.yaml` (**your** copy). If
you already have a `secrets.yaml` from another node, the command leaves it as it is.

A **YAML** file is just a text file with settings. Open it with **Notepad**. Lines that
start with `#` are notes to humans and are ignored.

3. Right-click `secrets.yaml`, choose **Open with** > **Notepad**. It looks like this:

```yaml
wifi_ssid: "YOUR_CONTROL_NETWORK"
wifi_password: "CHANGE_ME"
ota_password: "CHANGE_ME"
api_encryption_key: "PASTE_32_BYTE_BASE64_KEY_HERE"
wifi_ap_password: "CHANGE_ME_AP"
```

4. Change the part **between the quote marks** on each line. Keep the quote marks.

| Line | What to put |
|---|---|
| `wifi_ssid` | The exact name of the Wi-Fi network the Stagewatch computer is on. Capital letters matter. It must be a **2.4 GHz** network. |
| `wifi_password` | That network's password. |
| `ota_password` | Make one up. It protects future **wireless** updates of the node. You will rarely need it again. |
| `api_encryption_key` | A secret key that locks the link between the node and Stagewatch. Make one below. |
| `wifi_ap_password` | A password for the node's own back-up Wi-Fi network, used only if it cannot join yours. At least 8 characters. If your old `secrets.yaml` does not have this line, add it. (Ten of the files use it; the five older ones ignore it. It does no harm to have it.) |

### Make the key

5. In PowerShell, paste this and press Enter:

```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
```

**What you should see:** one line of 44 letters and numbers ending in `=`, something like
`Xy3k...=`. Copy it and paste it into `secrets.yaml` between the quote marks of
`api_encryption_key`. Save the file (Ctrl + S).

**Keep a copy of this key.** You paste it into Stagewatch when you adopt the node
(Step 7), and Stagewatch never shows it again. Use **a different key for every node**, and
keep a note of which key belongs to which node.

> **Keep `secrets.yaml` private.** It holds your Wi-Fi password and your keys. Never post it,
> email it or put it on GitHub.

---

## Step 4. Name the node

Two nodes with the same name will fight. Open the node file for your build in Notepad. Near
the top, under `substitutions:`, change these lines. Keep the quote marks and the spaces at
the start of each line.

| Line | What it does |
|---|---|
| `node_name` | The node's network name, no spaces, for example `stagewatch-stage-left`. It becomes `stagewatch-stage-left.local`. |
| `friendly_name` | The longer name shown in tools. |

Some files have a few more lines there (the screen title on the TFT node, the sensor's
address). Your build's page says which.

**Building a second board of the same type?** Make a copy of the file, give it a new name
such as `stagewatch-xiao-esp32c6-dps310-2.yaml`, and change `node_name` and
`friendly_name` in the copy. Put a new `api_encryption_key` in `secrets.yaml` before you
flash it.

---

## Step 5. Flash the node

The **first flash must be over USB**. Every later update can go over Wi-Fi.

Building the program for the first time needs **internet** and takes **5 to 15 minutes**,
because ESPHome downloads its tools. Do not unplug anything while it works.

### Put the board into flashing mode (if the computer does not find it)

| Board | What to do |
|---|---|
| **Feather S3** (plain or TFT) | 1. Plug the board in. 2. **Hold the BOOT button.** 3. **Tap the RESET button.** 4. **Let go of BOOT.** The screen or lights may stay dark. That is normal. |
| **XIAO** (C3 or C6) | Either **hold BOOT**, plug in the USB cable, then let go of BOOT. Or **hold BOOT**, tap **RESET**, then let go of BOOT. |
| **HUZZAH** (ESP8266) | Try **without touching any button** first. The board's USB chip usually puts it into flashing mode by itself. If it is not found: **hold the GPIO0 button**, press and let go of **RESET**, then let go of GPIO0. |

> Flashing a Feather S3 this way **replaces Adafruit's built-in "UF2" loader**. That is fine
> for Stagewatch. If you ever want it back, use Adafruit's web installer.

### Option A. ESPHome Device Builder (no command line)

This is the friendliest way. It is a normal Windows app.

1. Go to <https://esphome.io/guides/installing_esphome/>, choose the **Windows** tab, and
   download the installer (`.exe`).
2. Open it. Click **Yes** if Windows asks. If Windows says **"Windows protected your PC"**,
   click **More info** then **Run anyway**.
3. Start **ESPHome Device Builder** from the Start menu. Your web browser opens with its
   dashboard. The first start sets things up in the background and takes a minute.
4. Click **Secrets** (near the top right). Copy everything from your `secrets.yaml` file and
   paste it there. Save.
5. Click **Create device** (or **+**). Choose **Import from File**. Pick your node file from
   `Documents\stagewatch-node`.
6. Plug the board into the computer.
7. Click **Install** on the new device card. Choose the option that installs **over USB from
   this computer** (its wording changes between versions). Pick the board's port from the list.
8. Wait. You will see build messages and then an upload. When it says it succeeded, **tap
   the RESET button** on the board once.

**What you should see:** "INFO Successfully uploaded program" (or similar). The node starts,
joins Wi-Fi, and appears as **Online** on its card.

The exact button names in the app change from version to version. The official guide is
[Getting Started with ESPHome](https://esphome.io/guides/getting_started_command_line/).

### Option B. Command line

You already have `uv` if you installed Stagewatch on this PC.

1. In PowerShell, paste this and press Enter. It installs ESPHome:

```powershell
uv tool install esphome
```

2. **Close PowerShell and open a new one.** Then paste this, with the file name of your
   build in place of `YOUR-NODE-FILE.yaml` (for example `stagewatch-xiao-esp32c6-dps310.yaml`).
   It builds the node and flashes it:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run YOUR-NODE-FILE.yaml
```

3. When it asks how to upload, choose the **COM port** (a name like `COM3`), not "Over the air".
4. When it finishes, **tap RESET** on the board. ESPHome then shows the node's log. Press
   **Ctrl + C** to stop watching it.

### Why not just use web.esphome.io?

<https://web.esphome.io> can only flash a **ready-made starter** program. It **cannot build
Stagewatch's file** for you. It is handy for checking that your cable and board talk to the
computer, but you still need Option A or B to put the Stagewatch program on.

> **Not verified:** these steps use the official ESPHome tools. The node files check out as
> valid with ESPHome 2026.9.1. A full build and flash was not repeated for every file while
> writing this guide. The test status of each file is in the table at the top of this page.

---

## Step 6. Check the boot log

Right after the upload, ESPHome shows the node's **log** (the messages it prints as it
starts). You can open it again later. In Device Builder, click **Logs** on the node's card.
On the command line, paste this with your file name:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome logs YOUR-NODE-FILE.yaml
```

Near the start the node **scans the I²C bus** (the wires to the sensor) and lists each device
it finds, for example `Found i2c device at address 0x77`. Look for your sensor:

| Sensor | You should see |
|---|---|
| DPS310 | `0x77` (or `0x76` if you bridged the address jumper) |
| SHT45 | `0x44`. On an all-in-one node you should see **both** the DPS310 and `0x44`. |
| BME280 or BMP280 | `0x77`, or `0x76` if its address jumper is cut or the module is a cheap one |
| MS8607 | `0x76` and `0x40` |
| MPL3115A2 | `0x60` (it cannot be changed) |
| TMP117 | `0x48` (or `0x49`, `0x4A`, `0x4B` if its address pin is wired differently) |
| Battery gauge (Feather S3 only) | `0x36` on boards from early 2023, `0x0B` on older boards |

Then you should see readings such as **Pressure**, **Temperature**, **Humidity** and **WiFi
signal**, depending on the build.

If **no address is listed**, the sensor is not talking to the board. Re-seat the cable,
try another cable, or check the wires against your build's page.

Log lines mention your Wi-Fi name. Remove personal details before you post one online.

---

## Step 7. Add the node in Stagewatch

1. On any device on the network, open Stagewatch's admin page: the Stagewatch computer's
   address, then `/admin` (for example `http://192.168.1.50:8080/admin`). Log in with your PIN.
2. Find the card **ESPHome nodes**. Under **Discovered node** your new node should be listed
   by its name. The **Board** column says **encrypted**. This can take a minute.
3. Click **Adopt…** next to it. A form appears with the host and name already filled in.
4. In **Encryption key**, **paste the key** you made in Step 3.
5. In **Area**, type where the node is, for example `Stage L`, `FOH` or `Delay tower 1`.
   Change **Name** if you want.
6. Check **Role**:
   - **Environment** (the default) is for air. Its readings feed the site average, the speed
     of sound and the barometer.
   - **Equipment** is for gear, such as an amp rack. Its readings are shown on the Equipment
     card and **never** feed the site average.
   - **A rack or amp temperature node (the TMP117) must be set to Equipment before you
     adopt it.** Otherwise its temperature would count in the site average, as if it were air.
7. Click **Adopt**.

**What you should see:** a message "Node adopted". The node appears under **Adopted** with
the status **ok**. Its readings show up in the **Sensors** card and on the dashboards.

**If the node is not in the Discovered list**, use **Add by host / IP** on the same card.
Type the node's address (its IP address is on the TFT screen, in your router's list of
devices, or in the node's log) or `your-node-name.local`. Leave the port at `6053`, and fill
in the name, area, role and key. Then click **Adopt**.

Admin also has an **Ignore** button for a node you do not want listed. Ignoring only hides
it. Stagewatch does not connect to it either way.

More about what Stagewatch does with a node is in [ESPHome sensor nodes](../integrations/esphome.md).

---

## Step 8. Check and correct the readings (calibration)

Every sensor has its own small error. Check it against something you trust.

1. Put a **reference thermometer** (for example a Kestrel) next to the node. For a pressure
   node, use the **local airport pressure (QNH)** or a reference barometer instead, at your
   own height if you can. Wait 15 minutes.
2. In Admin, open the **Sensors: calibration & averaging** card.
3. If the node reads high or low, type the correction in **Offset**. It is **added** to every
   reading. If the node reads 0.6 °C too high, type `-0.6`. Click **Save**. Pressure offsets
   are in **pascals**: 1 hPa is 100 Pa, so if a node reads 2 hPa too high, type `-200`.
4. To leave a sensor out of the site average (for example one in direct sun, or one you do
   not trust yet), untick **Average**.
5. If the site has no pressure sensor, open the **Site** card and set the **Altitude (m)** of
   your venue.

Use the offset in Admin. Do not also add an offset in the node file.

Stagewatch ties the calibration to the **board itself**, so it stays with the board if its IP
address changes. The temperature and humidity of an all-in-one node each have their own
**Offset** and **Average** boxes.

Some sensors also report their own chip temperature. That is the temperature of the chip,
not of the air, and it reads warm. The files switch it off on purpose. If an installer turns
it on, untick **Average** for it.

More about the Admin cards is in [Sensors and alarms](../admin/sensors-and-alarms.md).

---

## Using it on a gig

### The status light

| Board | Light | Meaning |
|---|---|---|
| Feather S3 (plain, and the DPS310 files) | **Amber** | Starting up, or lost Wi-Fi or Stagewatch. |
| Feather S3 (plain, and the DPS310 files) | **Dim green** | Connected to Stagewatch. All well. |
| XIAO ESP32C6 | **Lit** | Starting up, or lost Wi-Fi or Stagewatch. |
| XIAO ESP32C6 | **Off** | Connected to Stagewatch. All well. |
| HUZZAH (blue LED) | **Lit** | Starting up, or lost Wi-Fi or Stagewatch. |
| HUZZAH (blue LED) | **Off** | Connected to Stagewatch. All well. |
| XIAO ESP32C3 | No light | These files have no status light. Look at the node list in Admin. |
| Feather S3 TFT | The screen | See [Feather S3 TFT node](feather-s3-tft-ms8607.md) and [Feather S3 environment node](feather-s3-env.md). |

On the XIAO ESP32C6 with a DPS310, tested on hardware, the LED stayed off while all was well.
The XIAO files assume the LED lights when its pin is low. If yours is the wrong way round
(lit when all is well), ask your installer to remove the `inverted: true` line under
`user_led` in the file.

### The node restarts itself

Ten of the node files restart the node if it has had **no Stagewatch connection for 15
minutes**. This clears a stuck Wi-Fi connection. You do not need to do anything. If it keeps
restarting, check the Wi-Fi and that Stagewatch is running. The five older files
(`stagewatch-feather-s3.yaml`, `stagewatch-feather-s3-tft.yaml`, `stagewatch-env.yaml`,
`stagewatch-env-sht45.yaml` and `stagewatch-env-poe.yaml`) do not do this.

### Battery nodes that sleep

A node that switches itself off between readings to save battery shows as missing in
between. To stop Stagewatch raising an offline alarm every time, set **Sleeps between
readings** for that node. See [Sleeping nodes](../integrations/esphome.md#sleeping-nodes).

---

## Updating a node later

After the first USB flash, you can update the node over Wi-Fi. The node and your computer
must be on the same network.

- **Device Builder:** open the file, click **Install**, and choose the wireless option
  instead of USB (the wording changes between versions).
- **Command line:** run the same `esphome run` command as in Step 5. When it asks how to
  upload, choose the node's name, not a COM port.

The update takes a few minutes and the node restarts. If you change `ota_password` in
`secrets.yaml`, update over USB once so the node learns the new one.

### Adding more nodes

For each extra node:

1. Make a **new key** (Step 3) and put it in the `api_encryption_key` line of `secrets.yaml`.
2. Give it a **new `node_name`** (Step 4).
3. Flash it (Step 5). After the first time, you can update it over Wi-Fi.
4. Adopt it (Step 7) with **its own key**.

---

## How to check it worked

- The boot log lists the sensor's address and shows readings.
- The status light (or the screen) shows the "all well" state for your board.
- In Admin, **ESPHome nodes** shows the node under **Adopted** with status **ok**.
- The reading matches your reference, give or take a little while the sensor settles.
- A dashboard shows the node's readings and a speed of sound.
- With two or more pressure nodes close together, their readings agree to within a hPa or
  two. If they do not, calibrate against a reference.

---

## If it didn't work

Problems that belong to one build are in that build's page. These ones apply to every node.

| What you see | Likely cause | What to do |
|---|---|---|
| The computer does not find the board (no COM port) | A charge-only USB cable, or the board is not in flashing mode. | Try another USB **data** cable and another USB port. Then use the button trick for your board in Step 5. |
| The build fails with a secrets error | A line in `secrets.yaml` is missing, or a quote mark is missing. | Compare with `secrets.example.yaml`. The key must be a proper 44-character key, not the words in the example. A key that is all `A`s is refused. |
| The node is not in the **Discovered** list | It is on a different network, or the network blocks node discovery (mDNS, how devices find each other by name). | Check it is on the same 2.4 GHz network as the Stagewatch computer, not a guest network. Turn off "client isolation" or "AP isolation" on the router. Check `wifi_ssid` and the password. Then use **Add by host / IP**. |
| Status shows **fault**: "encryption key missing or wrong" | The key in Stagewatch is not the key on the node. | Paste the exact key from `secrets.yaml` that was there when you flashed. If you lost it, put a new key in `secrets.yaml`, flash the node again, then remove and adopt it again. |
| Status shows **fault**: "different hardware at this address" | A different board now answers at that node's address (for example you swapped boards). Stagewatch holds its readings back so one board's calibration is not applied to another. | In Admin, under **Adopted**, click **Remove** next to the node (history is kept), then adopt the new board with **Adopt…**. Set its **Offset** again. If you did not swap anything, another board may be using the same name or address. |
| Status shows **fault**: "same board as another device" | The same board has been adopted twice. | Remove one of the two. Also check two files do not share the same `node_name`. |
| Status shows **missing**: "connection lost" | The node lost Wi-Fi or power. | Check power, the distance to the access point and the Wi-Fi name. Stagewatch reconnects on its own. |
| Two nodes fight or keep dropping | They have the same `node_name`. | Give each a different one and flash again. |
| The log has no address for the sensor | A wire or cable is loose, a wire is on the wrong pin, or the sensor has no power. | Push the STEMMA QT cable in at both ends, or check the wires against your build's page. Try another cable. |
| The log says the address is `0x76` but the file uses `0x77` (or the other way round) | The sensor answers at a different address from the one in the file. | Change the address in the file to match the scan and flash again. |
| Temperature reads high | The sensor is too close to the board, a lamp, an amp or the sun. | Move it out on the cable, then use an **Offset** (Step 8). |
| The site temperature jumped up after adding a node | A chip temperature was switched on in a node file and is being averaged, or a rack node was adopted as **Environment**. | In Admin, **Sensors: calibration & averaging**, untick **Average** for that reading, or change the node's **Role** to **Equipment** if it measures gear. |

More help: [Troubleshooting](../troubleshooting.md#sensor-node-problems).

---

## Advanced

For installers. Crew can skip this.

### A fixed IP address

To give the node a fixed address on the show network, open the node file and find the
commented-out `manual_ip` block under `wifi:`. Thirteen of the 15 files have it (the two
exceptions are `stagewatch-env-sht45.yaml` and `stagewatch-env-poe.yaml`). Remove the `#`
from the start of each line of the block. Change the numbers to suit your network. Keep the
spaces at the start of each line.

```yaml
  manual_ip:
    static_ip: 192.168.1.50
    gateway: 192.168.1.1
    subnet: 255.255.255.0
```

Pick an address outside your router's automatic range, and different for each node. Update
the node (over USB the first time). Stagewatch ties the calibration to the board, so the
calibration stays if the address changes.

### Moving between sites (several Wi-Fi networks)

A node can know more than one network, for example your test bench, home and a venue. In the
node file, replace the two lines `ssid:` and `password:` under `wifi:` with a `networks:`
list. Keep the spaces at the start of each line. Add the extra names and passwords to
`secrets.yaml` first, then flash again over USB or Wi-Fi.

```yaml
wifi:
  networks:
    - ssid: !secret wifi_ssid
      password: !secret wifi_password
    - ssid: !secret wifi_home_ssid
      password: !secret wifi_home_password
```

- Each time the node starts, or loses its network, it looks for the networks in the list and
  joins the strongest one it can see. Give a network `priority: 10` (a number from -128 to
  127) to make it win whenever it is in range.
- It does not move to another listed network while it is still connected. Power it off and
  on when you change site, or wait: if it cannot connect for 15 minutes it restarts and
  looks again (on the ten files that restart themselves).
- If none of the networks is found, it starts its own back-up network, named after
  `node_name`, using `wifi_ap_password`. The five older files have no back-up network.
- A fixed IP address belongs to one network, so put `manual_ip:` under that network in the
  list. Stagewatch finds nodes by name, so a different address on each network is fine.
- Stagewatch must be on the same network as the node to see it. Each network needs its own
  Stagewatch computer or a route to it.
- All the passwords are stored inside the node. Do not share a flashed node or its file.
- Hidden networks need `hidden: true`. Nodes on batteries that sleep can add
  `fast_connect: true` to save power, but it joins the first listed network it finds, not
  the strongest.

### Settings worth knowing

- Pressure is reported in hPa. Stagewatch converts it to pascals.
- The DPS310 and MPL3115A2 chip temperatures are switched off in the files because they read
  the chip, not the air.
- The node's own back-up network only starts if the node cannot join your Wi-Fi.

---

**Next:** [Dashboard cards](../cards/README.md) and the [Admin guide](../admin/README.md).
