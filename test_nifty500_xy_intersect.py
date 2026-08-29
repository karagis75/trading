import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import nifty500_xy_intersect as scanner


def make_excel_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False)
    return buffer.getvalue()


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
    def test_github_blob_url_is_converted_to_raw(self) -> None:
        raw_url = scanner.to_raw_github_url(scanner.DEFAULT_STOCK_LIST_URL)

        self.assertEqual(
            raw_url,
            "https://raw.githubusercontent.com/karagis75/trading/main/"
            "NSE_Stocks_List_20251230_1617.xlsx",
        )

    def test_excel_ticker_column_is_parsed_and_namespaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stocks.xlsx"
            pd.DataFrame(
                {
                    "Company Name": ["TCS", "Infosys"],
                    "Ticker": ["TCS", "INFY.NS"],
                }
            ).to_excel(path, index=False)

            tickers = scanner.get_nifty500_tickers(path)

        self.assertEqual(tickers, ["TCS.NS", "INFY.NS"])

    def test_excel_symbol_column_is_used_when_ticker_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stocks.xlsx"
            pd.DataFrame({"Symbol": ["RELIANCE", "HDFCBANK.NS"]}).to_excel(
                path, index=False
            )

            tickers = scanner.get_nifty500_tickers(path)

        self.assertEqual(tickers, ["RELIANCE.NS", "HDFCBANK.NS"])

    def test_csv_input_is_parsed_without_using_excel_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ind_nifty500list.csv"
            pd.DataFrame({"Symbol": ["RELIANCE", "HDFCBANK.NS"]}).to_csv(
                path, index=False
            )

            with patch.object(pd, "read_excel") as read_excel:
                tickers = scanner.get_nifty500_tickers(path)

        self.assertEqual(tickers, ["RELIANCE.NS", "HDFCBANK.NS"])
        read_excel.assert_not_called()

    def test_repo_nifty500_csv_loads_symbols(self) -> None:
        tickers = scanner.get_nifty500_tickers("ind_nifty500list.csv")

        self.assertEqual(len(tickers), 500)
        self.assertIn("RELIANCE.NS", tickers)
        self.assertIn("TCS.NS", tickers)

    def test_github_excel_is_downloaded_from_raw_url(self) -> None:
        response = Mock()
        response.content = make_excel_bytes(
            [{"Ticker": "TCS"}, {"Ticker": "INFY.NS"}]
        )
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response

        tickers = scanner.get_nifty500_tickers(
            scanner.DEFAULT_STOCK_LIST_URL,
            session=session,
        )

        self.assertEqual(tickers, ["TCS.NS", "INFY.NS"])
        session.get.assert_called_once()
        requested_url = session.get.call_args.args[0]
        self.assertEqual(
            requested_url,
            scanner.to_raw_github_url(scanner.DEFAULT_STOCK_LIST_URL),
        )
        self.assertNotIn("nseindia.com", requested_url)

    def test_remote_failure_falls_back_to_local_workbook(self) -> None:
        session = Mock()
        session.get.side_effect = RuntimeError("network blocked")

        tickers = scanner.get_nifty500_tickers(
            scanner.DEFAULT_STOCK_LIST_URL,
            session=session,
        )

        self.assertGreater(len(tickers), 400)
        self.assertIn("RELIANCE.NS", tickers)
        session.get.assert_called_once()

    def test_repo_excel_workbook_loads_namespaced_tickers(self) -> None:
        tickers = scanner.get_nifty500_tickers(scanner.LOCAL_STOCK_LIST)

        self.assertGreater(len(tickers), 400)
        self.assertTrue(all(ticker.endswith(".NS") for ticker in tickers))
        self.assertIn("RELIANCE.NS", tickers)
        self.assertIn("TCS.NS", tickers)

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

    def test_c_mar_signal_uses_slow_dma_as_trailing_exit(self) -> None:
        frame = make_evaluation_frame()
        frame.iloc[:, frame.columns.get_loc("ADX")] = 20.0
        frame.iloc[-2, frame.columns.get_loc("8_DMA")] = 100.0
        frame.iloc[-2, frame.columns.get_loc("18_DMA")] = 98.0
        frame.iloc[-1, frame.columns.get_loc("8_DMA")] = 101.0
        frame.iloc[-1, frame.columns.get_loc("18_DMA")] = 99.0
        frame.iloc[-1, frame.columns.get_loc("Close")] = 100.5
        frame.iloc[-1, frame.columns.get_loc("Low")] = 100.4

        result = scanner.evaluate_intersect_signal("INFY.NS", frame)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["Triggered Entry (Y)"], "C_MAR (Retest)")
        self.assertIn("DMA Stop: 99.0", result["X/Y Intersect Rule"])

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


    def test_download_history_retries_empty_data_and_succeeds(self) -> None:
        config = scanner.IntersectScannerConfig(
            max_retries=3,
            retry_delay=0.01,
        )
        history = make_history(3)
        responses = [pd.DataFrame(), history]

        with patch.object(scanner.yf, "download", side_effect=responses) as download_mock:
            result = scanner._download_history("RELIANCE.NS", config)

        self.assertEqual(len(result), len(history))
        self.assertEqual(download_mock.call_count, 2)

    def test_download_history_returns_empty_after_all_retries_fail(self) -> None:
        config = scanner.IntersectScannerConfig(
            max_retries=2,
            retry_delay=0.01,
        )

        with patch.object(scanner.yf, "download", return_value=pd.DataFrame()) as download_mock:
            result = scanner._download_history("BROKEN.NS", config)

        self.assertTrue(result.empty)
        self.assertEqual(download_mock.call_count, config.max_retries)

    def test_screen_respects_request_delay_between_tickers(self) -> None:
        history = make_history()

        def downloader(_ticker: str, _config: scanner.IntersectScannerConfig) -> pd.DataFrame:
            return history

        config = scanner.IntersectScannerConfig(
            request_delay=0.05,
            fast_dma=8,
            slow_dma=18,
        )

        with patch.object(scanner, "time") as time_mock:
            scanner.screen_stocks_with_intersect(
                ["A.NS", "B.NS", "C.NS"],
                config=config,
                downloader=downloader,
            )

        sleeps = [
            call.args[0]
            for call in time_mock.sleep.call_args_list
        ]
        self.assertEqual(sleeps, [0.05, 0.05])


if __name__ == "__main__":
    unittest.main()
