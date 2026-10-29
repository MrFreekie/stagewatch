# Wi-Fi

The **Wi-Fi** card shows the crew internet so nobody has to ask for the password. Each network has a
big QR code, the network name, the password and an optional note such as "2.4 GHz only" or
"Expires 23:00". Point a phone camera at the code and it offers to join.

It is not on any dashboard by default. Your tech adds the networks under **Admin, Wi-Fi** and adds
the card under **Admin, User dashboards, Edit cards**. It stays hidden until there is a network.

Back to [Dashboard cards](README.md).

> **Only share guest or crew internet here. Never the network your show equipment runs on.**
> Anyone who can open a dashboard with this card can read the password.

## Good to know

- Up to six networks, each with a label, name, password and note. A network can be open (no password).
- **Hide the password, show the join code only** keeps the password off the screen. The code still
  holds it, so it is not a way to keep the password secret from someone who scans it.
- Tick **Hidden network** if the network does not broadcast its name.
- The code is drawn on the dashboard itself. Nothing is fetched from the internet.
- The wall shows a large code and large name. Phones show the code above the details.
- Passwords are kept in Stagewatch's config file on the Stagewatch computer. They are only sent to
  dashboards that have this card, and are left out of the snapshot, the diagnostics bundle, the log
  files and the show report. If you back the config file up to a private Git repository, the
  password goes with it, so keep that repository private.
- It can be set to **Half** width in the card list.
- The join code is drawn at the lowest error correction level (L) so it stays small and quick to scan.
  The trade-off is that a scratched, dirty or glare-covered screen may not scan. If a phone struggles,
  type the password instead.
- **Not yet tested on real kit.** It has been checked with tests and simulated data only.
  Try the code on your crew's phones before you rely on it.
