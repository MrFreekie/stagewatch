# Feather S3 TFT node with MS8607

This builds a pocket-sized **node** with a colour screen that shows **temperature, humidity
and pressure**. It sends those readings to Stagewatch, which uses them to work out the
**speed of sound**. **No soldering.**

This page covers only what is special to this build: the parts, the screen, the buttons and
the lights. The steps every node shares (secrets, flashing, adopting in Admin, calibration,
updating, fixed IP, several Wi-Fi networks) are in the [node hub](README.md).

> **Test status:** tested on real hardware (Adafruit 5483 with an MS8607) with ESPHome 2026.x,
> and the screen works. The battery readings have not been seen on a real board yet.

> Stagewatch is an advisory tool with no warranty. Check your node's readings against a
> reference thermometer before you trust it. See the [notices](../integrations/README.md#notices).

Prefer a plain Feather with no screen, or a different sensor? Use the
[Feather S3 environment node](feather-s3-env.md) instead.

---

## What you need

| Item | Notes |
|---|---|
| **Adafruit ESP32-S3 TFT Feather** | The standard one, Adafruit product **5483**. **Not** the "Reverse TFT" (5691). It uses different pins and this guide will not work on it. |
| **Adafruit MS8607 sensor** | Adafruit product 4716. Gives temperature, humidity and pressure. It has a STEMMA QT socket. |
| **A STEMMA QT cable, 150 to 200 mm long** | Shorter cables hold the sensor too close to the board's heat. |
| **A USB-C data cable** | It must carry **data**, not just power. Many phone-charging cables are charge-only. |
| *Optional:* **a LiPo battery with a JST-PH plug** | Only a short ride-through if the power drops. See the LiPo warning in the [hub](README.md#what-you-need-every-node). |

The rest (a stable 5 V supply, a computer, 2.4 GHz Wi-Fi) is in the hub.

---

## Plug it together (hub Step 1)

1. Plug one end of the STEMMA QT cable into the **STEMMA QT port** on the Feather (the small
   white socket).
2. Plug the other end into the MS8607 sensor.

The plugs only fit one way round. Do not force them.

**What you should see:** nothing yet. The screen stays dark until the board has a program.

---

## The file and its three names (hub Steps 3 and 4)

Use `stagewatch-s3-tft-ms8607.yaml`. This file needs the `wifi_ap_password` line in
`secrets.yaml`, as described in the hub.

Under `substitutions:` there are three lines you can change. Keep the quote marks and the
spaces at the start of each line.

| Line | What it does |
|---|---|
| `node_name` | The node's network name, no spaces, for example `stagewatch-stage-left`. Two nodes with the same name will fight. |
| `friendly_name` | The longer name shown in tools. |
| `screen_title` | The text at the top left of the screen. Keep it to about 16 characters. |

---

## What success looks like (after hub Step 5)

After RESET, the Feather's screen lights up (dim, at 40 %). Within a minute you should see:

- the **node title** at the top left, for example "Stagewatch S3 1";
- **two green dots** at the top right;
- the **temperature** in large white digits with °C;
- the **humidity** (cyan, bottom left of the readings) and the **pressure** (cyan, right, for
  example 1,013.2 hPa);
- the node's **IP address** at the bottom left and the Wi-Fi signal (for example -60 dBm) at
  the bottom right.

If the picture is upside down or the colours look wrong, see "If it didn't work".

### Check it joined Wi-Fi

Look at the **first dot** at the top right of the screen. If it is **green**, the node is on
your Wi-Fi. The **second dot** is the Stagewatch connection. It stays **amber** until you
adopt the node ([hub Step 7](README.md#step-7-add-the-node-in-stagewatch)). That is normal.

After adopting, the second dot turns **green**. The IP address for **Add by host / IP** is
at the bottom of the screen.

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

The small LED on the board is **off** normally. It lights for **20 seconds** when something
is wrong, then goes off again:

- **Red glow:** the node lost the Wi-Fi.
- **Amber glow:** the node lost its connection to Stagewatch.

The screen dots stay lit after the LED goes out, so look at the dots for the current state.

### The node restarts itself

If the node has had **no Stagewatch connection for 15 minutes**, it restarts by itself. This
clears a stuck Wi-Fi connection. You do not need to do anything. If it keeps restarting,
check the Wi-Fi and that Stagewatch is running.

### Calibration

The MS8607 is reasonably accurate (about 0.5 °C, 3 % humidity and 2 hPa) but you should
still check it. Use [hub Step 8](README.md#step-8-check-and-correct-the-readings-calibration).

The node also reports **battery** readings. These are for information only. They may look
odd or be missing if no battery is fitted. That does no harm.

Placement is in [hub Step 2](README.md#step-2-decide-where-it-will-live). For this node,
remember the board, its regulator and the **screen backlight** all make heat, so keep the
sensor well away from the board and any battery.

---

## How to check it worked

- The screen shows the temperature, humidity and pressure, and **both dots are green**.
- In Admin, **ESPHome nodes** shows the node under **Adopted** with status **ok**.
- The temperature matches your reference thermometer, give or take a degree while it settles.
- A dashboard shows the node's readings and a speed of sound.

---

## If it didn't work

The shared problems (no COM port, node not discovered, key errors, hardware faults) are in
the [hub table](README.md#if-it-didnt-work). These are specific to this build.

| What you see | Likely cause | What to do |
|---|---|---|
| The screen is blank after flashing | The screen is turned off or very dim, or the flash did not finish. | Press **BOOT** to turn it on, and **D1** to turn the brightness up. If it is still blank, tap **RESET**. If that fails, flash it again over USB. |
| The screen shows **NO SENSOR** | The STEMMA QT cable is not fully in. | Push it in at both ends. Try another cable. If you can see the node's log, it should list devices at `0x76` and `0x40` (see Advanced). |
| Snow or noise down the sides of the screen, and the tops of the numbers flicker | Your copy of ESPHome is older than the node file expects. Older versions read the screen size the other way round. | Update ESPHome: in PowerShell, type `uv tool upgrade esphome`, then update the node again. |
| The picture is upside down, or the colours are wrong | The screen settings need a small change. | Ask your installer. In the node file, change `rotation: 90°` to `270°`, or flip `invert_colors`. Then update the node. |

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

### Other notes

- The node's own back-up network is named after `node_name` and uses `wifi_ap_password`. It
  only starts if the node cannot join your Wi-Fi.
- The Feather's chip temperature is **switched off** in the file on purpose. It reads the
  chip, not the air, and would pull the site average up.
- The pressure reading is in hPa. Stagewatch converts it to pascals.

---

**Next:** [Dashboard cards](../cards/README.md).
