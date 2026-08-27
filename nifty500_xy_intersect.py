"""NIFTY 500 X/Y intersect screener.

The Y axis contains the entry setups and the X axis contains the mapped
profit, trailing, and ATR-based exits.  Market data is fetched from NSE and
Yahoo Finance when the module is run as a script.
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


NSE_HOME_URL = "https://www.nseindia.com"
NIFTY_500_API_URL = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"
NSE_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_HOME_URL + "/",
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


def get_nifty500_tickers(
    session: requests.Session | None = None,
) -> list[str]:
    """Return current NIFTY 500 symbols from NSE's official API.

    NSE generally requires a homepage request before its API will return
    data. The parser also accepts a CSV response, which keeps this function
    compatible with official list-download responses and straightforward to
    test.
    """

    http = session or requests.Session()
    try:
        http.get(NSE_HOME_URL, headers=NSE_HEADERS, timeout=30)
        response = http.get(NIFTY_500_API_URL, headers=NSE_HEADERS, timeout=30)
        response.raise_for_status()

        symbols: list[str]
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            payload = None

        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            symbols = [
                str(item["symbol"]).strip().upper()
                for item in payload["data"]
                if isinstance(item, dict) and item.get("symbol")
            ]
        else:
            csv_df = pd.read_csv(io.StringIO(response.text))
            symbol_column = next(
                (column for column in ("Symbol", "symbol") if column in csv_df.columns),
                None,
            )
            if symbol_column is None:
                raise ValueError("NSE response has no Symbol column")
            symbols = (
                csv_df[symbol_column]
                .dropna()
                .astype(str)
                .str.strip()
                .str.upper()
                .tolist()
            )

        return [symbol if symbol.endswith(".NS") else f"{symbol}.NS" for symbol in symbols]
    except Exception as exc:
        logging.warning("Error fetching NIFTY 500 list from NSE: %s", exc)
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
    values = current[["Close", "8_DMA", "18_DMA", "ATR", "ADX"]]
    if values.isna().any() or previous[["18_DMA", "ADX"]].isna().any():
        return None

    adx_low_recently = recent_adx.lt(settings.adx_low_threshold).any()
    adx_turning_up = current["ADX"] > previous["ADX"]
    dma_18_rising = current["18_DMA"] > previous["18_DMA"]
    current_distance = abs(current["8_DMA"] - current["18_DMA"])
    previous_distance = abs(previous["8_DMA"] - previous["18_DMA"])
    dma_diverging = current_distance > previous_distance
    a_adx_signal = (
        adx_low_recently and adx_turning_up and dma_18_rising and dma_diverging
    )

    in_uptrend = current["8_DMA"] > current["18_DMA"] and dma_18_rising
    retest_8dma = (
        current["Low"] <= current["8_DMA"]
        and current["Close"] >= current["8_DMA"] * 0.995
    )
    retest_18dma = (
        current["Low"] <= current["18_DMA"]
        and current["Close"] >= current["18_DMA"] * 0.995
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
        float(current["8_DMA"]) if a_adx_signal else float(current["18_DMA"])
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan NIFTY 500 stocks for X/Y intersect entry and exit signals."
    )
    parser.add_argument(
        "--output",
        default="nifty500_xy_matrix_signals.csv",
        help="CSV path for the signal matrix.",
    )
    args = parser.parse_args()

    tickers = get_nifty500_tickers()
    if not tickers:
        print("Could not fetch the NIFTY 500 ticker list.")
        return

    signals = screen_stocks_with_intersect(tickers)
    print("\n=== NIFTY 500 FACTOR MATRIX INTERSECT RESULTS ===")
    if signals.empty:
        print("No NIFTY 500 stocks met your rule configurations today.")
        return

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)
    print(signals.to_string(index=False))
    output_path = Path(args.output)
    signals.to_csv(output_path, index=False)
    print(f"\nMatrix successfully saved to '{output_path}'")


if __name__ == "__main__":
    main()
