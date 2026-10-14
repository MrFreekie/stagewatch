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
| Smaart | Rational Acoustics | Up to three sound level values that Smaart reports | Yes | Sound level | Not yet tested against a live Smaart. Written from Smaart's own web client | [Smaart](smaart.md) |
| DirectOut GLOBCON | DirectOut | Channel level meters, names and mute state of one controller | Yes | DirectOut GLOBCON | Not yet tested on a real GLOBCON beyond one recording | [GLOBCON](globcon.md) |
| Ontime | Carlos Valente (open source) | Time of day, countdown, rundown position and ahead or behind | Yes | Wall Clock, Ontime Timer, Ontime Rundown | Rolling state tested on real Ontime 4.14.0. The rest is not yet tested | [Ontime](ontime.md) |
| OSC output | Not applicable | Nothing. It sends the site average and alarm state out | Not applicable (send only) | Not applicable | Working | [OSC output](osc-output.md) |

"Not yet tested" means built and checked with simulated data only. Check Stagewatch against the
product's own screen before you rely on it.

## Notices

> **Being written.** The notice about other makers' names, read-only use, safety and warranty goes here.
> Until then: Stagewatch is an advisory tool, not a certified safety system, and has no warranty.
> Product names belong to their owners and no endorsement is implied.

Back to the [guides index](../README.md).