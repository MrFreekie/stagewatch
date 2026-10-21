# Stagewatch guides

**Stagewatch** is a small program that watches the air at your show site.
Sensor nodes measure temperature, humidity and pressure. Stagewatch averages them,
works out the speed of sound, and shows it on dashboards you can open on a tablet,
a phone or a wall screen.

When you set a marker such as **Aligned** at soundcheck, Stagewatch tells you how much
the sound travel time has drifted since. That is the number that matters for delays and
sub alignment.

> **Please read this once.** Stagewatch is an **advisory tool**, not a certified safety system.
> It only reads data and never controls rigging, power or safety kit. It was written by an AI
> assistant with no human code review, and has no warranty. Test it on your own kit before a show.
> The full notice is in [Notices](integrations/README.md#notices).

## What you need

| You need | Notes |
|---|---|
| **A computer that stays on** | A Windows PC, a Raspberry Pi, or a Debian Linux PC. A laptop or mini-PC is fine. |
| **A sensor node** | A small Wi-Fi sensor box. The guide shows how to build one. You can start without one and try the fake sensors. |
| **A Wi-Fi network or router** | The Stagewatch computer, the nodes and your tablets must all be on the **same network**. Nodes need **2.4 GHz** Wi-Fi. |
| **Internet, for set-up only** | Needed to download things when you install and update. Not needed during the show. |
| **A tablet or phone** | To look at the dashboards. Any modern browser works. Nothing to install. |

You do not need to know how to code. Every guide tells you what you should see after each step.

## Quick answers

- **Where do I open it?** On the Stagewatch computer: `http://localhost:8080`.
  On a tablet: `http://` + the computer's address + `:8080`, for example `http://192.168.1.50:8080`.
- **Where is the admin page?** Add `/admin` to the end. See [Admin](admin/README.md).
- **Something is wrong right now.** Go to [Troubleshooting](troubleshooting.md).

## The guides, in the order to follow

**Set up (at home or in the office, not at the venue)**

1. Install Stagewatch on the computer:
   [Windows](install/windows.md), [Raspberry Pi](install/raspberry-pi.md) or [Debian Linux](install/debian-linux.md).
2. [Networking](networking.md): one network, or the sensor nodes on their own Wi-Fi.
3. [Build and add a sensor node](nodes/README.md).
4. [Connect other kit](integrations/README.md): Ontime, Smaart, DirectOut GLOBCON, Shelly power meters, WitMotion tilt sensors, OSC output.
5. [Set it up in Admin](admin/README.md).

**On the day**

6. [Dashboard cards](cards/README.md): what each card shows and what the colours mean.

**Looking after it**

7. [Updating and backups](updating-and-backups.md), including a forgotten PIN.
8. [Troubleshooting](troubleshooting.md). Find your symptom, get the fix.
9. [Glossary](glossary.md). Plain meanings of the odd words.

## Honest notes

- The managed install is **tested on Windows 11 and Windows 10 (virtual machines), on
  Debian 13.7 (virtual machine) and on a Raspberry Pi 4 (4 GB), by the project owner in
  October 2026. It is not tested on a Raspberry Pi 5, or with the Pi kiosk.** Try it at
  home first. Do not meet it for the first time at a festival.
- Which integrations have been tried on real kit is in one table: [Integrations at a glance](integrations/README.md#at-a-glance).
- Updates take a backup first and can be rolled back. See [Updating and backups](updating-and-backups.md).

Stagewatch is free and open source (GPL-3.0). Problems and ideas are welcome on the
[GitHub issues page](https://github.com/MrFreekie/stagewatch/issues).
Developers: see [CONTRIBUTING.md](../CONTRIBUTING.md).