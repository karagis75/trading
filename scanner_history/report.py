"""Write the dated membership-change workbook."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from . import queries


def _frame(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(row) for row in rows])


def write_daily_report(connection, output_path: str | Path, as_of: date) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    week_start, week_end = queries.week_bounds(as_of)
    sheets = {
        "Added_Today": _frame(queries.changes(connection, event="ADDED", start=as_of, end=as_of)),
        "Readded_Today": _frame(queries.changes(connection, event="READED", start=as_of, end=as_of)),
        "Dropped_Today": _frame(queries.changes(connection, event="DROPPED", start=as_of, end=as_of)),
        "Continuing": _frame(queries.changes(connection, event="CONTINUED", start=as_of, end=as_of)),
        "Weekly_Added": _frame(queries.changes(connection, event="ADDED", start=week_start, end=week_end)),
        "Weekly_Dropped": _frame(queries.changes(connection, event="DROPPED", start=week_start, end=week_end)),
        "Stock_Summary": _frame(queries.active(connection, min_streak=1, as_of=as_of)),
        "Scanner_Summary": _scanner_summary(connection, as_of),
        "Failed_Scans": _failed_scans(connection, as_of),
    }
    with pd.ExcelWriter(destination, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
    return destination


def _scanner_summary(connection, as_of: date) -> pd.DataFrame:
    rows = connection.execute(
        """
        SELECT
            d.scanner_id,
            SUM(CASE WHEN d.change_type = 'ADDED' THEN 1 ELSE 0 END) AS added,
            SUM(CASE WHEN d.change_type = 'READED' THEN 1 ELSE 0 END) AS readded,
            SUM(CASE WHEN d.change_type = 'CONTINUED' THEN 1 ELSE 0 END) AS continued,
            SUM(CASE WHEN d.change_type = 'DROPPED' THEN 1 ELSE 0 END) AS dropped,
            SUM(CASE WHEN d.picked = 1 THEN 1 ELSE 0 END) AS picked
        FROM stock_scanner_daily d
        JOIN scan_runs r ON r.run_id = d.run_id
        WHERE r.is_canonical = 1 AND d.scan_date = ?
        GROUP BY d.scanner_id
        ORDER BY d.scanner_id
        """,
        (as_of.isoformat(),),
    ).fetchall()
    return _frame(rows)


def _failed_scans(connection, as_of: date) -> pd.DataFrame:
    rows = connection.execute(
        """
        SELECT scanner_id, scan_date, status, error_message, output_path
        FROM scan_runs
        WHERE scan_date = ? AND status IN ('failed', 'skipped', 'indeterminate')
        ORDER BY scanner_id, revision
        """,
        (as_of.isoformat(),),
    ).fetchall()
    return _frame(rows)
