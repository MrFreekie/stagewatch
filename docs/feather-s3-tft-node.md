# Build a Feather sensor node (ESP32-S3 TFT + MS8607)

This builds a pocket-sized **node** (a small Wi-Fi box that measures the air). It has a
colour screen that shows **temperature, humidity and pressure**. It sends those readings to
Stagewatch, which uses them to work out the **speed of sound**. **No soldering.**

Time needed: about 30 minutes the first time. Most of that is waiting for the first build.

> **Test status:** tested on real hardware (Adafruit 5483 with an MS8607) with ESPHome 2026.x,
> and the screen works. The battery readings have not been seen on a real board yet.

> Stagewatch is an advisory tool with no warranty. Check your node's readings against a
> reference thermometer before you trust it. See the [main README](../README.md#stagewatch).

Prefer a plain Feather with no screen, or a different sensor? Use the
[first sensor node guide](first-sensor-node.md) instead. This guide links back to it for the
steps that are the same.

---

## What you need

| Item | Notes |
|---|---|
| **Adafruit ESP32-S3 TFT Feather** | The standard one, Adafruit product **5483**. **Not** the "Reverse TFT" (5691). It uses different pins and this guide will not work on it. |
| **Adafruit MS8607 sensor** | Adafruit product 4716. Gives temperature, humidity and pressure. It has a STEMMA QT socket. |
| **A STEMMA QT cable, 150 to 200 mm long** | Shorter cables hold the sensor too close to the board's heat. |
| **A USB-C data cable** | It must carry **data**, not just power. Many phone-charging cables are charge-only. |
| **A 5 V USB supply or power bank** | For show use, a stable USB supply is best. |
| **A computer** to flash it | Windows is described here. |
| **Wi-Fi at 2.4 GHz** | The board does not do 5 GHz. |
| *Optional:* **a LiPo battery with a JST-PH plug** | Only a short ride-through if the power drops. **Check the polarity** before you plug it in (see below). |

> **LiPo warning.** The Feather's battery socket has a **+** and a **-** marked on the board.
> Many packs are wired the other way round and will damage the board. Check the pack's
> plug against the markings. Use a protected pack only. Never leave a charging pack in a hot
> flight case or under lamps.

Words used here: **flashing** is copying a program onto the node. **Firmware** is that program.
**ESPHome** is the free tool that builds it. See the [glossary](glossary.md).

---

## Step 1. Plug it together

1. Plug one end of the STEMMA QT cable into the **STEMMA QT port** on the Feather (the small
   white socket).
2. Plug the other end into the MS8607 sensor.

The plugs only fit one way round. Do not force them.

**What you should see:** nothing yet. The screen stays dark until the board has a program.

---

## Step 2. Get your secrets ready

A **secrets file** holds your Wi-Fi password and the keys that lock your node. You make
one copy, called `secrets.yaml`, and fill it in.

First, get the node files. Open a normal **PowerShell** window (Start, type `powershell`,
Enter), paste this and press Enter:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $d = "$HOME\Documents\stagewatch-node"; New-Item -ItemType Directory -Force $d | Out-Null; foreach ($f in "stagewatch-s3-tft-ms8607.yaml","secrets.example.yaml") { Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/MrFreekie/stagewatch/main/esphome/$f" -OutFile "$d\$f" }; if (-not (Test-Path "$d\secrets.yaml")) { Copy-Item "$d\secrets.example.yaml" "$d\secrets.yaml" }; explorer $d
```

**What you should see:** a folder window opens. It holds `stagewatch-s3-tft-ms8607.yaml`
(the node's settings), `secrets.example.yaml` (an example, leave it alone) and `secrets.yaml`
(**your** copy). If you already have a `secrets.yaml` from another node, the command leaves it
as it is.

Right-click `secrets.yaml`, choose **Open with** > **Notepad**. Change the part **between the
quote marks** on each line. Keep the quote marks.

| Line | What to put |
|---|---|
| `wifi_ssid` | The exact name of the Wi-Fi network the Stagewatch computer is on. Capital letters matter. It must be 2.4 GHz. |
| `wifi_password` | That network's password. |
| `ota_password` | Make one up. It protects future **wireless** updates of the node. |
| `api_encryption_key` | A secret key that locks the link between the node and Stagewatch. **Make a new one for this node** (below). |
| `wifi_ap_password` | **New line.** A password for the node's own back-up Wi-Fi network, used only if it cannot join yours. At least 8 characters. If your old `secrets.yaml` does not have this line, add it. |

### Make the key

In PowerShell, paste this and press Enter:

```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
```

**What you should see:** one line of 44 letters and numbers ending in `=`. Copy it and paste
it into `secrets.yaml` between the quote marks of `api_encryption_key`. Save the file (Ctrl + S).

**Keep a copy of this key.** You paste it into Stagewatch in Step 5, and Stagewatch never shows
it again. Use a **different key for every node**.

> **Never share `secrets.yaml` or put it online.** It holds your Wi-Fi password and your keys.
> Do not email it, post it or upload it to GitHub.

Name each node differently. In `stagewatch-s3-tft-ms8607.yaml`, near the top under
`substitutions:`, there are three lines you can change. Keep the quote marks and the spaces at
the start of each line.

| Line | What it does |
|---|---|
| `node_name` | The node's network name, no spaces, for example `stagewatch-stage-left`. Two nodes with the same name will fight. |
| `friendly_name` | The longer name shown in tools. |
| `screen_title` | The text at the top left of the screen. Keep it to about 16 characters. |

---

## Step 3. Flash it over USB

The **first flash must be over USB**. Later updates can go over Wi-Fi.

**The first build takes several minutes** (5 to 15 is normal) and needs **internet**, because
ESPHome downloads its tools and the screen fonts. Do not unplug anything while it works.

### Option A. ESPHome Device Builder (no commands)

This is the friendliest way. It is a normal Windows app. Follow
[Option A in the first sensor node guide](first-sensor-node.md#option-a-esphome-device-builder-no-command-line)
to install it, paste your secrets in, and import the file. Use
`stagewatch-s3-tft-ms8607.yaml` from `Documents\stagewatch-node`. Then:

1. Plug the Feather into the computer with the USB cable.
2. Click **Install** on the device card.
3. Choose the option that installs **over USB from this computer**. The wording changes between
   versions.
4. Pick the Feather's port from the list.
5. Wait. Build messages scroll past, then an upload.
6. When it says it succeeded, tap the **RESET** button once.

### Option B. Command line

If you are happy pasting commands, paste this in PowerShell to install ESPHome (you already have
`uv` if Stagewatch is installed on this PC):

```powershell
uv tool install esphome
```

Close PowerShell and open a new one. Paste this to build and flash:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-s3-tft-ms8607.yaml
```

When it asks how to upload, choose the **COM port** (a name like `COM3`), not "Over the air".
When it finishes, tap **RESET** on the Feather.

### If the board is not found: the BOOT + Reset trick

1. **Hold the BOOT button.**
2. **Tap the RESET button.**
3. **Let go of BOOT.**

The board is now waiting for a program. The screen stays dark. Try the install again. If it is
still not found, see "If it didn't work" below.

> Flashing this way **replaces Adafruit's built-in "UF2" loader**. That is fine for Stagewatch. If
> you ever want it back, use Adafruit's web installer.

### Why not use the ESPHome web installer?

<https://web.esphome.io> can only flash a **ready-made** program. It cannot build the Stagewatch
file. It is handy for checking that your cable and board talk to the computer, but it will not
put Stagewatch on the node.

### What success looks like

After RESET, the Feather's screen lights up (dim, at 40 %). Within a minute you should see:

- the **node title** at the top left, for example "Stagewatch S3 1";
- **two green dots** at the top right;
- the **temperature** in large white digits with °C;
- the **humidity** (cyan, bottom left of the readings) and the **pressure** (cyan, right, for
  example 1,013.2 hPa);
- the node's **IP address** at the bottom left and the Wi-Fi signal (for example -60 dBm) at the
  bottom right.

If the picture is upside down or the colours look wrong, see "If it didn't work".

---

## Step 4. Check it joined Wi-Fi

Look at the **first dot** at the top right of the screen. If it is **green**, the node is on your
Wi-Fi. The **second dot** is the Stagewatch connection. It stays **amber** until you adopt the
node in the next step. That is normal.

---

## Step 5. Add the node in Stagewatch

1. On any device on the network, open Stagewatch's admin page: the Stagewatch computer's
   address, then `/admin` (for example `http://192.168.1.50:8080/admin`). Log in with your PIN.
2. Find the card **ESPHome nodes**. Under **Discovered node** your new node is listed by its
   name. This can take a minute.
3. Click **Adopt…** next to it. A form opens with the host and name already filled in.
4. In **Encryption key**, paste the key you made in Step 2.
5. In **Area**, type where the node lives, for example `FOH`, `Stage L` or `Delay tower 1`.
6. Click **Adopt**.

**What you should see:** a message "Node adopted". The node appears under **Adopted** with the
status **ok**. The second dot on the Feather's screen turns **green**.

If the node is not in the list, use **Add by host / IP** on the same card. Type the IP address
shown at the bottom of the Feather's screen (or `your-node-name.local`), leave the port at
`6053`, and fill in the name, area and key. Then click **Adopt**.

Then open a dashboard. The temperature, humidity and pressure appear on its cards, and the site
average and speed of sound start to update.

---

## Using it on a gig

### The screen

| You see | It means |
|---|---|
| **Temperature** in large white digits | The air temperature at the sensor, in °C. |
| **Humidity** in cyan on the left, **pressure** in cyan on the right | Relative humidity in %, and pressure in hPa. |
| **First dot** (Wi-Fi): green | The node is on your Wi-Fi. |
| **First dot**: red | The node has lost the Wi-Fi. Check the access point and the node's distance from it. |
| **Second dot** (Stagewatch): green | The node is connected to Stagewatch. |
| **Second dot**: amber | The node is on Wi-Fi but cannot reach Stagewatch. Check the Stagewatch computer is on and on the same network. |
| **NO SENSOR** in red, with "check STEMMA QT cable" | The node has had no good temperature for 30 seconds. The sensor cable is probably loose. |
| Bottom of the screen | The node's IP address (left) and Wi-Fi signal (right). |

### The buttons

| Button | What it does |
|---|---|
| **BOOT** | Turns the screen on and off. Useful in a dark venue. |
| **D1** | Steps the brightness: 10 %, then 40 %, then 100 %, then back to 10 %. It starts at 40 % each time the node powers up. |

Turning the screen off does not stop the node measuring.

### The LED

The small LED on the board is **off** normally. It lights for **20 seconds** when something is
wrong, then goes off again:

- **Red glow:** the node lost the Wi-Fi.
- **Amber glow:** the node lost its connection to Stagewatch.

The screen dots stay lit after the LED goes out, so look at the dots for the current state.

### The node restarts itself

If the node has had **no Stagewatch connection for 15 minutes**, it restarts by itself. This
clears a stuck Wi-Fi connection. You do not need to do anything. If it keeps restarting, check
the Wi-Fi and that Stagewatch is running.

### Where to put it

| Do | Don't |
|---|---|
| Hang the sensor **on its cable, in free air**, where the air matches the air the sound travels through (near the delay tower or the side of the stage). | Don't let the sensor **lie on or touch the Feather**. The board, its regulator and the screen backlight all make heat. |
| Keep the sensor **well away from the board** and any battery. | Don't put it near **amps, lamps or dimmers**. |
| Keep it **out of direct sun**. Outdoors, use a radiation shield (a louvred cover). | Don't put it by a **haze outlet**. It pushes the humidity up. |
| Power it from a **stable 5 V USB supply**. | Don't rely on a battery for a whole show. It is only a short ride-through. |
| Protect the board from rain and support the USB lead so it cannot be pulled out. | Don't leave a charging battery in a hot case or under lamps. |

### Checking and correcting the readings (calibration)

The MS8607 is reasonably accurate (about 0.5 °C, 3 % humidity and 2 hPa) but you should still
check it.

1. Put a **reference thermometer** (for example a Kestrel) next to the sensor. Wait 15 minutes.
2. In Admin, open the **Sensors: calibration & averaging** card.
3. If the node reads high or low, type the correction in **Offset**. It is **added** to every
   reading. If the node reads 0.6 °C too high, type `-0.6`. Click **Save**.
   (Pressure offsets are in pascals: 1 hPa is 100 Pa.)
4. To leave a sensor out of the site average (for example one in direct sun), untick
   **Average**.

Stagewatch ties the calibration to the **board itself**, so it stays with the board if its IP
address changes.

The node also reports **battery** readings. These are for information only. They may look odd
or be missing if no battery is fitted. That does no harm.

---

## Updating it later

After the first USB flash, you can update the node over Wi-Fi. The node and your computer must
be on the same network. Open the file in **ESPHome Device Builder** and click **Install**, then
choose the wireless option instead of USB (the wording changes between versions). Or, on the
command line:

```powershell
cd "$HOME\Documents\stagewatch-node"; esphome run stagewatch-s3-tft-ms8607.yaml
```

Choose the node's name (not a COM port) when it asks how to upload. The update takes a few
minutes and the node restarts. If you change `ota_password` in `secrets.yaml`, update over USB
once so the node learns the new one.

---

## How to check it worked

- The screen shows the temperature, humidity and pressure, and **both dots are green**.
- In Admin, **ESPHome nodes** shows the node under **Adopted** with status **ok**.
- The temperature matches your reference thermometer, give or take a degree while it settles.
- A dashboard shows the node's readings and a speed of sound.

---

## If it didn't work

| What you see | Likely cause | What to do |
|---|---|---|
| The computer does not find the board (no COM port) | A charge-only USB cable, or the board is not in flashing mode. | Try another USB **data** cable and another USB port. Then do the BOOT + RESET trick above. |
| The screen is blank after flashing | The screen is turned off or very dim, or the flash did not finish. | Press **BOOT** to turn it on, and **D1** to turn the brightness up. If it is still blank, tap **RESET**. If that fails, flash it again over USB. |
| The screen shows **NO SENSOR** | The STEMMA QT cable is not fully in. | Push it in at both ends. Try another cable. If you can see the node's log, it should list devices at `0x76` and `0x40` (see Advanced). |
| Snow or noise down the sides of the screen, and the tops of the numbers flicker | Your copy of ESPHome is older than the node file expects. Older versions read the screen size the other way round. | Update ESPHome: in PowerShell, type `uv tool upgrade esphome`, then update the node again. |
| The picture is upside down, or the colours are wrong | The screen settings need a small change. | Ask your installer. In the node file, change `rotation: 90°` to `270°`, or flip `invert_colors`. Then update the node. |
| The node is not in Stagewatch's **Discovered** list | It is on a different network, or the network blocks node discovery (mDNS). | Check it is on the same 2.4 GHz network as the Stagewatch computer, not a guest network. Turn off "client isolation" on the router. Then use **Add by host / IP** with the address from the Feather's screen. |
| Status shows **fault**: "encryption key missing or wrong" | The key in Stagewatch is not the key on the node. | Paste the exact key from `secrets.yaml` that was there when you flashed. If you lost it, put a new key in `secrets.yaml`, flash the node again, then remove and adopt it again. |
| Status shows **fault**: "different hardware at this address" | A different board now answers at that node's address (for example you swapped boards). Stagewatch holds its readings back so one board's calibration is not applied to another. | In Admin, under **Adopted**, click **Remove** next to the node (history is kept), then adopt the new board with **Adopt…**. You will need to set its **Offset** again. If you did not swap anything, another board may be using the same address, so check the node's name and IP address. |
| Status shows **fault**: "same board as another device" | The same board has been adopted twice. | Remove one of the two. |

More help: [Troubleshooting](troubleshooting.md#sensor-node-problems) and the
[first sensor node guide](first-sensor-node.md#if-it-doesnt-work).

---

## Advanced

For installers. Crew can skip this.

### Pins and addresses

| Part | Pin or address |
|---|---|
| STEMMA QT data / clock (I²C) | GPIO42 (SDA) / GPIO41 (SCL), 100 kHz |
| Power for the STEMMA QT port and the screen | GPIO21 (`TFT_I2C_POWER`). The config switches it on at boot. Without it neither works. |
| Screen (ST7789, 240 x 135) | CS GPIO7, DC GPIO39, reset GPIO40, clock GPIO36, data GPIO35, backlight GPIO45 |
| LED (NeoPixel) | Data GPIO33, power switch GPIO34 (off unless showing an alert) |
| BOOT button | GPIO0 |
| D1 button | GPIO1 (pulled down on this board, high when pressed) |

| I²C address | Part |
|---|---|
| `0x76` | MS8607 temperature and pressure |
| `0x40` | MS8607 humidity |
| `0x36` | Battery gauge (MAX17048) on boards from early 2023 |
| `0x0B` | Battery gauge (LC709203F) on older boards. The file has a comment showing the swap. |

The node scans the bus at start-up and logs each device it finds. You should see `0x76` and
`0x40`, and `0x36` or `0x0B`.

### A fixed IP address

To give the node a fixed address on the show network, open `stagewatch-s3-tft-ms8607.yaml`, find
the commented-out block under `wifi:` and remove the `#` from the start of each line. Change the
numbers to suit your network. Keep the spaces at the start of each line.

```yaml
  manual_ip:
    static_ip: 192.168.1.50
    gateway: 192.168.1.1
    subnet: 255.255.255.0
```

Pick an address outside your router's automatic range. Update the node (over USB the first time).
In Stagewatch, if the address changes, the calibration stays with the board.

### Moving between sites (several Wi-Fi networks)

A node can know more than one network, for example your test bench, home and a venue. Open
`stagewatch-s3-tft-ms8607.yaml` and replace the two lines `ssid:` and `password:` under `wifi:`
with a `networks:` list. Keep the spaces at the start of each line. Add the extra names and
passwords to `secrets.yaml` first, then flash again over USB or Wi-Fi.

```yaml
wifi:
  networks:
    - ssid: !secret wifi_ssid
      password: !secret wifi_password
    - ssid: !secret wifi_home_ssid
      password: !secret wifi_home_password
```

- Each time the node starts, or loses its network, it looks for the networks in the list and
  joins the strongest one it can see. Give a network `priority: 10` (a number from -128 to 127)
  to make it win whenever it is in range.
- It does not move to another listed network while it is still connected. Power it off and on
  when you change site, or wait: if it cannot connect for 15 minutes it restarts and looks again.
- If none of the networks is found, it starts its own back-up network (see "Other notes").
- A fixed IP address belongs to one network, so put `manual_ip:` under that network in the list.
  Stagewatch finds nodes by name, so a different address on each network is fine.
- Stagewatch must be on the same network as the node to see it. Each network needs its own
  Stagewatch computer or a route to it.
- All the passwords are stored inside the node. Do not share a flashed node or its file.
- Hidden networks need `hidden: true`. Nodes on batteries that sleep can add `fast_connect: true`
  to save power, but it joins the first listed network it finds, not the strongest.

### Other notes

- The node's own back-up network is named after `node_name` and uses `wifi_ap_password`. It
  only starts if the node cannot join your Wi-Fi.
- The Feather's chip temperature is **switched off** in the file on purpose. It reads the chip, not
  the air, and would pull the site average up.
- The pressure reading is in hPa. Stagewatch converts it to pascals.

---

**Next:** [Using Stagewatch on show day](using-stagewatch.md).
