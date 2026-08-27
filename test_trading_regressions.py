import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


MODULE_PATH = Path(__file__).with_name("combinedoptionanalyzedv8.py")
SPEC = importlib.util.spec_from_file_location("combinedoptionanalyzedv8", MODULE_PATH)
scanner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scanner)

RANGEBOUND_PATH = Path(__file__).with_name("rangeboundstocks.py")
RANGEBOUND_SPEC = importlib.util.spec_from_file_location("rangeboundstocks", RANGEBOUND_PATH)
rangebound = importlib.util.module_from_spec(RANGEBOUND_SPEC)
assert RANGEBOUND_SPEC.loader is not None
RANGEBOUND_SPEC.loader.exec_module(rangebound)


class TradingScannerRegressionTests(unittest.TestCase):
    def test_indicator_cleaning_handles_yfinance_multiindex_without_lookahead(self):
        columns = pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close"], ["TEST.NS"]]
        )
        frame = pd.DataFrame(
            [
                [np.nan, np.nan, np.nan, np.nan],
                [99, 101, 98, 100],
                [199, 201, 198, 200],
            ],
            columns=columns,
        )

        result = scanner.calculate_technical_indicators(frame)

        self.assertEqual(list(result.columns[:4]), ["Open", "High", "Low", "Close"])
        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0]["Close"], 100)
        self.assertFalse(result.iloc[0][["Open", "High", "Low", "Close"]].isna().any())

    def test_indicator_calculation_rejects_missing_ohlc_columns(self):
        with self.assertRaisesRegex(ValueError, "required columns"):
            scanner.calculate_technical_indicators(pd.DataFrame({"Close": [100, 101]}))

    def test_latest_expiry_records_excludes_other_expiries(self):
        selected = {"expiryDate": "01-Jan-2027", "CE": {"strikePrice": 100}}
        other = {"expiryDate": "08-Jan-2027", "CE": {"strikePrice": 100}}
        data = {
            "_selected_expiry": "01-Jan-2027",
            "records": {"data": [selected, other]},
        }

        self.assertEqual(scanner.latest_expiry_records(data), [selected])

    def test_market_context_tolerates_decimal_and_invalid_oi_values(self):
        expiry = "01-Jan-2027"
        data = {
            "_selected_expiry": expiry,
            "records": {
                "underlyingValue": "100.0",
                "data": [
                    {
                        "expiryDate": expiry,
                        "CE": {
                            "strikePrice": "100",
                            "openInterest": "10.5",
                            "impliedVolatility": "20",
                        },
                        "PE": {
                            "strikePrice": "100",
                            "openInterest": "bad",
                            "impliedVolatility": "22",
                        },
                    }
                ],
            },
        }

        context = scanner.build_market_context(
            data,
            "TEST",
            expiry,
            {"TEST": "neutral"},
            {},
            scanner.ScannerConfig(),
        )

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.underlying_price, 100.0)
        self.assertEqual(context.pcr, 0.0)
        self.assertEqual(context.max_open_interest, 10)
        self.assertEqual(context.atm_iv, 0.21)

    def test_crossed_quotes_are_not_treated_as_tradeable(self):
        option = {
            "bidprice": "12.0",
            "askPrice": "10.0",
            "openInterest": "1000",
            "totalTradedVolume": "100",
        }

        self.assertFalse(scanner.option_is_tradeable(option, scanner.ScannerConfig()))

    def test_credit_spread_probability_uses_breakeven(self):
        short_put = {
            "strikePrice": "95",
            "bidprice": "2.0",
            "askPrice": "2.2",
            "openInterest": "1000",
        }
        long_put = {
            "strikePrice": "90",
            "bidprice": "0.4",
            "askPrice": "0.5",
            "openInterest": "1000",
        }

        opportunity = scanner.build_credit_spread_opportunity(
            "TEST",
            "Bull Put Credit Spread",
            1.0,
            20.0,
            100.0,
            short_put,
            long_put,
            1000,
            "01-Jan-2099",
            scanner.ScannerConfig(),
            0.20,
        )

        self.assertIsNotNone(opportunity)
        assert opportunity is not None
        self.assertEqual(opportunity["Breakeven"], 93.5)
        expected_pop = scanner.probability_otm(
            100.0, 93.5, 0.20, scanner.days_to_expiry("01-Jan-2099"), "PUT"
        )
        self.assertEqual(opportunity["Probability of Profit"], round(expected_pop, 3))

    def test_malformed_records_payload_is_ignored(self):
        self.assertEqual(scanner.latest_expiry_records({"records": []}), [])
        self.assertEqual(scanner.latest_expiry_records({"records": {"data": [None]}}), [])

    def test_flat_market_indicators_remain_finite(self):
        frame = pd.DataFrame(
            {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0},
            index=pd.date_range("2026-01-01", periods=40),
        )

        result = rangebound.calculate_indicators(
            frame, rangebound.StrangleScannerConfig()
        )

        self.assertTrue(np.isfinite(result[["CCI", "ADX"]].to_numpy()).all())


if __name__ == "__main__":
    unittest.main()
