import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import bearish_fib_pinball as bearish
import fib_pinball_common as common
import nifty_pinball_yahoo as bullish

REPO = Path(__file__).resolve().parent
NIFTY_CSV = REPO / "ind_nifty500list.csv"


def rows_from_closes(closes: list[float], pad: float = 0.8) -> list[dict]:
    dates = pd.bdate_range("2024-01-02", periods=len(closes))
    rows = []
    for stamp, close in zip(dates, closes):
        rows.append(
            {
                "date": stamp.date().isoformat(),
                "open": close,
                "high": close + pad,
                "low": close - pad,
                "close": close,
                "volume": 1_000_000.0,
            }
        )
    return rows


def punch_low(rows: list[dict], index: int, low: float) -> None:
    rows[index]["low"] = low
    rows[index]["close"] = min(rows[index]["close"], low + 0.4)
    rows[index]["open"] = rows[index]["close"]
    for other in range(max(0, index - 5), min(len(rows), index + 6)):
        if other != index and rows[other]["low"] <= low:
            rows[other]["low"] = low + 1.25


def punch_high(rows: list[dict], index: int, high: float) -> None:
    rows[index]["high"] = high
    rows[index]["close"] = max(rows[index]["close"], high - 0.4)
    rows[index]["open"] = rows[index]["close"]
    for other in range(max(0, index - 5), min(len(rows), index + 6)):
        if other != index and rows[other]["high"] >= high:
            rows[other]["high"] = high - 1.25


def bullish_wave3_rows() -> list[dict]:
    closes: list[float] = []
    closes.extend([130 - i * 1.0 for i in range(20)])  # 130 -> 111
    closes.extend([111 + i * (89 / 19) for i in range(1, 21)])  # up toward 200
    closes.extend([200 - i * (50 / 14) for i in range(1, 16)])  # down toward 150
    closes.extend([150 + i * (100 / 24) for i in range(1, 26)])  # up toward 250
    rows = rows_from_closes(closes)
    punch_low(rows, 19, 100.0)
    punch_high(rows, 39, 200.0)
    punch_low(rows, 54, 150.0)
    rows[-1]["close"] = 250.0
    rows[-1]["open"] = 248.0
    rows[-1]["high"] = 252.0
    rows[-1]["low"] = 247.0
    return rows


def bearish_wave3_rows() -> list[dict]:
    closes: list[float] = []
    closes.extend([170 + i * 1.2 for i in range(20)])  # up toward W0
    closes.extend([194 - i * (94 / 19) for i in range(1, 21)])  # down toward 100
    closes.extend([100 + i * (50 / 14) for i in range(1, 16)])  # bounce toward 150
    closes.extend([150 - i * (100 / 24) for i in range(1, 26)])  # down toward 50
    rows = rows_from_closes(closes)
    punch_high(rows, 19, 200.0)
    punch_low(rows, 39, 100.0)
    punch_high(rows, 54, 150.0)
    rows[-1]["close"] = 50.0
    rows[-1]["open"] = 52.0
    rows[-1]["high"] = 53.0
    rows[-1]["low"] = 49.0
    return rows


def rows_to_history(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [row["open"] for row in rows],
            "High": [row["high"] for row in rows],
            "Low": [row["low"] for row in rows],
            "Close": [row["close"] for row in rows],
            "Volume": [row["volume"] for row in rows],
        },
        index=pd.to_datetime([row["date"] for row in rows]),
    )


class UniverseAndSymbolTests(unittest.TestCase):
    def test_repo_nifty500_csv_is_the_default_universe(self) -> None:
        self.assertTrue(NIFTY_CSV.exists())
        tickers = common.load_universe(NIFTY_CSV, engine="csv")
        self.assertGreater(len(tickers), 400)
        self.assertIn("TCS", tickers)
        self.assertIn("RELIANCE", tickers)
        self.assertEqual(common.DEFAULT_INPUT, "ind_nifty500list.csv")
        self.assertEqual(bullish.parse_args([]).input, "ind_nifty500list.csv")
        self.assertEqual(bearish.parse_args([]).input, "ind_nifty500list.csv")

    def test_yahoo_symbol_appends_ns(self) -> None:
        self.assertEqual(common.yahoo_symbol("TCS"), "TCS.NS")
        self.assertEqual(common.yahoo_symbol("360ONE.NS"), "360ONE.NS")
        self.assertEqual(common.display_symbol("TCS.NS"), "TCS")


class PivotAndLookaheadTests(unittest.TestCase):
    def test_find_pivots_keeps_unique_alternating_swings(self) -> None:
        rows = bullish_wave3_rows()
        pivots = common.find_pivots(rows, left=5, right=5)
        types = [pivot.type for pivot in pivots]
        for previous, current in zip(types, types[1:]):
            self.assertNotEqual(previous, current)
        lows = [pivot for pivot in pivots if pivot.type == "L"]
        highs = [pivot for pivot in pivots if pivot.type == "H"]
        self.assertTrue(any(abs(pivot.price - 100.0) < 0.01 for pivot in lows))
        self.assertTrue(any(abs(pivot.price - 200.0) < 0.01 for pivot in highs))
        self.assertTrue(any(abs(pivot.price - 150.0) < 0.01 for pivot in lows))

    def test_last_unconfirmed_bars_cannot_be_pivots(self) -> None:
        rows = bullish_wave3_rows()
        right = 5
        pivots = common.find_pivots(rows, left=5, right=right)
        last_allowed = len(rows) - 1 - right
        self.assertTrue(all(pivot.idx <= last_allowed for pivot in pivots))
        future = [dict(row) for row in rows]
        future[-1]["high"] = 10_000.0
        future[-1]["close"] = 9_999.0
        unchanged = common.find_pivots(future, left=5, right=right)
        self.assertEqual([pivot.idx for pivot in pivots], [pivot.idx for pivot in unchanged])


