import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import minervini_volume_cpr_scanner as scanner


REPO = Path(__file__).resolve().parent
NIFTY_CSV = REPO / "ind_nifty500list.csv"


def short_config(**overrides) -> scanner.VolumeCPRScannerConfig:
    values = dict(
        ema_fast=3,
        ema_mid=5,
        ema_slow=8,
        ema_150=10,
        ema_200=12,
        cci_period=5,
        atr_period=5,
        ema200_lookback=3,
        week52_bars=10,
        volume_ema=5,
        narrow_cpr_max_width_pct=0.05,
        combine_mode="all",
        min_sections=3,
    )
    values.update(overrides)
    return scanner.VolumeCPRScannerConfig(**values)


def indicator_row(**values) -> dict:
    row = {
        "Open": 100.0,
        "High": 110.0,
        "Low": 95.0,
        "Close": 108.0,
        "Volume": 2_000_000.0,
        "EMA50": 102.0,
        "EMA150": 98.0,
        "EMA200": 90.0,
        "EMA200_1M_AGO": 85.0,
        "EMA_VOL20": 1_000_000.0,
        "PIVOT": 100.0,
        "CPR_TOP": 101.0,
        "CPR_BOTTOM": 99.0,
        "CPR_WIDTH": 2.0,
        "CPR_WIDTH_PCT": 0.02,
        "LOW_52W": 70.0,
        "HIGH_52W": 120.0,
    }
    row.update(values)
    return row


def two_bar_frame(prev: dict, curr: dict, extra: list[dict] | None = None) -> pd.DataFrame:
    rows = list(extra or [])
    rows.extend([prev, curr])
    index = pd.bdate_range("2026-01-02", periods=len(rows))
    return pd.DataFrame(rows, index=index)


def uptrend_ohlcv(periods: int = 40, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=periods)
    close = start + np.arange(periods) * step
    high = close + 1.5
    low = close - 1.5
    open_ = close - 0.8
    volume = np.full(periods, 1_000_000.0)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


def passing_ohlcv(config: scanner.VolumeCPRScannerConfig) -> pd.DataFrame:
    """Build bars that pass all three sections after indicators are applied."""
    periods = max(config.minimum_history + 10, 32)
    frame = uptrend_ohlcv(periods=periods, start=80.0, step=0.4)
    base = float(frame.iloc[-4]["Close"])
    frame.iloc[-3] = {
        "Open": base,
        "High": base + 12.0,
        "Low": base - 12.0,
        "Close": base - 1.0,
        "Volume": 800_000.0,
    }
    frame.iloc[-2] = {
        "Open": base - 2.0,
        "High": base + 0.3,
        "Low": base + 0.5,
        "Close": base,
        "Volume": 2_000_000.0,
    }
    frame.iloc[-1] = {
        "Open": base + 0.5,
        "High": base + 40.0,
        "Low": base + 0.6,
        "Close": base + 38.0,
        "Volume": 4_000_000.0,
    }
    return frame


class IndicatorTests(unittest.TestCase):
    def test_cpr_width_columns_are_added(self) -> None:
        frame = pd.DataFrame(
            {
                "Open": [100.0, 108.0],
                "High": [110.0, 120.0],
                "Low": [90.0, 107.0],
                "Close": [105.0, 118.0],
                "Volume": [1_000.0, 2_000.0],
            },
            index=pd.bdate_range("2026-01-05", periods=2),
        )
        out = scanner.calculate_indicators(frame, short_config())
        last = out.iloc[-1]
        self.assertAlmostEqual(float(last["CPR_WIDTH"]), float(last["CPR_TOP"] - last["CPR_BOTTOM"]))
        self.assertAlmostEqual(float(last["CPR_WIDTH_PCT"]), float(last["CPR_WIDTH"] / last["Close"]))


