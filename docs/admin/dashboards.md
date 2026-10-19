# Admin: Dashboards

How to add a dashboard, choose which cards it shows, and put a tablet on it.

A **dashboard** is a page of **cards** at its own address, for example `/d/foh`. Crew open it on a
tablet, a phone or a wall screen. Nobody needs a login to look. You decide what each one shows
and what people on it may do.

Admin only changes Stagewatch's own settings. It never controls rigging, power or safety kit.
Back to [Admin](README.md).

## The standard dashboards

A new Stagewatch starts with three:

| Address ending | Title | Layout | Can add markers | Can acknowledge alarms |
|---|---|---|---|---|
| `/d/foh` | FOH | tablet | Yes | Yes |
| `/d/phone` | Phone | phone | Yes | No |
| `/d/wall` | Wall | wall | No | No |

You can rename, change or remove them, and add as many more as you like.

## Open the card

1. Open **Admin** and log in with your PIN.
2. Find **User dashboards**.

**What you'll see:** a table with one row per dashboard.

| Column | What it is |
|---|---|
| **URL name** | The end of the dashboard's address. `foh` gives `/d/foh`. Stagewatch tidies it to lower-case letters, numbers and underscores (`Main Stage` becomes `main_stage`). |
| **Title** | The name shown at the top of the screen. Up to 80 characters. |
| **Layout** | **tablet**, **phone** or **wall**. See below. |
| **Add markers** | Tick so people on this dashboard can add markers, and can add notes and hide markers. |
| **Ack alarms** | Tick so people on this dashboard get the **Acknowledge** button for alarms. |
| **Cards** | **Edit cards (5)**: the number is how many cards it shows. |
| (link) | **open** opens the dashboard in a new tab. |
| **✕** | Removes the row. |

### Layout

| Layout | For | What is different |
|---|---|---|
| **tablet** | A tablet at FOH or the monitor desk. | The standard look. The header stays at the top while you scroll. |
| **phone** | Your phone. | One column, smaller type. Every card is full width. The schedule is a short strip you tap. |
| **wall** | A wall screen or kiosk. | Very large type. **No buttons at all**, so it cannot be used to add markers or acknowledge alarms. No messages bell, and the header does not scroll. Hides the Schedule card once the day is over. |

An admin who is logged in on any browser can add markers and acknowledge alarms on a dashboard that
is not a wall layout, whatever the ticks say.

### Add a dashboard

1. Click **Add dashboard**. A new row and its card list open.
2. Type a **URL name** and a **Title**.
3. Choose the **Layout**. The cards change to that layout's usual set.
4. Tick **Add markers** and **Ack alarms** as you want.
5. Click **Save dashboards**.

**What you'll see:** `Dashboards saved`. The new dashboard is at `/d/` plus its URL name, and on the
home page.

### Remove a dashboard

1. Click **✕** on its row.
2. Click **Save dashboards**.

You cannot remove them all. There must be at least one, and every URL name must be different.

## Edit cards: choose what a dashboard shows

Each dashboard shows a set of [cards](../cards/README.md). You choose which ones, and in what
order.

1. In **User dashboards**, click **Edit cards** on the dashboard's row. The Edit cards page opens,
   with the list of every card on the left and a preview of the dashboard on the right (under the
   list on a phone). A new dashboard needs **Save dashboards** first.
2. Tick the cards you want. Untick the ones you do not.
3. Use **▲** and **▼** beside a card to move it up or down. The top of the list is the top of the
   screen.
4. For a card that offers it, choose how it looks (see below).
5. Optional: type a **Stage**, for example **Main stage**.
6. Click **Save** at the bottom of the list.

**What you'll see:** `Saved. The preview shows the saved dashboard.` The preview reloads, and
screens that are open on this dashboard reload by themselves within a few seconds and show the new
cards. To edit another dashboard, pick it at the top of the page. **Back to Admin** returns to
Admin; if you have unsaved changes, the browser asks first.

### The card list

The names in this list are slightly different from the headings on screen.

