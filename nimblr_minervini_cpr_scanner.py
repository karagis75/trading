"""Nifty 500 combined Nimblr + Minervini + CPR daily scanner.

Reads the Indian Nifty 500 universe from ``ind_nifty500list.csv`` (the daily
CSV used by the other scanners in this repo) and evaluates each ticker on
Yahoo Finance daily bars.

A stock qualifies in ``all`` mode only when every Nimblr, Minervini Trend
Template, and CPR-buy condition passes. ``score`` mode ranks names by how
many of the three sections pass.
"""

from __future__ import annotations

import argparse
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
import yfinance as yf

DEFAULT_INPUT = "ind_nifty500list.csv"
DEFAULT_OUTPUT = "Nimblr_Minervini_CPR_Scan.xlsx"

EXCEL_READ_ENGINES = {
    ".xlsx": "openpyxl",
    ".xlsm": "openpyxl",
    ".xltx": "openpyxl",
    ".xltm": "openpyxl",
    ".xls": "xlrd",
    ".xlsb": "pyxlsb",
    ".ods": "odf",
}
EXCEL_WRITE_ENGINES = {
    ".xlsx": "openpyxl",
    ".xlsm": "openpyxl",
    ".xlsb": "pyxlsb",
    ".ods": "odf",
}
TEXT_ENGINES = {"csv", "html", "htm"}
TICKER_COLUMNS = ("Ticker", "ticker", "Symbol", "symbol", "SYMBOL")
OLE_COMPOUND_SIGNATURE = b"\xd0\xcf\x11\xe0"
ZIP_SIGNATURE = b"PK\x03\x04"
OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
YAHOO_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}
_LOOKBACK_SECONDS = {
    "mo": 30 * 86400,
    "wk": 7 * 86400,
    "w": 7 * 86400,
    "y": 365 * 86400,
    "d": 86400,
}
_YAHOO_SESSION: Any = None


@dataclass(frozen=True)
class CombinedScannerConfig:
    ema_fast: int = 10
    ema_mid: int = 20
    ema_slow: int = 50
    ema_150: int = 150
    ema_200: int = 200
    cci_period: int = 34
    cci_level: float = 100.0
    body_range_ratio: float = 0.5
    atr_period: int = 14
    atr_breakout_multiplier: float = 1.0
    ema200_lookback: int = 21
    week52_bars: int = 252
    week52_min_periods: int = 126
    min_low_multiple: float = 1.30
    min_high_multiple: float = 0.75
    volume_ema: int = 20
    require_previous_high_breakout: bool = False
    lookback_period: str = "2y"
    combine_mode: str = "all"
    min_sections: int = 3
    max_retries: int = 3
    retry_delay: float = 1.0
    request_delay: float = 0.0

    def __post_init__(self) -> None:
        if self.max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        if self.retry_delay < 0 or not np.isfinite(self.retry_delay):
            raise ValueError("retry_delay must be a finite non-negative number")
        if self.request_delay < 0 or not np.isfinite(self.request_delay):
            raise ValueError("request_delay must be a finite non-negative number")

    @property
    def effective_week52_min_periods(self) -> int:
        """Bars required for the 52-week high/low proxy.

        Newer Nifty 500 listings (for example TENNIND, URBANCO) do not yet
        have 252 sessions. Use six months of history as a listing-range
        stand-in so they can still be scored against the Trend Template.
        """
        return max(1, min(self.week52_min_periods, self.week52_bars))

    @property
    def minimum_history(self) -> int:
        return max(
            self.ema_200 + self.ema200_lookback + 2,
            self.effective_week52_min_periods + 2,
            self.cci_period + 3,
            self.atr_period + 3,
            self.volume_ema + 3,
        )


@dataclass
class ConditionResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class SectionResult:
    name: str
    passed: bool
    conditions: list[ConditionResult] = field(default_factory=list)

    @property
    def failed_names(self) -> list[str]:
        return [item.name for item in self.conditions if not item.passed]


def excel_engine_for_path(
    path: str | Path,
    engine: str | None = None,
    *,
    mode: str = "reader",
) -> str | None:
    if engine:
        normalized = engine.strip().lower()
        if normalized in TEXT_ENGINES:
            return None
        return normalized
    suffix = Path(path).suffix.lower()
    mapping = EXCEL_READ_ENGINES if mode == "reader" else EXCEL_WRITE_ENGINES
    return mapping.get(suffix)


def sniff_excel_engine(path: str | Path) -> str | None:
    source = Path(path)
    with source.open("rb") as handle:
        peek = handle.read(8)
    if peek.startswith(OLE_COMPOUND_SIGNATURE):
        return "xlrd"
    if not peek.startswith(ZIP_SIGNATURE):
        return None
    try:
        with zipfile.ZipFile(source) as archive:
            names = {name.replace("\\", "/").lower() for name in archive.namelist()}
    except zipfile.BadZipFile:
        return None
    if "xl/workbook.bin" in names:
        return "pyxlsb"
    if "content.xml" in names:
        return "odf"
    if "xl/workbook.xml" in names:
        return "openpyxl"
    return None


