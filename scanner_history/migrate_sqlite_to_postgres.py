"""Copy scanner history from SQLite into PostgreSQL.

Usage:
    python -m scanner_history.migrate_sqlite_to_postgres \\
        --source scanner_history/scanner_history.sqlite3 \\
        --target "$TRADING_DATABASE_URL"

Migrate only Combined Option Spread Analysis:
    python -m scanner_history.migrate_sqlite_to_postgres \\
        --source scanner_history/scanner_history.sqlite3 \\
        --target "$TRADING_DATABASE_URL" \\
        --scanner combined-option-v8

``--scanner`` accepts either a scanner_id (for example ``combined-option-v8``)
or a display name (for example ``Combined Option Spread Analysis``).
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

# Friendly titles used by the dashboard when the SQLite row only stores the id.
KNOWN_SCANNER_ALIASES = {
    "combined option spread analysis": "combined-option-v8",
    "bullish bias nifty 500": "bullish-bias-nifty500",
    "bearish bias nifty 500": "bearish-bias-nifty500",
}


def resolve_scanner_id(source: sqlite3.Connection, scanner: str) -> str:
    """Resolve a scanner_id or display name to the canonical scanner_id."""
    needle = scanner.strip()
    if not needle:
        raise ValueError("scanner filter must not be empty")

    by_id = source.execute(
        "SELECT scanner_id FROM scanners WHERE scanner_id = ?",
        (needle,),
    ).fetchone()
    if by_id:
        return str(by_id[0])

    matches = source.execute(
        "SELECT scanner_id, display_name FROM scanners "
        "WHERE lower(COALESCE(display_name, '')) = lower(?)",
        (needle,),
    ).fetchall()
    if len(matches) == 1:
        return str(matches[0][0])
    if len(matches) > 1:
        ids = ", ".join(row[0] for row in matches)
        raise ValueError(
            f"Display name '{needle}' matches multiple scanners: {ids}"
        )

    alias = KNOWN_SCANNER_ALIASES.get(needle.lower())
    if alias:
        by_alias = source.execute(
            "SELECT scanner_id FROM scanners WHERE scanner_id = ?",
            (alias,),
        ).fetchone()
        if by_alias:
            return str(by_alias[0])

    known = source.execute(
        "SELECT scanner_id, COALESCE(display_name, '') FROM scanners "
        "ORDER BY scanner_id"
    ).fetchall()
    available = ", ".join(
        f"{scanner_id}" + (f" ({display})" if display else "")
        for scanner_id, display in known
    ) or "(none)"
    raise ValueError(
        f"Unknown scanner '{needle}'. Available scanners: {available}"
    )


def table_columns(source: sqlite3.Connection, table: str) -> list[str]:
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})").fetchall()]
    if not columns:
        raise ValueError(f"SQLite source is missing table '{table}'")
    return columns


def select_rows(
    source: sqlite3.Connection,
    table: str,
    columns: list[str],
    scanner_id: str | None,
) -> list[tuple]:
    """Return rows for ``table``, optionally filtered to one scanner."""
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    if scanner_id is None:
        return source.execute(f"SELECT {quoted_columns} FROM {table}").fetchall()

    if table == "meta":
        return source.execute(f"SELECT {quoted_columns} FROM meta").fetchall()

    if table == "scanners":
        return source.execute(
            f'SELECT {quoted_columns} FROM scanners WHERE scanner_id = ?',
            (scanner_id,),
        ).fetchall()

    if table == "scan_runs":
        return source.execute(
            f'SELECT {quoted_columns} FROM scan_runs WHERE scanner_id = ?',
            (scanner_id,),
        ).fetchall()

    if table == "stock_scanner_daily":
        return source.execute(
            f'SELECT {quoted_columns} FROM stock_scanner_daily WHERE scanner_id = ?',
            (scanner_id,),
        ).fetchall()

    if table == "scanner_result_detail":
        return source.execute(
            f"""
            SELECT {quoted_columns}
            FROM scanner_result_detail
            WHERE run_id IN (
                SELECT run_id FROM scan_runs WHERE scanner_id = ?
            )
            """,
            (scanner_id,),
        ).fetchall()

    if table == "stocks":
        return source.execute(
            f"""
            SELECT {quoted_columns}
            FROM stocks
            WHERE symbol IN (
                SELECT symbol FROM stock_scanner_daily WHERE scanner_id = ?
                UNION
                SELECT d.symbol
                FROM scanner_result_detail d
                JOIN scan_runs r ON r.run_id = d.run_id
                WHERE r.scanner_id = ?
            )
            """,
            (scanner_id, scanner_id),
        ).fetchall()

    raise ValueError(f"Unsupported table for scanner-filtered migration: {table}")


def migrate(
    source_path: str | Path,
    target_url: str,
    *,
    scanner: str | None = None,
) -> tuple[dict[str, int], str | None]:
    """Copy history rows, preserving IDs and ignoring existing rows.

    When ``scanner`` is set, only that scanner's membership history is copied
    (plus referenced stocks and shared meta). ``scanner`` may be a scanner_id
    or display name.

    Returns ``(row_counts_by_table, resolved_scanner_id)``.
    """
    source = sqlite3.connect(source_path)
    target = db.connect(target_url)
    counts: dict[str, int] = {}
    try:
        scanner_id = resolve_scanner_id(source, scanner) if scanner else None
        for table in TABLES:
            columns = table_columns(source, table)
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            rows = select_rows(source, table, columns, scanner_id)
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
        return counts, scanner_id
    finally:
        source.close()
        target.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, help="PostgreSQL connection URL.")
    parser.add_argument(
        "--scanner",
        default=None,
        help=(
            "Optional scanner_id or display name filter. "
            "Example: combined-option-v8 or 'Combined Option Spread Analysis'."
        ),
    )
    args = parser.parse_args()
    counts, resolved = migrate(args.source, args.target, scanner=args.scanner)
    print("Migration complete:")
    if resolved:
        print(f"  scanner filter: {resolved}")
    for table, count in counts.items():
        print(f"  {table}: {count} source row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
