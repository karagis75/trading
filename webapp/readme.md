# Trading Scans Webapp

Flask dashboard for daily scanner membership history. It reads from `scanner_history` (SQLite or PostgreSQL) and presents three main views: **Home**, **Scanner View**, and **Stock View**.

## Quick start

```bash
# From repo root
export TRADING_DATABASE_URL=postgresql://user:pass@localhost:5432/trading_history   # optional; defaults to scanner_history/scanner_history.sqlite3
python -m webapp
```

Open `http://127.0.0.1:8000/`.

On Windows (production path example):

```powershell
cd D:\trd\dailyschedule\trading29
echo $env:TRADING_DATABASE_URL   # must point at Postgres for Yahoo cache + pinball
python -m webapp
```

Hard-refresh the browser (Ctrl+F5) after pulling UI changes. Static assets use `?v=` cache-bust query strings in templates.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRADING_DATABASE_URL` | `scanner_history/scanner_history.sqlite3` | Membership history + Yahoo OHLCV cache |
| `TRADING_WEB_HOST` | `127.0.0.1` | Bind address |
| `TRADING_WEB_PORT` | `8000` | Listen port |
| `TRADING_WEB_DEBUG` | off | Flask debug mode |

Startup prints the resolved database URL (passwords redacted) and a pinball-chart hint.

## Layout

```
webapp/
├── __init__.py          # create_app(), cache headers, threaded dev server
├── __main__.py          # python -m webapp entry point
├── config.py            # AppConfig, jobs.json loader, SCANNER_DISPLAY metadata
├── helpers.py           # DB access, table builders, badges, health enrichment
├── perf.py              # TTL cache + thread-local DB connections
├── routes/
│   ├── main.py          # Home + run health strip
│   ├── scanners.py      # Scanner index + day tables
│   ├── stocks.py        # Stock universe list + ticker detail
│   └── api.py           # JSON: stock search + pinball chart
├── templates/
│   ├── base.html        # Header nav, global Search stocks typeahead
│   ├── index.html       # Home
│   ├── scanners/        # Scanner index + day view
│   └── stocks/          # Stock filter list + detail + pinball modal
└── static/
    ├── app.css          # Dark theme, filter bars, tables, modal
    ├── search.js        # Header typeahead → /stocks/<symbol>
    ├── stock_filter.js  # Stock View live filter (client-side)
    └── pinball_chart.js # Canvas chart from /api/stocks/<sym>/pinball-chart
```

## Routes

| Path | Handler | Description |
|------|---------|-------------|
| `GET /` | `main.index` | Home: latest scan date, health strip, links to views |
| `GET /scanners/` | `scanners.scanner_index` | All scanners with last run status |
| `GET /scanners/<id>` | `scanners.scanner_latest` | Redirect to latest scan day |
| `GET /scanners/<id>/<date>` | `scanners.scanner_day` | Day table with ADDED/CONTINUED/DROPPED badges |
| `GET /stocks/` | `stocks.stock_search` | Full Nifty 500 list + Filter stocks bar |
| `GET /stocks/<symbol>` | `stocks.stock_detail` | Scanner summary, membership matrix, pinball button |
| `GET /api/stocks/search?q=` | `api.api_stock_search` | Header typeahead JSON (universe, not scanner hits only) |
| `GET /api/stocks/<symbol>/pinball-chart` | `api.api_stock_pinball_chart` | Cache-only pinball payload (always 200 JSON) |

## Data flow

1. **Daily runner** (`daily_once_runner.py`) ingests scanner Excel/CSV outputs into `scanner_history`.
2. **Queries** (`scanner_history/queries.py`) read canonical runs, day rows, stock summaries, and the `stocks` universe table.
3. **Routes** call `queries.*` through `webapp.perf.cached()` with TTLs tuned for immutable history vs live summary data.
4. **Pinball chart** delegates to `stock_fib_pinball_chart.build_pinball_chart()` (repo root), which reads `yahoo_ohlcv_daily` only — never live Yahoo.

### Stock View search (two mechanisms)

- **Filter stocks** (`/stocks/`): server loads up to 600 universe rows once; `stock_filter.js` filters client-side by ticker, company, or industry; `?q=` synced via `history.replaceState`.
- **Search stocks** (header on every page): `search.js` calls `/api/stocks/search` and navigates to `/stocks/<symbol>`. Works for tickers never picked by a scanner (e.g. INFY).

### Scanner day tables

- Column order comes from `config.SCANNER_DISPLAY` with extras from row `metadata_json`.
- `helpers.build_table_columns()` + `row_cells()` map DB rows to display cells.
- Combined-option rows with `Validation Pass = False` get a faded row style (`validation_failed`).

## Caching and performance

`perf.py` defines:

| TTL | Seconds | Used for |
|-----|---------|----------|
| `SHORT_TTL` | 60 | Latest scan date, day statuses, current-day rows |
| `LIVE_TTL` | 300 | Scanner index, stock universe list |
| `HISTORICAL_TTL` | 86400 | Past scan days, stock info |

- DB connections are **thread-local** (`LazyConnection` opens on first query).
- Dev server runs `threaded=True` so static assets load concurrently.
- `create_app()` sets HTTP cache headers: historical scanner days cache 1 h; live pages `no-cache`.

## Adding a new scanner to the UI

1. Add the job to `scheduler/jobs.json` (with tracking enabled).
2. Add display metadata to `config.SCANNER_DISPLAY` (title + preferred columns).
3. Run the daily job so history is ingested.
4. No route changes needed — Scanner View picks up new scanners from the DB + jobs list.

## Testing

```bash
python3 -m unittest test_webapp -v
```

Key cases: routes, stock universe search, pinball API (cache-only, no `yfinance`), validation highlighting, thread-local DB safety.

For manual UI checks: start `python -m webapp`, open Stock View, filter by ticker/industry, use header search, open pinball on a ticker with cached bars.

## Related repo modules (outside `webapp/`)

| Module | Role |
|--------|------|
| `scanner_history/` | Schema, ingest, query layer |
| `scheduler/jobs.json` | Job order, roles, enabled flags |
| `stock_fib_pinball_chart.py` | One-stock pinball from `yahoo_ohlcv_daily` |
| `yahoo_bar_store.py` | OHLCV cache read/write |
| `test_webapp.py` | Integration tests for routes + queries |

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Stock View shows “No stocks in the universe yet” | `stocks` table empty — run daily job or `MembershipTracker.refresh_universe()` |
| Pinball says “No cached Yahoo bars” | `TRADING_DATABASE_URL` unset/wrong DB, or prefetch not run for that symbol |
| Stale UI after pull | Browser cache — hard refresh; check `?v=` on CSS/JS in `base.html` |
| Home health missing a new scanner | Job in `jobs.json` but not yet ingested — `enrich_health()` still shows it with “no run” |
