<#
.SYNOPSIS
  Remove the Stagewatch autostart task and firewall rules. Data is left in place.
#>
$ErrorActionPreference = "Stop"
Stop-ScheduledTask -TaskName "Stagewatch" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "Stagewatch" -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "Stagewatch*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "Stagewatch autostart removed. Data in $env:ProgramData\Stagewatch was kept."