def _read_first_html_table(path: Path) -> pd.DataFrame:
    tables = pd.read_html(path)
    if not tables:
        raise ValueError(f"No HTML tables found in '{path}'")
    return tables[0]


def _has_ticker_column(df: pd.DataFrame) -> bool:
    return any(column in df.columns for column in TICKER_COLUMNS)


def _try_read_csv(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    return frame if _has_ticker_column(frame) else None


def _try_read_html(path: Path) -> pd.DataFrame | None:
    try:
        frame = _read_first_html_table(path)
    except Exception:
        return None
    return frame if _has_ticker_column(frame) else None


def read_input_table(path: str | Path, engine: str | None = None) -> pd.DataFrame:
    """Load a ticker universe from Excel, CSV, or HTML."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")

    requested = (engine or "").strip().lower() or None
    suffix = source.suffix.lower()

    if requested == "csv" or (requested is None and suffix == ".csv"):
        return pd.read_csv(source)
    if requested in {"html", "htm"} or (requested is None and suffix in {".html", ".htm"}):
        return _read_first_html_table(source)

    excel_engine = excel_engine_for_path(source, requested)
    if excel_engine is None:
        excel_engine = sniff_excel_engine(source)
    if excel_engine:
        try:
            return pd.read_excel(source, engine=excel_engine)
        except Exception as exc:
            if requested:
                raise ValueError(
                    f"Failed to read '{source}' with engine '{excel_engine}': {exc}"
                ) from exc
            fallback = _try_read_html(source)
            if fallback is None:
                fallback = _try_read_csv(source)
            if fallback is not None:
                return fallback
            raise ValueError(
                "Excel file format cannot be determined, you must specify an engine "
                f"manually. Failed to read '{source}' with engine '{excel_engine}': {exc}"
            ) from exc

    fallback = _try_read_csv(source)
    if fallback is None:
        fallback = _try_read_html(source)
    if fallback is not None:
        return fallback
    raise ValueError(
        "Excel file format cannot be determined, you must specify an engine manually. "
        f"Pass --engine (openpyxl, xlrd, pyxlsb, odf, csv, html) for '{source}'."
    )


def extract_tickers(df: pd.DataFrame) -> list[str]:
    for column in TICKER_COLUMNS:
        if column in df.columns:
            values = df[column].dropna().astype(str).str.strip()
            return [value for value in values if value and value.lower() != "nan"]
    raise KeyError(
        "Input file must contain a 'Ticker' or 'Symbol' column. "
        f"Found columns: {list(df.columns)}"
    )


def extract_company_names(df: pd.DataFrame) -> dict[str, str]:
    """Build a normalized ticker-to-company-name map from a universe table."""
    ticker_column = next((column for column in TICKER_COLUMNS if column in df.columns), None)
    if ticker_column is None or "Company Name" not in df.columns:
        return {}
    names: dict[str, str] = {}
    for _, row in df.iterrows():
        ticker = display_symbol(str(row.get(ticker_column) or ""))
        value = row.get("Company Name")
        name = "" if pd.isna(value) else str(value).strip()
        if ticker and name and name.lower() not in {"nan", "none", "null"}:
            names[ticker] = name
    return names


def yahoo_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not cleaned:
        return cleaned
    if "." in cleaned or cleaned.startswith("^"):
        return cleaned
    return f"{cleaned}.NS"


def display_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if cleaned.endswith(".NS"):
        return cleaned[:-3]
    return cleaned


def lookback_seconds(period: str) -> int:
    """Convert a Yahoo period token such as ``2y`` or ``3mo`` into seconds."""
    text = str(period).strip().lower()
    for suffix, unit in (("mo", _LOOKBACK_SECONDS["mo"]), ("wk", _LOOKBACK_SECONDS["wk"]), ("y", _LOOKBACK_SECONDS["y"]), ("w", _LOOKBACK_SECONDS["w"]), ("d", _LOOKBACK_SECONDS["d"])):
        if text.endswith(suffix):
            amount = text[: -len(suffix)] or "1"
            return max(86400, int(float(amount) * unit))
    raise ValueError(f"Unsupported Yahoo lookback period: {period}")


def yahoo_http_session() -> Any:
    """Reuse one browser-like session so Yahoo crumb/cookie work is not repeated."""
    global _YAHOO_SESSION
    if _YAHOO_SESSION is not None:
        return _YAHOO_SESSION
    try:
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome")
    except Exception:
        session = requests.Session()
    session.headers.update(YAHOO_HEADERS)
    _YAHOO_SESSION = session
    return session


def reset_yahoo_http_session() -> None:
    global _YAHOO_SESSION
    _YAHOO_SESSION = None


def write_results(df: pd.DataFrame, path: str | Path, engine: str | None = None) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    requested = (engine or "").strip().lower() or None
    suffix = destination.suffix.lower()
    if requested == "csv" or suffix == ".csv":
        df.to_csv(destination, index=False)
        return
    excel_engine = excel_engine_for_path(destination, requested, mode="writer") or "openpyxl"
    df.to_excel(destination, index=False, engine=excel_engine)


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean Open/High/Low/Close/Volume frame with a DatetimeIndex."""
    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    rename = {str(column): str(column).title() for column in frame.columns}
    frame = frame.rename(columns=rename)
    missing = [column for column in OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Price data is missing columns: {missing}")
    frame = frame.loc[:, list(OHLCV_COLUMNS)].copy()
    for column in OHLCV_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.to_datetime(frame.index)
    if frame.index.tz is not None:
        try:
            frame.index = frame.index.tz_convert("Asia/Kolkata")
        except (TypeError, ValueError, AttributeError):
            pass
        frame.index = frame.index.tz_localize(None)
    frame.index = frame.index.normalize()
    return frame.sort_index()


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _mean_abs_deviation(values: np.ndarray) -> float:
    return float(np.mean(np.abs(values - values.mean())))


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame, config: CombinedScannerConfig) -> pd.DataFrame:
    """Add EMA, CCI, ATR, CPR and 52-week columns to daily OHLCV data."""
    frame = normalize_ohlcv(df)
    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    volume = frame["Volume"].fillna(0)

    frame["EMA10"] = _ema(close, config.ema_fast)
    frame["EMA20"] = _ema(close, config.ema_mid)
    frame["EMA50"] = _ema(close, config.ema_slow)
    frame["EMA150"] = _ema(close, config.ema_150)
    frame["EMA200"] = _ema(close, config.ema_200)
    frame["EMA_VOL20"] = _ema(volume, config.volume_ema)

    typical_price = (high + low + close) / 3.0
    sma_tp = typical_price.rolling(window=config.cci_period).mean()
    mad = typical_price.rolling(window=config.cci_period).apply(_mean_abs_deviation, raw=True)
    frame["CCI"] = (typical_price - sma_tp) / (0.015 * mad.replace(0, np.nan))

    frame["ATR"] = _wilder_atr(high, low, close, config.atr_period)
    frame["ATR_BREAKOUT"] = frame["EMA10"] + config.atr_breakout_multiplier * frame["ATR"]

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)
    pivot = (prev_high + prev_low + prev_close) / 3.0
    bc = (prev_high + prev_low) / 2.0
    tc = (2.0 * pivot) - bc
    frame["PIVOT"] = pivot
    frame["CPR_TOP"] = pd.concat([tc, bc], axis=1).max(axis=1)
    frame["CPR_BOTTOM"] = pd.concat([tc, bc], axis=1).min(axis=1)

    body = close - frame["Open"]
    candle_range = high - low
    frame["BODY"] = body
    frame["RANGE"] = candle_range
    frame["BULLISH_BODY"] = (close > frame["Open"]) & (
        body.abs() >= config.body_range_ratio * candle_range.replace(0, np.nan)
    )

    week52_floor = config.effective_week52_min_periods
    frame["LOW_52W"] = low.rolling(window=config.week52_bars, min_periods=week52_floor).min()
    frame["HIGH_52W"] = high.rolling(window=config.week52_bars, min_periods=week52_floor).max()
    frame["EMA200_1M_AGO"] = frame["EMA200"].shift(config.ema200_lookback)
    return frame


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _condition(name: str, passed: bool, detail: str = "") -> ConditionResult:
    return ConditionResult(name=name, passed=bool(passed), detail=detail)


