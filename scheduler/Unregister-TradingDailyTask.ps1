#Requires -Version 5.1
<#
.SYNOPSIS
    Remove the TradingDailyScans Windows scheduled task.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'TradingDailyScans'
)

$ErrorActionPreference = 'Stop'

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Scheduled task '$TaskName' is not registered."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'."
