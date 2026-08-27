#!/usr/bin/env python3
"""Regression tests for trading-script crash and signal bugs."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bearisbiasnifty500 as bearish
import bullbear_neowave as neowave
import bullishbiasnifty500 as bullish
import combinedoptionanalyzedv2_improvedv2 as v2
import combinedoptionanalyzedv5 as v5
import combinedoptionanalyzedv8 as v8
import neowave_commodity_shortterm as shortterm
import rangeboundstocks as rangebound


def _flat_ohlc(n: int, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": price,
            "High": price,
            "Low": price,
            "Close": price,
        },
        index=idx,
    )


def test_mode_prefix_defined_when_no_impulse() -> None:
    close = np.linspace(100, 110, 80)
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.1,
            "Low": close - 0.1,
            "Close": close,
        },
        index=pd.date_range("2024-01-01", periods=80, freq="B"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "chart.jpg"
        neowave.plot_chart(frame, "TEST", pivot_width=8, is_bearish=True, output=output)
        assert output.exists()


def test_automatic_pivot_width_handles_nan_atr() -> None:
    frame = _flat_ohlc(5)
    assert neowave.automatic_pivot_width(frame) == 8
    assert shortterm.automatic_pivot_width(frame) == 8


def test_bullish_impulse_requires_low_before_high() -> None:
    inverted = [
        {"index": 10, "kind": "H", "price": 200.0},
        {"index": 20, "kind": "L", "price": 50.0},
    ]
    assert shortterm.latest_impulse(inverted, bearish=False) is None

    chronological = [
        {"index": 5, "kind": "L", "price": 80.0},
        {"index": 12, "kind": "H", "price": 200.0},
        {"index": 20, "kind": "L", "price": 100.0},
    ]
    impulse = shortterm.latest_impulse(chronological, bearish=False)
    assert impulse is not None
    start, end = impulse
    assert start["kind"] == "L" and end["kind"] == "H"
    assert start["index"] < end["index"]


def test_zero_mad_does_not_create_infinite_cci() -> None:
    frame = _flat_ohlc(40)
    out = bullish.calculate_indicators(frame, bullish.BullishScannerConfig())
    last = out.iloc[-1]
    assert not np.isinf(last["CCI"])
    assert pd.isna(last["CCI"]) or last["CCI"] == 0

    bear_out = bearish.calculate_indicators(frame, bearish.BearishScannerConfig())
    assert not np.isinf(bear_out.iloc[-1]["CCI"])

    range_out = rangebound.calculate_indicators(frame, rangebound.StrangleScannerConfig())
    assert not np.isinf(range_out.iloc[-1]["CCI"])


def test_zero_di_does_not_create_nan_adx_crash() -> None:
    frame = _flat_ohlc(40)
    out = bullish.calculate_indicators(frame, bullish.BullishScannerConfig())
    # ADX may be NaN on a dead-flat series, but comparisons must not raise.
    curr = out.iloc[-1]
    assert curr["ADX"] != curr["ADX"] or math.isfinite(float(curr["ADX"]))
    assert not (pd.notna(curr["ADX"]) and curr["ADX"] >= 20)


def test_combined_cci_zero_mad_is_nan_not_zero_or_inf() -> None:
    frame = _flat_ohlc(40)
    for module in (v2, v5, v8):
        out = module.calculate_technical_indicators(frame)
        cci = out.iloc[-1]["CCI20"]
        assert not np.isinf(cci)
        assert pd.isna(cci), f"{module.__name__} should leave zero-MAD CCI as NaN, got {cci}"


def test_v2_validation_handles_unlimited_loss_and_missing_width() -> None:
    config = v2.ScannerConfig()
    context = v2.MarketContext(
        symbol="TEST",
        records=[],
        underlying_price=100.0,
        pcr=1.0,
        max_open_interest=1000,
        expiry="30-Dec-2026",
        trend="unknown",
        event_risk="unknown",
    )
    opportunity = {
        "Strategy": "Bull Call Spread",
        "Max Loss": "Unlimited",
        "Score": 50.0,
        "Avg OI": 500,
        "Bid-Ask Spread": 0.4,
        "Spread Width": None,
    }
    result = v2.add_validation_fields(opportunity, context, config)
    assert result is not None
    assert result["Estimated Margin"] == round(100.0 * 0.20 * config.lot_size, 2)
    assert result["Liquidity Pass"] is True


def test_top_n_keeps_best_of_each_strategy() -> None:
    candidates = [
        {"Strategy": "Bull Call Spread", "Score": 90},
        {"Strategy": "Bull Call Spread", "Score": 80},
        {"Strategy": "Iron Condor", "Score": 70},
        {"Strategy": "Iron Condor", "Score": 40},
        {"Strategy": "Long Strangle", "Score": 60},
    ]
    ranked = v5._top_n_per_strategy(candidates, 1)
    strategies = {item["Strategy"] for item in ranked}
    assert strategies == {"Bull Call Spread", "Iron Condor", "Long Strangle"}
    assert len(ranked) == 3
    by_name = {item["Strategy"]: item["Score"] for item in ranked}
    assert by_name["Bull Call Spread"] == 90
    assert by_name["Iron Condor"] == 70

    ranked_v8 = v8._top_n_per_strategy(candidates, 1)
    assert len(ranked_v8) == 3
    ranked_v2 = v2._top_n_per_strategy(candidates, 1)
    assert len(ranked_v2) == 3


if __name__ == "__main__":
    tests = [
        test_mode_prefix_defined_when_no_impulse,
        test_automatic_pivot_width_handles_nan_atr,
        test_bullish_impulse_requires_low_before_high,
        test_zero_mad_does_not_create_infinite_cci,
        test_zero_di_does_not_create_nan_adx_crash,
        test_combined_cci_zero_mad_is_nan_not_zero_or_inf,
        test_v2_validation_handles_unlimited_loss_and_missing_width,
        test_top_n_keeps_best_of_each_strategy,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\n{len(tests)} Python tests passed.")