def print_skip_summary(skipped: list[str], min_bars: int) -> None:
    """One-line note for newer listings that lack enough daily history."""
    if not skipped:
        return
    print(
        f"Skipped {len(skipped)} newer listing(s) with fewer than {min_bars} "
        f"daily bars: {', '.join(skipped)}"
    )


def evaluate_nimblr(frame: pd.DataFrame, index: int, config: CombinedScannerConfig) -> SectionResult:
    curr = frame.iloc[index]
    prev = frame.iloc[index - 1]
    conditions = [
        _condition(
            "EMA10 > EMA20 > EMA50",
            _finite(curr["EMA10"])
            and _finite(curr["EMA20"])
            and _finite(curr["EMA50"])
            and curr["EMA10"] > curr["EMA20"] > curr["EMA50"],
            f"{curr.get('EMA10', np.nan):.2f} > {curr.get('EMA20', np.nan):.2f} > {curr.get('EMA50', np.nan):.2f}",
        ),
        _condition(
            "CCI(34) crossed above 100",
            _finite(curr["CCI"])
            and _finite(prev["CCI"])
            and prev["CCI"] <= config.cci_level
            and curr["CCI"] > config.cci_level,
            f"prev={prev.get('CCI', np.nan):.2f} curr={curr.get('CCI', np.nan):.2f}",
        ),
        _condition(
            "Today body > 50% of range",
            bool(curr["BULLISH_BODY"]),
            f"body={curr.get('BODY', np.nan):.2f} range={curr.get('RANGE', np.nan):.2f}",
        ),
        _condition(
            "Yesterday body > 50% of range",
            bool(prev["BULLISH_BODY"]),
            f"body={prev.get('BODY', np.nan):.2f} range={prev.get('RANGE', np.nan):.2f}",
        ),
        _condition(
            "Close above EMA10 + ATR buffer",
            _finite(curr["Close"])
            and _finite(curr["ATR_BREAKOUT"])
            and curr["Close"] > curr["ATR_BREAKOUT"],
            f"close={curr.get('Close', np.nan):.2f} level={curr.get('ATR_BREAKOUT', np.nan):.2f}",
        ),
    ]
    return SectionResult("Nimblr", all(item.passed for item in conditions), conditions)


