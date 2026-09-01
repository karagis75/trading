"""Single-stock Fibonacci pinball chart from the Yahoo bar cache.

This is a one-ticker copy of ``nifty_pinball_yahoo.py`` for Stock View.
The original Nifty 500 scanners are unchanged.

Bars come only from ``yahoo_ohlcv_daily``. This program never calls yfinance.

Trend filter: bullish pinball when last close is above the weekly 20 EMA,
bearish pinball when last close is below it. Daily 9 EMA and 18 EMA are
embedded on the chart as stop / exit levels for the entry.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

import pandas as pd

import bearish_fib_pinball as bearish
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
BULLISH_OVERRIDES = {"early_wave1_min_move": 0.05, "early_wave1_max_move": 1.0}
BEARISH_OVERRIDES = {"early_wave1_min_move": 0.05, "early_wave1_max_move": 0.70}


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


def _round2(value: Any) -> float | None:
    number = _json_safe(value)
    if number is None:
        return None
    try:
        return round(float(number), 2)
    except (TypeError, ValueError):
        return None


def weekly_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    """Build NSE-style weekly bars (week ending Friday) from daily cache."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(common.OHLCV_COLUMNS))
    weekly = (
        frame.resample("W-FRI")
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna(subset=["Close"])
    )
    return weekly


def overlay_emas(frame: pd.DataFrame) -> pd.DataFrame:
    """Daily 9/18 EMA plus weekly 20 EMA forward-filled onto each daily bar."""
    out = frame.copy()
    close = out["Close"].astype(float)
    out["EMA9"] = close.ewm(span=9, adjust=False).mean()
    out["EMA18"] = close.ewm(span=18, adjust=False).mean()
    weekly = weekly_ohlc(out.loc[:, list(common.OHLCV_COLUMNS)])
    if weekly.empty:
        out["EMA20W"] = pd.NA
        return out
    weekly_ema = weekly["Close"].ewm(span=20, adjust=False).mean()
    out["EMA20W"] = weekly_ema.reindex(out.index, method="ffill")
    return out


def regime_from_weekly_ema20(overlays: pd.DataFrame) -> dict[str, Any]:
    """Bullish above weekly 20 EMA, bearish below. Used only by this chart copy."""
    last = overlays.iloc[-1]
    price = _round2(last["Close"])
    weekly_ema = _round2(last["EMA20W"]) if pd.notna(last.get("EMA20W")) else None
    if weekly_ema is None:
        side = "bullish"
        relation = "weekly EMA20 not available yet"
    elif price is not None and price >= weekly_ema:
        side = "bullish"
        relation = f"price {price} above weekly EMA20 {weekly_ema}"
    else:
        side = "bearish"
        relation = f"price {price} below weekly EMA20 {weekly_ema}"
    return {
        "side": side,
        "price": price,
        "weekly_ema20": weekly_ema,
        "ema9": _round2(last["EMA9"]),
        "ema18": _round2(last["EMA18"]),
        "relation": relation,
    }


