# Add a Shelly that sends to Stagewatch (H&T sensor, or a meter that pushes)

Most Shelly meters are read by **asking** them (see [Shelly power meters](shelly.md)). Some devices work
the other way round: they **send** their readings to Stagewatch. The Shelly **H&T Gen3** temperature and
humidity sensor is one, because it runs on batteries and sleeps between reports, so it cannot be asked.
A mains-powered meter such as the Pro 3EM can also be set to send.

This page is for the person who sets Stagewatch up. It covers switching the receiver on, pointing a
Shelly at it, and what to expect.

Stagewatch is independent. It is not made, approved or supported by Allterco Robotics or Shelly. See
the [notices](README.md#notices).

> **Test status: not tested on hardware.** This was built from Shelly's published documentation and
> from simulated devices. It has **not** been run against a real H&T Gen3 or a real Pro 3EM. The
> [list below](#what-is-not-confirmed-yet) says what is still a guess. Check the first readings against the
> Shelly's own page before you rely on them.

> **Not encrypted or signed.** A Shelly cannot prove who it is to Stagewatch, and its readings are not
> signed. Another device on the same network could pretend to be your sensor and send false readings.
> Stagewatch only reads devices you have added, and limits how much any one sender can send, but it
> cannot tell a real sensor from a copy. Use this on a show network you trust, and treat the readings as
> advisory.

## What Stagewatch does

- **It only listens.** Stagewatch sends the device nothing. It cannot switch, restart, update, reset or
  change a setting on a Shelly.
- **It is off until you switch it on.** Nothing listens for Shelly devices by default. Polling stays the
  normal way to read a meter.
- **Only devices you add are read, and only from the address they were added at.** A device that sends
  and is not on your list is put on a short "found devices" list (its model and the kind of readings it
  has, never the readings themselves), told goodbye, and forgotten when Stagewatch restarts. The list
  keeps the address a device was **first** seen at, and warns you if the same device is later heard from
  another address (something may be copying it). Adding a device uses the first address. Once a device
  is added, a message from any other address is not read: the admin page shows "now sending from
  <address>" with **Accept** and **Not now**, and nothing changes until you accept. Only accept an
  address you recognise. If a device really does get a new address from your router, you will see this
  once and accept it.
- **It is limited.** At most 32 connections at once (8 of them kept for the addresses of devices you
  have added), 4 from one address, 10 new connections a minute from one address, and a cap on how big
  and how fast the messages may be. A connection that has not sent something from an added device
  within 10 seconds is closed, and so is one whose first message is not a proper Shelly message. A
  device that is not on your list never pushes out a recently seen entry on a full found list (an
  entry is only replaced once it has not been heard for 5 minutes). A web page in a browser is refused.
- **Impossible values are gaps.** A temperature outside -40 to 85 °C, a Wi-Fi signal outside -120 to 0
  dBm, a battery voltage outside 0 to 10 V, or a meter reading beyond what is physically possible
  (for example over 1,000 V or 10,000 A) is treated as missing, never recorded. Whole numbers of more
  than 20 digits make the message be refused.

## Add an H&T sensor

1. In Admin, open **Integrations**, then **Shelly devices that send to Stagewatch**, and press
   **Switch on**.
2. Stagewatch shows one or more addresses that start with `ws://`. Press **Copy** on the one on the same
   network as the Shelly.
3. Open the sensor's own web page (press its button to wake it first; it only answers for a short
   time). Find the **Outbound WebSocket** setting (the menu wording can differ between firmware
   versions), switch it on and paste the address. Save.
4. Press the sensor's button to make it send. It appears under **Found devices**.
5. Give it a name and an area (for example "FOH tent", "FOH") and press **Add**.

The sensor now shows temperature, humidity and battery on the Sensors card. If it also reports Wi-Fi
signal, that is shown; if it does not, nothing is shown.

### What to expect

- **It reports in bursts.** A battery sensor wakes, sends and goes back to sleep, so a reading can be
  hours old. The **Updated** column on the Sensors card shows how long ago each reading arrived, and a
  reading is greyed out only after the sensor's own limit (about 2.5 hours for a 2 hour wake interval), not
  after the usual 60 seconds. A new sensor shows "Waiting for the first
  reading" until it has sent once.
- **Missing, not zero.** If the sensor does not report for longer than its wake interval plus a margin
  (about 2.5 hours for a 2 hour interval), it goes to **Missing** with a quiet notice
  ("No report since ... Batteries flat, out of range or switched off?"). Its last reading stays on the
  card with its age. Stagewatch never fills the gap with a guess.
- **Battery.** A quiet notice appears at 20 % and clears at 25 %. It is not raised while the sensor is on
  USB power.
- **Indoor use only.** The H&T is rated 0 to 40 °C. A reading outside that adds a hint on the device, not
  an alarm. It is not meant for outdoor stages.
- **Not in the site average (yet).** An H&T sits wherever you put it, so Stagewatch leaves it out of the
  site temperature and humidity average until you tick it under **Calibration**. It is still an
  Environment reading.
- **A new address waits for you.** Stagewatch recognises a sensor by its hardware address and the address
  it was added at. If your router gives it a new IP address, the admin page shows "now sending from ...",
  readings pause, and they carry on once you press **Accept**. Give the sensor a fixed address in your
  router (a DHCP reservation) to avoid this.
- **Ignore** puts a found device on a list that is closed on sight; **Remove** stops reading a sensor you
  added. History is kept. A device you have added cannot be ignored, and adding a device you had
  ignored takes it off the ignore list.

## A power meter that sends (Pro 3EM)

A meter is added from the **found list** (the **Add a meter** form in [Shelly power meters](shelly.md) always
polls), and is then read from what it sends instead of by polling. Its row on the Shelly power meters card
says "Sent by the meter". It shows the same readings as a polled meter (three phases, neutral if
the neutral clamp is fitted, totals and energy). A meter that has not sent for 10 minutes (2 minutes once
its connection has closed) goes to **Missing**, its readings freeze with their age, and a **meter back**
marker is added when it returns.

The meter's own one-minute lowest and highest records ("highest this show") are **not read yet**; see
below.

## What is not confirmed yet

Everything here is untested on hardware. Stagewatch is built to leave a gap, never a zero, where it is
unsure.

- Whether a real Shelly sends an `Origin` header or asks for a particular sub-protocol when it connects.
  Stagewatch refuses a connection that sends an `Origin` (that is how a web page is recognised). If a real
  Shelly does send one, the receiver would refuse it. Needs a check on real kit.
- The exact messages and wake interval of a real H&T Gen3, and whether it reports Wi-Fi signal.
- Whether a meter with a steady load sends regular updates, and whether the changes it sends use the same
  thresholds as its webhooks.
- The Pro 3EM minimum and maximum records: which fields, how many days it keeps them and how they are
  asked for. Stagewatch's read-only request list has deliberately **not** been widened to fetch them.
- How an H&T behaves while on USB power.
- A device that has a password on its WebSocket setting (digest authentication) is not supported.
- Connections are plain (`ws://`), not encrypted.

If you downgrade Stagewatch later, the older version does not know the new settings and drops them the
next time it saves its configuration: the receiver would be off and the sensors forgotten.

## Related

- [Shelly power meters](shelly.md)
- [Integrations](README.md)
