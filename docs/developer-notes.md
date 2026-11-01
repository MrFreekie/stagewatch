# Developer notes

## Browser layout tests (optional)

These open the dashboards and Admin in a real browser at phone (375x812), tablet (768x1024) and wall (1920x1080) sizes, in Day and Night. They check for sideways page scrolling, over-tall cards, a header that is too big on a phone, touch targets under 44 px, text contrast under 4.5:1, console errors, and that every Admin tab draws after login.

They are not part of the normal test run. They need Playwright and its Chromium, and only run when you ask with `-m ui`:

```bash
uv pip install playwright==1.64.1
uv run python -m playwright install chromium
uv run pytest -m ui tests/ui
```

Without `-m ui`, or without Playwright, the tests skip and the normal suite is unchanged. A full run takes about 1.5 minutes. Each run starts its own emulate-mode server on a free port with a temporary data folder and sets the first-run Admin PIN itself.

The Nightly workflow runs them on a manual trigger (Actions, Nightly, "Run workflow") and on the nightly schedule, in a separate `ui` job that does not hold up promotion.

Known finding: on the wall layout the Power card (about 3,700 px) and the Power tree card (about 1,800 px) are far taller than the 1,500 px cap, because the wall cannot fold them. `KNOWN_TALL_ON_WALL` in `tests/ui/test_layout.py` lists them so the test stays green; remove them from it once they are fixed.

## How readings are stored

`Recorder.record_state` thins what it writes (constants beside `HEARTBEAT_S` in `core/recorder.py`): a changed value is written at once, an unchanged one only as a heartbeat row every `UNCHANGED_HEARTBEAT_S` (30 s), and a changed calculated `site.*` value at most every `SITE_MIN_INTERVAL_S` (10 s). The schema is unchanged and dense rows from older versions read as before.

Rules for readers: never assume one row per second; a reading holds until the next row (as the show report does, up to its 120 s `HOLD_S`), and a longer silence is a gap, never filled in. Keep the heartbeat inside that hold and the chart gap (90 s). Averages per time bucket are of the stored rows, so with steady values they are not weighted by time; extremes are unaffected, as every change is stored.
