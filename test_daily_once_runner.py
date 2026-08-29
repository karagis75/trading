import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
import daily_once_runner as runner


def patch_stdout():
    buffer = io.StringIO()
    return _StdoutCapture(buffer)


class _StdoutCapture:
    def __init__(self, buffer: io.StringIO) -> None:
        self.buffer = buffer
        self._redirect = redirect_stdout(buffer)

    def __enter__(self) -> io.StringIO:
        self._redirect.__enter__()
        return self.buffer

    def __exit__(self, exc_type, exc, tb) -> None:
        self._redirect.__exit__(exc_type, exc, tb)


class FakeLock:
    def __init__(self, path: Path, held: bool = False) -> None:
        self.path = path
        self.held = held
        self.acquired = False
        self.released = False

    def acquire(self) -> bool:
        if self.held:
            return False
        self.acquired = True
        self.held = True
        return True

    def release(self) -> None:
        self.released = True
        self.held = False


class AlreadySucceededTests(unittest.TestCase):
    def test_success_marker_for_today_stops_later_triggers(self) -> None:
        state = {"date": "2026-08-27", "status": runner.STATUS_SUCCESS}
        self.assertTrue(runner.already_succeeded_today(state, date(2026, 8, 27)))

    def test_failed_run_does_not_stop_the_day(self) -> None:
        state = {"date": "2026-08-27", "status": runner.STATUS_FAILED}
        self.assertFalse(runner.already_succeeded_today(state, date(2026, 8, 27)))

    def test_yesterday_success_does_not_block_today(self) -> None:
        state = {"date": "2026-08-26", "status": runner.STATUS_SUCCESS}
        self.assertFalse(runner.already_succeeded_today(state, date(2026, 8, 27)))

    def test_missing_or_corrupt_state_does_not_block(self) -> None:
        self.assertFalse(runner.already_succeeded_today({}, date(2026, 8, 27)))


class JobSpecTests(unittest.TestCase):
    def test_job_spec_parses_enabled_args_and_timeout(self) -> None:
        job = runner.JobSpec.from_dict(
            {
                "name": "bullish",
                "script": "bullishbiasnifty500.py",
                "enabled": True,
                "args": ["--input", "ind_nifty500list.csv"],
                "timeout_seconds": 30,
            }
        )
        self.assertEqual(job.name, "bullish")
        self.assertEqual(job.args, ("--input", "ind_nifty500list.csv"))
        self.assertEqual(job.timeout_seconds, 30.0)

    def test_job_spec_requires_name_and_script(self) -> None:
        with self.assertRaises(ValueError):
            runner.JobSpec.from_dict({"script": "x.py"})
        with self.assertRaises(ValueError):
            runner.JobSpec.from_dict({"name": "x"})

    def test_input_path_reads_value_after_input_flag(self) -> None:
        job = runner.JobSpec("scan", "scan.py", args=("--input", "candidates.csv", "--output", "out.xlsx"))
        self.assertEqual(job.input_path, "candidates.csv")

    def test_input_path_is_none_without_input_flag(self) -> None:
        job = runner.JobSpec("scan", "scan.py", args=("--symbols", "TCS"))
        self.assertIsNone(job.input_path)

    def test_skip_if_empty_input_defaults_to_false(self) -> None:
        job = runner.JobSpec.from_dict({"name": "x", "script": "x.py"})
        self.assertFalse(job.skip_if_empty_input)
        self.assertFalse(job.tracking.enabled)

    def test_expanded_args_use_iso_date_folder(self) -> None:
        job = runner.JobSpec(
            "scan",
            "scan.py",
            args=("--output", r"outputs\{date}\scan.xlsx"),
        )
        self.assertEqual(
            job.expanded_args(date(2026, 8, 27)),
            ("--output", r"outputs\2026-08-27\scan.xlsx"),
        )


class DailyOnceRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_path = self.root / "state" / "run-state.json"
        self.lock_path = self.root / "state" / "run.lock"
        self.today = date(2026, 8, 27)
        self.now = datetime(2026, 8, 27, 8, 0, 0)
        self.executed: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _clock(self) -> datetime:
        current = self.now
        self.now = current + timedelta(seconds=1)
        return current

    def _make_runner(
        self,
        jobs: list[runner.JobSpec],
        executor=None,
        lock_held: bool = False,
    ) -> runner.DailyOnceRunner:
        def lock_factory(path: Path) -> FakeLock:
            return FakeLock(path, held=lock_held)

        return runner.DailyOnceRunner(
            repo_root=self.root,
            jobs=jobs,
            state_path=self.state_path,
            lock_path=self.lock_path,
            today_fn=lambda: self.today,
            now_fn=self._clock,
            job_executor=executor or self._succeed,
            lock_factory=lock_factory,
        )

    def _succeed(self, job: runner.JobSpec) -> runner.JobResult:
        self.executed.append(job.name)
        return runner.JobResult(name=job.name, returncode=0, duration_seconds=1.0, message="ok")

    def _fail(self, job: runner.JobSpec) -> runner.JobResult:
        self.executed.append(job.name)
        return runner.JobResult(name=job.name, returncode=1, duration_seconds=1.0, message="boom")

    def test_first_successful_run_writes_marker_and_later_trigger_is_skipped(self) -> None:
        jobs = [
            runner.JobSpec("bullish", "bullishbiasnifty500.py"),
            runner.JobSpec("bearish", "bearisbiasnifty500.py"),
        ]
        daily = self._make_runner(jobs)

        first = daily.run()
        self.assertEqual(first.status, runner.STATUS_SUCCESS)
        self.assertEqual(self.executed, ["bullish", "bearish"])
        self.assertTrue(self.state_path.exists())
        self.assertTrue(runner.already_succeeded_today(runner.load_state(self.state_path), self.today))

        self.executed.clear()
        second = daily.run()
        self.assertEqual(second.status, runner.STATUS_SKIPPED)
        self.assertEqual(self.executed, [])
        self.assertIn("stopping remaining schedule triggers", second.message)

    def test_failed_run_allows_retry_on_next_trigger(self) -> None:
        jobs = [runner.JobSpec("bullish", "bullishbiasnifty500.py")]
        failing = self._make_runner(jobs, executor=self._fail)

        first = failing.run()
        self.assertEqual(first.status, runner.STATUS_FAILED)
        self.assertFalse(runner.already_succeeded_today(runner.load_state(self.state_path), self.today))

        succeeding = self._make_runner(jobs, executor=self._succeed)
        retry = succeeding.run()
        self.assertEqual(retry.status, runner.STATUS_SUCCESS)
        self.assertEqual(self.executed, ["bullish", "bullish"])

    def test_next_calendar_day_runs_again(self) -> None:
        jobs = [runner.JobSpec("bullish", "bullishbiasnifty500.py")]
        daily = self._make_runner(jobs)
        self.assertEqual(daily.run().status, runner.STATUS_SUCCESS)

        self.today = date(2026, 8, 28)
        self.executed.clear()
        next_day = daily.run()
        self.assertEqual(next_day.status, runner.STATUS_SUCCESS)
        self.assertEqual(self.executed, ["bullish"])

    def test_force_reruns_after_success(self) -> None:
        jobs = [runner.JobSpec("bullish", "bullishbiasnifty500.py")]
        daily = self._make_runner(jobs)
        daily.run()
        self.executed.clear()
        forced = daily.run(force=True)
        self.assertEqual(forced.status, runner.STATUS_SUCCESS)
        self.assertEqual(self.executed, ["bullish"])

    def test_disabled_jobs_are_not_executed(self) -> None:
        jobs = [
            runner.JobSpec("on", "a.py", enabled=True),
            runner.JobSpec("off", "b.py", enabled=False),
        ]
        report = self._make_runner(jobs).run()
        self.assertEqual(report.status, runner.STATUS_SUCCESS)
        self.assertEqual(self.executed, ["on"])

    def test_lock_prevents_overlapping_8am_and_logon_runs(self) -> None:
        jobs = [runner.JobSpec("bullish", "bullishbiasnifty500.py")]
        report = self._make_runner(jobs, lock_held=True).run()
        self.assertEqual(report.status, runner.STATUS_ALREADY_RUNNING)
        self.assertEqual(self.executed, [])
        self.assertFalse(self.state_path.exists())

    def test_relative_script_is_resolved_from_repo_root(self) -> None:
        jobs = [runner.JobSpec("ok", "ok.py")]
        daily = runner.DailyOnceRunner(
            repo_root=self.root,
            jobs=jobs,
            state_path=self.state_path,
            lock_path=self.lock_path,
            today_fn=lambda: self.today,
            now_fn=self._clock,
            lock_factory=lambda path: FakeLock(path),
        )
        (self.root / "ok.py").write_text("print('ok')\n", encoding="utf-8")
        report = daily.run()
        self.assertEqual(report.status, runner.STATUS_SUCCESS)

    def test_date_output_directory_is_created_before_job_runs(self) -> None:
        output_path = "outputs/{date}/scan.txt"
        jobs = [runner.JobSpec("scan", "scan.py", args=("--output", output_path))]
        daily = self._make_runner(jobs)
        daily._prepare_output_paths(jobs[0].expanded_args(self.today))
        self.assertTrue((self.root / "outputs" / "2026-08-27").is_dir())

    def test_skip_if_empty_input_skips_job_without_running_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = root / "ran.txt"
            script = root / "would_run.py"
            script.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
            (root / "candidates.csv").write_text("Ticker\n", encoding="utf-8")

            jobs = [
                runner.JobSpec(
                    "option-scan",
                    "would_run.py",
                    args=("--input", "candidates.csv"),
                    skip_if_empty_input=True,
                )
            ]
            daily = runner.DailyOnceRunner(
                repo_root=root,
                jobs=jobs,
                state_path=root / "state.json",
                lock_path=root / "run.lock",
                today_fn=lambda: date(2026, 8, 27),
            )
            report = daily.run()
            self.assertEqual(report.status, runner.STATUS_SUCCESS)
            self.assertTrue(report.jobs[0].skipped)
            self.assertFalse(marker.exists())

    def test_skip_if_empty_input_runs_job_when_candidates_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = root / "ran.txt"
            script = root / "would_run.py"
            script.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
            (root / "candidates.csv").write_text("Ticker\nTCS\nINFY\n", encoding="utf-8")

            jobs = [
                runner.JobSpec(
                    "option-scan",
                    "would_run.py",
                    args=("--input", "candidates.csv"),
                    skip_if_empty_input=True,
                )
            ]
            daily = runner.DailyOnceRunner(
                repo_root=root,
                jobs=jobs,
                state_path=root / "state.json",
                lock_path=root / "run.lock",
                today_fn=lambda: date(2026, 8, 27),
            )
            report = daily.run()
            self.assertEqual(report.status, runner.STATUS_SUCCESS)
            self.assertFalse(report.jobs[0].skipped)
            self.assertTrue(marker.exists())

    def test_skip_if_empty_input_treats_missing_file_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs = [
                runner.JobSpec(
                    "option-scan",
                    "does_not_exist.py",
                    args=("--input", "missing_candidates.csv"),
                    skip_if_empty_input=True,
                )
            ]
            daily = runner.DailyOnceRunner(
                repo_root=root,
                jobs=jobs,
                state_path=root / "state.json",
                lock_path=root / "run.lock",
                today_fn=lambda: date(2026, 8, 27),
            )
            report = daily.run()
            self.assertEqual(report.status, runner.STATUS_SUCCESS)
            self.assertTrue(report.jobs[0].skipped)

    def test_missing_script_is_a_failed_job(self) -> None:
        jobs = [runner.JobSpec("missing", "does_not_exist.py")]
        daily = runner.DailyOnceRunner(
            repo_root=self.root,
            jobs=jobs,
            state_path=self.state_path,
            lock_path=self.lock_path,
            today_fn=lambda: self.today,
            now_fn=self._clock,
            lock_factory=lambda path: FakeLock(path),
        )
        report = daily.run()
        self.assertEqual(report.status, runner.STATUS_FAILED)
        self.assertEqual(report.jobs[0].returncode, 1)
        self.assertIn("Script not found", report.jobs[0].message)


