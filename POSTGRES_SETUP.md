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

## Verify the connection

```powershell
python -c "from scanner_history import db; c=db.connect(__import__('os').environ['TRADING_DATABASE_URL']); print('PostgreSQL connection OK'); c.close()"
```

The scheduled jobs still write their normal files under `outputs\YYYY-MM-DD`.
The runner reads those files and stores membership history in PostgreSQL.
