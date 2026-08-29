"""Shared helpers for dashboard routes."""

from __future__ import annotations

import json
from typing import Any

from flask import current_app, g

from scanner_history import db, queries
from scanner_history.normalize import normalize_symbol

from .config import AppConfig, display_name_for, preferred_columns


def get_config() -> AppConfig:
    return current_app.config["TRADING_CONFIG"]


def get_db():
    if "db" not in g:
        g.db = db.connect(get_config().database_url)
    return g.db


def close_db(_exc=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def parse_metadata(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_table_columns(scanner_id: str, rows: list[dict[str, Any]]) -> list[str]:
    preferred = preferred_columns(scanner_id)
    seen: set[str] = set()
    ordered: list[str] = []
    for column in preferred:
        ordered.append(column)
        seen.add(column)
    extras: list[str] = []
    for row in rows:
        meta = parse_metadata(row.get("metadata_json"))
        for key in meta:
            if key not in seen:
                seen.add(key)
                extras.append(key)
    return ordered + sorted(extras)


def row_cells(row: dict[str, Any], columns: list[str]) -> list[Any]:
    meta = parse_metadata(row.get("metadata_json"))
    symbol = row.get("symbol")
    cells: list[Any] = []
    for column in columns:
        if column in {"Ticker", "Symbol", "ticker", "symbol"}:
            value = meta.get(column, symbol)
        else:
            value = meta.get(column)
            if value is None and column in {"Status", "Setup Status", "Wave Position", "Strategy", "X/Y Intersect Rule"}:
                value = row.get("classification")
            if value is None and column in {"Confidence", "Score"}:
                value = row.get("confidence") if column == "Confidence" else (row.get("score") or row.get("confidence"))
            if value is None and column in {"Date", "Last Date", "Signal_Date"}:
                value = row.get("signal_date")
        cells.append(value)
    return cells


def change_badge(change_type: str | None, streak: int | None = None) -> dict[str, str]:
    label = str(change_type or "—")
    css = {
        "ADDED": "badge-added",
        "READED": "badge-readded",
        "CONTINUED": "badge-continued",
        "DROPPED": "badge-dropped",
        "UNIVERSE_REMOVED": "badge-dropped",
        "NOT_PICKED": "badge-muted",
        "INDETERMINATE": "badge-muted",
    }.get(label, "badge-muted")
    display = {
        "READED": "RE-ADDED",
        "UNIVERSE_REMOVED": "REMOVED",
        "NOT_PICKED": "—",
    }.get(label, label)
    if label == "CONTINUED" and streak:
        display = f"CONTINUED ×{streak}"
    return {"label": display, "css": css}


def status_css(status: str | None) -> str:
    return {
        "success": "status-success",
        "failed": "status-failed",
        "skipped": "status-skipped",
        "indeterminate": "status-indeterminate",
    }.get(str(status or "").lower(), "status-missing")


def enrich_scanner_index(rows: list[dict[str, Any]], jobs_order: list[str]) -> list[dict[str, Any]]:
    by_id = {row["scanner_id"]: row for row in rows}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scanner_id in jobs_order:
        row = dict(by_id.get(scanner_id) or {"scanner_id": scanner_id, "enabled": 1})
        row["title"] = display_name_for(scanner_id, row.get("display_name"))
        row["status_css"] = status_css(row.get("status"))
        ordered.append(row)
        seen.add(scanner_id)
    for scanner_id, row in by_id.items():
        if scanner_id in seen:
            continue
        payload = dict(row)
        payload["title"] = display_name_for(scanner_id, payload.get("display_name"))
        payload["status_css"] = status_css(payload.get("status"))
        ordered.append(payload)
    return ordered


def normalize_ticker(symbol: str) -> str:
    return normalize_symbol(symbol) or str(symbol or "").strip().upper()


__all__ = [
    "build_table_columns",
    "change_badge",
    "close_db",
    "display_name_for",
    "enrich_scanner_index",
    "get_config",
    "get_db",
    "normalize_ticker",
    "parse_metadata",
    "queries",
    "row_cells",
    "status_css",
]
