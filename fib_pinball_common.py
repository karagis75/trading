"""Shared helpers for Fibonacci pinball scanners.

Used by the bullish (`nifty_pinball_yahoo.py`) and bearish
(`bearish_fib_pinball.py`) conversions of the original Node scanners.
Both programs read the Nifty 500 universe from ``ind_nifty500list.csv``.
"""

from __future__ import annotations

import argparse
import logging
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd
import yfinance as yf

DEFAULT_INPUT = "ind_nifty500list.csv"
OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
TICKER_COLUMNS = ("Ticker", "ticker", "Symbol", "symbol", "SYMBOL")
OLE_COMPOUND_SIGNATURE = b"\xd0\xcf\x11\xe0"
ZIP_SIGNATURE = b"PK\x03\x04"
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
SKIPPED_COLUMNS = ("Ticker", "Reason", "Days_of_Data")


@dataclass(frozen=True)
class PinballConfig:
    lookback_days: int = 500
    min_bars: int = 60
    analysis_bars: int = 200
    pivot_left: int = 5
    pivot_right: int = 5
    max_days_since_w2: int = 120
    max_days_since_w0: int = 180
    request_delay: float = 0.0
    early_wave1_min_move: float = 0.05
    early_wave1_max_move: float = 1.0
    early_wave1_min_bars: int = 5
    early_wave1_prior_bars: int = 20
    sma_period: int = 10
    w4_lookback: int = 20
    early_w1_of_3_max_days: int = 30
    retrace_min: float = 0.236
    retrace_max: float = 0.886
    extension_ratios: tuple[float, ...] = (
        0.382,
        0.618,
        0.764,
        1.0,
        1.236,
        1.382,
        1.618,
        1.764,
        2.0,
    )


@dataclass(frozen=True)
class Pivot:
    idx: int
    type: str
    price: float
    date: str


def round2(value: float) -> float:
    return float(np.round(float(value), 2))


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
            seen: set[str] = set()
            tickers: list[str] = []
            for value in values:
                ticker = value.upper().replace(".NS", "")
                if not ticker or ticker.lower() == "nan" or ticker in seen:
                    continue
                seen.add(ticker)
                tickers.append(ticker)
            return tickers
    raise KeyError(
        "Input file must contain a 'Ticker' or 'Symbol' column. "
        f"Found columns: {list(df.columns)}"
    )


def load_universe(path: str | Path, engine: str | None = None) -> list[str]:
    tickers = extract_tickers(read_input_table(path, engine=engine))
    if not tickers:
        raise ValueError(f"Stock list is empty: {path}")
    return tickers


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


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
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


def ohlcv_to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    frame = normalize_ohlcv(df)
    rows: list[dict[str, Any]] = []
    for stamp, row in frame.iterrows():
        rows.append(
            {
                "date": pd.Timestamp(stamp).date().isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if pd.notna(row["Volume"]) else 0.0,
            }
        )
    return rows


def fetch_history(symbol: str, config: PinballConfig) -> pd.DataFrame:
    from yahoo_bar_store import get_daily_history

    def live(_symbol: str, _period: str) -> pd.DataFrame:
        ticker = yf.Ticker(yahoo_symbol(symbol))
        end = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None).normalize() + pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=config.lookback_days)
        history = ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
        )
        if history is None or history.empty:
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        return normalize_ohlcv(history)

    cached = get_daily_history(
        symbol,
        lookback_days=config.lookback_days,
        live_loader=live,
    )
    if cached is None or cached.empty:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS))
    return normalize_ohlcv(cached)


