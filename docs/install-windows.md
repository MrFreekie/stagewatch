# Install Stagewatch on Windows

This puts Stagewatch on a Windows 10 or 11 PC so that it **starts by itself whenever the
PC boots**, restarts itself if it crashes, and can be updated from a web page.

Time needed: about 20 minutes, mostly waiting for downloads.
Do this **at home or in the office**, with internet. Not at the venue.

> **Note.** This boot install has **not yet been tested on real hardware**.
> Try it on your own PC before you rely on it at a show.
> Stagewatch is an advisory tool with no warranty. See the
> [main README](../README.md#stagewatch).

You will need:

- A Windows PC where you can be an **Administrator** (you can approve the "Do you want to
  allow this app to make changes?" box).
- Internet.
- About 1 GB of free disk space.

---

## Words you will meet

- **PowerShell** is Windows' built-in command window. You paste a line, press Enter, and it
  runs.
- **Administrator** means "allowed to change the computer". The installer needs this.
- **IP address** is the computer's address on your network, like `192.168.1.50`.

More in the [glossary](glossary.md).

---

## Step 1. Install Git

Git is a free tool that downloads Stagewatch for you.

1. On the PC, open <https://git-scm.com/download/win>.
2. Click the link for the **64-bit Git for Windows Setup**. The file downloads.
3. Open the file. Click **Yes** if Windows asks to allow changes.
4. Click **Next** on every screen and leave every choice **as it is**. Then click
   **Install** and **Finish**.

**What you should see:** the installer finishes. Nothing else happens.

> **Important:** leave the install folder as the default (`C:\Program Files\Git`).
> The Stagewatch installer looks for Git there.

> **Quicker way, if you prefer.** Open PowerShell as Administrator (Step 2) and paste
> `winget install --id Git.Git -e --source winget`. It does the same job.

---

## Step 2. Open PowerShell as Administrator

1. Click the **Start** button (or press the Windows key).
2. Type `powershell`.
3. Right-click **Windows PowerShell** and choose **Run as administrator**.
4. Click **Yes** on the "Do you want to allow..." box.

**What you should see:** a blue (or black) window. The title bar says **Administrator:
Windows PowerShell**. If it does not say Administrator, close it and try again.

Keep this window handy. You will use it again.

---

## Step 3. Install uv

`uv` is a free tool that fetches and runs the right version of Python for Stagewatch.
You never have to touch Python yourself.

Paste this into the **Administrator PowerShell** window and press **Enter**:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

(This is the official command from [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/).)

**What you should see:** a few lines of text ending with something like
"everything's installed".

Now **close the PowerShell window and open a new Administrator one** (Step 2 again).
Windows only notices new programs in a new window.

Check both tools work. Paste this and press Enter:

```powershell
git --version; uv --version
```

**What you should see:** two lines, one starting `git version` and one starting `uv`.

If you get "not recognised" instead, see [If it doesn't work](#if-it-doesnt-work).

---

## Step 4. Download and run the Stagewatch installer

> **Read this first: which version?**
> The installer normally picks the **latest release** for you. **Until a newer release
> than v0.1.0 is published on the
> [Releases page](https://github.com/MrFreekie/stagewatch/releases), add `-Ref main`
> to the end of the second command below.** The old v0.1.0 does not contain the
> boot installer's parts and the install would fail.
> Once the Releases page shows **v0.2.0 or later**, leave `-Ref main` off.

**4a. Download the installer.** In the Administrator PowerShell window, paste this and
press Enter:

```powershell
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/MrFreekie/stagewatch/main/deploy/windows/install-service.ps1" -OutFile "$HOME\Downloads\install-service.ps1"
```

**What you should see:** nothing at all, and the prompt comes back. That is normal. A
file called `install-service.ps1` is now in your **Downloads** folder. It is plain text,
so you can open it in Notepad and read it if you like.

**4b. Run it.** Paste this and press Enter:

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\install-service.ps1" -Ref main
```

(Once a release later than v0.1.0 exists, drop the ` -Ref main` at the end.)

This takes **several minutes**. It downloads Stagewatch and its own copy of Python. Do not
close the window. Lots of text will scroll past.

**What you should see, in order:**

- `Cloning https://github.com/... into C:\Stagewatch ...`
- `Checked out ... (detached).`
- A lot of `uv` output as it downloads Python and libraries.
- `marker ok: stable 8080`
- At the end:

```text
Installed (managed). Stagewatch starts at every boot; the launcher restarts the server if it stops.
  Code:      C:\Stagewatch  ...
  Dashboard: http://localhost:8080   (tablets: http://<this-pc-ip>:8080)
  Data/logs: C:\ProgramData\Stagewatch ...
```

**That last block means it worked.**

What the installer just did, in plain words:

| Where | What |
|---|---|
| `C:\Stagewatch` | The program. Only Administrators can change it. |
| `C:\ProgramData\Stagewatch` | Your settings, history, PIN and logs. Only Administrators can read it. |
| Task Scheduler, task **Stagewatch** | Starts Stagewatch every time the PC boots, even before anyone logs in. |
| Windows Firewall | Lets tablets in on port **8080**, on **Private** networks only. |

---

## Step 5. Open Stagewatch on the PC

1. Wait about 30 seconds.
2. Open a web browser on the same PC.
3. Go to `http://localhost:8080`

**What you should see:** a page called **Stagewatch** with tiles for the dashboards
(**FOH**, **Phone**, **Wall**).

### Set your admin PIN

1. Go to `http://localhost:8080/admin`
2. It says **Welcome: set an admin PIN**. Choose a PIN of **at least 4 characters**.
   Type it twice.
3. Click **Set admin PIN**.

**Write the PIN down somewhere safe.** If you forget it there is a way back
([Updating and backups](updating-and-backups.md#forgotten-pin-or-recovery-required)), but it takes a
few steps.

> **Do this straight away.** Until a PIN is set, **anyone on the network** who opens
> `/admin` can set it.

**What you should see:** the admin page, with cards such as Site, Shows, Software and
ESPHome nodes.

---

## Step 6. Set the network to Private

The installer only opens the firewall for networks Windows calls **Private**. If your
network is set to **Public**, tablets will not get in.

**Windows 11**

1. Open **Settings** > **Network & internet**.
2. Click **Wi-Fi** (or **Ethernet** if the PC is plugged in by cable).
3. Click the **properties** of the connected network (its name, or **Properties**).
4. Under **Network profile type**, choose **Private network**.

**Windows 10**

1. Open **Settings** > **Network & Internet**.
2. Click **Wi-Fi** (or **Ethernet**), then click the name of your network.
3. Under **Network profile**, choose **Private**.

**Only do this on your own show network**, not on public Wi-Fi.

---

## Step 7. Open Stagewatch on a tablet or phone

1. Find the PC's **IP address**:
   - Open a normal PowerShell window (Start, type `powershell`, press Enter).
   - Paste `ipconfig` and press Enter.
   - Find your network in the list (Wi-Fi or Ethernet). Look for the line
     **IPv4 Address**. It looks like `192.168.1.50`. That is the address.
   - Or: **Settings** > **Network & internet** > your connection > **Properties**, and
     read **IPv4 address**.
2. Put the tablet on the **same Wi-Fi network** as the PC.
3. On the tablet, open the browser and type the address followed by `:8080`, for example
   `http://192.168.1.50:8080`
4. Bookmark a dashboard, or add it to the home screen (see
   [Using Stagewatch](using-stagewatch.md)).

**What you should see:** the same Stagewatch page as on the PC.

> **Tip: fix the PC's address.** Routers can change addresses when the PC restarts. Ask
> your router (or its owner) to always give this PC the same address. It is often called
> a "DHCP reservation" or "static lease". Then your tablet bookmarks keep working.

> **Also try** `http://stagewatch.local:8080`. Stagewatch announces this name on the
> network. It works on many phones, tablets and PCs but not all, so use the IP address
> if it doesn't.

---

## Step 8. Stop the PC going to sleep

A sleeping PC is not watching the air.

1. **Settings** > **System** > **Power** (or **Power & battery**).
2. Set **Screen and sleep** so the PC **never sleeps** while plugged in.
3. If it is a laptop, plug it in and think about what happens when you close the lid.

Nodes and tablets reconnect on their own after a restart.

---

## Try it without any sensors (optional)

Want to look around first? You can run a demo with **fake** sensors. It stores its data
separately and does not touch your real install. Only do this **if you have not yet
installed the boot install, or after you stop it**, because both use port 8080.

Paste this into a normal PowerShell window:

```powershell
git clone https://github.com/MrFreekie/stagewatch.git "$HOME\stagewatch-try"; cd "$HOME\stagewatch-try"; uv run stagewatch --emulate
```

The first run downloads a few things. When you see lines about "starting on
http://0.0.0.0:8080", open `http://localhost:8080`. A banner says **EMULATE MODE:
simulated sensors, not real data**.

Windows may pop up a firewall question. For a home demo you can click **Allow**.

Press **Ctrl + C** in the window to stop it.

> Do not install with `-Emulate` on the show PC. That would mix fake readings into the
> real history.

---

## If it doesn't work

| What you see | What it means | What to do |
|---|---|---|
| `Run this from an elevated (Administrator) PowerShell.` | The window is not an Administrator one. | Close it. Open a new one with **Run as administrator** (Step 2). |
| `git.exe not found at C:\Program Files\Git\cmd\git.exe` | Git is not installed, or it went somewhere else. | Redo Step 1 with the default folder. If Git is somewhere else, add `-GitPath "C:\path\to\git.exe"` to the run command. |
| `uv not found on PATH` or `'uv' is not recognized` | uv is not installed, or you did not open a **new** window after installing it. | Close PowerShell, open a **new Administrator** PowerShell, run `uv --version`. If it still fails, redo Step 3. |
| `git : The term 'git' is not recognized` | Same, for Git. | Close PowerShell and open a new one. If still no, redo Step 1. |
| `...public and reachable from this machine?` | The PC could not reach GitHub. | Check the internet. Open <https://github.com/MrFreekie/stagewatch> in a browser on that PC. Try again. Work networks sometimes block GitHub. |
| `No release tag ... Pass -Ref <tag-or-sha>` | No release is published. | Add `-Ref main` to the command in Step 4b. |
| An error mentioning `updater_common` or `launcher.py` | The installer picked the old v0.1.0 release. | Run Step 4b again **with** `-Ref main`. It is safe to re-run. |
| `C:\Stagewatch already exists and is not owned by BUILTIN\Administrators` | A folder called `C:\Stagewatch` was made by something else. The installer refuses on purpose. | Look inside it. If you made it and it holds nothing you need, delete it and try again. |
| `... is not empty and is not a Stagewatch clone` | Same, with files in it. | Same. Move or delete the folder, or choose another with `-InstallDir`. |
| The browser says "can't connect" on the PC | Stagewatch is still starting, or is not running. | Wait a minute. Then see [Troubleshooting](troubleshooting.md#the-page-will-not-load-on-the-pc). |
| Works on the PC, but the tablet cannot connect | Wrong network, Public profile, or a firewall or antivirus block. | See [Troubleshooting](troubleshooting.md#the-tablet-cannot-open-stagewatch). |
| Antivirus or SmartScreen warns about a download or script | Common with scripts and new programs. | Only continue if the file came from the links in this guide. The scripts are plain text you can read on the [GitHub page](https://github.com/MrFreekie/stagewatch). If your antivirus keeps blocking, pause it while installing, then turn it back on. |

Still stuck? Copy the **last lines** of the red error text and ask on the
[GitHub issues page](https://github.com/MrFreekie/stagewatch/issues). Do not post PINs or
Wi-Fi passwords.

---

## Uninstall

1. Open PowerShell **as Administrator** (Step 2).
2. Paste this and press Enter:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Stagewatch\deploy\windows\uninstall-service.ps1" -RemoveCode
```

**What you should see:** `Removed C:\Stagewatch.` then
`Stagewatch autostart removed. Data in C:\ProgramData\Stagewatch was kept.`

This removes the auto-start, the firewall rules and the program.
**Your data is always kept**, in `C:\ProgramData\Stagewatch`.
If you want it gone too, delete that folder yourself. It holds your history, PIN and
node keys, so make sure you want that first.

Leave out ` -RemoveCode` to remove only the auto-start and keep the program folder.

Git and uv stay installed. Remove them in **Settings** > **Apps** if you want.

---

**Next:** [Build your first sensor node](first-sensor-node.md).
