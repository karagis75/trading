import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

import daily_once_runner as runner
import validate_daily_outputs as validate
import yahoo_bar_store as store
from scanner_history.db import connect
from scanner_history.tracker import MembershipTracker, TrackingConfig


def write_xlsx(path: Path, tickers: list[str], **columns: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({"Ticker": tickers, **columns})
    frame.to_excel(path, index=False, engine="openpyxl")


def sample_jobs(tmp: Path) -> Path:
    jobs = {
        "jobs": [
            {
                "name": "prefetch-yahoo-ohlcv",
                "enabled": True,
                "script": "prefetch_yahoo_ohlcv.py",
                "tracking": {"enabled": False},
            },
            {
                "name": "bullish-bias-nifty500",
                "enabled": True,
                "script": "bullishbiasnifty500.py",
                "args": ["--output", "outputs/{date}/Bullish_Bias_Analysis.xlsx"],
                "tracking": {"enabled": True, "symbol_column": "Ticker"},
            },
            {
                "name": "minervini-volume-cpr",
                "enabled": True,
                "script": "minervini_volume_cpr_scanner.py",
                "args": ["--output", "outputs/{date}/Minervini_Volume_CPR_Scan.xlsx"],
                "tracking": {
                    "enabled": True,
                    "symbol_column": "Ticker",
                    "membership_filter": "Qualified=True",
                },
            },
            {
                "name": "combined-option-v8",
                "enabled": True,
                "script": "combinedoptionanalyzedv8.py",
                "args": ["--output", "outputs/{date}/Combined_Option_Spread_Analysis.xlsx"],
                "tracking": {"enabled": True, "sheet": "All Opportunities", "symbol_column": "Symbol"},
            },
        ]
    }
    path = tmp / "jobs.json"
    path.write_text(json.dumps(jobs), encoding="utf-8")
    return path


class DiscoverAndCompareTests(unittest.TestCase):
    def test_uses_only_files_already_present_for_aug30(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "outputs" / "2026-08-30"
            replay = root / "outputs" / "2026-08-30-cache-validation"
            write_xlsx(baseline / "Bullish_Bias_Analysis.xlsx", ["TCS", "INFY"])
            jobs = runner.load_jobs(sample_jobs(root))
            found = validate.discover_jobs(jobs, date(2026, 8, 30), baseline, replay)
            by_name = {item.name: item for item in found}
            self.assertEqual(by_name["prefetch-yahoo-ohlcv"].status, "skipped_no_output")
            self.assertEqual(by_name["bullish-bias-nifty500"].status, "pending")
            self.assertEqual(by_name["minervini-volume-cpr"].status, "skipped_missing_baseline")
            self.assertEqual(by_name["combined-option-v8"].status, "skipped_missing_baseline")

    def test_matching_ticker_sets_are_a_match(self) -> None:
        baseline = pd.DataFrame({"Ticker": ["TCS", "INFY"], "Close Price": [100.0, 200.0]})
        replay = pd.DataFrame({"Ticker": ["INFY", "TCS"], "Close Price": [200.0, 100.0]})
        result = validate.compare_frames(
            "bullish-bias-nifty500",
            "Bullish_Bias_Analysis.xlsx",
            baseline,
            replay,
            Path("baseline.xlsx"),
            Path("replay.xlsx"),
        )
        self.assertEqual(result.status, "match")
        self.assertEqual(result.shared, ["INFY", "TCS"])
        self.assertEqual(result.only_baseline, [])
        self.assertEqual(result.only_replay, [])

    def test_ticker_mismatch_is_reported(self) -> None:
        baseline = pd.DataFrame({"Ticker": ["TCS", "ABB"]})
        replay = pd.DataFrame({"Ticker": ["TCS", "INFY"]})
        result = validate.compare_frames(
            "minervini-volume-cpr",
            "Minervini_Volume_CPR_Scan.xlsx",
            baseline,
            replay,
            Path("baseline.xlsx"),
            Path("replay.xlsx"),
        )
        self.assertEqual(result.status, "ticker_mismatch")
        self.assertEqual(result.only_baseline, ["ABB"])
        self.assertEqual(result.only_replay, ["INFY"])

    def test_missing_baseline_folder_skips_with_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = sample_jobs(root)
            report = root / "report.xlsx"
            code = validate.main(
                [
                    "--date",
                    "2026-08-30",
                    "--jobs",
                    str(jobs),
                    "--baseline",
                    str(root / "outputs" / "2026-08-30"),
                    "--output",
                    str(report),
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(report.exists())
            summary = pd.read_excel(report, sheet_name="Summary")
            self.assertTrue((summary["Status"] != "ticker_mismatch").all())


class ReplayTests(unittest.TestCase):
    def test_replay_redirects_output_and_compares(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "outputs" / "2026-08-30"
            replay_dir = root / "outputs" / "2026-08-30-cache-validation"
            write_xlsx(baseline / "Bullish_Bias_Analysis.xlsx", ["TCS", "INFY"])
            jobs = sample_jobs(root)
            report = root / "Yahoo_Cache_Validation.xlsx"

            def fake_replay(job, run_date, dest, repo_root, runner_fn=None):
                args = validate.replay_args(job, run_date, dest)
                self.assertEqual(args[args.index("--output") + 1], str(dest / "Bullish_Bias_Analysis.xlsx"))
                write_xlsx(dest / "Bullish_Bias_Analysis.xlsx", ["INFY", "TCS"])
                result = Mock()
                result.returncode = 0
                result.stdout = "ok"
                result.stderr = ""
                return result

            original = validate.run_replay
            validate.run_replay = fake_replay  # type: ignore[method-assign]
            try:
                code = validate.main(
                    [
                        "--date",
                        "2026-08-30",
                        "--jobs",
                        str(jobs),
                        "--baseline",
                        str(baseline),
                        "--replay-dir",
                        str(replay_dir),
                        "--output",
                        str(report),
                        "--replay",
                    ]
                )
            finally:
                validate.run_replay = original  # type: ignore[method-assign]

            self.assertEqual(code, 0)
            summary = pd.read_excel(report, sheet_name="Summary")
            bullish = summary.loc[summary["Job"] == "bullish-bias-nifty500"].iloc[0]
            self.assertEqual(bullish["Status"], "match")
            volume = summary.loc[summary["Job"] == "minervini-volume-cpr"].iloc[0]
            self.assertEqual(volume["Status"], "skipped_missing_baseline")
            option = summary.loc[summary["Job"] == "combined-option-v8"].iloc[0]
            self.assertEqual(option["Status"], "skipped_missing_baseline")

    def test_bundled_schedule_includes_one_time_validation_job(self) -> None:
        jobs = {job.name: job for job in runner.load_jobs(runner.DEFAULT_JOBS_PATH)}
        job = jobs["validate-2026-08-30-outputs"]
        self.assertFalse(job.enabled)
        self.assertFalse(job.tracking.enabled)
        self.assertEqual(job.script, "validate_daily_outputs.py")
        self.assertIn("2026-08-30", job.args)
        self.assertTrue((runner.REPO_ROOT / job.script).exists())


class DatabaseAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.sqlite3"
        self.universe = self.root / "universe.csv"
        pd.DataFrame(
            {
                "Company Name": ["TCS Ltd", "INFY Ltd", "ABB Ltd"],
                "Industry": ["IT", "IT", "Cap Goods"],
                "Ticker": ["TCS", "INFY", "ABB"],
                "Series": ["EQ", "EQ", "EQ"],
                "ISIN Code": ["INE1", "INE2", "INE3"],
            }
        ).to_csv(self.universe, index=False)
        self.conn = connect(self.db)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_yahoo_cache_reports_fetch_once_shared_table(self) -> None:
        index = pd.bdate_range(end="2026-08-28", periods=20)
        history = pd.DataFrame(
            {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5, "Volume": 100.0},
            index=index,
        )
        store.prefetch_symbols(
            ["TCS", "INFY", "ABB"],
            period="1mo",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: history,
        )
        audit = validate.audit_yahoo_cache(self.conn, date(2026, 8, 30))
        self.assertEqual(audit["Status"], "fetch_once_shared_table")
        self.assertEqual(audit["success"], 3)
        self.assertEqual(audit["symbols"], 3)
        speed = validate.measure_cache_speed(self.conn, date(2026, 8, 30))
        self.assertEqual(speed["Status"], "fast")
        self.assertGreater(speed["symbols"], 0)
        self.assertIn("live Yahoo not called", speed["Message"])

    def test_scanner_db_matches_existing_output_file(self) -> None:
        baseline = self.root / "outputs" / "2026-08-30"
        hits = baseline / "Bullish_Bias_Analysis.xlsx"
        write_xlsx(hits, ["TCS", "INFY"], **{"Status": ["Confirmed Bullish", "Confirmed Bullish"]})
        tracker = MembershipTracker(self.conn, self.universe)
        tracker.ingest_output(
            scanner_id="bullish-bias-nifty500",
            tracking=TrackingConfig(
                enabled=True,
                role="primary_scanner",
                format="xlsx",
                symbol_column="Ticker",
                classification_column="Status",
            ),
            scan_date=date(2026, 8, 30),
            output_path=hits,
            job_ok=True,
        )
        jobs = runner.load_jobs(sample_jobs(self.root))
        rows = validate.audit_scanner_db(self.conn, jobs, date(2026, 8, 30), baseline)
        by_name = {row["Job"]: row for row in rows}
        self.assertEqual(by_name["bullish-bias-nifty500"]["Status"], "match")
        self.assertEqual(by_name["bullish-bias-nifty500"]["File tickers"], 2)
        self.assertEqual(by_name["bullish-bias-nifty500"]["DB tickers"], 2)
        self.assertEqual(by_name["minervini-volume-cpr"]["Status"], "no_db_or_file")

    def test_main_audits_sqlite_when_no_output_folder(self) -> None:
        index = pd.bdate_range(end="2026-08-28", periods=12)
        history = pd.DataFrame(
            {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5, "Volume": 100.0},
            index=index,
        )
        store.prefetch_symbols(
            ["TCS"],
            period="1mo",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda *_args: history,
        )
        report = self.root / "Yahoo_Cache_Validation.xlsx"
        code = validate.main(
            [
                "--date",
                "2026-08-30",
                "--jobs",
                str(sample_jobs(self.root)),
                "--baseline",
                str(self.root / "missing-outputs"),
                "--output",
                str(report),
                "--database",
                str(self.db),
            ]
        )
        self.assertEqual(code, 0)
        cache = pd.read_excel(report, sheet_name="Yahoo cache")
        self.assertIn("fetch_once_shared_table", set(cache["Status"]))
        speed = pd.read_excel(report, sheet_name="Cache speed")
        self.assertIn("fast", set(speed["Status"]))

    def test_stale_sqlite_mismatch_does_not_fail_when_postgres_matches(self) -> None:
        baseline = self.root / "outputs" / "2026-08-30"
        hits = baseline / "Bullish_Bias_Analysis.xlsx"
        write_xlsx(hits, ["TCS", "INFY"], **{"Status": ["Confirmed Bullish", "Confirmed Bullish"]})
        live = MembershipTracker(self.conn, self.universe)
        live.ingest_output(
            scanner_id="bullish-bias-nifty500",
            tracking=TrackingConfig(
                enabled=True,
                role="primary_scanner",
                format="xlsx",
                symbol_column="Ticker",
                classification_column="Status",
            ),
            scan_date=date(2026, 8, 30),
            output_path=hits,
            job_ok=True,
        )
        stale = self.root / "stale.sqlite3"
        stale_conn = connect(stale)
        try:
            stale_hits = self.root / "stale.xlsx"
            write_xlsx(stale_hits, ["WIPRO"], **{"Status": ["Confirmed Bullish"]})
            MembershipTracker(stale_conn, self.universe).ingest_output(
                scanner_id="bullish-bias-nifty500",
                tracking=TrackingConfig(
                    enabled=True,
                    role="primary_scanner",
                    format="xlsx",
                    symbol_column="Ticker",
                    classification_column="Status",
                ),
                scan_date=date(2026, 8, 30),
                output_path=stale_hits,
                job_ok=True,
            )
        finally:
            stale_conn.close()

        report = self.root / "Yahoo_Cache_Validation.xlsx"
        original_configured = validate.configured_databases

        def fake_configured(extra=None):
            return [("sqlite", str(stale)), ("postgres", str(self.db))]

        validate.configured_databases = fake_configured  # type: ignore[method-assign]
        try:
            code = validate.main(
                [
                    "--date",
                    "2026-08-30",
                    "--jobs",
                    str(sample_jobs(self.root)),
                    "--baseline",
                    str(baseline),
                    "--output",
                    str(report),
                ]
            )
        finally:
            validate.configured_databases = original_configured  # type: ignore[method-assign]
        self.assertEqual(code, 0)
        scanner_db = pd.read_excel(report, sheet_name="Scanner DB")
        sqlite_row = scanner_db.loc[
            (scanner_db["Database"] == "sqlite")
            & (scanner_db["Job"] == "bullish-bias-nifty500")
        ].iloc[0]
        postgres_row = scanner_db.loc[
            (scanner_db["Database"] == "postgres")
            & (scanner_db["Job"] == "bullish-bias-nifty500")
        ].iloc[0]
        self.assertEqual(sqlite_row["Status"], "ticker_mismatch")
        self.assertEqual(postgres_row["Status"], "match")

    def test_membership_filter_is_applied_to_file_tickers(self) -> None:
        baseline = self.root / "outputs" / "2026-08-30"
        hits = baseline / "Minervini_Volume_CPR_Scan.xlsx"
        write_xlsx(
            hits,
            ["TCS", "INFY"],
            **{"Qualified": [True, False]},
        )
        tracker = MembershipTracker(self.conn, self.universe)
        tracker.ingest_output(
            scanner_id="minervini-volume-cpr",
            tracking=TrackingConfig(
                enabled=True,
                role="primary_scanner",
                format="xlsx",
                symbol_column="Ticker",
                membership_filter="Qualified=True",
            ),
            scan_date=date(2026, 8, 30),
            output_path=hits,
            job_ok=True,
        )
        jobs = runner.load_jobs(sample_jobs(self.root))
        rows = validate.audit_scanner_db(self.conn, jobs, date(2026, 8, 30), baseline)
        by_name = {row["Job"]: row for row in rows}
        self.assertEqual(by_name["minervini-volume-cpr"]["Status"], "match")
        self.assertEqual(by_name["minervini-volume-cpr"]["File tickers"], 1)
        self.assertEqual(by_name["minervini-volume-cpr"]["DB tickers"], 1)


if __name__ == "__main__":
    unittest.main()
