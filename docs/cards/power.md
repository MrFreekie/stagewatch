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
- **This show day**: the energy used, the highest power and highest current (each with the time), and the lowest and highest voltage (with times). See below.
- **Apparent power** (VA), **Power factor** and the meter's own temperature, but only when the meter reports them. Many do not, and then they are left out.

A meter that measures three phases shows a small table with a row for each phase, and a total where the meter gives one. A missing value is a dash, never 0.

On a wall screen the power figure is very large, and the small explanatory lines are hidden.

## This show day

Under the live figures each meter shows what happened since the show day began. The show day follows the site's day rollover (06:00 unless you changed it in Admin), so a show running past midnight stays one show day.

- **Energy used this show day**: the meter's own running total now, minus its total at the first reading Stagewatch took that show day. It says "since" and the time of that first reading. Stagewatch does not estimate energy itself.
- **Highest power** and **Highest current**, with the time each was seen.
- **Voltage range**: the lowest and highest voltage, with their times.

These come only from readings Stagewatch actually received. A very short surge between two readings can be missed, so treat them as a guide, not a record of inrush. If Stagewatch was not reading (switched off, restarted, or the meter stopped answering for more than 30 seconds) the card adds a "Not reading" line with the times, and the figures miss those times. Nothing is filled in. A missing figure is a dash, never 0.

If the meter's energy total goes back (someone reset the meter), the energy used before that is kept, counting carries on from the new number, and the card says when it went back. Energy used around the reset is not counted.

A new show day starts all of this afresh. If Stagewatch is restarted during a show day, it rebuilds the figures from the readings it had already recorded.

## Load bar and UK limits

- **Load bar.** In Admin, give the meter a **rating in amps** (a Shelly PM Mini Gen3 is 16 A; use the breaker's rating if that is lower). The card then shows how loaded the circuit is, for example "1.80 A of 16 A (11%)". The bar is green below 80%, amber from 80% (HIGH LOAD) and red from 100% (OVER RATING). A mark on the bar shows today's highest current. Without a rating there is no bar.
- **Voltage swing meter.** A bar from 195 to 265 V with the UK range shaded: 216.2 to 253.0 V (230 V +10% / -6%). A marker shows the live voltage and a paler band shows today's lowest to highest. Outside the range it says LOW or HIGH in red. A supply under 180 V (110 V site power) is not judged.
- **Frequency swing meter.** A bar from 49.0 to 51.0 Hz. Green inside 49.8 to 50.2 Hz, amber (CLOSE TO LOW/HIGH LIMIT) between that and 49.5 or 50.5 Hz, red (LOW or HIGH) outside 49.5 to 50.5 Hz. Today's lowest and highest frequency are shown too. A supply that is not near 50 Hz (below 40 or above 55 Hz) is not judged.

**Add warnings.** After saving a rating, press **Add warnings** on the meter in Admin. It makes two ordinary thresholds you can see and edit under Thresholds: a level 1 notice at 80% of the rating and a level 2 alarm at 100%, each held for 5 seconds. Nothing is made until you press it, and pressing it again replaces only the ones it made. They are your own limits: Stagewatch does not protect or switch anything.

**Phase imbalance.** A three-phase meter shows an estimate: the biggest difference of any phase current from the average, as a percentage of the average. It is a display only and is not recorded.

The voltage and frequency warnings put a quiet notice in the alarm bar. It never makes a sound, and it clears itself when the reading is back inside with a little margin. All of this is advisory. Stagewatch only reads, and the rating is simply the number you typed in. Colour is never the only signal: the words are always shown.

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
