<#
.SYNOPSIS
  Forgotten the Stagewatch admin PIN, or the admin page says "Recovery required"?
  Run this on the Stagewatch computer to clear the PIN so you can set a new one.

.DESCRIPTION
  Right-click the Start button > "Terminal (Admin)" or "Windows PowerShell (Admin)", then:
    powershell -ExecutionPolicy Bypass -File C:\Stagewatch\deploy\windows\reset-admin-pin.ps1

  What it does: stops the "Stagewatch" scheduled task, clears the admin PIN in the data folder,
  and starts Stagewatch again. It reads the install and data folders from the install's marker
  file. Nothing else (devices, thresholds, history) is changed.

  Options (only needed for a non-standard install): -InstallDir C:\Stagewatch  -DataDir <folder>

  Until you set a new PIN, anyone who can reach Stagewatch on the network can set it, so do it
  straight away.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\Stagewatch",
    [string]$DataDir = ""
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This needs to run as Administrator." -ForegroundColor Yellow
    Write-Host "Right-click the Start button, choose 'Terminal (Admin)' or 'Windows PowerShell (Admin)', and run this again."
    exit 1
}

$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$marker = Join-Path $InstallDir ".git\stagewatch-managed.json"
$emulate = $false
if (Test-Path -LiteralPath $marker) {
    $m = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if ($m.repo_path) { $InstallDir = [string]$m.repo_path }
    if (-not $DataDir -and $m.data_dir) { $DataDir = [string]$m.data_dir }
    if ($m.emulate) { $emulate = $true }
}
if (-not $DataDir) { $DataDir = "$env:ProgramData\Stagewatch" }

$python = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Could not find Stagewatch at $InstallDir." -ForegroundColor Red
    Write-Host "If you installed it somewhere else, run this again with:  -InstallDir <folder>"
    exit 1
}
if (-not (Test-Path -LiteralPath $DataDir)) {
    Write-Host "Could not find the data folder $DataDir." -ForegroundColor Red
    Write-Host "Run this again with:  -DataDir <folder>"
    exit 1
}

$task = Get-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Stopping Stagewatch ..."
    # Ask the launcher to stop the server cleanly first; Stop-ScheduledTask only kills it.
    $stopRequest = Join-Path $InstallDir ".git\stagewatch\stop-request"
    if (Test-Path -LiteralPath (Split-Path $stopRequest)) {
        New-Item -ItemType File -Force -Path $stopRequest | Out-Null
        for ($i = 0; $i -lt 30; $i++) {   # up to ~15 s
            if ((Get-ScheduledTask -TaskName "Stagewatch").State -ne "Running") { break }
            Start-Sleep -Milliseconds 500
        }
        Remove-Item -LiteralPath $stopRequest -Force -ErrorAction SilentlyContinue
    }
    Stop-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5   # let the server let go of its files
} else {
    Write-Host "The 'Stagewatch' scheduled task was not found; carrying on (is Stagewatch running some other way? Stop it first)."
}

$code = 1
try {
    $env:PYTHONPATH = Join-Path $InstallDir "src"
    $pyArgs = @("-P", "-m", "stagewatch", "reset-admin-pin", "--data-dir", $DataDir)
    if ($emulate) { $pyArgs += "--emulate" }   # an emulate install keeps its data in <data folder>\emulate
    & $python @pyArgs
    $code = $LASTEXITCODE
} finally {
    Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "Starting Stagewatch again ..."
        Start-ScheduledTask -TaskName "Stagewatch"
    }
}

Write-Host ""
if ($code -eq 0) {
    Write-Host "Done. The admin PIN has been cleared." -ForegroundColor Green
    Write-Host "Next: open Stagewatch in your browser (http://localhost:8080/admin, or your tablet's address) and set a new PIN now."
    Write-Host "Until you do, anyone on the network could set it."
} else {
    Write-Host "The reset did not work (see the message above). Stagewatch has been started again; nothing was changed." -ForegroundColor Red
    Write-Host "If you are stuck, use 'Download diagnostics' on the admin page if you can, or ask for help with the message above."
}
exit $code
