# Power

Watts, volts, amps, frequency and energy from Shelly power meters on your network. It answers "is this rack or circuit drawing what I expect?".

Stagewatch is a guide. These figures do not replace the electrician's inspection and testing. Stagewatch only reads the meters. It never switches a relay or changes a setting on a meter.

Part of the [dashboard cards](README.md). Half size is available. The card stays hidden until a meter has been added in Admin. Finding a meter's address, adding it and renaming it are in [Shelly](../integrations/shelly.md).

## What you see

The card has one block for each meter. The block shows:

- The meter's name, and its area if one was set, such as "Mic rack · Stage left".
- A big **Power** figure in watts.
- **Voltage** (V), **Current** (A) and **Frequency** (Hz).
- **Energy**: the meter's own running total, shown as Wh below 1,000 and kWh above. It only counts up from when the meter's counter started. It is not an average, and it is not a level.
- **Apparent power** (VA), **Power factor** and the meter's own temperature, but only when the meter reports them. Many do not, and then they are left out.

A meter that measures three phases shows a small table with a row for each phase, and a total where the meter gives one. A missing value is a dash, never 0.

On a wall screen the power figure is very large, and the small explanatory lines are hidden.

## Equipment, not air

Power readings are always Equipment readings. They describe a circuit, never the air. They can never change a site average, the speed of sound or the barometer. They are recorded exactly as the meter sends them, with no offset.

## What the badge says

Each meter block has a badge beside its name.

| Badge | What it means |
|---|---|
| **OK** | Readings are arriving. |
| **▲ NO READING** | The meter has stopped answering. It may be switched off, have lost Wi-Fi, or have changed address. The last readings stay on screen, dimmed and struck through, with how old they are. They are never shown as zero. |
| **▲ OLD** | The readings have gone old. They stay on screen, dimmed, with their age. |
| **▲ PROBLEM** | The meter needs a password, a different meter answered at the saved address, or it is not sending power readings. The note says which. |
| **... WAITING** | The meter has been added but has not answered yet. |

After a few missed readings a quiet notice appears in the alarm bar. It never makes a sound. When the meter comes back, a marker called "(meter name): meter back" is added at that moment. Nothing is filled in for the time in between, so the history has a gap.

If an energy total ever goes back, someone has reset the meter. The new number is recorded as it came.

## About the status mark

This card has no Online, Offline or Error mark in its header. Each meter shows its own badge, as above. The marks on the other cards are explained in [the dashboard header](sensors.md#the-dashboard-header).

## Relays

Some Shelly products, such as a Plug, have a relay in the load path. Stagewatch reads the power from them and never touches the relay. It cannot stop the Shelly's own button, schedule or app from switching it. For a distro, use a meter with no relay, such as the Shelly PM Mini, or a clamp meter.

## Tested on real kit

The first version has been tried on one Shelly PM Mini Gen3 only. Other Shelly meters (three-phase meters, single-circuit meters, and relays that measure power) are read by the readings they report, but have not been tried. Check their figures against the Shelly's own page. A meter with a password set is not supported yet.

Related: [Equipment](equipment.md), [Troubleshooting](../troubleshooting.md).
