# Contributing to Stagewatch

This page is for people who work on the code. To *use* Stagewatch, start with the
[guides](docs/README.md).

Stagewatch is written entirely by an AI coding assistant, with no human code review. Issues and pull
requests from people who write code are very welcome. Please read the [security policy](SECURITY.md)
before reporting a vulnerability.

## Quick start (run from source)

Requires [uv](https://docs.astral.sh/uv/). It fetches Python 3.12 itself. This is for trying
it out or developing. For a show computer use the installers
([Windows](docs/install/windows.md), [Raspberry Pi](docs/install/raspberry-pi.md), [Debian Linux](docs/install/debian-linux.md)) instead.

```bash
git clone https://github.com/MrFreekie/stagewatch.git
cd stagewatch
uv sync
uv run stagewatch --emulate
```

Open <http://localhost:8080>. The first visit asks you to set an admin PIN.
`--emulate` runs simulated sensor nodes. Drop it once real nodes are on the network.

```
stagewatch [--host HOST] [--port 8080] [--data-dir DIR] [--print-data-dir] [--emulate] [--no-mdns] [-v] [--version]
stagewatch reset-admin-pin [--data-dir DIR] [--emulate] [--force]
```

## Development

**Before your first commit:** run `git config core.hooksPath .githooks`. The hook
blocks secrets (keys, PIN hashes, `secrets.yaml`, config and data files) from being
committed.

```bash
uv sync                       # install, including dev tools
uv run pytest                 # tests
uv run stagewatch --emulate   # run with simulated nodes
uv run python scripts/check_secrets.py --all   # scan tracked files for secrets
```

Layout:

```
src/stagewatch/
  acoustics.py        speed of sound (Cramer 1993), dew point, unit conversion
  core/               hub, model, config, recorder, alarms, derived values, cards, schedule,
                      updater, plugin API
  integrations/       esphome (+ emulator), globcon, ontime, shelly, smaart, osc_out
  web/                FastAPI server, auth, static dashboard/admin UI (no build step)
esphome/              example node configs
deploy/               Windows and Raspberry Pi autostart
scripts/              release and secret-check tools
tests/
```

New integrations subclass `stagewatch.core.plugin.Integration`. Each one ships a
`Manifest` (domain, name, version, direction, protocols, vendors, tier) and supports emulate mode.

## Rules the code keeps

- Stagewatch is **advisory** and **read only** towards rigging, power and safety systems.
- Secrets never go in git. The commit hook and `scripts/check_secrets.py` enforce it.
- No assumed data: a gap stays a gap. Offline means a gap and a restart marker, never a back-fill.

## Versioning and releases

Stagewatch follows [Semantic Versioning](https://semver.org), and changes are recorded
in [CHANGELOG.md](CHANGELOG.md):

- The version lives in `pyproject.toml` and `src/stagewatch/__init__.py`. A test keeps
  them in step with the changelog.
- The running version, git commit and config/database schema versions are shown in
  the admin header, in `/api/info`, and at the top of the log.
- `config.yaml` carries `schema_version`, and the database uses `PRAGMA user_version`.
  Both have migration hooks, so older data upgrades on start.

To cut a release, add notes under **[Unreleased]** in the changelog, then run:

```bash
uv run python scripts/bump_version.py minor   # or patch / major / X.Y.Z
git push --follow-tags
```

The script updates the version files, dates the changelog, commits, and creates the
tag `vX.Y.Z`. It never pushes.