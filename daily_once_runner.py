"""Run configured trading jobs at most once per successful calendar day.

Windows Task Scheduler can fire both the 8 AM trigger and a logon trigger.
This runner is the gate: after every job succeeds it writes a marker and later
triggers exit immediately instead of repeating the work. If some jobs fail,
later triggers retry only the failed or not-yet-run jobs; jobs that already
succeeded earlier the same day are left alone.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Sequence, TextIO

REPO_ROOT = Path(__file__).resolve().parent
SCHEDULER_DIR = REPO_ROOT / "scheduler"
DEFAULT_JOBS_PATH = SCHEDULER_DIR / "jobs.json"
DEFAULT_STATE_PATH = SCHEDULER_DIR / "state" / "run-state.json"
DEFAULT_LOCK_PATH = SCHEDULER_DIR / "state" / "run.lock"
DEFAULT_LOG_DIR = SCHEDULER_DIR / "logs"
DEFAULT_HISTORY_DB = os.environ.get(
    "TRADING_DATABASE_URL",
    str(REPO_ROOT / "scanner_history" / "scanner_history.sqlite3"),
)
DEFAULT_UNIVERSE_PATH = REPO_ROOT / "ind_nifty500list.csv"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ALREADY_RUNNING = "already_running"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ALREADY_RUNNING = 2

LOGGER = logging.getLogger("daily_once_runner")


def _redact_db_url(value: str | Path) -> str:
    """Hide a PostgreSQL password when logging the configured database target."""
    text = str(value)
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", text)


class FileLock:
    """Non-blocking exclusive lock so 8 AM and logon cannot overlap."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise BlockingIOError(f"Could not lock {self.path}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


@dataclass(frozen=True)
class TrackingSpec:
    enabled: bool = False
    role: str = "primary_scanner"
    format: str = "xlsx"
    sheet: str | None = None
    symbol_column: str = "Ticker"
    membership_filter: str | None = None
    signal_date_column: str | None = None
    classification_column: str | None = None
    confidence_column: str | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> "TrackingSpec":
        if not isinstance(raw, dict) or not raw:
            return cls()
        sheet = raw.get("sheet")
        return cls(
            enabled=bool(raw.get("enabled", False)),
            role=str(raw.get("role") or "primary_scanner"),
            format=str(raw.get("format") or "xlsx"),
            sheet=None if sheet in (None, "", "null") else str(sheet),
            symbol_column=str(raw.get("symbol_column") or "Ticker"),
            membership_filter=raw.get("membership_filter"),
            signal_date_column=raw.get("signal_date_column"),
            classification_column=raw.get("classification_column"),
            confidence_column=raw.get("confidence_column"),
        )


@dataclass(frozen=True)
class JobSpec:
    name: str
    script: str
    args: tuple[str, ...] = ()
    enabled: bool = True
    timeout_seconds: float | None = None
    skip_if_empty_input: bool = False
    tracking: TrackingSpec = field(default_factory=TrackingSpec)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "JobSpec":
        name = str(raw.get("name") or "").strip()
        script = str(raw.get("script") or "").strip()
        if not name:
            raise ValueError("Each job must have a non-empty 'name'.")
        if not script:
            raise ValueError(f"Job '{name}' is missing 'script'.")
        timeout = raw.get("timeout_seconds")
        return cls(
            name=name,
            script=script,
            args=tuple(str(part) for part in raw.get("args") or ()),
            enabled=bool(raw.get("enabled", True)),
            timeout_seconds=float(timeout) if timeout is not None else None,
            skip_if_empty_input=bool(raw.get("skip_if_empty_input", False)),
            tracking=TrackingSpec.from_dict(raw.get("tracking")),
        )

    @property
    def input_path(self) -> str | None:
        """Value passed after '--input' in args, if any."""
        for flag, value in zip(self.args, self.args[1:]):
            if flag == "--input":
                return value
        return None

    @property
    def output_path(self) -> str | None:
        """Value passed after '--output' in args, if any."""
        for flag, value in zip(self.args, self.args[1:]):
            if flag == "--output":
                return value
        return None

    def expanded_args(self, run_date: date) -> tuple[str, ...]:
        """Expand the supported date placeholder in command-line arguments."""
        return tuple(argument.replace("{date}", run_date.isoformat()) for argument in self.args)


