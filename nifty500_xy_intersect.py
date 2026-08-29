"""NIFTY 500 X/Y intersect screener.

The Y axis contains the entry setups and the X axis contains the mapped
profit, trailing, and ATR-based exits.  Price data is fetched from Yahoo
Finance.  The stock universe is loaded from the GitHub Excel list (or a local
CSV/Excel --input path), not from NSE.
"""

from __future__ import annotations

import argparse
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests
import yfinance as yf


DEFAULT_STOCK_LIST_URL = (
    "https://github.com/karagis75/trading/blob/main/"
    "NSE_Stocks_List_20251230_1617.xlsx"
)
LOCAL_STOCK_LIST = Path(__file__).resolve().parent / "NSE_Stocks_List_20251230_1617.xlsx"
HTTP_HEADERS = {
    "Accept": "*/*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
RESULT_COLUMNS = [
    "Ticker",
    "Price (INR)",
    "ATR",
    "Triggered Entry (Y)",
    "Assigned Exit Matrix (X)",
    "X/Y Intersect Rule",
    "Target Target Level",
]


@dataclass(frozen=True)
class IntersectScannerConfig:
    """Parameters for the indicator and signal calculations."""

    fast_dma: int = 8
    slow_dma: int = 18
    atr_period: int = 14
    adx_period: int = 14
    adx_low_threshold: float = 12.0
    adx_lookback: int = 5
    atr_target_multiple: float = 2.5
    download_period: str = "3mo"

    @property
    def minimum_history(self) -> int:
        # ADX needs an initial directional calculation and a second rolling
        # window. The extra rows make the latest signal reliably non-NaN.
        return max(self.slow_dma, self.atr_period * 2 + 1)


def to_raw_github_url(source: str) -> str:
    """Convert github.com blob URLs into raw.githubusercontent.com download URLs."""

    if "github.com/" not in source or "/blob/" not in source:
        return source

    after_host = source.split("github.com/", 1)[1]
    parts = after_host.split("/")
    if len(parts) < 5 or parts[2] != "blob":
        return source

    user, repo, _blob, ref = parts[:4]
    path = "/".join(parts[4:])
    return f"https://raw.githubusercontent.com/{user}/{repo}/{ref}/{path}"


def _namespace_symbols(symbols: list[str]) -> list[str]:
    """Uppercase symbols and append .NS when a Yahoo suffix is missing."""

    namespaced: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        cleaned = symbol.strip().upper()
        if not cleaned or cleaned in {"NAN", "NONE"}:
            continue
        if not (cleaned.endswith(".NS") or "." in cleaned or cleaned.startswith("^")):
            cleaned = f"{cleaned}.NS"
        if cleaned not in seen:
            seen.add(cleaned)
            namespaced.append(cleaned)
    return namespaced


def _symbols_from_frame(frame: pd.DataFrame) -> list[str]:
    column = next(
        (
            name
            for name in ("Ticker", "Symbol", "ticker", "symbol")
            if name in frame.columns
        ),
        None,
    )
    if column is None:
        raise ValueError("Stock list has no Ticker or Symbol column")
    return _namespace_symbols(frame[column].dropna().astype(str).tolist())


def _load_stock_list(
    source: str,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Read a CSV or Excel stock list from a URL or local path."""
    if source.startswith("http://") or source.startswith("https://"):
        url = to_raw_github_url(source)
        http = session or requests.Session()
        response = http.get(url, headers=HTTP_HEADERS, timeout=30)
        response.raise_for_status()
        if url.lower().split("?", 1)[0].endswith(".csv"):
            return pd.read_csv(io.BytesIO(response.content))
        return pd.read_excel(io.BytesIO(response.content))

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Stock list not found: {path}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


def get_nifty500_tickers(
    source: str | Path | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """Return NIFTY 500 symbols from a CSV/Excel list or GitHub URL.

    The default source is the repository workbook:
    https://github.com/karagis75/trading/blob/main/NSE_Stocks_List_20251230_1617.xlsx

    GitHub blob URLs are converted to raw download URLs. If the remote
    download fails, the local copy next to this script is used when present.
    """

    requested = str(source) if source is not None else DEFAULT_STOCK_LIST_URL
    candidates = [requested]
    default_raw = to_raw_github_url(DEFAULT_STOCK_LIST_URL)
    uses_default_list = (
        requested == DEFAULT_STOCK_LIST_URL
        or to_raw_github_url(requested) == default_raw
        or Path(requested).resolve() == LOCAL_STOCK_LIST
    )
    local_path = str(LOCAL_STOCK_LIST)
    if uses_default_list and LOCAL_STOCK_LIST.exists() and local_path not in candidates:
        candidates.append(local_path)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            tickers = _symbols_from_frame(_load_stock_list(candidate, session))
            if not tickers:
                raise ValueError(f"Stock list is empty: {candidate}")
            return tickers
        except Exception as exc:
            last_error = exc
            logging.warning("Error loading stock list from %s: %s", candidate, exc)

    if last_error is not None:
        logging.warning("Could not load NIFTY 500 ticker list: %s", last_error)
    return []


def calculate_indicators(
    df: pd.DataFrame,
    config: IntersectScannerConfig | None = None,
) -> pd.DataFrame:
    """Calculate 8/18 DMAs, ATR, and ADX without mutating the input frame."""

    settings = config or IntersectScannerConfig()
    required_columns = {"High", "Low", "Close"}
    missing = required_columns.difference(df.columns)
    if missing:
        raise ValueError(f"Historical data is missing columns: {sorted(missing)}")

    result = df.copy()
    high = pd.to_numeric(result["High"], errors="coerce")
    low = pd.to_numeric(result["Low"], errors="coerce")
    close = pd.to_numeric(result["Close"], errors="coerce")

    result[f"{settings.fast_dma}_DMA"] = close.rolling(settings.fast_dma).mean()
    result[f"{settings.slow_dma}_DMA"] = close.rolling(settings.slow_dma).mean()

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["ATR"] = true_range.rolling(settings.atr_period).mean()

    # Directional movement must compare the raw upward and downward moves.
    # Comparing a signed high diff with a negated low diff can misclassify
    # candles when one of the moves is negative.
    upward_move = high.diff()
    downward_move = low.shift(1) - low
    positive_dm = pd.Series(
        np.where(
            (upward_move > downward_move) & (upward_move > 0),
            upward_move,
            0.0,
        ),
        index=result.index,
    )
    negative_dm = pd.Series(
        np.where(
            (downward_move > upward_move) & (downward_move > 0),
            downward_move,
            0.0,
        ),
        index=result.index,
    )

    tr_smooth = true_range.rolling(settings.adx_period).sum()
    plus_di = 100 * (
        positive_dm.rolling(settings.adx_period).sum() / tr_smooth.replace(0, np.nan)
    )
    minus_di = 100 * (
        negative_dm.rolling(settings.adx_period).sum() / tr_smooth.replace(0, np.nan)
    )
    directional_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / directional_sum
    # A fully flat window has no directional bias, so its DX is zero. Keep
    # the initial warm-up NaNs intact while avoiding a permanently NaN ADX
    # after a flat period.
    dx = dx.mask(tr_smooth.notna() & directional_sum.isna(), 0.0)
    result["ADX"] = dx.rolling(settings.adx_period).mean()

    return result


def _normalise_yfinance_columns(data: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Convert yfinance's single- and multi-ticker column formats to OHLCV."""

    result = data.copy()
    if isinstance(result.columns, pd.MultiIndex):
        ticker_levels = [
            level
            for level in range(result.columns.nlevels)
            if ticker in result.columns.get_level_values(level)
        ]
        if ticker_levels:
            result = result.xs(ticker, axis=1, level=ticker_levels[-1])
        else:
            result.columns = result.columns.get_level_values(0)
    return result


def _download_history(ticker: str, config: IntersectScannerConfig) -> pd.DataFrame:
    """Download daily history for one Yahoo Finance ticker."""

    data = yf.download(
        ticker,
        period=config.download_period,
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    return _normalise_yfinance_columns(data, ticker)


def evaluate_intersect_signal(
    ticker: str,
    df: pd.DataFrame,
    config: IntersectScannerConfig | None = None,
) -> dict[str, Any] | None:
    """Evaluate entry setups and return their mandatory X/Y exit mapping."""

    settings = config or IntersectScannerConfig()
    if len(df) < settings.minimum_history:
        return None

    current = df.iloc[-1]
    previous = df.iloc[-2]
    recent_adx = df["ADX"].iloc[-settings.adx_lookback :]
    fast_dma_column = f"{settings.fast_dma}_DMA"
    slow_dma_column = f"{settings.slow_dma}_DMA"
    values = current[["Close", fast_dma_column, slow_dma_column, "ATR", "ADX"]]
    if values.isna().any() or previous[[slow_dma_column, "ADX"]].isna().any():
        return None

    adx_low_recently = recent_adx.lt(settings.adx_low_threshold).any()
    adx_turning_up = current["ADX"] > previous["ADX"]
    dma_18_rising = current[slow_dma_column] > previous[slow_dma_column]
    current_distance = abs(current[fast_dma_column] - current[slow_dma_column])
    previous_distance = abs(previous[fast_dma_column] - previous[slow_dma_column])
    dma_diverging = current_distance > previous_distance
    a_adx_signal = (
        adx_low_recently and adx_turning_up and dma_18_rising and dma_diverging
    )

    in_uptrend = current[fast_dma_column] > current[slow_dma_column] and dma_18_rising
    retest_8dma = (
        current["Low"] <= current[fast_dma_column]
        and current["Close"] >= current[fast_dma_column] * 0.995
    )
    retest_18dma = (
        current["Low"] <= current[slow_dma_column]
        and current["Close"] >= current[slow_dma_column] * 0.995
    )
    c_mar_signal = in_uptrend and (retest_8dma or retest_18dma)

    if not (a_adx_signal or c_mar_signal):
        return None

    entry_technique = (
        "A_ADX (Anticipatory)" if a_adx_signal else "C_MAR (Retest)"
    )
    close_price = float(current["Close"])
    atr_value = float(current["ATR"])
    trailing_exit_dma = (
        float(current[fast_dma_column])
        if a_adx_signal
        else float(current[slow_dma_column])
    )
    target_100bp = round(close_price * 1.01, 2)
    factor_target = round(close_price + atr_value * settings.atr_target_multiple, 2)

    return {
        "Ticker": ticker.removesuffix(".NS"),
        "Price (INR)": round(close_price, 2),
        "ATR": round(atr_value, 2),
        "Triggered Entry (Y)": entry_technique,
        "Assigned Exit Matrix (X)": "100BP / Target Target / DMA Exits",
        "X/Y Intersect Rule": (
            f"Entry [{entry_technique}] governed by "
            f"Exits: [100BP Target: {target_100bp}] OR "
            f"[DMA Stop: {round(trailing_exit_dma, 2)}]"
        ),
        "Target Target Level": factor_target,
    }


Downloader = Callable[[str, IntersectScannerConfig], pd.DataFrame]


def screen_stocks_with_intersect(
    tickers: list[str],
    config: IntersectScannerConfig | None = None,
    downloader: Downloader | None = None,
) -> pd.DataFrame:
    """Screen tickers while isolating download failures to individual symbols."""

    settings = config or IntersectScannerConfig()
    fetch = downloader or _download_history
    results: list[dict[str, Any]] = []

    print(f"Starting screen for {len(tickers)} stocks with X/Y Intersect checks...")
    for index, ticker in enumerate(tickers, 1):
        if index % 50 == 0:
            print(f"Processed {index}/{len(tickers)} stocks...")
        try:
            data = fetch(ticker, settings)
            if data.empty or len(data) < settings.minimum_history:
                continue
            indicators = calculate_indicators(data, settings)
            signal = evaluate_intersect_signal(ticker, indicators, settings)
            if signal is not None:
                results.append(signal)
        except Exception as exc:
            logging.warning("Error processing ticker %s: %s", ticker, exc)

    return pd.DataFrame(results, columns=RESULT_COLUMNS)


def write_signals_csv(path: str | Path, signals: pd.DataFrame) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame = signals if not signals.empty else pd.DataFrame(columns=RESULT_COLUMNS)
    frame.to_csv(destination, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan NIFTY 500 stocks for X/Y intersect entry and exit signals."
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_STOCK_LIST_URL,
        help=(
            "CSV/Excel file or GitHub URL with a Ticker/Symbol column. "
            "Defaults to the repository NSE stocks list."
        ),
    )
    parser.add_argument(
        "--output",
        default="nifty500_xy_matrix_signals.csv",
        help="CSV path for the signal matrix.",
    )
    args = parser.parse_args()

    tickers = get_nifty500_tickers(args.input)
    if not tickers:
        print("Could not load the NIFTY 500 ticker list from the Excel source.")
        return

    print(f"Loaded {len(tickers)} symbols from {args.input}")

    signals = screen_stocks_with_intersect(tickers)
    print("\n=== NIFTY 500 FACTOR MATRIX INTERSECT RESULTS ===")
    output_path = Path(args.output)
    if signals.empty:
        print("No NIFTY 500 stocks met your rule configurations today.")
        write_signals_csv(output_path, signals)
        print(f"\nMatrix successfully saved to '{output_path}'")
        return

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)
    print(signals.to_string(index=False))
    write_signals_csv(output_path, signals)
    print(f"\nMatrix successfully saved to '{output_path}'")


if __name__ == "__main__":
    main()