| In the list | On the dashboard | See |
|---|---|---|
| **Site readings** | The tiles | [Tiles](../cards/env-tiles.md) |
| **Schedule** | **Schedule** | [Schedule](../cards/schedule.md) |
| **History chart** | **History** | [History chart](../cards/chart.md) |
| **Markers** | **Markers** | [Markers](../cards/markers.md) |
| **Sensor nodes** | **Sensors** | [Sensors](../cards/sensors.md) |
| **Wall Clock** | **Wall Clock** | [Wall Clock](../cards/wall-clock.md) |
| **Ontime Timer** | **Ontime Timer** | [Ontime Timer](../cards/ontime-timer.md) |
| **Ontime Rundown** | **Ontime Rundown** | [Ontime Rundown](../cards/ontime-rundown.md) |
| **Equipment** | **Equipment** | [Equipment](../cards/equipment.md) |
| **Barometer** | **Barometer** | [Barometer](../cards/barometer.md) |
| **Sound level** | **Sound level** | [Sound level](../cards/sound-level.md) |
| **DirectOut GLOBCON** | **DirectOut GLOBCON** | [GLOBCON](../cards/globcon.md) |
| **Power** | **Power** | [Power](../cards/power.md) |
| **Sound level limits** | **Sound level limits** | [Sound level limits](../cards/sound-level-limits.md) |
| **Raspberry Pi** | **Raspberry Pi** | [Raspberry Pi](../cards/raspberry-pi.md) |
| **Plans on file** | **Plans on file** | [Plans on file](../cards/documents.md) |
| **Onsite contacts** | **Onsite contacts** | [Onsite contacts](../cards/contacts.md) |
| **Inclinometer (WitMotion)** | **Inclinometer** | [Inclinometer](../cards/witmotion-tilt.md) |
| **Alignment drift** | **Alignment drift** | [FOH advice cards](../cards/foh-advice.md) |
| **HF air loss** | **HF air loss** | [FOH advice cards](../cards/foh-advice.md) |
| **Open on a tablet** | The address and QR code at the bottom | [Dashboard cards](../cards/README.md) |

### What a new dashboard starts with

| Layout | Cards, top to bottom |
|---|---|
| **tablet** | Site readings, Schedule, History chart, Markers, Sensor nodes |
| **phone** | Site readings, Schedule, Markers, History chart |
| **wall** | Site readings, Schedule, History chart, Open on a tablet |

If you change the **Layout** of a new dashboard before saving, the ticks change to that layout's
set. On a dashboard that already exists, changing the layout does not change its cards. A dashboard
from an earlier version keeps exactly the cards it already had.

Every other card is never switched on by default. Tick them where you want them.

### Cards that hide themselves

A ticked card can still be hidden until it has something to show.

