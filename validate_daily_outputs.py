"""One-time validation of the 30 Aug 2026 run against the shared Yahoo cache.

Checks, in order:

1. Whatever Excel/CSV files already exist under ``outputs/YYYY-MM-DD``.
2. Each scanner's ingested hits in SQLite and/or PostgreSQL
   (``scan_runs`` / ``stock_scanner_daily``).
3. The new fetch-once design: ``yahoo_ohlcv_prefetch`` + ``yahoo_ohlcv_daily``
   in the same database, reused by later scanners without calling Yahoo.
4. Cache-hit latency so later scanners stay fast.

Disable the ``validate-2026-08-30-outputs`` schedule entry after you review
``outputs/2026-08-30/Yahoo_Cache_Validation.xlsx``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import daily_once_runner as runner
from scanner_history import db as history_db
from scanner_history import queries
from yahoo_bar_store import DEFAULT_DB, get_daily_history

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


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def configured_databases(extra: list[str] | None = None) -> list[tuple[str, str]]:
    """Return (label, url) pairs for local SQLite and configured PostgreSQL."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            found.append((label, url))

    sqlite_default = REPO_ROOT / "scanner_history" / "scanner_history.sqlite3"
    if sqlite_default.exists():
        add("sqlite", str(sqlite_default))
    env_url = os.environ.get("TRADING_DATABASE_URL") or os.environ.get("TRADING_YAHOO_CACHE_DB")
    if env_url:
        label = "postgres" if env_url.startswith("postgres") else "configured"
        add(label, env_url)
    if DEFAULT_DB and DEFAULT_DB not in seen and Path(str(DEFAULT_DB)).exists():
        add("sqlite", str(DEFAULT_DB))
    for item in extra or []:
        label = "postgres" if str(item).startswith("postgres") else "sqlite"
        add(label, item)
    return found


def picked_symbols(connection: Any, scanner_id: str, scan_date: date | str) -> list[str]:
    run = queries.scanner_day_run(connection, scanner_id, scan_date)
    if not run or not run.get("run_id"):
        return []
    rows = connection.execute(
        """
        SELECT symbol FROM stock_scanner_daily
        WHERE run_id = ? AND scanner_id = ? AND picked = 1
        ORDER BY symbol
        """,
        (run["run_id"], scanner_id),
    ).fetchall()
    return [normalize_ticker(_row_get(row, "symbol")) for row in rows if normalize_ticker(_row_get(row, "symbol"))]


def audit_yahoo_cache(connection: Any, fetch_date: date | str) -> dict[str, Any]:
    """Confirm one shared Yahoo fetch landed in yahoo_ohlcv_* tables."""
    day = fetch_date if isinstance(fetch_date, str) else fetch_date.isoformat()
    try:
        prefetch = connection.execute(
            """
            SELECT status, COUNT(*) AS n
            FROM yahoo_ohlcv_prefetch
            WHERE fetch_date = ?
            GROUP BY status
            """,
            (day,),
        ).fetchall()
        bars = connection.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT symbol) AS symbols FROM yahoo_ohlcv_daily"
        ).fetchone()
        latest = connection.execute(
            "SELECT MAX(bar_date) AS last_bar, MIN(bar_date) AS first_bar FROM yahoo_ohlcv_daily"
        ).fetchone()
    except Exception as exc:
        return {
            "Status": "cache_error",
            "Message": str(exc),
            "success": 0,
            "empty": 0,
            "error": 0,
            "bar_rows": 0,
            "symbols": 0,
        }
    counts = {str(_row_get(row, "status")): int(_row_get(row, "n") or 0) for row in prefetch}
    success = counts.get("success", 0)
    symbols = int(_row_get(bars, "symbols") or 0)
    bar_rows = int(_row_get(bars, "n") or 0)
    if success and symbols:
        status = "fetch_once_shared_table"
        message = (
            f"{success} symbol(s) prefetched on {day}; "
            f"{bar_rows} daily bars for {symbols} symbol(s) in yahoo_ohlcv_daily."
        )
    elif bar_rows:
        status = "bars_without_prefetch_row"
        message = f"{bar_rows} bars present but yahoo_ohlcv_prefetch has no success row for {day}."
    else:
        status = "pending_prefetch"
        message = (
            f"No Yahoo bars stored yet for {day}. "
            "Run prefetch-yahoo-ohlcv so later scanners can read the table."
        )
    return {
        "Status": status,
        "Message": message,
        "success": success,
        "empty": counts.get("empty", 0),
        "error": counts.get("error", 0),
        "bar_rows": bar_rows,
        "symbols": symbols,
        "first_bar": _row_get(latest, "first_bar"),
        "last_bar": _row_get(latest, "last_bar"),
    }


