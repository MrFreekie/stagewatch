# Start here

**Stagewatch** is a small program that watches the air at your show site.
Sensor nodes measure temperature, humidity and pressure. Stagewatch averages them,
works out the speed of sound, and shows it on dashboards you can open on a tablet,
a phone or a wall screen.

When you set a marker such as **Aligned** at soundcheck, Stagewatch tells you how much
the sound travel time has drifted since. That is the number that matters for delays and
sub alignment.

> **Please read this once.** Stagewatch is an **advisory tool**. It is **not** a
> certified safety system and it does not replace your riggers, electricians,
> wind-management or power plans. It only reads data. It never controls rigging, power
> or safety kit. It is also **100 % vibe coded** (written by an AI assistant, with no
> human developer review) and comes with **no warranty**. Test it on your own kit
> before a show. The full wording is at the top of the
> [main README](../README.md#stagewatch).

---

## What you need

| You need | Notes |
|---|---|
| **A Windows PC** or **a Raspberry Pi** | This is the "Stagewatch computer". It stays on at the venue. A laptop or mini-PC is fine. |
| **A sensor node** | An Adafruit ESP32-S3 Feather plus a temperature/humidity sensor. The guide shows how to build one. You can start without one and try the fake sensors. |
| **A Wi-Fi network or router** | The Stagewatch computer, the nodes and your tablets must all be on the **same network**. Nodes need **2.4 GHz** Wi-Fi. |
| **Internet, for set-up only** | Needed to download things when you install and update. Not needed during the show. |
| **A tablet or phone** | To look at the dashboards. Any modern browser works. Nothing to install. |

You do **not** need to know how to code. You will copy and paste a few lines.
Every guide tells you what you should see after each step.

---

## The guides, in the order to follow

**Setting up (do this at home or in the office, not at the venue)**

1. Install Stagewatch on the computer:
   - [Install on Windows](install-windows.md)
   - or [Install on a Raspberry Pi](install-raspberry-pi.md)
2. [Build and add your first sensor node](first-sensor-node.md)

**On the day**

3. [Using Stagewatch on show day](using-stagewatch.md). Short and made for a phone.

**Looking after it**

4. [Updating and backups](updating-and-backups.md), including a forgotten PIN
5. [Troubleshooting](troubleshooting.md). Find your symptom, get the fix.
6. [Glossary](glossary.md). Plain-English meanings of the odd words.

---

## Quick answers

- **Where do I open it?** On the Stagewatch computer: `http://localhost:8080`.
  On a tablet: `http://` + the computer's address + `:8080`
  (for example `http://192.168.1.50:8080`).
- **Where is the admin page?** Add `/admin` to the end.
- **Something is wrong right now.** Go to [Troubleshooting](troubleshooting.md).

---

## Honest notes

- The managed install is **tested on Windows 11 and Windows 10 (virtual machines), and on
  Debian 13.7 (virtual machine). It is not yet tested on Raspberry Pi hardware.** Try it at
  home first. Do not meet it for the first time at a festival.
- Updates take a backup first and can be rolled back. See [Updating and backups](updating-and-backups.md).

Stagewatch is free and open source (GPL-3.0). Problems and ideas are welcome on the
[GitHub issues page](https://github.com/MrFreekie/stagewatch/issues).
