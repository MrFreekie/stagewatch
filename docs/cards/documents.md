# Plans on file (event documents)

Keep the event's safety and risk plans on the show network, so crew can answer "what does our
plan say?" from the tablet they already use, even with no internet. Lightning, wind, heat,
evacuation, electrical, site rules, medical and noise plans all fit.

Stagewatch is a file shelf. It does not check that a plan is current, correct or compliant. The
duty holder's own plan and decisions always come first.

## Privacy first

Plans often name people and give phone numbers. **Dashboards have no login**: anyone on the show
network who opens a dashboard can read every document shared with it. So:

- Every document is **admin-only** until you tick a dashboard for it.
- Before you tick one, remove names and phone numbers, or keep that plan admin-only.
- Upload only files you trust. PDFs open in a new browser tab, kept apart from Stagewatch.

Documents are kept in the data folder (`documents/`), are included in update backups, and are
tracked by `scripts/site_config.py` if you use it, so keep that repository private. A replaced or
deleted file stays in that repository's history.

If you delete a dashboard, untick it on its documents too: a new dashboard with the same URL name
would see them.

## Add a document (for the person who looks after Stagewatch)

1. In Admin, open the **Documents** card. It shows the current event; choose another event from the
   list if you have more than one.
2. Open **Add a document**. Type a title, pick a category and choose the file:
   PDF (up to 10 MB), Markdown `.md` or plain text `.txt` (up to 256 KB, 100,000 characters and 2,000 lines). Word files: save as PDF first.
3. Tick the dashboards that may show it, or tick none to keep it admin-only.
4. Click **Add document**.

Each event holds up to 20 documents. **Replace…** swaps the file (the old one is not kept).
**Share…** changes which dashboards show it. **Delete** removes it for good.

## Show it on a dashboard

Add the **Plans on file** card to a dashboard (Admin, User dashboards, **Edit cards**). It is not on
any dashboard by default.

- **Tablet and phone:** a list of titles with the category and how long ago each was updated. Tap a
  text plan to read it full screen (**A−** and **A+** change the text size, remembered on that
  device). Tap a PDF to open it in a new tab.
- **Wall:** titles only, with "Read on a tablet or phone". Hidden while nothing is shared.

Only the current event's documents show on dashboards. Starting a new event hides the old ones
there; they stay in Admin under that event.

## If it didn't work

- **"No plans shared with this screen"**: no document in this event is ticked for this dashboard.
- **A PDF will not open on a kiosk screen**: some kiosk browsers have no PDF viewer. Walls show
  titles only anyway; read it on a tablet or phone.
- **"Use a PDF, a Markdown (.md) file or a plain text (.txt) file"**: the file is another type, or
  a text file with characters that cannot be shown. Save it as PDF or plain UTF-8 text.

## Tested status

**Tested on hardware: pending.** Checked with simulated data (emulate mode), not yet on real
tablets, phones or a Pi kiosk.

Related: [Dashboard cards](README.md), [Admin: Dashboards](../admin/dashboards.md).
