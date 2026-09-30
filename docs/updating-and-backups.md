# Updating and backups

How to update Stagewatch from its own admin page, how your data is protected, and what to
do if you forget your PIN.

> **Golden rule: do not update on a show day.** Update at home, days before, then test.
> Stagewatch does **not** check whether a show is running. Updating restarts it for about
> a minute and nothing is recorded while it restarts.
>
> Stagewatch is an advisory tool with no warranty. See the
> [main README](../README.md#stagewatch).

---

## The Software card

1. Open the admin page: the Stagewatch computer's address plus `/admin`, for example
   `http://192.168.1.50:8080/admin`.
2. Log in with your PIN.
3. Find the card called **Software**.

It shows:

- **Version** and **Commit** (the exact build you are running).
- **Channel**: Stable or Nightly.
- **Install**: says **Managed** if you used the installers in this guide.

**If it says "In-app updates are only available on a managed install":** this copy was not
installed with the Windows or Raspberry Pi installer (for example it was started by hand
from a downloaded folder). That copy cannot update itself here. Follow the
[install guide](README.md) to move to a managed install.

---

## Stable or Nightly? Use Stable.

| Channel | What it is | Use it |
|---|---|---|
| **Stable** (the default) | Proper numbered releases, like v0.2.0. | **For shows.** |
| **Nightly** | The newest code that passed the automatic tests. Only checked by a machine. | **Never on a show day.** For trying new things at home. |

To change channel, use the **Channel** menu on the Software card. The card warns you if
Nightly is selected.

---

## Check for an update

1. Tap **Check for updates**. (The PC needs internet.)
2. Wait a few seconds. You will see one of:
   - **Up to date** with a tick, on your channel. Nothing to do.
   - **Update available**, with the new version, the exact commit, and **What's changed**
     (a list of changes). Read it.
   - **Check failed**, with a reason. See below.

You can only check about once every 30 seconds.

If you installed with `-Ref main` before the first release was published, Stable shows
**Up to date** until the next numbered release appears. That is expected.

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

After the restart, the **Update history** list shows the update with a tick.

Check the dashboards, and that your nodes show **ok**.

If the message says **"Stagewatch has not come back"** after 10 minutes, see
[What to do if an update goes wrong](#what-to-do-if-an-update-goes-wrong).

---

## Automatic backups

You do not have to do anything.

- Before an update that **changes the format of your data**, Stagewatch makes a backup of
  your data first.
- It keeps the **last 10** backups.
- They are in a `backups` folder inside your data folder (see below).
- The Software card lists them under **Data backups**, with their sizes.

Plain updates that do not change the data format do not need a backup, so you may see
none for a while.

## Roll back

If the new version misbehaves, you can go back.

1. In **Update history**, find the update.
2. Tap **Roll back...** next to it.
3. Enter your **admin PIN** and confirm.

Stagewatch restarts on the old version. If a backup was made for that update, your data
is put back as it was.

**Anything recorded since the update is set aside, not deleted.** It goes in
`backups\displaced-...` inside the data folder. The Software card lists it under
**Displaced data**. Markers you added after the update are not in the restored data.

**Automatic rollback:** if a new version cannot start, Stagewatch goes back to the old
version and restores the backup **by itself**.

---

## Make your own backup (recommended before a tour)

The automatic backups are only for updates. To keep a copy of your settings and history,
copy the **data folder**. It holds your settings, history, PIN and node keys, so **keep the
copy private**. Do not email it or put it online.

**Windows.** Open PowerShell **as Administrator**. Paste this and press Enter. It stops
Stagewatch, copies the data to a folder called `stagewatch-backup` in your user folder
(`C:\Users\YOUR_NAME`), and starts it again:

```powershell
Stop-ScheduledTask -TaskName "Stagewatch"; Start-Sleep -Seconds 10; Copy-Item "C:\ProgramData\Stagewatch" "$HOME\stagewatch-backup" -Recurse; Start-ScheduledTask -TaskName "Stagewatch"
```

Then copy that folder to a USB stick you keep safe. Do not put it in a cloud folder.

**Raspberry Pi.** Paste this in a Terminal:

```bash
sudo systemctl stop stagewatch && sudo cp -r /var/lib/stagewatch ~/stagewatch-backup && sudo chown -R $USER ~/stagewatch-backup && sudo systemctl start stagewatch
```

You will be without live readings for about half a minute while it copies.

---

## What to do if an update goes wrong

| What you see | What to do |
|---|---|
| **Check failed: Update source not reachable (repository is private or offline).** | The computer has no internet, or GitHub cannot be reached. Try again with internet. |
| **Not enough free disk space for a data backup.** | Free up space on the computer, then try again. |
| **The update source rejected the fetch (a release tag may have changed)** | Stagewatch refuses on purpose. Do **not** force it. Ask whoever looks after Stagewatch to check with the project. |
| **This install has local changes; refusing to update.** | Someone edited files in the program folder. Ask a technical person. |
| Page stays on "restarting" | Wait 2 minutes. If nothing, reload the page. If nothing after 10 minutes, restart the computer. Then look at the log (see [Troubleshooting](troubleshooting.md#where-the-log-files-are)) and the `launcher.log`. |
| Stagewatch works, but the new version behaves badly | Use **Roll back...** (above). |

If Stagewatch will not start at all after an update, it should roll itself back within a
few minutes. If it has not, restart the computer, and check the log.

Other messages you may see are short and say what to do, such as "Check for updates
first." or "The target changes the Python requirement; update manually." Those last
ones need a technical person to update by hand.

---

## Forgotten PIN, or "Recovery required"

### Forgot the PIN

There is no "forgot password" button, on purpose. Anyone on the network could use it.
Instead, someone with access to the Stagewatch **computer itself** resets it.

### "Recovery required" screen

You may see **Recovery required** on the admin page. It means Stagewatch's settings file
(`config.yaml`) could not be read and the PIN could not be saved from it. Stagewatch
keeps the bad file, tries hard to keep every part that is still good (including the PIN),
and normally starts fine. It only shows this screen if the PIN itself was lost. While it
is showing, setting a new PIN over the network is switched off so a stranger cannot take
over.

Stagewatch also keeps `config.yaml.bak`, a copy of the last good settings. It uses it by
itself when the main file is empty or damaged.

**The fix is the same as for a forgotten PIN.** Do the steps below. Your dashboards keep
working during all of this.

### Reset the PIN on Windows

1. On the Stagewatch PC, open PowerShell **as Administrator**
   ([how](install-windows.md#step-2-open-powershell-as-administrator)).
2. **Stop Stagewatch.** Paste this and press Enter, then wait 10 seconds:

```powershell
Stop-ScheduledTask -TaskName "Stagewatch"
```

3. **Reset the PIN.** Paste this and press Enter:

```powershell
$env:PYTHONPATH = "C:\Stagewatch\src"; & "C:\Stagewatch\.venv\Scripts\python.exe" -P -m stagewatch reset-admin-pin --data-dir "C:\ProgramData\Stagewatch"
```

   **What you should see:**

```text
Admin PIN cleared. Restart Stagewatch, then open /admin to set a new PIN.
Note: until you set the new PIN in /admin, anyone on the network can set it. Restart Stagewatch and do it promptly.
```

4. **Start Stagewatch again:**

```powershell
Start-ScheduledTask -TaskName "Stagewatch"
```

5. Wait about 30 seconds. **Straight away**, open `http://localhost:8080/admin` and set
   a new PIN. Until you do, **anyone on the network can set it**.

If step 3 says **"Stagewatch appears to be running on this data folder"**, it did not stop
yet. Wait a bit and try again. Do not add `--force` unless you are sure it is stopped.

### Reset the PIN on a Raspberry Pi

1. On the Pi, open a **Terminal**.
2. Paste this and press Enter. It stops Stagewatch, resets the PIN, and starts it
   again:

```bash
sudo systemctl stop stagewatch; cd / && sudo -u stagewatch env PYTHONPATH=/opt/stagewatch/src /opt/stagewatch/.venv/bin/python -P -m stagewatch reset-admin-pin --data-dir /var/lib/stagewatch; sudo systemctl start stagewatch
```

**What you should see:** the two lines `Admin PIN cleared. Restart Stagewatch...` and
`Note: until you set the new PIN...`.

3. Wait 30 seconds. **Straight away**, open `http://localhost:8080/admin` and set a new PIN.

> The Pi steps have not been tested on real hardware.

### Or restore a backup instead

If you do not want to reset, the alternative is to copy a good `config.yaml` back into the
data folder from a backup, then restart Stagewatch. The bad file is kept next to it as
`config.invalid...yaml`.

---

**Next:** [Troubleshooting](troubleshooting.md).
