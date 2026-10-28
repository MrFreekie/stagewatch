# Security policy

Stagewatch runs on show control networks and can update itself, so security
reports are taken seriously, even though this is a one-person, 100 % vibe-coded
project (see the [notices](docs/integrations/README.md#notices)).

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository
(<https://github.com/MrFreekie/stagewatch/security/advisories/new>).

Include what you found, how to reproduce it, and which version or commit you tested.
You'll get a reply as soon as practical; fixes go out through the Stable channel.

## Supported versions

Only the latest release on the Stable channel (and the current Nightly) receive fixes.

## Scope notes

- Stagewatch is designed for a **private show network**, not the public internet.
- It is an **advisory tool**, not a certified safety system (see the [notices](docs/integrations/README.md#notices)).
- Please don't test against installations you don't own.
- Stagewatch's dashboards have no login. Anyone on the show network can see them. The admin page needs a PIN.
- RME units (12Mic and similar) have no authentication: they accept changes from any device on their network,
  including gain and 48 V. Keep the AVB and control network separate. Stagewatch sends no commands or settings; it only
  opens the link and answers the unit's keep-alive. It holds no unit credentials (see [RME interfaces](docs/integrations/rme.md)).

## How the admin page is protected

- The PIN is stored as a salted PBKDF2 hash. Sessions are signed cookies (HttpOnly, SameSite strict).
  Changing the PIN logs out every other session.
- Requests that come from another web page (cross-origin) are refused, and repeated wrong PINs are
  rate-limited.
- ESPHome encryption keys and saved passwords are never sent back to the browser.
- **The clock button (Raspberry Pi / Debian only).** The Admin **Clock** card can set the computer's
  clock from the browser. Stagewatch never runs a shell and never runs as root. It runs one
  root-owned file, `/usr/local/sbin/stagewatch-set-time`, through `sudo -n`, with an argument list.
  A sudoers rule, `/etc/sudoers.d/stagewatch-set-time`, lets the Stagewatch service user run that
  one file and nothing else (the installer checks it with `visudo -cf` first). The file takes a
  single whole number of seconds, refuses anything else or any year outside 2025 to 2099, and sets
  the clock (and the hardware clock if there is one). The request needs the admin login, is
  refused from another web page, and is limited to one change every 10 seconds; a jump of more
  than 5 seconds must be confirmed. The trade-off: `sudo` needs the service's "no new privileges"
  and "restrict SUID" systemd protections turned off, so the installer adds a drop-in,
  `/etc/systemd/system/stagewatch.service.d/clock-helper.conf`, for that. Skip it with
  `install.sh --no-clock-button`, or remove it with `install-clock-helper.sh --uninstall`. The
  copy in `/usr/local/sbin` is not updated by in-app updates; run the installer line again to refresh it.
- The in-app updater refuses downgrades, targets that are not on `main`, moved release tags and
  changes to where dependencies come from, and only runs code from the pinned GitHub origin. It
  **cannot** protect you if the GitHub account itself is compromised. Maintainers should turn on
  two-factor sign-in and set repository rules: no force-push or deletion on `main`, only GitHub
  Actions may update `nightly`, and no update or deletion of `v*` tags.