class JobsConfigAndCliTests(unittest.TestCase):
    def test_main_passes_configured_history_database_to_runner(self) -> None:
        postgres_url = "postgresql://user:password@localhost/trading_history"
        report = runner.RunReport(
            status=runner.STATUS_SUCCESS,
            run_date=date.today(),
            message="ok",
        )
        daily = Mock()
        daily.run.return_value = report

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_path = root / "jobs.json"
            jobs_path.write_text('{"jobs": []}', encoding="utf-8")
            with (
                patch.object(runner, "DailyOnceRunner", return_value=daily) as runner_class,
                patch.object(runner, "configure_logging"),
                patch_stdout(),
            ):
                code = runner.main(
                    [
                        "--jobs",
                        str(jobs_path),
                        "--state",
                        str(root / "state.json"),
                        "--lock",
                        str(root / "run.lock"),
                        "--log-dir",
                        str(root / "logs"),
                        "--repo-root",
                        str(root),
                        "--history-db",
                        postgres_url,
                    ]
                )

        self.assertEqual(code, runner.EXIT_OK)
        self.assertEqual(runner_class.call_args.kwargs["history_db"], postgres_url)

    def test_bundled_jobs_file_points_at_real_scripts(self) -> None:
        jobs = runner.load_jobs(runner.DEFAULT_JOBS_PATH)
        self.assertGreaterEqual(len(jobs), 5)
        enabled = [job for job in jobs if job.enabled]
        self.assertEqual(
            [job.script for job in enabled],
            [
                "bullishbiasnifty500.py",
                "bearisbiasnifty500.py",
                "nifty500_xy_intersect.py",
                "rangeboundstocks.py",
                "nimblr_minervini_cpr_scanner.py",
                "nifty_pinball_yahoo.py",
                "bearish_fib_pinball.py",
                "merge_ticker_candidates.py",
                "combinedoptionanalyzedv8.py",
            ],
        )
        xy_job = next(job for job in jobs if job.name == "nifty500-xy-intersect")
        self.assertEqual(
            xy_job.args,
            (
                "--input",
                "ind_nifty500list.csv",
                "--output",
                "outputs/{date}/nifty500_xy_matrix_signals.csv",
                "--request-delay",
                "0.1",
            ),
        )
        for job in jobs:
            self.assertTrue(
                (runner.REPO_ROOT / job.script).exists(),
                f"configured script is missing: {job.script}",
            )

    def test_option_scan_reads_merged_candidates_and_skips_when_empty(self) -> None:
        jobs = {job.name: job for job in runner.load_jobs(runner.DEFAULT_JOBS_PATH)}

        merge_job = jobs["merge-option-candidates"]
        self.assertEqual(
            list(merge_job.args),
            [
                "--sources",
                "outputs/{date}/Bullish_Bias_Analysis.xlsx",
                "outputs/{date}/Bearish_Momentum_Analysis.xlsx",
                "outputs/{date}/Strangle_Candidate_Analysis.xlsx",
                "--output",
                "outputs/{date}/Option_Scan_Candidates.csv",
            ],
        )

        option_job = jobs["combined-option-v8"]
        self.assertTrue(option_job.skip_if_empty_input)
        self.assertEqual(option_job.input_path, "outputs/{date}/Option_Scan_Candidates.csv")
        self.assertEqual(
            option_job.expanded_args(date(2026, 8, 27))[1],
            "outputs/2026-08-27/Option_Scan_Candidates.csv",
        )
        for flag in ("--browser-impersonation", "--request-delay", "1.5", "--max-retries", "6"):
            self.assertIn(flag, option_job.args)

    def test_status_cli_reports_success_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "run-state.json"
            log_dir = Path(temp_dir) / "logs"
            state_path.write_text(
                json.dumps({"date": date.today().isoformat(), "status": runner.STATUS_SUCCESS}),
                encoding="utf-8",
            )
            with patch_stdout() as buffer:
                code = runner.main(
                    ["--status", "--state", str(state_path), "--log-dir", str(log_dir)]
                )
            payload = json.loads(buffer.getvalue())
            self.assertEqual(code, runner.EXIT_OK)
            self.assertTrue(payload["already_succeeded"])

    def test_exit_codes(self) -> None:
        self.assertEqual(runner.exit_code_for(runner.STATUS_SUCCESS), 0)
        self.assertEqual(runner.exit_code_for(runner.STATUS_SKIPPED), 0)
        self.assertEqual(runner.exit_code_for(runner.STATUS_ALREADY_RUNNING), 2)
        self.assertEqual(runner.exit_code_for(runner.STATUS_FAILED), 1)

    def test_real_subprocess_success_then_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "ok.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            state_path = root / "state.json"
            lock_path = root / "run.lock"
            jobs = [runner.JobSpec("ok", "ok.py")]
            today = date(2026, 8, 27)
            daily = runner.DailyOnceRunner(
                repo_root=root,
                jobs=jobs,
                state_path=state_path,
                lock_path=lock_path,
                today_fn=lambda: today,
            )
            first = daily.run()
            self.assertEqual(first.status, runner.STATUS_SUCCESS)
            second = daily.run()
            self.assertEqual(second.status, runner.STATUS_SKIPPED)

    def test_cli_second_run_same_day_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ok.py").write_text("print('scan complete')\n", encoding="utf-8")
            jobs_path = root / "jobs.json"
            jobs_path.write_text(
                json.dumps({"jobs": [{"name": "demo-scan", "script": "ok.py", "enabled": True}]}),
                encoding="utf-8",
            )
            common = [
                "--jobs",
                str(jobs_path),
                "--state",
                str(root / "state.json"),
                "--lock",
                str(root / "run.lock"),
                "--log-dir",
                str(root / "logs"),
                "--repo-root",
                str(root),
            ]
            with patch_stdout() as buffer:
                first_code = runner.main(common)
                skip_code = runner.main(common)
                status_code = runner.main(["--status", "--state", str(root / "state.json")])
            self.assertEqual(first_code, runner.EXIT_OK)
            self.assertEqual(skip_code, runner.EXIT_OK)
            self.assertEqual(status_code, runner.EXIT_OK)
            output = buffer.getvalue()
            self.assertIn("Daily run succeeded", output)
            self.assertIn("stopping remaining schedule triggers", output)
            self.assertIn('"already_succeeded": true', output)

    def test_failed_subprocess_does_not_stop_the_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "fail.py"
            script.write_text("raise SystemExit(1)\n", encoding="utf-8")
            daily = runner.DailyOnceRunner(
                repo_root=root,
                jobs=[runner.JobSpec("fail", "fail.py")],
                state_path=root / "state.json",
                lock_path=root / "run.lock",
                today_fn=lambda: date(2026, 8, 27),
            )
            first = daily.run()
            self.assertEqual(first.status, runner.STATUS_FAILED)
            self.assertFalse(
                runner.already_succeeded_today(runner.load_state(root / "state.json"), date(2026, 8, 27))
            )

    def test_file_lock_blocks_second_acquirer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "run.lock"
            first = runner.FileLock(lock_path)
            second = runner.FileLock(lock_path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


class SchedulerScriptTests(unittest.TestCase):
    def test_windows_scripts_exist_and_describe_once_per_day_behavior(self) -> None:
        register = (runner.SCHEDULER_DIR / "Register-TradingDailyTask.ps1").read_text(encoding="utf-8")
        run = (runner.SCHEDULER_DIR / "Run-TradingDaily.ps1").read_text(encoding="utf-8")
        unregister = (runner.SCHEDULER_DIR / "Unregister-TradingDailyTask.ps1").read_text(encoding="utf-8")
        self.assertIn("New-ScheduledTaskTrigger -Daily -At $DailyAt", register)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", register)
        self.assertIn("StartWhenAvailable", register)
        self.assertIn("daily_once_runner.py", run)
        self.assertIn("'TRADING_DATABASE_URL'", run)
        self.assertIn(".venv\\Scripts\\python.exe", run)
        self.assertIn("Unregister-ScheduledTask", unregister)


if __name__ == "__main__":
    unittest.main()
