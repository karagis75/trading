# Start the Trading Scans dashboard on this machine.
# Uses TRADING_DATABASE_URL when set; otherwise the local SQLite history file.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$HostName = if ($env:TRADING_WEB_HOST) { $env:TRADING_WEB_HOST } else { "127.0.0.1" }
$Port = if ($env:TRADING_WEB_PORT) { $env:TRADING_WEB_PORT } else { "8000" }

Write-Host "Starting Trading Scans dashboard at http://${HostName}:${Port}/"
if ($env:TRADING_DATABASE_URL) {
    Write-Host "TRADING_DATABASE_URL is set (Postgres cache / membership history)."
} else {
    Write-Warning "TRADING_DATABASE_URL is not set in this window. The dashboard will use local SQLite and the pinball chart will not see today's prefetch."
}
python -m webapp
