import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import bearisbiasnifty500 as scanner


SAMPLE_TICKERS = pd.DataFrame({"Ticker": ["TCS", "INFY"], "Industry": ["IT", "IT"]})
SAMPLE_SYMBOLS = pd.DataFrame({"Symbol": ["RELIANCE", "HDFCBANK"]})


class ExcelEngineSelectionTests(unittest.TestCase):
    def test_engine_is_resolved_from_common_excel_suffixes(self) -> None:
        self.assertEqual(scanner.excel_engine_for_path("list.xlsx"), "openpyxl")
        self.assertEqual(scanner.excel_engine_for_path("list.XLSM"), "openpyxl")
        self.assertEqual(scanner.excel_engine_for_path("list.xls"), "xlrd")
        self.assertEqual(scanner.excel_engine_for_path("list.xlsb"), "pyxlsb")
        self.assertEqual(scanner.excel_engine_for_path("list.ods"), "odf")
        self.assertIsNone(scanner.excel_engine_for_path("list.csv"))

    def test_explicit_engine_overrides_suffix(self) -> None:
        self.assertEqual(scanner.excel_engine_for_path("list.xls", "openpyxl"), "openpyxl")
        self.assertIsNone(scanner.excel_engine_for_path("list.xlsx", "csv"))

    def test_write_engine_defaults_to_openpyxl_for_xlsx(self) -> None:
        self.assertEqual(
            scanner.excel_engine_for_path("out.xlsx", mode="writer"),
            "openpyxl",
        )


class InputTableReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_xlsx_is_read_with_openpyxl_engine(self) -> None:
        path = self.root / "universe.xlsx"
        SAMPLE_TICKERS.to_excel(path, index=False, engine="openpyxl")

        with patch.object(pd, "read_excel", wraps=pd.read_excel) as read_excel:
            frame = scanner.read_input_table(path)

        read_excel.assert_called_once()
        self.assertEqual(read_excel.call_args.kwargs["engine"], "openpyxl")
        self.assertEqual(scanner.extract_tickers(frame), ["TCS", "INFY"])

    def test_csv_input_does_not_use_read_excel(self) -> None:
        path = self.root / "universe.csv"
        SAMPLE_TICKERS.to_csv(path, index=False)

        with patch.object(pd, "read_excel") as read_excel:
            frame = scanner.read_input_table(path)

        read_excel.assert_not_called()
        self.assertEqual(scanner.extract_tickers(frame), ["TCS", "INFY"])

    def test_csv_engine_flag_reads_csv_even_with_xlsx_suffix(self) -> None:
        path = self.root / "misnamed.xlsx"
        SAMPLE_TICKERS.to_csv(path, index=False)

        frame = scanner.read_input_table(path, engine="csv")
        self.assertEqual(scanner.extract_tickers(frame), ["TCS", "INFY"])

    def test_extensionless_xlsx_uses_sniffed_openpyxl_engine(self) -> None:
        xlsx_path = self.root / "universe.xlsx"
        SAMPLE_TICKERS.to_excel(xlsx_path, index=False, engine="openpyxl")
        bare_path = self.root / "universe"
        shutil.copy(xlsx_path, bare_path)

        self.assertEqual(scanner.sniff_excel_engine(bare_path), "openpyxl")
        with patch.object(pd, "read_excel", wraps=pd.read_excel) as read_excel:
            frame = scanner.read_input_table(bare_path)

        self.assertEqual(read_excel.call_args.kwargs["engine"], "openpyxl")
        self.assertEqual(scanner.extract_tickers(frame), ["TCS", "INFY"])

    def test_html_xls_fallback_reads_nse_style_table(self) -> None:
        path = self.root / "nse_list.xls"
        path.write_text(
            "<html><body><table>"
            "<tr><th>Ticker</th><th>Industry</th></tr>"
            "<tr><td>TCS</td><td>IT</td></tr>"
            "<tr><td>INFY</td><td>IT</td></tr>"
            "</table></body></html>",
            encoding="utf-8",
        )

        frame = scanner.read_input_table(path)
        self.assertEqual(scanner.extract_tickers(frame), ["TCS", "INFY"])

    def test_html_engine_flag_reads_html_table(self) -> None:
        path = self.root / "universe.html"
        path.write_text(
            "<table><tr><th>Symbol</th></tr><tr><td>RELIANCE</td></tr></table>",
            encoding="utf-8",
        )

        frame = scanner.read_input_table(path, engine="html")
        self.assertEqual(scanner.extract_tickers(frame), ["RELIANCE"])

    def test_missing_file_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            scanner.read_input_table(self.root / "missing.xlsx")

    def test_unreadable_file_asks_for_engine(self) -> None:
        path = self.root / "garbage.bin"
        path.write_bytes(b"\x00\x01\x02not-an-excel-file")

        with self.assertRaises(ValueError) as raised:
            scanner.read_input_table(path)

        self.assertIn("specify an engine manually", str(raised.exception))


