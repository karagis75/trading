#Requires -Version 5.1
<#
.SYNOPSIS
    Register the TradingDailyScans Windows scheduled task.

.DESCRIPTION
    Creates a task that starts the scanners:
      * every day at 08:00 local time
      * the first time you log on (open the machine)

    The Python runner records a successful run for the calendar day. Any later
    trigger the same day exits immediately, which stops the schedule for the day.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scheduler\Register-TradingDailyTask.ps1
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'TradingDailyScans',
    [string]$DailyAt = '08:00',
    [int]$LogonDelaySeconds = 45,
    [int]$RestartCount = 3,
    [int]$RestartIntervalMinutes = 15
)

$ErrorActionPreference = 'Stop'
$SchedulerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunScript = Join-Path $SchedulerDir 'Run-TradingDaily.ps1'

if (-not (Test-Path -LiteralPath $RunScript)) {
    throw "Run-TradingDaily.ps1 was not found at $RunScript"
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory (Split-Path -Parent $SchedulerDir)

$daily = New-ScheduledTaskTrigger -Daily -At $DailyAt
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
if ($LogonDelaySeconds -gt 0) {
    $logon.Delay = "PT${LogonDelaySeconds}S"
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -RestartCount $RestartCount `
    -RestartInterval (New-TimeSpan -Minutes $RestartIntervalMinutes)

$settings.RunOnlyIfNetworkAvailable = $true
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries = $false

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

$description = @(
    "Run trading scanners daily at $DailyAt local time, or the first time this machine is opened."
    'After a successful run the Python marker stops later triggers for the rest of that calendar day.'
    'A failed run is retried on the next 8 AM or logon trigger.'
    'ExecutionTimeLimit is 6 hours. Prefetch itself times out after 20 minutes; if the daily log stops mid-job, check LastTaskResult (0xFFFD0000 means the task was killed).'
) -join ' '

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($daily, $logon) `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force | Out-Null

$triggerSummary = @(
    "daily $DailyAt"
    'at logon (first time the machine is opened)'
)

Write-Host "Registered scheduled task '$TaskName'."
Write-Host ("Triggers: " + ($triggerSummary -join '; '))
Write-Host "Action: powershell.exe -File $RunScript"
Write-Host "StartWhenAvailable is on, so a missed 8 AM run starts when the machine is next opened."
Write-Host "Use Unregister-TradingDailyTask.ps1 to remove the task."
Get-ScheduledTaskInfo -TaskName $TaskName | Format-List TaskName, NextRunTime, LastTaskResult
