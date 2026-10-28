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
- The in-app updater refuses downgrades, targets that are not on `main`, moved release tags and
  changes to where dependencies come from, and only runs code from the pinned GitHub origin. It
  **cannot** protect you if the GitHub account itself is compromised. Maintainers should turn on
  two-factor sign-in and set repository rules: no force-push or deletion on `main`, only GitHub
  Actions may update `nightly`, and no update or deletion of `v*` tags.
