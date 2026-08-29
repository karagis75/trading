"""Bullish Fibonacci pinball scanner for the Indian Nifty 500.

Python conversion of ``nifty_pinball_yahoo.js`` / ``fib_yahoo_pinball.js``.
The universe is ``ind_nifty500list.csv`` instead of hardcoded index symbols.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import fib_pinball_common as common

DEFAULT_OUTPUT = "Bullish_Fib_Pinball.xlsx"
BULLISH_COLUMNS = [
    "Ticker",
    "Last Date",
    "Wave Position",
    "Confidence",
    "Description",
    "W0 Low",
    "W0 Date",
    "W1 High",
    "W1 Date",
    "W2 Low",
    "W2 Date",
    "W2 Retrace %",
    "W1 Amplitude",
    "Current Price",
    "Ext Ratio",
    "0.382 Ext",
    "0.618 Ext",
    "0.764 Ext",
    "1.000 Ext",
    "1.236 Ext",
    "1.382 Ext",
    "1.618 Ext",
    "1.764 Ext",
    "2.000 Ext",
    "Days Since W2",
    "Days Since W0",
    "Days of Data",
]
BULLISH_WAVE_ORDER = {
    "Wave 3": 0,
    "Wave 3 Extended": 1,
    "Wave 5": 2,
    "Wave 5 Extended": 3,
    "Wave 1 of 3": 4,
    "Wave 1": 5,
    "Early Wave 1 of 3": 6,
    "Super Extended": 7,
}


def _result(
    ticker: str,
    *,
    last_date: str,
    wave: str,
    confidence: int,
    description: str,
    w0_low: float,
    w0_date: str,
    w1_high: float,
    w1_date: str,
    w2_low: Any,
    w2_date: Any,
    w2_retrace: Any,
    amplitude: float,
    price: float,
    ext_ratio: float,
    levels: dict[str, float],
    days_since_w2: Any,
    days_since_w0: int,
    days_of_data: int,
) -> dict[str, Any]:
    return {
        "Ticker": ticker,
        "Last Date": last_date,
        "Wave Position": wave,
        "Confidence": confidence,
        "Description": description,
        "W0 Low": common.round2(w0_low),
        "W0 Date": w0_date,
        "W1 High": common.round2(w1_high),
        "W1 Date": w1_date,
        "W2 Low": "" if w2_low == "" else common.round2(w2_low),
        "W2 Date": w2_date,
        "W2 Retrace %": "" if w2_retrace == "" else common.round2(w2_retrace),
        "W1 Amplitude": common.round2(amplitude),
        "Current Price": common.round2(price),
        "Ext Ratio": common.round2(ext_ratio),
        "0.382 Ext": common.round2(levels["e0_382"]),
        "0.618 Ext": common.round2(levels["e0_618"]),
        "0.764 Ext": common.round2(levels["e0_764"]),
        "1.000 Ext": common.round2(levels["e1_000"]),
        "1.236 Ext": common.round2(levels["e1_236"]),
        "1.382 Ext": common.round2(levels["e1_382"]),
        "1.618 Ext": common.round2(levels["e1_618"]),
        "1.764 Ext": common.round2(levels["e1_764"]),
        "2.000 Ext": common.round2(levels["e2_000"]),
        "Days Since W2": days_since_w2,
        "Days Since W0": days_since_w0,
        "Days of Data": days_of_data,
    }


def detect_early_wave1(
    ticker: str,
    rows: list[dict[str, Any]],
    use_rows: list[dict[str, Any]],
    price: float,
    last_date: str,
    config: common.PinballConfig,
) -> dict[str, Any] | None:
    n = len(use_rows)
    if n < 20:
        return None
    look_start = max(0, n - config.max_days_since_w0)
    w0_idx = min(range(look_start, n), key=lambda i: use_rows[i]["low"])
    w0_low = float(use_rows[w0_idx]["low"])
    days_since_w0 = n - 1 - w0_idx
    if days_since_w0 < config.early_wave1_min_bars or days_since_w0 > config.max_days_since_w0:
        return None
    gain = (price - w0_low) / w0_low if w0_low else 0.0
    if gain < config.early_wave1_min_move or gain > config.early_wave1_max_move:
        return None
    prior = use_rows[max(0, w0_idx - config.early_wave1_prior_bars) : w0_idx]
    if not prior:
        return None
    if w0_low >= min(row["low"] for row in prior):
        return None
    sma10 = common.sma(row["close"] for row in use_rows[-config.sma_period :])
    if price < sma10:
        return None
    high_after = max(row["high"] for row in use_rows[w0_idx:])
    amplitude = high_after - w0_low
    if amplitude <= 0:
        return None
    levels = common.extension_levels(w0_low, amplitude, downward=False)
    return _result(
        ticker,
        last_date=last_date,
        wave="Wave 1",
        confidence=60,
        description=(
            f"Early Wave 1: fresh low at {common.round2(w0_low)} ({days_since_w0} bars ago); "
            f"price gained {common.round2(gain * 100)}% from W0; Wave 1 high so far: {common.round2(high_after)}"
        ),
        w0_low=w0_low,
        w0_date=use_rows[w0_idx]["date"],
        w1_high=high_after,
        w1_date=last_date,
        w2_low="",
        w2_date="",
        w2_retrace="",
        amplitude=amplitude,
        price=price,
        ext_ratio=gain,
        levels=levels,
        days_since_w2="",
        days_since_w0=days_since_w0,
        days_of_data=len(rows),
    )


def analyze_bullish(
    ticker: str,
    rows: list[dict[str, Any]],
    config: common.PinballConfig,
) -> dict[str, Any] | None:
    if len(rows) < config.min_bars:
        return None
    use_rows = common.analysis_window(rows, config)
    current = rows[-1]
    price = float(current["close"])
    last_date = current["date"]
    pivots = common.find_pivots(use_rows, left=config.pivot_left, right=config.pivot_right)
    if len(pivots) < 3:
        return detect_early_wave1(ticker, rows, use_rows, price, last_date, config)

    n_use = len(use_rows)
    for index in range(len(pivots) - 1, 1, -1):
        w2 = pivots[index]
        w1 = pivots[index - 1]
        w0 = pivots[index - 2]
        if w2.type != "L" or w1.type != "H" or w0.type != "L":
            continue
        days_since_w2 = n_use - 1 - w2.idx
        days_since_w0 = n_use - 1 - w0.idx
        if days_since_w2 > config.max_days_since_w2 or days_since_w0 > config.max_days_since_w0:
            continue
        amplitude = w1.price - w0.price
        if amplitude <= 0 or w2.price <= w0.price:
            continue
        retrace = (w1.price - w2.price) / amplitude
        if retrace < config.retrace_min or retrace > config.retrace_max:
            continue
        levels = common.extension_levels(w2.price, amplitude, downward=False)
        wave = None
        confidence = 0
        description = ""
        if price < w2.price:
            continue
        if price <= w1.price:
            if days_since_w2 <= config.early_w1_of_3_max_days and price > w2.price:
                wave = "Early Wave 1 of 3"
                confidence = 55
                description = (
                    f"Possible early w1 of Wave 3; price bounced from W2 ({common.round2(w2.price)}) "
                    f"but not yet above W1 high ({common.round2(w1.price)})"
                )
        else:
            ext_ratio = (price - w2.price) / amplitude
            recent_low = min(row["low"] for row in use_rows[-config.w4_lookback :])
            if ext_ratio <= 0.618:
                wave = "Wave 1 of 3"
                confidence = 65
                description = (
                    f"In sub-wave 1 of Wave III; price ({common.round2(price)}) broke above W1 high "
                    f"({common.round2(w1.price)}) at {common.round2(ext_ratio * 100)}% of W1 amplitude from W2"
                )
            elif ext_ratio <= 1.236:
                wave = "Wave 3"
                confidence = 80
                description = (
                    f"In Wave III (strongest wave); price ({common.round2(price)}) at "
                    f"{common.round2(ext_ratio * 100)}%. Targets: 1.0 ext={common.round2(levels['e1_000'])} "
                    f"to 1.618 ext={common.round2(levels['e1_618'])}"
                )
            elif ext_ratio <= 1.618:
                if common.in_range(recent_low, levels["e0_764"], levels["e1_382"]):
                    wave = "Wave 5"
                    confidence = 72
                    description = (
                        f"In Wave V; W3 completed above 1.236 ext; W4 pulled back to ~{common.round2(recent_low)}. "
                        f"Targets: 1.764 ext={common.round2(levels['e1_764'])} to 2.0 ext={common.round2(levels['e2_000'])}"
                    )
                else:
                    wave = "Wave 3 Extended"
                    confidence = 75
                    description = (
                        f"In extended Wave III; price ({common.round2(price)}) at "
                        f"{common.round2(ext_ratio * 100)}% ext; target 1.618 ext={common.round2(levels['e1_618'])}"
                    )
            elif ext_ratio <= 2.000:
                if common.in_range(recent_low, levels["e1_000"], levels["e1_618"]):
                    wave = "Wave 5"
                    confidence = 78
                    description = (
                        f"In Wave V; W3 extended to {common.round2(ext_ratio * 100)}% ext; "
                        f"W4 low ~{common.round2(recent_low)}. Targets: {common.round2(levels['e1_764'])} "
                        f"to {common.round2(levels['e2_000'])}"
                    )
                else:
                    wave = "Wave 5 Extended"
                    confidence = 65
                    description = (
                        f"In extended Wave V territory at {common.round2(ext_ratio * 100)}% ext "
                        f"({common.round2(price)}); extreme target 2.618 ext"
                    )
            else:
                wave = "Super Extended"
                confidence = 50
                description = (
                    f"Price beyond 2.0 ext ({common.round2(ext_ratio * 100)}% of W1 amplitude from W2); "
                    "potential blow-off or start of a higher-degree wave"
                )
        if not wave:
            continue
        ext_ratio = (price - w2.price) / amplitude
        return _result(
            ticker,
            last_date=last_date,
            wave=wave,
            confidence=confidence,
            description=description,
            w0_low=w0.price,
            w0_date=w0.date,
            w1_high=w1.price,
            w1_date=w1.date,
            w2_low=w2.price,
            w2_date=w2.date,
            w2_retrace=retrace * 100,
            amplitude=amplitude,
            price=price,
            ext_ratio=ext_ratio,
            levels=levels,
            days_since_w2=days_since_w2,
            days_since_w0=days_since_w0,
            days_of_data=len(rows),
        )
    return detect_early_wave1(ticker, rows, use_rows, price, last_date, config)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Nifty 500 names from ind_nifty500list.csv for bullish Fibonacci pinball waves."
    )
    common.add_common_arguments(parser, default_output=DEFAULT_OUTPUT, default_lookback=500)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    return common.run_scanner(
        title="Bullish Fibonacci pinball",
        args=parse_args(argv),
        analyze=analyze_bullish,
        columns=BULLISH_COLUMNS,
        wave_order=BULLISH_WAVE_ORDER,
        config_overrides={"early_wave1_min_move": 0.05, "early_wave1_max_move": 1.0},
    )


if __name__ == "__main__":
    raise SystemExit(main())
