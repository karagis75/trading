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


if __name__ == "__main__":
    unittest.main()
