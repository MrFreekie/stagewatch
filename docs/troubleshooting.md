# Troubleshooting

Find what you see in the left column, then try the fix on the right. Work down each
list in order.

> Stagewatch is an advisory tool. If it stops working, carry on with your event's normal
> monitoring. See the [Notices](integrations/README.md#notices).

Jump to: [Install problems](#install-problems) |
[Cannot open the page](#cannot-open-the-page) |
[Dashboard problems](#dashboard-problems) |
[Sensor node problems](#sensor-node-problems) |
[Alarms](#alarms) | [Updates and PIN](#updates-and-pin) |
[Where the log files are](#where-the-log-files-are)

---

## Install problems

Each install guide ends with an **If it doesn't work** table for its own error messages:

- [Windows](install/windows.md#if-it-doesnt-work): Administrator PowerShell, Git, uv, release not found, SmartScreen.
- [Raspberry Pi](install/raspberry-pi.md#if-it-doesnt-work): uv, Git, release not found, kiosk.
- [Debian Linux](install/debian-linux.md#if-it-didnt-work): the same installer as the Pi, without the kiosk.

If **Admin → Software** shows **Nightly** and you want **Stable**, see [Updating and backups](updating-and-backups.md#stable-or-nightly-use-stable).

---

## Cannot open the page

### The page will not load on the PC

That means the browser on the Stagewatch computer itself shows "can't connect" for
`http://localhost:8080`.

1. **Wait a minute** after a restart. Stagewatch takes 20 to 60 seconds to start.
2. **Is it running?**

   *Windows:* open PowerShell as Administrator and paste:

```powershell
Get-ScheduledTask -TaskName "Stagewatch" | Select-Object TaskName, State
```

   It should say **Running**. If it says **Ready**, start it:

```powershell
Start-ScheduledTask -TaskName "Stagewatch"
```

   *Raspberry Pi:* in a Terminal paste:

```bash
sudo systemctl status stagewatch
```

   It should say **active (running)**. To restart it: `sudo systemctl restart stagewatch`.

3. **Is something else using port 8080?** On Windows, paste this in an Administrator
   PowerShell. If it lists a process that is not Stagewatch, close that program.

```powershell
netstat -ano | findstr :8080
```

4. **Read the log.** See [Where the log files are](#where-the-log-files-are). The last
   lines usually say what is wrong.
5. **Restart the computer.** Stagewatch starts by itself on boot.

### The tablet cannot open Stagewatch

The page works on the PC, but not on the tablet or phone.

1. **Same network?** The tablet must be on the **same Wi-Fi** as the Stagewatch computer.
   Not a guest network. Not mobile data. Turn off any VPN on the tablet.
2. **Type it exactly, or scan it.** Open the admin page on the PC: the **Connect a tablet**
   card shows the exact address and a QR code for every dashboard. Or type `http://` then
   the PC's address, then `:8080`, for example `http://192.168.1.50:8080`. Not `https`.
3. **Is the address still right?** Routers can change it after a restart. Look it up again
   (Windows: `ipconfig` and find **IPv4 Address**; Pi: `hostname -I`). Ask the router's
   owner for a fixed address for that computer.
4. **Windows only: is the network set to Private?** The firewall rule that lets tablets in
   only works on **Private** networks. Paste this in an Administrator PowerShell:

```powershell
Get-NetConnectionProfile
```

   If **NetworkCategory** says **Public**, change it in **Settings** > **Network & internet**
   > your connection > **Network profile type** > **Private network**
   ([Windows guide, Step 6](install/windows.md#step-6-set-the-network-to-private)).
   **More than one network card?** Each network has its own setting: check the line for the
   network the tablet is on. If it says **Unidentified network** and Settings won't change it, see
   [PCs with more than one network](install/windows.md#pcs-with-more-than-one-network).
5. **Windows only: is the firewall rule there?** Paste:

```powershell
Get-NetFirewallRule -DisplayName "Stagewatch*" | Select-Object DisplayName, Enabled
```

   You should see **Stagewatch web (TCP 8080)** and **Stagewatch mDNS (UDP 5353)**, both
   **True**. If not, run the installer again. It is safe to re-run.
6. **Router settings.** "Client isolation", "AP isolation" or a separate "guest" network
   stops devices seeing each other. Turn it off for your show network.
7. **Third-party antivirus or firewall** on the PC can block it. Add an exception for
   port 8080.
8. **Try another device.** If a phone works but the tablet does not, the problem is on the
   tablet.
9. **`stagewatch.local` does not work?** Use the IP address instead. `.local` names do not
   work on every device.

---

## Dashboard problems

| What you see | Try this |
|---|---|
| Red bar **Disconnected from Stagewatch - reconnecting...**, numbers grey | The tablet lost contact (it appears after about 3 seconds). It retries by itself and the bar goes away when it is back. Check the Wi-Fi, then reload the page. If the PC restarted, wait a minute. |
| The page shows tiles, but every number is **—** | No sensor is reporting. Check your nodes (below). |
| Numbers are **grey** | Readings are older than the stale time (60 s by default). The node has lost power or Wi-Fi. |
| No **Add marker** box | That dashboard is not allowed to add markers. Use the FOH dashboard, or ask the admin to tick **Add markers** for it. |
| No **Acknowledge** button | Only shows while an alarm is sounding, and only on dashboards allowed to acknowledge. |
| No alarm sound | Sound needs a tap. Tap **Alarm sound** in the top bar until it says **On** (you hear a short test beep). If it says **Off**, it is muted on that tablet. Also check the tablet volume and silent switch. |
| The dashboard address just bounces back to the home page | There is no dashboard with that name. Use the tiles on the home page. |
| The page only says **This browser is too old for Stagewatch** | That device's browser is too old. Dashboards need an iPad or iPhone on iOS 12 or later, or a recent Chrome, Edge, Firefox or Safari. The admin page needs iOS 13 or later. Use another device, or update this one. |
| An **EMULATE MODE** banner at the top | This is a demo with fake sensors, not the real install. |
| Times are wrong | The times come from the Stagewatch computer. On a Raspberry Pi with no internet, its clock may be out ([Pi guide](install/raspberry-pi.md#the-pi-has-no-clock-battery)). |
| Temperature or speed of sound looks silly | Check each sensor in the **Sensors** card. One sensor in the sun or by a lamp can be wrong. Untick **Average** for it in Admin, or move it. |
| The **Barometer** says **No pressure sensor** | No pressure sensor is reporting. Add a node with a BME280 (see [Build a sensor node](nodes/README.md)) and check it shows in the **Sensors** card. |
| The **Barometer** says **Collecting** or **Pressure gap** | It needs 3 hours of unbroken readings for the 3-hour change and the hint. A gap of more than 3 minutes (a sensor or Stagewatch stopped) starts the wait again. It does not guess what it missed. The card says when it will be ready. |
| The **Barometer** reads a few hPa away from the Met Office figure | Check the **Altitude** in **Admin → Site**, or use **Set altitude from today's sea-level pressure…** there. The card uses the measured temperature, so a hPa or so of difference from an airport figure is normal. |

---

## Sensor node problems

See also the table at the end of [Build a sensor node](nodes/README.md#if-it-didnt-work).

| What you see | Try this |
|---|---|
| Node not in the **Discovered** list in Admin | Same Wi-Fi as the Stagewatch computer, and 2.4 GHz. Check `wifi_ssid` and password. Turn off client isolation. Use **Add by host / IP** instead. |
| Node status **fault**, "encryption key missing or wrong" | The key pasted into Stagewatch is not the one on the node. Use the exact key from `secrets.yaml`. |
| Node status **missing**, "connection lost" | The node lost power or Wi-Fi. Check the USB power and the distance to the access point. Stagewatch reconnects by itself. |
| Node status **initializing** for a long time | It is still connecting. Wait a minute, then check its power and Wi-Fi. |
| Readings are all **—** but the node is **ok** | The sensor is not being read. Check the STEMMA QT cable and the node's log. |
| TFT screen says **NO SENSOR** | Sensor cable not fully in, or a faulty cable. |
| Temperature reads too high | Sensor too close to the board, sun or lamps. Move it out on the cable, then use an **Offset** in Admin. |
| Two nodes fight or keep dropping | They have the same `node_name`. Give each a different one and re-flash. |

---

## WitMotion tilt sensor problems

See [WitMotion tilt sensor](integrations/witmotion.md) for the setup. Never pair the sensor: Stagewatch does not need it and the sensor drops the link if it is paired.

| What you see | Try this |
|---|---|
| The Tilt card says **No data**, or the device status is **Sensor not found** | Is the sensor switched on and charged? Is the WitMotion phone app closed (only one device can connect at a time)? Is it within a few metres, and not behind a rack or truss? If you set an `address`, check it is the sensor's own address (the log shows it when you leave `sensors` out). A sensor you have renamed is not found by name: it needs its `address` in `config.yaml`. |
| The link keeps dropping, or the log mentions **GATT 147** (also shown as 0x93) | The sensor was probably paired. Remove it from the computer's Bluetooth settings (`bluetoothctl remove` followed by its address on a Pi) and do not pair it again. If it was never paired, it is likely out of range or in a crowded 2.4 GHz area: move the Pi nearer, use Ethernet instead of 2.4 GHz Wi-Fi, or add a short USB Bluetooth adaptor on a lead. |
| Status **Bluetooth problem** on a Raspberry Pi | The Stagewatch service may not be allowed to use Bluetooth. Add `SupplementaryGroups=bluetooth` to the service (the steps are under Raspberry Pi in [WitMotion tilt sensor](integrations/witmotion.md)), and check `systemctl is-active bluetooth` says `active`. |
| The log says the Bluetooth library is not installed | Update Stagewatch to a version that includes the WitMotion integration, or run `pip install bleak` in its Python. |
| Tilt shows after a few seconds and goes blank for a moment after each reconnect | Normal. The smoothing starts again after every gap and shows nothing until three new readings arrive. |
| Battery shows **not available for this sensor model** | The WT9011DCL sends a battery number Stagewatch cannot read as a voltage. Nothing is wrong. |
| A small-print value says **unknown** | The sensor did not answer that question at the last connection. Stagewatch never guesses. Reconnecting asks again. |
| Nothing works on Windows | Windows Bluetooth is untested for the boot install (no one logged in). See [WitMotion tilt sensor](integrations/witmotion.md#windows). |

---

## Alarms

| What you see | Try this |
|---|---|
| An alarm stays on after I press Acknowledge | Acknowledge only silences it. The alarm clears when the reading moves back past the limit by the **Hyst.** amount. |
| An alarm "Node: missing - connection lost" | A node is offline. See the node problems above. |
| I never get alarms | Out of the box there are **no limits** set. An admin must add them in **Threshold alarms**. |
| An alarm does not clear or never triggers on stale data | Stale readings do not raise or clear limit alarms. A node going offline gives its own alarm instead. |

---

## Updates and PIN

| What you see | Try this |
|---|---|
| Software card: "In-app updates are only available on a managed install" | This copy was not installed with the installer. Install it with the [Windows](install/windows.md), [Raspberry Pi](install/raspberry-pi.md) or [Debian Linux](install/debian-linux.md) guide. |
| "Update source not reachable (repository is private or offline)" | The PC has no internet, or cannot reach GitHub. On a Raspberry Pi or Linux PC that has moved to a different network, also check it can look up web addresses: `ping github.com`. |
| Update history says **refused (unsafe_permissions)** | Stagewatch will not update a program folder that someone other than its owner could change. On a Raspberry Pi or Linux PC, run the installer again from your Stagewatch folder (`bash deploy/pi/install.sh`, plus `--kiosk` if you use it). It fixes the folder permissions and keeps your data. Then tap **Check for updates** again. On Windows, run the installer again too. |
| Page stuck on "Stagewatch is restarting for an update" | Wait 2 minutes, reload, then see [Updating and backups](updating-and-backups.md#what-to-do-if-an-update-goes-wrong). |
| "Too many attempts; wait a minute" | Too many wrong PINs. Wait a minute. |
| Forgot the admin PIN | [Reset it](updating-and-backups.md#forgotten-pin-or-recovery-required) with the ready-made reset script (one command). |
| **Recovery required** on the admin page | [Same fix](updating-and-backups.md#forgotten-pin-or-recovery-required). |

---

## Where the log files are

Logs are text files where Stagewatch writes what it is doing, and what went wrong.
They are the first thing to look at when something breaks.

### Windows (boot install)

The folder is `C:\ProgramData\Stagewatch\logs`. **Only Administrators can open it.** It is
also hidden from the normal Explorer view unless you type the path in.

The files:

| File | What is in it |
|---|---|
| `stagewatch.log` | The main log. Start here. |
| `launcher.log` | The part that starts Stagewatch, restarts it, and does updates. Look here after an update or a crash. |
| `server-console.log` | Error text from Stagewatch's start-up. |

**To read the last 60 lines:** open PowerShell **as Administrator** and paste:

```powershell
Get-Content "C:\ProgramData\Stagewatch\logs\stagewatch.log" -Tail 60
```

**To open the whole file in Notepad:** in the same Administrator PowerShell paste:

```powershell
notepad "C:\ProgramData\Stagewatch\logs\stagewatch.log"
```

(Change `stagewatch.log` to `launcher.log` for the other files.)

### Raspberry Pi (managed install)

The data folder is only readable with `sudo`. In a Terminal paste:

```bash
sudo tail -n 60 /var/lib/stagewatch/logs/stagewatch.log
```

To see what the system says about the service:

```bash
sudo journalctl -u stagewatch -n 60 --no-pager
```

The same three files live in `/var/lib/stagewatch/logs/`.

### The demo copy (fake sensors)

If you started it yourself with `--emulate`, run this in its folder to find the data folder.
The logs are in a `logs` folder inside it:

```powershell
uv run stagewatch --emulate --print-data-dir
```

### Sending a log to someone

The easiest way: sign in to the admin page and press **Download diagnostics**. It saves one zip
file with the version, recent logs, the device list and your settings. **It contains no
passwords or keys** (PINs, encryption keys and passwords are removed before it is made). Send
this file when asking for help.

If you send raw log files instead:

Logs can include your node names and network addresses. Look through them first. They should
**not** contain PINs or keys. Remove anything you would not want strangers to see.

### A sensor node's log

Use **Logs** on the node's card in ESPHome Device Builder, or run `esphome logs` with the
node's file (see [Build a sensor node, Step 6](nodes/README.md#step-6-check-the-boot-log)).
