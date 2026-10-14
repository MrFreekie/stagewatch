# Alarm bar and notices

The **alarm bar** is the strip across the top of every dashboard that tells you something is wrong.
It is not a card, so it cannot be switched off. It appears **only when there is something to
say**. A dashboard with no cards still shows it.

Stagewatch is an **advisory tool**. The alarm bar tells you what the sensors and links report. It
does not decide what you must do, and it does not control anything. Your event's own safety, wind
and power plans do that. Back to [Dashboard cards](README.md).

## What the alarm colours mean

Every alarm has a **word** on the bar as well as a colour. The word is in capitals at the start of
the line.

| Word | Colour | What it means |
|---|---|---|
| **ADVISORY** | Amber | Something to be aware of. |
| **ALERT** | Orange | Needs attention. |
| **STOP** | Red | Serious. Follow your event's own plan. |
| **NOTICE** | Grey, no level colour | A quiet message. It never beeps. See [Quiet notices](#quiet-notices). |

The bar takes the colour of the worst alarm in it.

An alarm appears for one of these reasons:

- **A limit was crossed.** Your tech sets the limits. The line leads with the name of the reading,
  for example `Temperature: 36.2 above 35`.
- **A sensor node has gone offline.** This is an **ADVISORY**, such as `Stage L node: missing (connection
  lost)`. The reason in brackets is always a short plain phrase, such as `can't reach the node`,
  `connection refused`, `timed out`, `name not found` or `wrong encryption key`. Addresses and
  technical text go to the log, not the screen.

**Nothing alarms until your tech adds limits.** The only alarm out of the box is the node offline
one. See [Admin: Sensors and alarms](../admin/sensors-and-alarms.md#threshold-alarms).

## When an alarm is sounding

An alarm is **sounding** while it is not acknowledged. Then:

- The bar pulses slowly.
- A two-tone beep repeats every 2 seconds, if the **Alarm sound** button on that screen is on. See
  [Alarm sound button](sensors.md#alarm-sound-button). Do not rely on beeping being heard on a
  tablet. Turn the volume up, and keep watching the colours.

## Acknowledge

**Acknowledge** stops the beeping on every screen. It does **not** fix anything.

1. Tap **Acknowledge** at the end of the alarm bar.

**What you'll see:** the beeping and pulsing stop. The line stays on the bar, marked
**(acknowledged)**, until the problem clears.

The **Acknowledge** button only shows when two things are true:

- Something is sounding.
- This dashboard is allowed to acknowledge. Your tech sets that with **Ack alarms** in **Admin →
  User dashboards**. The FOH dashboard can by default. The phone and wall dashboards cannot. Someone
  logged in to Admin can acknowledge from any dashboard.

It acknowledges every sounding alarm at once.

**Markers.** An **ALERT** or **STOP** also drops a marker on the timeline that starts with
`ALARM:`. Acknowledge hides that marker. It stays in the history: tick **Show hidden** under the
marker list to see it. If the alarm clears by itself, its marker stays on the chart. See
[Markers](markers.md#alarm-markers).

## Quiet notices

Some messages are **notices**. They show as **NOTICE** in grey, with no level colour. They:

- **Never beep.** They never set off the sounding alarm or the beeping.
- **Cannot be acknowledged**, so they have no **Acknowledge** button.
- Clear by themselves when the problem goes.

Notices come from Stagewatch's links to other software and kit: Ontime, Smaart, GLOBCON and Shelly
power meters, if one stops answering. A venue without Ontime running must never beep. The
Barometer's optional "falling quickly" notice is quiet too. A quiet notice does not count in the
messages bell's alarm count; it shows as blue **i** Information.

## How long notices stay on screen

So the bar does not fill with old news, **advisory** notices time out on the dashboard. This is the
full list:

| What | What happens |
|---|---|
| An **acknowledged** advisory | Leaves the bar **2 minutes** after it was acknowledged. |
| A **quiet** advisory notice that has not changed for **30 minutes** | Moves into a fold-out under the bar, **Older notices (n)**. Tap it to read them. Nothing is hidden silently. |
| An advisory that is **still beeping** | Stays until someone acknowledges it. |
| **ALERT** and **STOP** | Never time out. They stay until the problem clears. |
| The problem **changes or comes back** | The notice shows again straight away and its timer starts again. A change of reason alone, such as `timed out` to `can't reach the node`, updates the line but does not restart the timers. |

A notice leaving the bar does **not** mean the problem has gone. A node that is still offline is
still marked **missing** in the [Sensors](sensors.md#what-the-status-means) list.

The server's clock decides, not the tablet's, so a page reload shows the same list. Your tech can
change both times, or set either to 0 for "never", in **Admin → Alarm notices**. See
[Admin: Sensors and alarms](../admin/sensors-and-alarms.md#alarm-notices). No flashing or sound changes.

## On the wall screen and in the messages bell

- **Wall screen:** the bar shows at most three lines, with the worst first. The rest become one line
  such as `+2 more`. The text is large enough to read from the back.
- **Messages bell:** the bell in the header lists the same items as the bar, so the two always
  agree. Acknowledged notices drop off the same way, and old quiet ones are marked **(older)**. The
  wall screen has no bell. See [the dashboard header](sensors.md#messages-bell).

## What happened earlier

**Admin → Alarm log (this show)** lists what alarmed, and when, for the current show day. It is for
your tech. See [Admin: Sensors and alarms](../admin/sensors-and-alarms.md#alarm-log).

## If it didn't work

- **The alarm keeps beeping.** Tap **Acknowledge** on a dashboard that allows it, such as FOH. If
  there is no button, this dashboard is not allowed to acknowledge. Use another, or ask your tech.
- **There is no Acknowledge button.** Nothing is sounding, or this dashboard is not allowed to
  acknowledge.
- **The notice has gone but I think the problem is still there.** Look at the
  [Sensors](sensors.md) card. A notice can time out while the problem remains.
- **There is no sound.** Tap the **Alarm sound** button once. See
  [the dashboard header](sensors.md#alarm-sound-button).
- More help: [Troubleshooting](../troubleshooting.md).

## Tested status

**Tested on hardware: pending.** The alarm bar, the notice timers and Acknowledge have been checked
with simulated alarms and times, not yet through a real show day with real nodes. The messages bell
has not yet been checked on real screens by the project owner.

Related: [Glossary](../glossary.md).
