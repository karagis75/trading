"""One-time validation: compare existing dated outputs to a cache-backed replay.

Uses whatever files are already under ``outputs/YYYY-MM-DD`` (for example the
30 Aug 2026 morning run) as the baseline. Jobs that produced a file can be
replayed with the shared Yahoo bar cache into a sibling folder, then ticker
sets are compared. Jobs with no baseline file are skipped.

Disable the ``validate-2026-08-30-outputs`` schedule entry after you review
``outputs/2026-08-30/Yahoo_Cache_Validation.xlsx``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import daily_once_runner as runner

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_JOBS = REPO_ROOT / "scheduler" / "jobs.json"
TICKER_COLUMNS = ("Ticker", "ticker", "Symbol", "symbol", "SYMBOL")
DEFAULT_SKIP_REPLAY = frozenset(
    {
        "prefetch-yahoo-ohlcv",
        "validate-2026-08-30-outputs",
        "merge-option-candidates",
        "combined-option-v8",
    }
)


def normalize_ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    if ticker.endswith(".NS"):
        ticker = ticker[:-3]
    if ticker.lower() in {"", "nan", "none", "null"}:
        return ""
    return ticker


def output_filename(job: runner.JobSpec, run_date: date) -> str | None:
    template = job.output_path
    if not template:
        return None
    return Path(template.replace("{date}", run_date.isoformat())).name


def read_output_frame(path: Path, sheet: str | None = None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    kwargs: dict[str, Any] = {"engine": "openpyxl"}
    if sheet:
        kwargs["sheet_name"] = sheet
        try:
            return pd.read_excel(path, **kwargs)
        except ValueError:
            return pd.read_excel(path, engine="openpyxl", sheet_name=0)
    try:
        return pd.read_excel(path, engine="openpyxl", sheet_name="All")
    except ValueError:
        return pd.read_excel(path, engine="openpyxl", sheet_name=0)


def ticker_column(frame: pd.DataFrame) -> str | None:
    for column in TICKER_COLUMNS:
        if column in frame.columns:
            return column
    return None


def ticker_set(frame: pd.DataFrame) -> list[str]:
    column = ticker_column(frame)
    if column is None or frame.empty:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for value in frame[column].tolist():
        ticker = normalize_ticker(value)
        if ticker and ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)
    return ordered


@dataclass
class JobCompare:
    name: str
    filename: str | None
    baseline_path: Path | None
    replay_path: Path | None
    status: str
    baseline_rows: int = 0
    replay_rows: int = 0
    shared: list[str] = field(default_factory=list)
    only_baseline: list[str] = field(default_factory=list)
    only_replay: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def matched(self) -> bool:
        return self.status in {"match", "skipped_missing_baseline"}


def compare_frames(
    name: str,
    filename: str,
    baseline: pd.DataFrame,
    replay: pd.DataFrame,
    baseline_path: Path,
    replay_path: Path,
) -> JobCompare:
    left = set(ticker_set(baseline))
    right = set(ticker_set(replay))
    only_left = sorted(left - right)
    only_right = sorted(right - left)
    shared = sorted(left & right)
    status = "match" if left == right else "ticker_mismatch"
    message = (
        f"{len(shared)} shared, {len(only_left)} only in baseline, {len(only_right)} only in replay"
    )
    return JobCompare(
        name=name,
        filename=filename,
        baseline_path=baseline_path,
        replay_path=replay_path,
        status=status,
        baseline_rows=len(baseline),
        replay_rows=len(replay),
        shared=shared,
        only_baseline=only_left,
        only_replay=only_right,
        message=message,
    )


def discover_jobs(
    jobs: list[runner.JobSpec],
    run_date: date,
    baseline_dir: Path,
    replay_dir: Path,
) -> list[JobCompare]:
    discovered: list[JobCompare] = []
    for job in jobs:
        filename = output_filename(job, run_date)
        if not filename:
            discovered.append(
                JobCompare(
                    name=job.name,
                    filename=None,
                    baseline_path=None,
                    replay_path=None,
                    status="skipped_no_output",
                    message="Job has no --output file to compare.",
                )
            )
            continue
        baseline_path = baseline_dir / filename
        replay_path = replay_dir / filename
        if not baseline_path.exists():
            discovered.append(
                JobCompare(
                    name=job.name,
                    filename=filename,
                    baseline_path=baseline_path,
                    replay_path=replay_path,
                    status="skipped_missing_baseline",
                    message=f"No existing {filename} under {baseline_dir}.",
                )
            )
            continue
        discovered.append(
            JobCompare(
                name=job.name,
                filename=filename,
                baseline_path=baseline_path,
                replay_path=replay_path,
                status="pending",
                message="Baseline file found.",
            )
        )
    return discovered


def replay_args(job: runner.JobSpec, run_date: date, replay_dir: Path) -> list[str]:
    expanded = list(job.expanded_args(run_date))
    filename = output_filename(job, run_date)
    if not filename:
        return expanded
    replay_output = str(replay_dir / filename)
    replaced = False
    patched: list[str] = []
    for index, part in enumerate(expanded):
        if replaced:
            replaced = False
            continue
        if part == "--output" and index + 1 < len(expanded):
            patched.extend(["--output", replay_output])
            replaced = True
            continue
        patched.append(part)
    if "--output" not in patched:
        patched.extend(["--output", replay_output])
    return patched


def run_replay(
    job: runner.JobSpec,
    run_date: date,
    replay_dir: Path,
    repo_root: Path,
    *,
    runner_fn: Callable[..., Any] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    replay_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(repo_root / job.script), *replay_args(job, run_date, replay_dir)]
    return runner_fn(command, cwd=str(repo_root), check=False, text=True, capture_output=True)


def compare_existing(
    item: JobCompare,
    sheet: str | None,
) -> JobCompare:
    assert item.baseline_path is not None and item.replay_path is not None
    if not item.replay_path.exists():
        item.status = "missing_replay"
        item.message = f"Replay file not written: {item.replay_path.name}"
        return item
    baseline = read_output_frame(item.baseline_path, sheet)
    replay = read_output_frame(item.replay_path, sheet)
    item.baseline_rows = len(baseline)
    item.replay_rows = len(replay)
    compared = compare_frames(
        item.name,
        item.filename or item.baseline_path.name,
        baseline,
        replay,
        item.baseline_path,
        item.replay_path,
    )
    return compared


def write_report(results: list[JobCompare], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(
        [
            {
                "Job": item.name,
                "File": item.filename or "",
                "Status": item.status,
                "Baseline rows": item.baseline_rows,
                "Replay rows": item.replay_rows,
                "Shared tickers": len(item.shared),
                "Only baseline": len(item.only_baseline),
                "Only replay": len(item.only_replay),
                "Message": item.message,
            }
            for item in results
        ]
    )
    details_rows: list[dict[str, str]] = []
    for item in results:
        for ticker in item.only_baseline:
            details_rows.append({"Job": item.name, "Ticker": ticker, "Side": "only_baseline"})
        for ticker in item.only_replay:
            details_rows.append({"Job": item.name, "Ticker": ticker, "Side": "only_replay"})
    details = pd.DataFrame(details_rows, columns=["Job", "Ticker", "Side"])
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        details.to_excel(writer, sheet_name="Ticker diffs", index=False)
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare existing dated scanner outputs to a cache-backed replay. "
            "Intended as a one-time check of the 2026-08-30 morning files."
        )
    )
    parser.add_argument("--date", default="2026-08-30", help="Baseline calendar date.")
    parser.add_argument(
        "--jobs",
        default=str(DEFAULT_JOBS),
        help="scheduler/jobs.json path.",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Folder with the original outputs (default: outputs/{date}).",
    )
    parser.add_argument(
        "--replay-dir",
        default=None,
        help="Folder for cache-backed replay outputs (default: outputs/{date}-cache-validation).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Validation workbook path (default: outputs/{date}/Yahoo_Cache_Validation.xlsx).",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Re-run jobs that already have a baseline file, writing into --replay-dir.",
    )
    parser.add_argument(
        "--skip-replay",
        default=",".join(sorted(DEFAULT_SKIP_REPLAY)),
        help="Comma-separated job names that should not be re-executed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_date = date.fromisoformat(args.date)
    baseline_dir = Path(args.baseline) if args.baseline else REPO_ROOT / "outputs" / run_date.isoformat()
    replay_dir = (
        Path(args.replay_dir)
        if args.replay_dir
        else REPO_ROOT / "outputs" / f"{run_date.isoformat()}-cache-validation"
    )
    report_path = (
        Path(args.output)
        if args.output
        else baseline_dir / "Yahoo_Cache_Validation.xlsx"
    )
    jobs = runner.load_jobs(Path(args.jobs))
    skip_replay = {name.strip() for name in str(args.skip_replay).split(",") if name.strip()}
    results = discover_jobs(jobs, run_date, baseline_dir, replay_dir)

    available = [item for item in results if item.status == "pending"]
    if not baseline_dir.exists() or not available:
        print(
            f"No existing scanner outputs found under {baseline_dir}. "
            "Nothing to validate; skipping."
        )
        write_report(results, report_path)
        print(f"Wrote inventory to '{report_path}'.")
        return 0

    print(f"Found {len(available)} existing output file(s) under {baseline_dir}.")
    refreshed: list[JobCompare] = []
    for item in results:
        job = next(job for job in jobs if job.name == item.name)
        if item.status != "pending":
            print(f"  {item.name}: {item.status} — {item.message}")
            refreshed.append(item)
            continue
        should_replay = args.replay and item.name not in skip_replay
        if should_replay:
            print(f"  {item.name}: replaying with cache into {replay_dir}...")
            completed = run_replay(job, run_date, replay_dir, REPO_ROOT)
            if completed.returncode != 0:
                item.status = "replay_failed"
                item.message = (completed.stderr or completed.stdout or "replay failed").strip()[:500]
                print(f"  {item.name}: replay_failed")
                refreshed.append(item)
                continue
        elif item.replay_path is None or not item.replay_path.exists():
            item.status = "skipped_not_replayed"
            item.message = (
                "Baseline kept; this job was not replayed "
                "(not a Yahoo-cache scanner, or --replay was off)."
            )
            print(f"  {item.name}: {item.status} — {item.message}")
            refreshed.append(item)
            continue
        compared = compare_existing(item, job.tracking.sheet)
        print(f"  {compared.name}: {compared.status} — {compared.message}")
        refreshed.append(compared)
    results = refreshed

    write_report(results, report_path)
    mismatches = [item for item in results if item.status in {"ticker_mismatch", "missing_replay", "replay_failed"}]
    matches = [item for item in results if item.status == "match"]
    print(
        f"Validation complete. match={len(matches)} mismatch={len(mismatches)}. "
        f"Saved '{report_path}'."
    )
    for item in results:
        if item.status in {"match", "skipped_missing_baseline", "skipped_no_output"}:
            continue
        print(f"  {item.name}: {item.status} — {item.message}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
