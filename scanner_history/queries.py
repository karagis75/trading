"""Query helpers for added/dropped/continuing scanner membership."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any, Iterable

from .normalize import normalize_symbol


def _rows(connection: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(connection.execute(sql, tuple(params)))


def stock_history(connection: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    ticker = normalize_symbol(symbol) or str(symbol or "").strip().upper()
    return _rows(
        connection,
        """
        SELECT d.*, s.display_name
        FROM stock_scanner_daily d
        JOIN scan_runs r ON r.run_id = d.run_id
        LEFT JOIN scanners s ON s.scanner_id = d.scanner_id
        WHERE d.symbol = ? AND r.is_canonical = 1
        ORDER BY d.scan_date DESC, d.scanner_id
        """,
        (ticker,),
    )


def changes(
    connection: sqlite3.Connection,
    *,
    event: str | None = None,
    start: date | None = None,
    end: date | None = None,
    scanner_id: str | None = None,
    min_streak: int = 0,
) -> list[sqlite3.Row]:
    clauses = ["r.is_canonical = 1"]
    params: list[Any] = []
    if event:
        clauses.append("d.change_type = ?")
        params.append(event.upper())
    if start:
        clauses.append("d.scan_date >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append("d.scan_date <= ?")
        params.append(end.isoformat())
    if scanner_id:
        clauses.append("d.scanner_id = ?")
        params.append(scanner_id)
    if min_streak:
        clauses.append("d.current_streak_scans >= ?")
        params.append(min_streak)
    where = " AND ".join(clauses)
    return _rows(
        connection,
        f"""
        SELECT d.*
        FROM stock_scanner_daily d
        JOIN scan_runs r ON r.run_id = d.run_id
        WHERE {where}
        ORDER BY d.scan_date, d.scanner_id, d.symbol
        """,
        params,
    )


def active(
    connection: sqlite3.Connection,
    *,
    min_streak: int = 1,
    as_of: date | None = None,
    scanner_id: str | None = None,
) -> list[sqlite3.Row]:
    as_of = as_of or date.today()
    clauses = ["r.is_canonical = 1", "d.picked = 1", "d.scan_date = ?"]
    params: list[Any] = [as_of.isoformat()]
    if min_streak:
        clauses.append("d.current_streak_scans >= ?")
        params.append(min_streak)
    if scanner_id:
        clauses.append("d.scanner_id = ?")
        params.append(scanner_id)
    where = " AND ".join(clauses)
    return _rows(
        connection,
        f"""
        SELECT d.*
        FROM stock_scanner_daily d
        JOIN scan_runs r ON r.run_id = d.run_id
        WHERE {where}
        ORDER BY d.scanner_id, d.current_streak_scans DESC, d.symbol
        """,
        params,
    )


def scanner_history(
    connection: sqlite3.Connection,
    scanner_id: str,
    *,
    days: int = 30,
    as_of: date | None = None,
) -> list[sqlite3.Row]:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=days)
    return changes(connection, start=start, end=as_of, scanner_id=scanner_id)


def latest_scan_date(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT MAX(scan_date) AS scan_date FROM scan_runs WHERE is_canonical = 1 AND status = 'success'"
    ).fetchone()
    return row["scan_date"] if row and row["scan_date"] else None


def week_bounds(as_of: date) -> tuple[date, date]:
    start = as_of - timedelta(days=as_of.weekday())
    return start, as_of


def list_scanners(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return _rows(
        connection,
        """
        SELECT scanner_id, display_name, role, output_format, source_sheet, symbol_column, enabled
        FROM scanners
        ORDER BY scanner_id
        """,
    )


def scanner_index(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Latest canonical run summary for every known scanner."""
    rows = _rows(
        connection,
        """
        SELECT
            s.scanner_id,
            s.display_name,
            s.role,
            s.enabled,
            r.scan_date,
            r.status,
            r.result_count,
            r.error_message
        FROM scanners s
        LEFT JOIN scan_runs r
            ON r.scanner_id = s.scanner_id
            AND r.is_canonical = 1
            AND r.scan_date = (
                SELECT MAX(r2.scan_date)
                FROM scan_runs r2
                WHERE r2.scanner_id = s.scanner_id AND r2.is_canonical = 1
            )
        ORDER BY s.scanner_id
        """,
    )
    return [dict(row) for row in rows]


def scanner_dates(
    connection: sqlite3.Connection,
    scanner_id: str,
    *,
    limit: int = 6,
) -> list[str]:
    """Most recent scan dates for one scanner, newest first.

    Includes failed/skipped/indeterminate days so the UI can show status banners.
    """
    rows = _rows(
        connection,
        """
        SELECT DISTINCT scan_date
        FROM scan_runs
        WHERE scanner_id = ?
        ORDER BY scan_date DESC
        LIMIT ?
        """,
        (scanner_id, limit),
    )
    return [row["scan_date"] for row in rows if row["scan_date"]]


