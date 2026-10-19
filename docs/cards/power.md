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
- **Apparent power**, **Power factor** and the meter's own temperature, when available. Apparent power shows in VA, or in kVA from 1,000 VA. If the meter does not report it, Stagewatch works it out as volts times amps (or watts divided by power factor when there is no current) and marks it **(calculated)**. If an input is missing or old it shows a dash. Power factor is shown only when the meter reports it.

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

## Lines, labels and ratings for each circuit

In Admin, each meter lists its circuits. For each one you can type a **label** (for example "Amp rack 3"), set a **rating** that overrides the meter's own, and, on a single-phase meter, say which **line** it is on.

- A three-phase meter shows its lines as **Line 1 (Brown)**, **Line 2 (Black)** and **Line 3 (Grey)**, and the neutral as **Neutral (Blue)**. The meter's A, B and C are shown as Line 1, 2 and 3.
- A single-phase meter you have assigned to a line shows that line beside its label, for example "Amp rack 3 · Line 2 (Black)".
- The load bar and the **Add warnings** button use the circuit's own rating, or the meter's rating when the circuit has none.

These are only labels you typed. Stagewatch cannot check which line a circuit is really on. Always check at the distro.

## Site total

To add the power of several circuits into one figure, for example one circuit on each of the three lines, tick **Include in site total** beside each circuit in Admin and save. The Power card then shows **Site total power** in watts at the top, with the list of circuits it adds up.

- Only the circuits you ticked are added, from any meter.
- A three-phase meter already gives its own total. Tick it once; do not tick its separate lines as well.
- The total shows only while **every** ticked circuit is reading. If a meter stops answering, or a reading is old, the total shows a dash and says **INCOMPLETE** with the names that are missing. Stagewatch never shows a part-total and never fills in a missing circuit.
- It is worked out on the screen from the latest readings. It is not recorded, and it has no history chart.

## Combined load bar

Some meters have two circuits that share one supply, for example a Shelly 2PM with two 10 A outputs on a 16 A feed. Each circuit can be fine while the two together are too much. In Admin, type the **rating of the whole supply** (16 in that example), give each circuit its own rating, and tick **Combined load bar**. The card then shows one extra bar, **Combined load**, for the amps of the circuits added together against the meter's rating, with the same amber at 80% and red at 100%. The circuits keep their own bars.

- The tickbox only appears for a meter with two or more single-phase circuits, and it needs a meter rating.
- If any circuit is not reading, or its reading is old, the bar says **Unavailable** rather than showing a part-total.
- A three-phase meter has no combined bar: each phase is its own circuit with its own limit, and the meter already shows its total power.
- This adds up amps against a limit. It is separate from **Site total power**, which adds up watts across circuits you choose.

## More meter types

Some meters report more than the Shelly does. When a meter sends them, the card also shows:

- **Line-to-line voltage** (Line 1 to Line 2 and so on) as a swing meter with a typical 400 V band of 376 to 440 V. Stagewatch never works this out itself from the line-to-neutral readings, because that needs the phase angles.
- **Neutral current** as a percentage of the biggest line current, with the word HIGH from 50%. It is a hint that the lines may be uneven, not a fault finding.
- **Voltage and current THD** (distortion): today's highest, advisory only and not a power-quality certificate.
- **Reactive power**.
- **Meter's own maximum**: a peak the meter keeps itself, shown separately and labelled "kept by the meter since its last reset". Stagewatch never resets it. If it goes back, someone reset the meter.

Rows that a meter does not send are left out. No supported meter sends these yet: they are ready for Modbus meters planned later (see the Shelly page for what is supported today).

## Load bar and UK limits

