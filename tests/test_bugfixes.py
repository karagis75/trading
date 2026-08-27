#!/usr/bin/env python3
"""Regression tests for trading-script bug fixes."""

from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_module(name: str, filename: str):
    path = ROOT / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bullish = load_module("bullishbiasnifty500", "bullishbiasnifty500.py")
bearish = load_module("bearisbiasnifty500", "bearisbiasnifty500.py")
rangebound = load_module("rangeboundstocks", "rangeboundstocks.py")
neowave_st = load_module("neowave_commodity_shortterm", "neowave_commodity_shortterm.py")
v2 = load_module("combinedoptionanalyzedv2_improvedv2", "combinedoptionanalyzedv2_improvedv2.py")
v8 = load_module("combinedoptionanalyzedv8", "combinedoptionanalyzedv8.py")


def option_leg(strike: float, bid: float, ask: float, oi: int = 1000, **extra) -> dict:
    payload = {
        "strikePrice": strike,
        "bidprice": bid,
        "askPrice": ask,
        "openInterest": oi,
        "impliedVolatility": 20,
    }
    payload.update(extra)
    return payload


class IndicatorSafetyTests(unittest.TestCase):
    def test_flat_prices_do_not_produce_inf_cci_or_adx(self):
        n = 80
        df = pd.DataFrame({
            "Open": np.full(n, 100.0),
            "High": np.full(n, 100.0),
            "Low": np.full(n, 100.0),
            "Close": np.full(n, 100.0),
        })
        for module, config_cls in (
            (bullish, bullish.BullishScannerConfig),
            (bearish, bearish.BearishScannerConfig),
            (rangebound, rangebound.StrangleScannerConfig),
        ):
            with self.subTest(module=module.__name__):
                out = module.calculate_indicators(df, config_cls())
                self.assertFalse(np.isinf(out["CCI"]).any(), "CCI should not be inf on flat prices")
                self.assertFalse(np.isinf(out["ADX"]).any(), "ADX should not be inf on flat prices")


class BullishCciTriggerTests(unittest.TestCase):
    def _stacked_frame(self) -> pd.DataFrame:
        n = 220
        close = np.linspace(100, 160, n)
        high = close + 0.3
        low = close - 1.4
        open_ = close - 1.2
        return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close})

    def test_cci_already_above_100_without_crossover_is_rejected(self):
        config = bullish.BullishScannerConfig()
        df = bullish.calculate_indicators(self._stacked_frame(), config)
        # Force CCI values after indicator calc so the tautology is the only variable.
        df.iloc[-2, df.columns.get_loc("CCI")] = 120.0
        df.iloc[-1, df.columns.get_loc("CCI")] = 130.0
        result = bullish.evaluate_bullish_bias("TEST", df, config)
        self.assertIsNone(result)

    def test_fresh_cci_crossover_can_qualify(self):
        config = bullish.BullishScannerConfig()
        df = bullish.calculate_indicators(self._stacked_frame(), config)
        df.iloc[-2, df.columns.get_loc("CCI")] = 90.0
        df.iloc[-1, df.columns.get_loc("CCI")] = 110.0
        result = bullish.evaluate_bullish_bias("TEST", df, config)
        self.assertIsNotNone(result)
        self.assertEqual(result["Status"], "Confirmed Bullish")


class NeoWaveImpulseTests(unittest.TestCase):
    def test_bullish_impulse_rejects_low_after_high(self):
        pivots = [
            {"index": 10, "kind": "H", "price": 120},
            {"index": 20, "kind": "L", "price": 80},
            {"index": 30, "kind": "H", "price": 110},
        ]
        self.assertIsNone(neowave_st.latest_impulse(pivots, bearish=False))

    def test_bullish_impulse_requires_low_before_high(self):
        pivots = [
            {"index": 10, "kind": "L", "price": 80},
            {"index": 20, "kind": "H", "price": 120},
            {"index": 30, "kind": "L", "price": 95},
        ]
        impulse = neowave_st.latest_impulse(pivots, bearish=False)
        self.assertIsNotNone(impulse)
        start, end = impulse
        self.assertEqual(start["kind"], "L")
        self.assertEqual(end["kind"], "H")
        self.assertLess(start["index"], end["index"])

    def test_mode_prefix_is_defined_before_fib_branch(self):
        source = (ROOT / "bullbear_neowave.py").read_text()
        fib_idx = source.index("fib = calculate_fib_levels")
        prefix_idx = source.index('mode_prefix = "Bear" if is_bearish else "Bull"')
        if_idx = source.index("if fib:", fib_idx)
        self.assertLess(prefix_idx, if_idx)