def recent_scan_dates(connection: sqlite3.Connection, *, limit: int = 6) -> list[str]:
    """Most recent distinct scan dates across all scanners, newest first."""
    rows = _rows(
        connection,
        """
        SELECT DISTINCT scan_date
        FROM scan_runs
        WHERE is_canonical = 1
        ORDER BY scan_date DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [row["scan_date"] for row in rows if row["scan_date"]]


def day_statuses(connection: sqlite3.Connection, scan_date: str | date) -> list[dict[str, Any]]:
    """Per-scanner status for a single day (latest revision when no canonical success)."""
    day = scan_date.isoformat() if isinstance(scan_date, date) else str(scan_date)
    rows = _rows(
        connection,
        """
        SELECT
            s.scanner_id,
            s.display_name,
            s.role,
            r.status,
            r.result_count,
            r.error_message,
            r.run_id
        FROM scanners s
        LEFT JOIN scan_runs r
            ON r.scanner_id = s.scanner_id
            AND r.scan_date = ?
            AND r.revision = (
                SELECT MAX(r2.revision)
                FROM scan_runs r2
                WHERE r2.scanner_id = s.scanner_id AND r2.scan_date = ?
            )
        ORDER BY s.scanner_id
        """,
        (day, day),
    )
    return [dict(row) for row in rows]


def scanner_day_run(
    connection: sqlite3.Connection,
    scanner_id: str,
    scan_date: str | date,
) -> dict[str, Any] | None:
    """Best run metadata for one scanner on one day.

    Prefers the canonical successful run; otherwise returns the latest revision
    so skipped/failed days still render a status banner.
    """
    day = scan_date.isoformat() if isinstance(scan_date, date) else str(scan_date)
    row = connection.execute(
        """
        SELECT r.*, s.display_name, s.role
        FROM scan_runs r
        LEFT JOIN scanners s ON s.scanner_id = r.scanner_id
        WHERE r.scanner_id = ? AND r.scan_date = ?
        ORDER BY r.is_canonical DESC, r.revision DESC
        LIMIT 1
        """,
        (scanner_id, day),
    ).fetchone()
    return dict(row) if row else None


def scanner_day_rows(
    connection: sqlite3.Connection,
    scanner_id: str,
    scan_date: str | date,
) -> list[dict[str, Any]]:
    """Picked/dropped detail rows for a scanner day, with metadata.

    When a symbol has multiple opportunity rows in ``scanner_result_detail``
    (e.g. Combined Option Spread Analysis), each detail record is returned so
    the UI can mirror the Excel workbook row-for-row, including per-row
    Validation Pass highlighting.
    """
    day = scan_date.isoformat() if isinstance(scan_date, date) else str(scan_date)
    run = scanner_day_run(connection, scanner_id, day)
    if not run or not run.get("run_id"):
        return []
    run_id = run["run_id"]
    # For downstream scanners (e.g. combined-option-v8) every opportunity row is
    # relevant — order by score descending so best scores appear first.
    # For primary scanners keep the change-type ordering (ADDED first) then alpha.
    is_downstream = (run.get("role") or "primary_scanner") == "downstream"
    if is_downstream:
        daily = _rows(
            connection,
            """
            SELECT d.*, st.company_name
            FROM stock_scanner_daily d
            LEFT JOIN stocks st ON st.symbol = d.symbol
            WHERE d.run_id = ? AND d.scanner_id = ?
              AND (d.picked = 1 OR d.change_type IN ('DROPPED', 'UNIVERSE_REMOVED'))
            ORDER BY COALESCE(d.confidence, 0) DESC, d.symbol
            """,
            (run_id, scanner_id),
        )
    else:
        daily = _rows(
            connection,
            """
            SELECT d.*, st.company_name
            FROM stock_scanner_daily d
            LEFT JOIN stocks st ON st.symbol = d.symbol
            WHERE d.run_id = ? AND d.scanner_id = ?
              AND (d.picked = 1 OR d.change_type IN ('DROPPED', 'UNIVERSE_REMOVED'))
            ORDER BY
                CASE change_type
                    WHEN 'ADDED' THEN 0
                    WHEN 'READED' THEN 1
                    WHEN 'CONTINUED' THEN 2
                    WHEN 'DROPPED' THEN 3
                    ELSE 4
                END,
                symbol
            """,
            (run_id, scanner_id),
        )
    details = _rows(
        connection,
        """
        SELECT *
        FROM scanner_result_detail
        WHERE run_id = ?
        ORDER BY symbol, record_number
        """,
        (run_id,),
    )
    detail_by_symbol: dict[str, list[sqlite3.Row]] = {}
    for detail in details:
        detail_by_symbol.setdefault(detail["symbol"], []).append(detail)

    rows: list[dict[str, Any]] = []
    for daily_row in daily:
        matches = detail_by_symbol.get(daily_row["symbol"]) or []
        if not matches:
            payload = dict(daily_row)
            payload["metadata_json"] = None
            payload["record_count"] = 0
            payload["score"] = None
            payload["record_number"] = None
            rows.append(payload)
            continue
        for detail in matches:
            payload = dict(daily_row)
            payload["signal_date"] = detail["signal_date"] or payload.get("signal_date")
            payload["classification"] = detail["classification"] or payload.get("classification")
            payload["confidence"] = (
                detail["confidence"] if detail["confidence"] is not None else payload.get("confidence")
            )
            payload["score"] = detail["score"]
            payload["metadata_json"] = detail["metadata_json"]
            payload["record_count"] = len(matches)
            payload["record_number"] = detail["record_number"]
            rows.append(payload)
    return rows


def stock_in_any_scanner(connection: sqlite3.Connection, symbol: str) -> bool:
    ticker = normalize_symbol(symbol) or str(symbol or "").strip().upper()
    if not ticker:
        return False
    row = connection.execute(
        """
        SELECT 1 AS found
        FROM stock_scanner_daily d
        JOIN scan_runs r ON r.run_id = d.run_id
        WHERE d.symbol = ? AND d.picked = 1 AND r.is_canonical = 1
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    return bool(row)


def search_stocks(
    connection: sqlite3.Connection,
    text: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search symbols/company names among stocks picked by at least one scanner."""
    needle = str(text or "").strip().upper()
    if not needle:
        return []
    pattern = f"%{needle}%"
    rows = _rows(
        connection,
        """
        SELECT DISTINCT
            st.symbol,
            st.company_name,
            st.industry
        FROM stocks st
        JOIN stock_scanner_daily d ON d.symbol = st.symbol
        JOIN scan_runs r ON r.run_id = d.run_id
        WHERE d.picked = 1
          AND r.is_canonical = 1
          AND (UPPER(st.symbol) LIKE ? OR UPPER(COALESCE(st.company_name, '')) LIKE ?)
        ORDER BY st.symbol
        LIMIT ?
        """,
        (pattern, pattern, limit),
    )
    return [dict(row) for row in rows]


def stock_info(connection: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    ticker = normalize_symbol(symbol) or str(symbol or "").strip().upper()
    row = connection.execute(
        "SELECT * FROM stocks WHERE symbol = ?",
        (ticker,),
    ).fetchone()
    return dict(row) if row else None


def stock_summary(
    connection: sqlite3.Connection,
    symbol: str,
    *,
    as_of: str | date | None = None,
) -> list[dict[str, Any]]:
    """Latest-day membership row per scanner for one stock."""
    ticker = normalize_symbol(symbol) or str(symbol or "").strip().upper()
    if as_of is None:
        day = latest_scan_date(connection)
    elif isinstance(as_of, date):
        day = as_of.isoformat()
    else:
        day = str(as_of)
    if not day:
        return []
    rows = _rows(
        connection,
        """
        SELECT
            d.*,
            s.display_name,
            s.role
        FROM stock_scanner_daily d
        JOIN scan_runs r ON r.run_id = d.run_id
        LEFT JOIN scanners s ON s.scanner_id = d.scanner_id
        WHERE d.symbol = ? AND d.scan_date = ? AND r.is_canonical = 1
        ORDER BY d.scanner_id
        """,
        (ticker, day),
    )
    return [dict(row) for row in rows]


def stock_change_matrix(
    connection: sqlite3.Connection,
    symbol: str,
    *,
    days: int = 6,
) -> dict[str, Any]:
    """Build a day x scanner change_type matrix for the last N scan dates."""
    ticker = normalize_symbol(symbol) or str(symbol or "").strip().upper()
    dates = recent_scan_dates(connection, limit=days)
    scanners = [dict(row) for row in list_scanners(connection)]
    cells: dict[str, dict[str, dict[str, Any]]] = {day: {} for day in dates}
    if dates:
        placeholders = ",".join("?" for _ in dates)
        rows = _rows(
            connection,
            f"""
            SELECT d.scan_date, d.scanner_id, d.change_type, d.picked,
                   d.current_streak_scans, d.classification, d.confidence
            FROM stock_scanner_daily d
            JOIN scan_runs r ON r.run_id = d.run_id
            WHERE d.symbol = ? AND r.is_canonical = 1 AND d.scan_date IN ({placeholders})
            """,
            (ticker, *dates),
        )
        for row in rows:
            cells[row["scan_date"]][row["scanner_id"]] = dict(row)
    return {
        "symbol": ticker,
        "dates": dates,
        "scanners": scanners,
        "cells": cells,
    }
