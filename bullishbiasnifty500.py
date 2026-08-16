import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class BullishScannerConfig:
    min_adx: float = 20.0
    cci_period: int = 20
    adx_period: int = 14
    ema_fast: int = 9
    ema_medium: int = 18
    ema_slow: int = 50
    ema_macro: int = 200
    lookback_period: str = "1y"


def calculate_indicators(df: pd.DataFrame, config: BullishScannerConfig) -> pd.DataFrame:
    """Calculates EMAs, CCI, and ADX for given price data."""
    df = df.copy()

    # 1. Exponential Moving Averages (EMAs)
    df["EMA9"] = df["Close"].ewm(span=config.ema_fast, adjust=False).mean()
    df["EMA18"] = df["Close"].ewm(span=config.ema_medium, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=config.ema_slow, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=config.ema_macro, adjust=False).mean()

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


def evaluate_bullish_bias(symbol: str, df: pd.DataFrame, config: BullishScannerConfig) -> dict[str, Any] | None:
    """Evaluates systematic bullish criteria on historical price data."""
    if len(df) < config.ema_macro:
        return None

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Rule 1: EMA Alignment & Stack Order (Price > 9 > 18 > 50 > 200)
    price_above_emas = (
        curr["Close"] > curr["EMA9"]
        and curr["EMA9"] > curr["EMA18"]
        and curr["EMA18"] > curr["EMA50"]
        and curr["EMA50"] > curr["EMA200"]
    )

    # Anchor condition: 200 EMA is flat or sloping upward
    ema200_sloping_up = curr["EMA200"] >= df.iloc[-5]["EMA200"]
    ema_aligned = price_above_emas and ema200_sloping_up

    # Rule 2: CCI Momentum Trigger & Sustained Check
    cci_trigger = (prev["CCI"] <= 100 and curr["CCI"] > 100) or (curr["CCI"] > 100)

    # Rule 3: Bullish Candle Check
    body_size = abs(curr["Close"] - curr["Open"])
    candle_range = curr["High"] - curr["Low"]
    is_bullish_candle = (curr["Close"] > curr["Open"]) and (body_size / candle_range >= 0.5 if candle_range > 0 else True)
    riding_ema9 = curr["Low"] >= curr["EMA9"] or curr["Close"] > curr["EMA9"]

    # Rule 4: ADX Filter
    adx_pass = curr["ADX"] >= config.min_adx

    # Combined Strategy Signal Validation
    is_confirmed = ema_aligned and cci_trigger and is_bullish_candle and riding_ema9 and adx_pass

    if not is_confirmed:
        return None

    return {
        "Ticker": symbol,
        "Close Price": round(curr["Close"], 2),
        "EMA9": round(curr["EMA9"], 2),
        "EMA18": round(curr["EMA18"], 2),
        "EMA50": round(curr["EMA50"], 2),
        "EMA200": round(curr["EMA200"], 2),
        "CCI": round(curr["CCI"], 2),
        "ADX": round(curr["ADX"], 2),
        "Aggressive SL (18 EMA)": round(curr["EMA18"], 2),
        "Swing SL (50 EMA)": round(curr["EMA50"], 2),
        "Status": "Confirmed Bullish",
    }


def analyze_symbol(symbol: str, config: BullishScannerConfig) -> dict[str, Any] | None:
    """Fetches stock data using yfinance and runs indicator evaluation."""
    # Ensure ticker has appropriate extension for NSE if not provided
    formatted_ticker = symbol if ("." in symbol or symbol.startswith("^")) else f"{symbol}.NS"
    try:
        ticker = yf.Ticker(formatted_ticker)
        df = ticker.history(period=config.lookback_period, interval="1d")

        if df.empty or len(df) < config.ema_macro:
            logging.warning(f"Insufficient historical data for {symbol}")
            return None

        df = calculate_indicators(df, config)
        return evaluate_bullish_bias(symbol, df, config)

    except Exception as exc:
        logging.warning(f"Error processing ticker {symbol}: {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan tickers from Excel file for Bullish EMA/CCI/ADX Setup.")
    parser.add_argument("--input", default="NSE_Stocks_List_20251230_1617.xlsx", help="Path to input Excel file.")
    parser.add_argument("--output", default="Bullish_Bias_Analysis.xlsx", help="Output path for results.")
    args = parser.parse_args()

    config = BullishScannerConfig()

    try:
        df_input = pd.read_excel(args.input)
        tickers = df_input["Ticker"].dropna().astype(str).tolist()
    except Exception as e:
        print(f"Excel Error: {e}")
        return

    results = []
    print(f"Scanning {len(tickers)} stocks for Bullish Structure (EMAs + CCI + ADX)...")

    for ticker in tickers:
        data = analyze_symbol(ticker, config)
        if data:
            results.append(data)

    if not results:
        print("No qualifying bullish setups found.")
        return

    output_path = Path(args.output)
    df_results = pd.DataFrame(results).sort_values(by=["ADX", "CCI"], ascending=False)
    
    if output_path.suffix.lower() == ".csv":
        df_results.to_csv(output_path, index=False)
    else:
        df_results.to_excel(output_path, index=False)

    print(f"Scan complete. Found {len(results)} bullish setup(s). Saved to '{output_path}'.")


if __name__ == "__main__":
    main()