class OptionPricingTests(unittest.TestCase):
    def test_bid_price_accepts_camel_case_field(self):
        self.assertEqual(v8.bid_price({"bidPrice": 1.25}), 1.25)
        self.assertEqual(v2.bid_price({"bidPrice": 2.5}), 2.5)
        self.assertEqual(v8.bid_price({"bidprice": 0.9}), 0.9)

    def test_matched_atm_legs_require_shared_strike(self):
        calls = [option_leg(100, 2, 2.2), option_leg(105, 1.1, 1.3)]
        puts = [option_leg(95, 1.4, 1.6), option_leg(100, 1.8, 2.0)]
        call, put = v8._matched_atm_legs(calls, puts, 101)
        self.assertEqual(call["strikePrice"], 100)
        self.assertEqual(put["strikePrice"], 100)

        mismatched_puts = [option_leg(95, 1.4, 1.6), option_leg(97, 1.5, 1.7)]
        call, put = v8._matched_atm_legs(calls, mismatched_puts, 100)
        self.assertIsNone(call)
        self.assertIsNone(put)

    def test_iron_butterfly_rejects_mismatched_atm_strikes(self):
        expiry = (date.today() + timedelta(days=14)).strftime("%d-%b-%Y")
        opp = v8.build_short_iron_butterfly_opportunity(
            "TEST", 1.0, 14.0, 100.0,
            option_leg(100, 3.0, 3.2),
            option_leg(95, 2.8, 3.0),
            option_leg(110, 0.4, 0.6),
            option_leg(90, 0.4, 0.6),
            5000, expiry, 0.2,
        )
        self.assertIsNone(opp)

    def test_iron_butterfly_builds_when_atm_strikes_match(self):
        expiry = (date.today() + timedelta(days=14)).strftime("%d-%b-%Y")
        opp = v8.build_short_iron_butterfly_opportunity(
            "TEST", 1.0, 14.0, 100.0,
            option_leg(100, 3.0, 3.2),
            option_leg(100, 2.8, 3.0),
            option_leg(110, 0.4, 0.6),
            option_leg(90, 0.4, 0.6),
            5000, expiry, 0.2,
        )
        self.assertIsNotNone(opp)
        self.assertEqual(opp["Strategy"], "Short Iron Butterfly")
        self.assertIn("100.0 CE + 100.0 PE", opp["Sell Leg (Strike)"])

    def test_take_top_keeps_highest_scores(self):
        items = [{"Score": 1}, {"Score": 9}, {"Score": 4}, {"Score": 7}]
        top = v8._take_top(items, 2)
        self.assertEqual([row["Score"] for row in top], [9, 7])

    def test_unlimited_max_loss_does_not_crash_validation(self):
        config = v2.ScannerConfig()
        context = v2.MarketContext(
            symbol="TEST",
            records=[],
            underlying_price=100.0,
            pcr=1.0,
            max_open_interest=1000,
            expiry="01-Jan-2099",
            trend="sideways",
        )
        opportunity = {
            "Strategy": "Bull Call Spread",
            "Max Loss": "Unlimited",
            "Net Debit": 0,
            "Credit": 5,
            "Avg OI": 500,
            "Bid-Ask Spread": 0.1,
            "Spread Width": 10,
            "Score": 10,
        }
        result = v2.add_validation_fields(opportunity, context, config)
        self.assertIsNotNone(result)
        self.assertTrue(math.isfinite(result["Estimated Margin"]))


if __name__ == "__main__":
    unittest.main()
