# Show report

At the end of a show day you can make a **report**: a printable summary of what Stagewatch recorded.
It covers temperatures and other sensor readings, alarms, markers, node outages, and any time when
Stagewatch itself was not recording. Use it for your own records, or hand an excerpt to a promoter or
production office.

The report only **reads** what is saved. It never changes your history and never controls anything.

Back to [Admin](README.md).

## Make a report

1. Open **Admin** and stay on the **Show day** tab.
2. Open the **Show report** card.
3. Choose the **Show day**. The show that is running now is marked "so far": it gives you a report up
   to this moment.
4. Choose the **Time range**:
   - **Full day**: the whole show day.
   - **Show time (doors to curfew)**: the planned doors and curfew times from the [Schedule](../cards/schedule.md). If the day has no doors or no curfew entry, Stagewatch says so.
   - **Between two markers**: pick the first and second marker.
   - **Choose times**: type a start and end as 24-hour times, for example 19:30 to 23:00. Times before 06:00 count as the next morning. A range can be at most 24 hours.
5. Tick the **sections** you want. Times when Stagewatch was not recording are always shown.
6. Press **Open report** to see it in a new tab, or **Download ZIP** for the report page plus spreadsheet (CSV) files.

To save the report as a PDF, open it and press **Ctrl+P** (**Cmd+P** on a Mac), then choose **Save as PDF**.
Stagewatch remembers your last choices in this browser.

## What is in the report

- **Summary**: the headline figures and anything that needs a look.
- **Gaps in the record**: when Stagewatch was off, when a sensor stopped reporting, and a note if the computer's clock moved. If the report cannot tell how a stop happened, it says so.
- **Environment**, **Wind**, **Sound level** and **Power**: minimum, maximum and average for the range, with charts, where Stagewatch has readings.
- **Alarms** and **Device outages**: what was raised, when it was acknowledged and when it cleared.
- **Markers**, **Running order** and **System events**.

Stagewatch is a guide. The report does not replace the certified monitoring or the person responsible for safety in the event's plan.

## Sharing an excerpt

Tick **Sharing excerpt** when the report is going to someone outside the crew. It leaves out who
acknowledged alarms, where markers came from, the detail of device faults, system events and the
running order. The top of the report lists what was left out. An excerpt of a time range contains
nothing from outside that range.

Network addresses and device identifiers are removed from every report, not just excerpts. Contacts
and setlists are never included.

## If something goes wrong

| What you see | What to do |
|---|---|
| "A report is already being made." | Wait a few seconds and try again. One report is made at a time. |
| "The report took too long." | Choose a shorter range, or make it on the Stagewatch computer with `stagewatch report` (below). |
| "No doors and curfew in the schedule" | Add them in the Schedule, or choose another range. |

## After a crash or a power cut

You can make a report without starting Stagewatch, from the computer that has the data folder, or
from a copy of it (for example on a USB stick).

```
stagewatch report --list
stagewatch report --show 12
stagewatch report --day 2026-10-03 --from 19:30 --to 23:00 --share --out excerpt.html
stagewatch report --show 12 --csv data.zip
```

- `--data-dir FOLDER` points at a data folder, a copy of one, or one of the folders inside `backups`.
- `--list` shows each event and show with its id, day and times. A day that had an unexpected stop is flagged.
- Choose the day with `--show ID`, or `--day YYYY-MM-DD` (add `--event ID` if two events share a day).
- Choose the range with `--from HH:MM --to HH:MM`, `--show-time`, or `--from-marker ID --to-marker ID`. With none of these you get the whole day.
- `--share` makes a Sharing excerpt. `--sections summary,alarms` picks sections.
- `--out FILE.html` and `--csv FILE.zip` say where to save. With neither, the report page is saved in the current folder.

The command works on a **copy** of the history. Your data folder is never changed, and the copy is
deleted afterwards. If a crash left recent readings waiting in the history's `-wal` file, they are
included. If the history file is damaged, the command says so and suggests trying a backup:
`stagewatch report --data-dir <your data folder>/backups/<name> --list`. See
[Updating and backups](../updating-and-backups.md).

If you set the data folder yourself when you start Stagewatch, give the same `--data-dir` here.

## Try it without a show

In emulate mode (`stagewatch --emulate`), the Show report card has **Add a demo day to report on**. It adds a
finished "Demo Festival" day with a seven-minute unexpected stop at 21:40, a node that went missing,
an alarm that was acknowledged, and some markers. It is never added outside emulate mode.
