"""Configuration for the trading dashboard."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = os.environ.get(
    "TRADING_DATABASE_URL",
    str(REPO_ROOT / "scanner_history" / "scanner_history.sqlite3"),
)
DEFAULT_JOBS = REPO_ROOT / "scheduler" / "jobs.json"
DEFAULT_UNIVERSE = REPO_ROOT / "ind_nifty500list.csv"

# Friendly titles and preferred column order for each scanner day table.
SCANNER_DISPLAY: dict[str, dict[str, Any]] = {
    "prefetch-yahoo-ohlcv": {
        "title": "Yahoo OHLCV Prefetch",
        "columns": ["Ticker"],
    },
    "validate-2026-08-30-outputs": {
        "title": "30 Aug 2026 output validation",
        "columns": ["Job", "Status", "Shared tickers"],
    },
    "bullish-bias-nifty500": {
        "title": "Bullish Bias NIFTY 500",
        "columns": [
            "Ticker",
            "Close Price",
            "EMA9",
            "EMA18",
            "EMA50",
            "EMA200",
            "CCI",
            "ADX",
            "Aggressive SL (18 EMA)",
            "Swing SL (50 EMA)",
            "Status",
        ],
    },
    "bearish-bias-nifty500": {
        "title": "Bearish Bias NIFTY 500",
        "columns": [
            "Ticker",
            "Close Price",
            "Fib S1",
            "EMA9",
            "EMA18",
            "EMA50",
            "CCI (14)",
            "ADX (14)",
            "Aggressive SL (18 EMA)",
            "Swing SL (50 EMA)",
            "Setup Status",
        ],
    },
    "nifty500-xy-intersect": {
        "title": "NIFTY 500 X/Y Intersect",
        "columns": [
            "Ticker",
            "Price (INR)",
            "ATR",
            "Triggered Entry (Y)",
            "Assigned Exit Matrix (X)",
            "X/Y Intersect Rule",
            "Target Target Level",
        ],
    },
    "rangebound-stocks": {
        "title": "Rangebound / Strangle Candidates",
        "columns": [
            "Ticker",
            "Close Price",
            "ADX (14)",
            "CCI (14)",
            "EMA Braid Spread %",
            "Box Range %",
            "Box High",
            "Box Low",
            "Setup Status",
        ],
    },
    "minervini-vcp": {
        "title": "Minervini VCP Scanner",
        "columns": [
            "Ticker",
            "Company Name",
            "Date",
            "Close",
            "EMA50",
            "EMA150",
            "EMA200",
            "ATR",
            "Contractions",
            "Latest_Pullback_%",
            "Base_Position",
            "Stage2_Trend",
            "VCP",
            "Sections_Passed",
            "Qualified",
        ],
    },
    "nimblr-minervini-cpr": {
        "title": "Nimblr Minervini CPR",
        "columns": [
            "Ticker",
            "Company Name",
            "Date",
            "Close",
            "CCI",
            "EMA10",
            "EMA20",
            "EMA50",
            "EMA150",
            "EMA200",
            "ATR",
            "Sections_Passed",
            "Qualified",
        ],
    },
    "minervini-volume-cpr": {
        "title": "Minervini Volume + CPR",
        "columns": [
            "Ticker",
            "Company Name",
            "Date",
            "Close",
            "EMA50",
            "EMA150",
            "EMA200",
            "CPR_Top",
            "CPR_Width_%",
            "Virgin_Above",
            "Minervini_Volume",
            "Virgin_CPR_Buy",
            "Narrow_CPR_Breakout",
            "Sections_Passed",
            "Qualified",
        ],
    },
    "nifty-fib-pinball-bullish": {
        "title": "Bullish Fib Pinball",
        "columns": ["Ticker", "Last Date", "Wave Position", "Confidence"],
    },
    "nifty-fib-pinball-bearish": {
        "title": "Bearish Fib Pinball",
        "columns": ["Ticker", "Last Date", "Wave Position", "Confidence"],
    },
    "merge-option-candidates": {
        "title": "Option Scan Candidates (Merged)",
        "columns": ["Ticker"],
    },
    "combined-option-v8": {
        "title": "Combined Option Spread Analysis",
        "columns": [
            "Symbol",
            "Strategy",
            "Expiry",
            "PCR",
            "Score",
            "R:R Ratio",
            "Validation Pass",
        ],
    },
}


def display_name_for(scanner_id: str, fallback: str | None = None) -> str:
    meta = SCANNER_DISPLAY.get(scanner_id) or {}
    return str(meta.get("title") or fallback or scanner_id)


def preferred_columns(scanner_id: str) -> list[str]:
    meta = SCANNER_DISPLAY.get(scanner_id) or {}
    return list(meta.get("columns") or [])


@dataclass
class JobMeta:
    name: str
    enabled: bool = True
    role: str = "primary_scanner"
    script: str = ""
    note: str = ""


@dataclass
class AppConfig:
    database_url: str = DEFAULT_DB
    jobs_path: Path = DEFAULT_JOBS
    universe_path: Path = DEFAULT_UNIVERSE
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    jobs: list[JobMeta] = field(default_factory=list)

    @classmethod
    def from_env(
        cls,
        *,
        database_url: str | None = None,
        jobs_path: Path | None = None,
        host: str | None = None,
        port: int | None = None,
        debug: bool | None = None,
    ) -> "AppConfig":
        path = Path(jobs_path) if jobs_path else DEFAULT_JOBS
        jobs = load_jobs_meta(path)
        return cls(
            database_url=database_url or DEFAULT_DB,
            jobs_path=path,
            host=host or os.environ.get("TRADING_WEB_HOST", "127.0.0.1"),
            port=int(port or os.environ.get("TRADING_WEB_PORT", "8000")),
            debug=bool(debug) if debug is not None else os.environ.get("TRADING_WEB_DEBUG", "").lower() in {"1", "true", "yes"},
            jobs=jobs,
        )


def load_jobs_meta(path: Path) -> list[JobMeta]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs: list[JobMeta] = []
    for raw in payload.get("jobs") or []:
        tracking = raw.get("tracking") or {}
        jobs.append(
            JobMeta(
                name=str(raw.get("name") or ""),
                enabled=bool(raw.get("enabled", True)),
                role=str(tracking.get("role") or "primary_scanner"),
                script=str(raw.get("script") or ""),
                note=str(raw.get("note") or ""),
            )
        )
    return jobs
