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
