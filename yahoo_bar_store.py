"""Shared daily Yahoo OHLCV cache for the scheduled Nifty 500 scanners.

The first job in ``scheduler/jobs.json`` (``prefetch-yahoo-ohlcv``) downloads
two years of daily bars once and writes them to the same SQLite or PostgreSQL
database used for scanner membership history (``TRADING_DATABASE_URL``).

Later jobs call ``get_daily_history`` and read those bars. After today's
prefetch has at least one successful symbol, later jobs stay on the cache and
do not call Yahoo / yfinance again — including short listings that are not
long enough for a scanner's EMA window.

Each scanner process reuses one database connection. Opening a new Postgres
connection and re-running schema init on every ticker was taking longer than
the original Yahoo downloads.

Cache writes happen only when ``TRADING_DATABASE_URL`` or
``TRADING_YAHOO_CACHE_DB`` is set, or when an explicit connection/database
URL is passed. That keeps unit tests from polluting the local history file.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from scanner_history.db import connect

LOGGER = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = os.environ.get(
    "TRADING_DATABASE_URL",
    str(REPO_ROOT / "scanner_history" / "scanner_history.sqlite3"),
)
OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
UPSERT_BAR_SQL = """
INSERT INTO yahoo_ohlcv_daily (
    symbol, yahoo_symbol, bar_date, open, high, low, close, volume, source, fetched_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, bar_date) DO UPDATE SET
    yahoo_symbol = excluded.yahoo_symbol,
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    source = excluded.source,
    fetched_at = excluded.fetched_at
