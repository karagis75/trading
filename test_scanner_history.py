import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scanner_history.adapters import parse_scanner_output
from scanner_history.normalize import normalize_symbol
from scanner_history.queries import active, changes, stock_history
from scanner_history.report import write_daily_report
from scanner_history.cli import main as history_cli
from scanner_history.tracker import (
    CHANGE_ADDED,
    CHANGE_CONTINUED,
    CHANGE_DROPPED,
    CHANGE_READED,
    MembershipTracker,
    TrackingConfig,
)


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


def write_hits(path: Path, tickers: list[str], sheet: str | None = None) -> None:
    frame = pd.DataFrame({"Ticker": tickers, "Status": ["hit"] * len(tickers)})
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
        return
    if sheet:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name=sheet, index=False)
    else:
        frame.to_excel(path, index=False, engine="openpyxl")


class NormalizeAndAdapterTests(unittest.TestCase):
    def test_normalize_strips_ns_and_case(self) -> None:
        self.assertEqual(normalize_symbol(" tcs.ns "), "TCS")
        self.assertEqual(normalize_symbol("RELIANCE"), "RELIANCE")

    def test_parser_reads_qualified_filter_and_all_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nimblr.xlsx"
            pd.DataFrame(
                {
                    "Ticker": ["TCS", "INFY"],
                    "Qualified": [True, False],
                    "Date": ["2026-08-27", "2026-08-27"],
                }
            ).to_excel(path, index=False, engine="openpyxl")
            parsed = parse_scanner_output(path, membership_filter="Qualified=True", signal_date_column="Date")
            self.assertEqual(parsed.symbols, ["TCS"])
            self.assertEqual(parsed.hits[0].signal_date, "2026-08-27")

            pinball = Path(tmp) / "pinball.xlsx"
            with pd.ExcelWriter(pinball, engine="openpyxl") as writer:
                pd.DataFrame({"Ticker": ["ABB"]}).to_excel(writer, sheet_name="All", index=False)
                pd.DataFrame({"Ticker": []}).to_excel(writer, sheet_name="Skipped", index=False)
            parsed_pinball = parse_scanner_output(pinball, sheet="All")
            self.assertEqual(parsed_pinball.symbols, ["ABB"])


class MembershipLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.universe = self.root / "ind_nifty500list.csv"
        write_universe(self.universe, ["TCS", "INFY", "RELIANCE"])
        self.tracker = MembershipTracker.from_path(self.root / "history.sqlite3", self.universe)
        self.tracking = TrackingConfig(enabled=True, role="primary_scanner", format="xlsx")

    def tearDown(self) -> None:
        self.tracker.close()
        self.tmp.cleanup()

    def _ingest(self, day: str, tickers: list[str]) -> None:
        path = self.root / f"{day}.xlsx"
        write_hits(path, tickers)
        result = self.tracker.ingest_output(
            scanner_id="bullish-bias-nifty500",
            tracking=self.tracking,
            scan_date=date.fromisoformat(day),
            output_path=path,
            job_ok=True,
        )
        self.assertEqual(result.status, "success")

    def _daily(self, day: str) -> dict[str, str]:
        rows = self.tracker.connection.execute(
            """
            SELECT symbol, change_type, picked, current_streak_scans, ended_streak_scans, total_times_picked
            FROM stock_scanner_daily d
            JOIN scan_runs r ON r.run_id = d.run_id
            WHERE d.scan_date = ? AND r.is_canonical = 1
            """,
            (day,),
        ).fetchall()
        return {row["symbol"]: row for row in rows}

    def test_added_continued_dropped_and_readded(self) -> None:
        self._ingest("2026-08-24", ["TCS"])
        first = self._daily("2026-08-24")
        self.assertEqual(first["TCS"]["change_type"], CHANGE_ADDED)
        self.assertEqual(first["TCS"]["current_streak_scans"], 1)
        self.assertEqual(first["INFY"]["change_type"], "NOT_PICKED")

        self._ingest("2026-08-25", ["TCS"])
        second = self._daily("2026-08-25")
        self.assertEqual(second["TCS"]["change_type"], CHANGE_CONTINUED)
        self.assertEqual(second["TCS"]["current_streak_scans"], 2)

        self._ingest("2026-08-26", [])
        dropped = self._daily("2026-08-26")
        self.assertEqual(dropped["TCS"]["change_type"], CHANGE_DROPPED)
        self.assertEqual(dropped["TCS"]["picked"], 0)
        self.assertEqual(dropped["TCS"]["ended_streak_scans"], 2)

        self._ingest("2026-08-27", ["TCS"])
        readded = self._daily("2026-08-27")
        self.assertEqual(readded["TCS"]["change_type"], CHANGE_READED)
        self.assertEqual(readded["TCS"]["current_streak_scans"], 1)
        self.assertEqual(readded["TCS"]["total_times_picked"], 3)

    def test_friday_to_monday_does_not_reset_streak(self) -> None:
        self._ingest("2026-08-21", ["INFY"])
        self._ingest("2026-08-24", ["INFY"])
        monday = self._daily("2026-08-24")
        self.assertEqual(monday["INFY"]["change_type"], CHANGE_CONTINUED)
        self.assertEqual(monday["INFY"]["current_streak_scans"], 2)

    def test_failed_run_does_not_create_false_drops(self) -> None:
        self._ingest("2026-08-25", ["TCS"])
        missing = self.root / "missing.xlsx"
        result = self.tracker.ingest_output(
            scanner_id="bullish-bias-nifty500",
            tracking=self.tracking,
            scan_date=date.fromisoformat("2026-08-26"),
            output_path=missing,
            job_ok=False,
            job_message="exit 1",
        )
        self.assertEqual(result.status, "failed")
        count = self.tracker.connection.execute(
            "SELECT COUNT(*) AS n FROM stock_scanner_daily WHERE scan_date = '2026-08-26'"
        ).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_queries_and_report(self) -> None:
        self._ingest("2026-08-24", ["TCS"])
        self._ingest("2026-08-25", ["TCS", "INFY"])
        self._ingest("2026-08-26", ["INFY"])
        dropped = changes(
            self.tracker.connection,
            event="DROPPED",
            start=date(2026, 8, 26),
            end=date(2026, 8, 26),
        )
        self.assertEqual([row["symbol"] for row in dropped], ["TCS"])
        added_week = changes(
            self.tracker.connection,
            event="ADDED",
            start=date(2026, 8, 24),
            end=date(2026, 8, 26),
        )
        self.assertIn("TCS", [row["symbol"] for row in added_week])
        still = active(self.tracker.connection, min_streak=1, as_of=date(2026, 8, 26))
        self.assertEqual([row["symbol"] for row in still], ["INFY"])
        tcs = stock_history(self.tracker.connection, "TCS.NS")
        self.assertGreaterEqual(len(tcs), 3)
        self.assertTrue(all(row["symbol"] == "TCS" for row in tcs))
        report_path = self.root / "report.xlsx"
        write_daily_report(self.tracker.connection, report_path, date(2026, 8, 26))
        dropped_sheet = pd.read_excel(report_path, sheet_name="Dropped_Today")
        self.assertEqual(list(dropped_sheet["symbol"]), ["TCS"])


