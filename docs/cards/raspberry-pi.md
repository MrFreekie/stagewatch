# Raspberry Pi

The **Raspberry Pi** card shows the health of the Raspberry Pi that Stagewatch runs on. It answers
one question: "is the computer running the show data OK?". It is not on any dashboard by default,
and it stays hidden unless Stagewatch is running on a Raspberry Pi.

> **Tested on a Raspberry Pi 4 (4 GB).** Other Pi models are not tested yet. Stagewatch only reads these figures. It never changes a Pi
> setting, a fan or a clock, and never restarts the Pi.

Back to [Dashboard cards](README.md).

## What it shows

| Line | What it means |
| --- | --- |
| CPU temperature | The processor's temperature. From 80 °C a Pi 4 starts to slow itself down. |
| Power supply | **OK**, **Under-voltage now** (the power supply or cable is too weak), or **OK now** if it was short of power earlier since it started. **Unknown** means the Pi did not say. |
| Speed | **Full speed**, or **Slowed down now (throttled)** because it is too hot or short of power. |
| CPU load | How busy it is, averaged over 1 minute. Up to 4 is normal on a 4-core Pi. |
| Memory used | How much memory is in use. |
| Disk free | Space left on the drive that holds Stagewatch's data. |
| Wi-Fi signal | In dBm. Nearer 0 is stronger; below about -75 dBm is weak. |
| Running for | Time since the Pi last started. |

A dash (—) means no reading came in. It is never shown as 0.

## Warnings

These show quietly in the dashboard header's messages and never sound:

- short of power (under-voltage);
- slowed down (throttled);
- 80 °C or more (it clears below 77 °C);
- under 1 GB or 5% disk free.

Under-voltage and throttling also add a **marker** when they start and when they stop, so you can
see them on the timeline later.

The readings are recorded as **Equipment**: you can chart them, and they never change a site
average.

## Admin

**Admin → This Raspberry Pi** shows the same figures plus the Pi's network addresses. The
addresses are only on the admin page, never on a dashboard.

The under-voltage and throttling figures come from the Pi's own `vcgencmd` tool. On Raspberry Pi OS
it is installed already. A new install lets the Stagewatch service use it. On a Pi installed with an
earlier version, the card says **Unknown** for the power supply until you run these two commands once
(the second restarts Stagewatch):

```bash
sudo usermod -aG video stagewatch
```

```bash
sudo systemctl restart stagewatch
```