"""
UPSERT_PREFETCH_SQL = """
INSERT INTO yahoo_ohlcv_prefetch (
    fetch_date, symbol, yahoo_symbol, bar_count, first_bar, last_bar, status, error_message, fetched_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(fetch_date, symbol) DO UPDATE SET
    yahoo_symbol = excluded.yahoo_symbol,
    bar_count = excluded.bar_count,
    first_bar = excluded.first_bar,
    last_bar = excluded.last_bar,
    status = excluded.status,
    error_message = excluded.error_message,
    fetched_at = excluded.fetched_at
"""

LiveLoader = Callable[[str, str], pd.DataFrame]

_LOCK = threading.Lock()
_SHARED_CONN: Any | None = None
_SHARED_URL: str | None = None
_PREFETCH_READY: dict[tuple[int, str], bool] = {}
_STATS = {"hits": 0, "empty": 0, "live": 0, "connects": 0}
_LAST_SOURCE = "empty"
_ATEXIT_REGISTERED = False


def display_database_url(url: str | Path | None) -> str:
    """Return a log-safe database URL with the password stripped."""
    if url is None:
        return ""
    text = str(url)
    parts = urlsplit(text)
    if not parts.password:
        return text
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def last_history_source() -> str:
    """How the most recent ``get_daily_history`` call was satisfied."""
    return _LAST_SOURCE


def cache_stats() -> dict[str, int]:
    return dict(_STATS)


def reset_shared_connection() -> None:
    """Close the process-wide cache connection. Used by tests."""
    global _SHARED_CONN, _SHARED_URL, _LAST_SOURCE
    with _LOCK:
        if _SHARED_CONN is not None:
            try:
                _SHARED_CONN.close()
            except Exception:
                pass
        _SHARED_CONN = None
        _SHARED_URL = None
        _PREFETCH_READY.clear()
        _STATS.update(hits=0, empty=0, live=0, connects=0)
        _LAST_SOURCE = "empty"


def _register_atexit() -> None:
    global _ATEXIT_REGISTERED
    if _ATEXIT_REGISTERED:
        return
    atexit.register(_log_cache_stats)
    _ATEXIT_REGISTERED = True


def _log_cache_stats() -> None:
    message = (
        "Yahoo cache replay: "
        f"hits={_STATS['hits']} empty={_STATS['empty']} "
        f"live={_STATS['live']} db-connects={_STATS['connects']}"
    )
    LOGGER.info(message)
    print(message)


def _note_source(source: str) -> None:
    global _LAST_SOURCE
    _LAST_SOURCE = source
    if source == "cache":
        _STATS["hits"] += 1
    elif source == "live":
        _STATS["live"] += 1
    else:
        _STATS["empty"] += 1


def _shared_connection(url: str) -> Any:
    global _SHARED_CONN, _SHARED_URL
    with _LOCK:
        if _SHARED_CONN is not None and _SHARED_URL == url:
            return _SHARED_CONN
        if _SHARED_CONN is not None:
            try:
                _SHARED_CONN.close()
            except Exception:
                pass
            _SHARED_CONN = None
        _SHARED_CONN = connect(url)
        _SHARED_URL = url
        _STATS["connects"] += 1
        _register_atexit()
        return _SHARED_CONN


def display_symbol(symbol: str) -> str:
    cleaned = str(symbol or "").strip().upper()
    if cleaned.endswith(".NS"):
        return cleaned[:-3]
    return cleaned


def yahoo_symbol(symbol: str) -> str:
    cleaned = str(symbol or "").strip().upper()
    if not cleaned:
        return cleaned
    if "." in cleaned or cleaned.startswith("^"):
        return cleaned
    return f"{cleaned}.NS"


def cache_database_url(explicit: str | Path | None = None) -> str | None:
    """Database used for cache reads.

    Explicit arguments and the dedicated cache env var win. Otherwise the
    membership-history URL (or its SQLite default) is used so scheduled jobs
    share one store.
    """
    if explicit:
        return str(explicit)
    dedicated = os.environ.get("TRADING_YAHOO_CACHE_DB")
    if dedicated:
        return dedicated
    return DEFAULT_DB


def cache_writes_enabled(explicit: str | Path | None = None) -> bool:
    if explicit:
        return True
    if os.environ.get("TRADING_YAHOO_CACHE_DB"):
        return True
    if os.environ.get("TRADING_DATABASE_URL"):
        return True
    return os.environ.get("TRADING_YAHOO_CACHE", "").lower() in {"1", "true", "yes"}


def _now_ist() -> pd.Timestamp:
    stamp = pd.Timestamp.now(tz="Asia/Kolkata")
    return stamp.tz_localize(None).normalize()


def lookback_cutoff(
    period: str | None = None,
    lookback_days: int | None = None,
    *,
    now: pd.Timestamp | None = None,
) -> pd.Timestamp:
    from nimblr_minervini_cpr_scanner import lookback_seconds

    today = now or _now_ist()
    if lookback_days is not None:
        return today - pd.Timedelta(days=int(lookback_days))
    return today - pd.Timedelta(seconds=lookback_seconds(period or "2y"))


def cache_covers(
    frame: pd.DataFrame,
    *,
    period: str | None = None,
    lookback_days: int | None = None,
    now: pd.Timestamp | None = None,
    prefetch_ok: bool = False,
) -> bool:
    """Return whether cached bars are fresh enough for the requested window."""
    if frame is None or frame.empty:
        return False
    today = now or _now_ist()
    last = pd.Timestamp(frame.index.max()).normalize()
    if (today - last).days > 4:
        return False
    if prefetch_ok:
        return True
    first = pd.Timestamp(frame.index.min()).normalize()
    needed = today - lookback_cutoff(period, lookback_days, now=today)
    return (last - first) >= needed * 0.8


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row[key]
    return row[key]


def _row_count(row: Any) -> int:
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(row.get("n") or row.get("count") or 0)
    try:
        return int(_row_get(row, "n"))
    except Exception:
        return int(row[0])


def prefetch_succeeded(connection: Any, symbol: str, fetch_date: date | str) -> bool:
    day = fetch_date if isinstance(fetch_date, str) else fetch_date.isoformat()
    row = connection.execute(
        "SELECT status FROM yahoo_ohlcv_prefetch WHERE fetch_date = ? AND symbol = ?",
        (day, display_symbol(symbol)),
    ).fetchone()
    return bool(row) and str(_row_get(row, "status")) == "success"


def prefetch_day_ready(connection: Any, fetch_date: date | str) -> bool:
    """True when today's prefetch job stored at least one successful symbol."""
    day = fetch_date if isinstance(fetch_date, str) else fetch_date.isoformat()
    key = (id(connection), day)
    cached = _PREFETCH_READY.get(key)
    if cached is not None:
        return cached
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM yahoo_ohlcv_prefetch WHERE fetch_date = ? AND status = ?",
        (day, "success"),
    ).fetchone()
    ready = _row_count(row) > 0
    _PREFETCH_READY[key] = ready
    return ready


