# Build your first sensor node

A **node** is a small Wi-Fi box that measures the air and sends it to Stagewatch.
This guide builds one from an Adafruit ESP32-S3 Feather and a plug-in sensor.
**No soldering.**

Time needed: about 45 minutes the first time (most of it is waiting for the first build).

Building the TFT Feather with the MS8607 sensor instead? Use the
[Feather sensor node guide](feather-s3-tft-node.md).

> Stagewatch is an advisory tool with no warranty. Check your node's readings against a
> reference thermometer before you trust it. See the [main README](../README.md#stagewatch).

---

## What you need

| Item | Notes |
|---|---|
| **Adafruit ESP32-S3 Feather** with STEMMA QT | Either the plain one, or the **TFT** one, which has a small screen. |
| **A sensor with a STEMMA QT connector** | **BME280** (Adafruit 2652) gives temperature, humidity **and pressure**. **SHT45** (Adafruit 5665) is more accurate on temperature and humidity but has **no pressure**. Easiest choice: the BME280. |
| **A STEMMA QT cable** | Get one **at least 10 cm long**. Shorter ones keep the sensor too close to the board's heat. |
| **A USB cable that carries data** | Not a charge-only cable. If the computer never sees the board, try another cable first. |
| **A computer** to flash it | Windows is described here. |
| **Wi-Fi at 2.4 GHz** | The ESP32 does not do 5 GHz. |

Words used here: **flashing** is copying a program onto the node. **Firmware** is that
program. **ESPHome** is the free tool that builds it. See the [glossary](glossary.md).

---

## Step 1. Plug it together

1. Plug the STEMMA QT cable into the Feather's **STEMMA QT port** (the small white socket).
2. Plug the other end into the sensor.

The plugs only fit one way round. Do not force them.

**What you should see:** nothing yet. There is nothing else to wire. No soldering.

---

## Step 2. Decide where it will live

The **Feather itself makes heat**. If the sensor is close to it, or lying on it, the
readings will be too high.

- Keep the sensor **on the cable, in open air**, away from the Feather, amps, lamps,
  dimmers, and anything else that gets warm.
- **Out of direct sun.** Outdoors, use a louvred or radiation shield.
- **Away from haze and fog outlets**, which push the humidity reading up.
- Put it where the air is like the air the sound travels through, for example near
  the delay tower or the side of the stage, not in a hot case.
- Power it from a **stable USB 5 V supply**. A battery, if you fit one, is only a short
  ride-through. Only use a protected LiPo with the right plug polarity. Never leave a
  charging battery in a hot flight case.
- Protect the board from rain, and support the USB cable so it cannot be pulled out.

Later you can check it against a reference thermometer and correct it in Stagewatch
(Step 9).

---

## Step 3. Get the node files

You need three small text files from the project. Open a normal **PowerShell** window
(Start, type `powershell`, Enter), paste this and press Enter:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $d = "$HOME\Documents\stagewatch-node"; New-Item -ItemType Directory -Force $d | Out-Null; foreach ($f in "stagewatch-feather-s3.yaml","stagewatch-feather-s3-tft.yaml","secrets.example.yaml") { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/MrFreekie/stagewatch/main/esphome/$f" -OutFile "$d\$f" }; Copy-Item "$d\secrets.example.yaml" "$d\secrets.yaml"; explorer $d
```

**What you should see:** a folder window opens with four files in it:

- `stagewatch-feather-s3.yaml`: for the **plain** Feather.
- `stagewatch-feather-s3-tft.yaml`: for the **TFT** Feather.
- `secrets.example.yaml`: an example. Leave it alone.
- `secrets.yaml`: **your** copy. You fill this in next.

A **YAML** file is just a text file with settings. Open it with **Notepad**. Lines that
start with `#` are notes to humans and are ignored.

---

## Step 4. Fill in `secrets.yaml`

Right-click `secrets.yaml`, choose **Open with** > **Notepad**. It looks like this:

```yaml
wifi_ssid: "YOUR_CONTROL_NETWORK"
wifi_password: "CHANGE_ME"
ota_password: "CHANGE_ME"
api_encryption_key: "PASTE_32_BYTE_BASE64_KEY_HERE"
```

Change the part **between the quote marks** on each line. Keep the quote marks.

| Line | What to put |
|---|---|
| `wifi_ssid` | The exact name of the Wi-Fi network the Stagewatch computer is on. Capital letters matter. It must be a **2.4 GHz** network. |
| `wifi_password` | That network's password. |
| `ota_password` | Make up a password. It protects future **wireless** updates of the node. You will rarely need it again. |
| `api_encryption_key` | A secret key that locks the link between the node and Stagewatch. Make one below. |

### Make the key

Open PowerShell, paste this and press Enter:

```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
```

**What you should see:** one line of 44 letters and numbers ending in `=`, something like
`Xy3k...=`. Select it, copy it, and paste it into `secrets.yaml` between the quote marks
of `api_encryption_key`.

**Keep a copy of this key.** You will paste it into Stagewatch in Step 8. Stagewatch
never shows it again.

Save the file (Ctrl + S).

> **Keep `secrets.yaml` private.** It holds your Wi-Fi password and key. Never post it,
> email it or put it on GitHub. Use **a new key for each node**.

---

## Step 5. Pick the right file and check the small settings

**Plain Feather (no screen):** use `stagewatch-feather-s3.yaml`. Near the top, under
`substitutions:`, check that the board matches yours. Look at the sticker or shop page:

| Your board | `board:` | `flash_size:` |
|---|---|---|
| Adafruit **5323** (STEMMA QT, 8 MB flash, no PSRAM). This is the default in the file. | `adafruit_feather_esp32s3_nopsram` | `8MB` |
| Adafruit **5477** (4 MB flash, 2 MB PSRAM) | `adafruit_feather_esp32s3` | `4MB` |

A wrong `flash_size` (bigger than fitted) means the board will not start.

**TFT Feather:** use `stagewatch-feather-s3-tft.yaml`. It needs no board choice.

**Name each node differently.** Under `substitutions:` change `node_name` to something
short and unique with no spaces, such as `stagewatch-stage-left`. That is the node's
network name (`stagewatch-stage-left.local`). Two nodes with the same name will fight.

**Using an SHT45 instead of the BME280?**

- **Plain Feather:** in the file, find the block that says `Primary: BME280`. Put a `#`
  at the start of each line of that block, so the file ignores it. Then remove the `#`
  from each line of the `Alternative: SHT45` block just below it. Keep the spaces at the
  start of each line exactly as they are. YAML cares about spaces.
- **TFT Feather:** it works but needs a second edit in the screen section (the comment in
  the file says what). The easiest way is to use the **BME280** on the TFT model.
- The SHT45 has no pressure sensor. Stagewatch will then work out pressure from the
  **site altitude**, which you set in Admin (Step 9).

If you are not sure, keep the BME280.

You may notice **battery** lines in the file. If your board's battery chip differs from
the one enabled, the battery reading may not appear. That does no harm.

---

## Step 6. Flash the node

The **first flash must be over USB**. Every later update can go over Wi-Fi.

Building the program for the first time needs **internet** and takes **5 to 15 minutes**,
because ESPHome downloads its tools. The TFT version also downloads a font.

### Put the Feather into flashing mode (first time only)

1. Plug the Feather into the computer with the USB cable.
2. **Hold the BOOT button.**
3. **Tap the RESET button.**
4. **Let go of BOOT.**

(BOOT and RESET are the two small buttons on the board.)

The Feather is now waiting for a new program. Its screen or lights may stay dark. That is
normal.

> Flashing this way **replaces Adafruit's built-in "UF2" loader**. That is fine for
> Stagewatch. If you ever want it back, use Adafruit's web installer.

### Option A. ESPHome Device Builder (no command line)

This is the friendliest way. It is a normal Windows app.

1. Go to <https://esphome.io/guides/installing_esphome/>, choose the **Windows** tab,
   and download the installer (`.exe`).
2. Open it. Click **Yes** if Windows asks. If Windows says **"Windows protected your PC"**,
   click **More info** then **Run anyway**.
3. Start **ESPHome Device Builder** from the Start menu. Your web browser opens with its
   dashboard. The first start sets things up in the background and takes a minute.
4. Add your secrets. Click **Secrets** (near the top right). Copy everything from your
   `secrets.yaml` file and paste it there. Save.
5. Click **Create device** (or **+**). Choose **Import from File**. Pick your
   `stagewatch-feather-s3.yaml` (or the `-tft` one) from
   `Documents\stagewatch-node`.
6. Click **Install** on the new device card. Choose the option that installs **over USB
   from this computer** (its wording changes between versions). Pick the Feather's port
   from the list.
7. Wait. You will see build messages and then an upload. When it says it succeeded,
   **tap the RESET button** on the Feather once.

**What you should see:** "INFO Successfully uploaded program" (or similar), then the node
starts, joins Wi-Fi, and appears as **Online** on its card.

The exact button names in the app can change from version to version. The official guide
is [Getting Started with ESPHome](https://esphome.io/guides/getting_started_command_line/).

### Option B. Command line (if you are happy pasting commands)

You already have `uv` if you installed Stagewatch on this PC.

1. Paste this and press Enter. It installs ESPHome:

```powershell
uv tool install esphome
```

2. **Close PowerShell and open a new one.** Then paste this. It builds the node and
   flashes it. Swap in `stagewatch-feather-s3-tft.yaml` for the TFT board:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-feather-s3.yaml
```

3. When it asks how to upload, choose the **COM port** (a name like `COM3`), not "Over
   the air".
4. When it finishes, **tap RESET** on the Feather. ESPHome then shows the node's log.
   Press **Ctrl + C** to stop watching it.

### Why not just use web.esphome.io?

<https://web.esphome.io> is a web page that can flash a **ready-made starter** ESPHome
program onto a board. It **cannot build Stagewatch's file** for you. It is handy for
checking your cable and board work, but you still need Option A or B to put the Stagewatch
program on. (The Device Builder can also save a firmware file for you to flash
elsewhere. That route hasn't been tried for this guide.)

> **Not verified:** these flashing steps use the official ESPHome tools. The two Stagewatch
> config files check out as valid with ESPHome 2026.9.1. A full build and flash of them was
> not repeated while writing this guide, and the TFT screen layout is marked untested on
> hardware in the file itself.

---

## Step 7. Check the node is alive

- **Plain Feather:** the little on-board light is **amber** while it starts or has no
  Stagewatch connection, and turns **dim green** when Stagewatch is connected.
- **TFT Feather:** the screen shows, from top to bottom:
  - the **node name**,
  - the **temperature** in large digits with °C,
  - the **humidity** (%) on the left and **pressure** (hPa) on the right,
  - along the bottom, the **Wi-Fi signal** (in dBm, for example `-60 dBm`, or
    `no Wi-Fi`) and the node's **IP address**.
  - If it shows **NO SENSOR** in red with `check STEMMA QT cable`, the sensor is not
    being read. See "If it doesn't work" below.
  - Press the **BOOT** button to turn the screen backlight off and on (useful in dark
    venues).

**What you should see:** the temperature matches the room, give or take a degree or two
while the sensor settles.

---

## Step 8. Add the node in Stagewatch

1. On any device on the network, open Stagewatch's admin page: the Stagewatch computer's
   address, then `/admin` (for example `http://192.168.1.50:8080/admin`). Log in with your PIN.
2. Find the card **ESPHome nodes**. Under **Discovered node** your new node should be
   listed by its name (the **Board** column says **encrypted**). This can take a minute.
3. Click **Adopt...** next to it. A small form appears with the host and name already
   filled in.
4. In **Encryption key**, **paste the key** you made in Step 4.
5. In **Area**, type where it is, for example `Stage L`, `FOH` or `Delay tower 1`.
   Change **Name** if you want.
6. Click **Adopt**.

**What you should see:** a message "Node adopted". The node appears under **Adopted**
with the status **ok**. Its readings show up in the **Sensors** card and on the
dashboards.

**If the node isn't in the Discovered list**, use **Add by host / IP** on the same card:
type the node's address (its IP address is on the TFT screen, or in your router's list of
devices, or in the ESPHome log) or `your-node-name.local`, leave the port at `6053`, and
fill in the name, area and key. Then click **Adopt**.

---

## Step 9. Check and correct the readings (calibration)

1. Put a **reference thermometer** (for example a Kestrel) next to the node. Wait 15
   minutes.
2. In Admin, open the **Sensors: calibration & averaging** card.
3. If the node reads high or low, type the correction in **Offset**. It is **added** to
   every reading. So if the node reads 0.6 °C too high, type `-0.6`. Click **Save**.
   (Pressure offsets are in **pascals**: 1 hPa is 100 Pa.)
4. To leave a sensor out of the site average (for example one in direct sun), untick
   **Average**.
5. If you have no pressure sensor, open the **Site** card and set the **Altitude (m)** of
   your venue.

Do this for each node. See [Using Stagewatch](using-stagewatch.md) for the rest of the
admin tasks.

---

## Adding more nodes

For each extra node:

1. Make a **new key** (Step 4) and put it in the `api_encryption_key` line of `secrets.yaml`.
2. Give it a **new `node_name`** (Step 5).
3. Flash it (Step 6). After the first time, you can update it over Wi-Fi.
4. Adopt it (Step 8) with **its own key**.

Keep a note of which key belongs to which node.

---

## If it doesn't work

| Problem | What to check |
|---|---|
| **The computer does not see the board** (no COM port) | Try another USB **data** cable and another USB port. Put the board into flashing mode (Step 6). On Windows the ESP32-S3's USB should work without a driver. |
| **Build fails with a secrets error** | A line in `secrets.yaml` is missing, or a quote mark is missing. Compare with `secrets.example.yaml`. The key must be a proper 44-character key, not the words in the example. A key that is all `A`s is refused. |
| **Build fails at the fonts (TFT)** | The build needs internet to download the font. Check your connection and try again. |
| **The node never appears in Discovered** | The node and the Stagewatch computer must be on the **same network**. Check `wifi_ssid` and password. Only **2.4 GHz** works. Guest networks and "client isolation" or "AP isolation" settings block it. Some networks block **mDNS** (see the glossary). Use **Add by host / IP** instead. |
| **Status shows `fault` with "encryption key missing or wrong"** | The key you pasted in Stagewatch is not the key on the node. Paste the key from `secrets.yaml` that was there when you flashed it. If you have lost it, put a new key in `secrets.yaml`, re-flash the node, then remove and re-adopt it. |
| **Status shows `missing` with "connection lost"** | The node lost Wi-Fi or power. Check power, distance to the access point, and the Wi-Fi name. Stagewatch will reconnect on its own. |
| **The TFT says NO SENSOR** (or readings never arrive) | Check the STEMMA QT cable is pushed in **fully** at both ends. Try the other way round if you have two ports on the sensor. Then check the log (below). |
| **The sensor is not detected** | ESPHome scans the sensor connection when it starts and writes each thing it finds into its log, for example `Found i2c device at address 0x77`. Open the log with **Logs** on the device card in Device Builder, or run `esphome logs stagewatch-feather-s3.yaml` in Option B. A BME280 should show `0x77` (or `0x76` if its ADR jumper has been cut). An SHT45 should show `0x44`. If **nothing** is found, re-seat the cable or try another cable. If a different number shows, tell the project via a GitHub issue. |
| **Temperature reads high** | The sensor is too close to the Feather, a lamp, an amp or the sun. Move it out on the cable, then use an offset (Step 9). |
| **Screen is upside down or colours look wrong (TFT)** | The comment at the top of the file says which two settings to change. It is marked untested on hardware. |

Log lines mention your Wi-Fi name. Remove personal details before you post one online.

---

**Next:** [Using Stagewatch on show day](using-stagewatch.md).
