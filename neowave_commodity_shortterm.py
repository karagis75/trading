#!/usr/bin/env python3
"""Rule-based Adaptive NeoWave-style chart for Global Commodity Futures.

Examples
--------
python neowave_commodity.py CRUDE
python neowave_commodity.py GOLD --period 2y
python neowave_commodity.py NG --pivot 8
python neowave_commodity.py SILVER --show

Install once:
    pip install yfinance pandas numpy matplotlib
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
import yfinance as yf

# Mapping easy commodity aliases to continuous front-month Yahoo Finance futures contract symbols
COMMODITY_ALIASES = {
    "CRUDE": "CL=F",
    "CRUDEOIL": "CL=F",
    "CRUDE OIL": "CL=F",
    "WTI": "CL=F",
    "NG": "NG=F",
    "NATURALGAS": "NG=F",
    "NATURAL GAS": "NG=F",
    "GAS": "NG=F",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
}

RETRACEMENTS = (0.236, 0.382, 0.500, 0.618, 0.786)
TARGETS = (1.000, 1.236, 1.382, 1.618, 1.764, 2.000)


def yahoo_symbol(symbol: str) -> str:
    """Map friendly names to Yahoo Finance continuous commodity ticker codes."""
    cleaned = symbol.strip().upper()
    if cleaned in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[cleaned]
    return cleaned


def load_ohlc(symbol: str, period: str, interval: str) -> pd.DataFrame:
    ticker = yahoo_symbol(symbol)
    frame = yf.download(ticker, period=period, interval=interval, auto_adjust=False,
                        progress=False, threads=False)
    if frame.empty:
        raise RuntimeError(f"No price data returned for {symbol} ({ticker}).")
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise RuntimeError(f"Price data is missing columns: {', '.join(missing)}")
    return frame.dropna(subset=required).copy()


def check_bearish_regime_50ema(frame: pd.DataFrame) -> bool:
    """Evaluate regime using the 50 EMA on the current dataset timeframe."""
    if len(frame) < 50:
        print("Warning: Insufficient data to calculate 50 EMA. Defaulting to Bullish.")
        return False
        
    close = frame["Close"].astype(float)
    ema_50 = close.ewm(span=50, adjust=False).mean()
    
    current_price = float(close.iloc[-1])
    current_ema = float(ema_50.iloc[-1])
    
    return current_price < current_ema


def automatic_pivot_width(frame: pd.DataFrame) -> int:
    if len(frame) < 14:
        raise ValueError("At least 14 price bars are required for automatic pivot width.")
    close = frame["Close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat([
        frame["High"] - frame["Low"],
        (frame["High"] - previous_close).abs(),
        (frame["Low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr_percent = float((true_range.rolling(14).mean() / close).iloc[-1] * 100)
    if not np.isfinite(atr_percent):
        raise ValueError("Automatic pivot width requires finite OHLC data.")
    history_component = len(frame) / 180
    volatility_component = atr_percent * 0.8
    return int(np.clip(round(4 + history_component + volatility_component), 5, 18))


def confirmed_pivots(frame: pd.DataFrame, width: int) -> list[dict]:
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    raw: list[dict] = []
    for i in range(width, len(frame) - width):
        low_window = lows[i - width:i + width + 1]
        high_window = highs[i - width:i + width + 1]
        if lows[i] == low_window.min() and np.count_nonzero(low_window == lows[i]) == 1:
            raw.append({"index": i, "kind": "L", "price": lows[i]})
        if highs[i] == high_window.max() and np.count_nonzero(high_window == highs[i]) == 1:
            raw.append({"index": i, "kind": "H", "price": highs[i]})
    raw.sort(key=lambda item: item["index"])

    filtered: list[dict] = []
    for pivot in raw:
        if not filtered or pivot["kind"] != filtered[-1]["kind"]:
            filtered.append(pivot)
        elif pivot["kind"] == "H" and pivot["price"] > filtered[-1]["price"]:
            filtered[-1] = pivot
        elif pivot["kind"] == "L" and pivot["price"] < filtered[-1]["price"]:
            filtered[-1] = pivot
    return filtered


def latest_impulse(pivots: list[dict], bearish: bool) -> tuple[dict, dict] | None:
    """Find the true absolute macro structural anchors from the recent active window period."""
    shown_pivots = pivots[-8:]  # Filter to the recent active segment window
    if len(shown_pivots) < 2:
        return None
        
    high_pivots = [p for p in shown_pivots if p["kind"] == "H"]
    low_pivots = [p for p in shown_pivots if p["kind"] == "L"]
    
    if not high_pivots or not low_pivots:
        return None
        
    if bearish:
        # Macro Bearish: Find absolute highest high and absolute lowest low in the wave period
        macro_high = max(high_pivots, key=lambda x: x["price"])
        macro_low = min(low_pivots, key=lambda x: x["price"])
        if macro_high["index"] < macro_low["index"]:
            return macro_high, macro_low
    else:
        # Macro Bullish: Find absolute lowest low and absolute highest high in the wave period
        macro_low = min(low_pivots, key=lambda x: x["price"])
        macro_high = max(high_pivots, key=lambda x: x["price"])
        if macro_low["index"] < macro_high["index"]:
            return macro_low, macro_high
        
    return None


def calculate_fib_levels(pivots: list[dict], bearish: bool) -> tuple[dict, dict, dict] | None:
    """Build structural Fibonacci references based on dynamic price layout direction."""
    impulse = latest_impulse(pivots, bearish)
    if not impulse:
        return None
    start, end = impulse
    
    if bearish:
        move = start["price"] - end["price"]
        retracements = {ratio: end["price"] + move * ratio for ratio in RETRACEMENTS}
        targets = {ratio: start["price"] - move * ratio for ratio in TARGETS}
    else:
        move = end["price"] - start["price"]
        retracements = {ratio: end["price"] - move * ratio for ratio in RETRACEMENTS}
        targets = {ratio: start["price"] + move * ratio for ratio in TARGETS}
        
    return {"start": start, "end": end}, retracements, targets


def draw_candles(ax: plt.Axes, frame: pd.DataFrame) -> None:
    dates = mdates.date2num(frame.index.to_pydatetime())
    width = 0.65 if len(frame) < 300 else 0.45
    for x, (_, row) in zip(dates, frame.iterrows()):
        up = row["Close"] >= row["Open"]
        colour = "#1f9d55" if up else "#d64545"
        ax.vlines(x, row["Low"], row["High"], color=colour, linewidth=0.7, zorder=1)
        body_low = min(row["Open"], row["Close"])
        body_height = max(abs(row["Close"] - row["Open"]), 0.01)
        ax.add_patch(Rectangle((x - width / 2, body_low), width, body_height,
                               facecolor=colour, edgecolor=colour, alpha=0.85, zorder=2))


def plot_chart(frame: pd.DataFrame, symbol: str, pivot_width: int, is_bearish: bool, output: Path, show: bool = False) -> None:
    pivots = confirmed_pivots(frame, pivot_width)
    shown_pivots = pivots[-8:]
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#111827")
    draw_candles(ax, frame)

    close = frame["Close"]
    ax.plot(frame.index, close.ewm(span=20, adjust=False).mean(), color="#f4c430", lw=1.1, label="EMA 20")
    ax.plot(frame.index, close.ewm(span=50, adjust=False).mean(), color="#60a5fa", lw=1.3, label="EMA 50")

    if shown_pivots:
        xs = [frame.index[pivot["index"]] for pivot in shown_pivots]
        ys = [pivot["price"] for pivot in shown_pivots]
        ax.plot(xs, ys, color="#e879f9", lw=1.6, marker="o", ms=4, label="Confirmed swing path")
        for count, pivot in enumerate(shown_pivots, start=max(len(pivots) - len(shown_pivots) + 1, 1)):
            label = f"{pivot['kind']}{count}"
            offset = 12 if pivot["kind"] == "H" else -18
            ax.annotate(label, (frame.index[pivot["index"]], pivot["price"]), xytext=(0, offset),
                        textcoords="offset points", ha="center", color="white", fontsize=9,
                        bbox={"boxstyle": "round,pad=0.18", "fc": "#7c3aed", "ec": "none", "alpha": 0.9})

    fib = calculate_fib_levels(pivots, is_bearish)
    mode_prefix = "Bear" if is_bearish else "Bull"
    
    if fib:
        anchors, retracements, targets = fib
        start_date = frame.index[anchors["start"]["index"]]
        end_date = frame.index[anchors["end"]["index"]]
        
        # Lookback validation timeline calculation anchor strategy
        short_term_lookback = min(60, len(frame) - anchors["start"]["index"])
        visualization_start_date = frame.index[-short_term_lookback]
        
        ret_color = "#f472b6" if is_bearish else "#60a5fa"      
        target_color = "#ef4444" if is_bearish else "#22c55e"   
        invalid_color = "#22c55e" if is_bearish else "#ef4444"  
        
        for ratio, price in retracements.items():
            ax.hlines(price, visualization_start_date, frame.index[-1], colors=ret_color, linestyles="--", lw=0.85, alpha=0.8)
            ax.text(frame.index[-1], price, f"  {mode_prefix} retr {ratio:.3f}: {price:.2f}", color=ret_color, va="center", fontsize=8)
            
        for ratio, price in targets.items():
            is_anchor_edge = ratio == 1.0
            ax.hlines(price, visualization_start_date, frame.index[-1], colors=target_color, linestyles="-" if is_anchor_edge else "-.",
                      lw=1.25 if is_anchor_edge else 0.9, alpha=0.9)
            ax.text(frame.index[-1], price, f"  {mode_prefix} {'L' if is_bearish and is_anchor_edge else 'H' if is_anchor_edge else 'target'} {ratio:.3f}: {price:.2f}",
                    color=target_color, va="center", fontsize=8)
            
        structural_anchor = anchors["start"]["price"]
        ax.hlines(structural_anchor, visualization_start_date, frame.index[-1], colors=invalid_color, linestyles=":", lw=1.1, alpha=0.9)
        ax.text(frame.index[-1], structural_anchor, f"  {mode_prefix} structure invalidation: {structural_anchor:.2f}", color=invalid_color, va="center", fontsize=8)
        
        direction_text = f"H → {end_date:%d-%b-%Y} L" if is_bearish else f"L → {end_date:%d-%b-%Y} H"
        ax.text(0.01, 0.02,
                f"Confirmed {mode_prefix.lower()} impulse: {start_date:%d-%b-%Y} {direction_text}. Targets conditional; invalidation past structure anchor.",
                transform=ax.transAxes, color="#cbd5e1", fontsize=8.5)
    else:
        ax.text(0.01, 0.02, f"No confirmed {mode_prefix.lower()} impulse found in this range history.",
                transform=ax.transAxes, color="#fca5a5", fontsize=9)

    ticker = yahoo_symbol(symbol)
    regime_lbl = "BEARISH REGIME (Close < 50EMA)" if is_bearish else "BULLISH REGIME (Close >= 50EMA)"
    ax.set_title(f"{symbol.upper()} NeoWave Commodity Risk Chart ({ticker}) | {regime_lbl}", color="white", fontsize=14, pad=16)
    ax.set_ylabel("Price (USD)", color="#e5e7eb")
    ax.grid(color="#374151", alpha=0.45, linewidth=0.5)
    ax.tick_params(axis="x", colors="#d1d5db")
    ax.tick_params(axis="y", colors="#d1d5db")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b-%y"))
    ax.legend(facecolor="#1f2937", edgecolor="#4b5563", labelcolor="white", loc="upper left")
    fig.autofmt_xdate()
    
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, facecolor=fig.get_facecolor())
    if show:
        plt.show()
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive NeoWave structural mapping tool for global commodities.")
    parser.add_argument("symbol", help="Commodity tracking alias: CRUDE, NG, GOLD, or SILVER")
    parser.add_argument("--period", default="5y", help="Historical lookup horizon, e.g. 1y, 2y, 5y (default: 5y)")
    parser.add_argument("--interval", default="1d", help="Execution window interval, e.g. 1d, 1wk (default: 1d)")
    parser.add_argument("--pivot", type=int, default=0, help="Window verification buffer filter on both sides (default: automatic)")
    parser.add_argument("--output", help="Output path name configuration rules (default: automated extension structure)")
    parser.add_argument("--show", action="store_true", help="Launch interactive engine workspace display pop-up")
    args = parser.parse_args()
    
    if args.pivot < 0 or args.pivot == 1:
        parser.error("--pivot must be 0 (automatic) or at least 2")
        
    output = Path(args.output or f"{args.symbol.strip().upper().replace(' ', '_')}_{args.period}_commodity.jpg")
    
    data = load_ohlc(args.symbol, args.period, args.interval)
    is_bearish = check_bearish_regime_50ema(data)
    pivot_width = args.pivot or automatic_pivot_width(data)
    
    if len(data) < pivot_width * 3:
        raise RuntimeError("Insufficient asset execution metrics bars detected inside the selection.")
        
    plot_chart(data, args.symbol, pivot_width, is_bearish, output, args.show)
    
    print(f"Saved chart: {output.resolve()}")
    print(f"Contract metrics bars processed: {len(data)} | Wave check window frame: {pivot_width}")
    print(f"Direction engine configuration sequence: {'BEARISH STRUCTURAL OVERLAY' if is_bearish else 'BULLISH STRUCTURAL OVERLAY'}")


if __name__ == "__main__":
    main()