"""Stock view routes."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from ..helpers import (
    change_badge,
    display_name_for,
    get_config,
    get_db,
    normalize_ticker,
    queries,
)

stocks_bp = Blueprint("stocks", __name__, url_prefix="/stocks")


@stocks_bp.route("/")
def stock_search():
    connection = get_db()
    query = (request.args.get("q") or "").strip()
    results = queries.search_stocks(connection, query) if query else []
    return render_template(
        "stocks/search.html",
        query=query,
        results=results,
        latest_date=queries.latest_scan_date(connection),
    )


@stocks_bp.route("/<symbol>")
def stock_detail(symbol: str):
    connection = get_db()
    cfg = get_config()
    ticker = normalize_ticker(symbol)
    latest = queries.latest_scan_date(connection)
    found = queries.stock_in_any_scanner(connection, ticker)
    info = queries.stock_info(connection, ticker)

    if not found:
        return render_template(
            "stocks/detail.html",
            symbol=ticker,
            found=False,
            info=info,
            latest_date=latest,
            summary=[],
            matrix={"dates": [], "scanners": [], "cells": {}},
            error_message=f"{ticker} was not found in any scanner results.",
        )

    summary_rows = queries.stock_summary(connection, ticker, as_of=latest)
    # Ensure every configured job appears, even if missing for the day.
    by_scanner = {row["scanner_id"]: row for row in summary_rows}
    summary = []
    for job in cfg.jobs:
        row = dict(by_scanner.get(job.name) or {"scanner_id": job.name, "picked": 0, "change_type": None})
        row["title"] = display_name_for(job.name, row.get("display_name"))
        row["badge"] = change_badge(row.get("change_type"), row.get("current_streak_scans"))
        summary.append(row)
    for scanner_id, row in by_scanner.items():
        if any(item["scanner_id"] == scanner_id for item in summary):
            continue
        payload = dict(row)
        payload["title"] = display_name_for(scanner_id, payload.get("display_name"))
        payload["badge"] = change_badge(payload.get("change_type"), payload.get("current_streak_scans"))
        summary.append(payload)

    matrix = queries.stock_change_matrix(connection, ticker, days=6)
    # Attach display titles + badges for template convenience.
    matrix_scanners = []
    for scanner in matrix["scanners"]:
        payload = dict(scanner)
        payload["title"] = display_name_for(payload["scanner_id"], payload.get("display_name"))
        matrix_scanners.append(payload)
    # Prefer jobs.json order for matrix columns.
    job_order = [job.name for job in cfg.jobs]
    order_index = {name: idx for idx, name in enumerate(job_order)}
    matrix_scanners.sort(key=lambda row: order_index.get(row["scanner_id"], 999))

    matrix_cells: dict[str, dict[str, dict]] = {}
    for day, by_id in matrix["cells"].items():
        matrix_cells[day] = {}
        for scanner_id, cell in by_id.items():
            payload = dict(cell)
            payload["badge"] = change_badge(payload.get("change_type"), payload.get("current_streak_scans"))
            matrix_cells[day][scanner_id] = payload

    return render_template(
        "stocks/detail.html",
        symbol=ticker,
        found=True,
        info=info,
        latest_date=latest,
        summary=summary,
        matrix={
            "dates": matrix["dates"],
            "scanners": matrix_scanners,
            "cells": matrix_cells,
        },
        error_message=None,
    )
