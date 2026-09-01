# Agent Instructions — `webapp/`

Guidance for AI agents editing the Trading Scans Flask dashboard.

## Before editing

1. Read the route, template, and static file you will change, plus any related code in `scanner_history/queries.py`.
2. Check `test_webapp.py` for existing coverage of the behavior.
3. Identify the smallest change that satisfies the request. Do not refactor unrelated routes or restyle the whole app.

## Scope

The webapp is **read-only** over scanner history. It does not run scanners, write bars, or schedule jobs.

| In scope | Out of scope (unless explicitly requested) |
|----------|---------------------------------------------|
| Routes, templates, static JS/CSS | Changing `daily_once_runner.py` or scanner scripts |
| Display metadata in `config.SCANNER_DISPLAY` | Modifying `nifty_pinball_yahoo.py` / `bearish_fib_pinball.py` |
| Cache TTLs and helpers | Database migrations |
| Wiring to existing query functions | Live Yahoo/`yfinance` calls from the dashboard |

## File map (where to edit)

| Task | Primary files |
|------|----------------|
| New page or URL | `routes/*.py`, `templates/`, register blueprint in `__init__.py` |
| Scanner table columns/labels | `config.py` (`SCANNER_DISPLAY`) |
| Badge/status styling | `helpers.py` (`change_badge`, `status_css`), `static/app.css` |
| Stock search/filter | `routes/stocks.py`, `templates/stocks/`, `static/search.js`, `static/stock_filter.js` |
| Pinball chart UI | `templates/stocks/detail.html`, `static/pinball_chart.js`, `routes/api.py` |
| Performance/caching | `perf.py`, cache keys in routes |
| DB access pattern | `helpers.py` (`get_db`, `LazyConnection`) |

## Conventions

- **Blueprints**: `main`, `scanners`, `stocks`, `api` — keep URL prefixes consistent.
- **Queries**: always go through `scanner_history.queries`; never embed raw SQL in routes.
- **Caching**: wrap DB reads in `cached(key, ttl, fn)` with the right TTL (`SHORT_TTL`, `LIVE_TTL`, `HISTORICAL_TTL`).
- **Templates**: extend `base.html`; put page-specific JS in `{% block scripts %}`.
- **Static cache bust**: bump `?v=` on changed CSS/JS in templates when users report stale UI.
- **Ticker normalization**: use `normalize_ticker()` from helpers.
- **Secrets**: never commit database passwords; use `TRADING_DATABASE_URL` from the environment.

## UI patterns already in use

- **Filter bar** (Scanner day + Stock View): `#stock-filter-input`, `data-*` attributes on rows, count label, optional clear button.
- **Header search**: `#stock-q` + `#stock-suggestions` in `base.html`, logic in `search.js`.
- **Pinball modal**: `#pinball-modal`, `#open-pinball-chart`, fetch from `/api/stocks/<symbol>/pinball-chart`.
- **Change badges**: ADDED, RE-ADDED, CONTINUED, DROPPED with CSS classes `badge-*`.

## Completion checklist

- [ ] Run `python3 -m unittest test_webapp -v` (or the focused test class if the change is narrow).
- [ ] For UI changes: start `python -m webapp`, verify in browser (filter, navigation, pinball if touched).
- [ ] Bump static `?v=` when CSS/JS changed.
- [ ] No debug prints, no credentials, no unrelated file changes.
- [ ] Commit with a descriptive message; push branch `cursor/<name>-6a74`.

## Common pitfalls

- **Empty Stock View**: universe comes from `stocks` table, not scanner picks. `list_stocks()` does not join `picked = 1`.
- **Pinball empty on Windows**: webapp must see the same Postgres URL as the daily runner (`TRADING_DATABASE_URL`).
- **PostgreSQL errors after exception**: `close_db()` calls `reset_thread_db()` on request failure — do not share one connection across threads.
- **Historical pages going stale**: past scanner days use `HISTORICAL_TTL`; do not shorten without reason.
- **Duplicate `#stock-q`**: only the header form in `base.html` should use that id; Stock View filter uses `#stock-filter-input`.
