<#
.SYNOPSIS
  Remove the Stagewatch autostart task and firewall rules. Data is left in place.

.DESCRIPTION
  Stops the task (the launcher's Job Object takes the server down with it), unregisters it and
  removes the firewall rules. The data folder (default C:\ProgramData\Stagewatch, with backups)
  is ALWAYS kept. The code folder (default C:\Stagewatch) is kept too unless you pass
  -RemoveCode, which deletes it only if it contains the managed marker.

  Run from an elevated PowerShell:
    powershell -ExecutionPolicy Bypass -File uninstall-service.ps1 [-InstallDir C:\Stagewatch] [-RemoveCode]
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\Stagewatch",
    [string]$DataDir = "$env:ProgramData\Stagewatch",
    [switch]$RemoveCode
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated (Administrator) PowerShell."
}

Stop-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "Stagewatch" -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "Stagewatch*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

if ($RemoveCode) {
    $InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    $marker = Join-Path $InstallDir ".git\stagewatch-managed.json"
    if (Test-Path -LiteralPath $marker) {
        Start-Sleep -Seconds 2   # let the server release its files
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
        Write-Host "Removed $InstallDir."
    } else {
        Write-Warning "$InstallDir has no managed marker; not deleting it."
    }
}

Write-Host "Stagewatch autostart removed. Data in $DataDir was kept."
