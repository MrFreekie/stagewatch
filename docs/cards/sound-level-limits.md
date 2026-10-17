# Sound level limits

The sound level limit in force now, the next change, and Smaart's live value beside it. It answers "what is our limit right now, are we over it, and when does it drop?".

**Reference only.** The licence is what counts. Stagewatch shows Smaart's figures next to numbers someone typed in. It never says you are compliant, it does not control the PA, and it is not a compliance record. **Not yet tested with a real Smaart and real limits.**

Part of the [dashboard cards](README.md). Add it to a dashboard under **Admin → User dashboards → Edit cards**. It is public: anyone on the show network who can open the dashboard can read it, so only enter public wording.

## What you see

- **Limit**: the licence limit in force now (or the guidance figure if there is no licence limit), with the name of the Smaart value it is tied to, such as "LAeq 15".
- **Now**: Smaart's latest value for that same name, with one decimal. **▲ OVER the limit** when it is above the number (equal is not over). **No reading** when Smaart has gone quiet or the value is old: that is never shown as OK. **Metric not available** when Stagewatch is not recording that Smaart value.
- **Next**: when the limit changes next, what it changes to, and how long until then.
- The other limits and guidance figures in force (tablet only).
- The **curfew** from the schedule, if there is one.
- **Set by, reference, revision and when it was last changed**, so a wrong or old limit is easy to spot. "Copied from another event, not yet confirmed" means somebody copied the limits and has not saved them since.

On a **phone** it is one compact block; on the **wall** only the limit, the live value and the next change, in big type. With no limits entered it says so on a tablet and hides itself on phones and walls.

## On the Sound level card

Each Sound level box that has a limit gets one more line, for example "Limit now 102 dB until 22:00, then 98". Over a licence limit it adds **▲ OVER** in orange; over a guidance figure, **▲ over guidance** in amber.

## Markers and notices

These are only made live, as things happen. If Stagewatch was off, nothing is filled in afterwards.

- **"Over limit: LAeq 15 103.1 vs 102 (limit)"**: the first time a live value goes above a licence limit. Another marker only after it has been at or under the limit for 2 minutes. A quiet notice stays in the alarm bar while it is over (it never sounds).
- **"Sound level limit now 98 (was 102): LAeq 15"**: when a limit changes, at the time it changes (not if Stagewatch notices more than 5 minutes late).
- **"Sound level limits updated (rev 3): ..."**: when someone saves the limits.
- A quiet notice **10 minutes before a limit drops** ("Sound level limit drops to 98 at 22:00"), cleared 2 minutes after.

Stagewatch looks at Smaart's latest value once a second, so a peak that comes and goes between two looks can be missed; the card itself always shows the latest value. On the two nights a year the clocks change, avoid limit times inside the hour that is skipped or repeated: check the card that night.

Guidance figures show on the cards only: no markers or notices. The notices and the warning time can be changed or switched off in Admin.

## Which Smaart number is compared

Each limit is tied to one Smaart value by its name, as Smaart writes it. **Stagewatch cannot check the Leq period.** "LAeq 15" is only a 15-minute figure if Smaart's Leq is set to 15 minutes. Before the show, check in Smaart that the value's period matches the licence.

There are no 63 Hz or 125 Hz limits yet: Smaart's API has not been shown to send those bands. Use a guidance figure on a Smaart value you do record if you need a reminder.

## Setting the limits (Admin)

**Admin → Sound level limits**:

- **Every day of this event** or **This day only**. A day's own limits replace the event's for that day; **Use the event's limits for this day** goes back.
- One row per limit: the Smaart value (from the values recorded under Sound level), **Licence limit** or **Guidance**, the dB figure, and **From / Until** on the site clock (empty means the start or end of the show day; a time before the day rollover, normally 06:00, is after midnight). Limits for the same value and kind must not overlap.
- **Set by**, **Reference** and **Notes shown on dashboards** are public. **Entered by** and **What changed** are kept in the admin history; "What changed" also goes on the timeline marker.
- **Copy limits from another event** brings in another event's limits, marked "not yet confirmed" until you save.
- Every save is kept as a revision.
