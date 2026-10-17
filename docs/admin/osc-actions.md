# OSC actions: start music, recording or a cue on time

Stagewatch can send a short OSC message (a network message that show software understands) to
QLab, Bitfocus Companion or similar software:

- **at a time in the running order**, for example "18:30 start the walk-in music" or "19:00 start recording";
- **when a threshold alarm is raised, cleared or acknowledged**, for example "wind alert raised: fire a QLab cue that plays an announcement".

> **Actions are for show audio and media. Never use them to control rigging, hoists, power or
> safety systems.** Stagewatch never mutes or changes the PA by itself.

Not yet tested with real QLab, Companion or Reaper. The messages have been tested against a test
receiver on the same computer only. Try each action on your own setup before a show.

## How it keeps you safe

- **Only admins** can make targets and actions. Dashboards never show them.
- **Actions are off after every start of Stagewatch.** Nothing is sent until an admin presses
  **Arm actions** on the Schedule page. They stay armed until someone presses **Switch actions off**.
  Every arm and switch-off is written to the alarm log.
- **Never late.** An action is sent only if Stagewatch is running at its time (2 seconds of leeway).
  If Stagewatch was off, restarting, or actions were off at that moment, the action is logged as
  **missed** or **off (not armed)** and is never sent later.
- **Rate limits.** At most 5 messages to one target in 10 seconds, and 60 an hour in total.
- **Every send is logged** in the **Action log** on the Schedule page: time, what, target, address
  and result.
- Stagewatch sends on this computer's clock. Keep the computer synced to network time.

## 1. Add a target

1. Open **Admin** and open the **Action targets** card.
2. Click **Add target**.
3. Type a **Name** (for example "QLab"), the **Address** of the computer that runs it (for example
   `192.168.1.20`, or a name ending in `.local`) and its **Port**.
   - QLab listens on port 53000. Check "OSC access" in the QLab workspace settings.
   - Companion listens on port 12321.
4. Click **Save targets**.
5. Click **Send test message**. It asks first, then sends `/stagewatch/test`. Watch for it in your
   software's OSC log.

Only local network addresses work. Broadcast addresses (ending in .255) and internet addresses are refused.

## 2. Add an action to the running order

1. Open the **Schedule** page (**Admin → Schedule → Open schedule editor**).
2. Click **Add item** and set **Kind** to **Action (OSC)**.
3. Type the **Start** time and a **Title** (the label, for example "Walk-in music").
4. In the panel that opens, choose the **target** and type the **OSC address**, for example `/go`
   or `/cue/1/start` for QLab, or `/location/1/0/1/press` for Companion.
5. Click **Add value** if the message needs numbers or text.
6. Optional: type an **End** time and a second message to send then (for example `/record` with the value 0).
7. Click **Save schedule**.

Any other line (an act, doors) can also carry messages: click **Actions** on that line. They follow
the line's own start and end times. If you move a line that has actions, Stagewatch tells you which
actions move and asks before saving.

Each line shows its result: **waiting**, **sent 19:00**, **failed**, **missed** or **off (not armed)**.

## 3. Arm actions for the day

1. On the Schedule page, click **Arm actions** and confirm.
2. The bar says **● Actions ARMED**. Due actions now send at their time.
3. At the end of the day, click **Switch actions off**.

## Threshold actions

1. In **Admin → Threshold alarms**, open **OSC actions** under a threshold.
2. Fill in any of **When raised**, **When cleared** and **When acknowledged**.
3. A value can be `{value}` (the reading), `{level}` (1 to 3) or `{label}` (the threshold's label).
4. **Cool-down**: each trigger sends at most once in this many seconds (60 unless you change it).
5. Click **Save thresholds**.

Threshold actions also need actions to be armed. An alarm that is already active when Stagewatch
starts sends nothing; the next time it clears and comes back, it sends.

## Alarm messages to OSC output

In **Admin → OSC output**, tick **Alarm messages** for a destination. Each threshold alarm change is
then also sent as `/stagewatch/alarm/event` with the alarm id, level (0 to 3), `raised`, `acked` or
`cleared`, and the message. This is information only, so it does not need actions to be armed.

## Print and export

- **Print** on the Schedule page shows action lines with their time and label only.
- **Export CSV** leaves action lines out, and importing a running order never makes an action.

## If it didn't work

- **Result "failed: Could not find that target"**: check the name or address on the Action targets card.
- **Result "off (not armed)"**: actions were off at that time. Arm them; past actions are not sent.
- **Nothing arrives**: check the port, and that the receiving software accepts OSC from the network.
  See [Troubleshooting](../troubleshooting.md).
