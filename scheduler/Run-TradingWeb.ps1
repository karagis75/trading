# Start the Trading Scans dashboard on this machine.
# Uses TRADING_DATABASE_URL when set; otherwise the local SQLite history file.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$HostName = if ($env:TRADING_WEB_HOST) { $env:TRADING_WEB_HOST } else { "127.0.0.1" }
$Port = if ($env:TRADING_WEB_PORT) { $env:TRADING_WEB_PORT } else { "8000" }

Write-Host "Starting Trading Scans dashboard at http://${HostName}:${Port}/"
python -m webapp
