"""CLI for scanner membership history."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from . import db, queries, report
from .tracker import MembershipTracker, TrackingConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "scanner_history" / "scanner_history.sqlite3"
DEFAULT_UNIVERSE = REPO_ROOT / "ind_nifty500list.csv"
DEFAULT_JOBS = REPO_ROOT / "scheduler" / "jobs.json"


def _print_table(rows: Sequence[Any]) -> None:
    if not rows:
        print("No matching rows.")
        return
    frame = pd.DataFrame([dict(row) for row in rows])
    preferred = [
        "scan_date",
        "scanner_id",
        "symbol",
        "change_type",
        "picked",
        "current_streak_scans",
        "current_streak_calendar_days",
        "total_times_picked",
        "first_picked_date",
        "last_picked_date",
        "classification",
    ]
    ordered = [column for column in preferred if column in frame.columns]
    extra = [column for column in frame.columns if column not in ordered]
    print(frame.loc[:, ordered + extra].to_string(index=False))


def _write_output(rows: Sequence[Any], output: str | None) -> None:
    if not output:
        _print_table(rows)
        return
    path = Path(output)
    frame = pd.DataFrame([dict(row) for row in rows]) if rows else pd.DataFrame()
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".json":
        path.write_text(json.dumps([dict(row) for row in rows], default=str, indent=2) + "\n", encoding="utf-8")
    elif suffix == ".xlsx":
        frame.to_excel(path, index=False, engine="openpyxl")
    else:
        frame.to_csv(path, index=False)
    print(f"Wrote {len(frame)} row(s) to '{path}'.")


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    return date.fromisoformat(text)


def load_jobs_tracking(jobs_path: Path) -> dict[str, tuple[dict[str, Any], TrackingConfig]]:
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    mapping: dict[str, tuple[dict[str, Any], TrackingConfig]] = {}
    for raw in payload.get("jobs") or []:
        tracking_raw = raw.get("tracking") or {}
        if not tracking_raw.get("enabled"):
            continue
        mapping[raw["name"]] = (
            raw,
            TrackingConfig(
                enabled=True,
                role=str(tracking_raw.get("role") or "primary_scanner"),
                format=str(tracking_raw.get("format") or "xlsx"),
                sheet=tracking_raw.get("sheet"),
                symbol_column=str(tracking_raw.get("symbol_column") or "Ticker"),
                membership_filter=tracking_raw.get("membership_filter"),
                signal_date_column=tracking_raw.get("signal_date_column"),
                classification_column=tracking_raw.get("classification_column"),
                confidence_column=tracking_raw.get("confidence_column"),
            ),
        )
    return mapping


def _output_template(job: dict[str, Any]) -> str | None:
    args = list(job.get("args") or [])
    for flag, value in zip(args, args[1:]):
        if flag == "--output":
            return value
    return None


def cmd_backfill(args: argparse.Namespace) -> int:
    tracker = MembershipTracker.from_path(args.db, args.universe)
    jobs = load_jobs_tracking(Path(args.jobs))
    root = Path(args.outputs)
    imported = 0
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            scan_date = date.fromisoformat(folder.name)
        except ValueError:
            continue
        for scanner_id, (job, tracking) in jobs.items():
            template = _output_template(job)
            if not template:
                continue
            relative = template.replace("{date}", scan_date.isoformat())
            output_path = (tracker.universe_path.parent / relative).resolve() if not Path(relative).is_absolute() else Path(relative)
            if args.outputs:
                # Prefer files under the given outputs tree.
                name = Path(relative).name
                candidate = folder / name
                if candidate.exists():
                    output_path = candidate
            if not output_path.exists():
                result = tracker.ingest_output(
                    scanner_id=scanner_id,
                    tracking=tracking,
                    scan_date=scan_date,
                    output_path=output_path,
                    job_ok=True,
                    job_message="historical output missing",
                )
                print(f"{scan_date.isoformat()} {scanner_id}: {result.status} ({result.error})")
                continue
            result = tracker.ingest_output(
                scanner_id=scanner_id,
                tracking=tracking,
                scan_date=scan_date,
                output_path=output_path,
                job_ok=True,
            )
            if result.status == "success":
                imported += 1
            print(f"{scan_date.isoformat()} {scanner_id}: {result.status} hits={result.result_count}")
    print(f"Backfill complete. Successful scanner-days imported: {imported}.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    as_of = _parse_date(args.date) or date.today()
    connection = db.connect(args.db)
    destination = Path(args.output) if args.output else REPO_ROOT / "outputs" / as_of.isoformat() / "Scanner_Membership_Changes.xlsx"
    path = report.write_daily_report(connection, destination, as_of)
    print(f"Wrote membership report to '{path}'.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query and maintain scanner membership history.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    sub = parser.add_subparsers(dest="command", required=True)

    stock = sub.add_parser("stock", help="Show one stock across scanners.")
    stock.add_argument("symbol")
    stock.add_argument("--output")

    changes = sub.add_parser("changes", help="List added/dropped/continued rows.")
    changes.add_argument("--event", default=None, help="ADDED, READED, DROPPED, CONTINUED, ...")
    changes.add_argument("--date")
    changes.add_argument("--from", dest="start_date")
    changes.add_argument("--to", dest="end_date")
    changes.add_argument(
        "--this-week",
        action="store_true",
        help="Limit to Monday through --date (or today) of the current week.",
    )
    changes.add_argument("--scanner")
    changes.add_argument("--output")

    active = sub.add_parser("active", help="Stocks currently picked, optionally by streak.")
    active.add_argument("--min-streak", type=int, default=1)
    active.add_argument("--date")
    active.add_argument("--scanner")
    active.add_argument("--output")

    scanner = sub.add_parser("scanner", help="Recent history for one scanner.")
    scanner.add_argument("scanner_id")
    scanner.add_argument("--days", type=int, default=30)
    scanner.add_argument("--date")
    scanner.add_argument("--output")

    backfill = sub.add_parser("backfill", help="Import existing outputs/{date} folders.")
    backfill.add_argument("--outputs", default=str(REPO_ROOT / "outputs"))
    backfill.add_argument("--jobs", default=str(DEFAULT_JOBS))

    report_cmd = sub.add_parser("report", help="Write the dated membership workbook.")
    report_cmd.add_argument("--date")
    report_cmd.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    connection = db.connect(args.db)
    if args.command == "stock":
        _write_output(queries.stock_history(connection, args.symbol), args.output)
        return 0
    if args.command == "changes":
        as_of = _parse_date(args.date) or date.today()
        if args.this_week:
            start, end = queries.week_bounds(as_of)
        else:
            start = _parse_date(args.start_date) or _parse_date(args.date)
            end = _parse_date(args.end_date) or _parse_date(args.date)
        _write_output(
            queries.changes(
                connection,
                event=args.event,
                start=start,
                end=end,
                scanner_id=args.scanner,
            ),
            args.output,
        )
        return 0
    if args.command == "active":
        as_of = _parse_date(args.date)
        if as_of is None:
            latest = queries.latest_scan_date(connection)
            as_of = date.fromisoformat(latest) if latest else date.today()
        _write_output(
            queries.active(connection, min_streak=args.min_streak, as_of=as_of, scanner_id=args.scanner),
            args.output,
        )
        return 0
    if args.command == "scanner":
        as_of = _parse_date(args.date) or date.today()
        _write_output(
            queries.scanner_history(connection, args.scanner_id, days=args.days, as_of=as_of),
            args.output,
        )
        return 0
    if args.command == "backfill":
        return cmd_backfill(args)
    if args.command == "report":
        return cmd_report(args)
    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