def audit_scanner_db(
    connection: Any,
    jobs: list[runner.JobSpec],
    scan_date: date,
    baseline_dir: Path,
) -> list[dict[str, Any]]:
    """Compare each scanner's DB hits to the existing dated output file."""
    rows: list[dict[str, Any]] = []
    day_rows = {item["scanner_id"]: item for item in queries.day_statuses(connection, scan_date)}
    for job in jobs:
        if not job.tracking.enabled:
            continue
        filename = output_filename(job, scan_date)
        file_path = baseline_dir / filename if filename else None
        file_tickers: list[str] = []
        file_status = "missing_file"
        if file_path and file_path.exists():
            frame = read_output_frame(file_path, job.tracking.sheet)
            if job.tracking.membership_filter:
                from scanner_history.adapters import _apply_filter

                frame = _apply_filter(frame, job.tracking.membership_filter)
            file_tickers = ticker_set(frame)
            file_status = "present"
        db_tickers = picked_symbols(connection, job.name, scan_date)
        run = day_rows.get(job.name) or {}
        only_file = sorted(set(file_tickers) - set(db_tickers))
        only_db = sorted(set(db_tickers) - set(file_tickers))
        if file_status == "missing_file" and not db_tickers and not run:
            status = "no_db_or_file"
            message = "No ingested run and no output file."
        elif file_status == "missing_file":
            status = "db_only"
            message = f"DB has {len(db_tickers)} hit(s); output file not on disk."
        elif not run:
            status = "file_only"
            message = f"File has {len(file_tickers)} ticker(s); no scan_runs row."
        elif set(file_tickers) == set(db_tickers):
            status = "match"
            message = f"{len(file_tickers)} ticker(s) match in file and DB."
        else:
            status = "ticker_mismatch"
            message = (
                f"{len(only_file)} only in file, {len(only_db)} only in DB "
                f"(file={len(file_tickers)} db={len(db_tickers)})."
            )
        rows.append(
            {
                "Job": job.name,
                "DB status": run.get("status"),
                "DB hits": run.get("result_count"),
                "File": filename or "",
                "File tickers": len(file_tickers),
                "DB tickers": len(db_tickers),
                "Shared": len(set(file_tickers) & set(db_tickers)),
                "Only file": len(only_file),
                "Only DB": len(only_db),
                "Status": status,
                "Message": message,
            }
        )
    return rows


def measure_cache_speed(
    connection: Any,
    fetch_date: date | str,
    *,
    limit: int = 25,
) -> dict[str, Any]:
    """Time cache-only reads. Live Yahoo must not be called."""
    day = fetch_date if isinstance(fetch_date, str) else fetch_date.isoformat()
    try:
        rows = connection.execute(
            """
            SELECT symbol FROM yahoo_ohlcv_prefetch
            WHERE fetch_date = ? AND status = 'success'
            ORDER BY symbol
            LIMIT ?
            """,
            (day, limit),
        ).fetchall()
    except Exception as exc:
        return {"Status": "cache_error", "symbols": 0, "seconds": 0.0, "ms_per_symbol": 0.0, "Message": str(exc)}
    symbols = [normalize_ticker(_row_get(row, "symbol")) for row in rows]
    symbols = [symbol for symbol in symbols if symbol]
    if not symbols:
        return {
            "Status": "pending_prefetch",
            "symbols": 0,
            "seconds": 0.0,
            "ms_per_symbol": 0.0,
            "Message": "No prefetched symbols to time.",
        }

    def forbidden(_symbol: str, _period: str) -> pd.DataFrame:
        raise AssertionError("Yahoo live loader must not run on a cache hit")

    # First call may preload the day's table; time the lookups after that.
    try:
        get_daily_history(
            symbols[0],
            period="2y",
            live_loader=forbidden,
            connection=connection,
            fetch_date=date.fromisoformat(day),
            persist=False,
        )
    except AssertionError as exc:
        return {
            "Status": "live_yahoo_called",
            "symbols": len(symbols),
            "seconds": 0.0,
            "ms_per_symbol": 0.0,
            "Message": str(exc),
        }
    started = time.perf_counter()
    loaded = 0
    try:
        for symbol in symbols:
            frame = get_daily_history(
                symbol,
                period="2y",
                live_loader=forbidden,
                connection=connection,
                fetch_date=date.fromisoformat(day),
                persist=False,
            )
            if frame is not None and not frame.empty:
                loaded += 1
    except AssertionError as exc:
        return {
            "Status": "live_yahoo_called",
            "symbols": len(symbols),
            "seconds": time.perf_counter() - started,
            "ms_per_symbol": 0.0,
            "Message": str(exc),
        }
    elapsed = time.perf_counter() - started
    per_ms = (elapsed / len(symbols)) * 1000.0
    # Live Yahoo is typically hundreds of ms to several seconds per name.
    # A local table read should stay well under two seconds even when cold.
    status = "fast" if per_ms <= 2000.0 else "slow"
    return {
        "Status": status,
        "symbols": loaded,
        "seconds": elapsed,
        "ms_per_symbol": per_ms,
        "Message": f"{loaded} cache read(s) in {elapsed:.3f}s ({per_ms:.1f} ms/symbol); live Yahoo not called.",
    }


