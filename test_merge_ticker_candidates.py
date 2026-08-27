import tempfile
import unittest
from pathlib import Path

import pandas as pd

import merge_ticker_candidates as merger


class NormalizeTickerTests(unittest.TestCase):
    def test_strips_ns_suffix_and_uppercases(self) -> None:
        self.assertEqual(merger.normalize_ticker(" tcs.NS "), "TCS")
        self.assertEqual(merger.normalize_ticker("infy"), "INFY")


class MergeCandidatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_excel(self, name: str, tickers: list[str]) -> Path:
        path = self.root / name
        pd.DataFrame({"Ticker": tickers}).to_excel(path, index=False)
        return path

    def test_union_dedupes_across_sources_preserving_first_seen_order(self) -> None:
        bullish = self._write_excel("bullish.xlsx", ["TCS", "INFY"])
        bearish = self._write_excel("bearish.xlsx", ["INFY", "RELIANCE"])

        merged, used, missing = merger.merge_candidates([bullish, bearish])

        self.assertEqual(merged, ["TCS", "INFY", "RELIANCE"])
        self.assertEqual(used, [bullish, bearish])
        self.assertEqual(missing, [])

    def test_missing_source_files_are_tolerated_not_fatal(self) -> None:
        bullish = self._write_excel("bullish.xlsx", ["TCS"])
        missing_source = self.root / "bearish.xlsx"

        merged, used, missing = merger.merge_candidates([bullish, missing_source])

        self.assertEqual(merged, ["TCS"])
        self.assertEqual(used, [bullish])
        self.assertEqual(missing, [missing_source])

    def test_all_sources_missing_yields_empty_candidate_list(self) -> None:
        merged, used, missing = merger.merge_candidates(
            [self.root / "a.xlsx", self.root / "b.xlsx"]
        )
        self.assertEqual(merged, [])
        self.assertEqual(used, [])
        self.assertEqual(len(missing), 2)

    def test_csv_source_with_symbol_column_is_supported(self) -> None:
        path = self.root / "rangebound.csv"
        pd.DataFrame({"Symbol": ["hdfcbank.NS", "TCS"]}).to_csv(path, index=False)

        merged, used, missing = merger.merge_candidates([path])

        self.assertEqual(merged, ["HDFCBANK", "TCS"])

    def test_source_without_ticker_or_symbol_column_raises(self) -> None:
        path = self.root / "bad.csv"
        pd.DataFrame({"Name": ["TCS"]}).to_csv(path, index=False)

        with self.assertRaises(ValueError):
            merger.merge_candidates([path])


class WriteCandidatesAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_write_candidates_creates_header_only_csv_when_empty(self) -> None:
        output_path = self.root / "nested" / "candidates.csv"
        merger.write_candidates([], output_path)

        contents = output_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(contents, ["Ticker"])

    def test_write_candidates_writes_each_ticker(self) -> None:
        output_path = self.root / "candidates.csv"
        merger.write_candidates(["TCS", "INFY"], output_path)

        frame = pd.read_csv(output_path)
        self.assertEqual(frame["Ticker"].tolist(), ["TCS", "INFY"])

    def test_cli_end_to_end_merges_bullish_bearish_and_rangebound_outputs(self) -> None:
        bullish = self.root / "Bullish_Bias_Analysis.xlsx"
        bearish = self.root / "Bearish_Momentum_Analysis.xlsx"
        rangebound = self.root / "Strangle_Candidate_Analysis.xlsx"
        pd.DataFrame({"Ticker": ["TCS"]}).to_excel(bullish, index=False)
        pd.DataFrame({"Ticker": ["INFY"]}).to_excel(bearish, index=False)
        # rangebound is intentionally missing (no qualifying setups today)
        output_path = self.root / "Option_Scan_Candidates.csv"

        exit_code = merger.main(
            [
                "--sources",
                str(bullish),
                str(bearish),
                str(rangebound),
                "--output",
                str(output_path),
            ]
        )

        self.assertEqual(exit_code, 0)
        frame = pd.read_csv(output_path)
        self.assertEqual(frame["Ticker"].tolist(), ["TCS", "INFY"])

    def test_cli_writes_empty_candidate_file_when_no_sources_exist(self) -> None:
        output_path = self.root / "Option_Scan_Candidates.csv"
        exit_code = merger.main(
            [
                "--sources",
                str(self.root / "a.xlsx"),
                str(self.root / "b.xlsx"),
                "--output",
                str(output_path),
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(output_path.exists())
        frame = pd.read_csv(output_path)
        self.assertEqual(len(frame), 0)


if __name__ == "__main__":
    unittest.main()
