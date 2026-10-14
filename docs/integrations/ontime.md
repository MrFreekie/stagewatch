# Ontime

**Ontime** is free, open-source software that runs a show's rundown, countdown and clock. It is made
by Carlos Valente and its code is at <https://github.com/cpvalente/ontime>. Stagewatch can listen to
Ontime, so the stage manager's countdown and clock can sit on the same dashboard as your alarms and
readings.

This page is for the person who sets Stagewatch up. It covers the connection. What each card shows
is on the card pages: [Wall Clock](../cards/wall-clock.md), [Ontime Timer](../cards/ontime-timer.md)
and [Ontime Rundown](../cards/ontime-rundown.md).

Stagewatch is independent. It is not made, approved or supported by Ontime. See the
[notices](README.md#notices).

> **Test status.** Tried on a real Ontime, version **4.14.0**, in its rolling state only. Pause,
> ready, stopped, running over and added time are built and checked with made-up data, but **not
> yet tested** against a real Ontime. See [Tested status](#tested-status).

## What Stagewatch reads, and what it never does

Stagewatch **only listens** to Ontime. It never starts, pauses, edits or changes anything in it.

It reads:

- The clock (time of day) and the main countdown, with the title and the warning times of the loaded
  event.
- Where the show is in the day: the event number and how many there are, the planned start and end,
  when Ontime expects to finish, when it started, and whether it is ahead or behind.
- The title and note of the running event.
- Only while a dashboard has the **Ontime Rundown** card: the list of events (cue, title, start and
  end time, and whether one is skipped).

It does not read colours, custom fields, triggers, messages, or the details of the next event.

### What Stagewatch sends

Every request is a plain read. Stagewatch never sends a command, and it never follows a redirect.

| What | When |
|---|---|
| A listening connection to `/ws` on the Ontime address | While a dashboard has a card that needs Ontime. Stagewatch sends **no data** down it. Only the connection's own keep-alive goes back. |
| `GET /api/version` | When it connects, and when you click **Test connection** |
| `GET /api/poll` | Once a second, **only** while the listening connection is not working, until Stagewatch can try it again |
| `GET /data/rundowns/current` | **Only** while a dashboard has the Ontime Rundown card: when it connects, when the running event or the number of events changes, and at least once a minute. Never more often than every 5 seconds. |

Replies over 1 MiB (about one million characters) are refused.

Stagewatch contacts Ontime only while a dashboard has one of these cards: **Ontime Timer**,
**Ontime Rundown**, or **Wall Clock** with its time source set to Ontime. With none of those on any
dashboard, Stagewatch does not contact Ontime at all.

## Connect Stagewatch to Ontime

1. Make sure Ontime is running.
2. Find the address and port of the computer that runs Ontime. If Ontime runs on the same computer
   as Stagewatch, use `127.0.0.1`. See [IP address](../glossary.md) in the glossary.
3. Open **Admin** and find the **Ontime** card. It is closed at first. Click its heading to open it.
4. Under **Connection**, type the address in **Ontime address (Ontime computer's address and
   port)**. Write it as `http://` then the address, a colon and the port, for example
   `http://192.168.1.50:4001`. It starts as `http://127.0.0.1:4001`. Check the port against your
   Ontime. Stagewatch accepts plain `http://` only, with nothing after the port.
5. Click **Test connection**.
6. Click **Save**.
7. Under **User dashboards**, click **Edit cards** on a dashboard, tick **Ontime Timer** or **Ontime
   Rundown**, and click **Save dashboards**. See [Dashboards](../admin/dashboards.md).

If you want the **Wall Clock** card to follow Ontime, also set its **Time source** to **Ontime** in
the **Wall Clock** card in Admin. Stagewatch never switches the time source by itself.

**What you'll see:** after **Test connection**, `Ontime 4.14.0 answered.` (with your version number).
Once a card is on a dashboard, the lines under **Connection** change from "not running" to
`connected, last message 14:02:11, Ontime version 4.14.0`.

### One connection for all three cards

All three cards share **one** address and **one** connection. The settings are all in the **Ontime**
card:

| Setting | What it does |
|---|---|
| **Ontime address** | Where Ontime is. Used by every Ontime card. |
| **Warn if more than this many seconds out** | Wall Clock only, and only when its time source is Ontime. The clock warns if Ontime and the Stagewatch computer differ by more than this. Starts at 2 seconds. You can use 1 to 60. |
| **Show the event title on dashboards** | Ticked to begin with. See below. |

Click **Save** in the **Ontime** card after any change.

### Hide the event titles

The title of an event in Ontime is often an artist's name. **Dashboards have no password**, so
anyone on the show network can read what a dashboard shows.

To keep titles off the screens, untick **Show the event title on dashboards (also the Ontime Rundown
card's titles and notes)** and click **Save**. This one tick covers both cards:

- The **Ontime Timer** shows no title.
- The **Ontime Rundown** shows no titles or notes. Its list keeps the times and cues, and shows
  `Event` in place of each title.

You can also leave the Ontime Rundown card off any dashboard that should not show titles.

### The Ontime logo

The Ontime cards show a small Ontime logo, so people can see where the numbers come from. The logo
belongs to the Ontime project. It is shown only for that reason. See the [notices](README.md#notices).

## How to check it worked

1. In Admin, open the **Ontime** card and click **Test connection**.
2. Add the **Ontime Timer** or **Ontime Rundown** card to a dashboard, then open that dashboard.

**What you'll see:** the card shows Ontime's countdown or position, and a small green `●` **Online**
beside the logo.

## If it didn't work

When Stagewatch cannot hear Ontime, the card says so, and a **quiet notice** appears in the alarm
bar. It never makes a sound. See [Alarm bar and notices](../cards/alarm-bar.md).

| What you see | What it means and what to do |
|---|---|
| `Can't reach Ontime at that address.` | Nothing answered. Check Ontime is running, the address and port are right, and both computers are on the same network. If Ontime runs on another computer, that computer's firewall must let this one reach Ontime's port. |
| `Ontime did not answer in time.` | Something is there but is slow. Check Ontime is running and not overloaded, then try again. |
| `Something answered, but not like Ontime.` | The address is right but something else is using that port. Check the port in Ontime. |
| `The address must look like http://host:4001` | The address is not in the form Stagewatch accepts. Use `http://`, the address, a colon and the port. |
| `A test is already running. Wait a few seconds and try again.` | Click **Test connection** once and wait. |
| The card says **Offline** or `OFFLINE` | Stagewatch cannot hear Ontime. Check Ontime is running. Tap the small status mark beside the logo for details. |
| The card says **Stale** | Nothing has arrived for a few seconds. The figures are old and struck through. Do not trust them. |
| The card says `CAN'T READ` (Rundown) | Ontime sent something Stagewatch could not read. The last good figures stay, struck through, until a readable update arrives. |

More: [Troubleshooting](../troubleshooting.md).

## Tested status

- **Tested against a real Ontime 4.14.0:** the rolling state of the countdown. The Wall Clock time.
  Which way ahead and behind go: a positive offset is **behind** and a negative one is **ahead**.
  The Rundown card with one recorded situation (running event 9 of 16, offset 0), and a show running
  early or late (Ontime sends a negative expected end, which Stagewatch now reads).
- **Not yet tested against a real Ontime:** pause, ready, stopped, running over and added time.
  What "Finished" and "No rundown" look like. Groups and milestones in the event list. A rundown
  that runs over several days. These are built from what Ontime's documents say and from made-up
  test data. If one looks wrong on your show, please say which.
- **Tested on hardware: pending** for everything else, including a full show day.

Ontime is made by others and can change. A new Ontime version may need a change to Stagewatch.

Related: [Notices](README.md#notices), [Glossary](../glossary.md), [Troubleshooting](../troubleshooting.md).

Back to [Integrations](README.md).
