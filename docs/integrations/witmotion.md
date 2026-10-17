# WitMotion tilt sensor (Bluetooth)

Reads a **WitMotion WT901BLE** (BLE 5.0) tilt and motion sensor over Bluetooth and shows its roll and pitch, acceleration, battery and temperature.

**Advisory only.** It is a trend for "has it moved since the marker?". It is not a safety system and not a load or array-angle measurement. Stagewatch only listens to the sensor and asks it for its battery and temperature. It never changes a setting on the sensor, never calibrates it, and never pairs with it (the sensor drops the link if you try).

## What you get

| Reading | Notes |
|---|---|
| Tilt roll, tilt pitch | As the sensor reports them, in degrees. Roll wraps at ±180°. Ignore yaw (it drifts). |
| Acceleration | The peak of each second, in g. Good for "was it knocked?", not for vibration analysis. |
| Time since last data | Seconds. |
| Sensor battery | Voltage, plus a percentage that is **approximate** (worked out from the voltage). |
| Sensor body temperature | The sensor chip's temperature, **not the air**. It is never used for the environment cards or the speed of sound. |

All readings are **Equipment**: they never reach a site average. They are stored once a second.

If the link is lost or the sensor goes quiet for 5 seconds, every reading becomes a gap (no value, never the last one, never zero), a **Tilt sensor link lost** marker is made, and a **Tilt sensor back** marker when it returns. Nothing is filled in afterwards.

## Switch it on

There is no Admin page for it yet. Stop Stagewatch, add this to `config.yaml` in the data folder, and start it again:

```yaml
witmotion:
  enabled: true
  sensors:
    - address: "AA:BB:CC:DD:EE:01"
      label: "Stage Right hang"
```

* **`address`** is the sensor's Bluetooth address. Stagewatch finds the sensor by this, not by its name, so it still works if someone renames the sensor. With two or more sensors each needs its own address (up to four).
* **`label`** is the name shown in Stagewatch. It is a Stagewatch setting only: Stagewatch never writes to the sensor, including its name.
* Not sure of the address? Set only `enabled: true` (no `sensors`). Stagewatch then connects to the first sensor whose own name still starts `WT901BLE` (the factory name, for example `WT901BLE67`). Find the address in the Stagewatch log or in a Bluetooth scanner app on your phone, then add it.

Try it without a sensor using `--emulate` (a simulated sensor that drops out for 20 seconds every 5 minutes).

## Raspberry Pi

* Use a Pi with Bluetooth (Pi 3, 4, 5, Zero W/2 W). Wi-Fi and Bluetooth share one antenna on the Pi: **Ethernet, or 5 GHz Wi-Fi, is far more reliable**.
* Range is about 5 to 10 metres, much less through a rack or truss.
* The Stagewatch service user must be allowed to use Bluetooth. This needs `SupplementaryGroups=bluetooth` in the service file (see the checklist in the build notes). The installer does not do this yet.
* Do not pair the sensor in the Pi's Bluetooth tools. Leave it unpaired.
* Only one device can be connected to the sensor at a time: close the WitMotion phone app first.

## Windows

Untested. Windows Bluetooth may not work for a program started at boot with nobody logged in (the SYSTEM boot task). Do not rely on it until it has been tried on real hardware.

## Known limits

* Whether the sensor puts its `ffe5` service in its advertisement is not known (the captures show the services only after connecting), so Stagewatch does not scan by service.
* Checked against real captures of one WT901BLECL (advertises as `WT901BLE67`). The live Bluetooth link has not yet been run on a Raspberry Pi.
* The battery voltage is read as the raw number divided by 100 (411 = 4.11 V). That is an inference from one sensor, not a documented fact. The percentage is a rough lithium-cell curve.
* The WT9011DCL and WIT-WWS01 are not supported yet.
