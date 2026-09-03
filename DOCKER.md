# Docker

Run the Flask dashboard and fire one daily scanner pass without Windows Task Scheduler.

The once-per-calendar-day gate is unchanged: `daily_once_runner.py` still uses the lock file and `scheduler/state/run-state.json`. Compose only supplies Postgres, timezone, and a way to invoke that runner.

## Build and start the dashboard

From the repository root:

```bash
cp .env.example .env   # then change POSTGRES_PASSWORD
docker compose up -d --build
```

Open `http://localhost:8000/`. Flask binds `0.0.0.0:8000` inside the container (`TRADING_WEB_HOST`). Host runs of `python -m webapp` still default to `127.0.0.1`.

`TRADING_DATABASE_URL` points at the Compose Postgres service (`postgres:16-alpine` on `postgres:5432`). Calendar day is `Asia/Kolkata`.

Stop:

```bash
docker compose down
```

Postgres data lives in the `postgres_data` volume. Scanner Excel/CSV files, the daily lock, and logs are bind-mounted at `./outputs`, `./scheduler/state`, and `./scheduler/logs`.

## Fire one daily pass

```bash
docker compose run --rm daily
```

That starts Postgres if needed, then runs `python daily_once_runner.py` once. A second call the same calendar day exits immediately after a successful run (`--status` reports `already_succeeded`). Retry failed jobs by running it again; use `--force` to ignore today's success marker:

```bash
docker compose run --rm daily daily_once_runner.py --status
docker compose run --rm daily daily_once_runner.py --force
```

Enabled jobs are still those in `scheduler/jobs.json` (`nimblr-minervini-cpr` and the one-time validate job stay disabled). This does **not** download a live Nifty 500 scan by itself in tests; a real pass needs network access to Yahoo/NSE and can take a long time.

### Schedule it (optional)

Compose does not install a container cron daemon. Keep the schedule on the host (or any orchestrator) and let the Python runner enforce once-per-day:

```cron
# 08:00 Asia/Kolkata — same clock the Windows task used
CRON_TZ=Asia/Kolkata
0 8 * * * cd /path/to/trading && docker compose run --rm daily
```

On Windows, Task Scheduler can call `docker compose run --rm daily` instead of `Run-TradingDaily.ps1`.

## Secrets

Passwords belong in `.env` (see `.env.example`). They are interpolated into `TRADING_DATABASE_URL` at Compose time and are not copied into the image. `.dockerignore` excludes `.env`.
