<#
.SYNOPSIS
  Managed install of Stagewatch on Windows: starts at boot (before anyone logs in), restarts
  itself if it crashes, and can be updated in-app.

.DESCRIPTION
  This is the ONLY install mode. (The old mode ran code from a user-writable checkout as SYSTEM,
  which let any process running as that user escalate to SYSTEM; it was removed.)

  What it does, in order:
    1. Refuses if InstallDir already exists and is not owned by BUILTIN\Administrators.
    2. Clones the repository into InstallDir (default C:\Stagewatch), checks out the requested
       release (default: the latest vX.Y.Z tag that is reachable from origin/main) detached.
    3. Sets the owner to Administrators and removes inherited ACLs: SYSTEM + Administrators
       full control, Users read/execute only. (Without this, C:\ would grant Modify to
       Authenticated Users.)
    4. Keeps the whole toolchain inside InstallDir: copies uv.exe to InstallDir\.uv\bin, puts
       Python and the uv cache in InstallDir\.uv (never in a user profile), then
       `uv python install` and `uv sync --frozen --no-dev --no-install-project`.
    5. Creates the data folder (default C:\ProgramData\Stagewatch) with a protected ACL:
       SYSTEM + Administrators only. Users get no access.
    6. Writes the managed marker InstallDir\.git\stagewatch-managed.json (absolute git.exe and
       uv.exe paths, uv environment, data dir, port, channel).
    7. Registers the Scheduled Task "Stagewatch": runs the LAUNCHER (not the server) as SYSTEM
       at startup. The launcher supervises the server (Task Scheduler itself does not restart a
       process that has exited), applies updates and rolls back failed ones.
    8. Opens the web port and mDNS on the Private/Domain firewall profiles.

  Run from an elevated PowerShell (any folder):
    powershell -ExecutionPolicy Bypass -File install-service.ps1
  Options:
    -InstallDir C:\Stagewatch   -DataDir C:\ProgramData\Stagewatch   -Port 8080
    -Channel stable|nightly     -Ref <tag-or-sha>   -SourceUrl <https git url>   -Emulate
    -GitPath "C:\Program Files\Git\cmd\git.exe"

  Tested on Windows 11 and Windows 10 virtual machines, not yet on a real show PC: try it on your
  own machine (ACLs, SYSTEM start, launcher restart) before relying on it at a show. Existing data in DataDir is kept.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\Stagewatch",
    [string]$DataDir = "$env:ProgramData\Stagewatch",
    [int]$Port = 8080,
    [ValidateSet("stable", "nightly")][string]$Channel = "stable",
    [string]$Ref = "",
    [string]$SourceUrl = "https://github.com/MrFreekie/stagewatch.git",
    [string]$GitPath = "C:\Program Files\Git\cmd\git.exe",
    [switch]$Emulate
)
$ErrorActionPreference = "Stop"

$ADMINS = "S-1-5-32-544"   # BUILTIN\Administrators (SIDs, so this works on any Windows language)
$SYSTEM = "S-1-5-18"
$USERS = "S-1-5-32-545"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated (Administrator) PowerShell."
}
if ($SourceUrl -notlike "https://*") { throw "SourceUrl must be an https:// URL." }
if (-not (Test-Path -LiteralPath $GitPath -PathType Leaf)) {
    throw "git.exe not found at $GitPath. Install Git for Windows (system-wide) or pass -GitPath."
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$DataDir = [IO.Path]::GetFullPath($DataDir).TrimEnd('\')

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) { throw "uv not found on PATH. Install it from https://docs.astral.sh/uv/ and re-run." }
$uvSource = $uvCmd.Source

function Get-OwnerSid([string]$Path) {
    (Get-Acl -LiteralPath $Path).GetOwner([Security.Principal.SecurityIdentifier]).Value
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($Exe)) $($Arguments -join ' ') failed (exit $LASTEXITCODE)" }
}

