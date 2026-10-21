# Updating and backups

How to update Stagewatch from its own admin page, where your data lives, how it is
protected, and what to do if you forget your PIN.

> **Update when it suits you.** Every update takes a backup first and can be rolled back.
> Stagewatch does **not** check whether a show is running. Updating restarts it for about
> a minute and nothing is recorded while it restarts, so pick a quiet moment if you can.
>
> Stagewatch is an advisory tool with no warranty. See the [Notices](integrations/README.md#notices).

> **Tested on hardware: pending.** Updating and rolling back from the admin page have been tested in
> a Windows 11 virtual machine and a Debian 13.7 virtual machine. They have not been reported as
> tested on a real show PC or on a Raspberry Pi. Rolling back an update that changed the data format
> was tested in a virtual machine, but whether history recorded after the update survives a roll back
> was not checked. Treat that history as set aside (see Roll back).

---

## The Software card

1. Open the admin page: the Stagewatch computer's address plus `/admin`, for example
   `http://192.168.1.50:8080/admin`.
2. Log in with your PIN.
3. Find the card called **Software** and open it.

It has these sections:

- **Installed**: the **Version** and **Commit** (the exact build you are running), the **Channel**
  (Stable or Nightly) and **Install**. **Install** says **Managed** if you used the installers in
  this guide.
- **Updates**: the **Channel** menu, the **Check for updates** button and the result.
- **History**: every update and roll back so far. Says **No updates yet.** until there is one.
- **Data backups** and **Displaced data**: appear once there is something to list (see below).

**If it says "In-app updates are only available on a managed install":** this copy was not
installed with the Windows or Raspberry Pi installer (for example it was started by hand
from a downloaded folder). That copy cannot update itself here. Follow an
[install guide](install/windows.md) ([Raspberry Pi](install/raspberry-pi.md),
[Debian Linux](install/debian-linux.md)) to move to a managed install. A copy started by hand
from a downloaded folder can be updated by updating that folder yourself.

---

## Stable or Nightly? Use Stable.

| Channel | What it is | Use it |
|---|---|---|
| **Stable** (the default) | Proper numbered releases, like v1.2.3. | **For shows.** |
| **Nightly** | The newest code that passed the automatic tests (the Windows test run). Only checked by a machine. | For trying new things. Try it away from a live show first. |

To change channel, use the **Channel** menu in the **Updates** section of the Software
card. The card warns you if Nightly is selected. The installer's choice is the starting
channel; your pick is saved in your settings.

---

## Check for an update

1. Tap **Check for updates**. (The PC needs internet.)
2. Wait a few seconds. You will see one of:
   - **Up to date** with a tick, on your channel. Nothing to do.
   - **Update available**, with the new version, the exact commit, and **What's changed**
     (a list of changes). Read it.
   - **Check failed**, with a reason. See below.

You can only check about once every 30 seconds.

## Update

1. On the **Update available** panel, tap **Update now...**.
2. A box shows the versions and the changes. If it says **"This update changes the data
   format"**, a backup will be made first (see below).
3. Type your **admin PIN** again. This is deliberate. It stops a tablet that was left
   logged in from starting an update.
4. Tap **Update now**.

**What you should see:** a full-screen message **"Stagewatch is restarting for an
update..."** with a timer. It says it takes about a minute. The page reloads by itself when
Stagewatch is back. Dashboards reconnect by themselves too.

After the restart, the **History** section lists the update with a tick.

Check the dashboards, and that your nodes show **ok**.

If the message says **"Stagewatch has not come back"** after 10 minutes, see
[What to do if an update goes wrong](#what-to-do-if-an-update-goes-wrong).

Stagewatch will not install an update that changes where its parts come from or which
version of Python it needs. It tells you so, and those need a technical person to update by hand.

---

## Automatic backups

You do not have to do anything.

- Before an update that **changes the format of your data**, Stagewatch makes a backup of
  your data first.
- It keeps the **last 10** backups.
- They are in a `backups` folder inside your data folder (see
  [Where your data lives](#where-your-data-lives)).
- The Software card lists them under **Data backups**, with their sizes.
- A backup holds your settings (`config.yaml`), the login key, contacts, event logos, event
  documents and the history database. It does **not** hold logs. Make your own backup
  (below) to keep everything.

Plain updates that do not change the data format do not need a backup, so you may see
none for a while.

Backups only ever cover your **real** data. If you run Stagewatch in demo mode (`-Emulate`
or `--emulate`, fake sensors), its data lives in a separate `emulate` folder inside the data
folder. That folder is never backed up or restored, and it never mixes with your real history.

## Roll back

If the new version misbehaves, you can go back.

1. In **History**, find the update.
2. Tap **Roll back...** next to it.
3. Enter your **admin PIN** and confirm.

Stagewatch restarts on the old version. If a backup was made for that update, your data
is put back as it was.

**Anything recorded since the update is set aside, not deleted.** It goes in
`backups\displaced-...` inside the data folder. The Software card lists it under
**Displaced data**. Markers you added after the update are not in the restored data.
Stagewatch never deletes the displaced folders by itself.

**Automatic rollback:** if a new version cannot start, Stagewatch goes back to the old
version and restores the backup **by itself**.

---

## Where your data lives

The program and your data are kept apart. You can update, re-install or delete and re-clone the
program and your data stays exactly as it was. Old settings and history are upgraded
automatically when a newer version starts.

| How Stagewatch was started | Data folder |
|---|---|
| Windows boot install | `C:\ProgramData\Stagewatch` |
| Raspberry Pi or Debian boot install | `/var/lib/stagewatch` |
| Run by hand on Windows | `%USERPROFILE%\StagewatchData` |
| Run by hand on Linux | `~/.local/share/stagewatch` |
| Demo (emulate) mode | an `emulate` folder inside whichever folder above applies |

The boot install's data folder is **not readable by ordinary users, on purpose**. Use an
Administrator PowerShell (Windows) or `sudo` (Pi, Debian) to look inside or copy it.

The data folder holds:

- `config.yaml`: your devices, thresholds, dashboards, PIN (as a one-way hash) and ESPHome keys.
- `config.yaml.bak`: the last good copy of the settings.
- `stagewatch.sqlite3`: the history.
- `secret.key`: the key that signs admin logins.
- `contacts`, `branding` and `documents`: contacts, event logos and event documents (these can
  hold personal details).
- `logs`: see [Troubleshooting](troubleshooting.md#where-the-log-files-are).
- `backups`: the automatic backups and any displaced data (above).

**It holds secrets, so keep every copy private.**

To change the folder for a copy you run by hand, use `--data-dir DIR` or set `STAGEWATCH_DATA`.
To print the folder that copy would use, run `uv run stagewatch --print-data-dir`. For the boot
installs the folder is fixed by the installer (see the installer options on the install pages).

To keep your settings in a private online repository as well, see
[Security and data](admin/security-and-data.md#keep-your-site-settings-in-a-private-repository-optional).

---

## Make your own backup (recommended before a tour)

The automatic backups are only for updates. To keep a copy of your settings and history,
copy the **data folder**. It holds your settings, history, PIN and node keys, so **keep the
copy private**. Do not email it or put it online.

**Windows.** Open PowerShell **as Administrator**. Paste this and press Enter. It stops
Stagewatch, copies the data to a new dated folder called `stagewatch-backup-` plus today's date
in your user folder (`C:\Users\YOUR_NAME`), and starts it again:

```powershell
Stop-ScheduledTask -TaskName "Stagewatch"; Start-Sleep -Seconds 10; Copy-Item "C:\ProgramData\Stagewatch" "$HOME\stagewatch-backup-$(Get-Date -Format yyyy-MM-dd)" -Recurse; Start-ScheduledTask -TaskName "Stagewatch"
```

Stagewatch notes this stop in its history as an unexpected stop. That is expected here.

Then copy that folder to a USB stick you keep safe. Do not put it in a cloud folder.

**Raspberry Pi or Debian.** Paste this in a Terminal:

```bash
sudo systemctl stop stagewatch; sudo cp -r /var/lib/stagewatch ~/stagewatch-backup-$(date +%F); sudo chown -R $USER ~/stagewatch-backup-$(date +%F); sudo systemctl start stagewatch
```

You will be without live readings for about half a minute while it copies.

To undo an update, use **Roll back...** above. To fix a damaged settings file, see the last
section. To put a whole copy back, see the next section.

---

## Put your own backup back (restore)

Use this when the data folder is lost or damaged, or after you reinstall the computer or the
Pi's SD card. Your settings, history, PIN and node keys come back exactly as they were when you
made the copy. Anything recorded after that is not in it.

> **Stop Stagewatch first, every time.** If you copy over the data while Stagewatch is
> running, the history database gets damaged.

Your current data is not deleted. It is moved to a folder called `before-restore` plus
today's date, so you can go back if you picked the wrong copy.

**After a reinstall:** install Stagewatch first, as on its
install page ([Raspberry Pi](install/raspberry-pi.md), [Debian](install/debian-linux.md), [Windows](install/windows.md)), and check it opens. Then restore. Do not set a new PIN: the
restored copy brings back your old one.

**Raspberry Pi or Debian.** Copy your backup folder onto the Pi (for example from a USB
stick into your home folder). Then paste this in a Terminal, with your folder's name in place
of `stagewatch-backup-2026-10-17`:

```bash
BK=~/stagewatch-backup-2026-10-17
sudo systemctl stop stagewatch
sudo mkdir /var/lib/stagewatch-before-restore-$(date +%F)
sudo sh -c 'mv /var/lib/stagewatch/* /var/lib/stagewatch-before-restore-'$(date +%F)'/'
sudo cp -r "$BK"/. /var/lib/stagewatch/
sudo chown -R stagewatch:stagewatch /var/lib/stagewatch
sudo systemctl start stagewatch
```

The `chown` line matters: without it, Stagewatch cannot write to the restored files and keeps
restarting.

**Windows.** Copy your backup folder to your user folder. Open PowerShell **as
Administrator** and paste this, with your folder's name in place of
`stagewatch-backup-2026-10-17`:

```powershell
$BK = "$HOME\stagewatch-backup-2026-10-17"; $OLD = "C:\ProgramData\Stagewatch-before-restore-$(Get-Date -Format yyyy-MM-dd)"
Stop-ScheduledTask -TaskName "Stagewatch"; Start-Sleep -Seconds 10
New-Item -ItemType Directory $OLD | Out-Null; Move-Item "C:\ProgramData\Stagewatch\*" $OLD
Copy-Item "$BK\*" "C:\ProgramData\Stagewatch" -Recurse
Start-ScheduledTask -TaskName "Stagewatch"
```

The files are copied **into** the existing folder, so they keep its protected settings.

**Check it worked.** Open Stagewatch, sign in to Admin with your **old** PIN, and look at
yesterday's history and markers. When you are happy, you can delete the `before-restore`
folder.

> Tested on 17 Oct 2026 by restoring a copy into a new data folder and starting Stagewatch
> from it (the old PIN worked and the markers were there). Not yet tried on a real Pi boot
> install or the Windows boot install.

---

## What to do if an update goes wrong

| What you see | What to do |
|---|---|
| **Check failed: Update source not reachable (repository is private or offline).** | The computer has no internet, or GitHub cannot be reached. Try again with internet. Updates need the project's page on GitHub to be public. They do not support signing in to a private one. |
| **Not enough free disk space for a data backup.** | Free up space on the computer, then try again. |
| **The update source rejected the fetch (a release tag may have changed)** | Stagewatch refuses on purpose. Do **not** force it. Ask whoever looks after Stagewatch to check with the project. They can follow [A release tag was changed](#a-release-tag-was-changed). |
| **This install has local changes; refusing to update.** | Someone edited files in the program folder. Ask a technical person. |
| Page stays on "restarting" | Wait 2 minutes. If nothing, reload the page. If nothing after 10 minutes, restart the computer. Then look at the log (see [Troubleshooting](troubleshooting.md#where-the-log-files-are)) and the `launcher.log`. |
| Stagewatch works, but the new version behaves badly | Use **Roll back...** (above). |

If Stagewatch will not start at all after an update, it should roll itself back within a
few minutes. If it has not, restart the computer, and check the log.

Other messages you may see are short and say what to do, such as "Check for updates
first." or "The target changes the Python requirement; update manually." Those last
ones need a technical person to update by hand.

### A release tag was changed

*For whoever looks after the install.* If a release is deleted and re-created on GitHub, even
with the same contents, Stagewatch refuses to update and the log says **Changed tag: vX.Y.Z**.
This is deliberate: a moved tag is what tampering looks like, so Stagewatch never accepts it
by itself. After you have confirmed with the project that the change was intended, delete the
local copy of the tag and check again.

Windows, in an Administrator PowerShell:

```powershell
git -C C:\Stagewatch tag -d vX.Y.Z
```

Raspberry Pi or Debian: the program folder belongs to the `stagewatch` user, so run git as that user:

```bash
sudo -u stagewatch git -c safe.directory=/opt/stagewatch -C /opt/stagewatch tag -d vX.Y.Z
```

Put the real tag in place of `vX.Y.Z`. If git on Windows complains about "dubious ownership",
add `-c safe.directory=C:/Stagewatch` straight after `git`. The Linux command has not been
tested on a real machine. Then tap **Check for updates** again.

### What the updater will and will not do

It refuses downgrades, targets that are not part of the project's main line, moved release tags
and changes to where its parts come from. It only fetches from the address the installer
recorded. It **cannot** protect you if the project's own GitHub account were taken over, so do
not treat it as a substitute for your own judgement about what you install.

---

## Forgotten PIN, or "Recovery required"

### Forgot the PIN

There is no "forgot password" button, on purpose. Anyone on the network could use it.
Instead, someone with access to the Stagewatch **computer itself** resets it.

### "Recovery required" screen

You may see **Recovery required** on the admin page. It means Stagewatch's settings file
(`config.yaml`) could not be read and the PIN could not be saved from it. Stagewatch
keeps the bad file as `config.invalid...yaml`, tries hard to keep every part that is still
good (including the PIN), and normally starts fine. It only shows this screen if the PIN
itself was lost. While it is showing, setting a new PIN over the network is switched off so
a stranger cannot take over.

Stagewatch also keeps `config.yaml.bak`, a copy of the last good settings. It uses it by
itself when the main file is empty or damaged.

**The fix is the same as for a forgotten PIN.** Do the steps below. Your dashboards keep
working during all of this.

### Reset the PIN on Windows

1. On the Stagewatch PC, open PowerShell **as Administrator**
   ([how](install/windows.md#step-2-open-powershell-as-administrator)).
2. Paste this and press Enter. It stops Stagewatch, resets the PIN and starts Stagewatch
   again, all in one go:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Stagewatch\deploy\windows\reset-admin-pin.ps1
```

   **What you should see:** "Done. The admin PIN has been cleared." followed by
   "Next: open Stagewatch in your browser ... and set a new PIN now."

3. Wait about 30 seconds. **Straight away**, open `http://localhost:8080/admin` and set
   a new PIN. Until you do, **anyone on the network can set it**.

If you installed to a different folder, add `-InstallDir "D:\Your\Folder"` to the command.
The script reads the data folder from the install, so you do not need to know where it is.

If the script says the file does not exist, your copy is older than this feature. Update
Stagewatch first if you can, or use the command in
[Reset by hand](#reset-by-hand-advanced).

### Reset the PIN on a Raspberry Pi or Debian PC

1. On the computer, open a **Terminal**.
2. Paste this and press Enter. It stops Stagewatch, resets the PIN and starts Stagewatch
   again:

```bash
bash /opt/stagewatch/deploy/pi/reset-admin-pin.sh
```

**What you should see:** "Done. The admin PIN has been cleared." and the next step.

3. Wait 30 seconds. **Straight away**, open `http://localhost:8080/admin` and set a new PIN.

> The install this script belongs to is tested on a Raspberry Pi 4. This reset script itself has not
> yet been reported as tested on a Pi.

### Reset by hand (advanced)

The scripts above run `stagewatch reset-admin-pin --data-dir <data folder>` for you. It needs
file access to the data folder and is not available over the network. Stop the Stagewatch
service first: the command refuses to run while it detects a running server, and `--force`
overrides that check. Add `--emulate` to reset a demo copy's PIN. After a reset, **anyone on
the network can set the new PIN until you do**, so set it promptly.

### Or restore a backup instead

If you do not want to reset, the alternative is to copy a good `config.yaml` back into the
data folder from a backup, then restart Stagewatch. The bad file is kept next to it as
`config.invalid...yaml`.

---

**Next:** [Troubleshooting](troubleshooting.md).
