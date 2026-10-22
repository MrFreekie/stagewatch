<#
.SYNOPSIS
    Stops Stagewatch cleanly on Windows, so a backup or restore copies a quiet data folder.
.DESCRIPTION
    Asks the launcher to stop the server (it writes the stop-request file the launcher watches, the same
    way install-service.ps1 does), waits up to about 15 seconds for the task to finish, then makes sure
    the scheduled task is stopped. Stagewatch does not record this as an unexpected stop.
    Run it in PowerShell as Administrator. Start Stagewatch again afterwards with:
        Start-ScheduledTask -TaskName "Stagewatch"
.PARAMETER InstallDir
    Where Stagewatch is installed (default C:\Stagewatch).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\Stagewatch"
)

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Stagewatch is not installed as a scheduled task here. Nothing to stop."
    exit 0
}

$stopRequest = Join-Path $InstallDir ".git\stagewatch\stop-request"
if (Test-Path -LiteralPath (Split-Path $stopRequest)) {
    New-Item -ItemType File -Force -Path $stopRequest | Out-Null
    for ($i = 0; $i -lt 30; $i++) {   # up to about 15 seconds
        if ((Get-ScheduledTask -TaskName "Stagewatch").State -ne "Running") { break }
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $stopRequest -Force -ErrorAction SilentlyContinue
}
else {
    Write-Host "Could not find the launcher's folder in $InstallDir. Stopping the task directly."
}

Stop-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3   # let the server release its files
Write-Host "Stagewatch is stopped."