def load_cached_history(
    connection: Any,
    symbol: str,
    *,
    period: str | None = None,
    lookback_days: int | None = None,
    now: pd.Timestamp | None = None,
    ignore_cutoff: bool = False,
) -> pd.DataFrame:
    cleaned = display_symbol(symbol)
    if ignore_cutoff:
        rows = connection.execute(
            """
            SELECT bar_date, open, high, low, close, volume
            FROM yahoo_ohlcv_daily
            WHERE symbol = ?
            ORDER BY bar_date
            """,
            (cleaned,),
        ).fetchall()
    else:
        cutoff = lookback_cutoff(period, lookback_days, now=now)
        rows = connection.execute(
            """
            SELECT bar_date, open, high, low, close, volume
            FROM yahoo_ohlcv_daily
            WHERE symbol = ? AND bar_date >= ?
            ORDER BY bar_date
            """,
            (cleaned, cutoff.date().isoformat()),
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS))
    frame = pd.DataFrame(
        [
            {
                "Open": float(_row_get(row, "open")),
                "High": float(_row_get(row, "high")),
                "Low": float(_row_get(row, "low")),
                "Close": float(_row_get(row, "close")),
                "Volume": float(_row_get(row, "volume") or 0.0),
            }
            for row in rows
        ],
        index=pd.to_datetime([_row_get(row, "bar_date") for row in rows]),
    )
    frame.index = pd.DatetimeIndex(frame.index).normalize()
    return frame.sort_index()


def upsert_bars(
    connection: Any,
    symbol: str,
    frame: pd.DataFrame,
    *,
    source: str = "yahoo-chart",
    fetched_at: str | None = None,
) -> int:
    if frame is None or frame.empty:
        return 0
    cleaned = display_symbol(symbol)
    yahoo = yahoo_symbol(symbol)
    fetched = fetched_at or datetime.now().isoformat(timespec="seconds")
    payload: list[tuple[Any, ...]] = []
    for stamp, row in frame.iterrows():
        bar_date = pd.Timestamp(stamp).date().isoformat()
        payload.append(
            (
                cleaned,
                yahoo,
                bar_date,
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row["Volume"]) if pd.notna(row.get("Volume")) else 0.0,
                source,
                fetched,
            )
        )
    connection.executemany(UPSERT_BAR_SQL, payload)
    connection.commit()
    return len(payload)


