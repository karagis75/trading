import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("combinedoptionanalyzedv8.py")
SPEC = importlib.util.spec_from_file_location("combinedoptionanalyzedv8", MODULE_PATH)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


class ScannerDataRobustnessTests(unittest.TestCase):
    def test_latest_expiry_records_excludes_other_expiries(self):
        data = {
            "_selected_expiry": "25-Aug-2026",
            "records": {
                "data": [
                    {"expiryDate": "25-Aug-2026", "strikePrice": 100},
                    {"expiryDate": "29-Aug-2026", "strikePrice": 110},
                ]
            },
        }

        records = scanner.latest_expiry_records(data)

        self.assertEqual([100], [row["strikePrice"] for row in records])

    def test_latest_expiry_records_does_not_fallback_to_another_expiry(self):
        data = {
            "_selected_expiry": "25-Aug-2026",
            "records": {
                "data": [{"expiryDate": "29-Aug-2026", "strikePrice": 110}]
            },
        }

        self.assertEqual([], scanner.latest_expiry_records(data))

    def test_explicit_unavailable_expiry_is_rejected_before_chain_request(self):
        session = object()
        with patch.object(
            scanner,
            "fetch_contract_info",
            return_value={"expiryDates": ["25-Aug-2026"]},
        ), patch.object(scanner, "get_json") as get_json:
            result = scanner.fetch_option_chain(session, "INFY", "auto", "29-Aug-2026")

        self.assertIsNone(result)
        get_json.assert_not_called()

    def test_malformed_nse_numeric_values_are_treated_as_missing(self):
        option = {
            "bidprice": "-",
            "askPrice": "—",
            "totalTradedVolume": "-",
            "openInterest": "-",
        }

        self.assertEqual(0.0, scanner.bid_price(option))
        self.assertEqual(0.0, scanner.ask_price(option))
        self.assertEqual(0, scanner.traded_volume(option))
        self.assertFalse(scanner.option_is_tradeable(option, scanner.ScannerConfig()))

    def test_market_context_uses_leg_underlying_and_safe_open_interest(self):
        data = {
            "_selected_expiry": "25-Aug-2026",
            "records": {
                "underlyingValue": "-",
                "data": [
                    {
                        "expiryDate": "25-Aug-2026",
                        "CE": {
                            "underlyingValue": "100",
                            "strikePrice": "100",
                            "openInterest": "-",
                            "impliedVolatility": "20",
                        },
                        "PE": {
                            "underlyingValue": "100",
                            "strikePrice": "100",
                            "openInterest": "200",
                            "impliedVolatility": "22",
                        },
                    }
                ],
            },
        }

        context = scanner.build_market_context(
            data,
            "INFY",
            None,
            {"INFY": "bullish"},
            {},
            scanner.ScannerConfig(),
        )

        self.assertIsNotNone(context)
        self.assertEqual(100.0, context.underlying_price)
        self.assertEqual(1.0, context.pcr)
        self.assertEqual(200, context.max_open_interest)
        self.assertEqual("100", context.records[0]["CE"]["strikePrice"])

    def test_indicator_cleaning_does_not_backfill_from_future_bars(self):
        import pandas as pd

        frame = pd.DataFrame(
            {
                "Open": [None, 10.0],
                "High": [None, 11.0],
                "Low": [None, 9.0],
                "Close": [None, 10.0],
            }
        )

        cleaned = scanner.calculate_technical_indicators(frame)

        self.assertEqual([1], cleaned.index.tolist())
        self.assertEqual(10.0, cleaned.iloc[0]["Close"])

    def test_invalid_expiry_is_rejected_for_risk_calculations(self):
        with self.assertRaisesRegex(ValueError, "Invalid NSE expiry"):
            scanner.days_to_expiry("not-an-expiry")


if __name__ == "__main__":
    unittest.main()
