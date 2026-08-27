import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import bullbear_neowave
import combinedoptionanalyzedv8 as options_v8
import neowave_chart
import neowave_commodity
import neowave_commodity_shortterm
import rangeboundstocks
from trading_utils import normalize_nse_ticker


class NeoWaveRegressionTests(unittest.TestCase):
    def setUp(self):
        close = np.linspace(100.0, 109.0, 10)
        self.short_frame = pd.DataFrame(
            {
                "Open": close - 0.5,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
            },
            index=pd.date_range("2026-01-01", periods=10),
        )

    def test_automatic_pivot_width_handles_short_history(self):
        modules = (
            bullbear_neowave,
            neowave_chart,
            neowave_commodity,
            neowave_commodity_shortterm,
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                width = module.automatic_pivot_width(self.short_frame)
                self.assertGreaterEqual(width, 5)
                self.assertLessEqual(width, 18)

    def test_short_term_impulse_uses_latest_chronological_swing(self):
        pivots = [
            {"index": 10, "kind": "H", "price": 200.0},
            {"index": 20, "kind": "L", "price": 100.0},
            {"index": 30, "kind": "H", "price": 160.0},
            {"index": 40, "kind": "L", "price": 140.0},
        ]
        start, end = neowave_commodity_shortterm.latest_impulse(pivots, bearish=True)
        self.assertEqual((start["index"], end["index"]), (30, 40))

    def test_chart_without_confirmed_impulse_is_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "chart.jpg"
            bullbear_neowave.plot_chart(
                self.short_frame,
                "TEST",
                pivot_width=5,
                is_bearish=True,
                output=output,
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)


class ScannerRegressionTests(unittest.TestCase):
    def test_numeric_excel_ticker_is_normalized_for_yahoo(self):
        self.assertEqual(normalize_nse_ticker("532540.0"), "532540.NS")
        self.assertEqual(normalize_nse_ticker("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(normalize_nse_ticker("^NSEI"), "^NSEI")

    @staticmethod
    def _range_frame(adx_tail, cci_tail):
        frame = pd.DataFrame(
            {
                "Open": [100.0] * 50,
                "High": [101.0] * 50,
                "Low": [99.0] * 50,
                "Close": [100.0] * 50,
                "EMA9": [100.0] * 50,
                "EMA18": [100.0] * 50,
                "EMA50": [100.0] * 50,
                "ADX": [20.0] * 50,
                "CCI": [0.0] * 50,
            }
        )
        frame.loc[frame.index[-len(adx_tail):], "ADX"] = adx_tail
        frame.loc[frame.index[-len(cci_tail):], "CCI"] = cci_tail
        return frame

    def test_rangebound_signal_requires_recent_adx_cross(self):
        config = rangeboundstocks.StrangleScannerConfig()
        crossed = self._range_frame([17.0, 14.0, 13.0, 12.0], [10.0, 20.0, -10.0])
        already_low = self._range_frame([10.0, 10.0, 10.0, 10.0], [10.0, 20.0, -10.0])
        self.assertIsNotNone(rangeboundstocks.evaluate_strangle_setup("TEST", crossed, config))
        self.assertIsNone(rangeboundstocks.evaluate_strangle_setup("TEST", already_low, config))

    def test_rangebound_signal_requires_cci_window_to_stay_tight(self):
        config = rangeboundstocks.StrangleScannerConfig()
        frame = self._range_frame([17.0, 14.0, 13.0, 12.0], [100.0, 10.0, 0.0])
        self.assertIsNone(rangeboundstocks.evaluate_strangle_setup("TEST", frame, config))


class OptionRegressionTests(unittest.TestCase):
    @staticmethod
    def _leg(strike, bid, ask):
        return {
            "strikePrice": strike,
            "bidprice": bid,
            "askPrice": ask,
            "openInterest": 1_000,
        }

    def test_iron_butterfly_rejects_mismatched_atm_strikes(self):
        result = options_v8.build_short_iron_butterfly_opportunity(
            "TEST",
            1.0,
            15.0,
            100.0,
            self._leg(100, 8, 9),
            self._leg(95, 7, 8),
            self._leg(110, 1, 2),
            self._leg(90, 1, 2),
            1_000,
            "31-Dec-2026",
            0.2,
        )
        self.assertIsNone(result)

    def test_unbounded_strategy_still_checks_bid_ask_liquidity(self):
        opportunity = {
            "Strategy": "Short Straddle",
            "Max Loss": "Unlimited",
            "Credit": 100.0,
            "Net Debit": 0.0,
            "Spread Width": None,
            "Avg OI": 1_000,
            "Bid-Ask Spread": 50.0,
            "Score": 50.0,
        }
        context = options_v8.MarketContext(
            symbol="TEST",
            records=[],
            underlying_price=100.0,
            pcr=1.0,
            max_open_interest=1_000,
            expiry="31-Dec-2026",
            trend="sideways",
        )
        result = options_v8.add_validation_fields(
            opportunity, context, options_v8.ScannerConfig()
        )
        self.assertFalse(result["Liquidity Pass"])


if __name__ == "__main__":
    unittest.main()
