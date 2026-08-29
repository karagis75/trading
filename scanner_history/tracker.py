"""Ingest scanner outputs and maintain stockwise membership history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import db
from .adapters import HitRecord, ParsedOutput, parse_scanner_output
from .normalize import UniverseStock, load_universe, normalize_symbol

CHANGE_ADDED = "ADDED"
CHANGE_READED = "READED"
CHANGE_CONTINUED = "CONTINUED"
CHANGE_DROPPED = "DROPPED"
CHANGE_NOT_PICKED = "NOT_PICKED"
CHANGE_UNIVERSE_REMOVED = "UNIVERSE_REMOVED"
CHANGE_INDETERMINATE = "INDETERMINATE"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_INDETERMINATE = "indeterminate"


def file_hash(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _calendar_days(start: str | None, end: str) -> int:
    if not start:
        return 0
    begin = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    return (finish - begin).days + 1


@dataclass
class TrackingConfig:
    enabled: bool = False
    role: str = "primary_scanner"
    format: str = "xlsx"
    sheet: str | None = None
    symbol_column: str = "Ticker"
    membership_filter: str | None = None
    signal_date_column: str | None = None
    classification_column: str | None = None
    confidence_column: str | None = None


@dataclass
class IngestResult:
    run_id: str
    status: str
    result_count: int = 0
    error: str | None = None


class MembershipTracker:
    def __init__(
        self,
        connection: sqlite3.Connection,
        universe_path: str | Path,
    ) -> None:
        self.connection = connection
        self.universe_path = Path(universe_path)

    @classmethod
    def from_path(cls, db_path: str | Path, universe_path: str | Path) -> "MembershipTracker":
        return cls(db.connect(db_path), Path(universe_path))

    def close(self) -> None:
        self.connection.close()

    def upsert_scanner(self, scanner_id: str, tracking: TrackingConfig, display_name: str | None = None) -> None:
        self.connection.execute(
            """
            INSERT INTO scanners(scanner_id, display_name, role, output_format, source_sheet, symbol_column, enabled)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(scanner_id) DO UPDATE SET
                display_name=excluded.display_name,
                role=excluded.role,
                output_format=excluded.output_format,
                source_sheet=excluded.source_sheet,
                symbol_column=excluded.symbol_column,
                enabled=1
            """,
            (
                scanner_id,
                display_name or scanner_id,
                tracking.role,
                tracking.format,
                tracking.sheet,
                tracking.symbol_column,
            ),
        )
        self.connection.commit()

    def refresh_universe(self, scan_date: date) -> list[UniverseStock]:
        stocks = load_universe(self.universe_path, scan_date)
        iso = scan_date.isoformat()
        seen = {stock.symbol for stock in stocks}
        for stock in stocks:
            existing = self.connection.execute(
                "SELECT first_universe_date FROM stocks WHERE symbol = ?",
                (stock.symbol,),
            ).fetchone()
            first = existing["first_universe_date"] if existing else iso
            self.connection.execute(
                """
                INSERT INTO stocks(symbol, company_name, industry, series, isin, active_in_universe, first_universe_date, last_universe_date)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    company_name=excluded.company_name,
                    industry=excluded.industry,
                    series=excluded.series,
                    isin=excluded.isin,
                    active_in_universe=1,
                    last_universe_date=excluded.last_universe_date
                """,
                (
                    stock.symbol,
                    stock.company_name,
                    stock.industry,
                    stock.series,
                    stock.isin,
                    first,
                    iso,
                ),
            )
        if seen:
            placeholders = ",".join("?" for _ in seen)
            self.connection.execute(
                f"UPDATE stocks SET active_in_universe = 0 WHERE symbol NOT IN ({placeholders})",
                tuple(seen),
            )
        self.connection.commit()
        return stocks

    def previous_canonical_run(self, scanner_id: str, scan_date: date) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM scan_runs
            WHERE scanner_id = ? AND status = ? AND is_canonical = 1 AND scan_date < ?
            ORDER BY scan_date DESC
            LIMIT 1
            """,
            (scanner_id, STATUS_SUCCESS, scan_date.isoformat()),
        ).fetchone()

    def previous_daily_map(self, run_id: str | None) -> dict[str, sqlite3.Row]:
        if not run_id:
            return {}
        rows = self.connection.execute(
            "SELECT * FROM stock_scanner_daily WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {row["symbol"]: row for row in rows}

    def _next_revision(self, scanner_id: str, scan_date: date) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) AS rev FROM scan_runs WHERE scanner_id = ? AND scan_date = ?",
            (scanner_id, scan_date.isoformat()),
        ).fetchone()
        return int(row["rev"]) + 1

    def record_run(
        self,
        *,
        scanner_id: str,
        tracking: TrackingConfig,
        scan_date: date,
        output_path: Path | None,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
        parsed: ParsedOutput | None = None,
    ) -> IngestResult:
        self.upsert_scanner(scanner_id, tracking)
        run_id = uuid.uuid4().hex
        revision = self._next_revision(scanner_id, scan_date)
        hits = parsed.hits if parsed else []
        canonical = 1 if status == STATUS_SUCCESS else 0
        if canonical:
            self.connection.execute(
                """
                UPDATE scan_runs SET is_canonical = 0
                WHERE scanner_id = ? AND scan_date = ? AND is_canonical = 1
                """,
                (scanner_id, scan_date.isoformat()),
            )
        self.connection.execute(
            """
            INSERT INTO scan_runs(
                run_id, scanner_id, scan_date, started_at, finished_at, status,
                output_path, output_hash, result_count, revision, is_canonical, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                scanner_id,
                scan_date.isoformat(),
                started_at.isoformat(timespec="seconds") if started_at else None,
                finished_at.isoformat(timespec="seconds") if finished_at else None,
                status,
                str(output_path) if output_path else None,
                file_hash(output_path) if output_path else None,
                len(parsed.symbols) if parsed else 0,
                revision,
                canonical,
                error,
            ),
        )
        if status == STATUS_SUCCESS and parsed is not None:
            self._write_details(run_id, hits)
            self._write_daily(run_id, scanner_id, scan_date, tracking, output_path, parsed)
        self.connection.commit()
        return IngestResult(run_id=run_id, status=status, result_count=len(parsed.symbols) if parsed else 0, error=error)

    def ingest_output(
        self,
        *,
        scanner_id: str,
        tracking: TrackingConfig,
        scan_date: date,
        output_path: Path,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        job_ok: bool = True,
        skipped: bool = False,
        job_message: str = "",
    ) -> IngestResult:
        if skipped:
            return self.record_run(
                scanner_id=scanner_id,
                tracking=tracking,
                scan_date=scan_date,
                output_path=output_path,
                status=STATUS_SKIPPED,
                started_at=started_at,
                finished_at=finished_at,
                error=job_message or "job skipped",
            )
        if not job_ok:
            return self.record_run(
                scanner_id=scanner_id,
                tracking=tracking,
                scan_date=scan_date,
                output_path=output_path if output_path.exists() else None,
                status=STATUS_FAILED,
                started_at=started_at,
                finished_at=finished_at,
                error=job_message or "job failed",
            )
        parsed = parse_scanner_output(
            output_path,
            fmt=tracking.format,
            sheet=tracking.sheet,
            symbol_column=tracking.symbol_column,
            membership_filter=tracking.membership_filter,
            signal_date_column=tracking.signal_date_column,
            classification_column=tracking.classification_column,
            confidence_column=tracking.confidence_column,
        )
        if parsed.error:
            return self.record_run(
                scanner_id=scanner_id,
                tracking=tracking,
                scan_date=scan_date,
                output_path=output_path,
                status=STATUS_INDETERMINATE,
                started_at=started_at,
                finished_at=finished_at,
                error=parsed.error,
                parsed=parsed,
            )
        return self.record_run(
            scanner_id=scanner_id,
            tracking=tracking,
            scan_date=scan_date,
            output_path=output_path,
            status=STATUS_SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            parsed=parsed,
        )

    def _write_details(self, run_id: str, hits: list[HitRecord]) -> None:
        counts: dict[str, int] = {}
        for hit in hits:
            counts[hit.symbol] = counts.get(hit.symbol, 0) + 1
            self.connection.execute(
                """
                INSERT INTO scanner_result_detail(
                    run_id, symbol, record_number, signal_date, classification, confidence, score, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    hit.symbol,
                    counts[hit.symbol],
                    hit.signal_date,
                    hit.classification,
                    hit.confidence,
                    hit.score,
                    json.dumps(hit.metadata, default=str),
                ),
            )

    def _write_daily(
        self,
        run_id: str,
        scanner_id: str,
        scan_date: date,
        tracking: TrackingConfig,
        output_path: Path | None,
        parsed: ParsedOutput,
    ) -> None:
        universe = self.refresh_universe(scan_date)
        universe_symbols = {stock.symbol for stock in universe}
        hit_by_symbol: dict[str, HitRecord] = {}
        for hit in parsed.hits:
            hit_by_symbol.setdefault(hit.symbol, hit)
        previous = self.previous_canonical_run(scanner_id, scan_date)
        prev_map = self.previous_daily_map(previous["run_id"] if previous else None)
        prev_picked = {symbol for symbol, row in prev_map.items() if row["picked"]}
        today_picked = set(hit_by_symbol)
        iso = scan_date.isoformat()
        if tracking.role == "primary_scanner":
            symbols = universe_symbols | today_picked | prev_picked
        else:
            symbols = today_picked | prev_picked
        source_file = str(output_path) if output_path else None
        source_sheet = tracking.sheet or parsed.sheet

        rows = []
        for symbol in sorted(symbols):
            picked = symbol in today_picked
            in_universe = symbol in universe_symbols
            prev = prev_map.get(symbol)
            was_picked = bool(prev["picked"]) if prev else False
            hit = hit_by_symbol.get(symbol)
            if not in_universe and was_picked and not picked:
                change = CHANGE_UNIVERSE_REMOVED
            elif picked and was_picked:
                change = CHANGE_CONTINUED
            elif picked and not was_picked:
                change = CHANGE_READED if prev and prev["last_picked_date"] else CHANGE_ADDED
            elif not picked and was_picked:
                change = CHANGE_DROPPED
            else:
                change = CHANGE_NOT_PICKED

            if picked:
                streak_scans = (int(prev["current_streak_scans"]) + 1) if was_picked else 1
                streak_start = prev["streak_start_date"] if was_picked and prev else iso
                total = (int(prev["total_times_picked"]) + 1) if prev else 1
                first_picked = (prev["first_picked_date"] if prev and prev["first_picked_date"] else iso)
                last_picked = iso
                ended = 0
            else:
                streak_scans = 0
                streak_start = None
                total = int(prev["total_times_picked"]) if prev else 0
                first_picked = prev["first_picked_date"] if prev else None
                last_picked = prev["last_picked_date"] if prev else None
                ended = int(prev["current_streak_scans"]) if was_picked and prev else 0

            rows.append(
                (
                    run_id,
                    iso,
                    scanner_id,
                    symbol,
                    1 if in_universe else 0,
                    1 if picked else 0,
                    change,
                    streak_scans,
                    _calendar_days(streak_start, iso) if picked else 0,
                    streak_start,
                    ended,
                    total,
                    first_picked,
                    last_picked,
                    hit.signal_date if hit else None,
                    hit.classification if hit else None,
                    hit.confidence if hit else None,
                    source_file,
                    source_sheet,
                )
            )
        self.connection.executemany(
            """
            INSERT INTO stock_scanner_daily(
                run_id, scan_date, scanner_id, symbol, in_universe, picked, change_type,
                current_streak_scans, current_streak_calendar_days, streak_start_date,
                ended_streak_scans, total_times_picked, first_picked_date, last_picked_date,
                signal_date, classification, confidence, source_file, source_sheet
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