class BullishAnalysisTests(unittest.TestCase):
    def test_wave3_when_price_is_one_extension_above_w2(self) -> None:
        rows = bullish_wave3_rows()
        result = bullish.analyze_bullish("TCS", rows, common.PinballConfig())
        self.assertIsNotNone(result)
        self.assertEqual(result["Wave Position"], "Wave 3")
        self.assertEqual(result["Confidence"], 80)
        self.assertAlmostEqual(result["W0 Low"], 100.0, places=1)
        self.assertAlmostEqual(result["W1 High"], 200.0, places=1)
        self.assertAlmostEqual(result["W2 Low"], 150.0, places=1)
        self.assertAlmostEqual(result["Current Price"], 250.0, places=1)
        self.assertAlmostEqual(result["Ext Ratio"], 1.0, places=1)
        self.assertAlmostEqual(result["1.618 Ext"], 150.0 + 1.618 * 100.0, places=1)

    def test_early_wave1_of_3_before_w1_break(self) -> None:
        rows = bullish_wave3_rows()
        rows[-1]["close"] = 180.0
        rows[-1]["open"] = 178.0
        rows[-1]["high"] = 181.0
        rows[-1]["low"] = 177.0
        result = bullish.analyze_bullish("TCS", rows, common.PinballConfig())
        self.assertIsNotNone(result)
        self.assertEqual(result["Wave Position"], "Early Wave 1 of 3")

    def test_below_w2_falls_through_without_that_setup(self) -> None:
        rows = bullish_wave3_rows()
        rows[-1]["close"] = 140.0
        rows[-1]["open"] = 141.0
        rows[-1]["high"] = 142.0
        rows[-1]["low"] = 139.0
        result = bullish.analyze_bullish("TCS", rows, common.PinballConfig(min_bars=60))
        if result is not None:
            self.assertNotEqual(result["Wave Position"], "Wave 3")


class BearishAnalysisTests(unittest.TestCase):
    def test_wave3_when_price_is_one_extension_below_w2(self) -> None:
        rows = bearish_wave3_rows()
        result = bearish.analyze_bearish("TCS", rows, common.PinballConfig())
        self.assertIsNotNone(result)
        self.assertEqual(result["Wave Position"], "Wave 3 (Bearish)")
        self.assertEqual(result["Confidence"], 80)
        self.assertAlmostEqual(result["W0 High"], 200.0, places=1)
        self.assertAlmostEqual(result["W1 Low"], 100.0, places=1)
        self.assertAlmostEqual(result["W2 High"], 150.0, places=1)
        self.assertAlmostEqual(result["Current Price"], 50.0, places=1)
        self.assertAlmostEqual(result["Ext Ratio"], 1.0, places=1)
        self.assertAlmostEqual(result["1.618 Ext"], 150.0 - 1.618 * 100.0, places=1)

    def test_w4_range_uses_ordered_bounds(self) -> None:
        self.assertTrue(common.in_range(120.0, 130.0, 80.0))
        self.assertFalse(common.in_range(70.0, 130.0, 80.0))


class CliAndWorkbookTests(unittest.TestCase):
    def test_bullish_cli_reads_nifty_csv_and_writes_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ind_nifty500list.csv"
            out = Path(tmp) / "Bullish_Fib_Pinball.xlsx"
            pd.DataFrame({"Company Name": ["TCS Ltd."], "Ticker": ["TCS"]}).to_csv(src, index=False)
            history = rows_to_history(bullish_wave3_rows())

            captured = io.StringIO()
            with patch.object(common, "fetch_history", return_value=history):
                with patch("sys.stdout", captured):
                    code = bullish.main(["--input", str(src), "--output", str(out), "--engine", "csv"])

            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            all_hits = pd.read_excel(out, sheet_name="All", engine="openpyxl")
            wave3 = pd.read_excel(out, sheet_name="Wave_3", engine="openpyxl")
            self.assertEqual(list(all_hits["Ticker"]), ["TCS"])
            self.assertEqual(list(wave3["Wave Position"]), ["Wave 3"])
            self.assertIn("ind_nifty500list.csv", captured.getvalue())

    def test_bearish_cli_writes_empty_headers_when_no_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ind_nifty500list.csv"
            out = Path(tmp) / "Bearish_Fib_Pinball.xlsx"
            pd.DataFrame({"Ticker": ["FLAT"]}).to_csv(src, index=False)
            flat = rows_from_closes([100.0] * 80)
            history = rows_to_history(flat)
            with patch.object(common, "fetch_history", return_value=history):
                code = bearish.main(
                    ["--input", str(src), "--output", str(out), "--include-failures"]
                )
            self.assertEqual(code, 0)
            skipped = pd.read_excel(out, sheet_name="Skipped", engine="openpyxl")
            all_hits = pd.read_excel(out, sheet_name="All", engine="openpyxl")
            self.assertEqual(len(all_hits), 0)
            self.assertEqual(list(skipped["Ticker"]), ["FLAT"])
            self.assertIn("no_matching_wave_structure", list(skipped["Reason"]))


if __name__ == "__main__":
    unittest.main()
