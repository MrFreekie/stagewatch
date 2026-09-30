# Using Stagewatch on show day

Short guide for crew. No installing here. Someone has already set it up.

> Stagewatch is an **advisory tool**. It does not replace your riggers, electricians or
> wind and power plans, and it does not control anything. It only reads the air.
> See the [main README](../README.md#stagewatch).

---

## Open a dashboard

Your tech will give you an address, like `http://192.168.1.50:8080`.

1. Join the show Wi-Fi.
2. Open the address in your browser. You will see tiles for each dashboard.
3. Tap the one for you.

The default dashboards are:

| Address ending | For |
|---|---|
| `/d/foh` | A tablet at FOH. Can add markers and acknowledge alarms. |
| `/d/phone` | Your phone. Can add markers. |
| `/d/wall` | A wall screen. Read only. |

No login needed to look. Your tech may have renamed them.

**Keep it handy:** bookmark it, or add it to your home screen.
On an iPhone or iPad: tap **Share**, then **Add to Home Screen**.
On Android: tap the browser's **menu** (three dots), then **Add to Home screen**.

---

## What is on the screen

- **Top bar:** the site name, the show name, the time, and a small **dot**.
  - **Green dot** = live. The screen is up to date.
  - **Red dot** = the tablet has lost contact. After a few seconds a red bar
    **Disconnected from Stagewatch - reconnecting...** covers the top and the numbers
    turn grey, so nobody trusts old readings. It goes away by itself when the connection
    returns. See [The tablet says it's disconnected](#the-tablet-says-its-disconnected).
- **Alarm bar:** only appears when something is wrong (see [Alarms](#what-the-alarm-colours-mean)).
- **Tiles:** the five numbers below.
- **History:** a graph. Buttons pick **Temp**, **RH**, **Pressure** or **c** (speed of
  sound) and how far back to look: **15m**, **1h**, **4h**, **12h**. The thick line is
  the site average. Thin lines are single sensors. The vertical lines are **markers**.
- **Markers:** add and read markers.
- **Sensors:** each node, whether it is working, and its latest readings.

---

## What the numbers mean

| Tile | In one line |
|---|---|
| **Temperature** | The **site average** of all the sensors, in °C. Hot air makes sound faster. |
| **Humidity** | How much water is in the air, in % relative humidity (RH). It changes the speed of sound only a little. |
| **Pressure** | Air pressure in hPa. It barely changes the speed of sound, but it is shown for the record. |
| **Speed of sound** | How fast sound is travelling right now, in metres per second (m/s), worked out from the readings. Under it is the time sound takes to go one metre. |
| **Dew point** | The temperature at which water would start to condense out of the air. Close to the air temperature means damp air. |

The **site average** is the combined reading from all your sensors. A sensor that has
stopped reporting is left out. One that is wildly different from the rest is also left out (when
there are three or more sensors). The result is smoothed so it does not jump about.

Some notes you may see under a tile:

- `3 sensor(s) averaged`: how many sensors are in the average.
- `no sensor: 50% assumed`: no humidity sensor is reporting, so Stagewatch assumed 50 %.
- `no sensor: from site altitude`: no pressure sensor is reporting, so it used the
  venue's altitude.

---

## Set an "Aligned" marker at soundcheck

**Why it matters.** Sound travels faster in warm air and slower in cold air. Over a
long throw, the arrival time changes as the day warms up or cools down. On a 30 m
delay path, a 10 °C change is about **1.5 ms**. That can move your alignment.

A **marker** is a bookmark in time. When you drop one at soundcheck, Stagewatch remembers
the temperature, humidity, pressure and speed of sound at that moment. It then keeps
telling you how far things have moved since.

**To set one:**

1. Get your system aligned as you normally do.
2. On your dashboard, find **Markers**.
3. In the box, type **Aligned**.
4. Tap **Add marker**.

**What you should see:** a small message "Marker 'Aligned' added", and **Aligned**
appears in the list with its time.

If you cannot see the box, that dashboard is not allowed to add markers. Use another
dashboard (for example FOH), or ask your tech.

You can add other markers too, for example **Doors** or **Headliner**.

### Read how far it has drifted

1. Tap a marker in the list.
2. A panel opens. It says **Since "Aligned" at 14:02** and shows a big number such as
   **+0.412 ms**.

**How to read it:**

- The big number is the **change in sound travel time** since you set the marker,
  over the **reference distance** (30 m unless your tech set another; the panel says
  which one).
- **Plus (+)** means sound now arrives **later** than at the marker. It is colder or the
  air has changed to make it slower.
- **Minus (-)** means sound now arrives **earlier**. It is warmer.
- **0.000 ms** means nothing has moved.
- The little table under it shows **Temp**, **RH**, **Pressure** and **c** at the marker,
  now, and the change.
- A dash **—** means it cannot work it out (for example no sensor was reporting).

Use it as a guide to decide when a re-check of your delays or sub alignment is worth doing.
Stagewatch does **not** change any of your processors. It only tells you.

The **History** graph shows markers as vertical lines. Tap a line to select it.

---

## What the alarm colours mean

If your tech has set alarm limits, the alarm bar appears at the top of the screen
when a limit is crossed. There are three levels:

| Level | Colour | Meaning |
|---|---|---|
| **Advisory** | Amber (yellow-orange) | Something to be aware of. |
| **Alert** | Orange | Needs attention. |
| **Stop** | Red | Serious. Follow your event's own plan. |

Stagewatch does not decide what you must do. Your event's safety, wind and power plans
do that. See the advisory notice above.

An alarm shows up for two reasons:

- A **limit** was crossed (for example, "Temperature: 36.2 above 35").
- A **node has gone offline**. This shows as an advisory alarm, such as
  "Stage L node: missing - connection lost".

**When an alarm is "sounding"**, the bar pulses and a beep repeats every two seconds.

- **Acknowledge** stops the beeping. The button only shows on dashboards allowed to
  acknowledge (FOH by default). The alarm stays on screen, marked **(acknowledged)**, until
  the problem clears. Acknowledging does **not** fix anything.
- **Alert** and **Stop** alarms also drop a marker on the timeline that starts with **ALARM:**.

### The sound button

The top bar always has a button that says **Alarm sound: On** or **Alarm sound: Off**.
Browsers block sound until you tap something, so **tap it once at soundcheck**. It turns
to **On** and plays a short test beep. Tap it again to mute this tablet. Stagewatch
remembers your choice on that tablet. If it says **Off - tap to turn on** (in orange), the
tablet has not been given permission yet: tap it.

On a wall display in full-screen kiosk mode, sound usually works without tapping, and the
button hides itself once sound works.
Do not rely on beeping being heard on a tablet. Turn the volume up, and keep watching
the colours.

---

## What "stale" and "offline" mean

Every sensor reports every few seconds. If a reading has not been updated for a while,
it is **stale**.

- By default, a reading older than **60 seconds** is stale. The **Sensors** card says
  this at the bottom.
- Stale readings are **greyed out** and are **left out of the site average**.
- The **Status** next to each node shows:
  - **ok** (green): working.
  - **initializing** (grey): just started.
  - **missing** (red): Stagewatch cannot reach it. It has lost power or Wi-Fi.
  - **fault** (red): it is reachable but something is wrong, such as a wrong key.
- The **Updated** column says how long ago each node reported, for example `12s ago`.
- A tile with a dash **—** and grey text has no current value.

If a node goes offline, Stagewatch keeps trying to reconnect. Once it comes back, it is
used again.

---

## The tablet says it's disconnected

That is the **red dot** in the top bar. It means this tablet has lost contact with the
Stagewatch computer. The screen retries by itself every few seconds.

Try in this order:

1. Check the tablet is on the **show Wi-Fi**.
2. Reload the page.
3. Check the Stagewatch computer is on and on the same network.
4. Still stuck? See [Troubleshooting](troubleshooting.md).

---

## Admin tasks, in brief

Admin is for the person who looks after Stagewatch. It needs the **admin PIN**.

Open the address plus `/admin` (for example `http://192.168.1.50:8080/admin`). Log in. Cards
on the page:

| Card | What you do there |
|---|---|
| **Site** | Set the show name, **altitude** (used only if there is no pressure sensor), the **reference distance** for the "Δ ms" number (default 30 m), how quickly readings go **stale** (default 60 s), and the smoothing. **Save site**. |
| **Shows** | Tap **Start new show** at the start of each show day. Give it a name. Dashboards then show only the new history and markers. Old shows stay listed. |
| **Software** | Check for and install updates. See [Updating and backups](updating-and-backups.md). |
| **ESPHome nodes** | Adopt new sensor nodes. See [First sensor node](first-sensor-node.md). Rename a node, change its area, or remove it. |
| **Sensors: calibration & averaging** | Correct a sensor with an **offset**. Untick **Average** to leave one out of the site average. |
| **Threshold alarms** | Add limits. Pick the **Entity** (choose the "Site:" ones for the average), the **Above** or **Below** value, the **Level** (Advisory, Alert, Stop), **Hyst.** (how far back past the limit before it clears) and **Hold s** (how many seconds it must last before it alarms). Values are in the units shown on the dashboards, **except pressure, which is in pascals (Pa)**. Click **Save thresholds**. |
| **User dashboards** | Add or remove dashboards. Each has a URL name, a title, a **Layout** (tablet, phone or wall), and ticks for whether people can **Add markers** and **Ack alarms**. Click **Save dashboards**. |
| **OSC output** | Send the site average and alarm state to other gear (the desk, for example). See the [main README](../README.md#osc-output). |
| **Security** | Change the admin PIN. |
| **Alarm log** | See what alarmed, and when, in this show. |

**Nothing alarms until you add limits.** Out of the box there are no threshold alarms.
There is only the "node offline" alarm.

Changes only stick after you click that card's **Save** button.

Only a person with the admin PIN can delete a marker (the **✕** appears next to markers
when logged in).

---

**Something not right?** [Troubleshooting](troubleshooting.md).
