#!/usr/bin/env python3
"""Rule-based Bearish/Adaptive NeoWave-style chart for NSE stocks and indices.

Examples
--------
python bear_neowave_chart.py BIOCON
python bear_neowave_chart.py RELIANCE     # automatically checks weekly EMA and flips logic if bearish
python bear_neowave_chart.py NIFTY --pivot 10

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

INDEX_ALIASES = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "BANKNIFTY": "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
}

# Unified Ratios
RETRACEMENTS = (0.236, 0.382, 0.500, 0.618, 0.786)
TARGETS = (1.000, 1.236, 1.382, 1.618, 1.764, 2.000)


def yahoo_symbol(symbol: str) -> str:
    """Map friendly NSE names to Yahoo Finance identifiers."""
    cleaned = symbol.strip().upper()
    if cleaned in INDEX_ALIASES:
        return INDEX_ALIASES[cleaned]
    if cleaned.startswith("^") or cleaned.endswith(".NS"):
        return cleaned
    return f"{cleaned}.NS"


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


def check_bearish_regime(symbol: str) -> bool:
    """Check if the current price is less than the 20-period weekly EMA."""
    try:
        # Download sufficient weekly history to stabilize the EMA calculation
        weekly_data = load_ohlc(symbol, period="2y", interval="1wk")
        if len(weekly_data) < 20:
            return False
        
        close = weekly_data["Close"].astype(float)
        ema_20_weekly = close.ewm(span=20, adjust=False).mean()
        
        current_price = float(close.iloc[-1])
        current_weekly_ema = float(ema_20_weekly.iloc[-1])
        
        return current_price < current_weekly_ema
    except Exception as e:
        print(f"Warning: Could not determine weekly regime status due to: {e}. Defaulting to Bullish mode.")
        return False


def automatic_pivot_width(frame: pd.DataFrame) -> int:
    close = frame["Close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat([
        frame["High"] - frame["Low"],
        (frame["High"] - previous_close).abs(),
        (frame["Low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr_percent = float((true_range.rolling(14).mean() / close).iloc[-1] * 100)
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


def latest_impulse(pivots: list[dict], bearish: bool) -> tuple[dict, dict] | None:
    """Find either the newest confirmed L->H or H->L swing based on target regime."""
    for start, end in zip(reversed(pivots[:-1]), reversed(pivots[1:])):
        if bearish:
            if start["kind"] == "H" and end["kind"] == "L" and end["price"] < start["price"]:
                return start, end
        else:
            if start["kind"] == "L" and end["kind"] == "H" and end["price"] > start["price"]:
                return start, end
    return None


def calculate_fib_levels(pivots: list[dict], bearish: bool) -> tuple[dict, dict, dict] | None:
    """Build structural Fibonacci references for either bullish or bearish impulses."""
    impulse = latest_impulse(pivots, bearish)
    if not impulse:
        return None
    start, end = impulse
    
    if bearish:
        # Bearish: High -> Low. Retracements bounce *up* from Low. Targets extend *down* from High.
        move = start["price"] - end["price"]
        retracements = {ratio: end["price"] + move * ratio for ratio in RETRACEMENTS}
        targets = {ratio: start["price"] - move * ratio for ratio in TARGETS}
    else:
        # Bullish: Low -> High. Retracements pull *down* from High. Targets extend *up* from Low.
        move = end["price"] - start["price"]
        retracements = {ratio: end["price"] - move * ratio for ratio in RETRACEMENTS}
        targets = {ratio: start["price"] + move * ratio for ratio in TARGETS}
        
    return {"start": start, "end": end}, retracements, targets


def plot_chart(frame: pd.DataFrame, symbol: str, pivot_width: int, is_bearish: bool, output: Path, show: bool = False) -> None:
    pivots = confirmed_pivots(frame, pivot_width)
    shown_pivots = pivots[-8:]
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#111827")
    draw_candles(ax, frame)

    close = frame["Close"]
    ax.plot(frame.index, close.ewm(span=20, adjust=False).mean(), color="#f4c430", lw=1.1, label="EMA 20")
    ax.plot(frame.index, close.ewm(span=50, adjust=False).mean(), color="#60a5fa", lw=1.1, label="EMA 50")

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
        
        # Color palettes configuration for Bearish vs Bullish structures
        ret_color = "#f472b6" if is_bearish else "#60a5fa"      # Pinkish support/resistance vs blue
        target_color = "#ef4444" if is_bearish else "#22c55e"   # Red downside target vs green upside
        invalid_color = "#22c55e" if is_bearish else "#ef4444"  # Green upper invalidation vs red lower invalidation
        
        for ratio, price in retracements.items():
            ax.hlines(price, start_date, frame.index[-1], colors=ret_color, linestyles="--", lw=0.85, alpha=0.8)
            ax.text(frame.index[-1], price, f"  {mode_prefix} retr {ratio:.3f}: {price:.2f}", color=ret_color, va="center", fontsize=8)
            
        for ratio, price in targets.items():
            is_anchor_edge = ratio == 1.0
            ax.hlines(price, start_date, frame.index[-1], colors=target_color, linestyles="-" if is_anchor_edge else "-.",
                      lw=1.25 if is_anchor_edge else 0.9, alpha=0.9)
            ax.text(frame.index[-1], price, f"  {mode_prefix} {'L' if is_bearish and is_anchor_edge else 'H' if is_anchor_edge else 'target'} {ratio:.3f}: {price:.2f}",
                    color=target_color, va="center", fontsize=8)
            
        structural_anchor = anchors["start"]["price"]
        ax.axhline(structural_anchor, color=invalid_color, linestyle=":", lw=1.1, alpha=0.9)
        ax.text(frame.index[-1], structural_anchor, f"  {mode_prefix} structure invalidation: {structural_anchor:.2f}", color=invalid_color, va="center", fontsize=8)
        
        direction_text = f"H → {end_date:%d-%b-%Y} L" if is_bearish else f"L → {end_date:%d-%b-%Y} H"
        ax.text(0.01, 0.02,
                f"Confirmed {mode_prefix.lower()} impulse: {start_date:%d-%b-%Y} {direction_text}. Targets are conditional; risk invalidates past structural anchor.",
                transform=ax.transAxes, color="#cbd5e1", fontsize=8.5)
    else:
        ax.text(0.01, 0.02, f"No confirmed {mode_prefix.lower()} impulse found in this segment.",
                transform=ax.transAxes, color="#fca5a5", fontsize=9)

    ticker = yahoo_symbol(symbol)
    regime_lbl = "BEARISH REGIME (Weekly < 20EMA)" if is_bearish else "BULLISH REGIME (Weekly >= 20EMA)"
    ax.set_title(f"{symbol.upper()} NeoWave-style [{regime_lbl}] | pivot: {pivot_width} bars", color="white", fontsize=14, pad=16)
    ax.set_ylabel("Price", color="#e5e7eb")
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
    parser = argparse.ArgumentParser(description="Adaptive Bullish/Bearish confirmed-pivot NeoWave-style NSE chart.")
    parser.add_argument("symbol", help="NSE equity (BIOCON/RELIANCE), NIFTY, or BANKNIFTY")
    parser.add_argument("--period", default="5y", help="Yahoo Finance history period, e.g. 1y, 2y, 5y (default: 5y)")
    parser.add_argument("--interval", default="1d", help="Yahoo interval, e.g. 1d, 1wk (default: 1d)")
    parser.add_argument("--pivot", type=int, default=0, help="Bars required on each side to confirm a pivot; 0 = automatic (default: 0)")
    parser.add_argument("--output", help="Chart path, e.g. .jpg or .pdf (default: <symbol>_neowave.jpg)")
    parser.add_argument("--show", action="store_true", help="Open the chart directly in a Matplotlib window after saving")
    args = parser.parse_args()
    
    if args.pivot < 0 or args.pivot == 1:
        parser.error("--pivot must be 0 (automatic) or at least 2")
        
    output = Path(args.output or f"{args.symbol.strip().upper().replace(' ', '_')}_{args.period}_neowave.jpg")
    
    # 1. Detect market environment using 20 Weekly EMA criteria
    is_bearish = check_bearish_regime(args.symbol)
    
    # 2. Load primary visualization framework data (e.g., Daily bars)
    data = load_ohlc(args.symbol, args.period, args.interval)
    pivot_width = args.pivot or automatic_pivot_width(data)
    
    if len(data) < pivot_width * 3:
        raise RuntimeError("Not enough price bars for the selected pivot width.")
        
    plot_chart(data, args.symbol, pivot_width, is_bearish, output, args.show)
    
    print(f"Saved chart: {output.resolve()}")
    print(f"Yahoo Finance bars loaded: {len(data)} | confirmed pivot width: {pivot_width}")
    print(f"Regime assigned automatically: {'BEARISH (H -> L)' if is_bearish else 'BULLISH (L -> H)'}")


if __name__ == "__main__":
    main()