def find_pivots(
    rows: list[dict[str, Any]],
    left: int = 5,
    right: int = 5,
) -> list[Pivot]:
    """Return alternating confirmed swing highs/lows.

    A pivot at ``i`` is only confirmed after ``right`` later bars, matching the
    original Node scanners and avoiding look-ahead on the latest incomplete swing.
    """
    raw: list[Pivot] = []
    last_index = len(rows) - right
    for index in range(left, last_index):
        is_high = True
        is_low = True
        for other in range(index - left, index + right + 1):
            if other == index:
                continue
            if rows[other]["high"] >= rows[index]["high"]:
                is_high = False
            if rows[other]["low"] <= rows[index]["low"]:
                is_low = False
        if is_high:
            raw.append(Pivot(index, "H", float(rows[index]["high"]), rows[index]["date"]))
        if is_low:
            raw.append(Pivot(index, "L", float(rows[index]["low"]), rows[index]["date"]))
    raw.sort(key=lambda item: item.idx)

    alternating: list[Pivot] = []
    for pivot in raw:
        if not alternating:
            alternating.append(pivot)
            continue
        previous = alternating[-1]
        if previous.type == pivot.type:
            if pivot.type == "H" and pivot.price > previous.price:
                alternating[-1] = pivot
            elif pivot.type == "L" and pivot.price < previous.price:
                alternating[-1] = pivot
        else:
            alternating.append(pivot)
    return alternating


def analysis_window(rows: list[dict[str, Any]], config: PinballConfig) -> list[dict[str, Any]]:
    if len(rows) > config.analysis_bars:
        return rows[-config.analysis_bars :]
    return list(rows)


def extension_levels(origin: float, amplitude: float, *, downward: bool = False) -> dict[str, float]:
    sign = -1.0 if downward else 1.0
    return {
        "e0_382": origin + sign * 0.382 * amplitude,
        "e0_618": origin + sign * 0.618 * amplitude,
        "e0_764": origin + sign * 0.764 * amplitude,
        "e1_000": origin + sign * 1.000 * amplitude,
        "e1_236": origin + sign * 1.236 * amplitude,
        "e1_382": origin + sign * 1.382 * amplitude,
        "e1_618": origin + sign * 1.618 * amplitude,
        "e1_764": origin + sign * 1.764 * amplitude,
        "e2_000": origin + sign * 2.000 * amplitude,
    }


def in_range(value: float, bound_a: float, bound_b: float) -> bool:
    lower, upper = (bound_a, bound_b) if bound_a <= bound_b else (bound_b, bound_a)
    return lower < value < upper


def sma(values: Iterable[float]) -> float:
    seq = list(values)
    if not seq:
        return float("nan")
    return float(sum(seq) / len(seq))


def categorize_waves(
    results: list[dict[str, Any]],
    order: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    wave1 = [row for row in results if "Wave 1" in str(row.get("Wave Position", ""))]
    wave3 = [row for row in results if "Wave 3" in str(row.get("Wave Position", ""))]
    wave5 = [
        row
        for row in results
        if "Wave 5" in str(row.get("Wave Position", ""))
        or "Super Extended" in str(row.get("Wave Position", ""))
    ]

    def by_confidence(row: dict[str, Any]) -> tuple[float, str]:
        return (-float(row.get("Confidence") or 0), str(row.get("Ticker") or ""))

    wave1.sort(key=by_confidence)
    wave3.sort(key=by_confidence)
    wave5.sort(key=by_confidence)
    ranked = sorted(
        results,
        key=lambda row: (
            order.get(str(row.get("Wave Position", "")), 99),
            -float(row.get("Confidence") or 0),
            str(row.get("Ticker") or ""),
        ),
    )
    return ranked, wave1, wave3, wave5


def format_frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows)
    ordered = [column for column in columns if column in frame.columns]
    extra = [column for column in frame.columns if column not in ordered]
    return frame.loc[:, ordered + extra]