def write_report(
    results: list[JobCompare],
    output_path: Path,
    *,
    cache_rows: list[dict[str, Any]] | None = None,
    db_rows: list[dict[str, Any]] | None = None,
    speed_rows: list[dict[str, Any]] | None = None,
) -> Path:
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
        pd.DataFrame(cache_rows or []).to_excel(writer, sheet_name="Yahoo cache", index=False)
        pd.DataFrame(db_rows or []).to_excel(writer, sheet_name="Scanner DB", index=False)
        pd.DataFrame(speed_rows or []).to_excel(writer, sheet_name="Cache speed", index=False)
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
    parser.add_argument(
        "--database",
        action="append",
        default=[],
        help="Extra SQLite path or PostgreSQL URL to audit (repeatable).",
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
    if available:
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
    else:
        print(
            f"No existing scanner outputs found under {baseline_dir}. "
            "File replay skipped; still auditing SQLite/PostgreSQL if present."
        )

    cache_rows: list[dict[str, Any]] = []
    db_rows: list[dict[str, Any]] = []
    speed_rows: list[dict[str, Any]] = []
    databases = configured_databases(args.database)
    primary_label = next((label for label, _url in databases if label == "postgres"), None)
    if primary_label is None and databases:
        primary_label = databases[0][0]
    if not databases:
        cache_rows.append(
            {
                "Database": "",
                "Status": "no_database",
                "Message": "No scanner_history.sqlite3 or TRADING_DATABASE_URL found.",
            }
        )
    for label, url in databases:
        print(f"Auditing {label} database...")
        try:
            connection = history_db.connect(url)
        except Exception as exc:
            cache_rows.append({"Database": label, "Status": "connect_failed", "Message": str(exc)})
            continue
        try:
            cache = audit_yahoo_cache(connection, run_date)
            cache["Database"] = label
            cache_rows.append(cache)
            print(f"  Yahoo cache: {cache['Status']} — {cache['Message']}")
            scanner_rows = audit_scanner_db(connection, jobs, run_date, baseline_dir)
            for row in scanner_rows:
                row["Database"] = label
                if row["Status"] == "ticker_mismatch":
                    print(
                        f"  Scanner DB {label}/{row['Job']}: {row['Status']} — {row['Message']}"
                    )
            db_rows.extend(scanner_rows)
            speed = measure_cache_speed(connection, run_date)
            speed["Database"] = label
            speed_rows.append(speed)
            print(f"  Cache speed: {speed['Status']} — {speed['Message']}")
        finally:
            connection.close()

    write_report(
        results,
        report_path,
        cache_rows=cache_rows,
        db_rows=db_rows,
        speed_rows=speed_rows,
    )
    mismatches = [item for item in results if item.status in {"ticker_mismatch", "missing_replay", "replay_failed"}]
    matches = [item for item in results if item.status == "match"]
    live_called = [row for row in speed_rows if row.get("Status") == "live_yahoo_called"]
    primary_db_mismatches = [
        row
        for row in db_rows
        if row.get("Status") == "ticker_mismatch" and row.get("Database") == primary_label
    ]
    sqlite_only_mismatches = [
        row
        for row in db_rows
        if row.get("Status") == "ticker_mismatch" and row.get("Database") != primary_label
    ]
    print(
        f"Validation complete. file_match={len(matches)} file_mismatch={len(mismatches)} "
        f"db_mismatch={len(primary_db_mismatches)} "
        f"secondary_db_mismatch={len(sqlite_only_mismatches)}. Saved '{report_path}'."
    )
    for item in results:
        if item.status in {"match", "skipped_missing_baseline", "skipped_no_output", "skipped_not_replayed"}:
            continue
        print(f"  {item.name}: {item.status} — {item.message}")
    if sqlite_only_mismatches and not primary_db_mismatches:
        print(
            "Secondary database ticker diffs are informational when PostgreSQL is the "
            "live store (leftover local SQLite membership rows)."
        )
    failed = bool(mismatches or primary_db_mismatches or live_called)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
