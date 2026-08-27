import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from trading_utils import normalize_nse_ticker


@dataclass(frozen=True)
class StrangleScannerConfig:
    adx_threshold: float = 15.0
    adx_lookback: int = 3
    cci_bound: float = 50.0
    ema_braid_max_diff_pct: float = 0.012  # Max 1.2% difference between EMAs
    box_days: int = 6  # 5-to-7 day window
    box_max_range_pct: float = 0.035  # Max 3.5% high-to-low range box
    cci_period: int = 14
    adx_period: int = 14
    lookback_period: str = "1y"


def calculate_indicators(df: pd.DataFrame, config: StrangleScannerConfig) -> pd.DataFrame:
    """Calculates EMAs (9, 18, 50), CCI (14), and ADX (14)."""
    df = df.copy()

    # 1. Exponential Moving Averages (EMAs)
    df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA18"] = df["Close"].ewm(span=18, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    # 2. Commodity Channel Index (CCI)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    sma_tp = tp.rolling(window=config.cci_period).mean()
    mad = tp.rolling(window=config.cci_period).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    df["CCI"] = (tp - sma_tp) / (0.015 * mad)

    # 3. Average Directional Index (ADX)
    high_diff = df["High"].diff()
    low_diff = -df["Low"].diff()

    pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)

    tr1 = df["High"] - df["Low"]
    tr2 = np.abs(df["High"] - df["Close"].shift(1))
    tr3 = np.abs(df["Low"] - df["Close"].shift(1))
    tr = pd.DataFrame({"tr1": tr1, "tr2": tr2, "tr3": tr3}).max(axis=1)

    atr = tr.rolling(window=config.adx_period).mean()
    pos_di = 100 * (pd.Series(pos_dm, index=df.index).rolling(window=config.adx_period).mean() / atr)
    neg_di = 100 * (pd.Series(neg_dm, index=df.index).rolling(window=config.adx_period).mean() / atr)

    dx = 100 * (np.abs(pos_di - neg_di) / (pos_di + neg_di))
    df["ADX"] = dx.rolling(window=config.adx_period).mean()

    return df


def evaluate_strangle_setup(symbol: str, df: pd.DataFrame, config: StrangleScannerConfig) -> dict[str, Any] | None:
    """Evaluates non-directional long strangle conditions."""
    if len(df) < 50:
        return None

    recent_df = df.iloc[-config.adx_lookback:]
    curr = df.iloc[-1]

    # Condition 1: ADX flipped below 15 within the last 1-3 days
    adx_window = df["ADX"].iloc[-(config.adx_lookback + 1):]
    adx_below_threshold = any(
        previous >= config.adx_threshold and current < config.adx_threshold
        for previous, current in zip(adx_window.iloc[:-1], adx_window.iloc[1:])
    )

    # Condition 2: CCI oscillating tightly between -50 and +50
    cci_tight = recent_df["CCI"].between(-config.cci_bound, config.cci_bound).all()

    # Condition 3: EMA Braid (9, 18, 50 converging tightly together)
    ema_min = min(curr["EMA9"], curr["EMA18"], curr["EMA50"])
    ema_max = max(curr["EMA9"], curr["EMA18"], curr["EMA50"])
    ema_spread_pct = (ema_max - ema_min) / curr["Close"]
    ema_braid_pass = ema_spread_pct <= config.ema_braid_max_diff_pct

    # Condition 4: Price Action in a 5-to-7 day narrow horizontal box
    box_df = df.iloc[-config.box_days:]
    box_high = box_df["High"].max()
    box_low = box_df["Low"].min()
    box_range_pct = (box_high - box_low) / curr["Close"]
    box_pass = box_range_pct <= config.box_max_range_pct

    # All strategic rules must pass
    is_confirmed = adx_below_threshold and cci_tight and ema_braid_pass and box_pass

    if not is_confirmed:
        return None

    return {
        "Ticker": symbol,
        "Close Price": round(curr["Close"], 2),
        "ADX (14)": round(curr["ADX"], 2),
        "CCI (14)": round(curr["CCI"], 2),
        "EMA Braid Spread %": round(ema_spread_pct * 100, 2),
        "Box Range %": round(box_range_pct * 100, 2),
        "Box High": round(box_high, 2),
        "Box Low": round(box_low, 2),
        "Setup Status": "Strangle Compression Confirmed",
    }


def analyze_symbol(symbol: str, config: StrangleScannerConfig) -> dict[str, Any] | None:
    """Fetches stock data and evaluates strangle setup criteria."""
    formatted_ticker = normalize_nse_ticker(symbol)
    try:
        ticker = yf.Ticker(formatted_ticker)
        df = ticker.history(period=config.lookback_period, interval="1d")

        if df.empty or len(df) < 50:
            logging.warning(f"Insufficient historical data for {symbol}")
            return None

        df = calculate_indicators(df, config)
        return evaluate_strangle_setup(symbol, df, config)

    except Exception as exc:
        logging.warning(f"Error processing ticker {symbol}: {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan tickers from Excel for Long Strangle Compression Setups.")
    parser.add_argument("--input", default="NSE_Stocks_List_20251230_1617.xlsx", help="Path to input Excel file.")
    parser.add_argument("--output", default="Strangle_Candidate_Analysis.xlsx", help="Output path for results.")
    args = parser.parse_args()

    config = StrangleScannerConfig()

    try:
        df_input = pd.read_excel(args.input)
        tickers = df_input["Ticker"].dropna().astype(str).tolist()
    except Exception as e:
        print(f"Excel Error: {e}")
        return

    results = []
    print(f"Scanning {len(tickers)} stocks for Strangle Compression Setups (Low ADX + Tight CCI + EMA Braid)...")

    for ticker in tickers:
        data = analyze_symbol(ticker, config)
        if data:
            results.append(data)

    if not results:
        print("No qualifying strangle compression setups found.")
        return

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df_results = pd.DataFrame(results).sort_values(by=["ADX (14)", "EMA Braid Spread %"], ascending=True)

    if output_path.suffix.lower() == ".csv":
        df_results.to_csv(output_path, index=False)
    else:
        df_results.to_excel(output_path, index=False)

    print(f"Scan complete. Found {len(results)} strangle candidate(s). Saved to '{output_path}'.")


if __name__ == "__main__":
    main()