"""Scanner view routes."""

from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

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
    validation_failed,
)
from ..perf import HISTORICAL_TTL, LIVE_TTL, SHORT_TTL, cached

scanners_bp = Blueprint("scanners", __name__, url_prefix="/scanners")


@scanners_bp.route("/")
def scanner_index():
    connection = get_db()
    cfg = get_config()
    jobs_by_name = {job.name: job for job in cfg.jobs}
    scanners = cached(
        "scanner_index",
        LIVE_TTL,
        lambda: enrich_scanner_index(
            queries.scanner_index(connection),
            [job.name for job in cfg.jobs],
            jobs_by_name,
        ),
    )
    return render_template(
        "scanners/index.html",
        scanners=scanners,
        latest_date=cached("latest_scan_date", SHORT_TTL, lambda: queries.latest_scan_date(connection)),
    )


@scanners_bp.route("/<scanner_id>")
def scanner_latest(scanner_id: str):
    connection = get_db()
    dates = cached(
        f"scanner_dates:6:{scanner_id}",
        SHORT_TTL,
        lambda: queries.scanner_dates(connection, scanner_id, limit=6),
    )
    if not dates:
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
            stock_filter="",
            has_validation=False,
            is_downstream=False,
        )
    return redirect(url_for("scanners.scanner_day", scanner_id=scanner_id, scan_date=dates[0]))


@scanners_bp.route("/<scanner_id>/<scan_date>")
def scanner_day(scanner_id: str, scan_date: str):
    connection = get_db()
    cfg = get_config()

    known = cached(
        "known_scanners",
        LIVE_TTL,
        lambda: {job.name for job in cfg.jobs} | {row["scanner_id"] for row in queries.list_scanners(connection)},
    )
    if scanner_id not in known:
        abort(404)

    stock_filter = (request.args.get("stock") or "").strip().upper()

    # Date chips — use SHORT_TTL so a new day appears within 1 min of run finish.
    dates = cached(
        f"scanner_dates:6:{scanner_id}",
        SHORT_TTL,
        lambda: queries.scanner_dates(connection, scanner_id, limit=6),
    )

    # Run metadata — historical days are immutable, use HISTORICAL_TTL.
    from datetime import date as _date
    try:
        _date.fromisoformat(scan_date)
        is_historical = scan_date != cached("latest_scan_date", SHORT_TTL, lambda: queries.latest_scan_date(connection))
    except ValueError:
        is_historical = False

    run_ttl = HISTORICAL_TTL if is_historical else SHORT_TTL
    run = cached(
        f"run:{scanner_id}:{scan_date}",
        run_ttl,
        lambda: queries.scanner_day_run(connection, scanner_id, scan_date),
    )

    title = display_name_for(scanner_id, (run or {}).get("display_name"))
    status = (run or {}).get("status")
    is_downstream = (run.get("role") if run else None) == "downstream"

    row_ttl = HISTORICAL_TTL if is_historical else SHORT_TTL
    rows = cached(
        f"day_rows:{scanner_id}:{scan_date}",
        row_ttl,
        lambda: queries.scanner_day_rows(connection, scanner_id, scan_date) if run and status == "success" else [],
    )

    columns = build_table_columns(scanner_id, rows) if rows else _preferred_fallback(scanner_id)

    table_rows = []
    has_validation = False
    for row in rows:
        badge = change_badge(row.get("change_type"), row.get("current_streak_scans"))
        failed = validation_failed(row)
        if failed is not None:
            has_validation = True
        table_rows.append(
            {
                "symbol": row.get("symbol") or "",
                "badge": badge,
                "cells": row_cells(row, columns),
                "picked": bool(row.get("picked")),
                "validation_failed": failed,
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
        row_count=len(table_rows),
        columns=columns,
        table_rows=table_rows,
        status=status,
        status_css=status_css(status),
        empty_reason=empty_reason,
        stock_filter=stock_filter,
        has_validation=has_validation,
        is_downstream=is_downstream,
    )


def _preferred_fallback(scanner_id: str) -> list[str]:
    from ..config import preferred_columns
    return preferred_columns(scanner_id) or ["Ticker"]
