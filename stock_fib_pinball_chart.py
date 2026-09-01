"""Single-stock bullish Fibonacci pinball chart from the Yahoo bar cache.

This is a one-ticker copy of ``nifty_pinball_yahoo.py`` for the dashboard
Stock View. The original Nifty 500 scanner is unchanged.

Bars come only from ``yahoo_ohlcv_daily`` (today's prefetch). This program
never calls yfinance or the Yahoo chart API.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

import pandas as pd

import fib_pinball_common as common
import nifty_pinball_yahoo as bullish
from yahoo_bar_store import (
    DEFAULT_DB,
    cache_database_url,
    display_database_url,
    display_symbol,
    load_cached_history,
)

LEVEL_FIELDS = (
    ("0.382 Ext", "0.382"),
    ("0.618 Ext", "0.618"),
    ("0.764 Ext", "0.764"),
    ("1.000 Ext", "1.000"),
    ("1.236 Ext", "1.236"),
    ("1.382 Ext", "1.382"),
    ("1.618 Ext", "1.618"),
    ("1.764 Ext", "1.764"),
    ("2.000 Ext", "2.000"),
)


def cache_only_history(
    symbol: str,
    config: common.PinballConfig,
    *,
    connection: Any | None = None,
    database_url: str | None = None,
) -> pd.DataFrame:
    """Load daily bars from the shared cache. Never hits Yahoo."""
    opened = False
    conn = connection
    if conn is None:
        from scanner_history.db import connect

        conn = connect(cache_database_url(database_url) or DEFAULT_DB)
        opened = True
    try:
        frame = load_cached_history(
            conn, symbol, lookback_days=config.lookback_days
        )
        if frame is None or frame.empty:
            frame = load_cached_history(
                conn,
                symbol,
                lookback_days=config.lookback_days,
                ignore_cutoff=True,
            )
        if frame is None or frame.empty:
            return pd.DataFrame(columns=list(common.OHLCV_COLUMNS))
        return common.normalize_ohlcv(frame)
    finally:
        if opened:
            conn.close()


def _json_safe(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _markers(wave: dict[str, Any]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for label, price_key, date_key in (
        ("W0", "W0 Low", "W0 Date"),
        ("W1", "W1 High", "W1 Date"),
        ("W2", "W2 Low", "W2 Date"),
    ):
        price = wave.get(price_key)
        date = wave.get(date_key)
        if price in ("", None) or not date:
            continue
        markers.append({"label": label, "date": str(date), "price": _json_safe(price)})
    return markers


def _levels(wave: dict[str, Any]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    for field, label in LEVEL_FIELDS:
        price = wave.get(field)
        if price in ("", None):
            continue
        levels.append({"label": label, "price": _json_safe(price)})
    return levels


def build_pinball_chart(
    symbol: str,
    *,
    connection: Any | None = None,
    database_url: str | None = None,
    config: common.PinballConfig | None = None,
) -> dict[str, Any]:
    """Analyze one ticker from cache and return a chart payload."""
    ticker = display_symbol(symbol)
    cfg = config or common.PinballConfig(
        early_wave1_min_move=0.05,
        early_wave1_max_move=1.0,
    )
    resolved_db = cache_database_url(database_url) or DEFAULT_DB
    db_label = display_database_url(resolved_db)
    history = cache_only_history(
        ticker, cfg, connection=connection, database_url=database_url
    )
    if history is None or history.empty:
        sqlite_hint = ""
        if "sqlite" in str(resolved_db).lower() or str(resolved_db).endswith(".sqlite3"):
            sqlite_hint = (
                " This dashboard is using local SQLite, not Postgres. "
                "Set TRADING_DATABASE_URL and restart python -m webapp."
            )
        return {
            "symbol": ticker,
            "source": "yahoo_ohlcv_daily",
            "database": db_label,
            "bars": [],
            "wave": None,
            "levels": [],
            "markers": [],
            "error": (
                f"No cached Yahoo bars for {ticker} in {db_label}."
                f"{sqlite_hint}"
            ),
        }

    rows = common.ohlcv_to_rows(history)
    window = common.analysis_window(rows, cfg)
    wave = bullish.analyze_bullish(ticker, rows, cfg)
    payload_wave = None
    if wave:
        payload_wave = {key: _json_safe(wave.get(key)) for key in bullish.BULLISH_COLUMNS}
        payload_wave["Ticker"] = ticker
    return {
        "symbol": ticker,
        "source": "yahoo_ohlcv_daily",
        "database": db_label,
        "bars": window,
        "wave": payload_wave,
        "levels": _levels(wave) if wave else [],
        "markers": _markers(wave) if wave else [],
        "error": None if wave else (
            f"Cached bars loaded for {ticker}, but no matching bullish "
            "Fibonacci pinball wave."
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a bullish Fibonacci pinball chart for one stock from the "
            "Yahoo bar cache (copy of nifty_pinball_yahoo.py)."
        )
    )
    parser.add_argument("--ticker", required=True, help="Single NSE ticker, for example TCS.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the chart payload as JSON instead of a short summary.",
    )
    parser.add_argument(
        "--database",
        default=None,
        help="SQLite path or PostgreSQL URL (default: TRADING_DATABASE_URL).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    payload = build_pinball_chart(args.ticker, database_url=args.database)
    if args.json:
        print(json.dumps(payload, indent=2))
    elif payload["wave"]:
        wave = payload["wave"]
        print(
            f"{payload['symbol']}: {wave['Wave Position']} "
            f"(confidence {wave['Confidence']}) from {payload['source']}, "
            f"bars={len(payload['bars'])}."
        )
        print(wave["Description"])
    else:
        print(payload["error"])
        return 1 if not payload["bars"] else 0
    return 0 if payload["bars"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
