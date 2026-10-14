# OSC output

**OSC** (Open Sound Control) is a simple message format that many show programs, desks and
lighting or button-box programs understand. Stagewatch can send its site average and alarm state
out as OSC messages, so other kit can show them or react to them.

This is the one integration that sends data **out**. It is not a link to any maker's product. It
sends only numbers, and only to an address you type in. Anything that receives OSC can use them.

> **Test status.** The messages are built and checked by automatic tests. **Not yet tested** with a
> real desk, show-control program or other receiver.

> **Advisory only.** The messages carry numbers, not commands. Do not connect them to anything where
> a wrong, late or missing number could matter for safety. OSC here is sent over UDP, which does not
> tell Stagewatch whether a message arrived.

## What Stagewatch sends

Nothing is sent **until you add a destination**. With none, OSC output does nothing.

| Address | Arguments (in order) | When |
|---|---|---|
| `/stagewatch/avg/env` | Temperature in °C, humidity in %, pressure in Pa, speed of sound in m/s, number of temperature sensors in the average (a whole number) | Always, to every destination that is switched on |
| `/stagewatch/alarm` | Highest active alarm level (0 = none, 1 = advisory, 2 = alert, 3 = stop), sounding (0 or 1) | Always, to every destination that is switched on |
| `/stagewatch/node/<id>/env` | Temperature in °C, humidity in %, pressure in Pa, for one node | **Only** if **Per-node messages** is ticked. One message for each device Stagewatch knows. `<id>` is the short name Stagewatch gives the device. |

- **A value that is not measured is sent as NaN** ("not a number"), never as 0. A reading that has
  gone stale is sent as NaN too. Your receiver must cope with NaN.
- Quiet notices from outside software (Ontime, Smaart, GLOBCON, Shelly) do not count in the alarm
  level and never set "sounding".
- The site values only ever use **Environment** sensors. A rack's temperature never moves them. A
  per-node message carries that node's own readings, including an Equipment node's. If a node has two
  sensors of the same kind, the message carries one of them.
- The messages are sent as often as **Rate (Hz)** says. It starts at 1 (once a second). You can use
  up to 20.
- Only these messages are sent. Stagewatch sends no cues, button presses or commands, and receives
  nothing back.

Failed sends are written to the log, and the **OSC output** card in Admin shows the last error.
Stagewatch does not keep a log of every message.

## Set it up

1. Open **Admin** and find the **OSC output** card. It is closed at first. Click its heading to open
   it.
2. Click **Add destination**.
3. Type the receiver's address in **Host** (an IP address or name) and its **Port**. The port starts
   at 9000.
4. Leave **On** ticked.
5. Optional: tick **Per-node messages**.
6. Optional: change **Rate (Hz)**.
7. Click **Save OSC**.

**What you'll see:** `OSC saved`, and messages start arriving at the receiver within a second.

To stop sending, untick **On** for a destination, or click **✕** next to it, then click **Save OSC**.

Use an address on your show network. Stagewatch does not stop you typing another address, and the
messages are not encrypted or signed.

## Receiving it

Whatever receives the messages decides what to do with them. Set that up in the receiving program.

- **Companion and QLab.** Both can take part in OSC, but Stagewatch's addresses are not commands for
  either of them. Stagewatch does not press Companion buttons or fire QLab cues. If you point
  Stagewatch at Companion's own OSC control port, note that port is **off until you switch it on in
  Companion** and has **no password**, so anything on the network can send it OSC. To read Stagewatch's
  numbers in Companion, use a module that listens for incoming OSC (see Companion's own guide).
- **A program that runs actions from incoming OSC** could act on what it receives. Keep Stagewatch's
  messages away from any action that matters.

Stagewatch is not made or approved by Bitfocus (Companion), Figure 53 (QLab) or anyone else who
makes a receiver. See the [notices](README.md#notices).

## How to check it worked

1. Set up the destination as above.
2. Look at the receiver. Many programs have an OSC monitor.

**What you'll see:** `/stagewatch/avg/env` arrives about once a second (at the default rate), with five
numbers.

## If it didn't work

| What you see | What to do |
|---|---|
| Nothing arrives | Check the address and port, that **On** is ticked, that you clicked **Save OSC**, and that the receiver is listening on that port. Check the receiver's computer firewall allows UDP on that port. |
| A red line under the card | That is the last send error. Check the address is spelt correctly and reachable. |
| The receiver shows `nan` | That value is not measured, or has gone stale. This is meant. |
| The numbers look odd | Pressure is in **pascals** (Pa), not hectopascals. 101,325 Pa is 1,013.25 hPa. |

More: [Troubleshooting](../troubleshooting.md).

Related: [Notices](README.md#notices), [Alarm bar and notices](../cards/alarm-bar.md).

Back to [Integrations](README.md).
