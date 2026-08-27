import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

import combinedoptionanalyzedv8 as scanner


SAMPLE_NIFTY_CSV = pd.DataFrame(
    {
        "Company Name": ["Tata Consultancy Services Ltd.", "Infosys Ltd."],
        "Industry": ["IT", "IT"],
        "Ticker": ["TCS", "INFY"],
        "Series": ["EQ", "EQ"],
        "ISIN Code": ["INE467B01029", "INE009A01021"],
    }
)


class StockListLoaderTests(unittest.TestCase):
    def test_github_blob_url_is_converted_to_raw(self) -> None:
        raw_url = scanner.to_raw_github_url(scanner.DEFAULT_STOCK_LIST_URL)

        self.assertEqual(
            raw_url,
            "https://raw.githubusercontent.com/karagis75/trading/main/"
            "ind_nifty500list.csv",
        )

    def test_csv_ticker_column_is_parsed_without_yahoo_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ind_nifty500list.csv"
            SAMPLE_NIFTY_CSV.assign(Ticker=["TCS", "INFY.NS"]).to_csv(path, index=False)

            symbols = scanner.load_symbols_from_input(path)

        self.assertEqual(symbols, ["TCS", "INFY"])

    def test_csv_symbol_column_is_used_when_ticker_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stocks.csv"
            pd.DataFrame({"Symbol": ["RELIANCE", "HDFCBANK.NS"]}).to_csv(
                path, index=False
            )

            symbols = scanner.load_symbols_from_input(path)

        self.assertEqual(symbols, ["RELIANCE", "HDFCBANK"])

    def test_github_csv_is_downloaded_from_raw_url(self) -> None:
        response = Mock()
        response.content = SAMPLE_NIFTY_CSV.to_csv(index=False).encode("utf-8")
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response

        symbols = scanner.load_symbols_from_input(
            scanner.DEFAULT_STOCK_LIST_URL,
            session=session,
        )

        self.assertEqual(symbols, ["TCS", "INFY"])
        session.get.assert_called_once()
        requested_url = session.get.call_args.args[0]
        self.assertEqual(
            requested_url,
            scanner.to_raw_github_url(scanner.DEFAULT_STOCK_LIST_URL),
        )

    def test_repo_nifty500_csv_loads_equity_tickers(self) -> None:
        symbols = scanner.load_symbols_from_input("ind_nifty500list.csv")

        self.assertEqual(len(symbols), 500)
        self.assertIn("INFY", symbols)
        self.assertIn("RELIANCE", symbols)
        self.assertIn("HDFCBANK", symbols)
        self.assertTrue(all("." not in symbol for symbol in symbols))

    def test_missing_ticker_column_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.csv"
            pd.DataFrame({"Company Name": ["TCS"]}).to_csv(path, index=False)

            with self.assertRaisesRegex(ValueError, "Ticker' or 'Symbol'"):
                scanner.load_symbols_from_input(path)

    def test_missing_file_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "Input file not found"):
            scanner.resolve_input_source("/tmp/does-not-exist-stock-list.csv")

    def test_symbols_flag_overrides_input_file(self) -> None:
        symbols, source = scanner.resolve_scan_symbols(
            ["infy", "HDFCBANK.NS"],
            "ind_nifty500list.csv",
        )

        self.assertEqual(symbols, ["INFY", "HDFCBANK"])
        self.assertEqual(source, "command line")


class CombinedScannerCliTests(unittest.TestCase):
    def test_input_argument_loads_csv_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            src = Path(temp_dir) / "ind_nifty500list.csv"
            SAMPLE_NIFTY_CSV.to_csv(src, index=False)
            analyzed: list[str] = []

            def fake_analyze(session, symbol, *args, **kwargs):
                analyzed.append(symbol.upper())
                return []

            with patch.object(scanner, "create_nse_session", return_value=object()):
                with patch.object(scanner, "fetch_india_vix", return_value=12.5):
                    with patch.object(
                        scanner, "analyze_symbol", side_effect=fake_analyze
                    ):
                        with patch(
                            "sys.argv",
                            [
                                "combinedoptionanalyzedv8.py",
                                "--input",
                                str(src),
                            ],
                        ):
                            captured = io.StringIO()
                            with patch("sys.stdout", captured):
                                scanner.main()

            output = captured.getvalue()
            self.assertEqual(analyzed, ["TCS", "INFY"])
            self.assertIn("Loaded 2 symbols from", output)
            self.assertIn("Analyzing TCS...", output)
            self.assertIn("Analyzing INFY...", output)

    def test_default_input_is_ind_nifty500list_csv(self) -> None:
        loaded_from: list[str] = []

        def fake_resolve(symbols, input_path, session=None):
            loaded_from.append(input_path)
            return ["INFY"], input_path

        with patch.object(
            scanner, "resolve_scan_symbols", side_effect=fake_resolve
        ):
            with patch.object(scanner, "create_nse_session", return_value=object()):
                with patch.object(scanner, "fetch_india_vix", return_value=12.5):
                    with patch.object(scanner, "analyze_symbol", return_value=[]):
                        with patch("sys.argv", ["combinedoptionanalyzedv8.py"]):
                            captured = io.StringIO()
                            with patch("sys.stdout", captured):
                                scanner.main()

        self.assertEqual(loaded_from, ["ind_nifty500list.csv"])
        self.assertIn("Loaded 1 symbol from ind_nifty500list.csv.", captured.getvalue())

    def test_symbols_argument_still_scans_explicit_subset(self) -> None:
        analyzed: list[str] = []

        def fake_analyze(session, symbol, *args, **kwargs):
            analyzed.append(symbol.upper())
            return []

        with patch.object(scanner, "create_nse_session", return_value=object()):
            with patch.object(scanner, "fetch_india_vix", return_value=12.5):
                with patch.object(scanner, "analyze_symbol", side_effect=fake_analyze):
                    with patch(
                        "sys.argv",
                        [
                            "combinedoptionanalyzedv8.py",
                            "--input",
                            "ind_nifty500list.csv",
                            "--symbols",
                            "SBIN",
                            "IOC",
                        ],
                    ):
                        captured = io.StringIO()
                        with patch("sys.stdout", captured):
                            scanner.main()

        self.assertEqual(analyzed, ["SBIN", "IOC"])
        self.assertIn("Loaded 2 symbols from command line.", captured.getvalue())


