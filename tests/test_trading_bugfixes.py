#!/usr/bin/env python3
"""Regression tests for trading-script bug fixes."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MPLBACKEND", "Agg")


def _ohlc_frame(n: int = 40, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": np.full(n, price),
            "High": np.full(n, price),
            "Low": np.full(n, price),
            "Close": np.full(n, price),
        },
        index=idx,
    )


def test_bullbear_plot_chart_without_impulse_does_not_crash():
    import matplotlib

    matplotlib.use("Agg")
    import bullbear_neowave as chart

    frame = _ohlc_frame(40)
    with mock.patch.object(chart, "calculate_fib_levels", return_value=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "no_impulse.jpg"
            chart.plot_chart(frame, "TEST", 2, True, out, show=False)
            assert out.exists()


def test_shortterm_bullish_impulse_requires_chronological_low_then_high():
    from neowave_commodity_shortterm import latest_impulse

    inverted = [
        {"kind": "H", "price": 120.0, "index": 3},
        {"kind": "L", "price": 80.0, "index": 15},
    ]
    assert latest_impulse(inverted, bearish=False) is None

    valid = [
        {"kind": "L", "price": 80.0, "index": 3},
        {"kind": "H", "price": 120.0, "index": 15},
    ]
    start, end = latest_impulse(valid, bearish=False)
    assert start["kind"] == "L" and start["index"] == 3
    assert end["kind"] == "H" and end["index"] == 15


def test_shortterm_bearish_impulse_still_requires_high_then_low():
    from neowave_commodity_shortterm import latest_impulse

    inverted = [
        {"kind": "L", "price": 80.0, "index": 3},
        {"kind": "H", "price": 120.0, "index": 15},
    ]
    assert latest_impulse(inverted, bearish=True) is None

    valid = [
        {"kind": "H", "price": 120.0, "index": 3},
        {"kind": "L", "price": 80.0, "index": 15},
    ]
    start, end = latest_impulse(valid, bearish=True)
    assert start["kind"] == "H"
    assert end["kind"] == "L"


def test_iron_butterfly_rejects_mismatched_atm_strikes():
    from combinedoptionanalyzedv8 import build_short_iron_butterfly_opportunity

    def opt(strike: float, bid: float, ask: float) -> dict:
        return {
            "strikePrice": strike,
            "bidprice": bid,
            "askPrice": ask,
            "openInterest": 1000,
        }

    mismatched = build_short_iron_butterfly_opportunity(
        "INFY", 1.0, 15.0, 1500.0,
        opt(1500, 20, 21), opt(1480, 20, 21),
        opt(1550, 5, 6), opt(1430, 5, 6),
        10_000, "30-Sep-2026", 0.18,
    )
    assert mismatched is None

    matched = build_short_iron_butterfly_opportunity(
        "INFY", 1.0, 15.0, 1500.0,
        opt(1500, 20, 21), opt(1500, 20, 21),
        opt(1550, 5, 6), opt(1450, 5, 6),
        10_000, "30-Sep-2026", 0.18,
    )
    assert matched is not None
    assert matched["Strategy"] == "Short Iron Butterfly"


def test_flat_price_series_does_not_nan_cci_or_adx():
    from bearisbiasnifty500 import BearishScannerConfig, calculate_indicators as bear_ind
    from bullishbiasnifty500 import BullishScannerConfig, calculate_indicators as bull_ind
    from rangeboundstocks import StrangleScannerConfig, calculate_indicators as range_ind
    from combinedoptionanalyzedv5 import calculate_technical_indicators

    frame = _ohlc_frame(80)
    bull = bull_ind(frame, BullishScannerConfig())
    bear = bear_ind(frame, BearishScannerConfig())
    rng = range_ind(frame, StrangleScannerConfig())
    v5 = calculate_technical_indicators(frame)

    for name, df, cci_col in (
        ("bull", bull, "CCI"),
        ("bear", bear, "CCI"),
        ("range", rng, "CCI"),
        ("v5", v5, "CCI14"),
    ):
        last_cci = df[cci_col].iloc[-1]
        last_adx = df["ADX"].iloc[-1]
        assert pd.notna(last_cci), f"{name} CCI is NaN on a flat series"
        assert pd.notna(last_adx), f"{name} ADX is NaN on a flat series"


def test_v2_validation_accepts_unlimited_max_loss():
    from combinedoptionanalyzedv2_improvedv2 import (
        MarketContext,
        ScannerConfig,
        add_validation_fields,
    )

    context = MarketContext(
        symbol="INFY",
        records=[],
        underlying_price=1500.0,
        pcr=1.0,
        max_open_interest=10_000,
        expiry="30-Sep-2026",
        trend="sideways",
        event_risk="no",
    )
    opportunity = {
        "Strategy": "Short Straddle",
        "Max Loss": "Unlimited",
        "Spread Width": None,
        "Avg OI": 500,
        "Bid-Ask Spread": 1.2,
        "Score": 40.0,
        "Credit": 25.0,
    }
    result = add_validation_fields(opportunity, context, ScannerConfig())
    assert result is not None
    assert "Estimated Margin" in result or "Score" in result


def test_negative_pandas_zero_index_is_first_not_last():
    idx = pd.date_range("2024-01-02", periods=5, freq="B")
    frame = pd.DataFrame({"Close": range(5)}, index=idx)
    assert frame.index[-0] == frame.index[0]
    lookback = 0
    visualization_start = frame.index[-1] if lookback <= 0 else frame.index[-lookback]
    assert visualization_start == frame.index[-1]


if __name__ == "__main__":
    tests = [
        test_bullbear_plot_chart_without_impulse_does_not_crash,
        test_shortterm_bullish_impulse_requires_chronological_low_then_high,
        test_shortterm_bearish_impulse_still_requires_high_then_low,
        test_iron_butterfly_rejects_mismatched_atm_strikes,
        test_flat_price_series_does_not_nan_cci_or_adx,
        test_v2_validation_accepts_unlimited_max_loss,
        test_negative_pandas_zero_index_is_first_not_last,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {test.__name__}: {exc!r}")
            raise
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
