# Integrations

An **integration** is a link between Stagewatch and something else, so Stagewatch can read from it or send to it.

Every integration here is **read only** apart from [OSC output](osc-output.md), which only sends numbers out.
Stagewatch does not switch, mute, restart or change anything it reads from.

## At a glance

This is the one table of integrations. Other pages link here rather than repeating it.

| Integration | Made by | What Stagewatch reads | Read only | Shown on | Status | Guide |
|---|---|---|---|---|---|---|
| ESPHome sensor nodes | ESPHome project | Temperature, humidity, pressure, rack temperature | Yes | Tiles, History, Barometer, Equipment | Working. Not yet tested on hardware for several boards (see the node guide) | [Build a node](../nodes/README.md), [ESPHome](esphome.md) |
| Shelly power meters | Allterco Robotics | Watts, volts, amps, frequency and energy, over the local network | Yes | Power | Tried on real kit with one Shelly PM Mini Gen3 only. Other models are not yet tested | [Shelly](shelly.md) |
| WitMotion tilt sensor (Bluetooth) | WitMotion | Roll and pitch, acceleration, battery and sensor temperature, with an optional target angle, over Bluetooth | Yes | Tilt (WitMotion), Equipment | WT901BLECL and WT9011DCL checked against real captures. The Bluetooth link is not yet run on a Pi, and Windows is untested | [WitMotion](witmotion.md) |
| Smaart | Rational Acoustics | Up to three sound level values that Smaart reports | Yes | Sound level | Tested live on Smaart Suite 9.6.4. Other Smaart versions are not tested. Port 26000 is the usual example: confirm it on your Smaart | [Smaart](smaart.md) |
| DirectOut GLOBCON | DirectOut | Channel level meters, names and mute state of one controller | Yes | DirectOut GLOBCON | Names and layers checked on one real controller. Live meters with audio running are not yet tested | [GLOBCON](globcon.md) |
| Ontime | Carlos Valente (open source) | Time of day, countdown, rundown position and ahead or behind | Yes | Wall Clock, Ontime Timer, Ontime Rundown | Rolling state tested on real Ontime 4.14.0. The rest is not yet tested | [Ontime](ontime.md) |
| OSC output | Not applicable | Nothing. It sends the site average and alarm state out | Not applicable (send only) | Not applicable | Messages built and tested. Not yet tested with a real receiver | [OSC output](osc-output.md) |

In **Admin → Integrations**, a small tag under each name says how it gets its data: **Local, live push**
(the device sends changes as they happen, on your network), **Local, checks every few seconds** (Stagewatch
asks, as it does for Shelly) or, shown as a warning, **Uses the internet**. No tag means Stagewatch has
nothing to say about it, as for OSC output.

"Not yet tested" means built and checked with simulated data only. Check Stagewatch against the
product's own screen before you rely on it.

## Notices

<!-- DRAFT wording: to be read once by the project owner and by someone who can give legal advice before the next release. -->

### An independent project

Stagewatch is independent and unofficial. It is **not made, approved, sponsored or supported by**
Ontime, Rational Acoustics (Smaart), DirectOut (GLOBCON), Allterco Robotics (Shelly), Bitfocus
(Companion), the ESPHome project, or any other maker named in these guides.

### Names and logos

Product names, company names and logos belong to their owners. Stagewatch uses them only to say
which product it can read from. Using one does not mean its owner knows about Stagewatch or agrees
with it. The Ontime and DirectOut logos appear small on the cards that read from those products,
for the same reason.

### Read only

Stagewatch only listens to these products. It does not switch, mute, restart, reset or change
them. The few messages it does send are listed in full on each guide, under **What Stagewatch
sends**. [OSC output](osc-output.md) is the one thing that goes the other way: it sends
Stagewatch's own numbers to an address that you choose, and sends nothing until you add one.

### How the links work, and where they can break

Each link was built from what the maker publishes (a web page, a developer document or a
recording), not from an agreement with the maker. A new version of a product can stop a link
working without warning. Each guide says what has been tested, and on which version. Where it says
**not yet tested**, it has not been. Check Stagewatch against the product's own screen before you
rely on it.

### Other programs' data

Stagewatch does not copy another program's show data, such as an Ontime rundown, into its own
records. It does record some numbers that you choose to put on the timeline, such as sound level
from Smaart or power from a Shelly meter, exactly as it receives them. The only copying is an
import that you start yourself, such as pasting a running order into the schedule.

### Your responsibility

You are responsible for having the right to use each product and to connect to it, and for
following its licence and terms. Keep passwords and addresses private. Dashboards have no login,
so anyone on your show network can see what a card shows.

### Advisory only

Stagewatch is a guide. It is not a safety, compliance or certified monitoring system, and nothing
it shows (including sound level and power) is a calibrated record. It does not replace the people,
plans and certified equipment that look after rigging, power, wind, lightning, fire or noise
limits.

### No warranty

Stagewatch is free software under the GNU General Public License version 3 (see
[LICENSE](../../LICENSE)) and comes with **no warranty of any kind**. The authors are not liable
for loss or damage from using it. It was written with an AI assistant and has not had a human
code review. Test it on your own kit before a show and use it at your own risk. This page is a
plain-words summary and does not change the licence.

Back to the [guides index](../README.md).