class SectionTests(unittest.TestCase):
    def test_minervini_volume_passes_with_trend_and_volume(self) -> None:
        curr = indicator_row()
        result = scanner.evaluate_minervini_volume(two_bar_frame(indicator_row(), curr), 1, short_config())
        self.assertTrue(result.passed, result.failed_names)

    def test_virgin_cpr_buy_requires_virgin_above_and_breakout(self) -> None:
        prev = indicator_row(Close=100.0, CPR_TOP=101.0, PIVOT=100.0, Open=99.0)
        curr = indicator_row(
            Low=100.5,
            Close=108.0,
            CPR_TOP=102.0,
            Open=103.0,
            Volume=2_000_000.0,
            PIVOT=101.0,
        )
        result = scanner.evaluate_virgin_cpr_buy(two_bar_frame(prev, curr), 1, short_config())
        self.assertTrue(result.passed, result.failed_names)

    def test_virgin_cpr_buy_fails_when_low_touches_prior_pivot(self) -> None:
        prev = indicator_row(Close=100.0, CPR_TOP=101.0, PIVOT=100.0)
        curr = indicator_row(Low=99.5, Close=108.0, CPR_TOP=102.0, Open=103.0)
        result = scanner.evaluate_virgin_cpr_buy(two_bar_frame(prev, curr), 1, short_config())
        self.assertFalse(result.passed)
        self.assertIn("Virgin above prior pivot", result.failed_names)

    def test_narrow_cpr_breakout_passes_on_tight_range_and_cross(self) -> None:
        prev = indicator_row(Close=100.0, CPR_TOP=101.0, Open=99.0)
        curr = indicator_row(
            Close=108.0,
            CPR_TOP=102.0,
            CPR_BOTTOM=101.5,
            CPR_WIDTH=0.5,
            CPR_WIDTH_PCT=0.004,
            Open=103.0,
            Volume=2_000_000.0,
        )
        result = scanner.evaluate_narrow_cpr_breakout(two_bar_frame(prev, curr), 1, short_config())
        self.assertTrue(result.passed, result.failed_names)

    def test_narrow_cpr_breakout_fails_when_cpr_is_wide(self) -> None:
        prev = indicator_row(Close=100.0, CPR_TOP=101.0)
        curr = indicator_row(
            Close=108.0,
            CPR_TOP=120.0,
            CPR_BOTTOM=90.0,
            CPR_WIDTH=30.0,
            CPR_WIDTH_PCT=0.25,
            Open=103.0,
        )
        result = scanner.evaluate_narrow_cpr_breakout(two_bar_frame(prev, curr), 1, short_config())
        self.assertFalse(result.passed)
        self.assertTrue(any("CPR width" in name for name in result.failed_names))


class CombinedTests(unittest.TestCase):
    def test_evaluate_scan_requires_all_sections_in_all_mode(self) -> None:
        config = short_config()
        frame = scanner.calculate_indicators(passing_ohlcv(config), config)
        snapshot = scanner.evaluate_scan(frame, config)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertTrue(snapshot["Minervini_Volume"])
        self.assertTrue(snapshot["Virgin_CPR_Buy"])
        self.assertTrue(snapshot["Narrow_CPR_Breakout"])
        self.assertTrue(snapshot["Qualified"])

    def test_score_mode_can_qualify_on_partial_sections(self) -> None:
        config = short_config(combine_mode="score", min_sections=2)
        frame = scanner.calculate_indicators(passing_ohlcv(config), config)
        frame.iloc[-1, frame.columns.get_loc("Low")] = 50.0
        snapshot = scanner.evaluate_scan(frame, config)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertFalse(snapshot["Virgin_CPR_Buy"])
        self.assertGreaterEqual(snapshot["Sections_Passed"], 2)
        self.assertTrue(snapshot["Qualified"])


class CliTests(unittest.TestCase):
    def test_main_writes_xlsx_from_csv_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "tickers.csv"
            out = Path(tmp) / "Minervini_Volume_CPR_Scan.xlsx"
            pd.DataFrame(
                {
                    "Ticker": ["TCS"],
                    "Company Name": ["Tata Consultancy Services Ltd."],
                }
            ).to_csv(src, index=False)

            hit = {
                "Ticker": "TCS",
                "Date": "2026-08-30",
                "Close": 3000.0,
                "EMA50": 2900.0,
                "EMA150": 2500.0,
                "EMA200": 2400.0,
                "CPR_Top": 2980.0,
                "CPR_Pivot": 2970.0,
                "CPR_Bottom": 2960.0,
                "CPR_Width": 20.0,
                "CPR_Width_%": 0.67,
                "Virgin_Above": True,
                "Low_52W": 1800.0,
                "High_52W": 3200.0,
                "Volume": 1_000_000.0,
                "Volume_EMA20": 800_000.0,
                "Next_Open": 3010.0,
                "Suggested_Stop": 2960.0,
                "Risk_Per_Share": 40.0,
                "Minervini_Volume": True,
                "Virgin_CPR_Buy": True,
                "Narrow_CPR_Breakout": True,
                "Sections_Passed": 3,
                "Qualified": True,
                "Failed_Conditions": "",
            }

            with patch.object(scanner, "analyze_symbol", return_value=hit):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    code = scanner.main(["--input", str(src), "--output", str(out), "--engine", "csv"])

            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            saved = pd.read_excel(out, engine="openpyxl")
            self.assertEqual(list(saved["Ticker"]), ["TCS"])
            self.assertEqual(list(saved["Company Name"]), ["Tata Consultancy Services Ltd."])
            self.assertIn("qualified setup", captured.getvalue().lower())

    def test_analyze_symbol_qualifies_crafted_history(self) -> None:
        config = short_config()
        result = scanner.analyze_symbol("TCS", config, history=passing_ohlcv(config))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["Ticker"], "TCS")
        self.assertTrue(result["Qualified"], result.get("Failed_Conditions"))

    def test_repo_nifty_csv_is_readable(self) -> None:
        self.assertTrue(NIFTY_CSV.exists())
        tickers = scanner.extract_tickers(scanner.read_input_table(NIFTY_CSV, engine="csv"))
        self.assertGreater(len(tickers), 100)
        self.assertIn("TCS", tickers)


if __name__ == "__main__":
    unittest.main()
