from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


def normalize_symbol(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    if ticker.endswith(".NS"):
        ticker = ticker[:-3]
    if ticker in {"", "NAN", "NONE", "NULL"}:
        return ""
    return ticker


def normalize_company_name(value: Any) -> str:
    """Return a clean text value, treating pandas nulls as missing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() in {"nan", "none", "null"} else text


def parse_date(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    try:
        return pd.Timestamp(text).date().isoformat()
    except (ValueError, TypeError):
        return text[:10] if len(text) >= 10 else text


@dataclass(frozen=True)
class UniverseStock:
    symbol: str
    company_name: str = ""
    industry: str = ""
    series: str = ""
    isin: str = ""


def load_universe(path: str | Path, as_of: date | None = None) -> list[UniverseStock]:
    frame = pd.read_csv(path)
    ticker_col = next(
        (column for column in ("Ticker", "ticker", "Symbol", "symbol") if column in frame.columns),
        None,
    )
    if ticker_col is None:
        raise KeyError(f"Universe file is missing a Ticker/Symbol column: {list(frame.columns)}")
    stocks: list[UniverseStock] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        symbol = normalize_symbol(row[ticker_col])
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        stocks.append(
            UniverseStock(
                symbol=symbol,
                company_name=normalize_company_name(row.get("Company Name")),
                industry=normalize_company_name(row.get("Industry")),
                series=normalize_company_name(row.get("Series")),
                isin=normalize_company_name(row.get("ISIN Code")),
            )
        )
    return stocks
