"""Main menu routes."""

from __future__ import annotations

from flask import Blueprint, render_template

from ..helpers import enrich_health, get_config, get_db, queries
from ..perf import SHORT_TTL, cached

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    connection = get_db()
    cfg = get_config()

    latest = cached("latest_scan_date", SHORT_TTL, lambda: queries.latest_scan_date(connection))

    raw_health = []
    if latest:
        raw_health = cached(
            f"day_statuses:{latest}",
            SHORT_TTL,
            lambda: queries.day_statuses(connection, latest),
        )
    health = enrich_health(raw_health, cfg.jobs)

    return render_template(
        "index.html",
        latest_date=latest,
        health=health,
        has_data=bool(latest),
    )