def record_prefetch(
    connection: Any,
    symbol: str,
    frame: pd.DataFrame,
    *,
    fetch_date: date | str,
    status: str,
    error_message: str | None = None,
) -> None:
    day = fetch_date if isinstance(fetch_date, str) else fetch_date.isoformat()
    cleaned = display_symbol(symbol)
    first = last = None
    count = 0
    if frame is not None and not frame.empty:
        count = len(frame)
        first = pd.Timestamp(frame.index.min()).date().isoformat()
        last = pd.Timestamp(frame.index.max()).date().isoformat()
    connection.execute(
        UPSERT_PREFETCH_SQL,
        (
            day,
            cleaned,
            yahoo_symbol(symbol),
            count,
            first,
            last,
            status,
            error_message,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    connection.commit()


def default_live_loader(symbol: str, period: str) -> pd.DataFrame:
    from nimblr_minervini_cpr_scanner import history_from_chart

    return history_from_chart(symbol, period)


def _cached_bars(
    conn: Any,
    symbol: str,
    *,
    period: str | None,
    lookback_days: int | None,
    reuse_short_listing: bool,
) -> pd.DataFrame:
    cached = load_cached_history(
        conn, symbol, period=period, lookback_days=lookback_days
    )
    if cached.empty and reuse_short_listing:
        cached = load_cached_history(
            conn,
            symbol,
            period=period,
            lookback_days=lookback_days,
            ignore_cutoff=True,
        )
    return cached


def get_daily_history(
    symbol: str,
    period: str = "2y",
    *,
    lookback_days: int | None = None,
    live_loader: LiveLoader | None = None,
    database_url: str | Path | None = None,
    connection: Any | None = None,
    fetch_date: date | None = None,
    persist: bool | None = None,
) -> pd.DataFrame:
    """Return daily OHLCV from today's cache, or fetch and optionally store it.

    After ``prefetch-yahoo-ohlcv`` writes a successful row for this calendar
    day, this function never calls ``live_loader`` (yfinance / chart API).
    """
    day = fetch_date or date.today()
    loader = live_loader or default_live_loader
    conn = connection
    url = cache_database_url(database_url) if connection is None else None
    if conn is None and url:
        try:
            conn = _shared_connection(url)
        except Exception:
            LOGGER.exception(
                "Yahoo cache database unavailable (%s); falling back to live Yahoo",
                display_database_url(url),
            )
            conn = None
    cached = pd.DataFrame(columns=list(OHLCV_COLUMNS))
    if conn is not None:
        day_ready = prefetch_day_ready(conn, day)
        symbol_ready = prefetch_succeeded(conn, symbol, day)
        reuse_prefetch = day_ready or symbol_ready
        cached = _cached_bars(
            conn,
            symbol,
            period=period,
            lookback_days=lookback_days,
            reuse_short_listing=reuse_prefetch,
        )
        if reuse_prefetch:
            _note_source("cache" if not cached.empty else "empty")
            return cached
        if cache_covers(
            cached,
            period=period,
            lookback_days=lookback_days,
            prefetch_ok=symbol_ready,
        ):
            _note_source("cache")
            return cached
    live = loader(symbol, period if lookback_days is None else f"{int(lookback_days)}d")
    should_write = persist if persist is not None else (
        connection is not None or cache_writes_enabled(database_url)
    )
    if conn is not None and should_write and live is not None and not live.empty:
        upsert_bars(conn, symbol, live)
        record_prefetch(conn, symbol, live, fetch_date=day, status="success")
        _PREFETCH_READY.pop((id(conn), day.isoformat() if not isinstance(day, str) else day), None)
    if live is not None and not live.empty:
        _note_source("live")
        return live
    _note_source("empty")
    return cached


def prefetch_symbols(
    tickers: list[str],
    *,
    period: str = "2y",
    fetch_date: date | None = None,
    database_url: str | Path | None = None,
    connection: Any | None = None,
    live_loader: LiveLoader | None = None,
    request_delay: float = 0.0,
) -> dict[str, int]:
    """Download and store daily bars for every ticker. Used by the first job."""
    import time

    day = fetch_date or date.today()
    loader = live_loader or default_live_loader
    opened = False
    conn = connection
    if conn is None:
        conn = connect(cache_database_url(database_url) or DEFAULT_DB)
        opened = True
    stats = {"success": 0, "empty": 0, "error": 0, "bars": 0}
    try:
        for symbol in tickers:
            try:
                frame = loader(symbol, period)
                if frame is None or frame.empty:
                    record_prefetch(
                        conn, symbol, pd.DataFrame(), fetch_date=day, status="empty"
                    )
                    stats["empty"] += 1
                else:
                    stats["bars"] += upsert_bars(conn, symbol, frame)
                    record_prefetch(conn, symbol, frame, fetch_date=day, status="success")
                    stats["success"] += 1
            except Exception as exc:
                record_prefetch(
                    conn,
                    symbol,
                    pd.DataFrame(),
                    fetch_date=day,
                    status="error",
                    error_message=str(exc),
                )
                stats["error"] += 1
            if request_delay:
                time.sleep(request_delay)
        return stats
    finally:
        if opened:
            conn.close()