def evaluate_minervini(frame: pd.DataFrame, index: int, config: CombinedScannerConfig) -> SectionResult:
    curr = frame.iloc[index]
    conditions = [
        _condition(
            "Close >= EMA150",
            _finite(curr["Close"]) and _finite(curr["EMA150"]) and curr["Close"] >= curr["EMA150"],
            f"{curr.get('Close', np.nan):.2f} vs {curr.get('EMA150', np.nan):.2f}",
        ),
        _condition(
            "Close >= EMA200",
            _finite(curr["Close"]) and _finite(curr["EMA200"]) and curr["Close"] >= curr["EMA200"],
            f"{curr.get('Close', np.nan):.2f} vs {curr.get('EMA200', np.nan):.2f}",
        ),
        _condition(
            "EMA150 >= EMA200",
            _finite(curr["EMA150"]) and _finite(curr["EMA200"]) and curr["EMA150"] >= curr["EMA200"],
            f"{curr.get('EMA150', np.nan):.2f} vs {curr.get('EMA200', np.nan):.2f}",
        ),
        _condition(
            "EMA200 rising vs 1 month ago",
            _finite(curr["EMA200"])
            and _finite(curr["EMA200_1M_AGO"])
            and curr["EMA200"] > curr["EMA200_1M_AGO"],
            f"{curr.get('EMA200', np.nan):.2f} vs {curr.get('EMA200_1M_AGO', np.nan):.2f}",
        ),
        _condition(
            "EMA50 > EMA150",
            _finite(curr["EMA50"]) and _finite(curr["EMA150"]) and curr["EMA50"] > curr["EMA150"],
            f"{curr.get('EMA50', np.nan):.2f} vs {curr.get('EMA150', np.nan):.2f}",
        ),
        _condition(
            "EMA50 > EMA200",
            _finite(curr["EMA50"]) and _finite(curr["EMA200"]) and curr["EMA50"] > curr["EMA200"],
            f"{curr.get('EMA50', np.nan):.2f} vs {curr.get('EMA200', np.nan):.2f}",
        ),
        _condition(
            "Close > EMA50",
            _finite(curr["Close"]) and _finite(curr["EMA50"]) and curr["Close"] > curr["EMA50"],
            f"{curr.get('Close', np.nan):.2f} vs {curr.get('EMA50', np.nan):.2f}",
        ),
        _condition(
            "Close >= 1.30 x 52-week low",
            _finite(curr["Close"])
            and _finite(curr["LOW_52W"])
            and curr["Close"] >= config.min_low_multiple * curr["LOW_52W"],
            f"{curr.get('Close', np.nan):.2f} vs {config.min_low_multiple:.2f}*{curr.get('LOW_52W', np.nan):.2f}",
        ),
        _condition(
            "Close >= 75% of 52-week high",
            _finite(curr["Close"])
            and _finite(curr["HIGH_52W"])
            and curr["Close"] >= config.min_high_multiple * curr["HIGH_52W"],
            f"{curr.get('Close', np.nan):.2f} vs {config.min_high_multiple:.2f}*{curr.get('HIGH_52W', np.nan):.2f}",
        ),
        _condition(
            "Volume >= EMA(volume, 20)",
            _finite(curr["Volume"])
            and _finite(curr["EMA_VOL20"])
            and curr["Volume"] >= curr["EMA_VOL20"],
            f"{curr.get('Volume', np.nan):.0f} vs {curr.get('EMA_VOL20', np.nan):.0f}",
        ),
    ]
    return SectionResult("Minervini", all(item.passed for item in conditions), conditions)


