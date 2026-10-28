# Import a log file

After a show, you can read an **L-Acoustics Network Manager** log, or a **Meyer Sound Nebra** Events
CSV, into Stagewatch. Faults and power actions then appear as markers on that show's timeline, and the
units appear on the [Device errors](../cards/device-errors.md) card. It is **experimental**.

Stagewatch is independent. It is not made, approved or supported by L-Acoustics or Meyer Sound. See the
[notices](../integrations/README.md#notices).

> **Tested on hardware: pending.** Built and checked with sample log files only. It has not yet been
> used on a real show's log by someone other than the project owner. Check what it says against Network
> Manager's own screen.

> **It only reads a copy of the file.** Stagewatch never talks to Network Manager or to the
> amplifiers. Nothing is added until you press **Import into this show**, and every import can be undone.

Back to [Admin](README.md).

> **Nebra: tested on hardware: pending.** The Nebra reader was built from the owner's two sample files
> (Nebra 2.6.9) and anonymised test files. What "locked" and "unlocked" mean in Nebra is understood from
> a comparison with the Network Manager log of the same show. Check the preview against Nebra's own screen.

## Before you start

1. Find the log on the computer that runs Network Manager. It is in the L-Acoustics folder inside
   `AppData`, under LA Network Manager, in **Log**. The name starts `Log_` and ends with the date and
   time, and the file ends in `.xml`.
2. Copy the file to the computer you use for Admin, for example on a USB stick.
3. Check what time zone that computer was in, and whether its clock was right. The log has times but
   no time zone.

For Nebra, export the **Events** report. The file is named like `2026-10-24_13-50-12_EventsReport.csv`,
has no header row and ends in `.csv`. The System Status Report (`.txt`) is not an import: see
[Preview a Nebra status report](#advanced-preview-a-nebra-status-report).

## Import it in Admin

1. Open **Admin** and go to the **Sensors and nodes** tab.
2. Find the **Import a log file** card.
3. Next to **Log file**, click **Choose file** and pick the `.xml` file (Network Manager) or the `.csv`
   file (Nebra). **Kind of log** changes to match. Check it, and change it if it is wrong.
4. Under **Show**, choose the show day the log belongs to. The current show is already chosen.
5. Optional: type the **Time zone of that computer**, for example `Europe/London`. Leave it blank to
   use this site's time zone.
6. Optional: type a **Clock offset (seconds)** if the laptop's clock was wrong. Type the seconds to
   add: a laptop 5 minutes slow is `300`.
7. Optional: tick **Also add markers for going online or offline and for Acknowledge all**, or **Also
   add markers for entering and leaving Load Checker** (it can make inputs unlock).
8. Read the preview.
9. Click **Import into this show**, or **Cancel**.

**What you'll see:** after step 3 the preview says which period the file covers and which period the
show day covers, then counts: new entries to add, markers, entries already imported, entries outside the
show day, and session lines left out. A **Faults in the file** list shows each fault with its unit and
how long it lasted. Changing the show, time zone, offset or ticks refreshes the preview.

After step 9: `Imported 120 entries and 4 markers. Use Undo below to take it back out.`

### What the preview warns about

| What you see | What it means and what to do |
|---|---|
| `Nothing falls on this show day. Check the show, the time zone and the clock offset above.` | The file's times and the show day do not overlap. Pick the right show, or fix the time zone or offset. |
| `Everything in this file is already imported.` | You have imported this file before. Nothing more is added. |
| `LA Network Manager had not closed this log...` | Network Manager was still running, or stopped suddenly. Stagewatch reads up to the last whole line. |
| `That file is too big to be a log from Network Manager or Nebra.` | The file is over about 5 MB of text. It is probably the wrong file. |
| `No Nebra events were found in that file.` | Choose the Events `.csv` from Nebra, with **Kind of log** set to Nebra. |
| `3 times fell in the hour when the clocks went back...` | Not an error. The time zone has two possible readings for those times, so Stagewatch reads them in file order: the first pass through that hour first, then the second. Check the times against Nebra's screen. |
| `... lock changes are already covered by the LA Network Manager log` | Not an error. Those lines are the same event and were left out, so nothing is counted twice. |
| `No LA Network Manager entries were found in that file.` | Choose a `Log_` file from Network Manager. |
| `That file has too many entries to import at once.` | More than 50,000 entries. Nothing was changed. |

### Undo an import

At the bottom of the card, **Imported into the selected show** lists each import with its file name,
period, number of entries and number of markers. Click **Undo**, then confirm. Its entries and markers
are removed from the show.

## What gets stored

- **Every entry in the show day** is stored, with the file's own time kept beside the corrected one.
- **Markers** are added for serious faults (Alert and Stop). The same fault on several units within
  2 seconds becomes one marker, naming up to three units and then counting the rest. Markers are also
  added for engineer actions such as **Standby All Units** and **Waking Up All Units**.
- **Session lines are left out**, because they hold the laptop's addresses. The two optional ticks add
  only a marker for each of the lines named in them.
- **Entries outside the show day are left out**, not moved. Nothing is guessed.
- **Nebra:** every line in the show day is stored except the "Database update" lines, which are session
  lines (tick the option to add a marker for each). A unit that disconnects is an Alert and gets a
  marker, one marker for many units within 2 seconds. A lost stream lock is an Advisory with no marker.
  A Hive client (another computer running Nebra) is stored as "Hive client", never with its computer's
  name. Wording Stagewatch does not know is stored as information and counted as no fault.
- **Nebra and Network Manager together:** a Nebra lock change for a unit that Network Manager already
  logged as an AVB stream unlock within 10 seconds is left out and counted, so the Device errors card
  does not count it twice. The unit is matched by its number (`STAGE.AMP 104` and Network Manager unit
  `104`).
- **Importing the same file twice adds nothing.** Overlapping files add only the entries that are new,
  and markers only from new entries.

## Advanced: the command line

The same import is available on the Stagewatch computer. It needs access to the data folder and is not
available over the network. Without `--write` it only shows a preview.

```bash
stagewatch import-log PATH/TO/Log_FILE.xml
```

When the preview looks right, add `--write`:

```bash
stagewatch import-log PATH/TO/Log_FILE.xml --write
```

If you run Stagewatch from the downloaded project folder, put `uv run` in front of `stagewatch`.

**What you'll see:** the file's period, the show day's period, the counts, and the list of faults, then
`Nothing has been imported. Add --write to import it.` After `--write`:
`Imported 120 entries and 4 markers (import lanwm-...).`

| Option | What it does |
|---|---|
| `FILE` | The log file to read. Files over 20 MB are refused. |
| `--format FORMAT` | `lanwm` (L-Acoustics Network Manager), `nebra` (Nebra Events CSV) or `nebra-status` (Nebra status report, preview only). Without it, Stagewatch works out which from the file. |
| `--events FILE` | With `nebra-status`: the Events CSV too, to list units it names that the report does not. |
| `--show ID` | The show to import into. Without it, the current show. If nothing falls on the show day, the command lists recent shows with their ids. |
| `--tz ZONE` | The time zone of the computer that wrote the file, for example `Europe/London`. Default: this site's time zone. |
| `--offset SECONDS` | Seconds to add if that computer's clock was wrong. Within 24 hours either way. |
| `--session-markers` | Also add markers for going online or offline and for Acknowledge all (Nebra: for database updates). |
| `--load-checker-markers` | Also add markers for entering and leaving Load Checker. |
| `--write` | Import it. Without this, only a preview. |
| `--data-dir DIR` | The data folder to use. Default: the usual one. `stagewatch --print-data-dir` shows which. |
| `--emulate` | Use the demo data folder. |
| `--force` | Run `--write` even though a Stagewatch server seems to be running on that data folder. |

With `--write`, the command stops if Stagewatch is running on that data folder:
`error: Stagewatch appears to be running on this data folder. Import from the Admin page instead, or stop it first, or use --force.`
Use the Admin card instead, which is the safer way while Stagewatch is running.

### Advanced: preview a Nebra status report

The System Status Report is a snapshot, not a log, so it is only previewed. Nothing is stored.

```bash
stagewatch import-log --format nebra-status PATH/TO/SystemStatusReport.txt --events PATH/TO/EventsReport.csv
```

**What you'll see:** the Nebra version and the time of the report, how many units are Available, any
active alerts, firmware versions with how many units run each, streams that report a failure code other
than `No Error`, and (with `--events`) units the events file names that are not in the report, marking
any whose last event was a disconnect. A unit that has gone can be missing from the report without an
alert, so this is the check for it. Serial numbers, MAC addresses and computer names are never read or
shown. `--write` is refused for this format.

There is no command-line undo. Use **Undo** on the Admin card.

## If it didn't work

| What you see | What to do |
|---|---|
| `That time zone is not one this computer knows.` | Type a zone name such as `Europe/London`, or leave it blank. |
| `The clock offset must be within 24 hours.` | Check the number of seconds you typed. |
| `That show does not exist.` | Choose a show from the list. With the command, check the `--show` id. |
| `There is nothing new to import from that file.` | Everything in it is outside the show day or already imported. |
| Times look hours out | Check **Time zone of that computer**. The log has no time zone of its own. |

More: [Troubleshooting](../troubleshooting.md).

Related: [Device errors](../cards/device-errors.md), [Markers](../cards/markers.md).