def write_workbook(
    path: str | Path,
    sheets: dict[str, pd.DataFrame],
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() == ".csv":
        first = next(iter(sheets.values()))
        first.to_csv(destination, index=False)
        return
    with pd.ExcelWriter(destination, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)


def add_common_arguments(parser: argparse.ArgumentParser, *, default_output: str, default_lookback: int) -> None:
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="Path to the Nifty 500 ticker CSV/Excel file (default: ind_nifty500list.csv).",
    )
    parser.add_argument("--output", default=default_output, help="Output workbook or CSV path.")
    parser.add_argument(
        "--engine",
        default=None,
        help="Input file engine: openpyxl, xlrd, pyxlsb, odf, csv, or html.",
    )
    parser.add_argument("--lookback-days", type=int, default=default_lookback)
    parser.add_argument("--pivot-left", type=int, default=5)
    parser.add_argument("--pivot-right", type=int, default=5)
    parser.add_argument("--max-days-since-w2", type=int, default=120)
    parser.add_argument("--max-days-since-w0", type=int, default=180)
    parser.add_argument("--request-delay", type=float, default=0.0)
    parser.add_argument("--limit", type=int, default=0, help="Scan only the first N tickers (0 = all).")
    parser.add_argument(
        "--include-failures",
        action="store_true",
        help="Write skipped/non-matching names on the Skipped sheet.",
    )


def config_from_args(args: argparse.Namespace, **overrides: Any) -> PinballConfig:
    values = dict(
        lookback_days=args.lookback_days,
        pivot_left=args.pivot_left,
        pivot_right=args.pivot_right,
        max_days_since_w2=args.max_days_since_w2,
        max_days_since_w0=args.max_days_since_w0,
        request_delay=args.request_delay,
    )
    values.update(overrides)
    return PinballConfig(**values)


def scan_tickers(
    tickers: Iterable[str],
    config: PinballConfig,
    analyze: Callable[[str, list[dict[str, Any]], PinballConfig], dict[str, Any] | None],
    *,
    include_failures: bool = False,
    history_loader=None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    loader = history_loader or fetch_history
    hits: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for ticker in tickers:
        name = display_symbol(ticker)
        try:
            history = loader(ticker, config)
            if history is None or history.empty or len(history) < config.min_bars:
                skipped.append(
                    {
                        "Ticker": name,
                        "Reason": "insufficient_history",
                        "Days_of_Data": 0 if history is None or history.empty else int(len(history)),
                    }
                )
                continue
            rows = ohlcv_to_rows(history)
            result = analyze(name, rows, config)
            if result:
                result["Ticker"] = name
                hits.append(result)
            elif include_failures:
                skipped.append(
                    {
                        "Ticker": name,
                        "Reason": "no_matching_wave_structure",
                        "Days_of_Data": len(rows),
                    }
                )
        except Exception as exc:
            logging.warning("Error processing ticker %s: %s", name, exc)
            skipped.append({"Ticker": name, "Reason": f"error: {exc}", "Days_of_Data": 0})
        if config.request_delay > 0:
            time.sleep(config.request_delay)
    return hits, skipped


def run_scanner(
    *,
    title: str,
    args: argparse.Namespace,
    analyze: Callable[[str, list[dict[str, Any]], PinballConfig], dict[str, Any] | None],
    columns: list[str],
    wave_order: dict[str, int],
    config_overrides: dict[str, Any] | None = None,
) -> int:
    config = config_from_args(args, **(config_overrides or {}))
    try:
        tickers = load_universe(args.input, engine=args.engine)
    except Exception as exc:
        print(f"Input Error: {exc}")
        return 1
    if args.limit and args.limit > 0:
        tickers = tickers[: args.limit]

    print(f"{title}: scanning {len(tickers)} names from '{args.input}'...")
    hits, skipped = scan_tickers(
        tickers,
        config,
        analyze,
        include_failures=args.include_failures,
    )
    ranked, wave1, wave3, wave5 = categorize_waves(hits, wave_order)
    write_workbook(
        args.output,
        {
            "All": format_frame(ranked, columns),
            "Wave_1": format_frame(wave1, columns),
            "Wave_3": format_frame(wave3, columns),
            "Wave_5": format_frame(wave5, columns),
            "Skipped": format_frame(skipped, list(SKIPPED_COLUMNS)),
        },
    )
    print(
        f"Scan complete. hits={len(hits)} wave1={len(wave1)} wave3={len(wave3)} "
        f"wave5={len(wave5)} skipped={len(skipped)}. Saved to '{args.output}'."
    )
    return 0
