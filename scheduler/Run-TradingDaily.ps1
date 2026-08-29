#Requires -Version 5.1
<#
.SYNOPSIS
    Task Scheduler entry point for the once-per-day trading scans.

.DESCRIPTION
    Invokes daily_once_runner.py. After a successful run the Python runner
    writes a marker, so later 8 AM / logon triggers exit immediately.

    When the scan finishes successfully the script:
      1. Starts the Trading Scans web dashboard (python -m webapp) if it is not
         already listening on the configured port.
      2. Opens http://127.0.0.1:<port>/ in the default browser.

    Set TRADING_WEB_PORT (default 8000) and TRADING_WEB_HOST (default 127.0.0.1)
    to override the dashboard address.
    Set TRADING_WEB_SKIP_OPEN=1 to disable the auto-open behaviour.
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Status,
    [switch]$SkipOpen
)

$ErrorActionPreference = 'Stop'
$SchedulerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot     = Split-Path -Parent $SchedulerDir
$Runner       = Join-Path $RepoRoot 'daily_once_runner.py'
$LogDir       = Join-Path $SchedulerDir 'logs'

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "daily_once_runner.py was not found at $Runner"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PythonExecutable {
    $candidates = @(
        @{ File = 'py';      Args = @('-3') },
        @{ File = 'python';  Args = @()     },
        @{ File = 'python3'; Args = @()     }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.File -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            $versionArgs = $candidate.Args + @('-c', 'import sys; print(sys.executable)')
            $executable  = & $command.Source @versionArgs 2>$null | Select-Object -Last 1
            if ($LASTEXITCODE -eq 0 -and $executable) {
                return @{ Path = $executable.Trim(); PrefixArgs = @() }
            }
        } catch { continue }
    }
    throw 'Python 3 was not found. Install Python and ensure py/python is on PATH.'
}

# ── Resolve dashboard URL ─────────────────────────────────────────────────────
$WebHost = if ($env:TRADING_WEB_HOST) { $env:TRADING_WEB_HOST } else { '127.0.0.1' }
$WebPort = if ($env:TRADING_WEB_PORT) { $env:TRADING_WEB_PORT } else { '8000' }
$DashUrl = "http://${WebHost}:${WebPort}/"

function Test-PortOpen([string]$hostname, [int]$port) {
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new()
        $async = $tcp.BeginConnect($hostname, $port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(800, $false)
        $tcp.Close()
        return $ok
    } catch {
        return $false
    }
}

function Start-Dashboard([string]$python) {
    $webScript = Join-Path $SchedulerDir 'Run-TradingWeb.ps1'
    if (Test-Path -LiteralPath $webScript) {
        Write-Host "Starting dashboard via Run-TradingWeb.ps1 ..."
        Start-Process -FilePath 'powershell.exe' `
            -ArgumentList '-NonInteractive', '-File', $webScript `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden
    } else {
        Write-Host "Starting dashboard via python -m webapp ..."
        $env:TRADING_WEB_HOST = $WebHost
        $env:TRADING_WEB_PORT = $WebPort
        Start-Process -FilePath $python `
            -ArgumentList '-m', 'webapp' `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden
    }
    # Give Flask up to 10 seconds to start accepting connections.
    $waited = 0
    while ($waited -lt 10) {
        Start-Sleep -Milliseconds 800
        $waited += 0.8
        if (Test-PortOpen $WebHost ([int]$WebPort)) {
            Write-Host "Dashboard is up at $DashUrl"
            return $true
        }
    }
    Write-Warning "Dashboard did not respond within 10 s. It may still be starting."
    return $false
}

# ── Run the daily scanner ─────────────────────────────────────────────────────
$python     = Get-PythonExecutable
$runnerArgs = @($python.PrefixArgs + @($Runner))
if ($Force)  { $runnerArgs += '--force'  }
if ($Status) { $runnerArgs += '--status' }

Set-Location -LiteralPath $RepoRoot
Write-Host "Running $($python.Path) $($runnerArgs -join ' ')"
& $python.Path @runnerArgs
$scanExitCode = $LASTEXITCODE

# ── Open dashboard on success (exit 0) ───────────────────────────────────────
$skipOpen = $SkipOpen -or ($env:TRADING_WEB_SKIP_OPEN -eq '1')
if ($scanExitCode -eq 0 -and -not $skipOpen) {
    Write-Host "Scan finished successfully. Opening dashboard ..."
    if (-not (Test-PortOpen $WebHost ([int]$WebPort))) {
        Start-Dashboard $python.Path | Out-Null
    } else {
        Write-Host "Dashboard already running at $DashUrl"
    }
    # Open the URL in the default browser.
    Start-Process $DashUrl
    Write-Host "Opened $DashUrl in default browser."
}

exit $scanExitCode
