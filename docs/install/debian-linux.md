# Install Stagewatch on Debian Linux

This puts Stagewatch on an ordinary Debian PC or server so that it **starts at boot**,
restarts itself if it stops, and can be updated from a web page. There is no full-screen
kiosk: use a browser on any tablet or PC to look at the dashboards.

Time needed: about 30 minutes, mostly waiting.
Do this **at home or in the office**, with internet. Not at the venue.

> **Tested on hardware: pending.** This is tested in a **Debian 13.7 virtual machine only**.
> It has not been tried on a real Debian PC. Other Debian-based systems (Ubuntu and so on)
> have not been tried. Try it on your own machine before you rely on it at a show.
> Stagewatch is an advisory tool with no warranty. See the [Notices](../integrations/README.md#notices).

It uses the same installer as the Raspberry Pi. For a Pi with a screen, follow
[Install on a Raspberry Pi](raspberry-pi.md) instead.

You will need:

- A Debian PC with internet, and a user who can run `sudo`.
- About 1 GB of free disk space.

---

## Step 1. Install Git and uv

In a Terminal, paste this and press Enter. It may ask for your password. You will not see
letters as you type it. That is normal.

```bash
sudo apt update && sudo apt install -y git curl python3
```

Then install `uv` (a free tool that fetches the right Python for Stagewatch):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close the Terminal and open a new one, then check both tools:

```bash
git --version && uv --version
```

**What you should see:** two lines, one starting `git version` and one starting `uv`.

## Step 2. Download Stagewatch and run the installer

Run the installer as your **normal user**, not as root. It uses `sudo` itself.

```bash
git clone https://github.com/MrFreekie/stagewatch.git ~/stagewatch
cd ~/stagewatch && bash deploy/pi/install.sh
```

This takes **several minutes**. The installer picks the **latest release** for you. The
options (`--ref`, `--channel`, `--port`, `--source-url`) are in the table in the
[Raspberry Pi guide](raspberry-pi.md#step-6-run-the-installer). Leave `--kiosk` off.

**What you should see near the end:**

```text
Service enabled (tested on a Raspberry Pi 4 (4 GB), owner report 2026-10; not tested on Pi 5 or with the kiosk): sudo systemctl status stagewatch
Code: /opt/stagewatch (...)   Data: /var/lib/stagewatch (0700, user stagewatch)
Done. Open http://YOUR-PC-NAME.local:8080 from a tablet on the same network.
```

The text in brackets is about the Raspberry Pi. It does not matter on a PC.

The program goes in `/opt/stagewatch`. Your data goes in `/var/lib/stagewatch`, which only
`sudo` can read. More in [Where your data lives](../updating-and-backups.md#where-your-data-lives).

## Step 3. Check it, and set your admin PIN

1. Run `sudo systemctl status stagewatch`. **What you should see:** **active (running)**. Press **q** to leave.
2. In a browser on the PC, go to `http://localhost:8080/admin`.
3. It says **Welcome: set an admin PIN**. Choose a PIN of **at least 4 characters** and type it twice.
4. Click **Set admin PIN**.

> **Do this straight away.** Until a PIN is set, **anyone on the network** who opens `/admin` can set it.
> More in [Security and data](../admin/security-and-data.md).

## Step 4. Open it from a tablet

1. Put the tablet on the **same network** as the PC.
2. Find the PC's address: run `hostname -I`. The first number, like `192.168.1.50`, is the address.
3. On the tablet, type `http://192.168.1.50:8080` (your own address).

**What you should see:** the Stagewatch page with the FOH, Phone and Wall dashboards.

The installer does **not** change any firewall. If tablets cannot connect and the PC runs a
firewall (such as `ufw`), allow **TCP 8080** (the web page) and **UDP 5353** (so tablets and
nodes can find Stagewatch by name).

## How to check it worked

- Step 3 shows **active (running)** and the admin page opens.
- **Admin > Software** says **Install: Managed**.
- Restart the PC. The page is back within a minute without you starting anything.

## If it didn't work

The messages are the same as on a Pi. See the table in
[Install on a Raspberry Pi](raspberry-pi.md#if-it-doesnt-work) (ignore the kiosk and screen rows). For
problems once it is running, and for where the log files are, see
[Troubleshooting](../troubleshooting.md).

## Try it without any sensors (optional)

To look around with **fake** sensors first, in the `~/stagewatch` folder run
`uv run stagewatch --emulate`, then open `http://localhost:8080`. A banner says
**EMULATE MODE**. Its data is kept apart from a real install. Stop the boot service first
(`sudo systemctl stop stagewatch`), because both use port 8080.

## Uninstall

```bash
bash ~/stagewatch/deploy/pi/uninstall.sh
```

**What you should see:** `Stagewatch service removed.` Your data in `/var/lib/stagewatch` and
the program in `/opt/stagewatch` are kept. To delete them too, see
[Uninstall on a Pi](raspberry-pi.md#uninstall).

---

**Next:** [Build your first sensor node](../nodes/README.md).
