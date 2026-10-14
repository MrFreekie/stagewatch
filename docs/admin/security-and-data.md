# Admin: Security and data

The PIN, who can see what, what the diagnostics file holds, and how to keep your site settings
safe. Where your data lives and how to back it up are in
[Updating and backups](../updating-and-backups.md#where-your-data-lives).

> **Tested on hardware: pending.** Nothing on this page has been checked on a real show PC or a
> Raspberry Pi. Stagewatch is an advisory tool with no warranty. See the
> [Notices](../integrations/README.md#notices).

---

## Who can see and change what

- **Dashboards have no login.** Anyone on the same network can open them and see your readings,
  markers and alarms. They cannot change settings. An admin can allow a dashboard to add markers
  or acknowledge alarms.
- **The admin page is locked with a PIN.** Only someone with the PIN can change devices, limits,
  dashboards, integrations and settings.
- **Run Stagewatch on a private show network, not the public internet.** It uses plain `http`, not
  `https`, so the PIN is not encrypted on the way. Do not put Stagewatch where strangers can reach it.
- **Stagewatch never controls equipment.** It reads and shows. It does not switch, mute or change
  rigging, power or the PA. The few messages it can send out (OSC output) are opt-in and set up by an
  admin. See [OSC output](../integrations/osc-output.md).

## Set or change the PIN

**First time.** Open the admin page (see [Admin](README.md#open-it)). It says **Welcome: set an
admin PIN**. Type a PIN of at least 4 characters twice and click **Set admin PIN**.

> **Do this straight away.** Until a PIN is set, **anyone on the network** who opens `/admin` can
> set it. The same is true for a short time after a PIN reset.

**Change it later.** In the **Security** card, type your current PIN and the new one, then click
**Change PIN**. A new PIN can be 4 to 64 characters.

**What you will see:** "PIN changed; other admin sessions logged out." Every other tablet logged in
as admin has to log in again.

**Good to know:**

- You stay logged in for 12 hours, then log in again.
- After 5 wrong PINs within a minute, you see "Too many attempts; wait a minute". Wait, then try again.
- Updating and rolling back ask for the PIN again, even when you are logged in. This is on purpose, so a
  tablet left logged in cannot start an update.
- Stagewatch stores only a scrambled form of the PIN (a salted hash), never the PIN itself.
- ESPHome node keys are kept in your settings. The admin page shows only that a key is **set**, never
  the key.

**Forgotten the PIN?** Someone with access to the Stagewatch computer resets it. Steps:
[Forgotten PIN, or "Recovery required"](../updating-and-backups.md#forgotten-pin-or-recovery-required).

## What the diagnostics file holds

If something is not working and you ask for help, send the diagnostics file rather than raw log files.

1. Open the admin page and log in.
2. Find the **Help** card and click **Download diagnostics**.
3. Wait a few seconds. A zip file downloads. The page says "Downloaded. Send that file when you ask for help."

**What is inside:** the version and build, the device list with statuses, your settings, the software
update status and history, recent alarms, and the last part of Stagewatch's own log files (up to
about 2,000 lines each).

**What is left out:** every PIN hash, password and encryption key shows as `[redacted]`. A Shelly power
meter's address and hardware address are removed too.

**What is still in it:** your show and device names and network addresses. Look through the file first
if that matters to you. Do not post it somewhere public.

Where the raw log files are, and how to read them, is in
[Troubleshooting](../troubleshooting.md#where-the-log-files-are).

## Where your data lives

See [Where your data lives](../updating-and-backups.md#where-your-data-lives) for the folders, what is
in them, and how to make your own backup. In short: your data is kept outside the program, it holds
secrets, and every copy must stay private.

## Keep your site settings in a private repository (optional)

*For a technical person.* This is a way to keep your settings (devices, thresholds, dashboards) in a
**private** online repository, so you have history and a copy away from the show computer. It is
optional and is not part of the installer.

It tracks only a fixed list of files: `config.yaml`, event logos, on-site contacts, and event
documents and templates. Contacts and documents can hold personal data, which is why the repository
must be private. It **never** tracks `secret.key`, the history database, logs, `config.yaml.bak`, the
`backups` folder, the `emulate` folder or temporary files.

It refuses a public repository, and refuses any address it cannot confirm is a private GitHub
repository (unless you add `--allow-unverified-remote`, in which case keeping it private is up to you).
Each `push` rewrites the repository's `.gitignore` to match the list. Untracking a file does not remove it
from history that was already pushed.

It runs from a downloaded copy of the project (a "source checkout", see
[CONTRIBUTING.md](../../CONTRIBUTING.md)), using `uv`. Use `--data-dir` to point it at your data folder.
Replace `YOUR_NAME` with your own GitHub name:

```bash
gh repo create YOUR_NAME/stagewatch-site --private
uv run python scripts/site_config.py init --remote https://github.com/YOUR_NAME/stagewatch-site.git
uv run python scripts/site_config.py push -m "Added delay tower nodes"
```

Other commands: `status` (what has changed) and `pull` (bring the settings back; **stop Stagewatch
first**, then start it again to load them). Without `--data-dir` it uses the folder a hand-run Stagewatch
would use. For a boot install, add `--data-dir C:\ProgramData\Stagewatch` on Windows or
`--data-dir /var/lib/stagewatch` on a Pi or Debian PC, and run it as Administrator or with `sudo`.

**Not tested:** this tool has not been tried against a boot install's protected data folder.

## If it didn't work

- "Too many attempts; wait a minute": wait a minute.
- Locked out, or the admin page says **Recovery required**: see
  [Forgotten PIN, or "Recovery required"](../updating-and-backups.md#forgotten-pin-or-recovery-required).
- Anything else: [Troubleshooting](../troubleshooting.md).

To report a security problem in Stagewatch itself, see [SECURITY.md](../../SECURITY.md).