def _markers(wave: dict[str, Any]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    pairs = (
        ("W0", "W0 Low", "W0 Date"),
        ("W1", "W1 High", "W1 Date"),
        ("W2", "W2 Low", "W2 Date"),
        ("W0", "W0 High", "W0 Date"),
        ("W1", "W1 Low", "W1 Date"),
        ("W2", "W2 High", "W2 Date"),
    )
    seen: set[str] = set()
    for label, price_key, date_key in pairs:
        if label in seen:
            continue
        price = wave.get(price_key)
        date = wave.get(date_key)
        if price in ("", None) or not date:
            continue
        markers.append({"label": label, "date": str(date), "price": _json_safe(price)})
        seen.add(label)
    return markers


def _levels(wave: dict[str, Any]) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    for field, label in LEVEL_FIELDS:
        price = wave.get(field)
        if price in ("", None):
            continue
        levels.append({"label": label, "price": _json_safe(price)})
    return levels


def _window_with_overlays(
    rows: list[dict[str, Any]],
    overlays: pd.DataFrame,
    config: common.PinballConfig,
) -> list[dict[str, Any]]:
    window = common.analysis_window(rows, config)
    by_date: dict[str, pd.Series] = {}
    for stamp, row in overlays.iterrows():
        by_date[pd.Timestamp(stamp).date().isoformat()] = row
    decorated: list[dict[str, Any]] = []
    for item in window:
        payload = dict(item)
        extra = by_date.get(item["date"])
        if extra is not None:
            payload["ema9"] = _round2(extra["EMA9"])
            payload["ema18"] = _round2(extra["EMA18"])
            payload["ema20w"] = _round2(extra["EMA20W"]) if pd.notna(extra.get("EMA20W")) else None
        decorated.append(payload)
    return decorated


def _analyze(
    ticker: str,
    rows: list[dict[str, Any]],
    side: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if side == "bearish":
        config = common.PinballConfig(**{**BEARISH_OVERRIDES})
        wave = bearish.analyze_bearish(ticker, rows, config)
        columns = bearish.BEARISH_COLUMNS
    else:
        config = common.PinballConfig(**{**BULLISH_OVERRIDES})
        wave = bullish.analyze_bullish(ticker, rows, config)
        columns = bullish.BULLISH_COLUMNS
    return wave, columns


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
    empty = {
        "symbol": ticker,
        "source": "yahoo_ohlcv_daily",
        "database": db_label,
        "bars": [],
        "wave": None,
        "levels": [],
        "markers": [],
        "regime": None,
        "stops": None,
    }
    if history is None or history.empty:
        sqlite_hint = ""
        if "sqlite" in str(resolved_db).lower() or str(resolved_db).endswith(".sqlite3"):
            sqlite_hint = (
                " This dashboard is using local SQLite, not Postgres. "
                "Set TRADING_DATABASE_URL and restart python -m webapp."
            )
        empty["error"] = (
            f"No cached Yahoo bars for {ticker} in {db_label}.{sqlite_hint}"
        )
        return empty

    overlays = overlay_emas(history)
    regime = regime_from_weekly_ema20(overlays)
    rows = common.ohlcv_to_rows(history)
    window = _window_with_overlays(rows, overlays, cfg)
    wave, columns = _analyze(ticker, rows, regime["side"])
    payload_wave = None
    if wave:
        payload_wave = {key: _json_safe(wave.get(key)) for key in columns}
        payload_wave["Ticker"] = ticker
        payload_wave["Side"] = regime["side"]
    stops = {
        "ema9": regime["ema9"],
        "ema18": regime["ema18"],
        "weekly_ema20": regime["weekly_ema20"],
        "tight": "EMA9",
        "swing": "EMA18",
        "note": (
            "EMA9 is the tight stop; EMA18 is the swing stop. "
            "For a bullish entry they sit below price; for bearish they sit above."
        ),
    }
    if wave:
        error = None
    else:
        error = (
            f"Cached bars loaded for {ticker}. {regime['relation'].capitalize()}. "
            f"No matching {regime['side']} Fibonacci pinball wave. "
            "EMA9 / EMA18 are still shown as stop levels."
        )
    return {
        "symbol": ticker,
        "source": "yahoo_ohlcv_daily",
        "database": db_label,
        "bars": window,
        "wave": payload_wave,
        "levels": _levels(wave) if wave else [],
        "markers": _markers(wave) if wave else [],
        "regime": regime,
        "stops": stops,
        "error": error,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a Fibonacci pinball chart for one stock from the Yahoo bar "
            "cache. Bullish above weekly 20 EMA, bearish below it."
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
        regime = payload["regime"] or {}
        print(
            f"{payload['symbol']}: {regime.get('side')} {wave['Wave Position']} "
            f"(confidence {wave['Confidence']}) {regime.get('relation')}; "
            f"stops EMA9={payload['stops']['ema9']} EMA18={payload['stops']['ema18']}."
        )
        print(wave["Description"])
    else:
        print(payload["error"])
        return 1 if not payload["bars"] else 0
    return 0 if payload["bars"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