def evaluate_cpr(frame: pd.DataFrame, index: int, config: CombinedScannerConfig) -> SectionResult:
    curr = frame.iloc[index]
    prev = frame.iloc[index - 1]
    close_cross = (
        _finite(prev["Close"])
        and _finite(prev["CPR_TOP"])
        and _finite(curr["Close"])
        and _finite(curr["CPR_TOP"])
        and prev["Close"] <= prev["CPR_TOP"]
        and curr["Close"] > curr["CPR_TOP"]
    )
    conditions = [
        _condition(
            "Close crossed above CPR top",
            close_cross,
            f"prev_close={prev.get('Close', np.nan):.2f} prev_top={prev.get('CPR_TOP', np.nan):.2f} "
            f"close={curr.get('Close', np.nan):.2f} top={curr.get('CPR_TOP', np.nan):.2f}",
        ),
        _condition(
            "Bullish close",
            _finite(curr["Close"]) and _finite(curr["Open"]) and curr["Close"] > curr["Open"],
            f"open={curr.get('Open', np.nan):.2f} close={curr.get('Close', np.nan):.2f}",
        ),
        _condition(
            "Volume >= EMA(volume, 20)",
            _finite(curr["Volume"])
            and _finite(curr["EMA_VOL20"])
            and curr["Volume"] >= curr["EMA_VOL20"],
            f"{curr.get('Volume', np.nan):.0f} vs {curr.get('EMA_VOL20', np.nan):.0f}",
        ),
    ]
    if config.require_previous_high_breakout:
        conditions.append(
            _condition(
                "Close above previous high",
                _finite(curr["Close"]) and _finite(prev["High"]) and curr["Close"] > prev["High"],
                f"{curr.get('Close', np.nan):.2f} vs {prev.get('High', np.nan):.2f}",
            )
        )
    return SectionResult("CPR", all(item.passed for item in conditions), conditions)


def evaluate_combined(
    frame: pd.DataFrame,
    config: CombinedScannerConfig,
    index: int | None = None,
) -> dict[str, Any] | None:
    if len(frame) < config.minimum_history:
        return None
    loc = len(frame) - 1 if index is None else index
    if loc < 1 or loc >= len(frame):
        return None
    curr = frame.iloc[loc]
    if not all(_finite(curr[column]) for column in ("Open", "High", "Low", "Close")):
        return None

    nimblr = evaluate_nimblr(frame, loc, config)
    minervini = evaluate_minervini(frame, loc, config)
    cpr = evaluate_cpr(frame, loc, config)
    sections = [nimblr, minervini, cpr]
    passed_count = sum(1 for section in sections if section.passed)
    qualified = (
        passed_count == 3
        if config.combine_mode == "all"
        else passed_count >= config.min_sections
    )
    failed = [name for section in sections for name in section.failed_names]
    next_open = frame.iloc[loc + 1]["Open"] if loc + 1 < len(frame) else np.nan
    atr = curr["ATR"] if _finite(curr["ATR"]) else np.nan
    stop = curr["CPR_BOTTOM"] if _finite(curr["CPR_BOTTOM"]) else np.nan
    risk = (curr["Close"] - stop) if _finite(stop) else np.nan
    signal_date = frame.index[loc]
    return {
        "Date": pd.Timestamp(signal_date).date().isoformat(),
        "Close": float(curr["Close"]),
        "Open": float(curr["Open"]),
        "High": float(curr["High"]),
        "Low": float(curr["Low"]),
        "Volume": float(curr["Volume"]) if _finite(curr["Volume"]) else np.nan,
        "EMA10": float(curr["EMA10"]) if _finite(curr["EMA10"]) else np.nan,
        "EMA20": float(curr["EMA20"]) if _finite(curr["EMA20"]) else np.nan,
        "EMA50": float(curr["EMA50"]) if _finite(curr["EMA50"]) else np.nan,
        "EMA150": float(curr["EMA150"]) if _finite(curr["EMA150"]) else np.nan,
        "EMA200": float(curr["EMA200"]) if _finite(curr["EMA200"]) else np.nan,
        "CCI": float(curr["CCI"]) if _finite(curr["CCI"]) else np.nan,
        "ATR": float(atr) if _finite(atr) else np.nan,
        "ATR_Breakout": float(curr["ATR_BREAKOUT"]) if _finite(curr["ATR_BREAKOUT"]) else np.nan,
        "CPR_Top": float(curr["CPR_TOP"]) if _finite(curr["CPR_TOP"]) else np.nan,
        "CPR_Pivot": float(curr["PIVOT"]) if _finite(curr["PIVOT"]) else np.nan,
        "CPR_Bottom": float(curr["CPR_BOTTOM"]) if _finite(curr["CPR_BOTTOM"]) else np.nan,
        "Low_52W": float(curr["LOW_52W"]) if _finite(curr["LOW_52W"]) else np.nan,
        "High_52W": float(curr["HIGH_52W"]) if _finite(curr["HIGH_52W"]) else np.nan,
        "Volume_EMA20": float(curr["EMA_VOL20"]) if _finite(curr["EMA_VOL20"]) else np.nan,
        "Next_Open": float(next_open) if _finite(next_open) else np.nan,
        "Suggested_Stop": float(stop) if _finite(stop) else np.nan,
        "Risk_Per_Share": float(risk) if _finite(risk) else np.nan,
        "Nimblr": nimblr.passed,
        "Minervini": minervini.passed,
        "CPR": cpr.passed,
        "Sections_Passed": passed_count,
        "Qualified": qualified,
        "Failed_Conditions": "; ".join(failed) if failed else "",
    }


