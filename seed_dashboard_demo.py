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
        "minervini-vcp": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx",
            symbol_column="Ticker", membership_filter="Qualified=True",
            signal_date_column="Date",
        ),
        "nimblr-minervini-cpr": TrackingConfig(
            enabled=True, role="primary_scanner", format="xlsx",
            symbol_column="Ticker", membership_filter="Qualified=True",
            signal_date_column="Date",
        ),
        "minervini-volume-cpr": TrackingConfig(
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
        "minervini-vcp": [
            ["EICHERMOT"],
            ["EICHERMOT", "TITAN"],
            ["TITAN"],
            ["TITAN", "BOSCHLTD"],
            ["BOSCHLTD"],
            ["BOSCHLTD", "HOMEFIRST"],
        ],
        "nimblr-minervini-cpr": [
            ["BAJFINANCE"],
            ["BAJFINANCE", "KOTAKBANK"],
            ["KOTAKBANK"],
            ["KOTAKBANK", "AXISBANK"],
            ["AXISBANK"],
            ["AXISBANK", "INDUSINDBK"],
        ],
        "minervini-volume-cpr": [
            ["DIVISLAB"],
            ["DIVISLAB", "SUNPHARMA"],
            ["SUNPHARMA"],
            ["SUNPHARMA", "CIPLA"],
            ["CIPLA"],
            ["CIPLA", "DRREDDY"],
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
            folder / "Minervini_VCP_Scan.xlsx",
            [{"Ticker": t, "Date": day.isoformat(), "Close": 1200 + index * 10, "EMA50": 1150,
              "EMA150": 1100, "EMA200": 1050, "ATR": 18, "Contractions": 3,
              "Latest_Pullback_%": 4.2, "Base_Position": 0.88, "Stage2_Trend": True, "VCP": True,
              "Sections_Passed": 2, "Qualified": True}
             for t in sequences["minervini-vcp"][index]],
        )
        _write(
            folder / "Nimblr_Minervini_CPR_Scan.xlsx",
            [{"Ticker": t, "Date": day.isoformat(), "Close": 900, "CCI": 100, "Qualified": True,
              "Sections_Passed": 3, "EMA10": 890, "EMA20": 880, "EMA50": 850, "EMA150": 800, "EMA200": 780,
              "ATR": 20, "Nimblr": True, "Minervini": True, "CPR": True}
             for t in sequences["nimblr-minervini-cpr"][index]],
        )
        _write(
            folder / "Minervini_Volume_CPR_Scan.xlsx",
            [{"Ticker": t, "Date": day.isoformat(), "Close": 1400 + index * 8, "EMA50": 1350,
              "EMA150": 1280, "EMA200": 1200, "CPR_Top": 1390, "CPR_Pivot": 1385, "CPR_Bottom": 1380,
              "CPR_Width": 10.0, "CPR_Width_%": 0.45, "Virgin_Above": True, "Low_52W": 900,
              "High_52W": 1500, "Volume": 1_200_000, "Volume_EMA20": 900_000, "Next_Open": 1405,
              "Suggested_Stop": 1380, "Risk_Per_Share": 20, "Minervini_Volume": True,
              "Virgin_CPR_Buy": True, "Narrow_CPR_Breakout": True, "Sections_Passed": 3,
              "Qualified": True}
             for t in sequences["minervini-volume-cpr"][index]],
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
            | {"ATHERENERG", "APLAPOLLO"}
        )
        _write(folder / "Option_Scan_Candidates.csv", [{"Ticker": t} for t in merged])
        option_path = folder / "Combined_Option_Spread_Analysis.xlsx"
        if index == 1:
            # Leave one day skipped for combined-option to exercise the banner.
            pass
        else:
            option_rows = [
                {
                    "Symbol": "ATHERENERG",
                    "Strategy": "Bull Call Spread",
                    "Expiry": "2026-09-25",
                    "PCR": 0.7,
                    "Score": 68 + index,
                    "R:R Ratio": 1.1,
                    "Validation Pass": False,
                },
                {
                    "Symbol": "APLAPOLLO",
                    "Strategy": "Bull Put Spread",
                    "Expiry": "2026-09-25",
                    "PCR": 1.1,
                    "Score": 81 + index,
                    "R:R Ratio": 1.6,
                    "Validation Pass": True,
                },
            ]
            for offset, ticker in enumerate(merged[:2]):
                option_rows.append(
                    {
                        "Symbol": ticker,
                        "Strategy": "Iron Condor",
                        "Expiry": "2026-09-25",
                        "PCR": 0.95,
                        "Score": 70 + index + offset,
                        "R:R Ratio": 1.3,
                        "Validation Pass": bool(offset % 2 == 0),
                    }
                )
            with pd.ExcelWriter(option_path, engine="openpyxl") as writer:
                pd.DataFrame(option_rows).to_excel(writer, sheet_name="All Opportunities", index=False)

        file_map = {
            "bullish-bias-nifty500": folder / "Bullish_Bias_Analysis.xlsx",
            "bearish-bias-nifty500": folder / "Bearish_Momentum_Analysis.xlsx",
            "nifty500-xy-intersect": folder / "nifty500_xy_matrix_signals.csv",
            "rangebound-stocks": folder / "Strangle_Candidate_Analysis.xlsx",
            "minervini-vcp": folder / "Minervini_VCP_Scan.xlsx",
            "nimblr-minervini-cpr": folder / "Nimblr_Minervini_CPR_Scan.xlsx",
            "minervini-volume-cpr": folder / "Minervini_Volume_CPR_Scan.xlsx",
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
