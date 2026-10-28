# Install Stagewatch on a Raspberry Pi

A Raspberry Pi is a small, cheap computer. It makes a good permanent Stagewatch box:
it starts by itself when powered, and it can drive a wall screen directly.

Time needed: about 40 minutes, mostly waiting.
Do this **at home or in the office**, with internet. Not at the venue.

> **Tested on hardware: partly.** The project owner reports that an install on a
> **Raspberry Pi 4 (4 GB)** worked, and so did a reboot afterwards (October 2026).
> The same installer is also tested in a **Debian 13.7 virtual machine**.
> **Not yet reported as tested:** a Raspberry Pi 5, the full-screen kiosk, in-app updates on a Pi,
> and a power cut.
> Try it at home before you rely on it at a show.
> Stagewatch is an advisory tool with no warranty. See the [Notices](../integrations/README.md#notices).

You will need:

- A **Raspberry Pi 4** (the one it was tried on) or newer, a power supply, and a microSD card (16 GB or more).
- A computer with an SD card slot or reader, to prepare the card.
- A **screen, keyboard and mouse** for the first set-up (you can remove them later).
- A network: Wi-Fi or an Ethernet cable, with internet.

No screen, or a PC rather than a Pi? See [Install on Debian Linux](debian-linux.md).

---

## Words you will meet

- **Terminal** is the Pi's command window. You paste a line, press Enter, and it runs.
- **SSH** is a way to use the Terminal from another computer. You do not need it here.
- **Kiosk** means one full-screen dashboard on the Pi's own screen, with nothing else on it.

More in the [glossary](../glossary.md).

---

## Step 1. Put the operating system on the SD card

1. On your normal computer, download **Raspberry Pi Imager** from
   <https://www.raspberrypi.com/software/> and install it.
2. Put the microSD card into your computer.
3. Open Raspberry Pi Imager and choose:
   - **Device:** your Pi model.
   - **Operating system:** **Raspberry Pi OS (64-bit)**, the normal one **with desktop**.
   - **Storage:** your SD card. Check carefully. Everything on it will be erased.
4. When the Imager offers to **customise** the settings (it may be called **Edit
   Settings** or shown as extra screens), fill in:
   - **Hostname:** something short, such as `stagewatch`. The Pi will then be reachable as
     `stagewatch.local`.
   - **Username and password:** choose your own. Write them down.
   - **Wi-Fi:** your network name and password (skip if you use a cable).
     Set the **wireless country**.
   - **Time zone** and **keyboard layout**.
   - **Enable SSH:** optional. It lets you use the Pi from another computer later.
5. Click **Write** and wait until it says it is finished.

**What you should see:** "Write successful". Remove the card.

## Step 2. First boot

1. Put the SD card in the Pi. Connect the screen, keyboard and mouse.
2. Plug in the power.
3. Wait. The first boot can take a couple of minutes.

**What you should see:** the Raspberry Pi desktop, with a taskbar at the top.

If it asks you to set up a country, user or Wi-Fi, follow the screens. Make sure the
Pi has internet (open the web browser and load any web page).

## Step 3. Open a Terminal

Click the **Terminal** icon in the top bar. It looks like a black box with `>_` in it.
(If you cannot see it: menu at top left > **Accessories** > **Terminal**.)

**What you should see:** a black window with a line ending in `$`. That is where you paste.

**How to paste in the Terminal:** press **Ctrl + Shift + V** (not just Ctrl + V), or
right-click and choose **Paste**.

## Step 4. Install Git and uv

Paste this and press Enter. It may ask for your **password** (the one you chose in Step 1).
You will not see letters as you type it. That is normal.

```bash
sudo apt update && sudo apt install -y git curl python3
```

**What you should see:** lots of text, then the `$` comes back. If it was already
installed, it says so. That is fine.

Now install `uv`. Paste this and press Enter:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(This is the official command from
[docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/).)

**What you should see:** text ending with something like "everything's installed".

Now **close the Terminal window and open a new one**, so the Pi notices `uv`. Check it:

```bash
git --version && uv --version
```

**What you should see:** two lines, one starting `git version` and one starting `uv`.

## Step 5. Download Stagewatch

Paste this and press Enter:

```bash
git clone https://github.com/MrFreekie/stagewatch.git ~/stagewatch
```

**What you should see:** `Cloning into '/home/YOUR_NAME/stagewatch'...` and then the
prompt comes back.

## Step 6. Run the installer

> **Which version do I get?**
> The installer picks the **latest release** for you (see the
> [Releases page](https://github.com/MrFreekie/stagewatch/releases)). You don't need to add
> anything. To choose differently, see the options table below.

Paste this and press Enter. It installs Stagewatch **and** the full-screen kiosk:

```bash
cd ~/stagewatch && bash deploy/pi/install.sh --kiosk
```

Leave `--kiosk` off if the Pi has no screen.

> **Installed Stagewatch on this Pi before?** Run the same command again. It stops the old
> service, switches to this install and **keeps your data** (it also saves a safety copy next
> to it, named `/var/lib/stagewatch.pre-managed-...`). If you typed `--managed` before, you can
> leave it off now; it does nothing.

It asks for your password. It then takes **several minutes** while it downloads Python
and libraries. Do not close the window.

What the options mean:

| Option | What it does |
|---|---|
| `--kiosk` | After the Pi logs in, opens the **wall** dashboard full screen on the Pi's screen. Leave this off if the Pi has no screen. |
| `--ref <tag-or-sha>` | Advanced. Install a particular version instead of the latest release. Leave it off normally. |
| `--channel nightly` | Advanced. Follow the Nightly build instead of Stable. See [Updating and backups](../updating-and-backups.md#stable-or-nightly-use-stable). |
| `--port 8080` | Optional. The default is 8080. |
| `--source-url <https address>` | Advanced. Install from a different copy of the project, such as your own fork. It must start with `https://`. In-app updates then follow that address. |
| `--emulate` | Fake sensors. Do not use this on a show Pi: fake readings would mix into the real history. |

**What you should see near the end:**

```text
Latest release tag: vX.Y.Z
Service enabled (tested on a Raspberry Pi 4 (4 GB), owner report 2026-10; not tested on Pi 5 or with the kiosk): sudo systemctl status stagewatch
Code: /opt/stagewatch (...)   Data: /var/lib/stagewatch (0700, user stagewatch)
Kiosk enabled: the 'wall' dashboard opens full screen after login.
Enable desktop auto-login in raspi-config (System Options > Boot / Auto Login).
Done. Open http://YOUR-PI-NAME.local:8080 from a tablet on the same network.
```

The text in brackets says what has been tested. It matches the note at the top of this page.

What the installer did, in plain words:

| Where | What |
|---|---|
| `/opt/stagewatch` | The program, with its own copy of Python. |
| `/var/lib/stagewatch` | Your settings, history, PIN and logs (the data folder). Only readable with `sudo`. More in [Where your data lives](../updating-and-backups.md#where-your-data-lives). |
| A system user called `stagewatch` | Runs Stagewatch. It cannot log in and has no `sudo`. |
| A boot service called `stagewatch` | Starts Stagewatch when the Pi powers up, restarts it if it stops, and applies updates. |
| Your own home folder | With `--kiosk`: a start-up entry for the full-screen dashboard (for the labwc desktop and for older X11 desktops). |

The installer does not change any firewall. Keep the `~/stagewatch` folder: you need it to
run the installer again or to uninstall.

## Step 7. Check it is running

Paste this and press Enter:

```bash
sudo systemctl status stagewatch
```

**What you should see:** a line with **active (running)** in green. Press **q** to leave.

## Step 8. Open Stagewatch and set your admin PIN

On the Pi, open the web browser and go to `http://localhost:8080/admin`.

1. It says **Welcome: set an admin PIN**. Choose a PIN of **at least 4 characters** and type
   it twice.
2. Click **Set admin PIN**.

**Write the PIN down.** If you forget it, see
[Updating and backups](../updating-and-backups.md#forgotten-pin-or-recovery-required).

> **Do this straight away.** Until a PIN is set, **anyone on the network** who opens
> `/admin` can set it.

More in [Security and data](../admin/security-and-data.md).

## Step 9. Open it from a tablet or phone

1. Put the tablet on the **same network** as the Pi.
2. In its browser, go to `http://stagewatch.local:8080`
   (use the hostname you chose in Step 1).
3. If `.local` names do not work on that tablet, use the Pi's IP address instead. On the
   Pi, paste this in the Terminal:

```bash
hostname -I
```

   The first number shown, like `192.168.1.50`, is the address. On the tablet type
   `http://192.168.1.50:8080`.

**What you should see:** the Stagewatch page with the FOH, Phone and Wall dashboards.

> **Tip:** ask your router to always give the Pi the same address (a "DHCP reservation"),
> so tablet bookmarks keep working.

## Step 10. Make the kiosk start by itself

The kiosk opens after the Pi **logs in**. So the Pi needs to log itself in.

1. In the Terminal, paste `sudo raspi-config` and press Enter.
2. Choose **System Options** (use the arrow keys and Enter).
3. Choose **Boot / Auto Login**.
4. Choose **Desktop Autologin**. (Menu wording may differ a little between versions.)
5. Choose **Finish**.

### Stop the screen going blank

1. Run `sudo raspi-config` again.
2. Choose **Display Options**, then **Screen Blanking**, and turn it **Off**.
3. Choose **Finish**.

### Restart and check

```bash
sudo reboot
```

**What you should see:** the Pi restarts, logs itself in, and the **wall** dashboard fills
the screen with no browser bars. After a power cut it should do the same.
This kiosk step has not yet been reported as tested on a real Pi.

To leave the kiosk for a moment: press **Alt + F4**, or press **Ctrl + Alt + T** for a
Terminal.

---

## The Pi has no clock battery

A Raspberry Pi does **not** keep time when it is switched off. With no internet it starts
with the **last time it saved**. That makes the history timestamps wrong.

Stagewatch stores every reading by time, so the Pi needs a time source. Before the show, do one
of these:

- **Connect the Pi to a network with internet.** It then sets its own clock (network time).
  This is the best answer if you can.
- **Fit an RTC** (real-time clock) add-on board with a battery. The Pi then keeps time with the
  power off. Follow the board maker's steps to switch it on, then set the clock once.
- **Set the clock from your tablet or phone.** In **Admin**, open the **System** tab, find the
  **Clock** card and press **Set the clock from this device**. See [below](#set-the-clock-from-your-tablet-or-phone).
- Use a local time server such as **chrony** (advanced). Run `sudo apt install chrony`, point it at
  a time source on your network, and leave Stagewatch's button alone.

Check the time on the Pi's screen (top right) before you set markers.

### Set the clock from your tablet or phone

This is a stopgap for a Pi with no network time and no clock battery. If the Pi does have an RTC,
the button writes the new time to it as well, so it keeps it after a power cut.

1. Open **Admin** on a tablet or phone whose own clock is right.
2. Open the **System** tab and find the **Clock** card. It shows the Pi's time, this device's time and the difference.
3. Press **Set the clock from this device**.
4. If the difference is more than 5 seconds, Stagewatch tells you how far the clock will jump and asks you to confirm.

**What you should see:** a message saying the clock has been set, and a marker in the show called
`Clock set from a browser (was ... s)`. The Pi's time on the card now matches the device.

**Do this before the show, not during it.** Readings that are already saved keep the time they
were saved with. Stagewatch does not rewrite or back-fill them. If the clock jumps forward, the
charts show a gap. If it jumps back, the charts show an overlap for that stretch. A small
correction of a few seconds is nothing to worry about.

The first time, the Clock card may say **Not set up on this Pi** and show one line to type in a
Terminal window on the Pi. Type it once (it asks for your Pi password), then reload Admin. A new
install made with `install.sh` sets this up for you (leave it out with `--no-clock-button`).

The button works with a network time source as well, but the Pi will correct itself again
at its next network time update. To take the button away, run
`sudo bash /opt/stagewatch/deploy/pi/install-clock-helper.sh --uninstall`.

---

## How to check it worked

- Step 7 shows **active (running)**.
- `http://localhost:8080` opens the Stagewatch page on the Pi, and the tablet opens it too (Step 9).
- **Admin > Software** says **Install: Managed**.
- After `sudo reboot`, the page is back within a minute or two without you doing anything.

---

## If it doesn't work

| What you see | What it means | What to do |
|---|---|---|
| `uv not found. Install it first: ...` when running the installer | uv is not installed, or the Terminal is old. | Close the Terminal and open a new one. Run `uv --version`. If it fails, repeat Step 4. |
| `git not found (apt install git).` | Git is missing. | Repeat the first command in Step 4. |
| `python3 not found.` | Python 3 is missing. | Repeat the first command in Step 4. |
| `no release tag found; pass --ref <tag-or-sha>` or `could not resolve ref` | The installer could not find a release to use. | Check the Pi can reach GitHub, then run Step 6 again. If it still fails, add `--ref` and a release tag from the [Releases page](https://github.com/MrFreekie/stagewatch/releases) to the command. It is safe to re-run. |
| `/opt/stagewatch exists and is owned by ...; refusing.` | Something else made that folder. | Look inside it. If you made it and it holds nothing you need: `sudo rm -rf /opt/stagewatch`, then run Step 6 again. |
| `existing clone has a different origin; refusing.` | `/opt/stagewatch` came from somewhere else. | As above. |
| Clone fails (network error) | The Pi cannot reach GitHub. | Check its internet. Open <https://github.com/MrFreekie/stagewatch> in the Pi's browser. |
| The status shows **failed** or **activating** | Stagewatch would not start. | See [Troubleshooting](../troubleshooting.md#the-page-will-not-load-on-the-pc). It shows how to read the log. |
| Tablet cannot connect | Different network, or a name that does not resolve. | Use the IP address (Step 9). See [Troubleshooting](../troubleshooting.md#the-tablet-cannot-open-stagewatch). |
| The kiosk does not appear after reboot | Auto-login is off, or Chromium is missing. | Redo Step 10. To check the browser: `sudo apt install -y chromium` (older Pi OS: `chromium-browser`). |
| The screen goes black after a while | Screen blanking is on. | Step 10, "Stop the screen going blank". |

---

## Uninstall

Paste this and press Enter:

```bash
bash ~/stagewatch/deploy/pi/uninstall.sh
```

**What you should see:** `Stagewatch service removed.`

This removes the boot service, the kiosk auto-start and the clock button's helper (if you set it up). It **keeps** your data in
`/var/lib/stagewatch` and the program in `/opt/stagewatch`. It also works if you still have
the older Linux install.

To remove the program too:

```bash
sudo rm -rf /opt/stagewatch
```

To also delete your settings, history, PIN and node keys (only if you are sure):

```bash
sudo rm -rf /var/lib/stagewatch
```

---

**Next:** [Build your first sensor node](../nodes/README.md).
