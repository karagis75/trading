"""Database schema and connection helpers for scanner membership history.

SQLite remains supported for local tests and backward compatibility. Set
TRADING_DATABASE_URL to a PostgreSQL URL for scheduled production runs.
"""

from __future__ import annotations

import sqlite3
import re
from typing import Any
from pathlib import Path

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY,
    company_name TEXT,
    industry TEXT,
    series TEXT,
    isin TEXT,
    active_in_universe INTEGER NOT NULL DEFAULT 1,
    first_universe_date TEXT,
    last_universe_date TEXT
);

CREATE TABLE IF NOT EXISTS scanners (
    scanner_id TEXT PRIMARY KEY,
    display_name TEXT,
    role TEXT NOT NULL,
    output_format TEXT,
    source_sheet TEXT,
    symbol_column TEXT,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    scanner_id TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL,
    output_path TEXT,
    output_hash TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    is_canonical INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (scanner_id) REFERENCES scanners(scanner_id)
);

CREATE TABLE IF NOT EXISTS scanner_result_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    record_number INTEGER NOT NULL DEFAULT 1,
    signal_date TEXT,
    classification TEXT,
    confidence REAL,
    score REAL,
    metadata_json TEXT,
    FOREIGN KEY (run_id) REFERENCES scan_runs(run_id)
);

CREATE TABLE IF NOT EXISTS stock_scanner_daily (
    run_id TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    scanner_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    in_universe INTEGER NOT NULL,
    picked INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    current_streak_scans INTEGER NOT NULL DEFAULT 0,
    current_streak_calendar_days INTEGER NOT NULL DEFAULT 0,
    streak_start_date TEXT,
    ended_streak_scans INTEGER NOT NULL DEFAULT 0,
    total_times_picked INTEGER NOT NULL DEFAULT 0,
    first_picked_date TEXT,
    last_picked_date TEXT,
    signal_date TEXT,
    classification TEXT,
    confidence REAL,
    source_file TEXT,
    source_sheet TEXT,
    PRIMARY KEY (run_id, scanner_id, symbol),
    FOREIGN KEY (run_id) REFERENCES scan_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_scanner_date
    ON scan_runs(scanner_id, scan_date, is_canonical);
CREATE INDEX IF NOT EXISTS idx_daily_lookup
    ON stock_scanner_daily(scanner_id, symbol, scan_date);
CREATE INDEX IF NOT EXISTS idx_daily_change
    ON stock_scanner_daily(scan_date, change_type);
CREATE INDEX IF NOT EXISTS idx_detail_symbol
    ON scanner_result_detail(symbol, run_id);
"""

POSTGRES_SCHEMA = SCHEMA.replace(
    "id INTEGER PRIMARY KEY AUTOINCREMENT",
    "id BIGSERIAL PRIMARY KEY",
).replace(
    "INTEGER PRIMARY KEY AUTOINCREMENT",
    "BIGSERIAL PRIMARY KEY",
)


class PostgresConnection:
    """Small DB-API compatibility layer for the existing tracker/query code."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        try:
            return self._connection.execute(sql.replace("?", "%s"), params)
        except Exception:
            self._rollback_quietly()
            raise

    def executemany(self, sql: str, params):
        try:
            return self._connection.executemany(sql.replace("?", "%s"), params)
        except Exception:
            self._rollback_quietly()
            raise

    def _rollback_quietly(self) -> None:
        """Best-effort rollback so a failed statement doesn't poison this
        connection for whatever runs on it next. Safe to call even when
        autocommit is enabled (nothing to roll back, and any error here is
        swallowed rather than masking the original exception)."""
        try:
            self._connection.rollback()
        except Exception:
            pass

    def commit(self) -> None:
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


def _initialize_postgres(connection: PostgresConnection) -> None:
    # PostgreSQL does not support SQLite's executescript(); execute each
    # statement separately while preserving the shared schema definition.
    for statement in POSTGRES_SCHEMA.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)
    current = connection.execute("SELECT value FROM meta WHERE key = ?", ("schema_version",)).fetchone()
    if current is None:
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
    connection.commit()


def connect(path: str | Path):
    """Connect to SQLite paths or PostgreSQL URLs.

    PostgreSQL URLs use the form
    ``postgresql://user:password@host:5432/database``.
    """
    value = str(path)
    if value.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL support requires psycopg. Install it with "
                "'python -m pip install \"psycopg[binary]\"'."
            ) from exc
        # autocommit=True: each statement completes independently, so a single
        # failing query cannot leave the connection in an aborted-transaction
        # state that blocks every later query. This matters because the
        # dashboard keeps a connection open per thread across many requests
        # (see webapp/perf.py) instead of opening a fresh one each time.
        connection = PostgresConnection(psycopg.connect(value, row_factory=dict_row, autocommit=True))
        _initialize_postgres(connection)
        return connection

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    initialize(connection)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    current = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if current is None:
        connection.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
    connection.commit()
