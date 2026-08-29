"""JSON API routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..helpers import get_db, queries

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/stocks/search")
def api_stock_search():
    query = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(limit or 20, 50))
    connection = get_db()
    results = queries.search_stocks(connection, query, limit=limit) if query else []
    return jsonify({"query": query, "results": results})
