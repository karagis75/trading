"""Bearish Fibonacci pinball scanner for the Indian Nifty 500.

Python conversion of ``bearsish_fib_pin_ball.js``. The universe is
``ind_nifty500list.csv`` instead of ``data/nifty500.csv``.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import fib_pinball_common as common

DEFAULT_OUTPUT = "Bearish_Fib_Pinball.xlsx"
BEARISH_COLUMNS = [
    "Ticker",
    "Last Date",
    "Wave Position",
    "Confidence",
    "Description",
    "W0 High",
    "W0 Date",
    "W1 Low",
    "W1 Date",
    "W2 High",
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
BEARISH_WAVE_ORDER = {
    "Wave 3 (Bearish)": 0,
    "Wave 3 Extended (Bearish)": 1,
    "Wave 5 (Bearish)": 2,
    "Wave 5 Extended (Bearish)": 3,
    "Wave 1 of 3 (Bearish)": 4,
    "Wave 1 (Bearish)": 5,
    "Early Wave 1 of 3 (Bearish)": 6,
    "Super Extended (Bearish)": 7,
}


def _result(
    ticker: str,
    *,
    last_date: str,
    wave: str,
    confidence: int,
    description: str,
    w0_high: float,
    w0_date: str,
    w1_low: float,
    w1_date: str,
    w2_high: Any,
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
        "W0 High": common.round2(w0_high),
        "W0 Date": w0_date,
        "W1 Low": common.round2(w1_low),
        "W1 Date": w1_date,
        "W2 High": "" if w2_high == "" else common.round2(w2_high),
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


def detect_early_bearish_wave1(
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
    w0_idx = max(range(look_start, n), key=lambda i: use_rows[i]["high"])
    w0_high = float(use_rows[w0_idx]["high"])
    days_since_w0 = n - 1 - w0_idx
    if days_since_w0 < config.early_wave1_min_bars or days_since_w0 > config.max_days_since_w0:
        return None
    loss = (w0_high - price) / w0_high if w0_high else 0.0
    if loss < config.early_wave1_min_move or loss > config.early_wave1_max_move:
        return None
    prior = use_rows[max(0, w0_idx - config.early_wave1_prior_bars) : w0_idx]
    if not prior:
        return None
    if w0_high <= max(row["high"] for row in prior):
        return None
    sma10 = common.sma(row["close"] for row in use_rows[-config.sma_period :])
    if price > sma10:
        return None
    low_after = min(row["low"] for row in use_rows[w0_idx:])
    amplitude = w0_high - low_after
    if amplitude <= 0:
        return None
    levels = common.extension_levels(w0_high, amplitude, downward=True)
    return _result(
        ticker,
        last_date=last_date,
        wave="Wave 1 (Bearish)",
        confidence=60,
        description=(
            f"Early Bearish Wave 1: fresh local peak at {common.round2(w0_high)} ({days_since_w0} bars ago); "
            f"price dropped {common.round2(loss * 100)}% from W0; low so far: {common.round2(low_after)}"
        ),
        w0_high=w0_high,
        w0_date=use_rows[w0_idx]["date"],
        w1_low=low_after,
        w1_date=last_date,
        w2_high="",
        w2_date="",
        w2_retrace="",
        amplitude=amplitude,
        price=price,
        ext_ratio=loss,
        levels=levels,
        days_since_w2="",
        days_since_w0=days_since_w0,
        days_of_data=len(rows),
    )


def analyze_bearish(
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
        return detect_early_bearish_wave1(ticker, rows, use_rows, price, last_date, config)

    n_use = len(use_rows)
    for index in range(len(pivots) - 1, 1, -1):
        w2 = pivots[index]
        w1 = pivots[index - 1]
        w0 = pivots[index - 2]
        if w2.type != "H" or w1.type != "L" or w0.type != "H":
            continue
        days_since_w2 = n_use - 1 - w2.idx
        days_since_w0 = n_use - 1 - w0.idx
        if days_since_w2 > config.max_days_since_w2 or days_since_w0 > config.max_days_since_w0:
            continue
        amplitude = w0.price - w1.price
        if amplitude <= 0 or w2.price >= w0.price:
            continue
        retrace = (w2.price - w1.price) / amplitude
        if retrace < config.retrace_min or retrace > config.retrace_max:
            continue
        levels = common.extension_levels(w2.price, amplitude, downward=True)
        wave = None
        confidence = 0
        description = ""
        if price > w2.price:
            continue
        if price >= w1.price:
            if days_since_w2 <= config.early_w1_of_3_max_days and price < w2.price:
                wave = "Early Wave 1 of 3 (Bearish)"
                confidence = 55
                description = (
                    f"Possible early downward w1 of Wave 3; price rejected from W2 high "
                    f"({common.round2(w2.price)}) but not yet below W1 low ({common.round2(w1.price)})"
                )
        else:
            ext_ratio = (w2.price - price) / amplitude
            recent_high = max(row["high"] for row in use_rows[-config.w4_lookback :])
            if ext_ratio <= 0.618:
                wave = "Wave 1 of 3 (Bearish)"
                confidence = 65
                description = (
                    f"In sub-wave 1 of Bearish Wave III; price ({common.round2(price)}) broke below W1 low "
                    f"({common.round2(w1.price)}) at {common.round2(ext_ratio * 100)}% of W1 amplitude from W2"
                )
            elif ext_ratio <= 1.236:
                wave = "Wave 3 (Bearish)"
                confidence = 80
                description = (
                    f"In Bearish Wave III; price ({common.round2(price)}) at {common.round2(ext_ratio * 100)}%. "
                    f"Targets: 1.0 ext={common.round2(levels['e1_000'])} to 1.618 ext={common.round2(levels['e1_618'])}"
                )
            elif ext_ratio <= 1.618:
                if common.in_range(recent_high, levels["e0_764"], levels["e1_382"]):
                    wave = "Wave 5 (Bearish)"
                    confidence = 72
                    description = (
                        f"In Bearish Wave V; W3 completed below 1.236 ext; W4 bounced to ~{common.round2(recent_high)}. "
                        f"Targets: 1.764 ext={common.round2(levels['e1_764'])} to 2.0 ext={common.round2(levels['e2_000'])}"
                    )
                else:
                    wave = "Wave 3 Extended (Bearish)"
                    confidence = 75
                    description = (
                        f"In extended Bearish Wave III; price ({common.round2(price)}) at "
                        f"{common.round2(ext_ratio * 100)}% ext; target 1.618 ext={common.round2(levels['e1_618'])}"
                    )
            elif ext_ratio <= 2.000:
                if common.in_range(recent_high, levels["e1_000"], levels["e1_618"]):
                    wave = "Wave 5 (Bearish)"
                    confidence = 78
                    description = (
                        f"In Bearish Wave V; W3 extended to {common.round2(ext_ratio * 100)}% ext; "
                        f"W4 peak ~{common.round2(recent_high)}. Targets: {common.round2(levels['e1_764'])} "
                        f"to {common.round2(levels['e2_000'])}"
                    )
                else:
                    wave = "Wave 5 Extended (Bearish)"
                    confidence = 65
                    description = (
                        f"In extended Bearish Wave V territory at {common.round2(ext_ratio * 100)}% ext "
                        f"({common.round2(price)})"
                    )
            else:
                wave = "Super Extended (Bearish)"
                confidence = 50
                description = (
                    f"Price expanded past 2.0 downside extension ({common.round2(ext_ratio * 100)}% of W1 drop); "
                    "structural capitulation or higher-degree sell-off"
                )
        if not wave:
            continue
        ext_ratio = (w2.price - price) / amplitude
        return _result(
            ticker,
            last_date=last_date,
            wave=wave,
            confidence=confidence,
            description=description,
            w0_high=w0.price,
            w0_date=w0.date,
            w1_low=w1.price,
            w1_date=w1.date,
            w2_high=w2.price,
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
    return detect_early_bearish_wave1(ticker, rows, use_rows, price, last_date, config)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Nifty 500 names from ind_nifty500list.csv for bearish Fibonacci pinball waves."
    )
    common.add_common_arguments(parser, default_output=DEFAULT_OUTPUT, default_lookback=420)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    return common.run_scanner(
        title="Bearish Fibonacci pinball",
        args=parse_args(argv),
        analyze=analyze_bearish,
        columns=BEARISH_COLUMNS,
        wave_order=BEARISH_WAVE_ORDER,
        config_overrides={"early_wave1_min_move": 0.05, "early_wave1_max_move": 0.70},
    )


if __name__ == "__main__":
    raise SystemExit(main())
