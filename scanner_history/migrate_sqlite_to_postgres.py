"""Copy scanner history from SQLite into PostgreSQL.

Usage:
    python -m scanner_history.migrate_sqlite_to_postgres \
        --source scanner_history/scanner_history.sqlite3 \
        --target "$TRADING_DATABASE_URL"
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from . import db

TABLES = (
    "meta",
    "stocks",
    "scanners",
    "scan_runs",
    "scanner_result_detail",
    "stock_scanner_daily",
)


def migrate(source_path: str | Path, target_url: str) -> dict[str, int]:
    """Copy all history rows, preserving IDs and ignoring existing rows."""
    source = sqlite3.connect(source_path)
    target = db.connect(target_url)
    counts: dict[str, int] = {}
    try:
        for table in TABLES:
            columns = [
                row[1]
                for row in source.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            if not columns:
                raise ValueError(f"SQLite source is missing table '{table}'")
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            rows = source.execute(f"SELECT {quoted_columns} FROM {table}").fetchall()
            if rows:
                target.executemany(
                    f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) "
                    "ON CONFLICT DO NOTHING",
                    rows,
                )
            counts[table] = len(rows)

        target.execute(
            "SELECT setval(pg_get_serial_sequence('scanner_result_detail', 'id'), "
            "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM scanner_result_detail"
        ).fetchone()
        target.commit()
        return counts
    finally:
        source.close()
        target.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, help="PostgreSQL connection URL.")
    args = parser.parse_args()
    counts = migrate(args.source, args.target)
    print("Migration complete:")
    for table, count in counts.items():
        print(f"  {table}: {count} source row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