class ResilientNseSessionTests(unittest.TestCase):
    """Covers request pacing, retry-on-exception, retry-on-5xx, and session
    refresh added to fix NSE resetting HTTP/2 streams after ~30 rapid calls.
    """

    def test_get_retries_on_exception_and_succeeds(self) -> None:
        ok_response = Mock(status_code=200)
        raw = Mock()
        raw.get.side_effect = [RuntimeError("stream reset"), ok_response]

        with patch.object(scanner, "_build_raw_nse_session", return_value=raw):
            session = scanner.ResilientNseSession(None, False, request_delay=0.0, max_retries=3)
            with patch("time.sleep", return_value=None):
                result = session.get("https://example.test/api")

        self.assertIs(result, ok_response)
        self.assertEqual(raw.get.call_count, 2)

    def test_get_refreshes_session_after_failure(self) -> None:
        ok_response = Mock(status_code=200)
        failing_raw = Mock()
        failing_raw.get.side_effect = RuntimeError("stream reset")
        fresh_raw = Mock()
        fresh_raw.get.return_value = ok_response

        with patch.object(
            scanner, "_build_raw_nse_session", side_effect=[failing_raw, fresh_raw]
        ) as build_mock:
            session = scanner.ResilientNseSession(None, False, request_delay=0.0, max_retries=3)
            with patch("time.sleep", return_value=None):
                result = session.get("https://example.test/api")

        self.assertIs(result, ok_response)
        self.assertEqual(build_mock.call_count, 2)

    def test_get_retries_on_server_error_status(self) -> None:
        busy_response = Mock(status_code=503)
        ok_response = Mock(status_code=200)
        raw = Mock()
        raw.get.side_effect = [busy_response, ok_response]

        with patch.object(scanner, "_build_raw_nse_session", return_value=raw):
            session = scanner.ResilientNseSession(None, False, request_delay=0.0, max_retries=3)
            with patch("time.sleep", return_value=None):
                result = session.get("https://example.test/api")

        self.assertIs(result, ok_response)
        self.assertEqual(raw.get.call_count, 2)

    def test_get_raises_after_exhausting_retries(self) -> None:
        raw = Mock()
        raw.get.side_effect = RuntimeError("stream reset")

        with patch.object(scanner, "_build_raw_nse_session", return_value=raw):
            session = scanner.ResilientNseSession(None, False, request_delay=0.0, max_retries=3)
            with patch("time.sleep", return_value=None):
                with self.assertRaises(RuntimeError):
                    session.get("https://example.test/api")

        self.assertEqual(raw.get.call_count, 3)

    def test_get_paces_requests_with_delay(self) -> None:
        raw = Mock()
        raw.get.return_value = Mock(status_code=200)

        with patch.object(scanner, "_build_raw_nse_session", return_value=raw):
            session = scanner.ResilientNseSession(None, False, request_delay=5.0, max_retries=3)
            session._last_request_at = time.monotonic()
            with patch("time.sleep") as sleep_mock:
                session.get("https://example.test/api")

        sleep_mock.assert_called_once()
        self.assertAlmostEqual(sleep_mock.call_args.args[0], 5.0, delta=0.1)

    def test_get_json_survives_transient_failures_via_session_retry(self) -> None:
        ok_response = Mock(status_code=200)
        ok_response.json.return_value = {"records": {"data": []}}
        session = Mock()
        session.get.return_value = ok_response

        result = scanner.get_json(session, "https://example.test/api")

        self.assertEqual(result, {"records": {"data": []}})


if __name__ == "__main__":
    unittest.main()