- **Load bar.** In Admin, give the meter a **rating in amps** (a Shelly PM Mini Gen3 is 16 A; use the breaker's rating if that is lower). The card then shows how loaded the circuit is, for example "1.80 A of 16 A (11%)". The bar is green below 80%, amber from 80% (HIGH LOAD) and red from 100% (OVER RATING). A mark on the bar shows today's highest current. Without a rating there is no bar.
- **Voltage swing meter.** A bar from 195 to 265 V with the UK range shaded: 216.2 to 253.0 V (230 V +10% / -6%). A marker shows the live voltage and a paler band shows today's lowest to highest. Outside the range it says LOW or HIGH in red. A supply under 180 V (110 V site power) is not judged.
- **Frequency swing meter.** A bar from 49.0 to 51.0 Hz. Green inside 49.8 to 50.2 Hz, amber (CLOSE TO LOW/HIGH LIMIT) between that and 49.5 or 50.5 Hz, red (LOW or HIGH) outside 49.5 to 50.5 Hz. Today's lowest and highest frequency are shown too. A supply that is not near 50 Hz (below 40 or above 55 Hz) is not judged.

**Add warnings.** After saving a rating, press **Add warnings** on the meter in Admin. It makes two ordinary thresholds you can see and edit under Thresholds: a level 1 notice at 80% of the rating and a level 2 alarm at 100%, each held for 5 seconds. Nothing is made until you press it, and pressing it again replaces only the ones it made. They are your own limits: Stagewatch does not protect or switch anything.

**Phase imbalance.** A three-phase meter shows an estimate: the biggest difference of any phase current from the average, as a percentage of the average. It is a display only and is not recorded.

The voltage and frequency warnings put a quiet notice in the alarm bar. It never makes a sound, and it clears itself when the reading is back inside with a little margin. All of this is advisory. Stagewatch only reads, and the rating is simply the number you typed in. Colour is never the only signal: the words are always shown.

## Calculated figures

These are worked out by Stagewatch from the meter readings. They say "(calculated)" and are a guide only. If a reading they need is missing or old, there is no figure: Stagewatch never guesses and never shows zero.

**Reactive power.** For meters that do not send it, Stagewatch works it out from volt-amps and watts. It shows the size only: it cannot say whether the load leads or lags. A meter that sends its own reactive power (the first-generation EM) is shown as it sent it. Shown as var, or kVAr from 1,000 var.

**Phase unbalance.** The biggest difference of any line current from the average of the three, as a percentage of that average (the NEMA MG 1 method). 0% means the lines carry the same current. Stagewatch works it out for each three-phase meter, and for the circuits you set to Line 1, 2 and 3 in Admin. You also see each line's share of the load. It shows a dash if any line is missing or old, or if the average current is under 1 A (at almost no load the percentage means nothing). It is in **Totals** and on the chart as **Phase unbalance**.

**Demand and load factor.** For the site total and for each meter: the average power over a rolling window (5, 15 or 30 minutes, set in **Admin → Power demand and generator size**; 15 is what electricity suppliers commonly use), the highest of those averages this show day (the peak), and the load factor, which is the show day's average divided by the peak, as a percentage. A silence in the readings is left out, never counted as zero, and the figure says INCOMPLETE. Nothing shows until a full window has passed. The show day starts and ends at the same time as the show day figures above.

**Generator size suggestion.** From the site total's peak demand in kVA: the smallest generator is the peak divided by your loading limit (80% unless you change it in Admin). It also shows the kW at the generator power factor (0.8 unless you change it), and the next standard size up. If you type the size of the generator you have, the card shows how loaded it is at the day's peak, with a warning at your loading limit and again at 100%.

This is a suggestion from the demand Stagewatch measured, nothing more. It leaves out loads that are not metered, starting surges, harmonics and anything added later. Ask a qualified person to size the generator.

## Totals

**Apparent power** is a chart choice beside Power and Current. In Totals it is added up per line as a **sum of VA** only when every circuit has it; adding VA this way can read higher than the true total when the loads are not in step.

Choose **Totals** in the chart's box to see the watts of each line now. A three-phase meter shows its own Line 1, Line 2, Line 3 and total. Single-phase circuits are added up by the line you set for each circuit in Admin (Line 1, 2 or 3, or "Line not set"), with a total for all lines. If any circuit in a line is not reading, that line says INCOMPLETE and names what is missing, instead of showing a part-total. Neutral current is listed as it is, never added up.

## Calculated neutral current

When you put single-phase circuits on Line 1, Line 2 and Line 3 in Admin, the Power card adds a **Calculated neutral** meter showing **Neutral current (calculated)**: the current that must be returning in the neutral because the three lines are not loaded equally. It is worked out, never measured, and it is labelled that way everywhere, including the graph (choose **Neutral and earth**) and the history.

- **Each line needs at least one circuit, and every circuit must be reading.** If a line has no circuit, or any circuit is not answering or its reading is old, there is no figure (a dash) and the card says why. Stagewatch never works it out from two lines, and never shows a missing line as zero.
- **Which method is used is stated on the card.** The better one uses each circuit's power factor and the direction of its reactive power; it is used only when every circuit gives both. The usual meters (PM Mini, 1PM, 2PM) do not, so the card shows an **estimate (assumes equal power factor)**, which is right only when all three lines have much the same power factor. With very different power factors it can be well out (for example 10, 6 and 8 A read 3.5 A by the estimate, but 5.1 A when the real power factors are 0.87 lagging, 1.0 and 0.94 leading).
- **Fundamental frequency only.** Switch-mode power supplies and LED drivers send third-harmonic current that adds up in the neutral instead of cancelling, so a real neutral can carry more than the calculation says, even with the lines balanced.
- **Meters are read at slightly different moments**, so a short-lived unbalance can be an artefact.
- **Loads on circuits that are not metered are not included.**
- **Three-phase meters are left out.** A meter that measures its own neutral (a Pro 3EM) shows that measured value instead.

Treat it as a guide for balancing a distro, not as a measurement. Stagewatch only reads.

## History chart

Under the meters is a chart of one measurement over time: **Power**, **Current**, **Voltage** or **Frequency**, for the last **15m**, **1h** or the whole **Show**. A three-phase meter gets one line per phase, and lines also differ by dash style, not just colour. The box above the chart chooses what it shows: **All on one graph** (every circuit of every meter as separate lines, each named in the key, up to nine at once), **Totals**, one meter, or just one circuit or phase of a meter (for example one switch of a 2PM, one channel of an EM, or phase 2 of a three-phase meter). Only one kind of measurement is drawn at a time, so watts and amps never share a scale. Your choice is kept on this device only. The chart draws what was recorded. When Stagewatch was not reading the meter, the line breaks. Nothing is joined up or filled in. Short surges between two readings can be missed. On a wall screen the controls are hidden and the chart shows the saved choice.

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

**Tested on hardware: pending** for a full show day. Live readings have been tried on one Shelly PM Mini Gen3 only. The first-generation Shelly EM is read from a saved reply of a real one, and has not yet been run live. Other Shelly meters (three-phase meters, single-circuit meters, and relays that measure power) are read by the readings they report, but have not been tried. Site total, the combined load bar, apparent power, Totals and the graph choices are checked with simulated meters only. Check the figures against the Shelly's own page. A meter with a password set is not supported yet.

Related: [Equipment](equipment.md), [Troubleshooting](../troubleshooting.md).
