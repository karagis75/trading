# Claude Instructions — `webapp/`

Implementation guidance for the Trading Scans Flask dashboard (`webapp/`).

## Purpose

Render daily scanner membership history from `scanner_history` as HTML pages and a small JSON API. Users browse scanners by day, filter the Nifty 500 universe, open per-ticker history, and view a cache-only Fibonacci pinball chart.

## Architecture

```
python -m webapp
    └── create_app()                    # webapp/__init__.py
            ├── AppConfig.from_env()    # DB URL, jobs.json, host/port
            ├── Blueprints: main, scanners, stocks, api
            ├── teardown: close_db()
            └── after_request: cache-control headers

Request
    └── get_db() → LazyConnection → thread_db(database_url)
    └── cached(key, ttl, fn) → scanner_history.queries.*
    └── render_template(...) or jsonify(...)
```

## Key modules

### `config.py`

- `AppConfig`: `database_url`, `jobs_path`, `jobs` list from `scheduler/jobs.json`.
- `SCANNER_DISPLAY`: human titles and preferred Excel column order per `scanner_id`.
- `display_name_for()`, `preferred_columns()` — use these instead of hard-coding labels in templates.

### `helpers.py`

- `get_db()` / `close_db()` — request-scoped lazy DB handle.
- `build_table_columns()`, `row_cells()` — scanner day table rendering from `metadata_json`.
- `change_badge()`, `status_css()` — membership and run status styling.
- `enrich_health()`, `enrich_scanner_index()` — merge `jobs.json` order with DB rows.
- `validation_failed()` — Combined Option Spread “Validation Pass” row highlighting.

### `perf.py`

- TTL cache (`cached`, `invalidate`) and thread-local connections.
- `LazyConnection` defers opening DB until first query (important with `threaded=True`).
- `reset_thread_db()` after request exceptions on PostgreSQL.

### Routes

| Module | Responsibility |
|--------|----------------|
| `routes/main.py` | Home, latest date, health strip |
| `routes/scanners.py` | Index, latest redirect, day view with `?stock=` filter |
| `routes/stocks.py` | Universe list (`list_stocks`), ticker detail + matrix |
| `routes/api.py` | `search_stocks` typeahead, `build_pinball_chart` JSON |

## Query layer (do not duplicate)

Use `scanner_history.queries`:

- `latest_scan_date`, `day_statuses`, `scanner_index`, `scanner_dates`
- `scanner_day_run`, `scanner_day_rows`
- `list_stocks`, `search_stocks`, `stock_info`, `stock_summary`, `stock_change_matrix`
- `stock_in_any_scanner`

Stock search searches the **universe** (`stocks` table), not only scanner picks.

## Frontend

| Asset | Role |
|-------|------|
| `templates/base.html` | Nav, header `Search stocks` form, loads `search.js` |
| `static/search.js` | Debounced `/api/stocks/search` typeahead → `/stocks/<sym>` |
| `static/stock_filter.js` | Client-side filter on `/stocks/` (`data-stock-row`) |
| `static/pinball_chart.js` | Modal canvas; fetches pinball API once per symbol |
| `static/app.css` | Dark theme; `.filter-bar`, `.header-search`, `.modal`, tables |

When editing JS/CSS, bump the `?v=` query string in the template that loads the file.

## Pinball chart contract

- API: `GET /api/stocks/<symbol>/pinball-chart` — always HTTP 200, JSON body.
- Backend: `stock_fib_pinball_chart.build_pinball_chart()` reads `yahoo_ohlcv_daily` only.
- Regime: weekly 20 EMA vs close → bullish or bearish analyzer; overlays EMA9/EMA18 stops.
- UI: button on `stocks/detail.html` even when `found=False` (universe ticker with no scanner hits).

## Implementation rules

1. **Read-only dashboard** — no writes to history or Yahoo cache from webapp code.
2. **Preserve URL shapes** — bookmarks and scheduler links depend on `/scanners/<id>/<date>` and `/stocks/<symbol>`.
3. **Cache keys must include** scanner id, date, or ticker where data varies.
4. **Historical immutability** — once a scan date is in the past, use `HISTORICAL_TTL` for its rows.
5. **Minimal diffs** — match existing Jinja patterns, badge helpers, and filter-bar markup.
6. **Tests** — extend `test_webapp.py` when changing route behavior or search semantics.

## Verification

```bash
python3 -m unittest test_webapp -v
python3 -m webapp   # manual: /, /scanners/, /stocks/, header search, pinball
```

For Postgres production parity, set `TRADING_DATABASE_URL` before starting the server.

## When adding features

| Feature type | Typical touch points |
|--------------|---------------------|
| New scanner column | `config.SCANNER_DISPLAY`, possibly `row_cells()` mapping |
| New stock field in list | `queries.list_stocks` SELECT, template `data-*` attrs, `stock_filter.js` haystack |
| New API endpoint | `routes/api.py`, tests in `test_webapp.py` |
| New page | blueprint route + template extending `base.html` |

Do not add Flask extensions or new dependencies unless the task explicitly requires them.
