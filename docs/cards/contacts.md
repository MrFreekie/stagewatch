# Onsite contacts

Show who is who today: production manager, stage manager, head rigger and so on, with their radio
channel and area. Crew and visiting techs can find the right person from the screen they already
use, without hunting for the crew sheet.

## Privacy first

Contacts are personal data. **Dashboards have no login**: anyone on the show network who opens a
dashboard with this card can see what it shows. So:

- A dashboard shows **job title, name, radio and area** only. Phone numbers stay hidden unless you
  allow them in **three** places: Admin → **Onsite contacts** (Allow phone numbers on dashboards),
  the dashboard's **Edit cards** panel (Show phone numbers), and the contact's own **Show** tick.
- You can also set a **dashboard PIN for phone numbers**. Screens then show a **Show numbers**
  button and ask for that PIN (6 to 12 digits; Stagewatch refuses the admin PIN here). The numbers hide again after 2 minutes. Five wrong tries lock it for a minute.
- Tell crew their details are on the screens. Prefer work or show phones. There is no notes field
  on purpose.
- Clear the list when the event is over (see below).

## Setting it up

1. Admin → **Onsite contacts**. Click **Add contact** for each person: job title (pick one from
   the list or type your own), name (leave empty for "TBC"), phone, radio, area and **On duty**.
   Use ▲ and ▼ to set the order. Click **Save contacts**.
2. Admin → Dashboards → **Edit cards**: tick **Onsite contacts**. Optionally pick an area, so the
   screen lists only that area's people (and people with no area).
3. Only if you want phone numbers: tick **Show phone numbers** on that dashboard and **Allow phone
   numbers on dashboards** under "Phone numbers on dashboards".

Contacts not on duty stay in Admin but are hidden from screens. On a phone or tablet a phone
number is a tap-to-call link. The wall shows the list in large type.

## One list per event

Each event has its own list; a new event starts empty. To reuse a list, open the new event's
contacts and use **Copy contacts from**. Job titles you type are remembered for every event.

When an event has ended and still holds contacts, Admin shows a reminder at the top of the
Onsite contacts card: **Clear them** deletes that event's list for good; **Keep them** stops the
reminder for that event. A list not edited for 14 days also shows a reminder. Nothing is ever
deleted automatically.

## Where they are kept

In the data folder, one file per event (`contacts/<event number>.yaml`), plus `contacts/roles.yaml`
for job titles. They are never in `config.yaml`, logs, the diagnostics bundle or the live data
feed. They are included in update backups, and in `scripts/site_config.py` backups if you use it,
so keep that repository private. A cleared list stays in that repository's history.

Not yet tested on real phones: how `tel:` links behave on an iPad or a tablet with no SIM.