| Card | Hidden until |
|---|---|
| **Schedule** | The show day has a schedule (for this dashboard's stage). Add it in **Admin → Schedule → Open schedule editor**. |
| **Equipment** | A sensor has the Equipment role. |
| **Barometer** | Hidden on a **wall** layout while there is no pressure sensor. Other layouts say there is no sensor. |
| **Sound level** | Sound level values are set up. |
| **DirectOut GLOBCON** | GLOBCON has answered. |
| **Power** | A power meter has been added. |
| **Sound level limits** | Limits have been entered in Admin. |
| **Raspberry Pi** | Stagewatch is running on a Raspberry Pi. |
| **Plans on file** | A document has been shared with this dashboard. |
| **Inclinometer (WitMotion)** | A tilt sensor has been set up. |
| **Alignment drift** | A delay path has been set up in **FOH advice**. |
| **HF air loss** | A position has been set up in **FOH advice**. |
| **Wall Clock**, **Ontime Timer**, **Ontime Rundown** | The first message from the clock or from Ontime arrives. |

### Card width: Full or Half

Six cards have a **Card width** choice, **Full** or **Half**, beside them once they are ticked:
**Wall Clock**, **Ontime Timer**, **Ontime Rundown**, **DirectOut GLOBCON**, **Power** and **Raspberry Pi**.

- Every card is **Full** until you choose **Half**.
- Put two **Half** cards next to each other in the order and they sit side by side on a tablet or a
  wall screen. A **Half** card with no partner stays half width.
- On a phone, and on any screen narrower than 900 px, every card is full width.

The other cards flow by themselves: the Markers, Sensors, Barometer, Equipment and Sound level cards
sit two to a row when they are next to each other in the order and the screen is wide enough.

### Wall Clock look

Beside **Wall Clock**, once it is ticked, **Wall Clock look** sets how the clock is drawn on
this dashboard:

- **Plain digits**: the time in large digits.
- **LED ring**: a ring of 60 lights for the seconds, a ring of 12 for the hours, and the time in the
  middle. On a card narrower than about 280 pixels it shows plain digits instead.
- **7-segment digits**: the time as on a studio clock.

The ring and 7-segment looks are always red on black. Each dashboard has its own look. See
[Wall Clock](../cards/wall-clock.md).

### GLOBCON controller and channels

Beside **DirectOut GLOBCON**, once it is ticked, you choose the **GLOBCON controller** (1 to 16)
and the **Channels shown** (1 to 4, 5 to 8, 1 to 8, 9 to 12, 13 to 16, 9 to 16 or 1 to 16) for this
dashboard. See [GLOBCON](../cards/globcon.md).

### Stage

**Stage** says which stage a screen follows. Cards that show one stage, such as the Schedule, then
show only that stage's items, plus items with no stage. Leave it empty to show every stage. It can be
up to 40 characters. Pick from the suggestions to match the names you already use.

### Open on a tablet

**Open on a tablet** is always at the bottom of the screen, wherever it sits in the list. Tick it on
any dashboard, not only wall screens, to show that dashboard's address and a QR code.

### A dashboard with no cards

With no cards ticked, the dashboard shows only alarms. That is useful for a screen that should
only shout when something is wrong. It says: **No cards are switched on for this screen. Alarms
still show here.**

### If it didn't work

- **The screen still shows the old cards.** Reload the page.
- **A card you ticked is not there.** It may be waiting for something to show. See
  [Cards that hide themselves](#cards-that-hide-themselves).
- **A warning about cards from a newer version.** This dashboard lists cards that this version
  cannot show. Saving removes them.
- More help: [Troubleshooting](../troubleshooting.md).

## Connect a tablet

The **Connect a tablet** card shows how to reach each dashboard from a tablet or phone.

1. On the tablet, join the **same Wi-Fi** as the Stagewatch computer.
2. In **Admin**, open **Connect a tablet**.
3. Find the dashboard you want. Tap its name to open its section.
4. Scan the QR code with the tablet's camera, or type the address into the browser.

**What you'll see:** the dashboard opens. Each section shows the QR code and one or more addresses.
On most devices you can also use a short name ending `.local`. If that does not work, use the
numbers.

**Bookmark it, or add it to the home screen.** On an iPhone or iPad, tap **Share**, then **Add to
Home Screen**. On Android, tap the browser's menu (three dots), then **Add to Home screen**.

The card has a section for each dashboard and one for the home page. If there are three or fewer
sections they all start open, otherwise only the first does. **Expand all** and **Collapse all** do
the lot. The addresses are only shown in Admin, not on the
dashboards.

If the card says **This computer does not seem to be on a network**, connect the computer to the
show network (Wi-Fi or cable) and reload.

## Supported browsers

Dashboards work on:

- An iPad or iPhone with **iOS 12 or later**. That includes the old iPad Air 1 and iPad mini 2 and 3.
- A recent **Chrome**, **Edge** (the newer one, based on Chromium), **Firefox** or **Safari**.

The **Admin** page needs a little more: **iOS 13 or later** on an iPad or iPhone.

If a browser is too old, you get a plain message, **This browser is too old for Stagewatch**, instead
of a blank page. Use another device or update the browser.

## Tested status

**Tested on hardware: pending.** The dashboards editor has been checked in test browsers and with
simulated sensors (emulate mode). It has not yet been checked on every kind of real tablet, phone
and wall screen. Try each dashboard on the device you will use before the show.

Related: [Dashboard cards](../cards/README.md), [Glossary](../glossary.md).