# Hardened git (same flags as the launcher/updater, amendment D).
function Invoke-Git([string[]]$GitArgs, [switch]$AllowFail) {
    $safe = $InstallDir.Replace('\', '/')
    $flags = @("-c", "safe.directory=$safe", "-c", "core.hooksPath=NUL", "-c", "core.fsmonitor=false",
               "-c", "credential.helper=", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always")
    $env:GIT_TERMINAL_PROMPT = "0"; $env:GCM_INTERACTIVE = "never"; $env:GIT_CONFIG_NOSYSTEM = "1"
    $env:GIT_CONFIG_GLOBAL = $script:EmptyGitConfig
    Remove-Item Env:\GIT_ASKPASS -ErrorAction SilentlyContinue
    $out = & $GitPath @flags @GitArgs
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) {
        throw "git $($GitArgs -join ' ') failed (exit $LASTEXITCODE)"
    }
    if ($AllowFail) { return $LASTEXITCODE }
    return $out
}

# ---- 0. stop any previous task so files are not locked ------------------------------------
if (Get-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3   # the launcher's Job Object takes the server down; let it release files
}

# ---- 1. pre-existing InstallDir must be Administrators-owned ------------------------------
$fresh = -not (Test-Path -LiteralPath $InstallDir)
if (-not $fresh) {
    if ((Get-OwnerSid $InstallDir) -ne $ADMINS) {
        throw "$InstallDir already exists and is not owned by BUILTIN\Administrators. Refusing (someone may have pre-planted files). Remove it or choose another -InstallDir."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir ".git"))) {
        if (@(Get-ChildItem -LiteralPath $InstallDir -Force).Count -gt 0) {
            throw "$InstallDir exists, is not empty and is not a Stagewatch clone. Refusing."
        }
        $fresh = $true
    }
}

