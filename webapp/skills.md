# Webapp Skills

Step-by-step recipes for common `webapp/` tasks. Run commands from the **repository root**.

---

## Skill: Run the dashboard locally

```bash
export TRADING_DATABASE_URL=postgresql://user:pass@localhost:5432/trading_history   # optional
python3 -m webapp
```

Windows:

```powershell
$env:TRADING_WEB_HOST = "127.0.0.1"
$env:TRADING_WEB_PORT = "8000"
python -m webapp
```

Verify: `http://127.0.0.1:8000/` shows Home with Scanner View and Stock View cards.

---

## Skill: Run webapp tests

```bash
python3 -m unittest test_webapp -v
```

Focused examples:

```bash
python3 -m unittest test_webapp.DashboardHarness.test_routes_stocks_and_api -v
python3 -m unittest test_webapp.DashboardHarness.test_pinball_chart_api_reads_cache_not_yahoo -v
```

Tests use a temporary SQLite DB and seeded scanner outputs — no live network.

---

## Skill: Add a scanner to the dashboard UI

1. Ensure the job exists in `scheduler/jobs.json` with tracking enabled.
2. Add an entry to `webapp/config.py` → `SCANNER_DISPLAY`:

```python
"my-new-scanner": {
    "title": "My New Scanner",
    "columns": ["Ticker", "Company Name", "Status"],
},
```

3. Ingest at least one day via the daily runner.
4. Open `/scanners/my-new-scanner` — columns fall back to metadata keys if Excel has extras.

No route changes required.

---

## Skill: Change scanner table column order or labels

1. Edit `SCANNER_DISPLAY[<scanner_id>]["columns"]` in `config.py`.
2. If a column maps from `metadata_json` with a non-obvious key, check `helpers.row_cells()` for special cases (`Status`, `Confidence`, `Date`, etc.).
3. Run `test_webapp` scanner day tests if you changed mapping logic.

---

## Skill: Extend Stock View search/filter

**Server (universe list):**

- Query: `scanner_history.queries.list_stocks()` / `search_stocks()`
- Route: `routes/stocks.py` → `stock_search()`
- Template: `templates/stocks/search.html` — `data-symbol`, `data-company-name`, `data-industry` on each row

**Client filter:**

- `static/stock_filter.js` — extend the `haystack` array in `apply()`
- Bump `?v=` in `search.html` scripts block

**Header typeahead:**

- `static/search.js` — uses `/api/stocks/search?q=`
- `routes/api.py` → `api_stock_search()`
- Markup in `templates/base.html` (`#stock-q`, `#stock-suggestions`)

Test: INFY (or any universe ticker never picked) must appear in search and filter.

---

## Skill: Add or change pinball chart behavior

**API (data):**

- Repo root: `stock_fib_pinball_chart.py` (`build_pinball_chart`)
- Wire: `routes/api.py` → `api_stock_pinball_chart`
- Must read `yahoo_ohlcv_daily` only — mock `yfinance` in tests

**UI (canvas):**

- Template: `templates/stocks/detail.html` — modal + `#open-pinball-chart`
- JS: `static/pinball_chart.js` — fetch, draw, EMA overlays
- Bump `?v=pinballN` on the detail template script tag after JS changes

Manual check: open `/stocks/TCS` → **Open pinball chart**. Empty chart usually means wrong DB or missing cache rows.

---

## Skill: Add client-side filter to a scanner day table

Pattern already used in `templates/scanners/day.html`:

1. Add `data-symbol` / `data-company-name` on `<tr>` elements.
2. Reuse `.filter-bar` markup and inline or shared filter script.
3. Support `?stock=` query param (route passes `stock_filter` to template).

Do not duplicate `#stock-q` — that id is reserved for the global header search.

---

## Skill: Tune caching

Edit `webapp/perf.py` TTL constants or per-route `cached()` keys in:

- `routes/main.py` — `latest_scan_date`, `day_statuses`
- `routes/scanners.py` — historical vs current day (`HISTORICAL_TTL` vs `SHORT_TTL`)
- `routes/stocks.py` — `stock_list:all`, `stock_summary`, `stock_matrix`

After cache logic changes, call `invalidate()` in tests (`test_webapp` already does in `setUp`).

Rule of thumb: immutable past scan days → `HISTORICAL_TTL` (24 h); live summary → `SHORT_TTL` (60 s).

---

## Skill: Fix “empty universe” on Stock View

Symptom: “No stocks in the universe yet.”

1. Confirm DB URL: startup log from `python -m webapp`.
2. Check `stocks` row count:

```python
from scanner_history import db
c = db.connect("YOUR_DATABASE_URL")
print(c.execute("SELECT COUNT(*) FROM stocks").fetchone())
```

3. If zero, run the daily job (universe refresh happens during ingest) or:

```python
from datetime import date
from scanner_history.tracker import MembershipTracker
t = MembershipTracker.from_path("DB_URL", "ind_nifty500list.csv")
t.refresh_universe(date.today())
```

---

## Skill: Fix stale CSS/JS in the browser

1. Bump `?v=` on changed assets in `base.html` or page `{% block scripts %}`.
2. `create_app()` sets `no_cache` on `/static/` responses — still hard-refresh (Ctrl+F5) on Windows.
3. Confirm the edited file is the one referenced in the template path.

---

## Skill: Debug PostgreSQL connection errors

1. Webapp uses `scanner_history.db.connect()` with `autocommit=True` for Postgres.
2. On request exception, `close_db()` → `reset_thread_db()` drops the thread-local connection.
3. Reproduce with `test_webapp.ThreadDbSafetyTests`.
4. Never share one connection across threads — `threaded=True` dev server requires thread-local pool.

---

## Skill: Manual UI smoke test checklist

1. `/` — health strip, latest date
2. `/scanners/` — scanner list
3. `/scanners/bullish-bias-nifty500/<latest>` — table, date chips, filter bar
4. `/stocks/` — 500 stocks, Filter stocks, count label
5. Filter `INFY` → 1 result; clear → 500
6. Header search `TCS` → typeahead → `/stocks/TCS`
7. **Open pinball chart** on a symbol with cached bars

---

## Skill: Commit webapp changes

```bash
git checkout -b cursor/<short-description>-6a74
# edit webapp/...
python3 -m unittest test_webapp -v
git add webapp/ test_webapp.py   # only if tests changed
git commit -m "Describe the user-visible change."
git push -u origin cursor/<short-description>-6a74
```

Keep commits focused. Do not commit `Combined_Option_Spread_Analysis.xlsx`, local `.sqlite3` files, or credentials.
