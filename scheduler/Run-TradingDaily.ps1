#Requires -Version 5.1
<#
.SYNOPSIS
    Task Scheduler entry point for the once-per-day trading scans.

.DESCRIPTION
    Invokes daily_once_runner.py. After a successful run the Python runner
    writes a marker, so later 8 AM / logon triggers exit immediately.
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$SchedulerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $SchedulerDir
$Runner = Join-Path $RepoRoot 'daily_once_runner.py'
$LogDir = Join-Path $SchedulerDir 'logs'

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "daily_once_runner.py was not found at $Runner"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PythonExecutable {
    $candidates = @(
        @{ File = 'py'; Args = @('-3') },
        @{ File = 'python'; Args = @() },
        @{ File = 'python3'; Args = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.File -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }
        try {
            $versionArgs = $candidate.Args + @('-c', 'import sys; print(sys.executable)')
            $executable = & $command.Source @versionArgs 2>$null | Select-Object -Last 1
            if ($LASTEXITCODE -eq 0 -and $executable) {
                return @{ Path = $executable.Trim(); PrefixArgs = @() }
            }
        } catch {
            continue
        }
    }
    throw 'Python 3 was not found. Install Python and ensure py/python is on PATH.'
}

$python = Get-PythonExecutable
$runnerArgs = @($python.PrefixArgs + @($Runner))
if ($Force) {
    $runnerArgs += '--force'
}
if ($Status) {
    $runnerArgs += '--status'
}

Set-Location -LiteralPath $RepoRoot
Write-Host "Running $($python.Path) $($runnerArgs -join ' ')"
& $python.Path @runnerArgs
exit $LASTEXITCODE
