# ESPHome sensor nodes

**ESPHome** is a free tool for building the program that runs on small Wi-Fi boards. Stagewatch
sensor nodes are boards running an ESPHome program (the project ships ready-made ones, see
[Build a sensor node](../nodes/README.md)). This page says what Stagewatch reads from a node
and how to manage nodes in Admin. For the build steps, use the [node hub](../nodes/README.md).

ESPHome is made by the ESPHome project. Stagewatch is an independent project and is not
approved or supported by them. See the [notices](README.md#notices).

> **Test status.** Several boards have run on real hardware and several have not. The table of
> every node file, with its test status, is in the [node hub](../nodes/README.md#all-the-node-files).

---

## What Stagewatch reads

- **What:** temperature, humidity, pressure, contact (open or closed) inputs, battery level,
  Wi-Fi signal strength and generic numbers that a node reports. It also reads each board's
  MAC address (its hardware number), so that a sensor's calibration stays with the board if the
  board's IP address changes.
- **How:** over ESPHome's own **native API**, on port `6053` of the node. The node sends each
  reading as it is taken, so the dashboards update live.
- **How Stagewatch finds nodes:** it listens for nodes announcing themselves on the network
  (mDNS, how devices find each other by name). That is why a node must be on the **same
  network** as the Stagewatch computer. If your network blocks that, add it by its address with **Add integration** on the **Integrations** tab in Admin.
- **Where it shows:** the tiles, the History chart, the Barometer and the Equipment card. See
  [Dashboard cards](../cards/README.md).

In **Admin**, the **Integrations** card lists ESPHome with a small tag, **Local, live push**:
the node pushes each reading to Stagewatch as it takes it, on your own network, with nothing
sent to the internet.

## Read only

**Stagewatch only reads from a node. It never sends a command to one.** It does not switch,
restart or change anything on a node.

ESPHome's native API offers **every part of a node** to whatever connects to it. That includes
any switch, light or button a node program defines, not only the sensors. If a node file has
controls like that, they are offered over that link too. Stagewatch does not use them.
Anything else that connects to the node could. That is a reason to keep the encryption key
private and the show network closed.

Stagewatch is a guide. It is not a certified monitoring system, and a node reading is not a
calibrated record. Check each node against a reference you trust.

---

## Adding a node

The full walk-through, with the key and the file, is [Step 7 in the node hub](../nodes/README.md#step-7-add-the-node-in-stagewatch).
In short, in **Admin**, on the **ESPHome nodes** card:

1. Under **Discovered node**, click **Adopt…** next to your node. A pop-out opens.
2. Paste the **Encryption key**, type the **Area**, and choose the **Role**.
3. Click **Adopt**.

When the node is not listed, add it by its address: on the **Integrations** tab in Admin, click **Add integration**, choose **ESPHome node**, and fill in the same boxes.

### The encryption key

Each node should have its own **encryption key**. It locks the link between the node and
Stagewatch, so nothing else on the network can read or use it. You make it when you build the
node (hub [Step 3](../nodes/README.md#step-3-get-the-node-files-and-fill-in-your-secrets)) and paste the same
key into Stagewatch when you adopt the node. Stagewatch never shows the key again, so keep a
copy. A node that is set up for a key shows **encrypted** in the Discovered list, and Stagewatch
then requires the key.

### Roles: Environment and Equipment

Every node has a **Role**.

| Role | Use it for | What happens to its readings |
|---|---|---|
| **Environment** (the default) | Sensors that measure the air at the site | Feed the site average, the speed of sound and the barometer |
| **Equipment** | Gear: amp racks, fans, power supplies | Shown on the **Equipment** card. **Never** averaged into the site |

A reading about equipment must not be able to move a site value. So set a rack temperature
node (such as the TMP117) to **Equipment** **before** you adopt it. You can change the role
later under **Adopted**, then click **Save**.

### Managing adopted nodes

Under **Adopted** on the same card you can change a node's **Name**, **Area** and **Role**,
set **Sleeps between readings**, click **Save**, or click **Remove** (history is kept). Under
**Discovered node**, **Ignore** hides a node you do not want listed; **Show ignored** brings
it back. Ignoring only hides it from the list.

A node's **Status** reads **ok** when readings are arriving. If it reads **missing**,
**fault** or **initializing**, see [Troubleshooting](../troubleshooting.md#sensor-node-problems).

---

## Sleeping nodes

Some battery nodes switch themselves off (deep sleep) between readings, so they are out of
contact most of the time. Without help, Stagewatch would show them as stale and missing, and
raise a "node offline" alarm, between every wake-up.

1. In **Admin → ESPHome nodes**, under **Adopted**, find the node.
2. Type the number of minutes the node sleeps in **Sleeps between readings**.
3. Click **Save**.

Leave it empty for a node that stays on. Nothing changes for those.

- While the node is out of contact but its last reading is no older than **2.5 times that
  interval** (rounded up to whole minutes, so 5 minutes gives 13 minutes), the node and its
  sensors say **Sleeping**, and the hover text gives the age of the last reading. No offline
  alarm is raised, and the value shown is the last real reading, not a guess.
- After that limit it shows stale and missing as usual, and the offline alarm is raised.
- Until a sleeping node has given Stagewatch a reading, it is shown as missing like any other
  node. Stagewatch does not assume it is asleep.
- Within the limit the reading still counts in the site average (Environment sensors), as it
  did before. The Environment and Equipment rules are unchanged.

**Not yet tested on a real deep-sleep node.** It has only been checked with simulated times.
Tell us how it behaves on yours. None of the node files that come with Stagewatch use deep
sleep.

If you go back to an older version of Stagewatch, it ignores the setting and treats the node as
always on.

---

## If it didn't work

See [Troubleshooting](../troubleshooting.md#sensor-node-problems) and the table at the end of
the [node hub](../nodes/README.md#if-it-didnt-work).

---

[Back to Integrations](README.md)
