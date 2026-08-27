"""Run configured trading jobs at most once per successful calendar day.

Windows Task Scheduler can fire both the 8 AM trigger and a logon trigger.
This runner is the gate: after a successful run it writes a marker and later
triggers exit immediately instead of repeating the work.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
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

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ALREADY_RUNNING = "already_running"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ALREADY_RUNNING = 2

LOGGER = logging.getLogger("daily_once_runner")


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
class JobSpec:
    name: str
    script: str
    args: tuple[str, ...] = ()
    enabled: bool = True
    timeout_seconds: float | None = None
    skip_if_empty_input: bool = False

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
        )

    @property
    def input_path(self) -> str | None:
        """Value passed after '--input' in args, if any."""
        for flag, value in zip(self.args, self.args[1:]):
            if flag == "--input":
                return value
        return None


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
            results = [self.job_executor(job) for job in self.jobs if job.enabled]
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

    def _execute_job(self, job: JobSpec) -> JobResult:
        if job.skip_if_empty_input:
            input_path = job.input_path
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

        command = [self.python_executable, str(script_path), *job.args]
        LOGGER.info("Starting job %s: %s", job.name, " ".join(command))
        started = self.now_fn()
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                check=False,
                timeout=job.timeout_seconds,
            )
            returncode = completed.returncode
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
    )
    report = runner.run(force=args.force)
    print(report.message)
    return exit_code_for(report.status)


if __name__ == "__main__":
    raise SystemExit(main())
