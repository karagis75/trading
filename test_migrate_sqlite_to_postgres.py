"""Tests for SQLite -> PostgreSQL scanner history migration."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scanner_history import db
from scanner_history.migrate_sqlite_to_postgres import (
    migrate,
    resolve_scanner_id,
    select_rows,
    table_columns,
)
from scanner_history.tracker import MembershipTracker, TrackingConfig


POSTGRES_URL = os.environ.get(
    "TRADING_DATABASE_URL",
    "postgresql://trading_app:trading_test_pw@127.0.0.1:5432/trading_history",
)


def _postgres_available() -> bool:
    try:
        connection = db.connect(POSTGRES_URL)
        connection.close()
        return True
    except Exception:
        return False


def write_universe(path: Path, tickers: list[str]) -> None:
    pd.DataFrame(
        {
            "Company Name": [f"{ticker} Ltd" for ticker in tickers],
            "Industry": ["IT"] * len(tickers),
            "Ticker": tickers,
            "Series": ["EQ"] * len(tickers),
            "ISIN Code": [f"INE{i:08d}" for i in range(len(tickers))],
        }
    ).to_csv(path, index=False)


def write_hits(path: Path, tickers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"Ticker": tickers, "Status": ["hit"] * len(tickers)}).to_csv(
        path, index=False
    )


def write_option_rows(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "Symbol": symbols,
            "Strategy": ["Bull Call Spread"] * len(symbols),
            "Expiry": ["2026-09-25"] * len(symbols),
            "PCR": [0.8] * len(symbols),
            "Score": [70.0] * len(symbols),
            "R:R Ratio": [1.5] * len(symbols),
            "Validation Pass": [True] * len(symbols),
        }
    ).to_excel(path, index=False)


class SqliteFilterHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "source.sqlite3"
        universe = self.root / "universe.csv"
        write_universe(universe, ["TCS", "INFY", "RELIANCE"])
        self.tracker = MembershipTracker.from_path(self.db_path, universe)

        bias_cfg = TrackingConfig(
            enabled=True,
            role="primary_scanner",
            format="csv",
            symbol_column="Ticker",
        )
        option_cfg = TrackingConfig(
            enabled=True,
            role="downstream",
            format="xlsx",
            symbol_column="Symbol",
        )
        self.tracker.upsert_scanner(
            "bullish-bias-nifty500",
            bias_cfg,
            display_name="Bullish Bias NIFTY 500",
        )
        self.tracker.upsert_scanner(
            "combined-option-v8",
            option_cfg,
            display_name="Combined Option Spread Analysis",
        )

        bias_path = self.root / "bias.csv"
        write_hits(bias_path, ["TCS", "INFY"])
        self.tracker.ingest_output(
            scanner_id="bullish-bias-nifty500",
            tracking=bias_cfg,
            scan_date=date(2026, 8, 25),
            output_path=bias_path,
            job_ok=True,
        )

        option_path = self.root / "option.xlsx"
        write_option_rows(option_path, ["RELIANCE", "TCS"])
        self.tracker.ingest_output(
            scanner_id="combined-option-v8",
            tracking=option_cfg,
            scan_date=date(2026, 8, 25),
            output_path=option_path,
            job_ok=True,
        )
        self.source = self.tracker.connection

    def tearDown(self) -> None:
        self.tracker.close()
        self.tmp.cleanup()

    def test_resolve_scanner_by_id_and_display_name(self) -> None:
        self.assertEqual(
            resolve_scanner_id(self.source, "combined-option-v8"),
            "combined-option-v8",
        )
        self.assertEqual(
            resolve_scanner_id(self.source, "Combined Option Spread Analysis"),
            "combined-option-v8",
        )
        with self.assertRaises(ValueError):
            resolve_scanner_id(self.source, "missing-scanner")

    def test_select_rows_filters_to_combined_option_only(self) -> None:
        scanner_id = "combined-option-v8"
        scanners = select_rows(
            self.source, "scanners", table_columns(self.source, "scanners"), scanner_id
        )
        self.assertEqual([row[0] for row in scanners], ["combined-option-v8"])

        runs = select_rows(
            self.source, "scan_runs", table_columns(self.source, "scan_runs"), scanner_id
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][1], "combined-option-v8")

        daily = select_rows(
            self.source,
            "stock_scanner_daily",
            table_columns(self.source, "stock_scanner_daily"),
            scanner_id,
        )
        self.assertEqual({row[3] for row in daily}, {"RELIANCE", "TCS"})

        stocks = select_rows(
            self.source, "stocks", table_columns(self.source, "stocks"), scanner_id
        )
        self.assertEqual({row[0] for row in stocks}, {"RELIANCE", "TCS"})

        details = select_rows(
            self.source,
            "scanner_result_detail",
            table_columns(self.source, "scanner_result_detail"),
            scanner_id,
        )
        self.assertEqual({row[2] for row in details}, {"RELIANCE", "TCS"})


@unittest.skipUnless(_postgres_available(), "PostgreSQL is not available")
class MigrateCombinedOptionToPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source_path = self.root / "source.sqlite3"
        universe = self.root / "universe.csv"
        write_universe(universe, ["TCS", "INFY", "RELIANCE", "HDFCBANK"])

        self.tracker = MembershipTracker.from_path(self.source_path, universe)
        bias_cfg = TrackingConfig(
            enabled=True,
            role="primary_scanner",
            format="csv",
            symbol_column="Ticker",
        )
        option_cfg = TrackingConfig(
            enabled=True,
            role="downstream",
            format="xlsx",
            symbol_column="Symbol",
        )
        self.tracker.upsert_scanner(
            "bullish-bias-nifty500",
            bias_cfg,
            display_name="Bullish Bias NIFTY 500",
        )
        self.tracker.upsert_scanner(
            "combined-option-v8",
            option_cfg,
            display_name="Combined Option Spread Analysis",
        )
        write_hits(self.root / "bias.csv", ["TCS", "INFY", "HDFCBANK"])
        self.tracker.ingest_output(
            scanner_id="bullish-bias-nifty500",
            tracking=bias_cfg,
            scan_date=date(2026, 8, 25),
            output_path=self.root / "bias.csv",
            job_ok=True,
        )
        write_option_rows(self.root / "option.xlsx", ["RELIANCE", "TCS"])
        self.tracker.ingest_output(
            scanner_id="combined-option-v8",
            tracking=option_cfg,
            scan_date=date(2026, 8, 25),
            output_path=self.root / "option.xlsx",
            job_ok=True,
        )
        self.tracker.close()

        # Isolate each run in a fresh database.
        self.target_url = (
            "postgresql://trading_app:trading_test_pw@127.0.0.1:5432/"
            "trading_history_migrate_test"
        )
        import psycopg

        with psycopg.connect(
            "postgresql://trading_app:trading_test_pw@127.0.0.1:5432/postgres",
            autocommit=True,
        ) as conn:
            conn.execute("DROP DATABASE IF EXISTS trading_history_migrate_test")
            conn.execute("CREATE DATABASE trading_history_migrate_test OWNER trading_app")

    def tearDown(self) -> None:
        import psycopg

        with psycopg.connect(
            "postgresql://trading_app:trading_test_pw@127.0.0.1:5432/postgres",
            autocommit=True,
        ) as conn:
            conn.execute("DROP DATABASE IF EXISTS trading_history_migrate_test")
        self.tmp.cleanup()

    def test_migrate_only_combined_option_spread_analysis(self) -> None:
        counts, resolved = migrate(
            self.source_path,
            self.target_url,
            scanner="Combined Option Spread Analysis",
        )
        self.assertEqual(resolved, "combined-option-v8")
        self.assertEqual(counts["scanners"], 1)
        self.assertEqual(counts["scan_runs"], 1)
        self.assertGreaterEqual(counts["stock_scanner_daily"], 2)
        self.assertEqual(counts["stocks"], 2)

        target = db.connect(self.target_url)
        try:
            scanners = [
                row["scanner_id"]
                for row in target.execute("SELECT scanner_id FROM scanners").fetchall()
            ]
            self.assertEqual(scanners, ["combined-option-v8"])

            other = target.execute(
                "SELECT COUNT(*) AS n FROM scan_runs WHERE scanner_id = ?",
                ("bullish-bias-nifty500",),
            ).fetchone()["n"]
            self.assertEqual(other, 0)

            symbols = {
                row["symbol"]
                for row in target.execute(
                    "SELECT symbol FROM stock_scanner_daily WHERE scanner_id = ?",
                    ("combined-option-v8",),
                ).fetchall()
            }
            self.assertEqual(symbols, {"RELIANCE", "TCS"})
        finally:
            target.close()


if __name__ == "__main__":
    unittest.main()
