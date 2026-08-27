"""Merge Ticker columns from multiple scanner outputs into one candidate list.

Used by the daily scheduler to narrow the slow NSE option-chain scan
(combinedoptionanalyzedv8.py) down to only the symbols that the faster
technical scanners (bullishbiasnifty500.py, bearisbiasnifty500.py,
rangeboundstocks.py) already flagged for the day, instead of re-scanning the
full NIFTY 500 list.

A source scanner writes nothing when it finds zero qualifying setups, so a
missing source file here means "no candidates from that scan", not an error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

TICKER_COLUMNS = ("Ticker", "ticker", "Symbol", "symbol", "SYMBOL")


def normalize_ticker(value: str) -> str:
    ticker = str(value).strip().upper()
    if ticker.endswith(".NS"):
        ticker = ticker[:-3]
    return ticker


def read_tickers(path: Path) -> list[str]:
    """Read Ticker/Symbol values from a scanner's CSV or Excel output."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path)
    else:
        frame = pd.read_excel(path)

    for column in TICKER_COLUMNS:
        if column in frame.columns:
            values = frame[column].dropna().astype(str)
            return [normalize_ticker(v) for v in values if normalize_ticker(v)]

    raise ValueError(
        f"{path} must contain a 'Ticker' or 'Symbol' column. Found: {list(frame.columns)}"
    )


def merge_candidates(sources: Sequence[Path]) -> tuple[list[str], list[Path], list[Path]]:
    """Return (unique tickers in first-seen order, files used, files skipped as missing)."""
    seen: set[str] = set()
    merged: list[str] = []
    used: list[Path] = []
    missing: list[Path] = []

    for source in sources:
        if not source.exists():
            missing.append(source)
            continue
        used.append(source)
        for ticker in read_tickers(source):
            if ticker not in seen:
                seen.add(ticker)
                merged.append(ticker)

    return merged, used, missing


def write_candidates(tickers: list[str], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"Ticker": tickers}).to_csv(output_path, index=False)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge Ticker columns from one or more scanner output files into a "
            "single deduplicated candidate CSV for the option-chain scan."
        )
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        required=True,
        help="Scanner output files (CSV or Excel) to read Ticker/Symbol values from.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="CSV path to write the merged, deduplicated Ticker list to.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    sources = [Path(source) for source in args.sources]
    output_path = Path(args.output)

    tickers, used, missing = merge_candidates(sources)
    write_candidates(tickers, output_path)

    for path in missing:
        print(f"No candidates from '{path}' (scanner found nothing or has not run yet).")
    if used:
        print(f"Merged {len(tickers)} unique ticker(s) from: {', '.join(str(p) for p in used)}")
    else:
        print("No source files were available; wrote an empty candidate list.")
    print(f"Saved candidates to '{output_path}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