@dataclass(frozen=True)
class JobResult:
    name: str
    returncode: int
    duration_seconds: float
    skipped: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.skipped or self.returncode == 0


@dataclass
class RunReport:
    status: str
    run_date: date
    jobs: list[JobResult] = field(default_factory=list)
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_state(self) -> dict[str, Any]:
        return {
            "date": self.run_date.isoformat(),
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "finished_at": (
                self.finished_at.isoformat(timespec="seconds") if self.finished_at else None
            ),
            "jobs": [
                {
                    "name": job.name,
                    "returncode": job.returncode,
                    "duration_seconds": round(job.duration_seconds, 3),
                    "skipped": job.skipped,
                    "message": job.message,
                    "ok": job.ok,
                }
                for job in self.jobs
            ],
        }


def load_jobs(path: Path) -> list[JobSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(raw_jobs, list):
        raise ValueError(f"{path} must contain a 'jobs' array.")
    return [JobSpec.from_dict(item) for item in raw_jobs]


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_state(path: Path, report: RunReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_state(), indent=2) + "\n", encoding="utf-8")


def already_succeeded_today(state: dict[str, Any], today: date) -> bool:
    """True when a previous trigger already completed every job today."""

    return state.get("date") == today.isoformat() and state.get("status") == STATUS_SUCCESS


def job_results_for_today(state: dict[str, Any], today: date) -> dict[str, dict[str, Any]]:
    """Return per-job results recorded earlier today, keyed by job name."""

    if state.get("date") != today.isoformat():
        return {}
    raw_jobs = state.get("jobs")
    if not isinstance(raw_jobs, list):
        return {}
    results: dict[str, dict[str, Any]] = {}
    for item in raw_jobs:
        if isinstance(item, dict) and item.get("name"):
            results[str(item["name"])] = item
    return results


def job_result_from_state(raw: dict[str, Any]) -> JobResult:
    """Rebuild a JobResult from a persisted state entry."""

    return JobResult(
        name=str(raw["name"]),
        returncode=int(raw.get("returncode") or 0),
        duration_seconds=float(raw.get("duration_seconds") or 0.0),
        skipped=bool(raw.get("skipped", False)),
        message=str(raw.get("message") or "already succeeded today"),
    )


def job_already_succeeded_today(raw: dict[str, Any]) -> bool:
    """True when a job already finished successfully earlier today."""

    return bool(raw.get("ok"))


def configure_logging(log_dir: Path, today: date, stream: TextIO | None = None) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_dir / f"{today.isoformat()}.log", encoding="utf-8"),
        logging.StreamHandler(stream or sys.stdout),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


