#!/usr/bin/env python3
"""Zero-hit scanners still write dated outputs so membership history can ingest them."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import bearisbiasnifty500
import bullishbiasnifty500
import nifty500_xy_intersect
import rangeboundstocks
from combinedoptionanalyzedv8 import write_results


class EmptyOutputTests(unittest.TestCase):
    def test_bullish_zero_hits_writes_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Bullish_Bias_Analysis.xlsx")
            bullishbiasnifty500.write_results(
                pd.DataFrame(columns=bullishbiasnifty500.BULLISH_RESULT_COLUMNS),
                path,
            )
            self.assertTrue(os.path.exists(path))
            df = pd.read_excel(path)
            self.assertEqual(len(df), 0)
            self.assertIn("Ticker", list(df.columns))

    def test_bearish_zero_hits_writes_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Bearish_Momentum_Analysis.xlsx")
            bearisbiasnifty500.write_results(
                pd.DataFrame(columns=bearisbiasnifty500.BEARISH_RESULT_COLUMNS),
                path,
            )
            self.assertTrue(os.path.exists(path))
            df = pd.read_excel(path)
            self.assertEqual(len(df), 0)
            self.assertIn("Ticker", list(df.columns))

    def test_rangebound_zero_hits_writes_xlsx_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "Strangle_Candidate_Analysis.xlsx")
            csv_path = os.path.join(tmp, "rangebound_candidates.csv")
            empty = pd.DataFrame(columns=rangeboundstocks.RANGEBOUND_RESULT_COLUMNS)
            rangeboundstocks.write_results(empty, xlsx)
            rangeboundstocks.write_results(empty, csv_path)
            self.assertTrue(os.path.exists(xlsx))
            self.assertTrue(os.path.exists(csv_path))
            self.assertEqual(len(pd.read_excel(xlsx)), 0)
            self.assertEqual(len(pd.read_csv(csv_path)), 0)
            self.assertIn("Ticker", list(pd.read_excel(xlsx).columns))

    def test_xy_zero_hits_writes_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.csv"
            nifty500_xy_intersect.write_signals_csv(path, pd.DataFrame())
            self.assertTrue(path.exists())
            df = pd.read_csv(path)
            self.assertEqual(len(df), 0)
            self.assertIn("Ticker", list(df.columns))

    def test_combined_option_zero_hits_writes_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Combined_Option_Spread_Analysis.xlsx")
            write_results([], Path(path))
            self.assertTrue(os.path.exists(path))
            df = pd.read_excel(path, sheet_name="All Opportunities")
            self.assertEqual(len(df), 0)
            self.assertIn("Symbol", list(df.columns))


if __name__ == "__main__":
    unittest.main()
