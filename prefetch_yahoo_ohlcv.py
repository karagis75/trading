"""First daily-schedule job: download Nifty 500 Yahoo bars once and store them.

Later scanners read ``yahoo_ohlcv_daily`` from the same SQLite/PostgreSQL
database instead of calling Yahoo Finance again.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

from nimblr_minervini_cpr_scanner import extract_tickers, read_input_table
from yahoo_bar_store import DEFAULT_DB, prefetch_symbols

DEFAULT_INPUT = "ind_nifty500list.csv"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prefetch two years of Yahoo daily OHLCV for the Nifty 500 universe "
            "and store it for the rest of today's scheduled scanners."
        )
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Ticker universe CSV/Excel path.")
    parser.add_argument(
        "--engine",
        default=None,
        help="Input file engine: openpyxl, xlrd, pyxlsb, odf, csv, or html.",
    )
    parser.add_argument("--lookback", default="2y", help="Yahoo lookback period (default: 2y).")
    parser.add_argument(
        "--database",
        default=os.environ.get("TRADING_YAHOO_CACHE_DB")
        or os.environ.get("TRADING_DATABASE_URL")
        or DEFAULT_DB,
        help="SQLite path or PostgreSQL URL (default: TRADING_DATABASE_URL or local sqlite).",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.1,
        help="Seconds to wait between Yahoo chart requests (default: 0.1).",
    )
    parser.add_argument("--limit", type=int, default=0, help="Prefetch only the first N tickers.")
    parser.add_argument(
        "--fetch-date",
        default=None,
        help="Calendar date to tag the prefetch with (default: today).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    try:
        tickers = extract_tickers(read_input_table(args.input, engine=args.engine))
    except Exception as exc:
        print(f"Input error: {exc}")
        return 1
    if args.limit and args.limit > 0:
        tickers = tickers[: args.limit]
    fetch_date = date.fromisoformat(args.fetch_date) if args.fetch_date else date.today()
    print(
        f"Prefetching Yahoo daily bars for {len(tickers)} ticker(s) "
        f"(lookback={args.lookback}) into {args.database}..."
    )
    stats = prefetch_symbols(
        tickers,
        period=args.lookback,
        fetch_date=fetch_date,
        database_url=args.database,
        request_delay=args.request_delay,
    )
    print(
        f"Yahoo prefetch complete. success={stats['success']} empty={stats['empty']} "
        f"error={stats['error']} bars={stats['bars']}."
    )
    if stats["success"] == 0 and tickers:
        print("No tickers were stored; later scanners will fall back to live Yahoo calls.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
