"""Scanner view routes."""

from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, url_for

from ..helpers import (
    build_table_columns,
    change_badge,
    display_name_for,
    enrich_scanner_index,
    get_config,
    get_db,
    queries,
    row_cells,
    status_css,
)

scanners_bp = Blueprint("scanners", __name__, url_prefix="/scanners")


@scanners_bp.route("/")
def scanner_index():
    connection = get_db()
    cfg = get_config()
    scanners = enrich_scanner_index(
        queries.scanner_index(connection),
        [job.name for job in cfg.jobs],
    )
    jobs_by_name = {job.name: job for job in cfg.jobs}
    for row in scanners:
        job = jobs_by_name.get(row["scanner_id"])
        row["job_enabled"] = job.enabled if job else bool(row.get("enabled", 1))
        row["role"] = row.get("role") or (job.role if job else "primary_scanner")
    return render_template(
        "scanners/index.html",
        scanners=scanners,
        latest_date=queries.latest_scan_date(connection),
    )


@scanners_bp.route("/<scanner_id>")
def scanner_latest(scanner_id: str):
    connection = get_db()
    dates = queries.scanner_dates(connection, scanner_id, limit=1)
    if not dates:
        # Fall back to the scanner index with a flash-like empty page.
        return render_template(
            "scanners/day.html",
            scanner_id=scanner_id,
            title=display_name_for(scanner_id),
            scan_date=None,
            dates=[],
            run=None,
            rows=[],
            columns=[],
            table_rows=[],
            status=None,
            status_css="status-missing",
            empty_reason="No scan history for this scanner yet.",
        )
    return redirect(url_for("scanners.scanner_day", scanner_id=scanner_id, scan_date=dates[0]))


@scanners_bp.route("/<scanner_id>/<scan_date>")
def scanner_day(scanner_id: str, scan_date: str):
    connection = get_db()
    cfg = get_config()
    known = {job.name for job in cfg.jobs} | {row["scanner_id"] for row in queries.list_scanners(connection)}
    if scanner_id not in known:
        abort(404)

    dates = queries.scanner_dates(connection, scanner_id, limit=6)
    run = queries.scanner_day_run(connection, scanner_id, scan_date)
    title = display_name_for(scanner_id, (run or {}).get("display_name"))
    status = (run or {}).get("status")
    rows = queries.scanner_day_rows(connection, scanner_id, scan_date) if run and status == "success" else []
    columns = build_table_columns(scanner_id, rows) if rows else preferred_fallback(scanner_id)
    table_rows = []
    for row in rows:
        badge = change_badge(row.get("change_type"), row.get("current_streak_scans"))
        table_rows.append(
            {
                "symbol": row.get("symbol"),
                "badge": badge,
                "cells": row_cells(row, columns),
                "picked": bool(row.get("picked")),
            }
        )

    empty_reason = None
    if run is None:
        empty_reason = f"No run recorded for {scan_date}."
    elif status != "success":
        empty_reason = (run.get("error_message") or f"Run status: {status}") if run else None
    elif not rows:
        empty_reason = "No qualifying setups this day."

    return render_template(
        "scanners/day.html",
        scanner_id=scanner_id,
        title=title,
        scan_date=scan_date,
        dates=dates,
        run=run,
        rows=rows,
        columns=columns,
        table_rows=table_rows,
        status=status,
        status_css=status_css(status),
        empty_reason=empty_reason,
    )


def preferred_fallback(scanner_id: str) -> list[str]:
    from ..config import preferred_columns

    columns = preferred_columns(scanner_id)
    return columns or ["Ticker"]
