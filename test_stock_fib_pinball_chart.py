import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import fib_pinball_common as common
import nifty_pinball_yahoo as bullish
import stock_fib_pinball_chart as chart
import yahoo_bar_store as store
from scanner_history.db import connect
from test_fib_pinball import bullish_wave3_rows, rows_to_history


class StockFibPinballChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "history.sqlite3"
        self.conn = connect(str(self.db_path))

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, symbol: str, rows: list[dict]) -> None:
        store.upsert_bars(self.conn, symbol, rows_to_history(rows))

    def test_builds_wave3_chart_from_cache_only(self) -> None:
        self._seed("TCS", bullish_wave3_rows())
        with patch.object(common.yf, "Ticker") as ticker:
            with patch.object(common, "fetch_history") as fetch:
                payload = chart.build_pinball_chart("TCS", connection=self.conn)
        ticker.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(payload["symbol"], "TCS")
        self.assertEqual(payload["source"], "yahoo_ohlcv_daily")
        self.assertGreater(len(payload["bars"]), 60)
        self.assertEqual(payload["wave"]["Wave Position"], "Wave 3")
        self.assertEqual(payload["wave"]["Confidence"], 80)
        labels = {item["label"] for item in payload["markers"]}
        self.assertEqual(labels, {"W0", "W1", "W2"})
        self.assertTrue(any(item["label"] == "1.618" for item in payload["levels"]))
        self.assertIsNone(payload["error"])

    def test_missing_cache_does_not_call_yahoo(self) -> None:
        with patch.object(common.yf, "Ticker") as ticker:
            payload = chart.build_pinball_chart("INFY", connection=self.conn)
        ticker.assert_not_called()
        self.assertEqual(payload["bars"], [])
        self.assertIsNone(payload["wave"])
        self.assertIn("No cached Yahoo bars", payload["error"])
        self.assertTrue(payload["database"])

    def test_cli_requires_one_ticker_and_prints_json(self) -> None:
        self._seed("TCS", bullish_wave3_rows())
        with patch.object(chart, "cache_only_history", wraps=chart.cache_only_history):
            with patch("sys.stdout", new_callable=io.StringIO) as buffer:
                code = chart.main(
                    ["--ticker", "TCS", "--json", "--database", str(self.db_path)]
                )
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["symbol"], "TCS")
        self.assertEqual(payload["wave"]["Wave Position"], "Wave 3")

    def test_original_scanner_module_is_unchanged_entry_point(self) -> None:
        self.assertTrue(hasattr(bullish, "analyze_bullish"))
        self.assertTrue(hasattr(bullish, "parse_args"))
        args = bullish.parse_args([])
        self.assertEqual(args.input, "ind_nifty500list.csv")
        self.assertFalse(hasattr(args, "ticker"))


if __name__ == "__main__":
    unittest.main()
