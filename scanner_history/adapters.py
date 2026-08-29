"""Parse dated scanner XLSX/CSV outputs into membership hits."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .normalize import normalize_symbol, parse_date


@dataclass
class HitRecord:
    symbol: str
    signal_date: str | None = None
    classification: str | None = None
    confidence: float | None = None
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedOutput:
    path: Path
    sheet: str | None
    hits: list[HitRecord]
    error: str | None = None

    @property
    def symbols(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for hit in self.hits:
            if hit.symbol and hit.symbol not in seen:
                seen.add(hit.symbol)
                ordered.append(hit.symbol)
        return ordered


def _read_frame(path: Path, sheet: str | None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    kwargs: dict[str, Any] = {"engine": "openpyxl"}
    if sheet:
        kwargs["sheet_name"] = sheet
        try:
            return pd.read_excel(path, **kwargs)
        except ValueError:
            return pd.read_excel(path, engine="openpyxl", sheet_name=0)
    return pd.read_excel(path, engine="openpyxl")


def _truthy_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def _apply_filter(frame: pd.DataFrame, membership_filter: str | None) -> pd.DataFrame:
    if not membership_filter or frame.empty:
        return frame
    if "=" not in membership_filter:
        return frame
    column, expected = [part.strip() for part in membership_filter.split("=", 1)]
    if column not in frame.columns:
        return frame
    if expected.lower() in {"true", "1", "yes"}:
        return frame.loc[_truthy_series(frame, column)].copy()
    return frame.loc[frame[column].astype(str) == expected].copy()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def parse_scanner_output(
    path: str | Path,
    *,
    fmt: str = "xlsx",
    sheet: str | None = None,
    symbol_column: str = "Ticker",
    membership_filter: str | None = None,
    signal_date_column: str | None = None,
    classification_column: str | None = None,
    confidence_column: str | None = None,
) -> ParsedOutput:
    source = Path(path)
    if not source.exists():
        return ParsedOutput(source, sheet, [], error=f"output file not found: {source}")
    try:
        frame = _read_frame(source, sheet)
    except Exception as exc:
        return ParsedOutput(source, sheet, [], error=f"failed to read {source}: {exc}")

    if frame is None or frame.empty:
        return ParsedOutput(source, sheet, [])

    frame = _apply_filter(frame, membership_filter)
    symbol_col = symbol_column if symbol_column in frame.columns else None
    if symbol_col is None:
        for candidate in ("Ticker", "ticker", "Symbol", "symbol", "SYMBOL"):
            if candidate in frame.columns:
                symbol_col = candidate
                break
    if symbol_col is None:
        return ParsedOutput(
            source,
            sheet,
            [],
            error=f"{source} has no Ticker/Symbol column: {list(frame.columns)}",
        )

    hits: list[HitRecord] = []
    for index, row in frame.iterrows():
        symbol = normalize_symbol(row.get(symbol_col))
        if not symbol:
            continue
        metadata = {
            str(key): (None if pd.isna(value) else value)
            for key, value in row.items()
            if str(key) not in {symbol_col}
        }
        # JSON-safe scalars only
        safe_meta = {}
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe_meta[key] = value
            else:
                safe_meta[key] = str(value)
        signal_source = signal_date_column if signal_date_column in frame.columns else None
        if signal_source is None:
            for candidate in ("Signal_Date", "Date", "Last Date"):
                if candidate in frame.columns:
                    signal_source = candidate
                    break
        class_source = classification_column if classification_column in frame.columns else None
        if class_source is None:
            for candidate in ("Wave Position", "Status", "Setup Status", "Strategy"):
                if candidate in frame.columns:
                    class_source = candidate
                    break
        conf_source = confidence_column if confidence_column in frame.columns else None
        if conf_source is None:
            for candidate in ("Confidence", "Score", "ADX", "ADX (14)"):
                if candidate in frame.columns:
                    conf_source = candidate
                    break
        hits.append(
            HitRecord(
                symbol=symbol,
                signal_date=parse_date(row.get(signal_source)) if signal_source else None,
                classification=None if class_source is None else str(row.get(class_source) or "") or None,
                confidence=_finite(row.get(conf_source)) if conf_source else None,
                score=_finite(row.get("Score")) if "Score" in frame.columns else None,
                metadata=safe_meta,
            )
        )
    return ParsedOutput(source, sheet, hits)
