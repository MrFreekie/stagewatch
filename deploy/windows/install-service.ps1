<#
.SYNOPSIS
  Install Stagewatch to start automatically at boot on Windows (before anyone logs in).

.DESCRIPTION
  Registers a Scheduled Task "Stagewatch" that runs as SYSTEM at startup and
  restarts automatically if the server stops. Uses only built-in Windows
  features (no third-party service wrapper). Also opens the web port on the
  Private/Domain firewall profiles so tablets on the show network can connect.

  Run from an elevated PowerShell in the repository folder:
    powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1
  Options:
    -Port 8080  -DataDir C:\ProgramData\Stagewatch  -Emulate
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$DataDir = "$env:ProgramData\Stagewatch",
    [switch]$Emulate
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated (Administrator) PowerShell."
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Write-Host "Repository: $repo"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { throw "uv not found on PATH. Install it from https://docs.astral.sh/uv/ and re-run." }

Push-Location $repo
try { & $uv.Source sync --frozen --no-dev; if ($LASTEXITCODE -ne 0) { throw "uv sync failed" } }
finally { Pop-Location }

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment missing: $python" }

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$argsLine = "-m stagewatch --port $Port --data-dir `"$DataDir`""
if ($Emulate) { $argsLine += " --emulate" }

$action = New-ScheduledTaskAction -Execute $python -Argument $argsLine -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName "Stagewatch" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $taskPrincipal -Force `
    -Description "Stagewatch show-site monitoring hub (http://localhost:$Port)" | Out-Null

# Firewall: web UI (TCP) and mDNS discovery (UDP 5353) on trusted profiles only.
Get-NetFirewallRule -DisplayName "Stagewatch*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Stagewatch web (TCP $Port)" -Direction Inbound -Protocol TCP `
    -LocalPort $Port -Action Allow -Profile Private,Domain | Out-Null
New-NetFirewallRule -DisplayName "Stagewatch mDNS (UDP 5353)" -Direction Inbound -Protocol UDP `
    -LocalPort 5353 -Program $python -Action Allow -Profile Private,Domain | Out-Null

Start-ScheduledTask -TaskName "Stagewatch"
Write-Host ""
Write-Host "Installed. Stagewatch starts at every boot and restarts if it stops."
Write-Host "  Dashboard: http://localhost:$Port   (tablets: http://<this-pc-ip>:$Port)"
Write-Host "  Data/logs: $DataDir"
Write-Host "Note: set the show network adapter to the 'Private' network profile so the firewall rules apply."
