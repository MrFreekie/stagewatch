# RME interfaces (12Mic)

**RME** makes audio interfaces that sit on an AVB network, such as the 12Mic, a 12-channel remote-controlled
microphone pre-amplifier. Stagewatch can listen to the status of each unit and show it on the **RME interfaces**
card: whether the unit is online, how hot it is, whether both power supplies are on, which clock it follows,
which preset is loaded and what the unit itself says is wrong.

Stagewatch is independent. It is not made, approved or supported by RME. See the [notices](README.md#notices).

> **Test status.** Tried on one RME 12Mic (firmware 2.0.1) only, from two recordings of its own web page. **Other RME
> units are untested.** Compare the card with RME's own web page before you rely on it. The words for a few of the
> unit's own states (Warning, No Routing, Issues with Input) have not been seen yet, so an unfamiliar word is shown
> as **Unrecognised** and never as OK.

## Anyone on the network can change an RME unit

RME units have **no login**. RME's own manual says their remote protocols are not protected against misuse, so any
device on the same network can change gain, 48 V, presets and the clock. Keep the AVB and control network separate
from guest and public Wi-Fi. Stagewatch only listens, and holds no password for the unit because there is none.

## What Stagewatch does

- **It only listens.** It opens one connection to each unit's status socket (a live web connection the unit's own
  page uses) and sends it **nothing**: no request, no question, no setting. It cannot change gain, 48 V, a preset,
  routing or the clock. A test checks that a unit receives nothing from Stagewatch.
- **Equipment, not air.** The unit's core temperature and its two power supplies are recorded as Equipment readings.
  They never count towards the site averages, the speed of sound or the barometer. The Equipment card shows only the temperature; the supplies are On or Off words on the RME interfaces card.
- **Changes are marked as they happen.** While Stagewatch is watching, a lost or restored power supply, a changed clock
  source, a clock that loses lock and a different loaded preset each add a marker, for example
  `RME Stage L: PSU 2 lost`. A unit going offline and coming back also adds markers. Things the unit says are worth a
  look (its Notice states) add **hidden** markers, which stay in the history and reports but stay off the chart.
- **Nothing is filled in.** If Stagewatch was not running, or the unit was away, there is a gap and a marker saying
  `back after 4 min (readings missing meanwhile)`. What happened in the gap is never guessed. Restarting Stagewatch
  does not add markers for what happened while it was off.
- **No sound.** Every notice from this card is silent. It appears on screen and in the alarm log, and never sounds.
- **Not read:** input gain, 48 V, labels, routing and the level meters. They are not recorded or shown.

## What the words mean

The card uses RME's own "soundness" words and says which area they come from.

| The unit says | Stagewatch shows | Level |
|---|---|---|
| Good | **OK** | none |
| Inactive | **Not monitored** (grey) | none |
| Notice, Notice in progress | **CHECK** (triangle) | Advisory |
| Warning | **WARNING** (cross) | Alert |
| No Routing, Issues with Input | **Info** (circle), in the fold-out only | none |
| Anything else | **Unrecognised: word** (question mark) | Advisory, never OK |

- A lost power supply is an **Alert** when the unit's own redundancy alert is on. Without it, a supply that goes off
  while Stagewatch is watching is an **Advisory**. A supply that was already off when Stagewatch connected is shown as
  off, but is not treated as a fault: a unit with one supply fitted reports the other as off all day.
- The overall word on a unit's tile is the worst of these, or **OFFLINE** when Stagewatch cannot reach the unit.
  When a unit is offline its last values stay on screen, dimmed, with how long ago they were true.
- "Clock locked" is read from the unit's own sync flag for the clock source it follows. It is not shown for the
  Internal clock.
- "Streams active" counts the AVB input streams the unit reports as Good.

## Set up

1. Give the unit an address on your network (RME's own page or its front panel). Use a fixed address.
2. Open **Admin**, go to the **Integrations** tab and click the **Add integration** tile. In the pop-out, pick
   **RME interface** from the **Product** list.
3. Type the unit's address in **Unit address** (for example `192.168.1.50`) and click **Test**. A good result reads
   like `RME 12Mic, firmware 2.0.1, named 'Stage L'. Reading works.` It shows only the model, firmware and name, and
   never the serial number or the address. **Save unit** stays switched off until a Test has worked.
4. Give it a name if you want a different one (it starts as the unit's own name), type an **Area** if you like, and
   click **Save unit**.
5. The unit now has its own tile on the **Integrations** tab and its own **RME interfaces** card below, with its
   status, a rename box and **Remove**. Under **User dashboards**, click **Edit cards** on a dashboard and tick
   **RME interfaces**. See [the card](../cards/rme-units.md).

You can add up to eight units. Under **Advanced** on each unit you can change the address and port (usually 80), and
use **Replace unit** after a hardware swap, so the new unit takes over the old unit's name and history. **Record input
changes (not available yet)** is shown switched off: gain and 48 V are not read yet.

The address must be an address on a private network, such as `192.168.x.x`, `10.x.x.x` or `172.16.x.x`. **Host names
(including `.local` names) are refused**, and so are public addresses. If the unit tries to send Stagewatch somewhere
else, Stagewatch stops and says so.

### Plain failure messages

| Message | What to check |
|---|---|
| That address is not on a private network | Use the unit's own address on your network |
| Nothing answered at that address | The unit is switched on, the address is right, and this computer can reach the unit's network |
| Something answered, but not like an RME unit | The address belongs to something else |
| Connected, but the unit sent nothing in 8 seconds | Try again; check the unit |
| A different RME unit answered here | The address was reused, or the unit was swapped. After a swap, choose **Replace unit** under Advanced |
| The unit tried to redirect Stagewatch, so it stopped | Check the address |

**Test** opens a second short connection to the unit and closes it at once. If a unit limits how many remote
controls it accepts, it may refuse it. RME says two or more controllers stay in step, so one extra listener is expected
to be harmless, but this has been tried on one unit only.

## Offline and gaps

Stagewatch drops the link if the unit sends nothing for 10 seconds (the unit normally sends meter data about 66 times a
second, which Stagewatch counts and throws away), then tries again after 1 second, doubling up to 30 seconds. While the
link is down the unit's device status is **Missing**, with a silent notice. It is never shown as online until the unit's
full status has arrived.

If the unit stays connected but sends no changes for 2 minutes, the tile adds `no updates for N min`. That is
information, not an outage: a quiet unit has nothing to say.

## Sizes and limits

Each unit sends about 70 KB a second of meter data that Stagewatch cannot switch off (the only way is a setting
change, which Stagewatch never makes). Eight units on a Raspberry Pi are about half a megabyte a second. Frames larger
than 512 KiB, damaged frames and floods are dropped, and a unit that keeps sending bad data is disconnected for a
while.

## Emulate mode

`stagewatch --emulate` adds two simulated units, **Stage L pre (simulated)** and **Monitor world (simulated)**. A
six-minute loop shows a temperature that drifts between 52 and 63 degC, a power supply dropping for 20 seconds, a clock
change with a Notice, a unit going offline for 30 seconds and coming back, and a soundness word nobody has seen
before. Everything is labelled simulated.

## What was not checked

Warning, No Routing and Issues with Input have not been seen from a real unit. The full list of causes is not known.
What `preset.active = 0` means, and what the clock sync flag and AVB stream state values mean, are read from one
unit only. Other RME units (Digiface AVB, M-1620 Pro and others) may send the same shape or not. Level meters and
input settings are not read.