class TickerExtractionAndWriteTests(unittest.TestCase):
    def test_symbol_column_is_accepted(self) -> None:
        self.assertEqual(
            scanner.extract_tickers(SAMPLE_SYMBOLS),
            ["RELIANCE", "HDFCBANK"],
        )

    def test_missing_ticker_column_raises(self) -> None:
        with self.assertRaises(KeyError):
            scanner.extract_tickers(pd.DataFrame({"Name": ["TCS"]}))

    def test_write_results_uses_openpyxl_for_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.xlsx"
            with patch.object(pd.DataFrame, "to_excel") as to_excel:
                scanner.write_results(SAMPLE_TICKERS, path)

            to_excel.assert_called_once()
            self.assertEqual(to_excel.call_args.kwargs["engine"], "openpyxl")
            self.assertFalse(to_excel.call_args.kwargs["index"])

    def test_write_results_uses_csv_for_csv_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            scanner.write_results(SAMPLE_TICKERS, path)
            loaded = pd.read_csv(path)
            self.assertEqual(list(loaded["Ticker"]), ["TCS", "INFY"])


class MainCliEngineTests(unittest.TestCase):
    def test_main_reads_csv_without_excel_format_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "tickers.csv"
            out = Path(tmp) / "Bearish_Momentum_Analysis.xlsx"
            SAMPLE_TICKERS.to_csv(src, index=False)

            def fake_analyze(symbol: str, config: scanner.BearishScannerConfig):
                return {"Ticker": symbol, "ADX (14)": 30.0, "CCI (14)": -120.0}

            with patch.object(scanner, "analyze_symbol", side_effect=fake_analyze):
                with patch(
                    "sys.argv",
                    ["bearisbiasnifty500.py", "--input", str(src), "--output", str(out)],
                ):
                    captured = io.StringIO()
                    with patch("sys.stdout", captured):
                        scanner.main()

            self.assertTrue(out.exists())
            saved = pd.read_excel(out, engine="openpyxl")
            self.assertEqual(list(saved["Ticker"]), ["TCS", "INFY"])
            self.assertNotIn("Excel Error", captured.getvalue())

    def test_main_honors_explicit_openpyxl_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "tickers.xlsx"
            SAMPLE_TICKERS.to_excel(src, index=False, engine="openpyxl")

            with patch.object(scanner, "analyze_symbol", return_value=None):
                with patch(
                    "sys.argv",
                    [
                        "bearisbiasnifty500.py",
                        "--input",
                        str(src),
                        "--engine",
                        "openpyxl",
                        "--output",
                        str(Path(tmp) / "out.xlsx"),
                    ],
                ):
                    captured = io.StringIO()
                    with patch("sys.stdout", captured):
                        scanner.main()

            self.assertIn("Scanning 2 stocks", captured.getvalue())
            self.assertNotIn("Excel Error", captured.getvalue())

    def test_repo_xlsx_and_nifty500_csv_are_readable(self) -> None:
        repo = Path(__file__).resolve().parent
        xlsx = repo / "NSE_Stocks_List_20251230_1617.xlsx"
        csv_path = repo / "nifty500.csv"
        self.assertTrue(xlsx.exists())
        self.assertTrue(csv_path.exists())

        excel_tickers = scanner.extract_tickers(scanner.read_input_table(xlsx, engine="openpyxl"))
        csv_tickers = scanner.extract_tickers(scanner.read_input_table(csv_path, engine="csv"))

        self.assertGreater(len(excel_tickers), 100)
        self.assertGreater(len(csv_tickers), 100)
        self.assertIn("TCS", {ticker.replace(".NS", "") for ticker in excel_tickers})
        self.assertIn("TCS", csv_tickers)


if __name__ == "__main__":
    unittest.main()
