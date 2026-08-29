#!/usr/bin/env python3
"""Seed a local SQLite history DB with synthetic multi-day scanner outputs for demos."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scanner_history.tracker import MembershipTracker, TrackingConfig

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = REPO_ROOT / "scanner_history" / "scanner_history.sqlite3"
DEFAULT_UNIVERSE = REPO_ROOT / "ind_nifty500list.csv"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_excel(path, index=False, engine="openpyxl")


def seed(db_path: Path, universe: Path, days: int = 6) -> None:
    tracker = MembershipTracker.from_path(db_path, universe)
    today = date.today()
    # Use weekdays only so the date bar looks realistic.
    scan_days: list[date] = []
    cursor = today
    while len(scan_days) < days:
        if cursor.weekday() < 5:
            scan_days.append(cursor)
        cursor -= timedelta(days=1)
    scan_days = list(reversed(scan_days))

    specs = {
        "bullish-bias-nifty500": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx",
            symbol_column="Ticker", classification_column="Status",
        ),
        "bearish-bias-nifty500": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx",
            symbol_column="Ticker", classification_column="Setup Status",
        ),
        "nifty500-xy-intersect": TrackingConfig(
            enabled=True, role="primary_scanner", format="csv",
            symbol_column="Ticker", classification_column="X/Y Intersect Rule",
        ),
        "rangebound-stocks": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx",
            symbol_column="Ticker", classification_column="Setup Status",
        ),
        "nimblr-minervini-cpr": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx",
            symbol_column="Ticker", membership_filter="Qualified=True",
            signal_date_column="Date",
        ),
        "nifty-fib-pinball-bullish": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx", sheet="All",
            symbol_column="Ticker", signal_date_column="Last Date",
            classification_column="Wave Position", confidence_column="Confidence",
        ),
        "nifty-fib-pinball-bearish": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx", sheet="All",
            symbol_column="Ticker", signal_date_column="Last Date",
            classification_column="Wave Position", confidence_column="Confidence",
        ),
        "merge-option-candidates": TrackingConfig(
            enabled=True, role="aggregator", format="csv", symbol_column="Ticker",
        ),
        "combined-option-v8": TrackingConfig(
            enabled=True, role="downstream", format="xlsx", sheet="All Opportunities",
            symbol_column="Symbol", classification_column="Strategy",
            confidence_column="Score",
        ),
    }

    sequences = {
        "bullish-bias-nifty500": [
            ["RELIANCE", "TCS"],
            ["RELIANCE", "TCS", "INFY"],
            ["RELIANCE", "INFY"],
            ["INFY", "HDFCBANK"],
            ["HDFCBANK", "SBIN"],
            ["HDFCBANK", "SBIN", "ICICIBANK"],
        ],
        "bearish-bias-nifty500": [
            ["WIPRO"],
            ["WIPRO", "ITC"],
            ["ITC"],
            ["ITC", "ONGC"],
            ["ONGC"],
            ["ONGC", "NTPC"],
        ],
        "nifty500-xy-intersect": [
            ["LT"],
            ["LT", "MARUTI"],
            ["MARUTI"],
            ["MARUTI", "TITAN"],
            ["TITAN"],
            ["TITAN", "ASIANPAINT"],
        ],
        "rangebound-stocks": [
            ["HINDUNILVR"],
            ["HINDUNILVR"],
            ["HINDUNILVR", "NESTLEIND"],
            ["NESTLEIND"],
            ["NESTLEIND", "BRITANNIA"],
            ["BRITANNIA"],
        ],
        "nimblr-minervini-cpr": [
            ["BAJFINANCE"],
            ["BAJFINANCE", "KOTAKBANK"],
            ["KOTAKBANK"],
            ["KOTAKBANK", "AXISBANK"],
            ["AXISBANK"],
            ["AXISBANK", "INDUSINDBK"],
        ],
        "nifty-fib-pinball-bullish": [
            ["ADANIENT"],
            ["ADANIENT", "ADANIPORTS"],
            ["ADANIPORTS"],
            ["ADANIPORTS", "POWERGRID"],
            ["POWERGRID"],
            ["POWERGRID", "NTPC"],
        ],
        "nifty-fib-pinball-bearish": [
            ["COALINDIA"],
            ["COALINDIA"],
            ["COALINDIA", "BPCL"],
            ["BPCL"],
            ["BPCL", "IOC"],
            ["IOC"],
        ],
    }

    outputs = REPO_ROOT / "outputs"
    for index, day in enumerate(scan_days):
        folder = outputs / day.isoformat()
        # Primary scanners
        _write(
            folder / "Bullish_Bias_Analysis.xlsx",
            [{"Ticker": t, "Status": "Bullish Bias", "Close Price": 1000 + index, "CCI": 80 + index, "ADX": 25}
             for t in sequences["bullish-bias-nifty500"][index]],
        )
        _write(
            folder / "Bearish_Momentum_Analysis.xlsx",
            [{"Ticker": t, "Setup Status": "Bearish Momentum", "Close Price": 500 + index, "CCI (14)": -90}
             for t in sequences["bearish-bias-nifty500"][index]],
        )
        _write(
            folder / "nifty500_xy_matrix_signals.csv",
            [{"Ticker": t, "Price (INR)": 2000 + index, "ATR": 40, "X/Y Intersect Rule": "Y1/X2",
              "Triggered Entry (Y)": "Y1", "Assigned Exit Matrix (X)": "X2", "Target Target Level": 2100}
             for t in sequences["nifty500-xy-intersect"][index]],
        )
        _write(
            folder / "Strangle_Candidate_Analysis.xlsx",
            [{"Ticker": t, "Setup Status": "Compression", "Close Price": 800, "ADX (14)": 12, "CCI (14)": 5,
              "EMA Braid Spread %": 0.4, "Box Range %": 3.2, "Box High": 820, "Box Low": 780}
             for t in sequences["rangebound-stocks"][index]],
        )
        _write(
            folder / "Nimblr_Minervini_CPR_Scan.xlsx",
            [{"Ticker": t, "Date": day.isoformat(), "Close": 900, "CCI": 100, "Qualified": True,
              "Sections_Passed": 3, "EMA10": 890, "EMA20": 880, "EMA50": 850, "EMA150": 800, "EMA200": 780,
              "ATR": 20, "Nimblr": True, "Minervini": True, "CPR": True}
             for t in sequences["nimblr-minervini-cpr"][index]],
        )
        bullish_fib = folder / "Bullish_Fib_Pinball.xlsx"
        bearish_fib = folder / "Bearish_Fib_Pinball.xlsx"
        with pd.ExcelWriter(bullish_fib, engine="openpyxl") as writer:
            pd.DataFrame(
                [{"Ticker": t, "Last Date": day.isoformat(), "Wave Position": "Wave 3", "Confidence": 0.8}
                 for t in sequences["nifty-fib-pinball-bullish"][index]]
            ).to_excel(writer, sheet_name="All", index=False)
        with pd.ExcelWriter(bearish_fib, engine="openpyxl") as writer:
            pd.DataFrame(
                [{"Ticker": t, "Last Date": day.isoformat(), "Wave Position": "Wave 5", "Confidence": 0.7}
                 for t in sequences["nifty-fib-pinball-bearish"][index]]
            ).to_excel(writer, sheet_name="All", index=False)

        merged = sorted(
            set(sequences["bullish-bias-nifty500"][index])
            | set(sequences["bearish-bias-nifty500"][index])
            | set(sequences["rangebound-stocks"][index])
        )
        _write(folder / "Option_Scan_Candidates.csv", [{"Ticker": t} for t in merged])
        option_path = folder / "Combined_Option_Spread_Analysis.xlsx"
        if index == 1:
            # Leave one day skipped for combined-option to exercise the banner.
            pass
        else:
            with pd.ExcelWriter(option_path, engine="openpyxl") as writer:
                pd.DataFrame(
                    [{"Symbol": t, "Strategy": "Bull Call Spread", "Expiry": "2026-09-25",
                      "PCR": 0.9, "Score": 72 + index, "R:R Ratio": 1.4}
                     for t in merged[:3]]
                ).to_excel(writer, sheet_name="All Opportunities", index=False)

        file_map = {
            "bullish-bias-nifty500": folder / "Bullish_Bias_Analysis.xlsx",
            "bearish-bias-nifty500": folder / "Bearish_Momentum_Analysis.xlsx",
            "nifty500-xy-intersect": folder / "nifty500_xy_matrix_signals.csv",
            "rangebound-stocks": folder / "Strangle_Candidate_Analysis.xlsx",
            "nimblr-minervini-cpr": folder / "Nimblr_Minervini_CPR_Scan.xlsx",
            "nifty-fib-pinball-bullish": bullish_fib,
            "nifty-fib-pinball-bearish": bearish_fib,
            "merge-option-candidates": folder / "Option_Scan_Candidates.csv",
            "combined-option-v8": option_path,
        }
        for scanner_id, tracking in specs.items():
            path = file_map[scanner_id]
            if scanner_id == "combined-option-v8" and index == 1:
                tracker.ingest_output(
                    scanner_id=scanner_id,
                    tracking=tracking,
                    scan_date=day,
                    output_path=path,
                    skipped=True,
                    job_message="No candidates in Option_Scan_Candidates.csv; skipping combined-option-v8.",
                )
            else:
                tracker.ingest_output(
                    scanner_id=scanner_id,
                    tracking=tracking,
                    scan_date=day,
                    output_path=path,
                    job_ok=True,
                )
        print(f"Seeded {day.isoformat()}")
    tracker.close()
    print(f"Wrote history database to {db_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--days", type=int, default=6)
    args = parser.parse_args()
    if args.db.exists():
        args.db.unlink()
    seed(args.db, args.universe, days=args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
