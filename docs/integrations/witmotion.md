# WitMotion tilt sensor (Bluetooth)

Stagewatch can read a **WitMotion tilt sensor** over Bluetooth and show its roll and pitch, its movement, its battery and its temperature. The readings appear on the [Tilt (WitMotion)](../cards/witmotion-tilt.md) card.

> **A guide, not a safety system.** Tilt is a way to see "has it moved since the marker?" and to help set a hang. It is not a load or array-angle measurement and it never replaces the rigger's own checks or any certified system. Stagewatch only listens to the sensor and asks it for a few figures. It never changes a setting on the sensor, never calibrates it and never renames it.

## Which sensors

| Sensor | How it was checked | Notes |
|---|---|---|
| **WitMotion WT901BLECL** (BLE 5.0), advertises as `WT901BLE67`, firmware seen `10080.1.8` | Real captures of one sensor | The battery voltage and the sensor temperature are read. |
| **WitMotion WT9011DCL** (BLE 5.0), advertises as `WT901BLE68`, firmware seen `13111` | Real captures of one sensor | It sends data in the same way. Its **battery shows "not available for this sensor model"**, because the number it sends cannot be read as a voltage. Its compass looked stuck, so do not rely on its yaw. Stagewatch does not use yaw anyway. |

Other WitMotion Bluetooth sensors may work if they use the same protocol, but have not been tried. The WIT-WWS01 temperature and humidity sensor is **not** supported.

## Never pair it

**Do not pair or bond these sensors, on any computer.** The sensor rejects pairing and drops the Bluetooth link. Stagewatch never asks to pair: it only connects and listens. If you have paired a sensor in a Bluetooth settings window, remove it there.

Only **one device at a time** can be connected to the sensor. Close the WitMotion phone app before Stagewatch connects.

## What you get

| Reading | Notes |
|---|---|
| Tilt X (roll) and Y (pitch) | In degrees, as the sensor reports them, smoothed (see below). Roll wraps at ±180°. |
| Acceleration | The peak of each second, in g. Good for "was it knocked?", not for vibration analysis. |
| Time since last data | In seconds. |
| Sensor battery | Voltage, plus a percentage that is **approximate** (worked out from the voltage). "Not available for this sensor model" on the WT9011DCL. |
| Sensor body temperature | The temperature of the sensor's own chip, **not the air**. It is never used for the environment cards or the speed of sound. |

All readings are **Equipment**: they never reach a site average. They are stored once a second.

If the link is lost, or the sensor goes quiet, every reading becomes a gap (no value, never the last one, never zero). A **link lost** marker is made when it goes and a **back** marker when it returns. Nothing is filled in afterwards. The sensor counts as quiet after 5 seconds without data (longer if the sensor sends slowly: at least three times the gap between its frames).

## Switch it on

There is no Admin page for it yet. Stop Stagewatch, add this to `config.yaml` in the data folder, and start it again:

```yaml
witmotion:
  enabled: true
  sensors:
    - address: "AA:BB:CC:DD:EE:01"
      label: "Stage Right hang"
      axis: both          # both, x or y
      smoothing_s: 2      # 0 (off), 1, 2, 5 or 10
      target_x: 2.0       # optional target angle for X (roll), in degrees
      target_y: -2.0      # optional target angle for Y (pitch)
      on_target_deg: 0.2  # "On target" within this many degrees
      close_deg: 1.0      # "Close" within this many degrees
```

