import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

import daily_once_runner as runner
import validate_daily_outputs as validate


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
                "tracking": {"enabled": True, "symbol_column": "Ticker"},
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
        self.assertTrue(job.enabled)
        self.assertFalse(job.tracking.enabled)
        self.assertEqual(job.script, "validate_daily_outputs.py")
        self.assertIn("2026-08-30", job.args)
        self.assertTrue((runner.REPO_ROOT / job.script).exists())


if __name__ == "__main__":
    unittest.main()