# A scratch empty gitconfig (GIT_CONFIG_GLOBAL) until the admin-only one exists.
$script:EmptyGitConfig = Join-Path ([IO.Path]::GetTempPath()) ("stagewatch-empty-gitconfig-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType File -Path $script:EmptyGitConfig | Out-Null

# ---- 2. clone / fetch, then check out the release detached ----------------------------
    if ($fresh) {
        # Create and lock the (empty) target BEFORE cloning: C:\ grants Modify to Authenticated
        # Users, so a clone into a fresh C:\Stagewatch would be user-writable until step 3.
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        Invoke-Native "icacls.exe" @($InstallDir, "/setowner", "*$ADMINS", "/C", "/Q")
        Invoke-Native "icacls.exe" @($InstallDir, "/inheritance:r", "/grant:r",
            "*${SYSTEM}:(OI)(CI)F", "*${ADMINS}:(OI)(CI)F", "*${USERS}:(OI)(CI)RX")
        Write-Host "Cloning $SourceUrl into $InstallDir ..."
        try {
            Invoke-Git @("clone", "--quiet", $SourceUrl, $InstallDir) | Out-Null
        } catch {
            # leave nothing behind (a re-run must not see a half-made install)
            Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
            throw "$($_.Exception.Message). Is $SourceUrl public and reachable from this machine? (In-app updates and this installer have no credential support; a private repository cannot be cloned.)"
        }
    } else {
        Write-Host "Existing managed clone found; fetching ..."
        $origin = (Invoke-Git @("-C", $InstallDir, "config", "--get", "remote.origin.url") | Select-Object -First 1)
        if ($origin -ne $SourceUrl) { throw "Existing clone's origin ($origin) differs from -SourceUrl ($SourceUrl). Refusing." }
        Invoke-Git @("-C", $InstallDir, "fetch", "--quiet", "--no-tags", "origin",
                     "+refs/heads/main:refs/remotes/origin/main", "+refs/heads/nightly:refs/remotes/origin/nightly",
                     "refs/tags/v*:refs/tags/v*") | Out-Null
    }
    $g = @("-C", $InstallDir)

    if ($Ref -ne "") {
        $sha = (Invoke-Git ($g + @("rev-parse", "--verify", "--quiet", "$Ref^{commit}")) | Select-Object -First 1)
    } elseif ($Channel -eq "nightly") {
        $sha = (Invoke-Git ($g + @("rev-parse", "--verify", "--quiet", "refs/remotes/origin/nightly^{commit}")) | Select-Object -First 1)
    } else {
        $tags = @(Invoke-Git ($g + @("tag", "--list", "v*")) | Where-Object { $_ -match '^v\d+\.\d+\.\d+$' })
        if ($tags.Count -eq 0) { throw "No release tag (vX.Y.Z) found in the repository. Pass -Ref <tag-or-sha>." }
        $latest = $tags | Sort-Object { [Version]($_.Substring(1)) } | Select-Object -Last 1
        Write-Host "Latest release tag: $latest"
        $sha = (Invoke-Git ($g + @("rev-parse", "--verify", "--quiet", "refs/tags/$latest^{commit}")) | Select-Object -First 1)
    }
    if (-not ($sha -match '^[0-9a-f]{40}$')) { throw "Could not resolve the requested ref to a commit." }
    # The commit must be reachable from origin/main (exit 0 yes, 1 no, >=2 error: both refuse).
    $rc = Invoke-Git ($g + @("merge-base", "--is-ancestor", $sha, "refs/remotes/origin/main")) -AllowFail
    if ($rc -ne 0) { throw "Commit $sha is not an ancestor of origin/main (or the check failed). Refusing." }
    Invoke-Git ($g + @("checkout", "--quiet", "--detach", $sha)) | Out-Null
    Write-Host "Checked out $sha (detached)."

    # ---- 3. ownership + ACLs on the code tree ---------------------------------------------
    Invoke-Native "icacls.exe" @($InstallDir, "/setowner", "*$ADMINS", "/T", "/C", "/Q")
    Invoke-Native "icacls.exe" @($InstallDir, "/inheritance:r", "/grant:r",
        "*${SYSTEM}:(OI)(CI)F", "*${ADMINS}:(OI)(CI)F", "*${USERS}:(OI)(CI)RX")
    foreach ($p in @($InstallDir, (Join-Path $InstallDir ".git"))) {
        $acl = (& icacls.exe $p) -join "`n"
        if ($acl -match "Authenticated Users|Everyone") { throw "ACL on $p still grants access to Authenticated Users/Everyone:`n$acl" }
    }

# ---- 4. self-contained toolchain -----------------------------------------------------------
$uvDir = Join-Path $InstallDir ".uv"
$uvBin = Join-Path $uvDir "bin"
New-Item -ItemType Directory -Force -Path $uvBin, (Join-Path $uvDir "python"), (Join-Path $uvDir "cache") | Out-Null
$uvExe = Join-Path $uvBin "uv.exe"
Copy-Item -LiteralPath $uvSource -Destination $uvExe -Force
Invoke-Native $uvExe @("--version")

$uvEnv = [ordered]@{
    UV_PYTHON_INSTALL_DIR      = (Join-Path $uvDir "python")
    UV_CACHE_DIR               = (Join-Path $uvDir "cache")
    UV_PYTHON_INSTALL_BIN      = "0"
    UV_PYTHON_INSTALL_REGISTRY = "0"
    UV_MANAGED_PYTHON          = "1"
}
foreach ($k in $uvEnv.Keys) { Set-Item -Path "Env:\$k" -Value $uvEnv[$k] }   # set BEFORE the venv is created

Push-Location $InstallDir
try {
    Invoke-Native $uvExe @("python", "install")
    $env:UV_PYTHON_DOWNLOADS = "never"
    Invoke-Native $uvExe @("sync", "--frozen", "--no-dev", "--no-install-project")
} finally { Pop-Location }

$python = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Virtual environment missing: $python" }

# Everything created since step 3 (.uv, .venv, downloaded Python) must be owned by Administrators
# too: the launcher refuses to apply updates if the repo root, .git, .venv, .uv or src is owned
# by anyone else. (An elevated token normally owns new files as Administrators; this makes it certain.)
Invoke-Native "icacls.exe" @($InstallDir, "/setowner", "*$ADMINS", "/T", "/C", "/Q")

# ---- 5. protected data folder --------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Invoke-Native "icacls.exe" @($DataDir, "/inheritance:r", "/grant:r", "*${SYSTEM}:(OI)(CI)F", "*${ADMINS}:(OI)(CI)F")

# ---- 6. state dir + managed marker ---------------------------------------------------------
$stateDir = Join-Path $InstallDir ".git\stagewatch"
New-Item -ItemType Directory -Force -Path (Join-Path $stateDir "backups") | Out-Null
$gitconfig = Join-Path $stateDir "gitconfig"
if (-not (Test-Path -LiteralPath $gitconfig)) { New-Item -ItemType File -Path $gitconfig | Out-Null }
Remove-Item -LiteralPath $script:EmptyGitConfig -Force -ErrorAction SilentlyContinue

$marker = Join-Path $InstallDir ".git\stagewatch-managed.json"
$markerObj = [ordered]@{
    format     = 1
    repo_path  = $InstallDir
    origin_url = $SourceUrl
    git_path   = $GitPath
    uv_path    = $uvExe
    uv_env     = $uvEnv
    data_dir   = $DataDir
    channel    = $Channel
    port       = $Port
    emulate    = [bool]$Emulate
    created    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
$json = $markerObj | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($marker, $json, (New-Object Text.UTF8Encoding($false)))   # UTF-8 without BOM

# Validate the marker with the launcher's own loader (fails loudly on a mistake).
$check = "import sys; sys.path.insert(0, r'$InstallDir\src'); from stagewatch import updater_common as u; m = u.load_marker(r'$marker'); print('marker ok:', m.channel, m.port)"
Invoke-Native $python @("-P", "-c", $check)

# ---- 7. Scheduled Task: launcher as SYSTEM at startup ---------------------------------------
$launcher = Join-Path $InstallDir "src\stagewatch\launcher.py"
$taskArgs = "-P `"$launcher`" --marker `"$marker`""
$action = New-ScheduledTaskAction -Execute $python -Argument $taskArgs -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
# ExecutionTimeLimit 0 (the 72 h default would kill the server); IgnoreNew (never two launchers).
# RestartCount/Interval are only a safety net for launch failures: Task Scheduler does NOT
# restart a process that ran and exited; the launcher does that.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "Stagewatch" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $taskPrincipal -Force `
    -Description "Stagewatch launcher/supervisor (http://localhost:$Port)" | Out-Null

# ---- 8. firewall: web UI (TCP) and mDNS (UDP 5353) on trusted profiles only -------------------
Get-NetFirewallRule -DisplayName "Stagewatch*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Stagewatch web (TCP $Port)" -Direction Inbound -Protocol TCP `
    -LocalPort $Port -Action Allow -Profile Private,Domain | Out-Null
# The venv's python.exe is only a redirector: the process that owns the socket is the real
# interpreter in .uv\python, and Windows Firewall matches on that image, so scope the rule to it.
$realPython = (& $python -P -c "import sys; print(sys._base_executable)" | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or -not $realPython -or -not (Test-Path -LiteralPath $realPython)) { $realPython = $python }
New-NetFirewallRule -DisplayName "Stagewatch mDNS (UDP 5353)" -Direction Inbound -Protocol UDP `
    -LocalPort 5353 -Program $realPython -Action Allow -Profile Private,Domain | Out-Null

Start-ScheduledTask -TaskName "Stagewatch"
Write-Host ""
Write-Host "Installed (managed). Stagewatch starts at every boot; the launcher restarts the server if it stops."
Write-Host "  Code:      $InstallDir  (release $sha, detached)"
Write-Host "  Dashboard: http://localhost:$Port   (tablets: http://<this-pc-ip>:$Port)"
Write-Host "  Data/logs: $DataDir  (SYSTEM + Administrators only; open an elevated Explorer/PowerShell to read)"
Write-Host "Note: set the show network adapter to the 'Private' network profile so the firewall rules apply."
Write-Host "Note: tested on Windows 11 and 10 virtual machines, not yet on a real show PC. Try it before show day."