class DailyOnceRunner:
    def __init__(
        self,
        repo_root: Path,
        jobs: Sequence[JobSpec],
        state_path: Path,
        lock_path: Path,
        python_executable: str | None = None,
        today_fn: Callable[[], date] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        job_executor: Callable[[JobSpec], JobResult] | None = None,
        lock_factory: Callable[[Path], FileLock] | None = None,
        history_db: str | Path | None = None,
        universe_path: Path | None = None,
        tracker: Any = None,
        write_history_report: bool = True,
    ) -> None:
        self.repo_root = repo_root
        self.jobs = list(jobs)
        self.state_path = state_path
        self.lock_path = lock_path
        self.python_executable = python_executable or sys.executable
        self.today_fn = today_fn or date.today
        self.now_fn = now_fn or datetime.now
        self.job_executor = job_executor or self._execute_job
        self.lock_factory = lock_factory or FileLock
        self.history_db = history_db or repo_root / "scanner_history" / "scanner_history.sqlite3"
        self.universe_path = Path(universe_path) if universe_path else repo_root / "ind_nifty500list.csv"
        self._injected_tracker = tracker
        self._live_tracker = None
        self.write_history_report = write_history_report

    def run(self, force: bool = False) -> RunReport:
        today = self.today_fn()
        started = self.now_fn()
        state = load_state(self.state_path)

        if not force and already_succeeded_today(state, today):
            message = (
                f"Already succeeded on {today.isoformat()}; "
                "stopping remaining schedule triggers for the day."
            )
            LOGGER.info(message)
            return RunReport(
                status=STATUS_SKIPPED,
                run_date=today,
                message=message,
                started_at=started,
                finished_at=self.now_fn(),
            )

        lock = self.lock_factory(self.lock_path)
        if not lock.acquire():
            message = "Another daily run is already in progress; not starting a second copy."
            LOGGER.info(message)
            return RunReport(
                status=STATUS_ALREADY_RUNNING,
                run_date=today,
                message=message,
                started_at=started,
                finished_at=self.now_fn(),
            )

        try:
            previous_job_results = {} if force else job_results_for_today(state, today)
            results: list[JobResult] = []
            for job in self.jobs:
                if not job.enabled:
                    continue
                previous = previous_job_results.get(job.name)
                if previous and job_already_succeeded_today(previous):
                    result = job_result_from_state(previous)
                    LOGGER.info(
                        "Skipping job %s; already succeeded earlier today.",
                        job.name,
                    )
                else:
                    result = self.job_executor(job)
                    self._ingest_history(job, result, today)
                results.append(result)
            self._write_membership_report(today)
            failed = [job for job in results if not job.ok]
            if failed:
                names = ", ".join(job.name for job in failed)
                message = f"Daily run failed: {names}"
                status = STATUS_FAILED
                LOGGER.error(message)
            else:
                message = f"Daily run succeeded for {today.isoformat()}."
                status = STATUS_SUCCESS
                LOGGER.info(message)

            report = RunReport(
                status=status,
                run_date=today,
                jobs=results,
                message=message,
                started_at=started,
                finished_at=self.now_fn(),
            )
            write_state(self.state_path, report)
            return report
        finally:
            lock.release()

    def _get_tracker(self):
        if self._injected_tracker is not None:
            return self._injected_tracker
        from scanner_history.tracker import MembershipTracker

        if self._live_tracker is None:
            self._live_tracker = MembershipTracker.from_path(self.history_db, self.universe_path)
        return self._live_tracker

    def _tracking_config(self, job: JobSpec):
        from scanner_history.tracker import TrackingConfig

        spec = job.tracking
        return TrackingConfig(
            enabled=spec.enabled,
            role=spec.role,
            format=spec.format,
            sheet=spec.sheet,
            symbol_column=spec.symbol_column,
            membership_filter=spec.membership_filter,
            signal_date_column=spec.signal_date_column,
            classification_column=spec.classification_column,
            confidence_column=spec.confidence_column,
        )

    def _ingest_history(self, job: JobSpec, result: JobResult, scan_date: date) -> None:
        if not job.tracking.enabled:
            return
        template = job.output_path
        if not template:
            LOGGER.warning("Tracking enabled for %s but job has no --output path.", job.name)
            return
        output_path = self._resolve_path(template.replace("{date}", scan_date.isoformat()))
        try:
            ingest = self._get_tracker().ingest_output(
                scanner_id=job.name,
                tracking=self._tracking_config(job),
                scan_date=scan_date,
                output_path=output_path,
                job_ok=result.returncode == 0 and not result.skipped,
                skipped=result.skipped,
                job_message=result.message,
            )
            LOGGER.info(
                "History ingest %s: status=%s hits=%s",
                job.name,
                ingest.status,
                ingest.result_count,
            )
        except Exception:
            LOGGER.exception("Failed to ingest scanner history for %s", job.name)

    def _write_membership_report(self, scan_date: date) -> None:
        if not self.write_history_report:
            return
        if not any(job.tracking.enabled for job in self.jobs if job.enabled):
            return
        try:
            from scanner_history.report import write_daily_report

            destination = self.repo_root / "outputs" / scan_date.isoformat() / "Scanner_Membership_Changes.xlsx"
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_daily_report(self._get_tracker().connection, destination, scan_date)
            LOGGER.info("Wrote membership report to %s", destination)
        except Exception:
            LOGGER.exception("Failed to write scanner membership report")

    def _resolve_path(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (self.repo_root / path).resolve()
        return path

    def _has_input_rows(self, input_path: str) -> bool:
        """True when a CSV input has at least one data row below the header."""
        path = self._resolve_path(input_path)
        if not path.exists():
            return False
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = csv.reader(handle)
                next(rows, None)  # header
                return next(rows, None) is not None
        except OSError:
            return False

    def _prepare_output_paths(self, args: Sequence[str]) -> None:
        """Create parent directories for scanner outputs before subprocesses run."""
        for flag, value in zip(args, args[1:]):
            if flag == "--output":
                self._resolve_path(value).parent.mkdir(parents=True, exist_ok=True)

    def _execute_job(self, job: JobSpec) -> JobResult:
        args = job.expanded_args(self.today_fn())
        if job.skip_if_empty_input:
            input_path = next(
                (value for flag, value in zip(args, args[1:]) if flag == "--input"),
                None,
            )
            if input_path is not None and not self._has_input_rows(input_path):
                message = f"No candidates in '{input_path}'; skipping {job.name}."
                LOGGER.info(message)
                return JobResult(
                    name=job.name,
                    returncode=0,
                    duration_seconds=0.0,
                    skipped=True,
                    message=message,
                )

        script_path = self._resolve_path(job.script)
        if not script_path.exists():
            message = f"Script not found: {script_path}"
            LOGGER.error(message)
            return JobResult(name=job.name, returncode=1, duration_seconds=0.0, message=message)

        self._prepare_output_paths(args)
        command = [self.python_executable, str(script_path), *args]
        LOGGER.info("Starting job %s: %s", job.name, " ".join(command))
        started = self.now_fn()
        try:
            returncode = self._run_logged_command(command, job.name, job.timeout_seconds)
            message = f"exit {returncode}"
        except subprocess.TimeoutExpired:
            returncode = 1
            message = f"timed out after {job.timeout_seconds}s"
            LOGGER.error("Job %s %s", job.name, message)
        duration = (self.now_fn() - started).total_seconds()
        if returncode == 0:
            LOGGER.info("Job %s finished in %.1fs (%s)", job.name, duration, message)
        else:
            LOGGER.error("Job %s failed in %.1fs (%s)", job.name, duration, message)
        return JobResult(
            name=job.name,
            returncode=returncode,
            duration_seconds=duration,
            message=message,
        )

    def _run_logged_command(
        self,
        command: Sequence[str],
        job_name: str,
        timeout_seconds: float | None,
    ) -> int:
        """Run a job and copy its stdout/stderr into the daily scheduler log.

        Scheduled tasks have no console, so prefetch ``print`` lines were
        previously invisible until the process exited.
        """
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            list(command),
            cwd=self.repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                text = line.rstrip()
                if text:
                    LOGGER.info("%s: %s", job_name, text)
            return process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        except Exception:
            process.kill()
            process.wait()
            raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run trading scanners once per calendar day. Later 8 AM / logon "
            "triggers exit immediately after a successful run."
        )
    )
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS_PATH, help="Path to jobs.json.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Success marker file.")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH, help="Exclusive run lock file.")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help="Directory for daily logs.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Directory used as the working folder and as the base for relative job scripts.",
    )
    parser.add_argument(
        "--history-db",
        default=DEFAULT_HISTORY_DB,
        help=(
            "Membership history database: a SQLite file path or a PostgreSQL URL "
            "(postgresql://user:pass@host:5432/db). Defaults to the TRADING_DATABASE_URL "
            "environment variable, falling back to scanner_history/scanner_history.sqlite3."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore today's success marker and run again.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print whether today's run already succeeded, then exit.",
    )
    return parser


def exit_code_for(status: str) -> int:
    if status in {STATUS_SUCCESS, STATUS_SKIPPED}:
        return EXIT_OK
    if status == STATUS_ALREADY_RUNNING:
        return EXIT_ALREADY_RUNNING
    return EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    today = date.today()

    if args.status:
        state = load_state(args.state)
        succeeded = already_succeeded_today(state, today)
        print(
            json.dumps(
                {"date": today.isoformat(), "already_succeeded": succeeded, "state": state},
                indent=2,
            )
        )
        return EXIT_OK

    configure_logging(args.log_dir, today)

    jobs = load_jobs(args.jobs)
    runner = DailyOnceRunner(
        repo_root=args.repo_root.resolve(),
        jobs=jobs,
        state_path=args.state,
        lock_path=args.lock,
        history_db=args.history_db,
    )
    LOGGER.info("Membership history database target: %s", _redact_db_url(args.history_db))
    report = runner.run(force=args.force)
    print(report.message)
    return exit_code_for(report.status)


if __name__ == "__main__":
    raise SystemExit(main())
