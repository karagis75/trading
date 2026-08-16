import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class BearishScannerConfig:
    min_adx: float = 20.0
    cci_threshold: float = -100.0
    cci_period: int = 14
    adx_period: int = 14
    ema_fast: int = 9
    ema_medium: int = 18
    ema_slow: int = 50
    lookback_period: str = "1y"


def calculate_indicators(df: pd.DataFrame, config: BearishScannerConfig) -> pd.DataFrame:
    """Calculates EMAs (9, 18, 50), CCI (14), ADX (14), and Fibonacci Pivot Point S1."""
    df = df.copy()

    # 1. Exponential Moving Averages (EMAs)
    df["EMA9"] = df["Close"].ewm(span=config.ema_fast, adjust=False).mean()
    df["EMA18"] = df["Close"].ewm(span=config.ema_medium, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=config.ema_slow, adjust=False).mean()

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

    # 4. Fibonacci Pivot Points (Calculated on previous day's High, Low, Close)
    prev_high = df["High"].shift(1)
    prev_low = df["Low"].shift(1)
    prev_close = df["Close"].shift(1)
    
    pivot = (prev_high + prev_low + prev_close) / 3
    df["Fib_S1"] = pivot - 0.382 * (prev_high - prev_low)

    return df


def evaluate_bearish_setup(symbol: str, df: pd.DataFrame, config: BearishScannerConfig) -> dict[str, Any] | None:
    """Evaluates systematic bearish momentum criteria on the latest daily bar."""
    if len(df) < 50:
        return None

    curr = df.iloc[-1]

    # Condition 1: ADX > 20 (Strong Trend Strength)
    adx_pass = curr["ADX"] >= config.min_adx

    # Condition 2: CCI <= -100 (Strong Downward Momentum / Bearish Trigger)
    cci_pass = curr["CCI"] <= config.cci_threshold

    # Condition 3: Price below 9, 18, and 50 EMAs with Bearish Stack (50 > 18 > 9 > Price)
    ema_alignment_pass = (
        curr["EMA50"] > curr["EMA18"]
        and curr["EMA18"] > curr["EMA9"]
        and curr["Close"] < curr["EMA9"]
    )

    # Condition 4: Price below Fibonacci Support 1 (S1 Breakdown)
    fib_s1_breakdown = curr["Close"] < curr["Fib_S1"]

    # All strategic rules must pass
    is_confirmed = adx_pass and cci_pass and ema_alignment_pass and fib_s1_breakdown

    if not is_confirmed:
        return None

    return {
        "Ticker": symbol,
        "Close Price": round(curr["Close"], 2),
        "Fib S1": round(curr["Fib_S1"], 2),
        "EMA9": round(curr["EMA9"], 2),
        "EMA18": round(curr["EMA18"], 2),
        "EMA50": round(curr["EMA50"], 2),
        "CCI (14)": round(curr["CCI"], 2),
        "ADX (14)": round(curr["ADX"], 2),
        "Aggressive SL (18 EMA)": round(curr["EMA18"], 2),
        "Swing SL (50 EMA)": round(curr["EMA50"], 2),
        "Setup Status": "Confirmed Bearish Breakdown",
    }


def analyze_symbol(symbol: str, config: BearishScannerConfig) -> dict[str, Any] | None:
    """Fetches stock data and evaluates bearish momentum criteria."""
    formatted_ticker = symbol if ("." in symbol or symbol.startswith("^")) else f"{symbol}.NS"
    try:
        ticker = yf.Ticker(formatted_ticker)
        df = ticker.history(period=config.lookback_period, interval="1d")

        if df.empty or len(df) < 50:
            logging.warning(f"Insufficient historical data for {symbol}")
            return None

        df = calculate_indicators(df, config)
        return evaluate_bearish_setup(symbol, df, config)

    except Exception as exc:
        logging.warning(f"Error processing ticker {symbol}: {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan tickers from Excel for Bearish Momentum Breakdowns.")
    parser.add_argument("--input", default="NSE_Stocks_List_20251230_1617.xlsx", help="Path to input Excel file.")
    parser.add_argument("--output", default="Bearish_Momentum_Analysis.xlsx", help="Output path for results.")
    args = parser.parse_args()

    config = BearishScannerConfig()

    try:
        df_input = pd.read_excel(args.input)
        tickers = df_input["Ticker"].dropna().astype(str).tolist()
    except Exception as e:
        print(f"Excel Error: {e}")
        return

    results = []
    print(f"Scanning {len(tickers)} stocks for Bearish Momentum (ADX > 20, CCI < -100, Price < EMAs & S1)...")

    for ticker in tickers:
        data = analyze_symbol(ticker, config)
        if data:
            results.append(data)

    if not results:
        print("No qualifying bearish breakdown setups found.")
        return

    output_path = Path(args.output)
    df_results = pd.DataFrame(results).sort_values(by=["ADX (14)", "CCI (14)"], ascending=[False, True])

    if output_path.suffix.lower() == ".csv":
        df_results.to_csv(output_path, index=False)
    else:
        df_results.to_excel(output_path, index=False)

    print(f"Scan complete. Found {len(results)} bearish candidate(s). Saved to '{output_path}'.")


if __name__ == "__main__":
    main()