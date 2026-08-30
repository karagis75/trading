# PostgreSQL setup

The scanners continue to write their configured Excel/CSV output files. The
membership history database is selected with `TRADING_DATABASE_URL`.

## Windows installation

Use the official PostgreSQL Windows installer from:
<https://www.postgresql.org/download/windows/>

Install PostgreSQL Server, Command Line Tools, and optionally pgAdmin. During
installation choose a strong password for the `postgres` administrator and
keep the default port `5432`.

Alternatively, Windows 10/11 systems with `winget` can use:

```powershell
winget install --id PostgreSQL.PostgreSQL.17 --exact `
  --accept-package-agreements --accept-source-agreements
```

## Create the Trading database and user

Open **SQL Shell (psql)** or a terminal where `psql` is on `PATH`:

```sql
CREATE USER trading_app WITH PASSWORD 'replace-with-a-strong-password';
CREATE DATABASE trading_history OWNER trading_app;
\q
```

Do not commit the password to the repository.

## Configure Windows

Set the connection URL for the account that runs Task Scheduler:

```powershell
[Environment]::SetEnvironmentVariable(
  "TRADING_DATABASE_URL",
  "postgresql://trading_app:replace-with-a-strong-password@localhost:5432/trading_history",
  "User"
)
```

Open a new PowerShell window after setting the variable. The application
creates the schema automatically on its first connection.

## Install the Python driver

From the repository:

```powershell
python -m pip install -r requirements.txt
```

## Migrate existing SQLite history

If `scanner_history\scanner_history.sqlite3` contains history to preserve:

```powershell
python -m scanner_history.migrate_sqlite_to_postgres `
  --source scanner_history\scanner_history.sqlite3 `
  --target $env:TRADING_DATABASE_URL
```

To migrate only Combined Option Spread Analysis (`combined-option-v8`):

```powershell
python -m scanner_history.migrate_sqlite_to_postgres `
  --source scanner_history\scanner_history.sqlite3 `
  --target $env:TRADING_DATABASE_URL `
  --scanner "Combined Option Spread Analysis"
```

`--scanner` accepts either the scanner id (`combined-option-v8`) or the
display name. Existing PostgreSQL rows are skipped (`ON CONFLICT DO NOTHING`),
so re-running the command is safe.
## Verify the connection

```powershell
python -c "from scanner_history import db; c=db.connect(__import__('os').environ['TRADING_DATABASE_URL']); print('PostgreSQL connection OK'); c.close()"
```

The scheduled jobs still write their normal files under `outputs\YYYY-MM-DD`.
The runner reads those files and stores membership history in PostgreSQL.

## Shared Yahoo daily-bar cache

The first scheduled job, `prefetch-yahoo-ohlcv`, stores Nifty 500 daily OHLCV
in PostgreSQL (or local SQLite if `TRADING_DATABASE_URL` is unset). There is
**no folder of 2-year bar files**. The cache is these tables in
`trading_history`:

- `yahoo_ohlcv_daily` (one row per symbol per session; this is the 2-year history)
- `yahoo_ohlcv_prefetch` (per-symbol status for that calendar day)

A first-time symbol downloads two years. On later mornings the job reads
`MAX(bar_date)` for each name and requests only the gap plus a few overlap
days, then upserts those rows. Scanner Excel/CSV files under
`outputs\YYYY-MM-DD` are hit lists, not the raw Yahoo bars.

Every later Yahoo scanner (`bullish-bias-nifty500`, `minervini-volume-cpr`,
fib pinball, and the rest) reads `yahoo_ohlcv_daily` instead of calling Yahoo
again.

If a ticker is missing from the cache, that scanner falls back to a live
Yahoo chart request and stores the result for the remainder of the day.

## Confirm the scheduled run is actually using PostgreSQL

`daily_once_runner.py` logs its resolved database target at the start of every
run:

```
INFO Membership history database target: postgresql://trading_app:***@localhost:5432/trading_history
```

If that line instead shows a path ending in
`scanner_history\scanner_history.sqlite3`, `TRADING_DATABASE_URL` is not
visible to the process that Task Scheduler launched (commonly because the
variable was set with User scope in a different login session than the one
Task Scheduler uses, or the task runs under a different account). Fixes:

- Set the variable at Machine scope instead of User scope:
  ```powershell
  [Environment]::SetEnvironmentVariable(
    "TRADING_DATABASE_URL",
    "postgresql://trading_app:replace-with-a-strong-password@localhost:5432/trading_history",
    "Machine"
  )
  ```
  Machine-scope variables are visible to every account and session without
  requiring a fresh logon.
- Or pass it explicitly on each run: `python daily_once_runner.py --history-db "%TRADING_DATABASE_URL%"`.
- After changing the variable, reboot or at minimum restart the Task
  Scheduler task's session (log off/on) before the next scheduled run.

If earlier runs already wrote to the local SQLite file while the environment
variable was not visible, backfill PostgreSQL from that file (safe to re-run;
existing rows are skipped):

```powershell
python -m scanner_history.migrate_sqlite_to_postgres `
  --source scanner_history\scanner_history.sqlite3 `
  --target $env:TRADING_DATABASE_URL
```

For Combined Option Spread Analysis only:

```powershell
python -m scanner_history.migrate_sqlite_to_postgres `
  --source scanner_history\scanner_history.sqlite3 `
  --target $env:TRADING_DATABASE_URL `
  --scanner combined-option-v8
```
