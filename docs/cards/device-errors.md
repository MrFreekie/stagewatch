# Device errors

The **Device errors** card in Admin shows how many problems each device had in a show, and when. It
answers "which box keeps dropping out?".

This is an Admin card. It is not on any dashboard, and only someone logged in to Admin can see it. It
is **experimental**.

> **Tested on hardware: pending.** Checked with Stagewatch's own simulated devices and with sample
> log files only. Treat the counts as a guide and check anything important against the device's own
> log. Stagewatch only reads. It never changes a device.

Back to [Dashboard cards](README.md).

## Open it

1. Open **Admin** and go to the **Sensors and nodes** tab.
2. Find the **Device errors** card.
3. Under **Show**, choose the day to look at. The current show is already chosen.
4. Under **How much**, choose **Just this day** or **All days of this event**.

**What you'll see:** a line such as `3 problems in this day: 0 Stop, 2 Alert, 1 Advisory, 0 drop-outs.`,
then a table with one row for each device that had a problem. `No problems recorded.` means there were none.

## What the table shows

| Column | What it means |
|---|---|
| **Device** | The device's name. A **flapping** tag means 3 or more problems within 10 minutes. |
| **Now** | The device's live status, or `imported` for a unit that only appears in an imported log. |
| **Stop**, **Alert**, **Advisory** | How many problems at each level. |
| **Drop-outs** | How many times a Stagewatch device went offline or faulted. |
| **Time in fault** | The total time from each problem starting to it clearing. `never cleared` shows how many had no end. |
| **Last problem** | The time, level and wording of the latest one. Press **Show** to open the full timeline for that device. |

Press **Show 4 quiet devices** (the number changes) to list the devices with no problems too.

## What counts as a problem

- **One raise is one problem.** A fault that lasts an hour is one problem. A fault reported by forty
  units at the same moment is one problem, not forty.
- A Stagewatch device going offline is a **drop-out**.
- An imported unit's entry counts by its level in the log: Critical is **Stop**, Major is **Alert**,
  Minor is **Advisory**. See [Import a log file](../admin/import-log.md).
- If the level rises during one problem, it stays one problem at its highest level.
- **Not counted:** information lines, clears, acknowledgements, software updates and session lines.
- **Limit alarms are not device problems.** A stage that is too hot is the environment, not a faulty
  box. Those stay in the alarm log.

## When Stagewatch was not running

Stagewatch can only count what it saw. If it was switched off or restarted during the show, the card
lists the break: `Stagewatch was not running 14:05 to 14:20, so problems in that time can't be counted.`
Nothing is filled in for that time.

A problem still going when Stagewatch stopped ends at that point. The timeline says
`(ended when Stagewatch restarted)`. Other endings are `(still going)`, `(still going when the day
ended)` and `(never cleared in the log)`.

## Save it as a file

Press **Save as CSV** to download the table. The file opens in a spreadsheet. It has one row for each
device, with the counts, the seconds in fault and the last problem, and is named like
`device-errors-main-stage-2026-10-18.csv`. Press **Refresh** to read the latest figures first.

## Also on the Sensors and nodes tab

Each device in the **ESPHome nodes** card shows a link such as `2 problems this show` when it has had
any. The link jumps to this card.

## If it didn't work

| What you see | What to do |
|---|---|
| `No problems recorded.` but a device did drop out | Check the **Show** and **How much** choices. Then check for a `Stagewatch was not running` note covering that time. |
| An imported unit is missing | Import its log first: [Import a log file](../admin/import-log.md). |
| The card shows an error instead of a table | Press **Refresh**. If it stays, see [Troubleshooting](../troubleshooting.md). |

Related: [Sensors and alarms](../admin/sensors-and-alarms.md), [Glossary](../glossary.md).