def _chart_value(values: Any, index: int) -> float | None:
    if not values or index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def frame_from_yahoo_chart(payload: dict[str, Any] | None) -> pd.DataFrame:
    """Turn a Yahoo v8 chart JSON payload into an OHLCV frame."""
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame()
    chart = result[0] or {}
    timestamps = chart.get("timestamp") or []
    indicators = chart.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0] or {}
    adj_block = (indicators.get("adjclose") or [{}])
    adjclose = (adj_block[0] or {}).get("adjclose") if adj_block else None
    closes = adjclose or quote.get("close") or []
    rows: list[dict[str, float]] = []
    index: list[pd.Timestamp] = []
    for position, stamp in enumerate(timestamps):
        close = _chart_value(closes, position)
        if close is None:
            continue
        rows.append(
            {
                "Open": _chart_value(quote.get("open"), position) or close,
                "High": _chart_value(quote.get("high"), position) or close,
                "Low": _chart_value(quote.get("low"), position) or close,
                "Close": close,
                "Volume": _chart_value(quote.get("volume"), position) or 0.0,
            }
        )
        index.append(pd.Timestamp(int(stamp), unit="s", tz="UTC"))
    if not rows:
        return pd.DataFrame()
    return normalize_ohlcv(pd.DataFrame(rows, index=pd.DatetimeIndex(index)))


def history_from_chart(symbol: str, period: str, session: Any | None = None) -> pd.DataFrame:
    """Fetch daily bars from Yahoo's public chart API without a crumb cookie.

    yfinance first talks to ``fc.yahoo.com`` for a cookie/crumb. When that DNS
    lookup fails it still calls history(), then labels a listed NSE name as
    "possibly delisted". The JS scanners in this repo already use this chart
    endpoint directly; it works without the crumb host.
    """
    yahoo = yahoo_symbol(symbol)
    encoded = quote(yahoo, safe="^")
    end = int(time.time())
    start = end - lookback_seconds(period)
    params = {
        "period1": start,
        "period2": end,
        "interval": "1d",
        "events": "div,splits",
        "includeAdjustedClose": "true",
    }
    http = session or yahoo_http_session()
    last_error: Exception | None = None
    for template in YAHOO_CHART_URLS:
        url = template.format(symbol=encoded)
        try:
            response = http.get(url, params=params, timeout=20)
            response.raise_for_status()
            frame = frame_from_yahoo_chart(response.json())
            if not frame.empty:
                return frame
            last_error = ValueError(f"empty Yahoo chart payload for {yahoo}")
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.DataFrame()


def _ticker_history(symbol: str, config: CombinedScannerConfig, session: Any) -> pd.DataFrame:
    yahoo = yahoo_symbol(symbol)
    try:
        ticker = yf.Ticker(yahoo, session=session)
    except TypeError:
        ticker = yf.Ticker(yahoo)
    history = ticker.history(period=config.lookback_period, interval="1d", auto_adjust=True)
    if history is None or history.empty:
        return pd.DataFrame()
    return normalize_ohlcv(history)


def _yf_download_history(symbol: str, config: CombinedScannerConfig, session: Any) -> pd.DataFrame:
    yahoo = yahoo_symbol(symbol)
    try:
        history = yf.download(
            yahoo,
            period=config.lookback_period,
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=False,
            session=session,
        )
    except TypeError:
        history = yf.download(
            yahoo,
            period=config.lookback_period,
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=False,
        )
    if history is None or history.empty:
        return pd.DataFrame()
    if isinstance(history.columns, pd.MultiIndex):
        history.columns = history.columns.get_level_values(0)
    return normalize_ohlcv(history)


def _download_yahoo_history(symbol: str, config: CombinedScannerConfig) -> pd.DataFrame:
    session = yahoo_http_session()
    for loader in (_ticker_history, _yf_download_history):
        try:
            frame = loader(symbol, config, session)
        except Exception as exc:
            logging.debug("Yahoo loader %s failed for %s: %s", loader.__name__, symbol, exc)
            continue
        if frame is not None and not frame.empty:
            return frame
    return history_from_chart(symbol, config.lookback_period, session)


def fetch_history(symbol: str, config: CombinedScannerConfig) -> pd.DataFrame:
    """Download daily OHLCV, retrying after Yahoo crumb/DNS empty responses.

    The "possibly delisted" yfinance message is often a cookie/crumb failure,
    not a real NSE delisting. Retry with backoff and fall back to the public
    chart API used by the Node scanners.
    """
    last_error: Exception | None = None
    for attempt in range(config.max_retries):
        try:
            frame = _download_yahoo_history(symbol, config)
            if frame is not None and not frame.empty:
                if config.request_delay:
                    time.sleep(config.request_delay)
                return frame
            last_error = ValueError(f"empty price data for {yahoo_symbol(symbol)}")
        except Exception as exc:
            last_error = exc
            logging.warning(
                "Yahoo download attempt %d/%d failed for %s: %s",
                attempt + 1,
                config.max_retries,
                yahoo_symbol(symbol),
                exc,
            )
            reset_yahoo_http_session()
        if attempt < config.max_retries - 1:
            time.sleep(config.retry_delay * (2 ** attempt))
    if last_error is not None:
        logging.warning(
            "All %d Yahoo attempts failed for %s: %s",
            config.max_retries,
            yahoo_symbol(symbol),
            last_error,
        )
    if config.request_delay:
        time.sleep(config.request_delay)
    return pd.DataFrame()


