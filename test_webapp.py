"""Tests for the trading dashboard webapp and new history queries."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scanner_history.queries import (
    day_statuses,
    recent_scan_dates,
    scanner_dates,
    scanner_day_rows,
    scanner_day_run,
    scanner_index,
    search_stocks,
    stock_change_matrix,
    stock_in_any_scanner,
    stock_summary,
)
from scanner_history.tracker import MembershipTracker, TrackingConfig
from webapp import create_app
from webapp.config import AppConfig, JobMeta


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


def write_hits(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_excel(path, index=False, engine="openpyxl")


class DashboardHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.universe = self.root / "universe.csv"
        self.db_path = self.root / "history.sqlite3"
        write_universe(self.universe, ["RELIANCE", "TCS", "INFY", "HDFCBANK", "WIPRO"])
        self.tracker = MembershipTracker.from_path(self.db_path, self.universe)
        self.bullish = TrackingConfig(
            enabled=True,
            role="primary_scanner",
            format="xlsx",
            symbol_column="Ticker",
            classification_column="Status",
        )
        self.bearish = TrackingConfig(
            enabled=True,
            role="primary_scanner",
            format="xlsx",
            symbol_column="Ticker",
            classification_column="Setup Status",
        )
        self.days = [date(2026, 8, 24) + timedelta(days=offset) for offset in range(6)]
        self._seed()

        jobs_path = self.root / "jobs.json"
        jobs_path.write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "name": "bullish-bias-nifty500",
                            "enabled": True,
                            "script": "bullishbiasnifty500.py",
                            "tracking": {"enabled": True, "role": "primary_scanner"},
                        },
                        {
                            "name": "bearish-bias-nifty500",
                            "enabled": True,
                            "script": "bearisbiasnifty500.py",
                            "tracking": {"enabled": True, "role": "primary_scanner"},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = AppConfig(
            database_url=str(self.db_path),
            jobs_path=jobs_path,
            jobs=[
                JobMeta(name="bullish-bias-nifty500", enabled=True, role="primary_scanner"),
                JobMeta(name="bearish-bias-nifty500", enabled=True, role="primary_scanner"),
            ],
        )
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tracker.close()
        self.tmp.cleanup()

    def _seed(self) -> None:
        # Day progression for bullish: RELIANCE added -> continued -> dropped
        # TCS appears later; INFY never picked by either scanner.
        bullish_by_day = {
            0: ["RELIANCE"],
            1: ["RELIANCE", "TCS"],
            2: ["RELIANCE", "TCS"],
            3: ["TCS"],
            4: ["TCS", "HDFCBANK"],
            5: ["HDFCBANK"],
        }
        bearish_by_day = {
            0: ["WIPRO"],
            1: ["WIPRO"],
            2: [],
            3: ["WIPRO"],
            4: ["WIPRO", "TCS"],
            5: ["TCS"],
        }
        for index, day in enumerate(self.days):
            bullish_path = self.root / f"bullish-{day.isoformat()}.xlsx"
            bearish_path = self.root / f"bearish-{day.isoformat()}.xlsx"
            write_hits(
                bullish_path,
                [{"Ticker": ticker, "Status": "Bullish", "Close Price": 100 + index} for ticker in bullish_by_day[index]],
            )
            write_hits(
                bearish_path,
                [{"Ticker": ticker, "Setup Status": "Bearish", "Close Price": 90 + index} for ticker in bearish_by_day[index]],
            )
            self.tracker.ingest_output(
                scanner_id="bullish-bias-nifty500",
                tracking=self.bullish,
                scan_date=day,
                output_path=bullish_path,
                job_ok=True,
            )
            if index == 2:
                # Simulate a skipped downstream-style day for bearish on day 2.
                self.tracker.ingest_output(
                    scanner_id="bearish-bias-nifty500",
                    tracking=self.bearish,
                    scan_date=day,
                    output_path=bearish_path,
                    skipped=True,
                    job_message="no candidates",
                )
            else:
                self.tracker.ingest_output(
                    scanner_id="bearish-bias-nifty500",
                    tracking=self.bearish,
                    scan_date=day,
                    output_path=bearish_path,
                    job_ok=True,
                )

    def test_query_scanner_dates_and_index(self) -> None:
        connection = self.tracker.connection
        dates = scanner_dates(connection, "bullish-bias-nifty500", limit=6)
        self.assertEqual(dates[0], self.days[-1].isoformat())
        self.assertEqual(len(dates), 6)
        index = scanner_index(connection)
        names = {row["scanner_id"] for row in index}
        self.assertIn("bullish-bias-nifty500", names)
        self.assertEqual(recent_scan_dates(connection, limit=6)[0], self.days[-1].isoformat())

    def test_query_scanner_day_rows_and_statuses(self) -> None:
        connection = self.tracker.connection
        day = self.days[-1].isoformat()
        run = scanner_day_run(connection, "bullish-bias-nifty500", day)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "success")
        rows = scanner_day_rows(connection, "bullish-bias-nifty500", day)
        symbols = {row["symbol"] for row in rows}
        self.assertIn("HDFCBANK", symbols)
        # DROPPED TCS should still appear for continuity.
        self.assertIn("TCS", symbols)
        statuses = day_statuses(connection, day)
        by_id = {row["scanner_id"]: row for row in statuses}
        self.assertEqual(by_id["bullish-bias-nifty500"]["status"], "success")

    def test_query_stock_search_and_matrix(self) -> None:
        connection = self.tracker.connection
        self.assertTrue(stock_in_any_scanner(connection, "RELIANCE"))
        self.assertFalse(stock_in_any_scanner(connection, "INFY"))
        matches = search_stocks(connection, "rel")
        self.assertEqual(matches[0]["symbol"], "RELIANCE")
        none = search_stocks(connection, "INFY")
        self.assertEqual(none, [])
        summary = stock_summary(connection, "TCS")
        self.assertTrue(any(row["scanner_id"] == "bullish-bias-nifty500" for row in summary))
        matrix = stock_change_matrix(connection, "TCS", days=6)
        self.assertEqual(len(matrix["dates"]), 6)
        self.assertIn("bullish-bias-nifty500", matrix["cells"][matrix["dates"][0]])

    def test_routes_main_and_scanners(self) -> None:
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"Scanner View", home.data)
        self.assertIn(b"Stock View", home.data)
        self.assertIn(self.days[-1].isoformat().encode(), home.data)

        index = self.client.get("/scanners/")
        self.assertEqual(index.status_code, 200)
        self.assertIn(b"bullish-bias-nifty500", index.data)

        latest = self.client.get("/scanners/bullish-bias-nifty500")
        self.assertEqual(latest.status_code, 302)
        day = self.client.get(f"/scanners/bullish-bias-nifty500/{self.days[-1].isoformat()}")
        self.assertEqual(day.status_code, 200)
        self.assertIn(b"HDFCBANK", day.data)
        self.assertIn(b"CONTINUED", day.data)
        self.assertIn(b"DROPPED", day.data)
        # Date chips for previous days
        self.assertIn(self.days[-2].isoformat().encode(), day.data)
        added_day = self.client.get(f"/scanners/bullish-bias-nifty500/{self.days[0].isoformat()}")
        self.assertEqual(added_day.status_code, 200)
        self.assertIn(b"ADDED", added_day.data)

        skipped = self.client.get(f"/scanners/bearish-bias-nifty500/{self.days[2].isoformat()}")
        self.assertEqual(skipped.status_code, 200)
        self.assertIn(b"no candidates", skipped.data)

        missing = self.client.get("/scanners/does-not-exist/2026-08-29")
        self.assertEqual(missing.status_code, 404)

    def test_routes_stocks_and_api(self) -> None:
        search = self.client.get("/stocks/?q=TCS")
        self.assertEqual(search.status_code, 200)
        self.assertIn(b"TCS", search.data)

        found = self.client.get("/stocks/TCS")
        self.assertEqual(found.status_code, 200)
        self.assertIn(b"Summary across scanners", found.data)
        self.assertIn(b"History", found.data)

        missing = self.client.get("/stocks/INFY")
        self.assertEqual(missing.status_code, 200)
        self.assertIn(b"was not found in any scanner results", missing.data)

        unknown = self.client.get("/stocks/NOTATICKER")
        self.assertEqual(unknown.status_code, 200)
        self.assertIn(b"was not found in any scanner results", unknown.data)

        api = self.client.get("/api/stocks/search?q=rel")
        self.assertEqual(api.status_code, 200)
        payload = api.get_json()
        self.assertEqual(payload["results"][0]["symbol"], "RELIANCE")


class OptionValidationHighlightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.universe = self.root / "universe.csv"
        self.db_path = self.root / "history.sqlite3"
        write_universe(self.universe, ["ATHERENERG", "APLAPOLLO", "RELIANCE"])
        self.tracker = MembershipTracker.from_path(self.db_path, self.universe)
        tracking = TrackingConfig(
            enabled=True,
            role="downstream",
            format="xlsx",
            sheet="All Opportunities",
            symbol_column="Symbol",
            classification_column="Strategy",
            confidence_column="Score",
        )
        day = date(2026, 8, 28)
        path = self.root / "Combined_Option_Spread_Analysis.xlsx"
        frame = pd.DataFrame(
            [
                {
                    "Symbol": "ATHERENERG",
                    "Strategy": "Bull Call Spread",
                    "Expiry": "2026-09-25",
                    "PCR": 0.7,
                    "Score": 68,
                    "R:R Ratio": 1.1,
                    "Validation Pass": False,
                },
                {
                    "Symbol": "APLAPOLLO",
                    "Strategy": "Bull Put Spread",
                    "Expiry": "2026-09-25",
                    "PCR": 1.1,
                    "Score": 81,
                    "R:R Ratio": 1.6,
                    "Validation Pass": True,
                },
                {
                    "Symbol": "ATHERENERG",
                    "Strategy": "Iron Condor",
                    "Expiry": "2026-09-25",
                    "PCR": 0.8,
                    "Score": 60,
                    "R:R Ratio": 1.0,
                    "Validation Pass": False,
                },
            ]
        )
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="All Opportunities", index=False)
        self.tracker.ingest_output(
            scanner_id="combined-option-v8",
            tracking=tracking,
            scan_date=day,
            output_path=path,
            job_ok=True,
        )
        jobs_path = self.root / "jobs.json"
        jobs_path.write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "name": "combined-option-v8",
                            "enabled": True,
                            "script": "combinedoptionanalyzedv8.py",
                            "tracking": {
                                "enabled": True,
                                "role": "downstream",
                                "format": "xlsx",
                                "sheet": "All Opportunities",
                                "symbol_column": "Symbol",
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.app = create_app(
            AppConfig(
                database_url=str(self.db_path),
                jobs_path=jobs_path,
                jobs=[JobMeta(name="combined-option-v8", enabled=True, role="downstream")],
            )
        )
        self.client = self.app.test_client()
        self.day = day.isoformat()

    def tearDown(self) -> None:
        self.tracker.close()
        self.tmp.cleanup()

    def test_validation_pass_colors_and_multi_rows(self) -> None:
        from webapp.helpers import validation_failed

        rows = scanner_day_rows(self.tracker.connection, "combined-option-v8", self.day)
        self.assertEqual(len(rows), 3)
        ather = [row for row in rows if row["symbol"] == "ATHERENERG"]
        apollo = [row for row in rows if row["symbol"] == "APLAPOLLO"]
        self.assertEqual(len(ather), 2)
        self.assertEqual(len(apollo), 1)
        self.assertTrue(validation_failed(ather[0]))
        self.assertFalse(validation_failed(apollo[0]))

        page = self.client.get(f"/scanners/combined-option-v8/{self.day}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"ATHERENERG", page.data)
        self.assertIn(b"APLAPOLLO", page.data)
        self.assertIn(b"validation-failed", page.data)
        self.assertIn(b"validation-passed", page.data)
        self.assertIn(b"Validation Pass", page.data)


if __name__ == "__main__":
    unittest.main()
