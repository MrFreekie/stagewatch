# Shelly power meters

**Shelly** meters are small Wi-Fi power meters made by Allterco Robotics. Stagewatch can read watts,
volts, amps, frequency and energy from them and show them on the [Power card](../cards/power.md), so
you can see whether a rack or circuit is drawing what you expect.

This page is for the person who sets Stagewatch up. It covers adding a meter and what Stagewatch
sends. What the card shows is on the card page.

Stagewatch is independent. It is not made, approved or supported by Allterco Robotics or Shelly. See
the [notices](README.md#notices).

> **Test status.** First version. It reads by **HTTP polling only** (Stagewatch asks the meter) and
> has been run live on **one Shelly PM Mini Gen3 only**. The first-generation Shelly EM is checked
> against a saved reply from a real one. Other models are read by what they report but
> are **not yet tested**. That includes the Shelly EM Mini Gen4 and the Shelly 3EM. Check their
> figures against the Shelly's own page.

> **Advisory only.** The figures do not replace an electrician's inspection and testing. Stagewatch
> does not control power. See the [notices](README.md#notices).

## What Stagewatch does

- **It only reads.** Stagewatch cannot switch a relay, change a setting, restart, update or reset a
  meter.
- **Relays are never switched.** Some Shelly products (a Plug, a Plus 1PM) have a relay in the load
  path. Stagewatch reads the power from them and **never touches the relay**. It cannot stop the
  Shelly's own button, schedule or app from switching it. **For a distro, use a meter with no relay**,
  such as the Shelly PM Mini, or a clamp meter.
- **Readings are Equipment, not air.** They describe a circuit, so they can never change a site
  average, the speed of sound or the barometer. They are recorded exactly as the meter sends them.
  There is no offset or role setting for them. See [Equipment](../cards/equipment.md).
- **Energy is the meter's own running total** (watt-hours). It only counts up from when the meter's
  counter started. It is not an average. If someone resets the meter, the new number is recorded as
  it came, and the meter's row in Admin says when the total went back.
- **This show day's figures** (energy used, highest power and current, lowest and highest voltage, each
  with its time) are worked out from the readings Stagewatch received since the show day began (the
  site's day rollover). Energy used is the meter's total now minus its total at the first reading of the
  day. Stagewatch never fills in time it was not reading: the card lists those times instead. See the
  [Power card](../cards/power.md#this-show-day).
- **Apparent power** shows the meter's own figure when it sends one. Otherwise Stagewatch calculates it (volts times amps, or watts divided by power factor for a first-generation EM) and marks it "(calculated)". **Power factor** shows only when a meter sends it.

### Which Shelly meters

Stagewatch reads Shelly meters of **any generation** that have an address on your network. That
includes the first-generation **Shelly EM** (and 3EM), which Stagewatch finds by itself when the
meter has no second-generation interface. It chooses what to read by what the meter reports, not by its name. It looks for these
kinds of meter:

- A single power meter (the kind of reading a PM Mini gives).
- A three-phase meter, with each phase, the neutral and the totals.
- A single-circuit energy meter.
- A relay that also measures power (read-only: its on/off state is never read).
- A first-generation Shelly EM: each of its energy-meter channels shows voltage, power, reactive
  power, power factor and energy (watt-hours and watt-hours returned, as the meter sends them). It
  sends no current, so none is shown. The relay in a Shelly EM is never read or switched. A channel
  the meter marks as not valid shows no reading, never zero.

**Not yet built:** meters that push their readings to Stagewatch (WebSocket or MQTT), and passwords.
A meter with a password set is not supported yet. Stagewatch says so in words.

### What Stagewatch sends

Only these requests, all plain `GET`s to the meter's address on port 80. Any other request is refused
before a connection is opened.

| Request | When |
|---|---|
| `GET /rpc/Shelly.GetDeviceInfo` | When it first connects, after any failed read (to check it is still the same meter), and when you click **Test** or **Save meter** |
| `GET /rpc/Shelly.GetStatus` | Every 3 seconds by default. You can set 2 to 5 seconds for each meter. Also on **Test**. |
| `GET /shelly` | First-generation meters only (a Shelly EM has no `/rpc`): in place of `GetDeviceInfo`, on the same occasions |
| `GET /status` | First-generation meters only: in place of `GetStatus`, at the same rate |
| `GET /rpc/Shelly.GetConfig` | Once after Stagewatch connects to a meter, on **Test** and **Save meter**, and when you click **Use the device's name**. It only reads the names typed into the meter |
| `GET /settings` | First-generation meters only: in place of `GetConfig`, for the meter's name |

Nothing else. Stagewatch does not follow a redirect. It gives up on a request after 2 seconds, and
refuses a reply over 256 KiB. It asks at most four meters at the same moment.

Stagewatch talks only to a **private-network IP address**. Names such as `shelly.local` and public
addresses are refused. You can add up to 16 meters.

## Add a meter

1. Find the meter's **IP address**: in your router's list of devices, or in the Shelly app under the
   meter's settings.
2. Give the meter a fixed address in your router (a "DHCP reservation"), so it stays put.
3. Open **Admin**, go to the **Integrations** tab and, in the **Integrations** card at the top, click **Add integration**
   and choose **Shelly power meter**. (The **Shelly power meters** card shows once a meter is added.)
4. Type the address in **Meter address (an IP address on your local network)**,
   for example `192.168.1.50`.
5. Click **Test**.
6. Read the answer. Nothing on the meter is changed.
7. Type a **Name** (for example `Mic rack`). It starts as `Power meter`.
8. Optional: type an **Area**, for example `FOH`.
9. Optional: change **Reads every (seconds, 2 to 5)**. It starts at 3.
10. Click **Save meter**.
11. Under **User dashboards**, click **Edit cards** on a dashboard, tick **Power**, and click **Save
    dashboards**. The card stays hidden until a meter has been added.

If the meter has a name stored in it (in the Shelly app), Stagewatch uses it: the meter's name fills **Name** while it still says `Power meter`, and a circuit named on the meter starts with that name as its label. A meter with no names, or one that does not answer the name request in time, just keeps the default labels. Stagewatch only reads these names. It never writes anything to the meter.

**What you'll see:** after **Test**, `Found <the meter's model>, firmware …. It measures: Power meter.`
and a list of live readings, then `These are live readings from the meter. Nothing has been changed
on it.` After **Save meter**, the meter appears in the table with a status.

**Test** and **Save meter** together are limited to 10 a minute. If you see `Too many requests`,
wait a minute.

### Name each circuit and set the limits

The **Shelly power meters** card is only about the connection: name, area, address and how often Stagewatch asks. Everything about what a meter feeds is in **Admin → Power distribution**. All of it is optional, and none of it changes the meter.

1. Find the meter in **Power distribution**. A meter with one circuit (a PM Mini, a 1PM, a Plug, a three-phase meter) is **one row**. A meter with two circuits (a 2PM, a first-generation EM) has a header row for the meter and a row for each circuit.
2. On a circuit, type a **Label**, for example `Amp rack 3`, and tick **Include in site total** if its watts should go into the one **Site total power** figure on the Power card.
3. Under **More options**: choose the **Line** (Line 1 Brown, Line 2 Black, Line 3 Grey; a three-phase meter shows 3P and has no choice), type the **Breaker or fuse rating (A)** if you know it and the **Device limit (A)** (what the meter itself can carry). The load bar uses the lower of the two.
4. Each circuit label has a **Use the device's name** button. It reads the name stored in the meter and puts it in the box; press **Save** to keep it. What you type always wins and is never replaced without you pressing the button. When the meter holds a different name, it is shown beside the box as **Name on the meter**. The meter's own name has the same button in the **Shelly power meters** card.
5. A meter with two or more single-phase circuits shows its circuits only, with no separate total for the whole meter. Its header row's **More options** has **All circuits, total**: tick it to add one summary line (and its demand figures), plus, when a limit is set, one bar for their amps together against the meter's lower limit. A three-phase meter keeps its Line 1, 2, 3 and Total readings: that total is the meter's own.
6. Click **Save** in Power distribution.
7. Click **Add warnings** on the meter in the Shelly card to make a notice at 80 % of the lower limit and an alarm at 100 %. Nothing is made until you click it.

**What you'll see:** the Power card shows the labels, the lines and the load bars. How each figure is worked out is on the [Power card](../cards/power.md) page. A circuit that is not reading never counts as zero in a total.

### How Stagewatch recognises a meter

Stagewatch remembers a meter by its own **hardware (MAC) address**, which it reads from the meter. It
does not rely on the IP address. If the meter's IP address changes:

1. Find its row in the table.
2. Type the new address in the address box.
3. Click **Save**.

Stagewatch checks that the same meter answers there. If a different meter answers, it refuses and
changes nothing.

**Remove** takes a meter off the list. Its history is kept.

## How to check it worked

1. In the table, read the **Status** of the meter. It says `Reading every 3 s`.
2. Open a dashboard that has the **Power** card.

**What you'll see:** the meter's name, with watts, volts, amps and frequency, and an energy line.

## If it didn't work

Error messages use fixed words and never repeat text from the meter.

| What you see | What it means and what to do |
|---|---|
| `That address is not on a private network. Stagewatch only talks to meters on your local network.` | Use the meter's IP address on your own network, not a name or a public address. |
| `The meter did not answer in time.` | Check the meter is switched on and on the same network. |
| `Could not reach that address. Check the meter is switched on and on the same network.` | Same check. |
| `Something is at that address, but it is not accepting requests.` | Check the address. |
| `This meter needs a password: not supported yet.` | Stagewatch cannot read a meter with a password set yet. |
| `That address did not answer like a Shelly (any generation).` | Check the address, and that it is a Shelly. |
| `This Shelly has no power readings to show.` | This Shelly does not report power. |
| `The meter tried to send Stagewatch somewhere else, so Stagewatch stopped.` | Stagewatch never follows a redirect. Check the address. |
| `A different meter answered at that address.` | Another device now has that IP address. Nothing was changed. Find the right address. |
| `That meter has already been added` | It is in the list already. |
| `That name is already in use. Choose another` | Use a different **Name**. |
| `There is room for 16 power meters. Remove one first` | Remove a meter you no longer use. |

If a meter stops answering later, the card shows its last readings dimmed with their age and the
words `NO READING`, and a quiet notice appears in the alarm bar. When it comes back, a marker
`<name>: meter back` is added at that moment. Nothing is filled in for the time between, so the
history has a gap. See the [Power card](../cards/power.md) and
[Alarm bar and notices](../cards/alarm-bar.md).

More: [Troubleshooting](../troubleshooting.md).

## Tested status

- **Tried on real kit:** one Shelly PM Mini Gen3, over HTTP polling.
- **First-generation Shelly EM:** tried on one real Shelly EM (firmware 1.14.0) in read-only fixtures; live use on a show day is pending. A Gen1 meter with a password set is not supported (it says so).
- **Not yet tested:** any other model, including the Shelly EM Mini Gen4 and the Shelly 3EM.
  Three-phase and single-circuit meters, and relays that measure power, are read by what they report
  but have not been tried.
- **Not built:** meters pushing readings (WebSocket or MQTT), and meters with a password.
- **Tested on hardware: pending** for a full show day.

The card works in emulate mode (`--emulate`) with a simulated PM Mini, a simulated three-phase meter and a simulated first-generation Shelly EM. The PM Mini stops answering for 40
seconds every 5 minutes, to show the frozen state.

## Power distribution (who feeds whom)

**Admin → Power distribution** records how your power is laid out, ready for a power flow view in a later version. The [Power tree](../cards/power-tree.md) card draws it.

1. Add a **point** for anything that has no meter: the generator, the grid supply, an intake unit. Give it a role, a number and, if it is fed from something else, its Upstream. A generator or supply has nothing above it. Several separate supplies are fine, for example a delay tower with its own generator.
2. For each power meter, choose what it is (a role). Stagewatch fills in the next free number for that role and suggests what feeds it (for example the only CDU for a final distribution unit); change either if it is wrong. A meter with two circuits has a row for each circuit if they feed different things.
3. Under **More options** (or switch on **Show advanced power options** to open them on every row): the **Way** is the circuit number on the unit above, so a unit on way 2 of CDU02 on line 1 reads **CDU02-C2-L1** (**CDU01-C1-3P** for a three-phase feed). Each way of a unit feeds one thing, and Stagewatch tells you if you use one twice. With no way set, the card shows just the unit above. Also here: line, breaker or fuse rating, device limit, and the typed allowance for a point nothing measures.
4. Save. Stagewatch refuses a loop, a unit fed from itself, a unit fed from the same level or from below, and a number used twice in the same role. Nothing is changed when it refuses, and it says why.

The order is supply, intake switch unit (ISU), central distribution unit (CDU), final distribution unit (FDU), then equipment. A unit may skip a level. A UPS can sit anywhere; choose its type if you know it. A point you mark **Not monitored** can carry a figure you type in: that is a guess, kept apart from measurements, and not used yet. The names ISU, CDU and FDU are the ones many crews use. Check them against your own paperwork.

Removing a point asks what to do with the units fed from it: move them up a level, or leave them with no Upstream. Removing a meter leaves whatever it fed with no Upstream.

Related: [Notices](README.md#notices), [Equipment](../cards/equipment.md), [Troubleshooting](../troubleshooting.md).

Back to [Integrations](README.md).
