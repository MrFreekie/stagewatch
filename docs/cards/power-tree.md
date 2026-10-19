# Power tree

Shows how power flows through the site: each supply (a generator or the grid) at the top, then intake units, main distribution, final distribution and equipment below it. It answers "where is the power going, and what are we not seeing?".

Stagewatch is a guide. These figures do not replace the electrician's inspection and testing. Stagewatch only reads the meters. It never switches anything.

Part of the [dashboard cards](README.md). It uses the same [Shelly](../integrations/shelly.md) meters as the [Power](power.md) card. Add it to a dashboard in **Edit cards**; it is never added by default. Who feeds whom is set up in Admin, under **Power distribution**. Until that is done the card says "No power distribution set up".

## What you see

- **Totals at the top**: one for each supply, and the **Site total**. They count only what is metered ("monitored only"), and they add watts across supplies, never amps. A total with a missing reading says **INCOMPLETE**, shows only the monitored part, and is not saved as a figure, so a graph never shows a false drop in load. Meters with no supply above them are listed in an **Unassigned** group and still count in the site total.
- **The tree**: one block for each supply, with its units below. Each unit shows its name (ISU01, CDU02 and so on) and the name you gave it, and the power it is carrying now. A unit with a meter also shows a load bar with the text, for example "11.2 A of 13 A (86%)" (amber from 80%, red from 100%), and the current on each line. The bar is measured against the lower of the unit's breaker or fuse rating and its device limit, and a line under it says which one limits. A supply, intake or distribution unit with no meter shows its breaker or fuse rating, or its kVA. A unit with no meter says **NOT MONITORED**, never zero. A meter that is not answering says **NO READING**, and an old reading is dimmed and says **OLD**.
- **Unmonitored (calculated)**: for a unit that has a meter and feeds other units, its power minus the power of everything fed from it. This is load Stagewatch cannot see, plus cable and board losses and meter error. It is only worked out when every unit fed from it is metered and reading. Otherwise it says **incomplete**. If what is fed from a unit reads more than the unit itself, it says **CHECK**: look at the Upstream, the line and the channel in Admin. A negative figure is never shown as a load. A meter measured against its own circuits is not shown, because that would always be zero.
- **UPS**: a UPS shows what goes in and what comes out, the **Loss and charging (calculated)** between them and its **Efficiency**, only when both are reading. If it gives out clearly more than it takes in, it says **MAY BE ON BATTERY**. Below a UPS is its own supply. A UPS with no meter leaves its supply's total incomplete, because its losses are not known.
- The arrow beside a unit opens or closes what is fed from it.

Two supplies that feed separate parts of the site (for example a generator at the delay towers) appear side by side as separate trees.

On a wall screen the figures are large, and the line currents and the explanation are hidden; the limit line stays. On a phone the tree is indented less so it fits.

## Not yet tested on hardware

The unmonitored gap, the CHECK warning, the UPS loss and efficiency and the totals are worked out from hand-checked cases and from the emulator. They have **not yet been tested against real meters**. Treat them as a guide until they have been.

## What it is not

The calculated figures are worked out by Stagewatch from the meters, saved with the other readings, and always marked as calculated. Loads that have no meter are not guessed. The typed allowances in Admin are not used here yet. Voltage drop, cable loss and the supply frequency are not shown yet.
