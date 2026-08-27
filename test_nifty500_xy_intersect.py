import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd

import nifty500_xy_intersect as scanner


def make_history(rows: int = 60) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=rows, freq="D")
    close = np.linspace(100, 110, rows)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1000,
        },
        index=index,
    )


def make_evaluation_frame() -> pd.DataFrame:
    frame = make_history()
    frame["8_DMA"] = 100.0
    frame["18_DMA"] = 98.0
    frame["ATR"] = 2.0
    frame["ADX"] = 20.0

    frame.iloc[-5:, frame.columns.get_loc("ADX")] = [20.0, 20.0, 10.0, 11.0, 13.0]
    frame.iloc[-2, frame.columns.get_loc("8_DMA")] = 99.0
    frame.iloc[-2, frame.columns.get_loc("18_DMA")] = 98.0
    frame.iloc[-1, frame.columns.get_loc("8_DMA")] = 101.0
    frame.iloc[-1, frame.columns.get_loc("18_DMA")] = 99.0
    frame.iloc[-2, frame.columns.get_loc("Close")] = 99.0
    frame.iloc[-1, frame.columns.get_loc("Close")] = 100.0
    frame.iloc[-1, frame.columns.get_loc("Low")] = 99.5
    return frame


class Nifty500IntersectTests(unittest.TestCase):
    def test_nse_json_response_is_parsed_and_namespaced(self) -> None:
        response = Mock()
        response.json.return_value = {"data": [{"symbol": "TCS"}, {"symbol": "INFY.NS"}]}
        response.text = ""
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.side_effect = [Mock(), response]

        tickers = scanner.get_nifty500_tickers(session)

        self.assertEqual(tickers, ["TCS.NS", "INFY.NS"])
        self.assertEqual(session.get.call_count, 2)

    def test_indicator_calculation_does_not_mutate_input(self) -> None:
        source = make_history()
        original = source.copy(deep=True)

        result = scanner.calculate_indicators(source)

        pd.testing.assert_frame_equal(source, original)
        self.assertTrue({"8_DMA", "18_DMA", "ATR", "ADX"}.issubset(result.columns))
        self.assertTrue(result["ATR"].iloc[-1] > 0)
        self.assertTrue(result["ADX"].iloc[-1] >= 0)

    def test_a_adx_signal_contains_exit_mapping(self) -> None:
        result = scanner.evaluate_intersect_signal("TCS.NS", make_evaluation_frame())

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["Triggered Entry (Y)"], "A_ADX (Anticipatory)")
        self.assertEqual(result["Price (INR)"], 100.0)
        self.assertEqual(result["Target Target Level"], 105.0)
        self.assertIn("100BP Target: 101.0", result["X/Y Intersect Rule"])
        self.assertIn("DMA Stop: 101.0", result["X/Y Intersect Rule"])

    def test_screen_uses_real_indicators_and_skips_failed_downloads(self) -> None:
        history = make_history()
        # The latest eight closes produce an upward 8-DMA over the 18-DMA.
        history.loc[:, "Close"] = 100.0
        history.loc[:, "High"] = 101.0
        history.loc[:, "Low"] = 99.0
        history.iloc[-8:, history.columns.get_loc("Close")] = 100.3
        history.iloc[-8:, history.columns.get_loc("High")] = 101.3
        history.iloc[-8:, history.columns.get_loc("Low")] = 99.3
        history.iloc[-1, history.columns.get_loc("Low")] = 100.0

        def downloader(ticker: str, _config: scanner.IntersectScannerConfig) -> pd.DataFrame:
            if ticker == "BROKEN.NS":
                raise RuntimeError("simulated provider failure")
            return history

        result = scanner.screen_stocks_with_intersect(
            ["GOOD.NS", "BROKEN.NS"],
            downloader=downloader,
        )

        self.assertEqual(list(result["Ticker"]), ["GOOD"])
        self.assertEqual(list(result.columns), scanner.RESULT_COLUMNS)

    def test_multiindex_yfinance_columns_are_flattened(self) -> None:
        data = make_history(3)
        data.columns = pd.MultiIndex.from_product([data.columns, ["ABC.NS"]])

        result = scanner._normalise_yfinance_columns(data, "ABC.NS")

        self.assertEqual(list(result.columns), list(data.columns.get_level_values(0).unique()))
        self.assertEqual(result["Close"].iloc[-1], data[("Close", "ABC.NS")].iloc[-1])


if __name__ == "__main__":
    unittest.main()