* **`address`** is the sensor's Bluetooth address. **Stagewatch finds the sensor by this address, not by its name**, so it still works if someone renames the sensor (the sensor's name can be changed in the WitMotion app). With two or more sensors each one needs its own address (up to four).
* **`label`** is the name shown in Stagewatch ("Stage Right hang"). It is a Stagewatch setting only, kept apart from the sensor's own name.
* **`axis`** is the tilt to watch: `x` (roll, side to side), `y` (pitch, front to back) or `both` (the default). An axis you do not watch is not recorded and cannot be used in a threshold. X and Y are the sensor's own axes. Which way each points in the room depends on how the sensor is fixed, so tilt it and see which reading moves.
* **`smoothing_s`** removes noise from the tilt (default 2 seconds; 0 turns the averaging off). Each reading first goes through a short median of the last five frames, which throws away a single spike, then a moving average over that many seconds. It works in seconds, so it behaves the same whatever rate the sensor sends at. **Thresholds act on the smoothed value.** The unsmoothed values are recorded beside it, labelled "unsmoothed", so the raw history is never changed. After any lost link the smoothing starts again from nothing: nothing is carried across the gap, and no tilt is shown until three new readings have arrived.
* **`target_x`, `target_y`** (optional) are target angles in degrees for X and Y. **`on_target_deg`** (default 0.2) and **`close_deg`** (default 1.0) set the bands: within the first is **On target**, within the second is **Close**, beyond that is **Off target**. The card shows the target, the difference and a marker, using the smoothed angle. This is display only and **never raises an alarm**. If you want an alarm, set an ordinary [threshold](../admin/sensors-and-alarms.md) on the tilt reading.

### Not sure of the address?

Set only `enabled: true` and leave out `sensors`. Stagewatch then connects to the first sensor whose own name still starts `WT901BLE` (the factory name, for example `WT901BLE67`) and writes its name and address to the log. Copy the address into `sensors` as above. This only works while the name has not been changed, and it is a first try, not a way to run two sensors.

Try it without a sensor using `--emulate`. You get one simulated sensor that loses its link for 20 seconds every 5 minutes.

## What the small print means

Each time Stagewatch connects, it asks the sensor how it is set up (the return rate, bandwidth, install direction and axis mode). It only reads. It never changes these. The answers show on the card as small print, for example "2 Hz · 20 Hz bandwidth · horizontal · 6-axis", with the battery and the sensor body temperature on the line below.

* **unknown** means the sensor did not answer that question, or gave an answer Stagewatch does not recognise. Stagewatch never guesses.
* Return rate codes `0x01` and `0x0A`/`0x0B` are marked **not confirmed**, because WitMotion's two protocol pages disagree about them.
* The firmware version cannot be read over Bluetooth, so it is not shown.
* If the sensor sends so slowly that Stagewatch could not tell a quiet sensor from a lost link, the card shows a warning. Raise the return rate in the WitMotion app.
* These figures are information only. They are not recorded and no alarm uses them. They are read again at every connection, so a change made in the phone app shows up after the next reconnect.

## Raspberry Pi

* Use a Pi with Bluetooth (Pi 3, 4, 5, Zero W or Zero 2 W). Wi-Fi and Bluetooth share one antenna on the Pi, so **Ethernet, or 5 GHz Wi-Fi, is far more reliable** than 2.4 GHz Wi-Fi.
* **Range is about 5 to 10 metres in the open, and much less near metal.** A rack, a flightcase or a truss can cut it to a few metres, and a full audience soaks up the signal too. If the sensor is further away than that, use a short USB Bluetooth adaptor on an extension lead, or move the Pi closer.
* **Let the Stagewatch service use Bluetooth.** The installer does not do this yet, so on a Pi installed with the managed installer, run this once:

  ```bash
  sudo systemctl edit stagewatch
  ```

  In the editor, add these two lines, save, and close it:

  ```
  [Service]
  SupplementaryGroups=bluetooth
  ```

  Then restart Stagewatch:

  ```bash
  sudo systemctl restart stagewatch
  ```

* Check Bluetooth is running with `systemctl is-active bluetooth`. It should print `active`.

## Windows

**Untested.** The Windows boot install runs Stagewatch as a background task with nobody logged in, and Windows Bluetooth may not work for a program started that way. Do not rely on it until it has been tried on real hardware. If it does not work, a small ESP32 Bluetooth bridge near the sensor is the planned alternative.

## If it does not work

See [Troubleshooting: WitMotion tilt sensor](../troubleshooting.md#witmotion-tilt-sensor-problems).

## Known limits

* The Bluetooth link has been tried against real captures only. It has **not yet been run on a Raspberry Pi** with a real sensor.
* Whether the sensor puts its Bluetooth service in its advertisement is not known, so Stagewatch does not scan by service.
* The battery voltage is read as the raw number divided by 100 (411 means 4.11 V). That is worked out from one sensor, not documented by WitMotion. The percentage is a rough lithium-cell curve.
* Up to four sensors. More than two or three has not been tried.