class RunnerTrackingTests(unittest.TestCase):
    def test_job_spec_parses_tracking_block(self) -> None:
        import daily_once_runner as runner

        job = runner.JobSpec.from_dict(
            {
                "name": "bullish-bias-nifty500",
                "script": "bullishbiasnifty500.py",
                "args": ["--output", "outputs/{date}/Bullish_Bias_Analysis.xlsx"],
                "tracking": {
                    "enabled": True,
                    "role": "primary_scanner",
                    "format": "xlsx",
                    "symbol_column": "Ticker",
                },
            }
        )
        self.assertTrue(job.tracking.enabled)
        self.assertEqual(job.output_path, "outputs/{date}/Bullish_Bias_Analysis.xlsx")

    def test_bundled_jobs_enable_tracking(self) -> None:
        import daily_once_runner as runner

        jobs = runner.load_jobs(runner.DEFAULT_JOBS_PATH)
        tracked = {job.name: job.tracking for job in jobs if job.tracking.enabled}
        expected = {
            "bullish-bias-nifty500",
            "bearish-bias-nifty500",
            "nifty500-xy-intersect",
            "rangebound-stocks",
            "nimblr-minervini-cpr",
            "nifty-fib-pinball-bullish",
            "nifty-fib-pinball-bearish",
            "merge-option-candidates",
            "combined-option-v8",
        }
        self.assertEqual(set(tracked), expected)
        self.assertEqual(tracked["nimblr-minervini-cpr"].membership_filter, "Qualified=True")
        self.assertEqual(tracked["nifty-fib-pinball-bullish"].sheet, "All")
        self.assertEqual(tracked["combined-option-v8"].role, "downstream")
        self.assertEqual(tracked["merge-option-candidates"].role, "aggregator")

    def test_runner_ingests_after_successful_job(self) -> None:
        import daily_once_runner as runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "ind_nifty500list.csv"
            write_universe(universe, ["TCS", "INFY"])
            out = root / "outputs" / "2026-08-27" / "Bullish_Bias_Analysis.xlsx"
            write_hits(out, ["TCS"])
            jobs = [
                runner.JobSpec.from_dict(
                    {
                        "name": "bullish-bias-nifty500",
                        "script": "bullishbiasnifty500.py",
                        "args": ["--output", "outputs/{date}/Bullish_Bias_Analysis.xlsx"],
                        "tracking": {"enabled": True, "role": "primary_scanner", "format": "xlsx"},
                    }
                )
            ]

            def execute(job: runner.JobSpec) -> runner.JobResult:
                return runner.JobResult(name=job.name, returncode=0, duration_seconds=0.1, message="ok")

            daily = runner.DailyOnceRunner(
                repo_root=root,
                jobs=jobs,
                state_path=root / "state.json",
                lock_path=root / "run.lock",
                today_fn=lambda: date(2026, 8, 27),
                job_executor=execute,
                history_db=root / "history.sqlite3",
                universe_path=universe,
                write_history_report=True,
            )
            report = daily.run()
            self.assertEqual(report.status, runner.STATUS_SUCCESS)
            conn = daily._get_tracker().connection
            rows = conn.execute(
                "SELECT symbol, change_type FROM stock_scanner_daily WHERE picked = 1"
            ).fetchall()
            self.assertEqual([row["symbol"] for row in rows], ["TCS"])
            self.assertTrue((root / "outputs" / "2026-08-27" / "Scanner_Membership_Changes.xlsx").exists())

    def test_skipped_job_does_not_create_membership_rows(self) -> None:
        import daily_once_runner as runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "ind_nifty500list.csv"
            write_universe(universe, ["TCS"])
            jobs = [
                runner.JobSpec.from_dict(
                    {
                        "name": "combined-option-v8",
                        "script": "combinedoptionanalyzedv8.py",
                        "args": ["--output", "outputs/{date}/Combined_Option_Spread_Analysis.xlsx"],
                        "tracking": {
                            "enabled": True,
                            "role": "downstream",
                            "format": "xlsx",
                            "sheet": "All Opportunities",
                            "symbol_column": "Symbol",
                        },
                    }
                )
            ]

            def execute(job: runner.JobSpec) -> runner.JobResult:
                return runner.JobResult(
                    name=job.name,
                    returncode=0,
                    duration_seconds=0.0,
                    skipped=True,
                    message="empty input",
                )

            daily = runner.DailyOnceRunner(
                repo_root=root,
                jobs=jobs,
                state_path=root / "state.json",
                lock_path=root / "run.lock",
                today_fn=lambda: date(2026, 8, 27),
                job_executor=execute,
                history_db=root / "history.sqlite3",
                universe_path=universe,
                write_history_report=True,
            )
            daily.run()
            conn = daily._get_tracker().connection
            status = conn.execute("SELECT status FROM scan_runs").fetchone()["status"]
            self.assertEqual(status, "skipped")
            count = conn.execute("SELECT COUNT(*) AS n FROM stock_scanner_daily").fetchone()["n"]
            self.assertEqual(count, 0)


class AggregatorAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.universe = self.root / "ind_nifty500list.csv"
        write_universe(self.universe, ["TCS", "INFY", "RELIANCE"])
        self.db = self.root / "history.sqlite3"
        self.tracker = MembershipTracker.from_path(self.db, self.universe)
        self.tracking = TrackingConfig(enabled=True, role="aggregator", format="csv")

    def tearDown(self) -> None:
        self.tracker.close()
        self.tmp.cleanup()

    def _ingest(self, day: str, tickers: list[str]) -> None:
        path = self.root / f"{day}.csv"
        write_hits(path, tickers)
        result = self.tracker.ingest_output(
            scanner_id="merge-option-candidates",
            tracking=self.tracking,
            scan_date=date.fromisoformat(day),
            output_path=path,
            job_ok=True,
        )
        self.assertEqual(result.status, "success")

    def test_aggregator_tracks_hits_and_previous_members_only(self) -> None:
        self._ingest("2026-08-25", ["TCS"])
        self._ingest("2026-08-26", [])
        rows = self.tracker.connection.execute(
            "SELECT symbol, change_type, picked FROM stock_scanner_daily WHERE scan_date = '2026-08-26'"
        ).fetchall()
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertEqual(set(by_symbol), {"TCS"})
        self.assertEqual(by_symbol["TCS"]["change_type"], CHANGE_DROPPED)
        self.assertEqual(by_symbol["TCS"]["picked"], 0)

    def test_missing_file_is_indeterminate_not_a_drop(self) -> None:
        self._ingest("2026-08-25", ["TCS"])
        result = self.tracker.ingest_output(
            scanner_id="merge-option-candidates",
            tracking=self.tracking,
            scan_date=date.fromisoformat("2026-08-26"),
            output_path=self.root / "missing.csv",
            job_ok=True,
            job_message="historical output missing",
        )
        self.assertEqual(result.status, "indeterminate")
        count = self.tracker.connection.execute(
            "SELECT COUNT(*) AS n FROM stock_scanner_daily WHERE scan_date = '2026-08-26'"
        ).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_cli_stock_changes_this_week_and_report(self) -> None:
        self._ingest("2026-08-24", ["TCS"])
        self._ingest("2026-08-25", ["TCS", "INFY"])
        self._ingest("2026-08-26", ["INFY"])
        db = str(self.db)
        universe = str(self.universe)
        dropped_out = self.root / "dropped.json"
        code = history_cli(
            [
                "--db",
                db,
                "--universe",
                universe,
                "changes",
                "--event",
                "DROPPED",
                "--date",
                "2026-08-26",
                "--output",
                str(dropped_out),
            ]
        )
        self.assertEqual(code, 0)
        dropped = pd.read_json(dropped_out)
        self.assertEqual(list(dropped["symbol"]), ["TCS"])

        week_out = self.root / "week.csv"
        code = history_cli(
            [
                "--db",
                db,
                "changes",
                "--event",
                "ADDED",
                "--date",
                "2026-08-26",
                "--this-week",
                "--output",
                str(week_out),
            ]
        )
        self.assertEqual(code, 0)
        week = pd.read_csv(week_out)
        self.assertIn("TCS", list(week["symbol"]))
        self.assertIn("INFY", list(week["symbol"]))

        stock_out = self.root / "tcs.json"
        code = history_cli(["--db", db, "stock", "tcs.ns", "--output", str(stock_out)])
        self.assertEqual(code, 0)
        tcs = pd.read_json(stock_out)
        self.assertGreaterEqual(len(tcs), 3)
        self.assertTrue((tcs["symbol"] == "TCS").all())

        report_path = self.root / "Scanner_Membership_Changes.xlsx"
        code = history_cli(
            ["--db", db, "report", "--date", "2026-08-26", "--output", str(report_path)]
        )
        self.assertEqual(code, 0)
        self.assertTrue(report_path.exists())
        self.assertEqual(list(pd.read_excel(report_path, sheet_name="Dropped_Today")["symbol"]), ["TCS"])


if __name__ == "__main__":
    unittest.main()

