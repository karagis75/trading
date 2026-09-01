"""JSON API routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..helpers import get_config, get_db, normalize_ticker, queries

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/stocks/<symbol>/pinball-chart")
def api_stock_pinball_chart(symbol: str):
    from stock_fib_pinball_chart import build_pinball_chart

    ticker = normalize_ticker(symbol)
    payload = build_pinball_chart(
        ticker,
        connection=get_db(),
        database_url=get_config().database_url,
    )
    return jsonify(payload)


@api_bp.route("/stocks/search")
def api_stock_search():
    query = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(limit or 20, 50))
    connection = get_db()
    results = (
        queries.search_stocks(
            connection,
            query,
            limit=limit,
            universe_path=get_config().universe_path,
        )
        if query
        else []
    )
    return jsonify({"query": query, "results": results})
