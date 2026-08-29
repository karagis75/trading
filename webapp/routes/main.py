"""Main menu routes."""

from __future__ import annotations

from flask import Blueprint, render_template

from ..helpers import display_name_for, enrich_scanner_index, get_config, get_db, queries, status_css

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    connection = get_db()
    cfg = get_config()
    latest = queries.latest_scan_date(connection)
    health = []
    if latest:
        for row in queries.day_statuses(connection, latest):
            payload = dict(row)
            payload["title"] = display_name_for(payload["scanner_id"], payload.get("display_name"))
            payload["status_css"] = status_css(payload.get("status"))
            health.append(payload)
    else:
        for job in cfg.jobs:
            health.append(
                {
                    "scanner_id": job.name,
                    "title": display_name_for(job.name),
                    "status": None,
                    "status_css": "status-missing",
                    "result_count": None,
                }
            )
    scanners = enrich_scanner_index(
        queries.scanner_index(connection),
        [job.name for job in cfg.jobs],
    )
    return render_template(
        "index.html",
        latest_date=latest,
        health=health,
        scanners=scanners,
        has_data=bool(latest),
    )