def analyze_symbol(
    symbol: str,
    config: CombinedScannerConfig,
    history: pd.DataFrame | None = None,
    skipped: list[str] | None = None,
) -> dict[str, Any] | None:
    try:
        frame = history if history is not None else fetch_history(symbol, config)
        if frame.empty or len(frame) < config.minimum_history:
            if skipped is not None:
                skipped.append(display_symbol(symbol))
            else:
                logging.debug("Insufficient historical data for %s", symbol)
            return None
        frame = calculate_indicators(frame, config)
        result = evaluate_combined(frame, config)
        if result is None:
            return None
        result["Ticker"] = display_symbol(symbol)
        return result
    except Exception as exc:
        logging.warning("Error processing ticker %s: %s", symbol, exc)
        return None


def scan_tickers(
    tickers: Iterable[str],
    config: CombinedScannerConfig,
    include_failures: bool = False,
    history_loader=fetch_history,
    skipped: list[str] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    skipped_names = skipped if skipped is not None else []
    for ticker in tickers:
        try:
            history = history_loader(ticker, config)
            result = analyze_symbol(ticker, config, history=history, skipped=skipped_names)
        except Exception as exc:
            logging.warning("Error processing ticker %s: %s", ticker, exc)
            continue
        if result is None:
            continue
        if result["Qualified"] or include_failures:
            results.append(result)
    if skipped is None and skipped_names:
        print_skip_summary(skipped_names, config.minimum_history)
    return results


def backtest_symbol(
    frame: pd.DataFrame,
    config: CombinedScannerConfig,
    hold_bars: int | None = None,
) -> list[dict[str, Any]]:
    """Buy the next session open after a qualified signal and hold to a later close.

    ``hold_bars`` is the number of completed daily bars after the signal date.
    ``None`` holds through the last available close. The signal bar itself is
    never used as the fill price.
    """
    data = calculate_indicators(frame, config)
    trades: list[dict[str, Any]] = []
    last_index = len(data) - 1
    start = config.minimum_history - 1
    for index in range(start, last_index):
        snapshot = evaluate_combined(data, config, index=index)
        if snapshot is None or not snapshot["Qualified"]:
            continue
        entry_index = index + 1
        exit_index = last_index if hold_bars is None else min(index + hold_bars, last_index)
        if exit_index <= entry_index:
            continue
        entry = data.iloc[entry_index]["Open"]
        exit_price = data.iloc[exit_index]["Close"]
        if not (_finite(entry) and _finite(exit_price) and entry > 0):
            continue
        profit = float(exit_price) - float(entry)
        trades.append(
            {
                "Signal_Date": pd.Timestamp(data.index[index]).date().isoformat(),
                "Entry_Date": pd.Timestamp(data.index[entry_index]).date().isoformat(),
                "Exit_Date": pd.Timestamp(data.index[exit_index]).date().isoformat(),
                "Entry": float(entry),
                "Exit": float(exit_price),
                "PnL": profit,
                "Return_Pct": profit / float(entry) * 100.0,
            }
        )
    return trades


def summarize_backtest(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "Trades": 0,
            "Wins": 0,
            "Losses": 0,
            "Cost": 0.0,
            "Value": 0.0,
            "PnL": 0.0,
            "Return_Pct": 0.0,
        }
    cost = sum(trade["Entry"] for trade in trades)
    value = sum(trade["Exit"] for trade in trades)
    pnl = value - cost
    wins = sum(trade["PnL"] > 0 for trade in trades)
    losses = sum(trade["PnL"] < 0 for trade in trades)
    return {
        "Trades": len(trades),
        "Wins": wins,
        "Losses": losses,
        "Cost": cost,
        "Value": value,
        "PnL": pnl,
        "Return_Pct": (pnl / cost * 100.0) if cost else 0.0,
    }


def _round_result(row: dict[str, Any]) -> dict[str, Any]:
    rounded = dict(row)
    for key, value in row.items():
        if isinstance(value, float) and np.isfinite(value):
            rounded[key] = round(value, 2)
    return rounded


RESULT_COLUMNS = [
    "Ticker",
    "Date",
    "Close",
    "CCI",
    "EMA10",
    "EMA20",
    "EMA50",
    "EMA150",
    "EMA200",
    "ATR",
    "ATR_Breakout",
    "CPR_Top",
    "CPR_Pivot",
    "CPR_Bottom",
    "Low_52W",
    "High_52W",
    "Volume",
    "Volume_EMA20",
    "Next_Open",
    "Suggested_Stop",
    "Risk_Per_Share",
    "Nimblr",
    "Minervini",
    "CPR",
    "Sections_Passed",
    "Qualified",
    "Failed_Conditions",
]


def format_results(results: list[dict[str, Any]]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    frame = pd.DataFrame([_round_result(row) for row in results])
    ordered = [column for column in RESULT_COLUMNS if column in frame.columns]
    extra = [column for column in frame.columns if column not in ordered]
    return frame.loc[:, ordered + extra].sort_values(
        by=["Qualified", "Sections_Passed", "CCI"],
        ascending=[False, False, False],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan the Indian Nifty 500 daily CSV for stocks that pass Nimblr, "
            "Minervini Trend Template, and CPR-buy together."
        )
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="Path to the Nifty 500 ticker CSV/Excel file (default: ind_nifty500list.csv).",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output path for scan results.")
    parser.add_argument(
        "--engine",
        default=None,
        help="Input file engine: openpyxl, xlrd, pyxlsb, odf, csv, or html.",
    )
    parser.add_argument(
        "--mode",
        choices=("scan", "backtest"),
        default="scan",
        help="scan writes today's combined hits; backtest buys next open after each hit.",
    )
    parser.add_argument(
        "--combine-mode",
        choices=("all", "score"),
        default="all",
        help="all requires every section; score keeps names passing --min-sections.",
    )
    parser.add_argument("--min-sections", type=int, default=3, help="Minimum sections for score mode.")
    parser.add_argument(
        "--include-failures",
        action="store_true",
        help="Also write names that fail, with Failed_Conditions filled in.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Scan only the first N tickers (0 = all).")
    parser.add_argument(
        "--atr-breakout-multiplier",
        type=float,
        default=1.0,
        help="Close must exceed EMA10 + multiplier * ATR(14).",
    )
    parser.add_argument(
        "--require-previous-high-breakout",
        action="store_true",
        help="Also require today's close above yesterday's high for CPR.",
    )
    parser.add_argument(
        "--backtest-days",
        type=int,
        default=30,
        help="Only keep backtest signals from the last N calendar days of history.",
    )
    parser.add_argument(
        "--lookback",
        default="2y",
        help="Yahoo Finance daily lookback period (default: 2y).",
    )
    return parser.parse_args(argv)


def load_universe(path: str | Path, engine: str | None = None) -> list[str]:
    tickers = extract_tickers(read_input_table(path, engine=engine))
    if not tickers:
        raise ValueError(f"Stock list is empty: {path}")
    return tickers


def run_scan(args: argparse.Namespace) -> int:
    config = CombinedScannerConfig(
        atr_breakout_multiplier=args.atr_breakout_multiplier,
        require_previous_high_breakout=args.require_previous_high_breakout,
        combine_mode=args.combine_mode,
        min_sections=args.min_sections,
        lookback_period=args.lookback,
    )
    try:
        tickers = load_universe(args.input, engine=args.engine)
    except Exception as exc:
        print(f"Input Error: {exc}")
        return 1
    if args.limit and args.limit > 0:
        tickers = tickers[: args.limit]

    print(
        f"Scanning {len(tickers)} Nifty 500 names from '{args.input}' "
        f"(Nimblr + Minervini + CPR, mode={config.combine_mode})..."
    )

    if args.mode == "backtest":
        return run_backtest(tickers, config, args)

    results = scan_tickers(tickers, config, include_failures=args.include_failures)
    qualified = [row for row in results if row.get("Qualified")]
    output = format_results(results)
    write_results(output, args.output)
    print(
        f"Scan complete. {len(qualified)} combined hit(s) out of {len(tickers)} ticker(s). "
        f"Saved to '{args.output}'."
    )
    return 0


def run_backtest(tickers: list[str], config: CombinedScannerConfig, args: argparse.Namespace) -> int:
    trades: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            history = fetch_history(ticker, config)
            if history.empty or len(history) < config.minimum_history:
                continue
            last_date = pd.Timestamp(history.index.max()).normalize()
            cutoff = last_date - pd.Timedelta(days=args.backtest_days)
            symbol_trades = backtest_symbol(history, config)
            for trade in symbol_trades:
                signal_ts = pd.Timestamp(trade["Signal_Date"]).normalize()
                if signal_ts < cutoff:
                    continue
                trade["Ticker"] = display_symbol(ticker)
                trades.append(trade)
        except Exception as exc:
            logging.warning("Backtest skipped %s: %s", ticker, exc)

    summary = summarize_backtest(trades)
    if trades:
        write_results(pd.DataFrame(trades), args.output)
    print(
        "Backtest (1 share per signal, next-session open, hold to latest close, no costs): "
        f"trades={summary['Trades']} cost=Rs {summary['Cost']:,.2f} "
        f"value=Rs {summary['Value']:,.2f} P/L=Rs {summary['PnL']:,.2f} "
        f"return={summary['Return_Pct']:.2f}% W/L={summary['Wins']}/{summary['Losses']}"
    )
    if trades:
        print(f"Trade list saved to '{args.output}'.")
    else:
        print("No combined signals in the selected backtest window.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    return run_scan(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
